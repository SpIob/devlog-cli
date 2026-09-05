"""Shared ISO 8601 timestamp parsing utilities.

Centralises the ``s.replace("Z", "+00:00")`` + ``fromisoformat`` pattern
used across multiple modules.
"""

from __future__ import annotations

import datetime


def parse_utc_iso(value: str) -> datetime.datetime:
    """Parse a UTC ISO 8601 timestamp string into a timezone-aware datetime.

    Accepts formats with ``Z`` suffix or ``+00:00`` offset.
    Raises ``ValueError`` on unparseable input.

    Args:
        value: ISO 8601 string (e.g. ``"2025-01-15T10:30:00Z"`` or
            ``"2025-01-15T10:30:00+00:00"``).

    Returns:
        Timezone-aware ``datetime`` in UTC.
    """
    parseable = value.replace(" ", "T")
    if parseable.endswith("Z"):
        parseable = parseable[:-1] + "+00:00"
    dt = datetime.datetime.fromisoformat(parseable)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    else:
        dt = dt.astimezone(datetime.timezone.utc)
    return dt


def try_parse_utc_iso(value: str) -> datetime.datetime | None:
    """Parse a UTC ISO 8601 timestamp, returning ``None`` on failure.

    Unlike :func:`parse_utc_iso`, this never raises — it returns ``None``
    for any unparseable input.

    Args:
        value: ISO 8601 string to parse.

    Returns:
        Timezone-aware ``datetime`` in UTC, or ``None`` if parsing fails.
    """
    try:
        return parse_utc_iso(value)
    except (ValueError, TypeError, AttributeError):
        return None


def utc_date_from_iso(value: str) -> datetime.date:
    """Extract the UTC date component from an ISO 8601 timestamp string.

    Returns ``datetime.date(1970, 1, 1)`` on unparseable input (epoch
    fallback), matching the behaviour of :func:`storage.local_date_for`
    when no timezone is available.

    Args:
        value: ISO 8601 string.

    Returns:
        The UTC date, or ``1970-01-01`` if parsing fails.
    """
    dt = try_parse_utc_iso(value)
    if dt is None:
        return datetime.date(1970, 1, 1)
    return dt.date()


def iso_to_epoch(value: str) -> int:
    """Convert a UTC ISO 8601 string to a POSIX epoch integer.

    Raises ``ValueError`` on unparseable input.

    Args:
        value: ISO 8601 string.

    Returns:
        Seconds since the Unix epoch (UTC).
    """
    dt = parse_utc_iso(value)
    return int(dt.timestamp())


def safe_epoch(value: str) -> int:
    """Convert a UTC ISO 8601 string to a POSIX epoch, returning 0 on failure.

    Used by sort keys where a single unparseable timestamp must not
    crash the whole command — instead it lands at the bottom of the
    ordering (epoch 0 = 1970-01-01).
    """
    dt = try_parse_utc_iso(value)
    if dt is None:
        return 0
    return int(dt.timestamp())


def is_valid_iso_timestamp(value: str) -> bool:
    """Check if a string is a valid UTC ISO 8601 timestamp.

    This is a cheap boolean check that mirrors :func:`parse_utc_iso`
    but never raises.

    Args:
        value: string to validate.

    Returns:
        ``True`` if valid, ``False`` otherwise.
    """
    return try_parse_utc_iso(value) is not None