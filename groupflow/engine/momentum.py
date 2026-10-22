"""Keeping a plan moving: reminders at a chosen time, and a check-in when a plan has gone quiet while something still waits.

Reminders. set_reminder schedules a message for a time the speaker gives exactly ("tomorrow at 9", "in 2 hours", "Friday 6pm") or
relative to the plan ("1 day before", "2 hours before the dinner", "the morning of"). A vague time is refused, like other vague times.
Who gets it is decided when it fires, from the plan as it is then: "remind whoever hasn't confirmed, the day before" reaches only the
people still missing at that moment, someone who has left the plan gets nothing, and the facts are read from the plan at that moment.
That late binding is what a plain device reminder cannot do.

Check-ins. A plan is stuck when nothing has happened in it for a while (48 hours, or 24 when the event is less than a week away; an
organizer can change this or turn it off) and something specific is waiting: answers on a version, a decision, an organizer step, an
overdue task, a detail still open close to the event. The server works out what waits on whom from the plan itself, reminds exactly
those people, gently and with an easy way to say no, and tells the organizer what it did. After two reminders about the same thing it
stops reminding that person and tells the organizer instead. A language model may review the same facts and decide whether the quiet is
a problem and how to word each message, but it can only drop or reword messages: every recipient and every word is checked here first.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from groupflow.core.errors import DomainError

from .core import ASSISTANT, Row, Scope, _names
from .notices import _or_names
from .text import hour24, parse_datetime, parse_time_of_day

log = logging.getLogger("groupflow.momentum")

REMINDERS_MAX = 20  # scheduled reminders per person
NOTE_MAX = 280
CHECKIN_DEFAULT_H = 48
CHECKIN_SOON_H = 24  # when the event is less than a week away
NUDGES_PER_BLOCKER = 2  # reminders to one person about one thing before the organizer is told instead
QUIET_START, QUIET_END = 22, 8  # local hours with no automatic check-ins
# Telling, reminding and asking are not progress: they never reset how long a plan has been quiet.
META_ACTIONS = ("plan.notified", "plan.nudged", "plan.reminded", "reminder.set", "reminder.cancelled", "followup.asked", "followup.answered",
                "plan.checkins")
NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty five": 45, "forty-five": 45, "a couple": 2,
                "a couple of": 2, "half an": 0.5, "half a": 0.5}
UNITS = {"minute": 1, "min": 1, "hour": 60, "hr": 60, "h": 60, "day": 1440, "week": 10080}
PARTS = {"morning": 9 * 60, "afternoon": 14 * 60, "evening": 18 * 60, "night": 19 * 60, "tonight": 19 * 60}
WEEKDAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
PLAN_STATE_WORDS = {"ACTIVE": "in progress", "DRAFT": "a draft, not sent out yet", "PROPOSED": "a new version, not sent out yet", "AWAITING_CONFIRMATIONS": "waiting for answers",
                    "READY_TO_COMMIT": "confirmed, not locked in yet", "COMMITTED": "agreed", "DEGRADED": "agreed, but no longer works",
                    "REPAIRING": "looking for alternatives", "BLOCKED": "blocked", "NEEDS_ATTENTION": "needs attention", "CANCELLED": "cancelled"}


def _dt(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _ago(delta: timedelta) -> str:
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return "less than an hour"
    if hours < 36:
        n = round(hours)
        return f"{n} hour{'s' if n != 1 else ''}"
    n = round(hours / 24)
    return f"{n} day{'s' if n != 1 else ''}"


def _local_text(dt: datetime) -> str:
    hour = dt.strftime("%I").lstrip("0") or "12"
    return f"{dt.strftime('%a, %b')} {dt.day}, {hour}:{dt.strftime('%M %p').lower()}"


def _duration(text: str) -> timedelta | None:
    """'2 hours', 'an hour', 'half an hour', '30 min', 'a day', 'two weeks'."""
    words = "|".join(sorted((re.escape(w) for w in NUMBER_WORDS), key=len, reverse=True))
    m = re.search(rf"\b(\d+(?:\.\d+)?|{words})\s*(minutes?|mins?|hours?|hrs?|h|days?|weeks?)\b", text)
    if not m:
        return None
    raw = m.group(1)
    amount = float(raw) if re.fullmatch(r"\d+(?:\.\d+)?", raw) else NUMBER_WORDS[raw]
    unit = next(v for k, v in UNITS.items() if m.group(2).startswith(k))
    return timedelta(minutes=amount * unit)


def _clock(text: str) -> int | None:
    """A time of day the speaker named: 'at 6pm', '6:30', '18:00', 'noon', or a part of the day ('in the morning', 'tonight')."""
    m = re.search(r"\b(?:at|by|around)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b", text) or re.search(r"\b(\d{1,2}:\d{2}\s*(?:am|pm)?)\b", text) \
        or re.search(r"\b(\d{1,2}\s*(?:am|pm))\b", text)
    if m:
        return parse_time_of_day(m.group(1))
    if re.search(r"\b(noon|midday)\b", text):
        return 12 * 60
    if re.search(r"\bmidnight\b", text):
        return 0
    for word, minutes in PARTS.items():
        if re.search(rf"\b{word}\b", text):
            return minutes
    return None


def resolve_when(said: str, now: datetime, anchor: datetime | None) -> tuple[datetime | None, str]:
    """The moment a spoken time means, in the plan's time zone: (datetime, '') or (None, why not).

    Relative to the plan ('1 day before', 'the morning of') needs the plan's date (anchor). A day without a time means 9 am, 'tonight'
    7 pm. A bare clock time is today if still ahead, else tomorrow. Anything that names no time, day or amount is not guessed."""
    text = re.sub(r"\s+", " ", (said or "").lower().replace("’", "'")).strip(" .!?")
    if not text:
        return None, "empty"
    clock = _clock(text)
    before = re.search(r"\b(before|ahead of|prior to)\b", text)
    part_of = re.search(r"\bthe (morning|afternoon|evening|night|day) (of|before)\b", text)
    span = _duration(text) if before else None
    if span is None and before and re.search(r"\b(the )?(day|week) before\b", text):
        span = timedelta(days=7) if re.search(r"\bweek before\b", text) else timedelta(days=1)
    if part_of or span is not None:
        if anchor is None:
            return None, "no_date"
        if part_of:
            day = anchor.date() - timedelta(days=1 if part_of.group(2) == "before" else 0)
            minutes = PARTS.get(part_of.group(1), clock if clock is not None else 9 * 60)
            return datetime.combine(day, time(minutes // 60, minutes % 60), tzinfo=anchor.tzinfo), ""
        assert span is not None
        target = anchor - span
        if span >= timedelta(days=1):
            minutes = clock if clock is not None else 9 * 60
            target = datetime.combine(target.date(), time(minutes // 60, minutes % 60), tzinfo=anchor.tzinfo)
        return target, ""
    if re.search(r"\bin\b", text) and (span := _duration(text)) is not None:
        return now + span, ""
    base: date | None = None
    iso = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if iso:
        base = date(int(iso[1]), int(iso[2]), int(iso[3]))
    elif re.search(r"\b(today|tonight)\b", text):
        base = now.date()
    elif re.search(r"\b(yesterday|ago)\b", text):
        base = now.date() - timedelta(days=1)  # refused below as already passed, never moved to tomorrow
    elif re.search(r"\b(tomorrow|tmrw|tmr|tomorow)\b", text):
        base = now.date() + timedelta(days=1)
    else:
        named = next((i for i, d in enumerate(WEEKDAY_NAMES) if re.search(rf"\b({d}|{d[:3]})\b", text)), None)
        parts = parse_datetime(text)
        if parts.month is not None and parts.day is not None:
            for year in (now.year, now.year + 1):
                try:
                    candidate = date(year, parts.month, parts.day)
                except ValueError:
                    continue
                if candidate >= now.date():
                    base = candidate
                    break
        elif named is not None:
            ahead = (named - now.weekday()) % 7
            if ahead == 0 and (clock is None or datetime.combine(now.date(), time(clock // 60, clock % 60), tzinfo=now.tzinfo) <= now):
                ahead = 7  # "Saturday" said on a Saturday means today only for a time still ahead
            base = now.date() + timedelta(days=ahead)
        elif parts.day is not None:
            for months in (0, 1):
                y, m = (now.year + (now.month + months - 1) // 12, (now.month + months - 1) % 12 + 1)
                try:
                    candidate = date(y, m, parts.day)
                except ValueError:
                    continue
                if candidate >= now.date():
                    base = candidate
                    break
    if base is None and clock is not None:
        today_at = datetime.combine(now.date(), time(clock // 60, clock % 60), tzinfo=now.tzinfo)
        return (today_at if today_at > now else today_at + timedelta(days=1)), ""
    if base is None:
        return None, "vague"
    minutes = clock if clock is not None else (19 * 60 if re.search(r"\btonight\b", text) else 9 * 60)
    return datetime.combine(base, time(minutes // 60, minutes % 60), tzinfo=now.tzinfo), ""


def _due_date(due: str, said_at: datetime) -> date | None:
    """A task's due phrase as a date, read relative to when it was said ('Friday' said on a Tuesday is that Friday). None if unclear."""
    text = (due or "").lower()
    iso = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if iso:
        return date(int(iso[1]), int(iso[2]), int(iso[3]))
    if re.search(r"\b(today|tonight)\b", text):
        return said_at.date()
    if re.search(r"\b(tomorrow|tmrw)\b", text):
        return said_at.date() + timedelta(days=1)
    parts = parse_datetime(text)
    if parts.month is not None and parts.day is not None:
        best = None
        for year in (said_at.year - 1, said_at.year, said_at.year + 1):
            try:
                d = date(year, parts.month, parts.day)
            except ValueError:
                continue
            if best is None or abs((d - said_at.date()).days) < abs((best - said_at.date()).days):
                best = d
        return best
    named = next((i for i, d in enumerate(WEEKDAY_NAMES) if re.search(rf"\b({d}|{d[:3]})\b", text)), None)
    if named is not None:
        return said_at.date() + timedelta(days=(named - said_at.weekday()) % 7)
    return None


class Momentum:
    """Mixed into GroupFlow, above Notices: reuses its roles, facts and leak check."""

    # ================================================================ time

    def _tz(self, plan: Row) -> ZoneInfo:
        group = self._group(plan["group_id"])
        try:
            return ZoneInfo(group["timezone"] if group else "America/Toronto")
        except Exception:  # noqa: BLE001 - an unknown zone name falls back to the default
            return ZoneInfo("America/Toronto")

    def _local(self, plan: Row, stamp: str | None = None) -> datetime:
        return _dt(stamp or self.ts()).astimezone(self._tz(plan))

    def _event_time(self, plan: Row, items: list[Row]) -> datetime | None:
        """When the plan (or the given items) happens: a date-and-time detail, else the first activity. None for plans without a date."""
        tz = self._tz(plan)
        start = date.fromisoformat(plan["start_date"]) if plan["start_date"] else None
        snap = self._latest_snapshot(plan) if self._has_activities(plan) else None

        def from_detail(item: Row) -> datetime | None:
            value = self._current_value(item) or ""
            p = parse_datetime(value)
            hour = hour24(p)
            minutes = (hour if hour is not None else 9) * 60 + (p.minute or 0)
            day: date | None = None
            if p.month is not None and p.day is not None:
                ref = start or self._local(plan).date()
                options = []
                for year in (ref.year - 1, ref.year, ref.year + 1):
                    try:
                        options.append(date(year, p.month, p.day))
                    except ValueError:
                        continue
                day = min(options, key=lambda d: abs((d - ref).days)) if options else None
            elif p.weekday is not None and start is not None:
                day = start + timedelta(days=((p.weekday + 6) % 7 - start.weekday()) % 7)
            return datetime.combine(day, time(minutes // 60 % 24, minutes % 60), tzinfo=tz) if day else None

        def from_activity(activity_id: str | None = None) -> datetime | None:
            if snap is None or start is None:
                return None
            acts = [a for a in snap.live if activity_id is None or a.id == activity_id]
            if not acts:
                return None
            first = min(acts, key=lambda a: a.start)
            return datetime.combine(start, time(0, 0), tzinfo=tz) + timedelta(minutes=first.start)

        for it in items:
            if it["shape"] == "detail" and it["value_kind"] == "datetime":
                if (found := from_detail(it)) is not None:
                    return found
            if it["shape"] == "activity" and (found := from_activity(it["id"])) is not None:
                return found
        if (found := from_activity()) is not None:
            return found
        # The plan's own date, not any date in it: a household plan's "book club" entry is not when the household happens.
        for it in self.db.all("SELECT * FROM items WHERE plan_id = ? AND archived = 0 AND shape = 'detail' AND value_kind = 'datetime' "
                              "ORDER BY created_at, rowid", plan["id"]):
            if re.search(r"(^|[._\s])(date|time|when|day)\b", f"{it['id']} {it['label']}".lower()) and (found := from_detail(it)) is not None:
                return found
        return None

    def _event_over(self, plan: Row, event: datetime | None, now: datetime) -> bool:
        """The plan has happened: six hours after it starts, or after its last day for plans spanning several days."""
        if event is None:
            return False
        end = event + timedelta(hours=6)
        if plan["start_date"] and plan["days"]:
            last = date.fromisoformat(plan["start_date"]) + timedelta(days=int(plan["days"]))
            end = max(end, datetime.combine(last, time(0, 0), tzinfo=event.tzinfo))
        return now > end

    # ================================================================ reminders

    def _reminder_recipients(self, scope: Scope, plan: Row, items: list[Row], wanted: list[str], creator: str) -> list[str]:
        roles = self._plan_roles(plan, items, scope)
        out: list[str] = []
        for ref in wanted:
            level = ref.lower().replace(" ", "_")
            if level in ("me", "myself", "i"):
                out.append(creator)
                continue
            level = {"all": "everyone", "everybody": "everyone", "organizer": "organizers", "participant": "participants",
                     "owner": "owners", "pending": "needs_answer", "waiting": "needs_answer"}.get(level, level)
            if level in roles:
                out += sorted(roles[level])  # the speaker too: "remind everyone" is about forgetting, and they can forget as well
            else:
                out.append(self.resolve_member(scope, ref, plan)["id"])
        return list(dict.fromkeys(out))

    def set_reminder(self, actor_id: str, when: str | None = None, plan: str | None = None, about: str | list[str] | None = None,
                     audience: list[str] | None = None, note: str | None = None, cancel: str | None = None) -> dict[str, Any]:
        self._rate_limit(actor_id)
        if cancel:
            def drop() -> dict[str, Any]:
                row = self.db.get("SELECT * FROM reminders WHERE id = ? AND created_by = ? AND status = 'scheduled'", cancel, actor_id)
                if row is None or not self.scope_for(actor_id).sees_plan(row["plan_id"]):
                    raise DomainError("NOT_FOUND", "No scheduled reminder of yours has that id.")
                self.db.run("UPDATE reminders SET status = 'cancelled', result = 'cancelled by its creator' WHERE id = ?", row["id"])
                self._audit(actor_id, "reminder.cancelled", "plan", row["plan_id"], self._plan_row(row["plan_id"]), "Cancelled a reminder.",
                            private_to=actor_id)
                return {"outcome": "cancelled", "id": row["id"], "message": "Cancelled. Nobody will get that reminder."}
            return self.db.tx(drop)
        try:
            return self._set_reminder(actor_id, when, plan, about, audience, note)
        except DomainError as err:
            if ASSISTANT.get():
                args = {"when": when, "plan": plan, "about": about, "audience": audience, "note": note or None}
                if err.code == "AMBIGUOUS_PLAN":
                    self._clarify_plan(actor_id, err, "set_reminder", args)  # type: ignore[attr-defined]
                elif err.code == "WHEN_UNCLEAR" and err.details.get("plan"):
                    self._clarify_when(actor_id, err, args)
            raise

    def _clarify_when(self, actor_id: str, err: DomainError, args: dict[str, Any]) -> None:
        """A vague reminder time ("later", "sometime"): keep "when?" with two to four concrete times, each the same reminder at that time."""
        p = self._plan_row(err.details["plan"])
        if p is None:
            return
        now = self._local(p)
        event = self._event_time(p, [])
        choices: list[tuple[str, str]] = []
        if now.hour < 17:
            choices.append(("This evening at 7", "today at 7pm"))
        choices.append(("Tomorrow at 9am", "tomorrow at 9am"))
        if event is not None and event - now > timedelta(days=1, hours=1):
            choices.append(("The day before at 9am", "1 day before"))
        if event is not None and event.date() > now.date():
            choices.append(("The morning of", "the morning of"))
        if len(choices) < 2:
            choices.append(("In 2 hours", "in 2 hours"))
        kept = {k: v for k, v in dict(args, plan=p["id"]).items() if v not in (None, [], "")}
        options = [{"label": label, "action": {"tool": "set_reminder", "arguments": dict(kept, when=when)}} for label, when in choices[:4]]
        question = "When should the reminder go off: " + _or_names([label[0].lower() + label[1:] for label, _ in choices[:4]]) + "?"
        try:
            fid = self.db.tx(lambda: self._store_followup(actor_id, "clarify", question, options, p, "suggested"))  # type: ignore[attr-defined]
        except DomainError:
            return
        err.details["followup"] = {"id": fid, "question": question, "options": [o["label"] for o in options]}

    def _set_reminder(self, actor_id: str, when: str | None, plan: str | None, about: str | list[str] | None, audience: list[str] | None,
                      note: str | None) -> dict[str, Any]:
        note = re.sub(r"\s+", " ", (note or "").strip())
        if len(note) > NOTE_MAX:
            raise DomainError("INVALID_ARGUMENT", f"Keep the note under {NOTE_MAX} characters.")
        refs = [a for a in ([about] if isinstance(about, str) else list(about or [])) if a and a.strip()]
        wanted = [a.strip() for a in (audience or ["me"]) if a and a.strip()] or ["me"]

        def run() -> dict[str, Any]:
            scope = self.scope_for(actor_id)
            p = self.resolve_plan(scope, plan) if plan else None
            items = list({i["id"]: i for i in (self.resolve_item(scope, r, p["id"] if p else None) for r in refs) if i}.values())
            if p is None:
                p = scope.plans[items[0]["plan_id"]] if items else self.resolve_plan(scope, None, purpose="a reminder could be about")
            if any(i["plan_id"] != p["id"] for i in items):
                raise DomainError("INVALID_ARGUMENT", "A reminder is about one plan; set one per plan.")
            recipients = self._reminder_recipients(scope, p, items, wanted, actor_id)
            # Checked against everyone who can see the plan, not only the recipients: the reminder lives in this plan, and its text can be
            # read out wherever the plan is (a family room, a kitchen Echo), even when it is only for the speaker.
            if note and (leak := self._note_leak(actor_id, note, set(self.plan_audience(p)) - {actor_id})):
                raise DomainError("PRIVATE_NOTE", f"The note mentions something {self.name(leak)} cannot see, and {self.name(leak)} can see "
                                  f"{p['title']}. Put the reminder on the plan it is about, or set it without that part.")
            now = self._local(p)
            due, why = resolve_when(when or "", now, self._event_time(p, items))
            if due is None:
                hint = {"no_date": f"{p['title']} has no date to count back from; say an exact time instead (\"Friday at 9am\").",
                        "empty": "Say when."}.get(why, "Say exactly when: a time (\"tomorrow at 9\"), a delay (\"in 2 hours\") or relative to the plan "
                                                         "(\"1 day before\"). Ask the speaker rather than guessing.")
                raise DomainError("WHEN_UNCLEAR", hint, {"said": when or "", "plan": p["id"]})
            if due <= now + timedelta(minutes=1):
                raise DomainError("INVALID_ARGUMENT", f"That is {_local_text(due)}, which has already passed. Pick a later time.")
            if due > now + timedelta(days=366):
                raise DomainError("INVALID_ARGUMENT", "Reminders can be at most a year ahead.")
            if (self.db.scalar("SELECT COUNT(*) FROM reminders WHERE created_by = ? AND status = 'scheduled'", actor_id) or 0) >= REMINDERS_MAX:
                raise DomainError("RATE_LIMITED", f"You already have {REMINDERS_MAX} reminders waiting. Cancel one first.")
            tokens = [w if w.lower().replace(" ", "_") in ("me", "myself", "i", "everyone", "all", "everybody", "organizers", "organizer",
                                                            "participants", "participant", "owners", "owner", "needs_answer", "pending", "waiting")
                      else "id:" + self.resolve_member(scope, w, p)["id"] for w in wanted]
            rid = self.new_id("rm")
            self.db.run("INSERT INTO reminders (id, plan_id, created_by, audience_json, about_json, note, when_text, due_at, status, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'scheduled', ?)", rid, p["id"], actor_id, json.dumps(tokens), json.dumps([i["id"] for i in items]),
                        note or None, (when or "").strip()[:120], _iso(due), self.ts())
            self._audit(actor_id, "reminder.set", "plan", p["id"], p, f"Set a reminder for {_local_text(due)}.", private_to=actor_id)
            who = "you" if recipients == [actor_id] else _names(["you" if m == actor_id else self.name(m) for m in recipients]) or "nobody right now"
            levels = [w for w in wanted if w.lower().replace(" ", "_") in ("needs_answer", "pending", "waiting", "participants", "owners", "organizers")]
            later = " Who still needs it is checked again when it goes off." if levels else ""
            whose = "your" if recipients == [actor_id] else "their"
            return {"outcome": "scheduled", "id": rid, "plan": p["title"], "when": _local_text(due), "to": who,
                    "about": [i["label"] for i in items], "message": f"Reminder set for {_local_text(due)} for {who}.{later} At that time it shows "
                    f"up in GroupFlow, and Alexa mentions it on {whose} next turn."}
        return self.db.tx(run)

    def _your_reminders(self, scope: Scope) -> list[dict[str, Any]]:
        rows = self.db.all("SELECT * FROM reminders WHERE created_by = ? AND status = 'scheduled' ORDER BY due_at LIMIT 10", scope.member_id)
        out = []
        for r in rows:
            if not scope.sees_plan(r["plan_id"]):
                continue
            p = scope.plans[r["plan_id"]]
            out.append({"id": r["id"], "plan": p["title"], "when": _local_text(self._local(p, r["due_at"])), "said": r["when_text"],
                        "about": [i["label"] for i in (self._item(x) for x in json.loads(r["about_json"])) if i] or None, "note": r["note"]})
        return out

    def deliver_due_reminders(self, as_of: str | None = None) -> int:
        """Send every reminder whose time has come. Called by the worker; returns how many people were reminded."""
        now = as_of or self.ts()
        sent = 0
        for row in self.db.all("SELECT * FROM reminders WHERE status = 'scheduled' AND due_at <= ? ORDER BY due_at LIMIT 50", now):
            try:
                sent += self.db.tx(lambda r=row: self._fire_reminder(r))
            except Exception as err:  # noqa: BLE001 - a broken reminder is closed, not retried every second ahead of the others
                log.exception("reminder %s failed", row["id"])
                self.db.run("UPDATE reminders SET status = 'cancelled', sent_at = ?, result = ? WHERE id = ?", self.ts(),
                            f"failed: {type(err).__name__}", row["id"])
        return sent

    def _fire_reminder(self, due: Row) -> int:
        r = self.db.get("SELECT * FROM reminders WHERE id = ? AND status = 'scheduled'", due["id"])
        if r is None:
            return 0  # another pass sent it a moment ago

        def close(result: str) -> int:
            self.db.run("UPDATE reminders SET status = 'cancelled', sent_at = ?, result = ? WHERE id = ?", self.ts(), result, r["id"])
            return 0

        p = self._plan_row(r["plan_id"])
        if p is None or p["status"] == "CANCELLED":
            return close("the plan was cancelled")
        creator = r["created_by"]
        try:
            scope = self.scope_for(creator)
        except DomainError:
            return close("its creator is gone")
        if not scope.sees_plan(p["id"]):
            return close("its creator can no longer see the plan")
        items = [i for i in (self._item(x) for x in json.loads(r["about_json"])) if i and not i["archived"]]
        wanted = [t[3:] if t.startswith("id:") else t for t in json.loads(r["audience_json"])]
        audience = set(self.plan_audience(p))
        recipients: list[str] = []
        try:
            recipients = [m for m in self._reminder_recipients(scope, p, items, wanted, creator) if m in audience]
        except DomainError:
            recipients = [m for m in (t[3:] for t in json.loads(r["audience_json"]) if t.startswith("id:")) if m in audience]
        if r["note"] and self._note_leak(creator, r["note"], set(audience) - {creator}):
            return close("its note mentions something someone who can now see the plan must not")
        facts = "; ".join(self._about_fact(p, i) for i in items)
        roles = self._plan_roles(p, items, scope)
        told = []
        for m in recipients:
            _, hint = self._role_hint(p, items, m, roles)
            if not (facts or r["note"] or hint):
                hint = f"It is {PLAN_STATE_WORDS.get(p['status'], 'in progress')}."  # a reminder about the plan as a whole
            if m == creator:
                said = [f'"{r["note"]}"'] if r["note"] else []
                text = f"Reminder you set for {p['title']}" + (": " + ". ".join(said + ([facts] if facts else [])) + "." if said or facts else ".")
            else:
                text = f"Reminder from {self.name(creator)} about {p['title']}" + (f": {facts}." if facts else ".")
                text += f' {self.name(creator)} says: "{r["note"]}"' if r["note"] else ""
            text += f" {hint}" if hint else ""
            self._notify([m], p, "reminder", text)
            told.append(m)
        self.db.run("UPDATE reminders SET status = 'sent', sent_at = ?, result = ? WHERE id = ?", self.ts(),
                    f"reminded {len(told)}" if told else "nobody needed it any more", r["id"])
        if told and told != [creator]:
            self._audit(creator, "plan.reminded", "plan", p["id"], p, f"Reminder from {self.name(creator)} sent to {len(told)} "
                        f"{'person' if len(told) == 1 else 'people'}.")
        return len(told)

    # ================================================================ is the plan stuck?

    def _checkin_hours(self, plan: Row) -> int | None:
        row = self.db.get("SELECT quiet_hours FROM plan_checkins WHERE plan_id = ?", plan["id"])
        return row["quiet_hours"] if row else None

    def _last_activity(self, plan: Row) -> str:
        marks = ",".join("?" for _ in META_ACTIONS)
        last = self.db.scalar(f"SELECT MAX(at) FROM audit WHERE plan_id = ? AND action NOT IN ({marks})", plan["id"], *META_ACTIONS)
        return last or plan["created_at"]

    def _blockers(self, plan: Row, now: datetime, event: datetime | None) -> list[dict[str, Any]]:
        """What the plan waits on, and on whom, from its own state. Names only of people in the plan; never anyone's private reasons."""
        out: list[dict[str, Any]] = []
        audience = set(self.plan_audience(plan))
        organizer = plan["organizer_id"]
        cand = self._revision(plan["candidate_revision_id"]) if plan["candidate_revision_id"] else None
        if cand is not None and self._round_open(cand):
            pending = sorted({s["member_id"] for s, status, _ in self._requirements(cand) if status == "pending"} & audience)
            if pending:
                sent = self.db.scalar("SELECT MAX(at) FROM audit WHERE plan_id = ? AND action = 'confirmations.requested'", plan["id"]) or cand["created_at"]
                out.append({"kind": "confirm", "key": f"confirm:{cand['id']}", "what": f"answers on the sent plan ({cand['label']})", "waiting_on": pending, "since": sent,
                            "next_step": "each of them confirms or declines on their own GroupFlow page"})
        for r in self.db.all("SELECT * FROM requests WHERE plan_id = ? AND status = 'open' ORDER BY created_at", plan["id"]):
            if r["assignee_id"] not in audience:
                continue
            item = self._item(r["item_id"]) if r["item_id"] else None
            what = f'{self.name(r["requested_by"])}\'s suggestion for {item["label"] if item else plan["title"]} ("{r["summary"]}")'
            out.append({"kind": "decide", "key": f"request:{r['id']}", "what": what, "waiting_on": [r["assignee_id"]], "since": r["created_at"],
                        "next_step": "a yes or no from them to Alexa"})
        status = plan["status"]
        last = self._last_activity(plan)
        if status == "READY_TO_COMMIT" and organizer in audience:
            out.append({"kind": "commit", "key": f"commit:{plan['candidate_revision_id']}", "what": "locking in the version everyone confirmed",
                        "waiting_on": [organizer], "since": last, "next_step": "the organizer asks Alexa to lock it in"})
        elif status in ("DEGRADED", "BLOCKED", "NEEDS_ATTENTION") and organizer in audience:
            out.append({"kind": "repair", "key": f"repair:{plan['aggregate_version']}", "what": "a fix, because the agreed plan no longer works",
                        "waiting_on": [organizer], "since": last, "next_step": "the organizer asks Alexa for alternatives"})
        elif status in ("DRAFT", "PROPOSED") and self._has_activities(plan) and organizer in audience:
            out.append({"kind": "send", "key": f"send:{plan['aggregate_version']}", "what": "sending the plan out for confirmation",
                        "waiting_on": [organizer], "since": last, "next_step": "the organizer asks Alexa to send it out"})
        tz = self._tz(plan)
        for t in self.db.all("SELECT * FROM items WHERE plan_id = ? AND shape = 'task' AND archived = 0 AND status <> 'done' AND due IS NOT NULL",
                             plan["id"]):
            due = _due_date(t["due"], _dt(t["updated_at"]).astimezone(tz))
            if due is not None and due < now.astimezone(tz).date() and t["owner_id"] in audience:
                out.append({"kind": "overdue", "key": f"overdue:{t['id']}:{t['due']}", "what": f'"{t["label"]}", due {t["due"]}',
                            "waiting_on": [t["owner_id"]], "since": t["updated_at"], "next_step": "the owner says it is done, or when it will be"})
        if event is not None and timedelta(0) <= event - now <= timedelta(days=7):
            for d in self.db.all("SELECT * FROM items WHERE plan_id = ? AND shape = 'detail' AND archived = 0 AND current_fact_id IS NULL", plan["id"]):
                if d["owner_id"] in audience:
                    out.append({"kind": "open_detail", "key": f"detail:{d['id']}", "what": f'"{d["label"]}", still not set', "waiting_on": [d["owner_id"]],
                                "since": d["created_at"], "next_step": "the owner decides it"})
        return out

    def _after_event_blockers(self, plan: Row, now: datetime, event: datetime | None) -> list[dict[str, Any]]:
        """What still waits once the plan has happened. Nothing here; shared costs add settling up."""
        return []

    def _loose_ends(self, plan: Row, now: datetime, event: datetime | None) -> list[dict[str, Any]]:
        """Tasks nobody has started with the event three days away or less. Not a blocker: some are meant for the day itself
        ("drive Linda to the party"). Rules only tell the organizer; a reviewing model may judge which owners to remind."""
        if event is None or not timedelta(0) <= event - now <= timedelta(days=3):
            return []
        audience = set(self.plan_audience(plan))
        return [{"kind": "not_started", "key": f"task:{t['id']}", "label": t["label"], "what": f'"{t["label"]}", not started', "waiting_on": [t["owner_id"]],
                 "since": t["updated_at"], "next_step": "the owner starts it, or says it is for the day itself"}
                for t in self.db.all("SELECT * FROM items WHERE plan_id = ? AND shape = 'task' AND archived = 0 AND status = 'todo' AND due IS NULL "
                                     "ORDER BY created_at, rowid", plan["id"])
                if t["owner_id"] in audience]

    def _nudge_text(self, plan: Row, b: dict[str, Any], member: str, quiet: timedelta) -> str:
        title, quiet_text = plan["title"], _ago(quiet)
        if b["kind"] == "confirm":
            return (f"Check-in on {title}: it has been waiting {quiet_text} for a few answers, yours included. Please confirm or decline on your "
                    "own screen; a no helps just as much as a yes.")
        if b["kind"] == "decide":
            return f"Check-in on {title}: {b['what']} has waited {quiet_text} for your decision. Tell Alexa yes or no; either answer lets the others plan."
        if b["kind"] == "commit":
            return f"Check-in on {title}: everyone needed has confirmed. It is waiting for you to lock it in; ask Alexa when you are ready."
        if b["kind"] == "repair":
            return f"Check-in on {title}: the agreed plan no longer works and nothing has changed for {quiet_text}. Ask Alexa for alternatives when you can."
        if b["kind"] == "send":
            return f"Check-in on {title}: it is still a draft after {quiet_text}, and nobody has been asked yet. Send it out when it looks right."
        if b["kind"] == "overdue":
            return f"Check-in on {title}: {b['what']}, is not done yet. If plans changed, tell Alexa, so the others know where it stands."
        if b["kind"] == "not_started":
            return (f"Check-in on {title}: {b['what']}, and the day is close. If it is meant for the day itself, all good; if you need a hand, "
                    "tell Alexa and the others will hear.")
        return f"Check-in on {title}: {b['what']} and the day is close. It is yours to decide; tell Alexa what it should be."

    def progress_report(self, plan_id: str, as_of: str | None = None) -> dict[str, Any]:
        """What a plan is waiting on and for how long, and who a check-in would remind now. The server's facts, for the worker, the
        organizer's review_plan and an optional reviewing model."""
        p = self._plan_row(plan_id)
        if p is None:
            raise DomainError("NOT_FOUND", "No such plan.")
        stamp = as_of or self.ts()
        now = _dt(stamp).astimezone(self._tz(p))
        last = self._last_activity(p)
        quiet = now - _dt(last)
        event = self._event_time(p, [])
        event_in = (event - now) if event is not None else None
        setting = self._checkin_hours(p)
        threshold = setting if setting else (CHECKIN_SOON_H if event_in is not None and timedelta(0) <= event_in <= timedelta(days=7) else CHECKIN_DEFAULT_H)
        over = self._event_over(p, event, now) or p["status"] == "CANCELLED"
        # After the day only what is left to wrap up (settling shared costs) is waited on; a cancelled plan waits on nothing.
        blockers = (self._after_event_blockers(p, now, event) if p["status"] != "CANCELLED" else []) if over else self._blockers(p, now, event)
        loose = [] if over else self._loose_ends(p, now, event)
        stuck = bool(blockers or loose) and quiet >= timedelta(hours=threshold)
        last_round = self.db.scalar("SELECT MAX(created_at) FROM nudges WHERE plan_id = ?", p["id"])
        due = stuck and setting != 0 and (last_round is None or last_round < last or now - _dt(last_round) >= timedelta(hours=threshold))
        drafts, optional, escalate = [], [], []
        for b in blockers + loose:
            for m in b["waiting_on"]:
                count = self.db.scalar("SELECT COUNT(*) FROM nudges WHERE plan_id = ? AND member_id = ? AND blocker = ?", p["id"], m, b["key"]) or 0
                draft = {"to": m, "name": self.name(m), "blocker": b["key"], "kind": b["kind"], "what": b["what"], "text": self._nudge_text(p, b, m, quiet)}
                if count >= NUDGES_PER_BLOCKER:
                    # After the limit the organizer is told once, then nothing more about it. Reminding the organizer about their own
                    # step goes nowhere new, so that just stops.
                    told = self.db.scalar("SELECT COUNT(*) FROM nudges WHERE plan_id = ? AND member_id = ? AND blocker = ?", p["id"], m,
                                          f"escalated:{b['key']}")
                    if m != p["organizer_id"] and not told and b not in loose:
                        escalate.append({"member": m, "name": self.name(m), "key": b["key"], "what": b["what"], "reminders": count})
                elif b in loose:
                    optional.append(draft)
                else:
                    drafts.append(draft)
        history = [{"at": a["at"], "who": self.name(a["actor_id"]) if a["actor_id"] else None, "what": a["detail"]} for a in self.db.all(
            f"SELECT * FROM audit WHERE plan_id = ? AND private_to IS NULL AND action NOT IN ({','.join('?' for _ in META_ACTIONS)}) "
            "ORDER BY seq DESC LIMIT 8", p["id"], *META_ACTIONS)]
        return {"plan": p["title"], "plan_id": p["id"], "status": p["status"], "status_words": PLAN_STATE_WORDS.get(p["status"], p["status"].lower()),
                "organizer": p["organizer_id"], "organizer_name": self.name(p["organizer_id"]), "last_activity": last, "quiet_for": _ago(quiet),
                "quiet_hours": round(quiet.total_seconds() / 3600, 1), "event": _local_text(event) if event else None,
                "event_in": _ago(event_in) if event_in is not None and event_in > timedelta(0) else None,
                "checkins": "off" if setting == 0 else f"after {threshold} quiet hours", "threshold_hours": threshold,
                "stuck": stuck, "due": due,
                "blockers": [dict(b, waiting_on_names=[self.name(m) for m in b["waiting_on"]], since_text=_ago(now - _dt(b["since"]))) for b in blockers],
                "loose_ends": [dict(b, waiting_on_names=[self.name(m) for m in b["waiting_on"]]) for b in loose],
                "drafts": drafts, "optional": optional, "escalate": escalate, "history": history, "round_at": last_round}

    def review_plan(self, actor_id: str, plan: str | None = None, check_ins: str | None = None) -> dict[str, Any]:
        """For any member: what the plan is waiting on. For the organizer: also turn automatic check-ins on, off or to 24/48 hours."""
        self._rate_limit(actor_id)

        def run() -> dict[str, Any]:
            scope = self.scope_for(actor_id)
            p = self.resolve_plan(scope, plan, purpose="you could check on")
            if check_ins:
                self._require_organizer(p, actor_id, "change automatic check-ins")
                hours = {"off": 0, "24h": 24, "48h": 48, "daily": 24}.get(check_ins.lower())
                if hours is None:
                    raise DomainError("INVALID_ARGUMENT", 'check_ins is "off", "24h" or "48h".')
                self.db.run("INSERT INTO plan_checkins (plan_id, quiet_hours, updated_by, updated_at) VALUES (?, ?, ?, ?) "
                            "ON CONFLICT(plan_id) DO UPDATE SET quiet_hours = excluded.quiet_hours, updated_by = excluded.updated_by, updated_at = excluded.updated_at",
                            p["id"], hours, actor_id, self.ts())
                self._audit(actor_id, "plan.checkins", "plan", p["id"], p, f"Automatic check-ins: {'off' if hours == 0 else f'after {hours} quiet hours'}.")
            r = self.progress_report(p["id"])

            def who(b: dict[str, Any]) -> list[str]:
                return ["you" if m == actor_id else n for m, n in zip(b["waiting_on"], b["waiting_on_names"])]

            waiting = [{"what": b["what"], "waiting_on": who(b), "for": b["since_text"], "next_step": b["next_step"]} for b in r["blockers"]]
            loose = [{"what": b["label"], "owner": who(b)} for b in r["loose_ends"]]
            parts = [f"{p['title']} has been quiet for {r['quiet_for']}."]
            if waiting:
                parts.append("Waiting on: " + "; ".join(f"{w['what']} from {_names(w['waiting_on'])}" for w in waiting) + ".")
            if loose:
                parts.append("Not started yet, with the day close: " + "; ".join(f'"{x["what"]}" ({_names(x["owner"])})' for x in loose) + ".")
            if not waiting and not loose:
                parts = [f"{p['title']} is not waiting on anyone right now."]
            elif r["checkins"] == "off":
                parts.append("Automatic check-ins are off for this plan.")
            elif r["stuck"]:
                organizer = "you" if p["organizer_id"] == actor_id else self.name(p["organizer_id"])
                if not (r["drafts"] or r["optional"] or r["escalate"]):
                    parts.append("Everyone it waits on has had two reminders, so no more go out automatically.")
                elif r["due"]:
                    parts.append("The next check-in will remind them." if r["drafts"] else f"The next check-in will point it out to {organizer}.")
                else:
                    parts.append(f"They were reminded; the next check-in comes after another {r['threshold_hours']} quiet hours.")
            out = {"plan": p["title"], "status": r["status_words"], "quiet_for": r["quiet_for"], "event": r["event"], "checkins": r["checkins"],
                   "stuck": r["stuck"], "waiting": waiting, "message": " ".join(parts)}
            if loose:
                out["not_started"] = loose
            if self.is_organizer(p, actor_id):
                silent = [f"{n} ({b['what']})" for b in r["blockers"] for m, n in zip(b["waiting_on"], b["waiting_on_names"]) if m != actor_id and
                          (self.db.scalar("SELECT COUNT(*) FROM nudges WHERE plan_id = ? AND member_id = ? AND blocker = ?", p["id"], m, b["key"]) or 0)
                          >= NUDGES_PER_BLOCKER]
                if silent:
                    out["no_answer_after_reminders"] = silent
            return out
        return self.db.tx(run)

    def plans_due_for_review(self, as_of: str | None = None, quiet_hours: bool = True) -> list[str]:
        """Plans a check-in should look at now: quiet long enough, something waiting, not already reminded this round, and daytime there."""
        out = []
        for p in self.db.all("SELECT * FROM plans WHERE status <> 'CANCELLED' ORDER BY created_at"):
            local = self._local(p, as_of)
            if quiet_hours and not QUIET_END <= local.hour < QUIET_START:
                continue
            if self._checkin_hours(p) == 0 or local - _dt(self._last_activity(p)) < timedelta(hours=CHECKIN_SOON_H):
                continue  # off, or active within the shortest quiet time: no need to work out the rest
            try:
                report = self.progress_report(p["id"], as_of)
            except Exception:  # noqa: BLE001 - one plan's odd state must not stop check-ins for every other plan
                log.exception("progress report failed for plan %s", p["id"])
                continue
            if report["due"] and (report["drafts"] or report["optional"] or report["escalate"]):
                out.append(p["id"])
        return out

    def _text_leak(self, plan: Row, text: str, member: str) -> bool:
        """A model's text for this person mentions something that anyone in the plan can see and they cannot (another plan, a hidden
        detail). The model was only given this plan's facts; this is the second lock."""
        return any(self._note_leak(other, text, {member}) for other in self.plan_audience(plan) if other != member)

    def _combined_text(self, plan: Row, drafts: list[dict[str, Any]]) -> str:
        """One message for everything one person is asked about in a round: the first in full, the rest named briefly."""
        others = [d["what"] for d in drafts[1:]]
        if not others:
            return drafts[0]["text"]
        return f"{drafts[0]['text']} Also waiting on you: " + "; ".join(others[:2]) + (f"; and {len(others) - 2} more" if len(others) > 2 else "") + "."

    def apply_review(self, plan_id: str, report: dict[str, Any], decision: dict[str, Any] | None = None, source: str = "rules") -> dict[str, Any]:
        """Send one check-in round for a plan. Without a decision the server's drafts go out: reminders about real blockers, and a summary
        for the organizer that lists loose ends. A decision (from a reviewing model) may say the plan is fine, choose whom to remind among
        the people the server listed (blockers and loose ends), and word each message; it can never add anyone, and every text is checked
        against what its recipient may see, falling back to the server's wording. One message per person per round."""
        def record(member: str, blocker: str, text: str, origin: str) -> None:
            self.db.run("INSERT INTO nudges (id, plan_id, member_id, blocker, text, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        self.new_id("nd"), plan_id, member, blocker, text, origin, self.ts())

        def clean(value: Any, limit: int) -> str:
            text = re.sub(r"\s+", " ", str(value or "")).strip()
            return text if 0 < len(text) <= limit else ""

        def run() -> dict[str, Any]:
            p = self._plan_row(plan_id)
            if p is None or p["status"] == "CANCELLED":
                return {"sent": [], "skipped": "plan gone"}
            if "round_at" in report and self.db.scalar("SELECT MAX(created_at) FROM nudges WHERE plan_id = ?", plan_id) != report["round_at"]:
                return {"sent": [], "skipped": "another check-in just ran"}
            organizer = p["organizer_id"]
            audience = set(self.plan_audience(p))
            if decision is not None and decision.get("stuck") is False:
                reason = clean(decision.get("reason"), 300)
                record(organizer, "round", f"not stuck: {reason}", source)
                self._audit(None, "plan.nudged", "plan", p["id"], p, "Check-in: quiet, but nothing is stuck; nobody was reminded.",
                            payload={"source": source, "reason": reason})
                return {"sent": [], "reviewed": "not stuck", "reason": reason}
            drafts = [d for d in report["drafts"] if d["to"] in audience]
            optional = [d for d in report.get("optional", []) if d["to"] in audience]
            covered: dict[str, list[dict[str, Any]]] = {}
            texts: dict[str, tuple[str, str]] = {}
            if decision is not None and isinstance(decision.get("nudges"), list):
                candidates: dict[str, list[dict[str, Any]]] = {}
                for d in drafts + optional:
                    candidates.setdefault(d["to"], []).append(d)
                for n in decision["nudges"]:
                    if not isinstance(n, dict) or n.get("to") not in candidates or n["to"] in covered:
                        continue  # a model cannot add anyone, or message someone twice
                    who = n["to"]
                    covered[who] = candidates[who]
                    text = clean(n.get("text"), 300)
                    safe = bool(text) and not self._text_leak(p, text, who)
                    texts[who] = (text, source) if safe else (self._combined_text(p, candidates[who]), "rules")
            else:
                for d in drafts:
                    covered.setdefault(d["to"], []).append(d)
                texts = {m: (self._combined_text(p, ds), "rules") for m, ds in covered.items()}
            reminded = sorted(self.name(m) for m in covered if m != organizer)
            nudged_keys = {d["blocker"] for ds in covered.values() for d in ds}
            notes: list[str] = []
            model_note = clean((decision or {}).get("organizer_summary"), 400)
            if model_note and not self._text_leak(p, model_note, organizer):
                notes.append(model_note)
            else:
                waits = [f"{b['what']} from {_names(b['waiting_on_names'])}" for b in report["blockers"] if organizer not in b["waiting_on"]]
                if waits:
                    notes.append("Waiting on " + "; ".join(waits[:3]) + (f"; and {len(waits) - 3} more" if len(waits) > 3 else "") + ".")
                loose = [b for b in report.get("loose_ends", []) if b["key"] not in nudged_keys]
                away = f", with the day {report['event_in']} away" if report.get("event_in") else ""
                tasks = [b for b in loose if b["kind"] == "not_started"]
                if tasks:
                    named = [f'"{b["label"]}" ({_names(b["waiting_on_names"])})' for b in tasks]
                    notes.append(f"Not started yet{away}: " + "; ".join(named[:3]) + (f"; and {len(named) - 3} more" if len(named) > 3 else "")
                                 + ". Ask Alexa to remind them if they should be on it already.")
                unclaimed = [b["label"] for b in loose if b["kind"] == "unclaimed"]
                if unclaimed:
                    notes.append(f"Nobody is bringing {_names(unclaimed[:4])}{' and more' if len(unclaimed) > 4 else ''} yet{away}. "
                                 "Ask Alexa to tell everyone if someone should pick them up.")
            if reminded:
                notes.append(f"I reminded {_names(reminded)}.")
            escalated = list(report["escalate"])
            by_what: dict[tuple[str, int], list[str]] = {}
            for e in escalated:
                by_what.setdefault((e["what"], e["reminders"]), []).append(e["name"])
            for (what, count), names in by_what.items():
                notes.append(f"{_names(names)} {'has' if len(names) == 1 else 'have'} not answered after {count} reminders about {what}. "
                             f"You could check with {'them' if len(names) > 1 else names[0]} directly, or ask Alexa for alternatives.")
            sent = []
            for m, ds in covered.items():
                text, origin = texts[m]
                if m == organizer and notes:
                    text = f"{text} {' '.join(notes)}"
                self._notify([m], p, "nudge", text)
                for d in ds:
                    record(m, d["blocker"], text, origin)
                sent.append({"to": m, "name": self.name(m), "text": text, "source": origin})
            summary = None
            if organizer in audience and organizer not in covered and (reminded or escalated or len(notes) > (1 if reminded else 0)):
                summary = f"Check-in on {p['title']}: nothing has moved for {report['quiet_for']}. " + " ".join(notes)
                self._notify([organizer], p, "nudge", summary)
                sent.append({"to": organizer, "name": self.name(organizer), "text": summary, "source": source if model_note else "rules"})
            for e in escalated:
                record(e["member"], f"escalated:{e['key']}", "told the organizer", "rules")
            record(organizer, "round", summary or "", source if decision else "rules")
            n = len(reminded)
            self._audit(None, "plan.nudged", "plan", p["id"], p, f"Check-in after {report['quiet_for']} without progress: reminded {n} "
                        f"{'person' if n == 1 else 'people'}." if n else f"Check-in after {report['quiet_for']} without progress.",
                        payload={"source": source if decision else "rules", "reason": clean((decision or {}).get("reason"), 300)})
            return {"sent": sent, "summary": summary}
        return self.db.tx(run)

    # ================================================================ hooks

    def context(self, actor_id: str, *args: Any, **kwargs: Any) -> Any:
        view = super().context(actor_id, *args, **kwargs)  # type: ignore[misc]

        def reminders() -> list[dict[str, Any]]:
            scope = self.scope_for(actor_id)
            hidden = {scope.plans[i]["title"] for i in scope.room_hidden if i != view.active_plan_id}
            return [r for r in self._your_reminders(scope) if r["plan"] not in hidden]
        view.your_reminders = self.db.tx(reminders)
        return view
