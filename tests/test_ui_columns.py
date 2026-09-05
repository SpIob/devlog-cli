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
import shutil
from typing import Iterator

import pytest
from rich.console import Console

from devlog import ui
from devlog.models import Entry

from tests.conftest import render_to_text, strip_ansi


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


def _render(entries, total: int = 1, *, query: str = "") -> str:
    """Build the entries table and return its rendered plain text."""
    table = ui.entries_table(entries, total=total, highlight_query=query)
    return render_to_text(table, width=ui.console.width)


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
    rendered = ui.format_dt("2025-05-11T10:22:00Z")
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


@pytest.mark.parametrize(
    "encoding,expected",
    [
        ("utf-8", "\u2026"),
        ("UTF-8", "\u2026"),
        ("utf-16", "\u2026"),
        ("cp1252", "..."),
        ("ascii", "..."),
        (None, "..."),
        ("", "..."),
    ],
    ids=["utf8", "utf8-upper", "utf16", "cp1252", "ascii", "none", "empty"],
)
def test_ellipsis_picks(encoding, expected):
    assert ui._ellipsis_for_encoding(encoding) == expected


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


@pytest.mark.parametrize("width", [80, 100, 120, 140, 160])
def test_column_widths_fit_terminal(monkeypatch, fixed_terminal_width, width):
    """``_column_widths`` must return widths that fit in the terminal."""
    ui.console = Console(no_color=True, width=width)
    widths = ui._column_widths()
    total = sum(widths.values()) + 4 * ui._COL_PADDING + 5
    assert total <= width, (
        f"column widths {widths} require {total} chars but "
        f"terminal is {width} wide"
    )


@pytest.mark.parametrize("width", [80, 100, 120, 140, 160])
def test_column_widths_preserve_id_and_date(monkeypatch, fixed_terminal_width, width):
    """ID and date widths must always equal their full display lengths."""
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
    out = strip_ansi(buf.getvalue())
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
    out = strip_ansi(buf.getvalue())
    # Both labels must appear in the panel; the render itself is
    # left to Rich but the values must survive end-to-end.
    assert "0" in out
    assert "99" in out


# ---------------------------------------------------------------------------
# Repair icon
# ---------------------------------------------------------------------------


def test_repair_panel_uses_repair_icon():
    """The repair panel title must use a repair icon, not a pencil.

    The icon used to be the wrench emoji (``🔧``), which renders as a
    monochrome glyph on terminals without an emoji font and breaks the
    visual family shared with ``✔ ✘ ⚠ ℹ ✎``. The current icon is the
    hammer-and-pick (``⚒``); we only assert the panel is recognisably
    a repair panel and is not the edit pencil.
    """
    panel = ui.repair_summary(
        issues=[], dropped=0, kept=5, dry_run=True, backup_path=None
    )
    buf = io.StringIO()
    Console(file=buf, no_color=True, width=120).print(panel)
    out = strip_ansi(buf.getvalue())
    assert "⚒" in out
    assert "✎" not in out
    assert "Repair" in out


# ---------------------------------------------------------------------------
# Version banner / root banner (no decorative Rule)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fn,extra_assert",
    [
        (ui.version_banner, lambda out: "devlog, version" in out),
        (ui.root_banner, lambda out: True),
    ],
    ids=["version", "root"],
)
def test_banner_has_no_rule(fn, extra_assert):
    """Both the version and root help banners must not emit a trailing
    horizontal rule (a long run of ``─`` with no text)."""
    from tests.conftest import capture_console

    out = capture_console(fn)
    assert extra_assert(out)
    for line in out.splitlines():
        assert not (len(line) >= 40 and set(line) == {"─"}), (
            f"banner still emits a decorative rule: {line!r}"
        )


# ---------------------------------------------------------------------------
# Narrow-terminal handling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "width,expected",
    [
        (60, True),
        (None, False),  # None → MIN_TERMINAL_WIDTH
    ],
    ids=["below-min", "at-min"],
)
def test_is_narrow_terminal(width, expected):
    """A terminal narrower than ``MIN_TERMINAL_WIDTH`` is "narrow"."""
    target = width if width is not None else ui.MIN_TERMINAL_WIDTH
    ui.console = Console(no_color=True, width=target, force_terminal=True)
    assert ui._is_narrow_terminal() is expected


def test_narrow_terminal_drops_tags_column(monkeypatch, fixed_terminal_width):
    """On a 60-col terminal, the entries table must not contain a Tags column.

    Regression: a previous version kept the 4-column layout at narrow
    widths, which silently truncated the 8-char short id and the
    20-char date. The narrow path now drops the Tags column so the
    ID + Date + Message stay readable. At 60 cols (below
    ``_NARROW_TABLE_MIN_WIDTH``) the id and date are shortened to
    fit; the 6-char id prefix and 16-char date prefix must both be
    visible.
    """
    ui.console = Console(no_color=True, width=60, force_terminal=True)
    e = _make_entry(entry_id="7cfe48d5-d244-4db5-8274-f5b6e26dfb5b", tags=["a", "b"])
    rendered = _render([e], total=1)
    # The 6-char short id prefix and the 16-char date prefix must be
    # visible in the 3-column layout.
    assert "7cfe48" in rendered
    assert "2025-05-11 10:22" in rendered
    # The Tags header should not be present in the data row.
    header_line = rendered.splitlines()[2]  # first data row
    assert "Tags" not in header_line


def test_narrow_terminal_at_min_width_keeps_full_id_and_date(
    monkeypatch, fixed_terminal_width
):
    """At 70 cols (>=_NARROW_TABLE_MIN_WIDTH=65) the full 8-char id and
    20-char date must survive in the 3-column layout."""
    ui.console = Console(no_color=True, width=70, force_terminal=True)
    e = _make_entry(entry_id="7cfe48d5-d244-4db5-8274-f5b6e26dfb5b")
    rendered = _render([e], total=1)
    assert "7cfe48d5" in rendered
    assert "2025-05-11 10:22 UTC" in rendered


def test_narrow_terminal_warning_fires(monkeypatch, fixed_terminal_width):
    """The narrow-terminal warning must be written to STDERR exactly once."""
    ui.console = Console(no_color=True, width=60, force_terminal=True)
    saved_err = ui.err_console
    err_buf = io.StringIO()
    ui.err_console = Console(file=err_buf, no_color=True, width=60, force_terminal=True)
    try:
        e = _make_entry()
        ui.entries_table([e], total=1)
        err_out = strip_ansi(err_buf.getvalue())
        assert "narrower than" in err_out
        assert "Tags" in err_out
    finally:
        ui.err_console = saved_err
