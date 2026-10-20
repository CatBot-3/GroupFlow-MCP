"""Probe a real local MCP transport without calling an LLM provider."""
from __future__ import annotations
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from groupflow.sim.mcp_pool import SessionPool
from groupflow.sim.traffic import TrafficLog
from groupflow.world import load_world


async def probe(url: str) -> dict:
    world = load_world()
    traffic = TrafficLog()
    pool = SessionPool({p.id: p.token for p in world.people}, url, traffic)
    result = {'host': 'official Python MCP SDK, legacy session host', 'endpoint': url,
              'external_llm_calls': 0, 'actual_alexa_tested': False, 'checks': {}}
    try:
        session = pool.get('alice')
        tools = await session.list_tools()
        result['protocol'] = session.protocol_version
        result['checks']['session_bound'] = bool(session.session_id)
        result['checks']['new_tools_discoverable'] = {'resolve_action','action_receipts','planning_options'} <= {t.name for t in tools}
        result['checks']['typed_results'] = all(t.output_schema for t in tools)
        context = await session.call_tool('get_context', {'detail': 'summary', 'mark_read': False})
        result['checks']['authenticated_context'] = context.ok and context.data['you'] == 'Alice'
        own = await pool.get('linda').call_tool('get_context', {'detail': 'summary', 'mark_read': False})
        result['checks']['hidden_plan_filtered'] = own.ok and not any(p['id'] == 'party' for p in own.data.get('plan_index', []))
        shared = pool.get('alice', 'shared-probe', headers={'X-GroupFlow-Listeners': 'group:family'})
        room = await shared.call_tool('get_context', {'detail': 'summary', 'mark_read': False})
        result['checks']['listener_filter'] = room.ok and not any(p['id'] == 'party' for p in room.data.get('plan_index', []))
        result['checks']['no_consent_tool'] = not any(t.name in {'approve_plan','human_decide','confirm_for'} for t in tools)
        result['tool_count'] = len(tools)
        result['passed'] = all(result['checks'].values())
        result['calls'] = traffic.since(0)
        return result
    finally:
        await pool.close_all()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:18987/mcp')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    result = asyncio.run(probe(args.url))
    Path(args.out).write_text(json.dumps(result, indent=2, default=str), encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k!='calls'}, indent=2))
    raise SystemExit(0 if result['passed'] else 1)
