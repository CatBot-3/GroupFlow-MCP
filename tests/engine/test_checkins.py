"""The worker's check-in pass and its reviewers: the server's rules by default, or a language model whose answer is used only within
what the server allows, and never needed for the check-in to happen (a failing model falls back to the rules)."""
from __future__ import annotations

import json

from groupflow.checkins import CheckIns, ModelReviewer, RulesReviewer, build_checkins, parse_decision
from groupflow.engine import fixed_clock
from groupflow.sim.llm.scripted import ScriptedProvider
from tests.engine.test_momentum import received, world

LATER = "2026-10-18T15:00:00Z"  # a day after Friday dinner was sent out, mid-morning in Toronto


def test_a_model_answer_is_read_only_when_it_is_a_usable_object():
    assert parse_decision('Sure! ```json\n{"stuck": true, "reason": "x", "nudges": []}\n```') == {"stuck": True, "reason": "x", "nudges": []}
    assert parse_decision('{"stuck": false, "reason": "fine"}')["stuck"] is False
    assert parse_decision('{"stuck": true}')["nudges"] is None  # stuck, but no choice of people: the rules choose
    for bad in ("", "no idea", '{"stuck": "yes"}', "[1, 2]", '{"reason": "x"}'):
        assert parse_decision(bad) is None, bad


async def test_the_rules_pass_reminds_the_people_a_plan_waits_on_and_only_in_the_chosen_group():
    gf = world({"friday_dinner": "awaiting"})
    gf.set_clock(fixed_clock(LATER))
    checkins = CheckIns(gf, RulesReviewer())
    assert await checkins.run_pass(group_id="family") == []
    [round_] = await checkins.run_pass(group_id="friends")
    assert round_["plan"] == "Friday dinner" and round_["reviewer"] == "rules" and sorted(s["name"] for s in round_["sent"]) == ["Alice", "Bob", "Chen", "Dana"]
    assert await checkins.run_pass(group_id="friends") == []  # this round is done


async def test_skipping_ahead_runs_one_more_round_per_press():
    # The demo console's "skip ahead" asks as of a later time but records each round at the real time, so real activity afterwards
    # still counts, and pressing again plays the next quiet stretch: two reminders, then the organizer is told, then nothing.
    gf = world({"friday_dinner": "awaiting"})
    checkins = CheckIns(gf, RulesReviewer())
    presses = [await checkins.run_pass(as_of=LATER, quiet_hours=False, group_id="friends") for _ in range(4)]
    assert [[s["name"] for s in p[0]["sent"]] if p else [] for p in presses] == [["Alice", "Bob", "Chen", "Dana"]] * 2 + [["Alice"], []]
    assert "Bob, Chen and Dana have not answered after 2 reminders" in presses[2][0]["sent"][0]["text"]


async def test_a_model_reviewer_sees_the_plans_facts_and_its_choices_are_applied():
    gf = world({"friday_dinner": "awaiting"})
    answer = {"stuck": True, "reason": "four answers missing, dinner in five days",
              "nudges": [{"to": "dana", "text": "Hi Dana! Are you in for Friday dinner? Yes or no on your GroupFlow page, either helps."}]}
    model = ScriptedProvider([{"text": json.dumps(answer)}])
    [round_] = await CheckIns(gf, ModelReviewer(model)).run_pass(as_of=LATER, group_id="friends")
    facts = json.loads(model.requests[0].messages[0].content)
    assert facts["plan"] == "Friday dinner" and sorted(c["id"] for c in facts["candidates"]) == ["alice", "bob", "chen", "dana"]
    assert facts["blockers"][0]["waiting_on"] == ["Alice", "Bob", "Chen", "Dana"] and model.requests[0].tools == []
    assert round_["reviewer"] == "model" and round_["reason"] == answer["reason"]
    # Dana gets the model's words; Alice, organizer and also still to answer, was not chosen, so she gets the summary.
    assert [(s["name"], s["source"]) for s in round_["sent"]] == [("Dana", "model"), ("Alice", "rules")]
    assert received(gf, "dana", "nudge") == [answer["nudges"][0]["text"]] and received(gf, "bob", "nudge") == []


async def test_a_failing_or_rambling_model_never_stops_the_check_in():
    for steps in ([], [{"text": "I think the plan is probably fine but who knows."}]):
        gf = world({"friday_dinner": "awaiting"})
        [round_] = await CheckIns(gf, ModelReviewer(ScriptedProvider(steps))).run_pass(as_of=LATER, group_id="friends")
        assert round_["reviewer"] == "rules" and len(round_["sent"]) == 4


def test_check_ins_can_be_switched_off_and_a_missing_model_falls_back_to_the_rules(monkeypatch):
    gf = world()
    assert build_checkins(gf, "off") is None
    assert isinstance(build_checkins(gf, "rules").reviewer, RulesReviewer)
    monkeypatch.setenv("AGENT_PROVIDER", "deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert isinstance(build_checkins(gf, "model").reviewer, RulesReviewer)
