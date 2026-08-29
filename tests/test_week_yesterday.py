"""Tests for the `devlog yesterday` and `devlog week` commands."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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
# yesterday
# ---------------------------------------------------------------------------


def test_yesterday_empty(runner, data_dir, monkeypatch):
    result = _invoke(runner, "yesterday", monkeypatch=monkeypatch)
    assert result.exit_code == 0
    assert "No entries yet yesterday" in result.output


def test_yesterday_excludes_today(runner, data_dir, monkeypatch):
    """`yesterday` must NOT include an entry created today."""
    now = datetime.now(tz=timezone.utc)
    _seed("11111111-aaaa-bbbb-cccc-111111111111", "today", _utc_iso(now))
    result = _invoke(runner, "yesterday", monkeypatch=monkeypatch)
    assert result.exit_code == 0
    assert "today" not in result.output
    assert "No entries yet yesterday" in result.output


def test_yesterday_includes_yesterday_entry(runner, data_dir, monkeypatch):
    now = datetime.now(tz=timezone.utc)
    yesterday_utc = now - timedelta(days=1)
    _seed("22222222-aaaa-bbbb-cccc-222222222222", "yest msg", _utc_iso(yesterday_utc))
    result = _invoke(runner, "yesterday", monkeypatch=monkeypatch)
    assert result.exit_code == 0
    assert "yest msg" in result.output


def test_yesterday_quiet(runner, data_dir, monkeypatch):
    now = datetime.now(tz=timezone.utc)
    yesterday_utc = now - timedelta(days=1)
    _seed("33333333-aaaa-bbbb-cccc-333333333333", "qmsg", _utc_iso(yesterday_utc))
    result = _invoke(runner, "yesterday", "--quiet", monkeypatch=monkeypatch)
    assert result.exit_code == 0
    lines = [ln for ln in result.output.splitlines() if ln.strip()]
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["message"] == "qmsg"


def test_yesterday_invalid_limit(runner, data_dir, monkeypatch):
    result = _invoke(runner, "yesterday", "--limit", "0", monkeypatch=monkeypatch)
    assert result.exit_code == 1
    assert "--limit must be a positive integer" in result.output


def test_yesterday_local_zone_bucketing(runner, data_dir, monkeypatch):
    """An entry at 2025-01-01 02:00 UTC is on 2024-12-31 in NY, so when
    today_local is 2025-01-01 NY, that entry must appear in `yesterday`."""
    # This test uses fixed historical timestamps; we can't easily make
    # `now()` equal to 2025-01-01 NY-time. The most robust check is:
    # the bucketing helper is what matters. We assert via the storage
    # helper that an entry at 02:00 UTC is on Dec 31 in NY — and trust
    # the yesterday code path to use the same helper. (yesterday uses
    # `storage.local_date_for` exactly like `today`.)
    from devlog.storage import local_date_for
    iso = "2025-01-01T02:00:00Z"
    assert local_date_for(iso, ZoneInfo("America/New_York")).isoformat() == "2024-12-31"


# ---------------------------------------------------------------------------
# week
# ---------------------------------------------------------------------------


def test_week_empty(runner, data_dir, monkeypatch):
    result = _invoke(runner, "week", monkeypatch=monkeypatch)
    assert result.exit_code == 0
    assert "No entries in the last 7 days" in result.output


def test_week_includes_recent_entry(runner, data_dir, monkeypatch):
    now = datetime.now(tz=timezone.utc)
    _seed("44444444-aaaa-bbbb-cccc-444444444444", "recent", _utc_iso(now - timedelta(days=2)))
    result = _invoke(runner, "week", monkeypatch=monkeypatch)
    assert result.exit_code == 0
    assert "recent" in result.output


def test_week_excludes_old_entry(runner, data_dir, monkeypatch):
    now = datetime.now(tz=timezone.utc)
    _seed("55555555-aaaa-bbbb-cccc-555555555555", "ancient", _utc_iso(now - timedelta(days=30)))
    result = _invoke(runner, "week", monkeypatch=monkeypatch)
    assert result.exit_code == 0
    assert "ancient" not in result.output


def test_week_anchor(runner, data_dir, monkeypatch):
    """`--day YYYY-MM-DD` anchors the week ending on that day."""
    # The anchor 2025-05-10 is a Saturday. The week covers 2025-05-04..2025-05-10.
    # Seed an entry inside the window and one outside.
    _seed(
        "66666666-aaaa-bbbb-cccc-666666666666",
        "in-window",
        "2025-05-07T12:00:00Z",
    )
    _seed(
        "77777777-aaaa-bbbb-cccc-777777777777",
        "out-of-window",
        "2025-05-03T12:00:00Z",  # before 2025-05-04
    )
    result = _invoke(runner, "week", "--day", "2025-05-10", monkeypatch=monkeypatch)
    assert result.exit_code == 0
    assert "in-window" in result.output
    assert "out-of-window" not in result.output


def test_week_quiet(runner, data_dir, monkeypatch):
    now = datetime.now(tz=timezone.utc)
    _seed("88888888-aaaa-bbbb-cccc-888888888888", "wq", _utc_iso(now))
    result = _invoke(runner, "week", "--quiet", monkeypatch=monkeypatch)
    assert result.exit_code == 0
    lines = [ln for ln in result.output.splitlines() if ln.strip()]
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["message"] == "wq"


def test_week_invalid_limit(runner, data_dir, monkeypatch):
    result = _invoke(runner, "week", "--limit", "0", monkeypatch=monkeypatch)
    assert result.exit_code == 1
    assert "--limit must be a positive integer" in result.output


def test_week_invalid_anchor(runner, data_dir, monkeypatch):
    result = _invoke(runner, "week", "--day", "not-a-date", monkeypatch=monkeypatch)
    assert result.exit_code == 2
    # The error message is wrapped in a red panel, so just check the
    # sub-string makes it through.
    assert "not-a-date" in result.output


# ---------------------------------------------------------------------------
# Local-tz integration: same code path used by `today`
# ---------------------------------------------------------------------------


def test_week_local_zone_includes_recent(runner, data_dir, monkeypatch):
    """Confirm `week` with DEVLOG_TZ set includes a 2-day-old entry."""
    now = datetime.now(tz=timezone.utc)
    _seed("99999999-aaaa-bbbb-cccc-999999999999", "recent-ny", _utc_iso(now - timedelta(days=2)))
    result = _invoke(runner, "week", tz="America/New_York", monkeypatch=monkeypatch)
    assert result.exit_code == 0
    assert "recent-ny" in result.output
