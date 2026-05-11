"""Tests for the `devlog add` command."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from devlog.cli import main
from devlog.storage import StoragePermissionError


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def runner(tmp_path):
    """Return a CliRunner pre-configured with an isolated DEVLOG_DATA_DIR."""
    return CliRunner(env={"DEVLOG_DATA_DIR": str(tmp_path)})


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_add_no_tags(runner):
    result = runner.invoke(main, ["add", "Fixed the login bug"])
    assert result.exit_code == 0
    assert "✔" in result.output
    assert "Fixed the login bug" in result.output


def test_add_with_tags(runner):
    result = runner.invoke(main, ["add", "Deploy pipeline", "-t", "CI", "-t", "backend"])
    assert result.exit_code == 0
    # tags normalised to lowercase
    assert "ci" in result.output
    assert "backend" in result.output


def test_add_tags_deduplicated(runner, tmp_path):
    runner.invoke(main, ["add", "Dup tags", "-t", "backend", "-t", "Backend"])
    entries_file = tmp_path / "entries.json"
    data = json.loads(entries_file.read_text())
    assert data["entries"][0]["tags"].count("backend") == 1


def test_add_quiet(runner):
    result = runner.invoke(main, ["add", "Silent entry", "-q"])
    assert result.exit_code == 0
    assert result.output.strip() == ""


# ---------------------------------------------------------------------------
# Validation errors (exit 1)
# ---------------------------------------------------------------------------


def test_add_empty_message(runner):
    result = runner.invoke(main, ["add", ""])
    assert result.exit_code == 1
    assert "MESSAGE cannot be empty" in result.output


def test_add_invalid_tag_chars(runner):
    result = runner.invoke(main, ["add", "Msg", "-t", "bad tag"])
    assert result.exit_code == 1
    assert "invalid characters" in result.output
    assert '"bad tag"' in result.output


def test_add_tag_too_long(runner):
    long_tag = "a" * 33
    result = runner.invoke(main, ["add", "Msg", "-t", long_tag])
    assert result.exit_code == 1
    assert "exceeds maximum length" in result.output


def test_add_too_many_tags(runner):
    tags = [f"tag{i}" for i in range(11)]
    args = ["add", "Msg"] + [arg for t in tags for arg in ("-t", t)]
    result = runner.invoke(main, args)
    assert result.exit_code == 1
    assert "Maximum 10 tags" in result.output


# ---------------------------------------------------------------------------
# Storage errors (exit 2)
# ---------------------------------------------------------------------------


def test_add_storage_permission_error(runner, tmp_path):
    with patch("devlog.storage.save_entries") as mock_save:
        mock_save.side_effect = StoragePermissionError(
            tmp_path / "entries.json", "write"
        )
        result = runner.invoke(main, ["add", "Will fail"])
    assert result.exit_code == 2
    assert "Cannot write to storage file" in result.output