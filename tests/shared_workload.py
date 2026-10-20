"""Reusable synthetic world and real transport helpers; no expected-outcome oracle."""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from groupflow.core.db import Db
from groupflow.core.identity import Person, seed_people
from groupflow.core.serve import BackgroundServer, serve_in_background
from groupflow.engine import GroupFlow
from groupflow.engine.worker import Worker
from groupflow.mcp_server.app import Services, create_server_app
from groupflow.world import approve_all

PEOPLE = tuple(Person(name, name.capitalize(), f'shared-test-{name}-token', f'shared-test-{name}-pass', '#335577')
               for name in ('avery', 'blair', 'casey', 'devon', 'ellis', 'quinn', 'morgan'))
MARKERS = {plan: f'{plan.upper()}_SCOPE_SENTINEL_X7Q' for plan in ('dinner', 'trip', 'surprise', 'release')}
GROUP_SOCIAL = 'commons'
GROUP_WORK = 'work'
ADMIN_KEY = 'synthetic-shared-workload-admin'


@dataclass
class SharedWorld:
    engine: GroupFlow
    items: dict[str, dict[str, str]]
    people: dict[str, Person]


def _item_directory(engine: GroupFlow) -> dict[str, dict[str, str]]:
    result = {}
    for plan in ('dinner', 'trip', 'surprise', 'release'):
        rows = engine.db.all('SELECT * FROM items WHERE plan_id=? AND archived=0 ORDER BY rowid', plan)
        entry = {}
        for row in rows:
            if row['shape'] == 'activity':
                entry['activity' if row['label'] == 'Meet' else 'second_activity'] = row['id']
            elif row['label'] == 'Prepare':
                entry['work_a' if plan == 'release' else 'task'] = row['id']
            elif plan == 'release' and row['label'] == 'Review':
                entry['work_b'] = row['id']
        result[plan] = entry
    return result


def build_world(db_path: str | Path, seed: bool = True) -> SharedWorld:
    engine = GroupFlow(Db(str(db_path)))
    try:
        if seed:
            if engine.db.scalar('SELECT count(*) FROM members'):
                raise ValueError('A fresh shared-workload database is required for seeding.')
            seed_people(engine.db, PEOPLE)
            engine.add_group(GROUP_SOCIAL, 'Harbour circle', 'America/Toronto', 'CAD', [
                {'id': p, 'role': 'organizer' if p == 'morgan' else 'member'}
                for p in ('avery', 'blair', 'casey', 'devon', 'ellis', 'morgan')])
            engine.add_group(GROUP_WORK, 'Workshop', 'America/Toronto', 'CAD', [
                {'id': p, 'role': 'organizer' if p == 'devon' else 'member'}
                for p in ('avery', 'blair', 'devon', 'ellis')])
            engine.human_workspace_role(engine.identity.get('devon'), GROUP_WORK, 'devon', 'lead', True)
            engine.human_workspace_role(engine.identity.get('devon'), GROUP_WORK, 'ellis', 'approver', True)
            engine.create_plan('avery', 'evening_out', 'Dinner ' + MARKERS['dinner'], group=GROUP_SOCIAL,
                               audience=['avery', 'blair', 'casey'], start_date='2026-11-07', starter=False, plan_id='dinner')
            catalog = {'version': 'synthetic-harbour-1', 'default_travel': 0,
                       'travel_minutes': [['harbour', 'harbour', 0]], 'places': [
                           {'id': 'harbour_a', 'name': 'Harbour Room A', 'kind': 'meal', 'zone': 'harbour',
                            'open': '08:00', 'close': '23:00', 'capacity': 8, 'price': 12,
                            'tags': ['step_free', 'vegetarian', 'nut_free']},
                           {'id': 'harbour_b', 'name': 'Harbour Room B', 'kind': 'meal', 'zone': 'harbour',
                            'open': '08:00', 'close': '23:00', 'capacity': 8, 'price': 12,
                            'tags': ['step_free', 'vegetarian', 'nut_free']}]}
            engine.db.run('UPDATE plans SET catalog_json=? WHERE id=?', json.dumps(catalog), 'dinner')
            engine.propose_change('avery', plan='dinner', new_type='stop', title='Meet', start='18:00', end='19:00',
                                  place='harbour_a', participants=['everyone'])
            engine.propose_change('avery', plan='dinner', new_type='stop', title='Dessert', start='20:00', end='20:30',
                                  place='harbour_b', participants=['everyone'])
            engine.propose_change('avery', plan='dinner', new_type='task', title='Prepare', owner='blair')
            engine.request_confirmations('avery', plan='dinner')
            for actor in ('avery', 'blair', 'casey'):
                approve_all(engine, engine.identity.get(actor), 'dinner')
            engine.commit_plan('avery', plan='dinner')

            engine.create_plan('blair', 'general_event', 'Trip ' + MARKERS['trip'], group=GROUP_SOCIAL,
                               audience=['avery', 'blair', 'devon'], start_date='2026-11-08', starter=False, plan_id='trip')
            engine.propose_change('blair', plan='trip', new_type='activity', title='Meet', start='10:00', end='11:00',
                                  place='Bayside Park', participants=['everyone'])
            engine.propose_change('blair', plan='trip', new_type='task', title='Prepare', owner='devon')

            engine.create_plan('casey', 'shared_tasks', 'Surprise ' + MARKERS['surprise'], group=GROUP_SOCIAL,
                               audience=['blair', 'casey', 'ellis'], start_date='2026-11-08', starter=False, plan_id='surprise')
            engine.propose_change('casey', plan='surprise', new_type='task', title='Prepare', owner='ellis')

            engine.create_plan('devon', 'project', 'Release ' + MARKERS['release'], group=GROUP_WORK,
                               audience=['avery', 'blair', 'devon', 'ellis'], start_date='2026-11-09', days=14,
                               starter=False, plan_id='release')
            engine.configure_project('devon', 'release', {'approvers': ['ellis'],
                'resources': [{'id': 'work.bench', 'name': 'Bench', 'capacity': 1}]})
            engine.upsert_work('devon', 'release', {'duration_minutes': 120, 'resources': {'work.bench': 1}}, title='Prepare', owner='avery')
            engine.upsert_work('devon', 'release', {'duration_minutes': 60}, title='Review', owner='blair')
            engine.reset_rate_limits()  # Only setup; measured work uses the production limiter.
        return SharedWorld(engine, _item_directory(engine), {p.id: p for p in PEOPLE})
    except BaseException:
        engine.db.close()
        raise


@dataclass
class SharedServer:
    background: BackgroundServer
    world: SharedWorld
    app: Any
    worker: Worker | None
    db_path: str
    trace: list[dict] = field(default_factory=list)

    @property
    def base(self):
        return self.background.base

    @property
    def engine(self):
        return self.world.engine

    @property
    def people(self):
        return self.world.people

    @property
    def items(self):
        return self.world.items

    def stop(self):
        self.background.stop()
        self.engine.db.close()


def start_shared_server(db_path: str | Path, seed: bool = True, worker: bool = True) -> SharedServer:
    world = build_world(db_path, seed)
    services = Services(world.engine.db, world.engine.identity, world.engine)
    background_worker = Worker(world.engine, interval_s=0.05) if worker else None
    app = create_server_app(services, admin_key=ADMIN_KEY, worker=background_worker)
    try:
        background = serve_in_background(app)
    except BaseException:
        world.engine.db.close()
        raise
    return SharedServer(background, world, app, background_worker, str(db_path))


@pytest.fixture
def shared_server(tmp_path):
    server = start_shared_server(tmp_path / 'shared-workload.sqlite3')
    try:
        yield server
    finally:
        server.stop()


_sessions: dict[int, tuple[SharedServer, str, str | None]] = {}


@asynccontextmanager
async def connect(server, actor: str, listeners: str | None = None):
    headers = {'authorization': 'Bearer ' + server.people[actor].token}
    if listeners:
        headers['x-groupflow-listeners'] = listeners
    async with httpx2.AsyncClient(headers=headers,timeout=30) as http:
        async with Client(streamable_http_client(server.base + '/mcp', http_client=http), mode='legacy') as client:
            _sessions[id(client)] = (server, actor, listeners)
            error = None
            try:
                yield client
            except BaseException as caught:
                # Close SDK task groups normally before re-raising the original
                # test failure; eight nested sessions must not hide its leaf.
                error = caught
            finally:
                _sessions.pop(id(client), None)
        if error is not None:
            raise error


async def call(client, tool: str, arguments: dict | None = None) -> dict:
    began = time.perf_counter_ns()
    result = await client.call_tool(tool, arguments or {})
    value = ({'error': json.loads(result.content[0].text)['error']} if result.is_error else result.structured_content)
    ended = time.perf_counter_ns()
    context = _sessions.get(id(client))
    if context:
        server, actor, listeners = context
        server.trace.append({'actor': actor, 'listeners': listeners, 'tool': tool, 'arguments': arguments or {},
                             'result': value, 'began_ns': began, 'ended_ns': ended, 'ms': (ended-began)/1e6})
    return value


async def guard(client, tool: str, arguments: dict, key: str, expected: str | None = None):
    if expected is None and arguments.get('plan'):
        resolved = await call(client, 'resolve_action', {'query': '', 'plan': arguments['plan']})
        if 'error' in resolved:
            return resolved
        expected = resolved['version']
    return await call(client, 'action_receipts', {'action': 'apply', 'tool': tool, 'arguments': arguments,
                                               'request_key': key, 'expected': expected})


class SharedPhone:
    def __init__(self, server, actor: str, http, headers):
        self.server, self.actor, self.http, self.headers = server, actor, http, headers

    async def post(self, path: str, body: dict):
        return await self.http.post(path, headers=self.headers, json=body)

    async def inbox(self):
        response = await self.http.get('/human/inbox', headers=self.headers)
        response.raise_for_status()
        return response.json()

    async def approve(self, plan: str):
        results = []
        for item in (await self.inbox())['pending']:
            if item['plan_id'] != plan:
                continue
            challenge = await self.post('/human/challenges', {'scope_id': item['scope_id']})
            challenge.raise_for_status()
            results.append(await self.post('/human/decisions', {**challenge.json(), 'decision': 'approved'}))
        return results

    async def approve_project(self, plan: str):
        inbox = await self.http.get('/human/projects/inbox', headers=self.headers)
        inbox.raise_for_status()
        results = []
        for item in inbox.json()['items']:
            if item['plan_id'] != plan or item['status'] in ('approved', 'carried_forward'):
                continue
            challenge = await self.post('/human/projects/challenges', {'scope_id': item['id']})
            challenge.raise_for_status()
            results.append(await self.post('/human/projects/decisions', {**challenge.json(), 'decision': 'approve'}))
        return results


@asynccontextmanager
async def phone(server, actor: str):
    async with httpx2.AsyncClient(base_url=server.base,timeout=30) as http:
        login = await http.post('/human/login', json={'member_id': actor, 'passcode': server.people[actor].passcode})
        login.raise_for_status()
        value = login.json()
        yield SharedPhone(server, actor, http, {'authorization': 'Bearer ' + value['token'], 'x-gf-csrf': value['csrf']})


async def run_wave(operations: dict[str, Awaitable]) -> dict[str, Any]:
    start = asyncio.Event()
    async def perform(name, operation):
        await start.wait()
        return name, await operation
    tasks = [asyncio.create_task(perform(name, operation)) for name, operation in operations.items()]
    start.set()
    return dict(await asyncio.gather(*tasks))
