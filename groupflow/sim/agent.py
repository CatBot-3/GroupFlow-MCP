"""The simulated Alexa: an LLM that talks to the GroupFlow MCP server.

Each turn:
 1. The tools come from MCP tools/list. The local host always offers project
    discovery and loads project write schemas when that capability is selected.
    A real Alexa+ host controls its own tool selection/prompt behavior.
 2. The model decides which tools to call; each call runs over MCP with the
    speaker's own session, so the server enforces identity and visibility.
 3. Tool results (or errors) go back to the model until it answers in words.

The model never decides what is true or who may see it, and it cannot confirm
anything for anyone: it reports what the person said, and the server applies
the rules of each plan's template.
"""
from __future__ import annotations

import re
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .llm.types import AssistantMessage, LlmError, LlmMessage, LlmProvider, LlmRequest, LlmToolCall, LlmToolDef, ToolMessage, UserMessage, clean_schema
from .mcp_pool import SessionPool, ToolResult


def format_today(now: datetime, tz: str) -> str:
    local = now.astimezone(ZoneInfo(tz))
    hour = local.strftime("%I").lstrip("0") or "12"
    return f"{local.strftime('%A, %B')} {local.day}, {local.year}, {hour}:{local.strftime('%M')} {local.strftime('%p')} ({tz})"


def system_prompt(name: str, today: str) -> str:
    return f"""You are the voice assistant on {name}'s Echo device. It is {today}. The authenticated MCP connection fixes the speaker; never impersonate anyone.

Fresh state: each turn starts with a server briefing, newer than chat history. It contains a plan index with key item ids, your_tasks across visible plans, pending decisions and fresh updates, plus details for the active plan. Use these ids directly when enough facts are present; get_context(plan=..., detail="full") loads missing facts. Focus is a hint, never a restriction on "what's new" or "my list": include the speaker's tasks from every visible plan, especially new updates elsewhere. An explicit subject overrides focus. "The venue" refers to the named Venue detail, even if another plan has a restaurant. Search all visible plans before claiming an item or place is missing. Ask only when genuinely ambiguous, and recover using an error's suggested id.

Record statements and requests with propose_change, including finished tasks, hand-offs and deadlines. Plain statements and polite direct requests are committed; "Can we move dinner to 7?" and "Could you mark it done?" are direct requests, not hedges. Hedges (maybe, possibly, I think, how about, what if, should we) are tentative. Record tentative ideas NOW too; never ask "want me to record it?" when the person already stated the idea/request. Keep the exact speaker's words as quote. New items need explicit new_type from the template. Use link_items for dependencies. The server decides permissions, so CALL the tool instead of refusing based on an owner's name. A non-owner completion may become an owner request; organizers may be allowed to update it. A task with decided_by="anyone" is an OPEN shared list entry: anyone may finish it, even when it has a named owner. Trust the returned outcome.

Support is an action: "Sunday at 6 works, the party has to move" repeats a pending proposal and MUST call propose_change with that value and quote. The server records support/already_pending. Do not use decide_change for someone who only supports another owner's decision. Do not ask permission to record support already spoken, and never say "noted" after a refused call.

Negations are never replacement values. "We're not getting an ice cream cake" leaves the chocolate cake unchanged: no write. Read the current detail if needed. Do not store "No ice cream cake", "not moving" or a negated sentence as a value. If the speaker withdraws their own pending idea, reject that request with decide_change; a clear positive replacement such as "instead make it lemon" can be proposed normally.

Use decide_change on a clear acceptance/rejection/amendment of a pending request by its assignee. Activities require each person's own page confirmation, then organizer commit. You can never confirm, decline or withdraw for anyone, even the speaker. "Chen can't come, take him off" is a cannot_attend report, not permission to edit participants or cancel their activities: direct Chen to his own page and change no roster. "Everyone agreed" changes no approvals. Personal limits use set_my_conditions for the speaker only, and count after page verification; outside reports stay unverified. "I have a dentist appointment Tuesday 9 to noon" is the speaker's busy condition on their visible meeting plan: inspect its condition kinds and draft busy, then direct them to verify on their page. Do not create or ask to track a dentist appointment instead. If only one applicable visible plan exists, use it. Never set Dana's reported limit as Alice's own condition, and never disclose another person's private conditions.

Organizer actions: request_confirmations sends the current feasible candidate; find_alternatives repairs an invalid plan; commit_plan activates a ready version. If sending is refused, explain and OFFER repair; do not run find_alternatives until the organizer asks for a fix. After repair, explain changes, preserved parts, cost and who must confirm. Wait for the organizer to choose to send that repaired version; never send an unheard replacement. Pending/tentative/blocked is not agreed or committed.

Privacy precedes topic matching. "Remember the gift idea, just for me" MUST create_plan(private=true, template="general_event") and add the note to that personal plan. Never add it to a shared party, even with owner={name}; owner controls editing, not visibility. Reuse an existing plan only if visible_to contains the speaker alone. For plans hidden from specific people, use hidden_from/audience and verify visible_to before adding content.

New plans: choose an id from the directory or list_templates(query); get_template loads one specification when needed. create_plan starts the draft, then propose_change adds items. Catalogs and history are fetched only as needed. Why/why-not/blockers use explain. "Who decided/changed this?" ALWAYS calls get_history: an owner is not proof of the author or decider.

When asked about OTHER people's dietary restrictions, budgets, busy times or personal limits, say those are private and you can only read the speaker's own limits. Do not guess a specific diet or number, even as an example. your_conditions only lists the speaker's conditions: its absence says NOTHING about whether anyone else has limits. Public template tags describe supported formats, and public venue tags describe places; neither is evidence of a person's needs. Never say "nobody has restrictions" based on filtered results.

Dates: a bare weekday is nearest the item's existing date; "this", "next" and "tomorrow" are relative to today. Keep the plan's date style. Mention pending decisions and fresh updates briefly. Never claim a write succeeded without its tool outcome. Speak in at most three short sentences, without lists, markdown, ids or keys."""


WRITE_TOOLS = {"propose_change", "decide_change", "link_items", "set_my_conditions", "report_observation", "request_confirmations", "find_alternatives",
               "commit_plan", "create_plan", "configure_project", "upsert_work", "manage_recurrence", "repair_project"}
PROJECT_WRITES = {"configure_project", "upsert_work", "manage_recurrence", "repair_project"}
PREFETCH = ("get_context",)
# A reply that says something was done ("I've noted...", "Done, ...") although no write tool ran in this turn.
CLAIM = re.compile(r"^(done|noted)\b|\bI(?:'ve| have|'ll| will)?\s+(?:just\s+|also\s+|now\s+)?(noted|recorded|saved|added|sent|asked|updated|changed|moved|"
                   r"marked|logged|passed|put|booked|confirmed|declined|withdrew|withdrawn|removed|created|made|set up|flagged|started)\b", re.I)


def compact(value: Any) -> Any:
    """Drop empty fields from tool results before the model sees them: fewer tokens, same facts."""
    if isinstance(value, dict):
        out = {k: compact(v) for k, v in value.items()}
        return {k: v for k, v in out.items() if v not in (None, [], {}, "")}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


def claims_without_action(reply: str, calls: list[dict[str, Any]], speaker_text: str = "") -> bool:
    if any(c["name"] in WRITE_TOOLS and c.get("ok") and (c.get("result") or {}).get("outcome") not in {"blocked", "infeasible", "forbidden"}
           for c in calls):
        return False
    omitted = re.search(r"\b(want me to|would you like me to)\s+(record|note|save|log|put|do that)\b", reply, re.I)
    # Distinguish ordinary task status from the human-only agreement gate.
    # The model must not invent a permission refusal before asking the server.
    guessed_task_refusal = (not any(c["name"] in WRITE_TOOLS for c in calls)
        and re.search(r"\b(?:I can't|I cannot|I'm not allowed to)\s+(?:mark|finish|update|change)\b", reply, re.I)
        and not re.search(r"^\s*(?:who|what|when|why|where|how|is|are|does|do)\b", speaker_text, re.I)
        and not re.search(r"\b(approv\w*|confirm\w*|withdraw\w*|declin\w*)\b", speaker_text, re.I))
    missing_explanation = (bool(re.search(r"^\s*why\b", speaker_text, re.I))
                           and not any(c["name"] in {"explain", "get_project"} for c in calls))
    unsupported_absence = (bool(re.search(r"\bI (?:don't|do not) see\b|\b(?:task|item|activity|plan) (?:doesn't|does not) exist\b", reply, re.I))
                           and not any(c["name"] in {"search_plan", "get_project"} for c in calls))
    return bool(CLAIM.search(reply) or omitted or guessed_task_refusal or missing_explanation or unsupported_absence)


def check_message(name: str) -> str:
    return (f"(Automatic check from the assistant software, not from {name}: your reply claims an action, offers to record an already-spoken request, "
            f"or guesses task-edit permissions without a tool result. If {name} asked for a change, call the tool and let the server enforce its policy. If nothing needs to "
            "change, answer again without claiming that anything was done. Do not ask whether to record an idea/request that was already spoken: "
            "record the tentative or requested intention now and let the server enforce permission. For a why-question, call explain (or get_project for structured project evidence) before answering. Before claiming a task/item is missing, call search_plan: the summary is capped and is not proof of absence. Do not invent writes for read-only questions.)")


def to_tool_defs(tools: list[Any], include_project: bool = True, descriptions: str = "expanded") -> list[LlmToolDef]:
    out = []
    for t in tools:
        if not include_project and t.name in PROJECT_WRITES:
            continue
        description = t.description or t.title or t.name
        if descriptions == "compact":
            description = description.split('. ')[0][:180]
        out.append(LlmToolDef(name=t.name, description=description, parameters=clean_schema(t.input_schema)))
    return out


@dataclass
class AgentTurn:
    member: str
    text: str
    reply: str
    provider: str
    model: str
    calls: list[dict[str, Any]]
    llm: dict[str, Any]
    ms: float
    at: str
    scenario: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"member": self.member, "text": self.text, "reply": self.reply, "provider": self.provider, "model": self.model,
                "calls": self.calls, "llm": self.llm, "ms": self.ms, "at": self.at, "scenario": self.scenario}


@dataclass
class LlmAgent:
    sessions: SessionPool
    provider: LlmProvider
    member_names: dict[str, str]
    now: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc))
    timezone: str = "America/Toronto"
    max_steps: int = 10
    history_turns: int = 6
    max_tokens: int = 900
    prefetch_state: bool = True  # start every turn with a fresh briefing/state read, so the model never answers from stale chat memory
    self_check: bool = True  # one corrective round when a reply claims a change that no tool call made
    descriptions: str = "expanded"
    history: dict[str, list[LlmMessage]] = field(default_factory=dict)
    active_plans: dict[str, str] = field(default_factory=dict)
    active_groups: dict[str, str] = field(default_factory=dict)
    _turns: int = 0

    @property
    def provider_info(self) -> dict[str, Any]:
        p = self.provider
        return {"id": p.id, "label": p.label, "model": p.model, "ready": p.id != "none", "reason": getattr(p, "reason", None)}

    def reset(self, member: str | None = None) -> None:
        if member:
            self.history.pop(member, None)
            self.active_plans.pop(member, None)
            self.active_groups.pop(member, None)
        else:
            self.history.clear()
            self.active_plans.clear()
            self.active_groups.clear()

    async def handle(self, member: str, text: str, *, plan: str | None = None, group: str | None = None) -> AgentTurn:
        started = time.perf_counter()
        if self.provider.id == "none":
            raise LlmError(getattr(self.provider, "reason", None) or "No LLM is configured.")
        if group and self.active_groups.get(member) != group:
            self.reset(member)
            self.active_groups[member] = group
        if plan:
            self.active_plans[member] = plan
        session = self.sessions.get(member, "alexa")
        mcp_tools = await session.list_tools()
        known = {t.name for t in mcp_tools}
        project_enabled = bool(re.search(r"\b(project|corporate|milestone|workstream|sprint|release|recurring|capacity)\b|项目|里程碑|周期任务|资源容量", text, re.I))
        tools = to_tool_defs(mcp_tools, project_enabled, self.descriptions)
        name = self.member_names.get(member, member)
        today = format_today(self.now(), self.timezone)
        system = system_prompt(name, today)

        prior = self.history.get(member, [])
        messages: list[LlmMessage] = [*prior, UserMessage(content=text)]
        calls: list[dict[str, Any]] = []
        llm_calls: list[dict[str, Any]] = []
        reply = ""
        checked = False
        if self.prefetch_state:
            # A client-side step, like a device sending context with the request: read the speaker's current state over MCP
            # and put it in the conversation as ordinary tool calls and results.
            for state_tool in PREFETCH:
                if state_tool not in known:
                    continue
                self._turns += 1
                pre = LlmToolCall(id=f"state_{self._turns}", name=state_tool, arguments={}, raw_arguments="{}")
                state_args: dict[str, Any] = {"detail": "summary"}
                if group:
                    state_args["group"] = group
                if self.active_plans.get(member):
                    state_args["plan"] = self.active_plans[member]
                pre.arguments, pre.raw_arguments = state_args, json.dumps(state_args)
                t0 = time.perf_counter()
                try:
                    r = await session.call_tool(state_tool, state_args)
                    if not r.ok and state_args.get("plan") and (r.error or {}).get("code") == "NOT_FOUND":
                        self.active_plans.pop(member, None)
                        state_args = {k: v for k, v in state_args.items() if k != "plan"}
                        pre.arguments, pre.raw_arguments = state_args, json.dumps(state_args)
                        r = await session.call_tool(state_tool, state_args)
                    if r.ok and (r.data or {}).get("active_plan_id"):
                        self.active_plans[member] = r.data["active_plan_id"]
                    if r.ok and any(p.get("project") for p in (r.data or {}).get("plans", [])):
                        project_enabled = True
                        tools = to_tool_defs(mcp_tools, True, self.descriptions)
                except Exception as err:  # noqa: BLE001
                    r = ToolResult(ok=False, data=None, error={"code": "TRANSPORT", "message": str(err)})
                calls.append({"name": state_tool, "args": dict(state_args), "ok": r.ok, "result": r.data if r.ok else r.error, "ms": (time.perf_counter() - t0) * 1000,
                              "prefetched": True})
                messages.append(AssistantMessage(content="", tool_calls=[pre]))
                messages.append(ToolMessage(tool_call_id=pre.id, name=state_tool, content=compact(r.data) if r.ok else {"error": r.error}, is_error=not r.ok))
        for _ in range(self.max_steps):
            # A result is only delivered when a subsequent provider request
            # includes it. The last budget-exhausting call may never be sent.
            for observed in calls:
                observed['sent_to_model'] = True
            if project_enabled and "Project workflow:" not in system:
                system += ("\n\nProject workflow: use get_project to page work and read ids. configure_project sets public stage policy; "
                           "upsert_work sets ISO dates, effort in 15-minute work units, resources and dependencies; manage_recurrence creates independent occurrences. "
                           "An owner/lead work edit is a draft, not a commitment. repair_project prepares a parallel resource schedule; "
                           "request_confirmations asks each owner for a commitment and workspace approvers for separate stage authorization on their own pages. "
                           "commit_plan rechecks both and reserves resources. Never substitute task status or a spoken group yes for human commitments. "
                           "If a project schedule is stale, prepare a fresh repair; report a timeout as unproven optimality.")
            res = await self.provider.complete(LlmRequest(system=system, messages=messages, tools=tools, max_tokens=self.max_tokens))
            llm_calls.append({"ms": res.ms, "inputTokens": res.usage.input_tokens, "outputTokens": res.usage.output_tokens,
                              "cachedInputTokens": res.usage.cached_input_tokens, "stop": res.stop, "toolCalls": [t.name for t in res.tool_calls]})
            if not res.tool_calls:
                reply = re.sub(r"\s+", " ", res.text).strip()
                if self.self_check and not checked and claims_without_action(reply, calls, text) and len(llm_calls) < self.max_steps:
                    checked = True
                    llm_calls[-1]["selfCheck"] = True
                    messages.append(AssistantMessage(content=reply))
                    messages.append(UserMessage(content=check_message(name)))
                    reply = ""
                    continue
                break
            messages.append(AssistantMessage(content=res.text, tool_calls=res.tool_calls, reasoning=res.reasoning))
            for tc in res.tool_calls:
                t0 = time.perf_counter()
                if tc.parse_error or tc.arguments is None:
                    r = ToolResult(ok=False, data=None, error={"code": "INVALID_ARGUMENTS", "message": tc.parse_error or "Arguments were missing."})
                elif tc.name not in known:
                    r = ToolResult(ok=False, data=None, error={"code": "UNKNOWN_TOOL", "message": f'There is no tool called "{tc.name}". Available: {", ".join(sorted(known))}.'})
                else:
                    try:
                        # Bind evidence to the actual current utterance, including
                        # when the model omits or paraphrases its quote argument.
                        if tc.name in {"set_my_conditions", "propose_change", "decide_change", "create_plan"}:
                            tc.arguments["quote"] = text
                        r = await session.call_tool(tc.name, tc.arguments)
                    except Exception as err:  # noqa: BLE001
                        r = ToolResult(ok=False, data=None, error={"code": "TRANSPORT", "message": str(err)})
                calls.append({"name": tc.name, "args": tc.arguments if tc.arguments is not None else {"raw": tc.raw_arguments}, "ok": r.ok,
                              "result": r.data if r.ok else r.error, "ms": (time.perf_counter() - t0) * 1000, "sent_to_model": False})
                if r.ok and r.data:
                    focus = r.data.get("active_plan_id")
                    if tc.name == "create_plan":
                        focus = (r.data.get("plan") or {}).get("id")
                    if focus:
                        self.active_plans[member] = focus
                    if tc.name in {"get_project", "repair_project", "upsert_work"} or (tc.name == "get_template" and r.data.get("id") == "project") or (tc.name == "create_plan" and (r.data.get("plan") or {}).get("project")):
                        project_enabled = True
                        tools = to_tool_defs(mcp_tools, True, self.descriptions)
                messages.append(ToolMessage(tool_call_id=tc.id, name=tc.name, content=compact(r.data) if r.ok else {"error": r.error}, is_error=not r.ok))
        if not reply:
            reply = ("Sorry, that took more steps than I'm allowed. Could you say it a different way?" if len(llm_calls) >= self.max_steps
                     else "Sorry, I couldn't finish that. Please try again.")
        # Keep a compact, text-only history so follow-ups like "yes, do it" have context.
        keep = self.history_turns * 2
        self.history[member] = [*prior, UserMessage(content=text), AssistantMessage(content=reply)][-keep:]
        total = lambda k: sum(c[k] for c in llm_calls)  # noqa: E731
        return AgentTurn(member=member, text=text, reply=reply, provider=self.provider.label, model=self.provider.model, calls=calls,
                         llm={"calls": llm_calls, "inputTokens": total("inputTokens"), "outputTokens": total("outputTokens"),
                              "cachedInputTokens": total("cachedInputTokens"), "ms": total("ms")},
                         ms=(time.perf_counter() - started) * 1000, at=datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"))
