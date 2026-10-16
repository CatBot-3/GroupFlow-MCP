from unittest.mock import patch

import pytest

from groupflow.core.errors import DomainError
from tests.conftest import seeded


def test_private_note_cannot_be_saved_in_a_shared_plan_even_with_personal_owner(gf):
    with pytest.raises(DomainError, match='private note'):
        gf.propose_change('bob', plan='party', new_type='detail', title='Gift idea', value='Pottery class', owner='Bob',
                          quote='Remember that the gift idea is a pottery class, just for me.')
    assert not gf.search('alice', 'pottery').items
    private = gf.create_plan('bob', 'general_event', 'My ideas', private=True)
    gf.propose_change('bob', plan=private.plan.id, new_type='detail', title='Gift idea', value='Pottery class', quote='Just for me')
    assert not gf.search('alice', 'pottery').items


def test_a_denial_cannot_replace_an_existing_detail(gf):
    before = gf._current_value(gf._item('cake.order'))
    with pytest.raises(DomainError, match='denial'):
        gf.propose_change('bob', item='cake.order', value='No ice cream cake', quote="We're not getting an ice cream cake.")
    assert gf._current_value(gf._item('cake.order')) == before
    assert gf.propose_change('bob', item='cake.order', value='Lemon cake', quote='Instead make it lemon cake.').outcome == 'confirmed'


def test_a_spoken_withdrawal_cannot_be_converted_to_a_roster_edit():
    gf = seeded({'friday_dinner': 'committed'})
    try:
        before = gf._plan_row('friday_dinner')
        with pytest.raises(DomainError, match='own page'):
            gf.propose_change('alice', plan='friday_dinner', item='dinner', participants=['Alice', 'Bob', 'Dana'],
                              quote="Chen can't come anymore, take him off the plan.")
        assert gf._plan_row('friday_dinner') == before
    finally:
        gf.db.close()


def test_focused_brief_still_delivers_new_tasks_and_decisions_from_other_plans(gf):
    gf.propose_change('linda', plan='home', new_type='chore', title='Fix the gutter', owner='Bob')
    gf.propose_change('charlie', item='party.date', value='Sunday, Oct 25, 6:00 pm')
    brief = gf.context('bob', plan='party', detail='summary', mark_read=False)
    assert any(t.title == 'Fix the gutter' and t.plan_id == 'home' for t in brief.your_tasks)
    assert [p.id for p in brief.plans] == ['party']
    assert any(i['label'] == 'Venue' for p in brief.plan_index if p.id == 'party' for i in p.key_items)
    alice = gf.context('alice', plan='friday_dinner', detail='summary', mark_read=False)
    assert any(d.item == 'party.date' for d in alice.decisions_for_you)


def test_a_running_repair_cannot_overwrite_a_newer_edit():
    from groupflow.engine import agreement

    gf = seeded({'friday_dinner': 'degraded'})
    real = agreement.repair
    try:
        def during(*args, **kwargs):
            outcome = real(*args, **kwargs)
            gf.propose_change('alice', plan='friday_dinner', item='dessert', start='20:00')
            return outcome
        with patch.object(agreement, 'repair', during):
            result = gf.find_alternatives('alice', plan='friday_dinner')
        assert result.status == 'failed'
        latest = gf._latest_snapshot(gf._plan_row('friday_dinner'))
        assert latest.activity('dessert').start == 1200
    finally:
        gf.db.close()
def test_new_work_cannot_overwrite_an_unrelated_existing_task(gf):
    from groupflow.core.errors import DomainError
    import pytest
    old = gf.db.get("SELECT * FROM items WHERE label='Fix the porch light'")
    with pytest.raises(DomainError) as err:
        gf.propose_change('linda',item=old['id'],title='Fix the gutter',owner='Bob',quote='Remind Bob to fix the gutter.')
    assert err.value.code == 'NEW_WORK_NOT_RENAME'
    assert gf._item(old['id'])['label'] == 'Fix the porch light'
    renamed = gf.propose_change('linda',item=old['id'],title='Check the porch light',quote='Rename the porch light task to Check the porch light.')
    assert renamed.outcome == 'updated'

def test_explicit_template_list_type_preserves_its_open_policy(gf):
    import pytest
    from groupflow.core.errors import DomainError
    with pytest.raises(DomainError) as err:
        gf.propose_change('chen',plan='cottage_weekend',new_type='task',title='Bug spray',quote='Add bug spray to the packing list.')
    assert err.value.code == 'ITEM_TYPE_MISMATCH'
    added = gf.propose_change('chen',plan='cottage_weekend',new_type='packing',title='Bug spray',quote='Add bug spray to the packing list.')
    assert gf._item(added.item)['policy'] == 'open'
    assert gf.propose_change('omar',item=added.item,status='done',quote="I've packed the bug spray.").outcome == 'updated'

def test_bare_weekday_rejects_a_model_invented_calendar_date(gf):
    import pytest
    from groupflow.core.errors import DomainError
    with pytest.raises(DomainError) as err:
        gf.propose_change('alice',item='party.date',value='Sunday, Oct 18, 5:00 pm',quote="Let's move the party to Sunday at 5.")
    assert err.value.code == 'DATE_REFERENCE_REQUIRED'
    assert gf._current_value(gf._item('party.date')) == 'Saturday, Oct 24, 6:00 pm'
    assert gf.propose_change('alice',item='party.date',value='Sunday, Oct 25, 5:00 pm',quote="Let's move the party to Sunday at 5.").outcome == 'confirmed'
    assert gf.propose_change('alice',item='party.date',value='Sunday, Oct 18, 5:00 pm',quote='Actually move it to Sunday, Oct 18 at 5 pm.').outcome == 'confirmed'



def test_a_self_corrected_weekday_is_accepted_and_other_weeks_still_are_not(gf):
    """Found by the realistic suite: "Friday night, wait no, make it Sunday" names two weekdays; the corrected one must be accepted."""
    import pytest
    from groupflow.core.errors import DomainError
    quote = "can we move the party to friday, same time. wait no, make it sunday, friday is my brother's thing"
    assert gf.propose_change('alice', item='party.date', value='Sunday, Oct 25, 6:00 pm', quote=quote).outcome == 'confirmed'
    with pytest.raises(DomainError) as err:
        gf.propose_change('alice', item='party.date', value='Sunday, Nov 1, 6:00 pm', quote=quote)
    assert err.value.code == 'DATE_REFERENCE_REQUIRED'


# ---------------------------------------------------------------- the same guards, said the way people actually talk

def test_casual_private_notes_are_kept_out_of_shared_plans(gf):
    import pytest
    from groupflow.core.errors import DomainError
    for quote in ("keep this to myself: the gift for mom is concert tickets", "note to myself, mom wants the blue scarf",
                  "for my eyes only lol, gift is a watch", "dont tell anyone but the gift is a kayak"):
        with pytest.raises(DomainError) as err:
            gf.propose_change('bob', plan='party', new_type='task', title='Gift idea', quote=quote)
        assert err.value.code == 'PRIVATE_SCOPE_REQUIRED', quote


def test_casual_denials_are_not_stored_but_casual_replacements_are(gf):
    import pytest
    from groupflow.core.errors import DomainError
    for quote in ("we're def not doing chocolate again", "nah not gonna get the bakery cake this time"):
        with pytest.raises(DomainError) as err:
            gf.propose_change('bob', item='cake.order', value='No chocolate cake', quote=quote)
        assert err.value.code == 'NEGATION_NOT_VALUE', quote
    ok = gf.propose_change('bob', item='cake.order', value='Lemon cake', quote="not doing chocolate, let's do lemon")
    assert ok.outcome == 'confirmed'


def test_casual_withdrawals_cannot_edit_the_roster(gf):
    import pytest
    from groupflow.core.errors import DomainError
    for quote in ("chen bailed, drop him from dinner", "chen isn't coming anymore", "chen won't make it, take him out", "count chen out lol"):
        with pytest.raises(DomainError) as err:
            gf.propose_change('alice', plan='friday_dinner', item='dinner', participants=['Alice', 'Bob', 'Dana'], quote=quote)
        assert err.value.code == 'SELF_WITHDRAWAL_REQUIRED', quote


def test_casual_first_person_limits_are_the_speakers_own(gf):
    r = gf.set_my_conditions('bob', [{'kind': 'home_by', 'value': {'time': '20:30'}}], plan='friday_dinner',
                             quote="im out by 8:30 tho, chen said he's fine later")
    assert r.outcome == 'recorded'
