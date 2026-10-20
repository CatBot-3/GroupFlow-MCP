"""Actual local-agent prompt delivery with a scripted model, not an LLM score."""
from __future__ import annotations

from groupflow.host_guidance import WEATHER_POLICY, LOCAL_HOST_CAPABILITIES
from groupflow.sim.llm.scripted import ScriptedProvider
from tests.sim.harness import start_harness


async def test_local_agent_receives_no_weather_capability_and_can_continue_planning():
    def choose_existing_plan(request):
        assert WEATHER_POLICY in request.system
        assert LOCAL_HOST_CAPABILITIES in request.system
        assert not any('weather' in tool.name.lower() or 'forecast' in tool.name.lower() for tool in request.tools)
        return {'tool_calls': [{'name': 'get_context', 'arguments': {'plan': 'friday_dinner'}}]}

    model = ScriptedProvider([choose_existing_plan, {'text': 'Here is the current Friday plan.'}])
    host = start_harness(model, prefetch=False)
    try:
        turn = await host.say('alice', 'Show Friday dinner; consider weather only if a forecast MCP is available.')
        assert any(call['name'] == 'get_context' for call in turn.calls)
        assert not any('weather' in call['name'].lower() for call in turn.calls)
        assert 'current Friday plan' in turn.reply
    finally:
        await host.close()
