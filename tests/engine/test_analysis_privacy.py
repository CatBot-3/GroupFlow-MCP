"""Independent acceptance of hypothetical planning, consented history and privacy.

Cases were frozen before reviewing the implementation. These tests use
synthetic databases only.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import pytest

from groupflow.core.db import Db
from groupflow.core.errors import DomainError
from groupflow.engine import GroupFlow, LISTENERS, fixed_clock
from groupflow.engine.agreement import snapshot_from_dict
from groupflow.repair.domain import burden, score
from groupflow.repair.model import Constraint, Unavailability
from groupflow.repair.validate import violations
from groupflow.world import approve_all, seed_group, seed_world, wipe_group
from tests.conftest import WORLD, seeded


PLAN = 'friday_dinner'
FRIENDS = ('alice', 'bob', 'chen', 'dana')
AT = '2026-10-19T14:00:00Z'


def business_state(gf):
    """Every persistent table except the explicitly permitted privacy counter."""
    result = {}
    for table in gf.db.all("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        name = table['name']
        if name in {'diagnostic_queries', 'sqlite_sequence'}:
            continue
        rows = gf.db.all('SELECT * FROM "' + name.replace('"', '""') + '"')
        result[name] = sorted(json.dumps(row, sort_keys=True, default=str) for row in rows)
    return result


def diagnostic(gf, mode='resilience', actor='alice', plan=PLAN, scenarios=None, candidate=None):
    kwargs = {'mode': mode}
    if scenarios is not None:
        kwargs['scenarios'] = scenarios
    if candidate is not None:
        kwargs['candidate'] = candidate
    return gf.planning_options(actor, plan, **kwargs)


def caught(fn):
    try:
        return fn()
    except DomainError as error:
        return {'error': {'code': error.code, 'message': str(error)}}


def budget_denied(value):
    error = value.get('error', {})
    marker = str(error.get('code', value.get('outcome', ''))).upper()
    return any(word in marker for word in ('BUDGET', 'RATE_LIMIT', 'DIAGNOSTIC_LIMIT'))


def first_scenario(result):
    assert result.get('hypothetical') is True or result.get('read_only') is True, result
    assert isinstance(result.get('scenarios'), list) and result['scenarios'], result
    return result['scenarios'][0]


def repair_snapshots(scenario):
    """Only validated hypothetical repairs, not the intentionally failing base."""
    repairs = scenario.get('repairs', [])
    if isinstance(repairs, dict):
        repairs = [repairs]
    snapshots = []
    for repair in repairs:
        data = repair.get('snapshot', repair.get('repair_snapshot'))
        if data:
            snapshots.append(snapshot_from_dict(data))
    return snapshots


def test_resilience_venue_closure_is_independently_feasible_and_business_read_only():
    gf = seeded({PLAN: 'committed'})
    gf.set_clock(fixed_clock(AT))
    plan = gf._plan_row(PLAN)
    base = gf._latest_snapshot(plan)
    problem = gf._problem(plan, base)
    before, version = business_state(gf), gf.state_token(PLAN)
    result = diagnostic(gf, scenarios=[{'kind': 'place_unavailable', 'place_id': 'restaurant_a', 'start': 1080, 'end': 1260}])
    scenario = first_scenario(result)
    repairs = repair_snapshots(scenario)
    assert repairs, 'The known Restaurant B repair should be inspectable.'
    truth = replace(problem, unavailable=problem.unavailable + (Unavailability('restaurant_a', 1080, 1260, 'independent-test'),))
    for candidate in repairs:
        assert candidate.plan_id == PLAN
        assert not violations(truth, candidate)
        assert candidate.activity('dinner').participants == base.activity('dinner').participants
        assert candidate.activity('dinner').place_id != 'restaurant_a'
    assert business_state(gf) == before
    assert gf.state_token(PLAN) == version
    assert not any(word in json.dumps(result).lower() for word in ('success_probability', 'failure_probability', 'globally_optimal'))
    # A search timeout is uncertainty, not proof that a future scenario has
    # no possible repair. Check the diagnostic wrapper preserves this fact.
    gf.solver_budget_s = 0
    uncertain = first_scenario(diagnostic(gf, scenarios=[{'kind': 'place_unavailable', 'place_id': 'restaurant_a', 'start': 1080, 'end': 1260}]))
    assert uncertain['status'] in {'unknown', 'best_found'}, uncertain
    assert business_state(gf) == before and gf.state_token(PLAN) == version


def test_resilience_price_change_reprices_every_returned_share_and_preserves_budgets():
    gf = seeded({PLAN: 'committed'})
    plan = gf._plan_row(PLAN)
    base = gf._latest_snapshot(plan)
    problem = gf._problem(plan, base)
    changed_place = replace(problem.catalog.places['restaurant_a'], price_minor=4000)
    catalog = replace(problem.catalog, places={**problem.catalog.places, 'restaurant_a': changed_place})
    before = business_state(gf)
    scenario = first_scenario(diagnostic(gf, scenarios=[{'kind': 'price_changed', 'place_id': 'restaurant_a', 'price_minor': 4000}]))
    repairs = repair_snapshots(scenario)
    assert repairs
    truth = replace(problem, catalog=catalog)
    for candidate in repairs:
        assert not violations(truth, candidate)
        for activity in candidate.live:
            if activity.place_id:
                assert all(activity.share(member) == catalog.places[activity.place_id].price_minor for member in activity.participants)
        assert all(candidate.member_total(member) <= 4500 for member in FRIENDS)
    assert business_state(gf) == before


def test_resilience_busy_participant_keeps_people_and_checks_their_new_window():
    gf = seeded({PLAN: 'committed'})
    plan = gf._plan_row(PLAN)
    base = gf._latest_snapshot(plan)
    problem = gf._problem(plan, base)
    busy = Constraint.make('independent-busy', 'bob', 'unavailable_window', {'windows': [(1110, 1125)]})
    before = business_state(gf)
    scenario = first_scenario(diagnostic(gf, scenarios=[{'kind': 'participant_unavailable', 'member_id': 'bob', 'start': 1110, 'end': 1125}]))
    truth = replace(problem, constraints=problem.constraints + (busy,))
    for candidate in repair_snapshots(scenario):
        assert not violations(truth, candidate)
        assert set(candidate.members) == set(base.members)
        assert candidate.activity('dinner').participants == base.activity('dinner').participants
    status = scenario.get('status', scenario.get('search_status'))
    assert status in {'optimal_within_domain', 'best_found', 'unknown', 'infeasible_within_domain'}, scenario
    assert business_state(gf) == before


@pytest.mark.parametrize('scenarios', [
    [{'kind': 'unsupported', 'place_id': 'restaurant_a'}],
    [{'kind': 'place_unavailable', 'place_id': 'no-such-place'}],
    [{'kind': 'participant_unavailable', 'member_id': 'linda'}],
    [{'kind': 'place_unavailable', 'place_id': 'restaurant_a', 'start': 1200, 'end': 1100}],
    [{'kind': 'price_changed', 'place_id': 'restaurant_a', 'price_minor': -1}],
    [{'kind': 'price_changed', 'place_id': 'restaurant_a', 'price_minor': 12.5}],
    [{'kind': 'price_changed', 'place_id': 'restaurant_a', 'price_minor': True}],
    [{'kind': 'price_changed', 'place_id': 'restaurant_a', 'price_minor': float('inf')}],
    [{'kind': 'place_unavailable', 'place_id': 'restaurant_a'}] * 7,
])
def test_invalid_resilience_inputs_fail_without_business_mutation(scenarios):
    gf = seeded({PLAN: 'committed'})
    before = business_state(gf)
    with pytest.raises(DomainError):
        diagnostic(gf, scenarios=scenarios)
    assert business_state(gf) == before


def test_foreign_and_discarded_candidates_cannot_be_an_analysis_oracle():
    gf = seeded({PLAN: 'committed'})
    foreign = gf._plan_row('design_review')['active_revision_id']
    before = business_state(gf)
    with pytest.raises(DomainError):
        diagnostic(gf, candidate=foreign, scenarios=[{'kind': 'place_unavailable', 'place_id': 'restaurant_a'}])
    assert business_state(gf) == before
    gf.propose_change('alice', plan=PLAN, item='dinner', place='Restaurant B')
    discarded = gf._plan_row(PLAN)['candidate_revision_id']
    gf.propose_change('alice', plan=PLAN, item='dessert', start='20:00')
    assert gf._revision(discarded)['kind'] == 'discarded'
    before = business_state(gf)
    with pytest.raises(DomainError):
        diagnostic(gf, candidate=discarded, scenarios=[{'kind': 'place_unavailable', 'place_id': 'restaurant_a'}])
    assert business_state(gf) == before


def test_three_diagnostic_modes_share_five_calls_despite_version_and_listener_churn():
    gf = seeded({PLAN: 'committed'})
    gf.set_clock(fixed_clock(AT))
    sequence = [('relaxations', None), ('resilience', None), ('fairness', frozenset({'alice', 'bob'})), ('resilience', None), ('fairness', None)]
    for index, (mode, listeners) in enumerate(sequence):
        ctx = LISTENERS.set(('alice', listeners)) if listeners else None
        try:
            result = caught(lambda: diagnostic(gf, mode=mode, scenarios=[{'kind': 'price_changed', 'place_id': 'restaurant_a', 'price_minor': 3300 + index}] if mode == 'resilience' else None))
            assert not budget_denied(result), (index, result)
        finally:
            if ctx is not None:
                LISTENERS.reset(ctx)
        if index == 2:
            gf.propose_change('alice', plan=PLAN, new_type='task', title='Budget version marker')
    before = business_state(gf)
    assert budget_denied(caught(lambda: diagnostic(gf, mode='relaxations')))
    assert business_state(gf) == before
    assert not budget_denied(caught(lambda: diagnostic(gf, actor='bob', mode='fairness')))
    gf.set_clock(fixed_clock('2026-10-19T14:10:01Z'))
    assert not budget_denied(caught(lambda: diagnostic(gf, mode='fairness')))


def test_parallel_diagnostics_cannot_exceed_the_shared_budget():
    gf = seeded({PLAN: 'committed'})
    gf.set_clock(fixed_clock(AT))
    before = business_state(gf)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: caught(lambda: diagnostic(gf, mode='fairness')), range(8)))
    assert sum(not budget_denied(result) for result in results) == 5, results
    assert business_state(gf) == before


def test_resilience_solving_releases_the_database_for_an_unrelated_plan(monkeypatch):
    from groupflow.engine import planning_analysis

    gf = seeded({PLAN: 'committed'})
    entered, release = Event(), Event()
    original = planning_analysis.repair
    observed_transactions = []

    def paused_repair(*args, **kwargs):
        observed_transactions.append(gf.db.in_transaction)
        entered.set()
        assert release.wait(5), 'Acceptance did not release the paused solver.'
        return original(*args, **kwargs)

    monkeypatch.setattr(planning_analysis, 'repair', paused_repair)
    with ThreadPoolExecutor(max_workers=2) as executor:
        running = executor.submit(diagnostic, gf, scenarios=[{'kind': 'place_unavailable', 'place_id': 'restaurant_a'}])
        try:
            assert entered.wait(5), 'The diagnostic did not enter its solver.'
            unrelated = executor.submit(gf.propose_change, 'alice', plan='party', new_type='task', title='Progress while diagnostics solve')
            assert unrelated.result(timeout=1).outcome == 'created'
        finally:
            release.set()
        assert running.result(timeout=5)['outcome'] == 'resilience'
    assert observed_transactions and not any(observed_transactions)


def test_diagnostic_budget_is_plan_local_and_persists_across_engine_restart(tmp_path):
    path = str(tmp_path / 'analysis-privacy-budget.sqlite3')
    gf = GroupFlow(Db(path))
    seed_world(gf, WORLD, {PLAN: 'committed'})
    gf.set_clock(fixed_clock(AT))
    other = gf.create_plan('alice', 'evening_out', 'Independent diagnostic allowance', group='friends', starter=False).plan.id
    for _ in range(5):
        assert not budget_denied(caught(lambda: diagnostic(gf, mode='fairness')))
    assert budget_denied(caught(lambda: diagnostic(gf, mode='fairness')))
    assert not budget_denied(caught(lambda: diagnostic(gf, mode='fairness', plan=other)))
    gf.db.close()
    reopened = GroupFlow(Db(path))
    try:
        reopened.set_clock(fixed_clock(AT))
        assert budget_denied(caught(lambda: diagnostic(reopened, mode='fairness')))
        assert not budget_denied(caught(lambda: diagnostic(reopened, mode='fairness', plan=other)))
    finally:
        reopened.db.close()


@pytest.mark.parametrize('mode', ['relaxations', 'resilience', 'fairness'])
def test_other_people_private_conditions_coarsen_room_diagnostics_without_owner_oracle(mode):
    gf = seeded({PLAN: 'committed'})
    private_ids = [row['id'] for row in gf.db.all("SELECT id FROM conditions WHERE plan_id=? AND owner_id!='alice' AND visibility='private' AND strength='hard' AND status='active'", PLAN)]
    assert private_ids
    before = business_state(gf)
    token = LISTENERS.set(('alice', frozenset({'alice', 'bob'})))
    try:
        result = diagnostic(gf, mode=mode, scenarios=[{'kind': 'price_changed', 'place_id': 'restaurant_a', 'price_minor': 987654}] if mode == 'resilience' else None)
    finally:
        LISTENERS.reset(token)
    assert result.get('outcome') == 'private_review_required', result
    text = json.dumps(result)
    assert all(value not in text for value in [*private_ids, 'Bob', 'Chen', 'Dana', '987654'])
    assert not any(key in result for key in ('scenarios', 'repairs', 'alternatives', 'feasible', 'conflict', 'private_owners', 'owners'))
    assert business_state(gf) == before


def test_listener_visibility_is_checked_before_private_condition_or_budget_information():
    gf = seeded({PLAN: 'committed'})
    before = business_state(gf)
    counter = gf.db.scalar('SELECT count(*) FROM diagnostic_queries')
    token = LISTENERS.set(('alice', frozenset({'alice', 'linda'})))
    try:
        with pytest.raises(DomainError) as error:
            diagnostic(gf, mode='resilience', scenarios=[{'kind': 'price_changed', 'place_id': 'restaurant_a', 'price_minor': 5000}])
        assert error.value.code in {'FORBIDDEN', 'NOT_FOUND'}
    finally:
        LISTENERS.reset(token)
    assert gf.db.scalar('SELECT count(*) FROM diagnostic_queries') == counter
    assert business_state(gf) == before


def test_room_policy_does_not_coarsen_when_only_the_speaker_has_private_conditions():
    gf = seeded({PLAN: 'committed'})
    # This is a public-condition fixture, not an API allowing the caller to edit
    # someone else's condition. Alice's own private limits remain unchanged.
    gf.db.run("UPDATE conditions SET visibility='group' WHERE owner_id!='alice'")
    token = LISTENERS.set(('alice', frozenset({'alice', 'bob'})))
    try:
        result = diagnostic(gf, mode='fairness')
    finally:
        LISTENERS.reset(token)
    assert result.get('outcome') != 'private_review_required', result


def test_personal_private_review_contains_only_own_relevant_conditions():
    gf = seeded({PLAN: 'committed'})
    before = business_state(gf)
    result = gf.human_private_review(gf.identity.get('bob'), PLAN)
    text = json.dumps(result)
    own = gf.db.all("SELECT id FROM conditions WHERE plan_id=? AND owner_id='bob' AND status='active'", PLAN)
    other = gf.db.all("SELECT id FROM conditions WHERE owner_id!='bob' OR plan_id!=?", PLAN)
    assert own and all(row['id'] in text for row in own)
    assert all(row['id'] not in text for row in other)
    assert business_state(gf) == before


def histories(gf, member=None, group='friends'):
    if member:
        return gf.db.all('SELECT * FROM fairness_history WHERE group_id=? AND member_id=? ORDER BY id', group, member)
    return gf.db.all('SELECT * FROM fairness_history WHERE group_id=? ORDER BY id', group)


def repair_and_commit(gf, place='Restaurant B'):
    gf.propose_change('alice', plan=PLAN, item='dinner', place=place)
    gf.request_confirmations('alice', plan=PLAN)
    for member in FRIENDS:
        approve_all(gf, gf.identity.get(member), PLAN)
    return gf.commit_plan('alice', plan=PLAN)


def test_only_subsequent_committed_repairs_count_once_for_personal_opt_in():
    gf = seeded({PLAN: 'draft'})
    alice, bob = gf.identity.get('alice'), gf.identity.get('bob')
    gf.human_fairness_preference(alice, 'friends', True)
    gf.request_confirmations('alice', plan=PLAN)
    for member in FRIENDS:
        approve_all(gf, gf.identity.get(member), PLAN)
    assert gf.commit_plan('alice', plan=PLAN).outcome == 'committed'
    assert histories(gf) == [], 'The initial plan establishes the baseline, not a historical accommodation.'
    plan = gf._plan_row(PLAN)
    before = gf._latest_snapshot(plan)
    problem = gf._problem(plan, before)
    gf.propose_change('alice', plan=PLAN, item='dinner', place='Restaurant B')
    assert histories(gf) == [], 'Proposals must not spend anyone\'s accommodation history.'
    assert gf.commit_plan('alice', plan=PLAN).outcome == 'blocked'
    assert histories(gf) == []
    gf.human_fairness_preference(bob, 'friends', True)
    gf.request_confirmations('alice', plan=PLAN)
    for member in FRIENDS:
        approve_all(gf, gf.identity.get(member), PLAN)
    assert gf.commit_plan('alice', plan=PLAN).outcome == 'committed'
    after = gf._latest_snapshot(gf._plan_row(PLAN))
    truth = burden(problem, before, after)
    rows = histories(gf)
    assert {row['member_id'] for row in rows} == {'alice', 'bob'}
    assert {row['member_id']: row['burden'] for row in rows} == {member: truth[member] for member in ('alice', 'bob')}
    assert gf.commit_plan('alice', plan=PLAN).outcome == 'already_committed'
    assert histories(gf) == rows


def test_fairness_opt_out_reset_and_group_ownership_are_enforced():
    gf = seeded({PLAN: 'committed'})
    alice, bob, linda = [gf.identity.get(member) for member in ('alice', 'bob', 'linda')]
    gf.human_fairness_preference(alice, 'friends', True)
    gf.human_fairness_preference(bob, 'friends', True)
    assert repair_and_commit(gf).outcome == 'committed'
    bob_rows = histories(gf, 'bob')
    assert bob_rows and histories(gf, 'alice')
    gf.human_fairness_preference(alice, 'friends', False)
    assert histories(gf, 'alice') == [] and histories(gf, 'bob') == bob_rows
    gf.human_fairness_preference(bob, 'friends', True, reset=True)
    assert histories(gf, 'bob') == []
    before = business_state(gf)
    with pytest.raises(DomainError):
        gf.human_fairness_preference(linda, 'friends', True)
    with pytest.raises((DomainError, TypeError)):
        gf.human_fairness_preference(alice, 'friends', True, member_id='bob')
    assert business_state(gf) == before


def test_fairness_without_current_alternatives_remains_read_only_and_creates_no_consent():
    gf = seeded({PLAN: 'committed'})
    before = business_state(gf)
    result = diagnostic(gf, mode='fairness')
    assert result.get('outcome') in {'search_required', 'no_candidates', 'no_alternatives'}, result
    assert not result.get('alternatives')
    assert business_state(gf) == before


def test_fairness_preference_and_history_persist_and_reset_stays_removed(tmp_path):
    path = str(tmp_path / 'analysis-privacy-history.sqlite3')
    gf = GroupFlow(Db(path))
    seed_world(gf, WORLD, {PLAN: 'committed'})
    for member in ('alice', 'bob'):
        gf.human_fairness_preference(gf.identity.get(member), 'friends', True)
    assert repair_and_commit(gf).outcome == 'committed'
    expected = histories(gf)
    assert len(expected) == 2
    gf.db.close()
    reopened = GroupFlow(Db(path))
    try:
        assert histories(reopened) == expected
        assert reopened.db.scalar("SELECT enabled FROM fairness_preferences WHERE group_id='friends' AND member_id='alice'") == 1
        reopened.human_fairness_preference(reopened.identity.get('alice'), 'friends', False)
        reopened.human_fairness_preference(reopened.identity.get('bob'), 'friends', True, reset=True)
    finally:
        reopened.db.close()
    final = GroupFlow(Db(path))
    try:
        assert histories(final) == []
        assert final.db.scalar("SELECT enabled FROM fairness_preferences WHERE group_id='friends' AND member_id='alice'") == 0
    finally:
        final.db.close()


def _commit_event(gf, plan, members):
    gf.request_confirmations('alice', plan=plan)
    for member in members:
        approve_all(gf, gf.identity.get(member), plan)
    result = gf.commit_plan('alice', plan=plan)
    assert result.outcome == 'committed', result


def _dinner_id(gf, plan):
    return next(activity.id for activity in gf._latest_snapshot(gf._plan_row(plan)).activities if activity.title == 'Dinner')


def _comparison_payload(value):
    """Discard only query-accounting and volatile stamps, never burden data."""
    volatile = {'runtime_ms', 'budget_remaining', 'remaining_queries', 'diagnostic_budget', 'query_budget',
                'version_token', 'expected', 'generated_at'}
    if isinstance(value, dict):
        return {key: _comparison_payload(item) for key, item in value.items() if key not in volatile}
    if isinstance(value, list):
        return [_comparison_payload(item) for item in value]
    return value


def test_hidden_and_cross_group_history_have_no_effect_on_fairness_ranking_or_reports():
    gf = seeded({PLAN: 'degraded'})
    for member in ('alice', 'bob'):
        gf.human_fairness_preference(gf.identity.get(member), 'friends', True)
    # An authentic, committed source plan visible to Alice alone. Bob cannot
    # learn even the existence of this plan through accommodation diagnostics.
    hidden = gf.create_plan('alice', 'evening_out', 'PRIVATE_HISTORY_SENTINEL_941', group='friends', audience=['alice']).plan.id
    _commit_event(gf, hidden, ('alice',))
    foreign = gf.create_plan('alice', 'evening_out', 'FOREIGN_GROUP_HISTORY_SENTINEL_927', group='family', audience=['alice', 'bob']).plan.id
    gf.human_fairness_preference(gf.identity.get('bob'), 'family', True)
    _commit_event(gf, foreign, ('alice', 'bob'))
    options = gf.planning_options('alice', PLAN)
    assert options['alternatives']
    before = _comparison_payload(diagnostic(gf, mode='fairness', actor='bob'))
    gf.propose_change('alice', plan=hidden, item=_dinner_id(gf, hidden), place='Restaurant B')
    _commit_event(gf, hidden, ('alice',))
    assert histories(gf, 'alice') and any(row['plan_id'] == hidden for row in histories(gf, 'alice'))
    gf.db.run('UPDATE fairness_history SET burden=999999 WHERE plan_id=?', hidden)
    after_hidden = _comparison_payload(diagnostic(gf, mode='fairness', actor='bob'))
    assert after_hidden == before
    gf.propose_change('alice', plan=foreign, item=_dinner_id(gf, foreign), place='Restaurant B')
    _commit_event(gf, foreign, ('alice', 'bob'))
    assert histories(gf, 'bob', group='family')
    gf.db.run('UPDATE fairness_history SET burden=888888 WHERE plan_id=?', foreign)
    after_foreign = _comparison_payload(diagnostic(gf, mode='fairness', actor='bob'))
    assert after_foreign == before
    text = json.dumps(after_foreign)
    assert all(secret not in text for secret in (hidden, foreign, 'PRIVATE_HISTORY_SENTINEL_941', 'FOREIGN_GROUP_HISTORY_SENTINEL_927', '999999', '888888'))


def test_fairness_history_is_filtered_for_every_room_listener_not_only_the_speaker():
    gf = seeded({PLAN: 'degraded'})
    gf.db.run("UPDATE conditions SET visibility='group'")
    gf.human_fairness_preference(gf.identity.get('alice'), 'friends', True)
    hidden = gf.create_plan('alice', 'evening_out', 'ROOM_HIDDEN_HISTORY_779', group='friends', audience=['alice']).plan.id
    _commit_event(gf, hidden, ('alice',))
    assert gf.planning_options('alice', PLAN)['alternatives']
    token = LISTENERS.set(('alice', frozenset({'alice', 'bob'})))
    try:
        before = _comparison_payload(diagnostic(gf, mode='fairness'))
    finally:
        LISTENERS.reset(token)
    gf.propose_change('alice', plan=hidden, item=_dinner_id(gf, hidden), place='Restaurant B')
    _commit_event(gf, hidden, ('alice',))
    gf.db.run('UPDATE fairness_history SET burden=777777 WHERE plan_id=?', hidden)
    token = LISTENERS.set(('alice', frozenset({'alice', 'bob'})))
    try:
        after = _comparison_payload(diagnostic(gf, mode='fairness'))
    finally:
        LISTENERS.reset(token)
    assert before == after
    assert hidden not in json.dumps(after) and '777777' not in json.dumps(after)


def test_fairness_alternatives_are_independently_feasible_and_selection_creates_no_consent():
    gf = seeded({PLAN: 'degraded'})
    for member in FRIENDS:
        gf.human_fairness_preference(gf.identity.get(member), 'friends', True)
    assert gf.planning_options('alice', PLAN)['alternatives']
    plan = gf._plan_row(PLAN)
    base = gf._snapshot(gf._revision(plan['active_revision_id']))
    problem = gf._problem(plan, base)
    before = business_state(gf)
    result = diagnostic(gf, mode='fairness')
    choices = result.get('alternatives', [])
    assert choices, result
    previous = None
    for choice in choices:
        revision = gf._revision(choice['candidate'])
        assert revision['plan_id'] == PLAN and revision['kind'] == 'candidate'
        candidate = gf._snapshot(revision)
        assert not violations(problem, candidate)
        current = burden(problem, base, candidate)
        # This particular fixture has no prior committed repair history, so
        # the historical maximum is exactly today's maximum burden.
        rank = (max(current.values(), default=0), score(problem, candidate).objective)
        assert previous is None or previous <= rank
        previous = rank
    assert business_state(gf) == before
    approvals = gf.db.all('SELECT * FROM approval_receipts ORDER BY id')
    assert gf.planning_options('alice', PLAN, choices[0]['candidate'])['outcome'] == 'selected'
    assert gf.db.all('SELECT * FROM approval_receipts ORDER BY id') == approvals
    assert gf.commit_plan('alice', plan=PLAN).outcome == 'blocked'


def test_fairness_never_ranks_an_existing_infeasible_or_stale_alternative():
    gf = seeded({PLAN: 'degraded'})
    gf.human_fairness_preference(gf.identity.get('bob'), 'friends', True)
    assert gf.planning_options('alice', PLAN)['alternatives']
    plan = gf._plan_row(PLAN)
    candidate = gf._revision(plan['candidate_revision_id'])
    snapshot = gf._snapshot(candidate)
    impossible = snapshot.with_activity(replace(snapshot.activity('dinner'), end=22 * 60))
    # Synthetic corrupt/stale option inside the current job: the comparison
    # must validate stored alternatives rather than trusting their labels.
    invalid = gf._new_revision(plan, impossible, 'Invalid acceptance fixture', 'candidate', 'repair', 'alice',
                               plan['active_revision_id'], job_id=candidate['repair_job_id'], alternate=True)
    registered = gf.db.get('SELECT * FROM repair_candidates WHERE job_id=? ORDER BY rank LIMIT 1', candidate['repair_job_id'])
    assert registered
    registered.update(id=gf.new_id('accept_bad_candidate'), revision_id=invalid['id'], rank=-1)
    columns = list(registered)
    gf.db.run('INSERT INTO repair_candidates(' + ','.join(columns) + ') VALUES(' + ','.join('?' for _ in columns) + ')',
              *(registered[column] for column in columns))
    before = business_state(gf)
    result = diagnostic(gf, mode='fairness')
    assert invalid['id'] not in {choice['candidate'] for choice in result.get('alternatives', [])}
    assert business_state(gf) == before
    gf.human_set_condition(gf.identity.get('bob'), 'budget', {'amount': 40}, PLAN)
    before = business_state(gf)
    stale = caught(lambda: diagnostic(gf, mode='fairness'))
    assert not stale.get('alternatives'), stale
    assert business_state(gf) == before


def test_consented_visible_history_can_change_the_recommended_feasible_option():
    gf = seeded({PLAN: 'committed'})
    gf.human_fairness_preference(gf.identity.get('bob'), 'friends', True)
    history_plan = gf.create_plan('alice', 'evening_out', 'Visible previous accommodation', group='friends', audience=['alice', 'bob']).plan.id
    _commit_event(gf, history_plan, ('alice', 'bob'))
    gf.propose_change('alice', plan=history_plan, item=_dinner_id(gf, history_plan), place='Restaurant B')
    _commit_event(gf, history_plan, ('alice', 'bob'))
    assert sum(row['burden'] for row in histories(gf, 'bob')) == 10
    for member in ('alice', 'chen'):
        gf.human_fairness_preference(gf.identity.get(member), 'friends', True)
    target = gf.create_plan('alice', 'evening_out', 'Different people bear different repairs', group='friends',
                            audience=['alice', 'bob', 'chen'], starter=False).plan.id
    catalog = {'version': 'independent-fairness-1', 'default_travel': 0,
               'travel_minutes': [['centre', 'centre', 0], ['far', 'far', 0], ['centre', 'far', 30], ['far', 'centre', 30]],
               'places': [
                   {'id': 'accept-meal-a', 'name': 'Original meal', 'kind': 'meal', 'zone': 'centre', 'open': '08:00', 'close': '23:00', 'capacity': 8, 'price': 12, 'tags': []},
                   {'id': 'accept-meal-b-early', 'name': 'Earlier further meal', 'kind': 'meal', 'zone': 'far', 'open': '08:00', 'close': '23:00', 'capacity': 8, 'price': 12, 'tags': [], 'start_slots': ['17:45']},
                   {'id': 'accept-meal-b', 'name': 'Further meal', 'kind': 'meal', 'zone': 'far', 'open': '08:00', 'close': '23:00', 'capacity': 8, 'price': 12, 'tags': [], 'start_slots': ['18:00']},
                   {'id': 'accept-dessert-a', 'name': 'Original dessert', 'kind': 'dessert', 'zone': 'centre', 'open': '08:00', 'close': '23:00', 'capacity': 8, 'price': 12, 'tags': []},
                   {'id': 'accept-dessert-b', 'name': 'Further dessert', 'kind': 'dessert', 'zone': 'far', 'open': '08:00', 'close': '23:00', 'capacity': 8, 'price': 12, 'tags': []}]}
    gf.db.run('UPDATE plans SET catalog_json=? WHERE id=?', json.dumps(catalog), target)
    gf.propose_change('alice', plan=target, new_type='stop', title='Meal', start='18:00', end='19:00', place='accept-meal-a', participants=['alice', 'bob'])
    gf.propose_change('alice', plan=target, new_type='stop', title='Dessert', start='19:15', end='19:45', place='accept-dessert-a', participants=['alice', 'chen'])
    _commit_event(gf, target, ('alice', 'bob', 'chen'))
    gf.inject_observation('friends', 'place_unavailable', {'place_id': 'accept-meal-a', 'from': '18:00', 'to': '19:00'},
                          'independent-acceptance', 'fairness-tradeoff')
    assert gf.planning_options('alice', target)['alternatives']
    plan = gf._plan_row(target)
    base = gf._snapshot(gf._revision(plan['active_revision_id']))
    problem = gf._problem(plan, base)
    before = business_state(gf)
    result = diagnostic(gf, mode='fairness', plan=target)
    assert result['history'] == {'alice': 0, 'bob': 10, 'chen': 0}
    assert result['fairer'] != result['least_change'], result
    independently_scored = {}
    for choice in result['alternatives']:
        candidate = gf._snapshot(gf._revision(choice['candidate']))
        assert not violations(problem, candidate)
        current = burden(problem, base, candidate)
        totals = {member: current[member] + result['history'][member] for member in ('alice', 'bob', 'chen')}
        assert choice['current_burden'] == current
        assert choice['totals'] == totals
        assert choice['maximum_total'] == max(totals.values())
        independently_scored[choice['candidate']] = (max(totals.values()), score(problem, candidate).objective, choice['rank'])
    assert result['fairer'] == min(independently_scored, key=independently_scored.get)
    assert independently_scored[result['fairer']][0] < independently_scored[result['least_change']][0]
    assert business_state(gf) == before


def test_demo_group_reset_removes_coordination_history_and_preserves_other_groups():
    """Additional compatibility case after a synthetic probe found FK failure."""
    gf = seeded({PLAN: 'committed'})
    alice = gf.identity.get('alice')
    gf.human_fairness_preference(alice, 'friends', True)
    gf.human_fairness_preference(alice, 'family', True)
    family_preference = gf.db.all("SELECT * FROM fairness_preferences WHERE group_id='family'")
    family_plans = gf.db.all("SELECT * FROM plans WHERE group_id='family' ORDER BY id")
    member_count = gf.db.scalar('SELECT count(*) FROM members')
    assert repair_and_commit(gf).outcome == 'committed' and histories(gf)
    wipe_group(gf.db, 'friends')
    assert histories(gf) == []
    assert not gf.db.all("SELECT * FROM fairness_preferences WHERE group_id='friends'")
    assert gf.db.all("SELECT * FROM fairness_preferences WHERE group_id='family'") == family_preference
    assert gf.db.all("SELECT * FROM plans WHERE group_id='family' ORDER BY id") == family_plans
    assert gf.db.scalar('SELECT count(*) FROM members') == member_count
    seed_group(gf, WORLD, 'friends', {})
    assert gf._plan_row(PLAN) and histories(gf) == []
