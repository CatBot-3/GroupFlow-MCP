"""Compare real CP-SAT with enumeration on identical, fixed-order finite domains.

No model calls, credentials or saved application data are used. Timing includes
domain construction, modelling, lexicographic optimisation and validation, but
excludes conflict narration. Run from the project root; the last result is summarized in docs/EVALUATION.md.
"""
from __future__ import annotations

import argparse
import json
import math
import platform
import random
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from groupflow.repair.cpsat import available
from groupflow.repair.domain import build_domain
from groupflow.repair.enumerate import domain_size
from groupflow.repair.model import Activity, Catalog, Constraint, Place, Problem, Snapshot, Unavailability
from groupflow.repair.solver import repair
from groupflow.repair.validate import violations

FAMILIES = ('one_closure', 'many_closures', 'tight_budget', 'infeasible_budget')


def make_problem(count: int, family: str, seed: int, options: int = 4) -> Problem:
    rng = random.Random(seed)
    members = ('m0', 'm1', 'm2', 'm3')
    places, activities = {}, []
    for i in range(count):
        start = 8 * 60 + i * 75
        # Skip midnight so opening-hour checks do not change the benchmark family.
        if start % 1440 > 23 * 60:
            start += 1440 - start % 1440
        prices = [1000] + [int(1000 - 750 * j / (options - 1)) + rng.randrange(0, 50) for j in range(1, options)]
        for j in range(options):
            pid = f'p{i}_{j}'
            places[pid] = Place(pid, pid, f'kind{i}', 'local', 0, 1439, 8, prices[j], frozenset({'step_free'}))
        activities.append(Activity(f'a{i}', f'Activity {i}', f'kind{i}', start, start + 30, f'p{i}_0', members,
                                   tuple((m, 1000) for m in members)))
    base = Snapshot('benchmark', '2026-10-19', max(a.end for a in activities) // 1440 + 1, 'America/Toronto', 'CAD', tuple(activities))
    closures = []
    if family in ('one_closure', 'many_closures'):
        targets = activities[:1] if family == 'one_closure' else activities
        closures = [Unavailability(a.place_id, a.start, a.end, f'closed_{a.id}') for a in targets]
    constraints = []
    if family in ('tight_budget', 'infeasible_budget'):
        cap = int(count * 1000 * 0.52) if family == 'tight_budget' else count * 100
        constraints = [Constraint.make(f'budget_{m}', m, 'max_total_cost', {'amount_minor': cap}) for m in members]
    return Problem(base, Catalog(places, {('local', 'local'): 0}), tuple(constraints), tuple(closures), shifts=(), max_options=options)


def compare(problem: Problem, repetitions: int, budget: float) -> dict:
    timings = {'enumeration': [], 'cp-sat': []}
    outcomes = {'enumeration': [], 'cp-sat': []}
    for rep in range(repetitions):
        # Alternate order to reduce systematic first-run and thermal bias.
        methods = ('enumeration', 'cp-sat') if rep % 2 == 0 else ('cp-sat', 'enumeration')
        for method in methods:
            started = time.perf_counter()
            out = repair(problem, method=method, time_budget_s=budget, max_candidates=3, explain=False)
            elapsed = (time.perf_counter() - started) * 1000
            if out.method != method:
                raise RuntimeError(f'Excluded fallback: requested {method}, ran {out.method}: {out.fallback_reason}')
            for cand in out.candidates:
                if violations(problem, cand.snapshot):
                    raise AssertionError('Invalid candidate')
            timings[method].append(elapsed)
            outcomes[method].append({'status': out.status, 'objective': list(out.candidates[0].objective) if out.candidates else None,
                                     'explored': out.explored})
        a, b = outcomes['enumeration'][-1], outcomes['cp-sat'][-1]
        proved = {'optimal_within_domain', 'infeasible_within_domain'}
        if a['status'] in proved and b['status'] in proved:
            if a['status'] != b['status'] or a['objective'] != b['objective']:
                raise AssertionError(f'Solvers disagree: {a} vs {b}')
    result = {}
    for method in timings:
        result[method] = {'median_ms': round(statistics.median(timings[method]), 4),
                          'samples_ms': [round(x, 4) for x in timings[method]], 'outcomes': outcomes[method]}
    result['speedup_cp_sat'] = result['enumeration']['median_ms'] / max(result['cp-sat']['median_ms'], 0.0001)
    return result


def summarize(rows: list[dict]) -> list[dict]:
    output = []
    for size in sorted({r['domain_size'] for r in rows}):
        group = [r for r in rows if r['domain_size'] == size]
        output.append({'domain_size': size, 'activities': group[0]['activities'], 'options_per_activity': group[0]['options_per_activity'], 'cases': len(group),
                       'enumeration_median_ms': round(statistics.median(r['enumeration']['median_ms'] for r in group), 3),
                       'cp_sat_median_ms': round(statistics.median(r['cp-sat']['median_ms'] for r in group), 3),
                       'cp_sat_win_fraction': sum(r['speedup_cp_sat'] > 1 for r in group) / len(group),
                       'geometric_mean_speedup': round(math.exp(statistics.mean(math.log(r['speedup_cp_sat']) for r in group)), 3)})
    return output


def recommend(summary: list[dict]) -> int | None:
    # Require two measured consecutive scales, >=75% case wins and >=25% geometric speedup.
    for a, b in zip(summary, summary[1:]):
        if all(r['cp_sat_win_fraction'] >= 0.75 and r['geometric_mean_speedup'] >= 1.25 for r in (a, b)):
            return a['domain_size']
    return None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--activities', type=int, nargs='+', default=[4, 5, 6, 7, 8])
    p.add_argument('--options', type=int, nargs='+', default=[3, 4, 5])
    p.add_argument('--seeds', type=int, default=3)
    p.add_argument('--repetitions', type=int, default=3)
    p.add_argument('--budget', type=float, default=2.0)
    p.add_argument('--out', type=Path, default=Path('eval/runs/solver_benchmark.json'))
    args = p.parse_args()
    if not available():
        raise SystemExit('Real CP-SAT unavailable: benchmark refused instead of timing fallback enumeration.')
    from importlib.metadata import version

    for method in ('enumeration', 'cp-sat'):
        repair(make_problem(3, 'tight_budget', 0), method=method, max_candidates=3, explain=False)
    rows = []
    for count, options in ((n, o) for n in args.activities for o in args.options):
        for family in FAMILIES:
            for seed in range(args.seeds):
                problem = make_problem(count, family, seed, options)
                row = {'activities': count, 'options_per_activity': options, 'domain_size': domain_size(build_domain(problem)),
                       'family': family, 'seed': seed, **compare(problem, args.repetitions, args.budget)}
                rows.append(row)
                print(f'{count} activities / {row["domain_size"]} combinations / {family} / seed {seed}: '
                      f'enum {row["enumeration"]["median_ms"]:.2f} ms, CP-SAT {row["cp-sat"]["median_ms"]:.2f} ms', flush=True)
                summary = summarize(rows)
                args.out.parent.mkdir(parents=True, exist_ok=True)
                report = {'created_at': datetime.now(timezone.utc).isoformat(), 'python': platform.python_version(),
                          'platform': platform.platform(), 'machine': platform.machine(), 'ortools': version('ortools'),
                          'repetitions': args.repetitions, 'seeds_per_family': args.seeds, 'budget_seconds_per_search': args.budget,
                          'max_candidates': 3, 'includes': 'domain/model/solve/independent validation; excludes conflict narration and LLM',
                          'summary': summary, 'recommended_cp_sat_threshold': recommend(summary), 'rows': rows}
                args.out.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'summary': summary, 'recommended_cp_sat_threshold': recommend(summary)}, indent=2))


if __name__ == '__main__':
    main()
