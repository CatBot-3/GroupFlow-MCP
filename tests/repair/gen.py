"""Reproducible random repair instances for cross-checking the solvers against brute force."""
from __future__ import annotations

import itertools
import random

from groupflow.repair.domain import assemble, build_domain, score
from groupflow.repair.model import Activity, Catalog, Constraint, Place, Problem, Snapshot, Unavailability
from groupflow.repair.validate import violations

KINDS = ["meetup", "meal", "dessert", "activity", "travel"]
ZONES = ["north", "centre", "south"]


def random_problem(seed: int, max_options: int = 4) -> Problem:
    rnd = random.Random(seed)
    zones = ZONES[: rnd.randint(2, 3)]
    travel = {}
    for i, a in enumerate(zones):
        for b in zones[i:]:
            travel[(a, b)] = 15 if a == b else rnd.choice([15, 30, 45])
    n_acts = rnd.randint(3, 6)
    kinds = ["meetup"] + [rnd.choice(["meal", "dessert", "activity"]) for _ in range(n_acts - 2)] + ["travel"]
    venues: dict[str, Place] = {}
    for kind in set(kinds):
        for j in range(rnd.randint(2, 3) if kind != "travel" else 1):
            vid = f"{kind}_{j}"
            open_min = rnd.choice([8 * 60, 12 * 60, 16 * 60])
            close_min = rnd.choice([22 * 60, 23 * 60, 23 * 60 + 45])
            slots = None
            if kind in ("meal", "activity") and rnd.random() < 0.25:
                slots = tuple(sorted({rnd.choice([18 * 60, 18 * 60 + 30, 19 * 60, 19 * 60 + 30]) for _ in range(2)}))
            venues[vid] = Place(
                id=vid, name=vid.replace("_", " ").title(), kind=kind, zone=rnd.choice(zones) if kind != "travel" else "transit",
                open_min=open_min, close_min=close_min, capacity=rnd.choice([4, 6, 10, 20]),
                price_minor=0 if kind == "meetup" else rnd.choice([300, 500, 800, 1500, 2600, 2800, 3000]),
                tags=frozenset([t for t in ("vegetarian", "nut_free") if rnd.random() < 0.85] + (["step_free"] if rnd.random() < 0.8 else [])),
                start_slots=slots,
            )
    catalog = Catalog(places=venues, travel=travel)
    members = [f"m{i}" for i in range(rnd.randint(3, 5))]
    acts = []
    t = 17 * 60 + 15 * rnd.randint(0, 4)
    for i, kind in enumerate(kinds):
        vid = next(v.id for v in venues.values() if v.kind == kind)
        dur = {"meetup": 15, "meal": 60, "dessert": 30, "activity": 45, "travel": 30}[kind]
        if i > 0:
            prev = acts[-1]
            t = prev.end + catalog.transfer(prev.place_id, vid)
        optional = kind in ("dessert", "activity") and rnd.random() < 0.3
        participants = tuple(members) if not optional or rnd.random() < 0.5 else tuple(sorted(rnd.sample(members, max(2, len(members) - 1))))
        price = venues[vid].price_minor
        acts.append(Activity(id=f"a{i}", title=f"{kind.title()} {i}", kind=kind, start=t, end=t + dur, place_id=vid, participants=participants,
                             cost_shares=tuple((m, price) for m in sorted(participants)), required=True))
    base = Snapshot(plan_id="p", start_date="2026-10-17", days=1, timezone="America/Toronto", currency="CAD", activities=tuple(acts),
                    dependencies=tuple((x.id, y.id) for x, y in zip(acts, acts[1:])))
    constraints = []
    end_time = acts[-1].end
    for m in members:
        if rnd.random() < 0.8:
            limit = end_time + rnd.choice([0, 15, 30, 45, 60, 90])
            constraints.append(Constraint.make(f"h_{m}", m, "latest_end", {"time": f"{limit // 60:02d}:{limit % 60:02d}"}))
        if rnd.random() < 0.8:
            total = base.member_total(m)
            constraints.append(Constraint.make(f"b_{m}", m, "max_total_cost", {"amount_minor": total + rnd.choice([0, 200, 500, 1000, 2000])}))
        if rnd.random() < 0.3:
            constraints.append(Constraint.make(f"d_{m}", m, "needs", {"tags": [rnd.choice(["vegetarian", "nut_free"])], "applies_to": ["meal", "dessert"]}))
    if rnd.random() < 0.3:
        a = rnd.choice(acts)
        constraints.append(Constraint.make("keep", members[0], "prefer_keep", {"activity_id": a.id}, strength="soft"))
    unavailable = []
    target = rnd.choice([a for a in acts if a.kind != "travel"])
    unavailable.append(Unavailability(target.place_id, target.start - 30, target.end + 30, "ev"))
    modes = {m: ("whole_plan" if rnd.random() < 0.2 else "scoped") for m in members}
    return Problem(base=base, catalog=catalog, constraints=tuple(constraints), unavailable=tuple(unavailable),
                   confirmation_mode=modes, max_options=max_options)


def brute_force_best(problem: Problem):
    """Plain product over the whole domain, no layering: the ground truth for the optimum."""
    domain = build_domain(problem)
    ids = [a.id for a in problem.base.activities]
    best = None
    for combo in itertools.product(*[domain[i] for i in ids]):
        snap = assemble(problem, dict(zip(ids, combo)))
        if violations(problem, snap):
            continue
        s = score(problem, snap)
        if best is None or s.objective < best.objective:
            best = s
    return best
