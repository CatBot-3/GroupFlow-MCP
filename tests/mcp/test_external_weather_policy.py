"""Removed routes, portable MCP policy and ordinary planning without weather."""
from __future__ import annotations

import json

import httpx2
import pytest

from groupflow.host_guidance import OPTIONAL_WEATHER_GUIDANCE, LOCAL_HOST_CAPABILITIES
from tests.shared_workload import call, connect, phone, start_shared_server
from tests.mcp.test_mcp import INIT, JSON_HEADERS


@pytest.fixture
def policy_server(tmp_path):
    server = start_shared_server(tmp_path / 'no-weather.sqlite3', worker=False)
    try:
        yield server
    finally:
        server.stop()


@pytest.mark.parametrize('method,path,body', [
    ('GET', '/human/weather/watches?plan=dinner', None),
    ('POST', '/human/weather/watches', {'plan': 'dinner', 'activity': 'meet', 'latitude': 43.65, 'longitude': -79.38}),
    ('POST', '/human/weather/refresh', {'id': 'old-watch'}),
    ('POST', '/human/weather/disable', {'id': 'old-watch'}),
])
async def test_retired_human_weather_routes_are_absent(policy_server, method, path, body):
    async with phone(policy_server, 'avery') as human:
        response = await human.http.request(method, path, headers=human.headers, json=body)
        assert response.status_code == 404, response.text
    assert not any('/human/weather/' in route.path for route in policy_server.app.api.routes if hasattr(route, 'path'))


async def test_real_initialize_exposes_optional_weather_policy_without_claiming_a_connection(policy_server):
    server = policy_server
    headers = {**JSON_HEADERS, 'authorization': 'Bearer ' + server.people['avery'].token}
    async with httpx2.AsyncClient() as http:
        response = await http.post(server.base + '/mcp', json=INIT, headers=headers)
        assert response.status_code == 200, response.text
        instructions = response.json()['result']['instructions']
        assert OPTIONAL_WEATHER_GUIDANCE in instructions
        assert LOCAL_HOST_CAPABILITIES not in instructions
    async with connect(server, 'avery') as client:
        tools = (await client.list_tools()).tools
        assert len(tools) == 35
        assert not any('weather' in tool.name.lower() or 'forecast' in tool.name.lower() for tool in tools)


async def test_no_weather_mcp_still_allows_normal_personal_confirmation_and_commit(policy_server):
    server = policy_server
    gf = server.engine
    activity = server.items['dinner']['activity']
    async with connect(server, 'avery') as client:
        changed = await call(client, 'propose_change', {'plan': 'dinner', 'item': activity, 'start': '18:15', 'end': '19:15'})
        assert 'error' not in changed, changed
        sent = await call(client, 'request_confirmations', {'plan': 'dinner'})
        assert 'error' not in sent, sent
        refused = await call(client, 'commit_plan', {'plan': 'dinner'})
        assert refused.get('outcome') != 'committed', refused
        for mid in ('avery', 'blair', 'casey'):
            async with phone(server, mid) as human:
                answers = await human.approve('dinner')
                assert answers and all(answer.status_code == 200 for answer in answers)
        committed = await call(client, 'commit_plan', {'plan': 'dinner'})
        assert committed['outcome'] == 'committed', committed
        assert gf._plan_row('dinner')['status'] == 'COMMITTED'
        facts = await call(client, 'planning_options', {'plan': 'dinner', 'mode': 'facts'})
        assert 'error' not in facts and not any(item.get('kind') == 'forecast' for item in facts['facts'])
        assert not gf.db.scalar('SELECT count(*) FROM weather_watches')
        assert 'open-meteo' not in json.dumps(facts).lower()
