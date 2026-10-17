"""The group chat: a second host for the same MCP server, one that real people can use.

People sign in on their own GroupFlow page (the human-only API), so the chat never keeps a
passcode. Each group is a room, and each person also has a private thread with Alexa. A room
message that addresses Alexa ("@Alexa ..." or "Alexa, ...") runs one assistant turn as the person
who wrote it, over that person's own MCP session, with the room's recent messages as context and
the room's members declared as listeners, so the server leaves out anything one of them may not
see. Confirmations never happen in a message: the chat shows each person their own pending
answers as cards, and the tap goes to the human-only API with that person's own session.

The assistant acts for a person only after they link it on their own page (simulated account
linking: the server issues this host a revocable token). The demo world's people are pre-linked.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .agent import ChatContext, LlmAgent
from .llm.types import LlmError
from .mcp_pool import SessionPool

log = logging.getLogger("groupflow.chat")

ASSISTANT = "assistant"
SYSTEM = "system"
HOST_NAME = "GroupFlow chat"
RECENT = 12  # earlier room messages given to the model as context
MAX_TEXT = 2000
ME_TTL_S = 15.0
# What the server queues for a person with nobody in a conversation, posted into their private thread. Other kinds (a version to
# confirm, a plan committed) already reach them as updates and to-dos.
RELAYED_KINDS = ("reminder", "nudge", "notice")
RELAY_EVERY_S = 10.0

# "@Alexa ...", "Alexa, ...", "hey Alexa: ..." at the start; or "@alexa" anywhere in the message.
_LEADING = re.compile(r"^\s*(?:@alexa\b[\s,:]*|(?:hey\s+|ok\s+)?alexa\s*[,:]\s*)", re.I)
_ANYWHERE = re.compile(r"@alexa\b", re.I)

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  room TEXT NOT NULL,
  author TEXT NOT NULL,
  author_name TEXT NOT NULL,
  text TEXT NOT NULL,
  at TEXT NOT NULL,
  meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS chat_messages_room ON chat_messages(room, id);
CREATE TABLE IF NOT EXISTS chat_links (
  member_id TEXT PRIMARY KEY,
  token TEXT NOT NULL,
  link_id TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_relayed (
  member_id TEXT NOT NULL,
  notification_id TEXT NOT NULL,
  PRIMARY KEY (member_id, notification_id)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def addressed(text: str) -> str | None:
    """The request in a room message that addresses Alexa, or None for ordinary chat between people."""
    m = _LEADING.match(text)
    if m:
        rest = text[m.end():].strip()
        return rest or None
    if _ANYWHERE.search(text):
        return _ANYWHERE.sub("Alexa", text).strip()
    return None


def notices_sent(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """notify_people results in a turn, run directly or as the answer to a follow-up."""
    out = []
    for c in calls:
        res = c.get("result") or {}
        if not c.get("ok"):
            continue
        if c["name"] == "answer_followup":
            action = res.get("action") or {}
            res = (action.get("result") or {}) if action.get("ok") and action.get("tool") == "notify_people" else {}
        elif c["name"] != "notify_people":
            continue
        if res.get("recipients"):
            out.append(res)
    return out


def asked_followup(calls: list[dict[str, Any]], reply: str) -> dict[str, Any] | None:
    """The question this reply asks, when the server holds it: quick-reply buttons for the person who was asked."""
    for c in reversed(calls):
        res = c.get("result") or {}
        if c["name"] == "ask_followup" and c.get("ok") and res.get("outcome") == "pending":
            return {"id": res["id"], "question": res["question"], "options": res["options"]}
    if "?" not in reply:
        return None
    for c in reversed(calls):
        res = c.get("result") or {}
        # A write's offer, an offer from the change a follow-up answer ran, or an error's "which plan?" question.
        nested = ((res.get("action") or {}).get("result") or {}) if c.get("ok") else {}
        offer = (res.get("suggested_followup") or nested.get("suggested_followup")) if c.get("ok") else (res.get("details") or {}).get("followup")
        if offer and offer.get("options"):
            return {"id": offer["id"], "question": offer["question"], "options": offer["options"]}
    return None


class ChatStore:
    """The host's own data: messages and the tokens people linked to this host. Not GroupFlow state."""

    def __init__(self, path: str = ":memory:"):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.raw = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.raw.row_factory = lambda c, r: {d[0]: v for d, v in zip(c.description, r)}
        self.lock = threading.Lock()
        with self.lock:
            self.raw.executescript(SCHEMA)

    def _out(self, row: dict[str, Any]) -> dict[str, Any]:
        return {**row, "meta": json.loads(row["meta"] or "{}")}

    def add(self, room: str, author: str, author_name: str, text: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
        with self.lock:
            cur = self.raw.execute("INSERT INTO chat_messages (room, author, author_name, text, at, meta) VALUES (?, ?, ?, ?, ?, ?)",
                                   (room, author, author_name, text, _now(), json.dumps(meta or {})))
            row = self.raw.execute("SELECT * FROM chat_messages WHERE id = ?", (cur.lastrowid,)).fetchone()
        return self._out(row)

    def since(self, room: str, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.raw.execute("SELECT * FROM chat_messages WHERE room = ? AND id > ? ORDER BY id LIMIT ?", (room, after, limit)).fetchall()
        return [self._out(r) for r in rows]

    def latest(self, room: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.raw.execute("SELECT * FROM chat_messages WHERE room = ? ORDER BY id DESC LIMIT ?", (room, limit)).fetchall()
        return [self._out(r) for r in reversed(rows)]

    def before(self, room: str, before_id: int, limit: int) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.raw.execute("SELECT * FROM chat_messages WHERE room = ? AND id < ? ORDER BY id DESC LIMIT ?", (room, before_id, limit)).fetchall()
        return [self._out(r) for r in reversed(rows)]

    def clear(self, rooms: list[str]) -> None:
        with self.lock:
            for room in rooms:
                self.raw.execute("DELETE FROM chat_messages WHERE room = ?", (room,))

    def save_link(self, member_id: str, token: str, link_id: str) -> None:
        with self.lock:
            self.raw.execute("INSERT OR REPLACE INTO chat_links (member_id, token, link_id, created_at) VALUES (?, ?, ?, ?)",
                             (member_id, token, link_id, _now()))

    def link_id(self, member_id: str) -> str | None:
        with self.lock:
            row = self.raw.execute("SELECT link_id FROM chat_links WHERE member_id = ?", (member_id,)).fetchone()
        return row["link_id"] if row else None

    def links(self) -> dict[str, str]:
        with self.lock:
            return {r["member_id"]: r["token"] for r in self.raw.execute("SELECT * FROM chat_links").fetchall()}

    def claim(self, member_id: str, notification_ids: list[str]) -> list[str]:
        """The notifications not yet posted to this person's private thread, now marked as posted (several tabs never post twice)."""
        fresh = []
        with self.lock:
            for nid in dict.fromkeys(notification_ids):
                cur = self.raw.execute("INSERT OR IGNORE INTO chat_relayed (member_id, notification_id) VALUES (?, ?)", (member_id, nid))
                if cur.rowcount:
                    fresh.append(nid)
        return fresh

    def close(self) -> None:
        with self.lock:
            self.raw.close()


@dataclass
class Me:
    """A signed-in person as the GroupFlow server describes them (GET /human/me)."""
    id: str
    name: str
    color: str | None
    groups: list[dict[str, Any]]
    links: list[dict[str, Any]]

    @property
    def rooms(self) -> set[str]:
        return {g["id"] for g in self.groups} | {f"dm:{self.id}"}

    def group(self, room: str) -> dict[str, Any] | None:
        return next((g for g in self.groups if g["id"] == room), None)


@dataclass(eq=False)
class Stream:
    member: str
    rooms: set[str]
    token: str = ""
    queue: asyncio.Queue[str] = field(default_factory=asyncio.Queue)


class ChatError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class ChatService:
    def __init__(self, store: ChatStore, agent: LlmAgent, sessions: SessionPool, server: Callable[[], httpx.AsyncClient],
                 assistant_name: str = "Alexa", host_name: str = HOST_NAME):
        self.store = store
        self.agent = agent
        self.sessions = sessions
        self.server = server
        self.assistant_name = assistant_name
        self.host_name = host_name
        self.streams: set[Stream] = set()
        self.room_locks: dict[str, asyncio.Lock] = {}
        self.tasks: set[asyncio.Task[Any]] = set()
        self._me: dict[str, tuple[float, Me]] = {}
        for member_id, token in store.links().items():
            if not sessions.has(member_id):
                sessions.tokens[member_id] = token

    # ------------------------------------------------------------ who is asking (the person's own GroupFlow session)

    async def me(self, token: str, refresh: bool = False) -> Me:
        if not token:
            raise ChatError(401, "Sign in first.")
        key = hashlib.sha256(token.encode()).hexdigest()
        hit = self._me.get(key)
        if hit and not refresh and time.monotonic() - hit[0] < ME_TTL_S:
            return hit[1]
        r = await self.server().get("/human/me", headers={"authorization": f"Bearer {token}"})
        if r.status_code != 200:
            self._me.pop(key, None)
            raise ChatError(401, "Your GroupFlow session ended. Sign in again.")
        body = r.json()
        me = Me(id=body["member"]["id"], name=body["member"]["name"], color=body["member"].get("color"), groups=body["groups"], links=body["links"])
        self._me[key] = (time.monotonic(), me)
        self.agent.member_names.setdefault(me.id, me.name)
        for s in self.streams:
            if s.member == me.id:
                s.rooms = me.rooms
        return me

    async def forward(self, method: str, path: str, token: str, csrf: str | None, body: Any = None) -> dict[str, Any]:
        """Call the human-only API with the person's own headers. The chat keeps nothing from it except what it returns."""
        headers = {"authorization": f"Bearer {token}"}
        if csrf:
            headers["x-gf-csrf"] = csrf
        r = await self.server().request(method, path, headers=headers, json=body)
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code >= 400:
            err = data.get("error")
            raise ChatError(r.status_code, err.get("message") if isinstance(err, dict) else str(err or f"Request failed ({r.status_code})"))
        return data

    # ------------------------------------------------------------ account linking, groups, invites

    def linked(self, me: Me) -> bool:
        """The host holds a token for this person that their page still lists (revoked or from another database: not linked).
        The demo world's people are pre-linked with their world-file tokens."""
        if not self.sessions.has(me.id):
            return False
        stored = self.store.link_id(me.id)
        return stored is None or any(link["id"] == stored for link in me.links)

    async def link(self, token: str, csrf: str | None) -> dict[str, Any]:
        """The person lets the chat's assistant act for them: the server issues this host a token, revocable on their page."""
        me = await self.me(token)
        body = await self.forward("POST", "/human/links", token, csrf, {"host": self.host_name})
        self.store.save_link(me.id, body["token"], body["id"])
        await self.sessions.set_token(me.id, body["token"])
        self.agent.member_names[me.id] = me.name
        await self.me(token, refresh=True)  # the cached view must list the new link
        return {"linked": True, "host": body["host"], "message": body.get("message")}

    async def sign_up(self, name: str, passcode: str, invite: str | None) -> dict[str, Any]:
        """Sign-up on the person's own page, passed through; when an invite was used, the room hears who joined."""
        r = await self.server().post("/human/signup", json={"name": name, "passcode": passcode, **({"invite": invite} if invite else {})})
        data = r.json()
        if r.status_code >= 400:
            err = data.get("error")
            raise ChatError(r.status_code, err.get("message") if isinstance(err, dict) else str(err or "Sign-up failed."))
        if data.get("joined"):
            self.system(data["joined"]["id"], f"{data['member']['name']} joined the group.")
        return data

    async def create_group(self, token: str, csrf: str | None, name: str) -> dict[str, Any]:
        body = await self.forward("POST", "/human/groups", token, csrf, {"name": name})
        me = await self.me(token, refresh=True)
        g = body["group"]
        self.system(g["id"], f"{me.name} started {g['name']}. Invite people from the group menu; each person joins on their own page.")
        return body

    async def invite(self, token: str, csrf: str | None, group: str, origin: str | None) -> dict[str, Any]:
        body = await self.forward("POST", "/human/groups/invites", token, csrf, {"group": group})
        if origin:
            body["join_url"] = f"{origin.rstrip('/')}/chat?invite={body['code']}"
        return body

    async def join(self, token: str, csrf: str | None, code: str) -> dict[str, Any]:
        body = await self.forward("POST", "/human/invites/accept", token, csrf, {"code": code})
        me = await self.me(token, refresh=True)
        if body.get("outcome") == "joined":
            self.system(body["group"]["id"], f"{me.name} joined the group.")
        return body

    async def leave(self, token: str, csrf: str | None, group: str) -> dict[str, Any]:
        me = await self.me(token)
        body = await self.forward("POST", "/human/groups/leave", token, csrf, {"group": group})
        await self.me(token, refresh=True)
        self.system(body["group"]["id"], f"{me.name} left the group.")
        return body

    def system(self, room: str, text: str) -> dict[str, Any]:
        msg = self.store.add(room, SYSTEM, "GroupFlow", text)
        self.broadcast(room, "message", msg)
        return msg

    # ------------------------------------------------------------ rooms and messages

    def rooms(self, me: Me) -> list[dict[str, Any]]:
        out = []
        for g in me.groups:
            last = self.store.latest(g["id"], 1)
            out.append({"id": g["id"], "title": g["name"], "kind": "group", "members": g["members"], "your_role": g.get("your_role"),
                        "last": last[0] if last else None})
        dm = f"dm:{me.id}"
        last = self.store.latest(dm, 1)
        out.append({"id": dm, "title": f"{self.assistant_name} (private)", "kind": "dm", "members": [], "last": last[0] if last else None})
        return out

    def messages(self, me: Me, room: str, after: int = 0) -> list[dict[str, Any]]:
        self._check(me, room)
        return self.store.since(room, after) if after else self.store.latest(room, 150)

    def _check(self, me: Me, room: str) -> None:
        if room not in me.rooms:
            raise ChatError(403, "You are not in this conversation.")

    async def post(self, me: Me, room: str, text: str) -> tuple[dict[str, Any], asyncio.Task[Any] | None]:
        self._check(me, room)
        text = (text or "").strip()
        if not text or len(text) > MAX_TEXT:
            raise ChatError(400, f"Write 1 to {MAX_TEXT} characters.")
        msg = self.store.add(room, me.id, me.name, text)
        self.broadcast(room, "message", msg)
        request = text if room.startswith("dm:") else addressed(text)
        task = None
        if request:
            task = asyncio.create_task(self._answer(me, room, msg, request))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        return msg, task

    def context_for(self, me: Me, room: str, before_id: int) -> ChatContext:
        if room.startswith("dm:"):
            return ChatContext(id="dm", title=f"{me.name}'s private thread", shared=False)
        g = me.group(room) or {"name": room, "members": []}
        recent = [(m["author_name"], m["text"]) for m in self.store.before(room, before_id, RECENT) if m["author"] != SYSTEM]
        return ChatContext(id=room, title=g["name"], shared=True, members=[m["name"] for m in g["members"]], recent=recent,
                           listeners=f"group:{room}")

    async def _answer(self, me: Me, room: str, msg: dict[str, Any], request: str) -> dict[str, Any]:
        lock = self.room_locks.setdefault(room, asyncio.Lock())
        async with lock:
            self.broadcast(room, "typing", {"room": room, "for": me.name})
            meta: dict[str, Any] = {"for": me.id, "in_reply_to": msg["id"]}
            try:
                if not self.linked(me):
                    reply = (f"{me.name}, I can't act for you yet. Link {self.assistant_name} to your GroupFlow account "
                             "from your profile menu, and I'll pick this up when you ask again.")
                    meta["error"] = "not_linked"
                else:
                    chat = self.context_for(me, room, msg["id"])
                    turn = await self.agent.handle(me.id, request, chat=chat, group=None if room.startswith("dm:") else room)
                    reply = turn.reply
                    meta["calls"] = [{"name": c["name"], "ok": c["ok"],
                                      "outcome": ((c.get("result") or {}).get("outcome") or (c.get("result") or {}).get("status")) if c["ok"]
                                      else (c.get("result") or {}).get("code")}
                                     for c in turn.calls if not c.get("prefetched")]
                    meta["ms"] = round(turn.ms)
                    if followup := asked_followup(turn.calls, reply):
                        meta["followup"] = followup
                    self.deliver_notices(me, turn.calls)
            except LlmError as err:
                reply, meta["error"] = f"Sorry, I can't answer right now ({err}).", "llm"
            except Exception:  # noqa: BLE001 - the room gets an answer either way
                log.exception("chat turn failed")
                reply, meta["error"] = "Sorry, something went wrong on my side. Please try again.", "internal"
            answer = self.store.add(room, ASSISTANT, self.assistant_name, reply, meta)
            self.broadcast(room, "message", answer)
            return answer

    def deliver_notices(self, me: Me, calls: list[dict[str, Any]]) -> int:
        """Post what notify_people told each person into that person's private thread, the chat's equivalent of Alexa reading
        it out on their next turn. The server wrote each text for its recipient, from a plan that person can see."""
        sent = 0
        for result in notices_sent(calls):
            for r in result.get("recipients") or []:
                if r.get("notice_id") and not self.store.claim(r["id"], [r["notice_id"]]):
                    continue
                room = f"dm:{r['id']}"
                msg = self.store.add(room, ASSISTANT, self.assistant_name, r["text"], {"notice": True, "from": me.id, "plan": result.get("plan")})
                self.broadcast(room, "message", msg)
                sent += 1
        return sent

    async def relay_inbox(self, member_id: str, token: str) -> int:
        """Reminders, check-ins and notices the server queued for this person (the worker sends them with nobody in a conversation),
        read with the person's own session and posted into their private thread once. The chat's stand-in for Alexa announcing them."""
        try:
            r = await self.server().get("/human/inbox", headers={"authorization": f"Bearer {token}"})
        except httpx.HTTPError:
            return 0
        if r.status_code != 200:
            return 0
        inbox = r.json()
        titles = {p["id"]: p["title"] for p in inbox.get("plans", [])}
        notes = [n for n in inbox.get("notifications", []) if n.get("kind") in RELAYED_KINDS]
        fresh = set(self.store.claim(member_id, [n["id"] for n in notes]))
        room = f"dm:{member_id}"
        for n in notes:
            if n["id"] in fresh:
                msg = self.store.add(room, ASSISTANT, self.assistant_name, n["text"], {"notice": True, "kind": n["kind"], "plan": titles.get(n.get("plan_id"))})
                self.broadcast(room, "message", msg)
        return len(fresh)

    async def relay_all(self) -> int:
        """Relay for everyone with the chat open now (after a demo skip-ahead, without waiting for their next poll)."""
        seen: dict[str, str] = {s.member: s.token for s in list(self.streams) if s.token}
        return sum([await self.relay_inbox(m, t) for m, t in seen.items()])

    async def updates(self, me: Me) -> dict[str, Any]:
        """The person's own unheard news and to-dos, read over their private session (no listeners), without marking anything heard."""
        if not self.linked(me):
            return {"linked": False, "headline": None, "updates": [], "todos": [], "decisions": []}
        result = await self.sessions.get(me.id, "chat").call_tool("get_context", {"detail": "summary", "mark_read": False})
        if not result.ok:
            raise ChatError(502, (result.error or {}).get("message", "Could not read your updates."))
        data = result.data or {}
        return {"linked": True, "headline": data.get("headline"), "updates": data.get("updates", []),
                "todos": [t for t in data.get("your_todos", []) if t.get("kind") in ("confirm", "verify_condition", "commit", "send", "repair")],
                "decisions": [{"plan": d["plan"], "what": d["what"], "proposed": d["proposed"], "by": d["by"]} for d in data.get("decisions_for_you", [])]}

    # ------------------------------------------------------------ live events

    def broadcast(self, room: str, event: str, data: Any) -> None:
        frame = f"event: {event}\ndata: {json.dumps({'room': room, **(data if isinstance(data, dict) else {'data': data})}, ensure_ascii=False, default=str)}\n\n"
        for s in list(self.streams):
            if room in s.rooms:
                s.queue.put_nowait(frame)

    async def close(self) -> None:
        for t in list(self.tasks):
            t.cancel()


# ---------------------------------------------------------------- HTTP routes (mounted on the simulator app)


class TextBody(BaseModel):
    text: str


class SignupBody(BaseModel):
    name: str
    passcode: str
    invite: str | None = None


class NameBody(BaseModel):
    name: str


class GroupBody(BaseModel):
    group: str


class CodeBody(BaseModel):
    code: str


def register_chat_routes(app: FastAPI, chat: ChatService) -> None:
    def creds(request: Request) -> tuple[str, str | None]:
        auth = request.headers.get("authorization", "")
        return (auth[7:].strip() if auth.lower().startswith("bearer ") else ""), request.headers.get("x-gf-csrf")

    def fail(err: ChatError) -> JSONResponse:
        return JSONResponse({"error": err.message}, status_code=err.status)

    @app.get("/api/chat/me")
    async def chat_me(request: Request, refresh: bool = False) -> JSONResponse:
        token, _ = creds(request)
        try:
            me = await chat.me(token, refresh=refresh)
        except ChatError as err:
            return fail(err)
        return JSONResponse({"member": {"id": me.id, "name": me.name, "color": me.color}, "linked": chat.linked(me), "links": me.links,
                             "assistant": chat.assistant_name, "rooms": chat.rooms(me)})

    @app.post("/api/chat/signup")
    async def chat_signup(body: SignupBody) -> JSONResponse:
        try:
            return JSONResponse(await chat.sign_up(body.name, body.passcode, body.invite))
        except ChatError as err:
            return fail(err)

    @app.post("/api/chat/link")
    async def chat_link(request: Request) -> JSONResponse:
        try:
            return JSONResponse(await chat.link(*creds(request)))
        except ChatError as err:
            return fail(err)

    @app.post("/api/chat/groups")
    async def chat_group(request: Request, body: NameBody) -> JSONResponse:
        try:
            return JSONResponse(await chat.create_group(*creds(request), body.name))
        except ChatError as err:
            return fail(err)

    @app.post("/api/chat/invites")
    async def chat_invite(request: Request, body: GroupBody) -> JSONResponse:
        try:
            return JSONResponse(await chat.invite(*creds(request), body.group, request.headers.get("origin")))
        except ChatError as err:
            return fail(err)

    @app.get("/api/chat/invites/{code}")
    async def chat_invite_preview(code: str) -> JSONResponse:
        r = await chat.server().get(f"/human/invites/{code}")
        return JSONResponse(r.json(), status_code=r.status_code)

    @app.post("/api/chat/join")
    async def chat_join(request: Request, body: CodeBody) -> JSONResponse:
        try:
            return JSONResponse(await chat.join(*creds(request), body.code))
        except ChatError as err:
            return fail(err)

    @app.post("/api/chat/leave")
    async def chat_leave(request: Request, body: GroupBody) -> JSONResponse:
        try:
            return JSONResponse(await chat.leave(*creds(request), body.group))
        except ChatError as err:
            return fail(err)

    @app.get("/api/chat/rooms/{room}/messages")
    async def chat_messages(request: Request, room: str, after: int = 0) -> JSONResponse:
        token, _ = creds(request)
        try:
            return JSONResponse({"messages": chat.messages(await chat.me(token), room, after)})
        except ChatError as err:
            return fail(err)

    @app.post("/api/chat/rooms/{room}/messages")
    async def chat_post(request: Request, room: str, body: TextBody) -> JSONResponse:
        token, _ = creds(request)
        try:
            msg, task = await chat.post(await chat.me(token), room, body.text)
        except ChatError as err:
            return fail(err)
        return JSONResponse({"message": msg, "assistant": task is not None})

    @app.get("/api/chat/updates")
    async def chat_updates(request: Request) -> JSONResponse:
        token, _ = creds(request)
        try:
            return JSONResponse(await chat.updates(await chat.me(token)))
        except ChatError as err:
            return fail(err)

    @app.get("/api/chat/stream", response_model=None)
    async def chat_stream(request: Request) -> StreamingResponse | JSONResponse:
        token, _ = creds(request)
        try:
            me = await chat.me(token)
        except ChatError as err:
            return fail(err)
        stream = Stream(member=me.id, rooms=me.rooms, token=token)
        chat.streams.add(stream)

        async def events() -> AsyncIterator[str]:
            try:
                yield ": connected\n\n"
                await chat.relay_inbox(me.id, token)
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        yield await asyncio.wait_for(stream.queue.get(), timeout=RELAY_EVERY_S)
                    except asyncio.TimeoutError:
                        await chat.relay_inbox(me.id, token)
                        yield ": ping\n\n"
            finally:
                chat.streams.discard(stream)

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
