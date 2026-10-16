"""Test double for an LLM: replays scripted responses and records every request.
Steps can be functions of the request so a script can react to tool results."""
from __future__ import annotations

import copy
import json
from collections.abc import Callable
from typing import Any, Union

from .types import LlmError, LlmRequest, LlmResponse, LlmUsage, ToolMessage, parse_arguments

ScriptedReply = dict[str, Any]  # {"text": str} or {"tool_calls": [{"name": str, "arguments": dict | str}]}
ScriptStep = Union[ScriptedReply, Callable[[LlmRequest], ScriptedReply]]


class ScriptedProvider:
    id = "scripted"
    label = "Scripted test model"
    model = "script"
    reason = None

    def __init__(self, steps: list[ScriptStep] | None = None):
        self.steps: list[ScriptStep] = list(steps or [])
        self.requests: list[LlmRequest] = []
        self._calls = 0

    def push(self, *steps: ScriptStep) -> None:
        self.steps.extend(steps)

    async def complete(self, req: LlmRequest) -> LlmResponse:
        self.requests.append(copy.deepcopy(req))
        if not self.steps:
            raise LlmError("Scripted model ran out of responses.")
        step = self.steps.pop(0)
        reply = step(req) if callable(step) else step
        calls = []
        for tc in reply.get("tool_calls", []):
            self._calls += 1
            args = tc.get("arguments", {})
            raw = args if isinstance(args, str) else json.dumps(args)
            calls.append(parse_arguments(f"call_{self._calls}", tc["name"], raw))
        return LlmResponse(text=reply.get("text", ""), tool_calls=calls, reasoning=None, stop="tool_calls" if calls else "end",
                           usage=LlmUsage(input_tokens=100, output_tokens=20), ms=1.0)


def last_tool_result(req: LlmRequest, tool: str) -> Any:
    for m in reversed(req.messages):
        if isinstance(m, ToolMessage) and m.name == tool:
            return m.content
    return None
