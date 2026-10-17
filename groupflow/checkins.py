"""Automatic check-ins on quiet plans, run by the server's worker.

The server finds plans that have gone quiet while something waits (engine/momentum.py). Who reviews them is set by GROUPFLOW_CHECKINS:

  rules  (default) the server's own judgement: remind the people a real blocker waits on, list loose ends for the organizer.
  model  a language model reads the same facts, reasons about whether the quiet is a problem (a plan waiting a week for a date is fine;
         a party in two days with no invitations sent is not), and words each message. It can only choose among the people the server
         listed and say less: apply_review checks every recipient and every word, and any failure falls back to the rules.
  off    no automatic check-ins. Reminders people set still go out.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger("groupflow.checkins")

REVIEW_PROMPT = """You review a group plan that has gone quiet, for a planning service used through Alexa. Decide whether the quiet is a
problem and, if it is, who to remind and how. Think it through privately first; answer with one JSON object only.

The facts below come from the plan itself. "blockers" are things the plan cannot move without (answers, a decision, the organizer's next
step, an overdue task). "loose_ends" are tasks nobody has started with the day close; some are meant for the day itself (driving someone
there, picking up a cake), so judge each one. "candidates" are the only people you may remind, each with the server's own draft.

Reason about: how long it has been quiet compared with how close the day is; whether anyone could be avoiding a hard conversation (a
pending decision, a vote nobody wants to cast) and would find a low-pressure message easier to answer; whether waiting is simply normal
here (nothing is due soon). Remind only people whose answer or action would actually move the plan.

Messages: one per person, at most 300 characters, warm and specific, no guilt, and make it easy to say no or hand off ("a no helps as
much as a yes"). Say what to do and where (confirmations happen on their own GroupFlow page; decisions and updates are told to Alexa). Use
only facts given here: never guess reasons, never mention anyone's private situation, never name people the recipient was not listed with.

Answer exactly:
{"stuck": true or false, "reason": "one sentence on why", "nudges": [{"to": "<candidate id>", "text": "..."}],
 "organizer_summary": "one or two sentences for the organizer (who was reminded is added for you), or empty"}"""


class RulesReviewer:
    source = "rules"
    label = "server rules"

    async def review(self, report: dict[str, Any]) -> dict[str, Any] | None:
        return None  # apply_review's own drafts


class ModelReviewer:
    """A language model as reviewer. Any failure (timeout, refusal, invalid JSON) returns None, and the rules decide."""

    source = "model"

    def __init__(self, provider: Any, timeout_s: float = 45.0):
        self.provider = provider
        self.timeout_s = timeout_s
        self.label = getattr(provider, "label", "model")

    @staticmethod
    def facts(report: dict[str, Any]) -> dict[str, Any]:
        candidates: dict[str, dict[str, Any]] = {}
        for d in report["drafts"] + report.get("optional", []):
            c = candidates.setdefault(d["to"], {"id": d["to"], "name": d["name"], "about": [], "server_draft": d["text"]})
            c["about"].append(d["kind"])
        return {
            "plan": report["plan"], "status": report["status_words"], "organizer": report["organizer_name"], "quiet_for": report["quiet_for"],
            "day": report["event"], "day_in": report["event_in"], "blockers": [
                {"kind": b["kind"], "what": b["what"], "waiting_on": b["waiting_on_names"], "for": b["since_text"], "next_step": b["next_step"]}
                for b in report["blockers"]],
            "loose_ends": [{"what": b["what"], "owner": b["waiting_on_names"]} for b in report.get("loose_ends", [])],
            "already_reminded_twice": [f"{e['name']} ({e['what']})" for e in report["escalate"]],
            "recent_history": report["history"], "candidates": list(candidates.values()),
        }

    async def review(self, report: dict[str, Any]) -> dict[str, Any] | None:
        from groupflow.sim.llm.types import LlmRequest, UserMessage

        req = LlmRequest(system=REVIEW_PROMPT, messages=[UserMessage(content=json.dumps(self.facts(report), ensure_ascii=False))], tools=[],
                         max_tokens=900)
        try:
            res = await asyncio.wait_for(self.provider.complete(req), timeout=self.timeout_s)
        except Exception as err:  # noqa: BLE001 - a reviewer failure must never stop the check-in
            log.warning("check-in reviewer failed for %s, using the rules: %s", report["plan_id"], err)
            return None
        return parse_decision(res.text)


def parse_decision(text: str) -> dict[str, Any] | None:
    """The model's JSON answer, tolerating a code fence or words around it. None if there is no usable object."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        out = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(out, dict) or not isinstance(out.get("stuck"), bool):
        return None
    if out["stuck"] and not isinstance(out.get("nudges"), list):
        out["nudges"] = None  # stuck, but no usable choice of people: the rules choose
    return out


class CheckIns:
    def __init__(self, engine: Any, reviewer: Any):
        self.engine = engine
        self.reviewer = reviewer

    async def run_pass(self, as_of: str | None = None, quiet_hours: bool = True, group_id: str | None = None) -> list[dict[str, Any]]:
        """Review every plan due for a check-in (or those of one group) and send the rounds. Returns what was sent, for logs and the
        demo console."""
        due = await asyncio.to_thread(self.engine.plans_due_for_review, as_of, quiet_hours)
        out = []
        for pid in due:
            if group_id and (self.engine._plan_row(pid) or {}).get("group_id") != group_id:
                continue
            try:
                report = await asyncio.to_thread(self.engine.progress_report, pid, as_of)
                decision = await self.reviewer.review(report)
                if decision is not None and decision.get("nudges") is None and decision.get("stuck") is not False:
                    decision = None
                source = self.reviewer.source if decision is not None else "rules"
                result = await asyncio.to_thread(self.engine.apply_review, pid, report, decision, source)
            except Exception:  # noqa: BLE001 - one plan's failure must not stop the others' check-ins
                log.exception("check-in on %s failed", pid)
                continue
            out.append({"plan": report["plan"], "plan_id": pid, "quiet_for": report["quiet_for"], "reviewer": source,
                        "stuck": decision.get("stuck") if decision else True, "reason": (decision or {}).get("reason"), **result})
            log.info("check-in on %s (%s): %d message(s)", report["plan"], source, len(result.get("sent", [])))
        return out


def build_checkins(engine: Any, mode: str) -> CheckIns | None:
    mode = (mode or "rules").lower()
    if mode == "off":
        return None
    if mode == "model":
        from groupflow.sim.llm.factory import MissingProvider, create_provider

        provider = create_provider()
        if isinstance(provider, MissingProvider):
            log.warning("GROUPFLOW_CHECKINS=model but no model is configured (%s); check-ins use the server rules.", provider.reason)
            return CheckIns(engine, RulesReviewer())
        return CheckIns(engine, ModelReviewer(provider))
    return CheckIns(engine, RulesReviewer())


def hours_ahead(engine: Any, hours: float) -> str:
    """The engine's clock moved forward, for the demo console's "skip ahead"."""
    now = datetime.fromisoformat(engine.ts().replace("Z", "+00:00"))
    return (now + timedelta(hours=hours)).astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
