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
# extra room goes to Message. Set high so tags effectively get half
# the surplus space (the `half_surplus` logic), allowing all tags
# to be visible on wide terminals.
_TAGS_GROWTH_CAP = 100

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
    "success_line",
    "destructive_line",
    "dry_run_line",
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
    "command_table",
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
    # Delegate to ``pluralize`` so the pluralisation rules (notably the
    # ``"entry"`` → ``"entries"`` special case) live in one place. The
    # ``n == 1`` early return just keeps the "1 row" wording intact
    # without a redundant count prefix.
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
    """Print a dim info line to STDOUT with an ℹ icon.

    The icon is the unicode ℹ (U+2139) on UTF-8-capable terminals
    and a plain ASCII ``i`` on legacy encodings where the unicode
    glyph would render as ``?``.
    """
    line = Text()
    icon = "i" if _ellipsis_for_encoding(sys.stdout.encoding) == "..." else "ℹ "
    line.append(f"{icon} ", style=_s("info_text"))
    line.append(message, style=_s("info_text"))
    console.print(line)


# ---------------------------------------------------------------------------
# One-liner confirmation helpers (used by tag-rename/merge, import,
# export, restore, backup). Centralising them keeps the icon style,
# spacing, and surrounding Typography consistent across commands.
# ---------------------------------------------------------------------------


def _icon_line(*, icon: str, text: str, icon_role: str, text_role: str) -> Text:
    """Build a ``<icon> <text>`` single-line confirmation Text.

    Shared by the three confirmation-line builders below.

    Args:
        icon: the leading icon + space (e.g. ``"✔ "`` or ``"DRY RUN: "``).
        icon_role: theme role for the icon (resolved with ``_bold``).
        text_role: theme role for the body text (resolved with ``_s``).
    """
    line = Text()
    line.append(icon, style=_bold(icon_role))
    line.append(text, style=_s(text_role))
    return line


def success_line(text: str) -> Text:
    """Build a green ✔ confirmation line for STDOUT.

    Args:
        text: the message to print after the check icon. Pass the body
            only; the icon and spacing are added here.

    Returns:
        A :class:`rich.text.Text` ready to ``console.print(...)``. The
        caller is responsible for actually printing it, so multi-line
        compositions (e.g. with extra detail after the headline) stay
        possible.
    """
    return _icon_line(icon="✔ ", text=text, icon_role="success_title", text_role="success_text")


def destructive_line(text: str) -> Text:
    """Build a red ✘ confirmation line for STDOUT.

    Args:
        text: the message to print after the cross icon.

    Returns:
        A :class:`rich.text.Text` ready to ``console.print(...)``.
    """
    return _icon_line(icon="✘ ", text=text, icon_role="delete_border", text_role="error_text")


def dry_run_line(text: str) -> Text:
    """Build a yellow DRY RUN preview line for STDOUT.

    Args:
        text: the message to print after the ``DRY RUN:`` label.

    Returns:
        A :class:`rich.text.Text` ready to ``console.print(...)``.
    """
    return _icon_line(icon="DRY RUN: ", text=text, icon_role="warning_text", text_role="warning_text")


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


def _styled_row(label: str, value, *, label_width: int = 9) -> Text:
    """Build a single ``Label : value`` row with aligned columns.

    Args:
        label: the row label, rendered dim.
        value: a plain string OR a ``Text`` to be styled as the value.
        label_width: the ``{:<N}`` width for the label column.
            ``_stats_row_text`` (now removed) used 12 for its wider
            left column; defaults to 9 to preserve the entry-panel look.

    Returns:
        A ``Text`` ready to be added to a Group inside a Panel.
    """
    line = Text()
    line.append(f"{label:<{label_width}}", style="dim")
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
    el = _ellipsis()
    if start > 0:
        parts.append(el)
    if prefix:
        parts.append(escape(prefix))
    parts.append(f"[{_s('match_highlight')}]{escape(match_text)}[/{_s('match_highlight')}]")
    if suffix:
        parts.append(escape(suffix))
    if end < len(message):
        parts.append(el)

    return "".join(parts)


def _ellipsis() -> str:
    """Return an ellipsis character that's safe to print on the current stdout.

    The Unicode ``…`` (U+2026) is the most compact form, but on
    legacy cp1252 (default Windows console) hosts it encodes to
    ``?`` and the user sees ``message with a ?`` instead of the
    intended glyph. Picking ``...`` on those hosts keeps the output
    readable everywhere.
    """
    return _ellipsis_for_encoding(sys.stdout.encoding)


def _ellipsis_for_encoding(encoding: str | None) -> str:
    """The encoding-aware core of :func:`_ellipsis`, split out for testing."""
    if (encoding or "").lower().startswith(("utf-8", "utf-16", "utf-32")):
        return "\u2026"
    return "..."


def _left_truncate(message: str, limit: int = MSG_TRUNCATE_LEN) -> str:
    """Truncate to *limit* chars from the left, appending an ellipsis.

    The budget accounts for the *length of the ellipsis on this
    terminal*, so a 3-char ``...`` on cp1252 still fits inside
    ``limit`` chars (a 1-char ``…`` was the implicit assumption before
    we made the ellipsis encoding-aware).
    """
    el = _ellipsis()
    if len(message) <= limit:
        return message
    keep = max(0, limit - len(el))
    return message[:keep] + el


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
    """
    kwargs = {
        "border_style": _s(border_role),
        "title": title,
        "title_align": "left",
    }
    if padding is not None:
        kwargs["padding"] = padding
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
        return Text(_format_dt(entry.updated_at), style=_s("updated"))
    return Text("—", style="dim")


def _date_text(entry: Entry) -> Text:
    """Build a styled "created at" line for entry-detail panels."""
    return Text(_format_dt(entry.created_at), style=_s("date"))


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
    # The cap is bounded by ``_TAGS_GROWTH_CAP`` (the historical max)
    # and also by half the available surplus, so a tight terminal
    # with very little headroom never lets tags consume more than
    # half the room — leaving a usable message column behind.
    half_surplus = extra // 2
    cap = min(max(0, _TAGS_GROWTH_CAP - COL_TAGS_MIN), half_surplus)
    tags_extra = min(extra, cap)
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
        caption_style=_s("table_caption"),
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
        footer_style=_s("table_footer"),
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

    title = _panel_title(
        icon=None, label="Entry", short_id=short_id, role="show_border"
    )

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

    title = _panel_title(
        icon="✘", label="Entry deleted", short_id=short_id, role="delete_border"
    )

    return _themed_panel(
        body,
        border_role="delete_border",
        title=title,
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

    title = _panel_title(
        icon="✎", label="Entry updated", short_id=short_id, role="edit_border"
    )

    return _themed_panel(
        body,
        border_role="edit_border",
        title=title,
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
        footer_style=_s("table_footer"),
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

# (upper-bound ratio, character, theme role) for the four non-zero
# heatmap tiers. Tiers are inclusive on the upper bound; the sentinel
# ``1.01`` ensures anything that survives the loop gets the top tier.
_HEATMAP_TIERS: tuple[tuple[float, str, str], ...] = (
    (0.25, "·", "heatmap_l1"),
    (0.50, "▪", "heatmap_l2"),
    (0.75, "▫", "heatmap_l3"),
    (1.01, "█", "heatmap_l4"),
)


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

    A 3-character gutter on the left of each row carries the
    short month name (``Jan``, ``Feb`` …) on the *first* week-column
    that contains at least one day of that month. Subsequent rows
    for the same week are blank, so the month label never repeats
    and the gutter stays narrow on a 7-row grid.

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
        ratio = count / max_count
        for threshold, char, role in _HEATMAP_TIERS:
            if ratio <= threshold:
                return char, role
        # Unreachable given the 1.01 sentinel, but keep a sane default.
        return "█", "heatmap_l4"

    # The display is laid out Sunday-first, so the row index is
    # ``(d.weekday() - SUNDAY) % 7`` (Python's ``date.weekday()`` is
    # Monday-first, 0=Mon). The loop index below happens to iterate in
    # the same order (0..6 = Sun..Sat), so the row index matches the
    # loop variable ``row``. Gutter labels are placed inline below
    # (see the rendering loop), so no separate ``gutter_per_row`` map
    # is needed.

    grid = Text()
    # The display is a 7-row × N-column heatmap, where N is
    # ``num_weeks``. Each weekday row is rendered as a single
    # data line (one heatmap char per week-column), with an
    # optional gutter line above it that carries 3-letter month
    # labels for the first week of each month. The gutter is
    # padded to ``num_weeks`` chars so the labels align with
    # the cells they describe. (This is the same layout
    # GitHub's contribution graph uses; the 3-letter label
    # may extend over neighbouring cells, but the alignment
    # stays correct because the cell char for the 1st of the
    # month is still drawn in the same column.)

    # Per-weekday row, a list of (week_col, month_label) pairs. Each
    # weekday row can have multiple labels (e.g. Feb 1 and Mar 1 are
    # both Sundays, so the Sunday row carries both "Feb" and "Mar").
    last_month = -1
    label_cols: list[list[tuple[int, str]]] = [[] for _ in range(7)]
    for weekday in range(7):
        for week in range(num_weeks):
            day_offset = week * 7 + weekday - first_col_pad
            if day_offset < 0 or day_offset >= total_days:
                continue
            d = jan1 + _dt.timedelta(days=day_offset)
            if d.day == 1 and d.month != last_month:
                label_cols[weekday].append((week, d.strftime("%b")))
                last_month = d.month

    # Build the gutter and data lines per weekday. The gutter is
    # padded to ``num_weeks`` chars so the panel can render both
    # lines with the same width. Multiple labels on the same row are
    # written into a shared char array; later writes win, which keeps
    # the visual order monotonic.
    for weekday in range(7):
        if label_cols[weekday]:
            chars = list(" " * num_weeks)
            for col, label in label_cols[weekday]:
                for i, ch in enumerate(label):
                    if col + i < num_weeks:
                        chars[col + i] = ch
            grid.append("".join(chars), style="dim")
            grid.append("\n")
        # Data line.
        for week in range(num_weeks):
            day_offset = week * 7 + weekday - first_col_pad
            if day_offset < 0 or day_offset >= total_days:
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
    # Build the "less  · ▪ ▫ █  more" legend from the same data
    # table the heatmap uses, so changing a threshold or character
    # only requires editing one place.
    legend = Text()
    legend.append("less ", style="dim")
    for _i, (_, char, role) in enumerate(_HEATMAP_TIERS):
        if _i:
            legend.append(" ", style="dim")
        legend.append(char, style=_s(role))
    legend.append(" more", style="dim")
    body_rows.append(legend)
    body_rows.append(
        Text(
            f"{active_days} active day{plural_s(active_days)} · "
            f"{total} {_plural_noun(total, 'entry')} in {year}",
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
            rows.append(_styled_row(f"  {tag}", str(count), label_width=12))
    else:
        rows.append(Text("No tags yet.", style="dim"))

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


def command_table(commands: Sequence[tuple[str, str]]) -> Table:
    """Build a 2-column table of command names and descriptions.

    Used by both the root banner and the REPL help.
    """
    table = Table(
        box=SIMPLE,
        show_header=False,
        padding=(0, 2),
        expand=False,
    )
    table.add_column(style=_s("banner_command"), no_wrap=True)
    table.add_column(style="default")
    for name, desc in commands:
        table.add_row(name, desc)
    return table


def root_banner(commands: Sequence[tuple[str, str]] | None = None) -> None:
    """Print a friendly banner when ``devlog`` is invoked with no subcommand.

    Replaces the raw Click help text with a banner + 2-column command
    table and a hint about ``--help``.

    Args:
        commands: optional list of ``(name, description)`` tuples. When
            omitted, falls back to ``devlog.cli.COMMANDS`` so the
            single source of truth lives in one place. The fallback is
            lazy-imported to avoid a circular dependency at module load.
    """
    if commands is None:
        from devlog.cli import COMMANDS as _COMMANDS
        commands = _COMMANDS

    console.print(
        Text("devlog", style="bold")
        + Text("  ·  a terminal-based developer journal", style="dim")
    )

    console.print(command_table(commands))

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
        rows.append(
            Text(
                f"Removed {dropped} {_plural_noun(dropped, 'entry')}, kept {kept}.",
                style=_s("success_border"),
            )
        )
    if backup_path:
        rows.append(Text(f"Backup written to {backup_path}", style="dim"))

    title = _panel_title(icon="🔧", label="Repair · devlog store", role="info_text")

    return _themed_panel(
        Group(*rows),
        border_role="info_text",
        title=title,
    )


# ---------------------------------------------------------------------------
# Backup result (`devlog backup`)
# ---------------------------------------------------------------------------


def backup_result(path: str, count: int) -> Text:
    """Build a one-line confirmation for a successful backup."""
    return success_line(
        f"Backed up {count} {_plural_noun(count, 'entry')} to "
    ).append(path, style="bold")


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
        rows.extend(
            _capped_enumerate(
                issues,
                5,
                lambda issue: Text()
                .append("    • ", style="dim")
                .append(f"[{issue.get('kind', 'issue')}] ", style=_s("warning_text"))
                .append(f"{(issue.get('entry_id') or '—')[:8] + '…' if issue.get('entry_id') and len(issue.get('entry_id')) > 8 else (issue.get('entry_id') or '—')} ", style=_s("id_dim"))
                .append(issue.get("message", ""), style="dim"),
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
        icon="🩺",
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
