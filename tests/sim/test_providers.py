"""Provider translation: what goes over the wire to DeepSeek and Bedrock, and how answers come back."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from groupflow.core.settings import LlmSettings
from groupflow.sim.llm.bedrock import BedrockProvider, to_converse_messages
from groupflow.sim.llm.factory import MissingProvider, create_provider
from groupflow.sim.llm.openai_compat import OpenAiCompatibleProvider
from groupflow.sim.llm.types import (AssistantMessage, LlmError, LlmRequest, LlmToolCall, LlmToolDef, ToolMessage, UserMessage,
                                     parse_arguments)

TOOLS = [LlmToolDef(name="get_briefing", description="Briefing",
                    parameters={"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "properties": {"mark_read": {"type": "boolean"}}})]

REQ = LlmRequest(
    system="You are a test.",
    tools=TOOLS,
    messages=[
        UserMessage(content="What's new?"),
        AssistantMessage(content="", tool_calls=[LlmToolCall(id="c1", name="get_briefing", arguments={}, raw_arguments="{}")], reasoning="thinking text"),
        ToolMessage(tool_call_id="c1", name="get_briefing", content={"headline": "All caught up."}, is_error=False),
    ],
)

OK_TOOL_CALL = (200, {
    "choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [
        {"id": "x1", "type": "function", "function": {"name": "propose_fact", "arguments": '{"slot":"party.date","value":"Sunday","certainty":"committed"}'}}]}}],
    "usage": {"prompt_tokens": 1200, "completion_tokens": 40, "prompt_cache_hit_tokens": 1000},
})


def fake(responses: list, seen: list) -> httpx.MockTransport:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if not queue:
            raise AssertionError("no more fake responses")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        status, body = item
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


def deepseek(transport, **kw) -> OpenAiCompatibleProvider:
    return OpenAiCompatibleProvider(id="deepseek", label="DeepSeek", base_url=kw.pop("base_url", "https://api.deepseek.com"),
                                    api_key=kw.pop("api_key", "k"), model=kw.pop("model", "deepseek-flash"), transport=transport, **kw)


async def test_deepseek_request_endpoint_auth_model_thinking_off_and_message_format():
    seen: list[httpx.Request] = []
    p = deepseek(fake([OK_TOOL_CALL], seen), base_url="https://api.deepseek.com/", api_key="sk-test")
    await p.complete(REQ)
    req = seen[0]
    assert str(req.url) == "https://api.deepseek.com/chat/completions"
    assert req.headers["authorization"] == "Bearer sk-test"
    body = json.loads(req.content)
    assert body["model"] == "deepseek-flash"
    assert body["thinking"] == {"type": "disabled"}
    assert body["temperature"] == 0.2
    assert body["tool_choice"] == "auto"
    assert body["tools"][0]["type"] == "function" and body["tools"][0]["function"]["name"] == "get_briefing"
    assert "$schema" not in body["tools"][0]["function"]["parameters"]
    assert [m["role"] for m in body["messages"]] == ["system", "user", "assistant", "tool"]
    assert body["messages"][2]["content"] is None
    assert body["messages"][2]["tool_calls"][0]["function"]["arguments"] == "{}"
    assert "reasoning_content" not in body["messages"][2], "reasoning is not sent when thinking is off"
    assert body["messages"][3]["tool_call_id"] == "c1"
    assert json.loads(body["messages"][3]["content"])["headline"] == "All caught up."


async def test_deepseek_response_tool_calls_and_usage_with_cache_hits():
    r = await deepseek(fake([OK_TOOL_CALL], [])).complete(REQ)
    assert r.stop == "tool_calls"
    assert r.tool_calls[0].arguments == {"slot": "party.date", "value": "Sunday", "certainty": "committed"}
    assert (r.usage.input_tokens, r.usage.output_tokens, r.usage.cached_input_tokens) == (1200, 40, 1000)


async def test_thinking_mode_sends_reasoning_back_and_omits_temperature():
    seen: list[httpx.Request] = []
    await deepseek(fake([OK_TOOL_CALL], seen), thinking="enabled").complete(REQ)
    body = json.loads(seen[0].content)
    assert body["thinking"] == {"type": "enabled"}
    assert "temperature" not in body
    assert body["messages"][2]["reasoning_content"] == "thinking text"


def test_invalid_tool_arguments_are_reported_not_raised():
    bad = parse_arguments("a", "propose_fact", '{"slot": "party.date", ')
    assert bad.arguments is None and "not valid JSON" in bad.parse_error
    arr = parse_arguments("b", "x", "[1,2]")
    assert "JSON object" in arr.parse_error
    assert parse_arguments("c", "x", "").arguments == {}


async def test_http_errors_become_readable_and_429_5xx_are_retried():
    with pytest.raises(LlmError, match="API key was rejected") as e401:
        await deepseek(fake([(401, {"error": {"message": "Authentication Fails"}})], []), api_key="bad").complete(REQ)
    assert e401.value.status == 401
    with pytest.raises(LlmError, match="no balance left"):
        await deepseek(fake([(402, {"error": {"message": "Insufficient Balance"}})], [])).complete(REQ)

    seen: list[httpx.Request] = []
    r = await deepseek(fake([(429, {}), (503, {}), OK_TOOL_CALL], seen), retry_delay_s=0).complete(REQ)
    assert len(seen) == 3 and len(r.tool_calls) == 1


async def test_network_failures_and_timeouts_name_the_endpoint():
    with pytest.raises(LlmError, match=r"Could not reach DeepSeek at https://api.deepseek.com"):
        await deepseek(fake([httpx.ConnectError("connection refused")], []), max_retries=0).complete(REQ)

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return httpx.Response(200, json={})

    with pytest.raises(LlmError, match="did not answer within"):
        await deepseek(httpx.MockTransport(slow), max_retries=0, timeout_s=0.05).complete(REQ)


async def test_api_key_never_appears_in_error_messages():
    with pytest.raises(LlmError) as err:
        await deepseek(fake([(401, {"error": {"message": "Authentication Fails"}})], []), api_key="sk-secret-123").complete(REQ)
    assert "sk-secret-123" not in str(err.value)


def test_bedrock_tool_results_travel_in_a_user_message():
    converse = to_converse_messages(REQ.messages)
    assert [m["role"] for m in converse] == ["user", "assistant", "user"]
    assert converse[1]["content"][0]["toolUse"]["name"] == "get_briefing"
    assert converse[2]["content"][0]["toolResult"]["toolUseId"] == "c1"


async def test_bedrock_tool_use_blocks_come_back_as_tool_calls():
    sent: list[dict] = []

    class FakeClient:
        def converse(self, **kwargs):
            sent.append(kwargs)
            return {"stopReason": "tool_use", "usage": {"inputTokens": 900, "outputTokens": 30},
                    "output": {"message": {"role": "assistant", "content": [{"toolUse": {"toolUseId": "t9", "name": "query_project_state", "input": {"topic": "cake"}}}]}}}

    p = BedrockProvider(model_id="us.amazon.nova-pro-v1:0", region="us-east-1", client=FakeClient())
    r = await p.complete(REQ)
    assert r.stop == "tool_calls"
    tc = r.tool_calls[0]
    assert (tc.id, tc.name, tc.arguments, tc.raw_arguments) == ("t9", "query_project_state", {"topic": "cake"}, '{"topic": "cake"}')
    assert sent[0]["modelId"] == "us.amazon.nova-pro-v1:0"
    assert sent[0]["system"][0]["text"] == "You are a test."
    assert "$schema" not in sent[0]["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]


async def test_factory_explains_what_is_missing_instead_of_failing_at_startup():
    base = dict(provider="deepseek", deepseek_api_key=None, deepseek_base_url="https://api.deepseek.com", deepseek_model="deepseek-flash",
                deepseek_thinking="disabled", aws_region="us-east-1", bedrock_model_id="m", timeout_s=1.0)
    none = create_provider(LlmSettings(**base))
    assert isinstance(none, MissingProvider) and "DEEPSEEK_API_KEY" in none.reason
    with pytest.raises(LlmError, match="DEEPSEEK_API_KEY"):
        await none.complete(REQ)
    ds = create_provider(LlmSettings(**{**base, "deepseek_api_key": "sk-x"}))
    assert (ds.id, ds.model) == ("deepseek", "deepseek-flash")
    assert create_provider(LlmSettings(**{**base, "provider": "bedrock"})).id == "bedrock"
    assert "Unknown AGENT_PROVIDER" in create_provider(LlmSettings(**{**base, "provider": "nope"})).reason
