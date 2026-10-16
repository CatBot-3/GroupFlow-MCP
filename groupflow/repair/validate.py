"""Independent hard-constraint validator.

Every candidate the solver publishes is re-checked here as a complete
snapshot, separately from the search code, so a search bug cannot publish an
infeasible plan. The same checks, run on one activity at a time, also prune
the CP-SAT model and explain why an alternative was rejected.

Each violation carries a constraint group (for conflict explanation) and two
wordings: a full one for the owner and a redacted one for the whole group, so
an explanation never reveals someone's private limit. Condition labels come
from the plan template ("latest time to be home", "busy time").
"""
from __future__ import annotations

from .model import DAY, SLOT, Activity, Constraint, Problem, Snapshot, Violation, hhmm

DEFAULT_LABELS = {
    "latest_end": "latest end time",
    "earliest_start": "earliest start time",
    "max_total_cost": "budget",
    "needs": "needs",
    "unavailable_window": "busy time",
}


def _constraints_of(problem: Problem, member: str, kind: str) -> list[Constraint]:
    return [c for c in problem.hard if c.owner == member and c.kind == kind]


def _on(problem: Problem, group: str) -> bool:
    """A check is skipped when its exact group or its kind (the part before ':') is disabled (conflict explanation)."""
    disabled = problem.disabled_groups
    return group not in disabled and group.split(":", 1)[0] not in disabled


def _label(c: Constraint) -> str:
    return str(c.get("label") or DEFAULT_LABELS.get(c.kind, c.kind))


def _at(problem: Problem, minutes: int) -> str:
    if not problem.multi_day:
        return hhmm(minutes)
    from .model import when_text

    return when_text(minutes, problem.base.start_date, True)


def activity_violations(problem: Problem, base: Snapshot, a: Activity) -> list[Violation]:
    """Checks that depend on one activity only (place facts and hours, needs, personal time windows, locks)."""
    out: list[Violation] = []
    base_a = base.activity(a.id) if base.has(a.id) else None

    if a.cancelled:
        rules = [c for c in problem.hard if c.kind == "must_attend" and c.get("activity_id") == a.id]
        if a.required or rules:
            group = f"must_attend:{a.id}" if rules else f"required:{a.id}"
            if _on(problem, group):
                out.append(Violation(group, "REQUIRED_ACTIVITY", a.id, (), f"{a.title} cannot be dropped.", f"{a.title} cannot be dropped.",
                                     relaxable_by=rules[0].owner if rules else None))
        return out

    place = problem.catalog.places.get(a.place_id or "")
    day0 = a.day * DAY
    if a.place_id and place is None:
        out.append(Violation("place_facts", "UNKNOWN_PLACE", a.id, (), f"{a.title}: unknown place {a.place_id}.", f"{a.title}: the place is unknown."))
    if a.start % SLOT or a.end % SLOT:
        if _on(problem, "place_hours"):
            out.append(Violation("place_hours", "OFF_GRID", a.id, (), f"{a.title} is not on the 15-minute grid.", f"{a.title} is not on the 15-minute grid."))
    if a.start < 0 or a.end > max(1, problem.base.days) * DAY:
        out.append(Violation("place_hours", "OUTSIDE_PLAN", a.id, (), f"{a.title} falls outside the plan's days.", f"{a.title} falls outside the plan's days."))

    if place is not None:
        if place.kind != a.kind and _on(problem, "place_facts"):
            out.append(Violation("place_facts", "WRONG_PLACE_KIND", a.id, (), f"{place.name} does not offer {a.kind}.", f"{place.name} does not offer {a.kind}."))
        if _on(problem, "place_hours"):
            if a.start < day0 + place.open_min or a.end > day0 + place.close_min:
                msg = f"{place.name} is open {hhmm(place.open_min)} to {hhmm(place.close_min)}, not {hhmm(a.start)} to {hhmm(a.end)}."
                out.append(Violation("place_hours", "OUTSIDE_HOURS", a.id, (), msg, msg))
            if place.start_slots is not None and a.start % DAY not in place.start_slots:
                slots = ", ".join(hhmm(s) for s in place.start_slots)
                msg = f"{place.name} can only start at {slots}."
                out.append(Violation("place_hours", "SLOT_UNAVAILABLE", a.id, (), msg, msg))
        for u in problem.unavailable:
            group = f"place_unavailable:{u.place_id}"
            if u.place_id == place.id and a.start < u.to_min and u.from_min < a.end and _on(problem, group):
                msg = f"{place.name} is unavailable from {_at(problem, u.from_min)} to {_at(problem, u.to_min)}."
                out.append(Violation(group, "PLACE_UNAVAILABLE", a.id, (), msg, msg))
        if len(a.participants) > place.capacity and _on(problem, "capacity"):
            msg = f"{place.name} has room for {place.capacity}, not {len(a.participants)}."
            out.append(Violation("capacity", "CAPACITY", a.id, (), msg, msg))

    for m in a.participants:
        if place is not None:
            for c in _constraints_of(problem, m, "needs"):
                group = f"needs:{m}"
                kinds = set(c.get("applies_to") or ())  # type: ignore[arg-type]
                if kinds and place.kind not in kinds:
                    continue
                missing = set(c.get("tags") or ()) - set(place.tags)  # type: ignore[arg-type]
                if missing and _on(problem, group):
                    label = _label(c)
                    out.append(Violation(group, "NEEDS", a.id, (m,),
                                         f"{place.name} does not meet {problem.name(m)}'s {label} ({', '.join(sorted(missing))}).",
                                         f"{place.name} does not meet a member's {label}.", relaxable_by=m))
        for c in _constraints_of(problem, m, "latest_end"):
            group = f"latest_end:{m}"
            limit = _minutes(c.get("time"))
            only_day = c.get("day")
            if limit is None or (only_day is not None and int(only_day) != a.day):  # type: ignore[arg-type]
                continue
            if a.end > day0 + limit and _on(problem, group):
                label = _label(c)
                out.append(Violation(group, "LATEST_END", a.id, (m,),
                                     f"{a.title} would end at {hhmm(a.end)}, after {problem.name(m)}'s {label} ({hhmm(limit)}).",
                                     f"{a.title} would end at {hhmm(a.end)}, after a member's {label}.", relaxable_by=m))
        for c in _constraints_of(problem, m, "earliest_start"):
            group = f"earliest_start:{m}"
            limit = _minutes(c.get("time"))
            only_day = c.get("day")
            if limit is None or (only_day is not None and int(only_day) != a.day):  # type: ignore[arg-type]
                continue
            if a.start < day0 + limit and _on(problem, group):
                label = _label(c)
                out.append(Violation(group, "EARLIEST_START", a.id, (m,),
                                     f"{a.title} would start at {hhmm(a.start)}, before {problem.name(m)}'s {label} ({hhmm(limit)}).",
                                     f"{a.title} would start at {hhmm(a.start)}, before a member's {label}.", relaxable_by=m))
        for c in _constraints_of(problem, m, "unavailable_window"):
            group = f"busy:{m}"
            for w in c.get("windows") or ():  # type: ignore[union-attr]
                lo, hi = int(w[0]), int(w[1])
                if a.start < hi and lo < a.end and _on(problem, group):
                    label = _label(c)
                    out.append(Violation(group, "BUSY", a.id, (m,),
                                         f"{a.title} at {_at(problem, a.start)} overlaps {problem.name(m)}'s {label} ({_at(problem, lo)} to {hhmm(hi)}).",
                                         f"{a.title} at {_at(problem, a.start)} overlaps a member's {label}.", relaxable_by=m))
                    break

    rules = [c for c in problem.hard if c.kind == "must_attend" and c.get("activity_id") == a.id]
    for c in rules:
        group = f"must_attend:{a.id}"
        everyone = set(base.members)
        if not everyone.issubset(a.participants) and _on(problem, group):
            out.append(Violation(group, "MUST_ATTEND", a.id, (), f"Everyone must attend {a.title}.", f"Everyone must attend {a.title}.", relaxable_by=c.owner))

    locked = a.locked or any(c.kind == "locked" and c.get("activity_id") == a.id for c in problem.hard)
    if locked and base_a is not None and (a.start, a.end, a.where, a.participants) != (base_a.start, base_a.end, base_a.where, base_a.participants):
        group = f"locked:{a.id}"
        if _on(problem, group):
            out.append(Violation(group, "LOCKED", a.id, (), f"{a.title} is locked and cannot change.", f"{a.title} is locked and cannot change."))
    return out


def pair_violations(problem: Problem, before: Activity, after: Activity) -> list[Violation]:
    """Travel time between two consecutive activities."""
    if before.cancelled or after.cancelled:
        return []
    group = f"transfer:{before.id}->{after.id}"
    if not _on(problem, group):
        return []
    need = problem.catalog.transfer(before.place_id, after.place_id)
    if before.end + need > after.start:
        places = problem.catalog.places
        a_name = places[before.place_id].name if before.place_id in places else before.title
        b_name = places[after.place_id].name if after.place_id in places else after.title
        msg = (f"Getting from {a_name} to {b_name} takes {need} minutes, but {before.title} ends at {hhmm(before.end)} "
               f"and {after.title} starts at {hhmm(after.start)}.")
        return [Violation(group, "TRANSFER", after.id, (), msg, msg)]
    return []


def chain(snapshot: Snapshot) -> list[tuple[Activity, Activity]]:
    """Consecutive pairs of live activities, in time order."""
    live = sorted(snapshot.live, key=lambda a: (a.start, a.id))
    return list(zip(live, live[1:]))


def violations(problem: Problem, snapshot: Snapshot) -> list[Violation]:
    out: list[Violation] = []
    for a in snapshot.activities:
        out.extend(activity_violations(problem, problem.base, a))
    for before, after in chain(snapshot):
        out.extend(pair_violations(problem, before, after))
    for m in snapshot.members:
        attended = sorted((a for a in snapshot.live if m in a.participants), key=lambda x: x.start)
        for x, y in zip(attended, attended[1:]):
            if x.end > y.start and _on(problem, f"overlap:{m}"):
                out.append(Violation(f"overlap:{m}", "OVERLAP", y.id, (m,), f"{problem.name(m)} cannot attend {x.title} and {y.title} at once.",
                                     f"A member cannot attend {x.title} and {y.title} at once."))
        for c in _constraints_of(problem, m, "max_total_cost"):
            group = f"budget:{m}"
            cap = int(c.get("amount_minor") or 0)  # type: ignore[arg-type]
            total = snapshot.member_total(m)
            if total > cap and _on(problem, group):
                label = _label(c)
                out.append(Violation(group, "BUDGET", None, (m,),
                                     f"{problem.name(m)}'s total would be {money(total, snapshot.currency)}, above their {label} of {money(cap, snapshot.currency)}.",
                                     f"The total would be above a member's {label}.", relaxable_by=m))
    return out


def is_feasible(problem: Problem, snapshot: Snapshot) -> bool:
    return not violations(problem, snapshot)


def money(minor: int, currency: str = "CAD") -> str:
    sign = "-" if minor < 0 else ""
    return f"{sign}{currency} {abs(minor) / 100:.2f}"


def _minutes(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and ":" in value:
        h, m = value.split(":")
        return int(h) * 60 + int(m)
    return None
