from datetime import date, timedelta

from .models import RecurrenceSpec


def occurrences(rule: RecurrenceSpec, start: date, end: date) -> list[date]:
    """Inclusive window. COUNT includes excluded dates; identities never move.

    DTSTART is local-date based. DST conversion happens independently for each
    occurrence in the scheduler; no repeated addition of 24 UTC hours.
    """
    out, count = [], 0
    day = rule.dtstart
    if (end - day).days > 3660:
        raise ValueError("recurrence expansion is limited to ten years from DTSTART")
    weekdays = rule.byday or [rule.dtstart.weekday()]
    week0 = rule.dtstart - timedelta(days=rule.dtstart.weekday())
    while day <= end and (rule.until is None or day <= rule.until):
        days = (day - rule.dtstart).days
        week = (day - week0).days // 7
        match = (days % rule.interval == 0 and (not rule.byday or day.weekday() in weekdays)
                 if rule.frequency == "DAILY" else week % rule.interval == 0 and day.weekday() in weekdays)
        if match:
            count += 1
            if rule.count and count > rule.count:
                break
            if day >= start and day not in rule.exdates:
                out.append(day)
        day += timedelta(days=1)
    return out
