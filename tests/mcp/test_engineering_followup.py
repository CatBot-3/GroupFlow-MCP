"""Actual MCP source-freshness regression, independent of any weather service."""
from __future__ import annotations

import pytest

from groupflow.engine import fixed_clock
from tests.shared_workload import call, connect, start_shared_server


@pytest.fixture
def fact_server(tmp_path):
    server = start_shared_server(tmp_path / 'verified-fact.sqlite3', worker=False)
    server.engine.set_clock(fixed_clock('2026-11-01T12:00:00Z'))
    try:
        yield server
    finally:
        server.stop()


async def test_mcp_expired_analysis_returns_stale_until_verified_fact_is_refreshed(fact_server, monkeypatch):
    import groupflow.engine.planning_analysis as analysis

    server = fact_server
    gf = server.engine
    payload = {'place_id': 'harbour_a', 'price_minor': 1200, 'valid_until': '2026-11-01T12:15:00Z'}
    gf.inject_observation('commons', 'price_changed', payload, 'synthetic-price-source', 'price-before', verified=True)
    original = analysis.repair

    def solve_then_expire(*args, **kwargs):
        result = original(*args, **kwargs)
        gf.set_clock(fixed_clock('2026-11-01T12:16:00Z'))
        return result

    monkeypatch.setattr(analysis, 'repair', solve_then_expire)
    async with connect(server, 'avery') as client:
        result = await call(client, 'planning_options', {'plan': 'dinner', 'mode': 'resilience'})
        assert result.get('error', {}).get('code') == 'STALE', result
        assert not any(field in result for field in ('scenarios', 'repairs', 'action_receipt'))
        gf.inject_observation('commons', 'price_changed', {**payload, 'valid_until': '2026-11-01T12:45:00Z'},
                              'synthetic-price-source', 'price-after', verified=True)
        recovered = await call(client, 'planning_options', {'plan': 'dinner', 'mode': 'resilience'})
        assert recovered['outcome'] == 'resilience' and recovered['read_only'], recovered
