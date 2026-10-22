"""The demo world: people, groups and plans from demo/world.yaml, seeded through the engine.

Plans are created from their templates and filled by replaying what people
said (`script`) through the same engine calls the MCP tools use, with a
scripted clock where a step gives one, so every value has real provenance.
Agreement plans can start at different stages (draft, proposed, awaiting,
committed, degraded); later stages are reached through the real flows: a
request for confirmations, each person confirming on their own page, a commit,
a verified outside fact.
"""
from __future__ import annotations

import json

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from groupflow.core.db import Db
from groupflow.core.identity import Person, seed_people
from groupflow.core.settings import REPO_ROOT
from groupflow.core.util import iso_now, new_id, now
from groupflow.engine import ASSISTANT, GroupFlow, fixed_clock
from groupflow.engine.text import day_label

WORLD_PATH = REPO_ROOT / "demo" / "world.yaml"
STAGES = ("draft", "proposed", "awaiting", "committed", "degraded")


@dataclass
class World:
    people: list[Person]
    groups: list[dict[str, Any]]
    plans: list[dict[str, Any]]
    raw: dict[str, Any] = field(default_factory=dict)

    def person(self, member_id: str) -> Person:
        return next(p for p in self.people if p.id == member_id)

    def group(self, group_id: str) -> dict[str, Any]:
        return next(g for g in self.groups if g["id"] == group_id)

    def plans_of(self, group_id: str) -> list[dict[str, Any]]:
        return [p for p in self.plans if p["group"] == group_id]

    def stages_of(self, plan_id: str) -> list[str]:
        spec = next((p for p in self.plans if p["id"] == plan_id), None)
        if not spec or "stages" not in spec:
            return []
        return [s for s in spec["stages"] if s != "default" and isinstance(spec["stages"][s], list)] or []

    def default_stage(self, plan_id: str) -> str | None:
        spec = next((p for p in self.plans if p["id"] == plan_id), None)
        return (spec or {}).get("stages", {}).get("default")


def load_world(path: str | Path | None = None) -> World:
    raw = yaml.safe_load(Path(path or WORLD_PATH).read_text(encoding="utf-8"))
    people = [Person(id=p["id"], name=p["name"], token=p["token"], passcode=p["passcode"], color=p.get("color", "#666666")) for p in raw["people"]]
    return World(people=people, groups=raw["groups"], plans=raw["plans"], raw=raw)


def resolve_date(spec: Any, tz: str, today: date | None = None) -> str:
    """A fixed ISO date, or {prefer: ISO, else: "next friday"}: the preferred date while it is still ahead, so approvals are never born expired."""
    today = today or now().astimezone(ZoneInfo(tz)).date()
    if isinstance(spec, str):
        return spec
    prefer = spec.get("prefer")
    if prefer and date.fromisoformat(prefer) > today:
        return prefer
    rule = str(spec.get("else", "next friday")).lower().replace("next ", "")
    names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    i = names.index(rule) if rule in names else 4
    ahead = (i - today.weekday()) % 7 or 7
    return (today + timedelta(days=ahead)).isoformat()


def seed_world(engine: GroupFlow, world: World, stages: dict[str, str] | None = None, groups: list[str] | None = None) -> None:
    seed_people(engine.db, world.people)
    for g in world.groups:
        if groups is None or g["id"] in groups:
            seed_group(engine, world, g["id"], stages or {})
    engine.reset_rate_limits()


def seed_group(engine: GroupFlow, world: World, group_id: str, stages: dict[str, str]) -> None:
    if stages.get(f"group:{group_id}") == "none":
        return  # the guided demo creates this group on camera, and its people join through an invite
    g = world.group(group_id)
    engine.add_group(g["id"], g["name"], g.get("timezone", "America/Toronto"), g.get("currency", "CAD"),
                     [{"id": m["id"], "role": m.get("role", "member"), "label": m.get("label"), "confirmation_mode": m.get("confirmation_mode", "scoped")}
                      for m in g["members"]], g.get("description"))
    for s in g.get("spaces", []):
        engine.add_space(s["id"], g["id"], s["name"], "shared", s["members"], s.get("description"))
    for spec in world.plans_of(group_id):
        stage = stages.get(spec["id"]) or spec.get("stages", {}).get("default")
        if stage == "none":
            continue  # this plan is made on camera instead (the guided demo starts without it)
        seed_plan(engine, world, spec, stage)
    # Earlier notifications were heard back then; the demo starts with clean inboxes.
    plan_ids = [p["id"] for p in world.plans_of(group_id)]
    marks = ",".join("?" for _ in plan_ids) or "''"
    engine.db.run(f"UPDATE notifications SET read_at = created_at WHERE read_at IS NULL AND plan_id IN ({marks})", *plan_ids)
    engine.reset_rate_limits()


def _fmt(value: Any, refs: dict[str, str], start_date: str) -> Any:
    if isinstance(value, str):
        if value.startswith("$") and value[1:] in refs:
            return refs[value[1:]]
        return re.sub(r"\{day:(\d+)\}", lambda m: day_label(start_date, int(m.group(1))), value)
    if isinstance(value, list):
        return [_fmt(v, refs, start_date) for v in value]
    if isinstance(value, dict):
        return {k: _fmt(v, refs, start_date) for k, v in value.items()}
    return value


def seed_plan(engine: GroupFlow, world: World, spec: dict[str, Any], stage: str | None) -> str:
    g = world.group(spec["group"])
    tz = g.get("timezone", "America/Toronto")
    start_date = resolve_date(spec["start_date"], tz)
    script = spec.get("script", [])
    first_at = next((s["at"] for s in script if "at" in s), None)
    previous_clock = engine._now_override
    # Seeding is never an assistant conversation, even when a test seeds a world while acting as one: the template's starter
    # is complete and nothing is offered.
    not_assistant = ASSISTANT.set(False)
    try:
        if first_at:
            engine.set_clock(fixed_clock(first_at))
        engine.create_plan(spec["organizer"], spec["template"], spec["title"], group=g["id"], start_date=start_date, days=spec.get("days"),
                           hidden_from=spec.get("hidden_from"), audience=spec.get("audience"), starter=spec.get("starter", True), plan_id=spec["id"],
                           lifecycle=spec.get("lifecycle"), preset=spec.get("preset"))
        if spec.get("project"):
            data = spec["project"]
            engine.configure_project(spec["organizer"], spec["id"], data.get("config", {}))
            work_refs = {}
            first = date.fromisoformat(start_date)
            for w in data.get("work", []):
                ws = dict(w.get("spec", {}))
                ws["not_before"] = (first + timedelta(days=w.get("start_day",0))).isoformat()
                ws["due"] = (first + timedelta(days=w.get("due_day",spec.get("days",42)-1))).isoformat()
                ws["depends_on"] = [work_refs[d] for d in w.get("depends_on", [])]
                result = engine.upsert_work(spec["organizer"], spec["id"], ws, title=w["title"], owner=w["owner"])
                work_refs[w["key"]] = result["item"]
            for series in data.get("series", []):
                rule = dict(series["rule"], dtstart=(first+timedelta(days=series.get("start_day",0))).isoformat())
                engine.manage_recurrence(spec["organizer"], spec["id"], "create", title=series["title"], owner=series["owner"], rule=rule, spec=series.get("spec"))
            if data.get("prepare", True):
                engine.repair_project(spec["organizer"], spec["id"])
                engine.request_confirmations(spec["organizer"], plan=spec["id"])
        for a in spec.get("activities", []):
            engine.propose_change(spec["organizer"], plan=spec["id"], new_type=a.get("type"), item=a["key"], title=a["title"], day=a.get("day"),
                                  start=a["start"], end=a["end"], place=a.get("place"))
        for c in spec.get("conditions", []):
            res = engine.set_my_conditions(c["owner"], [{"kind": c["name"], "value": c["value"]}], plan=spec["id"], source="seed", notify=False)
            engine.db.run("UPDATE conditions SET status = 'active', verified_at = ? WHERE id = ?", iso_now(), res.conditions[0].id)
        refs: dict[str, str] = {}
        for step in script:
            if "at" in step:
                engine.set_clock(fixed_clock(step["at"]))
            args = {k: _fmt(v, refs, start_date) for k, v in step.items() if k not in ("at", "as", "do", "ref")}
            if step["do"] == "change":
                res = engine.propose_change(step["as"], plan=spec["id"], **args)
                if step.get("ref"):
                    refs[step["ref"]] = res.item
            elif step["do"] == "link":
                engine.link_items(step["as"], args["item"], args["depends_on"], plan=spec["id"])
            elif step["do"] == "poll":
                res = engine.poll(step["as"], args.pop("action", "start"), plan=spec["id"], **args)
                if step.get("ref") and res.get("poll"):
                    refs[step["ref"]] = res["poll"]["id"]
            elif step["do"] == "rsvp":
                engine.rsvp(step["as"], args["answer"], plan=spec["id"], plus_ones=args.get("plus_ones"))
            elif step["do"] == "bring":
                engine.bring_list(step["as"], args.get("action", "bring"), args["things"], plan=spec["id"])
            elif step["do"] == "costs":
                engine.costs(step["as"], args.pop("action", "add"), plan=spec["id"], **args)
            else:
                raise ValueError(f"unknown script step {step['do']!r} in {spec['id']}")
        engine.set_clock(previous_clock)
        if stage and "stages" in spec:
            steps = spec["stages"].get(stage)
            if not isinstance(steps, list):
                if stage != spec["stages"].get("default"):
                    raise ValueError(f"{spec['id']} has no stage {stage!r}")
                steps = spec["stages"].get(stage) if isinstance(spec["stages"].get(stage), list) else []
            run_stage(engine, spec["id"], steps, start_date)
    finally:
        engine.set_clock(previous_clock)
        ASSISTANT.reset(not_assistant)
    return spec["id"]


def run_stage(engine: GroupFlow, plan_id: str, steps: list[Any], start_date: str) -> None:
    plan = engine._plan_row(plan_id)
    for step in steps:
        name = step if isinstance(step, str) else next(iter(step))
        if name == "propose":
            engine.propose_draft(plan["organizer_id"], plan_id)
        elif name == "send":
            engine.request_confirmations(plan["organizer_id"], plan=plan_id)
        elif name == "confirm_all":
            for m in engine.plan_audience(plan):
                approve_all(engine, engine.identity.get(m), plan_id)
        elif name == "commit":
            engine.commit_plan(plan["organizer_id"], plan=plan_id)
        elif name == "observe":
            body = dict(step)
            kind = body.pop("observe")
            payload = {"place_id": body["place"], "from": body.get("from", "00:00"), "to": body.get("to", "23:59"), "date": body.get("date", start_date)}
            engine.inject_observation(plan["group_id"], kind, payload, source="demo_console", source_event_id=f"seed-{plan_id}-{new_id('o')}")
        else:
            raise ValueError(f"unknown stage step {name!r}")
        engine.reset_rate_limits()


def approve_all(engine: GroupFlow, member: Any, plan_id: str | None = None, decision: str = "approved") -> list[str]:
    """Act as the person on their own page: open each pending item and answer it."""
    out = []
    for item in engine.human_inbox(member)["pending"]:
        if plan_id and item["plan_id"] != plan_id:
            continue
        ch = engine.human_challenge(member, item["scope_id"])
        out.append(engine.human_decide(member, ch["challenge_id"], ch["nonce"], decision)["outcome"])
    return out


def wipe_group(db: Db, group_id: str) -> None:
    """Delete one group and everything in it (demo reset). Other groups, and the people, stay."""
    def q(sql: str, *params: Any) -> list[str]:
        return [next(iter(r.values())) for r in db.all(sql, *params)]

    def run() -> None:
        plans = q("SELECT id FROM plans WHERE group_id = ?", group_id)
        spaces = q("SELECT id FROM spaces WHERE group_id = ?", group_id)
        items = q(f"SELECT id FROM items WHERE plan_id IN ({','.join('?' * len(plans)) or chr(39) * 2})", *plans) if plans else []
        revs = q(f"SELECT id FROM plan_revisions WHERE plan_id IN ({','.join('?' * len(plans))})", *plans) if plans else []
        jobs = q(f"SELECT id FROM repair_jobs WHERE plan_id IN ({','.join('?' * len(plans))})", *plans) if plans else []

        def delete(table: str, column: str, ids: list[str]) -> None:
            for i in range(0, len(ids), 400):
                chunk = ids[i:i + 400]
                db.run(f"DELETE FROM {table} WHERE {column} IN ({','.join('?' * len(chunk))})", *chunk)

        delete("resource_reservations", "plan_id", plans)
        # New coordination records refer to scopes/revisions that the reset
        # deletes before plans. Clear children first, for this group only.
        delete("fallback_uses", "revision_id", revs)
        delete("fallback_authorizations", "plan_id", plans)
        delete("fallback_challenges", "plan_id", plans)
        delete("agreement_contests", "plan_id", plans)
        delete("weather_watches", "plan_id", plans)  # cascades provider readings
        delete("diagnostic_queries", "plan_id", plans)
        db.run("DELETE FROM fairness_history WHERE group_id = ?", group_id)
        db.run("DELETE FROM fairness_preferences WHERE group_id = ?", group_id)
        delete("project_revisions", "plan_id", plans)  # cascades scopes, challenges, receipts before task deletion

        delete("links", "item_id", items)
        delete("links", "depends_on_id", items)
        delete("flags", "target_id", items)
        delete("flags", "cause_id", items)
        delete("requests", "plan_id", plans)
        delete("followups", "plan_id", plans)
        delete("delivery_preferences", "plan_id", plans)
        for receipt in db.all('SELECT id,plan_ids_json FROM action_receipts'):
            if set(json.loads(receipt['plan_ids_json'])) & set(plans):
                db.run('DELETE FROM action_receipts WHERE id=?', receipt['id'])
        delete("facts", "item_id", items)
        delete("items", "plan_id", plans)
        delete("notifications", "plan_id", plans)
        delete("notifications", "space_id", spaces)
        delete("audit", "plan_id", plans)
        db.run("DELETE FROM audit WHERE group_id = ?", group_id)
        delete("approval_carry_forwards", "target_revision_id", revs)
        delete("challenges", "revision_id", revs)
        delete("approval_receipts", "plan_id", plans)
        delete("approval_scopes", "revision_id", revs)
        delete("confirmation_rounds", "plan_id", plans)
        delete("repair_candidates", "job_id", jobs)
        delete("repair_jobs", "plan_id", plans)
        delete("executions", "plan_id", plans)
        delete("plan_revisions", "plan_id", plans)
        db.run("DELETE FROM conditions WHERE group_id = ?", group_id)
        db.run("DELETE FROM observations WHERE group_id = ?", group_id)
        db.run("DELETE FROM idempotency")
        db.run("DELETE FROM plans WHERE group_id = ?", group_id)
        delete("utterances", "space_id", spaces)
        delete("space_members", "space_id", spaces)
        db.run("DELETE FROM spaces WHERE group_id = ?", group_id)
        db.run("DELETE FROM group_members WHERE group_id = ?", group_id)
        db.run("DELETE FROM join_requests WHERE group_id = ?", group_id)
        db.run("DELETE FROM group_invites WHERE group_id = ?", group_id)
        db.run("DELETE FROM groups WHERE id = ?", group_id)
    db.tx(run)


__all__ = ["World", "load_world", "seed_world", "seed_group", "seed_plan", "wipe_group", "approve_all", "resolve_date", "STAGES", "WORLD_PATH"]
