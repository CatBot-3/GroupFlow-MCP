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
    assert create_provider(LlmSettings(**{**base, "provider": "relay"})).reason.endswith("Use deepseek, anthropic, bedrock or openai.")


def test_dotenv_lines_copied_from_the_example_mean_what_they_say(tmp_path, monkeypatch):
    from groupflow.core.settings import _load_dotenv
    keys = ["GF_T_KEY", "GF_T_EMPTY", "GF_T_INLINE", "GF_T_QUOTED", "GF_T_HASH", "GF_T_REAL"]
    for k in keys:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GF_T_REAL", "from-the-shell")
    (tmp_path / ".env").write_text("\n".join([
        "# a comment",
        "GF_T_KEY=sk-123",
        "GF_T_EMPTY=",
        "GF_T_INLINE=open            # open, invite or off",
        'GF_T_QUOTED="a # b"',
        "GF_T_HASH=# only a comment",
        "GF_T_REAL=from-the-file",
    ]), encoding="utf-8")
    _load_dotenv(tmp_path / ".env")
    import os
    assert os.environ["GF_T_KEY"] == "sk-123"
    assert "GF_T_EMPTY" not in os.environ and "GF_T_HASH" not in os.environ, "an unfilled line counts as not set"
    assert os.environ["GF_T_INLINE"] == "open"
    assert os.environ["GF_T_QUOTED"] == "a # b"
    assert os.environ["GF_T_REAL"] == "from-the-shell", "a real environment variable wins over the file"


ANTHROPIC_TOOL_USE = (200, {
    "content": [{"type": "text", "text": "Let me check."},
                {"type": "tool_use", "id": "toolu_1", "name": "propose_fact", "input": {"slot": "party.date", "value": "Sunday"}}],
    "stop_reason": "tool_use",
    "usage": {"input_tokens": 200, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 0, "output_tokens": 30},
})


def claude(transport, **kw):
    from groupflow.sim.llm.anthropic import AnthropicProvider
    return AnthropicProvider(api_key=kw.pop("api_key", "k"), model=kw.pop("model", "claude-sonnet-5-5"), transport=transport, retry_delay_s=0, **kw)


async def test_anthropic_request_uses_the_messages_api_with_tool_results_in_a_user_turn():
    seen: list[httpx.Request] = []
    p = claude(fake([ANTHROPIC_TOOL_USE], seen), api_key="sk-ant-test", base_url="https://api.anthropic.com/")
    res = await p.complete(REQ)
    sent = seen[0]
    assert str(sent.url) == "https://api.anthropic.com/v1/messages"
    assert sent.headers["x-api-key"] == "sk-ant-test" and sent.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(sent.content)
    assert body["model"] == "claude-sonnet-5-5" and "temperature" not in body
    assert body["system"] == [{"type": "text", "text": "You are a test.", "cache_control": {"type": "ephemeral"}}]
    assert body["tools"][0]["name"] == "get_briefing" and "$schema" not in body["tools"][0]["input_schema"]
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
    assert body["messages"][1]["content"] == [{"type": "tool_use", "id": "c1", "name": "get_briefing", "input": {}}]
    result = body["messages"][2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "c1" and result["is_error"] is False
    assert json.loads(result["content"]) == {"headline": "All caught up."}
    assert res.stop == "tool_calls" and res.text == "Let me check."
    assert res.tool_calls[0].name == "propose_fact" and res.tool_calls[0].arguments == {"slot": "party.date", "value": "Sunday"}
    assert (res.usage.input_tokens, res.usage.cached_input_tokens, res.usage.output_tokens) == (1200, 1000, 30)


def test_anthropic_consecutive_tool_results_share_one_user_turn_and_odd_ids_are_mapped():
    from groupflow.sim.llm.anthropic import to_messages
    calls = [LlmToolCall(id="a:1", name="explain", arguments={}, raw_arguments="{}"),
             LlmToolCall(id="b.2", name="search_plan", arguments={"q": "x"}, raw_arguments='{"q":"x"}')]
    msgs = to_messages([UserMessage(content="Why not 8?"), AssistantMessage(content="", tool_calls=calls),
                        ToolMessage(tool_call_id="a:1", name="explain", content="blocked", is_error=False),
                        ToolMessage(tool_call_id="b.2", name="search_plan", content={"error": "no"}, is_error=True)])
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert [b["id"] for b in msgs[1]["content"]] == ["a_1", "b_2"]
    assert [(b["tool_use_id"], b["is_error"]) for b in msgs[2]["content"]] == [("a_1", False), ("b_2", True)]


async def test_anthropic_overload_is_retried_and_a_rejected_key_is_not_echoed():
    seen: list[httpx.Request] = []
    ok = (200, {"content": [{"type": "text", "text": "Hi."}], "stop_reason": "end_turn", "usage": {"input_tokens": 5, "output_tokens": 2}})
    p = claude(fake([(529, {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}), ok], seen))
    res = await p.complete(REQ)
    assert len(seen) == 2 and res.text == "Hi." and res.stop == "end"
    seen.clear()
    bad = claude(fake([(401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})], seen), api_key="sk-ant-secret")
    with pytest.raises(LlmError) as err:
        await bad.complete(REQ)
    assert len(seen) == 1 and "ANTHROPIC_API_KEY" in str(err.value) and "sk-ant-secret" not in str(err.value)


def test_factory_builds_anthropic_only_with_a_key():
    base = dict(provider="anthropic", deepseek_api_key=None, deepseek_base_url="https://api.deepseek.com", deepseek_model="deepseek-flash",
                deepseek_thinking="disabled", anthropic_api_key=None, anthropic_base_url="https://api.anthropic.com",
                anthropic_model="claude-sonnet-5-5", aws_region="us-east-1", bedrock_model_id="m", timeout_s=1.0)
    missing = create_provider(LlmSettings(**base))
    assert isinstance(missing, MissingProvider) and "ANTHROPIC_API_KEY" in missing.reason
    ready = create_provider(LlmSettings(**{**base, "anthropic_api_key": "sk-ant-x"}))
    assert (ready.id, ready.label, ready.model) == ("anthropic", "Anthropic", "claude-sonnet-5-5")
