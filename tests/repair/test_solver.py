from dataclasses import replace

import pytest

from groupflow.repair.domain import build_domain
from groupflow.repair.enumerate import domain_size
from groupflow.repair.fixtures import demo_problem
from groupflow.repair.model import Activity, Constraint, Place, Unavailability
from groupflow.repair.solver import rejected_alternatives, repair
from groupflow.repair.validate import violations
from tests.repair.gen import brute_force_best, random_problem

A_UNAVAILABLE = (Unavailability("restaurant_a", 18 * 60, 21 * 60, "ev1"),)
NAMES = ("Alice", "Bob", "Chen", "Dana")


def demo():
    return demo_problem(unavailable=A_UNAVAILABLE)


def test_the_starting_plan_is_feasible_before_the_change():
    p = demo_problem()
    assert violations(p, p.base) == []


@pytest.mark.parametrize("method", ["enumeration", "cp-sat"])
def test_restaurant_b_replaces_a_with_one_change(method):
    o = repair(demo(), method=method)
    assert o.status == "optimal_within_domain"
    best = o.candidates[0]
    assert best.changed == ("dinner",)
    dinner = best.snapshot.activity("dinner")
    assert (dinner.place_id, dinner.start, dinner.end) == ("restaurant_b", 18 * 60 + 30, 19 * 60 + 30)
    assert best.objective == (1, 10, 40, 4, 0, 800)
    assert all(best.snapshot.member_total(m) == 4300 for m in best.snapshot.members)
    assert violations(demo(), best.snapshot) == []


def test_why_not_c_is_explained_without_naming_anyone():
    p = demo()
    o = repair(p)
    reasons = {r["option"]: r for r in rejected_alternatives(p, o.candidates[0], o.domain)}
    c = reasons["Restaurant C at 19:00"]
    assert c["feasible"] is False
    assert "21:15" in c["reason"] and "latest time to be home" in c["reason"]
    assert set(c["would_also_change"]) == {"dessert", "return_home"}
    assert "dietary" in reasons["Restaurant E at 18:30"]["reason"]
    for r in reasons.values():
        assert not any(n in r["reason"] for n in NAMES)


def test_budget_lowered_to_42_makes_the_domain_infeasible_and_explains_why():
    p = demo()
    cons = tuple(c for c in p.constraints if not (c.owner == "bob" and c.kind == "max_total_cost"))
    p = replace(p, constraints=cons + (Constraint.make("b42", "bob", "max_total_cost", {"amount_minor": 4200}),))
    for method in ("enumeration", "cp-sat"):
        o = repair(p, method=method)
        assert o.status == "infeasible_within_domain", method
        assert o.candidates == []
    groups = {v.group for v in repair(p).conflict}
    assert {"budget", "latest_end", "place_unavailable:restaurant_a"} <= groups
    assert not any(n in v.public for v in repair(p).conflict for n in NAMES)


def test_timeout_is_never_reported_as_proof():
    o = repair(demo(), method="enumeration", time_budget_s=0.0)
    assert o.status in ("unknown", "best_found")
    assert o.status != "infeasible_within_domain"


def test_locked_activities_never_move():
    p = demo()
    acts = tuple(replace(a, locked=True) if a.id == "dessert" else a for a in p.base.activities)
    p = replace(p, base=replace(p.base, activities=acts))
    o = repair(p)
    d = o.candidates[0].snapshot.activity("dessert")
    assert (d.start, d.place_id) == (19 * 60 + 45, "dessert_d")
    assert build_domain(p)["dessert"] == [(19 * 60 + 45, "dessert_d")]


def test_reconfirmation_is_limited_to_people_in_the_changed_activity():
    """Scope plan 14.3: an activity only Alice and Chen attend changes; Bob and Dana keep their approvals."""
    p = demo_problem()
    museum = Activity(id="museum", title="Museum visit", kind="dessert", start=19 * 60 + 45, end=20 * 60 + 15, place_id="dessert_d",
                      participants=("alice", "chen"), cost_shares=(("alice", 800), ("chen", 800)))
    acts = tuple(museum if a.id == "dessert" else a for a in p.base.activities)
    gelato = Place(id="gelato_g", name="Gelato G", kind="dessert", zone="harbour", open_min=12 * 60, close_min=22 * 60, capacity=10,
                   price_minor=900, tags=frozenset({"vegetarian", "nut_free", "step_free"}))
    catalog = replace(p.catalog, places={**p.catalog.places, "gelato_g": gelato})
    unavailable = (Unavailability("dessert_d", 19 * 60 + 30, 20 * 60 + 30, "ev2"),)
    p = replace(p, base=replace(p.base, activities=acts), catalog=catalog, unavailable=unavailable)
    o = repair(p)
    assert o.status == "optimal_within_domain"
    best = o.candidates[0]
    assert best.changed == ("museum",)
    assert best.reconfirm == frozenset({"alice", "chen"})


def test_whole_plan_members_reconfirm_any_change():
    p = replace(demo(), confirmation_mode={"alice": "scoped", "bob": "whole_plan", "chen": "scoped", "dana": "scoped"})
    assert "bob" in repair(p).candidates[0].reconfirm


FIXED_SEEDS = list(range(20))
RANDOM_SEEDS = list(range(1000, 1100))


@pytest.mark.parametrize("seed", FIXED_SEEDS + RANDOM_SEEDS)
def test_both_solvers_match_brute_force(seed):
    p = random_problem(seed)
    assert domain_size(build_domain(p)) <= 4096
    truth = brute_force_best(p)
    enum = repair(p, method="enumeration", time_budget_s=20, explain=False)
    cp = repair(p, method="cp-sat", time_budget_s=20, explain=False)
    if truth is None:
        assert enum.status == "infeasible_within_domain"
        assert cp.status == "infeasible_within_domain"
        return
    assert enum.status == "optimal_within_domain"
    assert cp.status == "optimal_within_domain"
    assert enum.candidates[0].objective == truth.objective
    assert cp.candidates[0].objective == truth.objective
    for c in enum.candidates + cp.candidates:
        assert violations(p, c.snapshot) == []


def test_cp_sat_is_really_used_when_available_and_falls_back_otherwise(monkeypatch):
    from groupflow.repair import cpsat, msvc, solver

    assert msvc.prepare() is True or cpsat.available() is False
    if cpsat.available():
        calls = []
        solve = cpsat._solve

        def counted(*args, **kwargs):
            calls.append(True)
            return solve(*args, **kwargs)

        monkeypatch.setattr(cpsat, '_solve', counted)
        assert repair(replace(demo(), shifts=()), method="cp-sat").method == "cp-sat"
        assert calls, 'The real CP-SAT model must run; a method label is not evidence.'
        calls.clear()
        fallback = repair(demo(), method='cp-sat')
        assert fallback.method == 'enumeration' and fallback.requested_method == 'cp-sat'
        assert fallback.fallback_reason == 'time_options_may_reorder_activities'
        assert not calls
    monkeypatch.setattr(solver, "cpsat_available", lambda: False)
    o = repair(demo(), method="cp-sat")
    assert o.method == "enumeration" and o.status == "optimal_within_domain"
    assert o.requested_method == 'cp-sat' and o.fallback_reason == 'cp_sat_unavailable'


@pytest.mark.parametrize('size,expected', [(16_383, 'enumeration'), (16_384, 'cp-sat'), (16_385, 'cp-sat')])
def test_auto_routing_at_the_measured_combination_threshold(monkeypatch, size, expected):
    from groupflow.repair import solver
    from groupflow.repair.model import RepairOutcome

    calls = []

    def outcome(method):
        def run(*args, **kwargs):
            calls.append(method)
            return RepairOutcome(status='unknown', method=method, candidates=[], search_complete=False,
                                 domain_size=size, explored=0, runtime_ms=0.0, domain={})
        return run

    monkeypatch.setattr(solver, 'domain_size', lambda domain: size)
    monkeypatch.setattr(solver, 'cpsat_available', lambda: True)
    monkeypatch.setattr(solver, 'enumerate_repair', outcome('enumeration'))
    monkeypatch.setattr(solver, 'cpsat_repair', outcome('cp-sat'))
    result = solver.repair(replace(demo(), shifts=()), explain=False)
    assert calls == [expected] and result.method == expected
    assert result.requested_method == 'auto'


@pytest.mark.parametrize('family', ['one_closure', 'many_closures', 'tight_budget', 'infeasible_budget'])
def test_real_large_auto_cp_sat_matches_completed_enumeration(family):
    from scripts.benchmark_solver import make_problem
    from groupflow.repair import cpsat

    if not cpsat.available():
        pytest.skip('OR-Tools unavailable')
    problem = make_problem(7, family, seed=1, options=4)
    assert domain_size(build_domain(problem)) == 16_384
    automatic = repair(problem, time_budget_s=10, explain=False)
    reference = repair(problem, method='enumeration', time_budget_s=10, explain=False)
    assert automatic.method == 'cp-sat' and automatic.requested_method == 'auto'
    assert automatic.fallback_reason is None
    assert automatic.status == reference.status
    assert automatic.status in ('optimal_within_domain', 'infeasible_within_domain')
    assert (automatic.candidates[0].objective if automatic.candidates else None) == (reference.candidates[0].objective if reference.candidates else None)


@pytest.mark.parametrize('unsupported', ['optional', 'reorder', 'unavailable'])
def test_large_auto_domains_keep_truthful_fallbacks(monkeypatch, unsupported):
    from groupflow.repair import solver
    from groupflow.repair.model import RepairOutcome

    p = demo()
    reason = 'time_options_may_reorder_activities'
    if unsupported == 'optional':
        acts = tuple(replace(a, required=False) if a.id == 'dessert' else a for a in p.base.activities)
        p = replace(p, base=replace(p.base, activities=acts), shifts=(), max_options=64)
        reason = 'optional_activity_changes_transfer_chain'
    elif unsupported == 'unavailable':
        reason = 'cp_sat_unavailable'
    monkeypatch.setattr(solver, 'domain_size', lambda domain: 65_536)
    monkeypatch.setattr(solver, 'cpsat_available', lambda: unsupported != 'unavailable')
    monkeypatch.setattr(solver, 'enumerate_repair', lambda *a, **kw: RepairOutcome(status='unknown', method='enumeration', candidates=[],
                          search_complete=False, domain_size=65_536, explored=0, runtime_ms=0.0, domain={}))
    monkeypatch.setattr(solver, 'cpsat_repair', lambda *a, **kw: pytest.fail('Unsupported domain must never enter CP-SAT'))
    result = solver.repair(p, explain=False)
    assert result.method == 'enumeration' and result.requested_method == 'auto'
    assert result.fallback_reason == reason


def test_multi_day_meeting_moves_a_day_when_a_person_is_busy():
    """A team meeting on Tuesday 10:00; Marcus becomes busy Tuesday morning; the repair may move it within the week."""
    from groupflow.repair.model import Catalog, Problem, Snapshot

    room = Place(id="maple", name="Maple room", kind="room", zone="office", open_min=8 * 60, close_min=18 * 60, capacity=6, price_minor=0)
    people = ("priya", "marcus", "jun")
    tue10 = 1440 + 10 * 60
    meeting = Activity(id="meeting", title="Design review", kind="room", start=tue10, end=tue10 + 60, place_id="maple", participants=people,
                       cost_shares=tuple((m, 0) for m in sorted(people)))
    base = Snapshot(plan_id="p", start_date="2026-10-19", days=5, timezone="America/Toronto", currency="CAD", activities=(meeting,))
    busy = Constraint.make("b", "marcus", "unavailable_window", {"windows": [[1440 + 9 * 60, 1440 + 12 * 60]], "label": "busy time"})
    p = Problem(base=base, catalog=Catalog(places={"maple": room}, travel={}), constraints=(busy,), shifts=(60, -60, 120, 1440, 240),
                max_options=8, names={"marcus": "Marcus"})
    assert [v.code for v in violations(p, base)] == ["BUSY"]
    assert "Marcus" not in violations(p, base)[0].public and "Tue 10:00" in violations(p, base)[0].public
    o = repair(p)
    best = o.candidates[0].snapshot.activity("meeting")
    assert best.start == 1440 + 12 * 60  # +120 minutes is the smallest move that clears the busy window
    assert o.candidates[0].reconfirm == frozenset(people)
