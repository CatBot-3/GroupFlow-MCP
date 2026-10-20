"""Frozen shared-workload restart cases; every crash kills a test-owned process.

Timing-only child hooks pause a real solve after its running claim, a second
job before its claim, and a successful HTTP response after the engine returned.
No job/receipt/approval state is injected. The retained evidence says exactly
which boundaries were reached. No external provider or API is used.
"""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
import uuid

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


ROOT = Path(__file__).resolve().parents[2]
from tests.evidence import FIXTURES, evidence_dir

ARTIFACT_ROOT = evidence_dir("2026-10-18-shared-workload/restart")
FROZEN_CASES = FIXTURES / "shared_workload" / "CASES_FROZEN.md"


def _json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")


def _field(obj, name):
    return obj[name] if isinstance(obj, dict) else getattr(obj, name)


def _rows(db_path: Path, sql: str, *params):
    with sqlite3.connect(str(db_path), timeout=3) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(sql, params)]


class Evidence:
    def __init__(self, directory: Path):
        self.directory = directory
        self.events = []
        self.checks = []
        self.started = time.monotonic()

    def event(self, kind, **data):
        event = {"at_s": round(time.monotonic() - self.started, 5), "kind": kind, **data}
        self.events.append(event)
        with (self.directory / "trace.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps(event, default=str) + "\n")

    def check(self, case, passed, **details):
        value = {"case": case, "passed": bool(passed), **details}
        self.checks.append(value)
        self.event("assertion", **value)
        self.save()

    def save(self, **extra):
        _json(self.directory / "summary.json", {
            "kind": "genuine abrupt process termination",
            "persistent_state_injection": False,
            "database": str(self.directory / "synthetic.db"),
            "external_execution": "internal simulated executions table only; no external API",
            "checks": self.checks,
            **extra,
        })


class ChildServer:
    def __init__(self, directory: Path, evidence: Evidence):
        self.directory, self.evidence = directory, evidence
        self.database = directory / "synthetic.db"
        self.process = None
        self.log = None
        self.base = None

    async def start(self, phase: str, barriers=False):
        self.phase = phase
        self.phase_dir = self.directory / phase
        self.phase_dir.mkdir()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = dict(os.environ)
        env.update(AGENT_PROVIDER="none", GROUPFLOW_CHECKINS="off", PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        # Windows venv launchers spawn a different server PID. Run the actual
        # interpreter with this environment's packages so the retained handle
        # identifies the exact server, with no descendant left behind.
        interpreter = [sys.executable]
        if os.name == "nt":
            package_dir = str(ROOT / ".venv/Lib/site-packages")
            bootstrap = ("import sys,site,runpy;"
                         f"sys.prefix=sys.exec_prefix={str(ROOT / '.venv')!r};"
                         f"site.addsitedir({package_dir!r});"
                         "sys.argv=['tests.mcp.test_shared_restart']+sys.argv[1:];"
                         "runpy.run_module('tests.mcp.test_shared_restart',run_name='__main__')")
            interpreter = [getattr(sys, "_base_executable", sys.executable), "-S", "-c", bootstrap]
        self.log = (self.phase_dir / "server.log").open("w", encoding="utf-8")
        command = interpreter + ([] if os.name == "nt" else ["-m", "tests.mcp.test_shared_restart"]) + [
                   "--serve", str(self.database), str(port), str(self.phase_dir), "barriers" if barriers else "clean"]
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        self.process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                        stdout=self.log, stderr=subprocess.STDOUT, **options)
        self.base = f"http://127.0.0.1:{port}"
        self.evidence.event("process_started", pid=self.process.pid, phase=phase,
                            database=str(self.database), base=self.base, barriers=barriers,
                            product_sha256={str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                            for path in (ROOT / "groupflow/engine/agreement.py", ROOT / "groupflow/engine/worker.py",
                                                         ROOT / "groupflow/mcp_server/tools.py", ROOT / "groupflow/engine/product.py")})
        ready = await self.wait_file("ready.json", timeout=20)
        assert ready["pid"] == self.process.pid, ("server must be exact retained process", ready, self.process.pid)
        async with httpx2.AsyncClient() as http:
            health = await http.get(self.base + "/healthz")
            assert health.status_code == 200, health.text
        return self

    async def wait_file(self, name, timeout=12):
        path = self.phase_dir / name
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.is_file():
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    await asyncio.sleep(0.01)
                    continue
                self.evidence.event("barrier_observed", name=name, phase=self.phase, data=value)
                return value
            assert self.process.poll() is None, (self.phase_dir / "server.log").read_text(encoding="utf-8", errors="replace")
            await asyncio.sleep(0.02)
        raise AssertionError(f"Timed out waiting for {path}")

    async def kill(self):
        if self.process and self.process.poll() is None:
            pid = self.process.pid
            self.evidence.event("abrupt_kill_requested", pid=pid, phase=self.phase)
            self.process.kill()
            code = self.process.wait(timeout=8)
            self.evidence.event("abrupt_kill_completed", pid=pid, phase=self.phase, returncode=code)
        if self.log:
            self.log.close()
            self.log = None


class _ActorSession:
    """Own SDK cancel scopes in one task, so an intentional crash stays local."""
    def __init__(self, url, headers):
        self.url, self.headers = url, headers
        self.queue = asyncio.Queue()
        self.ready = asyncio.get_running_loop().create_future()
        self.failure = None

    async def __aenter__(self):
        self.owner = asyncio.create_task(self._run())
        await self.ready
        return self

    async def __aexit__(self, *unused):
        if not self.owner.done():
            self.queue.put_nowait(None)
        await self.owner

    async def call_tool(self, name, arguments):
        if self.failure:
            raise RuntimeError(self.failure)
        answer = asyncio.get_running_loop().create_future()
        await self.queue.put((name, arguments, answer))
        return await answer

    async def _run(self):
        active = None
        try:
            async with httpx2.AsyncClient(headers=self.headers, timeout=30) as http:
                async with Client(streamable_http_client(self.url, http_client=http, terminate_on_close=False), mode="legacy") as client:
                    self.ready.set_result(True)
                    while True:
                        request = await self.queue.get()
                        if request is None:
                            break
                        name, arguments, active = request
                        result = await client.call_tool(name, arguments)
                        if not active.done():
                            active.set_result(result)
                        active = None
        except BaseException as error:
            self.failure = f"{type(error).__name__}: {error}"
            if not self.ready.done():
                self.ready.set_exception(RuntimeError(self.failure))
            if active is not None and not active.done():
                active.set_exception(RuntimeError(self.failure))
        finally:
            while not self.queue.empty():
                request = self.queue.get_nowait()
                if request is not None and not request[2].done():
                    request[2].set_exception(RuntimeError(self.failure or "MCP session closed"))


async def _clients(server: ChildServer, stack: AsyncExitStack, evidence: Evidence):
    from tests.shared_workload import PEOPLE

    persons = {person.id: person for person in PEOPLE}
    definitions = [("avery", None), ("blair", None), ("casey", None), ("devon", None),
                   ("ellis", None), ("quinn", None), ("avery", "avery,blair"),
                   ("blair", "blair,casey,ellis")]
    clients = {}
    for index, (actor, listeners) in enumerate(definitions):
        headers = {"authorization": f"Bearer {persons[actor].token}"}
        if listeners:
            headers["x-groupflow-listeners"] = listeners
        client = _ActorSession(server.base + "/mcp", headers)
        name = actor if index < 6 else actor + "_listeners"
        clients[name] = await stack.enter_async_context(client)
    evidence.event("mcp_sessions_open", count=len(clients), actors=[actor for actor, _ in definitions],
                   listener_scopes=[listeners for _, listeners in definitions], official_sdk=True)
    return clients


async def _call(client, name, arguments, evidence: Evidence | None = None, actor=None):
    started = time.monotonic()
    if evidence:
        evidence.event("mcp_started", actor=actor, tool=name, arguments=arguments)
    response = await client.call_tool(name, arguments)
    value = json.loads(response.content[0].text) if response.is_error else response.structured_content
    if evidence:
        evidence.event("mcp_completed", actor=actor, tool=name, arguments=arguments, result=value,
                       duration_ms=round((time.monotonic() - started) * 1000, 3))
    return value


async def _token(client, plan):
    result = await _call(client, "resolve_action", {"query": plan, "plan": plan})
    assert result.get("version"), result
    return result["version"]


async def _login(http, base, actor):
    from tests.shared_workload import PEOPLE

    person = next(person for person in PEOPLE if person.id == actor)
    result = await http.post(base + "/human/login", json={"member_id": actor, "passcode": person.passcode})
    assert result.status_code == 200, result.text
    data = result.json()
    return {"authorization": "Bearer " + data["token"], "x-gf-csrf": data["csrf"]}


async def _inbox(http, base, auth, plan="dinner", include_withdrawn=False):
    response = await http.get(base + "/human/inbox", headers=auth)
    assert response.status_code == 200, response.text
    data = response.json()
    entries = data["pending"] + ([item for item in data["answered"] if item.get("status") in {"revoked", "expired", "invalidated"}]
                                  if include_withdrawn else [])
    return [item for item in entries if item.get("plan_id") == plan or item.get("plan") == plan]


async def _challenge(http, base, auth, scope):
    response = await http.post(base + "/human/challenges", headers=auth, json={"scope_id": scope})
    assert response.status_code == 200, response.text
    data = response.json()
    return {"challenge_id": data["challenge_id"], "nonce": data["nonce"], "decision": "approved"}


async def _close_clients(stack, evidence):
    try:
        await stack.aclose()
    except Exception as error:  # crashed server may already have closed the MCP stream
        evidence.event("mcp_close_after_crash", error=type(error).__name__, detail=str(error)[:600])


async def _settle(tasks, evidence):
    values = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=12)
    for value in values:
        evidence.event("in_flight_after_crash", transport_error=type(value).__name__ if isinstance(value, BaseException) else None,
                       result=str(value)[:800] if isinstance(value, BaseException) else value)
    return values


def _child_main(database, port, directory, mode):
    """Test-only child, called with a process handle retained by its parent."""
    os.environ["AGENT_PROVIDER"] = "none"
    os.environ["GROUPFLOW_CHECKINS"] = "off"
    from groupflow.core.serve import serve_in_background
    from groupflow.engine.worker import Worker
    from groupflow.mcp_server.app import Services, create_server_app
    import groupflow.engine.agreement as agreement
    from tests.shared_workload import build_world

    directory = Path(directory)
    world = build_world(str(database), seed=False)
    engine = _field(world, "engine")
    if mode == "barriers":
        original_repair = agreement.repair

        def slow_repair(problem, *args, **kwargs):
            _json(directory / "running-solver-entered.json", {
                "pid": os.getpid(), "snapshot_plan": problem.base.plan_id if hasattr(problem, "base") else None,
                "jobs": engine.db.all("SELECT id,plan_id,status,started_at FROM repair_jobs ORDER BY created_at"),
                "timing_hook": "after genuine run_job durable running claim, before real repair solve",
            })
            deadline = time.monotonic() + 120
            while not (directory / "release-solver").exists():
                if time.monotonic() > deadline:
                    raise TimeoutError("test-owned solver barrier expired")
                time.sleep(0.01)
            return original_repair(problem, *args, **kwargs)

        agreement.repair = slow_repair
        original_run = engine.run_job

        def delayed_trip_claim(job_id):
            job = engine.db.get("SELECT id,plan_id,status FROM repair_jobs WHERE id=?", job_id)
            if job and job["plan_id"] == "trip" and job["status"] == "queued":
                _json(directory / "queued-before-claim.json", {
                    "pid": os.getpid(), "job": job,
                    "timing_hook": "unmodified synchronous MCP find_alternatives durably queued job; run_job paused before claim",
                })
                deadline = time.monotonic() + 120
                while not (directory / "release-trip").exists():
                    if time.monotonic() > deadline:
                        raise TimeoutError("test-owned pre-claim barrier expired")
                    time.sleep(0.01)
            return original_run(job_id)

        engine.run_job = delayed_trip_claim

    worker = Worker(engine, interval_s=0.05, checkins=None)
    app = create_server_app(Services(engine.db, engine.identity, engine), host="127.0.0.1", worker=worker,
                            admin_key="synthetic-restart-admin", responses="json")

    class HeldResponse:
        async def __call__(self, scope, receive, send):
            headers = dict(scope.get("headers", []))
            marker = headers.get(b"x-shared-restart-hold")

            async def intercept(message):
                if marker and message["type"] == "http.response.start" and message["status"] == 200:
                    name = marker.decode("ascii")
                    if name not in {"expense", "approval", "commit", "project-approval", "withdrawal"}:
                        raise ValueError("invalid test timing marker")
                    _json(directory / f"{name}-reply-held.json", {
                        "pid": os.getpid(), "path": scope.get("path"), "status": message["status"],
                        "timing_hook": "successful engine result completed; response start not delivered",
                    })
                    while not (directory / f"release-{name}").exists():
                        await asyncio.sleep(0.02)
                await send(message)

            await app(scope, receive, intercept)

    server = serve_in_background(HeldResponse(), port=int(port))
    _json(directory / "ready.json", {"pid": os.getpid(), "base": server.base, "database": str(database), "mode": mode})
    try:
        while server.thread.is_alive():
            time.sleep(0.1)
    finally:
        server.stop()
        engine.db.close()


async def test_true_process_restart_under_mixed_load():
    """One retained synthetic DB, eight live SDK sessions, four crash restarts."""
    from tests.shared_workload import build_world

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
    directory = ARTIFACT_ROOT / run_id
    directory.mkdir(parents=True)
    evidence = Evidence(directory)
    server = ChildServer(directory, evidence)
    (directory / "test_source.py").write_text(Path(__file__).read_text(encoding="utf-8"), encoding="utf-8")
    frozen = FROZEN_CASES
    (directory / "frozen_cases.md").write_text(frozen.read_text(encoding="utf-8"), encoding="utf-8")
    seeded = build_world(str(server.database), seed=True)
    items = _field(seeded, "items")
    _field(seeded, "engine").db.close()
    original_executions = _rows(server.database, "SELECT * FROM executions WHERE plan_id='dinner' ORDER BY id")
    evidence.event("synthetic_world_created", database=str(server.database), items=items)
    _json(directory / "case-freeze-copy.json", {"source": str(frozen), "sha256": hashlib.sha256(frozen.read_bytes()).hexdigest(),
          "cases": ["lost durable write reply", "queued recovery", "running recovery", "stale solve", "human confirmations", "execution replay"]})
    stack = AsyncExitStack()
    tasks = []
    try:
        await server.start("phase1-mixed-crash", barriers=True)
        clients = await _clients(server, stack, evidence)
        async with httpx2.AsyncClient(timeout=15) as http:
            auth = {actor: await _login(http, server.base, actor) for actor in ("avery", "blair", "casey", "devon", "ellis")}
            changed = await _call(clients["avery"], "propose_change", {"plan": "dinner", "item": items["dinner"]["activity"], "start": "18:15", "end": "19:15"}, evidence, "avery")
            assert "error" not in changed, changed
            requested = await _call(clients["avery"], "request_confirmations", {"plan": "dinner"}, evidence, "avery")
            assert requested.get("outcome") in {"requested", "ready"}, requested
            pending = await _inbox(http, server.base, auth["blair"])
            assert pending, (await http.get(server.base + "/human/inbox", headers=auth["blair"])).json()
            old_answer = await _challenge(http, server.base, auth["blair"], pending[0]["scope_id"])
            tasks.append(asyncio.create_task(_call(clients["avery"], "find_alternatives", {"plan": "dinner"}, evidence, "avery")))
            await server.wait_file("running-solver-entered.json")
            tasks.append(asyncio.create_task(_call(clients["blair"], "find_alternatives", {"plan": "trip"}, evidence, "blair")))
            await server.wait_file("queued-before-claim.json")
            project_schedule = await _call(clients["devon"], "repair_project", {"plan": "release"}, evidence, "devon")
            assert "error" not in project_schedule, project_schedule
            project_request = await _call(clients["devon"], "request_confirmations", {"plan": "release"}, evidence, "devon")
            assert "error" not in project_request, project_request
            project_inbox = await http.get(server.base + "/human/projects/inbox", headers=auth["ellis"])
            assert project_inbox.status_code == 200, project_inbox.text
            project_scope = next(item for item in project_inbox.json()["items"] if item["plan_id"] == "release" and item["kind"] == "authorization")
            project_challenge = await http.post(server.base + "/human/projects/challenges", headers=auth["ellis"], json={"scope_id": project_scope["id"]})
            assert project_challenge.status_code == 200, project_challenge.text
            project_answer = {"challenge_id": project_challenge.json()["challenge_id"], "nonce": project_challenge.json()["nonce"], "decision": "approve"}
            tasks.append(asyncio.create_task(http.post(server.base + "/human/projects/decisions", headers={**auth["ellis"], "x-shared-restart-hold": "project-approval"}, json=project_answer)))
            await server.wait_file("project-approval-reply-held.json")
            # A human workspace admin revokes the actual approver role while
            # that person's successful answer reply is still undelivered.
            revoked = await http.post(server.base + "/human/workspace/roles", headers=auth["devon"],
                                      json={"group": "work", "target": "ellis", "role": "approver", "grant": False})
            assert revoked.status_code == 200, revoked.text
            evidence.event("human_approver_role_revoked_before_crash", result=revoked.json(), pending_reply=project_answer["challenge_id"])
            old_jobs = _rows(server.database, "SELECT * FROM repair_jobs ORDER BY created_at")
            dinner_job = next(job["id"] for job in old_jobs if job["plan_id"] == "dinner")
            trip_job = next(job["id"] for job in old_jobs if job["plan_id"] == "trip")
            newer_title = "Restart newest dinner activity marker"
            dinner_edit, private_edit, release_read = await asyncio.gather(
                _call(clients["avery_listeners"], "propose_change", {"plan": "dinner", "item": items["dinner"]["activity"], "title": newer_title}, evidence, "avery_listeners"),
                _call(clients["ellis"], "propose_change", {"plan": "surprise", "item": items["surprise"]["task"], "status": "in_progress"}, evidence, "ellis"),
                _call(clients["devon"], "get_context", {"plan": "release", "mark_read": False}, evidence, "devon"))
            assert "error" not in dinner_edit, dinner_edit
            assert "error" not in private_edit, private_edit
            assert "error" not in release_read, release_read
            new_revision = _rows(server.database, "SELECT candidate_revision_id,draft_revision_id,aggregate_version FROM plans WHERE id='dinner'")[0]
            body = {"tool": "costs", "arguments": {"action": "add", "plan": "trip", "amount": "13.37", "what": "Restart durable lost reply expense"},
                    "request_key": "restart-lost-expense", "expected": await _token(clients["avery_listeners"], "trip")}
            tasks.append(asyncio.create_task(http.post(server.base + "/human/actions/apply", headers={**auth["blair"], "x-shared-restart-hold": "expense"}, json=body)))
            await server.wait_file("expense-reply-held.json")
            saved = _rows(server.database, "SELECT * FROM action_receipts WHERE actor_id='blair' AND request_key='restart-lost-expense'")
            assert len(saved) == 1
            saved_id = saved[0]["id"]
            evidence.event("before_first_crash", jobs=old_jobs, newest_dinner=new_revision, saved_receipt=saved_id,
                           pending_challenge=old_answer["challenge_id"], in_flight=len(tasks))
            await server.kill()
            await _settle(tasks, evidence)
            tasks = []
        await _close_clients(stack, evidence)
        stack = AsyncExitStack()

        await server.start("phase2-recovery")
        clients = await _clients(server, stack, evidence)
        recovered = []
        for _ in range(45):
            recovered = _rows(server.database, "SELECT * FROM repair_jobs WHERE id IN (?,?)", dinner_job, trip_job)
            if all(job["status"] in {"done", "failed"} for job in recovered):
                break
            await asyncio.sleep(0.05)
        dinner_state = next(job for job in recovered if job["id"] == dinner_job)
        trip_state = next(job for job in recovered if job["id"] == trip_job)
        evidence.check("running_repair_recoverable", dinner_state["status"] in {"done", "failed"}, state=dinner_state,
                       public=await _call(clients["avery"], "get_alternatives", {"job": dinner_job}, evidence, "avery"))
        evidence.check("queued_repair_recoverable", trip_state["status"] in {"done", "failed"}, state=trip_state,
                       public=await _call(clients["blair"], "get_alternatives", {"job": trip_job}, evidence, "blair"))
        after_dinner = _rows(server.database, "SELECT candidate_revision_id,draft_revision_id,aggregate_version FROM plans WHERE id='dinner'")[0]
        revision_id = new_revision["candidate_revision_id"] or new_revision["draft_revision_id"]
        preserved_snapshot = _rows(server.database, "SELECT snapshot_json FROM plan_revisions WHERE id=?", revision_id)[0]["snapshot_json"]
        evidence.check("stale_repair_preserves_newer_edit", after_dinner["candidate_revision_id"] == new_revision["candidate_revision_id"] and newer_title in preserved_snapshot,
                       before=new_revision, after=after_dinner, interrupted_job=dinner_state)
        original = json.loads(saved[0]["result_json"])
        status, replay, other_actor, narrowed = await asyncio.gather(
            _call(clients["blair"], "action_receipts", {"action": "status", "request_key": body["request_key"]}, evidence, "blair"),
            _call(clients["blair"], "action_receipts", {"action": "apply", **body}, evidence, "blair"),
            _call(clients["avery"], "action_receipts", {"action": "status", "request_key": body["request_key"]}, evidence, "avery"),
            _call(clients["blair_listeners"], "action_receipts", {"action": "status", "request_key": body["request_key"]}, evidence, "blair_listeners"))
        expenses = _rows(server.database, "SELECT * FROM expenses WHERE description=?", body["arguments"]["what"])
        evidence.check("committed_lost_reply_replayed_once", replay.get("replayed") is True and replay.get("action_receipt", {}).get("id") == saved_id and len(expenses) == 1 and saved_id in json.dumps(status),
                       replay=replay, status=status, expense_count=len(expenses), original_result=original)
        hidden = json.dumps([other_actor, narrowed])
        evidence.check("saved_result_bound_to_actor_listener", saved_id not in hidden and body["arguments"]["what"] not in hidden,
                       other_actor=other_actor, narrowed=narrowed)
        wrong_plan = await _call(clients["blair"], "action_receipts", {"action": "apply", **body,
                                 "arguments": {**body["arguments"], "plan": "dinner"}}, evidence, "blair")
        evidence.check("saved_key_cannot_cross_plan", wrong_plan.get("error", {}).get("code") == "KEY_REUSED" and saved_id not in json.dumps(wrong_plan), result=wrong_plan)
        async with httpx2.AsyncClient(timeout=15) as http:
            auth = {actor: await _login(http, server.base, actor) for actor in ("avery", "blair", "casey", "ellis")}
            stale_project = await http.post(server.base + "/human/projects/challenges", headers=auth["ellis"], json={"scope_id": project_scope["id"]})
            blocked_project = await _call(clients["devon"], "commit_plan", {"plan": "release"}, evidence, "devon")
            release_config = _rows(server.database, "SELECT * FROM project_config WHERE plan_id='release'")
            evidence.check("revoked_precrash_project_approval_cannot_commit", stale_project.status_code in {403, 409} and blocked_project.get("outcome") != "committed" and
                           not any(row.get("committed_revision") for row in release_config),
                           challenge_status=stale_project.status_code, challenge=stale_project.json(), commit=blocked_project,
                           project_scope=project_scope["id"], config=release_config)
            stale = await http.post(server.base + "/human/decisions", headers=auth["blair"], json=old_answer)
            evidence.check("precrash_stale_human_challenge_rejected", stale.status_code == 409 and stale.json().get("error", {}).get("code") == "STALE_CANDIDATE", status=stale.status_code, result=stale.json())
            # A fresh explicit request supersedes the old pending round safely.
            requested = await _call(clients["avery"], "request_confirmations", {"plan": "dinner"}, evidence, "avery")
            assert requested.get("outcome") in {"requested", "ready"}, requested
            avery_pending = await _inbox(http, server.base, auth["avery"])
            assert avery_pending
            avery_answer = await _challenge(http, server.base, auth["avery"], avery_pending[0]["scope_id"])
            tasks.append(asyncio.create_task(http.post(server.base + "/human/decisions", headers={**auth["avery"], "x-shared-restart-hold": "approval"}, json=avery_answer)))
            await server.wait_file("approval-reply-held.json")
            before_partial = _rows(server.database, "SELECT * FROM approval_receipts WHERE challenge_id=?", avery_answer["challenge_id"])
            assert len(before_partial) == 1
            tasks.append(asyncio.create_task(_call(clients["devon"], "get_context", {"plan": "release", "mark_read": False}, evidence, "devon")))
            await server.kill()
            await _settle(tasks, evidence)
            tasks = []
        await _close_clients(stack, evidence)
        stack = AsyncExitStack()

        await server.start("phase3-partial-approval")
        clients = await _clients(server, stack, evidence)
        blocked = await _call(clients["avery"], "commit_plan", {"plan": "dinner"}, evidence, "avery")
        evidence.check("partial_human_answers_cannot_commit", blocked.get("outcome") == "blocked" and _rows(server.database, "SELECT * FROM executions WHERE plan_id='dinner' ORDER BY id") == original_executions, result=blocked)
        async with httpx2.AsyncClient(timeout=15) as http:
            auth = {actor: await _login(http, server.base, actor) for actor in ("avery", "blair", "casey")}
            replay_answer = await http.post(server.base + "/human/decisions", headers=auth["avery"], json=avery_answer)
            forged = await http.post(server.base + "/human/decisions", headers=auth["blair"], json=avery_answer)
            wrong_project = await http.post(server.base + "/human/projects/decisions", headers=auth["avery"],
                                            json={**avery_answer, "decision": "approve"})
            evidence.check("used_and_cross_person_answers_rejected", replay_answer.status_code == 409 and forged.status_code == 403 and
                           len(_rows(server.database, "SELECT * FROM approval_receipts WHERE challenge_id=?", avery_answer["challenge_id"])) == 1,
                           used=replay_answer.json(), forged=forged.json())
            evidence.check("event_answer_cannot_authorize_project", wrong_project.status_code == 403 and
                           not _rows(server.database, "SELECT * FROM project_receipts WHERE member_id='avery'"), result=wrong_project.json())
            approvals = []
            for actor in ("avery", "blair", "casey"):
                for item in await _inbox(http, server.base, auth[actor]):
                    answer = await _challenge(http, server.base, auth[actor], item["scope_id"])
                    result = await http.post(server.base + "/human/decisions", headers=auth[actor], json=answer)
                    assert result.status_code == 200, result.text
                    approvals.append({"actor": actor, "scope_id": item["scope_id"], "result": result.json()})
            evidence.event("authorized_human_answers_completed", approvals=approvals)
            tasks.append(asyncio.create_task(http.post(server.base + "/human/revoke", headers={**auth["avery"], "x-shared-restart-hold": "withdrawal"},
                                                       json={"receipt_id": before_partial[0]["id"]})))
            await server.wait_file("withdrawal-reply-held.json")
            revoked_answer = _rows(server.database, "SELECT * FROM approval_receipts WHERE id=?", before_partial[0]["id"])
            assert revoked_answer[0]["status"] == "revoked", revoked_answer
            await server.kill()
            await _settle(tasks, evidence)
            tasks = []
        await _close_clients(stack, evidence)
        stack = AsyncExitStack()

        await server.start("phase4-withdrawn-answer")
        clients = await _clients(server, stack, evidence)
        blocked_withdrawn = await _call(clients["avery"], "commit_plan", {"plan": "dinner"}, evidence, "avery")
        after_withdrawal = _rows(server.database, "SELECT * FROM approval_receipts WHERE id=?", before_partial[0]["id"])
        evidence.check("withdrawn_answer_cannot_commit_after_restart", blocked_withdrawn.get("outcome") == "blocked" and after_withdrawal[0]["status"] == "revoked" and
                       _rows(server.database, "SELECT * FROM executions WHERE plan_id='dinner' ORDER BY id") == original_executions,
                       result=blocked_withdrawn, receipt=after_withdrawal)
        async with httpx2.AsyncClient(timeout=15) as http:
            auth = {"avery": await _login(http, server.base, "avery")}
            pending_again = await _inbox(http, server.base, auth["avery"], include_withdrawn=True)
            assert pending_again
            for item in pending_again:
                fresh_answer = await _challenge(http, server.base, auth["avery"], item["scope_id"])
                approved_again = await http.post(server.base + "/human/decisions", headers=auth["avery"], json=fresh_answer)
                assert approved_again.status_code == 200, approved_again.text
            evidence.event("withdrawn_person_explicitly_reapproved_current_scope", actor="avery", scopes=[item["scope_id"] for item in pending_again])
            commit_body = {"tool": "commit_plan", "arguments": {"plan": "dinner"}, "request_key": "restart-lost-commit", "expected": await _token(clients["avery"], "dinner")}
            tasks.append(asyncio.create_task(http.post(server.base + "/human/actions/apply", headers={**auth["avery"], "x-shared-restart-hold": "commit"}, json=commit_body)))
            await server.wait_file("commit-reply-held.json")
            executions = _rows(server.database, "SELECT * FROM executions WHERE plan_id='dinner' ORDER BY id")
            committed_receipt = _rows(server.database, "SELECT id FROM action_receipts WHERE actor_id='avery' AND request_key='restart-lost-commit'")
            assert executions and len(committed_receipt) == 1, (executions, committed_receipt)
            evidence.event("simulated_booking_committed_reply_not_delivered", executions=executions, receipt=committed_receipt)
            await server.kill()
            await _settle(tasks, evidence)
            tasks = []
        await _close_clients(stack, evidence)
        stack = AsyncExitStack()

        await server.start("phase5-execution-replay")
        clients = await _clients(server, stack, evidence)
        replay_commit = await _call(clients["avery"], "action_receipts", {"action": "apply", **commit_body}, evidence, "avery")
        second_commit = await _call(clients["avery"], "commit_plan", {"plan": "dinner"}, evidence, "avery")
        after_executions = _rows(server.database, "SELECT * FROM executions WHERE plan_id='dinner' ORDER BY id")
        duplicate_active = _rows(server.database, "SELECT revision_id,activity_id,count(*) AS n FROM executions WHERE plan_id='dinner' AND status='succeeded' GROUP BY revision_id,activity_id HAVING count(*)>1")
        evidence.check("simulated_execution_replay_at_most_once", replay_commit.get("replayed") is True and
                       replay_commit.get("action_receipt", {}).get("id") == committed_receipt[0]["id"] and second_commit.get("outcome") == "already_committed" and
                       after_executions == executions and not duplicate_active,
                       replay=replay_commit, second=second_commit, before=executions, after=after_executions)
        fresh_search = await _call(clients["avery"], "find_alternatives", {"plan": "dinner"}, evidence, "avery")
        evidence.check("interrupted_job_does_not_block_future_search", fresh_search.get("job") != dinner_job and fresh_search.get("status") in {"done", "failed"},
                       original_interrupted_job=dinner_job, result=fresh_search)
        private_snapshot = _rows(server.database, "SELECT * FROM items WHERE id=?", items["surprise"]["task"])
        evidence.check("unrelated_private_edit_survives_restarts", bool(private_snapshot) and private_snapshot[0]["status"] == "in_progress", rows=private_snapshot)
        evidence.save(completed=True)
    except BaseException as error:
        evidence.event("harness_or_case_exception", type=type(error).__name__, message=str(error))
        evidence.save(completed=False, exception={"type": type(error).__name__, "message": str(error)})
        raise
    finally:
        await server.kill()
        if tasks:
            await _settle(tasks, evidence)
        await _close_clients(stack, evidence)
    failures = [check for check in evidence.checks if not check["passed"]]
    assert not failures, f"Restart acceptance failures: {failures}. Evidence: {directory}"


if __name__ == "__main__" and len(sys.argv) == 6 and sys.argv[1] == "--serve":
    _child_main(*sys.argv[2:])
