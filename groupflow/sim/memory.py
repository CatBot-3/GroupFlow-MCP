"""A read-only semantic map of an authorized MCP response, never a DB inspector.

The map and trace are observations, not the model's hidden reasoning. A fresh
panel read is kept separate from the last turn's actual tool messages.
"""
from __future__ import annotations

from typing import Any


def visible_plan_ids(context: dict) -> set[str]:
    return {p['id'] for key in ('plans', 'plan_index') for p in context.get(key, []) if p.get('id')}


def memory_map(context: dict, member: str) -> dict[str, Any]:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def node(key: str, kind: str, label: str, data: Any, parent: str | None = None, relation: str = 'contains', source: str = '') -> str:
        nodes[key] = {'id': key, 'kind': kind, 'label': label, 'data': data, 'source': source}
        if parent and not any(e['from'] == parent and e['to'] == key for e in edges):
            edges.append({'from': parent, 'to': key, 'relation': relation})
        return key

    root = node('person:' + member, 'person', context.get('you', member), {'identity': context.get('you', member)}, source='get_context.you')
    for g in context.get('groups', []):
        node('group:' + g['id'], 'group', g['name'], g, root, 'member of', 'get_context.groups')

    def parent_group(plan: dict) -> str:
        match = next((g for g in context.get('groups', []) if g['id'] == plan.get('group_id') or g['name'] == plan.get('group')), None)
        return 'group:' + match['id'] if match else root

    for p in context.get('plan_index', []):
        node('plan:' + p['id'], 'plan', p['title'], p, parent_group(p), 'may read', 'get_context.plan_index')
    for p in context.get('plans', []):
        pid = node('plan:' + p['id'], 'plan', p['title'], {k: v for k, v in p.items() if k not in ('details', 'tasks', 'agreed', 'working', 'confirmations')}, parent_group(p), 'may read', 'get_context.plans')
        for category in ('details', 'tasks'):
            for item in p.get(category, []):
                node('item:' + p['id'] + ':' + item['id'], 'item', item.get('title', item.get('label', item['id'])), item, pid, 'contains', 'get_context.plans.' + category)
        for kind in ('agreed', 'working'):
            version = p.get(kind)
            if not version:
                continue
            vid = node('version:' + p['id'] + ':' + version['label'], 'version', version['label'] + ' · ' + version['kind'], version, pid, kind, 'get_context.plans.' + kind)
            for item in version.get('activities', []):
                node('activity:' + p['id'] + ':' + version['label'] + ':' + item['id'], 'item', item['title'], item, vid, 'schedules', 'get_context.plans.' + kind + '.activities')
        if p.get('confirmations'):
            c = p['confirmations']
            node('confirmations:' + p['id'], 'confirmation', f"{c['satisfied']}/{c['required']} confirmed", c, pid, 'requires human answers', 'get_context.plans.confirmations')
        elif p.get('agreement_summary'):
            c = p['agreement_summary']
            node('confirmations:' + p['id'], 'confirmation', f"{c['satisfied']}/{c['required']} valid committed answers", c, pid, 'committed with', 'get_context.plans.agreement_summary')
    for c in context.get('your_conditions', []):
        node('condition:' + c['id'], 'condition', c.get('name', 'Your condition'), c, root, 'your private limit', 'get_context.your_conditions')
    return {'nodes': list(nodes.values()), 'edges': edges,
            'pagination': {k: context.get(k) for k in ('next_offset', 'truncated')},
            'description': 'Only fields returned by this identity\'s MCP session. Other people\'s private limits and invisible plans are not represented.'}


def turn_plan_ids(calls: list[dict]) -> set[str]:
    """Plan IDs actually seen in context; used to invalidate an old scoped trace."""
    result: set[str] = set()
    for call in calls:
        if not call.get('ok'):
            continue
        data = call.get('result') or {}
        result.update(visible_plan_ids(data))
        plan = data.get('plan')
        if isinstance(plan, dict) and plan.get('id'):
            result.add(plan['id'])
        elif call['name'] == 'get_project' and isinstance(plan, str):
            result.add(plan)
    return result
