"""Rendering helpers for the devlog CLI.

Centralises all Rich-based output so ``cli.py`` stays a thin Click layer
and styling is consistent across commands.

Conventions
-----------
- Errors are written to ``err_console`` (STDERR) in red with a ✘ icon.
- Warnings are written to ``err_console`` in yellow with a ⚠ icon.
- Informational/empty-state lines are written to ``console`` (STDOUT)
  dimmed with a ℹ icon.
- Color is automatically disabled when the ``NO_COLOR`` environment
  variable is set (https://no-color.org) or when the target stream is
  not a TTY.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import shutil
import sys
from typing import Mapping, Sequence

from rich.box import ROUNDED, SIMPLE
from rich.console import Console, Group
from rich.markup import escape
from rich.padding import Padding
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text

from devlog import themes
from devlog.models import Entry


def _s(role: str) -> str:
    """Shorthand for :func:`devlog.themes.get_style`."""
    return themes.get_style(role)


def _bold(role: str) -> str:
    """Shorthand for :func:`devlog.themes.get_bold_style`."""
    return themes.get_bold_style(role)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VERSION = "1.5.0"

MSG_TRUNCATE_LEN = 60
ID_DISPLAY_LEN = 8
DATE_DISPLAY_LEN = 20  # "YYYY-MM-DD HH:MM UTC" (4+1+2+1+2+1+2+1+2+1+3)

# Column widths are *content* widths (excluding the (0, 1) padding each
# cell adds). The widths passed to Table.add_column() are bumped by
# ``_COL_PADDING`` so the content area matches the constants below.
_COL_PADDING = 2  # default Rich cell padding is (0, 1) on each side
# Maximum number of extra chars to give the Tags column before the
# extra room goes to Message.
_TAGS_GROWTH_CAP = 18

# Width budget at 80-col terminals. Columns 1-3 sum to ~46 chars (incl. padding).
COL_ID_WIDTH = ID_DISPLAY_LEN  # 8
COL_DATE_WIDTH = DATE_DISPLAY_LEN  # 20
COL_TAGS_MIN = 10
COL_MESSAGE_MIN = 27  # 27 chars is the smallest a journal message can be
                       # read at a glance; below this the table feels
                       # claustrophobic and the smart-truncated match
                       # often falls off the right edge.
# Minimum terminal width we bother to lay out for. Below this, the table
# cannot fit the full ID + date columns and Rich would start hiding
# columns — the user is told to widen their terminal instead.
MIN_TERMINAL_WIDTH = 80
MAX_TERMINAL_WIDTH = 160

TAG_NONE = "(none)"

# ---------------------------------------------------------------------------
# Console construction (NO_COLOR + non-TTY respected)
# ---------------------------------------------------------------------------


def _color_enabled(stream) -> bool:
    """Return True when ANSI color should be emitted on *stream*."""
    if "NO_COLOR" in os.environ:
        return False
    try:
        return bool(stream.isatty())
    except (ValueError, AttributeError):
        return False


console = Console(no_color=not _color_enabled(sys.stdout))
err_console = Console(stderr=True, no_color=not _color_enabled(sys.stderr))


__all__ = [
    "console",
    "err_console",
    "print_error",
    "print_warning",
    "print_info",
    "entry_panel",
    "entries_table",
    "tags_table",
    "theme_table",
    "show_panel",
    "edit_panel",
    "delete_panel",
    "smart_truncate",
    "highlight_message",
    "export_progress",
    "version_banner",
    "root_banner",
    "repair_summary",
    "backup_result",
    "doctor_report",
    "stats_panel",
    "sparkline",
    "Group",
    "Padding",
    "Text",
    "Panel",
    "Table",
    "Progress",
    "themes",
    "ID_DISPLAY_LEN",
    "MSG_TRUNCATE_LEN",
    "TAG_NONE",
    "VERSION",
    "pluralize",
    "plural_s",
]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def pluralize(n: int, singular: str, plural: str | None = None) -> str:
    """Return ``f"{n} {singular|plural}"`` with the right word form.

    Centralises the "1 entry" / "2 entries" pattern that was previously
    hand-rolled with inline ``'y' if n == 1 else 'ies'`` expressions,
    several of which got the pluralisation wrong.

    Args:
        n: the count. The form is selected purely on whether this is 1.
        singular: the singular noun form, e.g. ``"entry"``.
        plural: optional explicit plural form. Defaults to
            ``singular + "s"`` (with one special case: ``"entry"`` →
            ``"entries"``).

    Returns:
        A formatted ``"<n> <word>"`` string. Note: the count is included.
    """
    if n == 1:
        return f"{n} {singular}"
    if plural is None:
        plural = "entries" if singular == "entry" else f"{singular}s"
    return f"{n} {plural}"


def _plural_noun(n: int, singular: str, plural: str | None = None) -> str:
    """Like :func:`pluralize` but without the count prefix.

    Useful inside template strings that already include the count, e.g.
    ``f"{n} of {total} {_plural_noun(total, 'entry')}"``.

    For *n* in {0, 1}, returns ``singular`` (so "1 row" not "1 rows").
    """
    if n == 1:
        return singular
    if plural is None:
        plural = "entries" if singular == "entry" else f"{singular}s"
    return plural


def plural_s(n: int) -> str:
    """Return ``"s"`` for plurals (n != 1) and ``""`` for n == 1.

    Convenience for suffix-style pluralisation:
    ``f"duplicate{plural_s(n)}"`` → "duplicate" or "duplicates".
    """
    return "" if n == 1 else "s"


# ---------------------------------------------------------------------------
# Error / warning / info helpers
# ---------------------------------------------------------------------------


def print_error(message: str) -> None:
    """Print a red error panel to STDERR.

    Args:
        message: the error text. Should not be prefixed with an icon.
    """
    body = Text()
    body.append("✘ ", style=_bold("error_text"))
    body.append(message, style=_s("error_text"))
    err_console.print(
        Panel(
            body,
            border_style=_s("error_border"),
            title="Error",
            title_align="left",
        )
    )


def print_warning(message: str) -> None:
    """Print a yellow warning to STDERR (single line, no panel)."""
    line = Text()
    line.append("⚠ ", style=_bold("warning_text"))
    line.append(message, style=_s("warning_text"))
    err_console.print(line)


def print_info(message: str) -> None:
    """Print a dim info line to STDOUT with an ℹ icon."""
    line = Text()
    line.append("ℹ ", style=_s("info_text"))
    line.append(message, style=_s("info_text"))
    console.print(line)


# ---------------------------------------------------------------------------
# Entry panel (`devlog add` success)
# ---------------------------------------------------------------------------


def _format_dt(iso: str, *, tz=None, tz_label: str = "UTC") -> str:
    """Convert a stored ISO 8601 UTC string to display form.

    Args:
        iso: datetime string in ``YYYY-MM-DDTHH:MM:SSZ`` format.
        tz: optional :class:`zoneinfo.ZoneInfo`. When supplied, the
            value is converted to the local zone before rendering. The
            conversion does not change the on-disk timestamp; it only
            affects how the timestamp is *shown*.
        tz_label: suffix appended after the time (default ``"UTC"``).
            When a *tz* is supplied, callers usually pass the zone's
            key (e.g. ``"EST"``) for clarity.

    Returns:
        Formatted as ``YYYY-MM-DD HH:MM <tz_label>``. If the input is
        not a parseable ISO 8601 timestamp, the raw string is returned
        unchanged so callers (notably :func:`stats_panel`) never raise
        on a corrupt store.
    """
    if not iso:
        return "—"
    try:
        dt = _dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return iso
    if tz is not None:
        dt = dt.astimezone(tz)
    return dt.strftime(f"%Y-%m-%d %H:%M {tz_label}")


def _styled_row(label: str, value) -> Text:
    """Build a single ``Label : value`` row with aligned columns.

    Args:
        label: the row label, rendered dim.
        value: a plain string OR a ``Text`` to be styled as the value.

    Returns:
        A ``Text`` ready to be added to a Group inside a Panel.
    """
    line = Text()
    line.append(f"{label:<6}", style="dim")
    line.append(": ")
    if isinstance(value, Text):
        line.append_text(value)
    else:
        line.append(str(value))
    return line


def entry_panel(
    entry: Entry,
    *,
    title: str = "Entry added",
    title_icon: str = "✔",
    title_style: str | None = None,
    border_style: str | None = None,
    show_full_id: bool = False,
    footer_hint: str = "Run `devlog list` to see all entries.",
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
        footer_hint:  dim italic footer line (set to empty to hide).

    Returns:
        A configured ``rich.panel.Panel`` ready to print.
    """
    if title_style is None:
        title_style = _s("success_title")
    if border_style is None:
        border_style = _s("success_border")
    short_id = entry.id[:ID_DISPLAY_LEN]
    tags_display = (
        Text(", ".join(entry.tags), style=_s("tags"))
        if entry.tags
        else Text(TAG_NONE, style="dim")
    )
    date_text = Text(_format_dt(entry.created_at), style=_s("date"))

    rows = [
        _styled_row("Date", date_text),
        _styled_row("Tags", tags_display),
    ]
    if show_full_id:
        rows.append(_styled_row("ID", Text(entry.id, style=_s("id_dim"))))
    rows.append(_styled_row("Note", Text(entry.message)))
    rows.append(Text())
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
# Truncation & highlighting (`devlog list` / `devlog search`)
# ---------------------------------------------------------------------------


def smart_truncate(
    message: str, query: str = "", limit: int = MSG_TRUNCATE_LEN
) -> str:
    """Truncate a message around the first match of *query*.

    - When *query* is empty, falls back to left-truncation with ``…``.
    - When *query* matches, the cell is built as
      ``prefix…[bold yellow]match[/bold yellow]…suffix`` so the hit is
      always visible, capped at *limit* visible characters.
    - When *query* does not match (defensive), falls back to left-truncation.

    Args:
        message: the raw entry message.
        query:   the search string (case-insensitive).
        limit:   maximum visible characters (default 60).

    Returns:
        A Rich-markup string ready to be rendered in a Table cell.
    """
    if not query:
        return _left_truncate(message, limit)

    lower_msg = message.lower()
    lower_q = query.lower()
    idx = lower_msg.find(lower_q)
    if idx < 0:
        return _left_truncate(message, limit)

    q_len = len(query)
    match_end = idx + q_len
    match_text = message[idx:match_end]

    # Reserve budget on each side of the match so the hit stays visible.
    side_budget = max(0, (limit - q_len) // 2)
    start = max(0, idx - side_budget)
    end = min(len(message), match_end + side_budget)

    prefix = message[start:idx]
    suffix = message[match_end:end]

    parts = []
    if start > 0:
        parts.append("…")
    if prefix:
        parts.append(escape(prefix))
    parts.append(f"[{_s('match_highlight')}]{escape(match_text)}[/{_s('match_highlight')}]")
    if suffix:
        parts.append(escape(suffix))
    if end < len(message):
        parts.append("…")

    return "".join(parts)


def _left_truncate(message: str, limit: int = MSG_TRUNCATE_LEN) -> str:
    """Truncate to *limit* chars from the left, appending ``…``."""
    if len(message) > limit:
        return message[: limit - 1] + "…"
    return message


def highlight_message(message: str, query: str) -> str:
    """Backwards-compatible wrapper: truncate then highlight in place.

    Args:
        message: the raw entry message.
        query:   the search string.

    Returns:
        Rich-markup string.
    """
    trunc = _left_truncate(message)
    if not query:
        return escape(trunc)
    highlight = _s("match_highlight")
    return re.sub(
        re.escape(query),
        lambda m: f"[{highlight}]{escape(m.group(0))}[/{highlight}]",
        escape(trunc),
        flags=re.IGNORECASE,
    )


# ---------------------------------------------------------------------------
# Entries table
# ---------------------------------------------------------------------------


def _short_id(entry: Entry) -> str:
    return entry.id[:ID_DISPLAY_LEN]


def _tags_text(entry: Entry) -> Text:
    if not entry.tags:
        return Text(TAG_NONE, style="dim")
    return Text(", ".join(entry.tags), style=_s("tags"))


def _terminal_width() -> int:
    """Best-effort terminal width with a sane floor and cap.

    Reads from the active Rich ``console`` first, falling back to
    :func:`shutil.get_terminal_size` for the rare case where the
    console is queried before being initialised. The floor is
    :data:`MIN_TERMINAL_WIDTH` (80) so the table never tries to fit
    into a window too small to show the full 8-char short ID +
    19-char date + a usable message column.
    """
    try:
        w = console.width
        if w and w > 0:
            return max(MIN_TERMINAL_WIDTH, min(w, MAX_TERMINAL_WIDTH))
    except (AttributeError, RuntimeError):
        pass
    try:
        w = shutil.get_terminal_size((100, 20)).columns
    except (OSError, ValueError):
        w = 100
    return max(MIN_TERMINAL_WIDTH, min(w, MAX_TERMINAL_WIDTH))


def _column_widths() -> dict:
    """Compute column widths so the table fits the terminal.

    The widths returned are the *visible content* widths (what the user
    sees in the cell, excluding the (0, 1) padding that Rich adds on
    each side). Callers add :data:`_COL_PADDING` when passing them to
    :class:`rich.table.Table.add_column`.

    The sum of ``width + _COL_PADDING`` across all columns plus the
    box border overhead is guaranteed to be at most
    :func:`_terminal_width`, so Rich never has to shrink any column
    below its declared ``width=`` and the 8-char short id and 19-char
    date are never truncated.
    """
    total = _terminal_width()
    # Rich renders 4 columns with 5 box borders and 2 chars of padding
    # on each side of every cell, so the columns (with padding) must
    # fit in ``total - 5`` chars.
    col_budget = total - 5
    fixed = COL_ID_WIDTH + COL_DATE_WIDTH + COL_TAGS_MIN + COL_MESSAGE_MIN
    # The fixed widths (in content cells) plus their padding (4 cells
    # × 2 padding) must also fit in the column budget.
    min_with_padding = fixed + (4 * _COL_PADDING)
    extra = max(0, col_budget - min_with_padding)
    # Give the extra room to Tags first (a few more visible tags is
    # more useful than a few more message chars), then to Message.
    tags_extra = min(extra, _TAGS_GROWTH_CAP - COL_TAGS_MIN)
    tags_width = COL_TAGS_MIN + tags_extra
    message_width = COL_MESSAGE_MIN + (extra - tags_extra)
    return {
        "id": COL_ID_WIDTH,
        "date": COL_DATE_WIDTH,
        "tags": tags_width,
        "message": message_width,
    }


def entries_table(
    entries: Sequence[Entry],
    total: int,
    *,
    highlight_query: str = "",
    title: str = "",
    subtitle: str = "",
) -> Table:
    """Build a Rich Table for a list of entries.

    Args:
        entries:         entries to render (already sliced to limit).
        total:           total matched count (before limit) for the footer.
        highlight_query: when non-empty, smart-truncate around first match.
        title:           optional table title (e.g. ``"Journal · 3 entries"``).
        subtitle:        optional subtitle (e.g. ``'Query: "auth"'``).

    Returns:
        A fully configured ``rich.table.Table`` instance.
    """
    widths = _column_widths()

    table = Table(
        box=ROUNDED,
        show_footer=True,
        title=title or None,
        title_justify="left",
        title_style="bold",
        caption=subtitle or None,
        caption_style="dim",
        caption_justify="left",
        row_styles=["", _s("zebra_alt")],
        header_style="bold",
        expand=False,
    )
    # Widths passed to Rich include the (0, 1) cell padding on each
    # side, so the visible content area matches the *_DISPLAY_LEN
    # constants and the short ID is never truncated.
    table.add_column(
        "ID",
        style=_s("id_dim"),
        no_wrap=True,
        width=widths["id"] + _COL_PADDING,
    )
    table.add_column(
        "Date",
        style=_s("date"),
        no_wrap=True,
        width=widths["date"] + _COL_PADDING,
    )
    # Tags are truncated with an ellipsis when too long to fit, so a
    # single long tag list never pushes the row taller than its
    # neighbours. Users who want to see the full tag list can pass
    # --all to widen the table on a wide terminal.
    table.add_column(
        "Tags",
        style=_s("tags"),
        no_wrap=True,
        overflow="ellipsis",
        width=widths["tags"] + _COL_PADDING,
    )
    table.add_column(
        "Message",
        width=widths["message"] + _COL_PADDING,
        footer=f"Showing {len(entries)} of {total} {_plural_noun(total, 'entry')}.",
        footer_style="bold",
        # Keep short messages left-aligned (Rich centres them by
        # default) and keep the footer flush against the cell's left
        # edge so it does not float in whitespace.
        justify="left",
    )

    # Use the actual message column width as the truncation budget.
    # MSG_TRUNCATE_LEN is the spec-mandated *maximum* (60 chars) — we
    # never truncate *more* than that, but at narrow terminals the
    # column is narrower, so the budget shrinks with it. The
    # smart_truncate path always keeps the matched substring visible
    # inside the visible window, so even a narrow column still shows
    # the hit.
    msg_limit = min(MSG_TRUNCATE_LEN, widths["message"])
    for entry in entries:
        if highlight_query:
            msg_cell = smart_truncate(
                entry.message, highlight_query, msg_limit
            )
        else:
            msg_cell = escape(_left_truncate(entry.message, msg_limit))
        table.add_row(
            _short_id(entry),
            _format_dt(entry.created_at),
            _tags_text(entry),
            msg_cell,
        )

    return table


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
    short_id = entry.id[:ID_DISPLAY_LEN]
    full_id = Text(entry.id, style=_s("id_dim"))
    tags_display = (
        Text(", ".join(entry.tags), style=_s("tags"))
        if entry.tags
        else Text(TAG_NONE, style="dim")
    )
    created_text = Text(_format_dt(entry.created_at), style=_s("date"))
    updated_text = (
        Text(_format_dt(entry.updated_at), style=_s("updated"))
        if entry.updated_at
        else Text("—", style="dim")
    )
    message_text = Text(entry.message)

    body = Group(
        _styled_row("ID", full_id),
        _styled_row("Date", created_text),
        _styled_row("Updtd", updated_text),
        _styled_row("Tags", tags_display),
        Text(),
        message_text,
    )

    title = Text()
    title.append("Entry", style="bold")
    title.append(f"  ·  {short_id}", style="dim")

    return Panel(
        body,
        border_style=_s("show_border"),
        title=title,
        title_align="left",
        padding=(0, 1),
    )


def delete_panel(entry: Entry) -> Panel:
    """Render a destructive confirmation that an entry was deleted."""
    short_id = entry.id[:ID_DISPLAY_LEN]
    date_text = Text(_format_dt(entry.created_at), style=_s("date"))
    tags_str = ", ".join(entry.tags) if entry.tags else TAG_NONE
    tags_text = Text(tags_str, style=_s("tags") if entry.tags else "dim")

    body = Group(
        _styled_row("ID", Text(short_id, style=_s("id_dim"))),
        _styled_row("Date", date_text),
        _styled_row("Tags", tags_text),
        Text(),
        Text(entry.message, style="strike dim"),
    )

    title = Text()
    title.append("✘ ", style=_bold("delete_border"))
    title.append("Entry deleted", style=_bold("delete_border"))
    title.append(f"  ·  {short_id}", style="dim")

    return Panel(
        body,
        border_style=_s("delete_border"),
        title=title,
        title_align="left",
        padding=(0, 1),
    )


def edit_panel(entry: Entry) -> Panel:
    """Render a blue-bordered confirmation that an entry was edited."""
    short_id = entry.id[:ID_DISPLAY_LEN]
    date_text = Text(_format_dt(entry.created_at), style=_s("date"))
    updated_text = (
        Text(_format_dt(entry.updated_at), style=_s("updated"))
        if entry.updated_at
        else Text("—", style="dim")
    )
    tags_display = (
        Text(", ".join(entry.tags), style=_s("tags"))
        if entry.tags
        else Text(TAG_NONE, style="dim")
    )

    body = Group(
        _styled_row("Date", date_text),
        _styled_row("Updtd", updated_text),
        _styled_row("Tags", tags_display),
        Text(),
        Text(entry.message),
    )

    title = Text()
    title.append("✎ ", style=_bold("edit_border"))
    title.append("Entry updated", style=_bold("edit_border"))
    title.append(f"  ·  {short_id}", style="dim")

    return Panel(
        body,
        border_style=_s("edit_border"),
        title=title,
        title_align="left",
        padding=(0, 1),
    )


# ---------------------------------------------------------------------------
# Tags table (`devlog tags`)
# ---------------------------------------------------------------------------


def tags_table(
    rows: Sequence[tuple[str, int, str]],
    total_tags: int,
    total_entries: int,
) -> Table:
    """Render a table of tag usage.

    Args:
        rows:        sequence of (tag, count, last_used_iso) tuples.
        total_tags:  total number of distinct tags.
        total_entries: total number of entries considered.

    Returns:
        A configured ``rich.table.Table`` instance.
    """
    table = Table(
        box=ROUNDED,
        show_footer=True,
        title=f"Tags · {total_tags} distinct",
        title_justify="left",
        title_style="bold",
        header_style="bold",
        expand=False,
    )
    table.add_column("Tag", style=_s("tags"), no_wrap=True)
    table.add_column("Count", style="bold", justify="right", no_wrap=True)
    table.add_column(
        "Last used",
        style=_s("date"),
        no_wrap=True,
        footer=f"Across {total_entries} {_plural_noun(total_entries, 'entry')}.",
        footer_style="bold",
    )

    for tag, count, last_used in rows:
        last_used_text = _format_dt(last_used) if last_used else Text("—", style="dim")
        table.add_row(tag, str(count), last_used_text)

    return table


# ---------------------------------------------------------------------------
# Stats panel (`devlog stats`)
# ---------------------------------------------------------------------------


def sparkline(values: list[int]) -> str:
    """Build a compact horizontal sparkline using block characters.

    The character height encodes the *relative* value within the
    dataset: the largest value renders as the tallest block (``█``)
    and zero as the shortest (``▁``). Eight discrete levels are used,
    so a one-entry day and a 50-entry day are visually distinct but
    the bars never overflow the cell.

    Args:
        values: one integer per day, oldest first.

    Returns:
        A single string of block characters, one per day.
    """
    if not values:
        return ""
    max_v = max(values) or 1
    blocks = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    out = []
    for v in values:
        idx = min(len(blocks) - 1, int((v / max_v) * len(blocks)))
        out.append(blocks[idx])
    return "".join(out)


# ---------------------------------------------------------------------------
# Calendar heatmap (`devlog calendar`)
# ---------------------------------------------------------------------------


# Characters used by the heatmap. `█` for the busiest days, `▫` for
# the next tier, `▪` for the second-lightest, `·` for the lightest
# non-zero day, and a space for empty days. Two characters per day
# keeps the grid from collapsing visually on narrow terminals.
_HEATMAP_CHARS = [" ", "·", "▪", "▫", "█"]


def calendar_grid(per_day: dict, *, year: int) -> Text:
    """Build a year-grid heatmap (53 weeks × 7 days) of entry counts.

    Each cell is one character wide and styled with the corresponding
    ``heatmap_*`` theme role. Days outside the requested *year* are
    rendered as a space; days in the year with no entries are also a
    space but use the ``heatmap_empty`` style for clarity.

    The grid is laid out Sunday-first (the international convention)
    and each column is one ISO week. The first column may be padded
    with leading spaces so the 1st of January falls on the correct
    weekday row.

    Args:
        per_day: ``{YYYY-MM-DD: count}`` map. Dates outside *year* are
            ignored.
        year: the 4-digit year to render.

    Returns:
        A :class:`rich.text.Text` of the grid (one row per weekday,
        with a trailing newline per row, ready to print).
    """
    import calendar as _cal
    import datetime as _dt

    # Find the weekday of Jan 1 (0=Mon, 6=Sun). We render Sunday-first
    # so the first column is the week containing the first Sunday
    # on/before Jan 1.
    jan1 = _dt.date(year, 1, 1)
    dec31 = _dt.date(year, 12, 31)
    # `calendar.SUNDAY = 6` already; .firstweekday is what we want.
    firstweekday = _cal.SUNDAY  # always Sunday for this view
    first_col_pad = (jan1.weekday() - firstweekday) % 7  # 0 if Jan 1 is Sunday
    total_days = (dec31 - jan1).days + 1
    total_cells = first_col_pad + total_days
    num_weeks = (total_cells + 6) // 7  # ceil to whole weeks

    # Pre-compute the max for the relative scale.
    max_count = max(per_day.values()) if per_day else 0

    def _char_for(count: int) -> tuple[str, str]:
        """Map a count to (character, theme role)."""
        if count <= 0 or max_count <= 0:
            return " ", "heatmap_empty"
        # Distribute into 4 non-zero tiers based on fraction of max.
        ratio = count / max_count
        if ratio > 0.75:
            return "█", "heatmap_l4"
        if ratio > 0.5:
            return "▫", "heatmap_l3"
        if ratio > 0.25:
            return "▪", "heatmap_l2"
        return "·", "heatmap_l1"

    grid = Text()
    # Build a 7×N array of (char, role) tuples.
    for weekday in range(7):
        for week in range(num_weeks):
            cell_index = week * 7 + weekday
            day_offset = cell_index - first_col_pad
            if day_offset < 0 or day_offset >= total_days:
                # Outside the year → leading/trailing space.
                grid.append(" ", style=_s("heatmap_empty"))
            else:
                d = jan1 + _dt.timedelta(days=day_offset)
                key = d.strftime("%Y-%m-%d")
                count = per_day.get(key, 0)
                ch, role = _char_for(count)
                grid.append(ch, style=_s(role))
        grid.append("\n")
    return grid


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
    legend = Text()
    legend.append("less ", style="dim")
    legend.append("·", style=_s("heatmap_l1"))
    legend.append(" ", style="dim")
    legend.append("▪", style=_s("heatmap_l2"))
    legend.append(" ", style="dim")
    legend.append("▫", style=_s("heatmap_l3"))
    legend.append(" ", style="dim")
    legend.append("█", style=_s("heatmap_l4"))
    legend.append(" more", style="dim")
    body_rows.append(legend)
    body_rows.append(
        Text(
            f"{active_days} active day{plural_s(active_days)} · "
            f"{total} {_plural_noun(total, 'entry')} in {year}",
            style="dim",
        )
    )

    title = Text()
    title.append("Calendar ", style="bold")
    title.append(f"· {year}", style="dim")

    return Panel(
        Padding(Group(*body_rows), (0, 1)),
        border_style=_s("show_border"),
        title=title,
        title_align="left",
    )


def stats_panel(
    *,
    total: int,
    first_iso: str,
    last_iso: str,
    top_tags: list[tuple[str, int]],
    last_30_days: list[tuple[str, int]],
    tz=None,
    tz_label: str = "UTC",
) -> Panel:
    """Render a `devlog stats` summary as a single panel.

    The layout is a labelled two-column list for the top section,
    followed by a tag table for the most-used tags, followed by a
    colourised sparkline of the last 30 days with a 0/max scale
    underneath.

    Args:
        total: number of entries in the journal (or filtered subset).
        first_iso: ISO 8601 timestamp of the oldest entry.
        last_iso: ISO 8601 timestamp of the newest entry.
        top_tags: ``[(tag, count), ...]`` ordered most-used first.
        last_30_days: ``[(iso_date, count), ...]`` oldest first.
        tz: optional :class:`zoneinfo.ZoneInfo` used to render
            ``first_iso`` and ``last_iso`` in the user's local zone.
            The on-disk representation is unaffected.
        tz_label: label appended after the displayed timestamp
            (default ``"UTC"``). Pass the zone's key (e.g. ``"EST"``)
            when *tz* is set.

    Returns:
        A configured :class:`rich.panel.Panel`.
    """
    first_str = _format_dt(first_iso, tz=tz, tz_label=tz_label)
    last_str = _format_dt(last_iso, tz=tz, tz_label=tz_label)
    sparkline_values = [c for _, c in last_30_days]
    sparkline_max = max(sparkline_values) if sparkline_values else 0

    # Active-day span (avoid div-by-zero on single-entry journals).
    # We compute the span in the same zone the user is looking at, so
    # an entry at 23:00 UTC on Jan 1 and another at 01:00 UTC on Jan 3
    # span 3 local days in America/New_York (Dec 31, Jan 1, Jan 2) but
    # 2 UTC days.
    try:
        first_dt = _dt.datetime.fromisoformat(first_iso.replace("Z", "+00:00"))
        last_dt = _dt.datetime.fromisoformat(last_iso.replace("Z", "+00:00"))
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
        _styled_row("Avg/day", f"{avg_per_day:.2f}"),
    ]

    rows.append(Text())
    if top_tags:
        rows.append(Text(f"Top {len(top_tags)} tags", style="bold"))
        for tag, count in top_tags:
            rows.append(_stats_row_text(f"  {tag}", str(count)))
    else:
        rows.append(Text("No tags yet.", style="dim"))

    rows.append(Text())
    if sparkline_values:
        rows.append(Text("Last 30 days (entries per day)", style="bold"))
        rows.append(Text(sparkline(sparkline_values), style=_s("date")))
        rows.append(
            Text()
            .append("0", style="dim")
            .append(" " * max(0, 30 - len(str(sparkline_max)) - len("0")))
            .append(str(sparkline_max), style="dim")
        )

    return Panel(
        Padding(Group(*rows), (0, 1)),
        border_style=_s("show_border"),
        title="Journal Stats",
        title_align="left",
    )


def _stats_row_text(label: str, value: str) -> Text:
    """Build a labelled stat row matching the entry-panel style."""
    t = Text()
    t.append(f"{label:<12}", style="dim")
    t.append(": ")
    t.append(value)
    return t


# ---------------------------------------------------------------------------
# Export progress
# ---------------------------------------------------------------------------


def export_progress(total: int) -> Progress:
    """Return a configured Rich ``Progress`` for the export command.

    The progress bar writes to STDERR so it never pollutes the output
    file when the user pipes STDOUT.

    Args:
        total: number of entries to write.

    Returns:
        A ``Progress`` context manager. Caller must use it as
        ``with export_progress(n) as progress: ...``.
    """
    return Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=err_console,
    )


# ---------------------------------------------------------------------------
# Version banner / root help
# ---------------------------------------------------------------------------


def version_banner() -> None:
    """Print a styled version line for ``--version``.

    The previous design wrapped the version in a horizontal ``Rule``,
    but the rule carried no information and overflowed on narrow
    terminals. A single, prominent version line is enough.
    """
    console.print(
        Text("devlog, version ", style="bold")
        + Text(VERSION, style=_s("banner_version"))
    )


def root_banner() -> None:
    """Print a friendly banner when ``devlog`` is invoked with no subcommand.

    Replaces the raw Click help text with a banner + 2-column command
    table and a hint about ``--help``.
    """
    console.print(
        Text("devlog", style="bold")
        + Text("  ·  a terminal-based developer journal", style="dim")
    )
    console.print()

    table = Table(
        box=SIMPLE,
        show_header=False,
        padding=(0, 2),
        expand=False,
    )
    table.add_column(style=_s("banner_command"), no_wrap=True)
    table.add_column(style="default")
    table.add_row("add", "Add a new journal entry")
    table.add_row("show", "Show a single entry by ID")
    table.add_row("edit", "Edit an entry's message and/or tags")
    table.add_row("delete", "Delete an entry by ID")
    table.add_row("list", "List entries, newest first")
    table.add_row("search", "Search entry messages")
    table.add_row("today", "Show today's entries")
    table.add_row("yesterday", "Show yesterday's entries")
    table.add_row("week", "Show the last 7 days")
    table.add_row("tail", "Show the N most recent entries")
    table.add_row("tags", "List tags with usage counts")
    table.add_row("tag", "Show or delete entries with a tag")
    table.add_row("merge-tag", "Merge two tags across all entries")
    table.add_row("theme", "View or change the active color theme")
    table.add_row("stats", "Summarize the journal")
    table.add_row("calendar", "Show a year-grid heatmap of activity")
    table.add_row("rename-tag", "Rename a tag across all entries")
    table.add_row("import", "Import entries from a JSON or Markdown file")
    table.add_row("completions", "Print a shell completion script")
    table.add_row("export", "Export entries to a Markdown file")
    table.add_row("repair", "Inspect and repair the on-disk journal store")
    table.add_row("backup", "Write a timestamped copy of the journal")
    table.add_row("restore", "Restore the journal from a backup file")
    table.add_row("doctor", "Check the journal store for corruption")
    console.print(table)

    console.print(
        Text("Run ", style="dim")
        + Text("devlog <command> --help", style="bold")
        + Text(" for details on a specific command.", style="dim")
    )


# ---------------------------------------------------------------------------
# Repair summary (`devlog repair`)
# ---------------------------------------------------------------------------


def repair_summary(
    issues: list, dropped: int, kept: int, *, dry_run: bool, backup_path: str | None
) -> Panel:
    """Render a panel summarising a `devlog repair` invocation.

    Args:
        issues:      list of :class:`devlog.storage.Issue` objects.
        dropped:     number of entries the repair removed.
        kept:        number of entries retained.
        dry_run:     True when the user passed --dry-run (no write happened).
        backup_path: path to a backup file when --backup was used, else None.

    Returns:
        A configured :class:`rich.panel.Panel`.
    """
    from devlog.storage import Issue

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
        for issue in issues[:20]:
            assert isinstance(issue, Issue)
            short = (issue.entry_id[:8] + "…") if issue.entry_id and len(issue.entry_id) > 8 else (issue.entry_id or f"#{issue.index}")
            rows.append(Text(f"  • [{short}] {issue.message}", style=_s("warning_text")))
        if len(issues) > 20:
            rows.append(Text(f"  …and {len(issues) - 20} more", style="dim"))

    rows.append(Text())
    if dry_run:
        rows.append(Text("DRY RUN — no changes were written.", style=_bold("warning_text")))
    else:
        rows.append(
            Text(
                f"Removed {dropped} {_plural_noun(dropped, 'entry')}, kept {kept}.",
                style=_s("success_border"),
            )
        )
    if backup_path:
        rows.append(Text(f"Backup written to {backup_path}", style="dim"))

    title = Text()
    title.append("🔧 ", style=_bold("info_text"))
    title.append("Repair ", style="bold")
    title.append("· devlog store", style="dim")

    return Panel(
        Group(*rows),
        border_style=_s("info_text"),
        title=title,
        title_align="left",
        padding=(0, 1),
    )


# ---------------------------------------------------------------------------
# Backup result (`devlog backup`)
# ---------------------------------------------------------------------------


def backup_result(path: str, count: int) -> Text:
    """Build a one-line confirmation for a successful backup."""
    line = Text()
    line.append("✔ ", style=_bold("success_title"))
    line.append(
        f"Backed up {count} {_plural_noun(count, 'entry')} to ",
        style=_s("success_border"),
    )
    line.append(path, style="bold")
    return line


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
            Text("yes", style=_s("success_border")) if report["writable"] else Text("no", style=_s("error_text")),
        )
    )
    rows.append(_styled_row("Entries", Text(str(report["entry_count"]), style="bold")))

    days = report.get("days_since_last")
    if days is None:
        rows.append(_styled_row("Last entry", Text("—", style="dim")))
    elif days == 0:
        rows.append(_styled_row("Last entry", Text("today", style=_s("date"))))
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
        for issue in issues[:5]:
            eid = issue.get("entry_id") or "—"
            if eid and len(eid) > 8:
                eid = eid[:8] + "…"
            kind = issue.get("kind", "issue")
            msg = issue.get("message", "")
            rows.append(
                Text()
                .append("    • ", style="dim")
                .append(f"[{kind}] ", style=_s("warning_text"))
                .append(f"{eid} ", style=_s("id_dim"))
                .append(msg, style="dim")
            )
        if len(issues) > 5:
            rows.append(
                Text(
                    f"    …and {len(issues) - 5} more",
                    style="dim",
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
                    f" — {length} {('char' if length == 1 else 'chars')}",
                    style="dim",
                )
            )

    title = Text()
    title.append("🩺 ", style=_bold("info_text"))
    title.append("Doctor", style="bold")
    if report.get("ok"):
        title.append("  ·  all clear", style=_s("success_border"))
    else:
        title.append("  ·  attention", style=_s("warning_text"))

    border = _s("success_border") if report.get("ok") else _s("warning_text")
    return Panel(
        Group(*rows),
        border_style=border,
        title=title,
        title_align="left",
        padding=(0, 1),
    )


# ---------------------------------------------------------------------------
# Theme table (`devlog theme list` / `devlog theme show`)
# ---------------------------------------------------------------------------


def theme_table(
    palette: Mapping[str, str] | None = None,
    *,
    title: str = "Active theme",
) -> Table:
    """Render a two-column table of role → style mappings.

    Used by the ``devlog theme list`` subcommand. When *palette* is
    omitted, the active theme is rendered. Roles are shown in
    :data:`devlog.themes.ROLES` order so the output is stable across
    runs and easy to diff.

    Args:
        palette: a ``{role: style}`` mapping. Defaults to the active
            theme from :mod:`devlog.themes`.
        title:   the table title (default: ``"Active theme"``).

    Returns:
        A configured ``rich.table.Table`` instance.
    """
    if palette is None:
        palette = themes.get_active_theme()

    table = Table(
        box=ROUNDED,
        show_header=True,
        title=title,
        title_justify="left",
        title_style="bold",
        header_style="bold",
        expand=False,
    )
    table.add_column("Role", style=_s("id_dim"), no_wrap=True)
    table.add_column("Style", style="default")

    for role in sorted(themes.ROLES):
        table.add_row(role, palette[role])

    return table
