"""The GroupFlow server: MCP over Streamable HTTP (spec 2025-11-25) on /mcp,
the human-only confirmation API on /human, and demo endpoints on /debug.

Security model on /mcp:
  * Host header validation against DNS rebinding (SDK transport security).
  * Origin validation: browser requests from unknown origins are refused (403).
  * Bearer token per member; the token decides who is speaking (401 otherwise).
  * Each MCP session is bound to the member who initialized it (403 for anyone else).
  * Only the handshake era is served, so clients negotiate 2025-11-25.
  * Every member gets the same tools; what they can see is decided per plan by the engine.
  * A host may declare who else will hear the reply (X-GroupFlow-Listeners: member ids and/or
    group:<id>). The speaker's scope then narrows to what every listener may see; it never widens.
"""
from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from mcp_types.version import LATEST_HANDSHAKE_VERSION, MODERN_PROTOCOL_VERSIONS
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from groupflow.core.db import Db
from groupflow.core.identity import Identity, Member

from . import tools
from .common import ToolCallLog, ToolRunner

log = logging.getLogger("groupflow.server")

SERVER_NAME = "groupflow"
SERVER_TITLE = "GroupFlow"
SERVER_VERSION = "0.6.0"
PROTOCOL_VERSION = LATEST_HANDSHAKE_VERSION  # "2025-11-25"


@dataclass
class Services:
    db: Db
    identity: Identity
    engine: Any
    on_call: Callable[[ToolCallLog], None] | None = None
    on_reset: Callable[[str | None], None] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _rpc_error(code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": None}


def build_mcp(services: Services) -> MCPServer:
    mcp = MCPServer(name=SERVER_NAME, title=SERVER_TITLE, version=SERVER_VERSION, instructions=tools.INSTRUCTIONS)
    runner = ToolRunner(services.on_call)
    tools.register(mcp, services.engine, runner)

    async def handshake_only(ctx: Any, call_next: Any) -> Any:
        # The 2026-07-28 per-request envelope is not offered, so every client negotiates 2025-11-25.
        if ctx.protocol_version in MODERN_PROTOCOL_VERSIONS:
            raise MCPError(code=-32601, message="Method not found")
        return await call_next(ctx)

    mcp.middleware.insert(0, handshake_only)
    return mcp


class McpAuth:
    """ASGI wrapper for /mcp: origin check, bearer identity, and session-to-member binding."""

    def __init__(self, app: ASGIApp, identity: Identity, allowed_origins: list[str],
                 listeners: Callable[[str, str], frozenset[str]] | None = None):
        self.app = app
        self.identity = identity
        self.allowed_origins = set(allowed_origins)
        self.sessions: dict[str, str] = {}
        self.resolve_listeners = listeners

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        origin = headers.get("origin")
        if origin and origin not in self.allowed_origins:
            await JSONResponse(_rpc_error(-32000, f"Origin {origin} is not allowed."), status_code=403)(scope, receive, send)
            return
        auth = headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        member = self.identity.by_token(token) if token else None
        if member is None:
            await JSONResponse(_rpc_error(-32001, "Missing or invalid bearer token."), status_code=401,
                               headers={"WWW-Authenticate": 'Bearer realm="groupflow"'})(scope, receive, send)
            return
        sid = headers.get("mcp-session-id")
        if sid and sid in self.sessions and self.sessions[sid] != member.id:
            await JSONResponse(_rpc_error(-32003, "This session belongs to a different member."), status_code=403)(scope, receive, send)
            return
        declared = headers.get("x-groupflow-listeners")
        listeners: frozenset[str] | None = None
        if declared and self.resolve_listeners is not None:
            try:
                listeners = self.resolve_listeners(member.id, declared)
            except ValueError as err:
                await JSONResponse(_rpc_error(-32602, f"Invalid X-GroupFlow-Listeners: {err}"), status_code=400)(scope, receive, send)
                return
        scope.setdefault("state", {})
        scope["state"]["member"] = member
        scope["state"]["listeners"] = listeners
        if scope.get("method") == "DELETE" and sid:
            self.sessions.pop(sid, None)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start" and not sid:
                for k, v in message.get("headers", []):
                    if k.decode("latin-1").lower() == "mcp-session-id":
                        self.sessions[v.decode("latin-1")] = member.id
            await send(message)

        await self.app(scope, receive, send_wrapper)


def create_server_app(services: Services, *, host: str = "127.0.0.1", allowed_origins: list[str] | None = None,
                      demo_admin: bool = True, admin_key: str = "gf_demo_admin", worker: Any = None) -> "GroupFlowServer":
    origins = allowed_origins or []
    mcp = build_mcp(services)
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", "127.0.0.1", "localhost"],
        allowed_origins=origins,
    )
    starlette_mcp = mcp.streamable_http_app(json_response=True, host=host, transport_security=security, streamable_http_path="/mcp")
    auth = McpAuth(starlette_mcp, services.identity, origins, listeners=services.engine.resolve_listeners)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with mcp.session_manager.run():
            if worker is not None:
                await worker.start()
            try:
                yield
            finally:
                if worker is not None:
                    await worker.stop()

    app = FastAPI(title="GroupFlow server", version=SERVER_VERSION, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.services = services
    app.state.mcp = mcp
    app.state.mcp_auth = auth
    # CORS only for the human confirmation API (browser pages); /mcp checks Origin itself.
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["authorization", "content-type", "x-gf-csrf"])

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"ok": True, "server": {"name": SERVER_NAME, "version": SERVER_VERSION}, "protocol": PROTOCOL_VERSION,
                "transport": "streamable-http", "sessions": len(auth.sessions)}

    if demo_admin:
        def admin_ok(request: Request) -> bool:
            return request.headers.get("x-groupflow-admin") == admin_key

        @app.get("/debug/state")
        async def debug_state(request: Request, group: str | None = None) -> JSONResponse:
            if not admin_ok(request):
                return JSONResponse({"error": "Admin key required."}, status_code=401)
            view_as = request.query_params.get("as")
            try:
                return JSONResponse(services.engine.inspector(group, view_as))
            except Exception as err:  # noqa: BLE001
                return JSONResponse({"error": str(err)}, status_code=400)

        @app.get("/debug/groups")
        async def debug_groups(request: Request) -> JSONResponse:
            if not admin_ok(request):
                return JSONResponse({"error": "Admin key required."}, status_code=401)
            return JSONResponse({"groups": services.engine.groups()})

        @app.post("/debug/reset")
        async def debug_reset(request: Request, scenario: str | None = None) -> JSONResponse:
            if not admin_ok(request):
                return JSONResponse({"error": "Admin key required."}, status_code=401)
            if services.on_reset:
                services.on_reset(scenario)
            return JSONResponse({"ok": True})

        from groupflow.human_api.debug_routes import register_debug_routes

        register_debug_routes(app, services, admin_ok)

    from groupflow.human_api.routes import register_human_routes

    register_human_routes(app, services)

    return GroupFlowServer(app, auth)


class GroupFlowServer:
    """ASGI entry point: /mcp goes to the MCP transport, everything else to the FastAPI app (which owns the lifespan)."""

    def __init__(self, api: FastAPI, mcp_app: ASGIApp):
        self.api = api
        self.mcp_app = mcp_app
        self.state = api.state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") in ("/mcp", "/mcp/"):
            if scope["path"] == "/mcp/":
                scope = dict(scope, path="/mcp", raw_path=b"/mcp")
            await self.mcp_app(scope, receive, send)
            return
        await self.api(scope, receive, send)
