"""The GroupFlow engine, part 2: activities and agreements.

Activities (things people attend together) use the participants policy: a
change only counts once every person taking part has confirmed the exact
version on their own page and the organizer commits it. Revisions are
immutable snapshots; edits produce a draft or a candidate, never a silent
change to what was agreed.

Approvals only come from the human-only API, bound to one scope digest by a
one-time challenge; Alexa can ask for confirmations but never give them. When
a repair or an edit leaves a person's part unchanged, their earlier approval is
carried forward only after re-checking the digest, their current conditions,
their membership and the expiry. The commit gate re-checks everything before a
version takes effect. Conditions people state (a latest home time, a budget, a
diet, a busy time) are private drafts until they confirm them on their page.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from groupflow.core.errors import DomainError
from groupflow.core.util import canonical_json, digest, hash_secret, iso_now, new_id, new_secret, now
from groupflow.repair.domain import build_domain, changed_activities, option_of
from groupflow.repair.fixtures import catalog_from_dict
from groupflow.repair.model import DAY, Activity, Constraint, Problem, Snapshot, Unavailability, hhmm, when_text
from groupflow.repair.solver import judge_option, rejected_alternatives, repair
from groupflow.repair.validate import violations
from groupflow.templates import Template, apply_preset, get_template

from .core import UNSET, Core, Row, Scope, _names, _plural
from .text import dates_text, day_label, money, normalize_slot_key, parse_amount_minor, parse_day, parse_time_of_day, slugify, topic_stems
from .views import (
    ActivityView, AlternativesResult, AlternativeView, ChangeResult, CommitResult, ConditionsResult, ConditionView, ConfirmationRow,
    ConfirmationsResult, ConfirmationsView, CreatePlanResult, ExplainView, ObservationResult, PlanView, ProofView, RejectedOption, TodoView,
    VersionView,
)

log = logging.getLogger("groupflow.agreement")

CHALLENGE_TTL = timedelta(minutes=10)
SESSION_TTL = timedelta(hours=12)
SATISFIED = {"approved", "conditional_met", "carried_forward"}
BLOCKING = {"rejected", "revoked", "conditional_unmet", "expired"}
AGREEMENT_STATES = ("DRAFT", "PROPOSED", "AWAITING_CONFIRMATIONS", "READY_TO_COMMIT", "COMMITTED", "DEGRADED", "REPAIRING", "BLOCKED", "NEEDS_ATTENTION")
STATUS_WORDS_PAGE = {"approved": "approved", "pending": "pending", "rejected": "declined", "conditional_met": "approved with a condition (met)",
                     "conditional_unmet": "condition not met", "revoked": "withdrew", "expired": "expired", "carried_forward": "kept from before"}


# ---------------------------------------------------------------- snapshots

def snapshot_to_dict(s: Snapshot) -> dict[str, Any]:
    return {
        "plan_id": s.plan_id, "start_date": s.start_date, "days": s.days, "timezone": s.timezone, "currency": s.currency,
        "activities": [
            {"id": a.id, "title": a.title, "kind": a.kind, "start": a.start, "end": a.end, "place_id": a.place_id, "place_name": a.place_name,
             "participants": list(a.participants), "cost_shares": dict(a.cost_shares), "required": a.required, "locked": a.locked,
             "cancelled": a.cancelled}
            for a in s.activities
        ],
        "dependencies": [list(d) for d in s.dependencies],
    }


def snapshot_from_dict(d: dict[str, Any]) -> Snapshot:
    acts = tuple(
        Activity(id=a["id"], title=a["title"], kind=a["kind"], start=int(a["start"]), end=int(a["end"]), place_id=a.get("place_id"),
                 place_name=a.get("place_name"), participants=tuple(a["participants"]),
                 cost_shares=tuple(sorted((m, int(v)) for m, v in a["cost_shares"].items())), required=bool(a.get("required", True)),
                 locked=bool(a.get("locked", False)), cancelled=bool(a.get("cancelled", False)))
        for a in d["activities"]
    )
    return Snapshot(plan_id=d["plan_id"], start_date=d["start_date"], days=int(d.get("days", 1)), timezone=d["timezone"], currency=d["currency"],
                    activities=acts, dependencies=tuple(tuple(x) for x in d.get("dependencies", [])))  # type: ignore[arg-type]


def snapshot_json(s: Snapshot) -> str:
    return json.dumps(snapshot_to_dict(s), sort_keys=True)


def content_of(s: Snapshot) -> dict[str, Any]:
    d = snapshot_to_dict(s)
    d.pop("plan_id", None)
    return d


class Agreement(Core):
    solver_budget_s: float = 3.0
    method: str = "auto"

    # ================================================================ basic lookups

    def _revision(self, revision_id: str | None) -> Row | None:
        return self.db.get("SELECT * FROM plan_revisions WHERE id = ?", revision_id) if revision_id else None

    def _snapshot(self, rev: Row) -> Snapshot:
        return snapshot_from_dict(json.loads(rev["snapshot_json"]))

    def _catalog(self, plan: Row):
        return catalog_from_dict(json.loads(plan["catalog_json"] or "{}"))

    def _members_of(self, plan: Row) -> list[Row]:
        """The plan's audience with their group roles (confirmation mode, membership version)."""
        out = []
        for m in self.plan_audience(plan):
            g = self._group_role(plan["group_id"], m) or {"role": "member", "confirmation_mode": "scoped", "membership_version": 1}
            out.append({"id": m, "name": self.name(m), "confirmation_mode": g["confirmation_mode"], "membership_version": g["membership_version"]})
        return out

    def _has_activities(self, plan: Row) -> bool:
        return bool(plan["draft_revision_id"] or plan["candidate_revision_id"] or plan["active_revision_id"])

    def _working_rev(self, plan: Row) -> Row | None:
        """What activity edits apply to: the candidate, or the draft before anything was proposed. None means: start from the agreed version."""
        if plan["candidate_revision_id"]:
            return self._revision(plan["candidate_revision_id"])
        if not plan["active_revision_id"]:
            return self._revision(plan["draft_revision_id"])
        return None

    def _latest_snapshot(self, plan: Row) -> Snapshot | None:
        rev = self._working_rev(plan) or self._revision(plan["active_revision_id"])
        return self._snapshot(rev) if rev else None

    def _round_open(self, rev: Row | None) -> bool:
        return bool(rev) and self.db.get("SELECT 1 AS x FROM confirmation_rounds WHERE revision_id = ? AND status = 'open'", rev["id"]) is not None

    def _multi(self, plan: Row) -> bool:
        return int(plan["days"]) > 1

    def _when(self, plan: Row, a: Activity) -> str:
        if self._multi(plan):
            return f"{when_text(a.start, plan['start_date'], True)}-{hhmm(a.end)}"
        return f"{hhmm(a.start)}-{hhmm(a.end)}"

    def _place_name(self, plan: Row, a: Activity, catalog: Any = None) -> str | None:
        catalog = catalog or self._catalog(plan)
        if a.place_id and a.place_id in catalog.places:
            return catalog.places[a.place_id].name
        return a.place_name

    # ================================================================ conditions

    def _condition_rows(self, plan: Row, owner: str | None = None, statuses: tuple[str, ...] = ("active",)) -> list[Row]:
        marks = ",".join("?" for _ in statuses)
        sql = (f"SELECT * FROM conditions WHERE group_id = ? AND (plan_id = ? OR plan_id IS NULL) AND status IN ({marks})"
               + (" AND owner_id = ?" if owner else "") + " ORDER BY created_at, id")
        params: list[Any] = [plan["group_id"], plan["id"], *statuses] + ([owner] if owner else [])
        return [r for r in self.db.all(sql, *params) if self._in_space(r["owner_id"], plan["space_id"])]

    def _condition_text(self, r: Row, plan: Row | None = None) -> str:
        v = json.loads(r["value_json"])
        label = v.get("label") or r["name"]
        k = r["kind"]
        day = f" on {v['day']}" if v.get("day") not in (None, "") else ""
        if k == "latest_end":
            return f"{label.capitalize()}: {v.get('time')}{day}"
        if k == "earliest_start":
            return f"{label.capitalize()}: {v.get('time')}{day}"
        if k == "max_total_cost":
            return f"{label.capitalize()}: at most {money(int(v.get('amount_minor', 0)), plan['currency'] if plan else 'CAD')} in total"
        if k == "needs":
            return f"{label.capitalize()}: " + " and ".join(str(t).replace("_", "-") for t in v.get("tags", []))
        if k == "unavailable_window":
            return f"{label.capitalize()}{day}: {v.get('from')} to {v.get('to')}"
        if k == "must_attend":
            return f"Everyone attends {v.get('activity_title') or v.get('activity_id')}"
        if k == "prefer_keep":
            return f"Prefer to keep {v.get('activity_title') or v.get('activity_id')} as planned"
        return f"{label}: {json.dumps(v)}"

    def _condition_view(self, r: Row) -> ConditionView:
        plan = self._plan_row(r["plan_id"]) if r["plan_id"] else None
        return ConditionView(id=r["id"], plan=plan["title"] if plan else "all plans in the group", name=r["name"],
                             description=self._condition_text(r, plan), status=r["status"], private=r["visibility"] == "private")

    def _as_solver_constraints(self, plan: Row) -> tuple[Constraint, ...]:
        out = []
        for r in self._condition_rows(plan):
            v = dict(json.loads(r["value_json"]))
            if r["kind"] in ("latest_end", "earliest_start") and v.get("day") not in (None, ""):
                idx = parse_day(v["day"], plan["start_date"], plan["days"])
                if idx is None:
                    continue
                v["day"] = idx
            elif r["kind"] in ("latest_end", "earliest_start"):
                v.pop("day", None)
            if r["kind"] == "unavailable_window":
                lo, hi = parse_time_of_day(v.get("from")), parse_time_of_day(v.get("to"))
                if lo is None or hi is None:
                    continue
                if v.get("day") not in (None, ""):
                    idx = parse_day(v["day"], plan["start_date"], plan["days"])
                    days = [idx] if idx is not None else []
                else:
                    days = list(range(plan["days"]))
                v = {"windows": [[d * DAY + lo, d * DAY + hi] for d in days], "label": v.get("label")}
            out.append(Constraint.make(id=r["id"], owner=r["owner_id"], kind=r["kind"], value=v, strength=r["strength"], visibility=r["visibility"]))
        return tuple(out)

    def fingerprint(self, plan: Row, member_id: str) -> str:
        """What a member's approval silently depends on: their active conditions and membership."""
        cons = [(r["id"], r["version"]) for r in self._condition_rows(plan, member_id)]
        g = self._group_role(plan["group_id"], member_id)
        return digest({"conditions": cons, "membership": g["membership_version"] if g else None, "in_space": self._in_space(member_id, plan["space_id"])})

    def _input_versions(self, plan: Row) -> dict[str, Any]:
        cons = [(r["id"], r["version"]) for r in self._condition_rows(plan)]
        mems = [(m["id"], m["membership_version"], m["confirmation_mode"]) for m in self._members_of(plan)]
        obs = [r["id"] for r in self.db.all("SELECT id FROM observations WHERE group_id = ? AND verification = 'verified' ORDER BY id", plan["group_id"])]
        return {"conditions": digest(cons), "memberships": digest(mems), "observations": digest(obs), "catalog": digest(plan["catalog_json"])}

    # ================================================================ problem building

    def _unavailable(self, plan: Row) -> tuple[Unavailability, ...]:
        out = []
        start = date.fromisoformat(plan["start_date"])
        for e in self.db.all("SELECT * FROM observations WHERE group_id = ? AND verification = 'verified' AND kind = 'place_unavailable'", plan["group_id"]):
            p = json.loads(e["payload_json"])
            lo, hi = parse_time_of_day(p.get("from", "00:00")) or 0, parse_time_of_day(p.get("to", "23:59")) or (DAY - 1)
            if p.get("date"):
                d = (date.fromisoformat(p["date"]) - start).days
                if not 0 <= d < plan["days"]:
                    continue
                days = [d]
            else:
                days = list(range(plan["days"]))
            for d in days:
                out.append(Unavailability(place_id=p["place_id"], from_min=d * DAY + lo, to_min=d * DAY + hi, event_id=e["id"]))
        return tuple(out)

    def _problem(self, plan: Row, base: Snapshot) -> Problem:
        t = self.template(plan)
        members = self._members_of(plan)
        return Problem(base=base, catalog=self._catalog(plan), constraints=self._as_solver_constraints(plan), unavailable=self._unavailable(plan),
                       confirmation_mode={m["id"]: m["confirmation_mode"] for m in members}, names={m["id"]: m["name"] for m in members},
                       shifts=tuple(t.search.time_shifts), max_options=t.search.max_options)

    def _public_problems(self, plan: Row, snap: Snapshot) -> list[str]:
        return list(dict.fromkeys(v.public for v in violations(self._problem(plan, snap), snap)))

    # ================================================================ scopes and approvals

    def _scopes_for(self, revision: Row) -> list[Row]:
        """Create (once) and return the approval scopes of a revision: one per member per activity they take part in."""
        existing = self.db.all("SELECT * FROM approval_scopes WHERE revision_id = ? ORDER BY rowid", revision["id"])
        if existing:
            return existing
        plan = self._plan_row(revision["plan_id"])
        snap = self._snapshot(revision)
        catalog = self._catalog(plan)
        content = digest(content_of(snap))
        for m in self._members_of(plan):
            if m["id"] not in snap.members:
                continue
            if m["confirmation_mode"] == "whole_plan":
                canonical = {"member": m["id"], "mode": "whole_plan", "plan_id": plan["id"], "content_digest": content}
                self.db.run("INSERT INTO approval_scopes (id, revision_id, member_id, scope_kind, activity_id, canonical_json, scope_digest) "
                            "VALUES (?, ?, ?, 'whole_plan', '', ?, ?)", new_id("sc"), revision["id"], m["id"], canonical_json(canonical), digest(canonical))
                continue
            for a in snap.activities:
                if m["id"] not in a.participants:
                    continue
                canonical = {
                    "member": m["id"], "plan_id": plan["id"], "start_date": snap.start_date, "activity_id": a.id, "title": a.title,
                    "status": "cancelled" if a.cancelled else "planned", "start": a.start, "end": a.end, "place_id": a.place_id,
                    "place_name": self._place_name(plan, a, catalog), "cost_share_minor": a.share(m["id"]), "currency": snap.currency,
                    "participants": sorted(a.participants), "guards": ["arrive_in_time", "within_my_budget", "fits_my_conditions"],
                }
                self.db.run("INSERT INTO approval_scopes (id, revision_id, member_id, scope_kind, activity_id, canonical_json, scope_digest) "
                            "VALUES (?, ?, ?, 'activity', ?, ?, ?)", new_id("sc"), revision["id"], m["id"], a.id, canonical_json(canonical), digest(canonical))
        return self.db.all("SELECT * FROM approval_scopes WHERE revision_id = ? ORDER BY rowid", revision["id"])

    def _scope_text(self, scope_row: Row) -> str:
        c = json.loads(scope_row["canonical_json"])
        rev = self._revision(scope_row["revision_id"])
        plan = self._plan_row(rev["plan_id"])
        snap = self._snapshot(rev)
        if scope_row["scope_kind"] == "whole_plan":
            parts = [f"{a.title} {when_text(a.start, plan['start_date'], self._multi(plan))}" for a in snap.live]
            return (f"The whole of {plan['title']} ({dates_text(plan['start_date'], plan['days'])}): {', '.join(parts)}. "
                    f"Your total: {money(snap.member_total(scope_row['member_id']), snap.currency)}.")
        names = _names([self.name(p) for p in c["participants"]])
        day = day_label(plan["start_date"], c["start"] // DAY)
        if c["status"] == "cancelled":
            return f"{c['title']} on {day} is dropped from {plan['title']}."
        where = f" at {c['place_name']}" if c.get("place_name") else ""
        return (f"{plan['title']}: {c['title']} on {day}, {hhmm(c['start'])} to {hhmm(c['end'])}{where}. "
                f"Your share: {money(c['cost_share_minor'], c['currency'])}. With {names}.")

    def _expires_at(self, plan: Row) -> str:
        tz = ZoneInfo(plan["timezone"])
        last = date.fromisoformat(plan["start_date"]) + timedelta(days=max(1, plan["days"]) - 1)
        end_local = datetime.fromisoformat(last.isoformat() + "T23:59:00").replace(tzinfo=tz)
        return end_local.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def _receipt_state(self, receipt: Row, snap: Snapshot) -> str:
        if receipt["status"] in ("revoked", "invalidated"):
            return receipt["status"]
        if receipt["status"] == "expired" or receipt["expires_at"] < iso_now():
            return "expired"
        if receipt["decision"] in ("approved", "rejected"):
            return receipt["decision"]
        cond = json.loads(receipt["condition_json"] or "{}")
        return "conditional_met" if self._condition_holds(cond, receipt["member_id"], snap) else "conditional_unmet"

    @staticmethod
    def _condition_holds(cond: dict[str, Any], member_id: str, snap: Snapshot) -> bool:
        if "max_total_minor" in cond and snap.member_total(member_id) > int(cond["max_total_minor"]):
            return False
        if "latest_end" in cond:
            limit = parse_time_of_day(cond["latest_end"])
            for a in snap.live:
                if member_id in a.participants and limit is not None and a.end > a.day * DAY + limit:
                    return False
        return True

    def _scope_status(self, scope_row: Row, snap: Snapshot) -> tuple[str, str | None]:
        receipt = self.db.get("SELECT * FROM approval_receipts WHERE scope_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1", scope_row["id"])
        if receipt:
            return self._receipt_state(receipt, snap), "receipt"
        cf = self.db.get("SELECT cf.*, r.status AS r_status, r.expires_at AS r_expires FROM approval_carry_forwards cf "
                         "JOIN approval_receipts r ON r.id = cf.receipt_id WHERE cf.scope_id = ? ORDER BY cf.created_at DESC LIMIT 1", scope_row["id"])
        if cf:
            plan = self.db.get("SELECT p.* FROM plans p JOIN plan_revisions r ON r.plan_id = p.id WHERE r.id = ?", scope_row["revision_id"])
            if cf["r_status"] == "valid" and cf["r_expires"] >= iso_now() and cf["dependency_fingerprint"] == self.fingerprint(plan, scope_row["member_id"]):
                return "carried_forward", "carry_forward"
        return "pending", None

    def _requirements(self, revision: Row) -> list[tuple[Row, str, str | None]]:
        snap = self._snapshot(revision)
        return [(s, *self._scope_status(s, snap)) for s in self._scopes_for(revision)]

    def _confirmations(self, revision: Row) -> ConfirmationsView:
        reqs = self._requirements(revision)
        snap = self._snapshot(revision)
        titles = {a.id: a.title for a in snap.activities}
        rows: dict[str, ConfirmationRow] = {}
        for scope_row, status, _ in reqs:
            row = rows.setdefault(scope_row["member_id"], ConfirmationRow(person=self.name(scope_row["member_id"]), done=True, items={}))
            title = titles.get(scope_row["activity_id"], "Whole plan") if scope_row["scope_kind"] == "activity" else "Whole plan"
            row.items[title] = status
            if status not in SATISFIED:
                row.done = False
        satisfied = sum(1 for _, st, _ in reqs if st in SATISFIED)
        return ConfirmationsView(version=revision["label"], required=len(reqs), satisfied=satisfied, complete=bool(reqs) and satisfied == len(reqs),
                                 rows=list(rows.values()))

    def _carry_forward(self, plan: Row, target: Row) -> int:
        """Record proof that unchanged scopes keep an earlier approval: from the agreed version, or from the version this one replaced."""
        sources = [r for r in (self._revision(plan["active_revision_id"]),
                               self.db.get("SELECT * FROM plan_revisions WHERE plan_id = ? AND kind = 'discarded' AND id <> ? AND label <> 'Draft' "
                                           "ORDER BY created_at DESC, rowid DESC LIMIT 1", plan["id"], target["id"])) if r and r["id"] != target["id"]]
        if not sources:
            return 0
        target_snap = self._snapshot(target)
        feasible = not violations(self._problem(plan, target_snap), target_snap)
        carried = 0
        for base in sources:
            base_scopes = {s["scope_digest"]: s for s in self._scopes_for(base)}
            for scope_row in self._scopes_for(target):
                src = base_scopes.get(scope_row["scope_digest"])
                if not src or src["member_id"] != scope_row["member_id"]:
                    continue
                if self.db.get("SELECT 1 AS x FROM approval_carry_forwards WHERE scope_id = ?", scope_row["id"]):
                    continue
                receipt = self._approving_receipt(src)
                if not receipt:
                    continue
                current_fp = self.fingerprint(plan, scope_row["member_id"])
                check = {"digest_equal": True, "fingerprint_equal": receipt["fingerprint"] == current_fp, "hard_constraints_ok": feasible,
                         "not_expired": receipt["expires_at"] >= iso_now(), "source_revision": base["label"], "source_receipt": receipt["id"]}
                if not all((check["fingerprint_equal"], check["hard_constraints_ok"], check["not_expired"])):
                    continue
                self.db.run("INSERT OR IGNORE INTO approval_carry_forwards (id, receipt_id, target_revision_id, scope_id, dependency_fingerprint, check_json, created_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)", new_id("cf"), receipt["id"], target["id"], scope_row["id"], current_fp, json.dumps(check), iso_now())
                carried += 1
        return carried

    def _approving_receipt(self, scope_row: Row) -> Row | None:
        r = self.db.get("SELECT * FROM approval_receipts WHERE scope_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1", scope_row["id"])
        if r:
            return r if r["status"] == "valid" and r["decision"] == "approved" and r["expires_at"] >= iso_now() else None
        cf = self.db.get("SELECT r.* FROM approval_carry_forwards cf JOIN approval_receipts r ON r.id = cf.receipt_id "
                         "WHERE cf.scope_id = ? ORDER BY cf.created_at DESC LIMIT 1", scope_row["id"])
        if cf and cf["status"] == "valid" and cf["decision"] == "approved" and cf["expires_at"] >= iso_now():
            return cf
        return None

    # ================================================================ revisions

    def _next_label(self, plan: Row) -> str:
        n = self.db.scalar("SELECT COUNT(*) FROM plan_revisions WHERE plan_id = ? AND kind IN ('committed', 'superseded')", plan["id"]) or 0
        return f"P{n + 1}"

    def _new_revision(self, plan: Row, snap: Snapshot, label: str, kind: str, source: str, by: str | None, parent: str | None,
                      job_id: str | None = None, alternate: bool = False) -> Row:
        rid = new_id("rev")
        self.db.run("INSERT INTO plan_revisions (id, plan_id, parent_id, label, kind, source, snapshot_json, content_digest, input_versions, repair_job_id, "
                    "alternate, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rid, plan["id"], parent, label, kind, source,
                    snapshot_json(snap), digest(content_of(snap)), json.dumps(self._input_versions(plan)), job_id, 1 if alternate else 0, by, iso_now())
        return self._revision(rid)  # type: ignore[return-value]

    def _supersede_candidate(self, plan: Row) -> None:
        if plan["candidate_revision_id"]:
            self.db.run("UPDATE plan_revisions SET kind = 'discarded' WHERE id = ? AND kind = 'candidate'", plan["candidate_revision_id"])
            self.db.run("UPDATE confirmation_rounds SET status = 'superseded', closed_at = ? WHERE revision_id = ? AND status = 'open'",
                        iso_now(), plan["candidate_revision_id"])

    def _empty_snapshot(self, plan: Row) -> Snapshot:
        return Snapshot(plan_id=plan["id"], start_date=plan["start_date"], days=plan["days"], timezone=plan["timezone"], currency=plan["currency"], activities=())

    def _edit(self, plan: Row, snap: Snapshot, by: str) -> tuple[Row, str, Row]:
        """Store an edited version of the activities. Returns (plan, outcome, revision)."""
        snap = replace(snap, activities=tuple(sorted(snap.activities, key=lambda a: (a.start, a.id))))
        if not plan["active_revision_id"] and not plan["candidate_revision_id"]:
            old = self._revision(plan["draft_revision_id"])
            rev = self._new_revision(plan, snap, "Draft", "draft", "draft", by, old["id"] if old else None)
            if old:
                self.db.run("UPDATE plan_revisions SET kind = 'discarded' WHERE id = ?", old["id"])
            plan = self._bump(plan["id"], "DRAFT", None, draft_revision_id=rev["id"])
            return plan, "draft_updated", rev
        cand = self._revision(plan["candidate_revision_id"])
        if cand and not self._round_open(cand):
            rev = self._new_revision(plan, snap, cand["label"], "candidate", "edit", by, cand["parent_id"])
            self.db.run("UPDATE plan_revisions SET kind = 'discarded' WHERE id = ?", cand["id"])
            plan = self._bump(plan["id"], "PROPOSED", "changed since it was last sent" if plan["active_revision_id"] else None, candidate_revision_id=rev["id"])
            return plan, "candidate_updated", rev
        parent = plan["active_revision_id"] or (cand["parent_id"] if cand else plan["draft_revision_id"])
        label = self._next_label(plan) if plan["active_revision_id"] else (cand["label"] if cand else "P1")
        self._supersede_candidate(plan)
        rev = self._new_revision(plan, snap, label, "candidate", "edit", by, parent)
        plan = self._bump(plan["id"], "PROPOSED", "a change to the agreed plan" if plan["active_revision_id"] else None, candidate_revision_id=rev["id"])
        return plan, "candidate_created", rev

    # ================================================================ views

    def _activity_view(self, plan: Row, a: Activity, viewer: str | None, audience: list[str], catalog: Any) -> ActivityView:
        place = catalog.places.get(a.place_id or "")
        participants = "everyone" if set(a.participants) >= set(audience) else _names([self.name(p) for p in a.participants])
        return ActivityView(id=a.id, title=a.title, when=self._when(plan, a), place=self._place_name(plan, a, catalog), participants=participants,
                            cost_each=money(place.price_minor, plan["currency"]) if place and place.price_minor else None,
                            your_cost=money(a.share(viewer), plan["currency"]) if viewer and viewer in a.participants and a.share(viewer) else None,
                            status="dropped" if a.cancelled else "planned")

    def _version_view(self, plan: Row, rev: Row, viewer: str | None, compare: Snapshot | None) -> VersionView:
        snap = self._snapshot(rev)
        catalog = self._catalog(plan)
        audience = self.plan_audience(plan)
        kind = "draft" if rev["kind"] == "draft" else "committed" if rev["kind"] == "committed" else "candidate"
        total = snap.member_total(viewer) if viewer and viewer in snap.members else None
        return VersionView(label=rev["label"], kind=kind, activities=[self._activity_view(plan, a, viewer, audience, catalog) for a in snap.activities],
                           your_total=money(total, plan["currency"]) if total else None,
                           changes=self._diff(plan, compare, snap) if compare is not None else [],
                           changed_activity_ids=sorted(changed_activities(compare, snap)) if compare is not None else [])

    def _diff(self, plan: Row, before: Snapshot | None, after: Snapshot) -> list[str]:
        if before is None:
            return []
        catalog = self._catalog(plan)
        out = []
        b_acts = {a.id: a for a in before.activities}
        a_ids = {a.id for a in after.activities}
        for a in after.activities:
            b = b_acts.get(a.id)
            where = self._place_name(plan, a, catalog)
            if b is None:
                out.append(f"{a.title}: added ({self._when(plan, a)}{' at ' + where if where else ''})")
                continue
            if a.cancelled and not b.cancelled:
                out.append(f"{a.title}: dropped")
                continue
            if b.cancelled and not a.cancelled:
                out.append(f"{a.title}: back in the plan ({self._when(plan, a)})")
                continue
            if a.title != b.title:
                out.append(f"{b.title}: renamed to {a.title}")
            if a.where != b.where:
                out.append(f"{a.title}: {self._place_name(plan, b, catalog) or 'no place'} -> {where or 'no place'}")
            if (a.start, a.end) != (b.start, b.end):
                out.append(f"{a.title}: {self._when(plan, b)} -> {self._when(plan, a)}")
            if a.cost_shares != b.cost_shares and a.participants == b.participants:
                bc = sorted({v for _, v in b.cost_shares})
                ac = sorted({v for _, v in a.cost_shares})
                out.append(f"{a.title}: cost {' / '.join(money(x, after.currency) for x in bc)} -> {' / '.join(money(x, after.currency) for x in ac)} each")
            if a.participants != b.participants:
                out.append(f"{a.title}: with {_names([self.name(p) for p in b.participants])} -> with {_names([self.name(p) for p in a.participants])}")
        for b in before.activities:
            if b.id not in a_ids:
                out.append(f"{b.title}: removed")
        return out

    def _agreement_view(self, scope: Scope, plan: Row, view: PlanView) -> None:
        if not self._has_activities(plan):
            if self.template(plan).has_activities and plan["status"] == "ACTIVE":
                view.next_step = "No activities yet. The organizer can add some."
            return
        viewer = scope.member_id
        active = self._revision(plan["active_revision_id"])
        working = self._working_rev(plan)
        active_snap = self._snapshot(active) if active else None
        if active:
            view.agreed = self._version_view(plan, active, viewer, None)
            counts = self._confirmations(active)
            carried = sum(1 for _, status, _ in self._requirements(active) if status == 'carried_forward')
            parent = self._revision(active['parent_id']) if active['parent_id'] else None
            before = self._snapshot(parent) if parent and parent['plan_id'] == plan['id'] else None
            view.agreement_summary = {'version': active['label'], 'required': counts.required, 'satisfied': counts.satisfied,
                                      'carried_forward': carried, 'changes': self._diff(plan, before, active_snap) if before else [],
                                      'changed_activity_ids': sorted(changed_activities(before, active_snap)) if before else []}
        if working:
            compare = active_snap
            if compare is None and working["kind"] != "draft" and plan["draft_revision_id"] and working["source"] == "repair":
                compare = self._snapshot(self._revision(plan["draft_revision_id"]))
            view.working = self._version_view(plan, working, viewer, compare)
            if working["kind"] == "candidate" and self._round_open(working):
                view.confirmations = self._confirmations(working)
        view.observations = [self._observation_text(o) for o in self.db.all(
            "SELECT * FROM observations WHERE group_id = ? AND (plan_id IS NULL OR plan_id = ?) ORDER BY received_at DESC LIMIT 4", plan["group_id"], plan["id"])]
        view.bookings = [f'{e["action"]} ({e["status"]}, simulated, ref {e["provider_reference"]})' for e in self.db.all(
            "SELECT * FROM executions WHERE plan_id = ? ORDER BY created_at", plan["id"])]
        view.next_step = self._next_step(plan, viewer)

    def _observation_text(self, o: Row) -> str:
        p = json.loads(o["payload_json"])
        return f'{p.get("summary") or o["kind"]} ({o["verification"]})'

    def _todos(self, scope: Scope, plans: list[Row]) -> list[TodoView]:
        todos: list[TodoView] = []
        me = scope.member_id
        for plan in plans:
            for r in self._condition_rows(plan, me, ("unverified",)):
                if r["plan_id"] == plan["id"] or r["plan_id"] is None:
                    # Other listeners hear that something waits on the speaker's page, never what the condition says.
                    text = ("Check and confirm a new private condition." if scope.listeners
                            else f"Check and confirm your new condition: {self._condition_text(r, plan)}.")
                    todos.append(TodoView(kind="verify_condition", plan=plan["title"], where="your GroupFlow page", text=text))
            cand = self._revision(plan["candidate_revision_id"])
            if cand and self._round_open(cand) and plan["status"] in ("AWAITING_CONFIRMATIONS", "READY_TO_COMMIT"):
                for scope_row, status, _ in self._requirements(cand):
                    if scope_row["member_id"] == me and status == "pending":
                        todos.append(TodoView(kind="confirm", plan=plan["title"], where="your GroupFlow page",
                                              text=f"Confirm or decline: {self._scope_text(scope_row)}"))
            if self.is_organizer(plan, me):
                if plan["status"] == "READY_TO_COMMIT":
                    todos.append(TodoView(kind="commit", plan=plan["title"], where="ask Alexa",
                                          text=f"Everyone needed has confirmed {cand['label'] if cand else 'the plan'}; it can be committed."))
                elif plan["status"] in ("DEGRADED", "BLOCKED", "NEEDS_ATTENTION"):
                    todos.append(TodoView(kind="repair", plan=plan["title"], where="ask Alexa", text=f"{plan['title']} no longer works; ask for alternatives."))
                elif plan["status"] == "PROPOSED":
                    todos.append(TodoView(kind="send", plan=plan["title"], where="ask Alexa",
                                          text=f"{cand['label'] if cand else 'A new version'} is ready to send out for confirmation."))
        return todos

    def _my_conditions(self, scope: Scope, plans: list[Row]) -> list[ConditionView]:
        out, seen = [], set()
        for plan in plans:
            for r in self._condition_rows(plan, scope.member_id, ("active", "unverified")):
                if r["id"] not in seen:
                    seen.add(r["id"])
                    out.append(self._condition_view(r))
        return out

    def _next_step(self, plan: Row, member_id: str | None) -> str:
        st = plan["status"]
        org = self.name(plan["organizer_id"])
        cand = self._revision(plan["candidate_revision_id"])
        if st == "DRAFT":
            return f"Draft only; nothing is agreed. {org} can send it out with request_confirmations."
        if st == "PROPOSED":
            return f"{cand['label'] if cand else 'A new version'} is not sent yet. {org} can ask everyone affected to confirm with request_confirmations."
        if st == "AWAITING_CONFIRMATIONS" and cand:
            view = self._confirmations(cand)
            waiting = [r.person for r in view.rows if not r.done]
            blocked = [r.person for r in view.rows for v in r.items.values() if v in BLOCKING]
            if blocked:
                return f"{_names(blocked)} declined or set a condition that is not met. {org} can ask for alternatives with find_alternatives."
            return f"Waiting for {_names(waiting)} to confirm {cand['label']} on their own GroupFlow page. Nobody can confirm for someone else."
        if st == "READY_TO_COMMIT":
            return f"Everyone needed has confirmed. {org} can commit {cand['label'] if cand else 'it'} with commit_plan."
        if st == "COMMITTED":
            return "Agreed and in effect."
        if st == "DEGRADED":
            reason = plan["status_reason"] or "something changed."
            parts = [reason if cand and reason.startswith(cand["label"] + " ") else f"The agreed plan no longer works: {reason}"]
            if cand and self._round_open(cand):
                view = self._confirmations(cand)
                blocked = [r.person for r in view.rows for v in r.items.values() if v in BLOCKING]
                if blocked:
                    parts.append(f"{_names(list(dict.fromkeys(blocked)))} declined or set a condition that is not met.")
            parts.append(f"{org} can ask for alternatives with find_alternatives.")
            return " ".join(parts)
        if st == "REPAIRING":
            return "Alternatives are being computed. Check with get_alternatives."
        if st == "BLOCKED":
            return "No option in the current choices satisfies everyone's hard conditions. Someone would need to relax a condition, or new options are needed."
        if st == "NEEDS_ATTENTION":
            return "The search could not finish or needs more facts. Try again."
        return st

    # ================================================================ create_plan

    def create_plan(self, actor_id: str, template: str, title: str, group: str | None = None, start_date: str | None = None,
                    days: int | None = None, hidden_from: list[str] | None = None, audience: list[str] | None = None, starter: bool = True,
                    organizer: str | None = None, plan_id: str | None = None, private: bool = False,
                    preset: str | None = None, lifecycle: str | None = None, quote: str = '') -> CreatePlanResult:
        self._rate_limit(actor_id)
        if (quote and not private and not audience and re.search(r'\b(?:just|only)\s+with\b', quote, re.I)
                and not re.search(r'\bnot\s+(?:just|only)\s+with\b', quote, re.I)):
            raise DomainError('AUDIENCE_REQUIRED', 'This request explicitly limits who may see the plan. Pass audience with those named people; the speaker is included automatically. Activity participants do not restrict plan readers. No plan was created.')
        templates = self.templates()
        t = templates.get((template or "").strip()) or next((x for x in templates.values() if x.name.lower() == (template or "").strip().lower()), None)
        if t is None:
            try:
                t = get_template(template)
            except KeyError:
                pass
        if t is None:
            raise DomainError("INVALID_ARGUMENT", f"Unknown template {template!r}. Templates: " + ", ".join(f"{x.id} ({x.name})" for x in templates.values()))
        try:
            t = apply_preset(t, preset)
        except ValueError as err:
            raise DomainError("INVALID_ARGUMENT", str(err)) from err
        if preset == "surprise" and not hidden_from:
            raise DomainError("INVALID_ARGUMENT", "The surprise preset requires explicit hidden_from. Name the guest who must not see it.")
        if lifecycle is not None and lifecycle not in ("bounded", "ongoing"):
            raise DomainError("INVALID_ARGUMENT", "lifecycle must be bounded or ongoing.")
        n_days = t.days if days is None else days
        if isinstance(n_days, bool) or not isinstance(n_days, int) or not 1 <= n_days <= 366:
            raise DomainError("INVALID_ARGUMENT", "Planning window days must be an integer from 1 to 366; it is not an approval expiry.")

        listeners = self._listeners(actor_id)
        if listeners and private:
            raise DomainError("SHARED_CONVERSATION", "Other people are reading this conversation, so a plan only you can see cannot be made here. "
                              "Message the assistant privately to keep it to yourself. No plan was created.")

        def run() -> CreatePlanResult:
            groups = self.db.all("SELECT g.* FROM groups g JOIN group_members gm ON gm.group_id = g.id WHERE gm.member_id = ? AND gm.status = 'active' "
                                 "ORDER BY g.rowid", actor_id)
            if listeners:
                # A plan started in a shared conversation belongs to a group everyone listening is in.
                groups = [x for x in groups if listeners <= {r["member_id"] for r in self.db.all(
                    "SELECT member_id FROM group_members WHERE group_id = ? AND status = 'active'", x["id"])}]
            if not groups:
                raise DomainError("FORBIDDEN", "You are not in any group." if not listeners else
                                  "Not everyone in this conversation is in one of your groups, so no plan can be started here. No plan was created.")
            g = None
            if group:
                low = group.strip().lower()
                g = next((x for x in groups if x["id"] == group.strip() or x["name"].lower() == low), None)
                if g is None:
                    raise DomainError("NOT_FOUND", f'No group called "{group}" is available to you. Your groups: ' + ", ".join(x["name"] for x in groups) + ".")
            elif len(groups) == 1 or private:
                g = groups[0]  # a private plan is seen by the speaker alone, so any of their groups can hold it
            else:
                # The group that holds everyone named; otherwise ask.
                named = [n for n in (audience or []) + (hidden_from or [])]
                fits = [x for x in groups if all(self._name_in_group(x["id"], n) for n in named)] if named else []
                if len(fits) == 1:
                    g = fits[0]
                else:
                    raise DomainError("AMBIGUOUS_GROUP", "Which group is this plan for? Your groups: " + ", ".join(f"{x['name']} (id {x['id']})" for x in groups)
                                      + ". Pass group.")
            members = [r["member_id"] for r in self.db.all("SELECT member_id FROM group_members WHERE group_id = ? AND status = 'active' ORDER BY rowid", g["id"])]
            people = members
            if private:
                people = [actor_id]
            if audience and not private:
                people = list(dict.fromkeys([actor_id] + [self._member_in_group(g["id"], n) for n in audience]))
            if hidden_from and not private:
                hide = {self._member_in_group(g["id"], n) for n in hidden_from}
                if actor_id in hide:
                    raise DomainError("INVALID_ARGUMENT", "You cannot hide a plan from yourself.")
                people = [m for m in people if m not in hide]
            if listeners and not listeners <= set(people):
                hidden = [self.name(m) for m in sorted(listeners - set(people))]
                raise DomainError("SHARED_CONVERSATION", f"{_names(hidden)} {'is' if len(hidden) == 1 else 'are'} reading this conversation but would not be "
                                  "allowed to see this plan. Message the assistant privately to start a plan that is hidden from someone here. "
                                  "No plan was created.")
            if private:
                space_id = f"{g['id']}.private.{actor_id}"
                if not self._space(space_id):
                    self.add_space(space_id, g["id"], f"{self.name(actor_id)}'s private notes", "private", [actor_id])
            else:
                space_id = f"{g['id']}.everyone" if set(people) == set(members) else self._space_for_audience(g["id"], people)
            d = self._resolve_start_date(start_date, g["timezone"])
            org = organizer or actor_id
            if org not in people:
                raise DomainError("INVALID_ARGUMENT", "The organizer must be in the plan audience.")
            pid = plan_id or self._new_plan_id(title or t.name)
            stamp = self.ts()
            self.db.run("INSERT INTO plans (id, group_id, space_id, template, title, start_date, days, timezone, currency, status, organizer_id, catalog_json, "
                        "created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?, ?, ?)", pid, g["id"], space_id, t.id,
                        (title or t.name).strip(), d, n_days, g["timezone"], g["currency"], org, json.dumps(t.catalog.model_dump()), actor_id, stamp, stamp)
            self.db.run("INSERT INTO plan_extensions VALUES (?, ?, ?, ?, ?, ?)", pid, t.model_dump_json(), t.version, preset,
                        lifecycle or t.lifecycle, n_days)
            plan = self._plan_row(pid)
            self._audit(actor_id, "plan.created", "plan", pid, plan, f"{self.name(actor_id)} started {plan['title']} ({t.name}) for {dates_text(d, n_days)}.")
            if starter:
                for sd in t.starter.details:
                    self._insert_item(plan, "detail", sd.type, sd.label, org, actor_id, item_id=self._free_id(sd.key, plan), value_kind=sd.value_kind,
                                      aliases=sd.aliases)
                for st in t.starter.tasks:
                    row = self._insert_item(plan, "task", st.type, st.title, org, actor_id, status="todo")
                    self.db.run("UPDATE items SET version = 1 WHERE id = ?", row["id"])
                if t.starter.activities:
                    snap = self._empty_snapshot(plan)
                    catalog = self._catalog(plan)
                    for sa in t.starter.activities:
                        place = catalog.places.get(sa.place or "")
                        it = t.item_type(sa.type) or t.default_type("activity")
                        aid = self._free_id(sa.key, plan)
                        self._insert_item(plan, "activity", it.name, sa.title, org, actor_id, item_id=aid)
                        start = sa.day * DAY + (parse_time_of_day(sa.start) or 0)
                        end = sa.day * DAY + (parse_time_of_day(sa.end) or 0)
                        snap = snap.with_activity(Activity(id=aid, title=sa.title, kind=place.kind if place else it.name, start=start, end=end,
                                                           place_id=place.id if place else None, place_name=None if place else sa.place,
                                                           participants=tuple(people), cost_shares=tuple((m, place.price_minor if place else 0) for m in sorted(people))))
                    plan, _, _ = self._edit(plan, snap, actor_id)
            others = [m for m in self.plan_audience(plan) if m != actor_id]
            if others:
                self._notify(others, plan, "plan", f"{self.name(actor_id)} started {plan['title']}.")
            scope = self.scope_for(actor_id)
            hidden = (" Only you can see it." if private else
                      f" It is hidden from {_names([self.name(m) for m in members if m not in people])}." if set(people) != set(members) else "")
            return CreatePlanResult(outcome="created", plan=self._plan_view(scope, self._plan_row(pid)),
                                    message=f"Started {plan['title']} for {dates_text(d, n_days)}.{hidden} Nothing is agreed yet.")
        return self.db.tx(run)

    def _free_id(self, key: str, plan: Row) -> str:
        k = normalize_slot_key(key)
        if not self._item(k):
            return k
        return self._new_item_id(plan, k.split(".")[-1])

    def _name_in_group(self, group_id: str, name: str) -> bool:
        try:
            self._member_in_group(group_id, name)
            return True
        except DomainError:
            return False

    def _member_in_group(self, group_id: str, name: str) -> str:
        low = name.strip().lower()
        for r in self.db.all("SELECT gm.member_id, gm.label, m.name FROM group_members gm JOIN members m ON m.id = gm.member_id WHERE gm.group_id = ?", group_id):
            if r["member_id"] == name.strip() or r["name"].lower() == low or (r["label"] and r["label"].lower() == low):
                return r["member_id"]
        raise DomainError("NOT_FOUND", f'No one called "{name}" is in that group.')

    def _resolve_start_date(self, text: str | None, tz: str) -> str:
        today = self.today(tz).date()
        if not text:
            return (today + timedelta(days=1)).isoformat()
        t = text.strip().lower()
        try:
            return date.fromisoformat(t[:10]).isoformat()
        except ValueError:
            pass
        if t in ("today", "tonight"):
            return today.isoformat()
        if t == "tomorrow":
            return (today + timedelta(days=1)).isoformat()
        names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        word = t.replace("next ", "").replace("this ", "").strip()
        for i, n in enumerate(names):
            if word == n or word[:3] == n[:3]:
                ahead = (i - today.weekday()) % 7 or 7
                return (today + timedelta(days=ahead)).isoformat()
        from .text import parse_datetime

        p = parse_datetime(t)
        if p.month and p.day:
            d = date(today.year, p.month, p.day)
            if d < today:
                d = date(today.year + 1, p.month, p.day)
            return d.isoformat()
        raise DomainError("INVALID_ARGUMENT", 'start_date must be a date like "2026-10-24", a weekday like "Saturday", or "tomorrow".')

    # ================================================================ activity edits (propose_change on an activity)

    def _activity_blocker(self, item: Row) -> str | None:
        plan = self._plan_row(item["plan_id"])
        active = self._revision(plan["active_revision_id"])
        if not active or not self._snapshot(active).has(item["id"]):
            return f'{item["label"]} is not agreed yet ({plan["title"]} is {plan["status"].lower().replace("_", " ")}).'
        if plan["status"] == "DEGRADED":
            return f'The agreed plan for {item["label"]} no longer works and needs alternatives.'
        return None

    def _activity_state(self, item: Row) -> str:
        plan = self._plan_row(item["plan_id"])
        active = self._revision(plan["active_revision_id"])
        working = self._working_rev(plan)
        parts = []
        for rev in (active, working):
            if rev and self._snapshot(rev).has(item["id"]):
                a = self._snapshot(rev).activity(item["id"])
                where = self._place_name(plan, a)
                text = "dropped" if a.cancelled else f"{self._when(plan, a)}{' at ' + where if where else ''}"
                tag = "agreed" if rev["kind"] == "committed" else "draft" if rev["kind"] == "draft" else f"{rev['label']}, not agreed yet"
                parts.append(f"{text} ({tag})")
        return "; ".join(dict.fromkeys(parts)) or "not in the plan"

    def _activity_now(self, r: Row) -> str | None:
        item = self._item(r["item_id"]) if r["item_id"] else None
        return self._activity_state(item) if item else None

    def _resolve_place(self, plan: Row, text: str, kinds: list[str], scope: Scope | None = None) -> tuple[str | None, str | None, str | None]:
        """(place_id, free_text_name, kind) for what a person called a place."""
        catalog = json.loads(plan["catalog_json"] or "{}")
        places = catalog.get("places", [])
        low = text.strip().lower()
        pool = [p for p in places if not kinds or p["kind"] in kinds] or places
        for p in pool:
            if p["id"] == text.strip() or p["name"].lower() == low or low in [a.lower() for a in p.get("aliases") or []]:
                return p["id"], None, p["kind"]
        words = set(topic_stems(low))
        scored = sorted(((len(words & set(topic_stems(p["name"] + " " + " ".join(p.get("aliases") or [])))), p) for p in pool), key=lambda x: -x[0])
        if scored and scored[0][0] and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            return scored[0][1]["id"], None, scored[0][1]["kind"]
        if [p for p in places if not kinds or p["kind"] in kinds]:
            known = ", ".join(p["name"] for p in pool)
            # "The venue" may be a detail of another plan rather than a stop of this one: point at those, so nothing is guessed.
            elsewhere = []
            for other in (scope.plans.values() if scope else []):
                if other["id"] == plan["id"]:
                    continue
                elsewhere += [f'{i["label"]} in {other["title"]} (item {i["id"]})' for i in self._visible_items(scope, other["id"], ("detail",))
                              if i["value_kind"] == "place"]
            hint = f" If this is about a place decided in another plan, change that detail instead: {'; '.join(elsewhere)}." if elsewhere else ""
            raise DomainError("NOT_FOUND", f'"{text}" is not one of the places {plan["title"]} can use. Known places: {known}.{hint}')
        return None, text.strip(), None

    def _activity_spec(self, scope: Scope, plan: Row, item: Row | None, title: str | None, day: Any, start: Any, end: Any, place: str | None,
                       participants: list[str] | None, remove: bool, type_name: str | None) -> dict[str, Any]:
        t = self.template(plan)
        base = self._latest_snapshot(plan)
        cur = base.activity(item["id"]) if item and base and base.has(item["id"]) else None
        it = t.item_type(item["type"]) if item else (t.item_type(type_name) if type_name else None)
        if it is None or it.shape != "activity":
            it = t.default_type("activity")
        if it is None:
            raise DomainError("INVALID_ARGUMENT", f"{plan['title']} has no activities. Item types: {', '.join(x.name for x in t.item_types)}.")
        spec: dict[str, Any] = {"activity": item["id"] if item else None, "type": it.name}
        if remove:
            if not item:
                raise DomainError("INVALID_ARGUMENT", "Say which activity to drop.")
            spec["remove"] = True
            return spec
        d = parse_day(day, plan["start_date"], plan["days"]) if day not in (None, "") else None
        if day not in (None, "") and d is None:
            raise DomainError("INVALID_ARGUMENT", f'"{day}" is not one of the plan\'s days ({dates_text(plan["start_date"], plan["days"])}).')
        s = parse_time_of_day(start) if start not in (None, "") else None
        e = parse_time_of_day(end) if end not in (None, "") else None
        if start not in (None, "") and s is None:
            raise DomainError("INVALID_ARGUMENT", f'Could not read the start time "{start}". Use e.g. "19:00" or "7 pm".')
        if end not in (None, "") and e is None:
            raise DomainError("INVALID_ARGUMENT", f'Could not read the end time "{end}".')
        day_idx = d if d is not None else (cur.day if cur else 0)
        if cur is None:
            if s is None:
                raise DomainError("INVALID_ARGUMENT", "A new activity needs a start time.")
            new_start = day_idx * DAY + s
            new_end = day_idx * DAY + e if e is not None else new_start + it.default_minutes
        else:
            new_start = day_idx * DAY + (s if s is not None else cur.start % DAY)
            if e is not None:
                new_end = day_idx * DAY + e
            else:
                new_end = new_start + cur.duration
        if new_end <= new_start:
            raise DomainError("INVALID_ARGUMENT", "The end must be after the start.")
        spec.update(start=new_start, end=new_end)
        if place:
            pid, free, kind = self._resolve_place(plan, place, it.place_kinds or ([cur.kind] if cur and cur.place_id else []), scope)
            spec.update(place_id=pid, place_name=free, kind=kind or (cur.kind if cur else it.name))
        if participants:
            audience = self.plan_audience(plan)
            if any(str(p).strip().lower() in ("everyone", "all", "everybody") for p in participants):
                ids = audience
            else:
                ids = [self.resolve_member(scope, p, plan)["id"] for p in participants]
            spec["participants"] = list(dict.fromkeys(ids))
        if title and title.strip():
            spec["title"] = title.strip()
        if cur is None:
            spec.setdefault("title", (title or "").strip() or it.label)
            spec.setdefault("participants", self.plan_audience(plan))
            spec.setdefault("kind", it.place_kinds[0] if it.place_kinds and not spec.get("place_id") else spec.get("kind") or it.name)
        return spec

    def _spec_text(self, plan: Row, spec: dict[str, Any]) -> str:
        item = self._item(spec["activity"]) if spec.get("activity") else None
        name = spec.get("title") or (item["label"] if item else "New activity")
        if spec.get("remove"):
            return f"drop {name}"
        bits = []
        if "start" in spec:
            fake = Activity(id="x", title=name, kind="", start=spec["start"], end=spec["end"], place_id=None, participants=(), cost_shares=())
            bits.append(self._when(plan, fake))
        if spec.get("place_id") or spec.get("place_name"):
            catalog = self._catalog(plan)
            bits.append("at " + (catalog.places[spec["place_id"]].name if spec.get("place_id") in catalog.places else spec.get("place_name") or ""))
        if "participants" in spec:
            bits.append("with " + _names([self.name(p) for p in spec["participants"]]))
        return f"{name}: " + " ".join(bits) if bits else name

    def _change_activity(self, scope: Scope, actor_id: str, item: Row | None, plan: Row, **kw: Any) -> ChangeResult:
        words = (kw.get("quote") or "").lower()
        if kw.get("participants") is not None and re.search(r"\b(can't (?:come|attend|make)|cannot (?:come|attend)|withdraw|take (?:him|her|them|me) off)\b|不能参加|退出|不来了", words):
            raise DomainError("SELF_WITHDRAWAL_REQUIRED", "Attendance withdrawal must happen on that person's own page. Record a cannot_attend observation instead; do not edit the roster to withdraw for them.")
        spec = self._activity_spec(scope, plan, item, kw.get("title"), kw.get("day"), kw.get("start"), kw.get("end"), kw.get("place"),
                                   kw.get("participants"), bool(kw.get("remove")), kw.get("type_name"))
        utt = self._record_utterance(actor_id, plan["space_id"], kw.get("quote"))
        certainty = kw.get("certainty", "committed")
        summary = self._spec_text(plan, spec)
        if not self.is_organizer(plan, actor_id) or certainty == "tentative":
            org = plan["organizer_id"]
            dup = next((r for r in self.db.all("SELECT * FROM requests WHERE plan_id = ? AND kind = 'activity' AND status = 'open'", plan["id"])
                        if json.loads(r["change_json"]) == spec), None)
            if dup:
                return ChangeResult(outcome="already_pending", plan=plan["title"], item=item["id"] if item else None, request=self._request_view(scope, dup),
                                    message=f"That change is already waiting for {self.name(org)}.")
            kind = "pending_review" if certainty == "committed" else "tentative"
            rid = self._open_request(plan, item, "activity", spec, summary, certainty, org, actor_id, utt, plan["aggregate_version"])
            self._audit(actor_id, "activity.proposed", "item" if item else "plan", item["id"] if item else plan["id"], plan,
                        f"{self.name(actor_id)} suggested a change: {summary}.", utterance_id=utt)
            msg = (f"{self.name(org)} organizes {plan['title']}; asked them to decide: {summary}. Nothing changes until they adopt it and everyone "
                   f"taking part confirms on their own page." if kind == "pending_review" else f"Recorded as a suggestion for {self.name(org)}: {summary}. Nothing changed.")
            return ChangeResult(outcome=kind, plan=plan["title"], item=item["id"] if item else None, message=msg,  # type: ignore[arg-type]
                                request=self._request_view(scope, self._request(rid)), plan_status=plan["status"])
        return self._apply_activity_change(scope, actor_id, plan, item, spec, utt)

    def _apply_activity_change(self, scope: Scope, actor_id: str, plan: Row, item: Row | None, spec: dict[str, Any], utt: str | None) -> ChangeResult:
        plan = self._plan_row(plan["id"])
        base = self._latest_snapshot(plan) or self._empty_snapshot(plan)
        catalog = self._catalog(plan)
        aid = spec.get("activity")
        if aid is None:
            aid = self._free_id(slugify(spec.get("title") or "activity", 24), plan)
            item = self._insert_item(plan, "activity", spec["type"], spec.get("title") or "Activity", plan["organizer_id"], actor_id, item_id=aid)
        cur = base.activity(aid) if base.has(aid) else None
        if spec.get("remove"):
            if cur is None:
                raise DomainError("NOT_FOUND", f'{item["label"] if item else aid} is not in the current version.')
            if plan["active_revision_id"] and self._snapshot(self._revision(plan["active_revision_id"])).has(aid):
                snap = base.with_activity(replace(cur, cancelled=True, cost_shares=tuple((m, 0) for m, _ in cur.cost_shares)))
            else:
                snap = base.without(aid)
        else:
            participants = tuple(spec.get("participants") or (cur.participants if cur else self.plan_audience(plan)))
            place_id = spec["place_id"] if "place_id" in spec else (cur.place_id if cur else None)
            place_name = spec["place_name"] if "place_name" in spec else (cur.place_name if cur else None)
            place = catalog.places.get(place_id or "")
            price = place.price_minor if place else 0
            shares = tuple((m, price) for m in sorted(participants)) if place or not cur else tuple(
                (m, dict(cur.cost_shares).get(m, 0)) for m in sorted(participants))
            new = Activity(id=aid, title=spec.get("title") or (cur.title if cur else "Activity"), kind=spec.get("kind") or (cur.kind if cur else spec["type"]),
                           start=spec.get("start", cur.start if cur else 0), end=spec.get("end", cur.end if cur else 60), place_id=place_id, place_name=place_name,
                           participants=participants, cost_shares=shares, required=cur.required if cur else True, locked=cur.locked if cur else False)
            snap = base.with_activity(new)
            if item and new.title != item["label"]:
                self.db.run("UPDATE items SET label = ?, updated_at = ? WHERE id = ?", new.title, self.ts(), item["id"])
        if snap == base:
            return ChangeResult(outcome="corroborated", plan=plan["title"], item=aid, message="That is already the plan. Nothing changed.", plan_status=plan["status"])
        plan, outcome, rev = self._edit(plan, snap, actor_id)
        problems = self._public_problems(plan, snap)
        summary = self._spec_text(plan, spec)
        self._audit(actor_id, "activity.changed", "item", aid, plan, f"{self.name(actor_id)} changed {rev['label']}: {summary}.", utterance_id=utt)
        where = "the draft" if outcome == "draft_updated" else f"{rev['label']} (not agreed yet)"
        msg = f"Updated {where}: {summary}."
        if outcome != "draft_updated":
            msg += " Nothing changes until everyone affected confirms it on their own page; send it with request_confirmations."
        if problems:
            msg += " Heads up: " + " ".join(problems[:3])
        return ChangeResult(outcome=outcome, plan=plan["title"], item=aid, message=msg, plan_status=plan["status"], problems=problems)  # type: ignore[arg-type]

    # ================================================================ set_my_conditions

    def set_my_conditions(self, actor_id: str, conditions: list[dict[str, Any]], plan: str | None = None, source: str = "agent",
                          notify: bool = True, quote: str | None = None) -> ConditionsResult:
        self._rate_limit(actor_id)
        if quote and source == "agent":
            first_person = re.search(r"\b(I|I'm|I’m|my|me)\b|我", quote, re.I)
            other_people = [m["name"] for m in self.db.all("SELECT id, name FROM members WHERE id <> ?", actor_id)]
            if not first_person and (any(re.search(r"\b" + re.escape(n) + r"\b", quote, re.I) for n in other_people)
                                     or re.search(r"\b(her|his|their|she|he)\b|她|他", quote, re.I)):
                raise DomainError("SELF_CONDITIONS_ONLY", "This describes another person. Do not save it as your own condition; they must set it on their page.")

        def run() -> ConditionsResult:
            scope = self.scope_for(actor_id)
            p = self.resolve_plan(scope, plan, prefer=lambda r: bool(self.template(r).conditions), purpose="conditions apply")
            t = self.template(p)
            if not t.conditions:
                raise DomainError("INVALID_ARGUMENT", f"{p['title']} has no personal conditions.")
            created = []
            for ch in conditions or []:
                ct = t.condition(str(ch.get("kind") or ch.get("name") or ""))
                if ct is None:
                    raise DomainError("INVALID_ARGUMENT", f"Unknown condition {ch.get('kind')!r} for {p['title']}. Use one of: "
                                      + "; ".join(f"{c.name} ({c.label}, {c.hint})" for c in t.conditions))
                value = self._normalize_condition(p, ct, dict(ch.get("value") or {k: v for k, v in ch.items() if k not in ("kind", "name")}))
                cid = new_id("con")
                self.db.run("INSERT INTO conditions (id, group_id, plan_id, owner_id, name, kind, strength, value_json, visibility, status, version, source, created_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'unverified', 1, ?, ?)", cid, p["group_id"], p["id"], actor_id, ct.name, ct.check,
                            "soft" if ct.soft else "hard", json.dumps(value), "group" if ct.check in ("must_attend",) else "private", source, iso_now())
                created.append(self.db.get("SELECT * FROM conditions WHERE id = ?", cid))
                self._audit(actor_id, "condition.drafted", "plan", p["id"], p, f"{self.name(actor_id)} drafted a private condition.", private_to=actor_id)
            if notify and created:
                self._notify([actor_id], p, "verify_condition", "You have a new condition to check on your GroupFlow page before it counts.")
            return ConditionsResult(outcome="recorded", conditions=[self._condition_view(r) for r in created], needs_verification=bool(created),
                                    message="Saved as a draft. It only counts after you confirm it on your own GroupFlow page.")
        return self.db.tx(run)

    def _normalize_condition(self, plan: Row, ct: Any, value: dict[str, Any]) -> dict[str, Any]:
        k = ct.check
        out: dict[str, Any] = {"label": ct.label}
        if k in ("latest_end", "earliest_start"):
            m = parse_time_of_day(value.get("time"))
            if m is None:
                raise DomainError("INVALID_ARGUMENT", f'{ct.name} needs a time, e.g. {{"time": "21:00"}}.')
            out["time"] = hhmm(m)
            if value.get("day"):
                if parse_day(value["day"], plan["start_date"], plan["days"]) is None:
                    raise DomainError("INVALID_ARGUMENT", f'"{value["day"]}" is not one of the plan\'s days.')
                out["day"] = value["day"]
        elif k == "max_total_cost":
            amount = value.get("amount_minor")
            minor = int(amount) if amount is not None else parse_amount_minor(value.get("amount"))
            if minor is None:
                raise DomainError("INVALID_ARGUMENT", f'{ct.name} needs an amount, e.g. {{"amount": 45}}.')
            out["amount_minor"] = minor
        elif k == "needs":
            tags = [normalize_slot_key(str(x)).replace(".", "_").replace("-", "_") for x in (value.get("tags") or [])]
            if not tags:
                raise DomainError("INVALID_ARGUMENT", f'{ct.name} needs tags, e.g. {{"tags": ["{(ct.tags or ["vegetarian"])[0]}"]}}.')
            if ct.tags:
                unknown = [x for x in tags if x not in ct.tags]
                if unknown:
                    raise DomainError("INVALID_ARGUMENT", f"Unknown {ct.label}: {', '.join(unknown)}. Known: {', '.join(ct.tags)}.")
            out.update(tags=tags, applies_to=list(ct.applies_to))
        elif k == "unavailable_window":
            lo, hi = parse_time_of_day(value.get("from")), parse_time_of_day(value.get("to"))
            if lo is None or hi is None or hi <= lo:
                raise DomainError("INVALID_ARGUMENT", f'{ct.name} needs from and to times, e.g. {{"day": "Tue", "from": "13:00", "to": "15:00"}}.')
            out.update({"from": hhmm(lo), "to": hhmm(hi)})
            if value.get("day"):
                if parse_day(value["day"], plan["start_date"], plan["days"]) is None:
                    raise DomainError("INVALID_ARGUMENT", f'"{value["day"]}" is not one of the plan\'s days.')
                out["day"] = value["day"]
        elif k in ("must_attend", "prefer_keep"):
            ref = str(value.get("activity") or value.get("activity_id") or "")
            snap = self._latest_snapshot(plan)
            acts = list(snap.activities) if snap else []
            hit = next((a for a in acts if a.id == ref or a.title.lower() == ref.lower()), None)
            if not hit:
                raise DomainError("INVALID_ARGUMENT", f"{ct.name} needs an activity of the plan: " + ", ".join(a.title for a in acts) + ".")
            out.update(activity_id=hit.id, activity_title=hit.title)
        return out

    # ================================================================ report_observation

    def report_observation(self, actor_id: str, kind: str, place: str | None = None, plan: str | None = None, day: str | None = None,
                           start: str | None = None, end: str | None = None, price: Any = None, note: str | None = None) -> ObservationResult:
        self._rate_limit(actor_id)

        def run() -> ObservationResult:
            scope = self.scope_for(actor_id)
            p = self.resolve_plan(scope, plan, prefer=lambda r: self._has_activities(r), purpose="something could change")
            payload: dict[str, Any] = {"note": note} if note else {}
            me = self.name(actor_id)
            if kind in ("place_unavailable", "price_changed"):
                if not place:
                    raise DomainError("INVALID_ARGUMENT", "Say which place.")
                pid, free, _ = self._resolve_place(p, place, [])
                if not pid:
                    raise DomainError("NOT_FOUND", f'"{place}" is not one of the places this plan uses.')
                pname = self._catalog(p).places[pid].name
                payload["place_id"] = pid
                if kind == "place_unavailable":
                    if day:
                        idx = parse_day(day, p["start_date"], p["days"])
                        if idx is not None:
                            payload["date"] = (date.fromisoformat(p["start_date"]) + timedelta(days=idx)).isoformat()
                    payload.update({"from": start or "00:00", "to": end or "23:59"})
                    payload["summary"] = f"{me} reported that {pname} is unavailable (not verified)"
                else:
                    minor = parse_amount_minor(price)
                    if minor is None:
                        raise DomainError("INVALID_ARGUMENT", "Give the new price.")
                    payload["price_minor"] = minor
                    payload["summary"] = f"{me} reported that {pname} now costs {money(minor, p['currency'])} (not verified)"
            elif kind == "cannot_attend":
                payload["summary"] = f"{me} said they may not make it; they need to withdraw on their own page for it to count"
                self._notify([actor_id], p, "withdraw", "If you can't make it, withdraw your confirmation on your GroupFlow page.")
            else:
                raise DomainError("INVALID_ARGUMENT", "kind must be place_unavailable, price_changed or cannot_attend. "
                                  "Personal limits (home time, budget, busy times, diet) go to set_my_conditions.")
            sid = digest({"m": actor_id, "k": kind, "p": payload, "plan": p["id"]})
            dup = self.db.get("SELECT * FROM observations WHERE source = ? AND source_event_id = ?", f"member:{actor_id}", sid)
            if dup:
                return ObservationResult(outcome="duplicate", verified=False, plan_status=p["status"], message="This was already reported.")
            self.db.run("INSERT INTO observations (id, group_id, plan_id, kind, payload_json, verification, source, source_event_id, actor_id, received_at) "
                        "VALUES (?, ?, ?, ?, ?, 'unverified', ?, ?, ?, ?)", new_id("ob"), p["group_id"], p["id"], kind, json.dumps(payload),
                        f"member:{actor_id}", sid, actor_id, iso_now())
            self._audit(actor_id, "observation.reported", "plan", p["id"], p, payload["summary"] + ".")
            if p["organizer_id"] != actor_id:
                self._notify([p["organizer_id"]], p, "observation", payload["summary"] + ".")
            msg = ("Recorded as an unverified report. The plan does not change until a trusted source confirms it."
                   if kind != "cannot_attend" else payload["summary"] + ".")
            return ObservationResult(outcome="recorded", verified=False, plan_status=p["status"], message=msg)
        return self.db.tx(run)

    def inject_observation(self, group_id: str, kind: str, payload: dict[str, Any], source: str, source_event_id: str, verified: bool = True,
                           actor_id: str | None = None) -> dict[str, Any]:
        """A trusted source (a connector, or the demo console): a verified fact. De-duplicated by (source, source_event_id)."""
        def run() -> dict[str, Any]:
            if not self._group(group_id):
                raise DomainError("NOT_FOUND", "Unknown group.")
            dup = self.db.get("SELECT * FROM observations WHERE source = ? AND source_event_id = ?", source, source_event_id)
            if dup:
                return {"outcome": "duplicate", "observation_id": dup["id"], "affected_plans": []}
            body = dict(payload)
            plans = self.db.all("SELECT * FROM plans WHERE group_id = ? AND status <> 'CANCELLED'", group_id)
            place = None
            for p in plans:
                place = self._catalog(p).places.get(body.get("place_id", "")) or place
            if kind in ("place_unavailable", "price_changed") and place is None:
                raise DomainError("NOT_FOUND", "Unknown place.")
            if kind == "place_unavailable":
                body.setdefault("summary", f"{place.name} became unavailable from {body.get('from', '00:00')} to {body.get('to', '23:59')}"
                                + (f" on {body['date']}" if body.get("date") else ""))
            elif kind == "price_changed":
                body.setdefault("summary", f"{place.name} now costs {money(int(body['price_minor']))} per person")
            oid = new_id("ob")
            self.db.run("INSERT INTO observations (id, group_id, plan_id, kind, payload_json, verification, source, source_event_id, actor_id, received_at) "
                        "VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)", oid, group_id, kind, json.dumps(body), "verified" if verified else "unverified",
                        source, source_event_id, actor_id, iso_now())
            affected: list[str] = []
            if verified:
                if kind == "price_changed":
                    for p in plans:
                        cat = json.loads(p["catalog_json"] or "{}")
                        for pl in cat.get("places", []):
                            if pl["id"] == body["place_id"]:
                                pl["price"] = int(body["price_minor"]) / 100
                        self.db.run("UPDATE plans SET catalog_json = ? WHERE id = ?", json.dumps(cat), p["id"])
                affected = self._reassess(group_id, (body.get("summary") or kind) + ".")
            self.db.run("UPDATE observations SET applied_at = ? WHERE id = ?", iso_now(), oid)
            return {"outcome": "recorded", "observation_id": oid, "affected_plans": affected}
        return self.db.tx(run)

    def _with_current_prices(self, plan: Row, snap: Snapshot) -> Snapshot:
        catalog = self._catalog(plan)
        acts = []
        for a in snap.activities:
            place = catalog.places.get(a.place_id or "")
            if place and not a.cancelled:
                a = replace(a, cost_shares=tuple((m, place.price_minor) for m, _ in a.cost_shares))
            acts.append(a)
        return replace(snap, activities=tuple(acts))

    def _reassess(self, group_id: str, reason: str, plan_id: str | None = None) -> list[str]:
        """After a verified fact or an activated condition: an agreed plan that no longer works becomes DEGRADED."""
        affected = []
        rows = self.db.all("SELECT * FROM plans WHERE group_id = ? AND status IN ('COMMITTED', 'READY_TO_COMMIT', 'AWAITING_CONFIRMATIONS', 'PROPOSED')", group_id)
        for plan in rows:
            if plan_id and plan["id"] != plan_id:
                continue
            active = self._revision(plan["active_revision_id"])
            cand = self._revision(plan["candidate_revision_id"])
            if active and plan["status"] == "COMMITTED":
                snap = self._with_current_prices(plan, self._snapshot(active))
                if violations(self._problem(plan, snap), snap):
                    fresh = self._bump(plan["id"], "DEGRADED", reason)
                    self._audit(None, "plan.degraded", "plan", plan["id"], fresh, f"{active['label']} no longer works: {reason}")
                    self._notify(self.plan_audience(plan), fresh, "degraded",
                                 f"{plan['title']}: {reason} The agreed plan no longer works; {self.name(plan['organizer_id'])} can ask for alternatives.")
                    affected.append(plan["id"])
                    continue
            if cand and plan["status"] in ("READY_TO_COMMIT", "AWAITING_CONFIRMATIONS", "PROPOSED"):
                snap = self._snapshot(cand)
                if violations(self._problem(plan, snap), snap):
                    status = "DEGRADED" if active else plan["status"] if plan["status"] != "READY_TO_COMMIT" else "AWAITING_CONFIRMATIONS"
                    self._bump(plan["id"], status, f"{cand['label']} no longer works because {reason[:1].lower() + reason[1:]}")
                    affected.append(plan["id"])
                elif plan["status"] == "READY_TO_COMMIT" and not self._confirmations(cand).complete:
                    self._bump(plan["id"], "AWAITING_CONFIRMATIONS", "a confirmation no longer applies")
        return affected

    # ================================================================ request_confirmations

    def request_confirmations(self, actor_id: str, plan: str | None = None, expected_version: int | None = None) -> ConfirmationsResult:
        self._rate_limit(actor_id)

        def run() -> ConfirmationsResult:
            scope = self.scope_for(actor_id)
            p = self.resolve_plan(scope, plan, prefer=lambda r: r["status"] in ("DRAFT", "PROPOSED", "AWAITING_CONFIRMATIONS"), purpose="something can be sent out")
            self._require_organizer(p, actor_id, "ask for confirmations")
            self._check_version(p, expected_version)
            me = self.name(actor_id)
            if not self._has_activities(p):
                raise DomainError("INVALID_STATE", f"{p['title']} has no activities to agree on; details and tasks are decided by their owners.")
            cand = self._revision(p["candidate_revision_id"])
            if cand is None:
                if p["active_revision_id"]:
                    return ConfirmationsResult(outcome="ready", plan_status=p["status"], message=f"{p['title']} is already agreed and nothing has changed since.")
                draft = self._revision(p["draft_revision_id"])
                snap = self._snapshot(draft)
                problems = self._public_problems(p, snap)
                if problems:
                    return ConfirmationsResult(outcome="infeasible", plan_status=p["status"], problems=problems,
                                               message="The draft breaks someone's hard conditions, so it was not sent. Nothing changed. find_alternatives can look for the "
                                                       "smallest change that works; the organizer decides whether to send that.")
                cand = self._new_revision(p, snap, "P1", "candidate", "proposal", actor_id, draft["id"])
                p = self._bump(p["id"], "PROPOSED", None, candidate_revision_id=cand["id"])
                self._audit(actor_id, "plan.proposed", "plan", p["id"], p, f"{me} proposed {cand['label']}.")
            else:
                snap = self._snapshot(cand)
                problems = self._public_problems(p, snap)
                if problems:
                    return ConfirmationsResult(outcome="infeasible", plan_status=p["status"], version=cand["label"], problems=problems,
                                               message=f"{cand['label']} breaks someone's hard conditions, so it was not sent. Nothing changed. find_alternatives can "
                                                       "look for the smallest change that works; the organizer decides whether to send that.")
            for other in self.db.all("SELECT id FROM plan_revisions WHERE plan_id = ? AND kind = 'candidate' AND id <> ?", p["id"], cand["id"]):
                self.db.run("UPDATE plan_revisions SET kind = 'discarded' WHERE id = ?", other["id"])
            if not self._round_open(cand):
                self.db.run("INSERT INTO confirmation_rounds (id, plan_id, revision_id, status, created_by, created_at) VALUES (?, ?, ?, 'open', ?, ?)",
                            new_id("round"), p["id"], cand["id"], actor_id, iso_now())
            carried = self._carry_forward(p, cand)
            view = self._confirmations(cand)
            status = "READY_TO_COMMIT" if view.complete else "AWAITING_CONFIRMATIONS"
            p = self._bump(p["id"], status, None)
            waiting = [r.person for r in view.rows if not r.done]
            self._audit(actor_id, "confirmations.requested", "plan", p["id"], p,
                        f"{me} asked {_plural(len(waiting), 'person')} to confirm {cand['label']}; {carried} approval(s) carried forward.")
            pending: set[str] = set()
            kept: dict[str, list[str]] = {}
            for scope_row, st, via in self._requirements(cand):
                if st == "pending":
                    pending.add(scope_row["member_id"])
                elif via == "carry_forward":
                    kept.setdefault(scope_row["member_id"], []).append(self._activity_title(cand, scope_row["activity_id"]))
            for m in pending:
                if m != actor_id:
                    self._notify([m], p, "confirm", f"{me} asked you to confirm {p['title']} {cand['label']} on your GroupFlow page.")
            for m, titles in kept.items():
                self._notify([m], p, "carried", f"Your earlier approval still covers {_names(titles)} in {cand['label']}; nothing to do for those.")
            msg = ("Everyone needed has already confirmed; it can be committed." if view.complete else
                   f"Asked {_names(waiting)} to confirm {cand['label']} on their own GroupFlow page. Nobody can confirm for someone else.")
            if carried:
                msg += f" {carried} earlier approval(s) still hold for the parts that did not change."
            return ConfirmationsResult(outcome="ready" if view.complete else "requested", plan_status=p["status"], version=cand["label"],
                                       waiting_for=waiting, carried_forward=carried, message=msg)
        return self.db.tx(run)

    def propose_draft(self, actor_id: str, plan_id: str) -> Row:
        """Turn the draft into proposal P1 without sending it (demo stage "proposed")."""
        def run() -> Row:
            p = self._plan_row(plan_id)
            self._require_organizer(p, actor_id, "propose the plan")
            draft = self._revision(p["draft_revision_id"])
            cand = self._new_revision(p, self._snapshot(draft), "P1", "candidate", "proposal", actor_id, draft["id"])
            p = self._bump(p["id"], "PROPOSED", None, candidate_revision_id=cand["id"])
            self._audit(actor_id, "plan.proposed", "plan", p["id"], p, f"{self.name(actor_id)} proposed P1.")
            return cand
        return self.db.tx(run)

    def _activity_title(self, rev: Row, activity_id: str) -> str:
        if not activity_id:
            return "the whole plan"
        snap = self._snapshot(rev)
        return snap.activity(activity_id).title if snap.has(activity_id) else activity_id

    # ================================================================ find_alternatives (repair)

    def find_alternatives(self, actor_id: str, plan: str | None = None, expected_version: int | None = None, run_now: bool = True) -> AlternativesResult:
        self._rate_limit(actor_id)

        def start() -> tuple[str, bool]:
            scope = self.scope_for(actor_id)
            p = self.resolve_plan(scope, plan, prefer=lambda r: r["status"] in ("DEGRADED", "BLOCKED", "NEEDS_ATTENTION", "AWAITING_CONFIRMATIONS", "DRAFT", "PROPOSED"),
                                  purpose="alternatives could help")
            self._require_organizer(p, actor_id, "ask for alternatives")
            self._check_version(p, expected_version)
            if not self._has_activities(p):
                raise DomainError("INVALID_STATE", f"{p['title']} has no activities to rearrange.")
            existing = self.db.get("SELECT * FROM repair_jobs WHERE plan_id = ? AND status IN ('queued', 'running') ORDER BY created_at DESC LIMIT 1", p["id"])
            if existing:
                return existing["id"], False
            base = self._revision(p["active_revision_id"]) or self._revision(p["candidate_revision_id"]) or self._revision(p["draft_revision_id"])
            inputs = {"base_revision": base["id"], "versions": self._input_versions(p), "publish_version": p["aggregate_version"] + 1}
            job_id = new_id("job")
            self.db.run("INSERT INTO repair_jobs (id, plan_id, base_revision_id, input_digest, input_json, status, method, solver_budget_ms, requested_by, created_at) "
                        "VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)", job_id, p["id"], base["id"], digest(inputs), json.dumps(inputs), self.method,
                        int(self.solver_budget_s * 1000), actor_id, iso_now())
            p = self._bump(p["id"], "REPAIRING", "alternatives requested")
            self._audit(actor_id, "repair.requested", "plan", p["id"], p, f"{self.name(actor_id)} asked for alternatives to {base['label']}.")
            return job_id, True

        job_id, created = self.db.tx(start)
        if run_now:
            self.run_job(job_id)
        return self.get_alternatives(actor_id, job=job_id)

    def job_status(self, job_id: str) -> str | None:
        return self.db.scalar("SELECT status FROM repair_jobs WHERE id = ?", job_id)

    def run_queued_jobs(self) -> int:
        n = 0
        for job in self.db.all("SELECT id FROM repair_jobs WHERE status = 'queued' ORDER BY created_at"):
            self.run_job(job["id"])
            n += 1
        return n

    def run_job(self, job_id: str) -> None:
        claimed = self.db.tx(lambda: self.db.run("UPDATE repair_jobs SET status = 'running', started_at = ? WHERE id = ? AND status = 'queued'", iso_now(), job_id))
        if not claimed:
            return
        job = self.db.get("SELECT * FROM repair_jobs WHERE id = ?", job_id)
        plan = self._plan_row(job["plan_id"])
        base_rev = self._revision(job["base_revision_id"])
        try:
            base = self._with_current_prices(plan, self._snapshot(base_rev)) if base_rev["kind"] in ("committed",) else self._snapshot(base_rev)
            problem = self._problem(plan, base)
            outcome = repair(problem, method=job["method"], time_budget_s=job["solver_budget_ms"] / 1000)
        except Exception as err:  # noqa: BLE001
            log.exception("repair job %s failed", job_id)

            def fail() -> None:
                self.db.run("UPDATE repair_jobs SET status = 'failed', error = ?, finished_at = ? WHERE id = ?", str(err), iso_now(), job_id)
                fresh = self._plan_row(plan["id"])
                inputs = json.loads(job["input_json"])
                if fresh["aggregate_version"] == inputs.get("publish_version", fresh["aggregate_version"]):
                    self._bump(plan["id"], "NEEDS_ATTENTION", "the search for alternatives failed")
            self.db.tx(fail)
            return

        def store() -> None:
            fresh = self._plan_row(plan["id"])
            inputs = json.loads(job["input_json"])
            if fresh["aggregate_version"] != inputs.get("publish_version", fresh["aggregate_version"]) or self._input_versions(fresh) != inputs["versions"]:
                self.db.run("UPDATE repair_jobs SET status = 'failed', error = ?, finished_at = ? WHERE id = ?",
                            "STALE_INPUT: the plan or its conditions changed; request fresh alternatives", iso_now(), job_id)
                return  # Preserve the intervening edit and its candidate; never publish stale work.
            proof = {"status": outcome.status, "method": outcome.method, "domain_size": outcome.domain_size, "explored": outcome.explored,
                     "search_complete": outcome.search_complete, "runtime_ms": round(outcome.runtime_ms, 1),
                     "requested_method": outcome.requested_method, "fallback_reason": outcome.fallback_reason}
            label = self._next_label(fresh) if fresh["active_revision_id"] else "P1"
            nothing_to_do = bool(outcome.candidates) and not outcome.candidates[0].changed
            result = {"proof": proof, "conflict": [v.public for v in outcome.conflict], "nothing_to_do": nothing_to_do}
            if nothing_to_do:
                outcome.candidates = []
            # Different options can produce the same plan (two ways to reach "the hike at 11"); offer each plan once.
            seen: set[str] = set()
            unique = []
            for cand in outcome.candidates:
                key = canonical_json(snapshot_to_dict(cand.snapshot))
                if key not in seen:
                    seen.add(key)
                    unique.append(cand)
            outcome.candidates = unique
            self._supersede_candidate(fresh)
            raw_base = self._snapshot(base_rev)
            for rank, cand in enumerate(outcome.candidates):
                suffix = "" if rank == 0 else chr(ord("b") + rank - 1)
                rev = self._new_revision(fresh, cand.snapshot, f"{label}{suffix}", "candidate", "repair", None, parent=base_rev["id"], job_id=job_id, alternate=rank > 0)
                explanation = {"rejected": rejected_alternatives(problem, cand, outcome.domain) if rank == 0 else [],
                               "triggers": [self._observation_text(o) for o in self._trigger_observations(fresh)]}
                self.db.run("INSERT INTO repair_candidates (id, job_id, rank, revision_id, alternate, objective_json, burden_json, diff_json, reconfirm_json, "
                            "explanation_json, proof_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", new_id("rc"), job_id, rank, rev["id"], 1 if rank else 0,
                            json.dumps(dict(zip(("changed_activities", "max_person_burden", "total_person_burden", "reconfirmation_members",
                                                 "soft_preference_loss", "added_cost_minor"), cand.objective))),
                            json.dumps(cand.burden), json.dumps(self._diff(fresh, raw_base, cand.snapshot)), json.dumps(sorted(cand.reconfirm)),
                            json.dumps(explanation), json.dumps(proof))
                if rank == 0:
                    self.db.run("UPDATE plans SET candidate_revision_id = ? WHERE id = ?", rev["id"], plan["id"])
            if outcome.candidates:
                status, reason = "PROPOSED", f"alternatives found: {label}"
            elif nothing_to_do:
                status = "COMMITTED" if fresh["active_revision_id"] else ("DRAFT" if not fresh["candidate_revision_id"] else "PROPOSED")
                reason = "the current plan still works"
                if status == "DRAFT":
                    self.db.run("UPDATE plans SET candidate_revision_id = NULL WHERE id = ?", plan["id"])
            elif outcome.status == "infeasible_within_domain":
                status, reason = "BLOCKED", "no option in the current choices satisfies everyone's hard conditions"
            else:
                status, reason = "NEEDS_ATTENTION", "the search ran out of time before finding a plan"
            self.db.run("UPDATE repair_jobs SET status = 'done', result_status = ?, result_json = ?, finished_at = ? WHERE id = ?",
                        outcome.status, json.dumps(result), iso_now(), job_id)
            fresh = self._bump(plan["id"], status, reason)
            self._audit(None, "repair.completed", "plan", plan["id"], fresh,
                        f"Search finished: {outcome.status} ({outcome.method}, {len(outcome.candidates)} candidate(s)).", payload=proof)
            if fresh["organizer_id"] != job["requested_by"]:
                self._notify([plan["organizer_id"]], fresh, "repair", f"The search for {plan['title']} finished: {reason}.")
        self.db.tx(store)

    def _trigger_observations(self, plan: Row) -> list[Row]:
        return self.db.all("SELECT * FROM observations WHERE group_id = ? AND verification = 'verified' ORDER BY received_at DESC LIMIT 3", plan["group_id"])

    def get_alternatives(self, actor_id: str, plan: str | None = None, job: str | None = None) -> AlternativesResult:
        def run() -> AlternativesResult:
            scope = self.scope_for(actor_id)
            if job:
                j = self.db.get("SELECT * FROM repair_jobs WHERE id = ?", job)
                if not j or not scope.sees_plan(j["plan_id"]):
                    raise DomainError("NOT_FOUND", "No such search.")
            else:
                p = self.resolve_plan(scope, plan, prefer=lambda r: bool(self.db.get("SELECT 1 FROM repair_jobs WHERE plan_id = ?", r["id"])),
                                      purpose="alternatives were searched")
                j = self.db.get("SELECT * FROM repair_jobs WHERE plan_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1", p["id"])
                if not j:
                    return AlternativesResult(job="", status="done", plan_status=p["status"], message="Nobody has asked for alternatives for this plan yet.")
            p = self._plan_row(j["plan_id"])
            if j["status"] in ("queued", "running"):
                return AlternativesResult(job=j["id"], status=j["status"], plan_status=p["status"], message="Still searching. Ask again in a moment.")
            if j["status"] == "failed":
                return AlternativesResult(job=j["id"], status="failed", plan_status=p["status"], message="The search failed; the plan needs attention.")
            result = json.loads(j["result_json"])
            base = self._snapshot(self._revision(j["base_revision_id"]))
            alts = []
            for rc in self.db.all("SELECT * FROM repair_candidates WHERE job_id = ? ORDER BY rank", j["id"]):
                rev = self._revision(rc["revision_id"])
                snap = self._snapshot(rev)
                changed = set(changed_activities(base, snap))
                totals_before = sorted({base.member_total(m) for m in base.members})
                totals_after = sorted({snap.member_total(m) for m in snap.members})
                alts.append(AlternativeView(candidate=rev["id"], label=rev["label"], alternate=bool(rc["alternate"]), changes=json.loads(rc["diff_json"]),
                                            unchanged=[a.title for a in snap.activities if a.id not in changed],
                                            cost_each_before=" / ".join(money(t, snap.currency) for t in totals_before),
                                            cost_each_after=" / ".join(money(t, snap.currency) for t in totals_after),
                                            reconfirm=[self.name(m) for m in json.loads(rc["reconfirm_json"])]))
            if result.get("nothing_to_do"):
                msg = "Nothing needs to change: the current plan still satisfies everyone's conditions."
            elif alts:
                a = alts[0]
                msg = (f"{a.label}: " + "; ".join(a.changes) + f". Keeps {', '.join(a.unchanged) or 'nothing else'} as it was. "
                       f"It needs confirmation from {_names(a.reconfirm) or 'nobody'}; send it with request_confirmations.")
            elif j["result_status"] == "infeasible_within_domain":
                msg = "No option in the current choices satisfies everyone's hard conditions. " + " ".join(result.get("conflict", []))
            else:
                msg = "The search ran out of time without finding a plan; this is not proof that none exists."
            return AlternativesResult(job=j["id"], status="done", plan_status=p["status"], message=msg, proof=ProofView(**result["proof"]),
                                      alternatives=alts, conflict=result.get("conflict", []))
        return self.db.tx(run)

    # ================================================================ explain (activities)

    def _explain_activity(self, scope: Scope, actor_id: str, plan: Row | None, target: Row | None, option: str | None) -> ExplainView:
        if plan is None:
            plan = self.resolve_plan(scope, None, prefer=lambda r: self._has_activities(r), purpose="there is something to explain")
        if not self._has_activities(plan):
            return self._explain_hold_ups(scope)
        cand = self._revision(plan["candidate_revision_id"])
        rev = cand or self._revision(plan["active_revision_id"]) or self._revision(plan["draft_revision_id"])
        snap = self._snapshot(rev)
        parent = self._revision(rev["parent_id"]) if rev["parent_id"] else None
        base = self._snapshot(parent) if parent else None
        diff = self._diff(plan, base, snap) if base else []
        changed = set(changed_activities(base, snap)) if base else set()
        rc = self.db.get("SELECT * FROM repair_candidates WHERE revision_id = ?", rev["id"])
        explanation = json.loads(rc["explanation_json"]) if rc else {"rejected": [], "triggers": []}
        reconfirm_ids = json.loads(rc["reconfirm_json"]) if rc else []
        options = [RejectedOption(option=r["option"], works=r["feasible"], reason=r["reason"],
                                  would_also_change=[snap.activity(x).title if snap.has(x) else x for x in r["would_also_change"]])
                   for r in explanation.get("rejected", [])]
        if option:
            asked = self._judge_named_option(plan, rev, target, option)
            known = [o for o in options if o.option.lower().startswith(option.strip().lower())]
            options = (known or ([asked] if asked else [])) + [o for o in options if o not in known]
        if diff:
            msg = (f"{rev['label']} changes " + "; ".join(diff) + f", and keeps {', '.join(a.title for a in snap.activities if a.id not in changed) or 'nothing else'} as agreed.")
        else:
            msg = f"{rev['label']} is the first version; there is nothing earlier to compare with."
        if option and options:
            o = options[0]
            msg = (f"{o.option}: " + ("it would work, but " + o.reason if o.works else o.reason)) + " " + msg
        return ExplainView(subject=f"{plan['title']} {rev['label']}", message=msg, changes=diff, unchanged=[a.title for a in snap.activities if a.id not in changed],
                           triggers=explanation.get("triggers", []), options=options,
                           reconfirm=[self.name(m) for m in reconfirm_ids])

    def _judge_named_option(self, plan: Row, rev: Row, target: Row | None, option: str) -> RejectedOption | None:
        """Why not <place>? Hold that place for the matching activity and find the least-bad way to fit everything else around it."""
        snap = self._snapshot(rev)
        try:
            pid, _, kind = self._resolve_place(plan, option, [])
        except DomainError:
            return None
        if not pid:
            return None
        acts = [a for a in snap.live if (target and a.id == target["id"]) or (not target and a.kind == kind)]
        if not acts:
            return None
        a = acts[0]
        problem = self._problem(plan, snap)
        domain = build_domain(problem)
        place = problem.catalog.places[pid]
        start = a.start
        if place.start_slots is not None and start % DAY not in place.start_slots:
            day0 = start - start % DAY
            later = [day0 + s for s in place.start_slots if day0 + s >= a.start]
            start = min(later) if later else day0 + min(place.start_slots)
        choice = {x.id: option_of(x) for x in snap.activities}
        verdict = judge_option(problem, None, choice, a.id, (start, pid), domain)
        if not verdict:
            return None
        return RejectedOption(option=verdict["option"], works=verdict["feasible"], reason=verdict["reason"],
                              would_also_change=[snap.activity(x).title if snap.has(x) else x for x in verdict["would_also_change"]])

    # ================================================================ commit

    def gate(self, plan: Row, rev: Row) -> list[str]:
        """Every reason the candidate may not take effect now. Empty means it may be committed."""
        blockers: list[str] = []
        if rev["id"] != plan["candidate_revision_id"] or rev["kind"] != "candidate":
            return ["That is not the plan's current proposal."]
        if json.loads(rev["input_versions"]) != self._input_versions(plan):
            blockers.append("Conditions, membership or verified facts changed after this version was made; ask for alternatives or send a new version.")
        snap = self._snapshot(rev)
        for v in violations(self._problem(plan, snap), snap):
            if v.public not in blockers:
                blockers.append(v.public)
        if not self._round_open(rev):
            blockers.append("Nobody has been asked to confirm this version yet.")
            return blockers
        for scope_row, status, _ in self._requirements(rev):
            if status not in SATISFIED:
                who = self.name(scope_row["member_id"])
                what = self._activity_title(rev, scope_row["activity_id"])
                word = {"pending": "has not confirmed", "rejected": "declined", "revoked": "withdrew from", "expired": "has an expired confirmation for",
                        "conditional_unmet": "set a condition that is not met for"}.get(status, status)
                blockers.append(f"{who} {word} {what}.")
        return blockers

    def commit_plan(self, actor_id: str, plan: str | None = None, candidate: str | None = None, expected_version: int | None = None) -> CommitResult:
        self._rate_limit(actor_id)

        def run() -> CommitResult:
            scope = self.scope_for(actor_id)
            if candidate:
                rev = self._revision(candidate)
                if not rev or not scope.sees_plan(rev["plan_id"]):
                    raise DomainError("NOT_FOUND", "No such version is available to you.")
                p = scope.plans[rev["plan_id"]]
            else:
                p = self.resolve_plan(scope, plan, prefer=lambda r: r["status"] in ("READY_TO_COMMIT", "AWAITING_CONFIRMATIONS"), purpose="something can be committed")
                rev = self._revision(p["candidate_revision_id"])
            self._require_organizer(p, actor_id, "commit the plan")
            if rev is None:
                active = self._revision(p["active_revision_id"])
                if active:
                    return CommitResult(outcome="already_committed", plan_status=p["status"], message=f"{active['label']} is already the agreed plan.")
                return CommitResult(outcome="blocked", plan_status=p["status"], blockers=["Nothing has been proposed yet."],
                                    message="Not committed. Nothing has been sent out for confirmation yet.")
            if rev["kind"] == "committed" and p["active_revision_id"] == rev["id"]:
                return CommitResult(outcome="already_committed", plan_status=p["status"], message=f"{rev['label']} is already the agreed plan.")
            self._check_version(p, expected_version)
            blockers = self.gate(p, rev)
            if blockers:
                self._audit(actor_id, "commit.blocked", "plan", p["id"], p, f"Commit of {rev['label']} was blocked.", payload={"blockers": blockers})
                return CommitResult(outcome="blocked", plan_status=p["status"], blockers=blockers, message="Not committed. " + " ".join(blockers[:4]))
            previous = self._revision(p["active_revision_id"])
            if previous:
                self.db.run("UPDATE plan_revisions SET kind = 'superseded' WHERE id = ?", previous["id"])
            self.db.run("UPDATE plan_revisions SET kind = 'committed' WHERE id = ?", rev["id"])
            self.db.run("UPDATE confirmation_rounds SET status = 'closed', closed_at = ? WHERE revision_id = ? AND status = 'open'", iso_now(), rev["id"])
            p = self._bump(p["id"], "COMMITTED", None, active_revision_id=rev["id"], candidate_revision_id=None)
            snap = self._snapshot(rev)
            before = self._snapshot(previous) if previous else None
            t = self.template(p)
            catalog = self._catalog(p)
            bookings = []
            for a in snap.live:
                b = before.activity(a.id) if before and before.has(a.id) else None
                if b and (b.where, b.start) == (a.where, a.start):
                    continue
                # The booking made for the old time or place is released first (simulated, like the booking itself).
                for old in self.db.all("SELECT * FROM executions WHERE plan_id = ? AND activity_id = ? AND status = 'succeeded'", p["id"], a.id):
                    self.db.run("UPDATE executions SET status = 'cancelled' WHERE id = ?", old["id"])
                    bookings.append(f'cancel {old["action"]} (simulated, ref {old["provider_reference"]})')
                place = catalog.places.get(a.place_id or "")
                if not place or place.kind not in t.booking_kinds:
                    continue
                key = f"{rev['id']}:{a.id}"
                action = f"book {place.name} for {len(a.participants)} at {when_text(a.start, p['start_date'], self._multi(p))}"
                self.db.run("INSERT OR IGNORE INTO executions (id, plan_id, revision_id, activity_id, action, action_digest, idempotency_key, status, provider_reference, "
                            "simulated, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'succeeded', ?, 1, ?)", new_id("ex"), p["id"], rev["id"], a.id, action,
                            digest({"book": a.place_id, "start": a.start, "people": len(a.participants)}), key, f"SIM-{rev['label']}-{a.id}".upper(), iso_now())
                ref = f"SIM-{rev['label']}-{a.id}".upper()
                bookings.append(f"{action} (simulated, ref {ref})")
            live_ids = {a.id for a in snap.live}
            for old in self.db.all("SELECT * FROM executions WHERE plan_id = ? AND status = 'succeeded'", p["id"]):
                if old["activity_id"] not in live_ids:  # dropped from the plan: its booking goes too
                    self.db.run("UPDATE executions SET status = 'cancelled' WHERE id = ?", old["id"])
                    bookings.append(f'cancel {old["action"]} (simulated, ref {old["provider_reference"]})')
            # Items that watch a changed activity hear about it, like any other change.
            if before:
                impacts_scope = self.inspector_scope()
                for aid in changed_activities(before, snap):
                    item = self._item(aid)
                    if item and self._links_to(aid, "watches"):
                        from .views import ImpactView

                        a_new = snap.activity(aid) if snap.has(aid) else None
                        a_old = before.activity(aid) if before.has(aid) else None
                        txt = lambda x: "dropped" if x is None or x.cancelled else f"{self._when(p, x)} at {self._place_name(p, x) or 'no place'}"  # noqa: E731
                        self._propagate(impacts_scope, item, txt(a_old), txt(a_new), actor_id, ImpactView())
            for a in before.activities if before else ():
                if not snap.has(a.id):
                    self.db.run("UPDATE items SET archived = 1 WHERE id = ?", a.id)
            changes = self._diff(p, before, snap)
            summary = "; ".join(changes) if changes else "as proposed"
            self._audit(actor_id, "plan.committed", "plan", p["id"], p, f"{self.name(actor_id)} committed {rev['label']} ({summary}).")
            self._mark_stale_asks(p["id"], self.plan_audience(p))
            self._notify([m for m in self.plan_audience(p) if m != actor_id], p, "committed",
                         f"{p['title']} {rev['label']} is now the agreed plan ({summary}). Your total is on your page.")
            return CommitResult(outcome="committed", plan_status=p["status"], bookings=bookings,
                                message=f"{rev['label']} is now the agreed plan. Booking steps are simulated only.")
        return self.db.tx(run)

    def _mark_stale_asks(self, plan_id: str, member_ids: list[str]) -> None:
        """Unheard "please confirm" notices that no longer ask anything (answered, or the version was committed) are marked heard."""
        stamp = self.ts()
        for m in member_ids:
            self.db.run("UPDATE notifications SET read_at = ? WHERE member_id = ? AND plan_id = ? AND kind IN ('confirm', 'carried') AND read_at IS NULL",
                        stamp, m, plan_id)

    # ================================================================ human-only operations (each person's own page)

    def human_login(self, member_id: str, passcode: str) -> dict[str, Any]:
        member = self.identity.by_passcode(member_id, passcode)
        if not member:
            raise DomainError("UNAUTHENTICATED", "Wrong member or passcode.")
        token, csrf = new_secret(), new_secret(16)
        expires = (now() + SESSION_TTL).astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        self.db.tx(lambda: self.db.run("INSERT INTO human_sessions (token_hash, member_id, csrf_hash, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                                       hash_secret(token), member.id, hash_secret(csrf), iso_now(), expires))
        return {"token": token, "csrf": csrf, "member": {"id": member.id, "name": member.name}, "expires_at": expires}

    def human_session(self, token: str, csrf: str | None = None, require_csrf: bool = False):
        row = self.db.get("SELECT * FROM human_sessions WHERE token_hash = ?", hash_secret(token or ""))
        if not row or row["expires_at"] < iso_now():
            raise DomainError("UNAUTHENTICATED", "Sign in on your GroupFlow page first.")
        if require_csrf and hash_secret(csrf or "") != row["csrf_hash"]:
            raise DomainError("CSRF", "Missing or wrong CSRF token.")
        member = self.identity.get(row["member_id"])
        assert member is not None
        return member

    def human_inbox(self, member: Any) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            scope = self.scope_for(member.id)
            pending, answered, plans = [], [], []
            for p in scope.plans.values():
                cand = self._revision(p["candidate_revision_id"])
                if self._has_activities(p) or self.template(p).conditions:
                    plans.append({"id": p["id"], "title": p["title"], "status": p["status"], "conditions": [
                        {"name": c.name, "label": c.label, "check": c.check, "hint": c.hint, "tags": c.tags} for c in self.template(p).conditions]})
                if cand and self._round_open(cand):
                    for scope_row, status, via in self._requirements(cand):
                        if scope_row["member_id"] != member.id:
                            continue
                        item = {"scope_id": scope_row["id"], "plan": p["title"], "plan_id": p["id"], "revision": cand["label"],
                                "activity_id": scope_row["activity_id"] or None, "text": self._scope_text(scope_row), "status": status, "via": via,
                                "scope_digest": scope_row["scope_digest"]}
                        (pending if status == "pending" else answered).append(item)
            receipts = []
            for r in self.db.all("SELECT r.*, rev.label, p.title FROM approval_receipts r JOIN plan_revisions rev ON rev.id = r.revision_id "
                                 "JOIN plans p ON p.id = r.plan_id WHERE r.member_id = ? ORDER BY r.created_at DESC LIMIT 20", member.id):
                scope_row = self.db.get("SELECT * FROM approval_scopes WHERE id = ?", r["scope_id"])
                receipts.append({"receipt_id": r["id"], "plan": r["title"], "revision": r["label"], "decision": r["decision"], "status": r["status"],
                                 "condition": json.loads(r["condition_json"] or "null"), "text": self._scope_text(scope_row), "created_at": r["created_at"]})
            conditions, seen = [], set()
            for p in scope.plans.values():
                for c in self._condition_rows(p, member.id, ("unverified", "active")):
                    if c["id"] in seen:
                        continue
                    seen.add(c["id"])
                    conditions.append(dict(self._condition_view(c).model_dump(), plan_id=c["plan_id"], value=json.loads(c["value_json"]),
                                           verify_digest=digest({"id": c["id"], "version": c["version"], "value": json.loads(c["value_json"])})))
            notes = [{"id": n["id"], "text": n["text"], "created_at": n["created_at"], "plan_id": n["plan_id"]} for n in self.db.all(
                "SELECT * FROM notifications WHERE member_id = ? AND read_at IS NULL ORDER BY created_at, id", member.id)
                if n["space_id"] is None or n["space_id"] in scope.spaces]
            return {"member": {"id": member.id, "name": member.name}, "plans": plans, "pending": pending, "answered": answered, "receipts": receipts,
                    "conditions": conditions, "notifications": notes}
        return self.db.tx(run)

    def human_verify_condition(self, member: Any, condition_id: str, verify_digest: str) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            c = self.db.get("SELECT * FROM conditions WHERE id = ? AND owner_id = ?", condition_id, member.id)
            if not c:
                raise DomainError("NOT_FOUND", "No such condition of yours.")
            expected = digest({"id": c["id"], "version": c["version"], "value": json.loads(c["value_json"])})
            if verify_digest != expected:
                raise DomainError("STALE", "The condition changed since you opened the page. Reload and check it again.")
            if c["status"] == "active":
                return {"outcome": "already_active", "condition": self._condition_view(c).model_dump()}
            # A new condition replaces the person's earlier one of the same name in the same plan (busy times add up instead).
            if c["kind"] not in ("unavailable_window",):
                self.db.run("UPDATE conditions SET status = 'retired', retired_at = ? WHERE owner_id = ? AND name = ? AND group_id = ? "
                            "AND (plan_id IS ? OR plan_id = ?) AND status = 'active' AND id <> ?", iso_now(), member.id, c["name"], c["group_id"],
                            c["plan_id"], c["plan_id"], c["id"])
            self.db.run("UPDATE conditions SET status = 'active', verified_at = ?, version = version + 1 WHERE id = ?", iso_now(), c["id"])
            plan = self._plan_row(c["plan_id"]) if c["plan_id"] else None
            self._audit(member.id, "condition.verified", "plan", c["plan_id"] or c["group_id"], plan, f"{member.name} confirmed a private condition.",
                        private_to=member.id, group_id=c["group_id"])
            self._reassess(c["group_id"], "a member's conditions changed.", c["plan_id"])
            return {"outcome": "active", "condition": self._condition_view(self.db.get("SELECT * FROM conditions WHERE id = ?", c["id"])).model_dump()}
        return self.db.tx(run)

    def human_set_condition(self, member: Any, kind: str, value: dict[str, Any], plan_id: str | None = None) -> dict[str, Any]:
        """Typed by the person on their own page: active immediately (they are the owner)."""
        def run() -> dict[str, Any]:
            res = self.set_my_conditions(member.id, [{"kind": kind, "value": value}], plan=plan_id, source="human", notify=False)
            cid = res.conditions[0].id
            c = self.db.get("SELECT * FROM conditions WHERE id = ?", cid)
            return self.human_verify_condition(member, cid, digest({"id": c["id"], "version": c["version"], "value": json.loads(c["value_json"])}))
        return self.db.tx(run)

    def human_challenge(self, member: Any, scope_id: str) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            scope_row = self.db.get("SELECT * FROM approval_scopes WHERE id = ?", scope_id)
            if not scope_row or scope_row["member_id"] != member.id:
                raise DomainError("FORBIDDEN", "You can only confirm your own part of a plan.")
            rev = self._revision(scope_row["revision_id"])
            plan = self._plan_row(rev["plan_id"])
            if rev["id"] != plan["candidate_revision_id"] or not self._round_open(rev):
                raise DomainError("STALE_CANDIDATE", "This version is no longer waiting for confirmation.")
            nonce = new_secret(16)
            cid = new_id("ch")
            expires = (now() + CHALLENGE_TTL).astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            self.db.run("INSERT INTO challenges (id, member_id, revision_id, scope_id, scope_digest, candidate_digest, nonce_hash, created_at, expires_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", cid, member.id, rev["id"], scope_id, scope_row["scope_digest"], rev["content_digest"],
                        hash_secret(nonce), iso_now(), expires)
            return {"challenge_id": cid, "nonce": nonce, "scope_digest": scope_row["scope_digest"], "candidate_digest": rev["content_digest"],
                    "text": self._scope_text(scope_row), "expires_at": expires}
        return self.db.tx(run)

    def human_decide(self, member: Any, challenge_id: str, nonce: str, decision: str, condition: dict[str, Any] | None = None,
                     reason: str | None = None, new_limit: dict[str, Any] | None = None) -> dict[str, Any]:
        if decision not in ("approved", "rejected", "conditional"):
            raise DomainError("INVALID_ARGUMENT", "decision must be approved, rejected or conditional.")

        def run() -> dict[str, Any]:
            ch = self.db.get("SELECT * FROM challenges WHERE id = ?", challenge_id)
            if not ch or ch["member_id"] != member.id:
                raise DomainError("FORBIDDEN", "That confirmation request is not yours.")
            if ch["used_at"]:
                raise DomainError("CHALLENGE_USED", "This confirmation request was already used. Open the item again.")
            if ch["expires_at"] < iso_now():
                raise DomainError("CHALLENGE_EXPIRED", "This confirmation request expired. Open the item again.")
            if hash_secret(nonce or "") != ch["nonce_hash"]:
                raise DomainError("FORBIDDEN", "The confirmation request does not match.")
            self.db.run("UPDATE challenges SET used_at = ? WHERE id = ?", iso_now(), ch["id"])
            scope_row = self.db.get("SELECT * FROM approval_scopes WHERE id = ?", ch["scope_id"])
            rev = self._revision(ch["revision_id"])
            plan = self._plan_row(rev["plan_id"])
            if scope_row["scope_digest"] != ch["scope_digest"] or rev["content_digest"] != ch["candidate_digest"] or rev["id"] != plan["candidate_revision_id"]:
                raise DomainError("STALE_CANDIDATE", "What you were asked to confirm has changed. Nothing was recorded.")
            if decision == "conditional" and not condition:
                raise DomainError("INVALID_ARGUMENT", "A conditional answer needs a condition, e.g. a maximum total.")
            rid = new_id("rcpt")
            self.db.run("INSERT INTO approval_receipts (id, plan_id, revision_id, scope_id, member_id, decision, condition_json, reason, scope_digest, candidate_digest, "
                        "challenge_id, fingerprint, status, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'valid', ?, ?)",
                        rid, plan["id"], rev["id"], scope_row["id"], member.id, decision, json.dumps(condition) if condition else None, reason,
                        scope_row["scope_digest"], rev["content_digest"], ch["id"], self.fingerprint(plan, member.id), iso_now(), self._expires_at(plan))
            what = self._activity_title(rev, scope_row["activity_id"])
            word = {"approved": "confirmed", "rejected": "declined", "conditional": "answered with a condition on"}[decision]
            self._audit(member.id, f"approval.{decision}", "plan", plan["id"], plan, f"{member.name} {word} {what} in {rev['label']}.")
            if new_limit:
                # A refusal becomes the person's own new condition, used by the next search for alternatives.
                self.human_set_condition(member, new_limit["kind"], new_limit["value"], plan["id"])
            if not any(st == "pending" for sc, st, _ in self._requirements(rev) if sc["member_id"] == member.id):
                # They answered everything asked of them, on the page itself: "please confirm" is no longer news for Alexa to read out.
                self._mark_stale_asks(plan["id"], [member.id])
            plan = self._plan_row(plan["id"])
            if plan["candidate_revision_id"] == rev["id"]:
                view = self._confirmations(rev)
                blockers = self.gate(plan, rev)
                if view.complete and not blockers and plan["status"] != "READY_TO_COMMIT":
                    plan = self._bump(plan["id"], "READY_TO_COMMIT", None)
                    self._notify([plan["organizer_id"]], plan, "ready", f"Everyone needed confirmed {plan['title']} {rev['label']}. It can be committed.")
                elif not view.complete and plan["status"] in ("READY_TO_COMMIT", "PROPOSED"):
                    plan = self._bump(plan["id"], "AWAITING_CONFIRMATIONS", None)
                else:
                    plan = self._bump(plan["id"])
            if plan["organizer_id"] != member.id:
                self._notify([plan["organizer_id"]], plan, "answer", f"{member.name} {word} {what} in {plan['title']} {rev['label']}.")
            return {"outcome": decision, "receipt_id": rid, "plan_status": plan["status"]}
        return self.db.tx(run)

    def human_revoke(self, member: Any, receipt_id: str) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            r = self.db.get("SELECT * FROM approval_receipts WHERE id = ? AND member_id = ?", receipt_id, member.id)
            if not r:
                raise DomainError("NOT_FOUND", "No such answer of yours.")
            if r["status"] != "valid":
                return {"outcome": "already_" + r["status"]}
            self.db.run("UPDATE approval_receipts SET status = 'revoked', revoked_at = ? WHERE id = ?", iso_now(), r["id"])
            plan = self._plan_row(r["plan_id"])
            rev = self._revision(r["revision_id"])
            what = self._activity_title(rev, self.db.scalar("SELECT activity_id FROM approval_scopes WHERE id = ?", r["scope_id"]))
            self._audit(member.id, "approval.revoked", "plan", plan["id"], plan, f"{member.name} withdrew from {what} in {rev['label']}.")
            depends = [rev["id"]] + [x["target_revision_id"] for x in self.db.all("SELECT target_revision_id FROM approval_carry_forwards WHERE receipt_id = ?", r["id"])]
            if plan["active_revision_id"] in depends and plan["status"] == "COMMITTED":
                plan = self._bump(plan["id"], "DEGRADED", f"{member.name} withdrew from {what}.")
                self._notify(self.plan_audience(plan), plan, "degraded", f"{plan['title']}: {member.name} withdrew from {what}. The agreed plan needs attention.")
            elif plan["candidate_revision_id"] in depends and plan["status"] == "READY_TO_COMMIT":
                plan = self._bump(plan["id"], "AWAITING_CONFIRMATIONS", f"{member.name} withdrew a confirmation.")
            else:
                plan = self._bump(plan["id"])
            return {"outcome": "revoked", "plan_status": plan["status"]}
        return self.db.tx(run)


__all__ = ["Agreement", "snapshot_from_dict", "snapshot_to_dict", "SATISFIED", "BLOCKING", "UNSET"]
