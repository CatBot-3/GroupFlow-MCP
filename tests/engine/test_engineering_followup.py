"""Retained engineering checks after the local weather feature was retired.

The complete prior files and reports remain in their dated evidence snapshots.
"""
from __future__ import annotations

import pytest

from groupflow.core.errors import DomainError
from groupflow.engine import fixed_clock
from groupflow.world import approve_all
from tests.engine.test_agreement_fallback import make_lab, GROUP, PLAN
from tests.shared_workload import build_world


def test_personal_comparison_uses_each_activity_local_date_across_month_and_dst():
    lab = make_lab()
    gf = lab.gf
    try:
        gf.create_plan('nora', 'evening_out', 'Three-day gathering', group=GROUP,
                       start_date='2026-10-31', days=3, starter=False, plan_id='weekend')
        gf.db.run('UPDATE plans SET catalog_json=? WHERE id=?', gf._plan_row(PLAN)['catalog_json'], 'weekend')
        item = gf.propose_change('nora', plan='weekend', new_type='stop', title='Sunday tasting',
                                 day=1, start='13:00', end='14:00', place='birch_a', participants=['nora', 'amir']).item
        gf.request_confirmations('nora', plan='weekend')
        for mid in ('nora', 'amir'):
            approve_all(gf, gf.identity.get(mid), 'weekend')
        assert gf.commit_plan('nora', plan='weekend').outcome == 'committed'
        gf.propose_change('nora', plan='weekend', item=item, day=2)
        for view in (gf.human_agreements(gf.identity.get('amir'), 'weekend'),
                     gf.planning_options('amir', 'weekend', mode='agreement')):
            comparison = next(row for row in view['items'] if row['activity_id'] == item)
            assert comparison['before']['date'] == '2026-11-01'
            assert comparison['after']['date'] == '2026-11-02'
            assert comparison['before']['timezone'] == comparison['after']['timezone'] == 'America/Toronto'
    finally:
        gf.db.close()


@pytest.mark.parametrize('mode', ['resilience', 'relaxations'])
@pytest.mark.parametrize('expires', [False, True], ids=['source-still-fresh', 'source-expires'])
def test_comparison_rechecks_verified_fact_freshness_without_database_edit(monkeypatch, mode, expires):
    import groupflow.engine.planning_analysis as analysis

    world = build_world(':memory:')
    gf = world.engine
    try:
        gf.set_clock(fixed_clock('2026-11-01T12:00:00Z'))
        gf.inject_observation('commons', 'price_changed', {'place_id': 'harbour_a', 'price_minor': 1200,
                              'valid_until': '2026-11-01T12:15:00Z'}, 'synthetic-price-source', 'expiring-price', verified=True)
        version = gf.state_token('dinner')
        original = analysis.repair

        def solve_then_advance(*args, **kwargs):
            result = original(*args, **kwargs)
            gf.set_clock(fixed_clock('2026-11-01T12:16:00Z' if expires else '2026-11-01T12:05:00Z'))
            return result

        monkeypatch.setattr(analysis, 'repair', solve_then_advance)
        if expires:
            with pytest.raises(DomainError) as caught:
                gf.planning_options('avery', 'dinner', mode=mode)
            assert caught.value.code == 'STALE'
        else:
            result = gf.planning_options('avery', 'dinner', mode=mode)
            assert result['read_only'] and 'error' not in result
        assert gf.state_token('dinner') == version
    finally:
        gf.db.close()
