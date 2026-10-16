"""Over real HTTP: sign-up, groups and invites on the human-only API, an assistant link used as an MCP bearer token,
and the host's listener declaration narrowing what a speaker's MCP session returns."""
from __future__ import annotations

import json

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from tests.conftest import person
from tests.mcp.test_mcp import INIT, JSON_HEADERS, call, server  # noqa: F401 - the fixture is reused


def mcp_client(server, token: str, listeners: str | None = None) -> Client:
    headers = {"Authorization": f"Bearer {token}"}
    if listeners:
        headers["X-GroupFlow-Listeners"] = listeners
    return Client(streamable_http_client(f"{server.base}/mcp", http_client=httpx2.AsyncClient(headers=headers)), mode="legacy")


async def test_sign_up_create_group_invite_join_and_link_an_assistant(server):  # noqa: F811
    async with httpx2.AsyncClient(base_url=server.base) as http:
        r = await http.post("/human/signup", json={"name": "Maya", "passcode": "secret12"})
        assert r.status_code == 200, r.text
        maya = r.json()
        auth = {"authorization": f"Bearer {maya['token']}", "x-gf-csrf": maya["csrf"]}
        # Writes need the CSRF token as well as the session.
        assert (await http.post("/human/groups", headers={"authorization": auth["authorization"]}, json={"name": "Flatmates"})).status_code == 403
        g = (await http.post("/human/groups", headers=auth, json={"name": "Flatmates"})).json()["group"]
        inv = (await http.post("/human/groups/invites", headers=auth, json={"group": g["id"]})).json()
        assert inv["join_url"].endswith(f"?invite={inv['code']}")
        preview = (await http.get(f"/human/invites/{inv['code']}")).json()
        assert preview["group"]["name"] == "Flatmates"
        theo = (await http.post("/human/signup", json={"name": "Theo", "passcode": "secret12", "invite": inv["code"]})).json()
        assert theo["joined"]["name"] == "Flatmates"
        me = (await http.get("/human/me", headers={"authorization": f"Bearer {maya['token']}"})).json()
        assert [m["name"] for m in me["groups"][0]["members"]] == ["Maya", "Theo"]
        link = (await http.post("/human/links", headers=auth, json={"host": "GroupFlow chat"})).json()

    # The linked token is an ordinary MCP bearer token for Maya, with the same tools as anyone.
    async with mcp_client(server, link["token"]) as c:
        created = await call(c, "create_plan", {"template": "shared_tasks", "title": "Flat jobs"})
        assert created["outcome"] == "created" and "Theo" in created["plan"]["visible_to"]
        ctx = await call(c, "get_context", {"detail": "summary", "mark_read": False})
        assert ctx["you"] == "Maya" and any(p["title"] == "Flat jobs" for p in ctx["plan_index"])
        made = await call(c, "invite_to_group", {})
        assert made["outcome"] == "created" and len(made["code"]) == 9


async def test_a_revoked_link_stops_working(server):  # noqa: F811
    async with httpx2.AsyncClient(base_url=server.base) as http:
        maya = (await http.post("/human/signup", json={"name": "Maya", "passcode": "secret12"})).json()
        auth = {"authorization": f"Bearer {maya['token']}", "x-gf-csrf": maya["csrf"]}
        link = (await http.post("/human/links", headers=auth, json={"host": "Echo"})).json()
        assert (await http.post("/human/links/revoke", headers=auth, json={"id": link["id"]})).status_code == 200
        r = await http.post("/mcp", headers={**JSON_HEADERS, "authorization": f"Bearer {link['token']}"}, json=INIT)
        assert r.status_code == 401


async def test_the_listeners_header_narrows_what_the_speaker_session_returns(server):  # noqa: F811
    alice = person("alice").token
    async with mcp_client(server, alice) as c:
        alone = await call(c, "get_context", {"detail": "summary", "mark_read": False})
        assert any(p["title"] == "Linda's surprise party" for p in alone["plan_index"])
    async with mcp_client(server, alice, listeners="group:family") as c:
        room = await call(c, "get_context", {"detail": "summary", "mark_read": False})
        assert "surprise" not in json.dumps(room).lower()
        assert room["shared_with"] == ["Bob", "Charlie", "Linda"]
        hidden = await call(c, "get_context", {"plan": "party", "mark_read": False})
        assert hidden["error"]["code"] == "NOT_FOUND"
        refused = await call(c, "create_plan", {"template": "general_event", "title": "Gift ideas", "private": True})
        assert refused["error"]["code"] == "SHARED_CONVERSATION"


async def test_a_bad_listeners_header_is_refused(server):  # noqa: F811
    async with httpx2.AsyncClient(base_url=server.base) as http:
        headers = {**JSON_HEADERS, "authorization": f"Bearer {person('alice').token}"}
        r = await http.post("/mcp", headers={**headers, "x-groupflow-listeners": "group:team"}, json=INIT)
        assert r.status_code == 400 and "Listeners" in r.json()["error"]["message"]
        ok = await http.post("/mcp", headers={**headers, "x-groupflow-listeners": "group:family"}, json=INIT)
        assert ok.status_code == 200
