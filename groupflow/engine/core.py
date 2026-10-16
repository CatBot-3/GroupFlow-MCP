"""The GroupFlow engine, part 1: groups, spaces, plans and their items.

The language model never decides what is true. It reports what a person said
(the item, the new value, how firm it was, their words); this engine decides,
from the item's consent policy, whether that changes the plan, waits for the
item's owner, or stays a suggestion, and who is allowed to see the result.

Visibility: a plan lives in one space of its group, and only the people in
that space can see the plan, its items, its history and anything caused by it.
A change in a hidden plan can flag an item in a plan someone else sees (the
surprise party's date and the family's cover story), but the flag and its
cause stay invisible to anyone who cannot see both.

Listeners: when the host says other people will hear or read the reply (a group
chat, a shared kitchen device), the speaker's scope narrows to what every one of
those listeners may see. The restriction can only remove access, never add it.
"""
from __future__ import annotations

import contextvars
import json
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from groupflow.core.db import Db
from groupflow.core.errors import DomainError
from groupflow.core.identity import Identity
from groupflow.core.util import iso_now, now as util_now
from groupflow.templates import Template, get_template, load_templates

from . import schema  # noqa: F401  (registers the tables)
from .text import (
    canonical, dates_text, humanize, normalize_slot_key, same_value, slugify, stem_word, topic_stems, bare_weekday_dates, parse_datetime,
    vague_when,
)
from .views import (
    BlockerPath, ChangeResult, ConditionView, ContextView, DecisionResult, DetailView, ExplainView, FlagView, HistoryEntry, HistoryView,
    ImpactView, LinkResult, PlaceHit, PlanView, PlanSummaryView, RequestView, SearchHit, SearchView, SuggestionView, TaskView, TodoView,
    TemplateListView, TemplateSpecView, TemplateSummaryView, PersonalTaskView,
)

UNSET: Any = object()
"""Marks an optional argument that was not given (distinct from an explicit None)."""

LISTENERS: contextvars.ContextVar[tuple[str, frozenset[str]] | None] = contextvars.ContextVar("groupflow_listeners", default=None)
"""(speaker, everyone who will hear the reply) for the current MCP call, set by the server from the host's declaration."""

STATUS_WORDS = {"todo": "not started", "in_progress": "in progress", "done": "done"}
POLICY_WORDS = {"owner": "owner", "open": "anyone", "participants": "everyone taking part"}
Row = dict[str, Any]


@dataclass
class Scope:
    """What one caller may see. Built from the authenticated identity, never from tool arguments."""
    member_id: str | None
    name: str
    spaces: set[str] = field(default_factory=set)
    plans: dict[str, Row] = field(default_factory=dict)
    listeners: frozenset[str] | None = None  # set in a shared conversation: everyone who will hear the reply, the speaker included

    def sees_plan(self, plan_id: str | None) -> bool:
        return bool(plan_id) and plan_id in self.plans


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


def _names(names: list[str]) -> str:
    names = list(dict.fromkeys(names))
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


class Core:
    def __init__(self, db: Db, identity: Identity | None = None, now: Callable[[], str] | None = None):
        self.db = db
        self.identity = identity or Identity(db)
        self._now_override: Callable[[], str] | None = now
        self._rate: dict[str, list[float]] = {}

    # ================================================================ clock, ids, templates

    def ts(self) -> str:
        return self._now_override() if self._now_override else iso_now()

    def set_clock(self, fn: Callable[[], str] | None) -> None:
        self._now_override = fn

    @staticmethod
    def new_id(prefix: str) -> str:
        return f"{prefix}_{secrets.token_hex(4)}"

    def template(self, plan: Row) -> Template:
        saved = self.db.get("SELECT template_json FROM plan_extensions WHERE plan_id = ?", plan["id"])
        if saved:
            return Template.model_validate_json(saved["template_json"])
        return get_template(plan["template"])

    @staticmethod
    def templates() -> dict[str, Template]:
        return load_templates()

    def today(self, tz: str = "America/Toronto") -> datetime:
        if self._now_override:
            stamp = datetime.fromisoformat(self._now_override().replace("Z", "+00:00"))
        else:
            stamp = util_now()
        return stamp.astimezone(ZoneInfo(tz))

    # ================================================================ row helpers

    def _member(self, id: str) -> Row | None:
        return self.db.get("SELECT * FROM members WHERE id = ?", id)

    def name(self, id: str | None) -> str:
        if not id:
            return "someone"
        m = self._member(id)
        return m["name"] if m else id

    def _group(self, id: str) -> Row | None:
        return self.db.get("SELECT * FROM groups WHERE id = ?", id)

    def _space(self, id: str) -> Row | None:
        return self.db.get("SELECT * FROM spaces WHERE id = ?", id)

    def _plan_row(self, id: str) -> Row | None:
        return self.db.get("SELECT * FROM plans WHERE id = ?", id)

    def _item(self, id: str) -> Row | None:
        return self.db.get("SELECT * FROM items WHERE id = ?", id)

    def _fact(self, id: str | None) -> Row | None:
        return self.db.get("SELECT * FROM facts WHERE id = ?", id) if id else None

    def _request(self, id: str) -> Row | None:
        return self.db.get("SELECT * FROM requests WHERE id = ?", id)

    def _space_members(self, space_id: str) -> list[str]:
        return [r["member_id"] for r in self.db.all(
            "SELECT sm.member_id FROM space_members sm JOIN members m ON m.id = sm.member_id WHERE sm.space_id = ? ORDER BY m.rowid", space_id)]

    def _in_space(self, member_id: str, space_id: str) -> bool:
        return self.db.get("SELECT 1 AS x FROM space_members WHERE space_id = ? AND member_id = ?", space_id, member_id) is not None

    def _group_role(self, group_id: str, member_id: str) -> Row | None:
        return self.db.get("SELECT * FROM group_members WHERE group_id = ? AND member_id = ? AND status = 'active'", group_id, member_id)

    def plan_audience(self, plan: Row) -> list[str]:
        return self._space_members(plan["space_id"])

    def _current_value(self, item: Row) -> str | None:
        f = self._fact(item["current_fact_id"])
        return f["value"] if f else None

    def _open_requests(self, item_id: str) -> list[Row]:
        return self.db.all("SELECT * FROM requests WHERE item_id = ? AND status = 'open' ORDER BY created_at, id", item_id)

    # ================================================================ scope and permissions

    def scope_for(self, member_id: str) -> Scope:
        m = self._member(member_id)
        if not m:
            raise DomainError("UNAUTHENTICATED", "Unknown member.")
        spaces = {r["space_id"] for r in self.db.all("SELECT space_id FROM space_members WHERE member_id = ?", member_id)}
        listeners = self._listeners(member_id)
        if listeners:
            # Only spaces every listener belongs to: a plan some listener may not see is not in this conversation at all,
            # exactly as if it did not exist, and a private space never qualifies.
            spaces = {s for s in spaces if listeners <= set(self._space_members(s))}
        plans = {p["id"]: p for p in self.db.all("SELECT * FROM plans WHERE status <> 'CANCELLED' ORDER BY created_at, rowid") if p["space_id"] in spaces}
        return Scope(member_id=member_id, name=m["name"], spaces=spaces, plans=plans, listeners=listeners)

    def _listeners(self, member_id: str | None = None) -> frozenset[str] | None:
        """Everyone who will hear the current reply, when the host declared more people than the speaker. Applies only to the speaker's own scope."""
        value = LISTENERS.get()
        if not value:
            return None
        speaker, listeners = value
        if member_id is not None and member_id != speaker:
            return None
        return listeners if len(listeners) > 1 else None

    def resolve_listeners(self, member_id: str, declared: str) -> frozenset[str]:
        """Parse a host's listener declaration: comma-separated member ids and/or group:<id> (every active member of a group the speaker is in).
        Raises ValueError for anything malformed or unknown, so a host bug is visible instead of silently widening or narrowing."""
        out = {member_id}
        parts = [p.strip() for p in (declared or "").split(",") if p.strip()]
        if len(parts) > 50:
            raise ValueError("Too many listeners.")
        for part in parts:
            if part.startswith("group:"):
                gid = part[6:].strip()
                if not self._group_role(gid, member_id):
                    raise ValueError(f"The speaker is not an active member of group {gid!r}.")
                out |= {r["member_id"] for r in self.db.all("SELECT member_id FROM group_members WHERE group_id = ? AND status = 'active'", gid)}
            elif self._member(part):
                out.add(part)
            else:
                raise ValueError(f"Unknown listener {part!r}.")
        return frozenset(out)

    def inspector_scope(self, group_id: str | None = None) -> Scope:
        """Demo inspector only: sees every plan (of one group). Never reachable through MCP."""
        rows = self.db.all("SELECT * FROM plans WHERE status <> 'CANCELLED' ORDER BY created_at, rowid")
        plans = {p["id"]: p for p in rows if group_id is None or p["group_id"] == group_id}
        spaces = {r["id"] for r in self.db.all("SELECT id, group_id FROM spaces") if group_id is None or r["group_id"] == group_id}
        return Scope(member_id=None, name="Inspector", spaces=spaces, plans=plans)

    def is_organizer(self, plan: Row, member_id: str | None) -> bool:
        if not member_id:
            return False
        if plan["organizer_id"] == member_id:
            return True
        role = self._group_role(plan["group_id"], member_id)
        return bool(role and role["role"] == "organizer" and self._in_space(member_id, plan["space_id"]))

    def _require_organizer(self, plan: Row, member_id: str, action: str) -> None:
        if not self.is_organizer(plan, member_id):
            raise DomainError("FORBIDDEN", f"Only the plan's organizer ({self.name(plan['organizer_id'])}) can {action}.")

    def _check_version(self, plan: Row, expected: int | None) -> None:
        if expected is not None and int(expected) != int(plan["aggregate_version"]):
            raise DomainError("VERSION_CONFLICT", f"{plan['title']} changed (now version {plan['aggregate_version']}). Read it again with get_context.",
                              {"current_version": plan["aggregate_version"], "status": plan["status"]})

    def reset_rate_limits(self) -> None:
        self._rate.clear()

    def _rate_limit(self, member_id: str, per_minute: int = 60) -> None:
        import time

        # Nested engine calls belong to the same outer API operation (e.g.
        # bounded recurrence expansion), not separate client requests.
        if self.db.in_transaction:
            return

        window = [t for t in self._rate.get(member_id, []) if t > time.monotonic() - 60]
        if len(window) >= per_minute:
            raise DomainError("RATE_LIMITED", "Too many changes in a minute. Wait a moment and try again.")
        window.append(time.monotonic())
        self._rate[member_id] = window

    # ================================================================ resolution of names the model passes

    def resolve_plan(self, scope: Scope, ref: str | None, prefer: Callable[[Row], bool] | None = None, purpose: str = "") -> Row:
        plans = list(scope.plans.values())
        if not plans:
            raise DomainError("NOT_FOUND", "You have no plans yet.")
        if ref:
            r = ref.strip().lower()
            hit = next((p for p in plans if p["id"] == ref.strip()), None) or next((p for p in plans if p["title"].lower() == r), None)
            if not hit:
                words = set(topic_stems(r))
                scored = sorted(((len(words & set(topic_stems(p["title"]))), p) for p in plans), key=lambda x: -x[0])
                if scored and scored[0][0] and (len(scored) == 1 or scored[0][0] > scored[1][0]):
                    hit = scored[0][1]
            if not hit:
                raise DomainError("NOT_FOUND", "No such plan is available to you. Your plans: "
                                  + "; ".join(f"{p['title']} (id {p['id']})" for p in plans) + ".")
            return hit
        if len(plans) == 1:
            return plans[0]
        if prefer:
            fitting = [p for p in plans if prefer(p)]
            if len(fitting) == 1:
                return fitting[0]
            if fitting:
                plans = fitting
        raise DomainError("AMBIGUOUS_PLAN", f"You have several plans{(' where ' + purpose) if purpose else ''}: "
                          + "; ".join(f"{p['title']} (id {p['id']})" for p in plans) + ". Pass plan to say which one.",
                          {"plans": [{"id": p["id"], "title": p["title"]} for p in plans]})

    def _visible_items(self, scope: Scope, plan_id: str | None = None, shapes: tuple[str, ...] = ("detail", "task", "activity")) -> list[Row]:
        marks = ",".join("?" for _ in shapes)
        rows = self.db.all(f"SELECT * FROM items WHERE archived = 0 AND shape IN ({marks}) ORDER BY created_at, rowid", *shapes)
        return [r for r in rows if r["plan_id"] in scope.plans and (plan_id is None or r["plan_id"] == plan_id)]

    def resolve_item(self, scope: Scope, ref: str, plan_id: str | None = None, shapes: tuple[str, ...] = ("detail", "task", "activity"),
                     required: bool = True) -> Row | None:
        if not (ref or "").strip():
            if required:
                raise DomainError("INVALID_ARGUMENT", "Say which item.")
            return None
        items = self._visible_items(scope, plan_id, shapes)
        raw = ref.strip()
        lower = raw.lower()
        key = normalize_slot_key(raw)

        def hit(t: str, hay: list[str]) -> bool:
            return any(w == t or (min(len(w), len(t)) >= 4 and (w.startswith(t) or t.startswith(w))) for w in hay)

        def hay(i: Row) -> list[str]:
            return topic_stems(f'{i["label"]} {i["id"].replace(".", " ").replace("_", " ")} {" ".join(json.loads(i["aliases"] or "[]"))}', False)

        def tiers() -> list[list[Row]]:
            exact = [i for i in items if i["id"] == raw or i["id"] == key]
            label = [i for i in items if i["label"].lower() == lower]
            alias = [i for i in items if lower in [a.lower() for a in json.loads(i["aliases"] or "[]")]]
            suffix = [i for i in items if i["id"].split(".")[-1] == key.split(".")[-1] and len(key) >= 3]
            # "party.cake": a plan, then an item of that plan. Models build ids like this by analogy with ones they saw
            # (party.date, party.venue), so the part after the dot is matched inside that plan only.
            qualified: list[Row] = []
            head, _, rest = raw.partition(".")
            if rest.strip():
                head_stems = set(topic_stems(head.replace("_", " "), False))
                plans_hit = {p["id"] for p in scope.plans.values()
                             if p["id"] == normalize_slot_key(head) or (head_stems and head_stems <= set(topic_stems(p["title"], False)))}
                pool = [i for i in items if i["plan_id"] in plans_hit]
                rest_l = rest.strip().lower().replace("_", " ")
                rest_words = [w for w in topic_stems(rest_l) if w not in ("item", "task", "thing")]
                qualified = [i for i in pool if i["label"].lower() == rest_l or rest_l in [a.lower() for a in json.loads(i["aliases"] or "[]")]]
                if not qualified and rest_words:
                    qualified = [i for i in pool if all(hit(t, hay(i)) for t in rest_words)]
                    # A word that is a whole label ("cake" -> Cake) beats one that only appears in titles ("Order the cake").
                    whole = [i for i in qualified if topic_stems(i["label"], False) == rest_words]
                    qualified = whole or qualified
            words = [w for w in topic_stems(lower) if w not in ("item", "task", "thing")]
            fuzzy: list[Row] = []
            if words and "." not in raw:
                fuzzy = [i for i in items if all(hit(t, hay(i)) for t in words)]
            return [exact, label, alias, suffix, qualified, fuzzy]

        for tier in tiers():
            if len(tier) == 1:
                return tier[0]
            if len(tier) > 1:
                raise DomainError("AMBIGUOUS_ITEM", f'"{ref}" could mean ' + "; ".join(
                    f'{i["label"]} (id {i["id"]}, {scope.plans[i["plan_id"]]["title"]})' for i in tier[:6]) + ". Pass the id.")
        if required:
            raise DomainError("NOT_FOUND", f'No item called "{ref}" is available to you. Check get_context or search_plan for its id.')
        return None

    def _similar_items(self, scope: Scope, ref: str, plan_id: str | None) -> str:
        """Up to four visible items sharing a word with ref, for a helpful NOT_FOUND."""
        words = set(topic_stems(ref.replace(".", " ").replace("_", " ")))
        # Words that only name a plan ("party" in "party.cake") would match everything in it.
        plan_words = {w for p in scope.plans.values() for w in topic_stems(f'{p["title"]} {p["id"].replace("_", " ")}', False)}
        words = (words - plan_words) or words
        if not words:
            return ""
        scored = []
        for n, i in enumerate(self._visible_items(scope, plan_id, ("detail", "task", "activity"))):
            score = 2 * len(words & set(topic_stems(i["label"], False))) + len(words & set(topic_stems(i["id"].replace(".", " ").replace("_", " "), False)))
            if score:
                scored.append((-score, n, f'{i["label"]} (id {i["id"]}, {scope.plans[i["plan_id"]]["title"]})'))
        return "; ".join(s for _, _, s in sorted(scored)[:4])

    def resolve_member(self, scope: Scope, ref: str, plan: Row | None = None) -> Row:
        lower = ref.strip().lower()
        if plan is not None:
            candidates = [self._member(m) for m in self.plan_audience(plan)]
        else:
            candidates = self.db.all("""SELECT DISTINCT m.* FROM members m JOIN space_members sm ON sm.member_id = m.id
                                        WHERE sm.space_id IN (SELECT space_id FROM space_members WHERE member_id = ?)""", scope.member_id)
        if lower in ("me", "myself", "i", "you"):
            hit = next((m for m in candidates if m and m["id"] == scope.member_id), None)
        else:
            hit = next((m for m in candidates if m and (m["id"] == ref.strip() or m["name"].lower() == lower)), None)
        if not hit and plan is not None:
            for m in candidates:
                role = self._group_role(plan["group_id"], m["id"]) if m else None
                if role and role["label"] and stem_word(role["label"].split()[-1].lower()) == stem_word(lower.split()[-1]):
                    hit = m
                    break
        if not hit:
            where = f" in {plan['title']}" if plan is not None else ""
            raise DomainError("NOT_FOUND", f'No one called "{ref}" can see this plan{where}.' if plan is not None else f'No one called "{ref}" is available to you.')
        return hit

    # ================================================================ records

    def _record_utterance(self, member_id: str, space_id: str, text: str | None) -> str | None:
        if not text or not text.strip():
            return None
        uid = self.new_id("u")
        self.db.run("INSERT INTO utterances (id, member_id, space_id, text, at) VALUES (?, ?, ?, ?, ?)", uid, member_id, space_id, text.strip(), self.ts())
        return uid

    def _audit(self, actor: str | None, action: str, target_type: str, target_id: str, plan: Row | None, detail: str,
               before: str | None = None, after: str | None = None, utterance_id: str | None = None, private_to: str | None = None,
               payload: dict[str, Any] | None = None, group_id: str | None = None) -> None:
        self.db.run(
            """INSERT INTO audit (at, actor_id, group_id, plan_id, space_id, action, target_type, target_id, detail, before_value, after_value,
                                  utterance_id, private_to, aggregate_version, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            self.ts(), actor, plan["group_id"] if plan else group_id, plan["id"] if plan else None, plan["space_id"] if plan else None,
            action, target_type, target_id, detail, before, after, utterance_id, private_to, plan["aggregate_version"] if plan else None,
            json.dumps(payload or {}),
        )

    def _notify(self, member_ids: list[str], plan: Row | None, kind: str, text: str, space_id: str | None = None) -> None:
        for m in dict.fromkeys(member_ids):
            self.db.run("INSERT INTO notifications (id, member_id, plan_id, space_id, kind, text, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        self.new_id("n"), m, plan["id"] if plan else None, space_id or (plan["space_id"] if plan else None), kind, text, self.ts())

    def _insert_fact(self, item: Row, value: str, certainty: str, status: str, by: str, utterance_id: str | None, decided: bool) -> Row:
        fid = self.new_id("f")
        stamp = self.ts()
        self.db.run(
            """INSERT INTO facts (id, item_id, value, norm, certainty, status, proposed_by, utterance_id, created_at, decided_by, decided_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            fid, item["id"], value, canonical(item["value_kind"], value), certainty, status, by, utterance_id, stamp,
            by if decided else None, stamp if decided else None,
        )
        return self._fact(fid)  # type: ignore[return-value]

    def _upsert_flag(self, target: Row, cause: Row, owner_id: str, message: str) -> None:
        cause_plan = self._plan_row(cause["plan_id"])
        existing = self.db.get("SELECT * FROM flags WHERE target_id = ? AND cause_id = ? AND status = 'open'", target["id"], cause["id"])
        if existing:
            self.db.run("UPDATE flags SET message = ?, owner_id = ?, created_at = ? WHERE id = ?", message, owner_id, self.ts(), existing["id"])
        else:
            self.db.run("INSERT INTO flags (id, target_id, cause_id, space_id, owner_id, message, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'open', ?)",
                        self.new_id("g"), target["id"], cause["id"], cause_plan["space_id"], owner_id, message, self.ts())

    def _clear_flags(self, target_id: str, by: str) -> int:
        return self.db.run("UPDATE flags SET status = 'cleared', cleared_at = ?, cleared_by = ? WHERE target_id = ? AND status = 'open'",
                           self.ts(), by, target_id)

    def _flag_visible(self, scope: Scope, f: Row) -> bool:
        target = self._item(f["target_id"])
        cause = self._item(f["cause_id"])
        return bool(target and cause and scope.sees_plan(target["plan_id"]) and scope.sees_plan(cause["plan_id"]) and f["space_id"] in scope.spaces)

    def _flags_for(self, scope: Scope, item_id: str) -> list[str]:
        rows = self.db.all("SELECT * FROM flags WHERE target_id = ? AND status = 'open' ORDER BY created_at", item_id)
        return [f["message"] for f in rows if self._flag_visible(scope, f)]

    def _quote_for(self, scope: Scope, utterance_id: str | None) -> str | None:
        if not utterance_id:
            return None
        u = self.db.get("SELECT * FROM utterances WHERE id = ?", utterance_id)
        return u["text"] if u and u["space_id"] in scope.spaces else None

    # ================================================================ setup (world seeding and create_plan)

    def add_group(self, id: str, name: str, timezone_name: str, currency: str, members: list[dict[str, Any]], description: str | None = None) -> None:
        def write() -> None:
            self.db.run("INSERT INTO groups (id, name, timezone, currency, description) VALUES (?, ?, ?, ?, ?)", id, name, timezone_name, currency, description)
            for m in members:
                self.db.run("INSERT INTO group_members (group_id, member_id, role, label, confirmation_mode) VALUES (?, ?, ?, ?, ?)",
                            id, m["id"], m.get("role", "member"), m.get("label"), m.get("confirmation_mode", "scoped"))
            everyone = f"{id}.everyone"
            self.add_space(everyone, id, name, "shared", [m["id"] for m in members], f"Everyone in {name}")
            for m in members:
                self.add_space(f"{id}.private.{m['id']}", id, f"{self.name(m['id'])}'s private notes", "private", [m["id"]])
        self.db.tx(write)

    def add_space(self, id: str, group_id: str, name: str, kind: str, members: list[str], description: str | None = None) -> None:
        def write() -> None:
            self.db.run("INSERT INTO spaces (id, group_id, name, kind, description) VALUES (?, ?, ?, ?, ?)", id, group_id, name, kind, description)
            for m in members:
                self.db.run("INSERT INTO space_members (space_id, member_id) VALUES (?, ?)", id, m)
        self.db.tx(write)

    def _space_for_audience(self, group_id: str, audience: list[str]) -> str:
        """The space holding exactly these people in the group; created if missing."""
        want = set(audience)
        for s in self.db.all("SELECT * FROM spaces WHERE group_id = ? AND kind = 'shared' ORDER BY rowid", group_id):
            if set(self._space_members(s["id"])) == want:
                return s["id"]
        names = [self.name(m) for m in audience]
        sid = f"{group_id}.space_{secrets.token_hex(3)}"
        self.add_space(sid, group_id, _names(names), "shared", audience, f"Only {_names(names)}")
        return sid

    def is_empty(self) -> bool:
        return (self.db.scalar("SELECT COUNT(*) FROM groups") or 0) == 0

    def _new_plan_id(self, title: str) -> str:
        base = slugify(title, 24) or "plan"
        pid = base
        while self._plan_row(pid):
            pid = f"{base}_{secrets.token_hex(2)}"
        return pid

    def _new_item_id(self, plan: Row, name: str) -> str:
        key = normalize_slot_key(name) or "item"
        if "." not in key:
            key = f"{plan['id'].split('_')[0]}.{key}"
        out = key
        while self._item(out):
            out = f"{key}_{secrets.token_hex(2)}"
        return out

    def _insert_item(self, plan: Row, shape: str, type_name: str, label: str, owner_id: str, created_by: str, item_id: str | None = None,
                     value_kind: str = "text", aliases: list[str] | None = None, status: str | None = None, due: str | None = None,
                     policy: str | None = None) -> Row:
        t = self.template(plan)
        it = t.item_type(type_name) or t.default_type(shape)
        if it is None:
            raise DomainError("INVALID_ARGUMENT", f"{plan['title']} has no {shape} items. Item types: {', '.join(x.name for x in t.item_types)}.")
        iid = item_id or (self._new_item_id(plan, label) if shape != "task" else f"t_{slugify(label, 24)}_{secrets.token_hex(2)}")
        if self._item(iid):
            raise DomainError("FORBIDDEN", "That item cannot be changed by you. Pick a different name.")
        stamp = self.ts()
        self.db.run(
            """INSERT INTO items (id, plan_id, shape, type, label, owner_id, policy, value_kind, aliases, version, current_fact_id, status, due,
                                  created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?, ?, ?, ?)""",
            iid, plan["id"], it.shape, it.name, label, owner_id, policy or it.policy, value_kind if it.shape == "detail" else "text",
            json.dumps(aliases or []), status if it.shape == "task" else None, due, created_by, stamp, stamp,
        )
        return self._item(iid)  # type: ignore[return-value]

    def _bump(self, plan_id: str, status: str | None = None, reason: str | None = None, **fields: Any) -> Row:
        sets = ["aggregate_version = aggregate_version + 1", "updated_at = ?"]
        params: list[Any] = [self.ts()]
        if status is not None:
            sets += ["status = ?", "status_reason = ?"]
            params += [status, reason]
        for k, v in fields.items():
            sets.append(f"{k} = ?")
            params.append(v)
        self.db.run(f"UPDATE plans SET {', '.join(sets)} WHERE id = ?", *params, plan_id)
        return self._plan_row(plan_id)  # type: ignore[return-value]

    # ================================================================ derived state: details and tasks

    def _detail_status(self, item: Row) -> str:
        reqs = self._open_requests(item["id"])
        if any(r["certainty"] == "committed" for r in reqs):
            return "disputed"
        if reqs:
            return "has_suggestions"
        return "confirmed" if item["current_fact_id"] else "unset"

    def _suggestions(self, scope: Scope, item: Row) -> list[SuggestionView]:
        out = []
        for r in self._open_requests(item["id"]):
            f = self._fact(r["fact_id"])
            out.append(SuggestionView(request=r["id"], value=f["value"] if f else r["summary"], by=self.name(r["requested_by"]),
                                      firm=r["certainty"] == "committed", said=self._quote_for(scope, r["utterance_id"])))
        return out

    def _detail_view(self, scope: Scope, item: Row) -> DetailView:
        return DetailView(id=item["id"], label=item["label"], value=self._current_value(item), owner=self.name(item["owner_id"]),
                          decided_by=POLICY_WORDS[item["policy"]], status=self._detail_status(item),  # type: ignore[arg-type]
                          pending=self._suggestions(scope, item), flags=self._flags_for(scope, item["id"]))

    def _links_from(self, item_id: str, kind: str | None = None) -> list[Row]:
        if kind:
            return self.db.all("SELECT * FROM links WHERE item_id = ? AND kind = ? ORDER BY depends_on_id", item_id, kind)
        return self.db.all("SELECT * FROM links WHERE item_id = ? ORDER BY depends_on_id", item_id)

    def _links_to(self, item_id: str, kind: str | None = None) -> list[Row]:
        if kind:
            return self.db.all("SELECT * FROM links WHERE depends_on_id = ? AND kind = ? ORDER BY item_id", item_id, kind)
        return self.db.all("SELECT * FROM links WHERE depends_on_id = ? ORDER BY item_id", item_id)

    def _blockers_of(self, scope: Scope, t: Row) -> list[tuple[Row, str]]:
        """Direct blockers of a task as seen by one viewer: (blocking item, reason). A task only looks blocked if the viewer can see why."""
        out: list[tuple[Row, str]] = []
        for link in self._links_from(t["id"]):
            u = self._item(link["depends_on_id"])
            if not u or u["archived"] or not scope.sees_plan(u["plan_id"]):
                continue
            if u["shape"] == "task":
                if u["status"] == "done":
                    continue
                flags = self._flags_for(scope, u["id"])
                out.append((u, flags[0] if flags else f'"{u["label"]}" is {STATUS_WORDS[u["status"]]} ({self.name(u["owner_id"])}).'))
            elif u["shape"] == "detail":
                status = self._detail_status(u)
                if status == "disputed":
                    r = next(r for r in self._open_requests(u["id"]) if r["certainty"] == "committed")
                    f = self._fact(r["fact_id"])
                    out.append((u, f'{self.name(r["requested_by"])} proposed "{f["value"] if f else r["summary"]}" for {u["label"]}; '
                                   f'waiting for {self.name(u["owner_id"])} to decide.'))
                elif status == "unset":
                    out.append((u, f'{u["label"]} has no confirmed value yet.'))
            elif u["shape"] == "activity":
                reason = self._activity_blocker(u)
                if reason:
                    out.append((u, reason))
        return out

    def _activity_blocker(self, item: Row) -> str | None:  # overridden by the agreement layer
        return None

    def _task_view(self, scope: Scope, t: Row) -> TaskView:
        blockers = [] if t["status"] == "done" else [reason for _, reason in self._blockers_of(scope, t)]
        return TaskView(id=t["id"], title=t["label"], type=t["type"], owner=self.name(t["owner_id"]), status=t["status"], due=t["due"],
                        decided_by=POLICY_WORDS[t["policy"]], blocked_by=blockers, pending=self._suggestions(scope, t),
                        waits_on=[x["depends_on_id"] for x in self._links_from(t["id"], "waits_on")
                                  if (u := self._item(x["depends_on_id"])) and not u["archived"] and scope.sees_plan(u["plan_id"])],
                        flags=self._flags_for(scope, t["id"]))

    def _roots_of(self, scope: Scope, task: Row) -> list[tuple[Row, str, list[str]]]:
        out: dict[str, tuple[Row, str, list[str]]] = {}

        def visit(t: Row, path: list[str], seen: set[str]) -> None:
            for u, reason in self._blockers_of(scope, t):
                if u["shape"] != "task":
                    out.setdefault(u["id"], (u, reason, [*path, u["label"]]))
                    continue
                if u["id"] in seen:
                    continue
                if not self._blockers_of(scope, u):
                    out.setdefault(u["id"], (u, reason, [*path, u["label"]]))
                else:
                    visit(u, [*path, u["label"]], seen | {u["id"]})

        visit(task, [task["label"]], {task["id"]})
        return list(out.values())

    def _request_view(self, scope: Scope, r: Row) -> RequestView:
        plan = self._plan_row(r["plan_id"])
        item = self._item(r["item_id"]) if r["item_id"] else None
        f = self._fact(r["fact_id"])
        if r["kind"] == "value" and item:
            current = self._current_value(item)
        elif r["kind"] == "task" and item:
            current = f'{STATUS_WORDS[item["status"]]}, {self.name(item["owner_id"])}' + (f', due {item["due"]}' if item["due"] else "")
        else:
            current = self._activity_now(r) if item or r["kind"] == "activity" else None
        return RequestView(request=r["id"], plan=plan["title"], item=item["id"] if item else None,
                           what=item["label"] if item else "a new activity", current=current,
                           proposed=f["value"] if f else r["summary"], by=self.name(r["requested_by"]), firm=r["certainty"] == "committed",
                           said=self._quote_for(scope, r["utterance_id"]))

    def _activity_now(self, r: Row) -> str | None:  # overridden by the agreement layer
        return None

    # ================================================================ propagation after a change

    def _propagate(self, scope: Scope, cause: Row, before: str | None, after: str, actor_id: str, impacts: ImpactView) -> None:
        """Tell everything watching `cause` that it changed: reopen finished tasks, flag the rest, notify their owners.
        History rows about it are kept with the cause's plan, so nobody who cannot see the cause can read them."""
        now = self.ts()
        cause_plan = self._plan_row(cause["plan_id"])
        touched: set[str] = set()
        for link in self._links_to(cause["id"], "watches"):
            w = self._item(link["item_id"])
            if not w or w["archived"]:
                continue
            if w["shape"] == "task":
                touched.add(w["id"])
                if w["status"] == "done":
                    self.db.run("UPDATE items SET status = 'todo', version = version + 1, updated_at = ? WHERE id = ?", now, w["id"])
                    self._upsert_flag(w, cause, w["owner_id"], f'Reopened: it was done for "{before}", but {cause["label"]} is now "{after}".')
                    self._audit(actor_id, "task.reopened", "item", w["id"], cause_plan,
                                f'Reopened because {cause["label"]} changed to "{after}".', before="done", after="todo")
                    if scope.sees_plan(w["plan_id"]):
                        impacts.reopened.append(w["label"])
                else:
                    self._upsert_flag(w, cause, w["owner_id"], f'{cause["label"]} changed from "{before}" to "{after}". Check this still works.')
                    self._audit(actor_id, "task.flagged", "item", w["id"], cause_plan, f'Flagged because {cause["label"]} changed to "{after}".')
                    if scope.sees_plan(w["plan_id"]):
                        impacts.flagged.append(w["label"])
            else:
                self._upsert_flag(w, cause, w["owner_id"],
                                  f'{cause["label"]} is now "{after}", but {w["label"]} still says "{self._current_value(w) or "nothing"}".')
                self._audit(actor_id, "item.flagged", "item", w["id"], cause_plan, f'Flagged because {cause["label"]} changed.')
                if scope.sees_plan(w["plan_id"]):
                    impacts.flagged.append(w["label"])
            if w["owner_id"] != actor_id and self.name(w["owner_id"]) not in impacts.notified:
                impacts.notified.append(self.name(w["owner_id"]))
        queue = sorted(touched)
        seen = set(queue)
        while queue:
            tid = queue.pop(0)
            for link in self._links_to(tid, "waits_on"):
                d = link["item_id"]
                if d in seen:
                    continue
                seen.add(d)
                queue.append(d)
                t = self._item(d)
                if t and t["status"] != "done" and d not in touched and scope.sees_plan(t["plan_id"]):
                    impacts.downstream.append(t["label"])

    # ================================================================ details

    def _apply_detail(self, scope: Scope, item: Row, previous: Row | None, fact: Row, actor_id: str, utterance_id: str | None) -> ImpactView:
        """Make `fact` the item's value and propagate: supersede, close competing requests, reopen and flag."""
        now = self.ts()
        plan = self._plan_row(item["plan_id"])
        actor = self.name(actor_id)
        if previous:
            self.db.run("UPDATE facts SET status = 'superseded' WHERE id = ?", previous["id"])
        self.db.run("UPDATE facts SET status = 'confirmed', decided_by = ?, decided_at = ? WHERE id = ?", actor_id, now, fact["id"])
        self.db.run("UPDATE items SET current_fact_id = ?, version = version + 1, updated_at = ? WHERE id = ?", fact["id"], now, item["id"])
        impacts = ImpactView()
        for r in self._open_requests(item["id"]):
            if r["fact_id"] == fact["id"]:
                continue
            f = self._fact(r["fact_id"])
            matches = bool(f) and same_value(item["value_kind"], f["norm"], fact["norm"])
            self.db.run("UPDATE requests SET status = ?, resolved_at = ?, resolved_by = ?, note = ? WHERE id = ?",
                        "accepted" if matches else "stale", now, actor_id, "matched the new value" if matches else "superseded by a later decision", r["id"])
            if f:
                self.db.run("UPDATE facts SET status = ? WHERE id = ?", "corroborating" if matches else "rejected", f["id"])
            if r["requested_by"] != actor_id:
                self._notify([r["requested_by"]], plan, "request",
                             f'{actor} confirmed {item["label"]}: "{fact["value"]}", matching your suggestion.' if matches
                             else f'{actor} set {item["label"]} to "{fact["value"]}", so your suggestion "{f["value"] if f else r["summary"]}" was closed.')
            self._audit(actor_id, "request.accepted" if matches else "request.stale", "item", item["id"], plan,
                        f'Request "{f["value"] if f else r["summary"]}" closed ({"matches new value" if matches else "superseded"}).')
        self._clear_flags(item["id"], actor_id)
        self._audit(actor_id, "value.confirmed", "item", item["id"], plan,
                    f'{item["label"]} changed from "{previous["value"]}" to "{fact["value"]}".' if previous else f'{item["label"]} set to "{fact["value"]}".',
                    before=previous["value"] if previous else None, after=fact["value"], utterance_id=utterance_id)
        if previous and not same_value(item["value_kind"], previous["norm"], fact["norm"]):
            self._propagate(scope, item, previous["value"], fact["value"], actor_id, impacts)
        return impacts

    def _change_detail(self, scope: Scope, actor_id: str, item: Row, value: str, certainty: str, utterance_id: str | None, created: bool) -> ChangeResult:
        plan = self._plan_row(item["plan_id"])
        actor = self.name(actor_id)
        val = value.strip()
        current = self._fact(item["current_fact_id"])
        norm = canonical(item["value_kind"], val)
        can_set = item["owner_id"] == actor_id or item["policy"] == "open"
        owner = self.name(item["owner_id"])

        if current and same_value(item["value_kind"], current["norm"], norm):
            self._insert_fact(item, val, certainty, "corroborating", actor_id, utterance_id, False)
            if item["owner_id"] == actor_id:
                self._clear_flags(item["id"], actor_id)
            self._audit(actor_id, "value.corroborated", "item", item["id"], plan, f'{actor} repeated the current value "{current["value"]}".',
                        utterance_id=utterance_id)
            return ChangeResult(outcome="corroborated", plan=plan["title"], item=item["id"],
                                message=f'"{val}" matches the current {item["label"]} ("{current["value"]}"). Nothing changed.')

        if can_set and certainty == "committed":
            f = self._insert_fact(item, val, "committed", "confirmed", actor_id, utterance_id, True)
            impacts = self._apply_detail(scope, item, current, f, actor_id, utterance_id)
            msg = f'{item["label"]} is now "{val}" (was "{current["value"]}").' if current else f'{item["label"]} is now "{val}".'
            return ChangeResult(outcome="confirmed", plan=plan["title"], item=item["id"], message=msg, impacts=impacts)

        dup = next((r for r in self._open_requests(item["id"]) if (f := self._fact(r["fact_id"])) and same_value(item["value_kind"], f["norm"], norm)), None)
        if dup:
            self._insert_fact(item, val, certainty, "corroborating", actor_id, utterance_id, False)
            self._audit(actor_id, "value.corroborated", "item", item["id"], plan, f'{actor} backed the pending proposal "{val}".', utterance_id=utterance_id)
            return ChangeResult(outcome="already_pending", plan=plan["title"], item=item["id"], request=self._request_view(scope, dup),
                                message=f'"{val}" is already waiting for {owner} to decide.')

        status = "pending_review" if certainty == "committed" else "tentative"
        f = self._insert_fact(item, val, certainty, status, actor_id, utterance_id, False)
        rid = self._open_request(plan, item, "value", {"value": val}, val, certainty, item["owner_id"], actor_id, utterance_id, item["version"], f["id"])
        self._audit(actor_id, "value.proposed" if certainty == "committed" else "value.tentative", "item", item["id"], plan,
                    f'{actor} proposed "{val}" for {item["label"]}; sent to {owner} to decide.' if certainty == "committed"
                    else f'{actor} floated "{val}" for {item["label"]} (tentative; not applied).', utterance_id=utterance_id)
        keep = f' "{current["value"]}" stays in force until then.' if current else ""
        if certainty == "committed":
            message = f'{owner} owns {item["label"]}; asked {"you" if item["owner_id"] == actor_id else owner} to confirm "{val}".{keep}'
        else:
            message = f'Recorded "{val}" as a suggestion only; {item["label"]} is unchanged.' + (f' It is still "{current["value"]}".' if current else "")
        return ChangeResult(outcome=status, plan=plan["title"], item=item["id"], message=message,  # type: ignore[arg-type]
                            request=self._request_view(scope, self._request(rid)))

    def _open_request(self, plan: Row, item: Row | None, kind: str, change: dict[str, Any], summary: str, certainty: str, assignee: str,
                      requested_by: str, utterance_id: str | None, base_version: int, fact_id: str | None = None) -> str:
        rid = self.new_id("rq")
        self.db.run(
            """INSERT INTO requests (id, plan_id, item_id, kind, fact_id, change_json, summary, certainty, assignee_id, requested_by, utterance_id,
                                     base_version, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
            rid, plan["id"], item["id"] if item else None, kind, fact_id, json.dumps(change), summary, certainty, assignee, requested_by,
            utterance_id, base_version, self.ts(),
        )
        if certainty == "committed" and assignee != requested_by:
            pass  # the assignee hears about it in their context (decisions_for_you); no separate notification
        return rid

    # ================================================================ tasks

    def _change_task(self, scope: Scope, actor_id: str, item: Row | None, plan: Row, *, title: str | None, owner: str | None, status: str | None,
                     due: Any, remove: bool, type_name: str | None, certainty: str, utterance_id: str | None, quote_space: str) -> ChangeResult:
        actor = self.name(actor_id)
        if isinstance(due, str) and not due.strip():
            due = None
        if status is not None and status not in STATUS_WORDS:
            raise DomainError("INVALID_ARGUMENT", 'status must be "todo", "in_progress" or "done".')
        if item is None:
            new_title = (title or "").strip()
            if not new_title:
                raise DomainError("INVALID_ARGUMENT", "A title is required to add a task.")
            owner_row = self.resolve_member(scope, owner, plan) if owner else self._member(actor_id)
            t = self.template(plan)
            it = t.item_type(type_name) if type_name else None
            if it is None or it.shape != "task":
                it = t.default_type("task")
            if it is None:
                raise DomainError("INVALID_ARGUMENT", f"{plan['title']} has no task items.")
            row = self._insert_item(plan, "task", it.name, new_title, owner_row["id"], actor_id, status=status or "todo",
                                    due=None if due is UNSET else due)
            self.db.run("UPDATE items SET version = 1 WHERE id = ?", row["id"])
            self._audit(actor_id, "task.created", "item", row["id"], plan, f'{actor} added "{new_title}" for {owner_row["name"]}.', utterance_id=utterance_id)
            if owner_row["id"] != actor_id:
                self._notify([owner_row["id"]], plan, "task", f'{actor} added a {it.label.lower()} for you in {plan["title"]}: "{new_title}".')
            return ChangeResult(outcome="created", plan=plan["title"], item=row["id"],
                                message=f'Added "{new_title}" for {owner_row["name"]} in {plan["title"]}.')

        may = item["owner_id"] == actor_id or item["policy"] == "open" or self.is_organizer(plan, actor_id)
        changes: dict[str, Any] = {}
        if status is not None and status != item["status"]:
            changes["status"] = status
        new_owner = None
        if owner:
            new_owner = self.resolve_member(scope, owner, plan)
            if new_owner["id"] != item["owner_id"]:
                changes["owner"] = new_owner["id"]
        if due is not UNSET and due != item["due"]:
            changes["due"] = due
        if title and title.strip() and title.strip() != item["label"]:
            changes["title"] = title.strip()
        if remove:
            changes = {"remove": True}

        if not may or certainty == "tentative":
            if not changes:
                return ChangeResult(outcome="corroborated", plan=plan["title"], item=item["id"], message=f'Nothing to change on "{item["label"]}".')
            summary = self._task_change_text(changes)
            dup = next((r for r in self._open_requests(item["id"]) if json.loads(r["change_json"]) == changes), None)
            if dup:
                return ChangeResult(outcome="already_pending", plan=plan["title"], item=item["id"], request=self._request_view(scope, dup),
                                    message=f'That change to "{item["label"]}" is already waiting for {self.name(item["owner_id"])}.')
            kind = "pending_review" if certainty == "committed" else "tentative"
            rid = self._open_request(plan, item, "task", changes, summary, certainty, item["owner_id"], actor_id, utterance_id, item["version"])
            self._audit(actor_id, "task.proposed", "item", item["id"], plan, f'{actor} asked to change "{item["label"]}": {summary}.', utterance_id=utterance_id)
            who = self.name(item["owner_id"])
            msg = (f'"{item["label"]}" belongs to {who}; asked {who} to decide: {summary}.' if kind == "pending_review"
                   else f'Recorded as a suggestion for {who}: {summary}. Nothing changed.')
            return ChangeResult(outcome=kind, plan=plan["title"], item=item["id"], message=msg,  # type: ignore[arg-type]
                                request=self._request_view(scope, self._request(rid)))
        return self._apply_task(scope, actor_id, item, plan, changes, utterance_id)

    @staticmethod
    def _task_change_text(changes: dict[str, Any], names: Callable[[str], str] | None = None) -> str:
        parts = []
        if changes.get("remove"):
            return "remove it"
        if "status" in changes:
            parts.append(f'mark it {STATUS_WORDS[changes["status"]]}')
        if "owner" in changes:
            parts.append(f'hand it to {names(changes["owner"]) if names else changes["owner"]}')
        if "due" in changes:
            parts.append(f'due {changes["due"] or "none"}')
        if "title" in changes:
            parts.append(f'rename it to "{changes["title"]}"')
        return ", ".join(parts) or "no change"

    def _apply_task(self, scope: Scope, actor_id: str, item: Row, plan: Row, changes: dict[str, Any], utterance_id: str | None) -> ChangeResult:
        actor = self.name(actor_id)
        now = self.ts()
        if changes.get("remove"):
            self.db.run("UPDATE items SET archived = 1, version = version + 1, updated_at = ? WHERE id = ?", now, item["id"])
            self._audit(actor_id, "task.removed", "item", item["id"], plan, f'{actor} removed "{item["label"]}".', utterance_id=utterance_id)
            return ChangeResult(outcome="removed", plan=plan["title"], item=item["id"], message=f'Removed "{item["label"]}" from {plan["title"]}.')
        if "owner" in changes and not self._in_space(changes["owner"], plan["space_id"]):
            raise DomainError("FORBIDDEN", f'{self.name(changes["owner"])} cannot see "{item["label"]}", so it cannot be given to them.')
        before_blocked: dict[str, bool] = {}
        for link in self._links_to(item["id"], "waits_on"):
            dt = self._item(link["item_id"])
            if dt and scope.sees_plan(dt["plan_id"]) and dt["status"] != "done":
                before_blocked[dt["id"]] = bool(self._blockers_of(scope, dt))
        new_status = changes.get("status", item["status"])
        new_owner = changes.get("owner", item["owner_id"])
        new_due = changes.get("due", item["due"])
        new_title = changes.get("title", item["label"])
        self.db.run("UPDATE items SET label = ?, owner_id = ?, status = ?, due = ?, version = version + 1, updated_at = ? WHERE id = ?",
                    new_title, new_owner, new_status, new_due, now, item["id"])
        cleared = self._clear_flags(item["id"], actor_id)
        parts = []
        if new_status != item["status"]:
            parts.append(f'status {STATUS_WORDS[item["status"]]} -> {STATUS_WORDS[new_status]}')
        if new_owner != item["owner_id"]:
            parts.append(f'owner {self.name(item["owner_id"])} -> {self.name(new_owner)}')
        if new_due != item["due"]:
            parts.append(f'due {item["due"] or "none"} -> {new_due or "none"}')
        if new_title != item["label"]:
            parts.append(f'renamed from "{item["label"]}"')
        if cleared:
            parts.append(f"acknowledged {_plural(cleared, 'change flag')}")
        self._audit(actor_id, "task.updated", "item", item["id"], plan, f'{actor} updated "{new_title}": {"; ".join(parts) or "no changes"}.',
                    before=item["status"], after=new_status, utterance_id=utterance_id)
        if new_owner != item["owner_id"] and new_owner != actor_id:
            self._notify([new_owner], plan, "task", f'{actor} handed you "{new_title}" in {plan["title"]}.')
        impacts = ImpactView()
        for d, was in before_blocked.items():
            dt = self._item(d)
            if was and not self._blockers_of(scope, dt):
                impacts.unblocked.append(dt["label"])
                if dt["owner_id"] != actor_id:
                    self._notify([dt["owner_id"]], self._plan_row(dt["plan_id"]), "unblocked",
                                 f'"{dt["label"]}" is no longer blocked: {actor} finished "{new_title}".')
        msg = f'Updated "{new_title}"' + (f": {'; '.join(parts)}" if parts else "") + "."
        if impacts.unblocked:
            msg += " Now unblocked: " + ", ".join(f'"{u}"' for u in impacts.unblocked) + "."
        return ChangeResult(outcome="updated", plan=plan["title"], item=item["id"], message=msg, impacts=impacts)

    # ================================================================ the write tool: propose_change

    def propose_change(self, actor_id: str, item: str | None = None, plan: str | None = None, value: str | None = None, status: str | None = None,
                       owner: str | None = None, due: Any = UNSET, title: str | None = None, day: str | int | None = None,
                       start: str | None = None, end: str | None = None, place: str | None = None, participants: list[str] | None = None,
                       remove: bool = False, new_type: str | None = None, value_kind: str | None = None, aliases: list[str] | None = None,
                       certainty: str = "committed", quote: str | None = None) -> ChangeResult:
        if certainty not in ("tentative", "committed"):
            raise DomainError("INVALID_ARGUMENT", 'certainty must be "tentative" or "committed".')
        self._rate_limit(actor_id)

        def run() -> ChangeResult:
            scope = self.scope_for(actor_id)
            plan_row = self.resolve_plan(scope, plan) if plan else None
            target = self.resolve_item(scope, item, plan_row["id"] if plan_row else None, required=False) if item else None
            if target is not None and plan_row is None:
                plan_row = scope.plans[target["plan_id"]]
            words = (quote or "").lower().replace("’", "'")
            private_intent = re.search(r"\b(just for me|only for me|private note|keep (?:it|this) private|(?:keep (?:it|this)|note) to myself|for my eyes only|"
                                       r"don'?t tell anyone)\b|仅我|只有我|私人便签|仅自己", words)
            if private_intent and (plan_row is None or self.plan_audience(plan_row) != [actor_id]):
                raise DomainError("PRIVATE_SCOPE_REQUIRED", "This is a private note. First create_plan(private=true), then write in that personal plan; owner is not visibility.")
            if target and target["shape"] == "task" and title and title.casefold() != target["label"].casefold() and quote:
                explicit_rename = re.search(r"\b(rename|retitle|call (?:it|this|the task)|change .*(?:title|name))\b|改名|重命名", words)
                if not explicit_rename:
                    raise DomainError("NEW_WORK_NOT_RENAME", "Do not replace an existing task with different work. Add a new task with item omitted and new_type from the plan; renaming needs an explicit rename instruction.")
            if target and target["shape"] == "detail" and value and quote:
                if target["value_kind"] == "datetime":
                    expected = bare_weekday_dates(quote, self._current_value(target) or "", plan_row["start_date"])
                    supplied = parse_datetime(value)
                    iso = re.search(r"\b\d{4}-(\d{2})-(\d{2})\b", value)
                    month_day = (int(iso[1]), int(iso[2])) if iso else (supplied.month, supplied.day)
                    if expected and all(v is not None for v in month_day) and month_day not in {(d.month, d.day) for d in expected}:
                        raise DomainError("DATE_REFERENCE_REQUIRED", f"A bare weekday refers to the date nearest the existing item ({self._current_value(target)}): use "
                                          f"{' or '.join(d.isoformat() for d in expected)} with the requested time. The speaker did not give the different calendar date you supplied.")
                denial = re.search(r"\b(not (?:getting|having|moving|changing|doing)|not gonna (?:do|get|have|move|change)|(?:isn'?t|is not|aren'?t|are not) "
                                   r"(?:moving|changing)|don'?t (?:change|move))\b|不要改|不换|不是改成", words)
                replacement = re.search(r"\b(instead|replace .+ with|change .+ to|make it|let'?s (?:do|get|go with)|go with|switch(?:ing)? to)\b|改成|换成", words)
                if denial and not replacement and not same_value(target["value_kind"], value, self._current_value(target) or ""):
                    raise DomainError("NEGATION_NOT_VALUE", "A denial does not replace the current value. Read the item and leave it unchanged, or reject the speaker's pending idea with decide_change.")
            if target is None and plan_row and new_type and quote:
                matches = [it for it in self.template(plan_row).item_types if any(term.casefold() in words for term in it.intent_terms)]
                if len(matches) == 1 and matches[0].name != new_type:
                    selected = matches[0]
                    raise DomainError("ITEM_TYPE_MISMATCH", f"That explicit list uses new_type={selected.name} ({selected.policy} policy), not {new_type}. Retry with the template's named list type.")
            activity_fields = any(x is not None for x in (day, start, end, place, participants))
            when_change = (target is not None and target["shape"] == "detail" and target["value_kind"] == "datetime" and bool(value)) or \
                          ((target is None or target["shape"] == "activity") and any(x is not None for x in (day, start, end)))
            open_words = vague_when(quote) if quote and when_change else None
            if open_words:
                current = self._current_value(target) if target is not None and target["shape"] == "detail" else None
                raise DomainError("VAGUE_TIME", f'The speaker did not say exactly when ("{open_words}"), so nothing changed. Ask which time they mean '
                                  "before changing anything: ask_followup with two or three concrete times as options (each option's action is this "
                                  "same change with that time), or ask in your reply and record the time they give.",
                                  {"said": open_words, **({"current": current} if current else {})})

            if target is None:
                # A new item. Which shape: from the type, else from the fields given. Without a type or a title, an unknown
                # name is a lookup that failed, not a request to create something (unless it gives a value for a new detail).
                # A value alone is not enough either: a guessed id with a value used to become a second item owned by the speaker
                # ("party.cake" next to Bob's Cake), which sidestepped the real item's owner. New details need new_type.
                if item and not new_type and not title:
                    near = self._similar_items(scope, item, plan_row["id"] if plan_row else None)
                    raise DomainError("NOT_FOUND", f'No item called "{item}" is available to you.'
                                      + (f" Did you mean {near}?" if near else " Check get_context or search_plan for its id.")
                                      + " To add a new item instead, pass new_type.")
                shape = None
                if plan_row is None:
                    plan_row = self._infer_plan(scope, f"{item or ''} {title or ''} {value or ''}", [actor_id] + ([owner] if owner else []), new_type)
                t = self.template(plan_row)
                it = t.item_type(new_type) if new_type else None
                if new_type and it is None:
                    raise DomainError("INVALID_ARGUMENT", f"{plan_row['title']} has no item type {new_type!r}. Types: {', '.join(x.name for x in t.item_types)}.")
                shape = it.shape if it else ("activity" if activity_fields else "detail" if value is not None else "task")
                if shape == "detail":
                    if value is None or not value.strip():
                        if item and not title:
                            raise DomainError("NOT_FOUND", f'No item called "{item}" is available to you. Check get_context or search_plan for its id.')
                        raise DomainError("INVALID_ARGUMENT", "A value is required for a new detail.")
                    utt = self._record_utterance(actor_id, plan_row["space_id"], quote)
                    label = (title or "").strip() or humanize(normalize_slot_key(item or "detail"))
                    it = it or t.default_type("detail")
                    key = normalize_slot_key(item) if item and "." in item else None
                    if key and self._item(key):
                        raise DomainError("FORBIDDEN", "That item cannot be changed by you. Pick a different name.")
                    row = self._insert_item(plan_row, "detail", it.name if it else "detail", label, actor_id, actor_id, item_id=key,
                                            value_kind=value_kind or it.value_kind if it else "text", aliases=aliases)
                    self._audit(actor_id, "item.created", "item", row["id"], plan_row,
                                f'{self.name(actor_id)} started tracking "{label}" in {plan_row["title"]} (owner: {self.name(actor_id)}).')
                    return self._change_detail(scope, actor_id, row, value, certainty, utt, True)
                if shape == "task":
                    utt = self._record_utterance(actor_id, plan_row["space_id"], quote)
                    return self._change_task(scope, actor_id, None, plan_row, title=title or item, owner=owner, status=status, due=due, remove=False,
                                             type_name=it.name if it else None, certainty=certainty, utterance_id=utt, quote_space=plan_row["space_id"])
                return self._change_activity(scope, actor_id, None, plan_row, title=title or item, day=day, start=start, end=end, place=place,
                                             participants=participants, remove=False, type_name=it.name if it else None, certainty=certainty, quote=quote)

            utt_space = plan_row["space_id"]
            if target["shape"] == "detail":
                if owner and value is None:
                    return self._reassign_detail(scope, actor_id, target, plan_row, owner, quote)
                if value is None or not value.strip():
                    raise DomainError("INVALID_ARGUMENT", f'Give the new value for {target["label"]}. If the speaker only says what is not happening, change nothing.')
                utt = self._record_utterance(actor_id, utt_space, quote)
                return self._change_detail(scope, actor_id, target, value, certainty, utt, False)
            if target["shape"] == "task":
                utt = self._record_utterance(actor_id, utt_space, quote)
                return self._change_task(scope, actor_id, target, plan_row, title=title, owner=owner, status=status, due=due, remove=remove,
                                         type_name=None, certainty=certainty, utterance_id=utt, quote_space=utt_space)
            return self._change_activity(scope, actor_id, target, plan_row, title=title, day=day, start=start, end=end, place=place,
                                         participants=participants, remove=remove, type_name=None, certainty=certainty, quote=quote)
        return self.db.tx(run)

    def _change_activity(self, scope: Scope, actor_id: str, item: Row | None, plan: Row, **kw: Any) -> ChangeResult:  # agreement layer
        raise DomainError("INVALID_ARGUMENT", "Activities are not supported here.")

    def _reassign_detail(self, scope: Scope, actor_id: str, item: Row, plan: Row, owner: str, quote: str | None) -> ChangeResult:
        if item["owner_id"] != actor_id and not self.is_organizer(plan, actor_id):
            raise DomainError("FORBIDDEN", f'{self.name(item["owner_id"])} owns {item["label"]}; only they or the organizer can hand it on.')
        m = self.resolve_member(scope, owner, plan)
        self.db.run("UPDATE items SET owner_id = ?, version = version + 1, updated_at = ? WHERE id = ?", m["id"], self.ts(), item["id"])
        self._audit(actor_id, "item.owner", "item", item["id"], plan, f'{self.name(actor_id)} made {m["name"]} the owner of {item["label"]}.',
                    utterance_id=self._record_utterance(actor_id, plan["space_id"], quote))
        if m["id"] != actor_id:
            self._notify([m["id"]], plan, "owner", f'{self.name(actor_id)} made you the owner of {item["label"]} in {plan["title"]}.')
        return ChangeResult(outcome="updated", plan=plan["title"], item=item["id"], message=f'{m["name"]} now owns {item["label"]}.')

    def _infer_plan(self, scope: Scope, text: str, people: list[str], type_name: str | None) -> Row:
        """For a new item with no plan given: the visible plan whose items share its topic (a task "for the party" lands in the party's
        plan rather than the one everybody sees). Member names are ignored as topic words. Falls back to the only plan, or asks."""
        plans = list(scope.plans.values())
        if type_name:
            typed = [p for p in plans if self.template(p).item_type(type_name)]
            plans = typed or plans
        if len(plans) == 1:
            return plans[0]
        names = {stem_word(w) for r in self.db.all("SELECT name FROM members") for w in r["name"].lower().split()}
        stems = {s for s in topic_stems(text) if s not in names}
        scores: list[tuple[int, int, str]] = []
        for p in plans:
            words = set(topic_stems(p["title"]))
            for r in self.db.all("SELECT id, label, aliases FROM items WHERE plan_id = ? AND archived = 0", p["id"]):
                words |= set(topic_stems(f'{r["id"].replace(".", " ").replace("_", " ")} {r["label"]} {" ".join(json.loads(r["aliases"] or "[]"))}'))
            words |= set(topic_stems(self.template(p).name))
            hits = len(stems & (words - names))
            if hits:
                scores.append((hits, -len(self.plan_audience(p)), p["id"]))
        if scores:
            scores.sort(reverse=True)
            if len(scores) == 1 or scores[0][:2] != scores[1][:2]:
                return scope.plans[scores[0][2]]
        raise DomainError("AMBIGUOUS_PLAN", "Which plan is this for? Your plans: " + "; ".join(f"{p['title']} (id {p['id']})" for p in plans)
                          + ". Pass plan. Anything kept from someone belongs in a plan they cannot see.",
                          {"plans": [{"id": p["id"], "title": p["title"]} for p in plans]})

    # ================================================================ decide_change

    def decide_change(self, actor_id: str, request: str, decision: str, amended_value: str | None = None, quote: str | None = None) -> DecisionResult:
        if decision not in ("accept", "reject", "amend"):
            raise DomainError("INVALID_ARGUMENT", 'decision must be "accept", "reject" or "amend".')
        stale: list[DomainError] = []

        def run() -> DecisionResult | None:
            scope = self.scope_for(actor_id)
            r = self._request(request)
            if not r or not scope.sees_plan(r["plan_id"]):
                raise DomainError("NOT_FOUND", "No such request is available to you.")
            plan = self._plan_row(r["plan_id"])
            item = self._item(r["item_id"]) if r["item_id"] else None
            if r["status"] != "open":
                raise DomainError("REQUEST_CLOSED", f'That request is already {r["status"]}.', {"status": r["status"]})
            withdrawing = decision == "reject" and r["requested_by"] == actor_id and r["assignee_id"] != actor_id
            if not (withdrawing or r["assignee_id"] == actor_id or self.is_organizer(plan, actor_id) or (item and item["policy"] == "open")):
                who = self.name(r["assignee_id"])
                raise DomainError("FORBIDDEN", f'{who} decides this; only they or the organizer can.', {"decides": who})
            actor = self.name(actor_id)
            now = self.ts()
            utt = self._record_utterance(actor_id, plan["space_id"], quote)
            requester = self.name(r["requested_by"])
            f = self._fact(r["fact_id"])
            what = f["value"] if f else r["summary"]
            label = item["label"] if item else "the plan"

            if r["kind"] in ("value", "task") and item and item["version"] != r["base_version"]:
                self.db.run("UPDATE requests SET status = 'stale', resolved_at = ?, resolved_by = ?, note = 'item changed before decision' WHERE id = ?",
                            now, actor_id, r["id"])
                if f:
                    self.db.run("UPDATE facts SET status = 'rejected' WHERE id = ?", f["id"])
                current = self._current_value(item) if item["shape"] == "detail" else STATUS_WORDS.get(item["status"] or "", "")
                stale.append(DomainError("STALE_REQUEST", f'{label} changed after this was proposed. It is now "{current}".', {"current": current}))
                return None

            if decision == "reject":
                if f:
                    self.db.run("UPDATE facts SET status = 'rejected', decided_by = ?, decided_at = ? WHERE id = ?", actor_id, now, f["id"])
                self.db.run("UPDATE requests SET status = 'rejected', resolved_at = ?, resolved_by = ? WHERE id = ?", now, actor_id, r["id"])
                keep = self._current_value(item) if item and item["shape"] == "detail" else None
                stays = f' {label} stays "{keep}".' if keep else ""
                if withdrawing:
                    self._notify([r["assignee_id"]], plan, "request", f'{actor} withdrew the request to change {label} ("{what}"). Nothing for you to decide.')
                    self._audit(actor_id, "request.withdrawn", "item" if item else "plan", item["id"] if item else plan["id"], plan,
                                f'{actor} withdrew "{what}".', utterance_id=utt)
                    return DecisionResult(outcome="rejected", plan=plan["title"], item=item["id"] if item else None, message=f"Withdrew your request.{stays}")
                if r["requested_by"] != actor_id:
                    self._notify([r["requested_by"]], plan, "request", f'{actor} declined your suggestion for {label} ("{what}").' + stays)
                self._audit(actor_id, "request.rejected", "item" if item else "plan", item["id"] if item else plan["id"], plan,
                            f'{actor} declined "{what}" from {requester}.', utterance_id=utt)
                return DecisionResult(outcome="rejected", plan=plan["title"], item=item["id"] if item else None, message=f'Declined "{what}".{stays}')

            if r["kind"] == "value":
                confirmed = f
                outcome = "accepted"
                if decision == "amend":
                    amended = (amended_value or "").strip()
                    if not amended:
                        raise DomainError("INVALID_ARGUMENT", 'amended_value is required when decision is "amend".')
                    self.db.run("UPDATE facts SET status = 'rejected', decided_by = ?, decided_at = ? WHERE id = ?", actor_id, now, f["id"])
                    confirmed = self._insert_fact(item, amended, "committed", "pending_review", actor_id, utt, True)
                    outcome = "amended"
                current = self._fact(item["current_fact_id"])
                self.db.run("UPDATE requests SET status = ?, resolved_at = ?, resolved_by = ? WHERE id = ?", outcome, now, actor_id, r["id"])
                self._audit(actor_id, f"request.{outcome}", "item", item["id"], plan,
                            f'{actor} confirmed "{what}" proposed by {requester}.' if outcome == "accepted"
                            else f'{actor} amended "{what}" (from {requester}) to "{confirmed["value"]}".', utterance_id=utt)
                impacts = self._apply_detail(scope, item, current, confirmed, actor_id, utt)
                if r["requested_by"] != actor_id:
                    self._notify([r["requested_by"]], plan, "request",
                                 f'{actor} confirmed your change: {item["label"]} is now "{confirmed["value"]}".' if outcome == "accepted"
                                 else f'{actor} adjusted your change: {item["label"]} is now "{confirmed["value"]}".')
                was = f' (was "{current["value"]}")' if current else ""
                return DecisionResult(outcome=outcome, plan=plan["title"], item=item["id"], impacts=impacts,  # type: ignore[arg-type]
                                      message=f'{item["label"]} is now "{confirmed["value"]}"{was}.')

            if decision == "amend":
                raise DomainError("INVALID_ARGUMENT", "Only values can be amended; accept or reject this, then make your own change.")
            self.db.run("UPDATE requests SET status = 'accepted', resolved_at = ?, resolved_by = ? WHERE id = ?", now, actor_id, r["id"])
            self._audit(actor_id, "request.accepted", "item" if item else "plan", item["id"] if item else plan["id"], plan,
                        f'{actor} accepted "{what}" from {requester}.', utterance_id=utt)
            change = json.loads(r["change_json"])
            if r["kind"] == "task":
                res = self._apply_task(scope, actor_id, item, plan, change, utt)
            else:
                res = self._apply_activity_change(scope, actor_id, plan, item, change, utt)
            if r["requested_by"] != actor_id:
                self._notify([r["requested_by"]], plan, "request", f'{actor} accepted your change to {label}: {what}.')
            return DecisionResult(outcome="accepted", plan=plan["title"], item=item["id"] if item else None, impacts=res.impacts,
                                  plan_status=res.plan_status, message=res.message)

        result = self.db.tx(run)
        if stale:
            raise stale[0]
        return result  # type: ignore[return-value]

    def _apply_activity_change(self, scope: Scope, actor_id: str, plan: Row, item: Row | None, change: dict[str, Any], utt: str | None) -> ChangeResult:
        raise DomainError("INVALID_ARGUMENT", "Activities are not supported here.")

    # ================================================================ link_items

    def link_items(self, actor_id: str, item: str, depends_on: str, remove: bool = False, plan: str | None = None) -> LinkResult:
        def run() -> LinkResult:
            scope = self.scope_for(actor_id)
            plan_row = self.resolve_plan(scope, plan) if plan else None
            a = self.resolve_item(scope, item, plan_row["id"] if plan_row else None)
            b = self.resolve_item(scope, depends_on)
            if a["id"] == b["id"]:
                raise DomainError("CYCLE_REJECTED", "An item cannot depend on itself.")
            if a["shape"] == "activity":
                raise DomainError("INVALID_ARGUMENT", "Activities follow the plan's agreement; link a task or detail to the activity instead.")
            if b["shape"] == "task" and a["shape"] != "task":
                raise DomainError("INVALID_ARGUMENT", "Only tasks can wait on other tasks.")
            kind = "waits_on" if (a["shape"] == "task" and b["shape"] == "task") else "watches"
            # The history row names both items, so it lives with the plan fewer people can see.
            pa = scope.plans[a["plan_id"]]
            pb = scope.plans[b["plan_id"]]
            if set(self.plan_audience(pb)) < set(self.plan_audience(pa)):
                pa = pb
            if remove:
                n = self.db.run("DELETE FROM links WHERE item_id = ? AND depends_on_id = ?", a["id"], b["id"])
                if n:
                    self._audit(actor_id, "link.removed", "item", a["id"], pa, f'"{a["label"]}" no longer depends on "{b["label"]}".')
                return LinkResult(outcome="unlinked", message=f'"{a["label"]}" no longer depends on "{b["label"]}".')
            if kind == "waits_on":
                cycle = self._find_path(b["id"], a["id"])
                if cycle:
                    chain = " -> ".join(f'"{(self._item(i) or {}).get("label", i)}"' for i in [a["id"], *cycle])
                    raise DomainError("CYCLE_REJECTED", f"That would create a loop: {chain}.")
            n = self.db.run("INSERT OR IGNORE INTO links (item_id, depends_on_id, kind) VALUES (?, ?, ?)", a["id"], b["id"], kind)
            if n:
                self._audit(actor_id, "link.added", "item", a["id"], pa,
                            f'"{a["label"]}" now {"waits on" if kind == "waits_on" else "depends on"} "{b["label"]}".')
            msg = (f'"{a["label"]}" waits on "{b["label"]}".' if kind == "waits_on"
                   else f'"{a["label"]}" depends on {b["label"]}; changes to it will be flagged.')
            return LinkResult(outcome="linked" if n else "already_linked", message=msg)
        return self.db.tx(run)

    def _find_path(self, start: str, goal: str) -> list[str] | None:
        stack = [(start, [start])]
        seen: set[str] = set()
        while stack:
            node, path = stack.pop()
            if node == goal:
                return path
            if node in seen:
                continue
            seen.add(node)
            for link in self._links_from(node, "waits_on"):
                stack.append((link["depends_on_id"], [*path, link["depends_on_id"]]))
        return None

    # ================================================================ context (the read tool, prefetched every turn)

    def context(self, actor_id: str, plan: str | None = None, mark_read: bool = True, max_plans: int = 4,
                detail: str = "full", limit: int = 12, offset: int = 0, group: str | None = None) -> ContextView:
        if detail not in ("summary", "full") or not 1 <= limit <= 50 or offset < 0:
            raise DomainError("INVALID_ARGUMENT", "Use summary/full, limit 1..50 and a non-negative offset.")

        def run() -> ContextView:
            scope = self.scope_for(actor_id)
            focus = self.resolve_plan(scope, plan) if plan else None
            available = list(scope.plans.values())
            if group:
                available = [p for p in available if group.lower() in (p["group_id"].lower(), (self._group(p["group_id"]) or {}).get("name", "").lower())]
                if focus and focus["id"] not in {p["id"] for p in available}:
                    raise DomainError("NOT_FOUND", "No such plan is available in that group.")
            if detail == "summary" and focus is None and len(available) == 1:
                focus = available[0]
            plans = [focus] if focus else available
            visible_ids = {p["id"] for p in available}

            decisions = [self._request_view(scope, r) for r in self.db.all(
                "SELECT * FROM requests WHERE assignee_id = ? AND status = 'open' ORDER BY created_at, id", actor_id)
                if scope.sees_plan(r["plan_id"]) and r["plan_id"] in visible_ids and r["requested_by"] != actor_id and (detail == "summary" or not focus or r["plan_id"] == focus["id"])]
            # A person's own floated idea waiting on themselves (an owner's "maybe") is a decision too.
            decisions += [self._request_view(scope, r) for r in self.db.all(
                "SELECT * FROM requests WHERE assignee_id = ? AND requested_by = ? AND status = 'open' ORDER BY created_at, id", actor_id, actor_id)
                if scope.sees_plan(r["plan_id"]) and r["plan_id"] in visible_ids and (detail == "summary" or not focus or r["plan_id"] == focus["id"])]
            flags = []
            for f in self.db.all("SELECT * FROM flags WHERE owner_id = ? AND status = 'open' ORDER BY created_at, id", actor_id):
                if not self._flag_visible(scope, f):
                    continue
                t = self._item(f["target_id"])
                if t["plan_id"] not in visible_ids:
                    continue
                if detail != "summary" and focus and t["plan_id"] != focus["id"]:
                    continue
                flags.append(FlagView(item=t["id"], label=t["label"], plan=scope.plans[t["plan_id"]]["title"], message=f["message"]))
            # In a shared conversation a note without a space (or from a space someone listening is not in) stays unread for later.
            notes = [n for n in self.db.all("SELECT * FROM notifications WHERE member_id = ? AND read_at IS NULL ORDER BY created_at, id", actor_id)
                     if ((n["space_id"] is None and not scope.listeners) or n["space_id"] in scope.spaces) and n["plan_id"] in (None, *visible_ids)
                     and (detail == "summary" or not focus or n["plan_id"] in (None, focus["id"]))]
            truncated = {}
            if detail == "summary":
                truncated = {key: max(0, len(rows) - limit) for key, rows in (("decisions", decisions), ("updates", notes), ("affected", flags))
                             if len(rows) > limit}
                decisions, notes, flags = decisions[:limit], notes[:limit], flags[:limit]
            if mark_read and notes:
                stamp = self.ts()
                for n in notes:
                    self.db.run("UPDATE notifications SET read_at = ? WHERE id = ?", stamp, n["id"])
            todos = self._todos(scope, available if detail == "summary" else plans)
            tasks = [t for t in self.db.all("SELECT * FROM items WHERE shape = 'task' AND owner_id = ? AND archived = 0 AND status <> 'done' ORDER BY updated_at DESC, id", actor_id)
                     if t["plan_id"] in visible_ids]
            if detail == "summary" and len(tasks) > limit:
                truncated["your_tasks"] = len(tasks) - limit
            # A person's private conditions are never read out to other listeners, even their own.
            conditions = self._my_conditions(scope, plans) if (detail == "full" or focus) and not scope.listeners else []
            if detail == "summary" and len(todos) > limit:
                truncated["todos"] = len(todos) - limit
                todos = todos[:limit]
            # Plans with something for this person come first; the view is capped to keep it readable.
            pending_plans = {d.plan for d in decisions} | {t.plan for t in todos} | {f.plan for f in flags}
            ordered = sorted(plans, key=lambda p: (0 if p["title"] in pending_plans else 1))
            shown, rest = ordered[:max_plans], ordered[max_plans:]
            index = []
            next_offset = None
            if detail == "summary":
                index_rows = sorted(available, key=lambda p: (0 if focus and p["id"] == focus["id"] else 1,
                                                            0 if p["title"] in pending_plans else 1, p["id"]))
                page = index_rows[offset:offset + limit]
                index = [PlanSummaryView(id=p["id"], title=p["title"], template=p["template"],
                                         group=(self._group(p["group_id"]) or {}).get("name", p["group_id"]), group_id=p['group_id'],
                                         status=p["status"], version=p["aggregate_version"], you_organize=self.is_organizer(p, actor_id),
                                         unread_updates=self.db.scalar("SELECT COUNT(*) FROM notifications WHERE member_id = ? AND plan_id = ? AND read_at IS NULL", actor_id, p["id"]) or 0,
                                         pending_decisions=self.db.scalar("SELECT COUNT(*) FROM requests WHERE assignee_id = ? AND plan_id = ? AND status = 'open'", actor_id, p["id"]) or 0,
                                         your_open_tasks=sum(t["plan_id"] == p["id"] for t in tasks),
                                         key_items=[{"id": i["id"], "label": i["label"], "owner": self.name(i["owner_id"]), "shape": i["shape"], "type": i["type"], "decided_by": POLICY_WORDS[i["policy"]],
                                                     "value": (self._current_value(i) or "")[:160] if i["shape"] == "detail" else i["status"]}
                                                    for i in self._visible_items(scope, p["id"])[:8]],
                                         item_types=[{"name": it.name, "shape": it.shape, "policy": it.policy} for it in self.template(p).item_types]) for p in page]
                next_offset = offset + limit if offset + limit < len(index_rows) else None
                shown, rest = ([focus] if focus else []), []
            parts = []
            if decisions:
                parts.append(f"{_plural(len(decisions), 'decision')} waiting for you")
            confirm = [t for t in todos if t.kind == "confirm"]
            if confirm:
                parts.append(f"{_plural(len(confirm), 'item')} to confirm on your page")
            if flags:
                parts.append(f"{_plural(len(flags), 'item')} of yours affected by changes")
            if notes:
                parts.append(_plural(len(notes), "update"))
            today = self.today()
            directory = self.list_templates(actor_id) if detail == "summary" else None
            groups = self.db.all("SELECT g.id,g.name FROM groups g JOIN group_members gm ON gm.group_id=g.id WHERE gm.member_id=? AND gm.status='active' ORDER BY g.rowid", actor_id)
            if group:
                groups = [g for g in groups if group.lower() in (g['id'].lower(), g['name'].lower())]
            shared_with: list[str] = []
            if scope.listeners:
                groups = [g for g in groups if scope.listeners <= {r["member_id"] for r in self.db.all(
                    "SELECT member_id FROM group_members WHERE group_id = ? AND status = 'active'", g["id"])}]
                shared_with = [self.name(m) for m in sorted(scope.listeners) if m != actor_id]
            return ContextView(
                you=scope.name, today=today.strftime("%a, %b ") + str(today.day) + today.strftime(", %Y"),
                headline=(", ".join(parts) + ".") if parts else "Nothing new.",
                decisions_for_you=decisions, updates=[n["text"] for n in notes], your_todos=todos, affected=flags,
                your_conditions=conditions, plans=[self._plan_view(scope, p) for p in shown],
                other_plans=[f"{p['title']} (id {p['id']})" for p in rest],
                plan_index=index, active_plan_id=focus["id"] if focus else None, next_offset=next_offset, truncated=truncated,
                templates=directory.templates if directory else [], template_next_offset=directory.next_offset if directory else None,
                groups=groups[offset:offset+limit], group_next_offset=offset+limit if len(groups)>offset+limit else None,
                shared_with=shared_with,
                shared_note=(f"Shared conversation: {_names(shared_with)} will also read this reply. Only plans all of them can see are included; "
                             "the speaker's private conditions, private plans and plans hidden from any of them are left out, not missing. "
                             "For those, the speaker can message the assistant privately.") if shared_with else None,
                your_tasks=[PersonalTaskView(id=t["id"], title=t["label"], plan=scope.plans[t["plan_id"]]["title"], plan_id=t["plan_id"], status=t["status"], due=t["due"],
                                            blockers=[] if t['status']=='done' else [{'item':u['id'],'label':u['label'],'decided_by':self.name(u['owner_id']),'reason':reason} for u,reason in self._blockers_of(scope,t)],
                                            flags=self._flags_for(scope,t['id']))
                            for t in tasks[:limit]] if detail == "summary" else [],
            )
        return self.db.tx(run)

    def list_templates(self, actor_id: str, query: str = "", limit: int = 12, offset: int = 0) -> TemplateListView:
        self.scope_for(actor_id)
        if not 1 <= limit <= 50 or offset < 0:
            raise DomainError("INVALID_ARGUMENT", "Use limit 1..50 and a non-negative offset.")
        words = set(re.findall(r"\w+", query.lower()))
        matches = []
        for t in self.templates().values():
            text = f"{t.id} {t.name} {t.description}".lower()
            score = sum(w in text for w in words) + 3 * sum(w in f"{t.id} {t.name}".lower() for w in words)
            if words and not score:
                continue
            matches.append((score, t))
        matches.sort(key=lambda pair: (-pair[0], pair[1].id))
        page = matches[offset:offset + limit]
        return TemplateListView(templates=[TemplateSummaryView(id=t.id, name=t.name, days=t.days,
                                 summary=" ".join(t.description.split()).split(". ")[0][:180]) for _, t in page],
                                total=len(matches), next_offset=offset + limit if offset + limit < len(matches) else None)

    def get_template_spec(self, actor_id: str, template_id: str) -> TemplateSpecView:
        self.scope_for(actor_id)
        from groupflow.templates import presets_for
        try:
            t = self.templates().get(template_id) or get_template(template_id)
        except KeyError:
            raise DomainError("NOT_FOUND", "Unknown template; use list_templates to discover available templates.")
        return TemplateSpecView(id=t.id, name=t.name, days=t.days,
            version=t.version, lifecycle=t.lifecycle, presets=presets_for(t.id),
            item_types=[{"name": i.name, "label": i.label, "description": i.description, "intent_terms": i.intent_terms, "shape": i.shape, "policy": i.policy, "value_kind": i.value_kind,
                         "default_minutes": i.default_minutes, "place_kinds": i.place_kinds} for i in t.item_types],
            conditions=[{"name": c.name, "check": c.check, "hint": c.hint, "tags": c.tags,
                         "applies_to": c.applies_to, "soft": c.soft} for c in t.conditions],
            starters={"details": [i.label for i in t.starter.details], "tasks": [i.title for i in t.starter.tasks],
                      "activities": [i.title for i in t.starter.activities]})

    def _plan_view(self, scope: Scope, plan: Row) -> PlanView:
        t = self.template(plan)
        group = self._group(plan["group_id"])
        items = self._visible_items(scope, plan["id"], ("detail", "task"))
        details = [self._detail_view(scope, i) for i in items if i["shape"] == "detail"]
        tasks = [self._task_view(scope, i) for i in items if i["shape"] == "task"]
        view = PlanView(
            id=plan["id"], title=plan["title"], kind=t.name, group=group["name"] if group else plan["group_id"],
            visible_to=[self.name(m) for m in self.plan_audience(plan)], organizer=self.name(plan["organizer_id"]),
            you_organize=self.is_organizer(plan, scope.member_id), dates=dates_text(plan["start_date"], plan["days"]),
            status=plan["status"], status_reason=plan["status_reason"], version=plan["aggregate_version"], details=details, tasks=tasks,
            item_types=[f"{it.name} ({it.shape}, decided by {POLICY_WORDS[it.policy]})" for it in t.item_types],
            condition_kinds=[c.name for c in t.conditions], next_step="",
        )
        self._agreement_view(scope, plan, view)
        extension = self.db.get("SELECT lifecycle, planning_window_days, preset, template_version FROM plan_extensions WHERE plan_id = ?", plan["id"])
        if extension:
            view.lifecycle = extension["lifecycle"]
            view.planning_window_days = extension["planning_window_days"]
            view.preset = extension["preset"]
        if not view.next_step:
            view.next_step = self._living_next_step(scope, plan, details, tasks)
        return view

    def _agreement_view(self, scope: Scope, plan: Row, view: PlanView) -> None:  # agreement layer fills activities
        return None

    def _living_next_step(self, scope: Scope, plan: Row, details: list[DetailView], tasks: list[TaskView]) -> str:
        waiting = [d for d in details if d.status == "disputed"] + [t for t in tasks if t.pending]
        blocked = [t for t in tasks if t.blocked_by and t.status != "done"]
        bits = []
        if waiting:
            bits.append(f"{_plural(len(waiting), 'change')} waiting for an owner's decision")
        if blocked:
            bits.append(f"{_plural(len(blocked), 'task')} blocked")
        if not bits:
            return "Nothing is waiting. Owners decide their own details; tasks belong to whoever does them."
        return "; ".join(bits).capitalize() + "."

    def _todos(self, scope: Scope, plans: list[Row]) -> list[TodoView]:  # agreement layer adds confirmations
        return []

    def _my_conditions(self, scope: Scope, plans: list[Row]) -> list[ConditionView]:  # agreement layer
        return []

    # ================================================================ search_plan

    def search(self, actor_id: str, query: str, plan: str | None = None) -> SearchView:
        scope = self.scope_for(actor_id)
        focus = self.resolve_plan(scope, plan) if plan else None
        tokens = topic_stems(query or "")
        # People refer to each other by role ("Mom"): add the name of any visible member whose role matches.
        for p in ([focus] if focus else scope.plans.values()):
            for m in self.plan_audience(p):
                role = self._group_role(p["group_id"], m)
                words = [w for w in re.split(r"[^a-z0-9']+", ((role or {}).get("label") or "").lower()) if w]
                if words and stem_word(words[-1]) in tokens:
                    tokens.append(stem_word(self.name(m)))

        def hit(hay: str) -> bool:
            words = topic_stems(hay, False)
            return any(w == t or (min(len(w), len(t)) >= 4 and (w.startswith(t) or t.startswith(w))) for t in tokens for w in words)

        items = []
        for i in self._visible_items(scope, focus["id"] if focus else None):
            p = scope.plans[i["plan_id"]]
            state = self._item_state(scope, i)
            hay = f'{i["id"].replace(".", " ").replace("_", " ")} {i["label"]} {" ".join(json.loads(i["aliases"]))} {state} {self.name(i["owner_id"])}'
            if not tokens or hit(hay):
                items.append(SearchHit(plan=p["title"], id=i["id"], shape=i["shape"], label=i["label"], state=state,
                                       owner=None if i["shape"] == "activity" else self.name(i["owner_id"])))
        places = []
        seen_templates: set[str] = set()
        for p in ([focus] if focus else scope.plans.values()):
            if p["template"] in seen_templates:
                continue
            seen_templates.add(p["template"])
            catalog = json.loads(p["catalog_json"] or "{}")
            for pl in catalog.get("places", []):
                hay = f'{pl["name"]} {pl["kind"]} {" ".join(pl.get("tags") or [])} {" ".join(pl.get("aliases") or [])}'
                if tokens and hit(hay):
                    places.append(PlaceHit(plan=p["title"], id=pl["id"], name=pl["name"], kind=pl["kind"],
                                           price=f'{p["currency"]} {float(pl.get("price", 0)):.2f}', tags=list(pl.get("tags") or [])))
        return SearchView(query=query, matched=bool(items or places), items=items[:40], places=places[:20])

    def _item_state(self, scope: Scope, i: Row) -> str:
        if i["shape"] == "detail":
            return self._current_value(i) or "not set"
        if i["shape"] == "task":
            return STATUS_WORDS[i["status"]] + (f', due {i["due"]}' if i["due"] else "")
        return self._activity_state(i)

    def _activity_state(self, item: Row) -> str:  # agreement layer
        return ""

    # ================================================================ explain (blockers part) and history

    def explain(self, actor_id: str, item: str | None = None, option: str | None = None, plan: str | None = None) -> ExplainView:
        scope = self.scope_for(actor_id)
        plan_row = self.resolve_plan(scope, plan) if plan else None
        target = self.resolve_item(scope, item, plan_row["id"] if plan_row else None) if item else None
        if target and target["shape"] == "task":
            return self._explain_task(scope, target)
        if target and target["shape"] == "detail":
            h = self.history(actor_id, item=target["id"])
            return ExplainView(subject=target["label"], message=f'{target["label"]} is "{h.current or "not set"}". '
                               + (f"Last change: {h.entries[-1].detail}" if h.entries else ""))
        if target is None and plan_row is None and option is None:
            # Nothing specific: the biggest hold-ups across what the speaker can see.
            return self._explain_hold_ups(scope)
        return self._explain_activity(scope, actor_id, plan_row or (scope.plans[target["plan_id"]] if target else None), target, option)

    def _explain_activity(self, scope: Scope, actor_id: str, plan: Row | None, target: Row | None, option: str | None) -> ExplainView:
        raise DomainError("INVALID_ARGUMENT", "Nothing to explain there.")

    def _explain_task(self, scope: Scope, t: Row) -> ExplainView:
        if t["status"] == "done":
            return ExplainView(subject=t["label"], message=f'"{t["label"]}" is already done.')
        roots = self._roots_of(scope, t)
        visible_open = [x for x in self._visible_items(scope, None, ("task",)) if x["status"] != "done"]
        paths = []
        for root, reason, path in roots:
            holds = [x["label"] for x in visible_open if any(r["id"] == root["id"] for r, _, _ in self._roots_of(scope, x))]
            paths.append(BlockerPath(root=root["label"], owner=self.name(root["owner_id"]), reason=reason, path=path, holds_up=holds))
        if not paths:
            msg = f'Nothing is blocking "{t["label"]}". It is ready for {self.name(t["owner_id"])}.'
        else:
            msg = f'"{t["label"]}" is waiting on ' + " and ".join(f"{p.root} ({p.owner}): {p.reason}" for p in paths)
        return ExplainView(subject=t["label"], message=msg, blockers=paths)

    def _explain_hold_ups(self, scope: Scope) -> ExplainView:
        visible_open = [x for x in self._visible_items(scope, None, ("task",)) if x["status"] != "done"]
        by_root: dict[str, BlockerPath] = {}
        for x in visible_open:
            for root, reason, path in self._roots_of(scope, x):
                bp = by_root.setdefault(root["id"], BlockerPath(root=root["label"], owner=self.name(root["owner_id"]), reason=reason, path=path, holds_up=[]))
                bp.holds_up.append(x["label"])
        ranked = sorted(by_root.values(), key=lambda b: -len(b.holds_up))
        if not ranked:
            return ExplainView(subject="everything", message="Nothing is blocked right now.")
        top = ranked[0]
        return ExplainView(subject="everything", blockers=ranked,
                           message=f"Biggest hold-up: {top.root} ({top.owner}), holding up {_plural(len(top.holds_up), 'task')}: {top.reason}")

    def history(self, actor_id: str, item: str | None = None, plan: str | None = None, limit: int = 30) -> HistoryView:
        scope = self.scope_for(actor_id)
        plan_row = self.resolve_plan(scope, plan) if plan else None
        if item:
            t = self.resolve_item(scope, item, plan_row["id"] if plan_row else None)
            rows = [a for a in self.db.all("SELECT * FROM audit WHERE target_type = 'item' AND target_id = ? ORDER BY seq", t["id"])
                    if (not a["space_id"] or a["space_id"] in scope.spaces) and (not a["private_to"] or a["private_to"] == actor_id)]
            return HistoryView(subject=t["label"], current=self._item_state(scope, t), entries=[self._entry(scope, a) for a in rows][-limit:])
        if plan_row is None:
            plan_row = self.resolve_plan(scope, None, purpose="history was asked for")
        rows = [a for a in self.db.all("SELECT * FROM audit WHERE plan_id = ? ORDER BY seq DESC LIMIT ?", plan_row["id"], limit * 2)
                if not a["private_to"] or a["private_to"] == actor_id][:limit]
        return HistoryView(subject=plan_row["title"], current=plan_row["status"], entries=[self._entry(scope, a) for a in reversed(rows)])

    def _entry(self, scope: Scope, a: Row) -> HistoryEntry:
        return HistoryEntry(at=a["at"], who=self.name(a["actor_id"]) if a["actor_id"] else None, action=a["action"], detail=a["detail"],
                            said=self._quote_for(scope, a["utterance_id"]))


def fixed_clock(iso: str) -> Callable[[], str]:
    stamp = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    text = stamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return lambda: text
