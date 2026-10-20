"""What a deployed server needs: configurable Host names, no inspector without a real secret, protocol versions hosts actually send,
honest risk hints, briefings that do not change state by default, and bounded session bookkeeping."""
from __future__ import annotations

import json

import httpx
import pytest

from groupflow.core.serve import serve_in_background
from groupflow.core.settings import PUBLISHED_ADMIN_KEY, ServerSettings
from groupflow.mcp_server.app import MAX_BOUND_SESSIONS, McpAuth
from groupflow.runtime import build_runtime
from tests.conftest import person

ACCEPT = {"content-type": "application/json", "accept": "application/json, text/event-stream"}


def init(version: str = "2025-11-25") -> dict:
    return {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "probe", "version": "1"}}}


def parse(r: httpx.Response):
    if "event-stream" in r.headers.get("content-type", ""):
        for line in r.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:])
    return r.json()


def start(tmp_path, **kw):
    settings = ServerSettings(db_path=str(tmp_path / "gf.db"), allowed_origins=[], **kw)
    rt = build_runtime(settings, worker_interval_s=0.05)
    bg = serve_in_background(rt.app)
    bg.rt = rt
    return bg


@pytest.fixture
def plain(tmp_path):
    bg = start(tmp_path, admin_key=None)
    yield bg
    bg.stop()
    bg.rt.db.close()


async def session(h: httpx.AsyncClient, base: str, member: str, version: str = "2025-11-25", extra: dict | None = None):
    headers = {**ACCEPT, "authorization": f"Bearer {person(member).token}", **(extra or {})}
    r = await h.post(f"{base}/mcp", json=init(version), headers=headers)
    body = parse(r)
    sid = r.headers.get("mcp-session-id")
    headers.update({"mcp-session-id": sid, "mcp-protocol-version": body["result"]["protocolVersion"]})
    await h.post(f"{base}/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers)
    return headers, body


async def tool(h: httpx.AsyncClient, base: str, headers: dict, name: str, args: dict, rid: int = 7):
    r = await h.post(f"{base}/mcp", json={"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": {"name": name, "arguments": args}},
                     headers=headers)
    return parse(r)["result"]


async def test_public_host_names_come_from_configuration(tmp_path, plain):
    async with httpx.AsyncClient() as h:
        r = await h.post(f"{plain.base}/mcp", json=init(), headers={**ACCEPT, "authorization": f"Bearer {person('alice').token}",
                                                                   "host": "groupflow.example.com"})
        assert r.status_code == 421  # localhost only, unless configured
    public = start(tmp_path / "pub", admin_key=None, allowed_hosts=["127.0.0.1:*", "groupflow.example.com"])
    try:
        async with httpx.AsyncClient() as h:
            r = await h.post(f"{public.base}/mcp", json=init(), headers={**ACCEPT, "authorization": f"Bearer {person('alice').token}",
                                                                        "host": "groupflow.example.com"})
            assert r.status_code == 200, r.text
    finally:
        public.stop()
        public.rt.db.close()


async def test_the_inspector_needs_a_real_secret(tmp_path, plain):
    async with httpx.AsyncClient() as h:
        assert (await h.get(f"{plain.base}/debug/state", headers={"x-groupflow-admin": PUBLISHED_ADMIN_KEY})).status_code == 404
    published = start(tmp_path / "pub", admin_key=PUBLISHED_ADMIN_KEY)
    keyed = start(tmp_path / "key", admin_key="a-long-random-operator-secret")
    try:
        async with httpx.AsyncClient() as h:
            assert (await h.get(f"{published.base}/debug/state", headers={"x-groupflow-admin": PUBLISHED_ADMIN_KEY})).status_code == 404
            assert (await h.get(f"{keyed.base}/debug/state", headers={"x-groupflow-admin": "wrong"})).status_code == 401
            assert (await h.get(f"{keyed.base}/debug/state", headers={"x-groupflow-admin": "a-long-random-operator-secret"})).status_code == 200
    finally:
        for bg in (published, keyed):
            bg.stop()
            bg.rt.db.close()


async def test_older_handshake_versions_hosts_send_are_accepted(plain):
    async with httpx.AsyncClient() as h:
        for version in ("2025-03-26", "2025-06-18", "2025-11-25"):
            _, body = await session(h, plain.base, "alice", version)
            assert body["result"]["protocolVersion"] == version


async def test_risk_hints_say_which_tools_overwrite_or_remove(plain):
    async with httpx.AsyncClient() as h:
        headers, _ = await session(h, plain.base, "alice")
        r = await h.post(f"{plain.base}/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=headers)
        tools = {t["name"]: t["annotations"] for t in parse(r)["result"]["tools"]}
    assert all(a["openWorldHint"] is False for a in tools.values())
    for name, a in tools.items():
        if a["readOnlyHint"]:
            assert a["destructiveHint"] is False, name
    destructive = {n for n, a in tools.items() if a["destructiveHint"]}
    additive = {n for n, a in tools.items() if not a["readOnlyHint"] and not a["destructiveHint"]}
    assert destructive and additive
    for name in ("commit_plan", "costs", "propose_change", "lock_in", "split_costs", "change_plan"):
        if name in tools:
            assert name in destructive, name
    for name in ("notify_people", "create_plan", "tell_people", "start_plan"):
        if name in tools:
            assert name not in destructive, name


async def test_a_briefing_changes_nothing_until_heard(tmp_path):
    bg = start(tmp_path, admin_key=None, undeclared_audience="private")
    try:
        async with httpx.AsyncClient() as h:
            bob, _ = await session(h, bg.base, "bob")
            await tool(h, bg.base, bob, "notify_people", {"plan": "friday_dinner", "audience": ["Alice"], "note": "Bring the speaker"})
            alice, _ = await session(h, bg.base, "alice")
            first = (await tool(h, bg.base, alice, "get_context", {"plan": "friday_dinner"}))["structuredContent"]
            assert any("speaker" in u for u in first["updates"]) and first["heard_token"]
            again = (await tool(h, bg.base, alice, "get_context", {"plan": "friday_dinner"}, 8))["structuredContent"]
            assert any("speaker" in u for u in again["updates"]) and again["repeated_updates"] >= 1  # a lost turn does not lose the news
            gone = (await tool(h, bg.base, alice, "get_context", {"plan": "friday_dinner"}, 9))["structuredContent"]
            assert not any("speaker" in u for u in gone["updates"])  # shown twice counts as heard
            # Acknowledging with the token marks them heard after one telling.
            await tool(h, bg.base, bob, "notify_people", {"plan": "friday_dinner", "audience": ["Alice"], "note": "Gate code is 4471"}, 10)
            told = (await tool(h, bg.base, alice, "get_context", {"plan": "friday_dinner"}, 11))["structuredContent"]
            assert any("4471" in u for u in told["updates"])
            after = (await tool(h, bg.base, alice, "get_context", {"plan": "friday_dinner", "heard": told["heard_token"]}, 12))["structuredContent"]
            assert not any("4471" in u for u in after["updates"])
            # An explicit mark_read=false never marks anything.
            await tool(h, bg.base, bob, "notify_people", {"plan": "friday_dinner", "audience": ["Alice"], "note": "Parking is on Elm"}, 13)
            for rid in (14, 15, 16):
                peek = (await tool(h, bg.base, alice, "get_context", {"plan": "friday_dinner", "mark_read": False}, rid))["structuredContent"]
                assert any("Elm" in u for u in peek["updates"])
    finally:
        bg.stop()
        bg.rt.db.close()


def test_session_bindings_are_bounded():
    auth = McpAuth(app=None, identity=None, allowed_origins=[])  # type: ignore[arg-type]
    for i in range(MAX_BOUND_SESSIONS + 50):
        auth._bind(f"s{i}", "alice")
    assert len(auth.sessions) == MAX_BOUND_SESSIONS and "s0" not in auth.sessions and f"s{MAX_BOUND_SESSIONS + 49}" in auth.sessions
