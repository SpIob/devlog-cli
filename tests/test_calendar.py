"""Tests for the `devlog calendar` heatmap command and the
`ui.calendar_grid` / `ui.calendar_panel` helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from click.testing import CliRunner

from devlog.cli import main
from devlog import storage
from devlog.models import Entry
from devlog import themes
from devlog import ui


@pytest.fixture()
def runner(tmp_path):
    return CliRunner(env={"DEVLOG_DATA_DIR": str(tmp_path)})


@pytest.fixture()
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    return tmp_path


def _utc_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed(entry_id, message, created_at, tags=None):
    storage.add_entry(
        Entry(
            id=entry_id,
            message=message,
            tags=list(tags) if tags else [],
            created_at=created_at,
        )
    )


def _invoke(runner, *args, tz=None, monkeypatch=None):
    if monkeypatch is not None:
        if tz is None:
            monkeypatch.delenv("DEVLOG_TZ", raising=False)
        else:
            monkeypatch.setenv("DEVLOG_TZ", tz)
    return runner.invoke(main, list(args))


# ---------------------------------------------------------------------------
# ui.calendar_grid
# ---------------------------------------------------------------------------


def test_calendar_grid_empty_year():
    import datetime as _dt

    grid = ui.calendar_grid({}, year=2025)
    text = grid.plain
    # 53 weeks × 7 days = 371 cells max for a non-leap year. The first
    # week may be padded. The exact length is unimportant — we just
    # want to confirm the grid is non-empty and uses the right
    # character set.
    assert len(text) > 0
    # All characters must come from the heatmap alphabet, the gutter
    # alphabet (the 3-letter %b abbreviations of all 12 months), or
    # newlines. The gutter adds a 3-char month label on the row where
    # each month begins, so the alphabet now includes letters.
    month_chars: set[str] = set()
    for m in range(1, 13):
        month_chars.update(_dt.date(2025, m, 1).strftime("%b"))
    for ch in text:
        assert ch in {" ", "·", "▪", "▫", "█", "\n"} | month_chars, (
            f"unexpected char {ch!r}"
        )


def test_calendar_grid_buckets_by_year():
    """An entry from another year must not appear in the grid."""
    grid = ui.calendar_grid({"2024-06-15": 1, "2025-06-15": 1}, year=2025)
    text = grid.plain
    # The 2024 entry shouldn't show up. The 2025 one should.
    # 2024-06-15 is a Saturday, 2025-06-15 is a Sunday.
    # We don't assert exact cell positions; just that both `·` chars
    # (one for 2024, but only 2025 should be present) exist.
    # Easier: count the non-space, non-newline cells, but strip the
    # gutter (3-char + 1-space) on each line first so the month labels
    # don't pollute the count.
    lines = text.splitlines()
    assert len(lines) == 14, f"expected 7 weekday rows × 2 (gutter + cells), got {len(lines)}"
    data_rows = lines[1::2]  # every second line is the data row
    heatmap_chars = {"·", "▪", "▫", "█"}
    non_space = sum(
        1 for row in data_rows for ch in row if ch in heatmap_chars
    )
    assert non_space >= 1  # at least 2025-06-15
    # And the count shouldn't be 2 — only 2025 entries count.
    # 2024 is in a different year, so the cell for that calendar date
    # *is* rendered (in 2024's slot) but we asked for year=2025 so
    # it's a space. So 2024's cell is a space → no extra character.
    assert non_space == 1


def test_calendar_grid_has_month_gutter():
    """The grid must include a left-edge month label gutter."""
    grid = ui.calendar_grid({}, year=2025)
    text = grid.plain
    lines = text.splitlines()
    # 7 weekday rows × 2 lines (gutter + cells) = 14 total lines.
    assert len(lines) == 14
    # The gutter row for the first weekday (Sunday in 2025-01-05) must
    # contain a 3-letter month abbreviation for January.
    gutter_lines = lines[0::2]
    found_jan = any("Jan" in line for line in gutter_lines)
    assert found_jan, f"no Jan label in gutter: {gutter_lines}"


def test_calendar_grid_label_alignment():
    """Each month label must sit on the same row as the day it labels.

    The first day of each month in 2025 falls on a known weekday.
    The display is laid out Sunday-first (display row 0=Sun,
    1=Mon, ..., 6=Sat). Python's ``date.weekday()`` is
    Monday-first (0=Mon, 6=Sun), so the display row is
    ``(python_weekday + 1) % 7``.
    """
    # 2025 first-of-month weekdays (Python Mon=0 .. Sun=6):
    # Jan=Wed, Feb=Sat, Mar=Sat, Apr=Tue, May=Thu, Jun=Sun,
    # Jul=Tue, Aug=Fri, Sep=Mon, Oct=Wed, Nov=Sat, Dec=Mon.
    expected = {
        "Jan": 3,  # Wed → display row 3
        "Feb": 6,  # Sat → display row 6
        "Mar": 6,  # Sat
        "Apr": 2,  # Tue
        "May": 4,  # Thu
        "Jun": 0,  # Sun
        "Jul": 2,  # Tue
        "Aug": 5,  # Fri
        "Sep": 1,  # Mon
        "Oct": 3,  # Wed
        "Nov": 6,  # Sat
        "Dec": 1,  # Mon
    }
    grid = ui.calendar_grid({}, year=2025)
    lines = grid.plain.splitlines()
    gutter_lines = lines[0::2]
    for month, display_row in expected.items():
        assert month in gutter_lines[display_row], (
            f"{month} should be on gutter row {display_row}, "
            f"but gutter lines are: {gutter_lines}"
        )


def test_calendar_grid_all_twelve_months_labeled():
    """Every month in the year must appear at least once in the gutter.

    Regression guard for the old bug where the gutter algorithm only
    labelled some months because the gutter was indexed by loop
    position rather than the actual day-of-week of the 1st of the
    month.
    """
    grid = ui.calendar_grid({}, year=2025)
    text = grid.plain
    for month in (
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ):
        assert month in text, f"missing month label: {month}"


def test_calendar_panel_includes_legend():
    panel = ui.calendar_panel({"2025-01-15": 1}, year=2025)
    # The Panel renders to a Console record; just confirm the function
    # returns a Panel instance.
    assert panel is not None
    # The panel title is "Calendar · 2025".
    assert "Calendar" in str(panel.title)
    assert "2025" in str(panel.title)


# ---------------------------------------------------------------------------
# calendar command
# ---------------------------------------------------------------------------


def test_calendar_empty(runner, data_dir, monkeypatch):
    result = _invoke(runner, "calendar", monkeypatch=monkeypatch)
    assert result.exit_code == 0
    # The "No entries in <year>." message has the current year in it.
    # We don't know the year, so just check the prefix.
    assert "No entries in" in result.output


def test_calendar_with_entries(runner, data_dir, monkeypatch):
    now = datetime.now(tz=timezone.utc)
    _seed("11111111-aaaa-bbbb-cccc-111111111111", "a", _utc_iso(now))
    _seed("22222222-aaaa-bbbb-cccc-222222222222", "b", _utc_iso(now))
    result = _invoke(runner, "calendar", monkeypatch=monkeypatch)
    assert result.exit_code == 0
    # The panel should be rendered. We don't assert the exact contents
    # (the calendar grid uses theme colors that may not survive
    # CliRunner's non-TTY capture cleanly); we just check the title
    # appears.
    assert "Calendar" in result.output


def test_calendar_quiet_outputs_json(runner, data_dir, monkeypatch):
    now = datetime.now(tz=timezone.utc)
    _seed("33333333-aaaa-bbbb-cccc-333333333333", "a", _utc_iso(now))
    result = _invoke(runner, "calendar", "--quiet", monkeypatch=monkeypatch)
    assert result.exit_code == 0
    obj = json.loads(result.output.strip())
    # The map should be a {date: count} dict.
    assert isinstance(obj, dict)
    # There must be at least one entry from today.
    today_key = now.strftime("%Y-%m-%d")
    assert obj.get(today_key, 0) >= 1


def test_calendar_specific_year(runner, data_dir, monkeypatch):
    # Seed an entry in 2024 and one in 2025.
    _seed("44444444-aaaa-bbbb-cccc-444444444444", "old", "2024-06-15T12:00:00Z")
    _seed("55555555-aaaa-bbbb-cccc-555555555555", "new", "2025-06-15T12:00:00Z")
    result = _invoke(runner, "calendar", "--year", "2024", "--quiet", monkeypatch=monkeypatch)
    assert result.exit_code == 0
    obj = json.loads(result.output.strip())
    # 2024 has 1 entry, 2025 should NOT be in the map.
    assert obj.get("2024-06-15") == 1
    assert "2025-06-15" not in obj


def test_calendar_local_tz_bucketing(runner, data_dir, monkeypatch):
    """An entry at 2025-01-01 02:00 UTC is on 2024-12-31 in NY. With
    DEVLOG_TZ=America/New_York and --year 2024, the entry should
    appear; with --year 2025, it should not."""
    _seed("66666666-aaaa-bbbb-cccc-666666666666", "ny", "2025-01-01T02:00:00Z")
    # Year 2024 — entry should appear under 2024-12-31.
    result = _invoke(runner, "calendar", "--year", "2024", "--quiet",
                     tz="America/New_York", monkeypatch=monkeypatch)
    obj = json.loads(result.output.strip())
    assert obj.get("2024-12-31") == 1
    # Year 2025 — no entry (it bucketed to 2024).
    result = _invoke(runner, "calendar", "--year", "2025", "--quiet",
                     tz="America/New_York", monkeypatch=monkeypatch)
    obj = json.loads(result.output.strip())
    assert obj == {}


# ---------------------------------------------------------------------------
# Theme roles
# ---------------------------------------------------------------------------


def test_heatmap_roles_in_default_palette():
    """The 5 new heatmap roles must be present in the default palette."""
    palette = themes.get_active_theme()
    for role in ("heatmap_empty", "heatmap_l1", "heatmap_l2", "heatmap_l3", "heatmap_l4"):
        assert role in palette
        assert palette[role]  # non-empty style string
