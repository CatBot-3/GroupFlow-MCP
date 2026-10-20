"""Follow-up regressions for the personal review gaps found in the browser.

Main-agent tests, separate from the frozen independent acceptance suites.
"""
from __future__ import annotations

import hashlib
import pytest
from groupflow.core.db import Db, _SCHEMAS
from groupflow.core.errors import DomainError
from groupflow.engine import fixed_clock
from tests.engine.test_agreement_fallback import make_lab, requirements, state, PLAN


@pytest.fixture
def lab():
    fixture = make_lab()
    try:
        yield fixture
    finally:
        fixture.gf.db.close()


def contest(lab, who='amir'):
    gf = lab.gf
    member = gf.identity.get(who)
    gf.human_contest(member, PLAN, lab.first, 'A private review request', gf.state_token(PLAN))
    review = gf.human_agreements(member, PLAN)
    item = next(x for x in review['items'] if x['activity_id'] == lab.first)
    assert item['can_answer'] and item['confirmation_text'] and item['scope_id']
    return member, item['scope_id']


@pytest.mark.parametrize('candidate', [False, True])
def test_person_can_reaffirm_own_contest_without_other_answers_or_new_booking(lab, candidate):
    gf = lab.gf
    if candidate:
        gf.propose_change('nora', plan=PLAN, item=lab.third, start='17:15', end='17:30')
        gf.request_confirmations('nora', plan=PLAN)
    before = requirements(gf)
    member, scope = contest(lab)
    counts = {t: gf.db.scalar(f'SELECT count(*) FROM {t}') for t in ('plan_revisions', 'approval_scopes', 'repair_jobs', 'executions')}
    other = gf.db.all("SELECT * FROM approval_receipts WHERE member_id!='amir' ORDER BY rowid")
    ch = gf.human_challenge(member, scope)
    result = gf.human_decide(member, ch['challenge_id'], ch['nonce'], 'approved')
    assert result['outcome'] == 'approved'
    after = requirements(gf)
    assert after[('amir', lab.first)] == 'approved'
    assert {k: v for k, v in before.items() if k != ('amir', lab.first)} == {k: v for k, v in after.items() if k != ('amir', lab.first)}
    assert other == gf.db.all("SELECT * FROM approval_receipts WHERE member_id!='amir' ORDER BY rowid")
    assert counts == {t: gf.db.scalar(f'SELECT count(*) FROM {t}') for t in counts}
    if not candidate:
        assert gf._plan_row(PLAN)['status'] == 'COMMITTED'
    before_replay = state(gf)
    with pytest.raises(DomainError):
        gf.human_decide(member, ch['challenge_id'], ch['nonce'], 'approved')
    assert state(gf) == before_replay


@pytest.mark.parametrize('failure', ['nonce', 'expiry', 'changed_plan', 'hard_limit'])
def test_personal_reanswer_rejects_invalid_challenge_atomically(lab, failure):
    gf = lab.gf
    member, scope = contest(lab)
    ch = gf.human_challenge(member, scope)
    nonce = ch['nonce']
    if failure == 'nonce':
        nonce = 'wrong'
    elif failure == 'expiry':
        gf.set_clock(fixed_clock('2026-10-19T12:11:00Z'))
    elif failure == 'changed_plan':
        gf.propose_change('nora', plan=PLAN, item=lab.first, place='birch_b')
    else:
        gf.human_set_condition(member, 'budget', {'amount': 1}, PLAN)
    before = state(gf)
    with pytest.raises(DomainError):
        gf.human_decide(member, ch['challenge_id'], nonce, 'approved')
    assert state(gf) == before


def test_reanswer_decline_then_approve_uses_receipt_order_despite_clock_rewind(lab):
    gf = lab.gf
    member, scope = contest(lab)
    ch = gf.human_challenge(member, scope)
    gf.human_decide(member, ch['challenge_id'], ch['nonce'], 'rejected')
    assert requirements(gf)[('amir', lab.first)] == 'rejected'
    gf.set_clock(fixed_clock('2026-10-19T11:00:00Z'))
    ch = gf.human_challenge(member, scope)
    gf.human_decide(member, ch['challenge_id'], ch['nonce'], 'approved')
    assert requirements(gf)[('amir', lab.first)] == 'approved'
    assert gf._plan_row(PLAN)['status'] == 'COMMITTED'


def test_whole_plan_personal_reanswer_preserves_other_people():
    fixture = make_lab(whole_member=True)
    try:
        before = requirements(fixture.gf)
        member, scope = contest(fixture)
        ch = fixture.gf.human_challenge(member, scope)
        fixture.gf.human_decide(member, ch['challenge_id'], ch['nonce'], 'approved')
        assert requirements(fixture.gf) == before
    finally:
        fixture.gf.db.close()


def test_coordination_additive_migration_preserves_existing_approved_plans(tmp_path):
    path = str(tmp_path / 'synthetic-previous-version.db')
    fixture = make_lab(Db(path))
    db = fixture.gf.db
    names = ('plans', 'items', 'members', 'approval_scopes', 'approval_receipts', 'plan_revisions')
    before = {t: db.all(f'SELECT * FROM {t} ORDER BY rowid') for t in names}
    for table in ('diagnostic_queries', 'fairness_history', 'fairness_preferences', 'weather_readings', 'weather_watches',
                  'fallback_uses', 'fallback_authorizations', 'fallback_challenges', 'agreement_contests'):
        db.run(f'DROP TABLE {table}')
    sql = '\n'.join(s for name, s in _SCHEMAS if name != 'coordination_v1')
    db.run("UPDATE gf_meta SET value=? WHERE key='schema'", hashlib.sha256(sql.encode()).hexdigest()[:16])
    db.close()
    upgraded = Db(path)
    try:
        assert {t: upgraded.all(f'SELECT * FROM {t} ORDER BY rowid') for t in names} == before
        assert upgraded.all('PRAGMA foreign_key_check') == []
        assert upgraded.scalar('SELECT count(*) FROM fairness_history') == 0
    finally:
        upgraded.close()


@pytest.mark.parametrize('price_minor', [1200, 4000])
def test_old_price_scope_cannot_be_reaffirmed_after_verified_price_change(lab, price_minor):
    gf = lab.gf
    member, scope = contest(lab)
    ch = gf.human_challenge(member, scope)
    gf.inject_observation('birch-club', 'price_changed', {'place_id': 'birch_a', 'price_minor': price_minor},
                          'synthetic-price-source', 'active-price-change', verified=True)
    item = next(x for x in gf.human_agreements(member, PLAN)['items'] if x['activity_id'] == lab.first)
    assert not item['can_answer']
    before = state(gf)
    with pytest.raises(DomainError):
        gf.human_decide(member, ch['challenge_id'], ch['nonce'], 'approved')
    assert state(gf) == before
    assert gf._plan_row(PLAN)['status'] == 'DEGRADED'


def test_unused_venue_price_does_not_block_same_terms_personal_reanswer(lab):
    gf = lab.gf
    member, scope = contest(lab)
    gf.inject_observation('birch-club', 'price_changed', {'place_id': 'birch_d', 'price_minor': 1500},
                          'synthetic-price-source', 'unused-venue-price-change', verified=True)
    ch = gf.human_challenge(member, scope)
    gf.human_decide(member, ch['challenge_id'], ch['nonce'], 'approved')
    assert gf._plan_row(PLAN)['status'] == 'COMMITTED'
