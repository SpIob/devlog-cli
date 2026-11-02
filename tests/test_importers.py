"""Tests for Tier 2 journal-format importers (jrnl, Day One)."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from devlog import storage
from devlog._importers import (
    detect_format,
    import_dayone_json,
    import_jrnl_json,
)
from devlog.cli import main


@pytest.fixture()
def runner(tmp_path):
    return CliRunner(env={"DEVLOG_DATA_DIR": str(tmp_path)})


@pytest.fixture()
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


def test_detect_jrnl(tmp_path):
    f = tmp_path / "jrnl.json"
    f.write_text(json.dumps({"entries": [{"date": "2024-01-01T10:00:00", "text": "hi", "starred": False}]}))
    assert detect_format(str(f)) == "jrnl"


def test_detect_dayone(tmp_path):
    f = tmp_path / "dayone.json"
    f.write_text(json.dumps({"entries": [{"creationDate": "2024-01-01T10:00:00Z", "text": "hi"}]}))
    assert detect_format(str(f)) == "dayone"


def test_detect_devlog_json(tmp_path):
    f = tmp_path / "d.json"
    f.write_text(json.dumps({"entries": [{"id": "x", "message": "m", "created_at": "2024-01-01T00:00:00Z"}]}))
    assert detect_format(str(f)) == "json"


def test_detect_markdown_extension(tmp_path):
    f = tmp_path / "d.md"
    f.write_text("# hello")
    assert detect_format(str(f)) == "markdown"


def test_detect_unknown(tmp_path):
    f = tmp_path / "d.txt"
    f.write_text("plain text, no structure")
    assert detect_format(str(f)) == "unknown"


# ---------------------------------------------------------------------------
# jrnl parsing
# ---------------------------------------------------------------------------


def test_jrnl_import_basic(tmp_path):
    f = tmp_path / "jrnl.json"
    f.write_text(
        json.dumps(
            {
                "entries": [
                    {"date": "2024-01-15T10:30:00", "text": "jrnl entry one", "tags": ["work"], "starred": False},
                    {"date": "2024-01-16T09:00:00", "text": "jrnl entry two", "tags": [], "starred": True},
                ]
            }
        )
    )
    entries = import_jrnl_json(str(f))
    assert len(entries) == 2
    assert entries[0].message == "jrnl entry one"
    assert entries[0].created_at == "2024-01-15T10:30:00Z"
    assert entries[0].tags == ["work"]
    # starred flag becomes a tag
    assert "starred" in entries[1].tags


def test_jrnl_import_naive_date_treated_as_utc(tmp_path):
    f = tmp_path / "jrnl.json"
    f.write_text(json.dumps({"entries": [{"date": "2024-01-15T10:30:00", "text": "x", "starred": False}]}))
    entries = import_jrnl_json(str(f))
    assert entries[0].created_at == "2024-01-15T10:30:00Z"


# ---------------------------------------------------------------------------
# Day One parsing
# ---------------------------------------------------------------------------


def test_dayone_import_preserves_uuid_and_tags(tmp_path):
    f = tmp_path / "dayone.json"
    f.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "uuid": "DAYONE-UUID-1234",
                        "creationDate": "2024-02-20T08:15:30Z",
                        "text": "day one entry",
                        "tags": ["Travel", "Japan"],
                    }
                ]
            }
        )
    )
    entries = import_dayone_json(str(f))
    assert len(entries) == 1
    assert entries[0].id == "DAYONE-UUID-1234"
    assert entries[0].message == "day one entry"
    # tags normalised: lowercased, spaces → hyphens
    assert entries[0].tags == ["travel", "japan"]


def test_dayone_import_mints_ids_when_missing(tmp_path):
    f = tmp_path / "dayone.json"
    f.write_text(json.dumps({"entries": [{"creationDate": "2024-02-20T08:15:30Z", "text": "no uuid"}]}))
    entries = import_dayone_json(str(f))
    assert len(entries[0].id) >= 8


# ---------------------------------------------------------------------------
# CLI end-to-end (dedupe + dry-run reuse the shared import plumbing)
# ---------------------------------------------------------------------------


def test_cli_import_jrnl(runner, data_dir, tmp_path):
    f = tmp_path / "jrnl.json"
    f.write_text(
        json.dumps(
            {
                "entries": [
                    {"date": "2024-01-15T10:30:00", "text": "from jrnl", "tags": ["work"], "starred": False}
                ]
            }
        )
    )
    result = runner.invoke(main, ["import", str(f), "--format", "jrnl"])
    assert result.exit_code == 0, result.output
    assert "Imported 1 entry" in result.output
    entries = storage.load_entries()
    assert len(entries) == 1
    assert entries[0].message == "from jrnl"


def test_cli_import_jrnl_auto_detected(runner, data_dir, tmp_path):
    f = tmp_path / "whatever.json"
    f.write_text(
        json.dumps(
            {"entries": [{"date": "2024-01-15T10:30:00", "text": "auto", "starred": False}]}
        )
    )
    result = runner.invoke(main, ["import", str(f)])
    assert result.exit_code == 0, result.output
    assert len(storage.load_entries()) == 1


def test_cli_import_dayone(runner, data_dir, tmp_path):
    f = tmp_path / "dayone.json"
    f.write_text(
        json.dumps(
            {
                "entries": [
                    {"uuid": "D1", "creationDate": "2024-02-20T08:15:30Z", "text": "from day one", "tags": ["x"]}
                ]
            }
        )
    )
    result = runner.invoke(main, ["import", str(f), "--format", "dayone"])
    assert result.exit_code == 0, result.output
    entries = storage.load_entries()
    assert entries[0].id == "D1"


def test_cli_import_jrnl_idempotent(runner, data_dir, tmp_path):
    f = tmp_path / "jrnl.json"
    f.write_text(
        json.dumps(
            {"entries": [{"date": "2024-01-15T10:30:00", "text": "again", "starred": False}]}
        )
    )
    r1 = runner.invoke(main, ["import", str(f), "--format", "jrnl"])
    assert r1.exit_code == 0
    # jrnl rows have no ids → second import mints new uuids, but the
    # fingerprint dedupe (created_at, message) must catch the repeat.
    r2 = runner.invoke(main, ["import", str(f), "--format", "jrnl"])
    assert r2.exit_code == 0
    # Second pass reports the fingerprint hit on the no-new-import path.
    assert "1 duplicate skipped" in r2.output
    assert len(storage.load_entries()) == 1


def test_cli_import_jrnl_dry_run_does_not_write(runner, data_dir, tmp_path):
    f = tmp_path / "jrnl.json"
    f.write_text(
        json.dumps(
            {"entries": [{"date": "2024-01-15T10:30:00", "text": "dry", "starred": False}]}
        )
    )
    result = runner.invoke(main, ["import", str(f), "--format", "jrnl", "--dry-run"])
    assert result.exit_code == 0
    assert "would import 1 entry" in result.output
    assert storage.load_entries() == []


# ---------------------------------------------------------------------------
# Malformed input: clean errors, no raw tracebacks
# ---------------------------------------------------------------------------


def test_cli_import_jrnl_invalid_json_clean_error(runner, data_dir, tmp_path):
    """A non-JSON file with --format jrnl exits 2 with a clean message.

    Regression: the importers json.load()'d without validation, so a
    malformed file crashed with a raw JSONDecodeError traceback.
    """
    f = tmp_path / "broken.txt"
    f.write_text("this is not json at all")
    result = runner.invoke(main, ["import", str(f), "--format", "jrnl"])
    assert result.exit_code == 2
    assert "Invalid JSON in" in result.output
    assert "Traceback" not in result.output
    assert storage.load_entries() == []


def test_cli_import_dayone_wrong_shape_clean_error(runner, data_dir, tmp_path):
    """Valid JSON without an 'entries' list exits 2 with a clean message."""
    f = tmp_path / "wrong.json"
    f.write_text(json.dumps({"foo": []}))
    result = runner.invoke(main, ["import", str(f), "--format", "dayone"])
    assert result.exit_code == 2
    # Rich wraps the long message inside the error panel, so assert on
    # a short prefix rather than the full sentence.
    assert "Invalid import file" in result.output
    assert "Traceback" not in result.output
    assert storage.load_entries() == []


def test_import_jrnl_json_raises_value_error_on_bad_payload(tmp_path):
    f = tmp_path / "broken.json"
    f.write_text("{not json")
    with pytest.raises(ValueError, match="Invalid JSON"):
        import_jrnl_json(str(f))


def test_import_payload_allows_empty_entries_list(tmp_path):
    """An empty export is valid — no entries, no error."""
    f = tmp_path / "empty.json"
    f.write_text(json.dumps({"entries": []}))
    assert import_jrnl_json(str(f)) == []
    assert import_dayone_json(str(f)) == []
