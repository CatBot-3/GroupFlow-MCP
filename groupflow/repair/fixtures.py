"""Catalog conversion (template format to solver types) and the demo repair problem used by tests."""
from __future__ import annotations

from typing import Any

from .model import Activity, Catalog, Constraint, Place, Problem, Snapshot, Unavailability, parse_hhmm


def catalog_from_dict(d: dict[str, Any]) -> Catalog:
    """Build a solver catalog from the template catalog format (also what plans store)."""
    places = {}
    for p in d.get("places", []):
        price_minor = int(p["price_minor"]) if "price_minor" in p else int(round(float(p.get("price", 0)) * 100))
        places[p["id"]] = Place(
            id=p["id"], name=p["name"], kind=p["kind"], zone=p.get("zone", "local"), open_min=parse_hhmm(p.get("open", "00:00")),
            close_min=parse_hhmm(p.get("close", "23:59")), capacity=int(p.get("capacity", 99)), price_minor=price_minor,
            tags=frozenset(p.get("tags") or []),
            start_slots=tuple(parse_hhmm(s) for s in p["start_slots"]) if p.get("start_slots") else None,
        )
    travel = {(a, b): int(m) for a, b, m in d.get("travel_minutes", [])}
    return Catalog(places=places, travel=travel, data_version=str(d.get("version", "v1")), default_travel=d.get("default_travel"))


DEMO_MEMBERS = ("alice", "bob", "chen", "dana")
DEMO_NAMES = {"alice": "Alice", "bob": "Bob", "chen": "Chen", "dana": "Dana"}
DEMO_CONDITIONS: list[dict[str, Any]] = [
    *({"owner": m, "kind": "latest_end", "value": {"time": "21:00", "label": "latest time to be home"}} for m in DEMO_MEMBERS),
    *({"owner": m, "kind": "max_total_cost", "value": {"amount_minor": 4500, "label": "budget for the evening"}} for m in DEMO_MEMBERS),
    {"owner": "chen", "kind": "needs", "value": {"tags": ["nut_free"], "applies_to": ["meal", "dessert"], "label": "dietary needs"}},
    {"owner": "dana", "kind": "needs", "value": {"tags": ["vegetarian"], "applies_to": ["meal", "dessert"], "label": "dietary needs"}},
    {"owner": "alice", "kind": "must_attend", "value": {"activity_id": "dinner"}, "visibility": "group"},
    {"owner": "bob", "kind": "prefer_keep", "strength": "soft", "value": {"activity_id": "dessert"}},
]


def demo_problem(unavailable: tuple[Unavailability, ...] = (), **kwargs: Any) -> Problem:
    """The Friday dinner from the evening_out template, with the four friends' conditions."""
    from groupflow.templates import get_template

    t = get_template("evening_out")
    catalog = catalog_from_dict(t.catalog.model_dump())
    acts = []
    for a in t.starter.activities:
        place = catalog.places[a.place]  # type: ignore[index]
        start, end = a.day * 1440 + parse_hhmm(a.start), a.day * 1440 + parse_hhmm(a.end)
        acts.append(Activity(id=a.key, title=a.title, kind=place.kind, start=start, end=end, place_id=place.id, participants=DEMO_MEMBERS,
                             cost_shares=tuple((m, place.price_minor) for m in sorted(DEMO_MEMBERS))))
    base = Snapshot(plan_id="plan_friday", start_date="2026-10-16", days=1, timezone="America/Toronto", currency="CAD", activities=tuple(acts),
                    dependencies=tuple((x.id, y.id) for x, y in zip(acts, acts[1:])))
    cons = tuple(Constraint.make(id=f"c{i}", owner=c["owner"], kind=c["kind"], value=c["value"], strength=c.get("strength", "hard"),
                                 visibility=c.get("visibility", "private")) for i, c in enumerate(DEMO_CONDITIONS))
    kwargs.setdefault("shifts", tuple(t.search.time_shifts))
    kwargs.setdefault("max_options", t.search.max_options)
    return Problem(base=base, catalog=catalog, constraints=cons, unavailable=unavailable,
                   confirmation_mode={m: "scoped" for m in DEMO_MEMBERS}, names=dict(DEMO_NAMES), **kwargs)
