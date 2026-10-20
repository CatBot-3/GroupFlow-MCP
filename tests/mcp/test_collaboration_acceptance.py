"""Fresh synthetic collaboration cases over real MCP and human HTTP boundaries."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from groupflow.core.db import Db
from groupflow.core.serve import serve_in_background
from groupflow.engine import fixed_clock
from groupflow.mcp_server.app import Services, create_server_app
from tests.engine.test_collaboration_acceptance import (
    EXPIRY, GROUP, LATER, NOW, PEOPLE, PLAN, assert_capacity_witness, database_state, diagnostic_business_state, freeze_committed, instant, make_lab, restrict_capacity,
)


@pytest.fixture
def collaboration_server(tmp_path):
    lab = make_lab(Db(str(tmp_path / "finch-collaboration.sqlite3")))
    services = Services(db=lab.gf.db, identity=lab.gf.identity, engine=lab.gf)
    app = create_server_app(services, admin_key="synthetic-finch-admin", worker=None)
    server = serve_in_background(app)
    server.lab = lab
    try:
        yield server
    finally:
        server.stop()
        lab.gf.db.close()


def person(member):
    return next(row for row in PEOPLE if row.id == member)


@asynccontextmanager
async def mcp_for(server, member):
    async with httpx2.AsyncClient(headers={"authorization": "Bearer " + person(member).token}) as http:
        async with Client(streamable_http_client(server.base + "/mcp", http_client=http), mode="legacy") as client:
            yield client


async def call(client, tool, arguments):
    result = await client.call_tool(tool, arguments)
    if result.is_error:
        return {"error": json.loads(result.content[0].text)["error"]}
    return result.structured_content


@asynccontextmanager
async def phone_for(server, member):
    async with httpx2.AsyncClient(base_url=server.base) as http:
        login = await http.post("/human/login", json={"member_id": member, "passcode": person(member).passcode})
        assert login.status_code == 200, login.text
        session = login.json()
        headers = {"authorization": "Bearer " + session["token"], "x-gf-csrf": session["csrf"]}
        yield http, headers


async def test_mcp_preview_rolls_back_and_preserves_carry_delta(collaboration_server):
    server = collaboration_server
    lab, gf = server.lab, server.lab.gf
    freeze_committed(lab)
    async with mcp_for(server, "iris") as client:
        resolved = await call(client, "resolve_action", {"query": "Clay rehearsal", "plan": PLAN})
        assert resolved["targets"][0]["item"] == lab.first
        before = database_state(gf)
        preview = await call(client, "action_receipts", {
            "action": "preview", "tool": "propose_change",
            "arguments": {"plan": PLAN, "item": lab.first, "place": "finch_annex"},
            "expected": resolved["targets"][0]["version"],
        })
        assert "error" not in preview, preview
        assert preview["outcome"] == "preview"
        assert database_state(gf) == before
        assert set(preview["affected_participants"]) == {"Iris", "Mila"}
        assert len(preview["approval_impact"]["carried"]) == 3
        assert len(preview["approval_impact"]["needs_confirmation"]) == 2
        assert "action_receipt" not in preview
        assert "action_receipt" not in preview["result"]


async def test_human_preview_auth_stale_and_apply_exact_operation(collaboration_server):
    server = collaboration_server
    gf = server.lab.gf
    async with phone_for(server, "iris") as (http, headers):
        expected = gf.state_token(PLAN)
        body = {"tool": "propose_change", "arguments": {
            "plan": PLAN, "item": server.lab.first, "place": "finch_annex"}, "expected": expected,
            "request_key": "human-finch-edit"}
        before = database_state(gf)
        preview = await http.post("/human/actions/preview", headers=headers, json=body)
        assert preview.status_code == 200, preview.text
        assert preview.headers.get("x-request-id")
        assert preview.json()["outcome"] == "preview"
        assert database_state(gf) == before
        applied = await http.post("/human/actions/apply", headers=headers, json={**body, "request_key": "human-finch-edit"})
        assert applied.status_code == 200, applied.text
        assert applied.json()["action_receipt"]["plans"] == [PLAN]
        after = database_state(gf)
        stale = await http.post("/human/actions/preview", headers=headers, json=body)
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "STALE"
        assert database_state(gf) == after
    async with phone_for(server, "mila") as (http, headers):
        request = gf.propose_change("mila", plan=PLAN, item=server.lab.first, place="finch_studio").request.request
        before = database_state(gf)
        denied = await http.post("/human/actions/preview", headers=headers, json={
            "tool": "decide_change", "arguments": {"request": request, "decision": "accept"},
            "expected": gf.state_token(PLAN), "request_key": "mila-unauthorized-preview"})
        assert denied.status_code == 403, denied.text
        assert database_state(gf) == before
    async with httpx2.AsyncClient(base_url=server.base) as http:
        unauthenticated = await http.post("/human/actions/preview", json=body)
        assert unauthenticated.status_code == 401


async def test_exact_server_recipe_executes_over_mcp_without_creating_any_consent(collaboration_server):
    server = collaboration_server
    gf = server.lab.gf
    async with mcp_for(server, "mila") as member:
        found = await call(member, "resolve_action", {"query": "Clay rehearsal", "plan": PLAN})
        assert "error" not in found, found
        assert "next_actions" in found
        assert not any(row.get("tool") in {"request_confirmations", "commit_plan", "find_alternatives"}
                       for row in found["next_actions"])
    async with mcp_for(server, "iris") as organizer:
        found = await call(organizer, "resolve_action", {"query": "Clay rehearsal", "plan": PLAN})
        recipe = next(row for row in found["next_actions"] if row.get("tool") == "request_confirmations")
        assert recipe["arguments"]["plan"] == PLAN
        assert recipe["expected"] == gf.state_token(PLAN)
        assert recipe["reason"]
        done = await call(organizer, "action_receipts", {
            "action": "apply", "tool": recipe["tool"], "arguments": recipe["arguments"],
            "expected": recipe["expected"], "request_key": "exact-server-recipe",
        })
        assert done["outcome"] == "requested", done
        assert gf.db.scalar("SELECT count(*) FROM approval_receipts") == 0
        repeated = await call(organizer, "action_receipts", {
            "action": "apply", "tool": recipe["tool"], "arguments": recipe["arguments"],
            "expected": recipe["expected"], "request_key": "stale-server-recipe",
        })
        assert repeated["error"]["code"] == "STALE"
    async with phone_for(server, "mila") as (http, headers):
        response = await http.get("/human/decisions", headers=headers)
        assert response.status_code == 200
        card = next(row for row in response.json()["cards"] if row["plan"] == PLAN)
        assert any(row.get("human_page") for row in card["next_actions"])
        assert not any("approve" in row.get("tool", "") for row in card["next_actions"])


async def test_mcp_hypotheticals_feasible_public_witness_and_private_failure_are_read_only(collaboration_server):
    server = collaboration_server
    gf = server.lab.gf
    restrict_capacity(server.lab)
    async with mcp_for(server, "iris") as organizer:
        ordinary = await call(organizer, "planning_options", {"plan": PLAN})
        assert not ordinary["alternatives"], ordinary
        before = diagnostic_business_state(gf)
        queries = gf.db.scalar('SELECT COUNT(*) FROM diagnostic_queries')
        result = await call(organizer, "planning_options", {"plan": PLAN, "mode": "relaxations"})
        assert "error" not in result, result
        assert diagnostic_business_state(gf) == before
        assert gf.db.scalar('SELECT COUNT(*) FROM diagnostic_queries') == queries + 1
        assert result["suggestions"]
        assert result["search_scope"]
        assert all(row["hypothetical"] and row["snapshot"] and row["proof"] and len(row["changes"]) == 1
                   for row in result["suggestions"])
        for suggestion in result["suggestions"]:
            assert_capacity_witness(server.lab, suggestion)
    gf.human_set_condition(gf.identity.get("mila"), "budget", {"amount": "0.37"}, PLAN)
    async with mcp_for(server, "orin") as observer:
        before = diagnostic_business_state(gf)
        queries = gf.db.scalar('SELECT COUNT(*) FROM diagnostic_queries')
        result = await call(observer, "planning_options", {"plan": PLAN, "mode": "relaxations"})
        assert "error" not in result, result
        assert result["suggestions"] == []
        assert diagnostic_business_state(gf) == before
        assert gf.db.scalar('SELECT COUNT(*) FROM diagnostic_queries') == queries + 1
        public = json.dumps(result).lower()
        assert "mila" not in public and "0.37" not in public and "37" not in public
        assert "voluntar" in public


async def test_trusted_observation_expiry_is_visible_over_mcp_and_blocks_candidate(collaboration_server):
    server = collaboration_server
    gf = server.lab.gf
    async with httpx2.AsyncClient(base_url=server.base) as http:
        injected = await http.post("/debug/observations", headers={"x-groupflow-admin": "synthetic-finch-admin"}, json={
            "group_id": GROUP, "kind": "price_changed", "place_id": "finch_studio", "price": 11,
            "source_event_id": "http-finch-price", "valid_until": EXPIRY})
        assert injected.status_code == 200, injected.text
        observation = injected.json()["observation_id"]
    async with mcp_for(server, "iris") as organizer:
        before = database_state(gf)
        facts = await call(organizer, "planning_options", {"plan": PLAN, "mode": "facts"})
        assert "error" not in facts, facts
        assert database_state(gf) == before
        fact = next(row for row in facts["facts"] if row["id"] == observation)
        assert fact["source"] == "demo_console"
        assert instant(fact["verified_at"]) == instant(NOW)
        assert instant(fact["valid_until"]) == instant(EXPIRY)
        assert fact["verification"] == "verified" and fact["freshness"] in {"current", "fresh"}
        assert (await call(organizer, "request_confirmations", {"plan": PLAN}))["outcome"] == "requested"
        gf.set_clock(fixed_clock(LATER))
        before = database_state(gf)
        facts = await call(organizer, "planning_options", {"plan": PLAN, "mode": "facts"})
        assert database_state(gf) == before
        fact = next(row for row in facts["facts"] if row["id"] == observation)
        assert fact["freshness"] in {"stale", "expired"}
        confirmation = await call(organizer, "request_confirmations", {"plan": PLAN})
        assert confirmation.get("outcome") in {"blocked", "infeasible"}, confirmation
        committed = await call(organizer, "commit_plan", {"plan": PLAN})
        assert committed["outcome"] == "blocked", committed
        assert any("fact" in row.lower() or "recheck" in row.lower() or "expir" in row.lower()
                   for row in committed["blockers"])
