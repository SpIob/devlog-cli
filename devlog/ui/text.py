"""devlog.ui.text — Text-level helpers: plurals, error/warning/info lines, truncation
and query highlighting.
"""

from __future__ import annotations

import re
import sys

from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

from devlog import _iso
from devlog.models import Entry
from devlog.ui._base import (
    ID_DISPLAY_LEN,
    MSG_TRUNCATE_LEN,
    TAG_NONE,
    _bold,
    _s,
    console,
    err_console,
)

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


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
    return f"{n} {plural_noun(n, singular, plural)}"


def plural_noun(n: int, singular: str, plural: str | None = None) -> str:
    """Like :func:`pluralize` but without the count prefix.

    Useful inside template strings that already include the count, e.g.
    ``f"{n} of {total} {plural_noun(total, 'entry')}"``.

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
    """Print a yellow warning to STDERR in a slim bordered box.

    The box mirrors the visual grammar of :func:`print_error` (a
    border-styled container) and :func:`print_info` (a leading icon
    + body) so the three states read as a single family. Errors are
    loud red panels, warnings are a quieter bordered line, info is
    an unbordered dim line — three weights, one visual language.
    """
    line = Text()
    line.append("⚠ ", style=_bold("warning_text"))
    line.append(message, style=_s("warning_text"))
    err_console.print(
        Panel(
            line,
            border_style=_s("warning_text"),
            padding=(0, 1),
        )
    )


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
    return _icon_line(
        icon="DRY RUN: ", text=text, icon_role="warning_text", text_role="warning_text"
    )


# ---------------------------------------------------------------------------
# Entry panel (`devlog add` success)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Entry panel (`devlog add` success)
# ---------------------------------------------------------------------------


def format_dt(iso: str, *, tz=None, tz_label: str = "UTC") -> str:
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
    dt = _iso.try_parse_utc_iso(iso)
    if dt is None:
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


# ---------------------------------------------------------------------------
# Truncation & highlighting (`devlog list` / `devlog search`)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Truncation & highlighting (`devlog list` / `devlog search`)
# ---------------------------------------------------------------------------


def smart_truncate(message: str, query: str = "", limit: int = MSG_TRUNCATE_LEN) -> str:
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
    # Lazy import so tests can monkeypatch :data:`devlog.ui._ellipsis`.
    from devlog import ui

    el = ui._ellipsis()
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


# ---------------------------------------------------------------------------
# Entries table
# ---------------------------------------------------------------------------


def _short_id(entry: Entry, length: int | None = None) -> str:
    """Return the entry's short id, optionally truncated to *length* chars.

    The default uses :data:`ID_DISPLAY_LEN` (8) for the full short id.
    Pass a smaller *length* on very-narrow terminals where the id
    column is too narrow to fit the full 8 chars.
    """
    n = ID_DISPLAY_LEN if length is None else length
    return entry.id[:n]


def _tags_text(entry: Entry) -> Text:
    if not entry.tags:
        return Text(TAG_NONE, style="dim")
    return Text(", ".join(entry.tags), style=_s("tags"))
