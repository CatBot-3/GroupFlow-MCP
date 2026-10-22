"""A model on the other end of a queue: each completion request waits until an outside agent answers it.

The simulator's agent loop is unchanged. It still lists the tools from the MCP server, runs every tool call over the speaker's own
MCP session and enforces its usual checks; only the decision "which tool next, or what to say" comes from outside. The outside
agent reads pending requests from GET /api/relay/next and answers with POST /api/relay/{id} (scripts/relay_model.py wraps both).
It sees exactly what any provider would be sent: the system prompt, the tool definitions and the messages.

AGENT_PROVIDER=relay turns it on. RELAY_LABEL and RELAY_MODEL name the model behind it, for the transcript and the page.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from .types import AssistantMessage, LlmError, LlmRequest, LlmResponse, LlmUsage, ToolMessage, UserMessage, parse_arguments


def wire_messages(req: LlmRequest) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in req.messages:
        if isinstance(m, UserMessage):
            out.append({"role": "user", "content": m.content})
        elif isinstance(m, AssistantMessage):
            item: dict[str, Any] = {"role": "assistant", "content": m.content}
            if m.tool_calls:
                item["tool_calls"] = [{"id": t.id, "name": t.name, "arguments": t.arguments if t.arguments is not None else t.raw_arguments}
                                      for t in m.tool_calls]
            out.append(item)
        elif isinstance(m, ToolMessage):
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "name": m.name, "content": m.content, "is_error": m.is_error})
    return out


@dataclass
class Pending:
    id: str
    request: dict[str, Any]
    future: asyncio.Future
    created: float = field(default_factory=time.time)
    claimed: bool = False


class RelayProvider:
    id = "relay"
    reason = None

    def __init__(self, label: str = "Relay", model: str = "external", timeout_s: float = 1800):
        self.label = label
        self.model = model
        self.timeout_s = timeout_s
        self.pending: dict[str, Pending] = {}
        self.done = False
        self.answered = 0
        self._ids = itertools.count(1)
        self._calls = itertools.count(1)

    async def complete(self, req: LlmRequest) -> LlmResponse:
        loop = asyncio.get_running_loop()
        rid = f"r{next(self._ids)}"
        request = {"id": rid, "system": req.system, "tools": [{"name": t.name, "description": t.description, "parameters": t.parameters} for t in req.tools],
                   "messages": wire_messages(req), "max_tokens": req.max_tokens}
        p = Pending(rid, request, loop.create_future())
        self.pending[rid] = p
        started = time.perf_counter()
        try:
            answer = await asyncio.wait_for(p.future, timeout=self.timeout_s)
        except asyncio.TimeoutError:
            raise LlmError(f"The relayed model did not answer within {int(self.timeout_s)} seconds.") from None
        finally:
            self.pending.pop(rid, None)
        calls = []
        for tc in answer.get("tool_calls") or []:
            args = tc.get("arguments", {})
            raw = args if isinstance(args, str) else json.dumps(args)
            calls.append(parse_arguments(f"relay_{next(self._calls)}", str(tc.get("name", "")), raw))
        self.answered += 1
        return LlmResponse(text=str(answer.get("text") or ""), tool_calls=calls, reasoning=None, stop="tool_calls" if calls else "end",
                           usage=LlmUsage(), ms=(time.perf_counter() - started) * 1000)

    def next_request(self) -> dict[str, Any] | None:
        """The oldest request nobody has claimed yet."""
        for p in sorted(self.pending.values(), key=lambda x: x.created):
            if not p.claimed:
                p.claimed = True
                return p.request
        return None

    def answer(self, rid: str, body: dict[str, Any]) -> bool:
        p = self.pending.get(rid)
        if p is None or p.future.done():
            return False
        if not isinstance(body.get("tool_calls") or [], list):
            raise ValueError("tool_calls must be a list of {name, arguments}.")
        if not (body.get("tool_calls") or str(body.get("text") or "").strip()):
            raise ValueError("Answer with text, tool_calls, or both.")
        p.future.set_result(body)
        return True


def register_relay_routes(app: Any, provider: RelayProvider) -> None:
    @app.get("/api/relay/next")
    async def relay_next(wait: float = 60) -> JSONResponse:
        deadline = time.monotonic() + max(0.0, min(wait, 590.0))
        while True:
            if provider.done:
                return JSONResponse({"done": True})
            req = provider.next_request()
            if req is not None:
                return JSONResponse(req)
            if time.monotonic() >= deadline:
                return JSONResponse({"waiting": True})
            await asyncio.sleep(0.2)

    @app.post("/api/relay/done")
    async def relay_done(request: Request) -> JSONResponse:
        body = await request.json() if (await request.body()) else {}
        provider.done = bool(body.get("done", True))
        return JSONResponse({"done": provider.done, "answered": provider.answered})

    @app.post("/api/relay/{rid}")
    async def relay_answer(rid: str, request: Request) -> JSONResponse:
        try:
            ok = provider.answer(rid, await request.json())
        except (ValueError, json.JSONDecodeError) as err:
            return JSONResponse({"error": str(err)}, status_code=400)
        if not ok:
            return JSONResponse({"error": f"No open request {rid}."}, status_code=404)
        return JSONResponse({"ok": True})
