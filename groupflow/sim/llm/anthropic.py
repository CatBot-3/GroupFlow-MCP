"""Anthropic provider on the Claude Messages API (POST {base}/v1/messages), called with httpx like the OpenAI-compatible
provider, so it needs no extra package.

Claude specifics handled here:
  * The system prompt is a top-level field, not a message.
  * Tool calls come back as tool_use content blocks with their input already parsed. Tool results go back in a user message as
    tool_result blocks, and consecutive results share one message, because the API wants user and assistant turns to alternate.
  * The system prompt is marked for prompt caching: the tool list and system prompt repeat on every step of a turn, so later
    steps read them from the cache instead of paying for them again.
  * No temperature is sent; the model's default works for tool use, and some models accept only their default.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import httpx

from .types import AssistantMessage, LlmError, LlmMessage, LlmRequest, LlmResponse, LlmToolCall, LlmUsage, ToolMessage, UserMessage, clean_schema

API_VERSION = "2023-06-01"

STATUS_HELP = {
    400: "the request was malformed",
    401: "the API key was rejected; check ANTHROPIC_API_KEY in .env",
    403: "the API key is not allowed to use this model",
    404: "the model or endpoint was not found; check ANTHROPIC_MODEL and ANTHROPIC_BASE_URL",
    413: "the request was too large",
    429: "rate limited; slow down and try again",
    500: "Anthropic had a server error",
    529: "Anthropic is overloaded; try again shortly",
}


def _tool_id(raw: str) -> str:
    """Claude accepts tool ids made of letters, digits, '_' and '-'; ids from elsewhere are mapped the same way both times."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", raw) or "call"


def to_messages(messages: list[LlmMessage]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def push(role: str, blocks: list[dict[str, Any]]) -> None:
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})

    for m in messages:
        if isinstance(m, UserMessage):
            push("user", [{"type": "text", "text": m.content or "..."}])
        elif isinstance(m, AssistantMessage):
            blocks: list[dict[str, Any]] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            for tc in m.tool_calls:
                blocks.append({"type": "tool_use", "id": _tool_id(tc.id), "name": tc.name, "input": tc.arguments or {}})
            push("assistant", blocks or [{"type": "text", "text": "..."}])
        elif isinstance(m, ToolMessage):
            content = m.content if isinstance(m.content, str) else json.dumps(m.content, ensure_ascii=False)
            push("user", [{"type": "tool_result", "tool_use_id": _tool_id(m.tool_call_id), "content": content or "(empty)",
                           "is_error": bool(m.is_error)}])
    return out


class AnthropicProvider:
    id = "anthropic"
    label = "Anthropic"

    def __init__(self, api_key: str, model: str, base_url: str = "https://api.anthropic.com", timeout_s: float = 45.0,
                 max_retries: int = 2, retry_delay_s: float = 0.6, transport: httpx.AsyncBaseTransport | None = None):
        self.model = model
        self.reason: str | None = None
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.retry_delay_s = retry_delay_s
        self._transport = transport

    def request_body(self, req: LlmRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": req.max_tokens,
            "system": [{"type": "text", "text": req.system, "cache_control": {"type": "ephemeral"}}],
            "messages": to_messages(req.messages),
        }
        if req.tools:
            body["tools"] = [{"name": t.name, "description": t.description, "input_schema": clean_schema(t.parameters)} for t in req.tools]
        return body

    async def complete(self, req: LlmRequest) -> LlmResponse:
        url = f"{self.base_url}/v1/messages"
        body = self.request_body(req)
        headers = {"x-api-key": self._api_key, "anthropic-version": API_VERSION, "content-type": "application/json"}
        attempt = 0
        while True:
            started = time.perf_counter()
            try:
                async with httpx.AsyncClient(timeout=self.timeout_s, transport=self._transport) as client:
                    res = await asyncio.wait_for(client.post(url, json=body, headers=headers), self.timeout_s)
                if res.status_code >= 400:
                    detail = ""
                    try:
                        detail = (res.json().get("error") or {}).get("message", "")
                    except ValueError:
                        detail = res.text[:200]
                    retryable = res.status_code in (429, 529) or res.status_code >= 500
                    raise LlmError(f"{self.label} returned {res.status_code}: {STATUS_HELP.get(res.status_code, 'request failed')}"
                                   + (f" ({detail})" if detail else "") + ".", res.status_code, retryable)
                data = res.json()
                content = data.get("content") or []
                calls = [LlmToolCall(id=b.get("id", f"call_{i}"), name=b.get("name", ""), arguments=b.get("input") or {},
                                     raw_arguments=json.dumps(b.get("input") or {}, ensure_ascii=False))
                         for i, b in enumerate(content) if b.get("type") == "tool_use"]
                text = " ".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
                stop_reason = data.get("stop_reason") or ""
                stop = ("tool_calls" if calls or stop_reason == "tool_use" else "length" if stop_reason == "max_tokens"
                        else "end" if stop_reason in ("end_turn", "stop_sequence") else "other")
                usage = data.get("usage") or {}
                cached = usage.get("cache_read_input_tokens") or 0
                total_in = (usage.get("input_tokens") or 0) + cached + (usage.get("cache_creation_input_tokens") or 0)
                return LlmResponse(text=text, tool_calls=calls, reasoning=None, stop=stop,
                                   usage=LlmUsage(input_tokens=total_in, output_tokens=usage.get("output_tokens") or 0, cached_input_tokens=cached),
                                   ms=(time.perf_counter() - started) * 1000)
            except LlmError as err:
                error = err
            except (httpx.TimeoutException, asyncio.TimeoutError, TimeoutError):  # asyncio.TimeoutError is separate before 3.11
                error = LlmError(f"{self.label} did not answer within {round(self.timeout_s)} s.", None, True)
            except httpx.HTTPError as err:
                error = LlmError(f"Could not reach {self.label} at {self.base_url}: {err}.", None, True)
            if not error.retryable or attempt >= self.max_retries:
                raise error
            attempt += 1
            await asyncio.sleep(self.retry_delay_s * 2 ** (attempt - 1))
