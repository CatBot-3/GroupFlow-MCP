"""Exact search by layers of changed activities (scope plan 11.4, 11.5).

Layer k holds every candidate that changes exactly k activities. Layers are
searched in order, so the first layer with a feasible candidate proves the
minimum number of changes; comparing every candidate in that layer on the
remaining objectives (in lexicographic order) proves the full lexicographic
optimum within the domain. Later layers only supply clearly labelled
alternatives with a different venue choice.

Feasibility during the search uses per-option and per-pair checks computed
once (the same checks as the validator, memoised). Every returned candidate
is then re-validated as a whole snapshot by `validate.violations`.
"""
from __future__ import annotations

import itertools
import time

from .domain import Option, assemble, build_domain, make_activity, score
from .model import Activity, Problem, RepairOutcome, Scored
from .validate import _constraints_of, _on, activity_violations, pair_violations, violations


def domain_size(domain: dict[str, list[Option]]) -> int:
    size = 1
    for opts in domain.values():
        size *= len(opts)
    return size


class FastChecker:
    """Memoised hard-constraint check over option indices."""

    def __init__(self, problem: Problem, domain: dict[str, list[Option]]):
        self.problem = problem
        self.ids = [a.id for a in problem.base.activities]
        self.built: dict[tuple[str, int], Activity] = {}
        self.local_ok: dict[tuple[str, int], bool] = {}
        for a in problem.base.activities:
            for oi, opt in enumerate(domain[a.id]):
                act = make_activity(problem, a, opt)
                self.built[(a.id, oi)] = act
                self.local_ok[(a.id, oi)] = not activity_violations(problem, problem.base, act)
        self.pair_cache: dict[tuple[str, int, str, int], bool] = {}
        self.members = list(problem.base.members)
        self.caps = {}
        for m in self.members:
            for c in _constraints_of(problem, m, "max_total_cost"):
                if _on(problem, f"budget:{m}"):
                    self.caps[m] = min(self.caps.get(m, 10**12), int(c.get("amount_minor") or 0))  # type: ignore[arg-type]

    def feasible(self, choice: dict[str, int]) -> bool:
        acts = []
        for aid in self.ids:
            key = (aid, choice[aid])
            if not self.local_ok[key]:
                return False
            acts.append(self.built[key])
        live = sorted((a for a in acts if not a.cancelled), key=lambda a: (a.start, a.id))
        for x, y in zip(live, live[1:]):
            k = (x.id, choice[x.id], y.id, choice[y.id])
            ok = self.pair_cache.get(k)
            if ok is None:
                ok = not pair_violations(self.problem, x, y)
                self.pair_cache[k] = ok
            if not ok:
                return False
        for m in self.members:
            mine = [a for a in live if m in a.participants]
            if _on(self.problem, f"overlap:{m}"):
                for x, y in zip(mine, mine[1:]):
                    if x.end > y.start:
                        return False
            cap = self.caps.get(m)
            if cap is not None and sum(a.share(m) for a in mine) > cap:
                return False
        return True


def iter_layer(problem: Problem, domain: dict[str, list[Option]], k: int):
    """Yield option-index choices that change exactly k activities."""
    ids = [a.id for a in problem.base.activities]
    mutable = [i for i in ids if len(domain[i]) > 1]
    base_choice = {i: 0 for i in ids}
    for combo in itertools.combinations(mutable, k):
        for alts in itertools.product(*[range(1, len(domain[i])) for i in combo]):
            choice = dict(base_choice)
            choice.update(zip(combo, alts))
            yield choice


def _to_options(domain: dict[str, list[Option]], choice: dict[str, int]) -> dict[str, Option]:
    return {aid: domain[aid][oi] for aid, oi in choice.items()}


def enumerate_repair(problem: Problem, time_budget_s: float = 3.0, max_candidates: int = 3) -> RepairOutcome:
    started = time.perf_counter()
    deadline = started + time_budget_s
    domain = build_domain(problem)
    checker = FastChecker(problem, domain)
    mutable = [a.id for a in problem.base.activities if len(domain[a.id]) > 1]
    best_by_signature: dict[tuple, Scored] = {}
    explored = 0
    optimal_layer: int | None = None
    timed_out = False

    for k in range(0, len(mutable) + 1):
        for choice in iter_layer(problem, domain, k):
            if time.perf_counter() > deadline:
                timed_out = True
                break
            explored += 1
            if not checker.feasible(choice):
                continue
            s = score(problem, assemble(problem, _to_options(domain, choice)))
            current = best_by_signature.get(s.signature)
            if current is None or s.objective < current.objective:
                best_by_signature[s.signature] = s
        if timed_out:
            break
        if best_by_signature and optimal_layer is None:
            optimal_layer = k
        if optimal_layer is not None and len(best_by_signature) >= max_candidates:
            break

    ranked = sorted(best_by_signature.values(), key=lambda s: s.objective)[:max_candidates]
    ranked = [s for s in ranked if not violations(problem, s.snapshot)]  # independent whole-snapshot check
    if ranked:
        status = "optimal_within_domain" if optimal_layer is not None else "best_found"
    else:
        status = "unknown" if timed_out else "infeasible_within_domain"
    return RepairOutcome(
        status=status, method="enumeration", candidates=ranked, search_complete=optimal_layer is not None,
        domain_size=domain_size(domain), explored=explored, runtime_ms=(time.perf_counter() - started) * 1000, domain=domain,
    )


def any_feasible(problem: Problem, deadline: float) -> bool | None:
    """True if some candidate in the domain is feasible, False if none is, None if time ran out."""
    domain = build_domain(problem)
    checker = FastChecker(problem, domain)
    mutable = [a.id for a in problem.base.activities if len(domain[a.id]) > 1]
    for k in range(0, len(mutable) + 1):
        for choice in iter_layer(problem, domain, k):
            if time.perf_counter() > deadline:
                return None
            if checker.feasible(choice):
                return True
    return False
