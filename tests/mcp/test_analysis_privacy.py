"""Fresh transport acceptance of diagnostic and personal privacy rules."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from groupflow.engine import fixed_clock
from groupflow.mcp_server.tools import NAMES
from tests.conftest import person
from tests.engine.test_analysis_privacy import business_state, budget_denied
from tests.mcp.test_mcp import Phone, call, client_for, server  # noqa: F401


PLAN = 'friday_dinner'


@asynccontextmanager
async def room(server, member='alice', listeners='alice,bob'):
    headers = {'authorization': 'Bearer ' + person(member).token, 'x-groupflow-listeners': listeners}
    async with httpx2.AsyncClient(headers=headers) as http:
        error = None
        async with Client(streamable_http_client(server.base + '/mcp', http_client=http), mode='legacy') as client:
            try:
                yield client
            except BaseException as caught:
                error = caught
        if error is not None:
            raise error


async def test_mcp_resilience_returns_real_hypothetical_repairs_without_receipts_or_business_changes(server):  # noqa: F811
    engine = server.rt.services.engine
    async with client_for(server, 'alice') as client:
        before = business_state(engine)
        result = await call(client, 'planning_options', {'plan': PLAN, 'mode': 'resilience', 'scenarios': [
            {'kind': 'place_unavailable', 'place_id': 'restaurant_a', 'start': 1080, 'end': 1260}]})
        assert 'error' not in result, result
        assert result.get('hypothetical') is True or result.get('read_only') is True
        assert result.get('scenarios') and result['scenarios'][0].get('repairs')
        assert 'action_receipt' not in result
        assert business_state(engine) == before


async def test_mcp_budget_survives_new_keys_sessions_listeners_and_version(server):  # noqa: F811
    engine = server.rt.services.engine
    engine.set_clock(fixed_clock('2026-10-19T14:00:00Z'))
    for index, mode in enumerate(('fairness', 'relaxations', 'resilience', 'fairness', 'resilience')):
        connection = room(server) if index == 3 else client_for(server, 'alice')
        async with connection as client:
            args = {'plan': PLAN, 'mode': mode, 'request_key': 'new-diagnostic-key-' + str(index)}
            if mode == 'resilience':
                args['scenarios'] = [{'kind': 'price_changed', 'place_id': 'restaurant_a', 'price_minor': 3100 + index}]
            result = await call(client, 'planning_options', args)
            assert not budget_denied(result), (index, result)
            assert 'error' not in result, result
        if index == 2:
            engine.propose_change('alice', plan=PLAN, new_type='task', title='Transport version marker')
    async with client_for(server, 'alice') as client:
        before = business_state(engine)
        denied = await call(client, 'planning_options', {'plan': PLAN, 'mode': 'relaxations', 'request_key': 'sixth-fresh-key'})
        assert budget_denied(denied), denied
        assert business_state(engine) == before
    async with client_for(server, 'bob') as client:
        allowed = await call(client, 'planning_options', {'plan': PLAN, 'mode': 'fairness'})
        assert 'error' not in allowed and not budget_denied(allowed), allowed


async def test_room_private_review_never_reveals_scenario_result_or_private_owner(server):  # noqa: F811
    engine = server.rt.services.engine
    private_ids = [row['id'] for row in engine.db.all("SELECT id FROM conditions WHERE plan_id=? AND owner_id!='alice' AND visibility='private' AND status='active'", PLAN)]
    async with room(server) as client:
        before = business_state(engine)
        for mode in ('relaxations', 'resilience', 'fairness'):
            result = await call(client, 'planning_options', {'plan': PLAN, 'mode': mode, 'scenarios': [
                {'kind': 'price_changed', 'place_id': 'restaurant_a', 'price_minor': 876543}] if mode == 'resilience' else None})
            assert result.get('outcome') == 'private_review_required', result
            text = json.dumps(result)
            assert all(secret not in text for secret in [*private_ids, 'Bob', 'Chen', 'Dana', '876543'])
            assert not any(key in result for key in ('scenarios', 'repairs', 'alternatives', 'feasible', 'conflict', 'owners'))
        assert business_state(engine) == before
    async with room(server, listeners='alice,linda') as client:
        denied = await call(client, 'planning_options', {'plan': PLAN, 'mode': 'fairness'})
        assert denied.get('error', {}).get('code') in {'NOT_FOUND', 'FORBIDDEN'}, denied
        assert 'private_review_required' not in json.dumps(denied)


async def test_personal_fairness_changes_require_human_session_csrf_and_cannot_impersonate(server):  # noqa: F811
    engine = server.rt.services.engine
    alice, bob, linda = [await Phone(server, member).login() for member in ('alice', 'bob', 'linda')]
    path = server.base + '/human/fairness/preferences'
    try:
        body = {'group': 'friends', 'enabled': True}
        async with httpx2.AsyncClient() as public:
            unauthenticated = await public.post(path, json=body)
        assert unauthenticated.status_code == 401
        assert (await alice.http.post(path, headers=alice.headers(), json=body)).status_code == 403
        before = business_state(engine)
        impersonated = await alice.http.post(path, headers=alice.headers(True), json={**body, 'member_id': 'bob'})
        assert impersonated.status_code == 422
        assert business_state(engine) == before
        outsider = await linda.http.post(path, headers=linda.headers(True), json=body)
        assert outsider.status_code in {403, 404}
        changed = await alice.http.post(path, headers=alice.headers(True), json=body)
        assert changed.status_code == 200, changed.text
        rows = engine.db.all('SELECT * FROM fairness_preferences WHERE group_id=?', 'friends')
        assert [(row['member_id'], row['enabled']) for row in rows] == [('alice', 1)]
        bob_read = await bob.http.get(server.base + '/human/planning/privacy?plan=' + PLAN, headers=bob.headers())
        assert bob_read.status_code == 200, bob_read.text
        text = json.dumps(bob_read.json())
        own = engine.db.all("SELECT id FROM conditions WHERE plan_id=? AND owner_id='bob' AND status='active'", PLAN)
        other = engine.db.all("SELECT id FROM conditions WHERE owner_id!='bob'")
        assert all(row['id'] in text for row in own)
        assert all(row['id'] not in text for row in other)
    finally:
        for phone in (alice, bob, linda):
            await phone.http.aclose()


async def test_new_diagnostics_add_no_mcp_consent_tools_or_preference_write(server):  # noqa: F811
    engine = server.rt.services.engine
    async with client_for(server, 'alice') as client:
        tools = (await client.list_tools()).tools
        assert [tool.name for tool in tools] == NAMES
        assert len(tools) == 35
        assert not any(word in tool.name for tool in tools for word in ('fairness_preference', 'fallback_approve', 'approve_for'))
        before = engine.db.all('SELECT * FROM fairness_preferences')
        await call(client, 'planning_options', {'plan': PLAN, 'mode': 'fairness', 'enabled': True, 'member_id': 'bob'})
        assert engine.db.all('SELECT * FROM fairness_preferences') == before


async def test_human_and_mcp_diagnostics_share_the_same_budget_and_never_create_business_receipts(server):  # noqa: F811
    engine = server.rt.services.engine
    engine.set_clock(fixed_clock('2026-10-19T14:00:00Z'))
    alice = await Phone(server, 'alice').login()
    try:
        before = business_state(engine)
        first = await alice.http.post(server.base + '/human/planning', headers=alice.headers(True), json={'plan': PLAN, 'mode': 'fairness'})
        assert first.status_code == 200, first.text
        assert 'action_receipt' not in first.json()
        assert business_state(engine) == before
        async with client_for(server, 'alice') as client:
            for mode in ('relaxations', 'fairness', 'relaxations', 'fairness'):
                result = await call(client, 'planning_options', {'plan': PLAN, 'mode': mode})
                assert 'error' not in result and not budget_denied(result), result
                assert 'action_receipt' not in result
        denied = await alice.http.post(server.base + '/human/planning', headers=alice.headers(True), json={'plan': PLAN, 'mode': 'fairness'})
        assert denied.status_code >= 400 and budget_denied(denied.json()), denied.text
        assert business_state(engine) == before
    finally:
        await alice.http.aclose()
