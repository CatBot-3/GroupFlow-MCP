"""Text helpers: canonical forms, topic words, and parsing of times and days.

Canonical forms decide whether two statements say the same thing.

"Sunday the 25th at 6" and "Sunday, Oct 25, 6:00 pm" must compare equal,
otherwise a repeat of the plan would be treated as a conflicting change.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

WEEKDAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]

NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
}


@dataclass
class DateTimeParts:
    weekday: int | None = None
    month: int | None = None
    day: int | None = None
    hour: int | None = None
    minute: int | None = None
    meridiem: str | None = None


def parse_datetime(value: str) -> DateTimeParts:
    text = value.lower().replace(".", "").replace(",", " ")
    p = DateTimeParts()

    for i, d in enumerate(WEEKDAYS):
        if re.search(rf"\b{d}\b", text):
            p.weekday = i
            break
    else:
        abbr = re.search(r"\b(sun|mon|tue|tues|wed|thu|thur|thurs|fri|sat)\b", text)
        if abbr:
            p.weekday = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"].index(abbr.group(1)[:3])

    month_match = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+(\d{1,2})(st|nd|rd|th)?\b", text)
    if month_match:
        p.month = MONTHS.index(month_match.group(1)[:3]) + 1
        p.day = int(month_match.group(2))
    else:
        ordinal = re.search(r"\b(\d{1,2})(st|nd|rd|th)\b", text)
        if ordinal:
            p.day = int(ordinal.group(1))
        month_only = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", text)
        if month_only and month_only.group(1) != "may":
            p.month = MONTHS.index(month_only.group(1)) + 1

    # Time: explicit am/pm or hh:mm first, then "at 6" style.
    t = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text)
    if not t:
        t = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    if not t:
        t = re.search(r"\b(?:at|by|around)\s+(\d{1,2})(?::(\d{2}))?\b(?!\s*(st|nd|rd|th|guests|people))", text)
    if t:
        hour = int(t.group(1))
        if 0 <= hour <= 23:
            p.hour = hour
            p.minute = int(t.group(2)) if t.group(2) else 0
            mer = t.group(3) if t.lastindex and t.lastindex >= 3 else None
            if mer in ("am", "pm"):
                p.meridiem = mer
    elif re.search(r"\bnoon\b", text):
        p.hour, p.minute, p.meridiem = 12, 0, "pm"
    return p


def hour24(p: DateTimeParts) -> int | None:
    """24h hour, assuming afternoon for small bare hours (family events rarely start at 6 am)."""
    if p.hour is None:
        return None
    h = p.hour
    if p.meridiem == "pm" and h < 12:
        h += 12
    elif p.meridiem == "am" and h == 12:
        h = 0
    elif not p.meridiem and 1 <= h <= 7:
        h += 12
    return h


def bare_weekday_dates(quote: str, current: str, plan_start: str) -> list:
    """Every date a bare weekday in the quote can mean, each anchored to the existing item's week.

    A self-correction names several weekdays ("Friday night, wait no, make it Sunday, Friday is my
    sister's recital"), and which one the speaker settled on is for the model to read; the server
    only checks that the date is one the speaker could have meant, never a date from another week.
    Empty (no check) unless a weekday is the target of a move ("to Sunday", "on Friday"): a weekday
    mentioned only as context ("Rosa's double-booked Saturday") constrains nothing. Also empty when the
    quote gives an explicit date or this/next/last, or the item has no date to anchor to.
    """
    from datetime import date, timedelta

    weekdays = r"(sunday|monday|tuesday|wednesday|thursday|friday|saturday|sun|mon|tue|wed|thu|fri|sat)"
    quoted, existing = parse_datetime(quote), parse_datetime(current)
    if not re.search(r"(?:^|\b(?:to|on|for|now)\s+)" + weekdays + r"\b", quote, re.I):
        return []
    if quoted.month is not None or quoted.day is not None or re.search(r"\b(this|next|last|today|tomorrow)\b|\d{4}-\d{2}-\d{2}", quote, re.I):
        return []
    if existing.month is None or existing.day is None:
        return []
    anchor = date.fromisoformat(plan_start)
    candidates = []
    for year in (anchor.year - 1, anchor.year, anchor.year + 1):
        try:
            candidates.append(date(year, existing.month, existing.day))
        except ValueError:
            continue
    if not candidates:
        return []
    reference = min(candidates, key=lambda d: abs((d - anchor).days))
    out = []
    for word in re.findall(r"\b" + weekdays + r"\b", quote, re.I):
        spoken = parse_datetime(word)
        if spoken.weekday is None:
            continue
        delta = ((spoken.weekday + 6) % 7 - reference.weekday()) % 7  # parser Sunday=0, datetime Monday=0
        if delta > 3:
            delta -= 7
        d = reference + timedelta(days=delta)
        if d not in out:
            out.append(d)
    return out


_VAGUE_WHEN = re.compile(
    r"\b(?:a (?:bit|little|tad|smidge)(?: (?:later|earlier|sooner|after|before))?|(?:bit|little) (?:later|earlier|sooner)|later|earlier|sooner|"
    r"sometime|some time|whenever|at some point|another (?:day|time|night|evening|weekend)|(?:a|one) of these days|a few (?:days|hours|weeks)|"
    r"(?:push|pushed|pushing|bump|bumped|bumping|move|moved|moving|shift|shifted) (?:it|this|that|things|\w+) (?:back|up|forward)|"
    r"(?:push|bump|move|shift) back|(?:push|bump|move) up)\b", re.I)
_NUM_WORDS = r"(?:two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
_EXPLICIT_WHEN = re.compile(
    # Clock times, amounts and dates ("7", "7:30pm", "45 min", "the 28th", "11/20"), but not a number inside a name ("her 60th").
    r"\b\d{1,2}(?::\d{2})?(?:\s*(?:am|pm|ish|h|hrs?|mins?|minutes|hours))?\b|\b(?:[12]?\d|3[01])(?:st|nd|rd|th)\b|\b\d{1,2}/\d{1,2}\b|\b\d{4}-\d{2}-\d{2}\b|"
    r"\b(?:noon|midday|midnight|today|tonight|tomorrow|tmrw|tmr|an? (?:hour|day|week)|" + _NUM_WORDS + r"|"
    r"half (?:past|an? hour|hour|one|" + _NUM_WORDS + r")|and a half|quarter (?:past|to|after|of)|"
    r"(?:at|by|around|till|until|from) one|one (?:o'?clock|thirty|fifteen|forty|pm|am|ish)|"
    r"sun|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sunday|monday|tuesday|wednesday|thursday|friday|saturday|"
    r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december)\b", re.I)


def vague_when(quote: str) -> str | None:
    """The words that leave a time open ("push it back a bit", "sometime next week"), when the quote names no time, day, date or
    amount at all. A model would have to invent the exact time, so the server asks for it to be asked instead. None when concrete."""
    said = (quote or "").replace("’", "'")
    hit = _VAGUE_WHEN.search(said)
    if not hit or _EXPLICIT_WHEN.search(said):
        return None
    return hit.group(0)


def parse_count(value: str) -> int | None:
    text = value.lower()
    digits = re.search(r"\b(\d{1,4})\b", text)
    if digits:
        return int(digits.group(1))
    total: int | None = None
    for w in re.split(r"\s+", text.replace("-", " ")):
        if w in NUMBER_WORDS:
            total = (total or 0) + NUMBER_WORDS[w]
        elif total is not None:
            break
    return total


_STOP = {"the", "a", "an", "at", "from", "of", "in", "on", "to", "for", "and", "with", "our"}


def canonical_text(value: str) -> str:
    text = re.sub(r"['’]s\b", "s", value.lower())
    text = re.sub(r"[^a-z0-9\s:]", " ", text)
    return " ".join(w for w in re.split(r"\s+", text) if w and w not in _STOP)


def canonical(kind: str, value: str) -> str:
    if kind == "datetime":
        p = parse_datetime(value)
        h = hour24(p)
        if p.weekday is None and p.day is None and h is None:
            return f"text:{canonical_text(value)}"
        time = "-" if h is None else f"{h:02d}:{(p.minute or 0):02d}"
        return "dt:" + "|".join("-" if v is None else str(v) for v in (p.weekday, p.month, p.day)) + f"|{time}"
    if kind == "count":
        n = parse_count(value)
        return f"text:{canonical_text(value)}" if n is None else f"n:{n}"
    return f"text:{canonical_text(value)}"


def same_value(kind: str, a: str, b: str) -> bool:
    """Two canonical datetimes agree if every component present in both is equal and they share at least one."""
    if a == b:
        return True
    if kind != "datetime" or not a.startswith("dt:") or not b.startswith("dt:"):
        return False
    pa, pb = a[3:].split("|"), b[3:].split("|")
    shared = 0
    for x, y in zip(pa, pb):
        if x == "-" or y == "-":
            continue
        if x != y:
            return False
        shared += 1

    def day_known(p: list[str]) -> bool:
        return p[0] != "-" or p[2] != "-"

    return shared > 0 and day_known(pa) and day_known(pb)


def slugify(value: str, max_len: int = 40) -> str:
    s = re.sub(r"['’]", "", value.lower())
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s[:max_len]


def normalize_slot_key(value: str) -> str:
    s = re.sub(r"['’]", "", value.strip().lower())
    s = re.sub(r"[^a-z0-9._]+", "_", s)
    s = re.sub(r"^[._]+|[._]+$", "", s)
    return s[:64]


def humanize(key: str) -> str:
    last = key.split(".")[-1].replace("_", " ")
    return last[:1].upper() + last[1:]


TOPIC_STOP = {
    "the", "and", "for", "are", "what", "whats", "what's", "who", "whos", "who's", "when", "where", "how", "our", "any",
    "about", "this", "that", "with", "have", "has", "doing", "going", "need", "tell", "show", "your", "was", "will",
    "can", "please", "all",
}


def stem_word(word: str) -> str:
    """Crude stemmer so "decorations" finds "Decorate" and "driving" finds "Drive"."""
    w = re.sub(r"['’]s$", "", word.lower())
    for suffix in ("ations", "ation", "ions", "ion", "ings", "ing", "ers", "er", "ed", "es", "s", "e"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 4:
            return w[: -len(suffix)]
    return w


def topic_stems(text: str, drop_stop_words: bool = True) -> list[str]:
    words = re.split(r"[^a-z0-9'’]+", text.lower())
    return [stem_word(w) for w in words if len(w) > 2 and (not drop_stop_words or w not in TOPIC_STOP)]


# ---------------------------------------------------------------- times, days, money

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_LONG_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def parse_time_of_day(text: str | int | None) -> int | None:
    """Minutes after midnight from "19:00", "7 pm", "7:30pm", "noon", "7" (bare 1-7 means evening). None if unreadable."""
    if text is None:
        return None
    if isinstance(text, int):
        return text
    t = str(text).strip().lower().replace(".", "")
    if not t:
        return None
    if t in ("noon", "midday"):
        return 12 * 60
    if t == "midnight":
        return 0
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t)
    if not m:
        m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", t) or re.search(r"\b(\d{1,2}):(\d{2})\b", t)
        if not m:
            return None
    hour = int(m.group(1))
    minute = int(m.group(2)) if m.lastindex and m.lastindex >= 2 and m.group(2) else 0
    mer = m.group(3) if m.lastindex and m.lastindex >= 3 else None
    if hour > 23 or minute > 59:
        return None
    p = DateTimeParts(hour=hour, minute=minute, meridiem=mer)
    h = hour24(p)
    return (h if h is not None else hour) * 60 + minute


def parse_day(text: str | int | None, start_date: str, days: int) -> int | None:
    """Day index within a plan from "Sat", "Saturday", "2026-11-07", "day 2" or an int. None if unreadable or outside the plan."""
    from datetime import date, timedelta

    if text is None or text == "":
        return None
    if isinstance(text, int):
        return text if 0 <= text < max(1, days) else None
    t = str(text).strip().lower()
    start = date.fromisoformat(start_date)
    m = re.fullmatch(r"day\s*(\d+)", t)
    if m:
        i = int(m.group(1)) - 1
        return i if 0 <= i < max(1, days) else None
    if re.fullmatch(r"\d+", t):
        i = int(t)
        return i if 0 <= i < max(1, days) else None
    try:
        d = date.fromisoformat(t[:10])
        i = (d - start).days
        return i if 0 <= i < max(1, days) else None
    except ValueError:
        pass
    for i in range(max(1, days)):
        d = start + timedelta(days=i)
        name = _LONG_DAYS[d.weekday()]
        if t == name or t[:3] == name[:3]:
            return i
    md = parse_datetime(t)
    if md.month and md.day:
        for i in range(max(1, days)):
            d = start + timedelta(days=i)
            if (d.month, d.day) == (md.month, md.day):
                return i
    return None


def day_label(start_date: str, index: int) -> str:
    from datetime import date, timedelta

    d = date.fromisoformat(start_date) + timedelta(days=index)
    return f"{DAY_NAMES[d.weekday()]}, {d.strftime('%b')} {d.day}"


def dates_text(start_date: str, days: int) -> str:
    if days <= 1:
        return day_label(start_date, 0)
    return f"{day_label(start_date, 0)} to {day_label(start_date, days - 1)}"


def money(minor: int, currency: str = "CAD") -> str:
    sign = "-" if minor < 0 else ""
    return f"{sign}{currency} {abs(minor) / 100:.2f}"


def parse_amount_minor(value: object) -> int | None:
    """45, "45", "$45.50", "CAD 45" -> minor units."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(round(float(value) * 100))
    m = re.search(r"(\d+(?:\.\d{1,2})?)", str(value))
    return int(round(float(m.group(1)) * 100)) if m else None
