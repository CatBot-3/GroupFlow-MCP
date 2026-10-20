"""Shared costs: who paid what, each person's share, and who should pay whom to settle up.

Each expense has a payer, an amount in the plan's currency and the people it is split among, equally, with a guest someone brings
counting as part of that person's share. Without a list, it is split among everyone in the plan except people who said they are not
coming.

The person who handled the money confirms it: the payer for an expense, the receiver for a repayment. So "I paid 60 for the cake" counts at
once, while "Bob paid for the pizza" waits until Bob says it is right, and "I paid Alice back" waits until Alice confirms she got it.
Nothing is ever paid through GroupFlow; it keeps the list, works out the balances and reminds people after the day.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date, datetime, time, timedelta
from typing import Any

from groupflow.core.errors import DomainError

from .core import ASSISTANT, Row, Scope, _names
from .momentum import _dt
from .text import money

EXPENSES_MAX = 200  # per plan
AMOUNT_MAX = 100_000_00  # minor units, per expense
WHAT_MAX = 80
SETTLE_WINDOW = timedelta(days=30)  # after the day, check-ins keep asking people to settle for this long
UNDATED_AFTER = timedelta(days=3)  # plans without a date: settle-up check-ins start this long after the last expense
EVERYONE = {"everyone", "everybody", "all", "all of us", "the group", "us", "us all", "the whole group"}
COMING = {"coming", "everyone coming", "the people coming", "attendees", "people coming", "guests", "yes"}
CONFIRM = {"yes", "confirm", "right", "that's right", "thats right", "correct", "ok", "okay", "true", "yep", "yeah"}
REJECT = {"no", "reject", "wrong", "not right", "that's wrong", "thats wrong", "incorrect", "nope", "didn't", "didnt"}


def parse_money(value: Any) -> int | None:
    """45, "45", "$45.50", "1,200", "12 dollars" -> minor units. None when there is no number."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not isinstance(value, (int, float, Decimal)):
        currency = r"(?:[A-Z]{3}|[$€£])"
        m = re.fullmatch(rf"(?:{currency}\s*)?([+-]?(?:\d{{1,3}}(?:,\d{{3}})+|\d+)(?:\.\d{{1,2}})?)\s*(?:{currency}|dollars?|euros?|pounds?)?", text, re.I)
        if not m:
            return None
        text = m[1].replace(",", "")
    try:
        amount = Decimal(text)
        return int((amount * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP)) if amount.is_finite() else None
    except (InvalidOperation, ValueError, OverflowError):
        return None


def split_equal(total: int, weights: dict[str, int]) -> dict[str, int]:
    """Shares by weight that add up to the total exactly; the leftover cents go to the first people in a fixed order."""
    units = sum(weights.values())
    if units <= 0:
        return {}
    order = sorted(weights)
    shares = {m: total * weights[m] // units for m in order}
    left = total - sum(shares.values())
    for m in order:
        if left <= 0:
            break
        shares[m] += 1
        left -= 1
    return shares


def settle_up(balances: dict[str, int]) -> list[tuple[str, str, int]]:
    """Payments (from, to, amount) that bring every balance to zero: the largest debt paid to the largest credit first. At most n-1 payments."""
    debt = sorted(((-b, m) for m, b in balances.items() if b < 0), key=lambda x: (-x[0], x[1]))
    credit = sorted(((b, m) for m, b in balances.items() if b > 0), key=lambda x: (-x[0], x[1]))
    out: list[tuple[str, str, int]] = []
    i = j = 0
    debt_l = [list(x) for x in debt]
    credit_l = [list(x) for x in credit]
    while i < len(debt_l) and j < len(credit_l):
        pay = min(debt_l[i][0], credit_l[j][0])
        if pay > 0:
            out.append((debt_l[i][1], credit_l[j][1], pay))
        debt_l[i][0] -= pay
        credit_l[j][0] -= pay
        if debt_l[i][0] == 0:
            i += 1
        if credit_l[j][0] == 0:
            j += 1
    return out


class Costs:
    """Mixed into GroupFlow above Momentum."""

    # ================================================================ state

    def _money(self, plan: Row, minor: int) -> str:
        return money(minor, plan["currency"])

    def _expenses(self, plan: Row, statuses: tuple[str, ...] = ("active",)) -> list[Row]:
        marks = ",".join("?" for _ in statuses)
        return self.db.all(f"SELECT * FROM expenses WHERE plan_id = ? AND status IN ({marks}) ORDER BY created_at, rowid", plan["id"], *statuses)

    def _settlements(self, plan: Row, statuses: tuple[str, ...] = ("active",)) -> list[Row]:
        marks = ",".join("?" for _ in statuses)
        return self.db.all(f"SELECT * FROM settlements WHERE plan_id = ? AND status IN ({marks}) ORDER BY created_at, rowid", plan["id"], *statuses)

    def _balances(self, plan: Row) -> dict[str, int]:
        """Positive: the group owes them. Negative: they owe. Only confirmed entries count."""
        bal: dict[str, int] = {}
        for e in self._expenses(plan):
            bal[e["payer_id"]] = bal.get(e["payer_id"], 0) + e["amount_minor"]
            for m, share in json.loads(e["shares_json"]).items():
                bal[m] = bal.get(m, 0) - share
        for s in self._settlements(plan):
            bal[s["from_id"]] = bal.get(s["from_id"], 0) + s["amount_minor"]
            bal[s["to_id"]] = bal.get(s["to_id"], 0) - s["amount_minor"]
        return bal

    def _costs_view(self, plan: Row, viewer: str | None) -> dict[str, Any] | None:
        rows = self._expenses(plan, ("active", "pending"))
        pend_s = self._settlements(plan, ("pending",))
        if not rows and not pend_s and not self._settlements(plan):
            return None

        def nm(m: str | None) -> str:
            return "you" if m == viewer else self.name(m)

        bal = self._balances(plan)
        total = sum(e["amount_minor"] for e in rows if e["status"] == "active")
        expenses = []
        for e in rows[-20:]:
            shares = json.loads(e["shares_json"])
            entry = {"id": e["id"], "what": e["description"], "amount": self._money(plan, e["amount_minor"]), "paid_by": nm(e["payer_id"]),
                     "split_among": [nm(m) for m in shares], "status": "counted" if e["status"] == "active" else f"waiting for {nm(e['payer_id'])} to confirm"}
            if viewer in shares:
                entry["your_share"] = self._money(plan, shares[viewer])
            expenses.append(entry)
        view: dict[str, Any] = {
            "currency": plan["currency"], "total": self._money(plan, total), "expenses": expenses,
            "balances": [{"who": nm(m), "balance": ("is owed " if b > 0 else "owes ") + self._money(plan, abs(b))} for m, b in sorted(bal.items(), key=lambda x: x[1]) if b],
            "settle_up": [f"{nm(f)} {'pay' if f == viewer else 'pays'} {nm(t)} {self._money(plan, a)}" for f, t, a in settle_up(bal)],
        }
        if len(rows) > 20:
            view["earlier_expenses"] = len(rows) - 20
        if pend_s:
            view["pending_repayments"] = [{"id": s["id"], "from": nm(s["from_id"]), "to": nm(s["to_id"]),
                                           "amount": self._money(plan, s["amount_minor"])} for s in pend_s]
            view["repayments_waiting"] = [f"{nm(s['from_id'])} paid {nm(s['to_id'])} {self._money(plan, s['amount_minor'])}, waiting for "
                                          f"{nm(s['to_id'])} to confirm" for s in pend_s]
        if viewer is not None:
            view["your_balance"] = self._balance_text(plan, viewer, bal)
        return view

    def _balance_text(self, plan: Row, member: str, bal: dict[str, int] | None = None) -> str:
        mine = (bal if bal is not None else self._balances(plan)).get(member, 0)
        return "settled" if mine == 0 else (f"you are owed {self._money(plan, mine)}" if mine > 0 else f"you owe {self._money(plan, -mine)}")

    def _default_split(self, plan: Row) -> dict[str, int]:
        """Everyone in the plan except people who said they are not coming; a guest counts in the share of the person who brings them."""
        rsvps = {r["member_id"]: r for r in self.db.all("SELECT * FROM rsvps WHERE plan_id = ?", plan["id"])}
        return {m: 1 + (rsvps[m]["plus_ones"] if m in rsvps else 0) for m in self.plan_audience(plan)
                if not (m in rsvps and rsvps[m]["answer"] == "no")}

    def _split_words(self, plan: Row, weights: dict[str, int], shares: dict[str, int]) -> str:
        if len(shares) == 1:
            return f"all on {self.name(next(iter(shares)))}"
        if len(set(weights.values())) == 1:
            return f"split {len(shares)} ways ({self._money(plan, max(shares.values()))} each)"
        return f"split among {len(shares)} people, guests counted with whoever brings them"

    def _split_for(self, scope: Scope, plan: Row, actor_id: str, split_with: list[str] | str | None) -> dict[str, int]:
        refs = [split_with] if isinstance(split_with, str) else list(split_with or [])
        refs = [str(r).strip() for r in refs if str(r).strip()]
        if not refs:
            return self._default_split(plan)
        audience = set(self.plan_audience(plan))
        weights: dict[str, int] = {}
        for ref in refs:
            low = ref.lower()
            if low in EVERYONE:
                weights.update({m: 1 for m in audience})
            elif low in COMING:
                weights.update(self._default_split(plan))
            elif low in ("me", "myself", "i"):
                weights[actor_id] = 1
            else:
                m = self.resolve_member(scope, ref, plan)["id"]
                if m not in audience:
                    raise DomainError("INVALID_ARGUMENT", f"{self.name(m)} is not in {plan['title']}.")
                weights[m] = 1
        return weights

    def _find_expense(self, plan: Row, ref: str | None, statuses: tuple[str, ...]) -> Row:
        rows = self._expenses(plan, statuses)
        if ref:
            exact = [e for e in rows if e["id"] == ref.strip()]
            if exact:
                return exact[0]
            words = set(re.findall(r"\w+", ref.lower())) - {"the", "a", "for", "expense", "cost"}
            scored = [(len(words & set(re.findall(r"\w+", e["description"].lower()))), e) for e in rows]
            best = max((s for s, _ in scored), default=0)
            hits = [e for s, e in scored if s == best and best]
            if len(hits) == 1:
                return hits[0]
            if hits:
                rows = hits
        if len(rows) == 1:
            return rows[0]
        if not rows:
            raise DomainError("NOT_FOUND", "There is no expense like that.")
        raise DomainError("AMBIGUOUS_EXPENSE", "Which one? " + "; ".join(f'{e["description"]} ({self._money(plan, e["amount_minor"])}, id {e["id"]})' for e in rows[:6]))

    # ================================================================ writes

    def costs(self, actor_id: str, action: str, plan: str | None = None, amount: Any = None, what: str | None = None, paid_by: str | None = None,
              split_with: list[str] | str | None = None, expense: str | None = None, to: str | None = None, answer: str | None = None) -> dict[str, Any]:
        """One entry point for the MCP tool: add, remove, confirm, paid (a repayment) or show."""
        self._rate_limit(actor_id)
        if action == "reject":
            action, answer = "confirm", "no"
        if action not in ("add", "remove", "confirm", "paid", "show"):
            raise DomainError("INVALID_ARGUMENT", 'action is "add", "paid" (a repayment), "confirm", "reject", "remove" or "show".')
        if action in ("confirm", "remove") and expense is None and what:
            expense, what = what, None  # the tool names the expense by what it was for, or its id
        try:
            return self.db.tx(lambda: self._costs(actor_id, action, plan, amount, what, paid_by, split_with, expense, to, answer))
        except DomainError as err:
            if ASSISTANT.get() and err.code == "AMBIGUOUS_PLAN" and not plan:
                args = {"action": action, "amount": amount, "what": what, "paid_by": paid_by, "split_with": split_with, "expense": expense, "to": to,
                        "answer": answer}
                self._clarify_plan(actor_id, err, "costs", {k: v for k, v in args.items() if v not in (None, "", [])})  # type: ignore[attr-defined]
            raise

    def _costs(self, actor_id: str, action: str, plan: str | None, amount: Any, what: str | None, paid_by: str | None,
               split_with: list[str] | str | None, expense: str | None, to: str | None, answer: str | None) -> dict[str, Any]:
        scope = self.scope_for(actor_id)
        p = self.resolve_plan(scope, plan, purpose="costs could be shared in")
        me = self.name(actor_id)
        audience = set(self.plan_audience(p))
        if action == "show":
            ledger = self._costs_view(p, actor_id)
            view = self._plan_view(scope, p)
            agreed = view.agreed
            planned = {"version": agreed.label, "your_total": agreed.your_total} if agreed and agreed.your_total is not None else None
            return {"outcome": "shown", "costs": ledger or {"expenses": [], "your_balance": "no expenses recorded"},
                    "planned_cost": planned,
                    "message": "Expense balances cover recorded payments only, not the cost of attending. "
                               + (f"The agreed {planned['version']} costs you {planned['your_total']}. " if planned else "")
                               + "No recorded expenses does not mean the plan is free or paid for. Nothing changed."}
        if p["status"] == "CANCELLED":
            raise DomainError("INVALID_ARGUMENT", f"{p['title']} was cancelled.")

        if action == "add":
            minor = parse_money(amount)
            if minor is None or minor <= 0:
                raise DomainError("INVALID_ARGUMENT", "Say how much, e.g. 60 or 24.50.")
            if minor > AMOUNT_MAX:
                raise DomainError("INVALID_ARGUMENT", f"That is more than {self._money(p, AMOUNT_MAX)} for one expense; check the amount.")
            desc = re.sub(r"\s+", " ", (what or "").strip())
            if not desc:
                raise DomainError("INVALID_ARGUMENT", "Say what it was for.")
            if len(desc) > WHAT_MAX:
                raise DomainError("INVALID_ARGUMENT", f"Keep what it was for under {WHAT_MAX} characters.")
            if (leak := self._note_leak(actor_id, desc, audience - {actor_id})):  # type: ignore[attr-defined]
                raise DomainError("PRIVATE_NOTE", f"That mentions something {self.name(leak)} cannot see, and {self.name(leak)} can see {p['title']}. "
                                  "Record it on the plan it belongs to.")
            if (self.db.scalar("SELECT COUNT(*) FROM expenses WHERE plan_id = ? AND status <> 'removed'", p["id"]) or 0) >= EXPENSES_MAX:
                raise DomainError("RATE_LIMITED", f"{p['title']} already lists {EXPENSES_MAX} expenses.")
            payer = actor_id if not paid_by or paid_by.lower() in ("me", "i", "myself") else self.resolve_member(scope, paid_by, p)["id"]
            if payer not in audience:
                raise DomainError("INVALID_ARGUMENT", f"{self.name(payer)} is not in {p['title']}.")
            weights = self._split_for(scope, p, actor_id, split_with)
            if not weights:
                raise DomainError("INVALID_ARGUMENT", "Nobody to split it with.")
            shares = split_equal(minor, weights)
            status = "active" if payer == actor_id else "pending"
            eid = self.new_id("ex")
            self.db.run("INSERT INTO expenses (id, plan_id, payer_id, amount_minor, currency, description, shares_json, created_by, status, created_at, "
                        "confirmed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", eid, p["id"], payer, minor, p["currency"], desc, json.dumps(shares),
                        actor_id, status, self.ts(), self.ts() if status == "active" else None)
            amt = self._money(p, minor)
            ways = self._split_words(p, weights, shares)
            self._audit(actor_id, "expense.added", "plan", p["id"], p, f"{me} recorded {amt} for {desc}, paid by {self.name(payer)}, {ways}"
                        + ("" if status == "active" else f"; waiting for {self.name(payer)} to confirm") + ".")
            if status == "pending":
                self._notify([payer], p, "costs", f"{me} recorded that you paid {amt} for {desc} ({p['title']}). Is that right? Tell Alexa, or answer "
                             "on your GroupFlow page; it does not count until you confirm.")
                return {"outcome": "pending", "costs": self._costs_view(p, actor_id),
                        "message": f"Recorded, but {self.name(payer)} paid, so it counts once {self.name(payer)} confirms. I asked them."}
            self._tell_sharers(p, actor_id, eid)
            mine = shares.get(actor_id)
            return {"outcome": "added", "costs": self._costs_view(p, actor_id),
                    "message": f"Added {amt} for {desc}, {ways}." + ("" if mine is None else f" Your own share is {self._money(p, mine)}.")}

        if action == "paid":
            minor = parse_money(amount)
            if amount is not None and minor is None:
                raise DomainError("INVALID_ARGUMENT", "Check the amount; give one monetary amount.")
            if not to:
                raise DomainError("INVALID_ARGUMENT", "Say who was paid back.")
            receiver = actor_id if to.lower() in ("me", "i", "myself") else self.resolve_member(scope, to, p)["id"]
            payer = actor_id if not paid_by or paid_by.lower() in ("me", "i", "myself") else self.resolve_member(scope, paid_by, p)["id"]
            if receiver == payer:
                raise DomainError("INVALID_ARGUMENT", "A repayment goes from one person to another.")
            if receiver not in audience or payer not in audience:
                raise DomainError("INVALID_ARGUMENT", "Both people need to be in the plan.")
            if minor is None:  # "I paid Alice back": what I owed her by the suggested settle-up
                minor = next((a for f, t, a in settle_up(self._balances(p)) if f == payer and t == receiver), None)
                if minor is None:
                    raise DomainError("INVALID_ARGUMENT", f"Say how much {self.name(payer)} paid {self.name(receiver)}; the balances do not say "
                                      f"{self.name(payer)} owes {self.name(receiver)} anything.")
            if minor <= 0 or minor > AMOUNT_MAX:
                raise DomainError("INVALID_ARGUMENT", "Check the amount.")
            status = "active" if receiver == actor_id else "pending"
            sid = self.new_id("st")
            self.db.run("INSERT INTO settlements (id, plan_id, from_id, to_id, amount_minor, currency, created_by, status, created_at, confirmed_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", sid, p["id"], payer, receiver, minor, p["currency"], actor_id, status, self.ts(),
                        self.ts() if status == "active" else None)
            amt = self._money(p, minor)
            self._audit(actor_id, "settlement.added", "plan", p["id"], p, f"{me} recorded that {self.name(payer)} paid {self.name(receiver)} {amt}"
                        + ("" if status == "active" else f"; waiting for {self.name(receiver)} to confirm") + ".")
            if status == "pending":
                self._notify([receiver], p, "costs", f"{me} says {'they' if payer == actor_id else self.name(payer)} paid you {amt} for {p['title']}. "
                             "Did you get it? Tell Alexa, or answer on your GroupFlow page.")
                return {"outcome": "pending", "costs": self._costs_view(p, actor_id),
                        "message": f"Recorded. It counts once {self.name(receiver)} confirms they got it; I asked them."}
            others = [m for m in (payer,) if m != actor_id]
            self._notify(others, p, "costs", f"{me} confirmed getting {amt} from you for {p['title']}.")
            return {"outcome": "settled", "costs": self._costs_view(p, actor_id), "message": f"Noted: {self.name(payer)} paid you {amt}."}

        if action == "confirm":
            said = re.sub(r"[^\w\s']", "", (answer or "yes").lower()).strip()
            ok = said in CONFIRM or said.startswith(("yes", "right", "correct"))
            if not ok and not (said in REJECT or said.startswith(("no", "wrong"))):
                raise DomainError("INVALID_ARGUMENT", 'answer is "yes" (it is right) or "no".')
            pending_e = self._expenses(p, ("pending",))
            pending_s = self._settlements(p, ("pending",))
            mine_e = [e for e in pending_e if e["payer_id"] == actor_id]
            mine_s = [s for s in pending_s if s["to_id"] == actor_id]
            if expense:
                candidates = [("expense", e, f"{e['description']} {self.name(e['payer_id'])} {self._money(p, e['amount_minor'])}") for e in pending_e]
                candidates += [("repayment", s, f"{self.name(s['from_id'])} {self._money(p, s['amount_minor'])}") for s in pending_s]
                hits = [c for c in candidates if c[1]["id"] == expense.strip()]
                if not hits:
                    words = set(re.findall(r"\w+", expense.casefold())) - {"the", "a", "from", "for", "payment", "repayment", "expense", "paid", "me"}
                    scored = [(len(words & set(re.findall(r"\w+", c[2].casefold()))), c) for c in candidates]
                    best = max((score for score, _ in scored), default=0)
                    hits = [c for score, c in scored if score == best and best]
                if not hits:
                    raise DomainError("NOT_FOUND", "No pending expense or repayment matches that reference.")
                if len(hits) != 1:
                    raise DomainError("AMBIGUOUS_EXPENSE", "Which payment? " + "; ".join(f"{c[2]} (id {c[1]['id']})" for c in hits[:6]))
                kind, entry, _ = hits[0]
                confirmer = entry["payer_id"] if kind == "expense" else entry["to_id"]
                if confirmer != actor_id:
                    raise DomainError("FORBIDDEN", f"Only {self.name(confirmer)}, who {'paid' if kind == 'expense' else 'received the repayment'}, can confirm it.")
                return self._confirm_expense(p, actor_id, entry, ok) if kind == "expense" else self._confirm_settlement(p, actor_id, entry, ok)
            if len(mine_e) + len(mine_s) == 1:
                return self._confirm_expense(p, actor_id, mine_e[0], ok) if mine_e else self._confirm_settlement(p, actor_id, mine_s[0], ok)
            if not mine_e and not mine_s:
                raise DomainError("NOT_FOUND", "Nothing is waiting for you to confirm.")
            listing = [f'{e["description"]} ({self._money(p, e["amount_minor"])}, id {e["id"]})' for e in mine_e] + \
                      [f'{self._money(p, s["amount_minor"])} from {self.name(s["from_id"])} (id {s["id"]})' for s in mine_s]
            raise DomainError("AMBIGUOUS_EXPENSE", "Which one? " + "; ".join(listing))

        # remove
        e = self._find_expense(p, expense or what, ("active", "pending"))
        if actor_id not in (e["payer_id"], e["created_by"]) and not self.is_organizer(p, actor_id):
            raise DomainError("FORBIDDEN", f"Only {self.name(e['payer_id'])}, who paid, {self.name(e['created_by'])}, who recorded it, or the organizer "
                              "can remove it.")
        self.db.run("UPDATE expenses SET status = 'removed' WHERE id = ?", e["id"])
        amt = self._money(p, e["amount_minor"])
        self._audit(actor_id, "expense.removed", "plan", p["id"], p, f"{me} removed {amt} for {e['description']}.")
        if e["status"] == "active":
            for m in json.loads(e["shares_json"]):
                if m != actor_id and m in audience:
                    self._notify([m], p, "costs", f"{me} removed {amt} for {e['description']} from the costs of {p['title']}; there "
                                 f"{self._balance_text(p, m)}.")
        return {"outcome": "removed", "costs": self._costs_view(p, actor_id), "message": f"Removed {amt} for {e['description']}."}

    def _tell_sharers(self, plan: Row, actor_id: str, expense_id: str) -> None:
        e = self.db.get("SELECT * FROM expenses WHERE id = ?", expense_id)
        shares = json.loads(e["shares_json"])
        amt = self._money(plan, e["amount_minor"])
        by = self.name(e["payer_id"])
        for m, share in shares.items():
            if m in (actor_id, e["payer_id"]) or m not in self.plan_audience(plan):
                continue
            self._notify([m], plan, "costs", f"{by} paid {amt} for {e['description']} ({plan['title']}); your share is {self._money(plan, share)}. "
                         f"In {plan['title']} {self._balance_text(plan, m)}.")

    def _confirm_expense(self, plan: Row, actor_id: str, e: Row, ok: bool) -> dict[str, Any]:
        amt = self._money(plan, e["amount_minor"])
        if ok:
            self.db.run("UPDATE expenses SET status = 'active', confirmed_at = ? WHERE id = ?", self.ts(), e["id"])
            self._audit(actor_id, "expense.confirmed", "plan", plan["id"], plan, f"{self.name(actor_id)} confirmed paying {amt} for {e['description']}.")
            self._tell_sharers(plan, actor_id, e["id"])
            if e["created_by"] != actor_id and e["created_by"] not in json.loads(e["shares_json"]):
                self._notify([e["created_by"]], plan, "costs", f"{self.name(actor_id)} confirmed paying {amt} for {e['description']}.")
            return {"outcome": "confirmed", "costs": self._costs_view(plan, actor_id), "message": f"Confirmed; {amt} for {e['description']} now counts."}
        self.db.run("UPDATE expenses SET status = 'removed' WHERE id = ?", e["id"])
        self._audit(actor_id, "expense.rejected", "plan", plan["id"], plan, f"{self.name(actor_id)} said they did not pay {amt} for {e['description']}.")
        if e["created_by"] != actor_id:
            self._notify([e["created_by"]], plan, "costs", f"{self.name(actor_id)} says they did not pay {amt} for {e['description']}, so it was "
                         "taken off. Record it with whoever did pay.")
        return {"outcome": "rejected", "costs": self._costs_view(plan, actor_id), "message": "Taken off; it never counted."}

    def _confirm_settlement(self, plan: Row, actor_id: str, s: Row, ok: bool) -> dict[str, Any]:
        amt = self._money(plan, s["amount_minor"])
        frm = self.name(s["from_id"])
        if ok:
            self.db.run("UPDATE settlements SET status = 'active', confirmed_at = ? WHERE id = ?", self.ts(), s["id"])
            self._audit(actor_id, "settlement.confirmed", "plan", plan["id"], plan, f"{self.name(actor_id)} confirmed getting {amt} from {frm}.")
            self._notify([s["from_id"]], plan, "costs", f"{self.name(actor_id)} confirmed getting your {amt} for {plan['title']}.")
            return {"outcome": "confirmed", "costs": self._costs_view(plan, actor_id), "message": f"Confirmed: {frm} paid you {amt}."}
        self.db.run("UPDATE settlements SET status = 'removed' WHERE id = ?", s["id"])
        self._audit(actor_id, "settlement.rejected", "plan", plan["id"], plan, f"{self.name(actor_id)} said they did not get {amt} from {frm}.")
        self._notify([s["from_id"]], plan, "costs", f"{self.name(actor_id)} says the {amt} you sent for {plan['title']} has not arrived, so it does "
                     "not count yet. You may want to check with them.")
        return {"outcome": "rejected", "costs": self._costs_view(plan, actor_id), "message": "Noted: not received, so it does not count."}

    # ================================================================ hooks

    def _plan_view(self, scope: Scope, plan: Row) -> Any:
        view = super()._plan_view(scope, plan)  # type: ignore[misc]
        view.costs = self._costs_view(plan, scope.member_id)
        return view

    def _settle_time(self, plan: Row, now: Any, event: Any) -> bool:
        """Settling up is due: after the day (for a month), or for plans without a date, a few days after the last expense."""
        if event is None:
            ext = self.db.get("SELECT lifecycle FROM plan_extensions WHERE plan_id = ?", plan["id"])
            if (ext["lifecycle"] if ext else self.template(plan).lifecycle) != "ongoing" and plan["start_date"]:
                # A one-off plan whose time is not set yet still happens on its day; settling up waits for it.
                event = datetime.combine(date.fromisoformat(plan["start_date"]), time(12, 0), tzinfo=self._tz(plan))  # type: ignore[attr-defined]
        if event is not None:
            return self._event_over(plan, event, now) and now - event <= SETTLE_WINDOW  # type: ignore[attr-defined]
        last = self.db.scalar("SELECT MAX(created_at) FROM expenses WHERE plan_id = ? AND status = 'active'", plan["id"])
        return bool(last) and now - _dt(last) >= UNDATED_AFTER

    def _todos(self, scope: Scope, plans: list[Row]) -> list[Any]:
        from .views import TodoView

        todos = super()._todos(scope, plans)  # type: ignore[misc]
        me = scope.member_id
        for p in plans:
            for e in self._expenses(p, ("pending",)):
                if e["payer_id"] == me:
                    todos.append(TodoView(kind="confirm_cost", plan=p["title"], where="ask Alexa",
                                          text=f"Did you pay {self._money(p, e['amount_minor'])} for {e['description']}? {self.name(e['created_by'])} recorded it."))
            for s in self._settlements(p, ("pending",)):
                if s["to_id"] == me:
                    todos.append(TodoView(kind="confirm_cost", plan=p["title"], where="ask Alexa",
                                          text=f"Did you get {self._money(p, s['amount_minor'])} from {self.name(s['from_id'])}?"))
            now = self._local(p)  # type: ignore[attr-defined]
            if me and self._settle_time(p, now, self._event_time(p, [])):  # type: ignore[attr-defined]
                for f, t, a in settle_up(self._balances(p)):
                    if f == me:
                        todos.append(TodoView(kind="settle", plan=p["title"], where="ask Alexa",
                                              text=f"Pay {self.name(t)} {self._money(p, a)} to settle up, then say you did."))
        return todos

    def _cost_blockers(self, plan: Row, now: Any, event: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        audience = set(self.plan_audience(plan))
        waiting = sorted({e["payer_id"] for e in self._expenses(plan, ("pending",))} | {s["to_id"] for s in self._settlements(plan, ("pending",))})
        waiting = [m for m in waiting if m in audience]
        if waiting:
            since = self.db.scalar("SELECT MIN(created_at) FROM (SELECT created_at FROM expenses WHERE plan_id = ? AND status = 'pending' "
                                   "UNION ALL SELECT created_at FROM settlements WHERE plan_id = ? AND status = 'pending')", plan["id"], plan["id"])
            out.append({"kind": "confirm_cost", "key": f"costs-pending:{plan['id']}", "what": "a check of an expense or repayment recorded for them",
                        "waiting_on": waiting, "since": since or plan["created_at"], "next_step": "each of them says whether it is right"})
        if self._settle_time(plan, now, event):
            debtors = sorted({f for f, _, _ in settle_up(self._balances(plan))} & audience)
            if debtors:
                since = self.db.scalar("SELECT MAX(created_at) FROM expenses WHERE plan_id = ? AND status = 'active'", plan["id"])
                out.append({"kind": "settle", "key": f"settle:{plan['id']}", "what": "settling up the shared costs", "waiting_on": debtors,
                            "since": since or plan["created_at"], "next_step": "each of them pays what the balances say and tells Alexa"})
        return out

    def _blockers(self, plan: Row, now: Any, event: Any) -> list[dict[str, Any]]:
        return super()._blockers(plan, now, event) + self._cost_blockers(plan, now, event)  # type: ignore[misc]

    def _after_event_blockers(self, plan: Row, now: Any, event: Any) -> list[dict[str, Any]]:
        return super()._after_event_blockers(plan, now, event) + self._cost_blockers(plan, now, event)  # type: ignore[misc]

    def _nudge_text(self, plan: Row, b: dict[str, Any], member: str, quiet: Any) -> str:
        if b["kind"] == "settle":
            owed = [(t, a) for f, t, a in settle_up(self._balances(plan)) if f == member]
            what = _names([f"{self.name(t)} {self._money(plan, a)}" for t, a in owed]) or "a little"
            return (f"Check-in on {plan['title']}: the shared costs are not settled yet. By the list you owe {what}. Once you have paid, tell "
                    "Alexa (\"I paid them back\"), and they confirm it.")
        if b["kind"] == "confirm_cost":
            asks = [f"{self.name(e['created_by'])} recorded that you paid {self._money(plan, e['amount_minor'])} for {e['description']}"
                    for e in self._expenses(plan, ("pending",)) if e["payer_id"] == member]
            asks += [f"{self.name(s['created_by'])} recorded that {self.name(s['from_id'])} paid you {self._money(plan, s['amount_minor'])}"
                     for s in self._settlements(plan, ("pending",)) if s["to_id"] == member]
            return (f"Check-in on {plan['title']}: {'; '.join(asks[:2]) or 'a payment involving you is waiting'}. It only counts once you say "
                    "whether that is right; tell Alexa yes or no.")
        return super()._nudge_text(plan, b, member, quiet)  # type: ignore[misc]

    def _run_followup_action(self, actor_id: str, tool: str, args: dict[str, Any], question: str, label: str) -> dict[str, Any]:
        if tool == "costs":
            keep = ("plan", "amount", "what", "paid_by", "split_with", "expense", "to", "answer")
            return self.costs(actor_id, str(args.get("action") or ""), **{k: args[k] for k in keep if k in args})
        return super()._run_followup_action(actor_id, tool, args, question, label)  # type: ignore[misc]
