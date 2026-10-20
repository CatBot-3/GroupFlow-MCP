"""Independent seeded mixed workloads over one real MCP/HTTP server + database.

The behavior contract was frozen in randomized/FROZEN_CASES.md before inspecting
the server implementation. This module owns its oracle and evidence; the common
world/helper only transports calls and creates synthetic records.
"""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import statistics
import threading
import time
import uuid

import pytest

from tests.shared_workload import (
    MARKERS, call, connect, guard, phone, run_wave, shared_server,
)


from tests.evidence import evidence_dir

ARTIFACTS = evidence_dir("2026-10-18-shared-workload/randomized")
SEEDS = (1847, 2903, 6011)
# These stop hangs in a synthetic local run. They are not production SLAs.
WAVE_DEADLINE_S = 25
PROGRESS_DEADLINE_S = 8
SESSION_SPECS = {
    "avery": ("avery", None),
    "avery_dinner": ("avery", "blair"),
    "blair": ("blair", None),
    "blair_trip": ("blair", "avery"),
    "casey": ("casey", None),
    "devon": ("devon", None),
    "ellis": ("ellis", None),
    "quinn": ("quinn", None),
}
VISIBILITY = {
    "avery": {"dinner", "trip", "release"},
    "avery_dinner": {"dinner", "trip", "release"},
    "blair": {"dinner", "trip", "surprise", "release"},
    "blair_trip": {"dinner", "trip", "release"},
    "casey": {"dinner", "surprise"},
    "devon": {"trip", "release"},
    "ellis": {"surprise", "release"},
    "quinn": set(),
}


def _safe(value):
    """Keep replay evidence without writing synthetic authentication secrets."""
    if isinstance(value, dict):
        return {k: ("[redacted]" if k.lower() in {"token", "csrf", "passcode", "nonce", "authorization"}
                    else _safe(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    return value


def _classification(value):
    if isinstance(value, dict) and "error" in value:
        error = value["error"]
        code = error.get("code", "UNKNOWN") if isinstance(error, dict) else str(error)
        upper = (code + " " + str(error)).upper()
        if "LOCK" in upper or "SQLITE_BUSY" in upper:
            return "lock_failure:" + code
        if "RATE" in upper:
            return "rate_limit:" + code
        if code in {"INTERNAL", "INTERNAL_ERROR"}:
            return "internal_fault:" + code
        return "domain_rejection:" + code
    return "completed"


def _exception_leaves(exc):
    nested = getattr(exc, "exceptions", None)
    if nested:
        return [leaf for child in nested for leaf in _exception_leaves(child)]
    return [{"type": type(exc).__name__, "message": str(exc)}]


class Evidence:
    def __init__(self, seed, case, server):
        self.seed = seed
        self.case = case
        self.origin = time.perf_counter()
        self.requests = []
        self.waves = []
        self.checkpoints = []
        self.error = None
        self.extra = {}
        self.server_identity = {"base": server.base, "db_path": str(server.db_path)}

    async def request(self, session, tool, arguments, awaitable):
        row = {"session": session, "actor": SESSION_SPECS.get(session, (session,))[0],
               "tool": tool, "arguments": _safe(arguments),
               "started_s": time.perf_counter() - self.origin}
        self.requests.append(row)
        try:
            result = await awaitable
            if hasattr(result, "status_code"):
                body = result.json()
                row["http_status"] = result.status_code
            else:
                body = result
            row["result"] = _safe(body)
            row["classification"] = _classification(body)
            if session in VISIBILITY:
                response_text = json.dumps(body, default=str)
                for plan, marker in MARKERS.items():
                    if plan not in VISIBILITY[session]:
                        assert marker not in response_text, (session, plan, body)
            return result
        except BaseException as exc:
            row["classification"] = "transport_or_cancelled:" + type(exc).__name__
            row["exception"] = str(exc)
            raise
        finally:
            row["finished_s"] = time.perf_counter() - self.origin
            row["duration_ms"] = (row["finished_s"] - row["started_s"]) * 1000

    async def mcp(self, sessions, session, tool, args):
        return await self.request(session, tool, args, call(sessions[session], tool, args))

    async def guarded(self, sessions, session, tool, args, key, expected):
        body = {"action": "apply", "tool": tool, "arguments": args, "request_key": key, "expected": expected}
        return await self.request(session, "action_receipts", body,
                                  guard(sessions[session], tool, args, key, expected))

    async def wave(self, label, actions):
        self.waves.append({"label": label, "barrier_released_names": list(actions),
                           "mechanism": "common asyncio Event plus gather; client intervals are recorded separately"})
        return await asyncio.wait_for(run_wave(actions), timeout=WAVE_DEADLINE_S)

    def save(self):
        durations = sorted(r["duration_ms"] for r in self.requests if "duration_ms" in r)
        def maximum_overlap(rows):
            edges = [(r["started_s"], 1) for r in rows]
            edges += [(r["finished_s"], -1) for r in rows if "finished_s" in r]
            concurrent = maximum = 0
            for _, delta in sorted(edges, key=lambda pair: (pair[0], pair[1])):
                concurrent += delta
                maximum = max(maximum, concurrent)
            return maximum
        read_tools = {"get_context", "resolve_action", "/human/inbox", "/human/projects/inbox"}
        counts = {}
        for row in self.requests:
            category = row.get("classification", "unfinished")
            counts[category] = counts.get(category, 0) + 1
        summary = {
            "request_count": len(self.requests),
            "request_count_scope": "Measured MCP tool calls and human operation HTTP requests; excludes setup/login/handshake/teardown.",
            "completed_response_count": sum("result" in row for row in self.requests),
            "classification_counts": counts,
            "p50_ms": statistics.median(durations) if durations else None,
            "p95_ms": durations[max(0, math.ceil(len(durations) * .95) - 1)] if durations else None,
            "max_ms": max(durations) if durations else None,
            "max_overlapping_client_intervals": maximum_overlap(self.requests),
            "max_overlapping_write_client_intervals": maximum_overlap([r for r in self.requests if r["tool"] not in read_tools]),
            "overlap_claim": "Client waiting intervals overlap; this does not prove simultaneous server execution.",
            "production_sla": False,
            "deadline_s": WAVE_DEADLINE_S,
            "rate_limits": "Normal production limits retained throughout measured work.",
        }
        payload = {"case": self.case, "seed": self.seed, "sessions": SESSION_SPECS,
                   "server": self.server_identity, "summary": summary, "waves": self.waves,
                   "checkpoints": self.checkpoints, "requests": self.requests,
                   "extra": self.extra, "first_error": self.error}
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = ARTIFACTS / f"{self.case}-seed-{self.seed}-{stamp}-{uuid.uuid4().hex[:6]}.json"
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        if self.error:
            try:
                with (ARTIFACTS / "first_failure.json").open("x", encoding="utf-8") as handle:
                    json.dump({"artifact": path.name, **payload}, handle, indent=2, default=str)
            except FileExistsError:
                pass


@asynccontextmanager
async def measured(seed, case, server):
    evidence = Evidence(seed, case, server)
    try:
        yield evidence
    except BaseException as exc:
        evidence.error = {"type": type(exc).__name__, "message": str(exc), "leaves": _exception_leaves(exc)}
        raise
    finally:
        evidence.save()


@asynccontextmanager
async def eight_sessions(server):
    async with AsyncExitStack() as stack:
        sessions = {}
        for label, (actor, listeners) in SESSION_SPECS.items():
            sessions[label] = await stack.enter_async_context(connect(server, actor, listeners))
        assert len(sessions) == 8
        yield sessions


def _item(server, plan, kind="task"):
    return server.items[plan][kind]


def _snapshot(server):
    """Read saved domain rows, without deriving expectations from engine logic."""
    tables = ("items", "facts", "plans", "requests", "plan_revisions", "confirmation_rounds",
              "approval_receipts", "project_config", "project_work", "project_receipts",
              "expenses", "settlements", "bring_items")
    return {table: server.engine.db.all(f"SELECT * FROM {table} ORDER BY rowid") for table in tables}


def _success(value):
    assert "error" not in value, value
    return value


def _denied(value, allowed):
    assert value.get("error", {}).get("code") in allowed, value


def _receipt(server, value, actor, plan):
    _success(value)
    receipt = value["action_receipt"]
    assert receipt["actor"] == actor.title(), receipt
    assert receipt["plans"] == [plan], receipt
    saved = server.engine.db.get("SELECT * FROM action_receipts WHERE id=?", receipt["id"])
    assert saved and saved["actor_id"] == actor
    assert json.loads(saved["plan_ids_json"]) == [plan]
    return receipt


async def _token(evidence, sessions, session, plan, query="Prepare"):
    resolved = _success(await evidence.mcp(sessions, session, "resolve_action", {"plan": plan, "query": query}))
    matching = [row for row in resolved["targets"] if row["plan"] == plan]
    assert matching, resolved
    return matching[0]["version"]


def _consent_integrity(server):
    db = server.engine.db
    wrong = db.all("SELECT a.id FROM approval_receipts a JOIN approval_scopes s ON s.id=a.scope_id "
                   "JOIN plan_revisions r ON r.id=a.revision_id WHERE a.member_id<>s.member_id "
                   "OR a.revision_id<>s.revision_id OR a.plan_id<>r.plan_id OR a.scope_digest<>s.scope_digest")
    assert not wrong, wrong
    wrong_project = db.all("SELECT a.id FROM project_receipts a JOIN project_scopes s ON s.id=a.scope_id "
                           "WHERE a.member_id<>s.member_id OR a.digest<>s.digest OR a.fingerprint<>s.fingerprint")
    assert not wrong_project, wrong_project
    wrong_challenge = db.all("SELECT a.id FROM approval_receipts a JOIN challenges c ON c.id=a.challenge_id "
                             "WHERE a.member_id<>c.member_id OR a.scope_id<>c.scope_id "
                             "OR a.revision_id<>c.revision_id OR a.scope_digest<>c.scope_digest "
                             "OR a.candidate_digest<>c.candidate_digest")
    assert not wrong_challenge, wrong_challenge


def _checkpoint(server, evidence, label):
    _consent_integrity(server)
    # These synthetic schedules deliberately use unique labels for unique
    # logical intents. A retried key must not produce a second matching row.
    duplicate_expenses = server.engine.db.all(
        "SELECT plan_id,description,count(*) AS n FROM expenses GROUP BY plan_id,description HAVING count(*)>1")
    duplicate_claims = server.engine.db.all(
        "SELECT plan_id,label,count(*) AS n FROM bring_items WHERE status='open' GROUP BY plan_id,label HAVING count(*)>1")
    assert not duplicate_expenses, duplicate_expenses
    assert not duplicate_claims, duplicate_claims
    evidence.checkpoints.append({"label": label,
                                "expense_count": server.engine.db.scalar("SELECT count(*) FROM expenses"),
                                "bring_count": server.engine.db.scalar("SELECT count(*) FROM bring_items"),
                                "social_approval_count": server.engine.db.scalar("SELECT count(*) FROM approval_receipts"),
                                "project_approval_count": server.engine.db.scalar("SELECT count(*) FROM project_receipts")})


async def _visibility(evidence, sessions):
    actions = {name: evidence.mcp(sessions, name, "get_context", {"mark_read": False}) for name in sessions}
    values = await evidence.wave("visibility-all-eight", actions)
    for name, value in values.items():
        _success(value)
        public = json.dumps(value)
        for plan, marker in MARKERS.items():
            if plan not in VISIBILITY[name]:
                assert marker not in public, (name, plan, value)
    return values


@pytest.mark.parametrize("seed", SEEDS)
async def test_seeded_shared_mixed_actions_preserve_intended_records(shared_server, seed):
    """Generate independent per-plan edits, then race guarded retries/conflicts."""
    server = shared_server
    rng = random.Random(seed)
    async with measured(seed, "mixed-actions", server) as evidence, eight_sessions(server) as sessions:
        await _visibility(evidence, sessions)
        originals = {plan: server.engine.db.get("SELECT * FROM items WHERE id=?", _item(server, plan))
                     for plan in ("dinner", "trip", "surprise")}
        expected_due = {}
        for wave in range(3):
            choices = ["dinner", "trip", "surprise", "release"]
            rng.shuffle(choices)
            actors = {"dinner": "avery", "trip": "blair", "surprise": "casey", "release": "devon"}
            actions = {}
            submitted = {}
            for plan in choices:
                actor = actors[plan]
                if plan == "release":
                    minutes = rng.choice([30, 45, 60, 75, 90])
                    args = {"plan": plan, "item": _item(server, plan, "work_a"),
                            "spec": {"duration_minutes": minutes, "priority": rng.randrange(1, 9)}}
                    tool = "upsert_work"
                else:
                    due = f"seed-{seed}-wave-{wave}-{rng.randrange(1000, 10000)}"
                    args = {"plan": plan, "item": _item(server, plan), "due": due,
                            "quote": f"{plan} synthetic intent {seed}/{wave}"}
                    expected_due[plan] = due
                    tool = "propose_change"
                submitted[plan] = (actor, tool, args)
                actions[plan] = evidence.mcp(sessions, actor, tool, args)
            values = await evidence.wave(f"generated-independent-{wave}", actions)
            for plan, (actor, tool, args) in submitted.items():
                _receipt(server, values[plan], actor, plan)
                if plan == "release":
                    saved = server.engine.db.get("SELECT * FROM project_work WHERE item_id=?", args["item"])
                    spec = json.loads(saved["spec_json"])
                    assert spec["duration_minutes"] == args["spec"]["duration_minutes"]
                    assert spec["priority"] == args["spec"]["priority"]
                else:
                    saved = server.engine.db.get("SELECT * FROM items WHERE id=?", args["item"])
                    assert saved["plan_id"] == plan
                    assert saved["owner_id"] == originals[plan]["owner_id"]
                    assert saved["due"] == expected_due[plan]
            _checkpoint(server, evidence, f"generated-independent-{wave}")

        # Denied operations contend with valid edits to unrelated plans.
        hidden_before = server.engine.db.get("SELECT * FROM items WHERE id=?", _item(server, "surprise"))
        trip_due, surprise_due = f"trip-progress-{seed}", f"surprise-progress-{seed}"
        values = await evidence.wave("denied-and-unrelated-progress", {
            "hidden_surprise": evidence.mcp(sessions, "quinn", "propose_change",
                                           {"plan": "surprise", "item": _item(server, "surprise"), "due": "FORBIDDEN-QUINN"}),
            "trip_progress": evidence.mcp(sessions, "blair", "propose_change",
                                         {"plan": "trip", "item": _item(server, "trip"), "due": trip_due}),
            "surprise_progress": evidence.mcp(sessions, "casey", "propose_change",
                                             {"plan": "surprise", "item": _item(server, "surprise"), "due": surprise_due}),
            "self_approver": evidence.mcp(sessions, "avery", "configure_project",
                                         {"plan": "release", "changes": {"approvers": ["avery"]}}),
        })
        _denied(values["hidden_surprise"], {"NOT_FOUND", "FORBIDDEN"})
        _denied(values["self_approver"], {"FORBIDDEN"})
        assert MARKERS["surprise"] not in json.dumps(values["hidden_surprise"])
        _receipt(server, values["trip_progress"], "blair", "trip")
        _receipt(server, values["surprise_progress"], "casey", "surprise")
        assert server.engine.db.scalar("SELECT due FROM items WHERE id=?", _item(server, "trip")) == trip_due
        after_surprise = server.engine.db.get("SELECT * FROM items WHERE id=?", _item(server, "surprise"))
        assert after_surprise["due"] == surprise_due
        assert after_surprise["owner_id"] == hidden_before["owner_id"]
        cfg = json.loads(server.engine.db.scalar("SELECT config_json FROM project_config WHERE plan_id='release'"))
        assert "avery" not in cfg.get("approvers", [])
        assert server.engine.db.scalar("SELECT count(*) FROM requests WHERE requested_by='quinn'") == 0
        _checkpoint(server, evidence, "denied-and-unrelated-progress")

        # Same audience and key: one logical expense even with overlapping calls.
        token = await _token(evidence, sessions, "blair", "trip")
        description = f"expense-samekey-{seed}"
        expense_args = {"plan": "trip", "action": "add", "amount": "13.17", "what": description}
        key = f"samekey-{seed}"
        values = await evidence.wave("duplicate-expense-key", {
            label: evidence.guarded(sessions, "blair", "costs", expense_args, key, token)
            for label in rng.sample(["retry-first", "retry-second"], 2)
        })
        receipts = [_receipt(server, result, "blair", "trip") for result in values.values()]
        assert len({row["id"] for row in receipts}) == 1, values
        assert sum(bool(row.get("replayed")) for row in values.values()) == 1, values
        expenses = server.engine.db.all("SELECT * FROM expenses WHERE description=?", description)
        assert len(expenses) == 1
        assert expenses[0]["plan_id"] == "trip" and expenses[0]["payer_id"] == "blair"
        assert expenses[0]["created_by"] == "blair" and expenses[0]["amount_minor"] == 1317
        assert server.engine.db.scalar("SELECT count(*) FROM action_receipts WHERE actor_id='blair' AND request_key=?", key) == 1
        _checkpoint(server, evidence, "duplicate-expense-key")

        before = _snapshot(server)
        crossed = await evidence.wave("cross-plan-and-audience-key-replay", {
            "cross_plan": evidence.guarded(sessions, "blair", "costs", {**expense_args, "plan": "dinner"}, key,
                                             await _token(evidence, sessions, "blair", "dinner")),
            "cross_audience": evidence.guarded(sessions, "blair_trip", "costs", expense_args, key, token),
        })
        _denied(crossed["cross_plan"], {"KEY_REUSED"})
        _denied(crossed["cross_audience"], {"FORBIDDEN"})
        assert _snapshot(server) == before
        _checkpoint(server, evidence, "cross-plan-and-audience-key-replay")

        # Different keys, incompatible expense intents, same observed token.
        token = await _token(evidence, sessions, "blair", "trip")
        arguments = {label: {"plan": "trip", "action": "add", "amount": amount,
                             "what": f"conflict-{label}-{seed}"}
                     for label, amount in [("left", "7.03"), ("right", "8.09")]}
        order = ["left", "right"]
        rng.shuffle(order)
        conflicts = await evidence.wave("conflicting-expected-token", {
            label: evidence.guarded(sessions, "blair", "costs", arguments[label], f"conflict-{label}-{seed}", token)
            for label in order
        })
        won = [label for label, value in conflicts.items() if "error" not in value]
        lost = [label for label, value in conflicts.items() if "error" in value]
        assert len(won) == len(lost) == 1, conflicts
        _receipt(server, conflicts[won[0]], "blair", "trip")
        _denied(conflicts[lost[0]], {"STALE"})
        for label, args in arguments.items():
            rows = server.engine.db.all("SELECT * FROM expenses WHERE description=?", args["what"])
            assert len(rows) == int(label in won), (label, rows)
        replay = await evidence.guarded(sessions, "blair", "costs", arguments[won[0]], f"conflict-{won[0]}-{seed}", token)
        assert replay.get("replayed") is True
        assert replay["action_receipt"]["id"] == conflicts[won[0]]["action_receipt"]["id"]
        _checkpoint(server, evidence, "conflicting-expected-token")

        # Claim replay has one saved assignment and one receipt, never a duplicate row.
        claim = f"claim-{seed}"
        token = await _token(evidence, sessions, "avery", "trip")
        args = {"plan": "trip", "action": "bring", "things": [claim]}
        claimed = await evidence.wave("duplicate-claim-key", {
            label: evidence.guarded(sessions, "avery", "bring_list", args, f"claim-key-{seed}", token)
            for label in ("claim-one", "claim-two")
        })
        claim_receipts = [_receipt(server, value, "avery", "trip") for value in claimed.values()]
        assert len({row["id"] for row in claim_receipts}) == 1
        rows = server.engine.db.all("SELECT * FROM bring_items WHERE label=?", claim)
        assert len(rows) == 1 and rows[0]["plan_id"] == "trip" and rows[0]["claimed_by"] == "avery"
        _checkpoint(server, evidence, "duplicate-claim-key")
        await _visibility(evidence, sessions)


async def test_plan_organizer_rights_do_not_follow_group_role(shared_server):
    server = shared_server
    async with measured(1847, "plan-specific-organizer", server) as evidence, eight_sessions(server) as sessions:
        before = _snapshot(server)
        result = await evidence.mcp(sessions, "avery", "request_confirmations", {"plan": "trip"})
        after = _snapshot(server)
        evidence.extra["changed_domain_tables"] = [table for table in before if before[table] != after[table]]
        _denied(result, {"FORBIDDEN"})
        assert after == before, evidence.extra
        _checkpoint(server, evidence, "wrong-plan-organizer-rejected")


async def test_mixed_human_consent_rechecks_person_plan_and_current_work(shared_server):
    """Human answers race unrelated work; stale and borrowed tokens never commit."""
    server = shared_server
    rng = random.Random(2903)
    async with measured(2903, "mixed-human-consent", server) as evidence, eight_sessions(server) as sessions:
        async with AsyncExitStack() as stack:
            phones = {actor: await stack.enter_async_context(phone(server, actor))
                      for actor in ("avery", "blair", "casey", "ellis")}

            async def post(actor, path, body):
                return await evidence.request(actor, path, body, phones[actor].post(path, body))

            async def inbox(actor, project=False):
                path = "/human/projects/inbox" if project else "/human/inbox"
                response = await evidence.request(actor, path, {}, phones[actor].http.get(path, headers=phones[actor].headers))
                assert response.status_code == 200, response.text
                return response.json()

            async def approve_own(actor):
                approved = []
                social = await inbox(actor)
                for item in social["pending"]:
                    if item["plan_id"] != "dinner":
                        continue
                    challenge = await post(actor, "/human/challenges", {"scope_id": item["scope_id"]})
                    assert challenge.status_code == 200, challenge.text
                    body = challenge.json()
                    answer = await post(actor, "/human/decisions", {"challenge_id": body["challenge_id"],
                                        "nonce": body["nonce"], "decision": "approved"})
                    assert answer.status_code == 200, answer.text
                    approved.append(("dinner", item["scope_id"]))
                project = await inbox(actor, True)
                for item in project["items"]:
                    if item["plan_id"] != "release" or item["status"] in ("approved", "carried_forward"):
                        continue
                    challenge = await post(actor, "/human/projects/challenges", {"scope_id": item["id"]})
                    assert challenge.status_code == 200, challenge.text
                    body = challenge.json()
                    answer = await post(actor, "/human/projects/decisions", {"challenge_id": body["challenge_id"],
                                        "nonce": body["nonce"], "decision": "approve"})
                    assert answer.status_code == 200, answer.text
                    approved.append(("release", item["id"]))
                return approved

            # Four real workflows begin together while eight MCP sessions remain open.
            operations = {
                "dinner": evidence.mcp(sessions, "avery", "propose_change",
                                       {"plan": "dinner", "item": _item(server, "dinner", "activity"), "place": "harbour_b"}),
                "trip": evidence.mcp(sessions, "blair", "costs",
                                     {"plan": "trip", "action": "add", "amount": "4.27", "what": "consent-wave-trip-expense"}),
                "surprise": evidence.mcp(sessions, "casey", "propose_change",
                                         {"plan": "surprise", "item": _item(server, "surprise"), "due": "private-consent-wave"}),
                "release": evidence.mcp(sessions, "devon", "repair_project", {"plan": "release"}),
            }
            shuffled = {key: operations[key] for key in rng.sample(list(operations), len(operations))}
            first = await evidence.wave("four-plan-draft-and-repair", shuffled)
            for plan, value in first.items():
                _receipt(server, value, {"dinner": "avery", "trip": "blair", "surprise": "casey", "release": "devon"}[plan], plan)
            requested = await evidence.wave("two-plan-confirmation-requests", {
                "dinner": evidence.mcp(sessions, "avery", "request_confirmations", {"plan": "dinner"}),
                "release": evidence.mcp(sessions, "devon", "request_confirmations", {"plan": "release"}),
            })
            assert all(_success(value)["outcome"] == "requested" for value in requested.values()), requested
            _checkpoint(server, evidence, "two-plan-confirmation-requests")

            blair_pending = next(item for item in (await inbox("blair"))["pending"] if item["plan_id"] == "dinner")
            challenge = await post("blair", "/human/challenges", {"scope_id": blair_pending["scope_id"]})
            assert challenge.status_code == 200, challenge.text
            borrowed = challenge.json()
            baseline_approvals = server.engine.db.scalar("SELECT count(*) FROM approval_receipts")
            release_token = await _token(evidence, sessions, "devon", "release")
            rejected = await evidence.wave("wrong-person-and-cross-plan-token", {
                "wrong_person": post("avery", "/human/decisions", {"challenge_id": borrowed["challenge_id"],
                                      "nonce": borrowed["nonce"], "decision": "approved"}),
                "cross_plan_expected": evidence.guarded(sessions, "avery", "commit_plan", {"plan": "dinner"},
                                                         "wrong-plan-consent-token", release_token),
                "dinner_without_answers": evidence.mcp(sessions, "avery", "commit_plan", {"plan": "dinner"}),
                "release_without_answers": evidence.mcp(sessions, "devon", "commit_plan", {"plan": "release"}),
            })
            assert rejected["wrong_person"].status_code == 403, rejected["wrong_person"].text
            _denied(rejected["cross_plan_expected"], {"STALE"})
            assert _success(rejected["dinner_without_answers"])["outcome"] == "blocked"
            assert _success(rejected["release_without_answers"])["outcome"] == "blocked"
            assert server.engine.db.scalar("SELECT count(*) FROM approval_receipts") == baseline_approvals
            assert server.engine.db.scalar("SELECT used_at FROM challenges WHERE id=?", borrowed["challenge_id"]) is None
            _checkpoint(server, evidence, "wrong-person-and-cross-plan-token")

            # Capture a real approver challenge, then edit its underlying work.
            ellis_scope = next(item for item in (await inbox("ellis", True))["items"] if item["plan_id"] == "release")
            challenge = await post("ellis", "/human/projects/challenges", {"scope_id": ellis_scope["id"]})
            assert challenge.status_code == 200, challenge.text
            stale = challenge.json()
            edited = await evidence.wave("owner-edit-with-unrelated-trip-progress", {
                "release": evidence.mcp(sessions, "avery", "upsert_work",
                                        {"plan": "release", "item": _item(server, "release", "work_a"), "spec": {"duration_minutes": 135}}),
                "trip": evidence.mcp(sessions, "blair", "bring_list",
                                     {"plan": "trip", "action": "bring", "things": ["consent-wave-crate"]}),
            })
            _receipt(server, edited["release"], "avery", "release")
            _receipt(server, edited["trip"], "blair", "trip")
            baseline_project = server.engine.db.scalar("SELECT count(*) FROM project_receipts")
            stale_answer = await post("ellis", "/human/projects/decisions", {"challenge_id": stale["challenge_id"],
                                      "nonce": stale["nonce"], "decision": "approve"})
            assert stale_answer.status_code == 409, stale_answer.text
            assert stale_answer.json().get("error", {}).get("code") in {"STALE", "STALE_CANDIDATE"}
            assert server.engine.db.scalar("SELECT count(*) FROM project_receipts") == baseline_project
            blocked = _success(await evidence.mcp(sessions, "devon", "commit_plan", {"plan": "release"}))
            assert blocked["outcome"] == "blocked", blocked
            _checkpoint(server, evidence, "stale-project-challenge-rejected")

            _success(await evidence.mcp(sessions, "devon", "repair_project", {"plan": "release"}))
            assert _success(await evidence.mcp(sessions, "devon", "request_confirmations", {"plan": "release"}))["outcome"] == "requested"
            own_answers = await evidence.wave("own-human-answers-on-both-plans", {
                actor: approve_own(actor) for actor in rng.sample(list(phones), len(phones))
            })
            assert own_answers["avery"] and own_answers["blair"] and own_answers["casey"] and own_answers["ellis"]
            _checkpoint(server, evidence, "own-human-answers-on-both-plans")
            committed = await evidence.wave("commit-current-candidates", {
                "dinner": evidence.mcp(sessions, "avery", "commit_plan", {"plan": "dinner"}),
                "release": evidence.mcp(sessions, "devon", "commit_plan", {"plan": "release"}),
            })
            assert all(_success(value)["outcome"] == "committed" for value in committed.values()), committed
            active = server.engine.db.scalar("SELECT active_revision_id FROM plans WHERE id='dinner'")
            unsatisfied = server.engine.db.all(
                "SELECT s.id FROM approval_scopes s WHERE s.revision_id=? "
                "AND NOT EXISTS (SELECT 1 FROM approval_receipts a WHERE a.scope_id=s.id AND a.status='valid' AND a.decision='approved') "
                "AND NOT EXISTS (SELECT 1 FROM approval_carry_forwards c JOIN approval_receipts a ON a.id=c.receipt_id "
                "WHERE c.scope_id=s.id AND c.target_revision_id=? AND a.status='valid' AND a.decision='approved')", active, active)
            assert not unsatisfied, unsatisfied
            release_revision = server.engine.db.scalar("SELECT committed_revision FROM project_config WHERE plan_id='release'")
            missing = server.engine.db.all(
                "SELECT s.id FROM project_scopes s WHERE s.revision_id=? "
                "AND NOT EXISTS (SELECT 1 FROM project_receipts a WHERE a.scope_id=s.id AND a.decision='approve' "
                "AND a.digest=s.digest AND a.fingerprint=s.fingerprint)", release_revision)
            assert not missing, missing
            assert server.engine.db.scalar("SELECT status FROM project_revisions WHERE id=?", release_revision) == "committed"
            assert server.engine.db.scalar("SELECT count(*) FROM expenses WHERE description='consent-wave-trip-expense'") == 1
            assert server.engine.db.scalar("SELECT due FROM items WHERE id=?", _item(server, "surprise")) == "private-consent-wave"
            _checkpoint(server, evidence, "both-plans-committed-with-valid-current-human-answers")
            await _visibility(evidence, sessions)


async def test_paused_dinner_repair_does_not_block_unrelated_clients(shared_server, monkeypatch):
    """Hold the actual solver while unrelated workflows perform real writes."""
    import groupflow.engine.agreement as agreement

    server = shared_server
    entered, release = threading.Event(), threading.Event()
    original = agreement.repair

    def paused(*args, **kwargs):
        entered.set()
        if not release.wait(WAVE_DEADLINE_S):
            raise RuntimeError("test-owned dinner repair pause was not released")
        return original(*args, **kwargs)

    monkeypatch.setattr(agreement, "repair", paused)
    async with measured(1847, "paused-repair", server) as evidence, eight_sessions(server) as sessions:
        newer_plan = None
        task = asyncio.create_task(evidence.mcp(sessions, "avery", "find_alternatives", {"plan": "dinner"}))
        try:
            assert await asyncio.to_thread(entered.wait, PROGRESS_DEADLINE_S), "dinner repair never reached solver barrier"
            paused_at = time.perf_counter() - evidence.origin
            values = await asyncio.wait_for(evidence.wave("unrelated-progress-while-solver-held", {
                "trip": evidence.mcp(sessions, "blair", "propose_change",
                                     {"plan": "trip", "item": _item(server, "trip"), "due": "trip-during-paused-dinner"}),
                "surprise": evidence.mcp(sessions, "casey", "propose_change",
                                         {"plan": "surprise", "item": _item(server, "surprise"), "due": "surprise-during-paused-dinner"}),
                "release": evidence.mcp(sessions, "devon", "upsert_work",
                                        {"plan": "release", "item": _item(server, "release", "work_a"),
                                         "spec": {"priority": 6}}),
            }), timeout=PROGRESS_DEADLINE_S)
            finished_at = time.perf_counter() - evidence.origin
            evidence.extra["repair_pause"] = {"entered_s": paused_at, "unrelated_finished_s": finished_at,
                                                "release_was_set_at_unrelated_completion": release.is_set(),
                                                "deadlock_guard_s": PROGRESS_DEADLINE_S,
                                                "proof": "actual repair solver paused while unrelated HTTP/MCP writes completed"}
            for plan, value in values.items():
                _receipt(server, value, {"trip": "blair", "surprise": "casey", "release": "devon"}[plan], plan)
            assert not release.is_set()
            # The real worker may claim the job before run_now does, allowing
            # the initiating call to return 'running'. The actual solver
            # barrier, rather than a pending client reply, is our overlap proof.
            evidence.extra["initial_mcp_reply_before_solver_release"] = task.done()
            assert server.engine.db.scalar("SELECT due FROM items WHERE id=?", _item(server, "trip")) == "trip-during-paused-dinner"
            assert server.engine.db.scalar("SELECT due FROM items WHERE id=?", _item(server, "surprise")) == "surprise-during-paused-dinner"
            _checkpoint(server, evidence, "unrelated-progress-before-repair-release")
            # A newer same-plan edit must win over the now-obsolete solver
            # snapshot. Use a different authenticated conversation session.
            newer = await asyncio.wait_for(evidence.mcp(sessions, "avery_dinner", "propose_change", {
                "plan": "dinner", "item": _item(server, "dinner", "activity"), "place": "harbour_b",
                "quote": "newer dinner edit while old repair solver is paused"}), timeout=PROGRESS_DEADLINE_S)
            _receipt(server, newer, "avery", "dinner")
            newer_plan = server.engine.db.get("SELECT * FROM plans WHERE id='dinner'")
            evidence.extra["newer_dinner_edit"] = {key: newer_plan[key] for key in (
                "aggregate_version", "candidate_revision_id", "draft_revision_id", "active_revision_id")}
        finally:
            release.set()
            evidence.extra["repair_release_s"] = time.perf_counter() - evidence.origin
            result = await asyncio.wait_for(task, timeout=WAVE_DEADLINE_S)
            if "error" in result:
                _denied(result, {"STALE", "STALE_INPUT"})
            else:
                _success(result)
                deadline = time.perf_counter() + WAVE_DEADLINE_S
                while result.get("status") in {"queued", "running"}:
                    assert time.perf_counter() < deadline, "released repair did not reach a terminal state"
                    await asyncio.sleep(.05)
                    result = _success(await evidence.mcp(sessions, "avery", "get_alternatives", {"job": result["job"]}))
        assert newer_plan is not None
        saved_plan = server.engine.db.get("SELECT * FROM plans WHERE id='dinner'")
        for key in ("aggregate_version", "candidate_revision_id", "draft_revision_id", "active_revision_id"):
            assert saved_plan[key] == newer_plan[key], (key, newer_plan, saved_plan)
        jobs = server.engine.db.all("SELECT * FROM repair_jobs WHERE plan_id='dinner'")
        assert len(jobs) == 1, jobs
        job = jobs[0]
        if "job" in result:
            assert result["job"] == job["id"]
        assert job["status"] == "failed" and "STALE_INPUT" in job["error"], job
        assert server.engine.db.scalar("SELECT count(*) FROM repair_candidates WHERE job_id=?", job["id"]) == 0
        evidence.extra["stale_solver_result"] = {"job": job["id"], "status": job["status"], "error": job["error"]}
        _checkpoint(server, evidence, "repair-completed")
