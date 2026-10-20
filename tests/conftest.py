from __future__ import annotations

import os
from pathlib import Path

# Most tests stand for a person's own device. Without a listener declaration the server would assume a shared room and leave
# plans kept from someone out of summaries (the product default); tests/mcp/test_room_mode.py covers that default explicitly.
os.environ.setdefault("GROUPFLOW_UNDECLARED_AUDIENCE", "private")
# Most MCP tests exercise the full lab tool set on /mcp; tests/mcp/test_voice.py runs the voice profile (the product default).
os.environ.setdefault("GROUPFLOW_TOOL_PROFILE", "lab")

import pytest

from groupflow.core.db import Db
from groupflow.core.errors import DomainError
from groupflow.engine import GroupFlow
from groupflow.world import load_world, seed_world

ROOT = Path(__file__).resolve().parents[1]
WORLD = load_world(ROOT / "demo" / "world.yaml")


def seeded(stages: dict[str, str] | None = None) -> GroupFlow:
    engine = GroupFlow(Db(":memory:"))
    seed_world(engine, WORLD, stages or {})
    return engine


@pytest.fixture
def gf() -> GroupFlow:
    return seeded()


def code(fn) -> str:
    """Run fn and return the DomainError code it raised, or NO_ERROR."""
    try:
        fn()
    except DomainError as e:
        return e.code
    return "NO_ERROR"


def item_id(engine: GroupFlow, label: str, plan: str | None = None) -> str:
    row = engine.db.get("SELECT id FROM items WHERE label = ? AND archived = 0" + (" AND plan_id = ?" if plan else ""), label, *([plan] if plan else []))
    if not row:
        raise KeyError(label)
    return row["id"]


def detail(engine: GroupFlow, member: str, key: str):
    for p in engine.context(member, mark_read=False).plans:
        for d in p.details:
            if d.id == key:
                return d
    return None


def task(engine: GroupFlow, member: str, title: str):
    for p in engine.context(member, mark_read=False).plans:
        for t in p.tasks:
            if t.title == title:
                return t
    return None


def plan_view(engine: GroupFlow, member: str, plan_id: str):
    return engine.context(member, plan=plan_id, mark_read=False).plans[0]


def person(member_id: str):
    return WORLD.person(member_id)
