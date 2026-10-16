"""Provider-neutral shapes for talking to a tool-using LLM. The agent speaks only
this interface; each provider translates it to its own API."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, Union


@dataclass
class LlmToolDef:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema taken from the MCP server's tools/list


@dataclass
class LlmToolCall:
    id: str
    name: str
    arguments: dict[str, Any] | None  # None when the model produced invalid JSON (see parse_error)
    raw_arguments: str
    parse_error: str | None = None


@dataclass
class UserMessage:
    content: str
    role: Literal["user"] = "user"


@dataclass
class AssistantMessage:
    content: str
    tool_calls: list[LlmToolCall] = field(default_factory=list)
    reasoning: str | None = None
    role: Literal["assistant"] = "assistant"


@dataclass
class ToolMessage:
    tool_call_id: str
    name: str
    content: Any
    is_error: bool
    role: Literal["tool"] = "tool"


LlmMessage = Union[UserMessage, AssistantMessage, ToolMessage]


@dataclass
class LlmRequest:
    system: str
    messages: list[LlmMessage]
    tools: list[LlmToolDef]
    max_tokens: int = 900


@dataclass
class LlmUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0


@dataclass
class LlmResponse:
    text: str
    tool_calls: list[LlmToolCall]
    reasoning: str | None
    stop: Literal["tool_calls", "end", "length", "other"]
    usage: LlmUsage
    ms: float


class LlmProvider(Protocol):
    id: str
    label: str
    model: str
    reason: str | None

    async def complete(self, req: LlmRequest) -> LlmResponse: ...


class LlmError(Exception):
    """A failure talking to the model. The message is written for the person running the demo."""

    def __init__(self, message: str, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def clean_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    """Remove redundant schema metadata, preserving fields, constraints and references.

    A property can itself be named 'title', so recurse through schema positions
    rather than dropping matching keys from arbitrary dictionaries.
    """
    maps = {"properties", "$defs", "definitions", "patternProperties", "dependentSchemas"}
    arrays = {"allOf", "anyOf", "oneOf", "prefixItems"}
    singles = {"items", "additionalProperties", "not", "if", "then", "else", "contains", "propertyNames"}

    def trim(node: Any) -> Any:
        if not isinstance(node, dict):
            return node
        out = {}
        for key, value in node.items():
            if key in {"$schema", "title"}:
                continue
            if key in maps:
                value = {name: trim(child) for name, child in value.items()}
            elif key in arrays or (key == "items" and isinstance(value, list)):
                value = [trim(child) for child in value]
            elif key in singles:
                value = trim(value)
            out[key] = value
        return out

    out = trim(schema or {})
    out.setdefault("type", "object")
    out.setdefault("properties", {})
    return out


def parse_arguments(call_id: str, name: str, raw: str | None) -> LlmToolCall:
    raw = raw or ""
    if not raw.strip():
        return LlmToolCall(id=call_id, name=name, arguments={}, raw_arguments=raw)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as err:
        return LlmToolCall(id=call_id, name=name, arguments=None, raw_arguments=raw, parse_error=f"Arguments were not valid JSON: {err}")
    if not isinstance(parsed, dict):
        return LlmToolCall(id=call_id, name=name, arguments=None, raw_arguments=raw, parse_error="Arguments must be a JSON object.")
    return LlmToolCall(id=call_id, name=name, arguments=parsed, raw_arguments=raw)
