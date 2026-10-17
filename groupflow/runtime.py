"""Wiring for the GroupFlow server: database, engine, demo world, worker and app."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from groupflow.checkins import build_checkins
from groupflow.core.db import Db
from groupflow.core.identity import Identity
from groupflow.core.settings import ServerSettings
from groupflow.engine import GroupFlow
from groupflow.engine.worker import Worker
from groupflow.mcp_server.app import Services, create_server_app
from groupflow.mcp_server.common import ToolCallLog
from groupflow.world import World, load_world, seed_group, seed_world, wipe_group

log = logging.getLogger("groupflow")


@dataclass
class Runtime:
    db: Db
    services: Services
    worker: Worker
    app: Any
    world: World


def parse_scenario(scenario: str | None, world: World) -> tuple[list[str], dict[str, str]]:
    """"friends:committed" -> (["friends"], {"friday_dinner": "committed"}); "all" or None -> every group."""
    if not scenario or scenario == "all":
        return [g["id"] for g in world.groups], {}
    group, _, stage = scenario.partition(":")
    if group not in [g["id"] for g in world.groups]:
        raise ValueError(f"Unknown scenario {scenario!r}")
    stages = {p["id"]: stage for p in world.plans_of(group) if stage and stage in (p.get("stages") or {})}
    return [group], stages


def build_runtime(settings: ServerSettings | None = None, db_path: str | None = None, stages: dict[str, str] | None = None,
                  on_call: Any = None, solver_budget_s: float = 3.0, worker_interval_s: float = 0.15, world_path: str | None = None) -> Runtime:
    settings = settings or ServerSettings()
    db = Db(db_path or settings.db_path)
    world = load_world(world_path or settings.world_path)
    identity = Identity(db)
    engine = GroupFlow(db, identity, solver_budget_s=solver_budget_s)
    engine.signup_mode = settings.signup if settings.signup in ("open", "invite", "off") else "off"
    engine.join_base = settings.join_url or None
    if engine.is_empty():
        seed_world(engine, world, stages or {})
        log.info("seeded the demo world (%d groups)", len(world.groups))

    def reset(scenario: str | None) -> None:
        groups, plan_stages = parse_scenario(scenario, world)
        for g in groups:
            wipe_group(db, g)
            seed_group(engine, world, g, {**(stages or {}), **plan_stages})
        engine.reset_rate_limits()

    services = Services(db=db, identity=identity, engine=engine, on_call=on_call, on_reset=reset)
    checkins = build_checkins(engine, settings.checkins)
    services.extra["checkins"] = checkins
    worker = Worker(engine, interval_s=worker_interval_s, checkins=checkins)
    app = create_server_app(services, host=settings.host, allowed_origins=settings.allowed_origins, demo_admin=settings.demo_admin,
                            admin_key=settings.admin_key, worker=worker, responses=settings.mcp_responses)
    return Runtime(db=db, services=services, worker=worker, app=app, world=world)


def log_call(c: ToolCallLog) -> None:
    status = c.summary if c.ok else f"ERROR {c.summary}"
    print(f"[mcp] {c.member:<9} {c.tool:<22} {status} ({c.ms:.1f} ms)", flush=True)
