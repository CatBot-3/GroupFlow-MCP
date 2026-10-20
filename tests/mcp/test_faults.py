"""Independent fault/concurrency tests, frozen before implementation review.

Real local MCP/HTTP boundaries plus independent SQLite connections. Every
database is temporary synthetic data; no external model or data service runs.
"""
from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from groupflow.core.db import Db
from groupflow.core.errors import DomainError
from groupflow.engine import GroupFlow
from groupflow.world import approve_all, seed_world
from tests.conftest import WORLD
from tests.mcp.test_mcp import Phone, call, client_for, server  # noqa: F401


@pytest.fixture
def database_pair(tmp_path):
    opened = []

    def make(stage="committed"):
        path = str(tmp_path / f"fault-{len(opened)}.db")
        first = GroupFlow(Db(path))
        seed_world(first, WORLD, {"friday_dinner": stage})
        second = GroupFlow(Db(path))
        opened.extend([first, second])
        return first, second

    yield make
    for engine in reversed(opened):
        engine.db.close()


def state(engine):
    tables = ("plans", "plan_revisions", "confirmation_rounds", "approval_receipts", "challenges",
              "expenses", "settlements", "bring_items", "executions", "action_receipts", "audit", "notifications")
    return {table: sorted(engine.db.all(f"SELECT * FROM {table}"), key=lambda row: json.dumps(row, sort_keys=True))
            for table in tables}


def try_call(fn):
    try:
        return fn()
    except DomainError as err:
        return {"error": err.code}


def ready_candidate(engine):
    for actor in ("alice", "bob", "chen", "dana"):
        approve_all(engine, engine.identity.get(actor), "friday_dinner")
    plan = engine.db.get("SELECT * FROM plans WHERE id='friday_dinner'")
    assert plan["status"] == "READY_TO_COMMIT"
    assert plan["candidate_revision_id"]
    return plan


def dinner_approval(engine):
    row = engine.db.get("SELECT r.* FROM approval_receipts r JOIN approval_scopes s ON s.id=r.scope_id "
                        "WHERE r.plan_id='friday_dinner' AND r.member_id='bob' AND s.activity_id='dinner' "
                        "AND r.status='valid' ORDER BY r.rowid DESC")
    assert row
    return row


def pause_next_transaction(monkeypatch, engine):
    """Pause before SQLite BEGIN so a second connection can revoke first."""
    entered, resume = threading.Event(), threading.Event()
    original = engine.db.tx
    armed = True

    def paused(fn):
        nonlocal armed
        if armed:
            armed = False
            entered.set()
            assert resume.wait(5), "test did not release transaction barrier"
        return original(fn)

    monkeypatch.setattr(engine.db, "tx", paused)
    return entered, resume


def test_competing_expected_tokens_allow_exactly_one_complete_effect(database_pair):
    """F01: separate SQLite connections contend for the same expected state."""
    first, second = database_pair()
    token = first.state_token("party")
    start = threading.Barrier(2)
    baseline = first.db.scalar("SELECT count(*) FROM expenses")

    def compete(engine, label):
        start.wait(timeout=5)
        return try_call(lambda: engine.apply_action("alice", "costs", {
            "plan": "party", "action": "add", "amount": "11.00", "what": label}, label, token))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(compete, first, "race writer one"), pool.submit(compete, second, "race writer two")]
        results = [future.result(timeout=8) for future in futures]
    assert sum("action_receipt" in result for result in results) == 1, results
    assert [result["error"] for result in results if "error" in result] == ["STALE"], results
    assert first.db.scalar("SELECT count(*) FROM expenses") == baseline + 1
    assert first.db.scalar("SELECT count(*) FROM action_receipts WHERE request_key LIKE 'race writer %'") == 1
    assert first.db.scalar("SELECT count(*) FROM audit WHERE action='expense.added' AND detail LIKE '%race writer %'") == 1


def test_same_key_concurrent_retry_is_one_transactional_effect(database_pair):
    """F02: race the real receipt storage from independent DB connections."""
    first, second = database_pair()
    token = first.state_token("party")
    start = threading.Barrier(2)
    arguments = {"plan": "party", "action": "add", "amount": "13.00", "what": "race samekey"}

    def retry(engine):
        start.wait(timeout=5)
        return engine.apply_action("alice", "costs", arguments, "concurrent-shared-key", token)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(retry, first), pool.submit(retry, second)]
        results = [future.result(timeout=8) for future in futures]
    assert results[0]["action_receipt"]["id"] == results[1]["action_receipt"]["id"]
    assert sum(bool(result.get("replayed")) for result in results) == 1
    assert first.db.scalar("SELECT count(*) FROM expenses WHERE description='race samekey'") == 1
    assert first.db.scalar("SELECT count(*) FROM audit WHERE action='expense.added' AND detail LIKE '%race samekey%'") == 1
    assert first.db.scalar("SELECT count(*) FROM action_receipts WHERE request_key='concurrent-shared-key'") == 1


def test_injected_fault_after_ledger_and_audit_write_rolls_back_every_effect(database_pair, monkeypatch):
    """F01/F02 atomicity: fail after domain/audit writes but before receipt save."""
    first, _ = database_pair()
    before = state(first)
    original = first._audit

    def fail_after_audit(*args, **kwargs):
        result = original(*args, **kwargs)
        if len(args) > 1 and args[1] == "expense.added":
            raise RuntimeError("independent test fault after expense audit")
        return result

    monkeypatch.setattr(first, "_audit", fail_after_audit)
    with pytest.raises(RuntimeError, match="independent test fault"):
        first.apply_action("alice", "costs", {"plan": "party", "action": "add", "amount": "17", "what": "faulted row"},
                           "rollback-fault", first.state_token("party"))
    assert state(first) == before


def test_organizer_permission_revoked_before_commit_transaction_rejects_without_partial_effects(database_pair, monkeypatch):
    """F04: invocation starts, membership changes, authoritative transaction runs."""
    first, second = database_pair("awaiting")
    ready_candidate(first)
    expected = first.state_token("friday_dinner")
    entered, resume = pause_next_transaction(monkeypatch, first)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(lambda: try_call(lambda: first.apply_action(
            "alice", "commit_plan", {"plan": "friday_dinner"}, "revoked-organizer-commit", expected)))
        assert entered.wait(3)
        try:
            second.leave_group(second.identity.get("alice"), "friends")
            after_revocation = state(second)
        finally:
            resume.set()
        result = future.result(timeout=8)
    assert result.get("error") in {"NOT_FOUND", "FORBIDDEN", "STALE"}, result
    assert state(second) == after_revocation
    assert second.db.scalar("SELECT count(*) FROM action_receipts WHERE request_key='revoked-organizer-commit'") == 0


def test_confirmation_permission_revoked_after_challenge_before_transaction_rejects(database_pair, monkeypatch):
    """F04: a saved human challenge does not survive loss of current visibility."""
    first, second = database_pair("awaiting")
    bob = first.identity.get("bob")
    item = next(item for item in first.human_inbox(bob)["pending"] if item["plan_id"] == "friday_dinner")
    challenge = first.human_challenge(bob, item["scope_id"])
    entered, resume = pause_next_transaction(monkeypatch, first)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(lambda: try_call(lambda: first.human_decide(
            bob, challenge["challenge_id"], challenge["nonce"], "approved")))
        assert entered.wait(3)
        try:
            second.leave_group(second.identity.get("bob"), "friends")
            after_revocation = state(second)
        finally:
            resume.set()
        result = future.result(timeout=8)
    assert result.get("error") in {"NOT_FOUND", "FORBIDDEN", "STALE", "STALE_CANDIDATE"}, result
    assert state(second) == after_revocation
    assert second.db.scalar("SELECT used_at FROM challenges WHERE id=?", challenge["challenge_id"]) is None


def test_required_approval_revoked_before_commit_transaction_blocks(database_pair, monkeypatch):
    """F05: do not publish with an approval revoked before commit serialization."""
    first, second = database_pair("awaiting")
    plan = ready_candidate(first)
    approval = dinner_approval(first)
    expected = first.state_token("friday_dinner")
    entered, resume = pause_next_transaction(monkeypatch, first)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(lambda: try_call(lambda: first.apply_action(
            "alice", "commit_plan", {"plan": "friday_dinner"}, "revoked-approval-commit", expected)))
        assert entered.wait(3)
        try:
            second.human_revoke(second.identity.get("bob"), approval["id"])
            after_revocation = state(second)
        finally:
            resume.set()
        result = future.result(timeout=8)
    assert result.get("error") == "STALE", result
    assert state(second) == after_revocation
    assert second.db.scalar("SELECT kind FROM plan_revisions WHERE id=?", plan["candidate_revision_id"]) != "committed"
    assert second.db.scalar("SELECT count(*) FROM action_receipts WHERE request_key='revoked-approval-commit'") == 0


def test_revoke_contending_after_gate_serializes_after_commit_and_degrades_plan(database_pair, monkeypatch):
    """F05: hold the gate inside the transaction while another writer revokes."""
    first, second = database_pair("awaiting")
    plan = ready_candidate(first)
    approval = dinner_approval(first)
    expected = first.state_token("friday_dinner")
    checked, resume, revoke_started = threading.Event(), threading.Event(), threading.Event()
    original = first.gate

    def paused_gate(*args, **kwargs):
        blockers = original(*args, **kwargs)
        assert blockers == []
        checked.set()
        assert resume.wait(5), "test did not release commit gate"
        return blockers

    def revoke():
        revoke_started.set()
        return second.human_revoke(second.identity.get("bob"), approval["id"])

    monkeypatch.setattr(first, "gate", paused_gate)
    with ThreadPoolExecutor(max_workers=2) as pool:
        commit = pool.submit(first.apply_action, "alice", "commit_plan", {"plan": "friday_dinner"}, "serialized-commit", expected)
        assert checked.wait(3)
        withdrawal = pool.submit(revoke)
        try:
            assert revoke_started.wait(2)
            assert not withdrawal.done(), "revocation interleaved inside the commit transaction"
        finally:
            resume.set()
        assert commit.result(timeout=8)["outcome"] == "committed"
        assert withdrawal.result(timeout=8)["outcome"] == "revoked"
    assert second.db.scalar("SELECT status FROM plans WHERE id='friday_dinner'") == "DEGRADED"
    assert second.db.scalar("SELECT active_revision_id FROM plans WHERE id='friday_dinner'") == plan["candidate_revision_id"]
    events = second.db.all("SELECT action FROM audit WHERE plan_id='friday_dinner' AND action IN ('plan.committed','approval.revoked') ORDER BY seq")
    assert [row["action"] for row in events][-2:] == ["plan.committed", "approval.revoked"]
    assert second.db.scalar("SELECT count(*) FROM action_receipts WHERE request_key='serialized-commit'") == 1


async def test_real_mcp_concurrent_retry_status_and_dependency_correction(server):  # noqa: F811
    """F02/F08 plus C01/C07: actual SDK clients and the MCP tool dispatcher."""
    async with client_for(server, "alice") as first, client_for(server, "alice") as second:
        resolved = await call(first, "resolve_action", {"query": "Venue", "plan": "party"})
        envelope = {"action": "apply", "tool": "bring_list", "arguments": {
            "plan": "party", "action": "bring", "things": ["MCP fault rug"]},
                    "request_key": "mcp-concurrent-key", "expected": resolved["targets"][0]["version"]}
        results = await asyncio.gather(call(first, "action_receipts", envelope), call(second, "action_receipts", envelope))
        assert all("error" not in result for result in results), results
        receipt = results[0]["action_receipt"]["id"]
        assert results[1]["action_receipt"]["id"] == receipt
        known = await call(first, "action_receipts", {"action": "status", "request_key": "mcp-concurrent-key"})
        assert known["action_receipt"]["id"] == receipt
        assert (await call(first, "action_receipts", {"action": "status", "request_key": "unknown-mcp-key"}))["outcome"] == "unknown"
        async with client_for(server, "bob") as bob:
            assert (await call(bob, "action_receipts", {"action": "status", "request_key": "mcp-concurrent-key"}))["outcome"] == "unknown"
            assert (await call(bob, "bring_list", {"plan": "party", "action": "bring", "things": ["MCP later napkins"]}))["outcome"] == "updated"
            denied = await call(bob, "action_receipts", {"action": "correct", "receipt": receipt})
            assert denied["error"]["code"] == "NOT_FOUND"
        corrected = await call(first, "action_receipts", {"action": "correct", "receipt": receipt})
        assert "error" not in corrected, corrected
        before = state(server.rt.services.engine)
        assert (await call(first, "action_receipts", {"action": "correct", "receipt": receipt}))["outcome"] == "already_corrected"
        assert state(server.rt.services.engine) == before
    assert server.rt.db.scalar("SELECT count(*) FROM bring_items WHERE label='MCP fault rug'") == 1
    assert server.rt.db.scalar("SELECT claimed_by FROM bring_items WHERE label='MCP fault rug'") is None
    assert server.rt.db.scalar("SELECT claimed_by FROM bring_items WHERE label='MCP later napkins'") == "bob"
    assert server.rt.db.scalar("SELECT count(*) FROM action_receipts WHERE request_key='mcp-concurrent-key'") == 1


async def test_human_lost_successful_reply_lookup_and_same_key_recovery(server):  # noqa: F811
    """F03/F07: discard a real successful HTTP reply at the client boundary."""
    alice = await Phone(server, "alice").login()
    bob = await Phone(server, "bob").login()
    try:
        resolved = await alice.http.get(server.base + "/human/resolve", headers=alice.headers(), params={"plan": "party"})
        assert resolved.status_code == 200
        envelope = {"tool": "costs", "arguments": {"plan": "party", "action": "add", "amount": "9.99", "what": "lost reply supplies"},
                    "request_key": "human-lost-response", "expected": resolved.json()["version"]}
        committed_receipt = None

        async def send_but_lose_success():
            nonlocal committed_receipt
            response = await alice.http.post(server.base + "/human/actions/apply", headers=alice.headers(True), json=envelope)
            assert response.status_code == 200, response.text
            committed_receipt = response.json()["action_receipt"]["id"]
            raise ConnectionResetError("independent test discards response after committed write")

        with pytest.raises(ConnectionResetError, match="discards response"):
            await send_but_lose_success()
        assert server.rt.db.scalar("SELECT count(*) FROM expenses WHERE description='lost reply supplies'") == 1
        status = await alice.http.get(server.base + "/human/actions/status", headers=alice.headers(),
                                      params={"request_key": envelope["request_key"]})
        assert status.status_code == 200, status.text
        assert status.headers.get("x-request-id")
        assert status.json()["action_receipt"]["id"] == committed_receipt
        retry = await alice.http.post(server.base + "/human/actions/apply", headers=alice.headers(True), json=envelope)
        assert retry.status_code == 200, retry.text
        assert retry.headers.get("x-request-id")
        assert retry.json()["replayed"]
        assert retry.json()["action_receipt"]["id"] == committed_receipt
        for phone, key in [(alice, "never-sent-unknown"), (bob, envelope["request_key"])]:
            unknown = await phone.http.get(server.base + "/human/actions/status", headers=phone.headers(), params={"request_key": key})
            assert unknown.status_code == 200
            assert unknown.headers.get("x-request-id")
            assert unknown.json()["outcome"] == "unknown"
            assert "action_receipt" not in unknown.json()
        assert server.rt.db.scalar("SELECT count(*) FROM expenses WHERE description='lost reply supplies'") == 1
        assert server.rt.db.scalar("SELECT count(*) FROM audit WHERE action='expense.added' AND detail LIKE '%lost reply supplies%'") == 1
        assert server.rt.db.scalar("SELECT count(*) FROM action_receipts WHERE request_key='human-lost-response'") == 1
    finally:
        await alice.http.aclose()
        await bob.http.aclose()


async def test_human_recovery_after_visibility_loss_refuses_saved_result(server):  # noqa: F811
    """F06: knowing the old key cannot read or replay after leaving the group."""
    alice = await Phone(server, "alice").login()
    try:
        engine = server.rt.services.engine
        envelope = {"tool": "costs", "arguments": {"plan": "party", "action": "add", "amount": "4.20", "what": "hidden after leave"},
                    "request_key": "lost-visibility-key", "expected": engine.state_token("party")}
        first = await alice.http.post(server.base + "/human/actions/apply", headers=alice.headers(True), json=envelope)
        assert first.status_code == 200, first.text
        engine.leave_group(engine.identity.get("alice"), "family")
        before = state(engine)
        hidden = await alice.http.get(server.base + "/human/actions/status", headers=alice.headers(),
                                      params={"request_key": envelope["request_key"]})
        assert hidden.status_code in {403, 404}, hidden.text
        assert "hidden after leave" not in hidden.text
        assert "action_receipt" not in hidden.json()
        retry = await alice.http.post(server.base + "/human/actions/apply", headers=alice.headers(True), json=envelope)
        assert retry.status_code in {403, 404}, retry.text
        assert "hidden after leave" not in retry.text
        assert state(engine) == before
    finally:
        await alice.http.aclose()


async def test_human_confirmation_challenge_cannot_authorize_after_member_leaves(server):  # noqa: F811
    """F04/F07: membership is rechecked through the actual human decision route."""
    server.rt.services.on_reset("friends:awaiting")
    bob = await Phone(server, "bob").login()
    try:
        engine = server.rt.services.engine
        pending = next(item for item in (await bob.inbox())["pending"] if item["plan_id"] == "friday_dinner")
        response = await bob.http.post(server.base + "/human/challenges", headers=bob.headers(True),
                                       json={"scope_id": pending["scope_id"]})
        assert response.status_code == 200, response.text
        challenge = response.json()
        engine.leave_group(engine.identity.get("bob"), "friends")
        before = state(engine)
        decision = await bob.http.post(server.base + "/human/decisions", headers=bob.headers(True), json={
            "challenge_id": challenge["challenge_id"], "nonce": challenge["nonce"], "decision": "approved"})
        assert decision.status_code in {400, 403, 404, 409}, decision.text
        assert decision.json()["error"]["code"] in {"NOT_FOUND", "FORBIDDEN", "STALE", "STALE_CANDIDATE"}
        assert state(engine) == before
        assert engine.db.scalar("SELECT used_at FROM challenges WHERE id=?", challenge["challenge_id"]) is None
    finally:
        await bob.http.aclose()
