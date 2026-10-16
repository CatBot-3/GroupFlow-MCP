"""Over real MCP: telling people about a decision, the server's "want me to tell the others?" offer, follow-up questions kept by
the server, and the same question asked by the host itself through MCP elicitation when the server streams its responses."""
from __future__ import annotations

import httpx2
import mcp_types
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from groupflow.core.serve import serve_in_background
from groupflow.core.settings import ServerSettings
from groupflow.runtime import build_runtime
from tests.conftest import person
from tests.mcp.test_mcp import call, client_for, server  # noqa: F401 - the fixture is reused


@pytest.fixture
def streaming(tmp_path):
    settings = ServerSettings(db_path=str(tmp_path / "gf.db"), admin_key="k", allowed_origins=["http://localhost:8788"], mcp_responses="sse")
    rt = build_runtime(settings, worker_interval_s=0.05)
    bg = serve_in_background(rt.app)
    bg.rt = rt
    yield bg
    bg.stop()
    rt.db.close()


def eliciting_client(server, member: str, answer) -> Client:  # noqa: F811
    """A host that can show the person a question (MCP form elicitation) and returns what they picked."""
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {person(member).token}"})
    asked: list[dict] = []

    async def on_elicit(context, params):
        asked.append({"message": params.message, "schema": params.requested_schema})
        return answer(params)

    c = Client(streamable_http_client(f"{server.base}/mcp", http_client=http), mode="legacy", elicitation_callback=on_elicit)
    c.asked = asked  # type: ignore[attr-defined]
    return c


LATER = [{"label": "7 pm", "action": {"tool": "propose_change", "arguments": {"item": "party.date", "value": "Saturday, Oct 24, 7:00 pm",
                                                                                   "quote": "can we push it back a bit"}}},
         {"label": "7:30 pm", "action": {"tool": "propose_change", "arguments": {"item": "party.date", "value": "Saturday, Oct 24, 7:30 pm",
                                                                                     "quote": "can we push it back a bit"}}}]


async def test_a_decision_offers_to_tell_the_others_and_yes_tells_only_people_who_can_see_it(server):  # noqa: F811
    async with client_for(server, "alice") as c:
        changed = await call(c, "propose_change", {"item": "party.venue", "value": "Lucky Star karaoke", "quote": "let's do lucky star instead"})
        offer = changed["suggested_followup"]
        # Charlie's invitations task watches the venue, so the change already flagged it for him.
        assert changed["outcome"] == "confirmed" and offer["options"] == ["yes", "no"] and offer["question"].startswith("Want me to let Bob know")
        done = await call(c, "answer_followup", {"choice": "yeah do it", "followup": offer["id"]})
        assert done["action"]["ok"] and [r["name"] for r in done["action"]["result"]["recipients"]] == ["Bob"]
    async with client_for(server, "bob") as c:
        updates = (await call(c, "get_context", {"detail": "summary"}))["updates"]
        assert any("Lucky Star karaoke" in u and u.startswith("Alice asked Alexa") for u in updates)
    async with client_for(server, "linda") as c:
        ctx = await call(c, "get_context", {"detail": "summary", "mark_read": False})
        assert "karaoke" not in str(ctx).lower() and "surprise" not in str(ctx).lower()


async def test_notify_people_by_role_and_the_vague_time_guard(server):  # noqa: F811
    async with client_for(server, "alice") as c:
        sent = await call(c, "notify_people", {"plan": "home", "audience": ["everyone"], "note": "grabbing milk on the way home"})
        assert sent["outcome"] == "sent" and sorted(r["name"] for r in sent["recipients"]) == ["Bob", "Charlie", "Linda"]
        leak = await call(c, "notify_people", {"plan": "home", "note": "the surprise party is at Rosa's Trattoria"})
        assert leak["error"]["code"] == "PRIVATE_NOTE"
        vague = await call(c, "propose_change", {"item": "party.date", "value": "Saturday, Oct 24, 7:00 pm", "quote": "can we push it back a bit"})
        assert vague["error"]["code"] == "VAGUE_TIME"


async def test_without_elicitation_the_question_waits_on_the_server_for_the_spoken_answer(server):  # noqa: F811
    # JSON responses cannot carry a server request, so even a host that could show the question gets it back to ask in words.
    async with eliciting_client(server, "alice", lambda p: mcp_types.ElicitResult(action="accept", content={"choice": "7 pm"})) as c:
        asked = await call(c, "ask_followup", {"question": "Push it to 7 or 7:30?", "options": LATER})
        assert asked["outcome"] == "pending" and asked["options"] == ["7 pm", "7:30 pm"] and c.asked == []
    async with client_for(server, "alice") as c:  # a new session: the assistant's chat memory is gone
        ctx = await call(c, "get_context", {"detail": "summary", "mark_read": False})
        assert [q["id"] for q in ctx["open_questions"]] == [asked["id"]] and ctx["open_questions"][0]["plan"] == "Linda's surprise party"
        done = await call(c, "answer_followup", {"choice": "half seven, the 7:30 one"})
        assert done["choice"] == "7:30 pm" and done["action"]["result"]["outcome"] == "confirmed"


async def test_with_elicitation_the_host_asks_the_person_and_the_server_acts_on_their_pick(streaming):
    async with eliciting_client(streaming, "alice", lambda p: mcp_types.ElicitResult(action="accept", content={"choice": "7:30 pm"})) as c:
        done = await call(c, "ask_followup", {"question": "Push it to 7 or 7:30?", "options": LATER})
        assert c.asked[0]["message"] == "Push it to 7 or 7:30?"
        assert c.asked[0]["schema"]["properties"]["choice"]["enum"] == ["7 pm", "7:30 pm"]
        assert done["outcome"] == "answered" and done["asked_by_host"] and done["action"]["result"]["outcome"] == "confirmed"
        # The answered question is gone, and there is no offer: Bob's and Charlie's work watches the date, so they were flagged.
        ctx = await call(c, "get_context", {"plan": "party", "mark_read": False})
        assert ctx["open_questions"] == [] and sorted(done["action"]["result"]["impacts"]["notified"]) == ["Bob", "Charlie"]
    assert streaming.rt.services.engine._current_value(streaming.rt.services.engine._item("party.date")) == "Saturday, Oct 24, 7:30 pm"


async def test_a_declined_host_question_runs_nothing(streaming):
    engine = streaming.rt.services.engine
    before = engine._current_value(engine._item("party.date"))
    async with eliciting_client(streaming, "alice", lambda p: mcp_types.ElicitResult(action="decline")) as c:
        out = await call(c, "ask_followup", {"question": "Push it to 7 or 7:30?", "options": LATER})
        assert out["outcome"] == "declined"
    assert engine._current_value(engine._item("party.date")) == before
    # A host without elicitation on a streaming server still gets the question back to ask in words.
    async with client_for(streaming, "alice") as c:
        assert (await call(c, "ask_followup", {"question": "Push it to 7 or 7:30?", "options": LATER}))["outcome"] == "pending"
