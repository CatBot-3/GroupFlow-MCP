"""Simulator backend used by the web page (port 8788). It owns the per-member MCP
client sessions and the agent; the browser never talks to /mcp directly.

The members' confirmation pages ("phones") are different: they use the GroupFlow
server's human-only API (/human/...) with each person's own login, CSRF token and
one-time challenges. The page reaches it through a same-origin pass-through here that
forwards those headers unchanged and keeps nothing, so it works in any browser; the
agent has no route to it and never holds a human session.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from groupflow.core.settings import LlmSettings, SimSettings
from groupflow.sim.agent import LlmAgent, compact
from groupflow.sim.demo_script import SCRIPTS
from groupflow.sim.llm.types import LlmError
from groupflow.sim.mcp_pool import SessionPool
from groupflow.sim.memory import memory_map, turn_plan_ids, visible_plan_ids
from groupflow.sim.traffic import TrafficLog
from groupflow.world import World, resolve_date

log = logging.getLogger("groupflow.sim")

class SayBody(BaseModel):
    member: str
    text: str
    scenario: str | None = None
    plan: str | None = None


class ResetBody(BaseModel):
    scenario: str | None = None


class ForgetBody(BaseModel):
    member: str | None = None


class EventBody(BaseModel):
    group: str
    place_id: str
    kind: str = "place_unavailable"
    start: str = "00:00"
    end: str = "23:59"
    day: int | None = None
    date: str | None = None
    price: float | None = None


class ProjectToolBody(BaseModel):
    tool: str
    arguments: dict[str, Any]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def create_sim_app(*, world: World, agent: LlmAgent, sessions: SessionPool, traffic: TrafficLog, mcp_url: str, server_base: str, admin_key: str,
                   web_dist: str | None = None, demo_passcodes: bool = True) -> FastAPI:
    from groupflow.templates import get_template

    loop_ref: dict[str, asyncio.AbstractEventLoop] = {}
    backend_clients: dict[str, httpx.AsyncClient] = {}

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        loop_ref["loop"] = asyncio.get_running_loop()
        async with httpx.AsyncClient(base_url=server_base, timeout=30, trust_env=False) as backend:
            backend_clients["http"] = backend
            try:
                yield
            finally:
                await sessions.close_all()
                backend_clients.clear()

    app = FastAPI(title="GroupFlow Alexa+ simulator", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.agent = agent
    app.state.sessions = sessions
    app.state.traffic = traffic
    members: dict[str, dict[str, Any]] = {}
    for p in world.people:
        groups = [g["id"] for g in world.groups if any(m["id"] == p.id for m in g["members"])]
        members[p.id] = {"id": p.id, "name": p.name, "color": p.color, "groups": groups, **({"demoPasscode": p.passcode} if demo_passcodes else {})}
    scenarios = []
    for g in world.groups:
        plans = world.plans_of(g["id"])
        stages: list[str] = []
        places: list[dict[str, Any]] = []
        for spec in plans:
            stages += [s for s in (spec.get("stages") or {}) if s != "default" and s not in stages]
            t = get_template(spec["template"])
            places += [{"id": pl.id, "name": pl.name, "kind": pl.kind, "plan": spec["title"], "planId": spec["id"]} for pl in t.catalog.places
                       if pl.kind not in ("travel", "meetup") and pl.id not in {x["id"] for x in places}]
        scenarios.append({
            "id": g["id"], "label": g["name"], "description": g.get("description"),
            "members": [{**members[m["id"]], "label": m.get("label"), "role": m.get("role", "member")} for m in g["members"]],
            "plans": [{"id": spec["id"], "title": spec["title"], "template": spec["template"], "templateName": get_template(spec["template"]).name,
                       "defaultStage": (spec.get("stages") or {}).get("default")} for spec in plans],
            "stages": stages, "places": places, "script": SCRIPTS.get(g["id"], []),
        })

    transcripts: dict[str, list[dict[str, Any]]] = {}
    evidence: dict[str, dict[str, Any]] = {}
    locks: dict[str, asyncio.Lock] = {}
    streams: set[asyncio.Queue[str]] = set()

    def broadcast(event: str, data: Any) -> None:
        frame = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
        loop = loop_ref.get("loop")
        for q in list(streams):
            if loop is not None:
                loop.call_soon_threadsafe(q.put_nowait, frame)

    traffic.subscribe(lambda e: broadcast("traffic", e))

    async def admin(method: str, path: str, **kw: Any) -> httpx.Response:
        return await backend_clients["http"].request(method, path, headers={"x-groupflow-admin": admin_key}, **kw)

    def unreachable() -> JSONResponse:
        return JSONResponse({"error": f"The GroupFlow server is not reachable at {mcp_url}. Start it with: python -m groupflow server"}, status_code=502)

    def scenario_members(scenario: str | None) -> list[str]:
        if scenario in (None, "all"):
            return list(members)
        base = scenario.split(":", 1)[0]
        return [m for m, info in members.items() if base in info["groups"]]

    @app.get("/api/config")
    async def config() -> dict[str, Any]:
        from groupflow.templates import load_templates

        templates = [{"id": t.id, "name": t.name, "description": t.description} for t in load_templates().values()]
        return {"scenarios": scenarios, "templates": templates, "provider": agent.provider_info, "mcpUrl": mcp_url, "serverBase": server_base,
                "showcase": {"group": "friends", "plan": "friday_dinner", "script": SCRIPTS.get('friends', [])[:9]}}

    @app.get("/api/health")
    async def health() -> JSONResponse:
        try:
            r = await backend_clients["http"].get("/healthz", timeout=5)
            return JSONResponse({"simulator": "ok", "mcp": r.json()})
        except httpx.HTTPError:
            return JSONResponse({"simulator": "ok", "mcp": None, "error": f"MCP server not reachable at {mcp_url}"}, status_code=503)

    @app.post("/api/say")
    async def say(body: SayBody) -> JSONResponse:
        member, text = body.member, body.text.strip()
        if member not in members:
            return JSONResponse({"error": "Unknown member."}, status_code=400)
        if not text:
            return JSONResponse({"error": "Say something first."}, status_code=400)
        # One member's requests run in order, so "yes" never overtakes the question it answers.
        lock = locks.setdefault(member, asyncio.Lock())
        try:
            async with lock:
                turn = await agent.handle(member, text, plan=body.plan,
                                          group=body.scenario.split(":", 1)[0] if body.scenario else None)
        except LlmError as err:
            log.warning("LLM error: %s", err)
            return JSONResponse({"error": f"Alexa could not answer: {err}"}, status_code=503)
        except Exception as err:  # noqa: BLE001
            log.exception("turn failed")
            message = str(err) or type(err).__name__
            hint = f" Is the GroupFlow server running at {mcp_url}?" if "connect" in message.lower() or "refused" in message.lower() else ""
            return JSONResponse({"error": f"Alexa could not finish that: {message}.{hint}"}, status_code=502)
        lines = transcripts.setdefault(member, [])
        lines.append({"role": "user", "text": text, "at": turn.at})
        lines.append({"role": "alexa", "text": turn.reply, "at": _now(),
                      "turn": {"calls": turn.calls, "llm": turn.llm, "ms": turn.ms, "provider": turn.provider, "model": turn.model}})
        evidence[member] = {**turn.to_dict(), "group": body.scenario.split(":", 1)[0] if body.scenario else None,
                            "visible_plan_ids": sorted(turn_plan_ids(turn.calls))}
        del lines[:-60]
        broadcast("turn", {"member": member, "scenario": body.scenario, "groups": members[member]["groups"], "text": text, "reply": turn.reply})
        return JSONResponse(turn.to_dict())

    @app.get("/api/transcripts")
    async def get_transcripts() -> dict[str, Any]:
        return {m: transcripts.get(m, []) for m in members}

    @app.get("/api/view/{member}")
    async def view(member: str, plan: str | None = None) -> JSONResponse:
        """'View as': the member's own MCP session asks the server, so the panel shows exactly what their Alexa receives."""
        if member not in members:
            return JSONResponse({"error": "Unknown member."}, status_code=400)
        try:
            s = sessions.get(member, "panel")
            args: dict[str, Any] = {"mark_read": False}
            if plan:
                args["plan"] = plan
            state = await s.call_tool("get_context", args)
            return JSONResponse({"source": "mcp", "member": member, "context": state.data if state.ok else None, "error": None if state.ok else state.error})
        except Exception as err:  # noqa: BLE001
            return JSONResponse({"error": str(err) or type(err).__name__}, status_code=502)

    @app.get("/api/project/{member}")
    async def project_view(member: str, plan: str, workstream: str | None = None, limit: int = 25, offset: int = 0) -> JSONResponse:
        if member not in members:
            return JSONResponse({"error": "Unknown member."}, status_code=400)
        result = await sessions.get(member, "panel").call_tool("get_project", {"plan": plan, "workstream": workstream, "limit": limit, "offset": offset})
        return JSONResponse(result.data if result.ok else {"error": result.error}, status_code=200 if result.ok else 400)

    @app.get("/api/memory/{member}")
    async def memory(member: str, group: str | None = None, plan: str | None = None) -> JSONResponse:
        # Simulator persona selection, just like /api/view; the MCP bearer
        # identity enforces the actual scope. No admin/debug query is used.
        if member not in members:
            return JSONResponse({"error": "Unknown member."}, status_code=400)
        args: dict[str, Any] = {"detail": "summary", "mark_read": False}
        if group:
            args['group'] = group
        if plan:
            args['plan'] = plan
        try:
            result = await sessions.get(member, 'panel').call_tool('get_context', args)
        except Exception:  # a failed read must never fall back to admin data
            return JSONResponse({"error": "Could not read authorized MCP memory."}, status_code=502)
        if not result.ok:
            return JSONResponse({"error": result.error}, status_code=400)
        context = result.data or {}
        previous = evidence.get(member)
        # A historical trace must not expose a formerly visible plan after a
        # membership change, or a previous group's conversation in this view.
        if previous and (previous['group'] != group or not set(previous['visible_plan_ids']).issubset(visible_plan_ids(context))):
            previous = None
        if previous:
            previous = {**previous, 'calls': [{**c, 'sent_to_model': c.get('sent_to_model', False),
                         'model_content': (compact(c['result']) if c['ok'] else {'error': c['result']}) if c.get('sent_to_model') else None}
                        for c in previous['calls']]}
        return JSONResponse({"source": "mcp", "member": member, "at": _now(), "graph": memory_map(context, member),
                             "context": context, "last_turn": previous,
                             "trace_note": "Recorded tool messages sent to the model, not hidden reasoning. The fresh panel snapshot may be newer than that turn."})

    @app.post("/api/project/{member}/tools")
    async def project_action(member: str, body: ProjectToolBody) -> JSONResponse:
        # Demo persona selection, like the Echo panels; the MCP server still
        # checks this persona's actual scope/role. No human-only tool is routed.
        if member not in members or body.tool not in {"configure_project", "upsert_work", "manage_recurrence", "repair_project", "request_confirmations", "commit_plan", "propose_change"}:
            return JSONResponse({"error": "Unsupported project action."}, status_code=400)
        result = await sessions.get(member, "panel").call_tool(body.tool, body.arguments)
        if result.ok:
            broadcast("project", {"member": member, "plan": body.arguments.get("plan")})
        return JSONResponse(result.data if result.ok else {"error": result.error}, status_code=200 if result.ok else 400)

    @app.get("/api/inspector")
    async def inspector(request: Request, scenario: str | None = None) -> JSONResponse:
        """Demo inspector: everything in one group, unfiltered, for the narrator (admin-only server endpoint)."""
        params: dict[str, str] = {}
        if scenario:
            params["group"] = scenario.split(":", 1)[0]
        view_as = request.query_params.get("as")
        if view_as:
            params["as"] = view_as
        try:
            r = await admin("GET", "/debug/state", params=params)
            return JSONResponse(r.json(), status_code=r.status_code)
        except httpx.HTTPError:
            return unreachable()

    @app.get("/api/traffic")
    async def get_traffic(since: int = 0) -> list[dict[str, Any]]:
        return traffic.since(since)

    @app.post("/api/reset")
    async def reset(body: ResetBody | None = None) -> JSONResponse:
        scenario = body.scenario if body else None
        try:
            r = await admin("POST", "/debug/reset", params={"scenario": scenario} if scenario else None)
            if r.status_code != 200:
                return JSONResponse({"error": f"reset failed ({r.status_code})"}, status_code=502)
        except httpx.HTTPError:
            return unreachable()
        for m in scenario_members(scenario):
            agent.reset(m)
            transcripts.pop(m, None)
            evidence.pop(m, None)
        traffic.clear()
        broadcast("reset", {"scenario": scenario})
        return JSONResponse({"ok": True})

    @app.post("/api/forget")
    async def forget(body: ForgetBody) -> JSONResponse:
        """Wipe Alexa's chat memory (for one member or all) without touching the plan, to show the server is the memory."""
        if body.member and body.member not in members:
            return JSONResponse({"error": "Unknown member."}, status_code=400)
        agent.reset(body.member)
        if body.member:
            evidence.pop(body.member, None)
        else:
            evidence.clear()
        broadcast("forget", {"member": body.member})
        return JSONResponse({"ok": True})

    @app.post("/api/demo/event")
    async def demo_event(body: EventBody) -> JSONResponse:
        """Demo console: a trusted outside source reports a place unavailable or a new price (the role a connector would play)."""
        date = body.date
        if date is None:
            spec = next((p for p in world.plans_of(body.group) if any(pl.id == body.place_id for pl in get_template(p["template"]).catalog.places)), None)
            if spec is not None:
                from datetime import date as date_cls, timedelta

                g = world.group(body.group)
                start = resolve_date(spec["start_date"], g.get("timezone", "America/Toronto"))
                date = (date_cls.fromisoformat(start) + timedelta(days=body.day or 0)).isoformat()
        payload = {"group_id": body.group, "kind": body.kind, "place_id": body.place_id, "start": body.start, "end": body.end, "date": date, "price": body.price}
        try:
            r = await admin("POST", "/debug/observations", json=payload)
        except httpx.HTTPError:
            return unreachable()
        data = r.json()
        if r.status_code == 200:
            broadcast("event", {"group": body.group, "place_id": body.place_id, "result": data})
        return JSONResponse(data, status_code=r.status_code)

    @app.post("/api/notify")
    async def notify(request: Request) -> JSONResponse:
        """The page tells other open pages that a phone changed something, so every view refreshes."""
        try:
            data = await request.json()
        except ValueError:
            data = {}
        broadcast("human", data if isinstance(data, dict) else {})
        return JSONResponse({"ok": True})

    @app.api_route("/human/{path:path}", methods=["GET", "POST"])
    async def human_proxy(path: str, request: Request) -> JSONResponse:
        """Same-origin pass-through to the GroupFlow server's human-only API, so a member's page works in any browser
        (some block a page on one port from calling another). It forwards the person's own session and CSRF headers
        unchanged and keeps nothing; the agent has no route to it and holds no human session."""
        headers = {k: v for k, v in request.headers.items() if k.lower() in ("authorization", "x-gf-csrf", "content-type")}
        try:
            r = await backend_clients["http"].request(request.method, f"/human/{path}", content=await request.body(), headers=headers)
        except httpx.HTTPError:
            return unreachable()
        try:
            body = r.json()
        except ValueError:
            body = {"error": r.text[:200]}
        return JSONResponse(body, status_code=r.status_code)

    @app.get("/api/stream")
    async def stream(request: Request) -> StreamingResponse:
        queue: asyncio.Queue[str] = asyncio.Queue()
        loop_ref.setdefault("loop", asyncio.get_running_loop())
        streams.add(queue)

        async def events() -> AsyncIterator[str]:
            try:
                yield ": connected\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        yield await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
            finally:
                streams.discard(queue)

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    if web_dist and Path(web_dist, "index.html").is_file():
        dist = Path(web_dist)
        if (dist / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> FileResponse:
            target = (dist / path).resolve()
            if path and target.is_file() and dist.resolve() in target.parents:
                return FileResponse(target)
            return FileResponse(dist / "index.html")

    return app


def build_sim_app(sim: SimSettings | None = None, llm: LlmSettings | None = None, world_path: str | None = None) -> FastAPI:
    """Wire the simulator from settings: the demo world's people, the provider, sessions and the agent."""
    from groupflow.core.settings import ServerSettings
    from groupflow.sim.llm.factory import create_provider
    from groupflow.world import load_world

    sim = sim or SimSettings()
    llm = llm or LlmSettings()
    world = load_world(world_path or ServerSettings().world_path)
    tokens = {p.id: p.token for p in world.people}
    names = {p.id: p.name for p in world.people}
    traffic = TrafficLog()
    sessions = SessionPool(tokens, sim.mcp_url, traffic)
    agent = LlmAgent(sessions=sessions, provider=create_provider(llm), member_names=names, timezone=sim.timezone, max_steps=llm.max_steps)
    return create_sim_app(world=world, agent=agent, sessions=sessions, traffic=traffic, mcp_url=sim.mcp_url, server_base=sim.server_base,
                          admin_key=sim.admin_key, web_dist=sim.web_dist)
