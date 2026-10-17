"""Shapes returned by the GroupFlow tools (also their MCP output schemas).

People appear by name, because that is how Alexa talks about them and how the
tools take them. Ids are given for items, requests and plans, which the tools
take back. Nothing in these views ever carries another member's private
condition, reason or answer detail; the engine builds every view for one
viewer from what that viewer may see.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Certainty = Literal["tentative", "committed"]
PlanStatus = Literal["ACTIVE", "DRAFT", "PROPOSED", "AWAITING_CONFIRMATIONS", "READY_TO_COMMIT", "COMMITTED", "DEGRADED", "REPAIRING",
                     "BLOCKED", "NEEDS_ATTENTION", "CANCELLED"]


# ---------------------------------------------------------------- items

class SuggestionView(BaseModel):
    request: str | None = Field(description="Request id, for decide_change.")
    value: str
    by: str
    firm: bool = Field(description="True for a firm proposal; false for an idea floated with maybe / how about.")
    said: str | None = Field(default=None, description="The person's own words, if recorded.")


class DetailView(BaseModel):
    id: str
    label: str
    value: str | None
    owner: str
    decided_by: str = Field(description='"owner" (the owner decides) or "anyone" (anyone in the plan may change it).')
    status: Literal["confirmed", "disputed", "has_suggestions", "unset"]
    pending: list[SuggestionView] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


class TaskView(BaseModel):
    id: str
    title: str
    type: str
    owner: str
    status: Literal["todo", "in_progress", "done"]
    due: str | None = None
    decided_by: str
    blocked_by: list[str] = Field(default_factory=list)
    waits_on: list[str] = Field(default_factory=list, description="Ids of tasks this one waits on.")
    pending: list[SuggestionView] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


class ActivityView(BaseModel):
    id: str
    title: str
    when: str = Field(description='"18:30-19:30", or with the day for plans over several days.')
    place: str | None
    participants: str = Field(description='"everyone" or the names of the people taking part.')
    cost_each: str | None = None
    your_cost: str | None = None
    status: Literal["planned", "dropped"] = "planned"


class VersionView(BaseModel):
    label: str = Field(description='"Draft", or a proposal label like "P2".')
    kind: Literal["draft", "candidate", "committed"]
    activities: list[ActivityView]
    your_total: str | None = None
    changes: list[str] = Field(default_factory=list, description="What differs from the agreed version (or from the draft).")
    changed_activity_ids: list[str] = Field(default_factory=list, description="Distinct changed activities; several changed fields still count as one activity.")


class ConfirmationRow(BaseModel):
    person: str
    done: bool
    items: dict[str, str] = Field(description="Activity title -> approved / pending / declined / carried_forward / ...")


class ConfirmationsView(BaseModel):
    version: str
    required: int
    satisfied: int
    complete: bool
    rows: list[ConfirmationRow]


class RequestView(BaseModel):
    request: str
    plan: str
    item: str | None
    what: str = Field(description="What would change.")
    current: str | None
    proposed: str
    by: str
    firm: bool
    said: str | None = None


class FlagView(BaseModel):
    item: str
    label: str
    plan: str
    message: str


class TodoView(BaseModel):
    kind: Literal["confirm", "verify_condition", "decide", "repair", "commit", "send"]
    plan: str
    text: str
    where: str = Field(description='"your GroupFlow page" for things only a person may do there, or "ask Alexa".')


class ConditionView(BaseModel):
    id: str
    plan: str
    name: str
    description: str
    status: Literal["unverified", "active", "retired"]
    private: bool


class PlanView(BaseModel):
    id: str
    title: str
    kind: str = Field(description="Template name.")
    group: str
    group_id: str | None = None
    visible_to: list[str]
    organizer: str
    you_organize: bool
    dates: str
    status: PlanStatus
    status_reason: str | None = None
    version: int = Field(description="Optionally pass as expected_version on plan-level writes.")
    details: list[DetailView] = Field(default_factory=list)
    tasks: list[TaskView] = Field(default_factory=list)
    agreed: VersionView | None = Field(default=None, description="The version everyone agreed to.")
    working: VersionView | None = Field(default=None, description="The draft, or a proposed or repaired version not yet agreed.")
    confirmations: ConfirmationsView | None = None
    agreement_summary: dict | None = Field(default=None, description="Current committed version's checked confirmation counts and changes from its parent; not a new confirmation request.")
    observations: list[str] = Field(default_factory=list)
    bookings: list[str] = Field(default_factory=list)
    item_types: list[str] = Field(default_factory=list)
    condition_kinds: list[str] = Field(default_factory=list)
    next_step: str
    lifecycle: str = "bounded"
    planning_window_days: int | None = None
    preset: str | None = None
    project: dict | None = None


class PlanSummaryView(BaseModel):
    id: str
    title: str
    template: str
    group: str
    group_id: str = ''
    status: PlanStatus
    version: int
    you_organize: bool
    unread_updates: int = 0
    pending_decisions: int = 0
    your_open_tasks: int = 0
    key_items: list[dict] = Field(default_factory=list)
    item_types: list[dict] = Field(default_factory=list)


class PersonalTaskView(BaseModel):
    id: str
    title: str
    plan: str
    plan_id: str
    status: str
    due: str | None = None
    blockers: list[dict[str, str]] = Field(default_factory=list, description="Current visible dependency items: item id, label, decision maker and reason. These are task owners, not private condition owners.")
    flags: list[str] = Field(default_factory=list)


class TemplateSummaryView(BaseModel):
    id: str
    name: str
    summary: str
    days: int


class TemplateListView(BaseModel):
    templates: list[TemplateSummaryView]
    total: int
    next_offset: int | None = None


class TemplateSpecView(BaseModel):
    id: str
    name: str
    days: int
    item_types: list[dict]
    conditions: list[dict]
    starters: dict[str, list[str]]
    version: str = "1"
    lifecycle: str = "bounded"
    presets: list[dict[str, str]] = Field(default_factory=list)


class ContextView(BaseModel):
    you: str
    today: str
    headline: str
    decisions_for_you: list[RequestView]
    updates: list[str]
    your_todos: list[TodoView]
    affected: list[FlagView]
    your_conditions: list[ConditionView]
    plans: list[PlanView]
    other_plans: list[str] = Field(default_factory=list, description="Plans you can see but that were left out of this view.")
    plan_index: list[PlanSummaryView] = Field(default_factory=list)
    active_plan_id: str | None = None
    next_offset: int | None = None
    truncated: dict[str, int] = Field(default_factory=dict)
    templates: list[TemplateSummaryView] = Field(default_factory=list)
    template_next_offset: int | None = None
    your_tasks: list[PersonalTaskView] = Field(default_factory=list)
    groups: list[dict[str, str]] = Field(default_factory=list, description="Your active groups: canonical id and display name, for creation and disambiguation. No hidden plan metadata.")
    group_next_offset: int | None = None
    shared_with: list[str] = Field(default_factory=list, description="Other people who will read or hear this reply (a group chat, a shared device). Empty: only the speaker.")
    shared_note: str | None = None
    open_questions: list[dict[str, Any]] = Field(default_factory=list, description=(
        "Questions the assistant asked this speaker that are still unanswered (newest first). If the speaker's words answer one, call answer_followup."))
    your_reminders: list[dict[str, Any]] = Field(default_factory=list, description=(
        "Reminders this speaker set that have not gone off yet (soonest first). To drop one, call set_reminder with cancel=<id>."))


# ---------------------------------------------------------------- search

class SearchHit(BaseModel):
    plan: str
    id: str
    shape: Literal["detail", "task", "activity"]
    label: str
    state: str
    owner: str | None = None


class PlaceHit(BaseModel):
    plan: str
    id: str
    name: str
    kind: str
    price: str
    tags: list[str]


class SearchView(BaseModel):
    query: str
    matched: bool = Field(description="False if nothing matched; check get_context before saying something does not exist.")
    items: list[SearchHit]
    places: list[PlaceHit]


# ---------------------------------------------------------------- writes

class ImpactView(BaseModel):
    reopened: list[str] = Field(default_factory=list)
    flagged: list[str] = Field(default_factory=list)
    downstream: list[str] = Field(default_factory=list)
    notified: list[str] = Field(default_factory=list)
    unblocked: list[str] = Field(default_factory=list)


class ChangeResult(BaseModel):
    outcome: Literal["confirmed", "corroborated", "already_pending", "pending_review", "tentative", "created", "updated", "removed",
                     "draft_updated", "candidate_updated", "candidate_created", "accepted", "rejected", "amended"]
    message: str
    plan: str
    item: str | None = None
    request: RequestView | None = None
    impacts: ImpactView | None = None
    plan_status: PlanStatus | None = None
    problems: list[str] = Field(default_factory=list, description="Hard conditions the changed version would break (no names).")
    suggested_followup: dict[str, Any] | None = Field(default=None, description=(
        "A next step the server offers (e.g. telling the others about this decision). Ask it at the end of your reply; if the speaker agrees, "
        "call answer_followup with its id."))


class CreatePlanResult(BaseModel):
    outcome: Literal["created"]
    message: str
    plan: PlanView


class LinkResult(BaseModel):
    outcome: Literal["linked", "already_linked", "unlinked"]
    message: str


class ConditionsResult(BaseModel):
    outcome: Literal["recorded"]
    message: str
    conditions: list[ConditionView]
    needs_verification: bool


class ObservationResult(BaseModel):
    outcome: Literal["recorded", "duplicate"]
    message: str
    verified: bool
    plan_status: PlanStatus | None = None


class ConfirmationsResult(BaseModel):
    outcome: Literal["requested", "ready", "infeasible"]
    message: str
    plan_status: PlanStatus
    version: str | None = None
    waiting_for: list[str] = Field(default_factory=list)
    carried_forward: int = 0
    problems: list[str] = Field(default_factory=list)


class ProofView(BaseModel):
    status: Literal["optimal_within_domain", "best_found", "infeasible_within_domain", "unknown"]
    method: Literal["enumeration", "cp-sat"]
    domain_size: int
    explored: int
    search_complete: bool
    runtime_ms: float
    requested_method: str | None = None
    fallback_reason: str | None = None


class AlternativeView(BaseModel):
    candidate: str
    label: str
    alternate: bool = Field(description="True for a labelled fallback that is worse than the first one.")
    changes: list[str]
    unchanged: list[str]
    cost_each_before: str
    cost_each_after: str
    reconfirm: list[str]


class AlternativesResult(BaseModel):
    job: str
    status: Literal["queued", "running", "done", "failed"]
    message: str
    plan_status: PlanStatus
    proof: ProofView | None = None
    alternatives: list[AlternativeView] = Field(default_factory=list)
    conflict: list[str] = Field(default_factory=list, description="When nothing fits: which kinds of conditions clash, without naming anyone.")


class CommitResult(BaseModel):
    outcome: Literal["committed", "blocked", "already_committed"]
    message: str
    plan_status: PlanStatus
    blockers: list[str] = Field(default_factory=list)
    bookings: list[str] = Field(default_factory=list)


class DecisionResult(BaseModel):
    outcome: Literal["accepted", "rejected", "amended"]
    message: str
    plan: str
    item: str | None = None
    impacts: ImpactView | None = None
    plan_status: PlanStatus | None = None
    suggested_followup: dict[str, Any] | None = Field(default=None, description=(
        "A next step the server offers (e.g. telling the others about this decision). Ask it at the end of your reply; if the speaker agrees, "
        "call answer_followup with its id."))


# ---------------------------------------------------------------- explain and history

class BlockerPath(BaseModel):
    root: str
    owner: str
    reason: str
    path: list[str]
    holds_up: list[str]


class RejectedOption(BaseModel):
    option: str
    works: bool
    reason: str
    would_also_change: list[str] = Field(default_factory=list)


class ExplainView(BaseModel):
    subject: str
    message: str
    blockers: list[BlockerPath] = Field(default_factory=list)
    changes: list[str] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
    options: list[RejectedOption] = Field(default_factory=list, description="Other options and why they were not chosen (no names).")
    reconfirm: list[str] = Field(default_factory=list)


class HistoryEntry(BaseModel):
    at: str
    who: str | None
    action: str
    detail: str
    said: str | None = None


class HistoryView(BaseModel):
    subject: str
    current: str | None
    entries: list[HistoryEntry]
