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
