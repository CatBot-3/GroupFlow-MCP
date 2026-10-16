"""Interval CP-SAT project scheduler with a separate, independent validator.

One work item is one contiguous block (<=8h) on a 15-minute start grid. Long
deliverables consist of linked daily work units. Unlike route repair, parallel
items with disjoint people/resources have no artificial travel precedence.
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import date, datetime, time as day_time, timedelta, timezone
from zoneinfo import ZoneInfo

from .models import Calendar, ProjectConfig, WorkSpec


def utc_stamp(value: int) -> str:
    return datetime.fromtimestamp(value * 60, timezone.utc).isoformat(timespec="minutes")


def minutes(value: str | datetime) -> int:
    dt = datetime.fromisoformat(value) if isinstance(value, str) else value
    if dt.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return int(dt.timestamp() // 60)


def _calendar_key(work: dict, config: ProjectConfig) -> tuple:
    spec = work["spec"]
    calendar = config.private_calendars.get(work["owner_id"], config.calendar)
    return (str(spec["not_before"]), str(spec["due"]), spec.get("duration_minutes", 60),
            str(spec.get("locked_start")), calendar.model_dump_json())


def allowed_starts(work: dict, config: ProjectConfig, tz: str, window_start: str, days: int) -> list[tuple[int, int]]:
    spec = WorkSpec.model_validate(work["spec"])
    calendar = config.private_calendars.get(work["owner_id"], config.calendar)
    zone = ZoneInfo(tz)
    first = max(date.fromisoformat(window_start), spec.not_before)
    last = min(date.fromisoformat(window_start) + timedelta(days=days - 1), spec.due)
    result = []
    for n in range(max(0, (last - first).days + 1)):
        d = first + timedelta(days=n)
        if d.weekday() not in calendar.weekdays:
            continue
        lo = minutes(datetime.combine(d, day_time.fromisoformat(calendar.start), zone))
        hi = minutes(datetime.combine(d, day_time.fromisoformat(calendar.end), zone)) - spec.duration_minutes
        # Workdays may include a DST transition. Verify round-trip to reject
        # nonexistent local starts, rather than silently moving them.
        starts = []
        for t in range(lo, hi + 1, 15):
            local = datetime.fromtimestamp(t * 60, zone)
            end = datetime.fromtimestamp((t + spec.duration_minutes) * 60, zone)
            if local.date() != d or end.date() != d or local.strftime("%H:%M") < calendar.start or end.strftime("%H:%M") > calendar.end:
                continue
            if any(t < minutes(b) and minutes(a) < t + spec.duration_minutes for a, b in calendar.unavailable):
                continue
            if spec.locked_start is None or t == minutes(spec.locked_start):
                starts.append(t)
        result.extend((t, t) for t in starts)
    return result


def validate_schedule(schedule: list[dict], work: list[dict], config: ProjectConfig, tz: str, start: str, days: int,
                      capacities: dict[str, int], reservations: list[dict]) -> list[str]:
    """No CP model calls. Re-check coverage, calendars, edges and every capacity."""
    errors = []
    byid = {s["item_id"]: s for s in schedule}
    tasks = {w["id"]: w for w in work}
    if len(byid) != len(schedule) or set(byid) != set(tasks):
        return ["Schedule does not cover each work item exactly once."]
    intervals = defaultdict(list)
    calendar_domains = {}
    for iid, w in tasks.items():
        s, spec = byid[iid], WorkSpec.model_validate(w["spec"])
        lo, hi = minutes(s["start"]), minutes(s["end"])
        key = _calendar_key(w, config)
        if key not in calendar_domains:
            calendar_domains[key] = {a for a, _ in allowed_starts(w, config, tz, start, days)}
        legal = calendar_domains[key]
        if lo not in legal or hi - lo != spec.duration_minutes:
            errors.append(f"{iid}: time is outside the work window, calendar or duration.")
        if s.get("owner_id") != w["owner_id"] or s.get("resources", {}) != spec.resources:
            errors.append(f"{iid}: owner/resources do not match the input.")
        for dep in spec.depends_on:
            if dep not in byid or minutes(byid[dep]["end"]) > lo:
                errors.append(f"{iid}: dependency is missing or ends too late.")
        if hi > lo:
            intervals[f"person:{w['owner_id']}"].append((lo, hi, 1))
            for r, units in spec.resources.items():
                intervals[r].append((lo, hi, units))
    relevant = set(intervals)
    for r in reservations:
        if r["resource_id"] in relevant:
            intervals[r["resource_id"]].append((minutes(r["start_utc"]), minutes(r["end_utc"]), r["units"]))
    for resource, blocks in intervals.items():
        capacity = 1 if resource.startswith("person:") else capacities.get(resource, 0)
        events = sorted([(lo, units) for lo, hi, units in blocks if hi > lo] + [(hi, -units) for lo, hi, units in blocks if hi > lo])
        used = 0
        for _, delta in events:
            used += delta
            if used > capacity:
                errors.append("A person or shared resource has insufficient capacity.")
                break
    if config.budget_minor is not None and sum(WorkSpec.model_validate(w["spec"]).cost_minor for w in work) > config.budget_minor:
        errors.append("Stage cost exceeds the authorized budget.")
    return sorted(set(errors))


def solve(work: list[dict], config: ProjectConfig, tz: str, start: str, days: int, capacities: dict[str, int],
          reservations: list[dict], baseline: list[dict] | None = None, budget_s: float = 3.0) -> dict:
    # Reuse the guarded loader: on Windows a bundled older MSVC runtime can
    # terminate the process before Python has a chance to catch an exception.
    from groupflow.repair.cpsat import cp_model

    if cp_model is None:
        return {"status": "unavailable", "schedule": [], "backend": "cp_sat_intervals", "message": "Install OR-Tools and a supported C++ runtime; no schedule was published."}

    began = time.perf_counter()
    if not work:
        return {"status": "empty", "schedule": [], "backend": "cp_sat_intervals", "optimal": True, "ms": 0}
    if len(work) > 400:
        return {"status": "limit", "schedule": [], "backend": "cp_sat_intervals", "message": "Limit 400 work units per window; filter or split the project."}
    model = cp_model.CpModel()
    base = {s["item_id"]: s for s in baseline or []}
    epoch = minutes(datetime.combine(date.fromisoformat(start), day_time(), ZoneInfo(tz)))
    horizon = (days + 2) * 1440
    starts, ends, intervals = {}, {}, defaultdict(list)
    changed, moved = [], []
    specs = {w["id"]: WorkSpec.model_validate(w["spec"]) for w in work}
    calendar_domains = {}
    if config.budget_minor is not None and sum(s.cost_minor for s in specs.values()) > config.budget_minor:
        return {"status": "infeasible", "schedule": [], "backend": "cp_sat_intervals", "message": "Stage budget is exceeded."}
    for w in work:
        if time.perf_counter() - began >= budget_s:
            return {"status": "timeout", "schedule": [], "backend": "cp_sat_intervals", "ms": round((time.perf_counter()-began)*1000,2),
                    "message": "Model construction reached the time budget; use a smaller window or larger budget."}
        iid, spec = w["id"], specs[w["id"]]
        key = _calendar_key(w, config)
        if key not in calendar_domains:
            calendar_domains[key] = [(a - epoch, b - epoch) for a, b in allowed_starts(w, config, tz, start, days)]
        allowed = calendar_domains[key]
        if not allowed:
            return {"status": "infeasible", "schedule": [], "backend": "cp_sat_intervals", "message": "Some work has no valid time in its window/calendar."}
        sv = model.new_int_var_from_domain(cp_model.Domain.from_intervals(allowed), f"start:{iid}")
        ev = model.new_int_var(0, horizon, f"end:{iid}")
        iv = model.new_interval_var(sv, spec.duration_minutes, ev, iid)
        starts[iid], ends[iid] = sv, ev
        if spec.duration_minutes:
            intervals[f"person:{w['owner_id']}"].append((iv, 1))
            for resource, units in spec.resources.items():
                if resource not in capacities:
                    return {"status": "infeasible", "schedule": [], "backend": "cp_sat_intervals", "message": "A required resource is unavailable."}
                intervals[resource].append((iv, units))
        if iid in base:
            old = minutes(base[iid]["start"]) - epoch
            diff = model.new_bool_var(f"changed:{iid}")
            model.add(sv != old).only_enforce_if(diff)
            model.add(sv == old).only_enforce_if(diff.Not())
            delta = model.new_int_var(0, horizon + abs(old), f"delta:{iid}")
            model.add_abs_equality(delta, sv - old)
            changed.append(diff)
            moved.append(delta)
    for iid, spec in specs.items():
        for dep in spec.depends_on:
            if dep not in ends:
                return {"status": "infeasible", "schedule": [], "backend": "cp_sat_intervals", "message": "A dependency is outside the current window or missing."}
            model.add(ends[dep] <= starts[iid])
    relevant = set(intervals)
    for n, r in enumerate(reservations):
        resource = r["resource_id"]
        if resource not in relevant:
            continue
        lo, hi = minutes(r["start_utc"]) - epoch, minutes(r["end_utc"]) - epoch
        if hi <= 0 or lo >= horizon:
            continue
        intervals[resource].append((model.new_fixed_size_interval_var(lo, hi - lo, f"reserved:{n}"), r["units"]))
    for resource, blocks in intervals.items():
        if resource.startswith("person:"):
            model.add_no_overlap([iv for iv, _ in blocks])
        else:
            model.add_cumulative([iv for iv, _ in blocks], [d for _, d in blocks], capacities[resource])
    # True lexicographic optimization: fix an objective only after proof of
    # optimality. A timeout is feasible evidence, never a claim of minimum.
    levels = ([sum(changed), sum(moved)] if changed else []) + [sum(ends[w["id"]] * specs[w["id"]].priority for w in work)]
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    best, values, optimal = None, [], True
    for objective in levels:
        remaining = budget_s - (time.perf_counter() - began)
        if remaining <= 0:
            optimal = False
            break
        solver.parameters.max_time_in_seconds = remaining
        model.minimize(objective)
        status = solver.solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            optimal = False
            if best is None:
                return {"status": "infeasible" if status == cp_model.INFEASIBLE else "timeout", "schedule": [],
                        "backend": "cp_sat_intervals", "ms": round((time.perf_counter() - began) * 1000, 2)}
            break
        best = [{"item_id": w["id"], "title": w["title"], "owner_id": w["owner_id"],
                 "start": utc_stamp(solver.value(starts[w["id"]]) + epoch), "end": utc_stamp(solver.value(ends[w["id"]]) + epoch),
                 "resources": specs[w["id"]].resources} for w in work]
        value = solver.value(objective)
        values.append(value)
        if status != cp_model.OPTIMAL:
            optimal = False
            break
        model.add(objective == value)
    if best is None:
        return {"status": "timeout", "schedule": [], "backend": "cp_sat_intervals", "ms": round((time.perf_counter()-began)*1000,2)}
    errors = validate_schedule(best, work, config, tz, start, days, capacities, reservations)
    return {"status": "feasible" if not errors else "invalid", "schedule": best or [], "backend": "cp_sat_intervals",
            "optimal": optimal, "objectives": values, "objective_order": (["changed_starts", "moved_minutes"] if changed else []) + ["priority_weighted_completion"],
            "validation_errors": errors, "ms": round((time.perf_counter() - began) * 1000, 2)}
