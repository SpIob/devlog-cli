"""devlog CLI — The date-bucket commands: ``today``, ``yesterday``, ``week``,
``on``, ``calendar`` and ``tail``.
"""

from __future__ import annotations

import datetime
import json
import sys

import click

from devlog import (
    _dates,
    _filter,
    storage,
    ui,
)
from devlog.cli._shared import (
    _BY_CREATED_AT,
    _load_entries_or_exit,
    _print_jsonl,
    _require_positive_int,
    _resolve_tz_or_exit,
)

# Console instances (ui re-exports) so command bodies read `console.print`
console = ui.console
err_console = ui.err_console


# ---------------------------------------------------------------------------
# today
# ---------------------------------------------------------------------------

@click.option(
    "--limit", "-n", type=int, default=50, show_default=True, help="Max entries to show."
)
@click.option("--quiet", "-q", is_flag=True, help="Output raw JSON lines.")
def today(limit: int, quiet: bool) -> None:
    """Show entries created today, newest first.

    The "today" bucket is the local date in the ``DEVLOG_TZ`` zone when
    set, otherwise UTC. This matches how the rest of the CLI handles
    the env var.
    """
    _require_positive_int("--limit", limit)

    all_entries = _load_entries_or_exit()

    tz = _resolve_tz_or_exit()
    local_today = _dates.today_local_date(tz)
    today_entries = _dates._filter_by_local_window(all_entries, end_date=local_today, days=0, tz=tz)
    subtitle = local_today.strftime("%Y-%m-%d")

    today_entries.sort(key=_BY_CREATED_AT, reverse=True)

    total = len(today_entries)
    shown = today_entries[:limit]

    if quiet:
        _print_jsonl(shown)
        return

    if total == 0:
        ui.print_info("No entries yet today.")
        return

    title = f"Today · {subtitle} · {total} {ui.plural_noun(total, 'entry')}"
    table = ui.entries_table(shown, total, title=title)
    console.print(table)


# ---------------------------------------------------------------------------
# yesterday
# ---------------------------------------------------------------------------

@click.option(
    "--limit", "-n", type=int, default=50, show_default=True, help="Max entries to show."
)
@click.option("--quiet", "-q", is_flag=True, help="Output raw JSON lines.")
def yesterday(limit: int, quiet: bool) -> None:
    """Show entries created yesterday, newest first.

    Like ``today``, the bucket is computed in the ``DEVLOG_TZ`` zone
    when set, otherwise UTC.
    """
    _require_positive_int("--limit", limit)

    all_entries = _load_entries_or_exit()

    tz = _resolve_tz_or_exit()
    local_today = _dates.today_local_date(tz)
    local_yesterday = local_today - datetime.timedelta(days=1)
    yesterday_entries = _dates._filter_by_local_window(
        all_entries, end_date=local_yesterday, days=0, tz=tz
    )
    subtitle = local_yesterday.strftime("%Y-%m-%d")

    yesterday_entries.sort(key=_BY_CREATED_AT, reverse=True)
    total = len(yesterday_entries)
    shown = yesterday_entries[:limit]

    if quiet:
        _print_jsonl(shown)
        return

    if total == 0:
        ui.print_info("No entries yet yesterday.")
        return

    title = f"Yesterday · {subtitle} · {total} {ui.plural_noun(total, 'entry')}"
    table = ui.entries_table(shown, total, title=title)
    console.print(table)


# ---------------------------------------------------------------------------
# week
# ---------------------------------------------------------------------------

@click.option(
    "--limit", "-n", type=int, default=100, show_default=True, help="Max entries to show."
)
@click.option(
    "--quiet", "-q", is_flag=True, help="Output raw JSON lines."
)
@click.option(
    "--day",
    "anchor",
    default=None,
    help=(
        "Anchor day (YYYY-MM-DD). The week ends on this day (inclusive) and "
        "spans the 6 days before it. Defaults to today in the local zone "
        "(or UTC)."
    ),
)
def week(limit: int, quiet: bool, anchor: str | None) -> None:
    """Show entries from the last 7 days, newest first."""
    _require_positive_int("--limit", limit)

    all_entries = _load_entries_or_exit()

    tz = _resolve_tz_or_exit()
    if anchor:
        # Parse the anchor in the local zone when possible, then convert
        # to UTC. Falls back to UTC if no tz is set, mirroring
        # `_parse_date_bound` behaviour.
        try:
            anchor_dt = _dates._parse_date_bound(anchor, tz=tz)
        except click.BadParameter as exc:
            ui.print_error(str(exc))
            sys.exit(2)
        end_date = anchor_dt.astimezone(tz).date() if tz is not None else anchor_dt.date()
    else:
        end_date = _dates.today_local_date(tz)

    week_entries = _dates._filter_by_local_window(all_entries, end_date=end_date, days=6, tz=tz)
    start_date = end_date - datetime.timedelta(days=6)
    week_entries.sort(key=_BY_CREATED_AT, reverse=True)

    total = len(week_entries)
    shown = week_entries[:limit]

    if quiet:
        _print_jsonl(shown)
        return

    if total == 0:
        ui.print_info(
            f"No entries in the last 7 days ({start_date} to {end_date})."
        )
        return

    title = (
        f"Week · {start_date} → {end_date} · {total} "
        f"{ui.plural_noun(total, 'entry')}"
    )
    table = ui.entries_table(shown, total, title=title)
    console.print(table)


# ---------------------------------------------------------------------------
# on
# ---------------------------------------------------------------------------

@click.argument("date", required=True)
@click.option(
    "--limit", "-n", type=int, default=50, show_default=True, help="Max entries to show."
)
@click.option("--quiet", "-q", is_flag=True, help="Output raw JSON lines.")
def on(date: str, limit: int, quiet: bool) -> None:
    """Show entries for a specific date.

    DATE can be:
    - "today" / "yesterday" (local zone when DEVLOG_TZ is set)
    - YYYY-MM-DD (absolute date)
    - Nd (relative days ago, e.g. "3d")
    
    The bucket is computed in the DEVLOG_TZ zone when set, otherwise UTC.
    """
    _require_positive_int("--limit", limit)

    all_entries = _load_entries_or_exit()

    tz = _resolve_tz_or_exit()
    target_date = _dates._parse_on_date(date, tz=tz)
    
    if target_date is None:
        ui.print_error(f"Invalid date: {date}")
        sys.exit(2)

    on_entries = _dates._filter_by_local_window(all_entries, end_date=target_date, days=0, tz=tz)
    subtitle = target_date.strftime("%Y-%m-%d")

    on_entries.sort(key=_BY_CREATED_AT, reverse=True)
    total = len(on_entries)
    shown = on_entries[:limit]

    if quiet:
        _print_jsonl(shown)
        return

    if total == 0:
        ui.print_info(f"No entries on {subtitle}.")
        return

    title = f"On · {subtitle} · {total} {ui.plural_noun(total, 'entry')}"
    table = ui.entries_table(shown, total, title=title)
    console.print(table)


# ---------------------------------------------------------------------------
# calendar
# ---------------------------------------------------------------------------

@click.option(
    "--year",
    "year",
    type=int,
    default=None,
    help="Year to render. Defaults to the current local year (or UTC).",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Output a {YYYY-MM-DD: count} JSON map for the year.",
)
def calendar(year: int | None, quiet: bool) -> None:
    """Show a year-grid heatmap of journal activity."""
    tz = _resolve_tz_or_exit()
    if year is None:
        year = _dates.today_local_date(tz).year

    all_entries = _load_entries_or_exit()

    per_day: dict[str, int] = {}
    for entry in all_entries:
        local_d = storage.local_date_for(entry.created_at, tz)
        if local_d.year == year:
            key = local_d.strftime("%Y-%m-%d")
            per_day[key] = per_day.get(key, 0) + 1

    if quiet:
        print(json.dumps(per_day, ensure_ascii=False, sort_keys=True))
        return

    if not per_day:
        ui.print_info(f"No entries in {year}.")
        return

    panel = ui.calendar_panel(per_day, year=year, tz=tz)
    console.print(panel)


# ---------------------------------------------------------------------------
# tail
# ---------------------------------------------------------------------------

@click.argument("n", required=False, default=5, type=int)
@click.option("--tag", "-t", "tags", multiple=True, help="Filter by tag (AND).")
@click.option("--quiet", "-q", is_flag=True, help="Output raw JSON lines.")
def tail(n: int, tags: tuple[str, ...], quiet: bool) -> None:
    """Show the N most recent entries (default: 5)."""
    _require_positive_int("N", n)

    all_entries = _load_entries_or_exit()

    filtered = _filter.filter_pipeline(all_entries, tags=tags)
    shown = filtered[:n]
    total = len(filtered)

    if quiet:
        _print_jsonl(shown)
        return

    if total == 0:
        ui.print_info("No entries found.")
        return

    title = f"Tail · last {len(shown)} of {total}"
    table = ui.entries_table(shown, total, title=title)
    console.print(table)


def register(main) -> None:
    """Attach the views commands to *main*."""
    main.command()(today)
    main.command()(yesterday)
    main.command()(week)
    main.command()(on)
    main.command()(calendar)
    main.command()(tail)
