"""Human-only confirmation API. Never registered as MCP tools.

Every request carries the person's own session token (from /human/login with
their passcode) in the Authorization header; state-changing requests also
carry the session's CSRF token in X-GF-CSRF. Approvals additionally need a
one-time challenge bound to the exact scope and version digests, so an old
confirmation can never be replayed onto changed content. The agent's MCP
token is not accepted here.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from groupflow.core.errors import DomainError
from groupflow.core.identity import Member

STATUS = {"UNAUTHENTICATED": 401, "CSRF": 403, "FORBIDDEN": 403, "NOT_FOUND": 404, "STALE_CANDIDATE": 409, "STALE": 409,
          "CHALLENGE_USED": 409, "CHALLENGE_EXPIRED": 410, "INVALID_ARGUMENT": 400, "AMBIGUOUS_PLAN": 400, "DUPLICATE": 409,
          "RATE_LIMITED": 429, "CONSENT_REQUIRED": 400, "AMBIGUOUS_POLL": 400, "AMBIGUOUS_EXPENSE": 400, "PRIVATE_NOTE": 400}
STATUS.update(KEY_REUSED=409, EXPECTED_VERSION_REQUIRED=400, INVALID_EVENT_TIME=400, INFEASIBLE=400)


class PreviewBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    expected: str | None = None
    request_key: str | None = Field(default=None,min_length=1,max_length=120)


class ActionBody(PreviewBody):
    request_key: str = Field(min_length=1, max_length=120)


class PreferenceBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    plan: str
    muted: bool = False
    digest: bool = False
    quiet_start: int | None = Field(default=None, ge=0, le=23)
    quiet_end: int | None = Field(default=None, ge=0, le=23)
    timezone: str = 'America/Toronto'


class LoginBody(BaseModel):
    member_id: str
    passcode: str


class ChallengeBody(BaseModel):
    scope_id: str


class DecisionBody(BaseModel):
    challenge_id: str
    nonce: str
    decision: str
    condition: dict[str, Any] | None = None
    reason: str | None = None
    new_limit: dict[str, Any] | None = None


class VerifyBody(BaseModel):
    condition_id: str
    verify_digest: str


class ConditionBody(BaseModel):
    kind: str
    value: dict[str, Any]
    plan_id: str | None = None


class RevokeBody(BaseModel):
    receipt_id: str


class ProjectDecisionBody(BaseModel):
    challenge_id: str
    nonce: str
    decision: str


class ProjectCalendarBody(BaseModel):
    plan: str
    calendar: dict[str, Any]


class SignupBody(BaseModel):
    name: str
    passcode: str
    invite: str | None = None


class GroupBody(BaseModel):
    name: str
    timezone: str | None = None
    currency: str | None = None


class GroupRefBody(BaseModel):
    group: str


class InviteBody(BaseModel):
    code: str


class LinkBody(BaseModel):
    host: str


class LinkRefBody(BaseModel):
    id: str


class VoteBody(BaseModel):
    poll: str
    choice: str
    reason: str | None = None
    decide: bool = False


class RsvpBody(BaseModel):
    plan: str
    answer: str
    plus_ones: int | None = None


class BringBody(BaseModel):
    plan: str
    action: str
    things: list[str]


class CostConfirmBody(BaseModel):
    plan: str
    id: str
    answer: str


class RoleBody(BaseModel):
    group: str
    target: str
    role: str
    grant: bool = True


def _error(err: DomainError) -> JSONResponse:
    return JSONResponse({"error": err.to_json()}, status_code=STATUS.get(err.code, 400))


def register_human_routes(app: FastAPI, services: Any) -> None:
    engine = services.engine

    def session(request: Request, write: bool) -> Member:
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        return engine.human_session(token, request.headers.get("x-gf-csrf"), require_csrf=write)

    from .coordination import register_coordination_routes
    register_coordination_routes(app, engine, session, _error)

    async def run(request: Request, write: bool, fn) -> JSONResponse:
        try:
            member = session(request, write)
            if write and request.url.path not in {'/human/actions/apply', '/human/actions/correct'}:
                result = engine.capture_action(member.id, request.url.path.lstrip('/'), lambda: fn(member))
            else:
                result = fn(member)
            return JSONResponse(result)
        except DomainError as err:
            return _error(err)

    @app.post("/human/login")
    async def login(body: LoginBody) -> JSONResponse:
        try:
            return JSONResponse(engine.human_login(body.member_id, body.passcode))
        except DomainError as err:
            return _error(err)

    @app.get("/human/inbox")
    async def inbox(request: Request) -> JSONResponse:
        return await run(request, False, lambda m: engine.human_inbox(m))

    @app.get('/human/decisions')
    async def decision_cards(request: Request):
        return await run(request, False, lambda m: engine.decision_inbox(m.id))

    @app.get('/human/resolve')
    async def resolve_plan(request: Request, plan: str):
        return await run(request, False, lambda m: engine.resolve_action(m.id, '', plan))

    @app.get('/human/actions')
    async def actions(request: Request):
        return await run(request, False, lambda m: engine.action_history(m.id))

    @app.get('/human/actions/status')
    async def action_status(request: Request, request_key: str):
        return await run(request, False, lambda m: engine.action_status(m.id, request_key))

    @app.post('/human/actions/preview')
    async def action_preview(request: Request, body: PreviewBody):
        try:
            member=session(request, True)
            return JSONResponse(engine.preview_action(member.id,body.tool,body.arguments,body.expected))
        except DomainError as err:
            return _error(err)

    @app.get('/human/pilot')
    async def pilot(request: Request):
        return await run(request, False, lambda m: engine.pilot_metrics(m.id))

    @app.post('/human/actions/apply')
    async def apply_action(request: Request, body: ActionBody):
        return await run(request, True, lambda m: engine.apply_action(m.id, body.tool, body.arguments, body.request_key, body.expected))

    @app.post('/human/actions/correct')
    async def correct_action(request: Request, body: dict[str, Any]):
        return await run(request, True, lambda m: engine.correct_action(m.id, body.get('receipt', '')))

    @app.post('/human/preferences')
    async def preferences(request: Request, body: PreferenceBody):
        return await run(request, True, lambda m: engine.delivery_preferences(m.id, **body.model_dump()))

    @app.post('/human/calendar')
    async def calendar_issue(request: Request):
        return await run(request, True, lambda m: engine.issue_calendar(m.id))

    @app.post('/human/calendar/revoke')
    async def calendar_revoke(request: Request, body: dict[str, Any]):
        return await run(request, True, lambda m: engine.revoke_calendar(m.id, body.get('id', '')))

    @app.get('/calendar/{token}.ics')
    async def calendar_read(token: str):
        try:
            return Response(engine.calendar_feed(token), media_type='text/calendar', headers={'Cache-Control': 'private, no-store', 'Referrer-Policy': 'no-referrer'})
        except DomainError as err:
            return _error(err)

    @app.post("/human/challenges")
    async def challenge(request: Request, body: ChallengeBody) -> JSONResponse:
        return await run(request, True, lambda m: engine.human_challenge(m, body.scope_id))

    @app.post("/human/decisions")
    async def decide(request: Request, body: DecisionBody) -> JSONResponse:
        return await run(request, True, lambda m: engine.human_decide(m, body.challenge_id, body.nonce, body.decision, body.condition, body.reason,
                                                                       body.new_limit))

    @app.post("/human/conditions/verify")
    async def verify(request: Request, body: VerifyBody) -> JSONResponse:
        return await run(request, True, lambda m: engine.human_verify_condition(m, body.condition_id, body.verify_digest))

    @app.post("/human/conditions")
    async def set_condition(request: Request, body: ConditionBody) -> JSONResponse:
        return await run(request, True, lambda m: engine.human_set_condition(m, body.kind, body.value, body.plan_id))

    @app.post("/human/revoke")
    async def revoke(request: Request, body: RevokeBody) -> JSONResponse:
        return await run(request, True, lambda m: engine.human_revoke(m, body.receipt_id))

    @app.get("/human/projects/inbox")
    async def project_inbox(request: Request) -> JSONResponse:
        return await run(request, False, lambda m: engine.human_project_inbox(m))

    @app.post("/human/projects/challenges")
    async def project_challenge(request: Request, body: ChallengeBody) -> JSONResponse:
        return await run(request, True, lambda m: engine.human_project_challenge(m, body.scope_id))

    @app.post("/human/projects/decisions")
    async def project_decide(request: Request, body: ProjectDecisionBody) -> JSONResponse:
        return await run(request, True, lambda m: engine.human_project_decide(m, body.challenge_id, body.nonce, body.decision))

    @app.post("/human/projects/revoke")
    async def project_revoke(request: Request, body: ChallengeBody) -> JSONResponse:
        return await run(request, True, lambda m: engine.human_project_revoke(m, body.scope_id))

    @app.post("/human/projects/calendar")
    async def project_calendar(request: Request, body: ProjectCalendarBody) -> JSONResponse:
        return await run(request, True, lambda m: engine.human_project_calendar(m, body.plan, body.calendar))

    # ------------------------------------------------------------ accounts, groups and assistant links

    @app.post("/human/signup")
    async def signup(body: SignupBody) -> JSONResponse:
        try:
            return JSONResponse(engine.sign_up(body.name, body.passcode, body.invite))
        except DomainError as err:
            return _error(err)

    @app.get("/human/me")
    async def me(request: Request) -> JSONResponse:
        return await run(request, False, lambda m: engine.human_me(m))

    @app.post("/human/groups")
    async def create_group(request: Request, body: GroupBody) -> JSONResponse:
        return await run(request, True, lambda m: engine.create_group(m.id, body.name, body.timezone, body.currency))

    @app.post("/human/groups/invites")
    async def create_invite(request: Request, body: GroupRefBody) -> JSONResponse:
        return await run(request, True, lambda m: engine.invite_to_group(m.id, body.group))

    @app.post("/human/groups/leave")
    async def leave_group(request: Request, body: GroupRefBody) -> JSONResponse:
        return await run(request, True, lambda m: engine.leave_group(m, body.group))

    @app.get("/human/invites/{code}")
    async def invite_preview(code: str) -> JSONResponse:
        try:
            return JSONResponse(engine.invite_preview(code))
        except DomainError as err:
            return _error(err)

    @app.post("/human/invites/accept")
    async def accept_invite(request: Request, body: InviteBody) -> JSONResponse:
        return await run(request, True, lambda m: engine.accept_invite(m, body.code))

    @app.get("/human/links")
    async def links(request: Request) -> JSONResponse:
        return await run(request, False, lambda m: {"links": engine.linked_hosts(m)})

    @app.post("/human/links")
    async def link(request: Request, body: LinkBody) -> JSONResponse:
        return await run(request, True, lambda m: engine.link_host(m, body.host))

    @app.post("/human/links/revoke")
    async def unlink(request: Request, body: LinkRefBody) -> JSONResponse:
        return await run(request, True, lambda m: engine.revoke_link(m, body.id))

    # Votes, RSVPs, the bring list and payment checks: the same calls Alexa makes for the person, from their own page.
    @app.post("/human/polls/vote")
    async def poll_vote(request: Request, body: VoteBody) -> JSONResponse:
        if body.decide:
            return await run(request, True, lambda m: engine.human_decide_poll(m, body.poll, body.choice))
        return await run(request, True, lambda m: engine.human_vote(m, body.poll, body.choice, body.reason))

    @app.post("/human/rsvp")
    async def rsvp(request: Request, body: RsvpBody) -> JSONResponse:
        return await run(request, True, lambda m: engine.human_rsvp(m, body.plan, body.answer, body.plus_ones))

    @app.post("/human/bring")
    async def bring(request: Request, body: BringBody) -> JSONResponse:
        return await run(request, True, lambda m: engine.human_bring(m, body.plan, body.action, body.things))

    @app.post("/human/costs/confirm")
    async def cost_confirm(request: Request, body: CostConfirmBody) -> JSONResponse:
        return await run(request, True, lambda m: engine.human_confirm_cost(m, body.plan, body.id, body.answer))

    @app.post("/human/workspace/roles")
    async def workspace_role(request: Request, body: RoleBody) -> JSONResponse:
        return await run(request, True, lambda m: engine.human_workspace_role(m, body.group, body.target, body.role, body.grant))
