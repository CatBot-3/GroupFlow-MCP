"""Conflict explanation for repairs with no feasible candidate.

Deletion filter over constraint groups: start with every group active (the
problem is infeasible), then try switching each group off; if the problem is
still infeasible without it, that group is not part of the conflict and stays
off. What remains is an irreducible set: switching off any one of them makes
some candidate feasible. Personal constraints are grouped by kind ("each
member's latest time to be home"), so the result names what would have to
change without naming whose private limit it is.
"""
from __future__ import annotations

import time
from dataclasses import replace

from .enumerate import any_feasible
from .model import Problem
from .validate import DEFAULT_LABELS

KIND_LABELS = {
    "transfer": "travel times between places",
    "place_hours": "opening hours and bookable times",
    "capacity": "room at each place",
    "overlap": "people being in two places at once",
    "place_facts": "place facts",
}

# Validator group prefix -> condition kind.
PERSONAL = {"latest_end": "latest_end", "earliest_start": "earliest_start", "budget": "max_total_cost", "needs": "needs", "busy": "unavailable_window"}


def candidate_groups(problem: Problem) -> list[str]:
    groups = ["place_hours", "capacity", "transfer", "overlap", "place_facts"]
    kinds = {c.kind for c in problem.hard}
    for group, kind in PERSONAL.items():
        if kind in kinds:
            groups.append(group)
    for u in problem.unavailable:
        g = f"place_unavailable:{u.place_id}"
        if g not in groups:
            groups.append(g)
    for c in problem.hard:
        if c.kind == "must_attend":
            groups.append(f"must_attend:{c.get('activity_id')}")
        if c.kind == "locked":
            groups.append(f"locked:{c.get('activity_id')}")
    for a in problem.base.activities:
        if a.locked:
            groups.append(f"locked:{a.id}")
        if a.required:
            groups.append(f"required:{a.id}")
    seen: list[str] = []
    for g in groups:
        if g not in seen:
            seen.append(g)
    return seen


def find_conflict(problem: Problem, time_budget_s: float = 2.0) -> list[str] | None:
    """Irreducible set of constraint groups that cannot all hold, or None if time ran out."""
    deadline = time.perf_counter() + time_budget_s
    groups = candidate_groups(problem)
    active = list(groups)
    for g in groups:
        trial = [x for x in active if x != g]
        disabled = frozenset(set(groups) - set(trial))
        result = any_feasible(replace(problem, disabled_groups=disabled), deadline)
        if result is None:
            return None
        if result is False:
            active = trial  # still infeasible without g: g is not needed for the conflict
    return active


def _personal_label(problem: Problem, kind: str) -> str:
    for c in problem.hard:
        if c.kind == kind and c.get("label"):
            return str(c.get("label"))
    return DEFAULT_LABELS.get(kind, kind)


def describe(problem: Problem, groups: list[str]) -> list[dict]:
    """Public wording for each conflicting group, plus whose personal constraints are involved (for private follow-up)."""
    places = problem.catalog.places
    out = []
    for g in groups:
        kind, _, ref = g.partition(":")
        if kind == "place_unavailable":
            reasons = [u.reason for u in problem.unavailable if u.place_id == ref and u.reason]
            text = reasons[0] if reasons else f"{places[ref].name if ref in places else ref} is unavailable at the planned time."
        elif kind == "must_attend":
            text = f"Everyone has to attend {problem.base.activity(ref).title}." if problem.base.has(ref) else "Everyone has to attend."
        elif kind == "required":
            text = f"{problem.base.activity(ref).title} cannot be dropped."
        elif kind == "locked":
            text = f"{problem.base.activity(ref).title} is locked."
        elif kind in PERSONAL:
            text = f"The plan has to respect each member's {_personal_label(problem, PERSONAL[kind])}."
        else:
            text = f"The plan has to respect {KIND_LABELS.get(kind, kind)}."
        owners = sorted({c.owner for c in problem.hard if c.kind == PERSONAL.get(kind)}) if kind in PERSONAL else []
        out.append({"group": g, "kind": kind, "public": text, "personal": kind in PERSONAL, "owners": owners})
    return out
