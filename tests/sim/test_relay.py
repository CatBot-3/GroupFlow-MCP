"""The relay model (an outside agent answers each completion over /api/relay) and the story clock of the guided demo."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from groupflow.core.serve import serve_in_background
from groupflow.sim.app import create_sim_app
from groupflow.sim.demo_script import FRIENDS_SCRIPT, story_time
from groupflow.sim.llm.relay import RelayProvider
from tests.sim.harness import ADMIN_KEY, start_harness

TZ = "America/Toronto"


@pytest.fixture
async def relay_sim():
    model = RelayProvider(label="Outside agent", model="test")
    h = start_harness(model, stages={"friday_dinner": "committed"}, prefetch=True)
    app = create_sim_app(world=h.rt.world, agent=h.agent, sessions=h.sessions, traffic=h.traffic, mcp_url=h.url, server_base=h.bg.base,
                         admin_key=ADMIN_KEY, web_dist=None)
    bg = serve_in_background(app)
    client = httpx.AsyncClient(base_url=bg.base, timeout=60)
    client.model, client.h = model, h  # type: ignore[attr-defined]
    yield client
    await client.aclose()
    bg.stop()
    await h.close()


async def next_request(sim: httpx.AsyncClient) -> dict:
    r = await sim.get("/api/relay/next", params={"wait": 20})
    assert r.status_code == 200
    return r.json()


async def test_an_outside_agent_answers_each_completion_and_its_tool_calls_run_over_mcp(relay_sim):
    sim = relay_sim
    turn = asyncio.create_task(sim.post("/api/say", json={"member": "alice", "text": "What's the plan for Friday?", "scenario": "friends"}))
    first = await next_request(sim)
    assert "Alice's Echo" in first["system"] and {t["name"] for t in first["tools"]} >= {"get_context", "propose_change"}
    assert first["messages"][0] == {"role": "user", "content": "What's the plan for Friday?"}
    assert first["messages"][-1]["role"] == "tool" and first["messages"][-1]["name"] == "get_context", "the device's own state read"
    assert (await sim.post(f"/api/relay/{first['id']}", json={"tool_calls": [{"name": "explain", "arguments": {}}]})).status_code == 200
    second = await next_request(sim)
    assert second["messages"][-1]["role"] == "tool" and second["messages"][-1]["name"] == "explain"
    assert (await sim.post(f"/api/relay/{second['id']}", json={"text": "Dinner is set for Friday."})).status_code == 200
    reply = (await turn).json()
    assert reply["reply"] == "Dinner is set for Friday." and reply["provider"] == "Outside agent"
    assert [c["name"] for c in reply["calls"] if not c.get("prefetched")] == ["explain"]
    # A request is answered once; an empty answer is refused.
    assert (await sim.post(f"/api/relay/{second['id']}", json={"text": "again"})).status_code == 404
    late = asyncio.create_task(sim.post("/api/say", json={"member": "bob", "text": "hi", "scenario": "friends"}))
    req = await next_request(sim)
    assert (await sim.post(f"/api/relay/{req['id']}", json={"text": "  "})).status_code == 400
    assert (await sim.post(f"/api/relay/{req['id']}", json={"text": "Hi Bob."})).status_code == 200
    assert (await late).json()["reply"] == "Hi Bob."
    assert (await sim.post("/api/relay/done", json={})).json()["done"] is True
    assert (await next_request(sim)) == {"done": True}


async def test_the_story_clock_moves_only_the_assistant_and_reset_restores_it(relay_sim):
    sim = relay_sim
    clock = sim.h.agent.now
    r = await sim.post("/api/demo/clock", json={"chapter": "Monday", "time": "19:40"})
    assert r.status_code == 200 and r.json()["today"].startswith("Monday") and "7:40 PM" in r.json()["today"]
    turn = asyncio.create_task(sim.post("/api/say", json={"member": "alice", "text": "hello", "scenario": "friends"}))
    req = await next_request(sim)
    assert "It is Monday" in req["system"]
    await sim.post(f"/api/relay/{req['id']}", json={"text": "Hello."})
    await turn
    assert (await sim.post("/api/demo/clock", json={"chapter": "Someday"})).status_code == 400
    assert (await sim.post("/api/reset", json={"scenario": "friends"})).status_code == 200
    assert sim.h.agent.now is clock


@pytest.mark.parametrize("today, monday, friday", [
    ("2026-10-22T12:00:00-04:00", "2026-10-19", "2026-10-23"),  # Thursday: the week of tomorrow's Friday
    ("2026-10-23T09:00:00-04:00", "2026-10-19", "2026-10-23"),  # Friday: tonight's dinner
    ("2026-10-24T09:00:00-04:00", "2026-10-26", "2026-10-30"),  # Saturday: next week's Friday
])
def test_story_days_fall_in_the_week_that_ends_on_the_coming_friday(today, monday, friday):
    now = datetime.fromisoformat(today)
    assert story_time("Monday", "19:40", now, TZ).date().isoformat() == monday
    assert story_time("Friday", "16:30", now, TZ).isoformat().startswith(f"{friday}T16:30")


def test_every_chapter_of_the_story_has_a_clock_in_order():
    days = [(s["chapter"], s["clock"]) for s in FRIENDS_SCRIPT if s.get("chapter")]
    assert [d for d, _ in days] == ["Monday", "Tuesday", "Wednesday", "Friday"]
    now = datetime(2026, 10, 22, 16, tzinfo=timezone.utc)
    times = [story_time(d, c, now, TZ) for d, c in days]
    assert times == sorted(times)
    # The check-in skips 26 quiet hours: Tuesday's clock plus 26 hours is about Wednesday's.
    assert abs((times[2] - times[1]).total_seconds() / 3600 - 26) < 0.5
