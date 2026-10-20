"""No provider ownership: optional host guidance and legacy-only retirement."""
from __future__ import annotations

import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from groupflow.core.db import Db
from groupflow.engine import GroupFlow, fixed_clock
from groupflow.engine.worker import Worker
from groupflow.host_guidance import WEATHER_POLICY, OPTIONAL_WEATHER_GUIDANCE, LOCAL_HOST_CAPABILITIES
from groupflow.mcp_server.tools import INSTRUCTIONS, NAMES
from groupflow.sim.agent import system_prompt
from groupflow.world import approve_all
from tests.engine.test_agreement_fallback import make_lab, grant, use_branch, PLAN, GROUP, LATER


def legacy_watch(gf, plan=PLAN, activity=None):
    """Build the old persisted representation without restoring a provider."""
    p = gf._plan_row(plan)
    snap = gf._latest_snapshot(p)
    a = snap.activity(activity) if activity else snap.live[0]
    key, reading = 'retired-' + plan, 'retired-reading-' + plan
    values = {'id': key, 'plan_id': plan, 'activity_id': a.id, 'organizer_id': p['organizer_id'],
              'latitude': 43.65, 'longitude': -79.38, 'threshold': 60, 'apply_rule': 1, 'enabled': 1,
              'place_id': a.place_id, 'forecast_date': (date.fromisoformat(snap.start_date) + timedelta(days=a.day)).isoformat(),
              'config_digest': 'legacy-configuration', 'reading_id': reading, 'last_attempt': '2026-10-18T12:00:00Z',
              'next_refresh': '2026-10-18T12:15:00Z', 'error': None, 'created_at': '2026-10-18T12:00:00Z'}
    gf.db.run('INSERT INTO weather_watches(' + ','.join(values) + ') VALUES(' + ','.join('?' for _ in values) + ')', *values.values())
    gf.db.run('INSERT INTO weather_readings(id,watch_id,content_digest,probability,forecast_date,source,fetched_at,valid_until) '
              'VALUES(?,?,?,?,?,?,?,?)', reading, key, 'legacy-reading', 99, values['forecast_date'], 'retired-source',
              '2026-10-18T12:00:00Z', '2026-10-18T12:30:00Z')
    gf.db.run('INSERT INTO weather_refresh_claims(watch_id,token) VALUES(?,?)', key, 'retired-in-flight')
    for rev in gf.db.all('SELECT id,input_versions FROM plan_revisions WHERE plan_id=?', plan):
        versions = json.loads(rev['input_versions'])
        versions['weather_policy'] = 'legacy-policy'
        gf.db.run('UPDATE plan_revisions SET input_versions=? WHERE id=?', json.dumps(versions), rev['id'])
    return key, reading


def test_prompts_offer_optional_host_discovery_without_owning_an_api():
    prompt = system_prompt('Alice', 'Tuesday, October 13, 2026')
    assert WEATHER_POLICY in prompt and WEATHER_POLICY in INSTRUCTIONS
    assert LOCAL_HOST_CAPABILITIES in prompt and LOCAL_HOST_CAPABILITIES not in INSTRUCTIONS
    assert 'MCP discovery facility if available' in OPTIONAL_WEATHER_GUIDANCE
    assert 'continue without weather' in INSTRUCTIONS and 'weather API directly' in INSTRUCTIONS
    assert 'public location and date' in INSTRUCTIONS and 'personal confirmations' in INSTRUCTIONS
    assert len(prompt) < 8000
    assert len(NAMES) == 35 and not any('weather' in name.lower() or 'forecast' in name.lower() for name in NAMES)


def test_provider_and_engine_modules_have_been_removed():
    root = Path(__file__).resolve().parents[2]
    assert not (root / 'groupflow/engine/weather.py').exists()
    assert not (root / 'groupflow/integrations/weather.py').exists()
    assert importlib.util.find_spec('groupflow.engine.weather') is None


@pytest.mark.parametrize('blocker', ['none', 'withdrawn', 'hard_limit', 'expired_fact', 'changed_price'])
def test_retirement_rechecks_real_commitments_and_preserves_human_data(blocker):
    lab = make_lab()
    gf = lab.gf
    try:
        key, reading = legacy_watch(gf)
        if blocker == 'withdrawn':
            gf.human_contest(gf.identity.get('amir'), PLAN, lab.first, 'Keep this review private', gf.state_token(PLAN))
        elif blocker == 'hard_limit':
            gf.human_set_condition(gf.identity.get('amir'), 'budget', {'amount': 1}, PLAN)
        elif blocker in ('expired_fact', 'changed_price'):
            payload = {'place_id': 'birch_a', 'price_minor': 1200 if blocker == 'changed_price' else 1100}
            if blocker == 'expired_fact':
                gf.set_clock(fixed_clock('2026-10-18T11:00:00Z'))
                payload['valid_until'] = '2026-10-18T12:00:00Z'
            gf.inject_observation(GROUP, 'price_changed', payload, 'synthetic-public-source', 'existing-fact', verified=True)
            gf.set_clock(fixed_clock('2026-10-19T12:00:00Z'))
        gf._bump(PLAN, 'DEGRADED', 'Retired forecast rule needs a fresh review.')
        tables = ('approval_receipts', 'approval_scopes', 'challenges', 'executions', 'observations', 'conditions')
        before = {table: gf.db.all(f'SELECT * FROM {table} ORDER BY rowid') for table in tables}
        terms = gf.db.all('SELECT id,snapshot_json,content_digest FROM plan_revisions ORDER BY rowid')
        old_reading = gf.db.get('SELECT * FROM weather_readings WHERE id=?', reading)
        upgraded = GroupFlow(gf.db)
        assert upgraded._plan_row(PLAN)['status'] == ('COMMITTED' if blocker == 'none' else 'DEGRADED')
        assert {table: gf.db.all(f'SELECT * FROM {table} ORDER BY rowid') for table in tables} == before
        assert gf.db.all('SELECT id,snapshot_json,content_digest FROM plan_revisions ORDER BY rowid') == terms
        assert gf.db.get('SELECT * FROM weather_readings WHERE id=?', reading) == old_reading
        assert not gf.db.scalar('SELECT enabled FROM weather_watches WHERE id=?', key)
        assert not gf.db.scalar('SELECT count(*) FROM weather_refresh_claims')
        assert all('weather_policy' not in json.loads(row['input_versions']) for row in gf.db.all('SELECT input_versions FROM plan_revisions'))
        settled = upgraded.state_token(PLAN)
        assert upgraded.retire_local_weather() == []
        assert upgraded.state_token(PLAN) == settled
        assert gf.db.all('PRAGMA foreign_key_check') == []
    finally:
        gf.db.close()


@pytest.mark.parametrize('complete', [False, True], ids=['missing-answer', 'all-personal-answers'])
def test_retirement_does_not_manufacture_candidate_approval(complete):
    lab = make_lab()
    gf = lab.gf
    try:
        gf.propose_change('nora', plan=PLAN, item=lab.first, place='birch_b')
        gf.request_confirmations('nora', plan=PLAN)
        if complete:
            for mid in ('nora', 'amir', 'bea'):
                approve_all(gf, gf.identity.get(mid), PLAN)
        legacy_watch(gf)
        gf._bump(PLAN, 'DEGRADED', 'Retired forecast was a planning input.')
        before = {table: gf.db.all(f'SELECT * FROM {table} ORDER BY rowid') for table in ('approval_receipts', 'executions')}
        upgraded = GroupFlow(gf.db)
        assert upgraded._plan_row(PLAN)['status'] == ('READY_TO_COMMIT' if complete else 'AWAITING_CONFIRMATIONS')
        assert {table: gf.db.all(f'SELECT * FROM {table} ORDER BY rowid') for table in before} == before
    finally:
        gf.db.close()


def test_retirement_invalidates_only_old_weather_solves():
    lab = make_lab()
    gf = lab.gf
    try:
        old = gf.find_alternatives('nora', plan=PLAN).job
        other = gf.find_alternatives('nora', plan='birch-other').job
        data = json.loads(gf.db.scalar('SELECT input_json FROM repair_jobs WHERE id=?', old))
        data['versions']['weather_policy'] = 'retired-policy'
        gf.db.run('UPDATE repair_jobs SET input_json=? WHERE id=?', json.dumps(data), old)
        ordinary = json.loads(gf.db.scalar('SELECT input_json FROM repair_jobs WHERE id=?', other))
        ordinary['note'] = 'weather_policy is only a label here'
        gf.db.run('UPDATE repair_jobs SET input_json=? WHERE id=?', json.dumps(ordinary), other)
        gf.db.run("UPDATE repair_jobs SET status='queued',result_json=NULL,started_at=NULL,finished_at=NULL WHERE id IN (?,?)", old, other)
        GroupFlow(gf.db)
        assert gf.db.scalar('SELECT status FROM repair_jobs WHERE id=?', old) == 'failed'
        assert 'STALE_INPUT' in gf.db.scalar('SELECT error FROM repair_jobs WHERE id=?', old)
        assert gf.db.scalar('SELECT status FROM repair_jobs WHERE id=?', other) == 'queued'
        assert not gf.db.scalar("SELECT count(*) FROM executions WHERE provider_reference NOT LIKE 'SIM-%'")
    finally:
        gf.db.close()


def test_retirement_keeps_an_unsent_proposal_unsent():
    lab = make_lab()
    gf = lab.gf
    try:
        # Seed the persisted unsent proposal made by the retired workflow.
        # A new search on an already-feasible plan legitimately has no proposal.
        gf.propose_change('nora', plan=PLAN, item=lab.first, place='birch_b')
        p = gf._plan_row(PLAN)
        draft = gf._revision(p['draft_revision_id'])
        candidate = gf._new_revision(p, gf._snapshot(draft), 'P2', 'candidate', 'repair', 'nora', p['active_revision_id'])
        gf._bump(PLAN, 'PROPOSED', 'Existing unsent proposal.', candidate_revision_id=candidate['id'])
        assert candidate and not gf._round_open(candidate)
        legacy_watch(gf)
        gf._bump(PLAN, 'DEGRADED', 'Retired local weather affected the earlier agreement.')
        before = {table: gf.db.all(f'SELECT * FROM {table} ORDER BY rowid') for table in
                  ('approval_scopes', 'approval_receipts', 'confirmation_rounds', 'challenges', 'executions')}
        upgraded = GroupFlow(gf.db)
        assert upgraded._plan_row(PLAN)['status'] == 'PROPOSED'
        assert not upgraded._round_open(candidate)
        assert {table: gf.db.all(f'SELECT * FROM {table} ORDER BY rowid') for table in before} == before
    finally:
        gf.db.close()


def test_legacy_readings_are_not_current_planning_inputs():
    lab = make_lab()
    gf = lab.gf
    try:
        p = gf._plan_row(PLAN)
        before = gf.state_token(PLAN)
        key, reading = legacy_watch(gf)
        assert gf.state_token(PLAN) == before
        assert not gf._problem(p, gf._latest_snapshot(p)).unavailable
        assert 'weather_policy' not in gf._input_versions(p)
        facts = json.dumps(gf.planning_facts('nora', PLAN))
        assert key not in facts and reading not in facts and 'retired-source' not in facts
        for name in ('weather_client', 'human_weather_watch', 'human_weather_refresh', 'human_weather_disable', 'poll_weather_watches'):
            assert not hasattr(gf, name)
    finally:
        gf.db.close()


async def test_worker_keeps_fallback_checks_without_polling_weather():
    lab = make_lab()
    try:
        def forbidden():
            pytest.fail('The worker must not poll a legacy forecast hook.')
        lab.gf.poll_weather_watches = forbidden
        worker = Worker(lab.gf)
        await worker.tick()
        assert worker._coordination_at > 0
    finally:
        lab.gf.db.close()


@pytest.mark.parametrize('committed', [False, True])
async def test_worker_reconciles_fallback_expiry_after_weather_removal(committed):
    lab = make_lab()
    gf = lab.gf
    try:
        grant(lab)
        use_branch(lab)
        for mid in ('nora', 'bea'):
            approve_all(gf, gf.identity.get(mid), PLAN)
        assert gf._plan_row(PLAN)['status'] == 'READY_TO_COMMIT'
        if committed:
            assert gf.commit_plan('nora', plan=PLAN).outcome == 'committed'
        receipts = gf.db.all('SELECT * FROM approval_receipts ORDER BY rowid')
        gf.set_clock(fixed_clock(LATER))
        await Worker(gf).tick()
        assert gf._plan_row(PLAN)['status'] == ('DEGRADED' if committed else 'AWAITING_CONFIRMATIONS')
        assert gf.db.all('SELECT * FROM approval_receipts ORDER BY rowid') == receipts
        assert not hasattr(gf, 'poll_weather_watches')
    finally:
        gf.db.close()


def test_older_weather_database_reopens_without_deleting_old_readings_or_approvals(tmp_path):
    path = tmp_path / 'older-weather.sqlite3'
    lab = make_lab(Db(str(path)))
    gf = lab.gf
    key, reading = legacy_watch(gf)
    gf._bump(PLAN, 'DEGRADED', 'An old local weather rule was active.')
    protected = ('members', 'approval_scopes', 'approval_receipts', 'executions', 'weather_readings')
    before = {table: gf.db.all(f'SELECT * FROM {table} ORDER BY rowid') for table in protected}
    gf.db.close()
    upgraded = GroupFlow(Db(str(path)))
    try:
        assert upgraded._plan_row(PLAN)['status'] == 'COMMITTED'
        assert {table: upgraded.db.all(f'SELECT * FROM {table} ORDER BY rowid') for table in protected} == before
        assert upgraded.db.get('SELECT id FROM weather_readings WHERE id=?', reading)
        assert not upgraded.db.scalar('SELECT enabled FROM weather_watches WHERE id=?', key)
        assert upgraded.db.all('PRAGMA foreign_key_check') == []
    finally:
        upgraded.db.close()
