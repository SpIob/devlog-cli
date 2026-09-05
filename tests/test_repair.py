"""Tests for the `devlog repair` command and the storage validator."""

import json

import pytest
from click.testing import CliRunner

from devlog import storage
from devlog.cli import main
from devlog.models import Entry


# `runner`, `data_dir`, and `write_entries` come from tests/conftest.py.


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


@pytest.mark.parametrize(
    "field,bad_value,expected_kind",
    [
        ("id", None, "missing_field"),
        ("message", None, "missing_field"),
        ("created_at", "not-a-date", "bad_timestamp"),
        ("updated_at", "garbage", "bad_timestamp"),
    ],
    ids=["missing-id", "missing-message", "bad-created-at", "bad-updated-at"],
)
def test_validate_field_problems(field, bad_value, expected_kind):
    """Per-field validation: each row of this table drives a small
    payload past the validator and asserts the expected issue kind+field.
    """
    base = {
        "id": "a",
        "message": "x",
        "tags": [],
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": None,
    }
    if bad_value is None:
        base.pop(field, None)
    else:
        base[field] = bad_value
    issues = storage.validate_entries({"entries": [base]})
    assert any(
        i.kind == expected_kind and i.field == field for i in issues
    ), f"expected {expected_kind} on field {field!r}; got {[i.kind for i in issues]}"


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


def test_repair_clean_store_is_a_noop(runner, tmp_path, write_entries):
    write_entries(tmp_path,
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


def test_repair_strips_invalid_tags_keeps_entry(runner, tmp_path, write_entries):
    """An entry with one bad tag should keep the entry and lose the tag.

    Previously the whole entry was dropped — that was heavy-handed
    for the common case of a hand-edited ``entries.json`` with a
    single typo. Repair now scrubs bad tags and retains the row.
    """
    write_entries(tmp_path,
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
                    "tags": ["Bad Tag", "ok-tag"],
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
    # No entries were dropped, so exit is 0
    assert result.exit_code == 0
    data = json.loads((tmp_path / "entries.json").read_text())
    by_id = {e["id"]: e for e in data["entries"]}
    assert set(by_id) == {"good", "bad-tag", "good-2"}
    # The bad tag was stripped; the good tag on the same row survives
    assert by_id["bad-tag"]["tags"] == ["ok-tag"]
    # User can see what was changed
    assert "stripped 1 bad tag" in result.output


def test_repair_drops_entries_with_bad_timestamp(runner, tmp_path, write_entries):
    """A bad ``created_at`` is unrecoverable so the entry is dropped."""
    write_entries(tmp_path,
        {
            "entries": [
                {
                    "id": "good",
                    "message": "good",
                    "tags": ["x"],
                    "created_at": "2025-01-01T00:00:00Z",
                },
                {
                    "id": "bad-time",
                    "message": "bad time",
                    "tags": ["x"],
                    "created_at": "definitely not a date",
                },
            ]
        },
    )
    result = runner.invoke(main, ["repair", "-y"])
    assert result.exit_code == 1
    data = json.loads((tmp_path / "entries.json").read_text())
    assert {e["id"] for e in data["entries"]} == {"good"}


def test_repair_dedupes_duplicate_ids(runner, tmp_path, write_entries):
    write_entries(tmp_path,
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


def test_repair_dry_run_does_not_write(runner, tmp_path, write_entries):
    write_entries(tmp_path,
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


def test_repair_writes_backup_before_writing(runner, tmp_path, write_entries):
    """A backup is written whenever repair touches the file — even if
    the only change is a tag strip (no entry drop)."""
    write_entries(tmp_path,
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
    # No entries were dropped (the bad tag was stripped in place), so
    # exit is 0. But the file was rewritten, so a backup was taken.
    assert result.exit_code == 0
    backups = list((tmp_path / "backups").glob("*.json"))
    assert len(backups) == 1
    payload = json.loads(backups[0].read_text())
    assert payload["entries"][0]["id"] == "bad"
    # Repair report mentions the backup
    assert "Backup written to" in result.output


def test_repair_drops_unrecoverable_writes_backup(runner, tmp_path, write_entries):
    """A drop counts as a write too, so a backup is still produced."""
    write_entries(tmp_path,
        {
            "entries": [
                {
                    "id": "bad",
                    "message": "x",
                    "tags": [],
                    "created_at": "not a date",
                }
            ]
        },
    )
    result = runner.invoke(main, ["repair", "-y"])
    assert result.exit_code == 1
    backups = list((tmp_path / "backups").glob("*.json"))
    assert len(backups) == 1


def test_repair_no_backup_flag_skips_backup(runner, tmp_path, write_entries):
    write_entries(tmp_path,
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
    # No drop happened (bad tag was stripped in place), so exit is 0.
    assert result.exit_code == 0
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
    # The error string must not contain two adjacent periods (a
    # concatenation artifact from the storage error message ending in
    # "to reset." and the cli tacking on ". Restore from …").
    assert ".." not in result.output
    # The wrapped storage error starts with "Error:" — once routed
    # through the "Cannot repair:" prefix, only one colon boundary
    # should remain visible to the user.
    assert result.output.count("Error:") <= 1


def test_repair_recovers_truncated_trailing_garbage(runner, tmp_path):
    """A trailing half-written entry should not doom the whole file.

    Reproduces a power-loss-during-flush scenario: the JSON object
    closes cleanly, but the file has extra non-JSON bytes after the
    final ``}``. Recovery trims back to the last valid ``}`` and
    repair proceeds normally.
    """
    payload = {
        "entries": [
            {
                "id": "good",
                "message": "survivor",
                "tags": ["ok"],
                "created_at": "2025-01-01T00:00:00Z",
            }
        ]
    }
    corrupt = json.dumps(payload) + '{ "id": "broken", "mess'  # truncated
    (tmp_path / "entries.json").write_text(corrupt, encoding="utf-8")
    result = runner.invoke(main, ["repair", "-y"])
    # Repair succeeded: the recoverable entry was kept, the trailing
    # garbage was dropped on the floor.
    assert result.exit_code == 0
    data = json.loads((tmp_path / "entries.json").read_text())
    assert [e["id"] for e in data["entries"]] == ["good"]


def test_repair_recovery_fails_when_no_valid_object(runner, tmp_path):
    """If there is no recoverable object, fall back to the error path."""
    (tmp_path / "entries.json").write_text("not even an object", encoding="utf-8")
    result = runner.invoke(main, ["repair", "-y"])
    assert result.exit_code == 2
    assert "Cannot repair" in result.output


# ---------------------------------------------------------------------------
# Quiet mode
# ---------------------------------------------------------------------------


def test_repair_quiet_no_issues(runner, tmp_path):
    result = runner.invoke(main, ["repair", "--quiet"])
    assert result.exit_code == 0
    assert result.output.strip() == ""


def test_repair_quiet_with_issues(runner, tmp_path, write_entries):
    write_entries(tmp_path,
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
    # No issues panel, but the file is still rewritten. With the new
    # "strip bad tag" behaviour, the entry survives with empty tags.
    assert "Found" not in result.output
    data = json.loads((tmp_path / "entries.json").read_text())
    assert data["entries"] == [
        {
            "id": "bad",
            "message": "x",
            "tags": [],
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": None,
        }
    ]
