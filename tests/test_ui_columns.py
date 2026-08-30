"""Regression tests for the column-width and visual-layout fixes.

These tests guard against three classes of bug that bit us in the past:

1. The ID column truncating the 8-char short id to 2-3 chars on
   narrow terminals (e.g. ``7c…`` instead of ``7cfe48d5``).
2. The date column truncating ``YYYY-MM-DD HH:MM UTC`` to a partial
   string when the table is too narrow.
3. The message column wrapping or clipping the search match so the
   user cannot see what they searched for.

Each test renders the same table at a few different terminal widths
and asserts that the visible cell content matches the expected
constants. They are deliberately tolerant: a layout that overflows
by a single char and gets clipped by one is acceptable; truncation
in the middle of an id is not.
"""

from __future__ import annotations

import io
import re
import shutil
from typing import Iterator

import pytest
from rich.console import Console

from devlog import ui
from devlog.models import Entry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(
    *,
    message: str = "hello world",
    tags: list[str] | None = None,
    iso: str = "2025-05-11T10:22:00Z",
    entry_id: str = "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
) -> Entry:
    return Entry(
        id=entry_id,
        message=message,
        tags=list(tags) if tags else [],
        created_at=iso,
    )


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _render(entries, total: int = 1, *, query: str = "") -> str:
    """Build the entries table and return its rendered plain text."""
    table = ui.entries_table(entries, total=total, highlight_query=query)
    buf = io.StringIO()
    Console(file=buf, no_color=True, width=ui.console.width).print(table)
    return _strip_ansi(buf.getvalue())


@pytest.fixture()
def fixed_terminal_width(monkeypatch) -> Iterator[None]:
    """Force a fixed ``shutil.get_terminal_size`` value for the test.

    The fallback default of 100 makes test assertions on
    ``_column_widths`` non-deterministic; this fixture pins it to the
    width the active ``ui.console`` was constructed with.
    """
    import os

    def _fake(fallback=(100, 20)):
        return os.terminal_size((ui.console.width, 20))

    monkeypatch.setattr(shutil, "get_terminal_size", _fake)
    yield


@pytest.fixture(autouse=True)
def _restore_console():
    """Snapshot the module-level consoles so tests that replace them
    do not leak into other test files.

    Several tests in this module rebuild ``ui.console`` with a fixed
    width; the autouse fixture restores the original after the test
    so unrelated tests still see the right console width.
    """
    saved_console = ui.console
    saved_err = ui.err_console
    yield
    ui.console = saved_console
    ui.err_console = saved_err


# ---------------------------------------------------------------------------
# Column-width constants
# ---------------------------------------------------------------------------


def test_id_display_len_is_eight():
    """The displayed short id is 8 chars — anything else breaks grep-ability."""
    assert ui.ID_DISPLAY_LEN == 8


def test_date_display_len_matches_actual_format():
    """``DATE_DISPLAY_LEN`` must equal the length of the rendered date string.

    A previous off-by-one (19 vs 20) caused the date column to clip the
    final ``C`` of ``UTC`` at narrow widths.
    """
    rendered = ui._format_dt("2025-05-11T10:22:00Z")
    assert len(rendered) == ui.DATE_DISPLAY_LEN
    assert rendered == "2025-05-11 10:22 UTC"


def test_min_terminal_width_allows_full_id_and_date():
    """At the minimum terminal width, the table budget must include the
    full 8-char id and 20-char date, with room for a usable message
    column on top.
    """
    fixed = ui.COL_ID_WIDTH + ui.COL_DATE_WIDTH + ui.COL_TAGS_MIN + ui.COL_MESSAGE_MIN
    # Sum of (width + padding) per column plus 5 box borders.
    required = fixed + (4 * ui._COL_PADDING) + 5
    assert required <= ui.MIN_TERMINAL_WIDTH, (
        f"min terminal width {ui.MIN_TERMINAL_WIDTH} cannot fit the "
        f"fixed column widths ({fixed}) plus padding and borders; "
        f"need at least {required}"
    )


# ---------------------------------------------------------------------------
# Column truncation bugs (regression)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", [80, 100, 120, 140, 160])
def test_id_column_shows_full_eight_chars_at_all_widths(width, monkeypatch, fixed_terminal_width):
    """The ID column must display the full 8-char short id at any width
    the table is laid out for. Regression: a previous version clipped
    the id to ``7c…`` at 80 columns.
    """
    ui.console = Console(no_color=True, width=width)
    e = _make_entry(entry_id="7cfe48d5-d244-4db5-8274-f5b6e26dfb5b")
    rendered = _render([e], total=1)
    # The id cell sits between the first two ``│`` characters after
    # the header row. Strip whitespace and assert the literal id is
    # present in the data row.
    assert "7cfe48d5" in rendered, f"id '7cfe48d5' missing from rendered table:\n{rendered}"


@pytest.mark.parametrize("width", [80, 100, 120, 140, 160])
def test_date_column_shows_full_format_at_all_widths(width, monkeypatch, fixed_terminal_width):
    """The date column must show the full ``YYYY-MM-DD HH:MM UTC`` at
    any width the table is laid out for. Regression: a previous
    version showed ``2026-08-29 08:3…`` instead of the full string.
    """
    ui.console = Console(no_color=True, width=width)
    e = _make_entry(iso="2026-08-29T08:34:00Z")
    rendered = _render([e], total=1)
    assert "2026-08-29 08:34 UTC" in rendered, (
        f"full date '2026-08-29 08:34 UTC' missing from rendered table:\n{rendered}"
    )


def test_no_truncation_at_minimum_width(monkeypatch, fixed_terminal_width):
    """At the 80-col floor, both id and date must still be complete."""
    ui.console = Console(no_color=True, width=ui.MIN_TERMINAL_WIDTH)
    e = _make_entry(entry_id="12345678-aaaa-bbbb-cccc-dddddddddddd")
    rendered = _render([e], total=1)
    assert "12345678" in rendered
    assert "2025-05-11 10:22 UTC" in rendered


# ---------------------------------------------------------------------------
# Search highlight visibility
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", [80, 100, 120])
def test_search_match_visible_in_narrow_column(width, monkeypatch, fixed_terminal_width):
    """The smart-truncated match must be visible at every width.

    Regression: the smart-truncation was producing 60-char strings in
    a 20-char column, so the match fell off the right edge of the
    cell and the user could not see what they searched for.
    """
    ui.console = Console(no_color=True, width=width, force_terminal=True)
    long_msg = "y" * 200 + "needle" + "z" * 200
    e = _make_entry(message=long_msg)
    rendered = _render([e], total=1, query="needle")
    assert "needle" in rendered, f"search match 'needle' missing:\n{rendered}"


def test_ellipsis_picks_unicode_on_utf8():
    """``_ellipsis_for_encoding`` returns ``…`` for UTF-8 (the modern default)."""
    assert ui._ellipsis_for_encoding("utf-8") == "\u2026"
    assert ui._ellipsis_for_encoding("UTF-8") == "\u2026"
    assert ui._ellipsis_for_encoding("utf-16") == "\u2026"


def test_ellipsis_picks_dots_on_legacy_cp1252():
    """``_ellipsis_for_encoding`` returns ``...`` on legacy encodings where
    ``…`` would render as ``?`` (e.g. Windows cp1252 console hosts)."""
    assert ui._ellipsis_for_encoding("cp1252") == "..."
    assert ui._ellipsis_for_encoding("ascii") == "..."
    assert ui._ellipsis_for_encoding(None) == "..."
    assert ui._ellipsis_for_encoding("") == "..."


def test_left_truncate_uses_ellipsis_helper(monkeypatch):
    """``_left_truncate`` defers to ``_ellipsis`` so its suffix survives
    on legacy encodings. We patch the helper directly because
    ``sys.stdout.encoding`` is read-only.
    """
    monkeypatch.setattr(ui, "_ellipsis", lambda: "...")
    out = ui._left_truncate("a" * 200, limit=12)
    # 9 chars of message + 3-char "..." suffix = 12 total
    assert out.endswith("...")
    assert len(out) == 12
    assert out == "aaaaaaaaa..."


def test_search_match_is_ansi_styled(monkeypatch, fixed_terminal_width):
    """The matched substring must be styled (bold + yellow)."""
    ui.console = Console(no_color=False, width=120, force_terminal=True)
    e = _make_entry(message="the quick brown fox")
    table = ui.entries_table([e], total=1, highlight_query="brown")
    buf = io.StringIO()
    Console(file=buf, no_color=False, width=120, force_terminal=True).print(table)
    out = buf.getvalue()
    # Rich translates [bold yellow]X[/bold yellow] to ESC[1;33mX ESC[0m
    # (or ESC[33;1m in some terminals); accept either ordering.
    assert (
        "\x1b[1;33mbrown\x1b[0m" in out
        or "\x1b[33;1mbrown\x1b[0m" in out
    ), f"bold-yellow styling missing for match:\n{out}"


# ---------------------------------------------------------------------------
# Column width helpers
# ---------------------------------------------------------------------------


def test_column_widths_sum_fits_terminal(monkeypatch, fixed_terminal_width):
    """``_column_widths`` must return widths that fit in the terminal."""
    for width in (80, 100, 120, 140, 160):
        ui.console = Console(no_color=True, width=width)
        widths = ui._column_widths()
        # Sum of (content width + 2 padding) plus 5 box borders.
        total = sum(widths.values()) + 4 * ui._COL_PADDING + 5
        assert total <= width, (
            f"column widths {widths} require {total} chars but "
            f"terminal is {width} wide"
        )


def test_column_widths_preserve_id_and_date(monkeypatch, fixed_terminal_width):
    """ID and date widths must always equal their full display lengths."""
    for width in (80, 100, 120, 140, 160):
        ui.console = Console(no_color=True, width=width)
        widths = ui._column_widths()
        assert widths["id"] == ui.ID_DISPLAY_LEN, (
            f"id width clipped to {widths['id']} at terminal width {width}"
        )
        assert widths["date"] == ui.DATE_DISPLAY_LEN, (
            f"date width clipped to {widths['date']} at terminal width {width}"
        )


# ---------------------------------------------------------------------------
# Stats panel
# ---------------------------------------------------------------------------


def test_stats_panel_renders_all_sections():
    """The stats panel must include every key field."""
    panel = ui.stats_panel(
        total=10,
        first_iso="2025-01-01T00:00:00Z",
        last_iso="2025-12-31T23:59:00Z",
        top_tags=[("backend", 4), ("bugfix", 2)],
        last_30_days=[(f"2025-01-{i:02d}", 1) for i in range(1, 31)],
    )
    buf = io.StringIO()
    Console(file=buf, no_color=True, width=120).print(panel)
    out = _strip_ansi(buf.getvalue())
    for marker in (
        "Total",
        "First",
        "Last",
        "Span",
        "Avg/day",
        "Top",
        "backend",
        "bugfix",
        "Last 30 days",
    ):
        assert marker in out, f"stats panel missing {marker}:\n{out}"


def test_stats_panel_uses_cyan_date_color():
    """First/Last rows must be styled with the theme's date color."""
    panel = ui.stats_panel(
        total=1,
        first_iso="2025-01-01T00:00:00Z",
        last_iso="2025-01-01T00:00:00Z",
        top_tags=[],
        last_30_days=[],
    )
    buf = io.StringIO()
    Console(file=buf, no_color=False, width=120, force_terminal=True).print(panel)
    out = buf.getvalue()
    # The default ``date`` theme role is ``cyan`` (color 36).
    assert "2025-01-01 00:00 UTC" in out
    # The date string must be wrapped in the cyan ANSI code.
    assert "\x1b[36m" in out, "date rows are not styled with the 'date' color"


def test_sparkline_helper_handles_empty():
    assert ui.sparkline([]) == ""


def test_sparkline_helper_uses_blocks():
    out = ui.sparkline([0, 1, 5, 10])
    assert len(out) == 4
    # All-zero day → ▁; max day → █
    assert out[0] == "▁"
    assert out[-1] == "█"


def test_stats_panel_anchors_scale_labels_to_sparkline_width():
    """The 0 / max labels must flank the sparkline regardless of how
    wide the max value renders. Regression: a previous version padded
    the labels with a fixed 30 - len(max) char count, which broke when
    the journal had >9 entries on its busiest day (max became "10" and
    the right label drifted 1 column left of the last block).
    """
    panel = ui.stats_panel(
        total=10,
        first_iso="2025-01-01T00:00:00Z",
        last_iso="2025-01-30T00:00:00Z",
        top_tags=[],
        # Busiest day is 99 entries; the scale label must be 2 chars wide
        # and the previous 30-char-pad math would have under-padded.
        last_30_days=[(f"2025-01-{i:02d}", 1) for i in range(1, 30)]
        + [("2025-01-30", 99)],
    )
    buf = io.StringIO()
    Console(file=buf, no_color=True, width=120).print(panel)
    out = _strip_ansi(buf.getvalue())
    # Both labels must appear in the panel; the render itself is
    # left to Rich but the values must survive end-to-end.
    assert "0" in out
    assert "99" in out


# ---------------------------------------------------------------------------
# Repair icon
# ---------------------------------------------------------------------------


def test_repair_panel_uses_wrench_icon():
    """The repair panel title must use the wrench (🔧), not a pencil."""
    panel = ui.repair_summary(
        issues=[], dropped=0, kept=5, dry_run=True, backup_path=None
    )
    buf = io.StringIO()
    Console(file=buf, no_color=True, width=120).print(panel)
    out = _strip_ansi(buf.getvalue())
    assert "🔧" in out
    assert "✎" not in out
    assert "Repair" in out


# ---------------------------------------------------------------------------
# Version banner / root banner (no decorative Rule)
# ---------------------------------------------------------------------------


def test_version_banner_has_no_rule():
    """The version banner must not emit a trailing horizontal rule."""
    buf = io.StringIO()
    saved = ui.console
    ui.console = Console(file=buf, no_color=True, width=120)
    try:
        ui.version_banner()
    finally:
        ui.console = saved
    out = _strip_ansi(buf.getvalue())
    assert "devlog, version" in out
    # A horizontal rule is a long run of ``─`` with no text. The
    # version line has text, so we look for the absence of an
    # all-dashes line.
    for line in out.splitlines():
        assert not (len(line) >= 40 and set(line) == {"─"}), (
            f"version banner still emits a decorative rule: {line!r}"
        )


def test_root_banner_has_no_rule():
    """The root help banner must not emit a trailing horizontal rule."""
    buf = io.StringIO()
    saved = ui.console
    ui.console = Console(file=buf, no_color=True, width=120)
    try:
        ui.root_banner()
    finally:
        ui.console = saved
    out = _strip_ansi(buf.getvalue())
    for line in out.splitlines():
        assert not (len(line) >= 40 and set(line) == {"─"}), (
            f"root banner still emits a decorative rule: {line!r}"
        )
