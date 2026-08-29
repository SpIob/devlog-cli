"""Tests for the `devlog repair` command and the storage validator."""

import json

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


def _write_raw(tmp_path, payload):
    """Overwrite the entries.json with an arbitrary payload (possibly broken)."""
    path = tmp_path / "entries.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Storage validator (storage.validate_entries)
# ---------------------------------------------------------------------------


def test_validate_clean_payload_returns_no_issues():
    payload = {
        "entries": [
            {
                "id": "abc",
                "message": "hi",
                "tags": ["a"],
                "created_at": "2025-01-01T00:00:00Z",
            }
        ]
    }
    assert storage.validate_entries(payload) == []


def test_validate_non_dict_root():
    issues = storage.validate_entries(["not", "a", "dict"])
    assert len(issues) == 1
    assert issues[0].kind == "bad_root"


def test_validate_missing_entries_key():
    issues = storage.validate_entries({"not_entries": []})
    assert len(issues) == 1
    assert issues[0].kind == "missing_field"
    assert issues[0].field == "entries"


def test_validate_entries_not_list():
    issues = storage.validate_entries({"entries": "oops"})
    assert len(issues) == 1
    assert issues[0].kind == "bad_field"


def test_validate_item_not_dict():
    issues = storage.validate_entries({"entries": ["oops"]})
    assert len(issues) == 1
    assert issues[0].kind == "bad_item"
    assert issues[0].index == 0


def test_validate_missing_id():
    payload = {"entries": [{"message": "x", "tags": [], "created_at": "2025-01-01T00:00:00Z"}]}
    issues = storage.validate_entries(payload)
    assert any(i.kind == "missing_field" and i.field == "id" for i in issues)


def test_validate_missing_message():
    payload = {
        "entries": [
            {"id": "a", "tags": [], "created_at": "2025-01-01T00:00:00Z"}
        ]
    }
    issues = storage.validate_entries(payload)
    assert any(i.kind == "missing_field" and i.field == "message" for i in issues)


def test_validate_bad_timestamp():
    payload = {
        "entries": [
            {
                "id": "a",
                "message": "x",
                "tags": [],
                "created_at": "not-a-date",
            }
        ]
    }
    issues = storage.validate_entries(payload)
    assert any(i.kind == "bad_timestamp" and i.field == "created_at" for i in issues)


def test_validate_bad_updated_at():
    payload = {
        "entries": [
            {
                "id": "a",
                "message": "x",
                "tags": [],
                "created_at": "2025-01-01T00:00:00Z",
                "updated_at": "garbage",
            }
        ]
    }
    issues = storage.validate_entries(payload)
    assert any(i.kind == "bad_timestamp" and i.field == "updated_at" for i in issues)


def test_validate_bad_tag():
    payload = {
        "entries": [
            {
                "id": "a",
                "message": "x",
                "tags": ["Bad Tag"],
                "created_at": "2025-01-01T00:00:00Z",
            }
        ]
    }
    issues = storage.validate_entries(payload)
    assert any(i.kind == "bad_tag" for i in issues)


def test_validate_duplicate_id():
    payload = {
        "entries": [
            {
                "id": "dup",
                "message": "first",
                "tags": [],
                "created_at": "2025-01-01T00:00:00Z",
            },
            {
                "id": "dup",
                "message": "second",
                "tags": [],
                "created_at": "2025-01-02T00:00:00Z",
            },
        ]
    }
    issues = storage.validate_entries(payload)
    dup = [i for i in issues if i.kind == "duplicate_id"]
    assert len(dup) == 1
    assert dup[0].index == 1
    assert dup[0].entry_id == "dup"


# ---------------------------------------------------------------------------
# `devlog repair` happy paths
# ---------------------------------------------------------------------------


def test_repair_clean_store_is_a_noop(runner, tmp_path):
    _write_raw(
        tmp_path,
        {
            "entries": [
                {
                    "id": "abc",
                    "message": "fine",
                    "tags": [],
                    "created_at": "2025-01-01T00:00:00Z",
                }
            ]
        },
    )
    result = runner.invoke(main, ["repair"])
    assert result.exit_code == 0
    assert "No issues" in result.output
    # File untouched
    data = json.loads((tmp_path / "entries.json").read_text())
    assert len(data["entries"]) == 1


def test_repair_drops_invalid_entries(runner, tmp_path):
    _write_raw(
        tmp_path,
        {
            "entries": [
                {
                    "id": "good",
                    "message": "good",
                    "tags": ["x"],
                    "created_at": "2025-01-01T00:00:00Z",
                },
                {
                    "id": "bad-tag",
                    "message": "invalid tag",
                    "tags": ["Bad Tag"],
                    "created_at": "2025-01-02T00:00:00Z",
                },
                {
                    "id": "good-2",
                    "message": "another good",
                    "tags": [],
                    "created_at": "2025-01-03T00:00:00Z",
                },
            ]
        },
    )
    result = runner.invoke(main, ["repair", "-y"])
    assert result.exit_code == 1  # dropped some entries
    data = json.loads((tmp_path / "entries.json").read_text())
    assert {e["id"] for e in data["entries"]} == {"good", "good-2"}


def test_repair_dedupes_duplicate_ids(runner, tmp_path):
    _write_raw(
        tmp_path,
        {
            "entries": [
                {
                    "id": "dup",
                    "message": "first",
                    "tags": [],
                    "created_at": "2025-01-01T00:00:00Z",
                },
                {
                    "id": "dup",
                    "message": "second",
                    "tags": [],
                    "created_at": "2025-01-02T00:00:00Z",
                },
            ]
        },
    )
    result = runner.invoke(main, ["repair", "-y"])
    assert result.exit_code == 1
    data = json.loads((tmp_path / "entries.json").read_text())
    # First occurrence wins
    assert len(data["entries"]) == 1
    assert data["entries"][0]["message"] == "first"


def test_repair_dry_run_does_not_write(runner, tmp_path):
    _write_raw(
        tmp_path,
        {
            "entries": [
                {
                    "id": "bad",
                    "message": "x",
                    "tags": ["Bad Tag"],
                    "created_at": "2025-01-01T00:00:00Z",
                }
            ]
        },
    )
    before = (tmp_path / "entries.json").read_text()
    result = runner.invoke(main, ["repair", "--dry-run"])
    assert result.exit_code == 0
    assert "DRY RUN" in result.output
    after = (tmp_path / "entries.json").read_text()
    assert before == after


def test_repair_writes_backup_before_writing(runner, tmp_path):
    _write_raw(
        tmp_path,
        {
            "entries": [
                {
                    "id": "bad",
                    "message": "x",
                    "tags": ["Bad Tag"],
                    "created_at": "2025-01-01T00:00:00Z",
                }
            ]
        },
    )
    result = runner.invoke(main, ["repair", "-y"])
    assert result.exit_code == 1
    backups = list((tmp_path / "backups").glob("*.json"))
    assert len(backups) == 1
    payload = json.loads(backups[0].read_text())
    assert payload["entries"][0]["id"] == "bad"
    # Repair report mentions the backup
    assert "Backup written to" in result.output


def test_repair_no_backup_flag_skips_backup(runner, tmp_path):
    _write_raw(
        tmp_path,
        {
            "entries": [
                {
                    "id": "bad",
                    "message": "x",
                    "tags": ["Bad Tag"],
                    "created_at": "2025-01-01T00:00:00Z",
                }
            ]
        },
    )
    result = runner.invoke(main, ["repair", "-y", "--no-backup"])
    assert result.exit_code == 1
    assert not (tmp_path / "backups").exists() or not list((tmp_path / "backups").glob("*.json"))


def test_repair_no_file_is_noop(runner, tmp_path):
    """An absent entries.json should not be created by repair."""
    assert not (tmp_path / "entries.json").exists()
    result = runner.invoke(main, ["repair"])
    assert result.exit_code == 0
    assert "No journal yet" in result.output
    assert not (tmp_path / "entries.json").exists()


def test_repair_corrupt_json_errors(runner, tmp_path):
    (tmp_path / "entries.json").write_text("{ not valid json", encoding="utf-8")
    result = runner.invoke(main, ["repair", "-y"])
    assert result.exit_code == 2
    assert "Cannot repair" in result.output
    assert "restore" in result.output.lower()


# ---------------------------------------------------------------------------
# Quiet mode
# ---------------------------------------------------------------------------


def test_repair_quiet_no_issues(runner, tmp_path):
    result = runner.invoke(main, ["repair", "--quiet"])
    assert result.exit_code == 0
    assert result.output.strip() == ""


def test_repair_quiet_with_issues(runner, tmp_path):
    _write_raw(
        tmp_path,
        {
            "entries": [
                {
                    "id": "bad",
                    "message": "x",
                    "tags": ["Bad Tag"],
                    "created_at": "2025-01-01T00:00:00Z",
                }
            ]
        },
    )
    result = runner.invoke(main, ["repair", "-y", "--quiet"])
    # No issues panel, but the file is still rewritten
    assert "Found" not in result.output
    data = json.loads((tmp_path / "entries.json").read_text())
    assert data["entries"] == []
