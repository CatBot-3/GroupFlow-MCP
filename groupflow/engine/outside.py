"""Outside data: what GroupFlow gives other tools to search with, and how it checks what they found.

Weather, venues and reviews belong to other connected tools (Alexa+'s own providers, booking partners). Two rules keep the group's
private conditions inside GroupFlow:

  * The server decides what leaves. search_brief describes what a place must offer (kind, date, time window, party size, needs as
    tags, a rounded price band) without any name, any individual budget or whose need is whose.
  * Unknown is a third answer. check_candidates checks places found elsewhere against everyone's private limits and answers meets,
    breaks or not confirmed for each one. A missing or stale fact is never counted as meeting a limit, and no candidate field can hold
    review text: only typed facts (hours, price, tags) are read, and nothing is stored.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from groupflow.core.errors import DomainError
from groupflow.repair.model import DAY, Activity, hhmm
from groupflow.repair.validate import violations

PRICE_BANDS = (15, 25, 40, 60, 100, 150, 250)  # whole currency units per person
STALE_AFTER_S = 24 * 3600
TAG = re.compile(r"^[a-z0-9_]{2,30}$")


def _tag(value: str) -> str | None:
    t = re.sub(r"[\s-]+", "_", str(value).strip().lower())
    return t if TAG.match(t) else None


def _minutes(value: str | None) -> int | None:
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", value or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m and int(m.group(1)) < 24 and int(m.group(2)) < 60 else None


class OutsidePlaces:
    """Mixed into the engine."""

    def _outside_target(self, actor_id: str, plan: str | None, item: str | None) -> tuple[dict, Activity, Any, Any]:
        scope = self.scope_for(actor_id)
        p = self.resolve_plan(scope, plan, prefer=lambda r: self._has_activities(r), purpose="places could be searched")
        if not self._has_activities(p):
            raise DomainError("INVALID_STATE", f"{p['title']} has no activities that need a place.")
        snap = self._with_current_prices(p, self._latest_snapshot(p))
        live = [a for a in snap.live if a.place_id or a.place_name]
        if not live:
            raise DomainError("INVALID_STATE", f"{p['title']} has no activity at a place.")
        target = None
        if item:
            key = item.strip().lower()
            target = next((a for a in live if a.id.lower() == key or a.title.lower() == key), None)
            if target is None:
                raise DomainError("NOT_FOUND", "Say which activity: " + ", ".join(a.title for a in live) + ".")
        if target is None:
            problem = self._problem(p, snap)
            broken = {v.activity_id for v in violations(problem, snap) if v.activity_id}
            target = next((a for a in live if a.id in broken), None) or live[0]
        return p, target, snap, self._problem(p, snap)

    def _participant_rules(self, p: dict, a: Activity) -> list[dict]:
        out = []
        for r in self._condition_rows(p):
            if r["owner_id"] in a.participants:
                out.append({"owner": r["owner_id"], "kind": r["kind"], "value": json.loads(r["value_json"])})
        return out

    def search_brief(self, actor_id: str, plan: str | None = None, item: str | None = None) -> dict[str, Any]:
        """What a place must offer for one activity, with nothing that says who needs what."""
        def run() -> dict[str, Any]:
            p, a, snap, _ = self._outside_target(actor_id, plan, item)
            rules = self._participant_rules(p, a)
            needs = sorted({t for r in rules if r["kind"] == "needs" and (not r["value"].get("applies_to") or a.kind in r["value"]["applies_to"])
                            for t in (_tag(x) for x in r["value"].get("tags", [])) if t})
            day0 = a.day * DAY
            earliest = [m for r in rules if r["kind"] == "earliest_start" for m in [_minutes(r["value"].get("time"))] if m is not None]
            latest = [m for r in rules if r["kind"] == "latest_end" for m in [_minutes(r["value"].get("time"))] if m is not None]
            budgets = []
            for r in rules:
                if r["kind"] == "max_total_cost":
                    others = snap.member_total(r["owner"]) - a.share(r["owner"])
                    budgets.append(int(r["value"].get("amount_minor", 0)) - others)
            band = None
            if budgets:
                tightest = min(budgets) / 100
                fitting = [b for b in PRICE_BANDS if b <= tightest]
                band = f"up to {p['currency']} {fitting[-1]} per person" if fitting else f"under {p['currency']} {PRICE_BANDS[0]} per person"
            catalog = self._catalog(p)
            here = catalog.places.get(a.place_id or "")
            avoid = sorted({catalog.places[u.place_id].name for u in self._unavailable(p) if u.place_id in catalog.places
                            and u.from_min < a.end and a.start < u.to_min})
            return {
                "plan": p["title"], "for": a.title, "kind": a.kind, "date": (snap.start_date if p["days"] == 1 else None),
                "day": a.day if p["days"] > 1 else None,
                "time": f"{hhmm(a.start - day0)}-{hhmm(a.end - day0)}", "duration_minutes": a.duration, "party_size": len(a.participants),
                "start_no_earlier_than": hhmm(max(earliest)) if earliest else None, "end_no_later_than": hhmm(min(latest)) if latest else None,
                "needs": needs, "price_band": band, "area": here.zone if here and here.zone != "local" else None, "avoid": avoid,
                "privacy": "No names, individual budgets or whose need is whose. Pass places you find back as candidates to check them.",
            }
        return self.db.tx(run)

    def check_candidates(self, actor_id: str, plan: str | None, candidates: list[dict[str, Any]], item: str | None = None) -> dict[str, Any]:
        """meets / breaks / not confirmed for each outside place, against every participant's private limits, without naming anyone."""
        if not candidates or len(candidates) > 10:
            raise DomainError("INVALID_ARGUMENT", "Pass 1 to 10 candidate places.")

        def run() -> dict[str, Any]:
            p, a, snap, _ = self._outside_target(actor_id, plan, item)
            rules = self._participant_rules(p, a)
            day0 = a.day * DAY
            now = datetime.now(timezone.utc)
            out = []
            for c in candidates:
                checks: list[dict[str, str]] = []
                fetched = c.get("fetched_at")
                stale = False
                if fetched:
                    try:
                        stale = (now - datetime.fromisoformat(str(fetched).replace("Z", "+00:00"))).total_seconds() > STALE_AFTER_S
                    except ValueError:
                        stale = True
                opens, closes = _minutes(c.get("opens")), _minutes(c.get("closes"))
                if stale or opens is None or closes is None:
                    checks.append({"what": "opening hours", "result": "not confirmed by the venue"})
                else:
                    ok = day0 + opens <= a.start and a.end <= day0 + closes
                    checks.append({"what": "opening hours", "result": "meets" if ok else
                                   f"breaks: open {hhmm(opens)} to {hhmm(closes)}, not {hhmm(a.start - day0)} to {hhmm(a.end - day0)}"})
                caps = [r for r in rules if r["kind"] == "max_total_cost"]
                if caps:
                    price = c.get("price_per_person")
                    if stale or price is None:
                        checks.append({"what": "budget", "result": "not confirmed by the venue"})
                    else:
                        over = any(snap.member_total(r["owner"]) - a.share(r["owner"]) + round(float(price) * 100) > int(r["value"].get("amount_minor", 0))
                                   for r in caps)
                        checks.append({"what": "budget", "result": "breaks: above a member's budget" if over else "meets"})
                tags = {t for t in (_tag(x) for x in c.get("tags") or []) if t} if not stale else set()
                for r in rules:
                    if r["kind"] != "needs" or (r["value"].get("applies_to") and a.kind not in r["value"]["applies_to"]):
                        continue
                    wanted = {t for t in (_tag(x) for x in r["value"].get("tags", [])) if t}
                    if wanted:
                        # A source that does not list a tag has not said the place lacks it: unknown, never "meets".
                        checks.append({"what": "a member's dietary or access needs", "result": "meets" if wanted <= tags else "not confirmed by the venue"})
                results = [x["result"] for x in checks]
                status = "breaks" if any(r.startswith("breaks") for r in results) else "meets" if all(r == "meets" for r in results) else "not_confirmed"
                out.append({"name": str(c.get("name", ""))[:80], "provider": str(c.get("provider", ""))[:40], "place_id": str(c.get("place_id", ""))[:200],
                            "status": status, "checks": list({json.dumps(x, sort_keys=True): x for x in checks}.values())})
            order = {"meets": 0, "not_confirmed": 1, "breaks": 2}
            out.sort(key=lambda x: order[x["status"]])
            return {"plan": p["title"], "for": a.title, "candidates": out,
                    "message": ("Checked against everyone's private limits without naming anyone. 'not confirmed' means the source did not say; "
                                "ask the venue or another tool before treating it as fine. Nothing was stored or changed.")}
        return self.db.tx(run)
