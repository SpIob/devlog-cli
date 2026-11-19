"""devlog.ui.panels — The Rich ``Panel`` renderers for a single entry and for the
summary commands (stats, repair, doctor, backup).
"""

from __future__ import annotations

from rich.console import Group
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text

from devlog import _iso
from devlog.models import Entry
from devlog.ui._base import (
    ID_DISPLAY_LEN,
    TAG_NONE,
    _bold,
    _s,
)
from devlog.ui.calendar import (
    _HEATMAP_TIERS,
    calendar_grid,
    sparkline,
)
from devlog.ui.text import (
    _styled_row,
    _tags_text,
    format_dt,
    plural_noun,
    plural_s,
    success_line,
)


def _panel_title(
    *,
    icon: str | None,
    label: str,
    short_id: str = "",
    role: str = "success_border",
) -> Text:
    """Build a ``<icon> <label>  ·  <short_id>`` panel title.

    Centralises the 3-line ``Text.append().append().append()`` chain
    that the four entry-detail panels (``entry``, ``show``, ``delete``,
    ``edit``) and the report panels (``repair``, ``doctor``,
    ``calendar``) all duplicated.

    Args:
        icon: leading icon character (e.g. ``"✔"`` or ``"✘"``). Pass
            ``None`` for iconless panels (e.g. ``show``).
        label: the panel title text.
        short_id: the 8-char short id to append dim after a separator.
            Empty string suppresses the separator entirely.
        role: theme role for the icon and label colour.

    Returns:
        A :class:`rich.text.Text` ready to drop into ``Panel(title=...)``.
    """
    t = Text()
    if icon:
        t.append(f"{icon} ", style=_bold(role))
    t.append(label, style=_bold(role))
    if short_id:
        t.append(f"  ·  {short_id}", style="dim")
    return t


def _themed_panel(
    body,
    *,
    border_role: str,
    title=None,
    padding: tuple[int, int] | None = (0, 1),
    footer_hint: str | None = None,
) -> Panel:
    """Build a themed :class:`rich.panel.Panel` with shared defaults.

    All 8 panel builders in this module share the same ``title_align``
    and (where applicable) ``title`` placement. This helper centralises
    the boilerplate.

    Args:
        body: the renderable body (Group, Text, Padding, etc.).
        border_role: theme role whose style becomes the border colour.
        title: optional panel title (Text or str).
        padding: Rich padding tuple, or ``None`` to omit the padding
            (used by calendar/stats which already wrap the body in
            :class:`rich.padding.Padding`).
        footer_hint: optional dim italic hint line appended to the
            body (e.g. ``"Use `devlog list` to verify"``). The hint is
            appended to *body* if it's a :class:`rich.console.Group`;
            for other body types the caller must already include the
            hint. Pass ``""`` to explicitly opt out.
    """
    kwargs = {
        "border_style": _s(border_role),
        "title": title,
        "title_align": "left",
    }
    if padding is not None:
        kwargs["padding"] = padding
    if footer_hint:
        if isinstance(body, Group):
            body = Group(*body.renderables, Text(footer_hint, style="dim italic"))
    return Panel(body, **kwargs)


def _capped_enumerate(
    items: list,
    cap: int,
    item_formatter,
    tail_prefix: str = "…and {remaining} more",
) -> list[Text]:
    """Enumerate up to ``cap`` items with a tail if there are more.

    Args:
        items: the list of items to enumerate.
        cap: maximum items to show inline.
        item_formatter: callable(item) -> Text for each shown item.
        tail_prefix: format string for the tail; receives ``remaining``.

    Returns:
        A list of Text objects ready to append to a renderable list.
    """
    out = []
    for item in items[:cap]:
        out.append(item_formatter(item))
    if len(items) > cap:
        out.append(Text(tail_prefix.format(remaining=len(items) - cap), style="dim"))
    return out


def _updated_text(entry: Entry) -> Text:
    """Build a styled "updated at" line for entry-detail panels.

    Returns a dim "—" when ``updated_at`` is absent (so the layout
    stays consistent across freshly-added and edited entries).
    """
    if entry.updated_at:
        return Text(format_dt(entry.updated_at), style=_s("updated"))
    return Text("—", style="dim")


def _date_text(entry: Entry) -> Text:
    """Build a styled "created at" line for entry-detail panels."""
    return Text(format_dt(entry.created_at), style=_s("date"))


def entry_panel(
    entry: Entry,
    *,
    title: str = "Entry added",
    title_icon: str = "✔",
    title_style: str | None = None,
    border_style: str | None = None,
    show_full_id: bool = False,
    footer_hint: str | None = None,
) -> Panel:
    """Render a panel for an entry.

    Used by both ``add`` (confirmation, with green check icon) and
    ``show`` (entry detail, with neutral title). The full message is
    always rendered — never truncated — so ``show`` can display
    arbitrarily long entries.

    Args:
        entry:        the entry to render.
        title:        the panel title (the part after the icon).
        title_icon:   leading icon character (e.g. ``"✔"`` or ``"📄"``).
        title_style:  Rich style for the title. Defaults to the
            ``success_title`` theme role.
        border_style: Rich style for the panel border. Defaults to the
            ``success_border`` theme role.
        show_full_id: when True, render the full UUID as a row.
        footer_hint:  dim italic footer line. When ``None`` (the
            default), a hint that mentions the entry's short id is
            generated automatically; pass an empty string to suppress
            the footer entirely.

    Returns:
        A configured ``rich.panel.Panel`` ready to print.
    """
    if title_style is None:
        title_style = _s("success_title")
    if border_style is None:
        border_style = _s("success_border")
    short_id = entry.id[:ID_DISPLAY_LEN]
    tags_display = (
        Text(", ".join(entry.tags), style=_s("tags")) if entry.tags else Text(TAG_NONE, style="dim")
    )
    date_text = Text(format_dt(entry.created_at), style=_s("date"))

    rows = [
        _styled_row("Date", date_text),
        _styled_row("Tags", tags_display),
    ]
    if show_full_id:
        rows.append(_styled_row("ID", Text(entry.id, style=_s("id_dim"))))
    rows.append(_styled_row("Note", Text(entry.message)))
    rows.append(Text())
    # Resolve the default footer hint lazily so callers can pass either
    # a custom string (preserved verbatim) or ``None`` to get an
    # id-aware hint, or an empty string to suppress the line.
    if footer_hint is None:
        footer_hint = (
            f"Use `devlog show {short_id}` to view it again, "
            f"or `devlog edit {short_id}` to amend it."
        )
    if footer_hint:
        rows.append(Text(footer_hint, style="dim italic"))

    body = Group(*rows)

    title_text = Text()
    title_text.append(f"{title_icon} ", style=title_style)
    title_text.append(title, style=title_style)
    title_text.append(f"  ·  {short_id}", style="dim")

    return Panel(
        body,
        border_style=border_style,
        title=title_text,
        title_align="left",
        padding=(0, 1),
    )


# ---------------------------------------------------------------------------
# Show panel (single entry detail)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Show panel (single entry detail)
# ---------------------------------------------------------------------------


def show_panel(entry: Entry) -> Panel:
    """Render a single entry as a detailed view (used by `devlog show`).

    Includes the full id, the full message (no truncation), and both
    created/updated timestamps when present.

    Args:
        entry: the entry to render.

    Returns:
        A configured ``rich.panel.Panel`` ready to print.
    """
    short_id = entry.short_id
    full_id = Text(entry.id, style=_s("id_dim"))

    body = Group(
        _styled_row("ID", full_id),
        _styled_row("Date", _date_text(entry)),
        _styled_row("Updated", _updated_text(entry)),
        _styled_row("Tags", _tags_text(entry)),
        Text(),
        Text(entry.message),
    )

    title = _panel_title(icon=None, label="Entry", short_id=short_id, role="show_border")

    return _themed_panel(
        body,
        border_role="show_border",
        title=title,
    )


def delete_panel(entry: Entry) -> Panel:
    """Render a destructive confirmation that an entry was deleted."""
    short_id = entry.short_id
    tags_text = _tags_text(entry)

    body = Group(
        _styled_row("ID", Text(short_id, style=_s("id_dim"))),
        _styled_row("Date", _date_text(entry)),
        _styled_row("Tags", tags_text),
        Text(),
        Text(entry.message, style="strike dim"),
    )

    title = _panel_title(icon="✘", label="Entry deleted", short_id=short_id, role="delete_border")

    return _themed_panel(
        body,
        border_role="delete_border",
        title=title,
        footer_hint="Run `devlog list` to verify, or `devlog add` to write a new one.",
    )


def edit_panel(entry: Entry) -> Panel:
    """Render a blue-bordered confirmation that an entry was edited."""
    short_id = entry.short_id

    body = Group(
        _styled_row("Date", _date_text(entry)),
        _styled_row("Updated", _updated_text(entry)),
        _styled_row("Tags", _tags_text(entry)),
        Text(),
        Text(entry.message),
    )

    title = _panel_title(icon="✎", label="Entry updated", short_id=short_id, role="edit_border")

    return _themed_panel(
        body,
        border_role="edit_border",
        title=title,
        footer_hint=f"Run `devlog show {short_id}` to confirm the change.",
    )


def calendar_panel(per_day: dict, *, year: int, tz=None) -> Panel:
    """Wrap :func:`calendar_grid` in a cyan-bordered panel with a legend.

    Args:
        per_day: ``{YYYY-MM-DD: count}`` map. The bucketing date is
            the local date when *tz* is set, otherwise UTC.
        year: the year to render.
        tz: optional :class:`zoneinfo.ZoneInfo`.

    Returns:
        A configured :class:`rich.panel.Panel`.
    """
    grid = calendar_grid(per_day, year=year)
    total = sum(per_day.values())
    active_days = sum(1 for v in per_day.values() if v > 0)

    body_rows: list = [grid]
    body_rows.append(Text())
    # Build the "less · ▪ ▫ █ more" legend from the same data
    # table the heatmap uses, so changing a threshold or character
    # only requires editing one place. The tier chars keep their own
    # theme colour so the gradient reads left-to-right.
    legend = Text()
    legend.append("less ", style="dim")
    first = True
    for _char, role in [(t[1], t[2]) for t in _HEATMAP_TIERS]:
        if not first:
            legend.append(" ", style="dim")
        legend.append(_char, style=_s(role))
        first = False
    legend.append(" more", style="dim")
    body_rows.append(legend)
    body_rows.append(
        Text(
            f"{active_days} active day{plural_s(active_days)} · "
            f"{total} {plural_noun(total, 'entry')} in {year}",
            style="dim",
        )
    )

    title = _panel_title(icon=None, label=f"Calendar · {year}", role="show_border")

    return _themed_panel(
        Padding(Group(*body_rows), (0, 1)),
        border_role="show_border",
        title=title,
        padding=None,
    )


def stats_panel(
    *,
    total: int,
    first_iso: str,
    last_iso: str,
    top_tags: list[tuple[str, int]],
    last_30_days: list[tuple[str, int]],
    streaks: tuple[int, int] = (0, 0),
    top_tag_pairs: list[tuple[tuple[str, str], int]] | None = None,
    days_since: int | None = None,
    tz=None,
    tz_label: str = "UTC",
) -> Panel:
    """Render a `devlog stats` summary as a single panel.

    The layout is a labelled two-column list for the top section,
    followed by a tag table for the most-used tags, followed by a
    colourised sparkline of the last 30 days with a 0/max scale
    underneath. Stats v2 additions (streaks, tag pairs, days-since-last)
    slot into the labelled list and a pairs sub-section.

    Args:
        total: number of entries in the journal (or filtered subset).
        first_iso: ISO 8601 timestamp of the oldest entry.
        last_iso: ISO 8601 timestamp of the newest entry.
        top_tags: ``[(tag, count), ...]`` ordered most-used first.
        last_30_days: ``[(iso_date, count), ...]`` oldest first.
        streaks: ``(current, longest)`` consecutive-day streaks.
        top_tag_pairs: ``[((tag_a, tag_b), count), ...]`` most common
            tag co-occurrences, or ``None``/empty to omit the section.
        days_since: whole days since the most recent entry (negative
            when the newest entry is in the future), or ``None`` to omit.
        tz: optional :class:`zoneinfo.ZoneInfo` used to render
            ``first_iso`` and ``last_iso`` in the user's local zone.
            The on-disk representation is unaffected.
        tz_label: label appended after the displayed timestamp
            (default ``"UTC"``). Pass the zone's key (e.g. ``"EST"``)
            when *tz* is set.

    Returns:
        A configured :class:`rich.panel.Panel`.
    """
    first_str = format_dt(first_iso, tz=tz, tz_label=tz_label)
    last_str = format_dt(last_iso, tz=tz, tz_label=tz_label)
    sparkline_values = [c for _, c in last_30_days]
    sparkline_max = max(sparkline_values) if sparkline_values else 0

    # Active-day span (avoid div-by-zero on single-entry journals).
    # We compute the span in the same zone the user is looking at, so
    # an entry at 23:00 UTC on Jan 1 and another at 01:00 UTC on Jan 3
    # span 3 local days in America/New_York (Dec 31, Jan 1, Jan 2) but
    # 2 UTC days.
    try:
        first_dt = _iso.parse_utc_iso(first_iso)
        last_dt = _iso.parse_utc_iso(last_iso)
        if tz is not None:
            first_dt = first_dt.astimezone(tz)
            last_dt = last_dt.astimezone(tz)
        span_days = max(1, (last_dt.date() - first_dt.date()).days + 1)
    except ValueError:
        span_days = 1
    avg_per_day = total / span_days

    rows: list[Text] = [
        _styled_row("Total", str(total)),
        _styled_row("First", Text(first_str, style=_s("date"))),
        _styled_row("Last", Text(last_str, style=_s("date"))),
        _styled_row("Span", f"{span_days} day{plural_s(span_days)}"),
        # Sparse journals can average < 0.01/day; two decimals would
        # render a meaningless "0.00" there, so widen the precision
        # only when needed.
        _styled_row(
            "Avg/day",
            f"{avg_per_day:.2f}" if avg_per_day >= 0.01 else f"{avg_per_day:.4f}",
        ),
    ]

    # Stats v2 rows. Streaks always render (0 is meaningful — "your
    # streak is broken"); days-since-last only renders with a value.
    current_streak, longest_streak = streaks
    rows.append(
        _styled_row(
            "Streak", f"{current_streak} day{plural_s(current_streak)} (longest {longest_streak})"
        )
    )
    if days_since is not None:
        # Mirror the doctor panel's phrasing: a future-dated newest entry
        # must read "in N days (future-dated)", never "-N days ago".
        if days_since == 0:
            rows.append(_styled_row("Last entry", Text("today", style=_s("date"))))
        elif days_since < 0:
            rows.append(
                _styled_row(
                    "Last entry",
                    Text(
                        f"in {-days_since} day{plural_s(-days_since)} (future-dated)",
                        style=_s("warning_text"),
                    ),
                )
            )
        else:
            rows.append(_styled_row("Last entry", f"{days_since} day{plural_s(days_since)} ago"))

    rows.append(Text())
    if top_tags:
        rows.append(Text(f"Top {len(top_tags)} tags", style="bold"))
        for tag, count in top_tags:
            rows.append(_styled_row(f"  {tag}", str(count), label_width=12))
    else:
        rows.append(Text("No tags yet.", style="dim"))

    # Stats v2: tag co-occurrence pairs (only when the journal actually
    # has pairs; single-tag journals omit the section).
    if top_tag_pairs:
        rows.append(Text())
        rows.append(Text("Top tag pairs", style="bold"))
        for (a, b), count in top_tag_pairs:
            rows.append(_styled_row(f"  {a} + {b}", str(count), label_width=24))

    rows.append(Text())
    if sparkline_values:
        rows.append(Text("Last 30 days (entries per day)", style="bold"))
        spark = sparkline(sparkline_values)
        rows.append(Text(spark, style=_s("sparkline")))
        # Build the scale inline: "0" at left, max at right, aligned with
        # the sparkline. We pad the middle with spaces so the max value
        # sits flush against the last block character.
        scale = Text()
        scale.append("0", style="dim")
        # The sparkline length in characters equals the number of days (30).
        # Each block char is 1 char wide. We need to pad to align the max.
        pad_len = max(1, len(spark) - len(str(sparkline_max)) - 1)
        scale.append(" " * pad_len, style="dim")
        scale.append(str(sparkline_max), style="dim")
        rows.append(scale)

    return _themed_panel(
        Padding(Group(*rows), (0, 1)),
        border_role="show_border",
        title="Journal Stats",
        padding=None,
    )


# ---------------------------------------------------------------------------
# Repair summary (`devlog repair`)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Repair summary (`devlog repair`)
# ---------------------------------------------------------------------------


def repair_summary(
    issues: list,
    dropped: int,
    kept: int,
    *,
    dry_run: bool,
    backup_path: str | None,
    tags_stripped: int = 0,
) -> Panel:
    """Render a panel summarising a `devlog repair` invocation.

    Args:
        issues:        list of :class:`devlog.storage.Issue` objects.
        dropped:       number of entries the repair removed.
        kept:          number of entries retained.
        dry_run:       True when the user passed --dry-run (no write happened).
        backup_path:   path to a backup file when --backup was used, else None.
        tags_stripped: number of invalid tags removed from otherwise-valid
                       entries during this repair. Independent of ``dropped``;
                       the entry is kept, only the offending tag is gone.

    Returns:
        A configured :class:`rich.panel.Panel`.
    """

    rows: list[Text] = []
    if not issues:
        rows.append(Text("✔ No issues found. Nothing to repair.", style=_s("success_border")))
    else:
        rows.append(
            Text(
                f"Found {len(issues)} issue{plural_s(len(issues))}:",
                style="bold",
            )
        )
        rows.append(Text())
        # Show at most 20 issues to keep the panel readable
        rows.extend(
            _capped_enumerate(
                issues,
                20,
                lambda issue: Text(
                    f"  • [{(issue.entry_id[:8] + '…') if issue.entry_id and len(issue.entry_id) > 8 else (issue.entry_id or f'#{issue.index}')}] {issue.message}",
                    style=_s("warning_text"),
                ),
                tail_prefix="  …and {remaining} more",
            )
        )

    rows.append(Text())
    if dry_run:
        rows.append(Text("DRY RUN — no changes were written.", style=_bold("warning_text")))
    else:
        parts = [f"Removed {dropped} {plural_noun(dropped, 'entry')}", f"kept {kept}"]
        if tags_stripped:
            parts.append(f"stripped {tags_stripped} bad tag{plural_s(tags_stripped)}")
        rows.append(Text(", ".join(parts) + ".", style=_s("success_border")))
    if backup_path:
        rows.append(Text(f"Backup written to {backup_path}", style="dim"))

    title = _panel_title(icon="⚒", label="Repair · devlog store", role="info_text")

    return _themed_panel(
        Group(*rows),
        border_role="info_text",
        title=title,
    )


# ---------------------------------------------------------------------------
# Backup result (`devlog backup`)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Backup result (`devlog backup`)
# ---------------------------------------------------------------------------


def backup_result(path: str, count: int) -> Text:
    """Build a one-line confirmation for a successful backup.

    The path is wrapped in quotes and dimmed so it remains unambiguous
    when it contains spaces (a path like ``/Users/me/My Backups/x``
    would otherwise read as a sequence of separate words in the
    success line).
    """
    line = success_line(f"Backed up {count} {plural_noun(count, 'entry')} to ")
    line.append(f'"{path}"', style="dim")
    return line


# ---------------------------------------------------------------------------
# Doctor report (`devlog doctor`)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Doctor report (`devlog doctor`)
# ---------------------------------------------------------------------------


def doctor_report(report: dict) -> Panel:
    """Render a `devlog doctor` health report as a panel.

    Args:
        report: dict with keys:
            - ``ok``         (bool): True if the store is fully clean.
            - ``path``       (str): absolute path to the entries file.
            - ``writable``   (bool): whether the data dir is writable.
            - ``exists``     (bool): whether the entries file exists.
            - ``size_bytes`` (int): file size, or 0 if missing.
            - ``entry_count`` (int): number of valid entries.
            - ``issues``     (list[Issue]): validation issues, possibly empty.
            - ``days_since_last`` (int | None): days since the most recent entry.
            - ``top_messages`` (list[(str, int)]): top 3 longest messages.

    Returns:
        A configured :class:`rich.panel.Panel`.
    """
    rows: list[Text] = []
    rows.append(_styled_row("Path", Text(report["path"], style=_s("id_dim"))))
    rows.append(_styled_row("Exists", "yes" if report["exists"] else "no"))
    rows.append(
        _styled_row(
            "Size",
            f"{report['size_bytes']} byte{plural_s(report['size_bytes'])}",
        )
    )
    rows.append(
        _styled_row(
            "Writable",
            Text("yes", style=_s("success_border"))
            if report["writable"]
            else Text("no", style=_s("error_text")),
        )
    )
    rows.append(_styled_row("Entries", Text(str(report["entry_count"]), style="bold")))

    days = report.get("days_since_last")
    if days is None:
        rows.append(_styled_row("Last entry", Text("—", style="dim")))
    elif days == 0:
        rows.append(_styled_row("Last entry", Text("today", style=_s("date"))))
    elif days < 0:
        rows.append(
            _styled_row(
                "Last entry",
                Text(
                    f"in {-days} day{plural_s(-days)} (future-dated)",
                    style=_s("warning_text"),
                ),
            )
        )
    else:
        rows.append(
            _styled_row(
                "Last entry",
                f"{days} day{plural_s(days)} ago",
            )
        )

    issues = report.get("issues", [])
    rows.append(Text())
    if not issues:
        rows.append(Text("✔ No validation issues.", style=_s("success_border")))
    else:
        rows.append(
            Text(
                f"⚠ {len(issues)} validation issue{plural_s(len(issues))} — run `devlog repair` to fix.",
                style=_s("warning_text"),
            )
        )
        # Enumerate the first few issues inline so the user can act on
        # them without a second `devlog repair` round-trip. The full
        # list is preserved in `report["issues"]` for tooling.
        rows.extend(
            _capped_enumerate(
                issues,
                5,
                lambda issue: (
                    Text()
                    .append("    • ", style="dim")
                    .append(f"[{issue.get('kind', 'issue')}] ", style=_s("warning_text"))
                    .append(
                        f"{(issue.get('entry_id') or '—')[:8] + '…' if issue.get('entry_id') and len(issue.get('entry_id')) > 8 else (issue.get('entry_id') or '—')} ",
                        style=_s("id_dim"),
                    )
                    .append(issue.get("message", ""), style="dim")
                ),
                tail_prefix="    …and {remaining} more",
            )
        )

    top = report.get("top_messages") or []
    if top:
        rows.append(Text())
        rows.append(Text("Longest messages:", style="bold"))
        for short_id, length in top:
            rows.append(
                Text()
                .append("  • ", style="dim")
                .append(short_id, style=_s("id_dim"))
                .append(
                    f" — {length} char{plural_s(length)}",
                    style="dim",
                )
            )

    title = _panel_title(
        icon="♥",
        label=("Doctor · all clear" if report.get("ok") else "Doctor · attention"),
        role="success_border" if report.get("ok") else "warning_text",
    )

    border = _s("success_border") if report.get("ok") else _s("warning_text")
    return Panel(
        Group(*rows),
        border_style=border,
        title=title,
        title_align="left",
        padding=(0, 1),
    )
