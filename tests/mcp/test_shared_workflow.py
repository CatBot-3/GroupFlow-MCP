"""Complete overlapping workflows on one server/file, using real MCP and human HTTP."""
from __future__ import annotations

import asyncio
import json
import math
import os
import time
import uuid
from contextlib import AsyncExitStack
from pathlib import Path

import httpx2

from tests.shared_workload import ADMIN_KEY, MARKERS, call, connect, guard, phone, run_wave, shared_server  # noqa: F401


def assert_ok(result):
    assert 'error' not in result, result
    return result


def record_evidence(server, elapsed, checks):
    destination = os.environ.get('SHARED_WORKLOAD_EVIDENCE_DIR')
    if not destination:
        return
    out = Path(destination)
    out.mkdir(parents=True, exist_ok=True)
    times = sorted(entry['ms'] for entry in server.trace)
    def percentile(p):
        return times[max(0, math.ceil(len(times)*p)-1)] if times else None
    payload = {'kind':'shared-server-tool-workflow', 'sessions':8, 'database_count':1, 'workflow_count':4,
               'external_model_used':False, 'elapsed_s':elapsed, 'requests':len(times),
               'latency_ms':{'p50':percentile(.5),'p95':percentile(.95),'max':max(times,default=0)},
               'checks':checks,'trace':server.trace}
    (out / ('workflow-'+uuid.uuid4().hex[:12]+'.json')).write_text(json.dumps(payload,indent=2)+'\n',encoding='utf8')


async def test_four_complete_workflows_share_eight_sessions_and_one_database(shared_server):
    server=shared_server
    began=time.perf_counter()
    clients={}
    checks={}
    try:
        async with AsyncExitStack() as stack:
            for actor in ('avery','blair','casey','devon','ellis','quinn'):
                clients[actor]=await stack.enter_async_context(connect(server,actor))
            clients['avery_room']=await stack.enter_async_context(connect(server,'avery','avery,casey'))
            clients['blair_room']=await stack.enter_async_context(connect(server,'blair','blair,avery'))
            phones={actor:await stack.enter_async_context(phone(server,actor)) for actor in ('avery','blair','casey','devon','ellis')}

            contexts=await run_wave({name:call(client,'get_context',{'detail':'summary','mark_read':False}) for name,client in clients.items()})
            visible={'avery':{'dinner','trip','release'},'blair':{'dinner','trip','surprise','release'},
                     'casey':{'dinner','surprise'},'devon':{'trip','release'},'ellis':{'surprise','release'},
                     'quinn':set(),'avery_room':{'dinner'},'blair_room':{'dinner','trip','release'}}
            for name,context in contexts.items():
                assert_ok(context)
                assert {p['id'] for p in context['plan_index']}==visible[name], (name,context)
                for plan,marker in MARKERS.items():
                    assert (marker in json.dumps(context)) == (plan in visible[name]), (name,plan)
            checks['initial_visibility_matrix']=True

            async def repair_dinner():
                async with httpx2.AsyncClient(base_url=server.base) as http:
                    change=await http.post('/debug/observations',headers={'x-groupflow-admin':ADMIN_KEY},json={
                        'group_id':'commons','place_id':'harbour_a','start':'17:00','end':'20:00',
                        'date':'2026-11-07','source_event_id':'shared-dinner-closure'})
                    assert change.status_code==200,change.text
                return await call(clients['avery'],'find_alternatives',{'plan':'dinner'})
            phase=await run_wave({
                'dinner_repair':repair_dinner(),
                'trip_confirmation':call(clients['blair'],'request_confirmations',{'plan':'trip'}),
                'surprise_task':call(clients['casey'],'propose_change',{'plan':'surprise','new_type':'task','title':'Hidden decorations','owner':'casey'}),
                'surprise_owner':call(clients['ellis'],'propose_change',{'plan':'surprise','item':server.items['surprise']['task'],'status':'done'}),
                'release_schedule':call(clients['devon'],'repair_project',{'plan':'release'}),
                'outsider':call(clients['quinn'],'get_context',{'plan':'surprise','mark_read':False}),
                'narrowed_read':call(clients['avery_room'],'get_context',{'detail':'summary','mark_read':False}),
                'room_receipts':call(clients['blair_room'],'action_receipts',{'action':'list'}),
            })
            assert phase['outsider']['error']['code']=='NOT_FOUND'
            for name in ('dinner_repair','trip_confirmation','surprise_task','surprise_owner','release_schedule','narrowed_read','room_receipts'):
                assert_ok(phase[name])
            assert phase['dinner_repair']['alternatives'],phase['dinner_repair']
            assert phase['release_schedule']['outcome']=='proposed',phase['release_schedule']
            for name,plan in (('dinner_repair','dinner'),('release_schedule','release'),
                              ('surprise_task','surprise'),('surprise_owner','surprise')):
                assert phase[name]['action_receipt']['plans']==[plan],phase[name]
            assert all(effect.startswith('repair.') for effect in phase['dinner_repair']['action_receipt']['effects'])
            assert phase['release_schedule']['action_receipt']['effects']==['project.repaired']
            assert MARKERS['surprise'] not in json.dumps(phase['room_receipts'])
            checks['overlapping_distinct_workflows']=True

            prepared=await run_wave({
                'dinner':call(clients['avery'],'request_confirmations',{'plan':'dinner'}),
                'trip':call(clients['blair'],'request_confirmations',{'plan':'trip'}),
                'release':call(clients['devon'],'request_confirmations',{'plan':'release'}),
                'wrong_organizer':call(clients['avery'],'commit_plan',{'plan':'trip'}),
                'wrong_approver':phones['avery'].post('/human/workspace/roles',{'group':'work','target':'avery','role':'approver','grant':True}),
            })
            for plan in ('dinner','trip','release'):
                assert_ok(prepared[plan])
            assert prepared['wrong_organizer']['error']['code']=='FORBIDDEN'
            assert prepared['wrong_approver'].status_code==403
            assert server.engine.db.scalar("SELECT count(*) FROM workspace_roles WHERE group_id='work' AND member_id='avery' AND role='approver'")==0
            checks['roles_are_plan_specific']=True

            answers=await run_wave({
                'avery_dinner':phones['avery'].approve('dinner'),'avery_trip':phones['avery'].approve('trip'),
                'avery_work':phones['avery'].approve_project('release'),
                'blair_dinner':phones['blair'].approve('dinner'),'blair_trip':phones['blair'].approve('trip'),
                'blair_work':phones['blair'].approve_project('release'),
                'casey_dinner':phones['casey'].approve('dinner'),'devon_trip':phones['devon'].approve('trip'),
                'ellis_stage':phones['ellis'].approve_project('release'),
            })
            for responses in answers.values():
                for response in responses:
                    assert response.status_code==200,response.text

            commits=await run_wave({
                'dinner':call(clients['avery'],'commit_plan',{'plan':'dinner'}),
                'trip':call(clients['blair'],'commit_plan',{'plan':'trip'}),
                'release':call(clients['devon'],'commit_plan',{'plan':'release'}),
                'surprise':call(clients['casey'],'propose_change',{'plan':'surprise','new_type':'task','title':'Hidden ribbon','owner':'casey'}),
                'no_consent_tool':call(clients['quinn'],'action_receipts',{'action':'apply','tool':'human_decide','arguments':{},'request_key':'forbidden-consent'}),
            })
            for plan in ('dinner','trip','release'):
                assert assert_ok(commits[plan])['outcome']=='committed',(plan,commits[plan])
            assert commits['no_consent_tool']['error']['code']=='INVALID_ARGUMENT'
            checks['separate_human_consent_and_commits']=True

            token=(await call(clients['blair'],'resolve_action',{'query':'','plan':'trip'}))['version']
            expense={'plan':'trip','action':'add','amount':'12.34','what':'Shared test fuel'}
            ledger=await run_wave({
                'one':guard(clients['blair'],'costs',expense,'shared-fuel',token),
                'retry':guard(clients['blair'],'costs',expense,'shared-fuel',token),
                'hidden_work':call(clients['ellis'],'get_context',{'plan':'surprise','mark_read':False}),
            })
            assert assert_ok(ledger['one'])['action_receipt']['id']==assert_ok(ledger['retry'])['action_receipt']['id']
            assert server.engine.db.scalar("SELECT count(*) FROM expenses WHERE plan_id='trip' AND description='Shared test fuel'")==1
            assert server.engine.db.scalar("SELECT sum(amount_minor) FROM expenses WHERE plan_id='trip'")==1234
            assert server.engine.db.scalar("SELECT count(*) FROM executions WHERE plan_id='dinner' AND status='succeeded' AND simulated=1")==2
            checks['exact_retry_and_ledgers']=True

            for name,client in clients.items():
                view=await call(client,'get_context',{'detail':'summary','mark_read':False})
                for plan,marker in MARKERS.items():
                    if plan not in visible[name]:
                        assert marker not in json.dumps(view),(name,plan)
            checks['final_visibility_matrix']=True
            assert server.engine.db.scalar("SELECT owner_id FROM items WHERE id=?",server.items['trip']['task'])=='devon'
            assert server.engine.db.scalar("SELECT status FROM items WHERE id=?",server.items['surprise']['task'])=='done'
    finally:
        record_evidence(server,time.perf_counter()-began,checks)
