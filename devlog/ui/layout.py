"""devlog.ui.layout — Terminal-width arithmetic and the table renderers.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping, Sequence

from rich.box import ROUNDED, SIMPLE
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from devlog.models import Entry
from devlog.ui._base import (
    _COL_PADDING,
    _NARROW_TABLE_MIN_WIDTH,
    _TAGS_GROWTH_CAP,
    COL_DATE_WIDTH,
    COL_ID_WIDTH,
    COL_MESSAGE_MIN,
    COL_TAGS_MIN,
    MAX_TERMINAL_WIDTH,
    MIN_TERMINAL_WIDTH,
    MSG_TRUNCATE_LEN,
    _s,
    console,
    err_console,
    themes,
)
from devlog.ui.text import (
    _left_truncate,
    _short_id,
    _tags_text,
    format_dt,
    plural_noun,
    smart_truncate,
)


def _raw_terminal_width() -> int:
    """Return the *actual* terminal width with no floor or cap.

    Split out from :func:`_terminal_width` so the narrow-terminal
    detection in :func:`_is_narrow_terminal` can see the true width
    before the layout helper floors it at :data:`MIN_TERMINAL_WIDTH`.
    Returns 0 when the width is unknown (e.g. captured stdout with no
    declared width) so callers can treat that as "not narrow".
    """
    try:
        w = console.width
        if w and w > 0:
            return w
    except (AttributeError, RuntimeError):
        pass
    try:
        return shutil.get_terminal_size((100, 20)).columns
    except (OSError, ValueError):
        return 0


def _is_narrow_terminal() -> bool:
    """True when the active terminal is too small for the full table.

    The full 4-column table (ID + Date + Tags + Message) needs at least
    :data:`MIN_TERMINAL_WIDTH` (80) cols to render without truncating
    the 8-char short id. When the terminal is narrower than that, the
    entries table drops the Tags column and prints a one-line warning
    so the user understands why.
    """
    w = _raw_terminal_width()
    return 0 < w < MIN_TERMINAL_WIDTH


def _terminal_width() -> int:
    """Best-effort terminal width with a sane floor and cap.

    Reads from the active Rich ``console`` first, falling back to
    :func:`shutil.get_terminal_size` for the rare case where the
    console is queried before being initialised. The floor is
    :data:`MIN_TERMINAL_WIDTH` (80) so the table never tries to fit
    into a window too small to show the full 8-char short ID +
    19-char date + a usable message column.
    """
    w = _raw_terminal_width() or 100
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

    Note:
        When the terminal is narrower than :data:`MIN_TERMINAL_WIDTH`
        (80 cols) the Tags column is dropped so the 8-char short id
        and the 20-char date never get clipped, and a one-line warning
        is written to STDERR so the user knows why Tags are missing.
        On terminals narrower than :data:`_NARROW_TABLE_MIN_WIDTH`
        (65 cols) the date is shortened to ``YYYY-MM-DD HH:MM`` and
        the id to 6 chars so the table still fits.
    """
    widths = _column_widths()
    narrow = _is_narrow_terminal()
    very_narrow = False

    if narrow:
        # Build a one-line warning on STDERR. We print here (instead of
        # returning a flag) because the table is the *primary* surface
        # — the user shouldn't have to scroll past our warning to read
        # the rows. The warning goes to STDERR so it doesn't pollute
        # any piped output.
        err_console.print(
            f"[{_s('warning_text')}]⚠ Terminal is narrower than "
            f"{MIN_TERMINAL_WIDTH} cols; hiding the Tags column. "
            f"Widen the window to see tags.[/{_s('warning_text')}]"
        )
        # Drop the tags column and give the freed width to Message so
        # the table still uses the available space. Recompute as a
        # 3-column layout; box overhead drops from 5 to 4 separators.
        raw_w = _raw_terminal_width()
        if raw_w < _NARROW_TABLE_MIN_WIDTH:
            # Below the 3-column budget the table can't render the full
            # short id + full date + 27-char message. Use a shorter
            # date (no seconds, no zone) and a 6-char id so the table
            # still fits without Rich hiding any cells. The
            # ``very_narrow`` flag flips the date/id rendering.
            very_narrow = True
            id_w = 6
            date_w = 16  # "YYYY-MM-DD HH:MM"
        else:
            id_w = COL_ID_WIDTH
            date_w = COL_DATE_WIDTH
        col_budget = raw_w - 4
        msg_w = max(
            COL_MESSAGE_MIN,
            col_budget - id_w - date_w - (3 * _COL_PADDING),
        )
        widths = {
            "id": id_w,
            "date": date_w,
            "message": msg_w,
        }

    table = Table(
        box=ROUNDED,
        show_footer=True,
        show_lines=narrow,  # only on narrow to keep wide tables compact
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
    if not narrow:
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
        footer=f"Showing {len(entries)} of {total} {plural_noun(total, 'entry')}.",
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
        id_cell = _short_id(entry, length=6 if very_narrow else None)
        if very_narrow:
            # Strip the trailing "UTC" so the 16-char date format fits.
            date_cell = format_dt(entry.created_at)[:16]
        else:
            date_cell = format_dt(entry.created_at)
        if narrow:
            table.add_row(
                id_cell,
                date_cell,
                msg_cell,
            )
        else:
            table.add_row(
                _short_id(entry),
                format_dt(entry.created_at),
                _tags_text(entry),
                msg_cell,
            )

    return table


# ---------------------------------------------------------------------------
# Tags table (`devlog tags`)
# ---------------------------------------------------------------------------


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
        footer=f"Across {total_entries} {plural_noun(total_entries, 'entry')}.",
        footer_style=_s("table_footer"),
    )

    for tag, count, last_used in rows:
        last_used_text = format_dt(last_used) if last_used else Text("—", style="dim")
        table.add_row(tag, str(count), last_used_text)

    return table


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


# ---------------------------------------------------------------------------
# Theme table (`devlog theme list` / `devlog theme show`)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Theme table (`devlog theme list` / `devlog theme show`)
# ---------------------------------------------------------------------------

def theme_table(
    palette: Mapping[str, str] | None = None,
    *,
    title: str = "Active theme",
    show_source: bool = True,
    show_preview: bool = True,
    grouped: bool = True,
) -> Table:
    """Render a multi-column table of role → style mappings.

    Used by the ``devlog theme list`` subcommand. When *palette* is
    omitted, the active theme is rendered. Roles are shown in
    :data:`devlog.themes.ROLES` order so the output is stable across
    runs and easy to diff.

    Args:
        palette: a ``{role: style}`` mapping. Defaults to the active
            theme from :mod:`devlog.themes`.
        title: the table title (default: ``"Active theme"``).
        show_source: when True, render a footer row with the resolved
            theme path and current on-disk status. Off by default for
            programmatic callers that build their own footer.
        show_preview: when True, render a third "Preview" column with
            a 2-char colour swatch per role. Disable for diff-friendly
            plain text output.
        grouped: when True, insert a section subheader between each
            group defined in :data:`devlog.themes.SECTIONS`. Disable
            for a flat role list.

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
    if show_preview:
        table.add_column("Preview", no_wrap=True)

    if grouped:
        for section_name, roles in themes.SECTIONS.items():
            present = [r for r in roles if r in palette]
            if not present:
                continue
            table.add_row(
                Text(section_name, style="bold"),
                Text(""),
                *([Text("")] if show_preview else ()),
                end_section=True,
            )
            for role in present:
                if show_preview:
                    table.add_row(
                        role,
                        palette[role],
                        Text("██", style=palette[role]),
                    )
                else:
                    table.add_row(role, palette[role])
    else:
        for role in sorted(themes.ROLES):
            if show_preview:
                table.add_row(
                    role,
                    palette[role],
                    Text("██", style=palette[role]),
                )
            else:
                table.add_row(role, palette[role])

    if show_source:
        status = themes.get_theme_status()
        path = themes.get_theme_path()
        footer = f"path: {path}   status: {status}"
        style = "yellow" if status.startswith("error") else "dim"
        if show_preview:
            table.add_row(
                Text(footer, style=style),
                Text(""),
                Text(""),
                end_section=True,
            )
        else:
            table.add_row(
                Text(footer, style=style),
                Text(""),
                end_section=True,
            )

    return table
