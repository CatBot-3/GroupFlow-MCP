"""RSVPs with plus-ones, and a bring list people claim from.

An RSVP is each person's own yes, no or maybe for a plan, with how many guests they bring. Only the person can answer for themselves,
because an RSVP is a promise about where they will be, and the headcount it adds up to is computed from the answers rather than typed by
anyone. It counts the people in the plan and the guests they bring; a guest count detail, if the plan has one, stays its owner's to set,
because it may include guests who are not in GroupFlow.

Dietary needs are not part of an RSVP. They stay private conditions (set_my_conditions, confirmed on the person's own page), and the RSVP
view shows only the needs to cater for, as tags without names, for people who said yes or maybe.

The bring list is a potluck list: anyone in the plan adds things that are needed, and each person claims what they bring, so nobody turns
up with the third bag of chips. A claim is one person's own promise, so nobody can claim for someone else or take over another person's
claim. Things still unclaimed with the day three days away or less are loose ends for check-ins.
"""
from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Any

from groupflow.core.errors import DomainError

from .core import ASSISTANT, Row, Scope, _names

ANSWERS = {
    "yes": {"yes", "y", "yeah", "yep", "coming", "i'm coming", "im coming", "i'll be there", "ill be there", "count me in", "in", "going", "attending",
            "sure", "definitely", "will come", "be there"},
    "no": {"no", "n", "nope", "nah", "not coming", "can't come", "cant come", "can't make it", "cant make it", "won't make it", "wont make it",
           "out", "count me out", "not going", "decline", "skip"},
    "maybe": {"maybe", "not sure", "unsure", "might", "might come", "possibly", "perhaps", "tentative", "probably", "depends", "tbd"},
}
PLUS_MAX = 10
BRING_MAX = 40
BRING_LABEL_MAX = 60
UNCLAIMED_DAYS = 3
RSVP_WAIT_DAYS = 7


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s'-]", " ", (text or "").lower().replace("’", "'"))).strip()


def _thing_key(label: str) -> str:
    """"some chips", "the chips", "chips" are the same thing; plural -s too ("plate" and "plates")."""
    words = [w for w in _norm(label).split() if w not in ("a", "an", "the", "some", "few", "couple", "of", "bag", "bags", "box", "boxes", "pack")]
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words)


class Attendance:
    """Mixed into GroupFlow above Momentum."""

    # ================================================================ rsvp

    def _rsvps(self, plan: Row) -> dict[str, Row]:
        audience = set(self.plan_audience(plan))
        return {r["member_id"]: r for r in self.db.all("SELECT * FROM rsvps WHERE plan_id = ? ORDER BY updated_at", plan["id"]) if r["member_id"] in audience}

    def _uses_rsvp(self, plan: Row) -> bool:
        return bool(self.db.scalar("SELECT COUNT(*) FROM rsvps WHERE plan_id = ?", plan["id"]))

    def _headcount(self, plan: Row) -> dict[str, int]:
        rows = self._rsvps(plan).values()
        return {"yes": sum(1 + r["plus_ones"] for r in rows if r["answer"] == "yes"),
                "maybe": sum(1 + r["plus_ones"] for r in rows if r["answer"] == "maybe")}

    def _dietary_tags(self, plan: Row) -> list[str]:
        coming = {m for m, r in self._rsvps(plan).items() if r["answer"] in ("yes", "maybe")}
        tags: list[str] = []
        for c in self._condition_rows(plan, None, ("active",)):  # type: ignore[attr-defined]
            if c["kind"] == "needs" and c["owner_id"] in coming:
                tags += [t for t in json.loads(c["value_json"]).get("tags", []) if t not in tags]
        return tags

    def _rsvp_view(self, plan: Row, viewer: str | None) -> dict[str, Any] | None:
        if not self._uses_rsvp(plan):
            return None
        rows = self._rsvps(plan)

        def who(m: str) -> str:
            r = rows[m]
            name = "you" if m == viewer else self.name(m)
            return f"{name} +{r['plus_ones']}" if r["plus_ones"] else name

        by = {a: [who(m) for m, r in rows.items() if r["answer"] == a] for a in ("yes", "maybe", "no")}
        count = self._headcount(plan)
        view: dict[str, Any] = {
            "coming": by["yes"], "maybe": by["maybe"], "not_coming": by["no"],
            "not_answered": [("you" if m == viewer else self.name(m)) for m in self.plan_audience(plan) if m not in rows],
            "headcount": count["yes"], "headcount_with_maybes": count["yes"] + count["maybe"],
        }
        if viewer in rows:
            r = rows[viewer]
            view["your_answer"] = r["answer"] + (f" +{r['plus_ones']}" if r["plus_ones"] else "")
        if (tags := self._dietary_tags(plan)):
            view["dietary_needs"] = tags
            view["dietary_note"] = "From confirmed private conditions of people coming; names are never shown."
        return view

    def rsvp(self, actor_id: str, answer: str, plan: str | None = None, plus_ones: int | None = None) -> dict[str, Any]:
        self._rate_limit(actor_id)
        said = _norm(answer)
        picked = next((a for a, words in ANSWERS.items() if said in words), None)
        if picked is None:
            picked = "no" if re.search(r"\b(can't|cant|won't|wont|not)\b", said) else next(
                (a for a, words in ANSWERS.items() if said.split(" ")[0] in words), None)
        if picked is None:
            raise DomainError("INVALID_ARGUMENT", 'answer is "yes", "no" or "maybe".')
        if plus_ones is not None:
            try:
                plus_ones = int(plus_ones)
            except (TypeError, ValueError):
                raise DomainError("INVALID_ARGUMENT", "plus_ones is a number of extra guests, 0 to 10.") from None
            if not 0 <= plus_ones <= PLUS_MAX:
                raise DomainError("INVALID_ARGUMENT", "plus_ones is a number of extra guests, 0 to 10.")

        def run() -> dict[str, Any]:
            scope = self.scope_for(actor_id)
            p = self.resolve_plan(scope, plan, purpose="you could answer for")
            if p["status"] == "CANCELLED":
                raise DomainError("INVALID_ARGUMENT", f"{p['title']} was cancelled.")
            before = self.db.get("SELECT * FROM rsvps WHERE plan_id = ? AND member_id = ?", p["id"], actor_id)
            extra = plus_ones if plus_ones is not None else (before["plus_ones"] if before else 0)
            if picked == "no":
                extra = 0
            if before is not None and before["answer"] == picked and before["plus_ones"] == extra:
                return {"outcome": "unchanged", "rsvp": self._rsvp_view(p, actor_id), "message": "That is already your answer; nothing changed."}
            self.db.run("INSERT INTO rsvps (plan_id, member_id, answer, plus_ones, updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(plan_id, member_id) "
                        "DO UPDATE SET answer = excluded.answer, plus_ones = excluded.plus_ones, updated_at = excluded.updated_at",
                        p["id"], actor_id, picked, extra, self.ts())
            me = self.name(actor_id)
            words = {"yes": "is coming", "no": "is not coming", "maybe": "might come"}[picked] + (f" with {extra} more" if extra else "")
            released: list[str] = []
            if picked == "no":
                for b in self.db.all("SELECT * FROM bring_items WHERE plan_id = ? AND claimed_by = ? AND status = 'open'", p["id"], actor_id):
                    self.db.run("UPDATE bring_items SET claimed_by = NULL, updated_at = ? WHERE id = ?", self.ts(), b["id"])
                    released.append(b["label"])
            count = self._headcount(p)
            tally = f"{count['yes']} coming" + (f", {count['maybe']} maybe" if count["maybe"] else "")
            freed = f" {_names(released)} {'is' if len(released) == 1 else 'are'} unclaimed again on the bring list." if released else ""
            self._audit(actor_id, "rsvp.answered", "plan", p["id"], p, f"{me} {words}.{freed}")
            organizer = p["organizer_id"]
            if organizer != actor_id:
                self._notify([organizer], p, "rsvp", f"{me} {words} ({p['title']}: {tally}).{freed}")
            if released:
                others = [m for m in self.plan_audience(p) if m not in (actor_id, organizer)]
                self._notify(others, p, "rsvp", f"{me} cannot make {p['title']}, so {_names(released)} still needs someone to bring it. "
                             "Tell Alexa if you can.")
            msg = {"yes": "You're down as coming", "no": "You're down as not coming", "maybe": "You're down as a maybe"}[picked]
            msg += (f" with {extra} more" if extra else "") + f". {p['title']} has {tally}."
            if released:
                msg += f" I took you off {_names(released)} on the bring list."
            if picked != "no" and any(c.check == "needs" for c in self.template(p).conditions):
                msg += (" Dietary needs go in their private conditions, not here; confirmed ones show to the plan as tags, never with a name.")
            return {"outcome": "answered" if before is None else "changed", "rsvp": self._rsvp_view(p, actor_id), "message": msg}
        try:
            return self.db.tx(run)
        except DomainError as err:
            if ASSISTANT.get() and err.code == "AMBIGUOUS_PLAN" and not plan:
                self._clarify_plan(actor_id, err, "rsvp", {"answer": answer, "plus_ones": plus_ones})  # type: ignore[attr-defined]
            raise

    # ================================================================ bring list

    def _bring_rows(self, plan: Row) -> list[Row]:
        return self.db.all("SELECT * FROM bring_items WHERE plan_id = ? AND status = 'open' ORDER BY created_at, rowid", plan["id"])

    def _bring_view(self, plan: Row, viewer: str | None) -> list[dict[str, Any]]:
        audience = set(self.plan_audience(plan))
        out = []
        for b in self._bring_rows(plan):
            holder = b["claimed_by"] if b["claimed_by"] in audience else None
            out.append({"id": b["id"], "thing": b["label"], "bringing": ("you" if holder == viewer else self.name(holder)) if holder else None})
        return out

    def _find_thing(self, plan: Row, thing: str, loose: bool = False) -> Row | None:
        """The listed thing they mean: the same words ("the chips"), or one name inside the other ("samosas", "veggie samosas"). Sharing a
        word is not enough to claim it ("birthday candles" is not the "birthday banner"); dropping or removing may go by the best overlap."""
        key = _thing_key(thing)
        rows = self._bring_rows(plan)
        exact = [b for b in rows if b["id"] == thing.strip() or _thing_key(b["label"]) == key]
        if exact:
            return exact[0]
        words = set(key.split())
        if not words:
            return None
        inside = [b for b in rows if words <= set(_thing_key(b["label"]).split()) or set(_thing_key(b["label"]).split()) <= words]
        if len(inside) == 1:
            return inside[0]
        if not loose:
            return None
        scored = [(len(words & set(_thing_key(b["label"]).split())), b) for b in rows]
        best = max((s for s, _ in scored), default=0)
        hits = [b for s, b in scored if s == best and best]
        return hits[0] if len(hits) == 1 else None

    def bring_list(self, actor_id: str, action: str, things: list[str] | str | None = None, plan: str | None = None,
                   quote: str | None = None) -> dict[str, Any]:
        self._rate_limit(actor_id)
        names = [things] if isinstance(things, str) else list(things or [])
        names = [re.sub(r"\s+", " ", str(t)).strip() for t in names if str(t).strip()]
        if action not in ("add", "bring", "claim", "unclaim", "remove"):
            raise DomainError("INVALID_ARGUMENT", 'action is "add" (needed, nobody yet), "bring" (the speaker brings it), "unclaim" or "remove".')
        if not names:
            raise DomainError("INVALID_ARGUMENT", "Say which things.")
        if len(names) > 10 or any(len(t) > BRING_LABEL_MAX for t in names):
            raise DomainError("INVALID_ARGUMENT", f"Up to 10 things at a time, each under {BRING_LABEL_MAX} characters.")

        def run() -> dict[str, Any]:
            scope = self.scope_for(actor_id)
            p = self.resolve_plan(scope, plan, purpose="you could bring things to")
            from .intent import reaction_without_request
            if reaction_without_request(quote):
                raise DomainError("NO_ACTION_REQUESTED", "A reaction is not a request to edit the bring list.")
            if action in ("bring", "claim") and quote:
                for member in self.plan_audience(p):
                    if member == actor_id:
                        continue
                    name = re.escape(self.name(member))
                    if re.search(rf"\b(?:put|mark|sign|count)\s+{name}\b|\b{name}\s+(?:will|can|is bringing|brings)\b", quote, re.I):
                        raise DomainError("SELF_ONLY", "The request names another person, but bring_list claims only for the speaker. "
                                          "Use propose_change to assign them a task, or let them claim it themselves; do not claim it for the speaker.")
            packing = next((t for t in self.template(p).item_types if t.name == "packing"), None)
            if packing is not None:
                # One list per plan: a trip's packing list already says who brings what, so a second list would split it.
                raise DomainError("INVALID_ARGUMENT", f"{p['title']} keeps a packing list, which already says who brings what. Add an item "
                                  "there with propose_change (new_type packing), or make someone its owner.")
            me = self.name(actor_id)
            audience = set(self.plan_audience(p))
            done: list[str] = []
            notes: list[str] = []
            for t in names:
                hit = self._find_thing(p, t, loose=action in ("unclaim", "remove"))
                if action in ("add", "bring", "claim") and hit is None:
                    if len(self._bring_rows(p)) >= BRING_MAX:
                        raise DomainError("RATE_LIMITED", f"The bring list of {p['title']} is full ({BRING_MAX} things).")
                    if (leak := self._note_leak(actor_id, t, audience - {actor_id})):  # type: ignore[attr-defined]
                        raise DomainError("PRIVATE_NOTE", f"That mentions something {self.name(leak)} cannot see, and {self.name(leak)} can see "
                                          f"{p['title']}.")
                    bid = self.new_id("br")
                    self.db.run("INSERT INTO bring_items (id, plan_id, label, claimed_by, added_by, status, created_at, updated_at) "
                                "VALUES (?, ?, ?, ?, ?, 'open', ?, ?)", bid, p["id"], t, actor_id if action != "add" else None, actor_id, self.ts(), self.ts())
                    done.append(t)
                    continue
                if hit is None:
                    notes.append(f'"{t}" is not on the bring list.')
                    continue
                holder = hit["claimed_by"] if hit["claimed_by"] in audience else None
                if action == "add":
                    notes.append(f"{hit['label']} is already on the list" + (f" ({self.name(holder)} is bringing it)." if holder else "."))
                elif action in ("bring", "claim"):
                    if holder == actor_id:
                        notes.append(f"You are already bringing {hit['label']}.")
                    elif holder:
                        notes.append(f"{self.name(holder)} is already bringing {hit['label']}; only they can hand it over.")
                    else:
                        self.db.run("UPDATE bring_items SET claimed_by = ?, updated_at = ? WHERE id = ?", actor_id, self.ts(), hit["id"])
                        done.append(hit["label"])
                elif action == "unclaim":
                    if holder != actor_id:
                        notes.append(f"You were not bringing {hit['label']}." if not holder else
                                     f"{self.name(holder)} is bringing {hit['label']}; only they can drop it.")
                    else:
                        self.db.run("UPDATE bring_items SET claimed_by = NULL, updated_at = ? WHERE id = ?", self.ts(), hit["id"])
                        done.append(hit["label"])
                else:  # remove
                    if actor_id != hit["added_by"] and not self.is_organizer(p, actor_id):
                        raise DomainError("FORBIDDEN", f"{self.name(hit['added_by'])} added {hit['label']}; they or the organizer can take it off.")
                    self.db.run("UPDATE bring_items SET status = 'removed', updated_at = ? WHERE id = ?", self.ts(), hit["id"])
                    done.append(hit["label"])
                    if holder and holder != actor_id:
                        self._notify([holder], p, "bring", f"{me} took {hit['label']} off the bring list of {p['title']}; you no longer need to bring it.")
            if done:
                verb = {"add": "added to the bring list", "bring": "is bringing", "claim": "is bringing", "unclaim": "is no longer bringing",
                        "remove": "took off the bring list"}[action]
                text = f"{me} {verb} {_names(done)}" if action != "add" else f"{me} added {_names(done)} to the bring list"
                audit = {"add": "bring.added", "bring": "bring.claimed", "claim": "bring.claimed", "unclaim": "bring.unclaimed", "remove": "bring.removed"}
                self._audit(actor_id, audit[action], "plan", p["id"], p, f"{text}.")
                if action == "add":
                    self._notify(sorted(audience - {actor_id}), p, "bring", f"{text} of {p['title']}. Tell Alexa if you can bring one.")
                elif action == "unclaim":
                    self._notify(sorted(audience - {actor_id}), p, "bring", f"{text} to {p['title']}; {'it needs' if len(done) == 1 else 'they need'} "
                                 "someone else now.")
                elif action in ("bring", "claim") and p["organizer_id"] != actor_id:
                    self._notify([p["organizer_id"]], p, "bring", f"{text} to {p['title']}.")
            open_left = [b["label"] for b in self._bring_rows(p) if not (b["claimed_by"] in audience)]
            first = {"add": f"Added {_names(done)} to the bring list, and told everyone.", "bring": f"You're bringing {_names(done)}.",
                     "claim": f"You're bringing {_names(done)}.", "unclaim": f"You're no longer bringing {_names(done)}; everyone was told.",
                     "remove": f"Took {_names(done)} off the bring list."}[action] if done else ""
            msg = " ".join([first] + notes).strip()
            if open_left and action != "add":
                msg += f" Still unclaimed: {_names(open_left[:6])}{' and more' if len(open_left) > 6 else ''}."
            return {"outcome": "updated" if done else "unchanged", "bring_list": self._bring_view(p, actor_id), "message": msg}
        try:
            return self.db.tx(run)
        except DomainError as err:
            if ASSISTANT.get() and err.code == "AMBIGUOUS_PLAN" and not plan:
                self._clarify_plan(actor_id, err, "bring_list", {"action": action, "things": names})  # type: ignore[attr-defined]
            raise

    # ================================================================ hooks

    def _plan_view(self, scope: Scope, plan: Row) -> Any:
        view = super()._plan_view(scope, plan)  # type: ignore[misc]
        view.rsvp = self._rsvp_view(plan, scope.member_id)
        view.bring_list = self._bring_view(plan, scope.member_id)
        return view

    def _todos(self, scope: Scope, plans: list[Row]) -> list[Any]:
        from .views import TodoView

        todos = super()._todos(scope, plans)  # type: ignore[misc]
        me = scope.member_id
        for p in plans:
            if p["status"] != "CANCELLED" and self._uses_rsvp(p) and me not in self._rsvps(p):
                todos.append(TodoView(kind="rsvp", plan=p["title"], where="ask Alexa", text=f"Say whether you are coming to {p['title']}."))
        return todos

    def _blockers(self, plan: Row, now: Any, event: Any) -> list[dict[str, Any]]:
        out = super()._blockers(plan, now, event)  # type: ignore[misc]
        if self._uses_rsvp(plan) and event is not None and timedelta(0) <= event - now <= timedelta(days=RSVP_WAIT_DAYS):
            answered = self._rsvps(plan)
            waiting = [m for m in self.plan_audience(plan) if m not in answered and m != plan["organizer_id"]]
            if waiting:
                first = self.db.scalar("SELECT MIN(updated_at) FROM rsvps WHERE plan_id = ?", plan["id"]) or plan["created_at"]
                out.append({"kind": "rsvp", "key": f"rsvp:{plan['id']}", "what": "whether they are coming", "waiting_on": sorted(waiting),
                            "since": first, "next_step": "each of them says yes, no or maybe to Alexa"})
        return out

    def _loose_ends(self, plan: Row, now: Any, event: Any) -> list[dict[str, Any]]:
        out = super()._loose_ends(plan, now, event)  # type: ignore[misc]
        if event is None or not timedelta(0) <= event - now <= timedelta(days=UNCLAIMED_DAYS):
            return out
        audience = set(self.plan_audience(plan))
        for b in self._bring_rows(plan):
            if b["claimed_by"] not in audience and plan["organizer_id"] in audience:
                out.append({"kind": "unclaimed", "key": f"bring:{b['id']}", "label": b["label"], "what": f'"{b["label"]}" on the bring list, unclaimed',
                            "waiting_on": [plan["organizer_id"]], "since": b["updated_at"], "next_step": "someone says they will bring it"})
        return out

    def _nudge_text(self, plan: Row, b: dict[str, Any], member: str, quiet: Any) -> str:
        if b["kind"] == "rsvp":
            return (f"Check-in on {plan['title']}: the day is close and the others are counting heads. Are you coming? Tell Alexa yes, no or "
                    "maybe (and if you bring anyone).")
        if b["kind"] == "unclaimed":
            return (f"Check-in on {plan['title']}: nobody is bringing {b['label']} yet, and the day is close. You could ask Alexa to tell "
                    "everyone, or take it yourself.")
        return super()._nudge_text(plan, b, member, quiet)  # type: ignore[misc]

    def _run_followup_action(self, actor_id: str, tool: str, args: dict[str, Any], question: str, label: str) -> dict[str, Any]:
        if tool == "rsvp":
            return self.rsvp(actor_id, str(args.get("answer") or ""), plan=args.get("plan"), plus_ones=args.get("plus_ones"))
        if tool == "bring_list":
            return self.bring_list(actor_id, str(args.get("action") or ""), args.get("things"), plan=args.get("plan"))
        return super()._run_followup_action(actor_id, tool, args, question, label)  # type: ignore[misc]
