"""Read voice-profile tool calls as the lab operations they performed, so one set of scenario expectations scores both surfaces.

A scenario says "tools_called: [propose_change]" or checks an rsvp outcome; on the voice profile the model called change_plan or
my_part. The mapping uses the call's own arguments and result (receipts name the engine operation), never the model's wording.
"""
from __future__ import annotations

from typing import Any

from groupflow.mcp_server.voice import VOICE_NAMES


def _ops(name: str, args: dict[str, Any], result: dict[str, Any]) -> list[tuple[str, Any]]:
    """(lab tool, its result) pairs for one voice call."""
    if name == "catch_up":
        return [("get_context", result)] + ([("review_plan", result)] if args.get("plan") else [])
    if name == "ask_about":
        kind = args.get("kind")
        return [({"find": "search_plan", "why": "explain", "who": "get_history"}.get(kind, "search_plan"), result)]
    if name == "start_plan":
        return [("create_plan", result)]
    if name == "change_plan":
        if args.get("request") or args.get("decision"):
            return [("decide_change", result)]
        if args.get("depends_on"):
            return [("link_items", result)]
        return [("propose_change", result)]
    if name == "my_part":
        out: list[tuple[str, Any]] = []
        if args.get("coming") or args.get("plus_ones") is not None:
            out.append(("rsvp", result.get("rsvp", result)))
        for key in ("bring_list_bring", "bring_list_unclaim", "bring_list_add"):
            if key in result or args.get({"bring_list_bring": "bring", "bring_list_unclaim": "drop", "bring_list_add": "add_needed"}[key]):
                out.append(("bring_list", result.get(key, result)))
        if args.get("limits"):
            out.append(("set_my_conditions", result.get("limits", result)))
        return out or [("my_part", result)]
    if name == "find_options":
        out = []
        if args.get("report"):
            out.append(("report_observation", result.get("report", result)))
        if args.get("choose"):
            out.append(("planning_options", result))
        elif args.get("brief") or args.get("candidates"):
            out.append(("planning_options", result))
        elif result.get("alternatives") is not None or result.get("job") or not args.get("report"):
            out.append(("find_alternatives", result))
        return out
    if name == "tell_people":
        return [("set_reminder" if args.get("when") or args.get("cancel") else "notify_people", result)]
    if name == "invite_people":
        return ([("create_group", result.get("group", result))] if args.get("new_group") else []) + [("invite_to_group", result)]
    simple = {"send_for_confirmation": "request_confirmations", "lock_in": "commit_plan", "ask_group": "poll", "split_costs": "costs",
              "answer": "answer_followup"}
    return [(simple.get(name, name), result)]


def lab_view(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each voice call replaced by one entry per lab operation it performed (same ok flag; the matching part of the result)."""
    out = []
    for c in calls:
        if c.get("name") not in VOICE_NAMES:
            out.append(c)
            continue
        args = c.get("args") or {}
        result = c.get("result") if isinstance(c.get("result"), dict) else {}
        if not c.get("ok"):
            for lab, _ in _ops(c["name"], args, {}) or [(c["name"], None)]:
                out.append(dict(c, name=lab, voice=c["name"]))
            continue
        for lab, part in _ops(c["name"], args, result):
            out.append(dict(c, name=lab, voice=c["name"], result=part if isinstance(part, dict) else result))
    return out
