"""Replay the guided demo of one or more groups against a running simulator and print what happened.

Start the demo first (python -m groupflow demo), then:

    python scripts/play_demo.py                 # every group
    python scripts/play_demo.py friends team    # some groups
    python scripts/play_demo.py friends --until 5   # stop after step 5 (to look at the page)
    python scripts/play_demo.py friends --from 6    # carry on from step 6 without resetting

It drives the same endpoints as the page: "say" steps go to /api/say (the configured LLM answers
through MCP), "event" steps to the demo console, and "phones"/"verify" steps to each person's own
page through the human-only API, never through Alexa. At the end of each group it prints the plan
status the inspector reports, so a broken story shows up before anyone records it.
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Any

import httpx

BASE = "http://127.0.0.1:8788"


class Page:
    """One person's own GroupFlow page (the human-only API)."""

    def __init__(self, http: httpx.Client, member: str, passcode: str):
        self.http, self.member, self.passcode = http, member, passcode
        self.headers: dict[str, str] = {}

    def login(self) -> None:
        r = self.http.post(f"{BASE}/human/login", json={"member_id": self.member, "passcode": self.passcode})
        r.raise_for_status()
        body = r.json()
        self.headers = {"authorization": f"Bearer {body['token']}", "x-gf-csrf": body["csrf"]}

    def inbox(self) -> dict[str, Any]:
        r = self.http.get(f"{BASE}/human/inbox", headers=self.headers)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        r = self.http.post(f"{BASE}{path}", json=body, headers=self.headers)
        data = r.json()
        if r.status_code >= 400:
            raise RuntimeError(f"{path}: {data}")
        return data


def wrap(text: str, indent: str = "      ") -> str:
    words, lines, line = text.split(), [], ""
    for w in words:
        if len(line) + len(w) + 1 > 100:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        lines.append(line)
    return "\n".join(indent + x for x in lines)


def play(http: httpx.Client, scenario: dict[str, Any], first: int = 1, last: int | None = None) -> bool:
    group = scenario["id"]
    passcodes = {m["id"]: m.get("demoPasscode", "") for m in scenario["members"]}
    names = {m["id"]: m["name"] for m in scenario["members"]}
    plan_ids = {p["id"] for p in scenario["plans"]}
    print(f"\n=== {scenario['label']} ({group}) ===")
    if first <= 1:
        http.post(f"{BASE}/api/reset", json={"scenario": group}).raise_for_status()
        http.post(f"{BASE}/api/forget", json={"member": None})
    ok = True
    for i, step in enumerate(scenario["script"], 1):
        if i < first or (last is not None and i > last):
            continue
        kind = step["kind"]
        t0 = time.time()
        if kind == "say":
            r = http.post(f"{BASE}/api/say", json={"member": step["member"], "text": step["text"], "scenario": group}, timeout=180)
            if r.status_code >= 400:
                print(f"{i:>2} {names[step['member']]}: {step['text']}\n      ERROR {r.text[:300]}")
                ok = False
                continue
            turn = r.json()
            calls = ", ".join(("" if c.get("ok") else "!") + c["name"] for c in turn["calls"] if not c.get("prefetched"))
            print(f"{i:>2} {names[step['member']]}: {step['text']}")
            print(f"      [{calls or 'no tool calls'}] {time.time() - t0:.1f}s")
            print(wrap(turn["reply"]))
        elif kind == "event":
            r = http.post(f"{BASE}/api/demo/event", json={k: step[k] for k in ("group", "place_id", "start", "end", "day") if k in step})
            print(f"{i:>2} console: {step['place_id']} unavailable -> {r.json().get('outcome')} {r.json().get('affected_plans', '')}")
        elif kind in ("phones", "verify"):
            who = step.get("members") or [step["member"]]
            for m in who:
                page = Page(http, m, passcodes[m])
                page.login()
                inbox = page.inbox()
                if kind == "phones":
                    items = [x for x in inbox["pending"] if x["plan_id"] in plan_ids]
                    for item in items:
                        ch = page.post("/human/challenges", {"scope_id": item["scope_id"]})
                        page.post("/human/decisions", {"challenge_id": ch["challenge_id"], "nonce": ch["nonce"], "decision": step.get("decision", "approved")})
                    print(f"{i:>2} {names[m]}'s page: answered {len(items)} item(s)")
                    if not items:
                        ok = False
                        print("      nothing was waiting for this person")
                else:
                    drafts = [c for c in inbox["conditions"] if c["status"] == "unverified"]
                    for c in drafts:
                        page.post("/human/conditions/verify", {"condition_id": c["id"], "verify_digest": c["verify_digest"]})
                    print(f"{i:>2} {names[m]}'s page: confirmed {len(drafts)} drafted condition(s)")
                    if not drafts:
                        ok = False
        if step.get("note"):
            print(wrap("note: " + step["note"]))
    state = http.get(f"{BASE}/api/inspector", params={"scenario": group}).json()
    for p in state.get("plans", []):
        agreed = p.get("agreed") or {}
        acts = "; ".join(f"{a['title']} {a['when']} {a.get('place') or ''}".strip() for a in agreed.get("activities", []))
        print(f"   plan {p['title']}: {p['status']}" + (f" | agreed {agreed.get('label')}: {acts}" if acts else ""))
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("groups", nargs="*", help="groups to play (default: all)")
    ap.add_argument("--from", dest="first", type=int, default=1, help="start at this step without resetting the group")
    ap.add_argument("--until", dest="last", type=int, default=None, help="stop after this step")
    args = ap.parse_args(argv)
    with httpx.Client(timeout=60) as http:
        try:
            config = http.get(f"{BASE}/api/config").json()
        except httpx.HTTPError:
            print("The simulator is not reachable. Start it with: python -m groupflow demo", file=sys.stderr)
            return 2
        if not config["provider"]["ready"]:
            print(f"The LLM is not configured: {config['provider'].get('reason')}", file=sys.stderr)
            return 2
        chosen = [s for s in config["scenarios"] if not args.groups or s["id"] in args.groups]
        results = {s["id"]: play(http, s, args.first, args.last) for s in chosen}
    print("\n" + ", ".join(f"{g}: {'ok' if ok else 'CHECK'}" for g, ok in results.items()))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
