import httpx
import pytest

from tests.conftest import person
from tests.mcp.test_mcp import server, client_for, call  # noqa: F401 - shared real HTTP fixture


async def test_project_workflows_keep_top_level_mcp_contract_and_human_boundary(server):
    async with client_for(server, 'priya') as lead:
        created=await call(lead,'create_plan',{'template':'project','title':'MCP release','group':'team','start_date':'2026-10-19','days':42})
        pid=created['plan']['id']
        item=await call(lead,'upsert_work',{'plan':pid,'title':'Build API','owner':'marcus','spec':{'duration_minutes':120,'workstream':'Engineering'}})
        assert item['outcome']=='created'
        repair=await call(lead,'repair_project',{'plan':pid})
        assert repair['proof']['backend']=='cp_sat_intervals'
        sent=await call(lead,'request_confirmations',{'plan':pid})
        assert sent['outcome']=='requested' and len(sent['confirmations'])==2
        assert (await call(lead,'commit_plan',{'plan':pid}))['outcome']=='blocked'
        async with httpx.AsyncClient(base_url=server.base) as http:
            assert (await http.get('/human/projects/inbox',headers={'Authorization':f'Bearer {person("priya").token}'})).status_code==401
            for member in ('priya','marcus'):
                login=(await http.post('/human/login',json={'member_id':member,'passcode':person(member).passcode})).json()
                headers={'Authorization':f'Bearer {login["token"]}','X-GF-CSRF':login['csrf']}
                inbox=(await http.get('/human/projects/inbox',headers=headers)).json()
                scope=next(s for s in inbox['items'] if s['plan_id']==pid)
                no_csrf=await http.post('/human/projects/challenges',json={'scope_id':scope['id']},headers={'Authorization':headers['Authorization']})
                assert no_csrf.status_code==403
                challenge=(await http.post('/human/projects/challenges',json={'scope_id':scope['id']},headers=headers)).json()
                answer=await http.post('/human/projects/decisions',headers=headers,json={'challenge_id':challenge['challenge_id'],'nonce':challenge['nonce'],'decision':'approve'})
                assert answer.status_code==200,answer.text
                replay=await http.post('/human/projects/decisions',headers=headers,json={'challenge_id':challenge['challenge_id'],'nonce':challenge['nonce'],'decision':'approve'})
                assert replay.status_code==409
        assert (await call(lead,'commit_plan',{'plan':pid}))['outcome']=='committed'
    async with client_for(server,'jun') as member:
        result=await call(member,'upsert_work',{'plan':pid,'title':'Unauthorized assignment','owner':'sofia','spec':{}})
        assert result['error']['code']=='FORBIDDEN'
    async with client_for(server,'alice') as outsider:
        assert (await call(outsider,'get_project',{'plan':pid}))['error']['code']=='NOT_FOUND'


def test_enterprise_demo_load_reset_and_scoped_flow():
    from groupflow.runtime import build_runtime
    from groupflow.core.settings import ServerSettings
    from groupflow.core.identity import Member
    rt=build_runtime(ServerSettings(db_path=':memory:',world_path='demo/enterprise.yaml'))
    try:
        engine=rt.services.engine
        p=engine.get_project('ava','release')
        assert p['total']==18 and p['confirmation_totals']['required']==19
        assert len(p['workstreams'])==4
        for person_id in ('ava','ben','mei','lee'):
            member=Member(person_id,engine.name(person_id))
            for scope in engine.human_project_inbox(member)['items']:
                ch=engine.human_project_challenge(member,scope['id'])
                engine.human_project_decide(member,ch['challenge_id'],ch['nonce'],'approve')
        assert engine.commit_project('ava','release')['outcome']=='committed'
        rt.services.on_reset('enterprise')
        assert engine.get_project('ava','release')['confirmation_totals']=={'required':19,'satisfied':0}
    finally:
        rt.db.close()
