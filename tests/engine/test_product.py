"""State and authorization regressions for the product layer."""
import json
import pytest

from groupflow.engine import LISTENERS
from groupflow.engine.calendar import fold, utc_local
from groupflow.world import approve_all
from tests.conftest import code, seeded


def apply(gf, tool, args, key='one', actor='alice', version=None):
    return gf.apply_action(actor, tool, args, key, version or gf.state_token(args['plan']))


def test_retry_returns_one_receipt_and_one_expense(gf):
    args = {'action': 'add', 'plan': 'party', 'amount': '19.23', 'what': 'table supplies'}
    token = gf.state_token('party')
    first = apply(gf, 'costs', args, version=token)
    second = apply(gf, 'costs', args, version=token)
    assert first['action_receipt']['id'] == second['action_receipt']['id']
    assert second['replayed']
    assert gf.db.scalar("SELECT count(*) FROM expenses WHERE description='table supplies'") == 1
    assert code(lambda: apply(gf, 'costs', {**args, 'amount': '18.00'}, version=token)) == 'KEY_REUSED'


def test_state_token_covers_ledger_and_requires_refresh(gf):
    before = gf.state_token('party')
    gf.costs('alice', 'add', plan='party', amount='20', what='supplies')
    assert before != gf.state_token('party')
    assert code(lambda: apply(gf, 'bring_list', {'plan':'party','action':'bring','things':['cups']}, version=before)) == 'STALE'
    assert code(lambda: gf.apply_action('alice','bring_list',{'plan':'party','action':'bring','things':['cups']},'new')) == 'EXPECTED_VERSION_REQUIRED'
    assert not gf.db.scalar("SELECT count(*) FROM bring_items WHERE plan_id='party' AND label='cups'")


def test_bounded_correction_releases_only_own_claim_and_does_not_repeat(gf):
    r = apply(gf, 'bring_list', {'plan':'party','action':'bring','things':['cups']})['action_receipt']
    assert r['reversible']
    assert code(lambda: gf.correct_action('bob', r['id'])) == 'NOT_FOUND'
    corrected = gf.correct_action('alice', r['id'])
    assert corrected['action_receipt']['id']
    assert gf.db.scalar("SELECT claimed_by FROM bring_items WHERE label='cups'") is None
    assert gf.correct_action('alice', r['id'])['outcome'] == 'already_corrected'


def test_unrelated_later_work_preserved_during_correction(gf):
    r = apply(gf, 'bring_list', {'plan':'party','action':'bring','things':['cups']})['action_receipt']
    gf.bring_list('bob','bring',['napkins'],plan='party')
    gf.correct_action('alice',r['id'])
    assert gf.db.scalar("SELECT claimed_by FROM bring_items WHERE label='cups'") is None
    assert gf.db.scalar("SELECT claimed_by FROM bring_items WHERE label='napkins'") == 'bob'


def test_own_expense_correction_and_scope_bound_replay(gf):
    args = {'action':'add','plan':'party','amount':'12','what':'decorations'}
    r = apply(gf,'costs',args)['action_receipt']
    token = LISTENERS.set(('alice',frozenset({'alice','linda'})))
    try:
        assert gf.action_history('alice')['receipts'] == []
        assert code(lambda: gf.apply_action('alice','costs',args,'one')) == 'FORBIDDEN'
    finally:
        LISTENERS.reset(token)
    gf.correct_action('alice',r['id'])
    assert gf.db.scalar("SELECT status FROM expenses WHERE description='decorations'") == 'removed'


def test_resolution_and_target_guard_use_visible_exact_labels(gf):
    r = gf.resolve_action('bob', "Possibly the venue could be Luigi's instead.")
    assert r['targets'][0]['item'] == 'party.venue'
    assert gf.resolve_action('linda', 'Venue')['targets'] == []
    assert code(lambda: gf.propose_change('bob',item='dinner',plan='friday_dinner',place="Luigi's",quote='Maybe the venue could be Luigi\'s')) == 'TARGET_MISMATCH'
    assert code(lambda: gf.poll('alice','start',plan='friday_dinner',options=['7:30'],closes='Friday',question='Dinner time',quote='Dinner at 7:30 unless anyone objects')) == 'CONSENT_REQUIRED'


def test_current_inbox_deduplicates_and_keeps_decisions_when_muted(gf):
    gf.propose_change('charlie',item='party.date',value='Sunday, Oct 25, 6:00 pm')
    p = gf._plan_row('party')
    gf._notify(['alice'],p,'notice','same update')
    gf._notify(['alice'],p,'notice','same update')
    cards = gf.decision_inbox('alice')['cards']
    party = next(c for c in cards if c['plan']=='party')
    assert party['updates'].count('same update') == 1
    gf.delivery_preferences('alice','party',muted=True,digest=True)
    party = next(c for c in gf.decision_inbox('alice')['cards'] if c['plan']=='party')
    assert any(a['kind']=='request' for a in party['actions'])
    assert 'same update' not in party['updates']
    assert all(c['plan']!='party' for c in gf.decision_inbox('linda')['cards'])


def test_quiet_hours_defer_without_marking_heard(gf):
    from groupflow.engine import fixed_clock
    gf.set_clock(fixed_clock('2026-10-18T03:00:00Z'))
    gf.delivery_preferences('alice','party',quiet_start=22,quiet_end=8)
    assert not gf.notification_available('alice','party','reminder')
    assert gf.notification_available('alice','party','confirm')
    gf.set_clock(fixed_clock('2026-10-18T13:00:00Z'))
    assert gf.notification_available('alice','party','reminder')


def token(link):
    return link['url'].split('/')[-1][:-4]


def test_calendar_stable_identity_revision_and_revocation():
    gf = seeded({'friday_dinner':'committed'})
    link = gf.issue_calendar('alice')
    first = gf.calendar_feed(token(link))
    assert first.count('BEGIN:VEVENT') == 4 and 'Restaurant A' in first
    assert gf.calendar_feed(token(link)) == first
    gf.propose_change('alice',item='dinner',plan='friday_dinner',place='Restaurant B')
    assert gf.calendar_feed(token(link)) == first  # draft changes are not published
    gf.request_confirmations('alice',plan='friday_dinner')
    for who in ('alice','bob','chen','dana'):
        approve_all(gf,gf.identity.get(who),'friday_dinner')
    gf.commit_plan('alice',plan='friday_dinner')
    repaired = gf.calendar_feed(token(link))
    assert [l for l in first.splitlines() if l.startswith('UID:')] == [l for l in repaired.splitlines() if l.startswith('UID:')]
    assert repaired.count('SEQUENCE:1') == 1 and 'Restaurant B' in repaired
    gf.revoke_calendar('alice',link['id'])
    assert code(lambda: gf.calendar_feed(token(link))) == 'NOT_FOUND'


def test_calendar_cancellation_drops_titles_after_visibility_loss():
    gf = seeded({'friday_dinner':'committed'})
    link = gf.issue_calendar('bob')
    gf.calendar_feed(token(link))
    gf.leave_group(gf.identity.get('bob'),'friends')
    body = gf.calendar_feed(token(link))
    assert body.count('STATUS:CANCELLED') == 4 and 'SUMMARY:' not in body and 'Restaurant' not in body


def test_calendar_dst_and_utf8_folding():
    assert utc_local('2026-10-31',540,'America/Toronto').endswith('130000Z')
    assert utc_local('2026-11-01',540,'America/Toronto').endswith('140000Z')
    assert code(lambda: utc_local('2026-03-08',150,'America/Toronto')) == 'INVALID_EVENT_TIME'
    assert code(lambda: utc_local('2026-11-01',90,'America/Toronto')) == 'INVALID_EVENT_TIME'
    from groupflow.engine.text import parse_time_of_day
    assert parse_time_of_day('01:30') == 90 and parse_time_of_day('02:30') == 150
    assert parse_time_of_day('7:30') == 1170 and parse_time_of_day('19:30') == 1170
    assert all(len(line.encode('utf8')) <= 75 for line in fold('SUMMARY:'+'聚会'*100).split('\r\n'))


def test_feasible_choices_never_create_consent_and_reject_changed_inputs():
    gf = seeded({'friday_dinner':'degraded'})
    result = gf.planning_options('alice','friday_dinner')
    choice = result['alternatives'][0]['candidate']
    assert code(lambda: gf.planning_options('bob','friday_dinner',choice)) == 'FORBIDDEN'
    count = gf.db.scalar('SELECT count(*) FROM approval_receipts')
    assert gf.planning_options('alice','friday_dinner',choice)['outcome']=='selected'
    assert gf.db.scalar('SELECT count(*) FROM approval_receipts') == count
    assert gf.commit_plan('alice',plan='friday_dinner').outcome=='blocked'
    gf.human_set_condition(gf.identity.get('dana'),'budget',{'amount':'40'},'friday_dinner')
    assert code(lambda: gf.planning_options('alice','friday_dinner',choice)) in {'STALE','NOT_FOUND','INFEASIBLE'}


def test_duplicate_plan_titles_require_exact_visible_id(gf):
    first = gf.create_plan('alice','general_event','Moonrise Supper',group='friends',starter=False).plan.id
    second = gf.create_plan('alice','general_event','Moonrise Supper',group='friends',starter=False).plan.id
    args = {'plan':'Moonrise Supper','new_type':'task','title':'Response deadline','due':'Friday 17:00'}
    assert code(lambda: gf.propose_change('alice',**args)) == 'AMBIGUOUS_PLAN'
    assert not gf.db.scalar("SELECT count(*) FROM items WHERE label='Response deadline'")
    found = gf.resolve_action('alice','Moonrise Supper')
    assert found['outcome']=='ambiguous' and {t['plan'] for t in found['targets']}=={first,second}
    assert gf.resolve_action('linda','Moonrise Supper')['targets']==[]
    assert gf.propose_change('alice',**{**args,'plan':second}).outcome=='created'


def test_guarded_create_uses_public_tool_schema_and_retry_is_one_plan(gf):
    args = {'template':'general_event','title':'Boundary picnic','group':'friends','start_date':'2026-11-14','starter':False}
    first = gf.apply_action('alice','create_plan',args,'create-picnic')
    again = gf.apply_action('alice','create_plan',args,'create-picnic')
    assert first['plan']['id']==again['plan']['id'] and again['replayed']
    assert gf.db.scalar("SELECT count(*) FROM plans WHERE title='Boundary picnic'")==1
    assert code(lambda: gf.apply_action('alice','request_confirmations',{},'implicit-plan'))=='INVALID_ARGUMENT'


def test_feasible_first_draft_offers_selectable_current_arrangement(gf):
    plan = gf.create_plan('alice','evening_out','Copper Kettle Evening',group='friends',start_date='2026-11-14').plan.id
    options = gf.planning_options('alice',plan)
    assert options['alternatives']
    assert set(options['alternatives'][0]['reconfirm'])=={'Alice','Bob','Chen','Dana'}
    candidate = options['alternatives'][0]['candidate']
    assert gf.planning_options('alice',plan,candidate)['outcome']=='selected'
    assert gf.commit_plan('alice',plan=plan).outcome=='blocked'
    gf.request_confirmations('alice',plan=plan)
    for who in ('alice','bob','chen','dana'):
        approve_all(gf,gf.identity.get(who),plan)
    assert gf.commit_plan('alice',plan=plan).outcome=='committed'


def test_calendar_cancels_only_own_withdrawn_activity():
    gf = seeded({'friday_dinner':'committed'})
    link = gf.issue_calendar('bob')
    first = gf.calendar_feed(token(link))
    answer = gf.db.get("SELECT r.id FROM approval_receipts r JOIN approval_scopes s ON s.id=r.scope_id WHERE r.member_id='bob' AND r.plan_id='friday_dinner' AND s.activity_id='dinner'")
    gf.human_revoke(gf.identity.get('bob'),answer['id'])
    changed = gf.calendar_feed(token(link))
    assert changed.count('STATUS:CANCELLED')==1 and changed.count('STATUS:CONFIRMED')==3
    assert [x for x in first.splitlines() if x.startswith('UID:')]==[x for x in changed.splitlines() if x.startswith('UID:')]


def test_noop_retry_cannot_reveal_a_plan_after_membership_loss(gf):
    args = {'plan':'party','action':'show'}
    first = gf.apply_action('alice','costs',args,'read-costs',gf.state_token('party'))
    assert first['action_receipt']['plans']==['party']
    gf.leave_group(gf.identity.get('alice'),'family')
    assert code(lambda: gf.apply_action('alice','costs',args,'read-costs'))=='FORBIDDEN'


def test_demo_group_reset_drops_old_receipts_and_preferences(gf):
    from groupflow.world import wipe_group, load_world, seed_group
    apply(gf,'bring_list',{'plan':'party','action':'bring','things':['ribbon']},'old-action')
    gf.delivery_preferences('alice','party',muted=True)
    wipe_group(gf.db,'family')
    seed_group(gf,load_world(),'family',{})
    assert not gf.db.scalar("SELECT count(*) FROM action_receipts WHERE request_key='old-action'")
    assert not gf.db.scalar("SELECT count(*) FROM delivery_preferences WHERE plan_id='party'")
