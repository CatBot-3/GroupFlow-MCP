"""Context reduction keeps visibility, freshness, pagination and discovery intact."""
import json

from groupflow.templates import load_templates


def test_summary_returns_indexes_not_every_plan_body(gf):
    brief = gf.context('alice', detail='summary', mark_read=False)
    assert {p.id for p in brief.plan_index} == {'party', 'home', 'friday_dinner'}
    assert brief.plans == [] and brief.your_conditions == []
    assert len(brief.templates) == 8
    assert all(not hasattr(t, 'catalog') for t in brief.templates)
    full = gf.context('alice', mark_read=False)
    assert len(full.plans) == 3 and full.plan_index == []


def test_focused_summary_refreshes_one_plan_and_indexes_the_others(gf):
    brief = gf.context('alice', plan='friday_dinner', detail='summary', mark_read=False)
    assert brief.active_plan_id == 'friday_dinner'
    assert [p.id for p in brief.plans] == ['friday_dinner']
    assert len(brief.plan_index) == 3
    gf.propose_change('charlie', item='party.date', value='Sunday, Oct 25, 6:00 pm')
    newer = gf.context('alice', plan='friday_dinner', detail='summary', mark_read=False)
    assert next(p for p in newer.plan_index if p.id == 'party').pending_decisions == 1


def test_personal_summary_keeps_live_dependency_evidence_after_an_external_change(gf):
    gf.propose_change('alice', item='party.date', value='Sunday, Oct 25, 6:00 pm')
    brief = gf.context('charlie', detail='summary', mark_read=False)
    invitations = next(t for t in brief.your_tasks if t.title == 'Send invitations')
    assert any('Rosa' in b['label'] and b['decided_by'] == 'Alice' for b in invitations.blockers)
    assert invitations.flags
    booking = gf.db.get("SELECT id FROM items WHERE label=?", "Book Rosa's back room")
    gf.propose_change('alice', item=booking['id'], status='done')
    newer = gf.context('charlie', detail='summary', mark_read=False)
    assert not next(t for t in newer.your_tasks if t.id == invitations.id).blockers


def test_personal_dependency_summary_does_not_expose_hidden_party(gf):
    gf.propose_change('alice', item='party.date', value='Sunday, Oct 25, 6:00 pm')
    visible = gf.context('linda', detail='summary', mark_read=False).model_dump_json()
    assert "Book Rosa" not in visible and 'party.date' not in visible and 'Send invitations' not in visible


def test_group_directory_uses_canonical_ids_and_only_the_callers_active_memberships(gf):
    brief = gf.context('bob', detail='summary', mark_read=False)
    assert {g['id'] for g in brief.groups} == {'friends', 'family'}
    assert all(p.group_id in {'friends', 'family'} for p in brief.plan_index)
    assert [g['id'] for g in gf.context('linda', detail='summary', mark_read=False).groups] == ['family']
    page = gf.context('bob', detail='summary', mark_read=False, limit=1)
    assert len(page.groups) == 1 and page.group_next_offset == 1


def test_group_focus_is_a_filter_not_an_authority(gf):
    brief = gf.context('linda', detail='summary', group='family', mark_read=False)
    assert [p.id for p in brief.plan_index] == ['home']
    assert [p.id for p in brief.plans] == ['home']
    assert "Linda's surprise party" not in brief.model_dump_json()
    assert gf.context('linda', detail='summary', group='friends', mark_read=False).plan_index == []


def test_summary_paginates_plans_and_does_not_consume_unreturned_news(gf):
    plan = gf._plan_row('party')
    for i in range(10):
        gf.db.tx(lambda i=i: gf._notify(['alice'], plan, 'test', f'Update {i}'))
    before = gf.db.scalar("SELECT COUNT(*) FROM notifications WHERE member_id = 'alice' AND plan_id = 'party' AND read_at IS NULL")
    brief = gf.context('alice', plan='party', detail='summary', limit=2)
    after = gf.db.scalar("SELECT COUNT(*) FROM notifications WHERE member_id = 'alice' AND plan_id = 'party' AND read_at IS NULL")
    assert len(brief.updates) == 2 and after == before - 2
    assert brief.truncated['updates'] == before - 2
    assert brief.next_offset == 2
    page = gf.context('alice', detail='summary', limit=2, offset=2, mark_read=False)
    assert len(page.plan_index) == 1 and page.next_offset is None


def test_template_specs_are_on_demand_and_exclude_catalog_data(gf):
    page = gf.list_templates('alice', limit=2)
    assert page.total == 8 and page.next_offset == 2
    assert len(gf.list_templates('alice', offset=7).templates) == 1
    assert gf.list_templates('alice', query='meeting').templates[0].id == 'meeting'
    spec = gf.get_template_spec('alice', 'evening_out').model_dump()
    assert spec['item_types'][0]['policy'] == 'participants'
    assert next(c for c in spec['conditions'] if c['name'] == 'budget')['hint']
    assert 'catalog' not in spec and 'Restaurant A' not in json.dumps(spec)


def test_new_yaml_is_discoverable_without_changing_tool_text(gf, tmp_path, monkeypatch):
    from groupflow.engine import core
    from groupflow import templates

    template = {'id': 'volunteer_shift', 'name': 'Volunteer shift', 'description': 'Coordinate a volunteer shift.',
                'item_types': [{'name': 'job', 'label': 'Job', 'shape': 'task', 'policy': 'owner'}]}
    import yaml

    (tmp_path / 'volunteer_shift.yaml').write_text(yaml.safe_dump(template), encoding='utf-8')
    discovered = {**load_templates(), **load_templates(str(tmp_path))}
    monkeypatch.setattr(core, 'load_templates', lambda: discovered)
    monkeypatch.setattr(templates, 'load_templates', lambda: discovered)
    assert gf.list_templates('alice', query='volunteer').templates[0].id == 'volunteer_shift'
    assert gf.get_template_spec('alice', 'volunteer_shift').item_types[0]['name'] == 'job'
    created = gf.create_plan('alice', 'volunteer_shift', 'Saturday volunteering', group='friends')
    assert created.outcome == 'created'
