"""Tests for the `devlog backup` and `devlog restore` commands."""

import json
import os
from datetime import datetime, timezone

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


def _seed(entry_id, message, created_at="2025-01-01T00:00:00Z", tags=None):
    storage.add_entry(
        Entry(
            id=entry_id,
            message=message,
            tags=list(tags) if tags else [],
            created_at=created_at,
        )
    )


# ---------------------------------------------------------------------------
# backup
# ---------------------------------------------------------------------------


def test_backup_creates_file_in_backups_dir(runner, data_dir):
    _seed("11111111-1111-1111-1111-111111111111", "msg")
    result = runner.invoke(main, ["backup", "--quiet"])
    assert result.exit_code == 0
    out_path = result.output.strip()
    assert os.path.exists(out_path)
    assert "backups" in out_path
    assert out_path.endswith(".json")


def test_backup_filename_format(runner, data_dir):
    _seed("11111111-1111-1111-1111-111111111111", "msg")
    result = runner.invoke(main, ["backup", "--quiet"])
    out_path = result.output.strip()
    filename = os.path.basename(out_path)
    # Format: entries-YYYYMMDD-HHMMSS.json
    assert filename.startswith("entries-")
    assert filename.endswith(".json")
    ts_part = filename[len("entries-"):-len(".json")]
    # Should be parseable as YYYYMMDD-HHMMSS
    datetime.strptime(ts_part, "%Y%m%d-%H%M%S")


def test_backup_writes_valid_json(runner, data_dir):
    _seed("11111111-1111-1111-1111-111111111111", "alpha")
    _seed("22222222-2222-2222-2222-222222222222", "beta", tags=["t"])
    result = runner.invoke(main, ["backup", "--quiet"])
    out_path = result.output.strip()
    payload = json.loads(open(out_path).read())
    assert "entries" in payload
    assert len(payload["entries"]) == 2
    assert {e["message"] for e in payload["entries"]} == {"alpha", "beta"}


def test_backup_default_output(runner, data_dir):
    _seed("11111111-1111-1111-1111-111111111111", "msg")
    result = runner.invoke(main, ["backup"])
    assert result.exit_code == 0
    assert "Backed up" in result.output
    # The path should appear in the output
    assert "entries-" in result.output


def test_backup_explicit_output(runner, data_dir, tmp_path):
    _seed("11111111-1111-1111-1111-111111111111", "msg")
    out_path = tmp_path / "mybackup.json"
    result = runner.invoke(main, ["backup", "--output", str(out_path), "--quiet"])
    assert result.exit_code == 0
    assert result.output.strip() == str(out_path)
    assert out_path.exists()


def test_backup_empty_journal(runner, data_dir):
    """Backup on an empty store still works (zero entries)."""
    result = runner.invoke(main, ["backup", "--quiet"])
    assert result.exit_code == 0
    out_path = result.output.strip()
    payload = json.loads(open(out_path).read())
    assert payload["entries"] == []


def test_backup_creates_backups_dir_if_missing(runner, tmp_path):
    """The backups dir should be auto-created on first backup."""
    monkeypatch_data = tmp_path
    assert not (monkeypatch_data / "backups").exists()
    runner2 = CliRunner(env={"DEVLOG_DATA_DIR": str(monkeypatch_data)})
    result = runner2.invoke(main, ["backup", "--quiet"])
    assert result.exit_code == 0
    assert (monkeypatch_data / "backups").exists()


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------


def test_restore_round_trip(runner, data_dir):
    """Backup → mutate → restore returns to the backed-up state."""
    _seed("11111111-1111-1111-1111-111111111111", "original")
    backup_result = runner.invoke(main, ["backup", "--quiet"])
    assert backup_result.exit_code == 0
    backup_path = backup_result.output.strip()

    # Mutate
    _seed("22222222-2222-2222-2222-222222222222", "added after backup")

    # Restore
    result = runner.invoke(main, ["restore", backup_path, "-y"])
    assert result.exit_code == 0
    entries = storage.load_entries()
    assert len(entries) == 1
    assert entries[0].message == "original"


def test_restore_writes_entries(runner, data_dir, tmp_path):
    backup_file = tmp_path / "b.json"
    backup_file.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "restored-id",
                        "message": "from backup",
                        "tags": ["t"],
                        "created_at": "2025-02-01T00:00:00Z",
                    }
                ]
            }
        )
    )
    result = runner.invoke(main, ["restore", str(backup_file), "-y"])
    assert result.exit_code == 0
    assert "Restored" in result.output
    entries = storage.load_entries()
    assert len(entries) == 1
    assert entries[0].message == "from backup"


def test_restore_skips_invalid_rows(runner, data_dir, tmp_path):
    """Bad rows in the backup are skipped with a warning, not a hard error."""
    backup_file = tmp_path / "b.json"
    backup_file.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "ok",
                        "message": "valid",
                        "tags": [],
                        "created_at": "2025-01-01T00:00:00Z",
                    },
                    {
                        "id": "bad",
                        "message": "invalid",
                        "tags": ["Bad Tag"],
                        "created_at": "2025-01-02T00:00:00Z",
                    },
                ]
            }
        )
    )
    result = runner.invoke(main, ["restore", str(backup_file), "-y"])
    assert result.exit_code == 0
    assert "Skipped" in result.output
    entries = storage.load_entries()
    assert len(entries) == 1
    assert entries[0].id == "ok"


def test_restore_prompts_when_existing(runner, data_dir, tmp_path):
    _seed("11111111-1111-1111-1111-111111111111", "existing")
    backup_file = tmp_path / "b.json"
    backup_file.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "new",
                        "message": "replacement",
                        "tags": [],
                        "created_at": "2025-01-01T00:00:00Z",
                    }
                ]
            }
        )
    )
    # Without -y: the default is "no" → abort
    result = runner.invoke(main, ["restore", str(backup_file)], input="n\n")
    assert result.exit_code == 1  # Click abort=True → SystemExit(1) when user says no
    entries = storage.load_entries()
    assert entries[0].message == "existing"


def test_restore_invalid_json_errors(runner, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    result = runner.invoke(main, ["restore", str(bad), "-y"])
    assert result.exit_code == 2
    assert "not valid JSON" in result.output


def test_restore_structural_invalid_errors(runner, tmp_path):
    """A backup that's not a dict at the root or has a non-list 'entries' is rejected."""
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    result = runner.invoke(main, ["restore", str(bad), "-y"])
    assert result.exit_code == 2
    assert "structurally invalid" in result.output


def test_restore_unreadable_path():
    runner = CliRunner(env={"DEVLOG_DATA_DIR": "/tmp/devlog-test"})
    result = runner.invoke(main, ["restore", "/nonexistent/path.json", "-y"])
    assert result.exit_code == 2  # click's exists=True


def test_restore_dry_run_does_not_write(runner, data_dir, tmp_path):
    _seed("11111111-1111-1111-1111-111111111111", "original")
    backup_file = tmp_path / "b.json"
    backup_file.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "new",
                        "message": "would replace",
                        "tags": [],
                        "created_at": "2025-01-01T00:00:00Z",
                    }
                ]
            }
        )
    )
    result = runner.invoke(main, ["restore", str(backup_file), "--dry-run"])
    assert result.exit_code == 0
    assert "DRY RUN" in result.output
    # Original entry still there
    entries = storage.load_entries()
    assert entries[0].message == "original"


def test_restore_empty_backup(runner, data_dir, tmp_path):
    backup_file = tmp_path / "b.json"
    backup_file.write_text(json.dumps({"entries": []}))
    result = runner.invoke(main, ["restore", str(backup_file), "-y"])
    assert result.exit_code == 0
    assert "no valid entries" in result.output
    # Store should now be empty (the existing data is wiped even though the
    # backup was empty — the user's intent was to restore to that state).
    assert storage.load_entries() == []
