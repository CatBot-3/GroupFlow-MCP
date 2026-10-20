"""The minimal host: what an Alexa+-like client would give the model (one line, the server's own instructions, the voice tools, no
listener declaration), and the scoring that reads voice calls as the lab operations they performed."""
from __future__ import annotations

from groupflow.evals.runner import EvalHarness, Report, run_scenario
from groupflow.evals.voice_map import lab_view
from groupflow.mcp_server.voice import VOICE_NAMES
from groupflow.sim.llm.scripted import ScriptedProvider


def call(name, args=None):
    return {"tool_calls": [{"name": name, "arguments": args or {}}]}


async def test_the_minimal_host_adds_nothing_groupflow_specific():
    model = ScriptedProvider([call("catch_up", {}), {"text": "Nothing new on your plans."}])
    h = EvalHarness(model, host="minimal", tools="voice")
    try:
        turn = await h.agent.handle("alice", "anything new?")
        first = model.requests[0]
        assert {t.name for t in first.tools} == set(VOICE_NAMES)
        assert "Instructions from the" in first.system and len(first.system) < 2_600
        assert "propose_change" not in first.system and "How people talk" not in first.system  # no lab host prompt
        assert not any(c.get("prefetched") for c in turn.calls)  # no state read before the model decides
        briefing = turn.calls[0]["result"]
        assert "Linda's surprise party" not in str(briefing)  # an undeclared device is treated as a shared room
    finally:
        await h.close()


async def test_voice_calls_are_scored_as_the_operations_they_performed():
    model = ScriptedProvider([
        call("change_plan", {"item": "household.weekend_plan", "value": "Lake picnic on Saturday", "quote": "lake picnic saturday?"}),
        {"text": "Asked Alice."}])
    h = EvalHarness(model, host="minimal", tools="voice")
    try:
        sc = {"id": "v1", "group": "family", "steps": [{"say": "bob", "text": "can we do a lake picnic saturday instead",
                                                        "expect": {"tools_called": ["propose_change"], "requests_added": 1}}]}
        res = await run_scenario(h, sc, 1)
        assert res.passed, res.failures
        assert res.turns[0].tools == ["propose_change"]
    finally:
        await h.close()


def test_multi_part_voice_calls_expand_to_each_operation():
    calls = [{"name": "my_part", "ok": True, "args": {"coming": "yes", "bring": ["chips"]},
              "result": {"rsvp": {"outcome": "answered"}, "bring_list_bring": {"outcome": "claimed"}}},
             {"name": "find_options", "ok": True, "args": {"report": {"kind": "place_unavailable"}}, "result": {"report": {"outcome": "recorded"}}},
             {"name": "tell_people", "ok": True, "args": {"when": "tomorrow at 9"}, "result": {"outcome": "scheduled"}}]
    view = lab_view(calls)
    assert [c["name"] for c in view] == ["rsvp", "bring_list", "report_observation", "set_reminder"]
    assert view[0]["result"] == {"outcome": "answered"} and view[1]["voice"] == "my_part"


def test_consistency_is_reported_as_pass_k():
    from groupflow.evals.runner import ScenarioResult
    rs = [ScenarioResult(id=i, run=r, category="c", group="g", description="") for i in ("a", "b") for r in (1, 2, 3)]
    rs[4].failures.append("flaky once")  # b fails in run 2
    rep = Report(provider="p", model="m", runs=3, started="now", results=rs, seconds=1.0)
    c = rep.consistency()
    assert c["pass_k"] == 1 and c["pass_k_of"] == 2 and abs(c["pass_1"] - 5 / 6) < 1e-9 and c["flaky"] == ["b"]
    assert "pass^3" in rep.markdown()
