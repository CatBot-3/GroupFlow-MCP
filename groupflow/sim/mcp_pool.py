"""One simulated Echo's connection to GroupFlow: the official Python MCP client over
Streamable HTTP, authenticated with that member's bearer token, negotiating the
2025-11-25 handshake. Every JSON-RPC message is recorded for the traffic view."""
from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass
from typing import Any

import anyio
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.message import SessionMessage

from .traffic import TrafficLog


@dataclass
class ToolResult:
    ok: bool
    data: dict[str, Any] | None
    error: dict[str, Any] | None


def _dump(msg: Any) -> dict[str, Any]:
    try:
        return msg.model_dump(by_alias=True, exclude_none=True, mode="json")
    except Exception:  # noqa: BLE001
        return {"raw": str(msg)}


@contextlib.asynccontextmanager
async def recording_transport(url: str, http: httpx2.AsyncClient, record):
    """Wrap the SDK's Streamable HTTP transport and record messages in both directions."""
    async with streamable_http_client(url, http_client=http) as (read, write):
        to_client_send, to_client_recv = anyio.create_memory_object_stream(0)
        from_client_send, from_client_recv = anyio.create_memory_object_stream(0)

        async def pump_in() -> None:
            async with to_client_send:
                async for item in read:
                    if isinstance(item, SessionMessage):
                        record("in", _dump(item.message))
                    await to_client_send.send(item)

        async def pump_out() -> None:
            async with from_client_recv:
                async for item in from_client_recv:
                    if isinstance(item, SessionMessage):
                        record("out", _dump(item.message))
                    await write.send(item)

        async with anyio.create_task_group() as tg:
            tg.start_soon(pump_in)
            tg.start_soon(pump_out)
            try:
                yield to_client_recv, from_client_send
            finally:
                tg.cancel_scope.cancel()


class MemberSession:
    def __init__(self, member_id: str, token: str, url: str, source: str, traffic: TrafficLog, headers: dict[str, str] | None = None):
        self.member_id = member_id
        self.token = token
        self.url = url
        self.source = source
        self.traffic = traffic
        self.headers = dict(headers or {})  # e.g. X-GroupFlow-Listeners for a group chat session
        self.client: Client | None = None
        self._lock = asyncio.Lock()
        self._tools: list[Any] | None = None
        self.session_id: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._closing = asyncio.Event()

    def _record(self, direction: str, msg: dict[str, Any]) -> None:
        self.traffic.record(self.member_id, self.source, direction, msg, self.session_id)

    async def _connect(self) -> Client:
        """The session's async context lives in its own task, so any request task may use it and closing is always clean."""
        ready: asyncio.Future[Client] = asyncio.get_running_loop().create_future()
        self._closing = asyncio.Event()

        async def own() -> None:
            try:
                async with contextlib.AsyncExitStack() as stack:
                    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {self.token}", **self.headers}, timeout=60)

                    async def hook(response: httpx2.Response) -> None:
                        sid = response.headers.get("mcp-session-id")
                        if sid:
                            self.session_id = sid

                    http.event_hooks["response"].append(hook)
                    await stack.enter_async_context(http)
                    client = Client(recording_transport(self.url, http, self._record), mode="legacy", client_info=_client_info(self.member_id))
                    await stack.enter_async_context(client)
                    ready.set_result(client)
                    await self._closing.wait()
            except BaseException as err:  # noqa: BLE001
                if not ready.done():
                    ready.set_exception(err if isinstance(err, Exception) else RuntimeError(str(err)))

        self._task = asyncio.create_task(own(), name=f"mcp-{self.member_id}-{self.source}")
        self.client = await ready
        return self.client

    async def ensure(self) -> Client:
        async with self._lock:
            if self.client is None:
                await self._connect()
            return self.client  # type: ignore[return-value]

    async def list_tools(self) -> list[Any]:
        if self._tools is not None:
            return self._tools
        client = await self.ensure()
        self._tools = (await client.list_tools()).tools
        return self._tools

    @property
    def protocol_version(self) -> str | None:
        return self.client.session.protocol_version if self.client else None

    async def call_tool(self, name: str, args: dict[str, Any]) -> ToolResult:
        for attempt in range(2):
            try:
                client = await self.ensure()
                res = await client.call_tool(name, args)
                if res.is_error:
                    text = res.content[0].text if res.content else "{}"
                    try:
                        error = json.loads(text).get("error") or {"code": "ERROR", "message": text}
                    except json.JSONDecodeError:
                        error = {"code": "ERROR", "message": text}
                    return ToolResult(ok=False, data=None, error=error)
                return ToolResult(ok=True, data=res.structured_content or {}, error=None)
            except Exception:  # noqa: BLE001 - server restarted or session expired: reconnect once
                await self.close()
                if attempt:
                    raise
        raise RuntimeError("unreachable")

    async def close(self) -> None:
        task = getattr(self, "_task", None)
        self.client, self._tools, self.session_id = None, None, None
        if task is not None:
            self._closing.set()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=5)
            self._task = None


def _client_info(member: str):
    from mcp_types import Implementation

    return Implementation(name=f"groupflow-echo-{member}", version="0.6.0")


class SessionPool:
    def __init__(self, tokens: dict[str, str], url: str, traffic: TrafficLog):
        self.tokens = tokens
        self.url = url
        self.traffic = traffic
        self.pools: dict[str, MemberSession] = {}

    def get(self, member_id: str, source: str = "alexa", headers: dict[str, str] | None = None) -> MemberSession:
        """One MCP session per (person, source). A source with headers (a group chat room declaring its listeners) is its own session."""
        key = f"{member_id}:{source}"
        s = self.pools.get(key)
        if s is None:
            if member_id not in self.tokens:
                raise KeyError(f"Unknown member {member_id}")
            s = MemberSession(member_id, self.tokens[member_id], self.url, source, self.traffic, headers)
            self.pools[key] = s
        return s

    def has(self, member_id: str) -> bool:
        return member_id in self.tokens

    async def set_token(self, member_id: str, token: str) -> None:
        """A person linked (or re-linked) this host: later sessions use the new token, open ones are closed."""
        self.tokens[member_id] = token
        for key in [k for k in self.pools if k.startswith(f"{member_id}:")]:
            await self.pools.pop(key).close()

    async def close_all(self) -> None:
        for s in list(self.pools.values()):
            await s.close()
        self.pools.clear()
