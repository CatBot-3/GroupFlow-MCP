"""Demo console endpoints (admin key only): inject a verified outside fact, the
role a trusted connector would play. The page labels these as demo events."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from groupflow.core.errors import DomainError
from groupflow.core.util import new_id


class ObservationBody(BaseModel):
    group_id: str
    kind: str = "place_unavailable"
    place_id: str
    date: str | None = None
    start: str = "00:00"
    end: str = "23:59"
    price: float | None = None
    source_event_id: str | None = None
    verified: bool = True


def register_debug_routes(app: FastAPI, services: Any, admin_ok: Callable[[Request], bool]) -> None:
    engine = services.engine

    @app.post("/debug/observations")
    async def inject(request: Request, body: ObservationBody) -> JSONResponse:
        if not admin_ok(request):
            return JSONResponse({"error": "Admin key required."}, status_code=401)
        payload: dict[str, Any] = {"place_id": body.place_id, "from": body.start, "to": body.end}
        if body.date:
            payload["date"] = body.date
        if body.kind == "price_changed":
            payload = {"place_id": body.place_id, "price_minor": int(round(float(body.price or 0) * 100))}
        try:
            out = engine.inject_observation(body.group_id, body.kind, payload, source="demo_console",
                                            source_event_id=body.source_event_id or new_id("demo"), verified=body.verified)
            return JSONResponse(out)
        except DomainError as err:
            return JSONResponse({"error": err.to_json()}, status_code=400)

    @app.post("/debug/worker")
    async def run_worker(request: Request) -> JSONResponse:
        if not admin_ok(request):
            return JSONResponse({"error": "Admin key required."}, status_code=401)
        return JSONResponse({"jobs": engine.run_queued_jobs()})
