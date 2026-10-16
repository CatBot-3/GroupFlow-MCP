"""Amazon Bedrock provider on the Converse API (any Bedrock model with tool use).
Credentials come from the standard AWS SDK chain (aws configure, AWS_PROFILE,
or AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from .types import AssistantMessage, LlmError, LlmMessage, LlmRequest, LlmResponse, LlmToolCall, LlmUsage, ToolMessage, UserMessage, clean_schema


def to_converse_messages(messages: list[LlmMessage]) -> list[dict[str, Any]]:
    """Converse wants strict user/assistant alternation; tool results travel in a user message."""
    out: list[dict[str, Any]] = []

    def push(role: str, blocks: list[dict[str, Any]]) -> None:
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})

    for m in messages:
        if isinstance(m, UserMessage):
            push("user", [{"text": m.content}])
        elif isinstance(m, AssistantMessage):
            blocks: list[dict[str, Any]] = []
            if m.content:
                blocks.append({"text": m.content})
            for tc in m.tool_calls:
                blocks.append({"toolUse": {"toolUseId": tc.id, "name": tc.name, "input": tc.arguments or {}}})
            push("assistant", blocks or [{"text": "..."}])
        elif isinstance(m, ToolMessage):
            content = m.content if isinstance(m.content, dict) else {"result": m.content}
            push("user", [{"toolResult": {"toolUseId": m.tool_call_id, "content": [{"json": content}], "status": "error" if m.is_error else "success"}}])
    return out


class BedrockProvider:
    id = "bedrock"
    label = "Amazon Bedrock"

    def __init__(self, model_id: str, region: str, client: Any = None, temperature: float = 0.2):
        self.model = model_id
        self.region = region
        self.reason: str | None = None
        self.temperature = temperature
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            import boto3  # imported lazily so the demo starts without AWS set up

            self._client = boto3.client("bedrock-runtime", region_name=self.region)
        return self._client

    async def complete(self, req: LlmRequest) -> LlmResponse:
        started = time.perf_counter()
        kwargs: dict[str, Any] = {
            "modelId": self.model,
            "system": [{"text": req.system}],
            "messages": to_converse_messages(req.messages),
            "inferenceConfig": {"maxTokens": req.max_tokens, "temperature": self.temperature},
        }
        if req.tools:
            kwargs["toolConfig"] = {"tools": [{"toolSpec": {"name": t.name, "description": t.description,
                                                            "inputSchema": {"json": clean_schema(t.parameters)}}} for t in req.tools]}
        try:
            out = await asyncio.to_thread(lambda: self._get_client().converse(**kwargs))
        except Exception as err:  # noqa: BLE001 - botocore raises many types
            name = type(err).__name__
            code = getattr(err, "response", {}).get("Error", {}).get("Code", name) if hasattr(err, "response") else name
            status = getattr(err, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode") if hasattr(err, "response") else None
            hint = ""
            if code == "AccessDeniedException":
                hint = " Enable access to this model in the Bedrock console and check your AWS credentials."
            elif name in ("NoCredentialsError", "PartialCredentialsError"):
                hint = ' No AWS credentials were found; run "aws configure" or set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY.'
            raise LlmError(f"Amazon Bedrock: {err}.{hint}", status, code == "ThrottlingException" or (status or 0) >= 500) from err
        content = (out.get("output") or {}).get("message", {}).get("content", [])
        calls = [LlmToolCall(id=b["toolUse"]["toolUseId"], name=b["toolUse"]["name"], arguments=b["toolUse"].get("input") or {},
                             raw_arguments=json.dumps(b["toolUse"].get("input") or {})) for b in content if "toolUse" in b]
        stop_reason = out.get("stopReason")
        usage = out.get("usage") or {}
        return LlmResponse(
            text=" ".join(b.get("text", "") for b in content).strip(), tool_calls=calls, reasoning=None,
            stop="tool_calls" if calls or stop_reason == "tool_use" else "length" if stop_reason == "max_tokens" else "end" if stop_reason == "end_turn" else "other",
            usage=LlmUsage(input_tokens=usage.get("inputTokens", 0), output_tokens=usage.get("outputTokens", 0), cached_input_tokens=usage.get("cacheReadInputTokens", 0)),
            ms=(time.perf_counter() - started) * 1000,
        )
