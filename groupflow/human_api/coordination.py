"""Authenticated, CSRF-protected personal coordination pages. No MCP grants."""
from __future__ import annotations

import asyncio
from typing import Any, Literal
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from groupflow.core.errors import DomainError


class StrictBody(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ContestBody(StrictBody):
    plan: str
    activity: str
    reason: str = Field(min_length=1, max_length=1000)
    expected: str


class FallbackBody(StrictBody):
    plan: str
    activity: str
    places: list[str] = Field(min_length=1, max_length=3)
    expires_at: str
    max_total_minor: int | None = Field(default=None, ge=0, le=10**8, strict=True)
    latest_end: str | None = None


class FallbackDecision(StrictBody):
    challenge_id: str
    nonce: str
    decision: Literal['approve', 'decline']


class FallbackRevoke(StrictBody):
    id: str


class FairnessBody(StrictBody):
    group: str
    enabled: bool = Field(strict=True)
    reset: bool = Field(default=False, strict=True)


class PlanningBody(StrictBody):
    plan: str
    candidate: str | None = None
    mode: Literal['options', 'facts', 'relaxations', 'agreement', 'resilience', 'fairness'] = 'options'
    scenarios: list[dict[str, Any]] | None = Field(default=None, min_length=1, max_length=6)


def register_coordination_routes(app, engine, session, error_response):
    async def call(request, write, fn):
        try:
            member = session(request, write)
            return JSONResponse(await asyncio.to_thread(fn, member), headers={'Cache-Control': 'private, no-store'})
        except DomainError as err:
            return error_response(err)

    @app.get('/human/agreements')
    async def agreements(request: Request, plan: str):
        return await call(request, False, lambda m: engine.human_agreements(m, plan))

    @app.post('/human/agreements/contest')
    async def contest(request: Request, body: ContestBody):
        return await call(request, True, lambda m: engine.human_contest(m, **body.model_dump()))

    @app.get('/human/fallbacks')
    async def fallbacks(request: Request, plan: str):
        return await call(request, False, lambda m: engine.human_fallbacks(m, plan))

    @app.post('/human/fallbacks/challenges')
    async def fallback_challenge(request: Request, body: FallbackBody):
        return await call(request, True, lambda m: engine.human_fallback_challenge(m, **body.model_dump()))

    @app.post('/human/fallbacks/decisions')
    async def fallback_decision(request: Request, body: FallbackDecision):
        return await call(request, True, lambda m: engine.human_fallback_decide(m, **body.model_dump()))

    @app.post('/human/fallbacks/revoke')
    async def fallback_revoke(request: Request, body: FallbackRevoke):
        return await call(request, True, lambda m: engine.human_fallback_revoke(m, body.id))

    @app.post('/human/fairness/preferences')
    async def fairness_preferences(request: Request, body: FairnessBody):
        return await call(request, True, lambda m: engine.human_fairness_preference(m, **body.model_dump()))

    @app.get('/human/fairness/preferences')
    async def fairness_status(request: Request, group: str):
        return await call(request, False, lambda m: engine.human_fairness_status(m, group))

    @app.get('/human/planning/privacy')
    async def privacy_review(request: Request, plan: str):
        return await call(request, False, lambda m: engine.human_private_review(m, plan))

    @app.post('/human/planning')
    async def planning(request: Request, body: PlanningBody):
        def execute(m):
            args = body.model_dump()
            if body.mode == 'options':
                return engine.capture_workflow(m.id, 'planning_options', lambda: engine.planning_options(m.id, **args), arguments=args)
            return engine.planning_options(m.id, **args)
        return await call(request, True, execute)
