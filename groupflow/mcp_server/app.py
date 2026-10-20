"""The GroupFlow server: MCP over Streamable HTTP (spec 2025-11-25) on /mcp,
the human-only confirmation API on /human, and demo endpoints on /debug.

Security model on /mcp:
  * Host header validation against DNS rebinding (SDK transport security).
  * Origin validation: browser requests from unknown origins are refused (403).
  * Bearer token per member; the token decides who is speaking (401 otherwise).
  * Each MCP session is bound to the member who initialized it (403 for anyone else).
  * Only the handshake era is served: 2025-11-25 preferred, 2025-06-18 and 2025-03-26 accepted (Alexa+'s documented
    client initializes with 2025-03-26). The stateless 2026-07-28 envelope is refused, so v2 clients fall back.
  * Every member gets the same tools; what they can see is decided per plan by the engine.
  * A host may declare who else will hear the reply (X-GroupFlow-Listeners: member ids and/or
    group:<id>). The speaker's scope then narrows to what every listener may see; it never widens.
"""
from __future__ import annotations

import contextlib
import json
import logging
import time
from collections import OrderedDict
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
from groupflow.core.tracing import RequestTrace

from . import tools
from .common import ToolCallLog, ToolRunner

log = logging.getLogger("groupflow.server")

SERVER_NAME = "groupflow"
SERVER_TITLE = "GroupFlow"
SERVER_VERSION = "0.1.0"
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


TOOL_PROFILES = ("voice", "lab")


def build_mcp(services: Services, profile: str = "lab") -> MCPServer:
    """voice: the intent-shaped Alexa+ surface (voice.py). lab: the full 35-tool surface (tools.py). Same engine, same rules."""
    if profile == "voice":
        from .voice import VOICE_INSTRUCTIONS, register_voice, voice_terms

        mcp = MCPServer(name=SERVER_NAME, title=SERVER_TITLE, version=SERVER_VERSION, instructions=VOICE_INSTRUCTIONS)
        register_voice(mcp, services.engine, ToolRunner(services.on_call, services.engine, words=voice_terms))
    else:
        mcp = MCPServer(name=SERVER_NAME, title=SERVER_TITLE, version=SERVER_VERSION, instructions=tools.INSTRUCTIONS)
        tools.register(mcp, services.engine, ToolRunner(services.on_call, services.engine))

    async def handshake_only(ctx: Any, call_next: Any) -> Any:
        # The 2026-07-28 per-request envelope is not offered, so a client falls back to the handshake (2025-11-25 or older).
        if ctx.protocol_version in MODERN_PROTOCOL_VERSIONS:
            raise MCPError(code=-32601, message="Method not found")
        return await call_next(ctx)

    mcp.middleware.insert(0, handshake_only)
    return mcp


MAX_BOUND_SESSIONS = 5000  # session-to-member bindings kept; the oldest idle ones are forgotten first
SESSION_IDLE_S = 6 * 3600


class McpAuth:
    """ASGI wrapper for /mcp: origin check, bearer identity, and session-to-member binding."""

    def __init__(self, app: ASGIApp, identity: Identity, allowed_origins: list[str],
                 listeners: Callable[[str, str], frozenset[str]] | None = None, undeclared_audience: str = "room",
                 oauth: Callable[[str, str], Any] | None = None, public_url: str | None = None):
        self.app = app
        self.identity = identity
        self.oauth = oauth  # OAuth access tokens: (token, this server's resource) -> grant or None
        self.public_url = public_url.rstrip("/") if public_url else None
        self.allowed_origins = set(allowed_origins)
        self.sessions: OrderedDict[str, str] = OrderedDict()
        self._seen: dict[str, float] = {}
        self.resolve_listeners = listeners
        self.undeclared_audience = undeclared_audience if undeclared_audience in ("room", "private") else "room"

    def _bind(self, sid: str, member_id: str) -> None:
        now = time.monotonic()
        self.sessions[sid] = member_id
        self.sessions.move_to_end(sid)
        self._seen[sid] = now
        while len(self.sessions) > MAX_BOUND_SESSIONS:
            old, _ = self.sessions.popitem(last=False)
            self._seen.pop(old, None)
        for stale in [k for k, t in self._seen.items() if now - t > SESSION_IDLE_S]:
            self.sessions.pop(stale, None)
            self._seen.pop(stale, None)

    def _touch(self, sid: str) -> None:
        if sid in self.sessions:
            self.sessions.move_to_end(sid)
            self._seen[sid] = time.monotonic()

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
        base = self.public_url or f"{scope.get('scheme', 'http')}://{headers.get('host', 'localhost')}"
        metadata = f'{base}/.well-known/oauth-protected-resource'
        member = self.identity.by_token(token) if token else None
        service = None
        if member is None and token and self.oauth is not None:
            grant = self.oauth(token, base + "/mcp")
            if grant is not None and grant.member_id:
                member = self.identity.get(grant.member_id)
            elif grant is not None:
                service = grant  # tier 1: the host itself, no person yet
        if member is None and service is None:
            await JSONResponse(_rpc_error(-32001, "Missing or invalid bearer token."), status_code=401,
                               headers={"WWW-Authenticate": f'Bearer realm="groupflow", resource_metadata="{metadata}"'})(scope, receive, send)
            return
        principal = member.id if member is not None else f"service:{service.client_id}"
        if service is not None:
            # The service tier may initialize and list tools; running one needs a person, so answer 403 and let the host link.
            body = b""
            more = True
            while more:
                message = await receive()
                body += message.get("body", b"")
                more = message.get("more_body", False)
            try:
                parsed = json.loads(body or b"null")
            except ValueError:
                parsed = None
            calls = parsed if isinstance(parsed, list) else [parsed]
            if any(isinstance(c, dict) and c.get("method") == "tools/call" for c in calls):
                await JSONResponse(_rpc_error(-32003, "Link a GroupFlow account to use this tool."), status_code=403, headers={
                    "WWW-Authenticate": f'Bearer realm="groupflow", error="insufficient_scope", scope="mcp:resource", resource_metadata="{metadata}"'
                })(scope, receive, send)
                return
            replayed = False

            async def receive_again() -> Message:
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()
            receive = receive_again  # noqa: PLW2901 - the body was read once to inspect the method
        sid = headers.get("mcp-session-id")
        if sid and sid in self.sessions and self.sessions[sid] != principal:
            await JSONResponse(_rpc_error(-32003, "This session belongs to a different member."), status_code=403)(scope, receive, send)
            return
        if sid:
            self._touch(sid)
        declared = headers.get("x-groupflow-listeners")
        listeners: frozenset[str] | None = None
        audience = "undeclared"
        if declared and self.resolve_listeners is not None and member is not None:
            try:
                listeners = self.resolve_listeners(member.id, declared)
            except ValueError as err:
                await JSONResponse(_rpc_error(-32602, f"Invalid X-GroupFlow-Listeners: {err}"), status_code=400)(scope, receive, send)
                return
            audience = "private" if listeners == frozenset({member.id}) else "shared"
        if audience == "undeclared" and self.undeclared_audience == "private":
            audience = "private"
        scope.setdefault("state", {})
        scope["state"]["member"] = member
        scope["state"]["listeners"] = listeners
        scope["state"]["audience"] = audience
        if scope.get("method") == "DELETE" and sid:
            self.sessions.pop(sid, None)
            self._seen.pop(sid, None)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start" and not sid:
                for k, v in message.get("headers", []):
                    if k.decode("latin-1").lower() == "mcp-session-id":
                        self._bind(v.decode("latin-1"), principal)
            await send(message)

        await self.app(scope, receive, send_wrapper)


def create_server_app(services: Services, *, host: str = "127.0.0.1", allowed_origins: list[str] | None = None,
                      demo_admin: bool = True, admin_key: str | None = None, worker: Any = None, responses: str = "json",
                      allowed_hosts: list[str] | None = None, undeclared_audience: str | None = None,
                      tool_profile: str | None = None, public_url: str | None = None) -> "GroupFlowServer":
    if responses not in ("json", "sse"):
        raise ValueError('MCP responses must be "json" or "sse".')
    from groupflow.core.settings import LOCAL_HOSTS, PUBLISHED_ADMIN_KEY, env

    undeclared_audience = undeclared_audience or env("GROUPFLOW_UNDECLARED_AUDIENCE", "room") or "room"
    tool_profile = tool_profile or env("GROUPFLOW_TOOL_PROFILE", "voice") or "voice"
    if tool_profile not in TOOL_PROFILES:
        raise ValueError(f"tool profile must be one of {TOOL_PROFILES}")

    origins = allowed_origins or []
    mcp = build_mcp(services, tool_profile)
    lab = build_mcp(services, "lab") if tool_profile != "lab" else None  # /mcp/lab always serves the full set
    hosts = list(allowed_hosts) if allowed_hosts is not None else list(LOCAL_HOSTS)
    check_hosts = "*" not in hosts
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=check_hosts,
        allowed_hosts=[h for h in hosts if h != "*"] if check_hosts else [],
        allowed_origins=origins,
    )
    # Streamed (SSE) responses can carry a server request mid-call, which is how ask_followup asks the person through the host.
    starlette_mcp = mcp.streamable_http_app(json_response=responses == "json", host=host, transport_security=security, streamable_http_path="/mcp")
    oauth = getattr(services.engine, "oauth_access", None)
    auth = McpAuth(starlette_mcp, services.identity, origins, listeners=services.engine.resolve_listeners,
                   undeclared_audience=undeclared_audience, oauth=oauth, public_url=public_url)
    lab_auth = None
    if lab is not None:
        lab_app = lab.streamable_http_app(json_response=responses == "json", host=host, transport_security=security, streamable_http_path="/mcp/lab")
        lab_auth = McpAuth(lab_app, services.identity, origins, listeners=services.engine.resolve_listeners,
                           undeclared_audience=undeclared_audience, oauth=oauth, public_url=public_url)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with contextlib.AsyncExitStack() as stack:
            await stack.enter_async_context(mcp.session_manager.run())
            if lab is not None:
                await stack.enter_async_context(lab.session_manager.run())
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
    app.state.tool_profile = tool_profile
    # CORS only for the human confirmation API (browser pages); /mcp checks Origin itself.
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["authorization", "content-type", "x-gf-csrf"], expose_headers=['X-Request-ID'])
    app.add_middleware(RequestTrace)

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"ok": True, "server": {"name": SERVER_NAME, "version": SERVER_VERSION}, "protocol": PROTOCOL_VERSION,
                "transport": "streamable-http", "responses": responses, "tools": tool_profile,
                "sessions": len(auth.sessions) + (len(lab_auth.sessions) if lab_auth else 0)}

    # The demo inspector sees every plan, so it exists only with a configured secret (never the old published default).
    if demo_admin and admin_key and admin_key != PUBLISHED_ADMIN_KEY:
        import hmac

        def admin_ok(request: Request) -> bool:
            return hmac.compare_digest(request.headers.get("x-groupflow-admin", ""), admin_key)

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
    if oauth is not None:
        from groupflow.human_api.oauth_routes import register_oauth_routes

        register_oauth_routes(app, services, public_url)

    return GroupFlowServer(app, auth, lab_auth or auth)


class GroupFlowServer:
    """ASGI entry point: /mcp goes to the MCP transport, everything else to the FastAPI app (which owns the lifespan)."""

    def __init__(self, api: FastAPI, mcp_app: ASGIApp, lab_app: ASGIApp | None = None):
        self.api = api
        self.mcp_app = RequestTrace(mcp_app)
        self.lab_app = RequestTrace(lab_app) if lab_app is not None and lab_app is not mcp_app else None
        self.state = api.state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path") if scope["type"] == "http" else None
        if path in ("/mcp", "/mcp/"):
            if path == "/mcp/":
                scope = dict(scope, path="/mcp", raw_path=b"/mcp")
            await self.mcp_app(scope, receive, send)
            return
        if path in ("/mcp/lab", "/mcp/lab/"):
            if self.lab_app is None:  # the lab profile is already what /mcp serves
                scope = dict(scope, path="/mcp", raw_path=b"/mcp")
                await self.mcp_app(scope, receive, send)
                return
            if path == "/mcp/lab/":
                scope = dict(scope, path="/mcp/lab", raw_path=b"/mcp/lab")
            await self.lab_app(scope, receive, send)
            return
        await self.api(scope, receive, send)
