"""Measure a real MCP briefing with a scripted model; no external model calls.

Run from the project root using the development environment (tests harness).
The serializer counts JSON characters, not tokens or billed input usage.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from groupflow.sim.llm.openai_compat import OpenAiCompatibleProvider
from groupflow.sim.llm.scripted import ScriptedProvider, last_tool_result
from tests.sim.harness import start_harness


def chars(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')))


async def measure() -> dict:
    model = ScriptedProvider([{'text': 'Here is the current plan.'}] * 2)
    h = start_harness(model, prefetch=True)
    serializer = OpenAiCompatibleProvider(id='inspection', label='local serializer', base_url='http://localhost',
                                          api_key='', model='no-model', thinking=None)
    try:
        await h.say('alice', 'What are my plans?')
        request = model.requests[0]
        body = serializer.request_body(request)
        context = last_tool_result(request, 'get_context')
        await h.agent.handle('alice', 'What is the plan?', plan='friday_dinner')
        focused = last_tool_result(model.requests[-1], 'get_context')
        return {'units': 'serialized JSON characters, NOT tokens', 'tools_count': len(body['tools']),
                'tool_definitions_chars': chars(body['tools']), 'system_prompt_chars': len(request.system),
                'initial_context_chars': chars(context), 'plans_in_initial_context': len(context.get('plans', [])),
                'plans_in_initial_index': len(context.get('plan_index', [])), 'template_summaries': len(context.get('templates', [])),
                'full_request_chars': chars(body), 'focused_dinner_context_chars': chars(focused),
                'plans_in_focused_context': len(focused.get('plans', [])),
                'tools_largest_first': sorted([{'name': t['function']['name'], 'chars': chars(t)} for t in body['tools']],
                                              key=lambda x: -x['chars'])[:4]}
    finally:
        await h.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=Path('docs/context_measurement.json'))
    args = parser.parse_args()
    result = asyncio.run(measure())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
