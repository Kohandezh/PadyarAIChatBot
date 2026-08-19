"""Timestamp coercion across the two storage engines.

SQLite returned every timestamp as TEXT; PostgreSQL returns a real
`datetime`, and for TIMESTAMPTZ columns an AWARE one. Code written against
SQLite calls `datetime.fromisoformat(row["expiry"])`, which raises
`TypeError: fromisoformat: argument must be str` the moment the value is
already a datetime — and that took out EVERY admin request after cutover.

`as_datetime()` accepts either shape. `compare_now()` returns a "now" whose
awareness matches the value being compared, because Python refuses to compare
an aware datetime with a naive one and the app mixes both:
`datetime.utcnow()` (naive) is still used throughout the OTP module.
"""
from datetime import datetime, timezone


def as_datetime(value):
    """A datetime from either a TEXT timestamp or a real datetime. None passes."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
    return None


def compare_now(reference):
    """`now` with the same awareness as `reference`, so comparison is legal."""
    if reference is not None and getattr(reference, "tzinfo", None) is not None:
        return datetime.now(timezone.utc)
    return datetime.now()


def to_naive_utc(value):
    """Aware -> naive UTC. For code that still compares against utcnow()."""
    dt = as_datetime(value)
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt
