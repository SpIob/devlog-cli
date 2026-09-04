"""Tests for the `devlog today`, `devlog tail`, and `devlog stats` commands."""

import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner

from devlog.cli import main
from devlog import storage
from devlog.models import Entry


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
    """Inject an entry directly into storage, bypassing the CLI's clock."""
    storage.add_entry(
        Entry(
            id=entry_id,
            message=message,
            tags=list(tags) if tags else [],
            created_at=created_at,
        )
    )


# ---------------------------------------------------------------------------
# today
# ---------------------------------------------------------------------------


def test_today_empty(runner, data_dir):
    # No entries seeded → empty state
    result = runner.invoke(main, ["today"])
    assert result.exit_code == 0
    assert "No entries yet today" in result.output


def test_today_shows_todays_entries(runner, data_dir):
    now = datetime.now(tz=timezone.utc)
    # Use 30 minutes instead of 1 hour to avoid crossing midnight in UTC.
    _seed("11111111-aaaa-bbbb-cccc-111111111111", "today one", _utc_iso(now))
    _seed("22222222-aaaa-bbbb-cccc-222222222222", "today two", _utc_iso(now - timedelta(minutes=30)))
    result = runner.invoke(main, ["today"])
    assert result.exit_code == 0
    assert "today one" in result.output
    assert "today two" in result.output


def test_today_excludes_other_days(runner, data_dir):
    now = datetime.now(tz=timezone.utc)
    _seed("33333333-aaaa-bbbb-cccc-333333333333", "yesterday", _utc_iso(now - timedelta(days=1)))
    _seed("44444444-aaaa-bbbb-cccc-444444444444", "today", _utc_iso(now))
    result = runner.invoke(main, ["today"])
    assert result.exit_code == 0
    assert "today" in result.output
    assert "yesterday" not in result.output


def test_today_quiet(runner, data_dir):
    now = datetime.now(tz=timezone.utc)
    _seed("55555555-aaaa-bbbb-cccc-555555555555", "jsonable", _utc_iso(now))
    result = runner.invoke(main, ["today", "--quiet"])
    assert result.exit_code == 0
    obj = json.loads(result.output.strip().splitlines()[0])
    assert obj["message"] == "jsonable"


def test_today_invalid_limit(runner):
    result = runner.invoke(main, ["today", "--limit", "0"])
    assert result.exit_code == 1
    assert "--limit must be a positive integer" in result.output


# ---------------------------------------------------------------------------
# tail
# ---------------------------------------------------------------------------


def test_tail_default_n(runner, data_dir):
    for i in range(10):
        _seed(f"{i:08d}-aaaa-bbbb-cccc-dddddddddddd", f"msg {i}", _utc_iso(datetime.now(tz=timezone.utc)))
    result = runner.invoke(main, ["tail"])
    assert result.exit_code == 0
    # Default is 5
    assert "Showing 5" in result.output


def test_tail_explicit_n(runner, data_dir):
    for i in range(10):
        _seed(f"{i:08d}-bbbb-bbbb-cccc-dddddddddddd", f"m {i}", _utc_iso(datetime.now(tz=timezone.utc)))
    result = runner.invoke(main, ["tail", "3"])
    assert result.exit_code == 0
    assert "Showing 3" in result.output


def test_tail_with_tag_filter(runner, data_dir):
    now = datetime.now(tz=timezone.utc)
    _seed("66666666-aaaa-bbbb-cccc-666666666666", "backend one", _utc_iso(now), ["backend"])
    _seed("77777777-aaaa-bbbb-cccc-777777777777", "frontend one", _utc_iso(now), ["frontend"])
    result = runner.invoke(main, ["tail", "5", "-t", "backend"])
    assert result.exit_code == 0
    assert "backend one" in result.output
    assert "frontend one" not in result.output


def test_tail_invalid_n(runner):
    result = runner.invoke(main, ["tail", "0"])
    assert result.exit_code == 1
    assert "N must be a positive integer" in result.output


def test_tail_empty(runner):
    result = runner.invoke(main, ["tail", "5"])
    assert result.exit_code == 0
    assert "No entries found" in result.output


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


def test_stats_empty(runner):
    result = runner.invoke(main, ["stats"])
    assert result.exit_code == 0
    assert "No entries to summarize" in result.output


def test_stats_summary(runner, data_dir):
    now = datetime.now(tz=timezone.utc)
    _seed("88888888-aaaa-bbbb-cccc-888888888888", "a", _utc_iso(now - timedelta(days=2)), ["backend", "bugfix"])
    _seed("99999999-aaaa-bbbb-cccc-999999999999", "b", _utc_iso(now), ["backend"])
    _seed("aaaaaaaa-bbbb-cccc-dddd-aaaaaaaaaaaa", "c", _utc_iso(now), ["docs"])

    result = runner.invoke(main, ["stats"])
    assert result.exit_code == 0
    assert "Total" in result.output
    assert "First" in result.output
    assert "Last" in result.output
    assert "Span" in result.output
    assert "Top" in result.output
    assert "tags" in result.output
    assert "backend" in result.output
    assert "Last 30 days" in result.output


def test_stats_quiet(runner, data_dir):
    now = datetime.now(tz=timezone.utc)
    _seed("bbbbbbbb-cccc-dddd-eeee-bbbbbbbbbbbb", "only", _utc_iso(now), ["x"])
    result = runner.invoke(main, ["stats", "--quiet"])
    assert result.exit_code == 0
    obj = json.loads(result.output.strip().splitlines()[0])
    assert obj["total"] == 1
    assert "top_tags" in obj
    assert "last_30_days" in obj
    assert len(obj["last_30_days"]) == 30


def test_stats_does_not_crash_on_unparseable_created_at(runner, data_dir):
    """A store containing one valid entry and one with a bogus
    created_at must not crash `stats` — the bad row is dropped and the
    panel renders for the good row."""
    now = datetime.now(tz=timezone.utc)
    # Hand-craft the file so we can inject a corrupt created_at that
    # `add` would otherwise reject.
    payload = {
        "entries": [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "message": "good",
                "tags": ["x"],
                "created_at": _utc_iso(now),
                "updated_at": None,
            },
            {
                "id": "22222222-2222-2222-2222-222222222222",
                "message": "bad",
                "tags": [],
                "created_at": "not-a-date",
                "updated_at": None,
            },
        ]
    }
    (data_dir / "entries.json").write_text(json.dumps(payload))

    result = runner.invoke(main, ["stats"])
    assert result.exit_code == 0
    assert "Total" in result.output
    # The panel should reflect only the valid entry.
    assert re.search(r"Total\s*:\s*1", result.output) is not None
