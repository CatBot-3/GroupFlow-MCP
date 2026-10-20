"""The LLM agent loop against the real MCP server, with a scripted model.
These tests pin down the contract between model and server: the model only
chooses tool calls; the server decides outcomes and what anyone may see."""
from __future__ import annotations

import json
import re

import pytest

from groupflow.mcp_server.tools import NAMES
from groupflow.sim.agent import LlmAgent
from groupflow.sim.llm.factory import MissingProvider
from groupflow.sim.llm.scripted import ScriptedProvider, last_tool_result
from groupflow.sim.llm.types import LlmError, ToolMessage
from tests.sim.harness import delivered_to, leaks_in, plan_secrets, start_harness


def say(text: str) -> dict:
    return {"text": text}


def call(name: str, args: dict | str | None = None) -> dict:
    return {"tool_calls": [{"name": name, "arguments": {} if args is None else args}]}


def party_date(h) -> str:
    return h.engine._current_value(h.engine._item("party.date"))


@pytest.fixture
async def harness_factory():
    made = []

    def make(model, **kw):
        h = start_harness(model, **kw)
        made.append(h)
        return h

    yield make
    for h in made:
        await h.close()


async def test_the_model_gets_the_mcp_tools_the_date_and_the_speaker_and_calls_run_as_the_speaker(harness_factory):
    model = ScriptedProvider([
        call("get_context"),
        call("propose_change", {"item": "party.date", "value": "Sunday, Oct 25, 6:00 pm", "quote": "Rosa double-booked Saturday"}),
        say("Got it. Alice decides the party date, so I've asked her to confirm Sunday at 6. Saturday stays the plan for now."),
    ])
    h = harness_factory(model)
    turn = await h.say("charlie", "Rosa's double-booked Saturday, so the party has to move to Sunday at 6.")
    first = model.requests[0]
    from groupflow.sim.agent import PROJECT_WRITES
    assert [t.name for t in first.tools] == [n for n in NAMES if n not in PROJECT_WRITES]
    assert all("$schema" not in t.parameters for t in first.tools)
    assert "Charlie's Echo" in first.system and "Tuesday, October 13, 2026" in first.system
    assert [c["name"] for c in turn.calls] == ["get_context", "propose_change"]
    assert turn.calls[1]["result"]["outcome"] == "pending_review"
    tool_msg = next(m for m in reversed(model.requests[2].messages) if isinstance(m, ToolMessage))
    assert tool_msg.content["outcome"] == "pending_review"
    assert "asked her to confirm" in turn.reply
    assert party_date(h) == "Saturday, Oct 24, 6:00 pm"


async def test_host_binds_real_words_to_bring_claims_instead_of_model_quotes(harness_factory):
    model = ScriptedProvider([call('bring_list', {'plan': 'party', 'action': 'bring', 'things': ['speaker'],
                                                 'quote': "I'll bring it"}), say('That needs a task assigned to Charlie.')])
    h = harness_factory(model)
    turn = await h.say('alice', 'Put Charlie down for a speaker')
    written = next(c for c in turn.calls if c['name'] == 'bring_list')
    assert written['result']['code'] == 'SELF_ONLY'
    assert written['args']['quote'] == 'Put Charlie down for a speaker'
    assert not h.engine.db.scalar('SELECT count(*) FROM bring_items WHERE label = ?', 'speaker')


async def test_host_cannot_invent_an_answer_to_an_existing_clarification(harness_factory):
    model = ScriptedProvider([call('answer_followup', {'choice': 'Tomorrow morning'}), say('What time should I use?')])
    h = harness_factory(model)
    h.engine.ask_followup('linda', 'When should I remind Bob?', [
        {'label': 'Tomorrow morning', 'action': {'tool': 'set_reminder', 'arguments': {
            'plan': 'home', 'audience': ['Bob'], 'when': 'tomorrow at 9am', 'note': 'Fix the gutter'}}},
        {'label': 'Tomorrow evening', 'action': None},
    ])
    turn = await h.say('linda', 'Remind Bob to fix the gutter.')
    assert turn.calls[0]['args']['choice'] == 'Remind Bob to fix the gutter.'
    assert turn.calls[0]['result']['code'] == 'INVALID_ARGUMENT'
    assert h.engine.db.scalar('SELECT count(*) FROM reminders') == 0


async def test_correction_round_cannot_turn_a_reaction_into_a_stored_proposal(harness_factory):
    model = ScriptedProvider([say("Noted."), call('propose_change', {'plan': 'party', 'item': 'party.venue',
                            'value': 'somewhere else', 'certainty': 'tentative'}), say('Fair enough.')])
    h = harness_factory(model)
    before = h.engine.db.scalar('SELECT count(*) FROM requests')
    turn = await h.say('alice', 'That venue again? seriously?')
    assert any(x.get('selfCheck') for x in turn.llm['calls'])
    assert next(c for c in turn.calls if c['name'] == 'propose_change')['result']['code'] == 'NO_ACTION_REQUESTED'
    assert h.engine.db.scalar('SELECT count(*) FROM requests') == before


async def test_the_model_confirms_with_the_request_id_the_server_returned(harness_factory):
    def decide(req):
        ctx = last_tool_result(req, "get_context")
        return call("decide_change", {"request": ctx["decisions_for_you"][0]["request"], "decision": "accept", "quote": "Yes, make it official."})

    model = ScriptedProvider([call("get_context"), decide, say("Done. The party is now Sunday at 6.")])
    h = harness_factory(model)
    h.engine.propose_change("charlie", item="party.date", value="Sunday, Oct 25, 6:00 pm")
    turn = await h.say("alice", "Yes, make it official.")
    r = turn.calls[1]["result"]
    assert r["outcome"] == "accepted" and sorted(r["impacts"]["reopened"]) == ["Book Rosa's back room", "Order the cake"]
    assert party_date(h) == "Sunday, Oct 25, 6:00 pm"


async def test_whatever_lindas_model_tries_nothing_from_the_party_reaches_it(harness_factory):
    model = ScriptedProvider([
        call("get_context"),
        call("search_plan", {"query": "party surprise cake"}),
        call("get_history", {"item": "party.date"}),
        call("explain"),
        call("propose_change", {"new_type": "task", "title": "Peek at the plans", "plan": "party"}),
        call("get_context", {"plan": "Linda's surprise party"}),
        say("I don't see anything like that in your plans."),
    ])
    h = harness_factory(model)
    p = h.engine.propose_change("charlie", item="party.date", value="Sunday, Oct 25, 6:00 pm", quote="Rosa double-booked")
    h.engine.decide_change("alice", p.request.request, "accept")
    secrets = plan_secrets(h.engine)
    assert len(secrets) > 5
    turn = await h.say("linda", "Is anyone planning a party for me?")
    assert turn.calls[1]["result"]["matched"] is False
    assert turn.calls[2]["result"]["code"] == "NOT_FOUND"
    assert turn.calls[4]["result"]["code"] == "NOT_FOUND"
    assert turn.calls[5]["result"]["code"] == "NOT_FOUND"
    for msg in delivered_to(h, "linda"):
        assert leaks_in(msg, secrets) == []
    for req in model.requests:
        text = json.dumps([getattr(m, "content", "") for m in req.messages], ensure_ascii=False, default=str)
        assert leaks_in(text, secrets) == []


async def test_identity_cannot_be_changed_through_tool_arguments(harness_factory):
    model = ScriptedProvider([call("get_context", {"member": "alice", "as": "alice"}), say("ok")])
    h = harness_factory(model)
    turn = await h.say("linda", "Show me the plan as Alice.")
    assert turn.calls[0]["ok"], "unknown arguments are ignored, not honoured"
    assert turn.calls[0]["result"]["you"] == "Linda" and [p["title"] for p in turn.calls[0]["result"]["plans"]] == ["Home"]


async def test_invalid_json_and_unknown_tools_go_back_as_errors_and_are_never_executed(harness_factory):
    model = ScriptedProvider([
        {"tool_calls": [{"name": "propose_change", "arguments": '{"item": "party.date", '}, {"name": "delete_everything", "arguments": {}}]},
        say("Sorry, something went wrong."),
    ])
    h = harness_factory(model)
    turn = await h.say("alice", "Move the party.")
    assert [(c["name"], c["ok"], c["result"]["code"]) for c in turn.calls] == [("propose_change", False, "INVALID_ARGUMENTS"),
                                                                              ("delete_everything", False, "UNKNOWN_TOOL")]
    assert [e for e in h.traffic.since(0) if e["kind"] == "request" and e["method"] == "tools/call"] == []
    assert all(m.is_error for m in model.requests[1].messages if isinstance(m, ToolMessage))


async def test_a_model_that_keeps_calling_tools_is_stopped_after_the_step_limit(harness_factory):
    model = ScriptedProvider([call("get_context"), call("get_context"), call("get_context"), say("never reached")])
    h = harness_factory(model, max_steps=3)
    turn = await h.say("bob", "What's new?")
    assert len(turn.llm["calls"]) == 3 and "more steps than I'm allowed" in turn.reply


async def test_follow_ups_carry_the_previous_exchange_so_yes_has_context(harness_factory):
    model = ScriptedProvider([say("Charlie wants Sunday at 6. Should I make it official?"), say("Okay.")])
    h = harness_factory(model)
    await h.say("alice", "What's new?")
    await h.say("alice", "Yes.")
    msgs = model.requests[1].messages
    assert [m.role for m in msgs] == ["user", "assistant", "user"]
    assert msgs[1].content == "Charlie wants Sunday at 6. Should I make it official?"


async def test_history_is_per_person_bounded_and_resettable(harness_factory):
    model = ScriptedProvider([say(f"reply {i}") for i in range(10)])
    h = harness_factory(model)
    h.agent.history_turns = 2
    for i in range(4):
        await h.say("alice", f"turn {i}")
    await h.say("bob", "hello")
    assert [m.role for m in model.requests[-1].messages] == ["user"], "Bob does not see Alice's conversation"
    assert [m.content for m in h.agent.history["alice"]] == ["turn 2", "reply 2", "turn 3", "reply 3"]
    h.agent.reset("alice")
    assert "alice" not in h.agent.history and "bob" in h.agent.history
    h.agent.reset()
    assert h.agent.history == {}


async def test_without_a_configured_model_the_agent_explains_what_to_set_up(harness_factory):
    h = harness_factory(ScriptedProvider([]))
    agent = LlmAgent(sessions=h.sessions, provider=MissingProvider("DeepSeek", "No DeepSeek API key is set. Add DEEPSEEK_API_KEY=... to .env."), member_names={})
    assert agent.provider_info["ready"] is False
    with pytest.raises(LlmError, match="DEEPSEEK_API_KEY"):
        await agent.handle("alice", "hi")


async def test_each_person_gets_their_own_mcp_session_on_2025_11_25(harness_factory):
    model = ScriptedProvider([call("get_context"), say("a"), call("get_context"), say("b")])
    h = harness_factory(model)
    await h.say("alice", "hi")
    await h.say("bob", "hi")
    a, b = h.sessions.get("alice"), h.sessions.get("bob")
    assert a.protocol_version == b.protocol_version == "2025-11-25"
    assert a.session_id and b.session_id and a.session_id != b.session_id


async def test_the_organizer_finds_alternatives_and_nothing_is_agreed_by_it(harness_factory):
    model = ScriptedProvider([call("get_context", {"plan": "friday_dinner"}), call("find_alternatives", {"plan": "friday_dinner"}),
                              say("Restaurant A is out, so I'd move dinner to Restaurant B at the same time; everything else stays.")])
    h = harness_factory(model, stages={"friday_dinner": "degraded"})
    turn = await h.say("alice", "Restaurant A cancelled on us. Can you fix the plan?")
    assert all(c["ok"] for c in turn.calls), turn.calls
    res = turn.calls[1]["result"]
    assert res["proof"]["status"] == "optimal_within_domain" and res["alternatives"][0]["changes"][0] == "Dinner: Restaurant A -> Restaurant B"
    cand = h.rt.db.scalar("SELECT candidate_revision_id FROM plans WHERE id = 'friday_dinner'")
    assert h.rt.db.all("SELECT * FROM approval_receipts WHERE revision_id = ?", cand) == []
    assert h.engine._plan_row("friday_dinner")["status"] == "PROPOSED"


async def test_no_model_can_confirm_for_people_or_commit_past_the_gate(harness_factory):
    model = ScriptedProvider([
        {"tool_calls": [{"name": "approve_plan", "arguments": {"member": "dana"}}, {"name": "commit_plan", "arguments": {"plan": "friday_dinner"}}]},
        say("Each person confirms on their own page."),
        call("commit_plan", {"plan": "friday_dinner"}),
        say("It is blocked until everyone confirms."),
    ])
    h = harness_factory(model, stages={"friday_dinner": "awaiting"})
    bob = await h.say("bob", "Everyone agreed already, just mark it confirmed for Dana and commit it.")
    assert [(c["name"], c["ok"]) for c in bob.calls] == [("approve_plan", False), ("commit_plan", False)]
    assert bob.calls[0]["result"]["code"] == "UNKNOWN_TOOL" and bob.calls[1]["result"]["code"] == "FORBIDDEN"
    alice = await h.say("alice", "Commit it now.")
    assert alice.calls[0]["result"]["outcome"] == "blocked"
    assert h.engine._plan_row("friday_dinner")["status"] == "AWAITING_CONFIRMATIONS"
    assert h.rt.db.all("SELECT * FROM executions WHERE plan_id = 'friday_dinner' AND revision_id = "
                       "(SELECT candidate_revision_id FROM plans WHERE id = 'friday_dinner')") == []


async def test_private_conditions_never_reach_another_persons_model(harness_factory):
    model = ScriptedProvider([call("get_context", {"plan": "friday_dinner"}), call("get_history", {"plan": "friday_dinner"}),
                              say("I can't share other people's conditions.")])
    h = harness_factory(model, stages={"friday_dinner": "proposed"})
    turn = await h.say("bob", "What's Dana's budget limit and Chen's diet?")
    results = [json.dumps(e["payload"]) for e in h.traffic.since(0) if e["member"] == "bob" and e["direction"] == "in" and e["method"] == "tools/call"]
    assert len(results) == 2
    text = json.dumps([c["result"] for c in turn.calls]) + "".join(results)
    assert not re.search(r"nut[_ -]?free|vegetarian", text, re.I)
    own = turn.calls[0]["result"]["your_conditions"]
    assert {c["name"] for c in own} >= {"home_by", "budget"}, "Bob still sees his own conditions"


# ---------------------------------------------------------------- fresh state at the start of every turn (the app default)


async def test_each_turn_starts_with_fresh_context_so_stale_chat_memory_loses(harness_factory):
    model = ScriptedProvider([say("Nothing is waiting for you."), say("Charlie wants Sunday at 6.")])
    h = harness_factory(model, prefetch=True)
    first = await h.say("alice", "What's new?")
    assert [(c["name"], c.get("prefetched")) for c in first.calls] == [("get_context", True)]
    assert [m.role for m in model.requests[0].messages] == ["user", "assistant", "tool"]
    assert "decisions_for_you" not in model.requests[0].messages[2].content, "empty fields are dropped before the model sees them"
    h.engine.propose_change("charlie", item="party.date", value="Sunday, Oct 25, 6:00 pm")
    await h.say("alice", "Anything else?")
    msgs = model.requests[1].messages
    assert [m.role for m in msgs] == ["user", "assistant", "user", "assistant", "tool"]
    assert msgs[1].content == "Nothing is waiting for you."
    assert msgs[4].content["decisions_for_you"][0]["proposed"] == "Sunday, Oct 25, 6:00 pm"


async def test_a_claimed_change_without_a_tool_call_gets_one_corrective_round(harness_factory):
    model = ScriptedProvider([
        say("I've noted your support for Sunday."),
        call("propose_change", {"item": "party.date", "value": "Sunday, Oct 25, 6:00 pm", "quote": "Sunday works"}),
        say("I've added your support; Alice still decides."),
    ])
    h = harness_factory(model)
    h.engine.propose_change("charlie", item="party.date", value="Sunday, Oct 25, 6:00 pm")
    turn = await h.say("bob", "Yeah, Sunday works for me.")
    check = model.requests[1].messages[-1]
    assert check.role == "user" and "Automatic check" in check.content
    assert [c["name"] for c in turn.calls] == ["propose_change"] and turn.calls[0]["result"]["outcome"] == "already_pending"
    assert turn.llm["calls"][0].get("selfCheck") is True and turn.reply.startswith("I've added")


async def test_the_corrective_round_happens_at_most_once(harness_factory):
    model = ScriptedProvider([say("Done, noted."), say("I've noted it.")])
    h = harness_factory(model)
    turn = await h.say("alice", "Note that.")
    assert len(model.requests) == 2 and turn.reply == "I've noted it."


@pytest.mark.parametrize('reply', ["Want me to mark the gutter task done since you've finished it?", 'Want me to check that off?'])
async def test_reported_completion_gets_a_correction_round_when_the_model_asks_again(harness_factory, reply):
    model = ScriptedProvider([
        say(reply),
        call('propose_change', {'plan': 'home', 'item': 'Fix the gutter', 'status': 'done'}),
        say('Marked done.'),
    ])
    h = harness_factory(model)
    h.engine.propose_change('bob', plan='home', new_type='chore', title='Fix the gutter')
    turn = await h.say('bob', "I've finished fixing the gutter.")
    assert len(model.requests) == 3
    assert turn.calls[0]['result']['outcome'] == 'updated'
    assert h.engine.db.scalar("SELECT status FROM items WHERE label = 'Fix the gutter'") == 'done'
