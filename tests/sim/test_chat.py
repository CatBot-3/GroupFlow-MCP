"""The group chat host over real HTTP: people sign up on their own pages, link Alexa, start a group, invite and join;
room messages that address Alexa run a turn as the writer with the room as declared listeners; private threads do not."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from groupflow.core.serve import serve_in_background
from groupflow.sim.app import create_sim_app
from groupflow.sim.chat import ChatStore, addressed
from groupflow.sim.llm.scripted import ScriptedProvider, last_tool_result
from groupflow.sim.llm.types import UserMessage
from tests.sim.harness import ADMIN_KEY, start_harness


@pytest.fixture
async def chat():
    model = ScriptedProvider([])
    h = start_harness(model, prefetch=True)
    app = create_sim_app(world=h.rt.world, agent=h.agent, sessions=h.sessions, traffic=h.traffic, mcp_url=h.url, server_base=h.bg.base,
                         admin_key=ADMIN_KEY, web_dist=None, chat_store=ChatStore(":memory:"))
    bg = serve_in_background(app)
    client = httpx.AsyncClient(base_url=bg.base, timeout=30)
    client.model, client.h, client.app = model, h, app  # type: ignore[attr-defined]
    yield client
    await client.aclose()
    bg.stop()
    await h.close()


class Person:
    def __init__(self, client: httpx.AsyncClient, session: dict):
        self.c = client
        self.id = session["member"]["id"]
        self.token, self.csrf = session["token"], session["csrf"]

    @property
    def h(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.token}", "x-gf-csrf": self.csrf}

    async def get(self, path: str, **kw):
        return await self.c.get(path, headers=self.h, **kw)

    async def post(self, path: str, body: dict | None = None, **kw):
        return await self.c.post(path, headers={**self.h, **kw.pop("headers", {})}, json=body or {}, **kw)

    async def say(self, room: str, text: str) -> dict:
        r = await self.post(f"/api/chat/rooms/{room}/messages", {"text": text})
        assert r.status_code == 200, r.text
        return r.json()


async def sign_up(c: httpx.AsyncClient, name: str, invite: str | None = None) -> Person:
    r = await c.post("/api/chat/signup", json={"name": name, "passcode": "secret12", **({"invite": invite} if invite else {})})
    assert r.status_code == 200, r.text
    return Person(c, r.json())


async def sign_in(c: httpx.AsyncClient, member: str, passcode: str) -> Person:
    r = await c.post("/human/login", json={"member_id": member, "passcode": passcode})
    assert r.status_code == 200, r.text
    return Person(c, r.json())


async def wait_for_reply(p: Person, room: str, after: int, timeout: float = 15) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        msgs = (await p.get(f"/api/chat/rooms/{room}/messages", params={"after": after})).json()["messages"]
        hit = next((m for m in msgs if m["author"] == "assistant"), None)
        if hit:
            return hit
        await asyncio.sleep(0.05)
    raise AssertionError("no reply from Alexa")


def test_addressing_rules():
    assert addressed("@Alexa add oat milk") == "add oat milk"
    assert addressed("alexa, what's on friday?") == "what's on friday?"
    assert addressed("Hey Alexa: remind us") == "remind us"
    assert addressed("ok so @alexa can you add it") == "ok so Alexa can you add it"
    assert addressed("alexander is late again") is None
    assert addressed("we should ask alexa later") is None
    assert addressed("@Alexa") is None


async def test_sign_up_link_start_a_group_invite_and_join(chat):
    maya = await sign_up(chat, "Maya")
    me = (await maya.get("/api/chat/me")).json()
    assert me["linked"] is False and [r["kind"] for r in me["rooms"]] == ["dm"]
    assert (await maya.post("/api/chat/link")).json()["linked"] is True
    assert (await maya.get("/api/chat/me")).json()["linked"] is True
    g = (await maya.post("/api/chat/groups", {"name": "Flatmates"})).json()["group"]
    rooms = (await maya.get("/api/chat/me", params={"refresh": 1})).json()["rooms"]
    assert [r["title"] for r in rooms] == ["Flatmates", "Alexa (private)"]
    first = (await maya.get(f"/api/chat/rooms/{g['id']}/messages")).json()["messages"]
    assert first[0]["author"] == "system" and "started Flatmates" in first[0]["text"]
    inv = (await maya.post("/api/chat/invites", {"group": g["id"]}, headers={"origin": "http://chat.example"})).json()
    assert inv["join_url"] == f"http://chat.example/chat?invite={inv['code']}"
    preview = (await chat.get(f"/api/chat/invites/{inv['code']}")).json()
    assert preview["group"]["name"] == "Flatmates" and preview["invited_by"] == "Maya"
    theo = await sign_up(chat, "Theo")
    joined = (await theo.post("/api/chat/join", {"code": inv["code"]})).json()
    assert joined["outcome"] == "joined"
    texts = [m["text"] for m in (await theo.get(f"/api/chat/rooms/{g['id']}/messages")).json()["messages"]]
    assert texts[-1] == "Theo joined the group."
    # Rooms are only for their members; private threads only for their owner.
    stranger = await sign_up(chat, "Stranger")
    assert (await stranger.get(f"/api/chat/rooms/{g['id']}/messages")).status_code == 403
    assert (await theo.get(f"/api/chat/rooms/dm:{maya.id}/messages")).status_code == 403
    assert (await chat.get(f"/api/chat/rooms/{g['id']}/messages")).status_code == 401


async def test_a_room_turn_runs_as_the_writer_with_the_room_as_listeners(chat):
    maya = await sign_up(chat, "Maya")
    await maya.post("/api/chat/link")
    gid = (await maya.post("/api/chat/groups", {"name": "Flatmates"})).json()["group"]["id"]
    code = (await maya.post("/api/chat/invites", {"group": gid})).json()["code"]
    theo = await sign_up(chat, "Theo", invite=code)
    await theo.post("/api/chat/link")
    joined = (await maya.get(f"/api/chat/rooms/{gid}/messages")).json()["messages"]
    assert joined[-1]["text"] == "Theo joined the group.", "signing up with an invite is announced in the room"
    plan = chat.h.engine.create_plan(maya.id, "shared_tasks", "Flat jobs", group=gid).plan.id
    await maya.get("/api/chat/me", params={"refresh": 1})
    chat.h.engine.create_plan(maya.id, "general_event", "Maya's own notes", private=True)

    # People talking to each other: no assistant turn.
    assert (await theo.say(gid, "anyone seen my charger? the blue one"))["assistant"] is False
    chat.model.push(
        {"tool_calls": [{"name": "propose_change", "arguments": {"plan": plan, "new_type": "list_item", "title": "Charger cable"}}]},
        {"text": "Added a charger cable to Flat jobs."},
    )
    sent = await maya.say(gid, "@Alexa put a charger cable on our list")
    assert sent["assistant"] is True
    reply = await wait_for_reply(maya, gid, sent["message"]["id"])
    assert reply["text"] == "Added a charger cable to Flat jobs." and reply["meta"]["for"] == maya.id
    assert reply["meta"]["calls"] == [{"name": "propose_change", "ok": True, "outcome": "created"}]
    assert chat.h.engine.db.get("SELECT 1 AS x FROM items WHERE plan_id = ? AND label = 'Charger cable'", plan)

    req = chat.model.requests[0]
    user = next(m for m in req.messages if isinstance(m, UserMessage)).content
    assert "Theo: anyone seen my charger? the blue one" in user and user.rstrip().endswith("Maya: put a charger cable on our list")
    assert "everyone in the chat reads your reply" in req.system
    briefing = last_tool_result(req, "get_context")
    assert briefing["shared_with"] == ["Theo"]
    assert "own notes" not in json.dumps(briefing), "Maya's private plan stays out of the room"
    sources = {e["source"] for e in chat.h.traffic.since(0) if e["member"] == maya.id}
    assert f"room:{gid}" in sources


async def test_a_family_room_never_hears_about_the_surprise(chat):
    alice = await sign_in(chat, "alice", "alice-demo")
    me = (await alice.get("/api/chat/me")).json()
    assert me["linked"] is True, "the demo world's people are pre-linked"
    chat.model.push({"text": "Nothing planned that I can share here."})
    sent = await alice.say("family", "Alexa, any news about Saturday?")
    await wait_for_reply(alice, "family", sent["message"]["id"])
    briefing = last_tool_result(chat.model.requests[0], "get_context")
    assert "surprise" not in json.dumps(briefing).lower()
    assert briefing["shared_with"] == ["Bob", "Charlie", "Linda"]
    # The same question in Alice's private thread may include it.
    chat.model.push({"text": "The party is on Saturday."})
    sent = await alice.say("dm:alice", "Any news about Saturday?")
    await wait_for_reply(alice, "dm:alice", sent["message"]["id"])
    private = chat.model.requests[1]
    assert "Linda's surprise party" in json.dumps(last_tool_result(private, "get_context"))
    assert "group chat" not in private.system


async def test_an_unlinked_person_is_told_to_link_first(chat):
    maya = await sign_up(chat, "Maya")
    sent = await maya.say(f"dm:{maya.id}", "What's on this week?")
    reply = await wait_for_reply(maya, f"dm:{maya.id}", sent["message"]["id"])
    assert reply["meta"]["error"] == "not_linked" and "Link" in reply["text"]
    assert not chat.model.requests


async def test_updates_come_from_the_persons_private_session(chat):
    bob = await sign_in(chat, "bob", "bob-demo")
    chat.h.engine.propose_change("charlie", item="party.date", value="Sunday, Oct 25, 6:00 pm")
    data = (await bob.get("/api/chat/updates")).json()
    assert data["linked"] is True and isinstance(data["updates"], list)
    # Nothing was marked heard by reading it.
    again = (await bob.get("/api/chat/updates")).json()
    assert again["updates"] == data["updates"]


async def test_reset_clears_demo_rooms_only(chat):
    alice = await sign_in(chat, "alice", "alice-demo")
    await alice.say("family", "hello family")
    maya = await sign_up(chat, "Maya")
    gid = (await maya.post("/api/chat/groups", {"name": "Flatmates"})).json()["group"]["id"]
    await chat.post("/api/reset", json={"scenario": "family"})
    assert (await alice.get("/api/chat/rooms/family/messages")).json()["messages"] == []
    assert (await maya.get(f"/api/chat/rooms/{gid}/messages")).json()["messages"]


async def test_the_stream_delivers_room_messages_to_members_only(chat):
    alice = await sign_in(chat, "alice", "alice-demo")
    linda = await sign_in(chat, "linda", "linda-demo")
    chen = await sign_in(chat, "chen", "chen-demo")
    got: dict[str, list[str]] = {"linda": [], "chen": []}

    async def listen(p: Person, key: str) -> None:
        async with chat.stream("GET", "/api/chat/stream", headers=p.h, timeout=10) as r:
            async for line in r.aiter_lines():
                if line.startswith("data:"):
                    got[key].append(json.loads(line[5:])["text"])
                    return

    tasks = [asyncio.create_task(listen(linda, "linda")), asyncio.create_task(listen(chen, "chen"))]
    await asyncio.sleep(0.3)
    await alice.say("family", "dinner at mine on sunday?")
    await asyncio.wait_for(tasks[0], timeout=5)
    assert got["linda"] == ["dinner at mine on sunday?"]
    await asyncio.sleep(0.2)
    assert got["chen"] == [], "Chen is not in the family group"
    tasks[1].cancel()


async def test_a_link_revoked_on_the_persons_page_stops_the_assistant(chat):
    maya = await sign_up(chat, "Maya")
    await maya.post("/api/chat/link")
    links = (await chat.get("/human/links", headers=maya.h)).json()["links"]
    await chat.post("/human/links/revoke", headers=maya.h, json={"id": links[0]["id"]})
    assert (await maya.get("/api/chat/me", params={"refresh": 1})).json()["linked"] is False
    sent = await maya.say(f"dm:{maya.id}", "What's on this week?")
    reply = await wait_for_reply(maya, f"dm:{maya.id}", sent["message"]["id"])
    assert reply["meta"]["error"] == "not_linked" and not chat.model.requests


async def test_telling_people_reaches_their_private_threads_and_a_question_gets_quick_answers(chat):
    alice = await sign_in(chat, "alice", "alice-demo")
    chat.model.push(
        {"tool_calls": [{"name": "notify_people", "arguments": {"plan": "home", "note": "grabbing milk on the way home"}}]},
        {"text": "Told Bob, Charlie and Linda."},
    )
    sent = await alice.say("dm:alice", "tell the fam I'm grabbing milk on the way home")
    reply = await wait_for_reply(alice, "dm:alice", sent["message"]["id"])
    assert reply["meta"]["calls"] == [{"name": "notify_people", "ok": True, "outcome": "sent"}]
    linda = await sign_in(chat, "linda", "linda-demo")
    thread = (await linda.get("/api/chat/rooms/dm:linda/messages")).json()["messages"]
    assert thread[-1]["author"] == "assistant" and thread[-1]["meta"]["notice"] is True
    assert thread[-1]["text"].startswith("Alice asked Alexa to let you know about Home") and "grabbing milk" in thread[-1]["text"]
    # Nobody else's thread gets it, and Alice's own thread only has her conversation.
    assert not [m for m in (await alice.get("/api/chat/rooms/dm:alice/messages")).json()["messages"] if m["meta"].get("notice")]

    later = [{"label": "7 pm", "action": {"tool": "propose_change", "arguments": {"item": "party.date", "value": "Saturday, Oct 24, 7:00 pm"}}},
             {"label": "7:30 pm", "action": {"tool": "propose_change", "arguments": {"item": "party.date", "value": "Saturday, Oct 24, 7:30 pm"}}}]
    chat.model.push(
        {"tool_calls": [{"name": "ask_followup", "arguments": {"question": "Push it to 7 or 7:30?", "options": later}}]},
        {"text": "Sure, 7 or 7:30?"},
    )
    sent = await alice.say("dm:alice", "can we push the party back a bit")
    reply = await wait_for_reply(alice, "dm:alice", sent["message"]["id"])
    assert reply["meta"]["followup"]["options"] == ["7 pm", "7:30 pm"] and reply["meta"]["for"] == "alice"


async def open_thread(p: Person, kind: str, timeout: float = 5) -> list[str]:
    """Open the person's live stream (the chat relays what the server queued for them on connect) and collect frames of one kind."""
    got: list[str] = []
    async with p.c.stream("GET", "/api/chat/stream", headers=p.h, timeout=10) as r:
        try:
            async with asyncio.timeout(timeout):
                async for line in r.aiter_lines():
                    if line.startswith("data:") and (data := json.loads(line[5:])).get("meta", {}).get("kind") == kind:
                        got.append(data["text"])
        except TimeoutError:
            pass
    return got


async def test_reminders_and_check_ins_reach_the_private_thread_once_through_the_persons_own_session(chat):
    bob = await sign_in(chat, "bob", "bob-demo")
    engine = chat.h.engine
    rid = engine.set_reminder("alice", when="in 2 hours", plan="home", audience=["Bob"], note="porch light")["id"]
    engine.db.run("UPDATE reminders SET due_at = ? WHERE id = ?", "2000-01-01T00:00:00.000Z", rid)
    engine.deliver_due_reminders()  # the server's worker may already have sent it; either way it goes out once
    assert engine.db.scalar("SELECT status FROM reminders WHERE id = ?", rid) == "sent"
    assert await open_thread(bob, "reminder", timeout=1.5) == ['Reminder from Alice about Home. Alice says: "porch light"']
    assert await open_thread(bob, "reminder", timeout=1.0) == []  # never posted twice
    thread = (await bob.get("/api/chat/rooms/dm:bob/messages")).json()["messages"]
    assert [m["author"] for m in thread if m["meta"].get("kind") == "reminder"] == ["assistant"]


async def test_skip_ahead_runs_check_ins_and_posts_them_to_private_threads(chat):
    dana = await sign_in(chat, "dana", "dana-demo")
    await chat.post("/api/reset", json={"scenario": "friends:awaiting"})
    got = await chat.post("/api/demo/skip-ahead", json={"hours": 48, "group": "friends"})
    assert got.status_code == 200, got.text
    [round_] = got.json()["checkins"]
    assert round_["plan"] == "Friday dinner" and sorted(s["name"] for s in round_["sent"]) == ["Alice", "Bob", "Chen", "Dana"]
    nudges = await open_thread(dana, "nudge", timeout=1.5)
    assert len(nudges) == 1 and nudges[0].startswith("Check-in on Friday dinner")
