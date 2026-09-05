"""Date and timezone parsing utilities.

Centralises all date/timestamp parsing, filtering, and timezone logic
that was previously in ``cli.py``.
"""

from __future__ import annotations

import datetime
import os
import re
import sys
from typing import TYPE_CHECKING

import click

from devlog import _iso
from devlog import storage
from devlog import ui
from devlog.models import Entry

if TYPE_CHECKING:
    import zoneinfo

# Supported --since / --until input forms. ``None`` means "no bound".
_RELATIVE_DAY_RE = re.compile(r"^(\d+)\s*d$")
_RELATIVE_WEEK_RE = re.compile(r"^(\d+)\s*w$")

# Relative-time input forms for `add --at` / `edit --at`. Minute/hour-only
# because seconds-level backfill is rare and would just be confusing.
_RELATIVE_HOUR_RE = re.compile(r"^(\d+)\s*h$")
_RELATIVE_MINUTE_RE = re.compile(r"^(\d+)\s*m$")


def _resolve_local_tz():
    """Return the local :class:`zoneinfo.ZoneInfo` from ``DEVLOG_TZ``, or ``None``.

    Behaviour:
        * Env var unset → return ``None`` (callers should fall back to UTC).
        * Env var set to a valid IANA name → return the ``ZoneInfo``.
        * Env var set to an invalid name → render a red error panel and
          exit with code 1. We deliberately fail loudly: a silent
          fallback to UTC after a typo would mask the real configuration
          problem.

    The function delegates the actual IANA-name → ``ZoneInfo``
    resolution to :func:`devlog.storage._resolve_zoneinfo` so the
    lazy import lives in one place. The ``tzdata`` package is a
    small dependency that provides IANA data on Windows and as a
    fallback elsewhere.
    """
    raw = os.environ.get("DEVLOG_TZ")
    if not raw:
        return None
    try:
        return storage._resolve_zoneinfo(raw)
    except ValueError as exc:
        ui.print_error(
            f'Invalid DEVLOG_TZ "{raw}": {exc}. '
            "Use an IANA name like America/New_York or Europe/Berlin."
        )
        sys.exit(1)


def _parse_date_bound(
    value: str,
    *,
    is_upper: bool = False,
    tz=None,
) -> datetime.datetime:
    """Parse a user-supplied date bound into a UTC ``datetime``.

    Accepted forms (case-insensitive, whitespace stripped):

        * ``YYYY-MM-DD``                 — date only, midnight UTC.
        * ``YYYY-MM-DDTHH:MM``           — ISO local form, treated as UTC.
        * ``YYYY-MM-DDTHH:MM:SS``        — ISO local form, treated as UTC.
        * ``YYYY-MM-DD HH:MM`` / ``...:SS`` — same, with a space separator.
        * ``YYYY-MM-DDTHH:MM:SSZ`` / with timezone — explicit UTC.
        * ``today``                      — today at midnight UTC.
        * ``yesterday``                  — yesterday at midnight UTC.
        * ``Nd`` / ``Nw``                — N days/weeks ago at midnight UTC
                                           (e.g. ``7d``).

    Args:
        value:    the raw string from the user.
        is_upper: when True, a date-only value is interpreted as the
                  *end* of that day (23:59:59) rather than midnight. This
                  lets ``--until 2025-01-15`` mean "include 2025-01-15".
        tz:       when supplied, ``YYYY-MM-DD``, ``Nd``, ``Nw``, ``today``,
                  and ``yesterday`` are interpreted at *local* midnight in
                  this zone, then converted to UTC for the comparison.
                  ``YYYY-MM-DDTHH:MM[:SS]`` with no offset is also
                  interpreted as local. Inputs that already carry an
                  explicit offset (``Z``, ``+HH:MM``) are honoured as-is.

    Returns:
        A timezone-aware ``datetime`` in UTC.

    Raises:
        click.BadParameter: on any unparseable input. The message lists
            the supported formats so users can self-correct.
    """
    if not value or not value.strip():
        raise click.BadParameter("date bound cannot be empty")

    raw = value.strip()

    # Natural phrases
    lower = raw.lower()
    if lower in ("today", "now"):
        now = datetime.datetime.now(tz=tz or datetime.timezone.utc)
        if is_upper and lower == "today":
            return now.replace(hour=23, minute=59, second=59, microsecond=0)
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if lower == "yesterday":
        ref = datetime.datetime.now(tz=tz or datetime.timezone.utc).date() - datetime.timedelta(days=1)
        dt = datetime.datetime(ref.year, ref.month, ref.day, tzinfo=tz or datetime.timezone.utc)
        if is_upper:
            return dt.replace(hour=23, minute=59, second=59)
        return dt

    # Relative: 7d, 2w
    m = _RELATIVE_DAY_RE.match(lower)
    if m:
        days = int(m.group(1))
        ref = datetime.datetime.now(tz=tz or datetime.timezone.utc).date() - datetime.timedelta(days=days)
        dt = datetime.datetime(ref.year, ref.month, ref.day, tzinfo=tz or datetime.timezone.utc)
        if is_upper:
            return dt.replace(hour=23, minute=59, second=59)
        return dt
    m = _RELATIVE_WEEK_RE.match(lower)
    if m:
        weeks = int(m.group(1))
        ref = (
            datetime.datetime.now(tz=tz or datetime.timezone.utc).date()
            - datetime.timedelta(weeks=weeks)
        )
        dt = datetime.datetime(ref.year, ref.month, ref.day, tzinfo=tz or datetime.timezone.utc)
        if is_upper:
            return dt.replace(hour=23, minute=59, second=59)
        return dt

    # Date only: YYYY-MM-DD
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        dt = datetime.datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=tz or datetime.timezone.utc)
        if is_upper:
            return dt.replace(hour=23, minute=59, second=59)
        return dt

    # Full ISO with optional Z / +00:00 / +HH:MM
    parseable = raw.replace(" ", "T")
    if parseable.endswith("Z"):
        parseable = parseable[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(parseable)
    except ValueError:
        raise click.BadParameter(
            f'Invalid date "{value}". Supported formats: '
            '"YYYY-MM-DD", "YYYY-MM-DDTHH:MM", "YYYY-MM-DDTHH:MM:SSZ", '
            '"today", "yesterday", "Nd" (N days ago), "Nw" (N weeks ago).'
        )
    if dt.tzinfo is None:
        # Naive timestamp — honour the local zone if one is active,
        # otherwise treat as UTC (the historical default).
        dt = dt.replace(tzinfo=tz or datetime.timezone.utc)
    else:
        dt = dt.astimezone(datetime.timezone.utc)
    return dt


def _parse_timestamp(value: str, *, tz=None) -> datetime.datetime:
    """Parse a user-supplied timestamp for ``--at`` on ``add``/``edit``.

    Unlike :func:`_parse_date_bound`, this is a *point in time*, not a
    date bound. Accepted forms:

        * ``YYYY-MM-DD``                  — local midnight (00:00:00).
        * ``YYYY-MM-DDTHH:MM``            — local-tz interpretation.
        * ``YYYY-MM-DDTHH:MM:SS``         — local-tz interpretation.
        * ``YYYY-MM-DD HH:MM[:SS]``       — same, with space separator.
        * ``YYYY-MM-DDTHH:MM:SSZ`` / with offset — explicit (no
                                            local-tz reinterpretation).
        * ``Nh``                          — N hours ago, relative to now.
        * ``Nm``                          — N minutes ago, relative to now.
        * ``Nd``                          — N days ago at local midnight.
        * ``Nw``                          — N weeks ago at local midnight.

    Args:
        value: the raw string from the user.
        tz:    when supplied, naive (no-offset) inputs are interpreted
               in this zone, then converted to UTC. When ``None``,
               naive inputs are treated as UTC.

    Returns:
        A timezone-aware ``datetime`` in UTC.

    Raises:
        click.BadParameter: on any unparseable input. The error message
            lists the supported formats.
    """
    if not value or not value.strip():
        raise click.BadParameter("--at cannot be empty")

    raw = value.strip()
    lower = raw.lower()
    zone = tz or datetime.timezone.utc

    # Relative forms: 2h, 30m, 7d, 1w
    m = _RELATIVE_HOUR_RE.match(lower)
    if m:
        hours = int(m.group(1))
        return datetime.datetime.now(tz=zone) - datetime.timedelta(hours=hours)
    m = _RELATIVE_MINUTE_RE.match(lower)
    if m:
        minutes = int(m.group(1))
        return datetime.datetime.now(tz=zone) - datetime.timedelta(minutes=minutes)
    m = _RELATIVE_DAY_RE.match(lower)
    if m:
        days = int(m.group(1))
        ref = datetime.datetime.now(tz=zone).date() - datetime.timedelta(days=days)
        return datetime.datetime(
            ref.year, ref.month, ref.day, tzinfo=zone
        ).astimezone(datetime.timezone.utc)
    m = _RELATIVE_WEEK_RE.match(lower)
    if m:
        weeks = int(m.group(1))
        ref = (
            datetime.datetime.now(tz=zone).date()
            - datetime.timedelta(weeks=weeks)
        )
        return datetime.datetime(
            ref.year, ref.month, ref.day, tzinfo=zone
        ).astimezone(datetime.timezone.utc)

    # Date-only: YYYY-MM-DD → local midnight, then convert to UTC so
    # callers always get a UTC datetime.
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        dt = datetime.datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=zone)
        return dt.astimezone(datetime.timezone.utc)

    # Full ISO with optional Z / +00:00 / +HH:MM
    parseable = raw.replace(" ", "T")
    if parseable.endswith("Z"):
        parseable = parseable[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(parseable)
    except ValueError as exc:
        raise click.BadParameter(
            f'Invalid --at "{value}". Supported formats: '
            '"YYYY-MM-DD", "YYYY-MM-DDTHH:MM", "YYYY-MM-DDTHH:MM:SSZ", '
            '"YYYY-MM-DD HH:MM[:SS]", '
            '"Nh" (N hours ago), "Nm" (N minutes ago), '
            '"Nd" (N days ago), "Nw" (N weeks ago).'
        ) from exc
    if dt.tzinfo is None:
        # Naive timestamp — honour the local zone if one is active,
        # otherwise treat as UTC (the historical default). Convert
        # to UTC for storage so the contract — "returns a UTC
        # datetime" — holds regardless of the active zone.
        dt = dt.replace(tzinfo=zone)
    return dt.astimezone(datetime.timezone.utc)


def _filter_by_date(
    entries: list[Entry],
    since: datetime.datetime | None,
    until: datetime.datetime | None,
) -> list[Entry]:
    """Return entries with ``created_at`` in ``[since, until]``.

    Both bounds are inclusive. ``None`` means "no bound on that side".
    Entries whose ``created_at`` is unparseable are dropped when either
    bound is supplied, since a date filter is meaningless for them.

    Args:
        entries: full list of Entry objects.
        since:   lower bound (inclusive) or None.
        until:   upper bound (inclusive) or None.

    Returns:
        list[Entry]: filtered, in original order.
    """
    if since is None and until is None:
        return entries

    def _within(entry: Entry) -> bool:
        ts = _iso.try_parse_utc_iso(entry.created_at)
        if ts is None:
            return False
        if since is not None and ts < since:
            return False
        if until is not None and ts > until:
            return False
        return True

    return [e for e in entries if _within(e)]


def _parse_since_until(
    since: str | None, until: str | None
) -> tuple[datetime.datetime | None, datetime.datetime | None]:
    """Parse a ``(since, until)`` CLI pair into UTC datetimes.

    Both bounds default to ``None`` (no bound). The local-zone handling
    mirrors :func:`_parse_date_bound` — when ``DEVLOG_TZ`` is set, naive
    date inputs are interpreted in that zone.
    """
    tz = _resolve_local_tz()
    since_dt = _parse_date_bound(since, tz=tz) if since else None
    until_dt = _parse_date_bound(until, is_upper=True, tz=tz) if until else None
    return since_dt, until_dt


def _filter_by_local_window(
    entries: list[Entry],
    end_date: "datetime.date",
    days: int,
    tz,
) -> list[Entry]:
    """Return entries whose *local* date falls in ``[end_date - days, end_date]``.

    Centralises the tz-vs-UTC fork that ``today`` / ``yesterday`` /
    ``week`` previously inlined three times. When ``tz`` is set, the
    local-date bucketing uses ``storage.local_date_for``; otherwise it
    falls back to the UTC date derived from ``created_at``.
    """
    import datetime as _dt

    start_date = end_date - _dt.timedelta(days=days)
    out = []
    for entry in entries:
        if tz is not None:
            local_d = storage.local_date_for(entry.created_at, tz)
        else:
            # UTC fallback — preserve the same epoch-on-failure contract
            # as ``storage.local_date_for`` so unreadable timestamps
            # silently land in 1970-01-01 (and thus never match a recent
            # window) rather than crashing the command.
            local_d = _iso.utc_date_from_iso(entry.created_at)
        if start_date <= local_d <= end_date:
            out.append(entry)
    return out


def today_local_date(tz) -> datetime.date:
    """Return today's date in *tz*, or UTC when *tz* is ``None``.

    Centralises the ``if tz is not None: ... else: ...`` fork that
    ``today`` / ``yesterday`` / ``week`` / ``calendar`` / ``stats``
    previously inlined five times.
    """
    effective = tz or datetime.timezone.utc
    return datetime.datetime.now(tz=effective).date()