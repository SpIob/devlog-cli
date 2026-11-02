"""Tests for Stats v2: streaks, tag co-occurrence, days-since-last.

Unit tests target the pure helpers in ``devlog.storage``; CLI tests
cover the panel and ``--quiet`` JSON contract additions. All backend
independent: the helpers take plain lists, and the CLI paths go
through ``storage.load_entries()`` which both backends implement.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner

from devlog import storage, ui
from devlog.cli import main
from devlog.models import Entry
from tests.conftest import first_json_obj, render_to_text


def _utc_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed(entry_id: str, message: str, created_at: datetime, tags=None) -> None:
    storage.add_entry(
        Entry(
            id=entry_id,
            message=message,
            tags=list(tags) if tags else [],
            created_at=_utc_iso(created_at),
        )
    )


@pytest.fixture()
def runner(tmp_path):
    return CliRunner(env={"DEVLOG_DATA_DIR": str(tmp_path)})


@pytest.fixture()
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# compute_streaks
# ---------------------------------------------------------------------------


def test_streaks_empty():
    assert storage.compute_streaks([]) == (0, 0)


def test_streaks_single_day():
    today = datetime.now(tz=timezone.utc).date()
    assert storage.compute_streaks([today]) == (1, 1)


def test_streaks_current_counts_back_from_today():
    today = datetime.now(tz=timezone.utc).date()
    dates = [today - timedelta(days=i) for i in range(4)]
    assert storage.compute_streaks(dates) == (4, 4)


def test_streaks_yesterday_still_alive():
    """Writing yesterday but not today keeps the streak alive."""
    today = datetime.now(tz=timezone.utc).date()
    dates = [today - timedelta(days=i) for i in range(1, 4)]
    current, longest = storage.compute_streaks(dates)
    assert current == 3
    assert longest == 3


def test_streaks_broken_when_gap():
    today = datetime.now(tz=timezone.utc).date()
    # 5-day run ended 3 days ago — current is 0, longest is 5.
    dates = [today - timedelta(days=i) for i in range(3, 8)]
    current, longest = storage.compute_streaks(dates)
    assert current == 0
    assert longest == 5


def test_streaks_longest_is_historical_max():
    today = datetime.now(tz=timezone.utc).date()
    # 2-day run today; an older 6-day run in the past.
    dates = [today, today - timedelta(days=1)]
    dates += [today - timedelta(days=10 - i) for i in range(6)]
    current, longest = storage.compute_streaks(dates)
    assert current == 2
    assert longest == 6


def test_streaks_duplicates_collapse():
    today = datetime.now(tz=timezone.utc).date()
    dates = [today, today, today - timedelta(days=1), today - timedelta(days=1)]
    assert storage.compute_streaks(dates) == (2, 2)


# ---------------------------------------------------------------------------
# compute_tag_cooccurrence
# ---------------------------------------------------------------------------


def test_cooccurrence_counts_pairs():
    entries = [
        Entry(id="1", message="m", tags=["backend", "bugfix", "urgent"], created_at=_utc_iso(datetime.now(tz=timezone.utc))),
        Entry(id="2", message="m", tags=["backend", "bugfix"], created_at=_utc_iso(datetime.now(tz=timezone.utc))),
        Entry(id="3", message="m", tags=["docs"], created_at=_utc_iso(datetime.now(tz=timezone.utc))),
    ]
    pairs = storage.compute_tag_cooccurrence(entries)
    assert pairs[0] == (("backend", "bugfix"), 2)
    # Single-tag entries contribute nothing.
    assert all("docs" not in pair for pair, _ in pairs)


def test_cooccurrence_pair_order_is_alphabetical():
    entries = [
        Entry(id="1", message="m", tags=["zulu", "alpha"], created_at=_utc_iso(datetime.now(tz=timezone.utc))),
    ]
    pairs = storage.compute_tag_cooccurrence(entries)
    assert pairs == [(("alpha", "zulu"), 1)]


def test_cooccurrence_top_limits_output():
    entries = [
        Entry(id="1", message="m", tags=["a", "b", "c", "d"], created_at=_utc_iso(datetime.now(tz=timezone.utc))),
    ]
    assert len(storage.compute_tag_cooccurrence(entries, top=2)) == 2


def test_cooccurrence_empty():
    assert storage.compute_tag_cooccurrence([]) == []


# ---------------------------------------------------------------------------
# compute_days_since_last
# ---------------------------------------------------------------------------


def test_days_since_last_empty():
    assert storage.compute_days_since_last([]) is None


def test_days_since_last_today_is_zero():
    today = datetime.now(tz=timezone.utc).date()
    assert storage.compute_days_since_last([today]) == 0


def test_days_since_last_uses_max_date():
    today = datetime.now(tz=timezone.utc).date()
    assert storage.compute_days_since_last([today - timedelta(days=9), today - timedelta(days=2)]) == 2


# ---------------------------------------------------------------------------
# CLI: panel
# ---------------------------------------------------------------------------


def test_stats_panel_shows_streak_and_days_since():
    panel = ui.stats_panel(
        total=3,
        first_iso="2025-06-10T00:00:00Z",
        last_iso="2025-06-15T00:00:00Z",
        top_tags=[("backend", 2), ("docs", 1)],
        last_30_days=[("2025-06-15", 3)],
        streaks=(2, 5),
        top_tag_pairs=[(("backend", "docs"), 1)],
        days_since=0,
    )
    out = render_to_text(panel)
    assert "Streak" in out
    assert "longest 5" in out
    assert "Top tag pairs" in out
    assert "backend + docs" in out
    assert "Last entry" in out


def test_stats_panel_omits_pairs_when_none():
    panel = ui.stats_panel(
        total=1,
        first_iso="2025-06-10T00:00:00Z",
        last_iso="2025-06-10T00:00:00Z",
        top_tags=[],
        last_30_days=[],
    )
    out = render_to_text(panel)
    assert "Top tag pairs" not in out
    # Defaults keep the panel renderable for old callers.
    assert "Streak" in out


def test_stats_panel_zero_streak_renders():
    panel = ui.stats_panel(
        total=1,
        first_iso="2025-06-10T00:00:00Z",
        last_iso="2025-06-10T00:00:00Z",
        top_tags=[],
        last_30_days=[],
        streaks=(0, 1),
        days_since=3,
    )
    out = render_to_text(panel)
    assert "Streak" in out
    assert "3 days ago" in out


# ---------------------------------------------------------------------------
# CLI: end-to-end (both backends)
# ---------------------------------------------------------------------------


def test_stats_cli_shows_new_sections(runner, data_dir):
    now = datetime.now(tz=timezone.utc)
    _seed("11111111-aaaa-bbbb-cccc-111111111111", "a", now, ["backend", "bugfix"])
    _seed("22222222-aaaa-bbbb-cccc-222222222222", "b", now, ["backend"])

    result = runner.invoke(main, ["stats"])
    assert result.exit_code == 0, result.output
    assert "Streak" in result.output
    assert "Top tag pairs" in result.output
    assert "backend + bugfix" in result.output


def test_stats_quiet_json_includes_new_fields(runner, data_dir):
    now = datetime.now(tz=timezone.utc)
    _seed("11111111-aaaa-bbbb-cccc-111111111111", "a", now, ["backend", "bugfix"])
    _seed("22222222-aaaa-bbbb-cccc-222222222222", "b", now, ["backend"])

    result = runner.invoke(main, ["stats", "--quiet"])
    assert result.exit_code == 0, result.output
    obj = first_json_obj(result.output)
    assert obj["streaks"]["current"] == 1
    assert obj["streaks"]["longest"] == 1
    assert obj["days_since_last"] == 0
    assert {"tags": ["backend", "bugfix"], "count": 1} in obj["top_tag_pairs"]


def test_stats_quiet_json_days_since_matches_history(runner, data_dir):
    now = datetime.now(tz=timezone.utc)
    _seed("11111111-aaaa-bbbb-cccc-111111111111", "a", now - timedelta(days=4))

    result = runner.invoke(main, ["stats", "--quiet"])
    assert result.exit_code == 0, result.output
    obj = first_json_obj(result.output)
    assert obj["days_since_last"] == 4
    assert obj["streaks"]["current"] == 0
