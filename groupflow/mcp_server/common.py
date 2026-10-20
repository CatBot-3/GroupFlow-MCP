"""Shared plumbing for MCP tools: who is calling, error results, call logging."""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from mcp.server.mcpserver import Context
from mcp_types import CallToolResult, TextContent
from pydantic import BaseModel

from groupflow.core.errors import DomainError
from groupflow.core.identity import Member
from groupflow.engine.core import ASSISTANT, LISTENERS, ROOM
from groupflow.core.tracing import REQUEST_ID

log = logging.getLogger("groupflow.mcp")
T = TypeVar("T", bound=BaseModel)

@dataclass
class ToolCallLog:
    member: str
    tool: str
    ok: bool
    summary: str
    ms: float
    request_id: str = ''


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


def audience_of(ctx: Context) -> str:
    """private, shared or undeclared (the host said nothing about who hears the reply)."""
    req = ctx.request_context.request if ctx is not None else None
    state = req.scope.get("state", {}) if req is not None else {}
    value = state.get("audience")
    return value if value in ("private", "shared", "undeclared") else "private"


def arguments_of(ctx: Context) -> dict:
    """The public SDK request context retains the arguments of tools/call."""
    params = getattr(ctx.request_context, 'params', None) if ctx is not None else None
    arguments = params.get('arguments') if isinstance(params, Mapping) else None
    return dict(arguments) if isinstance(arguments, Mapping) else {}


def error_result(err: DomainError, words: Callable[[str], str] | None = None) -> CallToolResult:
    text = json.dumps({"error": err.to_json()})
    if words is not None:
        text = words(text)
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)


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

    def __init__(self, on_call: Callable[[ToolCallLog], None] | None = None, engine=None, words: Callable[[str], str] | None = None):
        self.on_call = on_call
        self.engine = engine
        self.words = words  # the voice profile rewrites lab tool names in error text

    def run(self, ctx: Context, tool: str, fn: Callable[[Member], T], *, wire: bool = True, capture: bool = True,
            op: str | None = None, arguments: dict | None = None) -> T | CallToolResult:
        """op names the engine operation a voice tool performed (its receipt and retry guard follow that operation), and arguments
        are the engine-shaped arguments recorded with it; both default to the tool's own name and wire arguments."""
        started = time.perf_counter()
        member_id = "?"
        token = None
        assistant = ASSISTANT.set(True)
        room = ROOM.set(audience_of(ctx) == "undeclared")
        try:
            member = caller(ctx)
            member_id = member.id
            listeners = listeners_of(ctx)
            token = LISTENERS.set((member.id, listeners) if listeners else None)
            from groupflow.engine.product import RECEIPT_TOOLS, WORKFLOW_TOOLS
            operation = op or tool
            if capture and self.engine is not None and operation in RECEIPT_TOOLS:
                record = self.engine.capture_workflow if operation in WORKFLOW_TOOLS else self.engine.capture_action
                recorded = {k: v for k, v in arguments.items() if v is not None} if arguments is not None else arguments_of(ctx)
                result = record(member.id, operation, lambda: fn(member), arguments=recorded)
            else:
                result = fn(member)
            self._log(member_id, tool, True, summarize(result), started)
            if wire and isinstance(result, dict) and ('action_receipt' in result or self.words is not None):
                return CallToolResult(content=[TextContent(type='text', text=json.dumps(result))], structured_content=result)
            return result
        except DomainError as err:
            self._log(member_id, tool, False, f"{err.code}: {err.message}", started)
            return error_result(err, self.words)
        except Exception:  # noqa: BLE001 - never leak internals to the client
            log.exception("tool %s failed", tool)
            self._log(member_id, tool, False, "INTERNAL", started)
            return error_result(DomainError("INTERNAL", "Unexpected server error."))
        finally:
            if token is not None:
                LISTENERS.reset(token)
            ROOM.reset(room)
            ASSISTANT.reset(assistant)

    def _log(self, member: str, tool: str, ok: bool, summary: str, started: float) -> None:
        if self.on_call:
            self.on_call(ToolCallLog(member=member, tool=tool, ok=ok, summary=summary, ms=(time.perf_counter() - started) * 1000,request_id=REQUEST_ID.get()))


def annotations(read_only: bool, idempotent: bool = False, destructive: bool = False) -> Any:
    from mcp_types import ToolAnnotations

    return ToolAnnotations(read_only_hint=read_only, destructive_hint=False if read_only else destructive,
                           idempotent_hint=idempotent, open_world_hint=False)


# MCP treats a tool that is not read-only as destructive unless told otherwise, and hosts use these hints to decide when to ask
# the person first. "Destructive" means it can overwrite or remove something that exists (a value, an approval round, an agreed
# plan, a claim, an expense); "additive" tools only add records. No tool reaches outside GroupFlow (openWorldHint false).
READ_ONLY = {"resolve_action", "search_plan", "get_alternatives", "explain", "get_history", "list_templates", "get_template", "get_project",
             "ask_about"}
ADDITIVE = {"create_plan", "create_group", "invite_to_group", "notify_people", "ask_followup", "report_observation", "find_alternatives",
            "repair_project", "get_context", "catch_up", "start_plan", "invite_people", "search_brief"}
IDEMPOTENT = {"get_context", "catch_up", "link_items", "rsvp", "review_plan", "search_plan", "get_alternatives", "explain", "get_history",
              "list_templates", "get_template", "get_project", "resolve_action", "ask_about", "search_brief"}


def hints(tool: str) -> Any:
    read_only = tool in READ_ONLY
    return annotations(read_only, idempotent=tool in IDEMPOTENT, destructive=not read_only and tool not in ADDITIVE)
