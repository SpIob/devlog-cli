"""Tests for the `devlog doctor` command."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner

from devlog import storage
from devlog.cli import main
from devlog.models import Entry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def runner(tmp_path):
    return CliRunner(env={"DEVLOG_DATA_DIR": str(tmp_path)})


@pytest.fixture()
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    return tmp_path


def _utc_iso(dt):
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


# ---------------------------------------------------------------------------
# Empty / clean states
# ---------------------------------------------------------------------------


def test_doctor_no_file(runner, tmp_path):
    """No entries.json → exit 0, clean report."""
    result = runner.invoke(main, ["doctor"])
    assert result.exit_code == 0
    assert "all clear" in result.output
    assert "Path" in result.output
    assert "Exists: no" in result.output


def test_doctor_clean_store(runner, data_dir):
    _seed("11111111-1111-1111-1111-111111111111", "fine", _utc_iso(datetime.now(tz=timezone.utc)))
    result = runner.invoke(main, ["doctor"])
    assert result.exit_code == 0
    assert "all clear" in result.output
    assert "1" in result.output  # entry count
    assert "today" in result.output  # last entry was today


def test_doctor_quiet_clean(runner, data_dir):
    _seed("11111111-1111-1111-1111-111111111111", "fine", _utc_iso(datetime.now(tz=timezone.utc)))
    result = runner.invoke(main, ["doctor", "--quiet"])
    assert result.exit_code == 0
    obj = json.loads(result.output.strip())
    assert obj["ok"] is True
    assert obj["exists"] is True
    assert obj["entry_count"] == 1
    assert obj["issues"] == []
    assert obj["writable"] is True


# ---------------------------------------------------------------------------
# Issues detected
# ---------------------------------------------------------------------------


def test_doctor_detects_bad_tags(runner, data_dir, tmp_path):
    """Hand-written entries.json with bad tags should be flagged."""
    (tmp_path / "entries.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "bad",
                        "message": "x",
                        "tags": ["Bad Tag"],
                        "created_at": "2025-01-01T00:00:00Z",
                    }
                ]
            }
        )
    )
    result = runner.invoke(main, ["doctor"])
    assert result.exit_code == 1
    assert "attention" in result.output
    assert "validation issue" in result.output
    assert "devlog repair" in result.output


def test_doctor_quiet_with_issues(runner, data_dir, tmp_path):
    (tmp_path / "entries.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "bad",
                        "message": "x",
                        "tags": ["Bad Tag"],
                        "created_at": "2025-01-01T00:00:00Z",
                    }
                ]
            }
        )
    )
    result = runner.invoke(main, ["doctor", "--quiet"])
    assert result.exit_code == 1
    obj = json.loads(result.output.strip())
    assert obj["ok"] is False
    assert any(i["kind"] == "bad_tag" for i in obj["issues"])


def test_doctor_detects_corrupt_json(runner, data_dir, tmp_path):
    (tmp_path / "entries.json").write_text("{ not valid", encoding="utf-8")
    result = runner.invoke(main, ["doctor"])
    assert result.exit_code == 2
    # Should mention the corruption
    assert "corrupt" in result.output.lower() or "valid" in result.output.lower() or "Doctor" in result.output


def test_doctor_quiet_corrupt_json(runner, data_dir, tmp_path):
    (tmp_path / "entries.json").write_text("{ not valid", encoding="utf-8")
    result = runner.invoke(main, ["doctor", "--quiet"])
    assert result.exit_code == 2
    obj = json.loads(result.output.strip())
    assert obj["ok"] is False
    assert any(i["kind"] == "corrupt_json" for i in obj["issues"])


# ---------------------------------------------------------------------------
# Stats within the report
# ---------------------------------------------------------------------------


def test_doctor_reports_days_since_last(runner, data_dir):
    now = datetime.now(tz=timezone.utc)
    five_days_ago = now - timedelta(days=5)
    _seed("11111111-1111-1111-1111-111111111111", "old", _utc_iso(five_days_ago))
    result = runner.invoke(main, ["doctor"])
    assert result.exit_code == 0
    assert "5 days ago" in result.output


def test_doctor_reports_longest_messages(runner, data_dir):
    now = datetime.now(tz=timezone.utc)
    _seed("aaaaaaaa-1111-1111-1111-111111111111", "short", _utc_iso(now))
    _seed("bbbbbbbb-2222-2222-2222-222222222222", "a longer entry than the others", _utc_iso(now))
    _seed("cccccccc-3333-3333-3333-333333333333", "tiny", _utc_iso(now))
    result = runner.invoke(main, ["doctor"])
    assert result.exit_code == 0
    assert "Longest messages" in result.output
    # The longest should appear first; sanity check by length
    assert "30 chars" in result.output  # "a longer entry than the others" = 30 chars


def test_doctor_reports_file_size(runner, data_dir):
    _seed("11111111-1111-1111-1111-111111111111", "x", _utc_iso(datetime.now(tz=timezone.utc)))
    result = runner.invoke(main, ["doctor"])
    assert result.exit_code == 0
    assert "bytes" in result.output
