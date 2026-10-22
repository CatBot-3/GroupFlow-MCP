"""The story clock of the demo: each day of the story sets only the simulated assistant's clock, and the story group exists only
once Alice makes it."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from groupflow.core.serve import serve_in_background
from groupflow.sim.app import create_sim_app
from groupflow.sim.demo_script import FRIENDS_SCRIPT, story_time
from groupflow.sim.llm.scripted import ScriptedProvider
from tests.sim.harness import ADMIN_KEY, start_harness

TZ = "America/Toronto"


async def _sim(model: ScriptedProvider, stages: dict[str, str]):
    h = start_harness(model, stages=stages, prefetch=True)
    app = create_sim_app(world=h.rt.world, agent=h.agent, sessions=h.sessions, traffic=h.traffic, mcp_url=h.url, server_base=h.bg.base,
                         admin_key=ADMIN_KEY, web_dist=None)
    bg = serve_in_background(app)
    client = httpx.AsyncClient(base_url=bg.base, timeout=60)
    client.model, client.h = model, h  # type: ignore[attr-defined]
    return client, bg, h


@pytest.fixture
async def story_sim():
    client, bg, h = await _sim(ScriptedProvider(), {"friday_dinner": "committed"})
    yield client
    await client.aclose()
    bg.stop()
    await h.close()


async def test_the_story_clock_moves_only_the_assistant_and_reset_restores_it(story_sim):
    sim = story_sim
    clock = sim.h.agent.now
    r = await sim.post("/api/demo/clock", json={"chapter": "Monday", "time": "19:40"})
    assert r.status_code == 200 and r.json()["today"].startswith("Monday") and "7:40 PM" in r.json()["today"]
    sim.model.push({"text": "Hello."})
    reply = await sim.post("/api/say", json={"member": "alice", "text": "hello", "scenario": "friends"})
    assert reply.json()["reply"] == "Hello."
    assert "It is Monday" in sim.model.requests[-1].system
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


@pytest.fixture
async def no_group_sim():
    client, bg, h = await _sim(ScriptedProvider(), {"friday_dinner": "none", "group:friends": "none"})
    yield client
    await client.aclose()
    bg.stop()
    await h.close()


async def test_the_story_group_does_not_exist_until_alice_makes_it_and_reset_removes_it(no_group_sim):
    sim = no_group_sim
    before = (await sim.get("/api/inspector", params={"scenario": "friends"})).json()
    assert before["group"] is None and before["plans"] == []
    sim.h.engine.create_group("alice", "Friends")
    made = (await sim.get("/api/inspector", params={"scenario": "friends"})).json()
    assert made["group"]["id"] == "friends" and [m["id"] for m in made["group"]["members"]] == ["alice"]
    assert (await sim.post("/api/reset", json={"scenario": "friends"})).status_code == 200
    assert (await sim.get("/api/inspector", params={"scenario": "friends"})).json()["group"] is None
