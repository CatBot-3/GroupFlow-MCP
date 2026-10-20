"""The person's own page for deciding things together: their votes, whether they are coming, what they bring, and the payments waiting for
their word. Every action here is the same call Alexa makes for them, as them; the page adds nothing Alexa could not do, it is just the
place to answer without speaking (and the place a check-in points to)."""
from __future__ import annotations

from typing import Any

from groupflow.core.errors import DomainError


class Together:
    """Mixed into GroupFlow first: reads the votes, RSVP, bring list and cost layers below it."""

    def human_together(self, member: Any) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            scope = self.scope_for(member.id)
            out = []
            for p in scope.plans.values():
                if p["status"] == "CANCELLED":
                    continue
                ext = self.db.get("SELECT lifecycle FROM plan_extensions WHERE plan_id = ?", p["id"])
                ongoing = (ext["lifecycle"] if ext else self.template(p).lifecycle) == "ongoing"
                polls = [dict(v, can_vote=v["status"] == "open" and v["decides"] != "you", can_decide=v["decides"] == "you" and v["status"] in ("open", "closed"))
                         for v in self._polls_in_view(p, member.id) if v["status"] in ("open", "closed")]  # type: ignore[attr-defined]
                rsvp = self._rsvp_view(p, member.id)  # type: ignore[attr-defined]
                # Plans with activities already ask each person for their own confirmation; an RSVP is offered there only once in use.
                # "Coming?" needs something to come to: a plan with a date of its own (a household's running list has none), and no
                # activities, which already ask each person for their own confirmation.
                offer_rsvp = rsvp is not None or (not self._has_activities(p) and not ongoing  # type: ignore[attr-defined]
                                                  and self._event_time(p, []) is not None)  # type: ignore[attr-defined]
                bring = self._bring_view(p, member.id)  # type: ignore[attr-defined]
                costs = self._costs_view(p, member.id)  # type: ignore[attr-defined]
                waiting = [{"id": e["id"], "text": f"{self.name(e['created_by'])} recorded that you paid {self._money(p, e['amount_minor'])} for "  # type: ignore[attr-defined]
                            f"{e['description']}."} for e in self._expenses(p, ("pending",)) if e["payer_id"] == member.id]  # type: ignore[attr-defined]
                waiting += [{"id": s["id"], "text": f"{self.name(s['created_by'])} recorded that {self.name(s['from_id'])} paid you "
                             f"{self._money(p, s['amount_minor'])}."} for s in self._settlements(p, ("pending",)) if s["to_id"] == member.id]  # type: ignore[attr-defined]
                if not (polls or offer_rsvp or bring or costs or waiting):
                    continue
                out.append({"plan_id": p["id"], "title": p["title"], "polls": polls, "rsvp": rsvp, "offer_rsvp": offer_rsvp, "bring_list": bring,
                            "costs": costs, "to_confirm": waiting})
            return {"plans": out}
        return self.db.tx(run)

    def human_inbox(self, member: Any) -> dict[str, Any]:
        result = super().human_inbox(member)  # type: ignore[misc]
        result["together"] = self.human_together(member)["plans"]
        return result

    def human_vote(self, member: Any, poll_id: str, choice: str, reason: str | None = None) -> dict[str, Any]:
        return self.vote(member.id, choice, poll=poll_id, reason=reason)  # type: ignore[attr-defined]

    def human_decide_poll(self, member: Any, poll_id: str, choice: str) -> dict[str, Any]:
        return self.decide_poll(member.id, choice, poll=poll_id)  # type: ignore[attr-defined]

    def human_rsvp(self, member: Any, plan_id: str, answer: str, plus_ones: int | None = None) -> dict[str, Any]:
        return self.rsvp(member.id, answer, plan=plan_id, plus_ones=plus_ones)  # type: ignore[attr-defined]

    def human_bring(self, member: Any, plan_id: str, action: str, things: list[str]) -> dict[str, Any]:
        return self.bring_list(member.id, action, things, plan=plan_id)  # type: ignore[attr-defined]

    def human_confirm_cost(self, member: Any, plan_id: str, entry_id: str, answer: str) -> dict[str, Any]:
        if answer not in ("yes", "no"):
            raise DomainError("INVALID_ARGUMENT", 'answer is "yes" or "no".')
        return self.costs(member.id, "confirm", plan=plan_id, expense=entry_id, answer=answer)  # type: ignore[attr-defined]
