"""Fresh recovery/transport cases, frozen before implementation review.

All sessions, database files and costs are synthetic. Real loopback HTTP and
MCP exercise the public boundary; separate SQLite connections add contention.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import re
import socket
import threading

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from groupflow.core.db import Db
from groupflow.core.errors import DomainError
from groupflow.core.serve import serve_in_background
from groupflow.core.settings import ServerSettings
from groupflow.engine import GroupFlow
from groupflow.runtime import build_runtime
from tests.conftest import person


class LostReplyBoundary:
    """Delay the first selected successful reply after the engine committed.

    A short real client's read timeout then loses that response. The event
    proves execution finished before the client reports transport failure.
    """
    def __init__(self, app):
        self.app = app
        self.committed = threading.Event()
        self.armed = True

    async def __call__(self, scope, receive, send):
        headers = dict(scope.get("headers", []))
        selected = (scope.get("path") == "/human/actions/apply"
                    and headers.get(b"x-test-lose-reply") == b"yes" and self.armed)

        async def intercept(message):
            if selected and message["type"] == "http.response.start" and message["status"] == 200:
                self.armed = False
                self.committed.set()
                await asyncio.sleep(0.25)
            await send(message)

        await self.app(scope, receive, intercept)


@pytest.fixture
def recovery_server(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_PROVIDER", "none")
    settings = ServerSettings(db_path=str(tmp_path / "independent-recovery.db"), admin_key="synthetic-admin", checkins="off")
    rt = build_runtime(settings, worker_interval_s=0.1)

    @rt.app.api.get("/test/unhandled-error")
    async def fail_for_trace_test():
        raise RuntimeError("isolated synthetic trace failure")

    boundary = LostReplyBoundary(rt.app)
    bg = serve_in_background(boundary)
    bg.rt, bg.boundary = rt, boundary
    yield bg
    bg.stop()
    rt.db.close()


def mcp_for(server, actor="alice", listeners=None):
    headers = {"authorization": f"Bearer {person(actor).token}"}
    if listeners:
        headers["x-groupflow-listeners"] = listeners
    return Client(streamable_http_client(server.base + "/mcp", http_client=httpx2.AsyncClient(headers=headers)), mode="legacy")


async def tool(client, name, arguments):
    result = await client.call_tool(name, arguments)
    if result.is_error:
        return json.loads(result.content[0].text)
    return result.structured_content


async def login(http, actor="alice"):
    response = await http.post("/human/login", json={"member_id": actor, "passcode": person(actor).passcode})
    assert response.status_code == 200, response.text
    data = response.json()
    return {"authorization": "Bearer " + data["token"], "x-gf-csrf": data["csrf"]}


def cost_request(gf, key="independent-key", what="independent-recovery-expense"):
    return {"tool": "costs", "arguments": {"action": "add", "plan": "party", "amount": "17.29", "what": what},
            "request_key": key, "expected": gf.state_token("party")}


def effect_state(gf):
    return {table: gf.db.all(f"SELECT * FROM {table} ORDER BY rowid")
            for table in ("expenses", "action_receipts", "audit", "notifications")}


def dictionaries(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from dictionaries(item)
    elif isinstance(value, list):
        for item in value:
            yield from dictionaries(item)


def assert_saved(status, original, marker):
    assert "error" not in status, status
    assert status.get("outcome") != "unknown", status
    receipt = original["action_receipt"]["id"]
    assert any(item.get("id") == receipt for item in dictionaries(status)), status
    assert marker in json.dumps(status), status


def assert_hidden(status, secret_receipt, marker):
    assert status.get("outcome") == "unknown" or status.get("error", {}).get("code") in {"FORBIDDEN", "NOT_FOUND"}, status
    serialized = json.dumps(status)
    assert secret_receipt not in serialized and marker not in serialized


async def test_unknown_status_over_real_mcp_and_human_http_is_read_only(recovery_server):
    """R01: unknown never claims that the write failed."""
    gf = recovery_server.rt.services.engine
    async with httpx2.AsyncClient(base_url=recovery_server.base) as http:
        auth = await login(http)
        before = effect_state(gf)
        response = await http.get("/human/actions/status", headers=auth, params={"request_key": "never-sent"})
        assert response.status_code == 200, response.text
        assert response.json()["outcome"] == "unknown"
        assert response.json().get("write_failed") is not True
    async with mcp_for(recovery_server) as client:
        result = await tool(client, "action_receipts", {"action": "status", "request_key": "never-sent"})
        assert result.get("outcome") == "unknown", result
        assert result.get("write_failed") is not True
    assert effect_state(gf) == before


async def test_saved_status_and_replay_return_original_effect_over_public_boundaries(recovery_server):
    """R02/R08: result identity is canonical and altered arguments conflict."""
    gf = recovery_server.rt.services.engine
    body = cost_request(gf)
    async with mcp_for(recovery_server) as client:
        original = await tool(client, "action_receipts", {"action": "apply", **body})
        assert "error" not in original, original
        before = effect_state(gf)
        status = await tool(client, "action_receipts", {"action": "status", "request_key": body["request_key"]})
        assert_saved(status, original, body["arguments"]["what"])
        replay = await tool(client, "action_receipts", {"action": "apply", **body})
        assert replay["action_receipt"]["id"] == original["action_receipt"]["id"]
        assert replay.get("replayed") is True
        bad = {**body, "arguments": {**body["arguments"], "amount": "99.99"}}
        rejected = await tool(client, "action_receipts", {"action": "apply", **bad})
        assert rejected.get("error", {}).get("code") == "KEY_REUSED", rejected
        altered_guard = await tool(client, "action_receipts", {"action": "apply", **{**body, "expected": "different-expected-token"}})
        assert altered_guard.get("error", {}).get("code") == "KEY_REUSED", altered_guard
    async with httpx2.AsyncClient(base_url=recovery_server.base) as http:
        auth = await login(http)
        response = await http.get("/human/actions/status", headers=auth, params={"request_key": body["request_key"]})
        assert response.status_code == 200, response.text
        assert_saved(response.json(), original, body["arguments"]["what"])
    assert effect_state(gf) == before
    assert gf.db.scalar("SELECT count(*) FROM expenses WHERE description=?", body["arguments"]["what"]) == 1


async def test_status_does_not_disclose_another_actor_result(recovery_server):
    """R03: knowing another member's key cannot retrieve their saved result."""
    gf = recovery_server.rt.services.engine
    body = cost_request(gf, what="actor-private-recovery-marker")
    async with mcp_for(recovery_server) as alice:
        original = await tool(alice, "action_receipts", {"action": "apply", **body})
    async with mcp_for(recovery_server, "bob") as bob:
        status = await tool(bob, "action_receipts", {"action": "status", "request_key": body["request_key"]})
        assert_hidden(status, original["action_receipt"]["id"], body["arguments"]["what"])
    async with httpx2.AsyncClient(base_url=recovery_server.base) as http:
        bob_auth = await login(http, "bob")
        response = await http.get("/human/actions/status", headers=bob_auth, params={"request_key": body["request_key"]})
        assert response.status_code in {200, 403, 404}
        assert_hidden(response.json(), original["action_receipt"]["id"], body["arguments"]["what"])


async def test_status_and_replay_recheck_current_visibility_and_listener_audience(recovery_server):
    """R04: success in an earlier conversation cannot bypass current access."""
    gf = recovery_server.rt.services.engine
    body = cost_request(gf, what="scope-sensitive-marker")
    async with mcp_for(recovery_server) as alice:
        original = await tool(alice, "action_receipts", {"action": "apply", **body})
    async with mcp_for(recovery_server, listeners="alice,linda") as narrowed:
        status = await tool(narrowed, "action_receipts", {"action": "status", "request_key": body["request_key"]})
        assert_hidden(status, original["action_receipt"]["id"], body["arguments"]["what"])
        replay = await tool(narrowed, "action_receipts", {"action": "apply", **body})
        assert_hidden(replay, original["action_receipt"]["id"], body["arguments"]["what"])
    async with httpx2.AsyncClient(base_url=recovery_server.base) as http:
        auth = await login(http)
        gf.leave_group(gf.identity.get("alice"), "family")
        before = effect_state(gf)
        status = await http.get("/human/actions/status", headers=auth, params={"request_key": body["request_key"]})
        assert_hidden(status.json(), original["action_receipt"]["id"], body["arguments"]["what"])
        replay = await http.post("/human/actions/apply", headers=auth, json=body)
        assert_hidden(replay.json(), original["action_receipt"]["id"], body["arguments"]["what"])
    assert effect_state(gf) == before


async def test_lost_http_reply_after_commit_resolves_through_status_and_same_key(recovery_server):
    """R05/H03: client timeout is uncertain; committed state remains recoverable."""
    gf = recovery_server.rt.services.engine
    body = cost_request(gf, key="lost-reply", what="lost-reply-marker")
    async with httpx2.AsyncClient(base_url=recovery_server.base) as http:
        auth = await login(http)
        with pytest.raises(httpx2.ReadTimeout):
            await http.post("/human/actions/apply", headers={**auth, "x-test-lose-reply": "yes"}, json=body, timeout=0.08)
        assert recovery_server.boundary.committed.is_set(), "fault must occur after committed execution"
        saved = (await http.get("/human/actions/status", headers=auth, params={"request_key": body["request_key"]})).json()
        replay_response = await http.post("/human/actions/apply", headers=auth, json=body)
        assert replay_response.status_code == 200, replay_response.text
        replay = replay_response.json()
        assert replay.get("replayed") is True
        assert_saved(saved, replay, body["arguments"]["what"])
        assert (await http.get("/healthz")).status_code == 200
    assert gf.db.scalar("SELECT count(*) FROM expenses WHERE description='lost-reply-marker'") == 1
    assert gf.db.scalar("SELECT count(*) FROM action_receipts WHERE actor_id='alice' AND request_key='lost-reply'") == 1


@pytest.mark.parametrize("same_key", [True, False])
async def test_concurrent_human_http_requests_are_atomic(recovery_server, same_key):
    """R06/R07: exact retries converge, competing expected versions do not."""
    gf = recovery_server.rt.services.engine
    body = cost_request(gf, key="race-one", what="http-race-marker")
    second = body if same_key else {**body, "request_key": "race-two"}
    async with httpx2.AsyncClient(base_url=recovery_server.base) as http:
        auth = await login(http)
        one, two = await asyncio.gather(
            http.post("/human/actions/apply", headers=auth, json=body),
            http.post("/human/actions/apply", headers=auth, json=second))
    if same_key:
        assert [one.status_code, two.status_code] == [200, 200], (one.text, two.text)
        assert one.json()["action_receipt"]["id"] == two.json()["action_receipt"]["id"]
        assert sum(bool(r.json().get("replayed")) for r in (one, two)) == 1
    else:
        assert sorted([one.status_code, two.status_code]) == [200, 409], (one.text, two.text)
        rejected = next(r.json() for r in (one, two) if r.status_code == 409)
        assert rejected["error"]["code"] == "STALE"
    assert gf.db.scalar("SELECT count(*) FROM expenses WHERE description='http-race-marker'") == 1


@pytest.mark.parametrize("same_key", [True, False])
def test_separate_database_connections_contend_atomically(recovery_server, same_key):
    """R06/R07: independent connections require SQLite atomicity too."""
    gf = recovery_server.rt.services.engine
    engines = [GroupFlow(Db(gf.db.path)), GroupFlow(Db(gf.db.path))]
    body = cost_request(gf, key="sqlite-one", what="sqlite-race-marker")
    barrier = threading.Barrier(2)

    def perform(index):
        barrier.wait(timeout=5)
        try:
            return engines[index].apply_action("alice", body["tool"], body["arguments"],
                                               body["request_key"] if same_key or index == 0 else "sqlite-two", body["expected"])
        except DomainError as exc:
            return {"error": exc.to_json()}

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(perform, i) for i in range(2)]
            results = [future.result(timeout=10) for future in futures]
        if same_key:
            assert all("error" not in result for result in results), results
            assert results[0]["action_receipt"]["id"] == results[1]["action_receipt"]["id"]
        else:
            assert sum("error" not in result for result in results) == 1, results
            assert next(result["error"]["code"] for result in results if "error" in result) == "STALE"
        assert gf.db.scalar("SELECT count(*) FROM expenses WHERE description='sqlite-race-marker'") == 1
    finally:
        for engine in engines:
            engine.db.close()


async def test_http_trace_ids_cover_success_auth_validation_missing_and_unhandled_errors(recovery_server, caplog):
    """H01/H02: each response is traceable without logging credentials/body."""
    caplog.set_level(logging.INFO)
    credential_marker = "synthetic-credential-marker"
    body_marker = "synthetic-body-marker"
    async with httpx2.AsyncClient(base_url=recovery_server.base) as http:
        responses = [
            await http.get("/healthz", headers={"x-request-id": "X" * 4096}),
            await http.get("/human/actions/status", headers={"authorization": "Bearer " + credential_marker}, params={"request_key": "unknown"}),
            await http.post("/human/login", json={"member_id": "alice", "passcode": body_marker}),
            await http.post("/human/login", content="{broken-json", headers={"content-type": "application/json"}),
            await http.get("/test/missing"),
            await http.get("/test/unhandled-error"),
        ]
    assert [response.status_code for response in responses] == [200, 401, 401, 422, 404, 500]
    ids = [response.headers.get("x-request-id", "") for response in responses]
    assert len(set(ids)) == len(ids)
    assert all(re.fullmatch(r"[A-Za-z0-9-]{1,128}", request_id) for request_id in ids), ids
    assert credential_marker not in caplog.text and body_marker not in caplog.text
    assert "X" * 256 not in caplog.text
    for request_id in ids:
        assert request_id in caplog.text, caplog.text
    for status in (200, 401, 422, 404, 500):
        assert str(status) in caplog.text


async def test_malformed_body_and_abandoned_socket_do_not_poison_transport(recovery_server):
    """H03: truncated callers cannot close the server's listening socket."""
    connection = socket.create_connection((recovery_server.host, recovery_server.port), timeout=3)
    connection.sendall(b"POST /human/login HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nContent-Length: 200\r\n\r\n{\"member_id\":")
    connection.close()
    async with httpx2.AsyncClient(base_url=recovery_server.base) as http:
        auth = await login(http)
        malformed = await http.post("/human/actions/apply", headers={**auth, "content-type": "application/json"}, content="{")
        assert malformed.status_code == 422
        assert malformed.headers.get("x-request-id")
        status = await http.get("/human/actions/status", headers=auth, params={"request_key": "abandoned-request"})
        assert status.status_code == 200 and status.json()["outcome"] == "unknown"
        assert (await http.get("/healthz")).json()["ok"] is True


@pytest.mark.parametrize("day,start,end,folds", [
    ("2026-03-08", "02:30", "03:30", {}),
    ("2026-11-01", "01:15", "02:30", {}),
    ("2026-11-01", "00:30", "01:30", {}),
    ("2026-11-01", "01:45", "01:15", {"start_fold": 1, "end_fold": 0}),
])
async def test_real_mcp_entry_rejects_invalid_dst_without_publishing(recovery_server, day, start, end, folds):
    """T01-T03/T05: MCP schema and dispatch must retain strict validation."""
    gf = recovery_server.rt.services.engine
    plan = gf.create_plan("alice", "general_event", "MCP clock boundary", group="friends",
                          audience=["alice"], start_date=day, days=2, starter=False).plan.id
    before = gf.db.scalar("SELECT count(*) FROM items WHERE plan_id=?", plan)
    async with mcp_for(recovery_server) as client:
        response = await tool(client, "propose_change", {"plan": plan, "new_type": "activity", "title": "Clock check",
                                                         "start": start, "end": end, **folds})
    assert response.get("error", {}).get("code") == "INVALID_EVENT_TIME", response
    assert gf.db.scalar("SELECT count(*) FROM items WHERE plan_id=?", plan) == before


async def test_real_mcp_fold_and_cross_midnight_fields_survive_schema(recovery_server):
    """T04/T06/T07: the public tool must expose the disambiguation fields."""
    gf = recovery_server.rt.services.engine
    repeated = gf.create_plan("alice", "general_event", "MCP repeated hour", group="friends",
                              audience=["alice"], start_date="2026-11-01", days=2, starter=False).plan.id
    overnight = gf.create_plan("alice", "general_event", "MCP overnight", group="friends",
                               audience=["alice"], start_date="2026-03-07", days=2, starter=False).plan.id
    async with mcp_for(recovery_server) as client:
        folded = await tool(client, "propose_change", {"plan": repeated, "new_type": "activity", "title": "Folded interval",
                                                       "start": "01:45", "end": "01:15", "start_fold": 0, "end_fold": 1})
        assert folded.get("outcome") == "draft_updated", folded
        crossed = await tool(client, "propose_change", {"plan": overnight, "new_type": "activity", "title": "Overnight interval",
                                                        "day": "2026-03-07", "end_day": "2026-03-08", "start": "23:30", "end": "01:30"})
        assert crossed.get("outcome") == "draft_updated", crossed
    act = gf._snapshot(gf._working_rev(gf._plan_row(repeated))).activities[0]
    assert (act.start_fold, act.end_fold) == (0, 1)
    act = gf._snapshot(gf._working_rev(gf._plan_row(overnight))).activities[0]
    assert (act.start, act.end) == (1410, 1530)


async def test_real_human_confirmation_blocks_invalid_persisted_time(recovery_server):
    """T10: human confirmation independently rechecks a legacy bad snapshot."""
    gf = recovery_server.rt.services.engine
    plan = gf.create_plan("alice", "general_event", "Human clock boundary", group="friends",
                          audience=["alice"], start_date="2026-11-01", days=2, starter=False).plan.id
    gf.propose_change("alice", plan=plan, new_type="activity", title="Clock check", start="00:30", end="02:30")
    async with mcp_for(recovery_server) as client:
        assert (await tool(client, "request_confirmations", {"plan": plan}))["outcome"] == "requested"
    async with httpx2.AsyncClient(base_url=recovery_server.base) as http:
        auth = await login(http)
        inbox = (await http.get("/human/inbox", headers=auth)).json()
        pending = next(item for item in inbox["pending"] if item["plan_id"] == plan)
        challenge = (await http.post("/human/challenges", headers=auth, json={"scope_id": pending["scope_id"]})).json()
        revision = gf._plan_row(plan)["candidate_revision_id"]
        raw = json.loads(gf.db.scalar("SELECT snapshot_json FROM plan_revisions WHERE id=?", revision))
        raw["activities"][0].update(start=75, end=150)
        gf.db.run("UPDATE plan_revisions SET snapshot_json=? WHERE id=?", json.dumps(raw), revision)
        before = gf.db.scalar("SELECT count(*) FROM approval_receipts")
        answer = await http.post("/human/decisions", headers=auth, json={
            "challenge_id": challenge["challenge_id"], "nonce": challenge["nonce"], "decision": "approved"})
        assert answer.status_code in {400, 409}, answer.text
        assert answer.json()["error"]["code"] in {"INVALID_EVENT_TIME", "INFEASIBLE", "STALE_CANDIDATE"}
    assert gf.db.scalar("SELECT count(*) FROM approval_receipts") == before


async def test_mcp_http_success_and_auth_error_also_carry_trace_ids(recovery_server):
    """H01/T13: HTTP tracing must also cover the MCP transport branch."""
    request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "independent-recovery", "version": "1"}}}
    headers = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
    async with httpx2.AsyncClient(base_url=recovery_server.base) as http:
        denied = await http.post("/mcp", headers=headers, json=request)
        allowed = await http.post("/mcp", headers={**headers, "authorization": "Bearer " + person("alice").token}, json=request)
        assert denied.status_code == 401 and allowed.status_code == 200
        for response in (denied, allowed):
            assert re.fullmatch(r"[A-Za-z0-9-]{1,128}", response.headers.get("x-request-id", "")), response.headers
        if session := allowed.headers.get("mcp-session-id"):
            await http.delete("/mcp", headers={"authorization": "Bearer " + person("alice").token, "mcp-session-id": session})
