"""Shared filter chain for commands that take tag and date bounds.

Centralises the 4-step pipeline (tag filter → date bound parse → date
filter → sort) that ``list``, ``search``, ``tail``, ``stats``, and
``export`` previously inlined. Returns a new list; the input is left
untouched.
"""

from __future__ import annotations

from devlog import _dates
from devlog import _tagops
from devlog.models import BY_CREATED_AT, Entry


def filter_pipeline(
    entries: list[Entry],
    *,
    tags: tuple[str, ...] = (),
    since: str | None = None,
    until: str | None = None,
    sort: bool = True,
) -> list[Entry]:
    """Apply tag filter, date filter, and (optionally) sort by created_at.

    Args:
        entries: source list (unchanged).
        tags:    AND-filter: keep only entries carrying every tag.
        since:   ISO date / ``today`` / ``Nd`` / ``Nw`` — inclusive lower.
        until:   ISO date / ``today`` / ``Nd`` / ``Nw`` — inclusive upper.
        sort:    when True (the default), sort the result by
                 ``created_at`` descending. ``stats`` and ``export`` set
                 this to False to keep the natural input order.

    Returns:
        A new list of entries, post-filter (and post-sort if requested).
    """
    filtered = _tagops._filter_by_tags(entries, tags)
    since_dt, until_dt = _dates._parse_since_until(since, until)
    filtered = _dates._filter_by_date(filtered, since_dt, until_dt)
    if sort:
        filtered.sort(key=BY_CREATED_AT, reverse=True)
    return filtered
