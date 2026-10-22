"""Be the model behind the simulated Alexa (AGENT_PROVIDER=relay).

    python scripts/relay_model.py next                 wait for the next completion request and print it
    python scripts/relay_model.py answer r7 < reply.json   answer request r7, then wait for and print the next one

A reply is JSON: {"text": "what Alexa says"} to finish the turn, or {"tool_calls": [{"name": "...", "arguments": {...}}]} to call
GroupFlow tools (the simulator runs them over the speaker's own MCP session and sends the results back as the next request).

Each request is printed once in full. After that only what is new is printed: the system prompt and the tool list are shown
again only if they change, and messages already printed for the same speaker are not repeated. The state behind that is kept in
--state (default data/relay_state.json); delete it to see everything again.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DATE = re.compile(r" It is ([^.]*?\(\w+/\w+\))\.")
SPEAKER = re.compile(r"^You are (?:the voice assistant on (\w+)'s Echo device|Alexa in (\w+)'s private chat thread|Alexa in a group chat; (\w+) is talking)")


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"system": [], "tools": [], "speakers": {}}


def render(req: dict, state: dict) -> str:
    out: list[str] = []
    system = req["system"]
    m = SPEAKER.search(system)
    speaker = next((g for g in (m.groups() if m else ()) if g), "unknown")
    today = (DATE.search(system) or [None, "unknown"])[1]
    shape = digest(DATE.sub(" It is <now>.", system).replace(speaker, "<name>"))
    out.append(f"=== REQUEST {req['id']} | speaker: {speaker} | it is {today} ===")
    if shape in state["system"]:
        out.append(f"[System prompt: same as shown before, for {speaker}. First line: {system.splitlines()[0]}]")
    else:
        state["system"].append(shape)
        out.append("--- SYSTEM PROMPT ---\n" + system)
    tools = req.get("tools") or []
    tools_key = digest(tools)
    if tools_key in state["tools"]:
        out.append(f"[Tools: the same {len(tools)} tools as shown before: {', '.join(t['name'] for t in tools)}]")
    else:
        state["tools"].append(tools_key)
        out.append(f"--- TOOLS ({len(tools)}) ---")
        for t in tools:
            out.append(f"* {t['name']}: {t['description']}\n  parameters: {json.dumps(t['parameters'], separators=(',', ':'))}")
    messages = req.get("messages") or []
    keys = [digest(x) for x in messages]
    before = state["speakers"].get(speaker, [])
    same = 0
    while same < min(len(keys), len(before)) and keys[same] == before[same]:
        same += 1
    state["speakers"][speaker] = keys
    if same:
        out.append(f"[Messages 1 to {same} of this conversation were shown in an earlier request.]")
    for i, x in enumerate(messages[same:], same + 1):
        role = x["role"]
        if role == "user":
            out.append(f"--- message {i}: user ---\n{x['content']}")
        elif role == "assistant":
            calls = x.get("tool_calls") or []
            head = f"--- message {i}: assistant ---"
            body = [x["content"]] if x.get("content") else []
            body += [f"tool call {c['id']}: {c['name']} {json.dumps(c['arguments'], separators=(',', ':'))}" for c in calls]
            out.append(head + "\n" + "\n".join(body))
        elif role == "tool":
            flag = " (error)" if x.get("is_error") else ""
            out.append(f"--- message {i}: result of {x['name']} ({x['tool_call_id']}){flag} ---\n{json.dumps(x['content'], separators=(',', ':'), default=str)}")
    out.append(f">>> Answer with: python scripts/relay_model.py answer {req['id']} <<'JSON'\n"
               '{"text": "..."}   or   {"tool_calls": [{"name": "...", "arguments": {...}}]}\nJSON')
    return "\n".join(out)


def wait_next(http: httpx.Client, base: str, wait: float, state_path: Path) -> int:
    deadline = time.monotonic() + wait
    while True:
        left = max(1.0, deadline - time.monotonic())
        r = http.get(f"{base}/api/relay/next", params={"wait": min(left, 60)}, timeout=left + 30)
        r.raise_for_status()
        data = r.json()
        if data.get("done"):
            print("DONE: the recording is finished. Stop here.")
            return 0
        if data.get("waiting"):
            if time.monotonic() >= deadline:
                print("No request yet (the demo is between steps). Run: python scripts/relay_model.py next")
                return 0
            continue
        state = load(state_path)
        text = render(data, state)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state))
        print(text)
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["next", "answer"])
    ap.add_argument("id", nargs="?")
    ap.add_argument("--base", default="http://127.0.0.1:8788")
    ap.add_argument("--wait", type=float, default=100, help="seconds to wait for a request before giving up (default 100)")
    ap.add_argument("--state", type=Path, default=ROOT / "data" / "relay_state.json")
    a = ap.parse_args()
    with httpx.Client(trust_env=False) as http:
        if a.command == "answer":
            if not a.id:
                print("answer needs the request id, e.g. answer r7", file=sys.stderr)
                return 2
            raw = sys.stdin.read()
            try:
                body = json.loads(raw)
            except json.JSONDecodeError as err:
                print(f"Not valid JSON ({err}). Nothing was sent; answer {a.id} again.", file=sys.stderr)
                return 2
            r = http.post(f"{a.base}/api/relay/{a.id}", json=body, timeout=30)
            if r.status_code != 200:
                print(f"Not accepted ({r.status_code}): {r.text}", file=sys.stderr)
                return 1
            print(f"[{a.id} answered]")
        return wait_next(http, a.base, a.wait, a.state)


if __name__ == "__main__":
    sys.exit(main())
