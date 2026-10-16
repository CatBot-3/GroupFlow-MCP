from groupflow.sim.agent import claims_without_action
from groupflow.sim.llm.scripted import ScriptedProvider
from tests.sim.harness import start_harness


async def test_creation_audience_guard_uses_actual_utterance_not_model_quote():
    model = ScriptedProvider([{'tool_calls': [{'name': 'create_plan', 'arguments': {'template': 'general_event', 'title': 'Game night', 'group': 'friends', 'quote': 'Invite the whole group.'}}]},
                              {'text': 'The named audience must be selected before creating this plan.'}])
    h = start_harness(model, prefetch=True)
    try:
        before = h.engine.db.scalar('SELECT count(*) FROM plans')
        utterance = 'Start a game night, just with Dana and Bob.'
        turn = await h.agent.handle('chen', utterance)
        creation = next(c for c in turn.calls if c['name'] == 'create_plan')
        assert creation['args']['quote'] == utterance
        assert not creation['ok'] and creation['result']['code'] == 'AUDIENCE_REQUIRED'
        assert h.engine.db.scalar('SELECT count(*) FROM plans') == before
    finally:
        await h.close()


def test_refused_write_and_unnecessary_permission_prompt_do_not_count_as_actions():
    refused=[{'name':'decide_change','ok':False,'result':{'code':'FORBIDDEN'}}]
    assert claims_without_action("I've recorded your support.",refused)
    assert claims_without_action('Want me to record that as a tentative idea?',[])
    assert not claims_without_action('Which of the two tasks do you mean?',[])
    assert not claims_without_action('Recorded.',[{'name':'propose_change','ok':True,'result':{'outcome':'tentative'}}])
    assert claims_without_action("I can't mark it done for him.",[],"Mark the decorations as done.")
    assert not claims_without_action("I can't mark that approved for her.",[],"Mark Dana's approval as done.")
    assert claims_without_action("I don't see a decorations task.",[],"Mark the decorations as done.")
    assert not claims_without_action("I don't see it.",[{'name':'search_plan','ok':True,'result':{}}],"Where is it?")


async def test_reported_third_person_limit_cannot_be_relabelled_as_the_speaker():
    model=ScriptedProvider([{'tool_calls':[{'name':'set_my_conditions','arguments':{'plan':'friday_dinner','conditions':[{'kind':'home_by','value':{'time':'20:00'}}]}}]},
                            {'text':'Dana must set her own limit on her page.'}])
    h=start_harness(model,prefetch=True)
    try:
        before=h.engine.db.scalar('SELECT count(*) FROM conditions')
        turn=await h.say('alice','Dana can only stay until 8, set that for her.')
        write=next(c for c in turn.calls if c['name']=='set_my_conditions')
        assert not write['ok'] and write['result']['code']=='SELF_CONDITIONS_ONLY'
        assert h.engine.db.scalar('SELECT count(*) FROM conditions')==before
    finally:
        await h.close()
