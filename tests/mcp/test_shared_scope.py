"""Independent mixed-scope oracle over eight live clients and one real server.

Cases were frozen in eval/runs/2026-10-18-shared-workload/scope before the
shared fixture or implementation was inspected. All people/data are synthetic.
"""
from __future__ import annotations

import asyncio
import json
import time
import traceback
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.shared_workload import MARKERS, call, connect, phone, run_wave, shared_server  # noqa: F401


ROOT = Path(__file__).resolve().parents[2]
from tests.evidence import evidence_dir

ARTIFACTS = evidence_dir("2026-10-18-shared-workload/scope")
VIEWERS = {
    "dinner": {"avery", "blair", "casey"},
    "trip": {"avery", "blair", "devon"},
    "surprise": {"blair", "casey", "ellis"},
    "release": {"avery", "blair", "devon", "ellis"},
}
SESSIONS = {
    "avery": ("avery", ()),
    "avery_room": ("avery", ("blair",)),
    "blair": ("blair", ()),
    "blair_room": ("blair", ("avery",)),
    "casey": ("casey", ()),
    "devon": ("devon", ()),
    "ellis": ("ellis", ()),
    "quinn": ("quinn", ()),
}


def serial(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def snapshot(server, plans):
    """Independent domain snapshot, excluding only rates/session bookkeeping."""
    db = server.engine.db
    wanted = set(plans)
    items = {row["id"] for row in db.all("SELECT id,plan_id FROM items") if row["plan_id"] in wanted}
    revisions = {r["id"] for table in ("plan_revisions", "project_revisions")
                 for r in db.all(f"SELECT id,plan_id FROM {table}") if r["plan_id"] in wanted}
    scopes = {r["id"] for table in ("approval_scopes", "project_scopes")
              for r in db.all(f"SELECT id,revision_id FROM {table}") if r["revision_id"] in revisions}
    result = {}
    for table in ("plans", "items", "facts", "requests", "bring_items", "rsvps", "expenses", "settlements",
                  "plan_revisions", "confirmation_rounds", "executions", "action_receipts", "notifications", "audit",
                  "approval_scopes", "approval_receipts", "approval_carry_forwards", "challenges",
                  "project_config", "project_work", "project_revisions", "project_scopes", "project_receipts",
                  "project_challenges", "resource_reservations"):
        if not db.get("SELECT name FROM sqlite_master WHERE type='table' AND name=?", table):
            continue
        rows = db.all(f'SELECT * FROM "{table}"')
        if table == "plans":
            rows = [r for r in rows if r["id"] in wanted]
        elif table == "facts":
            rows = [r for r in rows if r["item_id"] in items]
        elif table == "action_receipts":
            rows = [r for r in rows if set(json.loads(r["plan_ids_json"])) & wanted]
        elif table in {"approval_scopes", "project_scopes", "challenges"}:
            rows = [r for r in rows if r["revision_id"] in revisions]
        elif table in {"project_receipts", "project_challenges"}:
            rows = [r for r in rows if r["scope_id"] in scopes]
        elif table == "approval_carry_forwards":
            rows = [r for r in rows if r["target_revision_id"] in revisions]
        else:
            rows = [r for r in rows if r.get("plan_id") in wanted]
        result[table] = sorted(rows, key=serial)
    return result


def receipt(result, actor, plan):
    assert "error" not in result, result
    saved = result["action_receipt"]
    assert saved["actor"] == actor.capitalize(), saved
    assert saved["plans"] == [plan], saved
    return saved


def denied(result, codes=("FORBIDDEN", "NOT_FOUND", "STALE")):
    assert result.get("error", {}).get("code") in codes, result
    assert "action_receipt" not in result, result


def category(result, status=200):
    code = result.get("error", {}).get("code", "")
    if status >= 500 or code in {"INTERNAL", "INTERNAL_ERROR", "SERVER_ERROR"}:
        return "internal_fault"
    return "domain_rejection" if code or status >= 400 else "success"


class Probe:
    def __init__(self, server):
        self.server = server
        self.clients = {}
        self.phones = {}
        self.viewers = {p: set(m) for p, m in VIEWERS.items()}
        self.records = []
        self.checks = []
        self.violations = []
        self.receipt_violations = []
        self.scope_events = []
        self.private_envelope = None
        self.initial_roles = {
            "groups": server.engine.db.all("SELECT group_id,member_id,role,status FROM group_members ORDER BY group_id,member_id"),
            "workspaces": server.engine.db.all("SELECT * FROM workspace_roles ORDER BY group_id,member_id,role"),
            "plans": server.engine.db.all("SELECT id,organizer_id FROM plans ORDER BY id"),
        }

    def allowed(self, actor, listeners=()):
        return {plan for plan, members in self.viewers.items() if {actor, *listeners} <= members}

    def oracle(self, actor, listeners, value, operation):
        text = serial(value)
        for plan, marker in MARKERS.items():
            if plan not in self.allowed(actor, listeners) and marker in text:
                self.violations.append({"actor": actor, "listeners": list(listeners), "hidden_plan": plan,
                                        "operation": operation, "response": value})

    def receipt_oracle(self, actor, listeners, arguments, value, operation):
        candidates = [(value.get("action_receipt"), arguments.get("arguments", arguments).get("plan"))]
        candidates.extend((saved, None) for saved in value.get("receipts", []) if isinstance(saved, dict))
        for saved, target in candidates:
            if not isinstance(saved, dict) or "plans" not in saved:
                continue
            reasons = []
            if saved.get("actor") != actor.capitalize():
                reasons.append("receipt actor differs from authenticated actor")
            if target and saved.get("plans") != [target]:
                reasons.append("receipt does not bind the explicit target plan")
            if not set(saved.get("plans", [])) <= self.allowed(actor, listeners):
                reasons.append("receipt names a plan outside current conversation scope")
            if "error" in value:
                reasons.append("denied response includes a success receipt")
            if reasons:
                self.receipt_violations.append({"actor": actor, "listeners": list(listeners), "operation": operation,
                    "arguments": arguments, "reasons": reasons, "receipt": saved})

    async def tool(self, session, tool, arguments=None):
        actor, listeners = SESSIONS[session]
        began = time.perf_counter_ns()
        try:
            result = await call(self.clients[session], tool, arguments or {})
        except Exception as exc:
            ended = time.perf_counter_ns()
            self.records.append({"surface": "mcp", "session": session, "actor": actor, "listeners": list(listeners),
                "operation": tool, "arguments": arguments or {}, "category": "transport_fault", "exception": repr(exc),
                "began_ns": began, "ended_ns": ended, "ms": (ended - began) / 1e6})
            raise
        ended = time.perf_counter_ns()
        self.records.append({"surface": "mcp", "session": session, "actor": actor, "listeners": list(listeners),
                             "operation": tool, "arguments": arguments or {}, "result": result,
                             "began_ns": began, "ended_ns": ended, "ms": (ended - began) / 1e6,
                             "category": category(result)})
        self.oracle(actor, listeners, result, tool)
        self.receipt_oracle(actor, listeners, arguments or {}, result, tool)
        return result

    async def human(self, actor, method, path, body=None):
        p = self.phones[actor]
        began = time.perf_counter_ns()
        try:
            response = await p.http.request(method, path, headers=p.headers, json=body)
        except Exception as exc:
            ended = time.perf_counter_ns()
            self.records.append({"surface": "human_http", "actor": actor, "listeners": [], "operation": path,
                "method": method, "arguments": body, "category": "transport_fault", "exception": repr(exc),
                "began_ns": began, "ended_ns": ended, "ms": (ended - began) / 1e6})
            raise
        ended = time.perf_counter_ns()
        result = response.json()
        self.records.append({"surface": "human_http", "actor": actor, "listeners": [], "operation": path,
                             "method": method, "arguments": body, "status": response.status_code, "result": result,
                             "began_ns": began, "ended_ns": ended, "ms": (ended - began) / 1e6,
                             "category": category(result, response.status_code)})
        self.oracle(actor, (), result, path)
        self.receipt_oracle(actor, (), body or {}, result, path)
        return response.status_code, result

    async def envelope(self, session, tool, arguments, key):
        found = await self.tool(session, "resolve_action", {"query": "", "plan": arguments["plan"]})
        assert "error" not in found, found
        return {"action": "apply", "tool": tool, "arguments": arguments,
                "request_key": key, "expected": found["version"]}

    async def wave(self, operations):
        return await asyncio.wait_for(run_wave(operations), timeout=45)

    async def stage(self, name, operation):
        began = time.perf_counter_ns()
        try:
            await operation()
        except Exception as exc:  # Keep independent later cases and the original evidence.
            self.checks.append({"case": name, "passed": False, "error": str(exc),
                                "traceback": traceback.format_exc(), "began_ns": began,
                                "ended_ns": time.perf_counter_ns()})
        else:
            self.checks.append({"case": name, "passed": True, "began_ns": began,
                                "ended_ns": time.perf_counter_ns()})

    async def surfaces(self):
        for tool, args in (("get_context", {"mark_read": False, "detail": "full"}),
                           ("resolve_action", {"query": "Prepare"}),
                           ("action_receipts", {"action": "inbox"}),
                           ("action_receipts", {"action": "list"})):
            values = await self.wave({s: self.tool(s, tool, args) for s in SESSIONS})
            if tool == "get_context":
                for s, value in values.items():
                    actor, listeners = SESSIONS[s]
                    assert "error" not in value, (s, value)
                    assert {p["id"] for p in value["plans"]} == self.allowed(actor, listeners), (s, value)
        for path in ("/human/inbox", "/human/projects/inbox", "/human/decisions"):
            values = await self.wave({p: self.human(p, "GET", path) for p in self.phones})
            assert all(status == 200 for status, _ in values.values()), values

    def save(self):
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        previous = list(ARTIFACTS.glob("run-*.json"))
        run_number = len(previous) + 1
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        artifact = ARTIFACTS / f"run-{run_number:02d}-{stamp}.json"
        events = sorted([(r["began_ns"], 1) for r in self.records] + [(r["ended_ns"], -1) for r in self.records])
        active = peak = 0
        for _, delta in events:
            active += delta
            peak = max(peak, active)
        artifact.write_text(json.dumps({"kind": "first_run" if run_number == 1 else "retest",
            "session_count": len(self.clients), "sessions": SESSIONS, "server": self.server.base,
            "database": self.server.db_path, "peak_overlapping_requests": peak,
            "initial_visibility_matrix": {p: sorted(m) for p, m in VIEWERS.items()},
            "initial_roles": self.initial_roles, "scope_events": self.scope_events,
            "checks": self.checks, "visibility_violations": self.violations,
            "receipt_binding_violations": self.receipt_violations, "calls": self.records},
            indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return artifact


async def test_shared_mixed_scope_roles_and_targets(shared_server):  # noqa: F811
    server = shared_server
    probe = Probe(server)
    artifact = None
    try:
        async with AsyncExitStack() as stack:
            for name, (actor, listeners) in SESSIONS.items():
                probe.clients[name] = await stack.enter_async_context(connect(server, actor, ",".join(listeners) or None))
            assert len({id(c) for c in probe.clients.values()}) == 8
            for actor in ("avery", "blair", "casey", "devon", "ellis", "quinn"):
                probe.phones[actor] = await stack.enter_async_context(phone(server, actor))

            async def visible_reads():
                await probe.surfaces()
                private = await probe.tool("blair", "get_context", {"mark_read": False})
                assert all(marker in serial(private) for marker in MARKERS.values()), private

            await probe.stage("01 eight sessions and independent visibility matrix", visible_reads)

            async def notifications():
                mapping = {"dinner": "avery", "trip": "blair", "surprise": "casey", "release": "devon"}
                results = await probe.wave({p: probe.tool(actor, "notify_people", {
                    "plan": p, "note": MARKERS[p] + " synthetic local update"}) for p, actor in mapping.items()})
                for plan, result in results.items():
                    receipt(result, mapping[plan], plan)
                await probe.surfaces()
                assert server.engine.db.scalar("SELECT count(*) FROM notifications WHERE member_id='quinn'") == 0
                for plan in VIEWERS:
                    recipients = {r["member_id"] for r in server.engine.db.all("SELECT member_id FROM notifications WHERE plan_id=?", plan)}
                    assert recipients <= VIEWERS[plan], (plan, recipients)

            await probe.stage("02 notifications, recipes, receipt lists and human inboxes", notifications)

            async def targets():
                found = await probe.tool("blair", "resolve_action", {"query": "Prepare"})
                assert found["outcome"] == "ambiguous" and len(found["targets"]) >= 3, found
                resolved = await probe.wave({p: probe.tool("blair", "resolve_action", {"query": "Prepare", "plan": p})
                                             for p in ("dinner", "trip")})
                for plan, result in resolved.items():
                    assert result["outcome"] == "resolved", result
                    assert result["targets"][0]["plan"] == plan
                    assert result["targets"][0]["item"] == server.items[plan]["task"]
                activities = await probe.wave({p: probe.tool("blair", "resolve_action", {"query": "Meet", "plan": p})
                                              for p in ("dinner", "trip")})
                for plan, result in activities.items():
                    assert result["outcome"] == "resolved", result
                    assert result["targets"][0]["plan"] == plan
                    assert result["targets"][0]["item"] == server.items[plan]["activity"]
                before = snapshot(server, {"dinner", "trip"})
                results = await probe.wave({
                    "wrong_dinner": probe.tool("avery", "action_receipts", {
                        "action": "apply", "tool": "propose_change", "arguments": {"plan": "dinner", "item": server.items["trip"]["task"], "status": "done"},
                        "expected": resolved["dinner"]["version"], "request_key": "scope-wrong-dinner-target"}),
                    "wrong_trip": probe.tool("blair", "action_receipts", {
                        "action": "apply", "tool": "propose_change", "arguments": {"plan": "trip", "item": server.items["dinner"]["task"], "status": "done"},
                        "expected": resolved["trip"]["version"], "request_key": "scope-wrong-trip-target"}),
                    "wrong_dinner_activity": probe.tool("avery", "propose_change", {
                        "plan": "dinner", "item": server.items["trip"]["activity"], "start": "11:15"}),
                    "wrong_trip_activity": probe.tool("blair", "propose_change", {
                        "plan": "trip", "item": server.items["dinner"]["activity"], "start": "18:15"}),
                    "unrelated": probe.tool("avery", "upsert_work", {"plan": "release", "item": server.items["release"]["work_a"], "spec": {"cost_minor": 19}}),
                })
                denied(results["wrong_dinner"], ("NOT_FOUND", "INVALID_ARGUMENT", "TARGET_MISMATCH"))
                denied(results["wrong_trip"], ("NOT_FOUND", "INVALID_ARGUMENT", "TARGET_MISMATCH"))
                denied(results["wrong_dinner_activity"], ("NOT_FOUND", "INVALID_ARGUMENT", "TARGET_MISMATCH"))
                denied(results["wrong_trip_activity"], ("NOT_FOUND", "INVALID_ARGUMENT", "TARGET_MISMATCH"))
                assert snapshot(server, {"dinner", "trip"}) == before
                receipt(results["unrelated"], "avery", "release")

            await probe.stage("03 identical labels and wrong explicit targets", targets)

            async def organizer_split():
                results = await probe.wave({
                    "avery_dinner": probe.tool("avery", "commit_plan", {"plan": "dinner"}),
                    "blair_trip": probe.tool("blair", "commit_plan", {"plan": "trip"}),
                    "avery_trip": probe.tool("avery_room", "commit_plan", {"plan": "trip"}),
                    "blair_dinner": probe.tool("blair_room", "commit_plan", {"plan": "dinner"}),
                })
                denied(results["avery_trip"], ("FORBIDDEN",))
                denied(results["blair_dinner"], ("FORBIDDEN",))
                receipt(results["avery_dinner"], "avery", "dinner")
                receipt(results["blair_trip"], "blair", "trip")
                assert results["blair_trip"]["outcome"] == "blocked", results["blair_trip"]
                assert server.engine.db.scalar("SELECT count(*) FROM approval_receipts WHERE revision_id IN (SELECT id FROM plan_revisions WHERE plan_id='trip')") == 0

            await probe.stage("04 organizer here, participant there and no cross-plan consent", organizer_split)

            async def request_keys():
                envelopes = {p: await probe.envelope("blair", "bring_list", {
                    "plan": p, "action": "bring", "things": ["scope shared-key claim"]}, "scope-cross-plan-key") for p in ("dinner", "trip")}
                results = await probe.wave({p: probe.tool("blair", "action_receipts", body) for p, body in envelopes.items()})
                for plan, result in results.items():
                    if "error" in result:
                        denied(result, ("KEY_REUSED",))
                        envelopes[plan] = await probe.envelope("blair", "bring_list", {
                            "plan": plan, "action": "bring", "things": ["scope shared-key claim"]}, "scope-cross-plan-key-" + plan)
                        results[plan] = await probe.tool("blair", "action_receipts", envelopes[plan])
                    receipt(results[plan], "blair", plan)
                replays = await probe.wave({p: probe.tool("blair", "action_receipts", body) for p, body in envelopes.items()})
                for plan, result in replays.items():
                    assert result.get("replayed") is True, result
                    assert receipt(result, "blair", plan)["id"] == results[plan]["action_receipt"]["id"]
                    rows = server.engine.db.all("SELECT * FROM bring_items WHERE plan_id=? AND label='scope shared-key claim'", plan)
                    assert len(rows) == 1 and rows[0]["claimed_by"] == "blair", rows
                before = snapshot(server, {"dinner", "trip"})
                conflict = {**envelopes["dinner"], "arguments": {**envelopes["dinner"]["arguments"], "things": ["scope forbidden second claim"]}}
                denied(await probe.tool("blair", "action_receipts", conflict), ("KEY_REUSED",))
                conflicting_token = {**envelopes["dinner"], "expected": "scope-conflicting-expected-token"}
                denied(await probe.tool("blair", "action_receipts", conflicting_token), ("KEY_REUSED", "STALE"))
                fresh_stale = {**conflicting_token, "request_key": "scope-fresh-stale-token"}
                denied(await probe.tool("blair", "action_receipts", fresh_stale), ("STALE",))
                assert snapshot(server, {"dinner", "trip"}) == before

            await probe.stage("05 request-key isolation, replay and exactly one effect", request_keys)

            async def private_and_narrowed():
                probe.private_envelope = await probe.envelope("blair", "bring_list", {
                    "plan": "surprise", "action": "bring", "things": [MARKERS["surprise"] + " private claim"]}, "scope-private-surprise-key")
                private = await probe.tool("blair", "action_receipts", probe.private_envelope)
                private_receipt = receipt(private, "blair", "surprise")
                found = await probe.tool("blair", "resolve_action", {"query": "Prepare", "plan": "surprise"})
                assert found["targets"][0]["plan"] == "surprise", found
                owner_found = await probe.tool("ellis", "resolve_action", {"query": "Prepare", "plan": "surprise"})
                assert owner_found.get("next_actions"), owner_found
                before = snapshot(server, {"surprise"})
                outputs = await probe.wave({
                    "narrow_read": probe.tool("blair_room", "get_context", {"plan": "surprise", "mark_read": False}),
                    "narrow_status": probe.tool("blair_room", "action_receipts", {"action": "status", "request_key": "scope-private-surprise-key"}),
                    "narrow_replay": probe.tool("blair_room", "action_receipts", probe.private_envelope),
                    "narrow_correct": probe.tool("blair_room", "action_receipts", {"action": "correct", "receipt": private_receipt["id"]}),
                    "private_still_works": probe.tool("blair", "get_context", {"plan": "surprise", "mark_read": False}),
                })
                denied(outputs["narrow_read"], ("NOT_FOUND",))
                denied(outputs["narrow_replay"], ("FORBIDDEN", "NOT_FOUND"))
                denied(outputs["narrow_correct"], ("FORBIDDEN", "NOT_FOUND"))
                assert "action_receipt" not in outputs["narrow_status"] and outputs["narrow_status"].get("outcome") != "known", outputs["narrow_status"]
                assert MARKERS["surprise"] in serial(outputs["private_still_works"])
                assert snapshot(server, {"surprise"}) == before
                # A recipe obtained privately cannot grant a narrowed conversation access.
                recipe = next((r for r in owner_found["next_actions"] if r.get("tool") and r.get("arguments", {}).get("plan") == "surprise"), None)
                assert recipe is not None, owner_found
                replay = await probe.tool("blair_room", "action_receipts", {"action": "preview", "tool": recipe["tool"],
                    "arguments": recipe["arguments"], "expected": recipe.get("expected", found["version"])})
                denied(replay, ("FORBIDDEN", "NOT_FOUND", "INVALID_ARGUMENT"))
                await probe.surfaces()

            await probe.stage("06 one person private and listener-narrowed at once", private_and_narrowed)

            async def outsider():
                before = snapshot(server, set(VIEWERS))
                operations = {}
                for plan in VIEWERS:
                    operations["read_" + plan] = probe.tool("quinn", "get_context", {"plan": plan, "mark_read": False})
                    operations["write_" + plan] = probe.tool("quinn", "propose_change", {"plan": plan, "item": "Prepare", "status": "done"})
                operations["stolen_key"] = probe.tool("quinn", "action_receipts", {"action": "status", "request_key": "scope-private-surprise-key"})
                if probe.private_envelope:
                    operations["stolen_envelope"] = probe.tool("quinn", "action_receipts", probe.private_envelope)
                results = await probe.wave(operations)
                for name, result in results.items():
                    if name == "stolen_key":
                        assert result.get("outcome") == "unknown", result
                    else:
                        denied(result, ("NOT_FOUND", "FORBIDDEN"))
                assert snapshot(server, set(VIEWERS)) == before

            await probe.stage("07 outsider known IDs, same labels and stolen key", outsider)

            async def membership_change():
                sent = await probe.tool("blair", "request_confirmations", {"plan": "trip"})
                assert sent["outcome"] == "requested", sent
                status, inbox = await probe.human("devon", "GET", "/human/inbox")
                assert status == 200
                scope = next(row for row in inbox["pending"] if row["plan_id"] == "trip")
                status, challenge = await probe.human("devon", "POST", "/human/challenges", {"scope_id": scope["scope_id"]})
                assert status == 200, challenge
                challenge_body = {"challenge_id": challenge["challenge_id"], "nonce": challenge["nonce"], "decision": "approved"}
                status, wrong_human = await probe.human("blair", "POST", "/human/decisions", challenge_body)
                assert status == 403, wrong_human
                pending = await probe.envelope("devon", "propose_change", {
                    "plan": "trip", "item": server.items["trip"]["task"], "status": "in_progress"}, "scope-stale-membership")
                preview = await probe.tool("devon", "action_receipts", {**pending, "action": "preview"})
                assert preview.get("outcome") == "preview", preview
                values = await probe.wave({
                    "leave": probe.human("devon", "POST", "/human/groups/leave", {"group": "commons"}),
                    "release": probe.tool("avery", "upsert_work", {"plan": "release", "item": server.items["release"]["work_a"], "spec": {"cost_minor": 23}}),
                    "dinner": probe.tool("casey", "bring_list", {"plan": "dinner", "action": "bring", "things": ["scope unaffected dessert"]}),
                })
                assert values["leave"][0] == 200 and values["leave"][1]["outcome"] == "left", values
                probe.viewers["trip"].remove("devon")
                probe.scope_events.append({"at_ns": time.perf_counter_ns(), "kind": "membership_left",
                                           "actor": "devon", "group": "commons", "lost_plans": ["trip"]})
                receipt(values["release"], "avery", "release")
                receipt(values["dinner"], "casey", "dinner")
                before = snapshot(server, {"trip"})
                outputs = await probe.wave({
                    "stale_write": probe.tool("devon", "action_receipts", pending),
                    "stale_answer": probe.human("devon", "POST", "/human/decisions", challenge_body),
                    "release_visible": probe.tool("devon", "get_project", {"plan": "release"}),
                    "trip_hidden": probe.tool("devon", "get_context", {"plan": "trip", "mark_read": False}),
                })
                denied(outputs["stale_write"])
                assert outputs["stale_answer"][0] in (403, 404, 409), outputs["stale_answer"]
                denied(outputs["trip_hidden"], ("NOT_FOUND",))
                assert "error" not in outputs["release_visible"]
                assert snapshot(server, {"trip"}) == before
                await probe.surfaces()

            await probe.stage("08 membership loss rechecks preview, challenge and current scope", membership_change)

            async def role_change():
                status, result = await probe.human("avery", "POST", "/human/workspace/roles", {
                    "group": "work", "target": "avery", "role": "approver", "grant": True})
                assert status == 403, result
                assert server.engine.db.scalar("SELECT count(*) FROM workspace_roles WHERE group_id='work' AND member_id='avery' AND role='approver'") == 0
                repaired = await probe.tool("devon", "repair_project", {"plan": "release"})
                assert "error" not in repaired, repaired
                sent = await probe.tool("devon", "request_confirmations", {"plan": "release"})
                assert sent["outcome"] == "requested", sent
                for actor in ("avery", "blair"):
                    status, inbox = await probe.human(actor, "GET", "/human/projects/inbox")
                    assert status == 200
                    for scope in [r for r in inbox["items"] if r["plan_id"] == "release" and r["kind"] == "commitment"]:
                        status, challenge = await probe.human(actor, "POST", "/human/projects/challenges", {"scope_id": scope["id"]})
                        assert status == 200, challenge
                        status, answer = await probe.human(actor, "POST", "/human/projects/decisions", {
                            "challenge_id": challenge["challenge_id"], "nonce": challenge["nonce"], "decision": "approve"})
                        assert status == 200 and answer["outcome"] == "approve", answer
                status, inbox = await probe.human("ellis", "GET", "/human/projects/inbox")
                assert status == 200
                scope = next(r for r in inbox["items"] if r["plan_id"] == "release" and r["kind"] == "authorization")
                status, challenge = await probe.human("ellis", "POST", "/human/projects/challenges", {"scope_id": scope["id"]})
                assert status == 200, challenge
                answer_body = {"challenge_id": challenge["challenge_id"], "nonce": challenge["nonce"], "decision": "approve"}
                status, other_answer = await probe.human("avery", "POST", "/human/projects/decisions", answer_body)
                assert status == 403, other_answer
                before_receipts = server.engine.db.scalar("SELECT count(*) FROM project_receipts WHERE member_id='ellis'")
                values = await probe.wave({
                    "revoke": probe.human("devon", "POST", "/human/workspace/roles", {
                        "group": "work", "target": "ellis", "role": "approver", "grant": False}),
                    "dinner": probe.tool("blair", "bring_list", {"plan": "dinner", "action": "bring", "things": ["scope role-wave napkins"]}),
                    "surprise": probe.tool("ellis", "propose_change", {"plan": "surprise", "item": server.items["surprise"]["task"], "status": "in_progress"}),
                })
                assert values["revoke"][0] == 200, values
                probe.scope_events.append({"at_ns": time.perf_counter_ns(), "kind": "role_revoked",
                                           "actor": "ellis", "group": "work", "role": "approver"})
                receipt(values["dinner"], "blair", "dinner")
                receipt(values["surprise"], "ellis", "surprise")
                status, rejected = await probe.human("ellis", "POST", "/human/projects/decisions", answer_body)
                assert status in (403, 409), rejected
                assert server.engine.db.scalar("SELECT count(*) FROM project_receipts WHERE member_id='ellis'") == before_receipts
                committed = await probe.tool("devon", "commit_plan", {"plan": "release"})
                assert committed.get("outcome") == "blocked", committed
                assert server.engine.db.scalar("SELECT status FROM plans WHERE id='release'") != "COMMITTED"
                await probe.surfaces()

            await probe.stage("09 role loss rejects pending human approval while other plans progress", role_change)
    finally:
        artifact = probe.save()
    failures = [row for row in probe.checks if not row["passed"]]
    assert not probe.violations, f"Visibility oracle violations; evidence: {artifact}: {serial(probe.violations)}"
    assert not probe.receipt_violations, f"Receipt binding violations; evidence: {artifact}: {serial(probe.receipt_violations)}"
    assert not failures, f"Shared scope failures; evidence: {artifact}: {serial(failures)}"
    assert len(probe.checks) == 9
    assert max(sum(r["began_ns"] <= t < r["ended_ns"] for r in probe.records)
               for t in [r["began_ns"] for r in probe.records]) >= 6
