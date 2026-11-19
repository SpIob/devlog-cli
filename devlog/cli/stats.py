"""devlog CLI — The ``stats`` command."""

from __future__ import annotations

import datetime
import json

import click

from devlog import (
    _dates,
    _filter,
    _iso,
    storage,
    ui,
)
from devlog.cli._shared import (
    _load_entries_or_exit,
    _resolve_tz_or_exit,
)
from devlog.models import Entry

# Console instances (ui re-exports) so command bodies read `console.print`
console = ui.console
err_console = ui.err_console


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


@click.option("--since", default=None, help="Only include entries on/after this date (UTC).")
@click.option("--until", default=None, help="Only include entries on/before this date (UTC).")
@click.option(
    "--preset",
    type=click.Choice(
        [
            "today",
            "yesterday",
            "last-7d",
            "last-30d",
            "last-week",
            "last-month",
            "this-week",
            "this-month",
            "this-year",
            "year-to-date",
        ],
        case_sensitive=False,
    ),
    default=None,
    help="Date-range preset (overrides --since/--until).",
)
@click.option("--quiet", "-q", is_flag=True, help="Output a single JSON summary.")
def stats(since: str | None, until: str | None, preset: str | None, quiet: bool) -> None:
    """Show a summary of the journal: total entries, date range, top tags, and a 30-day sparkline."""
    all_entries = _load_entries_or_exit()

    all_entries = _filter.filter_pipeline(
        all_entries, since=since, until=until, preset=preset, sort=False
    )
    # Also drop entries whose created_at cannot be parsed at all, so
    # downstream formatting (which assumes a valid ISO timestamp) never
    # crashes. Other commands go through this filter implicitly via
    # --since/--until; `stats` without date bounds does not, so we apply
    # it unconditionally here.
    #
    # Performance note: previously this filtered first and then the
    # per-day loop parsed ``created_at`` a second time inside
    # ``storage.local_date_for``. We now parse once here and reuse the
    # resulting date — saves one ``parse_utc_iso`` call per entry
    # (10k+ syscalls on large journals).
    tz = _resolve_tz_or_exit()
    valid_entries: list[tuple[Entry, datetime.date]] = []
    for entry in all_entries:
        dt = _iso.try_parse_utc_iso(entry.created_at)
        if dt is None:
            continue
        local_d = dt.astimezone(tz).date() if tz is not None else dt.date()
        valid_entries.append((entry, local_d))
    if not valid_entries:
        ui.print_info("No entries to summarize.")
        return

    # Total & date range
    total = len(valid_entries)
    # Sort by the parsed dt to find first/last; this avoids a second
    # lexical sort of the ISO strings (which would still be correct
    # but slower than sorting datetimes).
    sorted_by_date = sorted(valid_entries, key=lambda ed: ed[1])
    first_iso = sorted_by_date[0][0].created_at
    last_iso = sorted_by_date[-1][0].created_at

    # Per-day counts for the last 30 days. We already parsed each
    # timestamp above, so just reuse the date — no second parse.
    per_day: dict[str, int] = {}
    for _entry, local_d in valid_entries:
        key = local_d.strftime("%Y-%m-%d")
        per_day[key] = per_day.get(key, 0) + 1

    last_30_days: list[tuple[str, int]] = []
    ref_date = _dates.today_local_date(tz)
    for i in range(29, -1, -1):
        d = ref_date - datetime.timedelta(days=i)
        iso = d.strftime("%Y-%m-%d")
        last_30_days.append((iso, per_day.get(iso, 0)))

    # Top tags
    from collections import Counter

    tag_counter: Counter = Counter()
    for entry, _ in valid_entries:
        for tag in entry.tags:
            tag_counter[tag] += 1
    top_tags = tag_counter.most_common(5)

    # Stats v2: streaks, tag co-occurrence, days-since-last. All are
    # computed from the already-parsed local dates, so they add no
    # extra timestamp parsing on large journals.
    local_dates = [local_d for _e, local_d in valid_entries]
    streaks = storage.compute_streaks(local_dates)
    top_tag_pairs = storage.compute_tag_cooccurrence([e for e, _ in valid_entries])
    days_since = storage.compute_days_since_last(local_dates)

    if quiet:
        print(
            json.dumps(
                {
                    "total": total,
                    "first": first_iso,
                    "last": last_iso,
                    "top_tags": [{"tag": t, "count": c} for t, c in top_tags],
                    "last_30_days": [{"date": d, "count": c} for d, c in last_30_days],
                    "streaks": {"current": streaks[0], "longest": streaks[1]},
                    "top_tag_pairs": [
                        {"tags": list(pair), "count": c} for pair, c in top_tag_pairs
                    ],
                    "days_since_last": days_since,
                },
                ensure_ascii=False,
            )
        )
        return

    # Render
    panel = ui.stats_panel(
        total=total,
        first_iso=first_iso,
        last_iso=last_iso,
        top_tags=top_tags,
        last_30_days=last_30_days,
        streaks=streaks,
        top_tag_pairs=top_tag_pairs,
        days_since=days_since,
        tz=tz,
        tz_label=str(tz) if tz is not None else "UTC",
    )
    console.print(panel)


def register(main) -> None:
    """Attach the stats commands to *main*."""
    main.command()(stats)
