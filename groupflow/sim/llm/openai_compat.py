"""Chat Completions provider for OpenAI-compatible APIs. DeepSeek is the default
(https://api.deepseek.com, model deepseek-flash); any compatible endpoint works.

DeepSeek specifics handled here:
  * "thinking" is on by default on DeepSeek; a voice assistant wants short
    latency, so it is sent as disabled unless configured otherwise.
  * In thinking mode the model's reasoning_content is passed back on the
    assistant messages that carry tool calls, within the same turn.
  * Tool call arguments arrive as a JSON string that may be invalid; they are
    parsed defensively and errors go back to the model instead of raising.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from .types import AssistantMessage, LlmError, LlmRequest, LlmResponse, LlmUsage, ToolMessage, UserMessage, clean_schema, parse_arguments

STATUS_HELP = {
    400: "the request was malformed",
    401: "the API key was rejected; check the key in .env",
    402: "the account has no balance left; top it up on the provider website",
    403: "the API key is not allowed to use this model",
    404: "the model or endpoint was not found; check the model name and base URL",
    422: "a request parameter was invalid",
    429: "rate limited; slow down and try again",
    500: "the provider had a server error",
    503: "the provider is overloaded",
}


class OpenAiCompatibleProvider:
    def __init__(self, id: str, label: str, base_url: str, api_key: str, model: str, thinking: str | None = "disabled",
                 temperature: float = 0.2, timeout_s: float = 45.0, max_retries: int = 2, retry_delay_s: float = 0.6,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.id = id
        self.label = label
        self.model = model
        self.reason: str | None = None
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.thinking = thinking
        self.temperature = temperature
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.retry_delay_s = retry_delay_s
        self._transport = transport

    def wire_messages(self, req: LlmRequest) -> list[dict[str, Any]]:
        thinking_on = self.thinking == "enabled"
        out: list[dict[str, Any]] = [{"role": "system", "content": req.system}]
        for m in req.messages:
            if isinstance(m, UserMessage):
                out.append({"role": "user", "content": m.content})
            elif isinstance(m, ToolMessage):
                out.append({"role": "tool", "tool_call_id": m.tool_call_id,
                            "content": m.content if isinstance(m.content, str) else json.dumps(m.content, ensure_ascii=False)})
            elif isinstance(m, AssistantMessage):
                msg: dict[str, Any] = {"role": "assistant", "content": m.content or (None if m.tool_calls else "")}
                if m.tool_calls:
                    msg["tool_calls"] = [{"id": tc.id, "type": "function",
                                          "function": {"name": tc.name, "arguments": tc.raw_arguments or json.dumps(tc.arguments or {})}}
                                         for tc in m.tool_calls]
                    if thinking_on and m.reasoning:
                        msg["reasoning_content"] = m.reasoning
                out.append(msg)
        return out

    def request_body(self, req: LlmRequest) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self.model, "messages": self.wire_messages(req), "max_tokens": req.max_tokens, "stream": False}
        if req.tools:
            body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": clean_schema(t.parameters)}}
                             for t in req.tools]
            body["tool_choice"] = "auto"
        if self.thinking:
            body["thinking"] = {"type": self.thinking}
        if self.thinking != "enabled":
            body["temperature"] = self.temperature
        return body

    async def complete(self, req: LlmRequest) -> LlmResponse:
        url = f"{self.base_url}/chat/completions"
        body = self.request_body(req)
        attempt = 0
        while True:
            started = time.perf_counter()
            try:
                async with httpx.AsyncClient(timeout=self.timeout_s, transport=self._transport) as client:
                    # httpx timeouts are per network operation; wait_for bounds the whole request.
                    res = await asyncio.wait_for(client.post(url, json=body, headers={"Authorization": f"Bearer {self._api_key}"}), self.timeout_s)
                if res.status_code >= 400:
                    detail = ""
                    try:
                        detail = (res.json().get("error") or {}).get("message", "")
                    except ValueError:
                        detail = res.text[:200]
                    retryable = res.status_code == 429 or res.status_code >= 500
                    raise LlmError(f"{self.label} returned {res.status_code}: {STATUS_HELP.get(res.status_code, 'request failed')}"
                                   + (f" ({detail})" if detail else "") + ".", res.status_code, retryable)
                data = res.json()
                choice = (data.get("choices") or [None])[0]
                if not choice or not choice.get("message"):
                    raise LlmError(f"{self.label} returned no message.", res.status_code, True)
                msg = choice["message"]
                calls = [parse_arguments(tc.get("id", f"call_{i}"), (tc.get("function") or {}).get("name", ""), (tc.get("function") or {}).get("arguments"))
                         for i, tc in enumerate(msg.get("tool_calls") or [])]
                finish = choice.get("finish_reason") or ""
                usage = data.get("usage") or {}
                stop = "tool_calls" if calls or finish == "tool_calls" else "length" if finish == "length" else "end" if finish == "stop" else "other"
                return LlmResponse(
                    text=(msg.get("content") or "").strip(), tool_calls=calls, reasoning=msg.get("reasoning_content"), stop=stop,
                    usage=LlmUsage(input_tokens=usage.get("prompt_tokens", 0), output_tokens=usage.get("completion_tokens", 0),
                                   cached_input_tokens=usage.get("prompt_cache_hit_tokens", (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0))),
                    ms=(time.perf_counter() - started) * 1000,
                )
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
