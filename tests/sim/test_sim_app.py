"""The simulator backend the web page uses: config for every group, turns, views through each person's own MCP session,
the inspector, resets, the demo console and the live event stream."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from groupflow.core.serve import serve_in_background
from groupflow.sim.app import create_sim_app
from groupflow.sim.llm.scripted import ScriptedProvider
from tests.sim.harness import ADMIN_KEY, start_harness


@pytest.fixture
async def sim():
    model = ScriptedProvider([])
    h = start_harness(model, stages={"friday_dinner": "committed"})
    app = create_sim_app(world=h.rt.world, agent=h.agent, sessions=h.sessions, traffic=h.traffic, mcp_url=h.url, server_base=h.bg.base,
                         admin_key=ADMIN_KEY, web_dist=None)
    bg = serve_in_background(app)
    client = httpx.AsyncClient(base_url=bg.base, timeout=30)
    client.model, client.h = model, h  # type: ignore[attr-defined]
    yield client
    await client.aclose()
    bg.stop()
    await h.close()


async def test_config_lists_every_group_with_people_plans_scripts_and_provider(sim):
    cfg = (await sim.get("/api/config")).json()
    assert [s["id"] for s in cfg["scenarios"]] == ["family", "friends", "cottage", "team", "study"]
    family, friends = cfg["scenarios"][0], cfg["scenarios"][1]
    assert [m["id"] for m in family["members"]] == ["alice", "bob", "charlie", "linda"]
    assert [p["id"] for p in family["plans"]] == ["party", "home"]
    assert friends["plans"][0]["template"] == "evening_out" and "committed" in friends["stages"]
    assert any(p["id"] == "restaurant_a" for p in friends["places"])
    assert family["script"][0]["member"] == "charlie" and friends["script"][0]["kind"] == "event"
    assert friends["members"][0]["groups"] == ["family", "friends"], "Alice is in two groups"
    assert len(cfg["templates"]) == 8
    assert cfg["provider"]["id"] == "scripted" and "token" not in str(cfg).lower()


async def test_human_proxy_preserves_query_and_guarded_new_plan_journey(sim):
    login = await sim.post('/human/login',json={'member_id':'alice','passcode':sim.h.rt.world.person('alice').passcode})
    session = login.json()
    headers = {'Authorization':'Bearer '+session['token'],'X-GF-CSRF':session['csrf']}
    created = await sim.post('/human/actions/apply',headers=headers,json={'tool':'create_plan','request_key':'proxy-create',
        'arguments':{'template':'general_event','title':'Proxy picnic','group':'friends','start_date':'2026-11-14','starter':False}})
    assert created.status_code==200,created.text
    plan = created.json()['plan']['id']
    resolved = await sim.get('/human/resolve',headers=headers,params={'plan':plan})
    assert resolved.status_code==200 and resolved.json()['version']
    added = await sim.post('/human/actions/apply',headers=headers,json={'tool':'propose_change','request_key':'proxy-add',
        'expected':resolved.json()['version'],'arguments':{'plan':plan,'new_type':'activity','title':'Picnic together',
            'place':'Local park','start':'18:00','end':'20:00','participants':['everyone']}})
    assert added.status_code==200 and added.json()['action_receipt']['plans']==[plan],added.text
    options = await sim.post('/human/planning',headers=headers,json={'plan':plan})
    assert options.status_code==200 and options.json()['alternatives'],options.text
    issued = await sim.post('/human/calendar',headers=headers,json={})
    assert issued.status_code==200
    feed = await sim.get(issued.json()['url'])
    assert feed.status_code==200 and 'BEGIN:VCALENDAR' in feed.text and 'Proxy picnic' not in feed.text
    assert feed.headers['cache-control']=='private, no-store'


async def test_say_runs_a_turn_records_the_transcript_and_rejects_bad_input(sim):
    sim.model.push({"tool_calls": [{"name": "get_context", "arguments": {}}]}, {"text": "You're all caught up."})
    r = await sim.post("/api/say", json={"member": "bob", "text": "What's new?", "scenario": "family"})
    assert r.status_code == 200, r.text
    turn = r.json()
    assert turn["reply"] == "You're all caught up." and turn["calls"][0]["name"] == "get_context"
    lines = (await sim.get("/api/transcripts")).json()["bob"]
    assert [x["role"] for x in lines] == ["user", "alexa"] and lines[1]["turn"]["calls"][0]["ok"] is True
    assert (await sim.post("/api/say", json={"member": "nobody", "text": "hi"})).status_code == 400
    assert (await sim.post("/api/say", json={"member": "bob", "text": "  "})).status_code == 400


async def test_llm_errors_are_reported_as_503(sim):
    r = await sim.post("/api/say", json={"member": "alice", "text": "hi"})
    assert r.status_code == 503 and "ran out" in r.json()["error"]


async def test_views_come_from_each_persons_own_session(sim):
    alice = (await sim.get("/api/view/alice")).json()["context"]
    linda = (await sim.get("/api/view/linda")).json()["context"]
    assert [p["title"] for p in alice["plans"]] == ["Linda's surprise party", "Home", "Friday dinner"]
    assert [p["title"] for p in linda["plans"]] == ["Home"]
    bob = (await sim.get("/api/view/bob", params={"plan": "friday_dinner"})).json()["context"]
    assert bob["plans"][0]["status"] == "COMMITTED"
    assert (await sim.get("/api/view/zed")).status_code == 400


async def test_memory_read_is_scoped_non_consuming_and_never_uses_inspector(sim):
    response = await sim.get('/api/memory/linda', params={'group': 'family', 'plan': 'home'})
    assert response.status_code == 200
    data = response.json()
    assert data['source'] == 'mcp' and data['last_turn'] is None
    assert "Linda's surprise party" not in response.text
    assert all(n['source'].startswith('get_context.') for n in data['graph']['nodes'])
    panel_calls = [e for e in sim.h.traffic.entries if e['source'] == 'panel' and e['kind'] == 'request' and e['method'] == 'tools/call']
    assert panel_calls[-1]['payload']['params']['arguments']['mark_read'] is False
    # A plan kept from her is absent, not an error: the map shows what her session does return, with no trace of the party.
    absent = await sim.get('/api/memory/linda', params={'group': 'family', 'plan': 'party'})
    assert absent.status_code == 200 and absent.json()['plan_not_visible'] == 'party'
    assert 'surprise' not in absent.text.lower() and [n['label'] for n in absent.json()['graph']['nodes'] if n['kind'] == 'plan'] == ['Home']
    assert (await sim.get('/api/memory/nobody')).status_code == 400


async def test_memory_trace_is_actual_model_input_and_clears_on_forget_or_reset(sim):
    sim.h.agent.prefetch_state = True
    sim.model.push({'text': 'Your home list is up to date.'})
    await sim.post('/api/say', json={'member': 'linda', 'text': 'What is on my list?', 'scenario': 'family', 'plan': 'home'})
    data = (await sim.get('/api/memory/linda', params={'group': 'family', 'plan': 'home'})).json()
    trace = data['last_turn']
    assert trace['text'] == 'What is on my list?' and trace['calls'][0]['sent_to_model']
    assert trace['calls'][0]['model_content'] == sim.model.requests[0].messages[-1].content
    other = (await sim.get('/api/memory/linda', params={'group': 'friends'})).json()
    assert other['last_turn'] is None
    await sim.post('/api/forget', json={'member': 'linda'})
    assert (await sim.get('/api/memory/linda', params={'group': 'family', 'plan': 'home'})).json()['last_turn'] is None


async def test_exhausted_tool_budget_is_not_reported_as_model_consumption(sim):
    sim.h.agent.prefetch_state = True
    sim.h.agent.max_steps = 1
    sim.model.push({'tool_calls': [{'name': 'get_history', 'arguments': {'plan': 'home'}}]})
    await sim.post('/api/say', json={'member': 'linda', 'text': 'Read the recent home history.', 'scenario': 'family', 'plan': 'home'})
    trace = (await sim.get('/api/memory/linda', params={'group': 'family', 'plan': 'home'})).json()['last_turn']
    assert trace['calls'][0]['sent_to_model']
    assert not trace['calls'][-1]['sent_to_model'] and trace['calls'][-1]['model_content'] is None


async def test_memory_does_not_replay_a_trace_after_visibility_is_removed(sim):
    sim.h.agent.prefetch_state = True
    sim.model.push({'text': 'Your home list is up to date.'})
    await sim.post('/api/say', json={'member': 'linda', 'text': 'Read Home.', 'scenario': 'family', 'plan': 'home'})
    sim.h.engine.db.run('DELETE FROM space_members WHERE member_id=?', 'linda')
    data = (await sim.get('/api/memory/linda', params={'group': 'family'})).json()
    assert data['last_turn'] is None
    assert not any(n['kind'] in ('plan', 'item', 'version', 'confirmation') for n in data['graph']['nodes'])


async def test_inspector_per_group(sim):
    fam = (await sim.get("/api/inspector", params={"scenario": "family"})).json()
    assert [p["id"] for p in fam["plans"]] == ["party", "home"] and fam["group"]["members"]
    fr = (await sim.get("/api/inspector", params={"scenario": "friends"})).json()
    assert fr["plans"][0]["status"] == "COMMITTED" and len(fr["group"]["members"]) == 4


async def test_demo_event_degrades_the_plan_and_reset_restores_one_group(sim):
    r = await sim.post("/api/demo/event", json={"group": "friends", "place_id": "restaurant_a", "start": "18:00", "end": "21:00"})
    assert r.status_code == 200 and r.json()["outcome"] == "recorded"
    fr = (await sim.get("/api/inspector", params={"scenario": "friends"})).json()
    assert fr["plans"][0]["status"] == "DEGRADED"
    sim.model.push({"text": "ok"})
    await sim.post("/api/say", json={"member": "linda", "text": "hi"})
    assert sim.h.agent.history.get("linda")
    assert (await sim.post("/api/reset", json={"scenario": "friends:committed"})).json() == {"ok": True}
    fr = (await sim.get("/api/inspector", params={"scenario": "friends"})).json()
    assert fr["plans"][0]["status"] == "COMMITTED"
    assert sim.h.agent.history.get("linda"), "a reset of the friends leaves Linda's conversation alone"
    await sim.post("/api/reset", json={})
    assert sim.h.agent.history == {}


async def test_forget_wipes_chat_memory_but_not_the_plan(sim):
    sim.model.push({"text": "Charlie wants Sunday. Make it official?"})
    await sim.post("/api/say", json={"member": "alice", "text": "What's new?"})
    assert (await sim.post("/api/forget", json={"member": "alice"})).json() == {"ok": True}
    assert "alice" not in sim.h.agent.history
    assert (await sim.post("/api/forget", json={"member": "ghost"})).status_code == 400


async def test_stream_pushes_traffic_and_turns(sim):
    received: list[str] = []

    async def listen() -> None:
        async with httpx.AsyncClient(base_url=str(sim.base_url), timeout=30) as c:
            async with c.stream("GET", "/api/stream") as r:
                async for line in r.aiter_lines():
                    received.append(line)
                    if line == "event: turn":
                        return

    task = asyncio.create_task(listen())
    await asyncio.sleep(0.3)
    sim.model.push({"tool_calls": [{"name": "get_context", "arguments": {}}]}, {"text": "The plan is agreed."})
    await sim.post("/api/say", json={"member": "dana", "text": "What's the plan?"})
    await asyncio.wait_for(task, 10)
    assert "event: traffic" in received and "event: turn" in received


async def test_human_pass_through_forwards_the_persons_own_session_only(sim):
    login = await sim.post("/human/login", json={"member_id": "bob", "passcode": "bob-demo"})
    assert login.status_code == 200
    token, csrf = login.json()["token"], login.json()["csrf"]
    inbox = await sim.get("/human/inbox", headers={"authorization": f"Bearer {token}"})
    assert inbox.status_code == 200 and inbox.json()["member"]["name"] == "Bob"
    assert (await sim.get("/human/inbox")).status_code == 401
    assert (await sim.get("/human/inbox", headers={"authorization": "Bearer gf_demo_bob_2m9q"})).status_code == 401
    body = {"kind": "home_by", "value": {"time": "21:00"}, "plan_id": "friday_dinner"}
    assert (await sim.post("/human/conditions", headers={"authorization": f"Bearer {token}"}, json=body)).status_code == 403
    r = await sim.post("/human/conditions", headers={"authorization": f"Bearer {token}", "x-gf-csrf": csrf}, json=body)
    assert r.status_code == 200, r.text
