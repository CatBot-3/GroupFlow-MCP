"""Polls for open choices, and decide-by deadlines with a default.

A poll asks everyone who can see a plan to choose among a few options ("which Saturday?", "Thai or pizza?"). A vote is a preference,
never consent: it changes nothing by itself, and the person who decides the question anyway still decides it. Who that is follows the
plan's own rules: the owner of the detail the poll is about, otherwise the plan's organizer. Votes are open to everyone in the plan.

A decide-by deadline lets silence become an answer that everyone was told about in advance: "if nobody objects by Thursday we go with
Rosa's" (an objection poll), or "vote by Friday, otherwise the leading option" (a choice poll with a fallback). Only the person who could
decide the question anyway may set one, and never for an activity: an activity counts only when each person taking part confirms it on
their own page, so neither a vote nor silence can settle it. When the deadline passes the server applies the result, or, if the default
did not hold (an objection, a tie), asks the decider, and tells everyone what happened either way.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import timedelta
from typing import Any

from groupflow.core.errors import DomainError

from .core import ASSISTANT, Row, Scope, _names
from .momentum import _dt, _iso, _local_text, resolve_when
from .notices import _or_names

log = logging.getLogger("groupflow.polls")

OPTIONS_MIN, OPTIONS_MAX = 2, 8
OPTION_MAX = 80
QUESTION_MAX = 160
REASON_MAX = 200
OPEN_POLLS_MAX = 5  # per plan
CLOSE_MAX = timedelta(days=60)
RECENT = timedelta(days=3)  # decided polls stay in the plan view this long
ABSTAIN = {"none", "no preference", "either", "either one", "any", "anything", "dont mind", "don't mind", "no opinion", "whatever", "pass"}
OBJECT = {"object", "no", "nope", "nah", "against", "i object", "not ok", "not okay", "dont", "don't", "disagree", "veto"}
AGREE = {"fine", "ok", "okay", "yes", "yeah", "sure", "agree", "sounds good", "works", "good", "go for it"}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s:'’-]", " ", (text or "").lower().replace("’", "'"))).strip()


class Polls:
    """Mixed into GroupFlow above Momentum: reuses its time words, worker hook and check-in blockers."""

    # ================================================================ rows and views

    def _poll_options(self, row: Row) -> list[str]:
        return json.loads(row["options_json"])

    def _votes(self, row: Row) -> list[Row]:
        return self.db.all("SELECT * FROM poll_votes WHERE poll_id = ? ORDER BY updated_at, member_id", row["id"])

    def _tally(self, row: Row) -> dict[str, list[str]]:
        """Option -> voters, in option order; for an objection poll the keys are 'object' and 'fine'."""
        audience = set(self.plan_audience(self._plan_row(row["plan_id"])))
        keys = ["object", "fine"] if row["kind"] == "objection" else self._poll_options(row)
        out: dict[str, list[str]] = {k: [] for k in keys}
        for v in self._votes(row):
            if v["member_id"] in audience and v["choice"] in out:
                out[v["choice"]].append(v["member_id"])
        return out

    def _leader(self, row: Row) -> str | None:
        counts = {k: len(v) for k, v in self._tally(row).items()}
        best = max(counts.values(), default=0)
        leaders = [k for k, n in counts.items() if n == best]
        return leaders[0] if best and len(leaders) == 1 else None

    def _tally_text(self, row: Row) -> str:
        if row["kind"] == "objection":
            objectors = self._tally(row)["object"]
            return f"objections from {_names([self.name(m) for m in objectors])}" if objectors else "no objections"
        parts = [f"{opt} {len(v)}" for opt, v in self._tally(row).items()]
        return ", ".join(parts)

    def _poll_view(self, row: Row, viewer: str) -> dict[str, Any]:
        p = self._plan_row(row["plan_id"])
        tally = self._tally(row)
        mine = self.db.get("SELECT * FROM poll_votes WHERE poll_id = ? AND member_id = ?", row["id"], viewer)
        audience = self.plan_audience(p)
        voted = {v["member_id"] for v in self._votes(row)}
        view: dict[str, Any] = {
            "id": row["id"], "kind": "unless someone objects" if row["kind"] == "objection" else "vote", "question": row["question"],
            "status": row["status"], "decides": "you" if row["decider_id"] == viewer else self.name(row["decider_id"]),
            "votes": {k: [("you" if m == viewer else self.name(m)) for m in v] for k, v in tally.items()},
            "not_voted": [self.name(m) for m in audience if m not in voted and m != row["decider_id"]] if row["kind"] == "choice" else [],
        }
        if row["kind"] == "choice":
            view["options"] = self._poll_options(row)
        else:
            view["proposal"] = self._poll_options(row)[0]
        if row["item_id"] and (it := self._item(row["item_id"])):
            view["about"] = it["label"]
        if row["closes_at"]:
            view["closes"] = _local_text(self._local(p, row["closes_at"]))
        if row["fallback"]:
            view["if_undecided"] = "the leading option" if row["fallback"] == "leading" else row["fallback"]
        if mine:
            view["your_vote"] = "no preference" if mine["choice"] == "" else mine["choice"]
        if row["status"] in ("decided", "closed", "cancelled") and row["result"]:
            view["result"] = row["result"]
        return view

    def _polls_in_view(self, plan: Row, viewer: str) -> list[dict[str, Any]]:
        since = _iso(_dt(self.ts()) - RECENT)
        rows = self.db.all("SELECT * FROM polls WHERE plan_id = ? AND (status IN ('open', 'closed') OR (status = 'decided' AND closed_at >= ?)) "
                           "ORDER BY created_at, rowid", plan["id"], since)
        return [self._poll_view(r, viewer) for r in rows]

    def _resolve_poll(self, scope: Scope, ref: str | None, plan: Row | None, statuses: tuple[str, ...] = ("open",), choice: str | None = None) -> Row:
        marks = ",".join("?" for _ in statuses)
        rows = [r for r in self.db.all(f"SELECT * FROM polls WHERE status IN ({marks}) ORDER BY created_at DESC, rowid DESC", *statuses)
                if scope.sees_plan(r["plan_id"]) and (plan is None or r["plan_id"] == plan["id"])]
        if ref and ref.strip():
            exact = [r for r in rows if r["id"] == ref.strip()]
            if exact:
                return exact[0]
            words = set(_norm(ref).split()) - {"the", "a", "poll", "vote", "on", "about", "for"}
            scored = [(len(words & set(_norm(f"{r['question']} {' '.join(self._poll_options(r))}").split())), r) for r in rows]
            best = max((s for s, _ in scored), default=0)
            hits = [r for s, r in scored if s == best and best > 0]
            if len(hits) == 1:
                return hits[0]
            rows = hits or rows
        if len(rows) > 1 and choice:
            # "I vote Thai" with two votes open: the one that has Thai as an option.
            said = _norm(choice)
            fits = [r for r in rows if (r["kind"] == "objection" and (said in OBJECT or said in AGREE)) or
                    (r["kind"] == "choice" and self._match_option([{"label": o} for o in self._poll_options(r)], choice) is not None)]  # type: ignore[attr-defined]
            rows = fits or rows
        if len(rows) == 1:
            return rows[0]
        if not rows:
            raise DomainError("NOT_FOUND", "There is no open vote like that in your plans." if "open" in statuses else "No such vote.")
        listing = "; ".join(f'"{r["question"]}" (id {r["id"]}, {scope.plans[r["plan_id"]]["title"]})' for r in rows[:5])
        raise DomainError("AMBIGUOUS_POLL", f"Which vote? {listing}. Pass poll.")

    def _decider(self, plan: Row, item: Row | None, creator: str) -> str:
        """Who could decide this anyway: anyone for an open detail (so whoever asked), the owner of any other detail, else the organizer."""
        if item is not None:
            return creator if item["policy"] == "open" else item["owner_id"]
        return plan["organizer_id"]

    # ================================================================ start

    def start_poll(self, actor_id: str, plan: str | None = None, question: str | None = None, options: list[str] | None = None,
                   item: str | None = None, closes: str | None = None, fallback: str | None = None, proposal: str | None = None) -> dict[str, Any]:
        self._rate_limit(actor_id)
        try:
            return self._start_poll(actor_id, plan, question, options, item, closes, fallback, proposal)
        except DomainError as err:
            if ASSISTANT.get():
                args = {"action": "start", "plan": plan, "question": question, "options": options, "item": item, "closes": closes,
                        "fallback": fallback, "proposal": proposal}
                if err.code == "AMBIGUOUS_PLAN":
                    self._clarify_plan(actor_id, err, "poll", args)  # type: ignore[attr-defined]
                elif err.code == "WHEN_UNCLEAR" and err.details.get("plan"):
                    self._clarify_close(actor_id, err, args)
            raise

    def _clarify_close(self, actor_id: str, err: DomainError, args: dict[str, Any]) -> None:
        """A vague closing time ("soon", "in a bit"): keep "until when?" with concrete times, each the same poll with that deadline."""
        p = self._plan_row(err.details["plan"])
        if p is None:
            return
        now = self._local(p)
        event = self._event_time(p, [])
        choices = [("Tomorrow evening at 6", "tomorrow at 6pm"), ("In two days", "in 2 days")]
        if event is not None and event - now > timedelta(days=2):
            choices.append(("The day before at 9am", "1 day before"))
        kept = {k: v for k, v in dict(args, plan=p["id"]).items() if v not in (None, [], "") and k != "closes"}
        opts = [{"label": label, "action": {"tool": "poll", "arguments": dict(kept, closes=when)}} for label, when in choices]
        question = "Until when should people have to answer: " + _or_names([c[0][0].lower() + c[0][1:] for c in choices]) + "?"
        try:
            fid = self.db.tx(lambda: self._store_followup(actor_id, "clarify", question, opts, p, "suggested"))  # type: ignore[attr-defined]
        except DomainError:
            return
        err.details["followup"] = {"id": fid, "question": question, "options": [o["label"] for o in opts]}

    def _start_poll(self, actor_id: str, plan: str | None, question: str | None, options: list[str] | None, item: str | None,
                    closes: str | None, fallback: str | None, proposal: str | None) -> dict[str, Any]:
        objection = bool(proposal and proposal.strip())
        opts = [re.sub(r"\s+", " ", str(o)).strip() for o in (options or []) if str(o).strip()]
        if objection:
            opts = [re.sub(r"\s+", " ", proposal or "").strip()]
        elif not OPTIONS_MIN <= len(opts) <= OPTIONS_MAX:
            raise DomainError("INVALID_ARGUMENT", f"A vote needs {OPTIONS_MIN} to {OPTIONS_MAX} options. For one proposal, pass proposal instead "
                                                  "(it is settled unless someone objects).")
        if any(len(o) > OPTION_MAX for o in opts):
            raise DomainError("INVALID_ARGUMENT", f"Keep each option under {OPTION_MAX} characters.")
        if len({_norm(o) for o in opts}) != len(opts):
            raise DomainError("INVALID_ARGUMENT", "Two options are the same.")

        def run() -> dict[str, Any]:
            scope = self.scope_for(actor_id)
            p = self.resolve_plan(scope, plan) if plan else None
            it = self.resolve_item(scope, item, p["id"] if p else None) if item else None
            if p is None:
                p = scope.plans[it["plan_id"]] if it else self.resolve_plan(scope, None, purpose="a vote could be about")
            if it is not None and it["plan_id"] != p["id"]:
                raise DomainError("INVALID_ARGUMENT", f"{it['label']} is not in {p['title']}.")
            if it is not None and it["shape"] != "detail":
                if it["shape"] == "activity":
                    raise DomainError("CONSENT_REQUIRED", f"{it['label']} is an activity: it counts only when each person taking part confirms it on "
                                      "their own page, so neither a vote nor silence can settle it. Start the vote without item; the organizer then "
                                      "changes the plan and sends it out for confirmation.")
                raise DomainError("INVALID_ARGUMENT", "A vote can be about a detail (one agreed value), or about nothing in particular.")
            decider = self._decider(p, it, actor_id)
            if (objection or fallback) and decider != actor_id:
                whose = f"{self.name(decider)} decides {it['label']}" if it is not None else f"{self.name(decider)} organizes {p['title']}"
                raise DomainError("FORBIDDEN", f"{whose}, so only {self.name(decider)} can let a deadline or silence settle it. Start a plain vote "
                                  "instead (votes are suggestions), or suggest it to them.")
            if (self.db.scalar("SELECT COUNT(*) FROM polls WHERE plan_id = ? AND status = 'open'", p["id"]) or 0) >= OPEN_POLLS_MAX:
                raise DomainError("RATE_LIMITED", f"{p['title']} already has {OPEN_POLLS_MAX} open votes. Decide or cancel one first.")
            if it is not None and self.db.get("SELECT 1 AS x FROM polls WHERE item_id = ? AND status IN ('open', 'closed')", it["id"]):
                raise DomainError("ALREADY_OPEN", f"There is already an open vote about {it['label']}.")
            fb: str | None = None
            if fallback and not objection:
                f = _norm(fallback)
                if f in ("leading", "the leading option", "most votes", "the most votes", "majority", "whatever wins", "top"):
                    fb = "leading"
                else:
                    hit = self._match_option([{"label": o} for o in opts], fallback)  # type: ignore[attr-defined]
                    if hit is None:
                        raise DomainError("INVALID_ARGUMENT", 'fallback is "leading" or one of the options.')
                    fb = hit["label"]
            due = None
            if closes and closes.strip():
                now = self._local(p)
                due, why = resolve_when(closes, now, self._event_time(p, [it] if it else []))
                if due is None:
                    raise DomainError("WHEN_UNCLEAR", "Say exactly until when (\"Thursday 6pm\", \"in 2 days\", \"the day before\"). Ask rather than guess.",
                                      {"said": closes, "plan": p["id"]})
                if due <= now + timedelta(minutes=1):
                    raise DomainError("INVALID_ARGUMENT", f"That is {_local_text(due)}, which has already passed.")
                if due > now + CLOSE_MAX:
                    raise DomainError("INVALID_ARGUMENT", "A vote can stay open for at most 60 days.")
            elif objection or fb:
                raise DomainError("WHEN_UNCLEAR", "Silence can only settle it by a deadline everyone is told about. Say until when.",
                                  {"said": "", "plan": p["id"]})
            q = re.sub(r"\s+", " ", (question or "").strip())
            if objection:
                subject = it["label"] if it is not None else (q or "The plan")
                q = f"{subject}: {opts[0]}, unless someone objects"
            if not q:
                raise DomainError("INVALID_ARGUMENT", "Say what the vote is about.")
            if len(q) > QUESTION_MAX:
                raise DomainError("INVALID_ARGUMENT", f"Keep the question under {QUESTION_MAX} characters.")
            audience = set(self.plan_audience(p))
            for text in [q, *opts]:
                if (leak := self._note_leak(actor_id, text, audience - {actor_id})):  # type: ignore[attr-defined]
                    raise DomainError("PRIVATE_NOTE", f"That mentions something {self.name(leak)} cannot see, and {self.name(leak)} can see "
                                      f"{p['title']}. Start the vote on the plan it is about.")
            pid = self.new_id("pl")
            self.db.run("INSERT INTO polls (id, plan_id, item_id, base_fact_id, kind, question, options_json, created_by, decider_id, closes_at, "
                        "closes_text, fallback, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)",
                        pid, p["id"], it["id"] if it else None, it["current_fact_id"] if it else None, "objection" if objection else "choice", q,
                        json.dumps(opts), actor_id, decider, _iso(due) if due else None, (closes or "").strip()[:120] or None, fb, self.ts())
            row = self.db.get("SELECT * FROM polls WHERE id = ?", pid)
            by = self.name(actor_id)
            until = f" by {_local_text(due)}" if due else ""
            if objection:
                text = (f"{by}: unless someone objects{until}, {it['label'] if it else p['title']} will be {opts[0]}. If you object, tell Alexa "
                        "(or say so on your GroupFlow page); if it is fine, there is nothing to do.")
                detail = f"{by} proposed {opts[0]} for {it['label'] if it else p['title']}, settled{until} unless someone objects."
            else:
                then = ""
                if fb == "leading":
                    then = " If it is still open then, the leading option wins."
                elif fb:
                    then = f" If there is no clear winner then, it is {fb}."
                elif due:
                    then = f" {self.name(decider)} decides once the votes are in."
                text = f"{by} started a vote on {p['title']}: {q} Options: {', '.join(opts)}.{' Votes close' + until + '.' if due else ''}{then} Tell Alexa your pick."
                detail = f"{by} started a vote: {q} ({', '.join(opts)})."
            self._notify([m for m in audience if m != actor_id], p, "poll", text)
            self._audit(actor_id, "poll.started", "plan", p["id"], p, detail)
            view = self._poll_view(row, actor_id)
            msg = (f"Done: {opts[0]} for {it['label'] if it else p['title']} unless someone objects{until}. Everyone in {p['title']} was told."
                   if objection else f"The vote is open and everyone in {p['title']} was told.{until and ' It closes' + until + '.'}")
            return {"outcome": "started", "poll": view, "message": msg}
        return self.db.tx(run)

    # ================================================================ vote

    def vote(self, actor_id: str, choice: str, poll: str | None = None, plan: str | None = None, reason: str | None = None) -> dict[str, Any]:
        self._rate_limit(actor_id)
        reason = re.sub(r"\s+", " ", (reason or "").strip())[:REASON_MAX] or None

        def run() -> dict[str, Any]:
            scope = self.scope_for(actor_id)
            p = self.resolve_plan(scope, plan) if plan else None
            row = self._resolve_poll(scope, poll, p, ("open",), choice)
            pl = self._plan_row(row["plan_id"])
            said = _norm(choice)
            if row["kind"] == "objection":
                if said in OBJECT or said.startswith(("i object", "object")):
                    picked = "object"
                elif said in AGREE:
                    picked = "fine"
                else:
                    raise DomainError("INVALID_ARGUMENT", f'For "{row["question"]}" say "object" (with a reason if they gave one) or "fine".')
            elif said in ABSTAIN:
                picked = ""
            else:
                hit = self._match_option([{"label": o} for o in self._poll_options(row)], choice)  # type: ignore[attr-defined]
                if hit is None:
                    raise DomainError("INVALID_ARGUMENT", f"Pick one of: {', '.join(self._poll_options(row))} (or no preference).")
                picked = hit["label"]
            before = self.db.get("SELECT * FROM poll_votes WHERE poll_id = ? AND member_id = ?", row["id"], actor_id)
            self.db.run("INSERT INTO poll_votes (poll_id, member_id, choice, reason, updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(poll_id, member_id) "
                        "DO UPDATE SET choice = excluded.choice, reason = excluded.reason, updated_at = excluded.updated_at",
                        row["id"], actor_id, picked, reason, self.ts())
            me = self.name(actor_id)
            shown = {"": "no preference", "object": "an objection", "fine": "fine"}.get(picked, picked)
            self._audit(actor_id, "poll.voted", "plan", pl["id"], pl, f'{me} answered "{row["question"]}": {shown}.')
            decider = row["decider_id"]
            if row["kind"] == "objection" and picked == "object" and (before is None or before["choice"] != "object") and decider != actor_id:
                because = f': "{reason}"' if reason else ""
                self._notify([decider], pl, "poll", f"{me} objects to {row['question'].split(', unless')[0]}{because}. It will not be applied by "
                             "itself; you decide what happens.")
            if row["kind"] == "choice" and decider != actor_id:
                voted = {v["member_id"] for v in self._votes(row)}
                waiting = [m for m in self.plan_audience(pl) if m not in voted and m != decider]
                if not waiting and (before is None):
                    self._notify([decider], pl, "poll", f'Everyone has answered "{row["question"]}": {self._tally_text(row)}. You can decide it now.')
            msg = {"object": "Your objection is recorded; it stops the default, and " + ("you decide." if decider == actor_id else f"{self.name(decider)} decides."),
                   "fine": "Noted as fine.", "": "Noted: no preference."}.get(picked, f"Your vote for {picked} is in. Votes so far: {self._tally_text(row)}.")
            if before is not None and before["choice"] != picked:
                msg = "Changed. " + msg
            return {"outcome": "voted", "poll": self._poll_view(row, actor_id), "message": msg}
        return self.db.tx(run)

    # ================================================================ decide and cancel

    def _apply_choice(self, row: Row, option: str) -> str:
        """Write the decided option into the detail the poll is about, as its decider. Returns a short note for the result."""
        if not row["item_id"]:
            return ""
        it = self._item(row["item_id"])
        if it is None or it["archived"]:
            return " (the detail it was about is gone, so nothing was written)"
        scope = self.scope_for(row["decider_id"])
        res = self._change_detail(scope, row["decider_id"], it, option, "committed", None, False)  # type: ignore[attr-defined]
        return "" if res.outcome in ("confirmed", "updated", "corroborated") else f" ({res.message})"

    def _finish(self, row: Row, option: str | None, how: str, by: str | None, result: str, status: str = "decided") -> None:
        self.db.run("UPDATE polls SET status = ?, decided_option = ?, decided_by = ?, decided_how = ?, result = ?, closed_at = ? WHERE id = ?",
                    status, option, by, how, result, self.ts(), row["id"])

    def decide_poll(self, actor_id: str, choice: str, poll: str | None = None, plan: str | None = None) -> dict[str, Any]:
        self._rate_limit(actor_id)

        def run() -> dict[str, Any]:
            scope = self.scope_for(actor_id)
            p = self.resolve_plan(scope, plan) if plan else None
            row = self._resolve_poll(scope, poll, p, ("open", "closed"), choice)
            pl = self._plan_row(row["plan_id"])
            if row["decider_id"] != actor_id:
                raise DomainError("FORBIDDEN", f'{self.name(row["decider_id"])} decides "{row["question"]}"; votes are suggestions to them.')
            options = self._poll_options(row)
            if row["kind"] == "objection":
                c = _norm(choice)
                if c in AGREE or c in ("apply", "go ahead", "keep it", "do it"):
                    option = options[0]
                elif c in OBJECT or c in ("drop", "drop it", "cancel", "forget it"):
                    option = None
                else:
                    hit = self._match_option([{"label": o} for o in options], choice)  # type: ignore[attr-defined]
                    option = hit["label"] if hit else re.sub(r"\s+", " ", choice).strip()[:OPTION_MAX] or None
            else:
                hit = self._match_option([{"label": o} for o in options], choice)  # type: ignore[attr-defined]
                if hit is None:
                    raise DomainError("INVALID_ARGUMENT", f"Decide one of: {', '.join(options)}.")
                option = hit["label"]
            tally = self._tally_text(row)
            if option is None:
                self._finish(row, None, "dropped", actor_id, f"{self.name(actor_id)} dropped it ({tally}).", "cancelled")
                text = f'{self.name(actor_id)} dropped "{row["question"]}" ({tally}). Nothing changed.'
            else:
                note = self._apply_choice(row, option)
                self._finish(row, option, "decider", actor_id, f"{self.name(actor_id)} decided {option} ({tally}).{note}")
                text = f'{self.name(actor_id)} decided "{row["question"].split(", unless")[0]}": {option} ({tally}).{note}'
            self._notify([m for m in self.plan_audience(pl) if m != actor_id], pl, "poll", text)
            self._audit(actor_id, "poll.decided", "plan", pl["id"], pl, text)
            fresh = self.db.get("SELECT * FROM polls WHERE id = ?", row["id"])
            return {"outcome": "decided" if option else "dropped", "poll": self._poll_view(fresh, actor_id),
                    "message": f"Decided: {option}. Everyone in {pl['title']} was told." if option else "Dropped; nothing changed, and everyone was told."}
        return self.db.tx(run)

    def cancel_poll(self, actor_id: str, poll: str | None = None, plan: str | None = None) -> dict[str, Any]:
        self._rate_limit(actor_id)

        def run() -> dict[str, Any]:
            scope = self.scope_for(actor_id)
            p = self.resolve_plan(scope, plan) if plan else None
            row = self._resolve_poll(scope, poll, p, ("open", "closed"))
            pl = self._plan_row(row["plan_id"])
            if actor_id not in (row["created_by"], row["decider_id"]) and not self.is_organizer(pl, actor_id):
                raise DomainError("FORBIDDEN", f"Only {self.name(row['created_by'])}, who started it, or {self.name(row['decider_id'])} can cancel it.")
            self._finish(row, None, "cancelled", actor_id, f"Cancelled by {self.name(actor_id)}.", "cancelled")
            self._notify([m for m in self.plan_audience(pl) if m != actor_id], pl, "poll", f'{self.name(actor_id)} cancelled the vote "{row["question"]}".')
            self._audit(actor_id, "poll.cancelled", "plan", pl["id"], pl, f'{self.name(actor_id)} cancelled the vote "{row["question"]}".')
            return {"outcome": "cancelled", "message": "Cancelled, and everyone was told. Nothing changed."}
        return self.db.tx(run)

    def poll(self, actor_id: str, action: str, plan: str | None = None, poll: str | None = None, question: str | None = None,
             options: list[str] | None = None, item: str | None = None, closes: str | None = None, fallback: str | None = None,
             proposal: str | None = None, choice: str | None = None, reason: str | None = None, quote: str | None = None) -> dict[str, Any]:
        """One entry point for the MCP tool."""
        from .intent import reaction_without_request
        if reaction_without_request(quote):
            raise DomainError("NO_ACTION_REQUESTED", "A reaction does not start or answer a vote. Leave it unchanged.")
        if action == "start":
            if proposal is None and options and len(options) == 1:
                proposal, options = options[0], None  # one option: settled unless someone objects
            return self.start_poll(actor_id, plan, question, options, item, closes, fallback, proposal)
        poll = poll or question  # after start, the question's words (or its id) name the vote
        if action in ("vote", "object"):
            return self.vote(actor_id, choice or ("object" if action == "object" else ""), poll=poll, plan=plan, reason=reason)
        if action == "decide":
            if not choice:
                raise DomainError("INVALID_ARGUMENT", "Say which option they decided.")
            return self.decide_poll(actor_id, choice, poll=poll, plan=plan)
        if action == "cancel":
            return self.cancel_poll(actor_id, poll=poll, plan=plan)
        raise DomainError("INVALID_ARGUMENT", 'action is "start", "vote", "decide" or "cancel".')

    # ================================================================ deadlines (called by the worker)

    def close_due_polls(self, as_of: str | None = None) -> int:
        now = as_of or self.ts()
        done = 0
        for row in self.db.all("SELECT * FROM polls WHERE status = 'open' AND closes_at IS NOT NULL AND closes_at <= ? ORDER BY closes_at LIMIT 50", now):
            try:
                done += self.db.tx(lambda r=row: self._close_poll(r))
            except Exception as err:  # noqa: BLE001 - a broken poll is closed for its decider, not retried every second
                log.exception("closing poll %s failed", row["id"])
                self.db.run("UPDATE polls SET status = 'closed', result = ?, closed_at = ? WHERE id = ?", f"could not close: {type(err).__name__}",
                            self.ts(), row["id"])
        return done

    def _close_poll(self, due: Row) -> int:
        row = self.db.get("SELECT * FROM polls WHERE id = ? AND status = 'open'", due["id"])
        if row is None:
            return 0
        p = self._plan_row(row["plan_id"])
        if p is None or p["status"] == "CANCELLED":
            self._finish(row, None, "plan_cancelled", None, "The plan was cancelled.", "cancelled")
            return 0
        audience = set(self.plan_audience(p))
        decider = row["decider_id"]
        q = row["question"].split(", unless")[0]
        tally = self._tally_text(row)
        changed = bool(row["item_id"]) and ((it := self._item(row["item_id"])) is None or it["current_fact_id"] != row["base_fact_id"])
        if decider not in audience:
            self._finish(row, None, "no_decider", None, f"Closed ({tally}); the person who decides it is no longer in the plan.", "closed")
            self._notify(sorted(audience), p, "poll", f'Voting on "{q}" closed ({tally}). Its decider left the plan, so nothing was applied.')
            return 1
        option: str | None = None
        how = ""
        if row["kind"] == "objection":
            if not self._tally(row)["object"] and not changed:
                option, how = self._poll_options(row)[0], "no_objection"
        elif row["fallback"] and not changed:
            leader = self._leader(row)
            if leader is not None:
                option, how = leader, "leading"
            elif row["fallback"] != "leading":
                option, how = row["fallback"], "fallback"
        if option is not None:
            note = self._apply_choice(row, option)
            why = {"no_objection": "nobody objected by the deadline", "leading": f"it led when votes closed ({tally})",
                   "fallback": f"there was no clear winner when votes closed ({tally}), so the agreed default applies"}[how]
            self._finish(row, option, how, decider, f"{option}: {why}.{note}")
            self._notify(sorted(audience), p, "poll", f'"{q}" is settled: {option}, because {why}.{note}')
            self._audit(None, "poll.closed", "plan", p["id"], p, f'"{q}" settled at its deadline: {option} ({how}).')
            return 1
        if row["kind"] == "objection":
            why = "the value was changed meanwhile" if changed else f"{tally}"
            result = f"Not applied: {why}."
            self._finish(row, None, "objected" if not changed else "changed", None, result, "closed")
            self._notify([decider], p, "poll", f'The deadline for "{row["question"]}" passed with {why}, so nothing was applied. You decide what happens.')
            others = sorted(audience - {decider})
            self._notify(others, p, "poll", f'"{q}" was not settled by silence ({why}); {self.name(decider)} decides.')
        else:
            why = "the value was changed meanwhile" if changed else (f"there was no clear winner ({tally})" if row["fallback"] == "leading" else f"votes are in: {tally}")
            self._finish(row, None, "closed", None, f"Voting closed; {why}.", "closed")
            self._notify([decider], p, "poll", f'Voting on "{q}" closed; {why}. You decide; tell Alexa which.')
            self._notify(sorted(audience - {decider}), p, "poll", f'Voting on "{q}" closed ({tally}); {self.name(decider)} decides.')
        self._audit(None, "poll.closed", "plan", p["id"], p, f'Voting on "{q}" closed without a result ({tally}).')
        return 1

    # ================================================================ hooks into context, to-dos and check-ins

    def _plan_view(self, scope: Scope, plan: Row) -> Any:
        view = super()._plan_view(scope, plan)  # type: ignore[misc]
        view.polls = self._polls_in_view(plan, scope.member_id)
        return view

    def _todos(self, scope: Scope, plans: list[Row]) -> list[Any]:
        from .views import TodoView

        todos = super()._todos(scope, plans)  # type: ignore[misc]
        me = scope.member_id
        for p in plans:
            for row in self.db.all("SELECT * FROM polls WHERE plan_id = ? AND status IN ('open', 'closed') ORDER BY created_at", p["id"]):
                if row["status"] == "open" and row["kind"] == "choice" and row["decider_id"] != me and \
                        not self.db.get("SELECT 1 AS x FROM poll_votes WHERE poll_id = ? AND member_id = ?", row["id"], me):
                    until = f" (closes {_local_text(self._local(p, row['closes_at']))})" if row["closes_at"] else ""
                    todos.append(TodoView(kind="vote", plan=p["title"], where="ask Alexa", text=f'Vote: {row["question"]}{until}'))
                elif row["status"] == "closed" and row["decider_id"] == me:
                    todos.append(TodoView(kind="decide", plan=p["title"], where="ask Alexa",
                                          text=f'Decide "{row["question"].split(", unless")[0]}": {self._tally_text(row)}.'))
        return todos

    def _blockers(self, plan: Row, now: Any, event: Any) -> list[dict[str, Any]]:
        out = super()._blockers(plan, now, event)  # type: ignore[misc]
        audience = set(self.plan_audience(plan))
        for row in self.db.all("SELECT * FROM polls WHERE plan_id = ? AND status IN ('open', 'closed') ORDER BY created_at", plan["id"]):
            if row["status"] == "open" and row["kind"] == "choice":
                voted = {v["member_id"] for v in self._votes(row)}
                waiting = sorted(m for m in audience if m not in voted and m not in (row["decider_id"],))
                if waiting:
                    out.append({"kind": "vote", "key": f"vote:{row['id']}", "what": f'a vote on "{row["question"]}"', "waiting_on": waiting,
                                "since": row["created_at"], "next_step": "they vote, or say they have no preference"})
            elif row["status"] == "closed" and row["decider_id"] in audience:
                out.append({"kind": "decide_poll", "key": f"poll:{row['id']}", "what": f'deciding "{row["question"].split(", unless")[0]}"',
                            "waiting_on": [row["decider_id"]], "since": row["closed_at"] or row["created_at"],
                            "next_step": "the decider tells Alexa which option"})
        return out

    def _nudge_text(self, plan: Row, b: dict[str, Any], member: str, quiet: Any) -> str:
        if b["kind"] == "vote":
            return (f"Check-in on {plan['title']}: {b['what']} is still waiting for your pick. Tell Alexa your choice, or that you have no "
                    "preference; either helps the others decide.")
        if b["kind"] == "decide_poll":
            return f"Check-in on {plan['title']}: the votes are in for {b['what'][len('deciding '):]}. It is yours to decide; tell Alexa which."
        return super()._nudge_text(plan, b, member, quiet)  # type: ignore[misc]

    def _run_followup_action(self, actor_id: str, tool: str, args: dict[str, Any], question: str, label: str) -> dict[str, Any]:
        if tool == "poll":
            keep = ("plan", "poll", "question", "options", "item", "closes", "fallback", "proposal", "choice", "reason")
            return self.poll(actor_id, str(args.get("action") or ""), **{k: args[k] for k in keep if k in args})
        return super()._run_followup_action(actor_id, tool, args, question, label)  # type: ignore[misc]
