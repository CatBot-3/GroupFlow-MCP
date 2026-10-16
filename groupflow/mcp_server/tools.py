"""The generic GroupFlow tools and on-demand template discovery.

One set of tools serves every kind of plan; templates decide what a plan holds.
None of the tools can approve, decline or withdraw for a person: those actions
exist only on the human-only API, behind the person's own login.
"""
from __future__ import annotations

import asyncio
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from pydantic import BaseModel, Field

from groupflow.engine import UNSET, GroupFlow
from groupflow.engine.views import (
    AlternativesResult, ChangeResult, CommitResult, ConditionsResult, ConfirmationsResult, ContextView, CreatePlanResult, DecisionResult,
    ExplainView, HistoryView, LinkResult, ObservationResult, SearchView, TemplateListView, TemplateSpecView,
)

from .common import ToolRunner, annotations

INSTRUCTIONS = (
    "GroupFlow keeps shared, permission-checked plans. The connection fixes the speaker. Start each turn with get_context(detail='summary'); "
    "People talk casually (slang, typos, jokes, venting, side chatter): act only on what they ask or state about a plan, let the last version of "
    "a self-correction win, and change nothing for chatter. "
    "read one plan in full when needed. Discover templates in the short directory or list_templates, then get_template for one specification. "
    "Record statements with propose_change; hedged ideas are tentative. Owner items wait for their owner, open items allow any member, "
    "and activities need each person's own page confirmation followed by organizer commit. You can never confirm, decline or withdraw for "
    "anyone; reported group agreement changes no approvals. Report tool outcomes exactly. The server filters visibility and private conditions. "
    "create_group starts a group and invite_to_group makes a code; people join only on their own page. When get_context lists shared_with, "
    "those people also read the reply and only plans all of them can see are included."
)

NAMES = ["get_context", "search_plan", "create_plan", "propose_change", "decide_change", "link_items", "set_my_conditions", "report_observation",
         "request_confirmations", "find_alternatives", "get_alternatives", "commit_plan", "explain", "get_history", "list_templates", "get_template",
         "get_project", "configure_project", "upsert_work", "manage_recurrence", "repair_project", "create_group", "invite_to_group"]

PLAN_ARG = Field(description='Plan id/title; pass it when more than one plan could match.')


def result_dict(value) -> dict[str, Any]:
    # A union return annotation causes the SDK to wrap results in {result:...}.
    # Preserve the existing top-level MCP wire contract for all plan types.
    return value.model_dump(mode="json") if isinstance(value, BaseModel) else value


class Condition(BaseModel):
    kind: str = Field(description='A condition name from the plan\'s condition_kinds, e.g. "home_by", "budget", "diet", "busy".')
    value: dict[str, Any] = Field(description=(
        'Condition value; get_template returns the exact accepted format and tags.'))


def register(mcp: MCPServer, engine: GroupFlow, runner: ToolRunner) -> None:
    @mcp.tool(
        title="Everything the speaker needs to know",
        description=(
            'Read fresh state. summary includes a paged plan/template index, key item ids, the speaker\'s open tasks and updates across visible plans, plus details of the selected plan. Focus does not hide other plans\' new work. Use returned ids; read another plan before guessing a venue or value. your_conditions is ONLY the speaker\'s: an empty list never means nobody else has private limits. Returned updates with mark_read=true are marked heard.'
        ),
        annotations=annotations(read_only=False),
    )
    async def get_context(
        ctx: Context,
        plan: Annotated[str | None, Field(description="Only this plan (id or title). Default: every plan the speaker can see.")] = None,
        mark_read: Annotated[bool | None, Field(description="Mark returned updates as heard. Default true.")] = None,
        detail: Annotated[Literal["summary", "full"], Field(description="summary: short index plus the selected plan; full: detailed plan views.")] = "full",
        group: Annotated[str | None, Field(description="Optional group id or name; visibility is still checked.")] = None,
        limit: Annotated[int, Field(ge=1, le=50, description="Page size for the summary index and updates.")] = 12,
        offset: Annotated[int, Field(ge=0, description="Summary index offset.")] = 0,
    ) -> ContextView:
        return runner.run(ctx, "get_context", lambda m: engine.context(m.id, plan=plan, mark_read=mark_read is not False,
                          detail=detail, group=group, limit=limit, offset=offset))  # type: ignore[return-value]

    @mcp.tool(
        title="Find items and places by words",
        description=(
            'Search visible items and venue catalogs by words. If unmatched, inspect the relevant plan before saying it does not exist.'
        ),
        annotations=annotations(read_only=True, idempotent=True),
    )
    async def search_plan(
        ctx: Context,
        query: Annotated[str, Field(description="Words to look for.")],
        plan: Annotated[str | None, PLAN_ARG] = None,
    ) -> SearchView:
        return runner.run(ctx, "search_plan", lambda m: engine.search(m.id, query, plan=plan))  # type: ignore[return-value]

    @mcp.tool(
        title="Start a new plan",
        description=(
            'Create a draft using a template id; get_template supplies fields. For "just for me", "keep this to myself", personal gift ideas or private notes, set private=true BEFORE recording content, then write in the returned plan. A shared plan with a personal owner is still shared. audience/hidden_from restrict readers; inspect visible_to. Template names do not enforce secrecy. Add items with propose_change.'
        ),
        annotations=annotations(read_only=False),
    )
    async def create_plan(
        ctx: Context,
        template: Annotated[str, Field(description="Template id, e.g. general_event.")],
        title: Annotated[str, Field(description='Short title, e.g. "Sunday picnic".')],
        start_date: Annotated[str | None, Field(description='First day: "2026-11-20", a weekday like "Saturday", or "tomorrow".')] = None,
        days: Annotated[int | None, Field(ge=1, le=366, description="Planning window in calendar days; override the template default. This is not an approval lifetime.")] = None,
        group: Annotated[str | None, Field(description="Group name or id, if the speaker is in several groups.")] = None,
        hidden_from: Annotated[list[str] | None, Field(description="Names of people who must not see this plan (e.g. the guest of honour).")] = None,
        audience: Annotated[list[str] | None, Field(description='Plan reader allowlist, besides the speaker. Required for "just/only with" named people. Activity participants do NOT limit readers.')] = None,
        starter: Annotated[bool | None, Field(description="Add the template's usual starting items. Default true.")] = None,
        private: Annotated[bool | None, Field(description="Only the speaker will see this plan, e.g. their own notes or gift ideas.")] = None,
        preset: Annotated[str | None, Field(description="Optional preset from get_template: surprise requires hidden_from; weekend_cottage supplies a synthetic trip.")] = None,
        lifecycle: Annotated[Literal["bounded", "ongoing"] | None, Field(description="bounded ends with the window; ongoing can roll its planning window. Confirmations remain content-bound and expire separately.")] = None,
        quote: Annotated[str, Field(description='Original creation request, for preserving explicit audience intent. The local host binds the current utterance.')] = '',
    ) -> CreatePlanResult:
        return runner.run(ctx, "create_plan", lambda m: engine.create_plan(  # type: ignore[return-value]
            m.id, template, title, group=group, start_date=start_date, days=days, hidden_from=hidden_from, audience=audience, starter=starter is not False,
            private=bool(private), preset=preset, lifecycle=lifecycle, quote=quote))

    @mcp.tool(
        title="Record what the speaker said about a plan",
        description=(
            'Record statements, completed tasks, deadlines and support for pending proposals, however casually said. Use exact item ids; new items need new_type. "ya 7:30 works for me too" must call this even when pending; decide_change is for the assignee deciding. Hedges (maybe, idk, what if) are tentative. "nah, no pinata this year" changes no value. Private notes belong in create_plan(private=true), never a shared plan with a personal owner. Non-owner task updates wait for the owner. Do not edit participants to withdraw for someone. Report the exact outcome.'
        ),
        annotations=annotations(read_only=False),
    )
    async def propose_change(
        ctx: Context,
        item: Annotated[str | None, Field(description="Item id from get_context (preferred) or its name. Omit to add a new item.")] = None,
        value: Annotated[str | None, Field(description='Details: the new value as a short plain phrase, e.g. "Friday, Mar 6, 7:30 pm" or "12 people", even when said in slang. Never a negation.')] = None,
        status: Annotated[Literal["todo", "in_progress", "done"] | None, Field(description="Tasks.")] = None,
        owner: Annotated[str | None, Field(description="Tasks (who does it) or details (who decides it): a name.")] = None,
        due: Annotated[str | None, Field(description='Tasks: due date as a short phrase; "" clears it.')] = None,
        title: Annotated[str | None, Field(description="Name of a new task or activity, or a new name for an existing one.")] = None,
        day: Annotated[str | None, Field(description='Activities: the day, e.g. "Sat" or "2026-11-07", for plans over several days.')] = None,
        start: Annotated[str | None, Field(description='Activities: start time, e.g. "19:00".')] = None,
        end: Annotated[str | None, Field(description='Activities: end time. If omitted, the length stays the same.')] = None,
        place: Annotated[str | None, Field(description="Activities: a place from the plan's catalog (by name), or free text where the plan has no catalog.")] = None,
        participants: Annotated[list[str] | None, Field(description='Activities: names of the people taking part, or ["everyone"].')] = None,
        remove: Annotated[bool | None, Field(description="Drop this activity, or remove this task or list entry.")] = None,
        new_type: Annotated[str | None, Field(description='Required for a new item: a type from the selected template.')] = None,
        value_kind: Annotated[Literal["datetime", "place", "count", "text"] | None, Field(description="Only for a new detail.")] = None,
        certainty: Annotated[Literal["committed", "tentative"] | None, Field(description=(
            'committed for direct requests/statements, however casual ("can u push it to 8"); tentative for maybe, idk, prob, we could, how about, what if, should we.'))] = None,
        quote: Annotated[str | None, Field(description="The speaker's own words, kept as evidence.")] = None,
        plan: Annotated[str | None, PLAN_ARG] = None,
    ) -> ChangeResult:
        due_arg = UNSET if due is None else (None if due == "" else due)
        return runner.run(ctx, "propose_change", lambda m: engine.propose_change(  # type: ignore[return-value]
            m.id, item=item, plan=plan, value=value, status=status, owner=owner, due=due_arg, title=title, day=day, start=start, end=end, place=place,
            participants=participants, remove=bool(remove), new_type=new_type, value_kind=value_kind, certainty=certainty or "committed", quote=quote))

    @mcp.tool(
        title="Decide a pending request",
        description=(
            'Resolve a pending request only on a clear instruction: accept/reject/amend (values only). Caller must be its assignee, organizer or an allowed open-item editor. A requester may reject their own request to withdraw it. Activity acceptance still needs page confirmations.'
        ),
        annotations=annotations(read_only=False),
    )
    async def decide_change(
        ctx: Context,
        request: Annotated[str, Field(description="Request id.")],
        decision: Literal["accept", "reject", "amend"],
        amended_value: Annotated[str | None, Field(description='Required when decision is "amend".')] = None,
        quote: Annotated[str | None, Field(description="The speaker's own words.")] = None,
    ) -> DecisionResult:
        return runner.run(ctx, "decide_change", lambda m: engine.decide_change(m.id, request, decision, amended_value=amended_value, quote=quote))  # type: ignore[return-value]

    @mcp.tool(
        title="Link what depends on what",
        description=(
            'Add/remove an item dependency. Tasks wait on tasks; watched inputs flag affected work when changed. Cycles are refused.'
        ),
        annotations=annotations(read_only=False, idempotent=True),
    )
    async def link_items(
        ctx: Context,
        item: Annotated[str, Field(description="The item that depends (id or name).")],
        depends_on: Annotated[str, Field(description="What it depends on (id or name).")],
        remove: bool | None = None,
        plan: Annotated[str | None, PLAN_ARG] = None,
    ) -> LinkResult:
        return runner.run(ctx, "link_items", lambda m: engine.link_items(m.id, item, depends_on, remove=bool(remove), plan=plan))  # type: ignore[return-value]

    @mcp.tool(
        title="Save the speaker's own conditions",
        description=(
            "Draft ONLY the speaker's own private limits. 'jay can only do 40 bucks, set that for him' must make NO condition write, including no write to the speaker's own conditions. Point Jay to his own page. Use get_template for accepted formats. The speaker verifies their own drafts on their page before they count."
        ),
        annotations=annotations(read_only=False),
    )
    async def set_my_conditions(
        ctx: Context,
        conditions: Annotated[list[Condition], Field(description="One or more conditions.")],
        plan: Annotated[str | None, PLAN_ARG] = None,
        quote: Annotated[str | None, Field(description="The speaker's exact words about their own limit; never relabel someone else's reported limit as the speaker's.")] = None,
    ) -> ConditionsResult:
        changes = [c.model_dump() for c in conditions]
        return runner.run(ctx, "set_my_conditions", lambda m: engine.set_my_conditions(m.id, changes, plan=plan, quote=quote))  # type: ignore[return-value]

    @mcp.tool(
        title="Report something that changed outside the plan",
        description=(
            "Record an unverified outside report: place_unavailable, price_changed or cannot_attend. It changes no trusted fact. Attendance withdrawal must happen on the person's page."
        ),
        annotations=annotations(read_only=False),
    )
    async def report_observation(
        ctx: Context,
        kind: Literal["place_unavailable", "price_changed", "cannot_attend"],
        place: Annotated[str | None, Field(description="The place, by name.")] = None,
        day: Annotated[str | None, Field(description="Which day it applies to, for plans over several days.")] = None,
        start: Annotated[str | None, Field(description='From what time, e.g. "18:00".')] = None,
        end: Annotated[str | None, Field(description='Until what time.')] = None,
        price: Annotated[str | None, Field(description="price_changed: the new price per person.")] = None,
        note: Annotated[str | None, Field(description="The speaker's words.")] = None,
        plan: Annotated[str | None, PLAN_ARG] = None,
    ) -> ObservationResult:
        return runner.run(ctx, "report_observation", lambda m: engine.report_observation(  # type: ignore[return-value]
            m.id, kind, place=place, plan=plan, day=day, start=start, end=end, price=price, note=note))

    @mcp.tool(
        title="Send a version out for confirmation",
        description=(
            "Organizer: send the current candidate to affected people's pages. Refuses hard-condition violations; unchanged approvals may carry forward after checks. A repaired candidate must be described to the organizer before they choose to send it."
        ),
        annotations=annotations(read_only=False),
    )
    async def request_confirmations(
        ctx: Context,
        plan: Annotated[str | None, PLAN_ARG] = None,
        expected_version: Annotated[int | None, Field(description="The plan version you read, to guard against acting on stale state.")] = None,
    ) -> dict[str, Any]:
        return runner.run(ctx, "request_confirmations", lambda m: result_dict(engine.request_confirmations(m.id, plan=plan, expected_version=expected_version)))

    @mcp.tool(
        title="Find the smallest change that works",
        description=(
            'Organizer: search finite options for the least disruptive feasible repair. Returns changes, preserved items, reconfirmations and proof status. Does not send or commit; explain infeasibility without naming private owners.'
        ),
        annotations=annotations(read_only=False),
    )
    async def find_alternatives(
        ctx: Context,
        plan: Annotated[str | None, PLAN_ARG] = None,
        expected_version: Annotated[int | None, Field(description="The plan version you read.")] = None,
    ) -> AlternativesResult:
        return await asyncio.to_thread(runner.run, ctx, "find_alternatives",  # type: ignore[return-value]
                                       lambda m: engine.find_alternatives(m.id, plan=plan, expected_version=expected_version))

    @mcp.tool(
        title="Read the latest alternatives",
        description='Read the latest repair or a given job, including candidates and proof status.',
        annotations=annotations(read_only=True, idempotent=True),
    )
    async def get_alternatives(
        ctx: Context,
        plan: Annotated[str | None, PLAN_ARG] = None,
        job: Annotated[str | None, Field(description="Search job id.")] = None,
    ) -> AlternativesResult:
        return runner.run(ctx, "get_alternatives", lambda m: engine.get_alternatives(m.id, plan=plan, job=job))  # type: ignore[return-value]

    @mcp.tool(
        title="Make a confirmed version the agreed plan",
        description=(
            'Organizer: activate the current candidate only after all confirmations and server checks pass. Report blocked/already_committed exactly. Booking effects are simulated.'
        ),
        annotations=annotations(read_only=False),
    )
    async def commit_plan(
        ctx: Context,
        plan: Annotated[str | None, PLAN_ARG] = None,
        expected_version: Annotated[int | None, Field(description="The plan version you read.")] = None,
    ) -> dict[str, Any]:
        return runner.run(ctx, "commit_plan", lambda m: result_dict(engine.commit_plan(m.id, plan=plan, expected_version=expected_version)))

    @mcp.tool(
        title="Explain why",
        description=(
            "Use for why/why-not/blocked questions. item explains dependencies; option evaluates an alternative; plan explains changes and confirmations. Never guess or reveal another person's private reasons."
        ),
        annotations=annotations(read_only=True, idempotent=True),
    )
    async def explain(
        ctx: Context,
        item: Annotated[str | None, Field(description="Item id or name (task, detail or activity).")] = None,
        option: Annotated[str | None, Field(description='An alternative the speaker asks about, e.g. "Restaurant C".')] = None,
        plan: Annotated[str | None, PLAN_ARG] = None,
    ) -> ExplainView:
        return runner.run(ctx, "explain", lambda m: engine.explain(m.id, item=item, option=option, plan=plan))  # type: ignore[return-value]

    @mcp.tool(
        title="Who changed what",
        description=(
            "Read permitted item/plan history and recorded quotes. Other people's private entries are excluded."
        ),
        annotations=annotations(read_only=True, idempotent=True),
    )
    async def get_history(
        ctx: Context,
        item: Annotated[str | None, Field(description="Item id or name. Omit for the plan's history.")] = None,
        plan: Annotated[str | None, PLAN_ARG] = None,
    ) -> HistoryView:
        return runner.run(ctx, "get_history", lambda m: engine.history(m.id, item=item, plan=plan))  # type: ignore[return-value]


    @mcp.tool(title="Discover plan templates", description="Search short template summaries. Use next_offset to page; get_template loads one specification.",
              annotations=annotations(read_only=True, idempotent=True))
    async def list_templates(ctx: Context, query: str = "", limit: Annotated[int, Field(ge=1, le=50)] = 12,
                             offset: Annotated[int, Field(ge=0)] = 0) -> TemplateListView:
        return runner.run(ctx, "list_templates", lambda m: engine.list_templates(m.id, query, limit, offset))  # type: ignore[return-value]

    @mcp.tool(title="Read one template specification", description="Load one template's item types, condition formats and starter labels. No full venue catalog.",
              annotations=annotations(read_only=True, idempotent=True))
    async def get_template(ctx: Context, template: Annotated[str, Field(description="Template id from list_templates or the context directory.")]) -> TemplateSpecView:
        return runner.run(ctx, "get_template", lambda m: engine.get_template_spec(m.id, template))  # type: ignore[return-value]

    @mcp.tool(title="Read project work and schedule", description="Page structured project work, workstreams, recurrence series, resource schedule, solver proof and confirmation states. Only your private calendar is returned. Start here for corporate/long-term projects; use next_offset for more work.",
              annotations=annotations(read_only=True, idempotent=True))
    async def get_project(ctx: Context, plan: str, workstream: str | None = None,
                          limit: Annotated[int, Field(ge=1, le=100)] = 25, offset: Annotated[int, Field(ge=0)] = 0) -> dict[str, Any]:
        return runner.run(ctx, "get_project", lambda m: engine.get_project(m.id, plan, workstream, limit, offset))

    @mcp.tool(title="Configure a project stage", description="Project lead: set stage, approver member ids, budget_minor, public calendar work hours, or rolling window_start/days for an ongoing project. Only a workspace admin can define resources [{id:group_id.name,name,capacity}]. Approvers need a workspace role granted on an admin's human page. No approval is given here.",
              annotations=annotations(read_only=False))
    async def configure_project(ctx: Context, plan: str, changes: dict[str, Any], expected_version: int | None = None) -> dict[str, Any]:
        return runner.run(ctx, "configure_project", lambda m: engine.configure_project(m.id, plan, changes, expected_version))

    @mcp.tool(title="Add or edit structured project work", description="Project lead creates/assigns work; owners can edit their work. spec fields: not_before/due ISO dates, duration_minutes (0 milestone; 15-minute multiples <=480, split longer work), workstream, milestone, resources {resource_id:units}, depends_on [task ids], cost_minor, priority, locked_start (timestamp with offset). Updates merge fields. A work edit is a draft, never a human commitment. Use propose_change for owner-decided task status.",
              annotations=annotations(read_only=False))
    async def upsert_work(ctx: Context, plan: str, spec: dict[str, Any], item: str | None = None, title: str | None = None,
                          owner: str | None = None, expected_version: int | None = None) -> dict[str, Any]:
        return runner.run(ctx, "upsert_work", lambda m: engine.upsert_work(m.id, plan, spec, item, title, owner, expected_version))

    @mcp.tool(title="Create or expand recurring work", description="Lead: create a series or idempotently expand an existing series into the current window. rule fields: frequency DAILY/WEEKLY, dtstart ISO date, interval, byday weekdays 0=Monday..6, count OR until, exdates. Each occurrence has a stable original date and independent owner commitment; edit a returned item id for a single exception. This is a documented RFC5545 subset, not an arbitrary RRULE parser.",
              annotations=annotations(read_only=False))
    async def manage_recurrence(ctx: Context, plan: str, action: Literal["create", "expand"], series: str | None = None,
                                title: str | None = None, owner: str | None = None, rule: dict[str, Any] | None = None,
                                spec: dict[str, Any] | None = None) -> dict[str, Any]:
        return runner.run(ctx, "manage_recurrence", lambda m: engine.manage_recurrence(m.id, plan, action, series, title, owner, rule, spec))

    @mcp.tool(title="Repair a project resource schedule", description="Lead: prepare a parallel schedule respecting task dependencies, individual work calendars, shared capacity, locks, deadlines and other committed projects. Minimize changed start times, then moved minutes, then priority-weighted completion. Proof reports whether optimality was proven. This creates a proposal only; then request_confirmations and commit_plan. Never approve for a person.",
              annotations=annotations(read_only=False))
    async def repair_project(ctx: Context, plan: str, budget_s: Annotated[float, Field(ge=0.05, le=10)] = 3) -> dict[str, Any]:
        return await asyncio.to_thread(runner.run, ctx, "repair_project", lambda m: engine.repair_project(m.id, plan, budget_s))

    @mcp.tool(title="Start a group", description="Start a new group of people who plan together, with the speaker as organizer. Nobody else is added: share an invite from invite_to_group and each person joins on their own page.",
              annotations=annotations(read_only=False))
    async def create_group(ctx: Context, name: Annotated[str, Field(description='Group name, e.g. "Cousins" or "Running club".')],
                           timezone: Annotated[str | None, Field(description="IANA time zone; default: the speaker's other groups, else America/Toronto.")] = None,
                           currency: Annotated[str | None, Field(description="Three-letter currency code for shared costs.")] = None) -> dict[str, Any]:
        return runner.run(ctx, "create_group", lambda m: engine.create_group(m.id, name, timezone, currency))

    @mcp.tool(title="Invite people to a group", description="Organizer: make a shareable invite code (and link) for one of the speaker's groups. It adds nobody by itself; each person accepts it on their own GroupFlow page. Read the code out exactly.",
              annotations=annotations(read_only=False))
    async def invite_to_group(ctx: Context, group: Annotated[str | None, Field(description="Group id or name; needed when the speaker is in several groups.")] = None) -> dict[str, Any]:
        return runner.run(ctx, "invite_to_group", lambda m: engine.invite_to_group(m.id, group))


__all__ = ["register", "INSTRUCTIONS", "NAMES"]
