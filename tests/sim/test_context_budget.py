import json

from groupflow.sim.agent import system_prompt, to_tool_defs
from groupflow.sim.llm.scripted import ScriptedProvider, last_tool_result
from tests.sim.harness import start_harness


def test_schema_trimming_preserves_named_properties_and_validation():
    from groupflow.sim.llm.types import clean_schema

    schema = {'$schema': 'https://json-schema.org/draft/2020-12/schema', 'title': 'Arguments', 'type': 'object',
              'properties': {'title': {'title': 'Title', 'type': 'string', 'minLength': 1},
                             'parts': {'type': 'array', 'items': {'$ref': '#/$defs/Part'}}},
              'required': ['title'], '$defs': {'Part': {'title': 'Part', 'type': 'string', 'enum': ['a', 'b']}}}
    cleaned = clean_schema(schema)
    assert '$schema' not in cleaned and 'title' not in cleaned
    assert cleaned['properties']['title'] == {'type': 'string', 'minLength': 1}
    assert cleaned['properties']['parts']['items']['$ref'] == '#/$defs/Part'
    assert cleaned['$defs']['Part'] == {'type': 'string', 'enum': ['a', 'b']}
    assert cleaned['required'] == ['title']
    assert schema['properties']['title']['title'] == 'Title'  # no mutation of the MCP contract


async def test_focus_is_remembered_and_refreshed_without_unrelated_plan_bodies():
    model = ScriptedProvider([
        {'tool_calls': [{'name': 'get_context', 'arguments': {'plan': 'party'}}]}, {'text': 'Okay.'}, {'text': 'Okay.'},
    ])
    h = start_harness(model, prefetch=True)
    try:
        await h.say('alice', 'Tell me about the party.')
        assert h.agent.active_plans['alice'] == 'party'
        assert not last_tool_result(model.requests[0], 'get_context').get('plans')
        h.engine.propose_change('charlie', item='party.date', value='Sunday, Oct 25, 6:00 pm')
        await h.say('alice', 'What changed?')
        state = last_tool_result(model.requests[-1], 'get_context')
        assert state['active_plan_id'] == 'party'
        assert [p['id'] for p in state['plans']] == ['party']
        assert state['decisions_for_you'][0]['proposed'] == 'Sunday, Oct 25, 6:00 pm'
        assert 'Friday dinner' not in json.dumps(state['plans'])
    finally:
        await h.close()


async def test_ui_group_switch_clears_focus_and_a_hidden_plan_hint_never_opens_it():
    model = ScriptedProvider([{'text': 'Okay.'}] * 3)
    h = start_harness(model, prefetch=True)
    try:
        await h.agent.handle('alice', 'What is the plan?', group='friends', plan='friday_dinner')
        assert h.agent.active_plans['alice'] == 'friday_dinner'
        await h.agent.handle('alice', 'What is happening here?', group='family')
        state = last_tool_result(model.requests[-1], 'get_context')
        assert {p['id'] for p in state['plan_index']} == {'party', 'home'}
        assert not state.get('plans') and 'alice' not in h.agent.active_plans
        assert [m.role for m in model.requests[-1].messages] == ['user', 'assistant', 'tool']
        await h.agent.handle('linda', 'Show it.', group='family', plan='party')
        state = last_tool_result(model.requests[-1], 'get_context')
        assert state['active_plan_id'] == 'home'
        assert "Linda's surprise party" not in json.dumps(state)
    finally:
        await h.close()


async def test_tools_remain_discoverable_with_a_bounded_description_budget():
    h = start_harness(ScriptedProvider([]))
    try:
        discovered = await h.sessions.get('alice').list_tools()
        assert len(discovered) == 28
        tools = to_tool_defs(discovered, include_project=False)
        assert len(tools) == 24
        assert {'list_templates', 'get_template', 'create_group', 'invite_to_group', 'notify_people', 'ask_followup', 'answer_followup',
                'set_reminder', 'review_plan'} <= {t.name for t in tools}
        serialized = json.dumps([{'name': t.name, 'description': t.description, 'parameters': t.parameters} for t in tools])
        # About 900 bytes per tool on average; the cap moves only when a tool is added, never to fit longer descriptions.
        assert len(serialized) < 24000
        assert len(system_prompt('Alice', 'Tuesday, October 13, 2026')) < 8000
    finally:
        await h.close()
