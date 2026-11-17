"""Shared filter chain for commands that take tag and date bounds.

Centralises the 4-step pipeline (tag filter -> date bound parse -> date
filter -> sort) that ``list``, ``search``, ``tail``, ``stats``, and
``export`` previously inlined. Returns a new list; the input is left
untouched.
"""

from __future__ import annotations

from devlog import _dates, _tagops
from devlog.models import BY_CREATED_AT, Entry


def filter_pipeline(
    entries: list[Entry],
    *,
    tags: tuple[str, ...] = (),
    since: str | None = None,
    until: str | None = None,
    preset: str | None = None,
    sort: bool = True,
) -> list[Entry]:
    """Apply tag filter, date filter, and (optionally) sort by created_at.

    Args:
        entries: source list (unchanged).
        tags:    Tag query strings (supports AND, OR via comma, NOT via !).
        since:   ISO date / ``today`` / ``Nd`` / ``Nw`` -- inclusive lower.
        until:   ISO date / ``today`` / ``Nd`` / ``Nw`` -- inclusive upper.
        preset:  Date-range preset (e.g. ``last-week``, ``last-month``).
                 Overrides ``since``/``until`` if provided.
        sort:    when True (the default), sort the result by
                 ``created_at`` descending. ``stats`` and ``export`` set
                 this to False to keep the natural input order.

    Returns:
        A new list of entries, post-filter (and post-sort if requested).
    """
    # Resolve preset if provided
    if preset:
        since_dt, until_dt = _dates.resolve_preset(preset)
        if since_dt is not None:
            since = since_dt.isoformat().replace("+00:00", "Z")
        if until_dt is not None:
            until = until_dt.isoformat().replace("+00:00", "Z")

    # Parse tag query (supports AND, OR, NOT)
    tag_query = _tagops.parse_tag_query(tags)
    filtered = _tagops._filter_by_tag_query(entries, tag_query)

    since_dt, until_dt = _dates._parse_since_until(since, until)
    filtered = _dates._filter_by_date(filtered, since_dt, until_dt)
    if sort:
        filtered.sort(key=BY_CREATED_AT, reverse=True)
    return filtered
