"""One strict conversion for entry, search, consent and calendar publication."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from groupflow.core.errors import DomainError


def local_instant(day: str, minute: int, zone: str, fold: int | None = None) -> datetime:
    try:
        if fold is not None and (type(fold) is not int or fold not in (0, 1)):
            raise ValueError('fold must be 0 or 1')
        tz = ZoneInfo(zone)
        naive = datetime.combine(date.fromisoformat(day), time()) + timedelta(minutes=minute)
        first = naive.replace(tzinfo=tz, fold=0)
        second = naive.replace(tzinfo=tz, fold=1)
        valid = [d for d in (first, second) if d.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) == naive]
        if not valid:
            raise DomainError('INVALID_EVENT_TIME', 'This local time does not exist because the clock moves forward. Choose another time.')
        if first.utcoffset() != second.utcoffset() and fold is None:
            raise DomainError('INVALID_EVENT_TIME', 'This local time occurs twice. Set start_fold/end_fold to 0 (first) or 1 (second).')
        return naive.replace(tzinfo=tz, fold=fold or 0).astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError, ZoneInfoNotFoundError):
        raise DomainError('INVALID_EVENT_TIME', 'Use a valid date, timezone and fold (0 or 1).') from None


def validate_interval(day: str, start: int, end: int, zone: str, start_fold: int | None = None, end_fold: int | None = None):
    lo = local_instant(day, start, zone, start_fold)
    hi = local_instant(day, end, zone, end_fold)
    if hi <= lo:
        raise DomainError('INVALID_EVENT_TIME', 'The end must follow the start in actual time.')
    return lo, hi
