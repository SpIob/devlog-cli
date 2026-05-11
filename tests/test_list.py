"""Tests for the `devlog list` command."""

import json

import pytest
from click.testing import CliRunner

from devlog.cli import main


@pytest.fixture()
def runner(tmp_path):
    return CliRunner(env={"DEVLOG_DATA_DIR": str(tmp_path)})


def _add(runner, message, *tags):
    args = ["add", message] + [a for t in tags for a in ("-t", t)]
    runner.invoke(main, args)


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------


def test_list_empty(runner):
    result = runner.invoke(main, ["list"])
    assert result.exit_code == 0
    assert "No entries found" in result.output


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------


def test_list_shows_entries(runner):
    _add(runner, "First entry", "backend")
    result = runner.invoke(main, ["list"])
    assert result.exit_code == 0
    assert "First entry" in result.output
    assert "ID" in result.output
    assert "Date" in result.output


def test_list_limit(runner):
    for i in range(5):
        _add(runner, f"Entry {i}")
    result = runner.invoke(main, ["list", "--limit", "2"])
    assert result.exit_code == 0
    assert "Showing 2 of 5" in result.output


def test_list_show_all(runner):
    for i in range(25):
        _add(runner, f"Entry {i}")
    result = runner.invoke(main, ["list", "--all"])
    assert result.exit_code == 0
    assert "Showing 25 of 25" in result.output


def test_list_tag_filter(runner):
    _add(runner, "Backend task", "backend")
    _add(runner, "Frontend task", "frontend")
    result = runner.invoke(main, ["list", "-t", "backend"])
    assert result.exit_code == 0
    assert "Backend task" in result.output
    assert "Frontend task" not in result.output


def test_list_tag_filter_no_match(runner):
    _add(runner, "A task", "backend")
    result = runner.invoke(main, ["list", "-t", "nonexistent"])
    assert "No entries match your filters" in result.output


# ---------------------------------------------------------------------------
# Quiet mode
# ---------------------------------------------------------------------------


def test_list_quiet(runner):
    _add(runner, "JSON entry", "api")
    result = runner.invoke(main, ["list", "--quiet"])
    assert result.exit_code == 0
    lines = [l for l in result.output.strip().splitlines() if l]
    obj = json.loads(lines[0])
    assert obj["message"] == "JSON entry"


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


def test_list_invalid_limit(runner):
    result = runner.invoke(main, ["list", "--limit", "0"])
    assert result.exit_code == 1
    assert "--limit must be a positive integer" in result.output


# ---------------------------------------------------------------------------
# Corrupted storage
# ---------------------------------------------------------------------------


def test_list_corrupted_file(tmp_path):
    entries_file = tmp_path / "entries.json"
    entries_file.write_text("not valid json", encoding="utf-8")
    runner = CliRunner(env={"DEVLOG_DATA_DIR": str(tmp_path)})
    result = runner.invoke(main, ["list"])
    assert result.exit_code == 2
    assert "corrupted" in result.output.lower()