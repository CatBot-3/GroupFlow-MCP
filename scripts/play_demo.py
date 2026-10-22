"""Replay the guided demo against a running simulator and print what happened, as a rehearsal before recording.

Start the demo first (python -m groupflow demo), then:

    python scripts/play_demo.py                     # the whole story, from a reset
    python scripts/play_demo.py --until 5           # stop after step 5 (to look at the page)
    python scripts/play_demo.py --from 6            # carry on from step 6 without resetting

It drives the same endpoints as the page: "say" steps go to /api/say (the configured LLM answers
through MCP), "event" steps to the demo console, "skip" steps to the console's skip-ahead, and
"confirm"/"verify" steps to each person's own screen through the human-only API, never through Alexa;
"join" steps have each person say the invite code from Alice's turn to their own Alexa and then say yes on
their own screen, and "deliver" steps print what arrived.
At the end it prints the plan status the inspector reports, so a broken story shows up before
anyone records it.
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Any

import httpx

BASE = "http://127.0.0.1:8788"


class Page:
    """One person's own screen: their GroupFlow session through the human-only API, which the model cannot reach."""

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
    print(f"\n=== {scenario['label']} ({group}) ===")
    if first <= 1:
        http.post(f"{BASE}/api/reset", json={"scenario": group}).raise_for_status()
        http.post(f"{BASE}/api/forget", json={"member": None})
    ok = True
    invite_code = None
    for i, step in enumerate(scenario["script"], 1):
        if i < first or (last is not None and i > last):
            continue
        kind = step["kind"]
        t0 = time.time()
        if step.get("chapter"):
            print(f"\n-- {step['chapter']} --")
            if step.get("clock"):
                http.post(f"{BASE}/api/demo/clock", json={"chapter": step["chapter"], "time": step["clock"]}).raise_for_status()
        if kind == "say":
            r = http.post(f"{BASE}/api/say", json={"member": step["member"], "text": step["text"], "scenario": group}, timeout=180)
            if r.status_code >= 400:
                print(f"{i:>2} {names[step['member']]}: {step['text']}\n      ERROR {r.text[:300]}")
                ok = False
                continue
            turn = r.json()
            for c in turn["calls"]:
                if c["name"] == "invite_to_group" and c.get("ok") and (c.get("result") or {}).get("code"):
                    invite_code = c["result"]["code"]
            calls = ", ".join(("" if c.get("ok") else "!") + c["name"] for c in turn["calls"] if not c.get("prefetched"))
            print(f"{i:>2} {names[step['member']]}: {step['text']}")
            print(f"      [{calls or 'no tool calls'}] {time.time() - t0:.1f}s")
            print(wrap(turn["reply"]))
        elif kind == "join":
            # The invite code comes from Alice's turn (invite_to_group); each person says it to their own Alexa, which asks
            # GroupFlow to join, and then says yes on their own screen.
            for m in step["members"]:
                if not invite_code:
                    ok = False
                    print(f"{i:>2} no invite code from an earlier turn")
                    break
                text = step["text"].replace("{invite}", invite_code)
                r = http.post(f"{BASE}/api/say", json={"member": m, "text": text, "scenario": group}, timeout=180)
                if r.status_code >= 400:
                    ok = False
                    print(f"{i:>2} {names[m]}: {text}\n      ERROR {r.text[:300]}")
                    continue
                turn = r.json()
                calls = ", ".join(("" if c.get("ok") else "!") + c["name"] for c in turn["calls"] if not c.get("prefetched"))
                print(f"{i:>2} {names[m]}: {text}\n      [{calls or 'no tool calls'}]")
                print(wrap(turn["reply"]))
                page = Page(http, m, passcodes[m])
                page.login()
                waiting = page.inbox().get("joins", [])
                if not waiting:
                    ok = False
                    print("      nothing to join was waiting on their screen")
                    continue
                r2 = page.post("/human/joins/decide", {"id": waiting[0]["id"], "accept": True})
                print(f"      {names[m]}'s screen: {r2.get('outcome')}")
        elif kind == "deliver":
            plan_ids = {p["id"] for p in http.get(f"{BASE}/api/inspector", params={"scenario": group}).json().get("plans", [])}
            for m in step["members"]:
                page = Page(http, m, passcodes[m])
                page.login()
                notes = [n for n in page.inbox()["notifications"] if n.get("plan_id") in plan_ids]
                print(f"{i:>2} {names[m]}'s Echo: " + (notes[-1]["text"] if notes else "NOTHING ARRIVED"))
                ok = ok and bool(notes)
        elif kind == "event":
            r = http.post(f"{BASE}/api/demo/event", json={k: step[k] for k in ("group", "place_id", "start", "end", "day") if k in step})
            print(f"{i:>2} console: {step['place_id']} unavailable -> {r.json().get('outcome')} {r.json().get('affected_plans', '')}")
            plan_ids = {p["id"] for p in http.get(f"{BASE}/api/inspector", params={"scenario": group}).json().get("plans", [])}
            for m in step.get("members", []):
                page = Page(http, m, passcodes[m])
                page.login()
                notes = [n for n in page.inbox()["notifications"] if n.get("plan_id") in plan_ids]
                print(wrap(f"{names[m]}'s Echo: " + (notes[-1]["text"] if notes else "NOTHING ARRIVED")))
        elif kind == "skip":
            r = http.post(f"{BASE}/api/demo/skip-ahead", json={"hours": step.get("hours", 48), "group": group}).json()
            print(f"{i:>2} console: skip ahead {step.get('hours', 48)} quiet hours")
            for c in r.get("checkins", []):
                for sent in c.get("sent", []):
                    print(wrap(f"to {sent['name']}: {sent['text']}"))
            if not any(c.get("sent") for c in r.get("checkins", [])):
                ok = False
                print("      nobody was checked in with")
        elif kind in ("confirm", "verify"):
            # Plans made during the run are not in the world file, so ask the server which plans the group has now.
            plan_ids = {p["id"] for p in http.get(f"{BASE}/api/inspector", params={"scenario": group}).json().get("plans", [])}
            who = step.get("members") or [step["member"]]
            for m in who:
                page = Page(http, m, passcodes[m])
                page.login()
                inbox = page.inbox()
                if kind == "confirm":
                    items = [x for x in inbox["pending"] if x["plan_id"] in plan_ids]
                    for item in items:
                        ch = page.post("/human/challenges", {"scope_id": item["scope_id"]})
                        page.post("/human/decisions", {"challenge_id": ch["challenge_id"], "nonce": ch["nonce"], "decision": step.get("decision", "approved")})
                    print(f"{i:>2} {names[m]}'s screen: answered {len(items)} item(s)")
                    if not items:
                        ok = False
                        print("      nothing was waiting for this person")
                else:
                    drafts = [c for c in inbox["conditions"] if c["status"] == "unverified"]
                    for c in drafts:
                        page.post("/human/conditions/verify", {"condition_id": c["id"], "verify_digest": c["verify_digest"]})
                    print(f"{i:>2} {names[m]}'s screen: confirmed {len(drafts)} drafted condition(s)")
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
    ap.add_argument("groups", nargs="*", help="groups to play (default: every group with a guided script)")
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
        chosen = [s for s in config["scenarios"] if s.get("script") and (not args.groups or s["id"] in args.groups)]
        results = {s["id"]: play(http, s, args.first, args.last) for s in chosen}
    print("\n" + ", ".join(f"{g}: {'ok' if ok else 'CHECK'}" for g, ok in results.items()))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
