"""End to end over real MCP Streamable HTTP with the official Python SDK client: the handshake, the generic tools, identity,
origin checks and session binding; and the full agreement loop through the MCP tools plus each person's own page."""
from __future__ import annotations

import json

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from groupflow.core.serve import serve_in_background
from groupflow.core.settings import ServerSettings
from groupflow.mcp_server.tools import NAMES
from groupflow.runtime import build_runtime
from tests.conftest import person

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "raw", "version": "1"}}}
JSON_HEADERS = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
ADMIN = {"x-groupflow-admin": "k"}


@pytest.fixture
def server(tmp_path):
    settings = ServerSettings(db_path=str(tmp_path / "gf.db"), admin_key="k", allowed_origins=["http://localhost:8788"])
    rt = build_runtime(settings, worker_interval_s=0.05)
    bg = serve_in_background(rt.app)
    bg.rt = rt
    yield bg
    bg.stop()
    rt.db.close()


def client_for(server, member: str) -> Client:
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {person(member).token}"})
    return Client(streamable_http_client(f"{server.base}/mcp", http_client=http), mode="legacy")


async def call(c: Client, tool: str, args: dict):
    res = await c.call_tool(tool, args)
    if res.is_error:
        return {"error": json.loads(res.content[0].text)["error"]}
    return res.structured_content


class Phone:
    """A person's own confirmation page, talking to the human-only API."""

    def __init__(self, server, member: str):
        self.base = server.base
        self.member = member
        self.http = httpx2.AsyncClient()
        self.token = self.csrf = None

    async def login(self):
        r = await self.http.post(f"{self.base}/human/login", json={"member_id": self.member, "passcode": person(self.member).passcode})
        assert r.status_code == 200, r.text
        body = r.json()
        self.token, self.csrf = body["token"], body["csrf"]
        return self

    def headers(self, write=False):
        h = {"authorization": f"Bearer {self.token}"}
        if write:
            h["x-gf-csrf"] = self.csrf
        return h

    async def inbox(self):
        return (await self.http.get(f"{self.base}/human/inbox", headers=self.headers())).json()

    async def answer_all(self, decision="approved", **extra):
        out = []
        for item in (await self.inbox())["pending"]:
            ch = (await self.http.post(f"{self.base}/human/challenges", headers=self.headers(True), json={"scope_id": item["scope_id"]})).json()
            r = await self.http.post(f"{self.base}/human/decisions", headers=self.headers(True),
                                     json={"challenge_id": ch["challenge_id"], "nonce": ch["nonce"], "decision": decision, **extra})
            out.append(r.json())
        return out


async def close_restaurant_a(server, source_event_id="rest-a-closed"):
    async with httpx2.AsyncClient() as h:
        r = await h.post(f"{server.base}/debug/observations", headers=ADMIN,
                         json={"group_id": "friends", "place_id": "restaurant_a", "start": "18:00", "end": "21:00", "source_event_id": source_event_id})
        return r.json()


# ---------------------------------------------------------------- transport, identity, tools

async def test_initialize_negotiates_2025_11_25(server):
    async with client_for(server, "alice") as c:
        assert c.session.protocol_version == "2025-11-25"
        info = c.session.initialize_result
        assert info.server_info.name == "groupflow"
        assert "get_context" in (info.instructions or "") and "never confirm" in (info.instructions or "")


async def test_auto_mode_client_also_lands_on_2025_11_25(server):
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {person('bob').token}"})
    async with Client(streamable_http_client(f"{server.base}/mcp", http_client=http), mode="auto") as c:
        assert c.session.protocol_version == "2025-11-25"


async def test_everyone_gets_the_same_tools_with_schemas(server):
    for member in ("linda", "priya"):
        async with client_for(server, member) as c:
            tools = (await c.list_tools()).tools
            assert [t.name for t in tools] == NAMES
            for t in tools:
                assert t.input_schema["type"] == "object", t.name
                assert t.output_schema and t.output_schema["type"] == "object", t.name
                assert isinstance(t.annotations.read_only_hint, bool), t.name
            assert next(t for t in tools if t.name == "explain").annotations.read_only_hint is True
            assert not any(word in t.name for t in tools for word in ("approve", "decline", "withdraw"))


async def test_template_discovery_and_compact_context_over_real_mcp(server):
    async with client_for(server, 'alice') as c:
        directory = await call(c, 'list_templates', {'query': 'meeting'})
        assert directory['templates'][0]['id'] == 'meeting'
        spec = await call(c, 'get_template', {'template': 'team_meeting'})
        assert any(i['name'] == 'meeting' for i in spec['item_types'])
        assert 'catalog' not in spec
        context = await call(c, 'get_context', {'detail': 'summary', 'plan': 'friday_dinner', 'mark_read': False})
        assert context['active_plan_id'] == 'friday_dinner' and len(context['plans']) == 1
        assert len(context['plan_index']) == 3 and len(context['templates']) == 8


async def test_state_is_shared_across_sessions_through_the_server(server):
    async with client_for(server, "charlie") as c:
        sc = await call(c, "propose_change", {"item": "party.date", "value": "Sunday, Oct 25, 6:00 pm", "quote": "Rosa's double-booked Saturday"})
        assert sc["outcome"] == "pending_review"
    async with client_for(server, "alice") as c:
        ctx = await call(c, "get_context", {})
        req = ctx["decisions_for_you"][0]
        assert req["request"] == sc["request"]["request"] and req["said"] == "Rosa's double-booked Saturday"
        done = await call(c, "decide_change", {"request": req["request"], "decision": "accept"})
        assert done["outcome"] == "accepted"


async def test_rule_violations_are_tool_errors(server):
    async with client_for(server, "linda") as c:
        # A hidden item reads exactly like one that does not exist, even to a write.
        r = await call(c, "propose_change", {"item": "party.date", "value": "Monday"})
        assert r["error"]["code"] == "NOT_FOUND"
        r = await call(c, "get_context", {"plan": "party"})
        assert r["error"]["code"] == "NOT_FOUND"
    async with client_for(server, "bob") as c:
        r = await call(c, "commit_plan", {"plan": "friday_dinner"})
        assert r["error"]["code"] == "FORBIDDEN", "only the organizer commits"


async def test_identity_claims_in_arguments_are_ignored(server):
    async with client_for(server, "linda") as c:
        res = await c.call_tool("get_context", {"member": "alice", "as": "alice"})
        ctx = res.structured_content
        assert ctx["you"] == "Linda" and [p["title"] for p in ctx["plans"]] == ["Home"]


async def test_missing_or_bad_token_is_401(server):
    async with httpx2.AsyncClient() as h:
        none = await h.post(f"{server.base}/mcp", headers=JSON_HEADERS, json=INIT)
        assert none.status_code == 401 and "Bearer" in none.headers.get("www-authenticate", "")
        wrong = await h.post(f"{server.base}/mcp", headers={**JSON_HEADERS, "authorization": "Bearer nope"}, json=INIT)
        assert wrong.status_code == 401


async def test_unknown_browser_origin_is_403(server):
    async with httpx2.AsyncClient() as h:
        r = await h.post(f"{server.base}/mcp", headers={**JSON_HEADERS, "authorization": f"Bearer {person('alice').token}", "origin": "http://evil.example"},
                         json=INIT)
        assert r.status_code == 403


async def test_a_session_cannot_be_used_with_another_persons_token(server):
    async with httpx2.AsyncClient() as h:
        init = await h.post(f"{server.base}/mcp", headers={**JSON_HEADERS, "authorization": f"Bearer {person('charlie').token}"}, json=INIT)
        session_id = init.headers["mcp-session-id"]
        r = await h.post(f"{server.base}/mcp", headers={**JSON_HEADERS, "authorization": f"Bearer {person('linda').token}", "mcp-session-id": session_id,
                                                        "mcp-protocol-version": "2025-11-25"},
                         json={"jsonrpc": "2.0", "id": 9, "method": "tools/list", "params": {}})
        assert r.status_code == 403


async def test_the_demo_inspector_requires_the_admin_key(server):
    async with httpx2.AsyncClient() as h:
        assert (await h.get(f"{server.base}/debug/state")).status_code == 401
        body = (await h.get(f"{server.base}/debug/state?group=family&as=linda", headers=ADMIN)).json()
        assert [p["title"] for p in body["plans"]] == ["Home"]
        groups = (await h.get(f"{server.base}/debug/groups", headers=ADMIN)).json()["groups"]
        assert [g["id"] for g in groups] == ["family", "friends", "cottage", "team", "study"]


async def test_healthz_reports_protocol(server):
    async with httpx2.AsyncClient() as h:
        body = (await h.get(f"{server.base}/healthz")).json()
        assert body["protocol"] == "2025-11-25" and body["transport"] == "streamable-http"


# ---------------------------------------------------------------- the agreement loop over MCP and the human-only API

async def test_full_loop_change_repair_reconfirm_commit(server):
    async with client_for(server, "alice") as alice:
        dinner = (await call(alice, "get_context", {"plan": "friday_dinner"}))["plans"][0]
        assert dinner["status"] == "COMMITTED" and dinner["agreed"]["label"] == "P1"
        assert (await close_restaurant_a(server))["outcome"] == "recorded"
        assert (await close_restaurant_a(server))["outcome"] == "duplicate"
        dinner = (await call(alice, "get_context", {"plan": "friday_dinner"}))["plans"][0]
        assert dinner["status"] == "DEGRADED"

        alts = await call(alice, "find_alternatives", {"plan": "friday_dinner", "expected_version": dinner["version"]})
        assert alts["proof"]["status"] == "optimal_within_domain"
        best = alts["alternatives"][0]
        assert best["changes"][0] == "Dinner: Restaurant A -> Restaurant B" and best["unchanged"] == ["Meet up", "Dessert", "Ride home"]

        why = await call(alice, "explain", {"plan": "friday_dinner", "option": "Restaurant C"})
        assert "21:15" in why["options"][0]["reason"] and "Bob" not in why["options"][0]["reason"]

        req = await call(alice, "request_confirmations", {"plan": "friday_dinner"})
        assert req["outcome"] == "requested" and req["carried_forward"] == 12
        blocked = await call(alice, "commit_plan", {"plan": "friday_dinner"})
        assert blocked["outcome"] == "blocked" and any("has not confirmed Dinner" in b for b in blocked["blockers"])

        for m in ("alice", "bob", "chen", "dana"):
            phone = await Phone(server, m).login()
            inbox = await phone.inbox()
            assert [i["activity_id"] for i in inbox["pending"]] == ["dinner"]
            assert {i["status"] for i in inbox["answered"]} == {"carried_forward"}
            assert [r["outcome"] for r in await phone.answer_all()] == ["approved"]

        done = await call(alice, "commit_plan", {"plan": "friday_dinner"})
        assert done["outcome"] == "committed" and done["bookings"] == ["cancel book Restaurant A for 4 at 18:30 (simulated, ref SIM-P1-DINNER)", "book Restaurant B for 4 at 18:30 (simulated, ref SIM-P2-DINNER)"]
        assert (await call(alice, "commit_plan", {"plan": "friday_dinner"}))["outcome"] == "already_committed"

    async with client_for(server, "bob") as bob:
        ctx = await call(bob, "get_context", {"plan": "friday_dinner"})
        p = ctx["plans"][0]
        assert p["status"] == "COMMITTED" and p["agreed"]["label"] == "P2" and p["agreed"]["your_total"] == "CAD 43.00"
        assert any("P2 is now the agreed plan" in u for u in ctx["updates"])


async def test_the_agent_cannot_approve_for_anyone(server):
    await close_restaurant_a(server, "x1")
    async with client_for(server, "alice") as alice:
        await call(alice, "find_alternatives", {"plan": "friday_dinner"})
        await call(alice, "request_confirmations", {"plan": "friday_dinner"})
    async with httpx2.AsyncClient() as h:
        r = await h.get(f"{server.base}/human/inbox", headers={"authorization": f"Bearer {person('alice').token}"})
        assert r.status_code == 401, "the MCP token is useless on the human-only API"
    cand = server.rt.db.scalar("SELECT candidate_revision_id FROM plans WHERE id = 'friday_dinner'")
    assert server.rt.db.all("SELECT * FROM approval_receipts WHERE revision_id = ?", cand) == []


async def test_the_human_api_requires_csrf_and_rejects_replayed_challenges(server):
    await close_restaurant_a(server, "x2")
    async with client_for(server, "alice") as alice:
        await call(alice, "find_alternatives", {"plan": "friday_dinner"})
        await call(alice, "request_confirmations", {"plan": "friday_dinner"})
    bob = await Phone(server, "bob").login()
    item = (await bob.inbox())["pending"][0]
    no_csrf = await bob.http.post(f"{server.base}/human/challenges", headers=bob.headers(False), json={"scope_id": item["scope_id"]})
    assert no_csrf.status_code == 403
    ch = (await bob.http.post(f"{server.base}/human/challenges", headers=bob.headers(True), json={"scope_id": item["scope_id"]})).json()
    body = {"challenge_id": ch["challenge_id"], "nonce": ch["nonce"], "decision": "approved"}
    assert (await bob.http.post(f"{server.base}/human/decisions", headers=bob.headers(True), json=body)).status_code == 200
    replay = await bob.http.post(f"{server.base}/human/decisions", headers=bob.headers(True), json=body)
    assert replay.status_code == 409 and replay.json()["error"]["code"] == "CHALLENGE_USED"
    dana = await Phone(server, "dana").login()
    r = await dana.http.post(f"{server.base}/human/challenges", headers=dana.headers(True), json={"scope_id": item["scope_id"]})
    assert r.status_code == 403


async def test_conditions_from_alexa_are_private_drafts(server):
    async with client_for(server, "bob") as bob:
        r = await call(bob, "set_my_conditions", {"plan": "friday_dinner", "conditions": [{"kind": "home_by", "value": {"time": "20:30"}}]})
        assert r["needs_verification"] is True and r["conditions"][0]["status"] == "unverified"
        r = await call(bob, "request_confirmations", {"plan": "friday_dinner"})
        assert r["error"]["code"] == "FORBIDDEN"
    async with client_for(server, "alice") as alice:
        assert "20:30" not in json.dumps(await call(alice, "get_context", {}))
    phone = await Phone(server, "bob").login()
    c = next(x for x in (await phone.inbox())["conditions"] if x["status"] == "unverified")
    r = await phone.http.post(f"{server.base}/human/conditions/verify", headers=phone.headers(True),
                              json={"condition_id": c["id"], "verify_digest": c["verify_digest"]})
    assert r.json()["outcome"] == "active"
