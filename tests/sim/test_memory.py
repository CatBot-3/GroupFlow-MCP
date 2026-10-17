import json

from groupflow.sim.memory import memory_map


def test_map_contains_only_the_selected_identity_context(gf):
    context = gf.context('linda', group='family', detail='summary', mark_read=False).model_dump(mode='json')
    mapped = memory_map(context, 'linda')
    assert any(n['label'] == 'Home' for n in mapped['nodes'])
    text = json.dumps(mapped)
    assert "surprise" not in text.lower() and "party.date" not in text
    assert all(e['from'] in {n['id'] for n in mapped['nodes']} and e['to'] in {n['id'] for n in mapped['nodes']} for e in mapped['edges'])


def test_map_versions_and_confirmations_come_from_returned_fields(gf):
    context = gf.context('bob', plan='friday_dinner', detail='summary', mark_read=False).model_dump(mode='json')
    mapped = memory_map(context, 'bob')
    assert any(n['kind'] == 'version' for n in mapped['nodes'])
    confirmation = next(n for n in mapped['nodes'] if n['kind'] == 'confirmation')
    assert confirmation['data'] == context['plans'][0]['agreement_summary']
    assert all(n['data'] in context['your_conditions'] for n in mapped['nodes'] if n['kind'] == 'condition')
    assert 'diet' not in ' '.join(n['label'].lower() for n in mapped['nodes'] if n['kind'] == 'condition')


def test_empty_or_paged_context_does_not_invent_hidden_nodes():
    mapped = memory_map({'you': 'Someone', 'next_offset': 20, 'truncated': {'plans': 40}}, 'someone')
    assert len(mapped['nodes']) == 1 and mapped['edges'] == []
    assert mapped['pagination'] == {'next_offset': 20, 'truncated': {'plans': 40}}


def test_a_plan_hangs_under_its_own_group_when_two_groups_share_a_name():
    context = {'you': 'Maya', 'groups': [{'id': 'g1', 'name': 'Flatmates'}, {'id': 'g2', 'name': 'Flatmates'}],
               'plan_index': [{'id': 'p2', 'title': 'Rent', 'group': 'Flatmates', 'group_id': 'g2'}],
               'plans': [{'id': 'p2', 'title': 'Rent', 'group': 'Flatmates', 'group_id': 'g2', 'details': [], 'tasks': []}]}
    mapped = memory_map(context, 'maya')
    assert [e['from'] for e in mapped['edges'] if e['to'] == 'plan:p2'] == ['group:g2']


def test_versions_schedule_activities_and_no_edge_joins_a_kind_to_itself(gf):
    context = gf.context('alice', plan='friday_dinner', detail='summary', mark_read=False).model_dump(mode='json')
    mapped = memory_map(context, 'alice')
    kinds = {n['id']: n['kind'] for n in mapped['nodes']}
    pairs = {(kinds[e['from']], kinds[e['to']]) for e in mapped['edges']}
    # The explorer lays these out so every edge runs between neighbouring columns or straight up between versions and activities.
    assert pairs <= {('person', 'group'), ('person', 'condition'), ('person', 'plan'), ('group', 'plan'), ('plan', 'item'), ('plan', 'version'),
                     ('plan', 'confirmation'), ('version', 'item')}
    assert ('version', 'item') in pairs
