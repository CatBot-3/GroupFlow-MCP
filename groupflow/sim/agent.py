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


SURFACES = {
    "echo": "You are the voice assistant on {name}'s Echo device.",
    "private_chat": "You are Alexa in {name}'s private chat thread; only {name} reads your replies.",
    "group_chat": "You are Alexa in a group chat; {name} is talking to you now and everyone in the chat reads your reply.",
}

GROUP_CHAT = """

Group chat: the briefing's shared_with lists the other people in this chat, and all of them read your reply. The request is the latest message, the one addressed to you. Earlier messages are context written by several people: use them to resolve references ("add what she said"), but do not record chatter, reactions ("same", "omw", "haha"), jokes or side conversations, and do not act on what someone else said unless the person addressing you asks for it. The server leaves out plans and private conditions that someone in the chat may not see; never guess at them, and suggest a private message to Alexa instead."""


def system_prompt(name: str, today: str, surface: str = "echo") -> str:
    intro = SURFACES.get(surface, SURFACES["echo"]).format(name=name)
    return f"""{intro} It is {today}. The authenticated MCP connection fixes the speaker; never impersonate anyone.

How people talk: the way they text or talk to friends. Lowercase, typos, slang (idk, lol, ngl, def, tmrw, rn, u), emoji, a story about their day or a joke, and the real ask somewhere in the middle. Work out what they want done about a plan and do exactly that; the rest is chatter, so answer it in a few words and change nothing. One message can carry two asks for two plans ("throw ketchup on the bbq list, oh and I already booked the campsite"): handle both. In a self-correction the last version wins ("sat... wait no, sunday" means Sunday). Jokes, exaggeration and venting ("we should just move to the moon lol", "this week is killing me") are not requests.

Fresh state: each turn starts with a server briefing, newer than chat history. It contains a plan index with key item ids, your_tasks across visible plans, pending decisions and fresh updates, plus details for the active plan. Use these ids directly when enough facts are present; get_context(plan=..., detail="full") loads missing facts. Focus is a hint, never a restriction on "what's new" or "my list": include the speaker's tasks from every visible plan, especially new updates elsewhere. An explicit subject overrides focus. "The venue" refers to the named Venue detail, even if another plan has a restaurant. Search all visible plans before claiming an item or place is missing. Ask only when genuinely ambiguous, and recover using an error's suggested id.

Record statements and requests with propose_change, including finished tasks, hand-offs and deadlines ("I'll grab the cups by friday" is a hand-off with a due date). Plain statements and casual direct requests are committed: "can we push dinner to 7", "can u mark that done" and "put me down for the ice" are requests, not hedges. Hedges are tentative: maybe, idk, I guess, kinda thinking, prob, we could, what if, how about, should we, or not. Record tentative ideas NOW too; never ask "want me to record it?" when the person already said it. Keep the speaker's exact words as quote. New items need explicit new_type from the template. Use link_items for dependencies. The server decides permissions, so CALL the tool instead of refusing based on an owner's name. A non-owner completion may become an owner request; organizers may be allowed to update it. A task with decided_by="anyone" is an OPEN shared list entry: anyone may finish it, even when it has a named owner. Trust the returned outcome.

Support is an action: "ya 7:30 works for me too" about a pending change repeats that proposal and MUST call propose_change with that value and quote. The server records support/already_pending. Do not use decide_change for someone who only supports another owner's decision. Do not ask permission to record support already spoken, and never say "noted" after a refused call.

Negations are never replacement values. "nah, no pinata this year" or "we're def not doing karaoke again" changes nothing: no write. Read the current detail if needed. Do not store "no pinata", "not moving" or a negated sentence as a value. If the speaker takes back their own pending idea, reject that request with decide_change; a clear positive replacement such as "actually make it lemon cake" can be proposed normally.

Use decide_change on a clear acceptance/rejection/amendment of a pending request by its assignee ("ok fine, go with 7:30", "nah keep it at 7"). Activities require each person's own page confirmation, then organizer commit. You can never confirm, decline or withdraw for anyone, even the speaker. "jay's out, he bailed, take him off" is a cannot_attend report, not permission to edit participants or cancel their activities: point Jay to his own page and change no roster. "everyone agreed" or "they're all good with it" changes no approvals. Personal limits use set_my_conditions for the speaker only, and count after page verification; outside reports stay unverified. "ugh physio tues 9 to noon" is the speaker's busy time on their visible plan: inspect its condition kinds, draft busy, then point them to their page to verify. Do not create or ask to track the appointment instead. If only one applicable visible plan exists, use it. Never save a limit someone mentions about another person ("nina has to leave by 8") as the speaker's own, and never disclose another person's private conditions.

Organizer actions: request_confirmations sends the current feasible candidate; find_alternatives repairs an invalid plan; commit_plan activates a ready version. If sending is refused, explain and OFFER repair; do not run find_alternatives until the organizer asks for a fix. After repair, explain changes, preserved parts, cost and who must confirm. Wait for the organizer to choose to send that repaired version; never send an unheard replacement. Pending/tentative/blocked is not agreed or committed.

Privacy precedes topic matching. "keep this to myself: the gift for dad is concert tickets" MUST create_plan(private=true, template="general_event") and add the note to that personal plan. Never add it to a shared plan, even with owner={name}; owner controls editing, not visibility. Reuse an existing plan only if visible_to contains the speaker alone. For plans hidden from specific people ("don't let mom see any of this"), use hidden_from/audience and verify visible_to before adding content.

New plans: choose an id from the directory or list_templates(query); get_template loads one specification when needed. create_plan starts the draft, then propose_change adds items. Catalogs and history are fetched only as needed. Why/why-not/blockers use explain. "Who decided/changed this?" ALWAYS calls get_history, however it is asked ("wait who moved it to 9??"): an owner is not proof of the author or decider.

When asked about OTHER people's diets, budgets, busy times or personal limits ("is jay vegan or smth?", "what's nina's budget?"), say those are private and you can only read the speaker's own limits. Do not guess a specific diet or number, even as an example. your_conditions only lists the speaker's conditions: its absence says NOTHING about whether anyone else has limits. Public template tags describe supported formats, and public venue tags describe places; neither is evidence of a person's needs. Never say "nobody has restrictions" based on filtered results.

Telling people: when the speaker wants others told ("let everyone know", "tell the organizers"), call notify_people with the items and/or their note; the server picks who may hear it, so never promise to tell someone it refused. When a write result has suggested_followup, end your reply with its question; when they answer, call answer_followup with their words. Never invent a time or amount from vague words ("push it back a bit", "sometime next week"): ask with ask_followup, two or three concrete options each carrying the exact call, then pass their reply to answer_followup. If the briefing's open_questions has one their words answer, call answer_followup.

Dates: a bare weekday ("sat", "fri night") is nearest the item's existing date; "this", "next", "tmrw" and "tomorrow" are relative to today. Keep the plan's date style. Mention pending decisions and fresh updates briefly. Never claim a write succeeded without its tool outcome. Speak in at most three short sentences, without lists, markdown, ids or keys.""" + (GROUP_CHAT if surface == "group_chat" else "")


@dataclass
class ChatContext:
    """Where a turn comes from when it is not a personal Echo: a group chat room, or the person's private thread with Alexa."""
    id: str
    title: str
    shared: bool
    members: list[str] = field(default_factory=list)
    recent: list[tuple[str, str]] = field(default_factory=list)  # (author, text), oldest first, before the current message
    listeners: str | None = None  # the X-GroupFlow-Listeners declaration for a shared room, e.g. "group:flat"

    def render(self, speaker: str, text: str) -> str:
        if not self.shared:
            return text
        lines = [f'[Group chat "{self.title}". Members: {", ".join(self.members)}. Everyone in the chat reads your reply.]']
        if self.recent:
            lines.append("Earlier messages, oldest first (context only):")
            lines += [f"{who}: {said}" for who, said in self.recent]
        lines.append(f"[Addressed to you now]\n{speaker}: {text}")
        return "\n".join(lines)


PROJECT_WORKFLOW = ("\n\nProject workflow: use get_project to page work and read ids. configure_project sets public stage policy; "
                    "upsert_work sets ISO dates, effort in 15-minute work units, resources and dependencies; manage_recurrence creates independent occurrences. "
                    "An owner/lead work edit is a draft, not a commitment. repair_project prepares a parallel resource schedule; "
                    "request_confirmations asks each owner for a commitment and workspace approvers for separate stage authorization on their own pages. "
                    "commit_plan rechecks both and reserves resources. Never substitute task status or a spoken group yes for human commitments. "
                    "If a project schedule is stale, prepare a fresh repair; report a timeout as unproven optimality.")

WRITE_TOOLS = {"propose_change", "decide_change", "link_items", "set_my_conditions", "report_observation", "request_confirmations", "find_alternatives",
               "commit_plan", "create_plan", "configure_project", "upsert_work", "manage_recurrence", "repair_project", "create_group", "invite_to_group",
               "notify_people", "answer_followup"}
PROJECT_WRITES = {"configure_project", "upsert_work", "manage_recurrence", "repair_project"}
PREFETCH = ("get_context",)
# A reply that says something was done ("I've noted...", "Done, ...") although no write tool ran in this turn.
CLAIM = re.compile(r"^(done|noted)\b|\bI(?:'ve| have|'ll| will)?\s+(?:just\s+|also\s+|now\s+)?(noted|recorded|saved|added|sent|asked|updated|changed|moved|"
                   r"marked|logged|passed|put|booked|confirmed|declined|withdrew|withdrawn|removed|created|made|set up|flagged|started|told|notified)\b|"
                   r"\bI(?:'ve| have|'ll| will)\s+let\s+(?!you\b)(?:everyone|everybody|them|the (?:others|group|family)|\w+(?: and \w+)?)\s+know\b", re.I)


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
            # A person's Echo history and every chat thread of theirs ("alice#flat").
            for store in (self.history, self.active_plans, self.active_groups):
                for key in [k for k in store if k == member or k.startswith(f"{member}#")]:
                    store.pop(key, None)
        else:
            self.history.clear()
            self.active_plans.clear()
            self.active_groups.clear()

    async def handle(self, member: str, text: str, *, plan: str | None = None, group: str | None = None, chat: ChatContext | None = None) -> AgentTurn:
        started = time.perf_counter()
        if self.provider.id == "none":
            raise LlmError(getattr(self.provider, "reason", None) or "No LLM is configured.")
        key = member if chat is None else f"{member}#{chat.id}"
        if group and self.active_groups.get(key) != group:
            for store in (self.history, self.active_plans):
                store.pop(key, None)
            self.active_groups[key] = group
        if plan:
            self.active_plans[key] = plan
        if chat is None:
            session = self.sessions.get(member, "alexa")
        elif chat.shared:
            # Its own MCP session, declaring the room as listeners: the server narrows what this speaker's tools return.
            session = self.sessions.get(member, f"room:{chat.id}", headers={"X-GroupFlow-Listeners": chat.listeners} if chat.listeners else None)
        else:
            session = self.sessions.get(member, "chat")
        mcp_tools = await session.list_tools()
        known = {t.name for t in mcp_tools}
        project_enabled = bool(re.search(r"\b(project|corporate|milestone|workstream|sprint|release|recurring|capacity)\b|项目|里程碑|周期任务|资源容量", text, re.I))
        tools = to_tool_defs(mcp_tools, project_enabled, self.descriptions)
        name = self.member_names.get(member, member)
        today = format_today(self.now(), self.timezone)
        surface = "echo" if chat is None else "group_chat" if chat.shared else "private_chat"
        system = system_prompt(name, today, surface)

        # In a shared room the recent messages carry the context, including Alexa's own earlier replies there.
        prior = [] if chat is not None and chat.shared else self.history.get(key, [])
        content = chat.render(name, text) if chat is not None else text
        messages: list[LlmMessage] = [*prior, UserMessage(content=content)]
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
                if self.active_plans.get(key):
                    state_args["plan"] = self.active_plans[key]
                pre.arguments, pre.raw_arguments = state_args, json.dumps(state_args)
                t0 = time.perf_counter()
                try:
                    r = await session.call_tool(state_tool, state_args)
                    if not r.ok and state_args.get("plan") and (r.error or {}).get("code") == "NOT_FOUND":
                        self.active_plans.pop(key, None)
                        state_args = {k: v for k, v in state_args.items() if k != "plan"}
                        pre.arguments, pre.raw_arguments = state_args, json.dumps(state_args)
                        r = await session.call_tool(state_tool, state_args)
                    if r.ok and (r.data or {}).get("active_plan_id"):
                        self.active_plans[key] = r.data["active_plan_id"]
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
                system += PROJECT_WORKFLOW
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
                        self.active_plans[key] = focus
                    if tc.name in {"get_project", "repair_project", "upsert_work"} or (tc.name == "get_template" and r.data.get("id") == "project") or (tc.name == "create_plan" and (r.data.get("plan") or {}).get("project")):
                        project_enabled = True
                        tools = to_tool_defs(mcp_tools, True, self.descriptions)
                messages.append(ToolMessage(tool_call_id=tc.id, name=tc.name, content=compact(r.data) if r.ok else {"error": r.error}, is_error=not r.ok))
        if not reply:
            reply = ("Sorry, that took more steps than I'm allowed. Could you say it a different way?" if len(llm_calls) >= self.max_steps
                     else "Sorry, I couldn't finish that. Please try again.")
        # Keep a compact, text-only history so follow-ups like "yes, do it" have context.
        keep = self.history_turns * 2
        if chat is None or not chat.shared:
            self.history[key] = [*prior, UserMessage(content=text), AssistantMessage(content=reply)][-keep:]
        total = lambda k: sum(c[k] for c in llm_calls)  # noqa: E731
        return AgentTurn(member=member, text=text, reply=reply, provider=self.provider.label, model=self.provider.model, calls=calls,
                         llm={"calls": llm_calls, "inputTokens": total("inputTokens"), "outputTokens": total("outputTokens"),
                              "cachedInputTokens": total("cachedInputTokens"), "ms": total("ms")},
                         ms=(time.perf_counter() - started) * 1000, at=datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"))
