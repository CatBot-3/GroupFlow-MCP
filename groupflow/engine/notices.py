"""Telling the people a decision affects, and asking the speaker before guessing.

notify_people: the speaker asks Alexa to let others know about something in a plan. The server picks
the recipients by their role in that plan (everyone, organizers, participants, owners, people whose
answer is still needed, or named people), writes the facts from the plan itself, and adds what each
person has to do, if anything. Only people who can see the plan are ever told, a hidden plan's guest
never is, a note that mentions a plan some recipient cannot see is refused, and telling people changes
nothing in the plan and never counts as anyone's agreement. Hosts deliver the notices: Alexa reads them
out on a person's next turn, the group chat posts them to the person's private thread.

Follow-up questions: when a request is vague ("push it back a bit") or there is a next step the speaker
did not ask for ("want me to tell the others?"), the question and the exact action behind each answer
are kept by the server. The answer then runs exactly what was offered, as the speaker, with every
permission check at that moment, even if Alexa's chat memory was lost in between or the person answers
from another device. Kinds: offer (the server's "tell the others?" after a decision), clarify (the
server's "which plan?" when a request could mean several), ask (the assistant's own question).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from groupflow.core.errors import DomainError

from .core import ASSISTANT, STATUS_WORDS, UNSET, Row, Scope, _names

NOTIFY_LEVELS = ("everyone", "organizers", "participants", "owners", "needs_answer")
LEVEL_WORDS = {"all": "everyone", "everybody": "everyone", "the_group": "everyone", "group": "everyone", "organizer": "organizers",
               "hosts": "organizers", "participant": "participants", "attendees": "participants", "owner": "owners",
               "people_with_a_job": "owners", "pending": "needs_answer", "waiting": "needs_answer"}
NOTIFY_WINDOW = timedelta(minutes=10)
NOTIFY_PER_WINDOW = 6
NOTE_MAX = 280
ABOUT_MAX = 5
FOLLOWUP_TTL = timedelta(minutes=30)
OFFER_TTL = timedelta(minutes=10)  # an offer the assistant may not have asked goes stale sooner than a question it did ask
FOLLOWUP_OPEN_MAX = 3
FOLLOWUP_SUGGESTED_MAX = 5
FOLLOWUP_TOOLS = ("notify_people", "propose_change", "decide_change", "request_confirmations", "find_alternatives", "commit_plan",
                  "create_plan", "report_observation", "set_my_conditions", "set_reminder")
YES = {"yes", "y", "yeah", "yea", "yep", "yup", "ya", "ye", "sure", "ok", "okay", "k", "do it", "go ahead", "please", "yes please", "sounds good",
       "go for it", "definitely", "absolutely", "of course", "why not"}
NO = {"no", "n", "nah", "nope", "no thanks", "dont", "don't", "skip", "skip it", "not now", "leave it", "never mind", "nevermind", "no need"}
ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4, "fifth": 5, "5th": 5,
            "last": -1}
FILLER = {"the", "a", "an", "one", "that", "this", "it", "its", "please", "for", "to", "of", "on", "in", "at", "with", "and", "or", "option",
          "i", "mean", "meant", "is", "go", "lets", "let's", "um", "uh", "like"}


def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _shift(stamp: str, delta: timedelta) -> str:
    return _stamp(datetime.fromisoformat(stamp.replace("Z", "+00:00")) + delta)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", (text or "").lower().replace("’", "'"))).strip()


def _words(text: str) -> str:
    """Lowercase words without possessives or punctuation, padded for whole-phrase matching."""
    return " " + re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", re.sub(r"'s\b", "", (text or "").lower().replace("’", "'")))).strip() + " "


def _or_names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]


class Notices:
    """Mixed into GroupFlow. Uses the core's scope, rows, audit and notifications, and the agreement layer's snapshots."""

    # ================================================================ hooks on the write and read paths

    def propose_change(self, actor_id: str, *args: Any, **kwargs: Any) -> Any:
        marker = self._notification_marker()
        try:
            result = super().propose_change(actor_id, *args, **kwargs)  # type: ignore[misc]
        except DomainError as err:
            if not args and ASSISTANT.get():
                self._clarify_plan(actor_id, err, "propose_change", kwargs)
            raise
        return self._offer_to_tell(actor_id, result, marker)

    def decide_change(self, actor_id: str, *args: Any, **kwargs: Any) -> Any:
        marker = self._notification_marker()
        return self._offer_to_tell(actor_id, super().decide_change(actor_id, *args, **kwargs), marker)  # type: ignore[misc]

    def context(self, actor_id: str, *args: Any, **kwargs: Any) -> Any:
        view = super().context(actor_id, *args, **kwargs)  # type: ignore[misc]
        view.open_questions = self.db.tx(lambda: self._open_questions(self.scope_for(actor_id)))
        if view.open_questions:
            n = len(view.open_questions)
            note = f"{n} open question{'s' if n != 1 else ''}"
            view.headline = f"{note}." if view.headline == "Nothing new." else f"{view.headline.rstrip('.')}, {note}."
        return view

    # ================================================================ who has which role in a plan

    def _plan_roles(self, plan: Row, items: list[Row], scope: Scope) -> dict[str, set[str]]:
        """Who has which role. Dependent work counts only in plans the speaker can see, so the result never hints at a hidden plan's tasks."""
        audience = set(self.plan_audience(plan))
        organizers = {m for m in audience if self.is_organizer(plan, m)}
        snap = self._latest_snapshot(plan) if self._has_activities(plan) else None
        if snap is None:
            participants = set(audience)  # a plan without activities: everyone in it takes part
        else:
            acts = {i["id"] for i in items if i["shape"] == "activity"}
            participants = {p for a in snap.live if not acts or a.id in acts for p in a.participants}
        if items:
            owners = {i["owner_id"] for i in items}
            for i in items:
                owners |= {dep["owner_id"] for link in self._links_to(i["id"]) if (dep := self._item(link["item_id"])) and scope.sees_plan(dep["plan_id"])}
            requests = [r for i in items for r in self._open_requests(i["id"])]
        else:
            owners = {r["owner_id"] for r in self.db.all("SELECT owner_id FROM items WHERE plan_id = ? AND archived = 0 AND (shape <> 'task' OR status <> 'done')",
                                                         plan["id"])}
            requests = self.db.all("SELECT * FROM requests WHERE plan_id = ? AND status = 'open'", plan["id"])
        needs_answer = {r["assignee_id"] for r in requests}
        cand = self._revision(plan["candidate_revision_id"]) if plan["candidate_revision_id"] else None
        if cand is not None and self._round_open(cand):
            needs_answer |= {s["member_id"] for s, status, _ in self._requirements(cand) if status == "pending"}
        roles = {"everyone": audience, "organizers": organizers, "participants": participants, "owners": owners, "needs_answer": needs_answer}
        return {k: v & audience for k, v in roles.items()}

    def _about_fact(self, plan: Row, about: Row) -> str:
        """What the notice is about, written from the plan's own state (never from the model's wording)."""
        if about["shape"] == "detail":
            fact = f'{about["label"]} is "{self._current_value(about) or "not set"}"'
            pending = self._open_requests(about["id"])
            if pending:
                f = self._fact(pending[0]["fact_id"]) if pending[0]["fact_id"] else None
                fact += f' ({self.name(pending[0]["requested_by"])} suggested "{f["value"] if f else pending[0]["summary"]}", '
                fact += f'waiting for {self.name(pending[0]["assignee_id"])})'
            return fact
        if about["shape"] == "task":
            bits = [STATUS_WORDS.get(about["status"] or "todo", about["status"] or "")]
            if about["due"]:
                bits.append(f'due {about["due"]}')
            return f'{about["label"]} ({", ".join(bits)}; {self.name(about["owner_id"])})'
        snap = self._latest_snapshot(plan)
        a = next((x for x in (snap.activities if snap else ()) if x.id == about["id"]), None)
        if a is None:
            return about["label"]
        if a.cancelled:
            return f"{a.title} is off"
        where = self._place_name(plan, a)
        agreed = plan["status"] == "COMMITTED" and not plan["candidate_revision_id"]
        return f"{a.title}: {self._when(plan, a)}{' at ' + where if where else ''}" + ("" if agreed else " (not agreed yet)")

    def _role_hint(self, plan: Row, items: list[Row], member: str, roles: dict[str, set[str]]) -> tuple[str, str]:
        requests = [r for i in items for r in self._open_requests(i["id"])] if items else \
            self.db.all("SELECT assignee_id FROM requests WHERE plan_id = ? AND status = 'open'", plan["id"])
        if any(r["assignee_id"] == member for r in requests):
            return "decide", "It is waiting for your decision; tell Alexa yes or no."
        if member in roles["needs_answer"]:
            return "confirm", "Your answer is needed on your GroupFlow page."
        if items and member in roles["owners"] and all(member != i["owner_id"] for i in items):
            return "check", "Check that your part still works with this."
        if member in roles["organizers"]:
            if plan["status"] == "READY_TO_COMMIT":
                return "organizer", "Everyone needed has answered; you can lock it in."
            if plan["status"] in ("DEGRADED", "BLOCKED", "NEEDS_ATTENTION"):
                return "organizer", "It needs a fix; ask Alexa for alternatives."
            return "organizer", ""
        if member in roles["owners"]:
            return "owner", ""
        return "fyi", ""

    # ================================================================ notes never carry another plan's secrets

    def _plan_phrases(self, plan: Row) -> list[str]:
        """The plan's title and its place and text values, whole and as their runs of capitalized words ("Rosa's Trattoria")."""
        out = [plan["title"]]
        for it in self.db.all("SELECT * FROM items WHERE plan_id = ? AND archived = 0 AND shape = 'detail' AND value_kind IN ('place', 'text')", plan["id"]):
            value = self._current_value(it)
            if value:
                out.append(value)
        for text in list(out):
            out += [m.group(0) for m in re.finditer(r"(?:[A-Z][\w'’]+)(?:\s+[A-Z][\w'’]+)+", text)]
        return out

    def _secret_phrases(self, actor_id: str, recipient: str) -> set[str]:
        """Phrases that would tell the recipient about a plan kept from them (a plan of their own group that the speaker sees and they do
        not: a surprise, someone's private notes): its title without people's names, and its place and text values, unless the same words
        also appear in a plan the recipient can see. Plans of groups they are not in are not secrets from them, only not theirs."""
        def spaces(member: str) -> set[str]:
            return {r["space_id"] for r in self.db.all("SELECT space_id FROM space_members WHERE member_id = ?", member)}

        mine, theirs = spaces(actor_id), spaces(recipient)
        groups = {r["group_id"] for r in self.db.all("SELECT group_id FROM group_members WHERE member_id = ? AND status = 'active'", recipient)}
        plans = self.db.all("SELECT * FROM plans WHERE status <> 'CANCELLED'")
        hidden = [p for p in plans if p["space_id"] in mine and p["space_id"] not in theirs and p["group_id"] in groups]
        if not hidden:
            return set()
        seen = "".join(_words(t) for p in plans if p["space_id"] in theirs for t in self._plan_phrases(p))
        names = {w for r in self.db.all("SELECT name FROM members") for w in _words(r["name"]).split()}
        out: set[str] = set()
        for p in hidden:
            for phrase in self._plan_phrases(p):
                ph = " ".join(w for w in _words(phrase).split() if w not in names)
                if len(ph) >= 4 and f" {ph} " not in seen:
                    out.add(ph)
        return out

    def _note_leak(self, actor_id: str, note: str, recipients: set[str]) -> str | None:
        said = _words(note)
        for m in sorted(recipients):
            if any(f" {ph} " in said for ph in self._secret_phrases(actor_id, m)):
                return m
        return None

    # ================================================================ notify_people

    def notify_people(self, actor_id: str, plan: str | None = None, about: str | list[str] | None = None, audience: list[str] | None = None,
                      note: str | None = None) -> dict[str, Any]:
        self._rate_limit(actor_id)
        note = re.sub(r"\s+", " ", (note or "").strip())
        if len(note) > NOTE_MAX:
            raise DomainError("INVALID_ARGUMENT", f"Keep the note under {NOTE_MAX} characters.")
        refs = [a for a in ([about] if isinstance(about, str) else list(about or [])) if a and a.strip()]
        if len(refs) > ABOUT_MAX:
            raise DomainError("INVALID_ARGUMENT", f"Tell people about at most {ABOUT_MAX} items at once.")
        wanted = [a.strip() for a in (audience or ["everyone"]) if a and a.strip()] or ["everyone"]

        def run() -> dict[str, Any]:
            scope = self.scope_for(actor_id)
            p = self.resolve_plan(scope, plan) if plan else None
            items = [self.resolve_item(scope, r, p["id"] if p else None) for r in refs]
            items = list({i["id"]: i for i in items if i}.values())
            if p is None:
                p = scope.plans[items[0]["plan_id"]] if items else self.resolve_plan(scope, None, purpose="people could be told")
            if any(i["plan_id"] != p["id"] for i in items):
                raise DomainError("INVALID_ARGUMENT", "Everything in one update must belong to the same plan; send one update per plan.")
            since = _shift(self.ts(), -NOTIFY_WINDOW)
            sent_lately = self.db.scalar("SELECT COUNT(*) FROM audit WHERE actor_id = ? AND plan_id = ? AND action = 'plan.notified' AND at >= ?",
                                         actor_id, p["id"], since) or 0
            if sent_lately >= NOTIFY_PER_WINDOW:
                raise DomainError("RATE_LIMITED", f"You have sent {sent_lately} updates about {p['title']} in the last few minutes. Wait a bit before sending more.")
            roles = self._plan_roles(p, items, scope)
            heard = set(scope.listeners or ())
            recipients: set[str] = set()
            in_room: set[str] = set()
            for ref in wanted:
                level = ref.lower().replace(" ", "_")
                level = LEVEL_WORDS.get(level, level)
                if level in NOTIFY_LEVELS:
                    # People in this conversation already heard it; asking for a level never tells them twice.
                    recipients |= roles[level] - heard
                    in_room |= roles[level] & heard
                else:
                    recipients.add(self.resolve_member(scope, ref, p)["id"])
            recipients.discard(actor_id)
            in_room.discard(actor_id)
            if note and (leak := self._note_leak(actor_id, note, recipients)):
                who = "someone it would go to" if scope.listeners else self.name(leak)
                raise DomainError("PRIVATE_NOTE", f"Nobody was told: the note mentions something {who} cannot see. Say it without that part, "
                                  "or tell only people who can see it.")
            facts = "; ".join(self._about_fact(p, i) for i in items)
            hints = {m: self._role_hint(p, items, m, roles) for m in recipients}
            # A bare reminder ("nudge whoever hasn't answered") is fine when it tells each person what to do; otherwise say what to tell.
            if not items and not note and not all(hint for _, hint in hints.values()):
                raise DomainError("INVALID_ARGUMENT", "Say what to tell them: pass about (items in the plan) or a short note in the speaker's words.")
            speaker = self.name(actor_id)
            stale = _shift(self.ts(), -timedelta(minutes=30))
            told, skipped = [], [{"id": m, "name": self.name(m), "reason": "heard it in this conversation"} for m in sorted(in_room - recipients)]
            for m in sorted(recipients, key=lambda x: self.name(x)):
                role, hint = hints[m]
                if facts or note:
                    text = f"{speaker} asked Alexa to let you know about {p['title']}" + (f": {facts}." if facts else ".")
                else:
                    text = f"{speaker} asked Alexa to remind you about {p['title']}."
                text += (f' {speaker} says: "{note}"' if note else "") + (f" {hint}" if hint else "")
                if self.db.get("SELECT 1 AS x FROM notifications WHERE member_id = ? AND plan_id = ? AND text = ? AND read_at IS NULL AND created_at >= ?",
                               m, p["id"], text, stale):
                    skipped.append({"id": m, "name": self.name(m), "reason": "already told"})
                    continue
                notice = self._notify([m], p, "notice", text)[0]
                told.append({"id": m, "name": self.name(m), "role": role, "text": text, "notice_id": notice})
            if not told and not skipped:
                raise DomainError("NOT_FOUND", "Nobody else matches that in this plan, so nobody was told.")
            names = [t["name"] for t in told]
            what = ", ".join(i["label"] for i in items) or p["title"]
            self._audit(actor_id, "plan.notified", "plan", p["id"], p,
                        f"{speaker} told {_names(names) or 'nobody new'} about {what}." + (f' Note: "{note}"' if note else ""),
                        payload={"recipients": [t["id"] for t in told], "about": [i["id"] for i in items]})
            if names:
                msg = f"Told {_names(names)}. They hear it next time they talk to Alexa or open GroupFlow."
            else:
                msg = "Nobody new to tell: " + "; ".join(f'{s["name"]} {s["reason"]}' for s in skipped) + "."
            msg += " Nothing in the plan changed, and nobody's agreement was given."
            return {"outcome": "sent" if told else "already_told", "plan": p["title"], "about": [i["label"] for i in items],
                    "recipients": told, "skipped": skipped, "message": msg}

        try:
            return self.db.tx(run)
        except DomainError as err:
            if ASSISTANT.get():
                self._clarify_plan(actor_id, err, "notify_people", {"plan": plan, "about": about, "audience": audience, "note": note or None})
            raise

    # ================================================================ "should I tell the others?" after a decision

    def _notification_marker(self) -> int:
        return int(self.db.scalar("SELECT COALESCE(MAX(rowid), 0) FROM notifications") or 0)

    def _offer_to_tell(self, actor_id: str, result: Any, marker: int) -> Any:
        """After a decision that took effect, offer to tell the people in the plan who did not hear about it. One offer per plan stays
        pending: a second decision soon after widens it (same id), so a single yes tells everyone about both."""
        if not ASSISTANT.get() or result is None or getattr(result, "outcome", None) not in ("confirmed", "accepted", "amended") \
                or not getattr(result, "item", None):
            return result
        item = self._item(result.item)
        if item is None or item["shape"] != "detail":
            return result

        def run() -> dict[str, Any] | None:
            p = self._plan_row(item["plan_id"])
            stamp = self.ts()
            declined = self.db.get("SELECT 1 AS x FROM followups WHERE member_id = ? AND plan_id = ? AND kind = 'offer' AND status = 'answered' "
                                   "AND answer = 'no' AND answered_at >= ?", actor_id, p["id"], _shift(stamp, -FOLLOWUP_TTL))
            if declined:  # they just said no to being asked; do not ask again for a while
                return None
            told = {r["member_id"] for r in self.db.all("SELECT member_id FROM notifications WHERE rowid > ? AND plan_id = ?", marker, p["id"])}
            # Owners of work that depends on the item were already flagged by the change itself ("check this still works").
            alerted = set(getattr(getattr(result, "impacts", None), "notified", None) or [])
            told |= {m for m in self.plan_audience(p) if self.name(m) in alerted}
            heard = set(self._listeners(actor_id) or ())
            others = [m for m in self.plan_audience(p) if m != actor_id and m not in told and m not in heard]
            self._expire_followups()
            prior = self.db.get("SELECT * FROM followups WHERE member_id = ? AND plan_id = ? AND kind = 'offer' AND status = 'suggested' "
                                "ORDER BY created_at DESC, rowid DESC LIMIT 1", actor_id, p["id"])
            about = [item["id"]]
            if prior:
                old = (json.loads(prior["options_json"])[0].get("action") or {}).get("arguments") or {}
                about = list(dict.fromkeys([*(old.get("about") or []), item["id"]]))[-ABOUT_MAX:]
                others = list(dict.fromkeys([*(old.get("audience") or []), *others]))
                others = [m for m in others if m != actor_id and m not in heard and m in self.plan_audience(p)]
            if not others:
                return None
            names = [self.name(m) for m in others]
            who = _names(names) if len(names) <= 4 else f"the other {len(names)} people in {p['title']}"
            facts = " and ".join(f'{i["label"]} is now "{self._current_value(i)}"' for i in (self._item(x) for x in about) if i)
            question = f"Want me to let {who} know that {facts}?"
            options = [{"label": "yes", "action": {"tool": "notify_people", "arguments": {"plan": p["id"], "about": about, "audience": others}}},
                       {"label": "no", "action": None}]
            if prior:
                self.db.run("UPDATE followups SET question = ?, options_json = ?, created_at = ?, expires_at = ? WHERE id = ?",
                            question, json.dumps(options), stamp, _shift(stamp, OFFER_TTL), prior["id"])
                fid = prior["id"]
            else:
                fid = self._store_followup(actor_id, "offer", question, options, p, "suggested")
            return {"id": fid, "question": question, "options": ["yes", "no"]}

        offer = self.db.tx(run)
        if offer:
            result.suggested_followup = offer
        return result

    # ================================================================ follow-up questions

    def _expire_followups(self) -> None:
        self.db.run("UPDATE followups SET status = 'cancelled' WHERE status IN ('open', 'suggested') AND expires_at < ?", self.ts())

    @staticmethod
    def _clean_options(options: list[Any]) -> list[dict[str, Any]]:
        opts: list[dict[str, Any]] = []
        for o in options or []:
            o = {"label": o} if isinstance(o, str) else (o.model_dump() if hasattr(o, "model_dump") else dict(o or {}))
            label = re.sub(r"\s+", " ", str(o.get("label") or "").strip())
            if not label or len(label) > 60:
                raise DomainError("INVALID_ARGUMENT", "Each option needs a label of 1 to 60 characters.")
            action = o.get("action")
            if action:
                action = dict(action)
                if action.get("tool") not in FOLLOWUP_TOOLS:
                    raise DomainError("INVALID_ARGUMENT", f"An option can run one of: {', '.join(FOLLOWUP_TOOLS)}.")
                args = action.get("arguments") or {}
                if not isinstance(args, dict) or len(json.dumps(args, default=str)) > 4000:
                    raise DomainError("INVALID_ARGUMENT", "An option's arguments must be a small object.")
                action = {"tool": action["tool"], "arguments": args}
            opts.append({"label": label, "action": action or None})
        if not 2 <= len(opts) <= 5 or len({_norm(o["label"]) for o in opts}) != len(opts):
            raise DomainError("INVALID_ARGUMENT", "Give 2 to 5 options with different labels.")
        return opts

    def _store_followup(self, actor_id: str, kind: str, question: str, options: list[dict[str, Any]], plan: Row | None, status: str) -> str:
        self._expire_followups()
        cap = FOLLOWUP_OPEN_MAX if status == "open" else FOLLOWUP_SUGGESTED_MAX
        rows = self.db.all("SELECT id FROM followups WHERE member_id = ? AND status = ? ORDER BY created_at DESC, rowid DESC", actor_id, status)
        for r in rows[cap - 1:]:
            self.db.run("UPDATE followups SET status = 'cancelled' WHERE id = ?", r["id"])
        fid = self.new_id("fu")
        stamp = self.ts()
        ttl = FOLLOWUP_TTL if status == "open" else OFFER_TTL
        self.db.run("INSERT INTO followups (id, member_id, plan_id, kind, question, options_json, status, created_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    fid, actor_id, plan["id"] if plan else None, kind, question, json.dumps(options, default=str), status, stamp, _shift(stamp, ttl))
        return fid

    def _clarify_plan(self, actor_id: str, err: DomainError, tool: str, kwargs: dict[str, Any]) -> None:
        """A request that could mean several plans: keep "which plan?" with the same call behind each answer, so the reply is one word."""
        plans = (err.details or {}).get("plans") or []
        if err.code != "AMBIGUOUS_PLAN" or kwargs.get("plan") or not 2 <= len(plans) <= 5:
            return
        args: dict[str, Any] = {}
        for k, v in kwargs.items():
            if v is UNSET or v is None or v is False or v == []:
                continue
            args[k] = v
        if "due" in kwargs and kwargs["due"] is None:
            args["due"] = ""
        labels = [p["title"][:60] for p in plans]
        if len({_norm(x) for x in labels}) != len(labels):
            labels = [f'{p["title"][:40]} ({p["id"]})' for p in plans]
        options = [{"label": label, "action": {"tool": tool, "arguments": dict(args, plan=p["id"])}} for label, p in zip(labels, plans)]
        question = f"Which plan is that for: {_or_names([p['title'] for p in plans])}?"
        try:
            fid = self.db.tx(lambda: self._store_followup(actor_id, "clarify", question, options, None, "suggested"))
        except DomainError:
            return
        err.details["followup"] = {"id": fid, "question": question, "options": labels}

    def ask_followup(self, actor_id: str, question: str | None = None, options: list[Any] | None = None, plan: str | None = None,
                     followup: str | None = None) -> dict[str, Any]:
        self._rate_limit(actor_id)
        if followup:
            def promote() -> dict[str, Any]:
                scope = self.scope_for(actor_id)
                row = next((r for r in self._followup_rows(scope) if r["id"] == followup), None)
                if row is None:
                    raise DomainError("NOT_FOUND", "That offer has expired or was answered. Act on the request directly.")
                stamp = self.ts()
                self.db.run("UPDATE followups SET status = 'open', created_at = ?, expires_at = ? WHERE id = ?", stamp, _shift(stamp, FOLLOWUP_TTL), row["id"])
                return self._pending(row["id"], row["question"], json.loads(row["options_json"]))
            return self.db.tx(promote)

        q = re.sub(r"\s+", " ", (question or "").strip())
        if not q or len(q) > 300:
            raise DomainError("INVALID_ARGUMENT", "Ask a question of 1 to 300 characters, or pass followup to ask an offered one.")
        opts = self._clean_options(options or [])

        def run() -> dict[str, Any]:
            scope = self.scope_for(actor_id)
            p = self.resolve_plan(scope, plan) if plan else self._plan_of_options(scope, opts)
            fid = self._store_followup(actor_id, "ask", q, opts, p, "open")
            self._audit(actor_id, "followup.asked", "plan" if p else "member", p["id"] if p else actor_id, p, f'Alexa asked {self.name(actor_id)}: "{q}"',
                        private_to=actor_id)
            return self._pending(fid, q, opts)
        return self.db.tx(run)

    def _plan_of_options(self, scope: Scope, options: list[dict[str, Any]]) -> Row | None:
        """The one plan every option's action is about, if they agree (so a shared conversation shows the question only where it belongs)."""
        found: set[str] = set()
        for o in options:
            args = (o.get("action") or {}).get("arguments") or {}
            try:
                if args.get("plan"):
                    found.add(self.resolve_plan(scope, str(args["plan"]))["id"])
                for ref in [args.get("item"), *([args["about"]] if isinstance(args.get("about"), str) else args.get("about") or [])]:
                    if isinstance(ref, str) and ref.strip() and (it := self.resolve_item(scope, ref, required=False)):
                        found.add(it["plan_id"])
            except DomainError:
                continue
        return scope.plans[found.pop()] if len(found) == 1 else None

    def dismiss_followup(self, actor_id: str, followup: str) -> dict[str, Any]:
        """The person declined to answer (e.g. closed the host's prompt): drop the question, run nothing."""
        def run() -> dict[str, Any]:
            row = next((r for r in self._followup_rows(self.scope_for(actor_id)) if r["id"] == followup), None)
            if row is None:
                raise DomainError("NOT_FOUND", "That question was already answered or has expired.")
            self.db.run("UPDATE followups SET status = 'cancelled' WHERE id = ?", row["id"])
            return {"outcome": "dismissed", "id": row["id"], "question": row["question"], "message": "They did not answer; nothing was changed or sent."}
        return self.db.tx(run)

    @staticmethod
    def _pending(fid: str, question: str, options: list[dict[str, Any]]) -> dict[str, Any]:
        return {"outcome": "pending", "id": fid, "question": question, "options": [o["label"] for o in options],
                "message": "Ask this in your reply, with the options. When they answer, call answer_followup with their words; the server runs what "
                           "they chose."}

    def _followup_rows(self, scope: Scope, statuses: tuple[str, ...] = ("open", "suggested")) -> list[Row]:
        self._expire_followups()
        marks = ",".join("?" for _ in statuses)
        rows = self.db.all(f"SELECT * FROM followups WHERE member_id = ? AND status IN ({marks}) ORDER BY created_at DESC, rowid DESC",
                           scope.member_id, *statuses)
        if scope.listeners:  # in a shared conversation only questions about plans everyone listening can see
            return [r for r in rows if r["plan_id"] and scope.sees_plan(r["plan_id"])]
        return [r for r in rows if not r["plan_id"] or scope.sees_plan(r["plan_id"])]

    def _open_questions(self, scope: Scope) -> list[dict[str, Any]]:
        """Questions the assistant asked the speaker that still wait for an answer, newest first. A server offer it may never have asked is
        not listed (answer_followup still finds it by id, or by matching a reply like "yeah"), so a briefing never claims a question nobody
        heard."""
        return [{"id": r["id"], "question": r["question"], "options": [o["label"] for o in json.loads(r["options_json"])],
                 "plan": scope.plans[r["plan_id"]]["title"] if r["plan_id"] else None, "since": r["created_at"]}
                for r in self._followup_rows(scope, ("open",))]

    @staticmethod
    def _match_option(options: list[dict[str, Any]], choice: str) -> dict[str, Any] | None:
        c = _norm(choice)
        if not c:
            return None
        labels = [_norm(o["label"]) for o in options]
        if c in labels:
            return options[labels.index(c)]
        # "7" is "7 pm", not "7:30 pm": times compare without am/pm and :00.
        cores = [re.sub(r"\s+", " ", re.sub(r"\b(?:am|pm|o'?clock)\b|\b00\b", "", lab)).strip() for lab in labels]
        said_core = re.sub(r"\s+", " ", re.sub(r"\b(?:am|pm|o'?clock)\b|\b00\b", "", c)).strip()
        if said_core and cores.count(said_core) == 1:
            return options[cores.index(said_core)]
        # "7" picks "7 pm" before it could mean the seventh option.
        hits = [o for o, lab in zip(options, labels) if lab and (f" {lab} " in f" {c} " or f" {c} " in f" {lab} ")]
        if len(hits) == 1:
            return hits[0]
        first = c.split(" ")[0]
        yes_no = all(lab in YES or lab in NO for lab in labels)  # "1" is never a yes
        index = (int(c) if c.isdigit() and not yes_no else None) or ORDINALS.get(first) or \
            (ORDINALS.get(c.split(" ")[1]) if first == "the" and " " in c else None)
        if index:
            if index == -1:
                return options[-1]
            if 1 <= index <= len(options):
                return options[index - 1]
        for words in (YES, NO):
            if c in words or first in words or " ".join(c.split(" ")[:2]) in words:
                hits = [o for o, lab in zip(options, labels) if lab in words or lab.split(" ")[0] in words]
                if len(hits) == 1:
                    return hits[0]
        said = set(c.split()) - FILLER
        scores = [len(said & (set(lab.split()) - FILLER)) for lab in labels]
        best = max(scores, default=0)
        return options[scores.index(best)] if best and scores.count(best) == 1 else None

    def answer_followup(self, actor_id: str, choice: str, followup: str | None = None) -> dict[str, Any]:
        self._rate_limit(actor_id)

        def pick() -> tuple[Row, dict[str, Any]]:
            scope = self.scope_for(actor_id)
            rows = self._followup_rows(scope)
            if followup:
                rows = [r for r in rows if r["id"] == followup]
            if not rows:
                raise DomainError("NOT_FOUND", "There is no open question to answer (it was answered, replaced or expired). Act on the request directly.")
            # Without an id (the host keeps only text between turns), the newest question their words answer: an "7:30" answers the
            # time question even if an offer to tell people came after it, and a "yes" answers the newest yes/no.
            row, chosen = rows[0], None
            for r in rows:
                if (hit := self._match_option(json.loads(r["options_json"]), choice)) is not None:
                    row, chosen = r, hit
                    break
            if chosen is None:
                opts = json.loads(row["options_json"])
                raise DomainError("INVALID_ARGUMENT", f'"{choice}" does not match an option of "{row["question"]}": {", ".join(o["label"] for o in opts)}. '
                                  "Ask again, or act on what they said directly.", {"options": [o["label"] for o in opts], "followup": row["id"]})
            self.db.run("UPDATE followups SET status = 'answered', answer = ?, answered_at = ? WHERE id = ?", chosen["label"], self.ts(), row["id"])
            self._audit(actor_id, "followup.answered", "member", actor_id, self._plan_row(row["plan_id"]) if row["plan_id"] else None,
                        f'{self.name(actor_id)} answered "{chosen["label"]}" to "{row["question"]}"', private_to=actor_id)
            return row, chosen

        row, chosen = self.db.tx(pick)
        out: dict[str, Any] = {"outcome": "answered", "id": row["id"], "question": row["question"], "choice": chosen["label"]}
        action = chosen.get("action")
        if not action:
            out["message"] = "Noted; nothing was changed or sent."
            return out
        try:
            result = self._run_followup_action(actor_id, action["tool"], action["arguments"], row["question"], chosen["label"])
            out["action"] = {"tool": action["tool"], "ok": True, "result": result}
            out["message"] = f'Ran {action["tool"]} as they chose; report its outcome exactly.'
        except DomainError as err:
            out["action"] = {"tool": action["tool"], "ok": False, "error": err.to_json()}
            out["message"] = f'{action["tool"]} was refused; tell the speaker why. Nothing else was done.'
        self.db.tx(lambda: self.db.run("UPDATE followups SET result_json = ? WHERE id = ?", json.dumps(out["action"], default=str), row["id"]))
        return out

    def _run_followup_action(self, actor_id: str, tool: str, args: dict[str, Any], question: str, label: str) -> dict[str, Any]:
        """Run an offered action exactly as the speaker could have run it themselves. The question and the answer join their words as
        evidence, so a vague request ("a bit later") that was settled by a concrete choice is recorded with that choice."""
        a = dict(args)
        if "quote" in a or tool in ("propose_change", "decide_change", "create_plan", "set_my_conditions"):
            a["quote"] = f'{a.get("quote") or ""} [asked: "{question}" answered: "{label}"]'.strip()

        def keep(*names: str) -> dict[str, Any]:
            return {k: a[k] for k in names if k in a}

        def dump(value: Any) -> dict[str, Any]:
            return value.model_dump(mode="json") if hasattr(value, "model_dump") else value

        if tool == "notify_people":
            return self.notify_people(actor_id, **keep("plan", "about", "audience", "note"))
        if tool == "propose_change":
            kw = keep("item", "plan", "value", "status", "owner", "title", "day", "start", "end", "place", "participants", "new_type", "value_kind",
                      "certainty", "quote")
            kw["due"] = (None if a["due"] == "" else a["due"]) if "due" in a else UNSET
            kw["remove"] = bool(a.get("remove"))
            return dump(self.propose_change(actor_id, **kw))
        if tool == "decide_change":
            return dump(self.decide_change(actor_id, a.get("request", ""), a.get("decision", ""), **keep("amended_value", "quote")))
        if tool == "request_confirmations":
            return dump(self.request_confirmations(actor_id, **keep("plan", "expected_version")))
        if tool == "find_alternatives":
            return dump(self.find_alternatives(actor_id, **keep("plan", "expected_version")))
        if tool == "commit_plan":
            return dump(self.commit_plan(actor_id, **keep("plan", "expected_version")))
        if tool == "create_plan":
            kw = keep("group", "start_date", "days", "hidden_from", "audience", "preset", "lifecycle", "quote")
            return dump(self.create_plan(actor_id, a.get("template", ""), a.get("title", ""), starter=a.get("starter") is not False,
                                         private=bool(a.get("private")), **kw))
        if tool == "report_observation":
            return dump(self.report_observation(actor_id, a.get("kind", ""), **keep("place", "plan", "day", "start", "end", "price", "note")))
        if tool == "set_my_conditions":
            return dump(self.set_my_conditions(actor_id, list(a.get("conditions") or []), **keep("plan", "quote")))
        if tool == "set_reminder":
            return self.set_reminder(actor_id, **keep("when", "plan", "about", "audience", "note"))  # type: ignore[attr-defined]
        raise DomainError("INVALID_ARGUMENT", f"{tool} cannot be run from a follow-up.")
