"""Shared plumbing for MCP tools: who is calling, error results, call logging."""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from mcp.server.mcpserver import Context
from mcp_types import CallToolResult, TextContent
from pydantic import BaseModel

from groupflow.core.errors import DomainError
from groupflow.core.identity import Member
from groupflow.engine.core import ASSISTANT, LISTENERS

log = logging.getLogger("groupflow.mcp")
T = TypeVar("T", bound=BaseModel)

@dataclass
class ToolCallLog:
    member: str
    tool: str
    ok: bool
    summary: str
    ms: float


def caller(ctx: Context) -> Member:
    """The authenticated member, set by the HTTP layer from the bearer token. Never from tool arguments."""
    req = ctx.request_context.request if ctx is not None else None
    state = req.scope.get("state", {}) if req is not None else {}
    member = state.get("member")
    if not isinstance(member, Member):
        raise DomainError("UNAUTHENTICATED", "This connection has no authenticated member.")
    return member


def listeners_of(ctx: Context) -> frozenset[str] | None:
    """Who else the host said will hear this reply (resolved and validated by the HTTP layer), or None."""
    req = ctx.request_context.request if ctx is not None else None
    state = req.scope.get("state", {}) if req is not None else {}
    value = state.get("listeners")
    return value if isinstance(value, frozenset) else None


def error_result(err: DomainError) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=json.dumps({"error": err.to_json()}))], is_error=True)


def summarize(result: BaseModel | dict) -> str:
    data = result.model_dump() if isinstance(result, BaseModel) else result
    for key in ("outcome", "status", "headline"):
        if isinstance(data.get(key), str):
            return data[key]
    if isinstance(data.get("message"), str):
        return data["message"][:80]
    return "ok"


class ToolRunner:
    """Runs a tool body for the calling member, maps DomainError to a tool error result, and logs the call."""

    def __init__(self, on_call: Callable[[ToolCallLog], None] | None = None):
        self.on_call = on_call

    def run(self, ctx: Context, tool: str, fn: Callable[[Member], T]) -> T | CallToolResult:
        started = time.perf_counter()
        member_id = "?"
        token = None
        assistant = ASSISTANT.set(True)
        try:
            member = caller(ctx)
            member_id = member.id
            listeners = listeners_of(ctx)
            token = LISTENERS.set((member.id, listeners) if listeners else None)
            result = fn(member)
            self._log(member_id, tool, True, summarize(result), started)
            return result
        except DomainError as err:
            self._log(member_id, tool, False, f"{err.code}: {err.message}", started)
            return error_result(err)
        except Exception:  # noqa: BLE001 - never leak internals to the client
            log.exception("tool %s failed", tool)
            self._log(member_id, tool, False, "INTERNAL", started)
            return error_result(DomainError("INTERNAL", "Unexpected server error."))
        finally:
            if token is not None:
                LISTENERS.reset(token)
            ASSISTANT.reset(assistant)

    def _log(self, member: str, tool: str, ok: bool, summary: str, started: float) -> None:
        if self.on_call:
            self.on_call(ToolCallLog(member=member, tool=tool, ok=ok, summary=summary, ms=(time.perf_counter() - started) * 1000))


def annotations(read_only: bool, idempotent: bool = False, destructive: bool = False) -> Any:
    from mcp_types import ToolAnnotations

    return ToolAnnotations(read_only_hint=read_only, destructive_hint=destructive, idempotent_hint=idempotent, open_world_hint=False)
