"""Real authenticated human HTTP and MCP consent boundary for personal agreement review and fallbacks."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from groupflow.core.db import Db
from groupflow.core.serve import serve_in_background
from groupflow.core.settings import ServerSettings
from groupflow.engine import fixed_clock
from groupflow.runtime import build_runtime
from tests.engine.test_agreement_fallback import (
    EXPIRY, NOW, PLAN, SECRET, Lab, make_lab, requirements, state,
)


@pytest.fixture
def agreement_server(tmp_path):
    path = tmp_path / "human-boundary.db"
    seeded = make_lab(Db(str(path)))
    ids = (seeded.first, seeded.second, seeded.third, seeded.foreign)
    seeded.gf.db.close()
    settings = ServerSettings(db_path=str(path), admin_key="synthetic-fallback-admin",
                              allowed_origins=["http://localhost:8788"])
    rt = build_runtime(settings, worker_interval_s=0.05)
    rt.services.engine.set_clock(fixed_clock(NOW))
    server = serve_in_background(rt.app)
    server.rt = rt
    server.lab = Lab(rt.services.engine, *ids)
    try:
        yield server
    finally:
        server.stop()
        assert not server.thread.is_alive(), "The test-owned loopback server must stop."
        rt.db.close()


@asynccontextmanager
async def phone(server, member):
    async with httpx2.AsyncClient() as http:
        signed = await http.post(server.base + "/human/login", json={
            "member_id": member, "passcode": f"synthetic-{member}-pass",
        })
        assert signed.status_code == 200, signed.text
        account = signed.json()
        headers = {"authorization": "Bearer " + account["token"], "x-gf-csrf": account["csrf"]}
        yield http, headers


def mcp_client(server, member):
    http = httpx2.AsyncClient(headers={"authorization": f"Bearer synthetic-{member}-token"})
    return Client(streamable_http_client(server.base + "/mcp", http_client=http), mode="legacy")


async def call(client, name, arguments):
    response = await client.call_tool(name, arguments)
    if response.is_error:
        return {"error": json.loads(response.content[0].text)["error"]}
    return response.structured_content


def fallback_body(server, **changes):
    return {"plan": PLAN, "activity": server.lab.first, "places": ["birch_b"],
            "expires_at": EXPIRY, "max_total_minor": 2500, "latest_end": "18:00", **changes}


async def test_agreement_routes_bind_identity_csrf_and_exact_version(agreement_server):
    server = agreement_server
    async with httpx2.AsyncClient() as anonymous:
        assert (await anonymous.get(server.base + "/human/agreements", params={"plan": PLAN})).status_code == 401
    async with phone(server, "amir") as (http, headers):
        page_response = await http.get(server.base + "/human/agreements", params={"plan": PLAN}, headers=headers)
        assert page_response.status_code == 200, page_response.text
        page = page_response.json()
        assert {item["activity_id"] for item in page["items"]} == {server.lab.first, server.lab.second}
        body = {"plan": PLAN, "activity": server.lab.first, "reason": SECRET, "expected": page["version_token"]}
        before = state(server.rt.services.engine)
        no_csrf = await http.post(server.base + "/human/agreements/contest", json=body,
                                  headers={"authorization": headers["authorization"]})
        assert no_csrf.status_code == 403
        assert state(server.rt.services.engine) == before
        override = await http.post(server.base + "/human/agreements/contest", headers=headers,
                                   json={**body, "member_id": "bea"})
        assert override.status_code == 422
        assert state(server.rt.services.engine) == before
        foreign = await http.post(server.base + "/human/agreements/contest", headers=headers,
                                  json={**body, "activity": server.lab.foreign})
        assert foreign.status_code == 404
        assert state(server.rt.services.engine) == before
        saved = await http.post(server.base + "/human/agreements/contest", json=body, headers=headers)
        assert saved.status_code == 200 and saved.json()["outcome"] == "contested", saved.text
        now = state(server.rt.services.engine)
        stale = await http.post(server.base + "/human/agreements/contest", json=body, headers=headers)
        assert stale.status_code in {400, 409} and stale.json()["error"]["code"] == "STALE"
        assert state(server.rt.services.engine) == now
    async with phone(server, "nora") as (http, headers):
        personal = await http.get(server.base + "/human/agreements", params={"plan": PLAN}, headers=headers)
        assert personal.status_code == 200 and SECRET not in personal.text
    async with phone(server, "owen") as (http, headers):
        hidden = await http.get(server.base + "/human/agreements", params={"plan": PLAN}, headers=headers)
        assert hidden.status_code == 404


async def test_fallback_routes_require_personal_nonce_and_hide_other_permissions(agreement_server):
    server = agreement_server
    async with phone(server, "amir") as (amir, amir_headers), phone(server, "bea") as (bea, bea_headers):
        route = server.base + "/human/fallbacks/challenges"
        before = state(server.rt.services.engine)
        no_csrf = await amir.post(route, json=fallback_body(server), headers={"authorization": amir_headers["authorization"]})
        assert no_csrf.status_code == 403
        identity_override = await amir.post(route, json=fallback_body(server, member_id="nora"), headers=amir_headers)
        assert identity_override.status_code == 422
        assert state(server.rt.services.engine) == before
        ch_response = await amir.post(route, json=fallback_body(server), headers=amir_headers)
        assert ch_response.status_code == 200, ch_response.text
        challenge = ch_response.json()
        assert "Birch B" in ch_response.text and "13:00" in ch_response.text
        body = {"challenge_id": challenge["challenge_id"], "nonce": challenge["nonce"], "decision": "approve"}
        before = state(server.rt.services.engine)
        not_owner = await bea.post(server.base + "/human/fallbacks/decisions", json=body, headers=bea_headers)
        assert not_owner.status_code in {403, 404}
        wrong_nonce = await amir.post(server.base + "/human/fallbacks/decisions", json={**body, "nonce": "wrong"}, headers=amir_headers)
        assert wrong_nonce.status_code == 403
        assert state(server.rt.services.engine) == before
        approved = await amir.post(server.base + "/human/fallbacks/decisions", json=body, headers=amir_headers)
        assert approved.status_code == 200 and approved.json()["authorization_id"], approved.text
        permission = approved.json()["authorization_id"]
        replay = await amir.post(server.base + "/human/fallbacks/decisions", json=body, headers=amir_headers)
        assert replay.status_code in {400, 409} and replay.json()["error"]["code"] == "CHALLENGE_USED"
        own_list = await amir.get(server.base + "/human/fallbacks", params={"plan": PLAN}, headers=amir_headers)
        other_list = await bea.get(server.base + "/human/fallbacks", params={"plan": PLAN}, headers=bea_headers)
        assert len(own_list.json()["authorizations"]) == 1
        assert other_list.json()["authorizations"] == []
        assert challenge["nonce"] not in own_list.text + other_list.text
        cross_revoke = await bea.post(server.base + "/human/fallbacks/revoke", json={"id": permission}, headers=bea_headers)
        assert cross_revoke.status_code == 404
        revoked = await amir.post(server.base + "/human/fallbacks/revoke", json={"id": permission}, headers=amir_headers)
        assert revoked.status_code == 200 and revoked.json()["outcome"] == "revoked", revoked.text


async def test_mcp_identity_cannot_be_used_as_human_permission_grant(agreement_server):
    server = agreement_server
    async with httpx2.AsyncClient() as http:
        before = state(server.rt.services.engine)
        response = await http.post(server.base + "/human/fallbacks/challenges", json=fallback_body(server), headers={
            "authorization": "Bearer synthetic-amir-token", "x-gf-csrf": "made-up",
        })
        assert response.status_code == 401
        assert state(server.rt.services.engine) == before
    async with mcp_client(server, "amir") as client:
        tools = (await client.list_tools()).tools
        assert len(tools) == 35
        assert not any(part in tool.name for tool in tools for part in ("fallback_approve", "human_fallback", "approve_for"))
        before = state(server.rt.services.engine)
        out = await call(client, "action_receipts", {"action": "apply", "tool": "human_fallback_decide", "arguments": {
            "plan": PLAN, "decision": "approve", "challenge_id": "fake", "nonce": "fake",
        }, "request_key": "forged-human-grant", "expected": server.rt.services.engine.state_token(PLAN)})
        assert "error" in out
        assert state(server.rt.services.engine) == before


async def test_personal_contest_and_fallback_details_are_not_shared_to_mcp(agreement_server):
    server = agreement_server
    async with phone(server, "amir") as (http, headers):
        ch = (await http.post(server.base + "/human/fallbacks/challenges", json=fallback_body(server), headers=headers)).json()
        approved = await http.post(server.base + "/human/fallbacks/decisions", headers=headers, json={
            "challenge_id": ch["challenge_id"], "nonce": ch["nonce"], "decision": "approve",
        })
        assert approved.status_code == 200, approved.text
        expected = (await http.get(server.base + "/human/agreements", params={"plan": PLAN}, headers=headers)).json()["version_token"]
        contested = await http.post(server.base + "/human/agreements/contest", headers=headers, json={
            "plan": PLAN, "activity": server.lab.first, "expected": expected, "reason": SECRET,
        })
        assert contested.status_code == 200, contested.text
    for speaker in ("nora", "amir"):
        async with mcp_client(server, speaker) as client:
            context = await call(client, "get_context", {"plan": PLAN, "mark_read": False})
            agreement = await call(client, "planning_options", {"plan": PLAN, "mode": "agreement"})
            shown = json.dumps({"context": context, "agreement": agreement})
            assert SECRET not in shown and ch["nonce"] not in shown
            assert approved.json()["authorization_id"] not in shown
            assert requirements(server.rt.services.engine)[("amir", server.lab.first)] != "fallback_approved"
