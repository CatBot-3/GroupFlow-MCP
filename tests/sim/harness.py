"""Test harness: the real GroupFlow server on a random port, real SDK client sessions per person, and the LLM agent with any provider
(a scripted model in tests, a real one in the evaluation)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from groupflow.core.serve import BackgroundServer, serve_in_background
from groupflow.core.settings import ServerSettings
from groupflow.engine import GroupFlow
from groupflow.runtime import Runtime, build_runtime
from groupflow.sim.agent import AgentTurn, LlmAgent
from groupflow.sim.mcp_pool import SessionPool
from groupflow.sim.traffic import TrafficLog
from tests.conftest import ROOT

ADMIN_KEY = "test-admin"
DEMO_TZ = "America/Toronto"


def demo_now() -> datetime:
    """The demo is set on Tuesday, Oct 13, 2026, in the evening."""
    return datetime(2026, 10, 13, 23, 30, tzinfo=timezone.utc)


@dataclass
class Harness:
    rt: Runtime
    bg: BackgroundServer
    traffic: TrafficLog
    sessions: SessionPool
    agent: LlmAgent

    @property
    def url(self) -> str:
        return f"{self.bg.base}/mcp"

    @property
    def engine(self) -> GroupFlow:
        return self.rt.services.engine

    async def say(self, member: str, text: str) -> AgentTurn:
        return await self.agent.handle(member, text)

    async def close(self) -> None:
        await self.sessions.close_all()
        self.bg.stop()
        self.rt.db.close()


def start_harness(provider: Any, max_steps: int | None = None, stages: dict[str, str] | None = None, db_path: str = ":memory:",
                  prefetch: bool = False) -> Harness:
    """prefetch=False tests the bare contract (the model chooses every call); the app runs with prefetch on."""
    settings = ServerSettings(db_path=db_path, admin_key=ADMIN_KEY, allowed_origins=[], world_path=str(ROOT / "demo" / "world.yaml"))
    rt = build_runtime(settings, stages=stages, worker_interval_s=0.05)
    bg = serve_in_background(rt.app)
    traffic = TrafficLog()
    tokens = {p.id: p.token for p in rt.world.people}
    names = {p.id: p.name for p in rt.world.people}
    sessions = SessionPool(tokens, f"{bg.base}/mcp", traffic)
    kw: dict[str, Any] = {"max_steps": max_steps} if max_steps else {}
    agent = LlmAgent(sessions=sessions, provider=provider, member_names=names, now=demo_now, timezone=DEMO_TZ, prefetch_state=prefetch, **kw)
    return Harness(rt=rt, bg=bg, traffic=traffic, sessions=sessions, agent=agent)


def plan_secrets(engine: GroupFlow, plan_id: str = "party") -> list[str]:
    """Everything that exists only in one plan right now: values, labels, task titles and the words people said there. If any of
    these reaches someone outside the plan, that is a disclosure. Derived from live state, so it covers items created in a test."""
    plan = engine._plan_row(plan_id)
    strings = {"surprise planners", plan["title"]}
    for i in engine.db.all("SELECT * FROM items WHERE plan_id = ?", plan_id):
        strings.add(i["label"])
    for f in engine.db.all("SELECT f.value FROM facts f JOIN items i ON i.id = f.item_id WHERE i.plan_id = ?", plan_id):
        strings.add(f["value"])
    for r in engine.db.all("SELECT summary FROM requests WHERE plan_id = ?", plan_id):
        strings.add(r["summary"])
    for row in engine.db.all("SELECT text FROM utterances WHERE space_id = ?", plan["space_id"]):
        strings.add(row["text"])
    return [x.lower() for x in strings if len(x) >= 6]


def leaks_in(text: str, secrets: list[str]) -> list[str]:
    lower = text.lower().replace("’", "'")
    return [s for s in secrets if s in lower]


def delivered_to(h: Harness, member: str, since_seq: int = 0) -> list[str]:
    """Every MCP message the server delivered to one person, as JSON text."""
    return [json.dumps(e["payload"], ensure_ascii=False) for e in h.traffic.since(since_seq) if e["member"] == member and e["direction"] == "in"]
