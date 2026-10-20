"""An unrelated hypothesis must not hide a real price change already received."""
import pytest
from tests.conftest import seeded


@pytest.mark.parametrize('scenario', [
    {'kind': 'place_unavailable', 'place_id': 'restaurant_e'},
    {'kind': 'participant_unavailable', 'member_id': 'bob', 'start': 0, 'end': 15},
])
def test_starting_feasibility_uses_current_verified_prices_for_every_scenario(scenario):
    gf = seeded({'friday_dinner': 'committed'})
    try:
        gf.inject_observation('friends', 'price_changed', {'place_id': 'restaurant_a', 'price_minor': 4000},
                              'synthetic-price-provider', 'real-current-price-before-hypothesis', verified=True)
        before = gf.db.all('SELECT * FROM plan_revisions ORDER BY rowid')
        result = gf.planning_options('alice', 'friday_dinner', mode='resilience', scenarios=[scenario])
        comparison = result['scenarios'][0]['comparisons'][0]
        # Existing dinner40 + dessert8 + ride5 exceeds the already-declared45 budget.
        # The unrelated outage/busy window affects none of the current activities.
        assert comparison['currently_feasible_in_scenario'] is False
        assert comparison['repairs']
        for repair in comparison['repairs']:
            totals = {}
            for a in repair['snapshot']['activities']:
                for mid, share in a['cost_shares'].items():
                    totals[mid] = totals.get(mid, 0) + share
            assert all(total <= 4500 for total in totals.values())
        assert gf.db.all('SELECT * FROM plan_revisions ORDER BY rowid') == before
    finally:
        gf.db.close()
