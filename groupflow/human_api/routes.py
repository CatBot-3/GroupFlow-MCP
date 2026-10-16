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
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from groupflow.core.errors import DomainError
from groupflow.core.identity import Member

STATUS = {"UNAUTHENTICATED": 401, "CSRF": 403, "FORBIDDEN": 403, "NOT_FOUND": 404, "STALE_CANDIDATE": 409, "STALE": 409,
          "CHALLENGE_USED": 409, "CHALLENGE_EXPIRED": 410, "INVALID_ARGUMENT": 400, "AMBIGUOUS_PLAN": 400}


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

    async def run(request: Request, write: bool, fn) -> JSONResponse:
        try:
            member = session(request, write)
            return JSONResponse(fn(member))
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

    @app.post("/human/workspace/roles")
    async def workspace_role(request: Request, body: RoleBody) -> JSONResponse:
        return await run(request, True, lambda m: engine.human_workspace_role(m, body.group, body.target, body.role, body.grant))
