"""PlanRepair entry point: pick a method, run it, explain the result."""
from __future__ import annotations

from .conflicts import describe, find_conflict
from .cpsat import available as cpsat_available, cpsat_repair, fallback_reason
from .domain import Option, assemble, build_domain, changed_activities, option_of, score
from .enumerate import domain_size, enumerate_repair
from .model import DROP, Problem, RepairOutcome, Scored, Violation, when_text
from .validate import violations

# Calibrated on 180 fixed-order fixtures, three paired repetitions each.
# This counts combinations after domain construction, not activities.
# The benchmark behind the threshold is summarized in docs/EVALUATION.md; scripts/benchmark_solver.py recalibrates it.
CP_SAT_THRESHOLD = 16_384
ENUMERATION_LIMIT = CP_SAT_THRESHOLD - 1  # compatibility: largest size routed to enumeration by size alone
PHYSICAL = {"transfer", "place_hours", "place_unavailable", "capacity", "overlap", "place_facts"}


def repair(problem: Problem, method: str = "auto", time_budget_s: float = 3.0, max_candidates: int = 3, explain: bool = True) -> RepairOutcome:
    requested_method = method
    domain = build_domain(problem)
    size = domain_size(domain)
    reason = None
    if method == "auto":
        method = "enumeration"
        if size >= CP_SAT_THRESHOLD:
            reason = fallback_reason(problem, domain) if cpsat_available() else "cp_sat_unavailable"
            if reason is None:
                method = "cp-sat"
    if method == "cp-sat" and not cpsat_available():
        method = "enumeration"  # still exact, only slower on large domains
        reason = "cp_sat_unavailable"
    if method == "cp-sat":
        outcome = cpsat_repair(problem, time_budget_s=time_budget_s, max_candidates=max_candidates)
    else:
        outcome = enumerate_repair(problem, time_budget_s=time_budget_s, max_candidates=max_candidates)
    outcome.requested_method = requested_method
    outcome.fallback_reason = outcome.fallback_reason or reason
    if explain and outcome.status == "infeasible_within_domain":
        groups = find_conflict(problem, time_budget_s=max(1.0, time_budget_s))
        if groups:
            outcome.conflict = [Violation(group=d["group"], code="CONFLICT", activity_id=None, members=tuple(d["owners"]),
                                          detail=d["public"], public=d["public"]) for d in describe(problem, groups)]
    return outcome


def option_label(problem: Problem, activity_id: str, opt: Option) -> str:
    start, place_id = opt
    if place_id == DROP:
        return "Dropping it"
    place = problem.catalog.places.get(place_id or "")
    name = place.name if place else (problem.base.activity(activity_id).place_name or problem.base.activity(activity_id).title)
    return f"{name} at {when_text(start, problem.base.start_date, problem.multi_day)}"


def best_completion(problem: Problem, base_choice: dict[str, Option], fixed_id: str, opt: Option,
                    domain: dict[str, list[Option]]) -> tuple[object, object, list[Violation]] | None:
    """Hold one option fixed and search the other activities' options for the completion that breaks the least.

    Prefers completions that respect physical facts (travel, hours, availability) and only break people's limits:
    "to fit C, the evening would end at 21:15" explains more than a travel clash."""
    ids = [a.id for a in problem.base.activities]
    best = None
    stack = [dict(base_choice, **{fixed_id: opt})]
    for other in (i for i in ids if i != fixed_id):
        expanded = []
        for partial in stack:
            for o in domain.get(other, [base_choice[other]]):
                expanded.append(dict(partial, **{other: o}))
        stack = expanded[:5000]
    for choice in stack:
        snap = assemble(problem, choice)
        v = violations(problem, snap)
        physical = sum(1 for item in v if item.group.split(":", 1)[0] in PHYSICAL)
        personal = len({item.group.split(":", 1)[0] for item in v})
        key = (physical, personal, len(v), score(problem, snap).objective)
        if best is None or key < best[0]:
            best = (key, snap, v)
    return best


def rejected_alternatives(problem: Problem, chosen: Scored, domain: dict[str, list[Option]]) -> list[dict]:
    """Why each other option for the changed activities was not chosen. Wording is the redacted one."""
    out = []
    chosen_choice = {a.id: option_of(a) for a in chosen.snapshot.activities}
    for aid in chosen.changed:
        if aid not in chosen_choice:
            continue
        for opt in domain.get(aid, []):
            if opt == chosen_choice[aid]:
                continue
            out.append(judge_option(problem, chosen, chosen_choice, aid, opt, domain))
    return [o for o in out if o]


def judge_option(problem: Problem, chosen: Scored | None, choice: dict[str, Option], aid: str, opt: Option,
                 domain: dict[str, list[Option]]) -> dict:
    best = best_completion(problem, choice, aid, opt, domain)
    if best is None:
        return {}
    _, snap, v = best
    if not v:
        s = score(problem, snap)  # type: ignore[arg-type]
        reason = ("Feasible, but it changes more or costs more: " + _objective_text(s, chosen)) if chosen else "It would work."
        feasible = True
    else:
        publics: list[str] = []
        for item in v:
            if item.public not in publics:
                publics.append(item.public)
        reason = " ".join(publics[:3])
        feasible = False
    return {"activity_id": aid, "option": option_label(problem, aid, opt), "feasible": feasible, "reason": reason,
            "would_also_change": [a for a in changed_activities(problem.base, snap) if a != aid],  # type: ignore[arg-type]
            "groups": sorted({item.group for item in v})}


def _objective_text(s: Scored, chosen: Scored | None) -> str:
    if chosen is None:
        return ""
    names = ["changed activities", "largest personal burden", "total burden", "people to reconfirm", "soft preferences missed", "added cost"]
    for i, (a, b) in enumerate(zip(s.objective, chosen.objective)):
        if a != b:
            return f"{names[i]} {a} vs {b}."
    return "same score."


__all__ = ["repair", "rejected_alternatives", "judge_option", "option_label", "CP_SAT_THRESHOLD", "ENUMERATION_LIMIT"]
