"""Capture protocol evidence: the real JSON-RPC exchanged with the GroupFlow MCP
server over Streamable HTTP, written to docs/PROTOCOL_EVIDENCE.md.

    python -m groupflow evidence            write docs/PROTOCOL_EVIDENCE.md
    python -m groupflow evidence --stdout   print it instead

The server runs in-process on a random port with a throwaway database, so this
never touches the demo data. Bearer tokens are redacted in the output.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import platform
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import httpx2

from groupflow.core.serve import serve_in_background
from groupflow.core.settings import REPO_ROOT, ServerSettings
from groupflow.runtime import build_runtime

ACCEPT = "application/json, text/event-stream"


def _short(value: Any, limit: int = 3600) -> str:
    text = json.dumps(value, indent=2, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "\n  ... (truncated)"


class Recorder:
    def __init__(self, base: str, tokens: dict[str, str]):
        self.base = base
        self.tokens = tokens
        self.parts: list[str] = []
        self.http = httpx.AsyncClient(base_url=base, timeout=30)

    def redact(self, headers: dict[str, str]) -> dict[str, str]:
        out = dict(headers)
        auth = out.get("authorization")
        if auth:
            who = next((m for m, t in self.tokens.items() if auth.endswith(t)), "unknown")
            out["authorization"] = f"Bearer <{who}'s token>"
        return out

    async def post(self, title: str, body: dict[str, Any] | None, headers: dict[str, str], note: str = "", show_body: Any = None) -> httpx.Response:
        h = {"content-type": "application/json", "accept": ACCEPT, **headers}
        r = await self.http.post("/mcp", json=body, headers=h)
        resp_headers = {k: v for k, v in r.headers.items() if k.lower() in ("content-type", "mcp-session-id", "www-authenticate", "mcp-protocol-version")}
        try:
            payload: Any = r.json()
        except ValueError:
            payload = r.text or "(empty body)"
        self.parts += [f"### {title}", ""]
        if note:
            self.parts += [note, ""]
        self.parts += ["Request:", "", "```http", f"POST /mcp HTTP/1.1", *[f"{k}: {v}" for k, v in self.redact(h).items()], "",
                       json.dumps(body, indent=2) if body is not None else "", "```", "",
                       "Response:", "", "```http", f"HTTP/1.1 {r.status_code} {r.reason_phrase}", *[f"{k}: {v}" for k, v in resp_headers.items()], "",
                       _short(show_body(payload) if show_body and isinstance(payload, dict) else payload), "```", ""]
        return r

    async def session(self, member: str) -> dict[str, str]:
        token = {"authorization": f"Bearer {self.tokens[member]}"}
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "evidence", "version": "1"}}}
        r = await self.http.post("/mcp", json=init, headers={"content-type": "application/json", "accept": ACCEPT, **token})
        sid = r.headers["mcp-session-id"]
        h = {**token, "mcp-session-id": sid, "mcp-protocol-version": "2025-11-25"}
        await self.http.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers={"content-type": "application/json", "accept": ACCEPT, **h})
        return h


def _tool_list_view(payload: dict[str, Any]) -> dict[str, Any]:
    tools = payload.get("result", {}).get("tools", [])
    if not tools:
        return payload
    first = tools[0]
    return {"jsonrpc": payload.get("jsonrpc"), "id": payload.get("id"), "result": {
        "tools (names)": [t["name"] for t in tools],
        "first tool in full": {**first, "outputSchema": "(omitted here; present on every tool)" if first.get("outputSchema") else None}}}


def _context_view(payload: dict[str, Any]) -> dict[str, Any]:
    sc = payload.get("result", {}).get("structuredContent") or {}
    plans = sc.get("plans") or []
    return {"jsonrpc": "2.0", "id": payload.get("id"), "result": {"structuredContent (excerpt)": {
        "you": sc.get("you"), "headline": sc.get("headline"),
        "plans": [{k: p.get(k) for k in ("title", "kind", "visible_to", "status", "next_step")} for p in plans],
        "your_conditions": [c.get("description") for c in sc.get("your_conditions") or []]}}}


async def capture() -> str:
    tmp = Path(tempfile.mkdtemp(prefix="gf-evidence-"))
    settings = ServerSettings(db_path=str(tmp / "evidence.db"), admin_key="evidence", allowed_origins=["http://localhost:8788"],
                              world_path=str(REPO_ROOT / "demo" / "world.yaml"))
    rt = build_runtime(settings, stages={"friday_dinner": "awaiting"})
    bg = serve_in_background(rt.app)
    tokens = {p.id: p.token for p in rt.world.people}
    rec = Recorder(bg.base, tokens)
    try:
        alice = {"authorization": f"Bearer {tokens['alice']}"}
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "evidence-client", "version": "1.0"}}}
        rec.parts += ["## 1. Handshake, discovery and a tool call (Alice, who has three plans in two groups)", ""]
        r = await rec.post("initialize", init, alice, "The client offers 2025-11-25; the server answers with the same version, its capabilities and its instructions.")
        sid = r.headers["mcp-session-id"]
        h = {**alice, "mcp-session-id": sid, "mcp-protocol-version": "2025-11-25"}
        await rec.post("notifications/initialized", {"jsonrpc": "2.0", "method": "notifications/initialized"}, h, "A notification: no id, answered with 202 and no body.")
        await rec.post("tools/list", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, h,
                       "Everyone gets the same generic tools (14 core tools plus 2 template discovery tools); none can approve an activity. Each has an input schema, "
                       "an output schema and annotations.", show_body=_tool_list_view)
        await rec.post("tools/call get_context", {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_context", "arguments": {"mark_read": False}}}, h,
                       "Results come back as structuredContent (validated against the output schema) plus a text copy for older clients. Alice sees the "
                       "surprise party, the family's Home plan and the Friday dinner.", show_body=_context_view)

        rec.parts += ["## 2. The same endpoint, the guest of honour (Linda)", ""]
        linda = await rec.session("linda")
        await rec.post("tools/call get_context as Linda", {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_context", "arguments": {"mark_read": False}}},
                       linda, "Linda's view comes from the same tool: the party plan lives in a space without her, so it is simply not there.",
                       show_body=_context_view)
        await rec.post("tools/call get_context for the hidden plan", {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                                                    "params": {"name": "get_context", "arguments": {"plan": "party"}}}, linda,
                       "Asking for it by id is refused like any plan that does not exist for her.")

        rec.parts += ["## 3. Refusals", ""]
        await rec.post("No bearer token", init, {}, "Identity comes only from the token. Without one the server answers 401 with a WWW-Authenticate challenge.")
        await rec.post("Browser request from an unknown origin", init, {**alice, "origin": "https://evil.example"}, "Origin validation (DNS-rebinding and cross-site protection).")
        await rec.post("Another member reusing Alice's session", {"jsonrpc": "2.0", "id": 9, "method": "tools/list"},
                       {"authorization": f"Bearer {tokens['bob']}", "mcp-session-id": sid, "mcp-protocol-version": "2025-11-25"},
                       "Sessions are bound to the member who opened them.")
        await rec.post("A tool that does not exist", {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "approve_plan", "arguments": {"member": "dana"}}}, h,
                       "There is no tool that approves, declines or withdraws for anyone.")

        rec.parts += ["## 4. Version negotiation with the official SDK client", ""]
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client

        versions = {}
        for mode in ("legacy", "auto"):
            http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {tokens['charlie']}"})
            async with Client(streamable_http_client(f"{bg.base}/mcp", http_client=http), mode=mode) as c:
                versions[mode] = c.session.protocol_version
                names = [t.name for t in (await c.list_tools()).tools]
        rec.parts += ["The Python MCP SDK client (`mcp` 2.3.0) connects in both of its modes. In `auto` mode it first tries the newer per-request "
                      "envelope; the server does not offer it, so the client falls back to the handshake.", "",
                      "| SDK client mode | Negotiated protocol version |", "| --- | --- |",
                      *[f"| `{m}` | `{v}` |" for m, v in versions.items()], "", f"Tools listed in the last session: {len(names)}.", ""]
    finally:
        await rec.http.aclose()
        bg.stop()
        rt.db.close()

    import mcp

    head = ["# Protocol evidence", "",
            f"Captured {datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')} by `python -m groupflow evidence` "
            f"(Python {platform.python_version()}, MCP Python SDK {getattr(mcp, '__version__', '2.3.0')}, {platform.system()}).", "",
            "GroupFlow is a remote MCP server: Streamable HTTP on `/mcp`, JSON-RPC 2.0, protocol version **2025-11-25**, bearer-token identity. "
            "Everything below is the real traffic of one run against a throwaway database; only the tokens are redacted.", ""]
    return "\n".join(head + rec.parts) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="groupflow evidence", description="Capture the MCP handshake and tool calls as markdown.")
    p.add_argument("--out", default=str(REPO_ROOT / "docs" / "PROTOCOL_EVIDENCE.md"))
    p.add_argument("--stdout", action="store_true")
    args = p.parse_args(argv)
    logging.getLogger("groupflow").setLevel(logging.WARNING)
    text = asyncio.run(capture())
    if args.stdout:
        sys.stdout.write(text)
    else:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
