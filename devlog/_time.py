"""Timezone and timestamp utilities — no dependencies on other devlog modules.

This module is a leaf in the dependency graph; _base, _tagops, and _dates
can import from it without creating cycles.
"""

from __future__ import annotations

import datetime
from datetime import date as _date
from datetime import timezone

from devlog import _iso


def utc_now_iso() -> str:
    """Return the current UTC time as a ``YYYY-MM-DDTHH:MM:SSZ`` string."""
    return datetime.datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_zoneinfo(tz_name: str):
    """Resolve an IANA tz name to a ``ZoneInfo``, or raise ``ValueError``."""
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    except ImportError as exc:  # pragma: no cover - 3.9+ always has it
        raise ValueError("zoneinfo is not available on this Python build") from exc
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(str(exc)) from exc


def local_date_for(iso: str, tz) -> _date:
    """Return the local date for a stored UTC ISO 8601 timestamp."""
    if not iso:
        return _date(1970, 1, 1)
    try:
        dt = _iso.parse_utc_iso(iso)
    except (ValueError, TypeError, AttributeError):
        return _date(1970, 1, 1)
    if tz is not None:
        dt = dt.astimezone(tz)
    return dt.date()
