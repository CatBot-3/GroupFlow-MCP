"""CP-SAT solver for larger domains (scope plan 11.7), using Google OR-Tools.

The model picks one option per activity. Activity-local checks (venue hours,
availability, diet, personal time windows, locks) remove options up front;
transfer times become forbidden option pairs; budgets are linear sums. The
lexicographic objective is optimised in stages: each stage is solved, proved
optimal, then fixed before the next one starts. If a stage only reaches
FEASIBLE within the time limit, the result is reported as best_found, never as
optimal. Every solution is re-checked by the independent validator.
"""
from __future__ import annotations

import time

from .domain import Option, assemble, build_domain, make_activity, score
from .enumerate import domain_size
from .model import DROP, SLOT, Problem, RepairOutcome, Scored
from .validate import activity_violations, chain, pair_violations, violations

from .msvc import prepare as _prepare_runtime

cp_model = None
if _prepare_runtime():  # on Windows, load a new enough C++ runtime first (see msvc.py)
    try:
        from ortools.sat.python import cp_model
    except ImportError:  # pragma: no cover - ortools is a declared dependency
        cp_model = None  # type: ignore[assignment]


def available() -> bool:
    return cp_model is not None


def fallback_reason(problem: Problem, domain: dict[str, list[Option]] | None = None) -> str | None:
    domain = domain if domain is not None else build_domain(problem)
    if any(opt[1] == DROP for opts in domain.values() for opt in opts):
        return "optional_activity_changes_transfer_chain"
    if _may_reorder(list(problem.base.activities), domain):
        return "time_options_may_reorder_activities"
    return None


def cpsat_repair(problem: Problem, time_budget_s: float = 5.0, max_candidates: int = 3) -> RepairOutcome:
    if cp_model is None:  # pragma: no cover
        raise RuntimeError("CP-SAT is not available here (OR-Tools missing, or no new enough Visual C++ runtime on Windows)")
    started = time.perf_counter()
    domain = build_domain(problem)
    reason = fallback_reason(problem, domain)
    if reason:
        from .enumerate import enumerate_repair

        out = enumerate_repair(problem, time_budget_s=time_budget_s, max_candidates=max_candidates)
        out.requested_method, out.fallback_reason = "cp-sat", reason
        out.runtime_ms = (time.perf_counter() - started) * 1000
        return out
    candidates: list[Scored] = []
    excluded: list[tuple] = []
    statuses: list[str] = []
    for _ in range(max_candidates):
        remaining = time_budget_s - (time.perf_counter() - started)
        if remaining <= 0.05:
            break
        status, scored = _solve(problem, domain, excluded, remaining)
        statuses.append(status)
        if scored is None:
            break
        candidates.append(scored)
        excluded.append(scored.signature)
        if status != "OPTIMAL":
            break
    first = statuses[0] if statuses else "UNKNOWN"
    if candidates:
        status = "optimal_within_domain" if first == "OPTIMAL" else "best_found"
    elif first == "INFEASIBLE":
        status = "infeasible_within_domain"
    else:
        status = "unknown"
    return RepairOutcome(status=status, method="cp-sat", candidates=candidates, search_complete=first == "OPTIMAL",
                         domain_size=domain_size(domain), explored=0, runtime_ms=(time.perf_counter() - started) * 1000, domain=domain)


def _solve(problem: Problem, domain: dict[str, list[Option]], excluded: list[tuple], time_limit: float) -> tuple[str, Scored | None]:
    base = problem.base
    acts = list(base.activities)
    members = list(base.members)
    w = problem.weights
    model = cp_model.CpModel()

    # Options that survive activity-local checks.
    allowed: dict[str, list[int]] = {}
    built = {}
    for a in acts:
        keep = []
        for oi, opt in enumerate(domain[a.id]):
            act = make_activity(problem, a, opt)
            built[(a.id, oi)] = act
            if not activity_violations(problem, base, act):
                keep.append(oi)
        allowed[a.id] = keep
        if not keep:
            return "INFEASIBLE", None

    x = {(a.id, oi): model.NewBoolVar(f"x_{a.id}_{oi}") for a in acts for oi in allowed[a.id]}
    for a in acts:
        model.AddExactlyOne(x[(a.id, oi)] for oi in allowed[a.id])

    # Eligibility was checked before modelling: the transfer chain is fixed.
    ordered = sorted(acts, key=lambda a: (a.start, a.id))
    for before, after in zip(ordered, ordered[1:]):
        for oi in allowed[before.id]:
            for oj in allowed[after.id]:
                if pair_violations(problem, built[(before.id, oi)], built[(after.id, oj)]):
                    model.AddBoolOr([x[(before.id, oi)].Not(), x[(after.id, oj)].Not()])

    # Personal overlaps for members who attend non-consecutive activities are covered by the chain when everyone
    # attends in plan order, which the domain preserves (participants never change).

    # Budgets.
    totals = {}
    for m in members:
        expr = sum(built[(a.id, oi)].share(m) * x[(a.id, oi)] for a in acts for oi in allowed[a.id])
        totals[m] = expr
        for c in problem.hard:
            if c.kind == "max_total_cost" and c.owner == m and f"budget:{m}" not in problem.disabled_groups and "budget" not in problem.disabled_groups:
                model.Add(expr <= int(c.get("amount_minor") or 0))

    # Objective terms.
    changed = {}
    for a in acts:
        orig = 0
        var = model.NewBoolVar(f"changed_{a.id}")
        if orig in allowed[a.id]:
            model.Add(var == 1 - x[(a.id, orig)])
        else:
            model.Add(var == 1)
        changed[a.id] = var

    burden = {}
    for m in members:
        terms = []
        for a in acts:
            if m not in a.participants:
                continue
            for oi in allowed[a.id]:
                act = built[(a.id, oi)]
                b = w.time_slot * (abs(act.start - a.start) // SLOT) + (w.venue if act.where != a.where else 0)
                if b:
                    terms.append(b * x[(a.id, oi)])
        base_total = base.member_total(m)
        steps = model.NewIntVar(0, 10_000, f"steps_{m}")
        model.Add(w.cost_step_minor * steps >= totals[m] - base_total)
        bm = model.NewIntVar(0, 1_000_000, f"burden_{m}")
        model.Add(bm == sum(terms) + w.cost_unit * steps)
        burden[m] = bm
    max_burden = model.NewIntVar(0, 1_000_000, "max_burden")
    model.AddMaxEquality(max_burden, list(burden.values()) or [0])
    total_burden = sum(burden.values())

    reconfirm = []
    any_change = model.NewBoolVar("any_change")
    model.AddMaxEquality(any_change, list(changed.values()))
    for m in members:
        r = model.NewBoolVar(f"reconfirm_{m}")
        if problem.confirmation_mode.get(m, "scoped") == "whole_plan":
            model.Add(r == any_change)
        else:
            mine = [changed[a.id] for a in acts if m in a.participants]
            model.AddMaxEquality(r, mine or [0])
        reconfirm.append(r)
    soft_loss = sum(changed[c.get("activity_id")] for c in problem.soft if c.kind == "prefer_keep" and c.get("activity_id") in changed)
    added_cost = sum(totals[m] - base.member_total(m) for m in members)

    for sig in excluded:
        lits = []
        for a, venue in zip(acts, sig):
            same = [x[(a.id, oi)] for oi in allowed[a.id] if (domain[a.id][oi][1] or a.place_name) == venue]
            lits.append(sum(same) if same else 0)
        model.Add(sum(lits) <= len(acts) - 1)

    stages = [sum(changed.values()), max_burden, total_burden, sum(reconfirm), soft_loss, added_cost]
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1  # deterministic
    deadline = time.perf_counter() + time_limit
    overall = "OPTIMAL"
    for expr in stages:
        if isinstance(expr, int):
            continue
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            overall = "FEASIBLE"
            break
        solver.parameters.max_time_in_seconds = max(0.01, remaining)
        model.Minimize(expr)
        status = solver.Solve(model)
        if status == cp_model.INFEASIBLE:
            return "INFEASIBLE", None
        if status == cp_model.MODEL_INVALID:
            raise RuntimeError("CP-SAT model invalid")
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return "UNKNOWN", None
        value = int(round(solver.ObjectiveValue()))
        if status == cp_model.OPTIMAL:
            model.Add(expr == value)
        else:
            overall = "FEASIBLE"
            model.Add(expr <= value)
            break
        model.ClearHints()
        for k, v in x.items():
            model.AddHint(v, solver.Value(v))

    choice = {}
    for a in acts:
        for oi in allowed[a.id]:
            if solver.Value(x[(a.id, oi)]):
                choice[a.id] = domain[a.id][oi]
    snap = assemble(problem, choice)
    if violations(problem, snap):  # independent re-check; a mismatch means a modelling bug
        raise RuntimeError("CP-SAT returned a candidate that fails validation")
    return overall, score(problem, snap)


def _may_reorder(acts: list, domain: dict[str, list[Option]]) -> bool:
    """True if some options could change the order of activities, which changes the transfer chain (enumeration handles it)."""
    order = sorted(acts, key=lambda a: (a.start, a.id))
    for x, y in zip(order, order[1:]):
        if max(o[0] for o in domain[x.id]) > min(o[0] for o in domain[y.id]):
            return True
    return False


__all__ = ["available", "cpsat_repair", "chain", "fallback_reason"]
