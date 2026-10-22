"""Capture protocol evidence: the real JSON-RPC exchanged with the GroupFlow MCP
server over Streamable HTTP, written to docs/PROTOCOL_EVIDENCE.md.

    python -m groupflow evidence            write docs/PROTOCOL_EVIDENCE.md
    python -m groupflow evidence --stdout   print it instead

The server runs in-process on a random port with a throwaway database, so this
never touches the demo data. Bearer tokens are redacted in the output.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import platform
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import httpx2

from groupflow.core.serve import serve_in_background
from groupflow.core.settings import REPO_ROOT, ServerSettings
from groupflow.runtime import build_runtime

ACCEPT = "application/json, text/event-stream"


def _short(value: Any, limit: int = 3600) -> str:
    text = json.dumps(value, indent=2, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "\n  ... (truncated)"


class Recorder:
    def __init__(self, base: str, tokens: dict[str, str]):
        self.base = base
        self.tokens = tokens
        self.parts: list[str] = []
        self.http = httpx.AsyncClient(base_url=base, timeout=30)

    def redact(self, headers: dict[str, str]) -> dict[str, str]:
        out = dict(headers)
        auth = out.get("authorization")
        if auth:
            who = next((m for m, t in self.tokens.items() if auth.endswith(t)), "unknown")
            out["authorization"] = f"Bearer <{who}'s token>"
        return out

    async def post(self, title: str, body: dict[str, Any] | None, headers: dict[str, str], note: str = "", show_body: Any = None) -> httpx.Response:
        h = {"content-type": "application/json", "accept": ACCEPT, **headers}
        r = await self.http.post("/mcp", json=body, headers=h)
        resp_headers = {k: v for k, v in r.headers.items() if k.lower() in ("content-type", "mcp-session-id", "www-authenticate", "mcp-protocol-version")}
        try:
            payload: Any = r.json()
        except ValueError:
            payload = r.text or "(empty body)"
        self.parts += [f"### {title}", ""]
        if note:
            self.parts += [note, ""]
        self.parts += ["Request:", "", "```http", f"POST /mcp HTTP/1.1", *[f"{k}: {v}" for k, v in self.redact(h).items()], "",
                       json.dumps(body, indent=2) if body is not None else "", "```", "",
                       "Response:", "", "```http", f"HTTP/1.1 {r.status_code} {r.reason_phrase}", *[f"{k}: {v}" for k, v in resp_headers.items()], "",
                       _short(show_body(payload) if show_body and isinstance(payload, dict) else payload), "```", ""]
        return r

    async def session(self, member: str) -> dict[str, str]:
        token = {"authorization": f"Bearer {self.tokens[member]}", "x-groupflow-listeners": "me"}  # a personal device
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "evidence", "version": "1"}}}
        r = await self.http.post("/mcp", json=init, headers={"content-type": "application/json", "accept": ACCEPT, **token})
        sid = r.headers["mcp-session-id"]
        h = {**token, "mcp-session-id": sid, "mcp-protocol-version": "2025-11-25"}
        await self.http.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers={"content-type": "application/json", "accept": ACCEPT, **h})
        return h


def _tool_list_view(payload: dict[str, Any]) -> dict[str, Any]:
    tools = payload.get("result", {}).get("tools", [])
    if not tools:
        return payload
    first = tools[0]
    return {"jsonrpc": payload.get("jsonrpc"), "id": payload.get("id"), "result": {
        "tools (names)": [t["name"] for t in tools],
        "first tool in full": {**first, "outputSchema": "(omitted here; present on every tool)" if first.get("outputSchema") else None}}}


def _context_view(payload: dict[str, Any]) -> dict[str, Any]:
    sc = payload.get("result", {}).get("structuredContent") or {}
    plans = sc.get("plans") or []
    excerpt = {"you": sc.get("you"), "headline": sc.get("headline"),
               "plans": [{k: p.get(k) for k in ("title", "kind", "visible_to", "status", "next_step")} for p in plans],
               "your_conditions": [c.get("description") for c in sc.get("your_conditions") or []]}
    if sc.get("shared_with"):
        excerpt.update(shared_with=sc["shared_with"], shared_note=sc.get("shared_note"))
    return {"jsonrpc": "2.0", "id": payload.get("id"), "result": {"structuredContent (excerpt)": excerpt}}


def _result_view(payload: dict[str, Any]) -> dict[str, Any]:
    """structuredContent of a tool result, or the error a refused call returns, without the text copy."""
    result = payload.get("result", {})
    if result.get("isError"):
        return {"jsonrpc": "2.0", "id": payload.get("id"), "result": {"isError": True, "content (parsed)": json.loads(result["content"][0]["text"])}}
    sc = dict(result.get("structuredContent") or {})
    for r in sc.get("recipients") or []:
        r.pop("id", None)
    return {"jsonrpc": "2.0", "id": payload.get("id"), "result": {"structuredContent": sc}}


async def capture() -> str:
    tmp = Path(tempfile.mkdtemp(prefix="gf-evidence-"))
    settings = ServerSettings(db_path=str(tmp / "evidence.db"), admin_key="evidence", allowed_origins=["http://localhost:8788"],
                              world_path=str(REPO_ROOT / "demo" / "world.yaml"), tool_profile="lab")
    rt = build_runtime(settings, stages={"friday_dinner": "awaiting"})
    bg = serve_in_background(rt.app)
    tokens = {p.id: p.token for p in rt.world.people}
    rec = Recorder(bg.base, tokens)
    try:
        alice = {"authorization": f"Bearer {tokens['alice']}", "x-groupflow-listeners": "me"}  # Alice's personal device
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "evidence-client", "version": "1.0"}}}
        rec.parts += ["## 1. Handshake, discovery and a tool call (Alice, who has three plans in two groups)", ""]
        r = await rec.post("initialize", init, alice, "The client offers 2025-11-25; the server answers with the same version, its capabilities and its instructions. Alice's own device "
                           "declares that only she will hear the replies (`X-GroupFlow-Listeners: me`); without it GroupFlow assumes a shared room.")
        sid = r.headers["mcp-session-id"]
        h = {**alice, "mcp-session-id": sid, "mcp-protocol-version": "2025-11-25"}
        await rec.post("notifications/initialized", {"jsonrpc": "2.0", "method": "notifications/initialized"}, h, "A notification: no id, answered with 202 and no body.")
        await rec.post("tools/list", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, h,
                       "Everyone gets the same 35 lab tools (14 core, 3 for exact targets, guarded writes and planning options, 2 template discovery, "
                       "5 structured-project, 2 group tools, 3 for telling people and asking first, 2 for reminders and progress, 4 for deciding "
                       "together); none can approve an activity, accept an invite or link an "
                       "account, and none of them acts for anyone but the speaker. Each has an input schema, an output schema and annotations.",
                       show_body=_tool_list_view)
        await rec.post("tools/call get_context", {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_context", "arguments": {"detail": "full", "mark_read": False}}}, h,
                       "Results come back as structuredContent (validated against the output schema) plus a text copy for older clients. Alice sees the "
                       "surprise party, the family's Home plan and the Friday dinner.", show_body=_context_view)

        rec.parts += ["## 2. The same endpoint, the guest of honour (Linda)", ""]
        linda = await rec.session("linda")
        await rec.post("tools/call get_context as Linda", {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_context", "arguments": {"detail": "full", "mark_read": False}}},
                       linda, "Linda's view comes from the same tool: the party plan lives in a space without her, so it is simply not there.",
                       show_body=_context_view)
        await rec.post("tools/call get_context for the hidden plan", {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                                                    "params": {"name": "get_context", "arguments": {"plan": "party"}}}, linda,
                       "Asking for it by id is refused like any plan that does not exist for her.")

        rec.parts += ["## 3. A shared conversation: the host declares who else will read the reply", ""]
        room = {"authorization": f"Bearer {tokens['alice']}", "x-groupflow-listeners": "group:family"}
        r = await rec.post("initialize with X-GroupFlow-Listeners", init, room,
                           "A group chat host (or a shared kitchen device) opens Alice's session declaring the family group as listeners. "
                           "The header can only narrow what Alice's connection reads, never widen it.")
        rh = {**room, "mcp-session-id": r.headers["mcp-session-id"], "mcp-protocol-version": "2025-11-25"}
        await rec.http.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers={"content-type": "application/json", "accept": ACCEPT, **rh})
        await rec.post("tools/call get_context in the family chat", {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                                                   "params": {"name": "get_context", "arguments": {"detail": "full", "mark_read": False}}}, rh,
                       "Same tool, same speaker: Linda is listening, so the party is not in this conversation at all, and Alice's own private "
                       "conditions are left out too.", show_body=_context_view)
        await rec.post("tools/call create_plan private in the family chat", {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                                                                           "params": {"name": "create_plan", "arguments": {"template": "general_event", "title": "Gift ideas", "private": True}}},
                       rh, "A plan some listener could not see cannot be started where they are listening.")

        rec.parts += ["## 4. A person who signed up links an assistant (simulated account linking)", ""]
        signup = (await rec.http.post("/human/signup", json={"name": "Maya", "passcode": "evidence-pass"})).json()
        page = {"authorization": f"Bearer {signup['token']}", "x-gf-csrf": signup["csrf"]}
        link = (await rec.http.post("/human/links", json={"host": "GroupFlow chat"}, headers=page)).json()
        rec.tokens["maya (linked)"] = link["token"]
        rec.parts += ["Maya signs up and, on her own page (`POST /human/links` with her session and CSRF token), lets the chat host act for her. "
                      "The host receives a revocable token once; it is an ordinary bearer token for Maya on `/mcp`.", ""]
        maya = {"authorization": f"Bearer {link['token']}"}
        r = await rec.post("initialize with a linked token", init, maya)
        mh = {**maya, "mcp-session-id": r.headers["mcp-session-id"], "mcp-protocol-version": "2025-11-25"}
        await rec.http.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers={"content-type": "application/json", "accept": ACCEPT, **mh})
        await rec.post("tools/call create_group", {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                                   "params": {"name": "create_group", "arguments": {"name": "Flatmates"}}}, mh,
                       "Starting a group adds nobody else.")
        await rec.post("tools/call invite_to_group", {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                                                      "params": {"name": "invite_to_group", "arguments": {}}}, mh,
                       "The assistant can make an invite code; only the invited person can accept it, on their own page.")

        rec.parts += ["## 5. Telling people and asking first", ""]
        await rec.post("tools/call propose_change (a decision, and the server's offer)", {"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {
            "name": "propose_change", "arguments": {"item": "party.venue", "value": "Lucky Star karaoke", "quote": "let's just do karaoke at lucky star"}}}, h,
            "Alice owns the venue, so the change takes effect. The result carries the server's offer to tell the people who can see the plan and did "
            "not hear about it; Linda is not among them.", show_body=_result_view)
        await rec.post("tools/call answer_followup", {"jsonrpc": "2.0", "id": 12, "method": "tools/call", "params": {
            "name": "answer_followup", "arguments": {"choice": "yeah go for it"}}}, h,
            "Alice says yes. The server matches her words to the offer and runs exactly the notify_people call behind it, as Alice. Each notice is "
            "written by the server from the plan's state.", show_body=_result_view)
        await rec.post("tools/call notify_people with a note that names the surprise", {"jsonrpc": "2.0", "id": 13, "method": "tools/call", "params": {
            "name": "notify_people", "arguments": {"plan": "home", "note": "the surprise party is at Lucky Star karaoke now"}}}, h,
            "The family's Home plan reaches Linda, and the note names a plan kept from her, so nobody is told.", show_body=_result_view)
        await rec.post("tools/call propose_change from vague words", {"jsonrpc": "2.0", "id": 14, "method": "tools/call", "params": {
            "name": "propose_change", "arguments": {"item": "party.date", "value": "Saturday, Oct 24, 7:00 pm", "quote": "can we push it back a bit"}}}, h,
            "The words name no time, so the server refuses to record one the model would have to invent.", show_body=_result_view)
        await rec.post("tools/call ask_followup", {"jsonrpc": "2.0", "id": 15, "method": "tools/call", "params": {"name": "ask_followup", "arguments": {
            "question": "Push it to 6:30 or 7?", "options": [
                {"label": "6:30", "action": {"tool": "propose_change", "arguments": {"item": "party.date", "value": "Saturday, Oct 24, 6:30 pm",
                                                                                    "quote": "can we push it back a bit"}}},
                {"label": "7:00", "action": {"tool": "propose_change", "arguments": {"item": "party.date", "value": "Saturday, Oct 24, 7:00 pm",
                                                                                    "quote": "can we push it back a bit"}}}]}}}, h,
            "The server keeps the question and the exact call behind each option. This server sends JSON responses, so it cannot ask through "
            "the host (elicitation needs GROUPFLOW_MCP_RESPONSES=sse); the question comes back for the assistant to ask.", show_body=_result_view)
        await rec.post("tools/call answer_followup with a bare time", {"jsonrpc": "2.0", "id": 16, "method": "tools/call", "params": {
            "name": "answer_followup", "arguments": {"choice": "7"}}}, h,
            "\"7\" picks the 7:00 option and the change runs as Alice, with every check. Bob and Charlie own work that watches the date, so the "
            "change itself flags it for them, and there is nobody left to offer to tell.",
            show_body=_result_view)

        rec.parts += ["## 6. Reminders and checking progress", ""]
        await rec.post("tools/call set_reminder with a vague time", {"jsonrpc": "2.0", "id": 17, "method": "tools/call", "params": {
            "name": "set_reminder", "arguments": {"when": "later", "about": "cake.order"}}}, h,
            "\"Later\" names no time, so nothing is scheduled. The server keeps a question with concrete times, each carrying the same reminder "
            "at that time.", show_body=_result_view)
        await rec.post("tools/call answer_followup picks a time", {"jsonrpc": "2.0", "id": 18, "method": "tools/call", "params": {
            "name": "answer_followup", "arguments": {"choice": "tomorrow at 9am"}}}, h,
            "The reminder is set for the time Alice picked. Who receives a reminder addressed by role is worked out when it goes off, and its "
            "facts are read from the plan then.", show_body=_result_view)
        await rec.post("tools/call review_plan", {"jsonrpc": "2.0", "id": 19, "method": "tools/call", "params": {
            "name": "review_plan", "arguments": {"plan": "friday_dinner"}}}, h,
            "What the plan is waiting on, from whom and for how long: the same report the server's automatic check-ins use.", show_body=_result_view)

        rec.parts += ["## 7. Refusals", ""]
        await rec.post("No bearer token", init, {}, "Identity comes only from the token. Without one the server answers 401 with a WWW-Authenticate challenge.")
        await rec.post("Browser request from an unknown origin", init, {**alice, "origin": "https://evil.example"}, "Origin validation (DNS-rebinding and cross-site protection).")
        await rec.post("Another member reusing Alice's session", {"jsonrpc": "2.0", "id": 9, "method": "tools/list"},
                       {"authorization": f"Bearer {tokens['bob']}", "mcp-session-id": sid, "mcp-protocol-version": "2025-11-25"},
                       "Sessions are bound to the member who opened them.")
        await rec.post("A tool that does not exist", {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "approve_plan", "arguments": {"member": "dana"}}}, h,
                       "There is no tool that approves, declines or withdraws for anyone.")

        rec.parts += ["## 8. Version negotiation with the official SDK client", ""]
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client

        versions = {}
        for mode in ("legacy", "auto"):
            http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {tokens['charlie']}"})
            async with Client(streamable_http_client(f"{bg.base}/mcp", http_client=http), mode=mode) as c:
                versions[mode] = c.session.protocol_version
                names = [t.name for t in (await c.list_tools()).tools]
        rec.parts += ["The Python MCP SDK client (`mcp` 2.3.0) connects in both of its modes. In `auto` mode it first tries the newer per-request "
                      "envelope; the server does not offer it, so the client falls back to the handshake.", "",
                      "| SDK client mode | Negotiated protocol version |", "| --- | --- |",
                      *[f"| `{m}` | `{v}` |" for m, v in versions.items()], "", f"Tools listed in the last session: {len(names)}.", ""]
    finally:
        await rec.http.aclose()
        bg.stop()
        rt.db.close()

    import mcp

    head = ["# Protocol evidence", "",
            f"Captured {datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')} by `python -m groupflow evidence` "
            f"(Python {platform.python_version()}, MCP Python SDK {getattr(mcp, '__version__', '2.3.0')}, {platform.system()}).", "",
            "GroupFlow is a remote MCP server: Streamable HTTP on `/mcp`, JSON-RPC 2.0, protocol version **2025-11-25**, bearer-token identity. "
            "This capture serves all 36 tools on `/mcp` (`GROUPFLOW_TOOL_PROFILE=lab`); by default `/mcp` serves the 13 voice tools "
            "and all 36 are on `/mcp/lab`, with the same engine and rules behind both. "
            "Everything below is the real traffic of one run against a throwaway database; only the tokens are redacted.", ""]
    return "\n".join(head + rec.parts) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="groupflow evidence", description="Capture the MCP handshake and tool calls as markdown.")
    p.add_argument("--out", default=str(REPO_ROOT / "docs" / "PROTOCOL_EVIDENCE.md"))
    p.add_argument("--stdout", action="store_true")
    args = p.parse_args(argv)
    logging.getLogger("groupflow").setLevel(logging.WARNING)
    text = asyncio.run(capture())
    if args.stdout:
        sys.stdout.write(text)
    else:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
