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
from typing import Sequence

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
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from devlog.models import Entry

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VERSION = "1.3.0"

MSG_TRUNCATE_LEN = 60
ID_DISPLAY_LEN = 8
DATE_DISPLAY_LEN = 19  # "YYYY-MM-DD HH:MM UTC"

# Width budget at 80-col terminals. Columns 1-3 sum to ~46 chars (incl. padding).
COL_ID_WIDTH = 8
COL_DATE_WIDTH = DATE_DISPLAY_LEN + 2  # +2 for padding
COL_TAGS_MIN = 12
COL_MESSAGE_MIN = 20

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
    "show_panel",
    "edit_panel",
    "delete_panel",
    "smart_truncate",
    "highlight_message",
    "export_progress",
    "version_banner",
    "root_banner",
    "Group",
    "Padding",
    "Text",
    "Panel",
    "Table",
    "Progress",
    "ID_DISPLAY_LEN",
    "MSG_TRUNCATE_LEN",
    "TAG_NONE",
    "VERSION",
]


# ---------------------------------------------------------------------------
# Error / warning / info helpers
# ---------------------------------------------------------------------------


def print_error(message: str) -> None:
    """Print a red error panel to STDERR.

    Args:
        message: the error text. Should not be prefixed with an icon.
    """
    body = Text()
    body.append("✘ ", style="bold red")
    body.append(message, style="red")
    err_console.print(
        Panel(body, border_style="red", title="Error", title_align="left")
    )


def print_warning(message: str) -> None:
    """Print a yellow warning to STDERR (single line, no panel)."""
    line = Text()
    line.append("⚠ ", style="bold yellow")
    line.append(message, style="yellow")
    err_console.print(line)


def print_info(message: str) -> None:
    """Print a dim info line to STDOUT with an ℹ icon."""
    line = Text()
    line.append("ℹ ", style="dim")
    line.append(message, style="dim")
    console.print(line)


# ---------------------------------------------------------------------------
# Entry panel (`devlog add` success)
# ---------------------------------------------------------------------------


def _format_dt(iso: str) -> str:
    """Convert a stored ISO 8601 UTC string to display form.

    Args:
        iso: datetime string in ``YYYY-MM-DDTHH:MM:SSZ`` format.

    Returns:
        Formatted as ``YYYY-MM-DD HH:MM UTC``.
    """
    dt = _dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt.strftime("%Y-%m-%d %H:%M UTC")


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
    title_style: str = "bold green",
    border_style: str = "green",
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
        title_style:  Rich style for the title.
        border_style: Rich style for the panel border.
        show_full_id: when True, render the full UUID as a row.
        footer_hint:  dim italic footer line (set to empty to hide).

    Returns:
        A configured ``rich.panel.Panel`` ready to print.
    """
    short_id = entry.id[:ID_DISPLAY_LEN]
    tags_display = (
        Text(", ".join(entry.tags), style="magenta")
        if entry.tags
        else Text(TAG_NONE, style="dim")
    )
    date_text = Text(_format_dt(entry.created_at), style="cyan")

    rows = [
        _styled_row("Date", date_text),
        _styled_row("Tags", tags_display),
    ]
    if show_full_id:
        rows.append(_styled_row("ID", Text(entry.id, style="dim white")))
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
    parts.append(f"[bold yellow]{escape(match_text)}[/bold yellow]")
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
    return re.sub(
        re.escape(query),
        lambda m: f"[bold yellow]{escape(m.group(0))}[/bold yellow]",
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
    return Text(", ".join(entry.tags), style="magenta")


def _terminal_width() -> int:
    """Best-effort terminal width with a sane floor and cap."""
    try:
        w = shutil.get_terminal_size((100, 20)).columns
    except (OSError, ValueError):
        w = 100
    return max(60, min(w, 160))


def _column_widths() -> dict:
    """Compute column widths so the table fits the terminal.

    The Message column gets the leftover space; if the terminal is too
    narrow we shrink tags before we ever let message wrap. Tags are
    allowed to wrap (a single tag is short, but multiple comma-separated
    tags can easily exceed 12 chars).
    """
    total = _terminal_width()
    overhead = 12  # box drawing + column padding
    # Reserve more space for Tags so a typical "frontend, backend" pair
    # (≈18 chars) fits without wrapping, but shrink it gracefully.
    tags_width = max(18, min(28, total // 5))
    msg = max(
        COL_MESSAGE_MIN,
        total - overhead - COL_ID_WIDTH - COL_DATE_WIDTH - tags_width,
    )
    return {
        "id": COL_ID_WIDTH,
        "date": COL_DATE_WIDTH,
        "tags": tags_width,
        "message": msg,
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
        row_styles=["", "dim"],
        header_style="bold",
        expand=False,
    )
    table.add_column("ID", style="dim white", no_wrap=True, width=widths["id"])
    table.add_column("Date", style="cyan", no_wrap=True, width=widths["date"])
    # Tags may wrap when many tags are present; this is preferable to
    # truncating them mid-word.
    table.add_column("Tags", style="magenta", width=widths["tags"])
    table.add_column(
        "Message",
        width=widths["message"],
        footer=f"Showing {len(entries)} of {total} entries.",
        footer_style="bold",
    )

    for entry in entries:
        if highlight_query:
            msg_cell = smart_truncate(
                entry.message, highlight_query, MSG_TRUNCATE_LEN
            )
        else:
            msg_cell = escape(_left_truncate(entry.message, MSG_TRUNCATE_LEN))
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
    full_id = Text(entry.id, style="dim white")
    tags_display = (
        Text(", ".join(entry.tags), style="magenta")
        if entry.tags
        else Text(TAG_NONE, style="dim")
    )
    created_text = Text(_format_dt(entry.created_at), style="cyan")
    updated_text = (
        Text(_format_dt(entry.updated_at), style="yellow")
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
        border_style="cyan",
        title=title,
        title_align="left",
        padding=(0, 1),
    )


def delete_panel(entry: Entry) -> Panel:
    """Render a destructive confirmation that an entry was deleted."""
    short_id = entry.id[:ID_DISPLAY_LEN]
    date_text = Text(_format_dt(entry.created_at), style="cyan")
    tags_str = ", ".join(entry.tags) if entry.tags else TAG_NONE
    tags_text = Text(tags_str, style="magenta" if entry.tags else "dim")

    body = Group(
        _styled_row("ID", Text(short_id, style="dim white")),
        _styled_row("Date", date_text),
        _styled_row("Tags", tags_text),
        Text(),
        Text(entry.message, style="strike dim"),
    )

    title = Text()
    title.append("✘ ", style="bold red")
    title.append("Entry deleted", style="bold red")
    title.append(f"  ·  {short_id}", style="dim")

    return Panel(
        body,
        border_style="red",
        title=title,
        title_align="left",
        padding=(0, 1),
    )


def edit_panel(entry: Entry) -> Panel:
    """Render a blue-bordered confirmation that an entry was edited."""
    short_id = entry.id[:ID_DISPLAY_LEN]
    date_text = Text(_format_dt(entry.created_at), style="cyan")
    updated_text = (
        Text(_format_dt(entry.updated_at), style="yellow")
        if entry.updated_at
        else Text("—", style="dim")
    )
    tags_display = (
        Text(", ".join(entry.tags), style="magenta")
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
    title.append("✎ ", style="bold blue")
    title.append("Entry updated", style="bold blue")
    title.append(f"  ·  {short_id}", style="dim")

    return Panel(
        body,
        border_style="blue",
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
    table.add_column("Tag", style="magenta", no_wrap=True)
    table.add_column("Count", style="bold", justify="right", no_wrap=True)
    table.add_column(
        "Last used",
        style="cyan",
        no_wrap=True,
        footer=f"Across {total_entries} entr{'y' if total_entries == 1 else 'ies'}.",
        footer_style="bold",
    )

    for tag, count, last_used in rows:
        last_used_text = _format_dt(last_used) if last_used else Text("—", style="dim")
        table.add_row(tag, str(count), last_used_text)

    return table


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
    """Print a styled version line followed by a dim rule."""
    console.print(Text("devlog, version ", style="bold") + Text(VERSION, style="bold cyan"))
    console.print(Rule(style="dim"))


def root_banner() -> None:
    """Print a friendly banner when ``devlog`` is invoked with no subcommand.

    Replaces the raw Click help text with a banner + 2-column command
    table and a hint about ``--help``.
    """
    console.print(
        Text("devlog", style="bold")
        + Text("  ·  a terminal-based developer journal", style="dim")
    )
    console.print(Rule(style="dim"))

    table = Table(
        box=SIMPLE,
        show_header=False,
        padding=(0, 2),
        expand=False,
    )
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(style="default")
    table.add_row("add", "Add a new journal entry")
    table.add_row("show", "Show a single entry by ID")
    table.add_row("edit", "Edit an entry's message and/or tags")
    table.add_row("delete", "Delete an entry by ID")
    table.add_row("list", "List entries, newest first")
    table.add_row("search", "Search entry messages")
    table.add_row("today", "Show today's entries")
    table.add_row("tail", "Show the N most recent entries")
    table.add_row("tags", "List tags with usage counts")
    table.add_row("stats", "Summarize the journal")
    table.add_row("rename-tag", "Rename a tag across all entries")
    table.add_row("import", "Import entries from a JSON or Markdown file")
    table.add_row("completions", "Print a shell completion script")
    table.add_row("export", "Export entries to a Markdown file")
    console.print(table)

    console.print(
        Text("Run ", style="dim")
        + Text("devlog <command> --help", style="bold")
        + Text(" for details on a specific command.", style="dim")
    )
