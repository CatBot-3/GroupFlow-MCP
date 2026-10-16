"""Finite candidate domain and objective for PlanRepair.

For each activity the domain lists the current choice first, then other places
of the same kind from the template's catalog, then time shifts allowed by the
template (a meeting may move a day; a dinner only a few minutes), then dropping
it if it is optional. The order is deterministic, so the same inputs always give
the same domain and the same proof.
"""
from __future__ import annotations

import math

from .model import DROP, SLOT, Activity, Objective, Problem, Scored, Snapshot

Option = tuple[int, str | None]


def option_of(a: Activity) -> Option:
    return (a.start, DROP if a.cancelled else a.place_id)


def build_domain(problem: Problem) -> dict[str, list[Option]]:
    if problem.domain_override is not None:
        return {k: list(v) for k, v in problem.domain_override.items()}
    places = problem.catalog.places
    horizon = max(1, problem.base.days) * 1440
    domain: dict[str, list[Option]] = {}
    for a in problem.base.activities:
        options: list[Option] = [option_of(a)]
        locked = a.locked or any(c.kind == "locked" and c.get("activity_id") == a.id for c in problem.hard)
        if locked or a.cancelled:
            domain[a.id] = options
            continue
        here = places.get(a.place_id or "")
        if a.place_id:
            alternatives = sorted(
                (p for p in places.values() if p.kind == a.kind and p.id != a.place_id),
                key=lambda p: (0 if here and p.zone == here.zone else 1, p.price_minor, p.id),
            )
            for p in alternatives:
                start = a.start
                if p.start_slots is not None and start % 1440 not in p.start_slots:
                    day0 = start - start % 1440
                    later = [day0 + s for s in p.start_slots if day0 + s >= a.start]
                    start = min(later) if later else day0 + min(p.start_slots)
                options.append((start, p.id))
        for d in problem.shifts:
            s = a.start + d
            if 0 <= s and s + a.duration <= horizon and s % SLOT == 0:
                options.append((s, a.place_id))
        if not a.required:
            options.append((a.start, DROP))
        seen: list[Option] = []
        for o in options:
            if o not in seen:
                seen.append(o)
        domain[a.id] = seen[: max(1, problem.max_options)]
    return domain


def make_activity(problem: Problem, base_a: Activity, option: Option) -> Activity:
    start, place_id = option
    common = dict(id=base_a.id, title=base_a.title, kind=base_a.kind, start=start, end=start + base_a.duration,
                  participants=base_a.participants, required=base_a.required, locked=base_a.locked)
    if place_id == DROP:
        return Activity(**common, place_id=base_a.place_id, place_name=base_a.place_name, cancelled=True,
                        cost_shares=tuple((m, 0) for m in sorted(base_a.participants)))
    if place_id is None or place_id not in problem.catalog.places:
        return Activity(**common, place_id=None, place_name=base_a.place_name, cost_shares=base_a.cost_shares)
    place = problem.catalog.places[place_id]
    shares = tuple((m, place.price_minor) for m in sorted(base_a.participants))
    return Activity(**common, place_id=place_id, cost_shares=shares)


def assemble(problem: Problem, choice: dict[str, Option]) -> Snapshot:
    acts = tuple(make_activity(problem, a, choice.get(a.id, option_of(a))) for a in problem.base.activities)
    b = problem.base
    return Snapshot(plan_id=b.plan_id, start_date=b.start_date, days=b.days, timezone=b.timezone, currency=b.currency,
                    activities=acts, dependencies=b.dependencies)


def activity_changed(before: Activity, after: Activity) -> bool:
    return (before.start, before.end, before.where, before.participants, before.cost_shares, before.cancelled, before.title) != (
        after.start, after.end, after.where, after.participants, after.cost_shares, after.cancelled, after.title)


def changed_activities(base: Snapshot, cand: Snapshot) -> tuple[str, ...]:
    """Activities that differ, were added or were removed."""
    out = []
    base_ids = {a.id: a for a in base.activities}
    cand_ids = {a.id: a for a in cand.activities}
    for a in cand.activities:
        b = base_ids.get(a.id)
        if b is None or activity_changed(b, a):
            out.append(a.id)
    for a in base.activities:
        if a.id not in cand_ids:
            out.append(a.id)
    return tuple(out)


def default_reconfirm(problem: Problem, base: Snapshot, cand: Snapshot) -> frozenset[str]:
    """Who must confirm again: in scoped mode, anyone in a changed activity; in whole-plan mode, everyone if anything changed."""
    changed = set(changed_activities(base, cand))
    if not changed:
        return frozenset()
    out: set[str] = set()
    members = list(dict.fromkeys((*base.members, *cand.members)))
    acts = {a.id: a for a in base.activities}
    acts_new = {a.id: a for a in cand.activities}
    for m in members:
        if problem.confirmation_mode.get(m, "scoped") == "whole_plan":
            out.add(m)
            continue
        for aid in changed:
            if (aid in acts and m in acts[aid].participants) or (aid in acts_new and m in acts_new[aid].participants):
                out.add(m)
                break
    return frozenset(out)


def burden(problem: Problem, base: Snapshot, cand: Snapshot) -> dict[str, int]:
    w = problem.weights
    result: dict[str, int] = {}
    new = {a.id: a for a in cand.activities}
    for m in base.members:
        b = 0
        for a_base in base.activities:
            a_new = new.get(a_base.id)
            if m not in a_base.participants or a_new is None:
                continue
            if a_new.cancelled and not a_base.cancelled:
                b += w.cancel
                continue
            b += w.time_slot * (abs(a_new.start - a_base.start) // SLOT)
            if a_new.where != a_base.where:
                b += w.venue
        added = cand.member_total(m) - base.member_total(m)
        if added > 0:
            b += w.cost_unit * math.ceil(added / w.cost_step_minor)
        result[m] = b
    return result


def score(problem: Problem, cand: Snapshot) -> Scored:
    base = problem.base
    changed = changed_activities(base, cand)
    per = burden(problem, base, cand)
    reconfirm = default_reconfirm(problem, base, cand)
    soft_loss = sum(1 for c in problem.soft if c.kind == "prefer_keep" and c.get("activity_id") in changed)
    added_cost = sum(cand.member_total(m) - base.member_total(m) for m in base.members)
    objective: Objective = (len(changed), max(per.values(), default=0), sum(per.values()), len(reconfirm), soft_loss, added_cost)
    return Scored(snapshot=cand, objective=objective, changed=changed, burden=per, reconfirm=reconfirm,
                  signature=tuple(None if a.cancelled else a.where for a in cand.activities))
