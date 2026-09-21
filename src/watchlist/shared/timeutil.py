"""Timestamp formatting shared by every writer and reader of effective_at."""

import datetime as dt


def to_iso(value: dt.datetime) -> str:
    """UTC, always six fractional digits, trailing Z.

    The Redis guard compares these as strings. `isoformat()` alone drops the
    fractional part when microsecond == 0, and "...20Z" sorts *after*
    "...20.999999Z" because "Z" > "." - so a timestamp at exactly .000000 would
    beat a newer one from the same second. Fixed width makes lexical order
    equal chronological order.
    """
    return value.astimezone(dt.UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def now_iso() -> str:
    return to_iso(dt.datetime.now(dt.UTC))
