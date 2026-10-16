"""Plain data types for PlanRepair. No database access: the solver works on a
consistent snapshot handed to it by the agreement layer, which keeps it
reproducible and easy to test against brute-force enumeration.

Times are minutes after midnight of the plan's first day, so a three-day trip
runs from 0 to 3 * 1440; everything sits on a 15-minute grid. Money is integer
minor units (cents). Places come from the plan template's catalog; an activity
may also name a free-text place, which the solver keeps as it is.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Literal

SLOT = 15
DAY = 1440
DROP = "__drop__"  # option meaning "drop this activity" (only for activities that are not required)
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def hhmm(minutes: int) -> str:
    """Time of day of an absolute minute."""
    m = minutes % DAY
    return f"{m // 60:02d}:{m % 60:02d}"


def parse_hhmm(text: str) -> int:
    h, m = str(text).strip().split(":")
    return int(h) * 60 + int(m)


def day_of(minutes: int) -> int:
    return minutes // DAY


@dataclass(frozen=True)
class Place:
    id: str
    name: str
    kind: str
    zone: str
    open_min: int
    close_min: int
    capacity: int
    price_minor: int
    tags: frozenset[str] = frozenset()
    start_slots: tuple[int, ...] | None = None  # allowed start times of day


@dataclass(frozen=True)
class Catalog:
    places: dict[str, Place]
    travel: dict[tuple[str, str], int]
    data_version: str = "v1"
    default_travel: int | None = None

    def transfer(self, from_place: str | None, to_place: str | None) -> int:
        """Minutes needed between two places. A ride (kind travel) starts where the previous stop ended."""
        if not from_place or not to_place or from_place not in self.places or to_place not in self.places:
            return 0
        a, b = self.places[from_place], self.places[to_place]
        if b.kind == "travel" or a.kind == "travel":
            return 0
        key = (a.zone, b.zone)
        if key in self.travel:
            return self.travel[key]
        if (b.zone, a.zone) in self.travel:
            return self.travel[(b.zone, a.zone)]
        return self.default_travel if self.default_travel is not None else 10_000  # unknown route: never feasible


@dataclass(frozen=True)
class Activity:
    id: str
    title: str
    kind: str
    start: int
    end: int
    place_id: str | None  # catalog place; None with place_name set means a free-text place
    participants: tuple[str, ...]
    cost_shares: tuple[tuple[str, int], ...]  # (member, minor) sorted by member
    required: bool = True
    locked: bool = False
    cancelled: bool = False
    place_name: str | None = None

    @property
    def duration(self) -> int:
        return self.end - self.start

    @property
    def day(self) -> int:
        return day_of(self.start)

    def share(self, member: str) -> int:
        for m, v in self.cost_shares:
            if m == member:
                return v
        return 0

    @property
    def where(self) -> str | None:
        """Place identity for comparisons (catalog id, or the free-text name)."""
        return self.place_id or self.place_name


@dataclass(frozen=True)
class Snapshot:
    plan_id: str
    start_date: str
    days: int
    timezone: str
    currency: str
    activities: tuple[Activity, ...]
    dependencies: tuple[tuple[str, str], ...] = ()  # (before, after)

    def activity(self, activity_id: str) -> Activity:
        for a in self.activities:
            if a.id == activity_id:
                return a
        raise KeyError(activity_id)

    def has(self, activity_id: str) -> bool:
        return any(a.id == activity_id for a in self.activities)

    def with_activity(self, new: Activity) -> Snapshot:
        if self.has(new.id):
            return replace(self, activities=tuple(new if a.id == new.id else a for a in self.activities))
        return replace(self, activities=tuple(sorted((*self.activities, new), key=lambda a: (a.start, a.id))))

    def without(self, activity_id: str) -> Snapshot:
        return replace(self, activities=tuple(a for a in self.activities if a.id != activity_id))

    def member_total(self, member: str) -> int:
        return sum(a.share(member) for a in self.activities if not a.cancelled)

    @property
    def live(self) -> tuple[Activity, ...]:
        return tuple(a for a in self.activities if not a.cancelled)

    @property
    def members(self) -> tuple[str, ...]:
        seen: list[str] = []
        for a in self.activities:
            for m in a.participants:
                if m not in seen:
                    seen.append(m)
        return tuple(seen)


ConstraintKind = Literal["latest_end", "earliest_start", "max_total_cost", "needs", "unavailable_window", "must_attend", "prefer_keep", "locked"]


@dataclass(frozen=True)
class Constraint:
    id: str
    owner: str
    kind: str
    strength: Literal["hard", "soft"]
    value: tuple[tuple[str, object], ...]  # frozen dict items
    visibility: Literal["private", "group"] = "private"

    def get(self, key: str, default: object = None) -> object:
        for k, v in self.value:
            if k == key:
                return v
        return default

    @staticmethod
    def make(id: str, owner: str, kind: str, value: dict, strength: str = "hard", visibility: str = "private") -> Constraint:
        frozen = tuple(sorted((k, tuple(v) if isinstance(v, list) else v) for k, v in value.items()))
        return Constraint(id=id, owner=owner, kind=kind, strength=strength, value=frozen, visibility=visibility)  # type: ignore[arg-type]


@dataclass(frozen=True)
class Unavailability:
    place_id: str
    from_min: int
    to_min: int
    event_id: str


@dataclass(frozen=True)
class Weights:
    time_slot: int = 2   # w_t per 15-minute shift
    venue: int = 8       # w_v per place change
    cost_unit: int = 1   # w_c per currency unit of added cost
    cancel: int = 20     # w_d per dropped activity
    cost_step_minor: int = 100


ReconfirmFn = Callable[[Snapshot, Snapshot], frozenset[str]]


@dataclass
class Problem:
    base: Snapshot
    catalog: Catalog
    constraints: tuple[Constraint, ...]
    unavailable: tuple[Unavailability, ...] = ()
    confirmation_mode: dict[str, str] = field(default_factory=dict)  # member -> "scoped" | "whole_plan"
    weights: Weights = field(default_factory=Weights)
    max_options: int = 4
    shifts: tuple[int, ...] = (15, 30, -15, 45)
    disabled_groups: frozenset[str] = frozenset()
    domain_override: dict[str, list[tuple[int, str | None]]] | None = None
    names: dict[str, str] = field(default_factory=dict)  # member id -> display name, for explanation text

    def name(self, member: str) -> str:
        return self.names.get(member, member)

    @property
    def hard(self) -> tuple[Constraint, ...]:
        return tuple(c for c in self.constraints if c.strength == "hard")

    @property
    def soft(self) -> tuple[Constraint, ...]:
        return tuple(c for c in self.constraints if c.strength == "soft")

    @property
    def multi_day(self) -> bool:
        return self.base.days > 1


@dataclass(frozen=True)
class Violation:
    group: str          # constraint group used for conflict explanation, e.g. "latest_end:bob"
    code: str           # machine code
    activity_id: str | None
    members: tuple[str, ...]
    detail: str         # full detail (may name a member's private limit)
    public: str         # redacted wording safe for the whole group
    relaxable_by: str | None = None  # owner who could relax it, if it is a personal constraint


Objective = tuple[int, int, int, int, int, int]
OBJECTIVE_NAMES = ("changed_activities", "max_person_burden", "total_person_burden", "reconfirmation_members", "soft_preference_loss", "added_cost_minor")


@dataclass
class Scored:
    snapshot: Snapshot
    objective: Objective
    changed: tuple[str, ...]
    burden: dict[str, int]
    reconfirm: frozenset[str]
    signature: tuple[str | None, ...]


@dataclass
class RepairOutcome:
    status: Literal["optimal_within_domain", "best_found", "infeasible_within_domain", "unknown"]
    method: Literal["enumeration", "cp-sat"]
    candidates: list[Scored]
    search_complete: bool
    domain_size: int
    explored: int
    runtime_ms: float
    domain: dict[str, list[tuple[int, str | None]]]
    conflict: list[Violation] = field(default_factory=list)
    requested_method: str | None = None
    fallback_reason: str | None = None


def when_text(minutes: int, start_date: str | None = None, multi_day: bool = False) -> str:
    """'18:30', or 'Sat 18:30' for plans over several days."""
    if not multi_day or start_date is None:
        return hhmm(minutes)
    from datetime import date, timedelta

    d = date.fromisoformat(start_date) + timedelta(days=day_of(minutes))
    return f"{WEEKDAYS[d.weekday()]} {hhmm(minutes)}"
