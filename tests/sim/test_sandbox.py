"""The sandbox (`python -m groupflow sandbox`): its own database with the demo world seeded as the server seeds it, a page that opens
on the Echos instead of the story, and --fresh that starts both the database and its group chat over."""
from __future__ import annotations

import argparse

import httpx
import pytest

from groupflow.__main__ import _build_runtime
from groupflow.core.serve import serve_in_background
from groupflow.core.settings import chat_db_for
from groupflow.sim.app import create_sim_app
from groupflow.sim.llm.scripted import ScriptedProvider
from tests.conftest import ROOT
from tests.sim.harness import ADMIN_KEY, start_harness


def _args(db: str, fresh: bool = False) -> argparse.Namespace:
    return argparse.Namespace(db=db, fresh=fresh, port=None, world=str(ROOT / "demo" / "world.yaml"), stage=None)


def test_the_sandbox_seeds_the_demo_world_with_its_groups_and_plans(tmp_path):
    db = str(tmp_path / "sandbox.db")
    _, rt = _build_runtime(_args(db), sandbox=True)
    try:
        engine = rt.services.engine
        friends = engine.db.get("SELECT id FROM groups WHERE id = 'friends'")
        assert friends is not None, "the sandbox starts with the demo world's groups, unlike the story"
        assert engine.db.get("SELECT id FROM plans WHERE id = 'friday_dinner'") is not None
    finally:
        rt.db.close()


def test_fresh_starts_the_database_and_its_group_chat_over(tmp_path):
    db = str(tmp_path / "sandbox.db")
    chat = chat_db_for(db)
    _, rt = _build_runtime(_args(db), sandbox=True)
    rt.services.engine.create_group("alice", "Kept between runs")
    rt.db.close()
    (tmp_path / "sandbox.chat.db").write_text("old messages")
    _, rt = _build_runtime(_args(db), sandbox=True)
    try:
        assert rt.services.engine.db.get("SELECT id FROM groups WHERE name = 'Kept between runs'") is not None
    finally:
        rt.db.close()
    _, rt = _build_runtime(_args(db, fresh=True), sandbox=True)
    try:
        assert rt.services.engine.db.get("SELECT id FROM groups WHERE name = 'Kept between runs'") is None
        assert not (tmp_path / "sandbox.chat.db").exists() and chat.endswith("sandbox.chat.db")
    finally:
        rt.db.close()


@pytest.fixture
async def sandbox_sim():
    h = start_harness(ScriptedProvider([]), stages=None)
    app = create_sim_app(world=h.rt.world, agent=h.agent, sessions=h.sessions, traffic=h.traffic, mcp_url=h.url, server_base=h.bg.base,
                         admin_key=ADMIN_KEY, web_dist=None, sandbox=True)
    bg = serve_in_background(app)
    client = httpx.AsyncClient(base_url=bg.base, timeout=30)
    yield client
    await client.aclose()
    bg.stop()
    await h.close()


async def test_the_sandbox_page_has_no_story_to_play(sandbox_sim):
    cfg = (await sandbox_sim.get("/api/config")).json()
    assert cfg["sandbox"] is True and "showcase" not in cfg
    assert [s["id"] for s in cfg["scenarios"]] == ["family", "friends", "cottage", "team", "study"]
