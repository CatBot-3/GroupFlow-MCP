"""Records every JSON-RPC message between the simulated Echos and the MCP server,
so the page shows the real protocol traffic, not a reconstruction."""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

MAX = 800


def _trim(value: Any, depth: int = 0) -> Any:
    if isinstance(value, str):
        return value if len(value) <= 400 else value[:400] + "..."
    if isinstance(value, list):
        head = [_trim(v, depth + 1) for v in value[:12]]
        return head + [f"... {len(value) - 12} more"] if len(value) > 12 else head
    if isinstance(value, dict):
        if depth > 6:
            return "..."
        return {k: _trim(v, depth + 1) for k, v in value.items()}
    return value


def _short_args(args: Any) -> str:
    if not isinstance(args, dict):
        return ""
    parts = []
    for k, v in args.items():
        if k in ("quote", "idempotency_key"):
            continue
        text = json.dumps(v, ensure_ascii=False)
        parts.append(f"{k}={text[:40] + '...' if len(text) > 40 else text}")
    return ", ".join(parts)


def _result_summary(result: Any) -> str:
    if not isinstance(result, dict):
        return "ok"
    if result.get("protocolVersion"):
        return f"protocol {result['protocolVersion']}"
    if isinstance(result.get("tools"), list):
        return f"{len(result['tools'])} tools"
    if result.get("isError"):
        try:
            return "error " + json.loads(result["content"][0]["text"])["error"]["code"]
        except Exception:  # noqa: BLE001
            return "error"
    sc = result.get("structuredContent")
    if not isinstance(sc, dict):
        return "ok"
    for key in ("outcome", "headline"):
        if isinstance(sc.get(key), str):
            return sc[key]
    if isinstance(sc.get("plan"), dict) and sc["plan"].get("status"):
        return f"plan {sc['plan']['status']}"
    if isinstance(sc.get("slots"), list):
        return f"{len(sc['slots'])} slots, {len(sc.get('tasks', []))} tasks visible"
    if isinstance(sc.get("roots"), list):
        return f"{len(sc['roots'])} root blocker(s)"
    if isinstance(sc.get("entries"), list):
        return f"{len(sc['entries'])} history entries"
    if isinstance(sc.get("status"), str):
        return sc["status"]
    return "ok"


class TrafficLog:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []
        self.seq = 0
        self.listeners: list[Callable[[dict[str, Any]], None]] = []
        self.pending: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def subscribe(self, fn: Callable[[dict[str, Any]], None]) -> None:
        self.listeners.append(fn)

    def record(self, member: str, source: str, direction: str, msg: dict[str, Any], session_id: str | None) -> dict[str, Any]:
        with self._lock:
            key = f"{member}:{source}:{msg.get('id')}"
            method = msg.get("method") or ""
            ms = None
            if msg.get("method") and "id" in msg:
                kind = "request"
                self.pending[key] = (method, time.perf_counter())
                params = msg.get("params") or {}
                summary = f"{params.get('name')}({_short_args(params.get('arguments'))})" if method == "tools/call" else method
            elif msg.get("method"):
                kind, summary = "notification", method
            else:
                started = self.pending.pop(key, None)
                method = started[0] if started else "response"
                ms = (time.perf_counter() - started[1]) * 1000 if started else None
                if "error" in msg:
                    kind, summary = "error", str((msg.get("error") or {}).get("message"))
                else:
                    kind, summary = "response", _result_summary(msg.get("result"))
            self.seq += 1
            entry = {"seq": self.seq, "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                     "member": member, "source": source, "direction": direction, "kind": kind, "method": method, "id": msg.get("id"),
                     "summary": summary, "ms": ms, "sessionId": session_id, "payload": _trim(msg)}
            self.entries.append(entry)
            if len(self.entries) > MAX:
                del self.entries[: len(self.entries) - MAX]
        for fn in list(self.listeners):
            try:
                fn(entry)
            except Exception:  # noqa: BLE001
                pass
        return entry

    def since(self, seq: int) -> list[dict[str, Any]]:
        with self._lock:
            return [e for e in self.entries if e["seq"] > seq]

    def clear(self) -> None:
        with self._lock:
            self.entries = []
            self.pending.clear()
