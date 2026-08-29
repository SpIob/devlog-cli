"""Tests for the `devlog delete` command."""

import re

import pytest
from click.testing import CliRunner

from devlog.cli import main
from devlog import storage


@pytest.fixture()
def runner(tmp_path):
    return CliRunner(env={"DEVLOG_DATA_DIR": str(tmp_path)})


@pytest.fixture()
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    return tmp_path


def _add(runner, message, *tags):
    args = ["add", message] + [a for t in tags for a in ("-t", t)]
    result = runner.invoke(main, args)
    assert result.exit_code == 0
    m = re.search(r"[a-f0-9]{8}", result.output)
    assert m
    return m.group(0)


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_delete_with_yes_flag(runner, data_dir):
    sid = _add(runner, "to be deleted", "backend")
    result = runner.invoke(main, ["delete", sid, "--yes"])
    assert result.exit_code == 0
    assert "Entry deleted" in result.output
    # File is now empty
    assert storage.load_entries() == []


def test_delete_quiet(runner, data_dir):
    sid = _add(runner, "msg")
    result = runner.invoke(main, ["delete", sid, "--yes", "--quiet"])
    assert result.exit_code == 0
    assert result.output.strip() == ""
    assert storage.load_entries() == []


def test_delete_by_prefix(runner, data_dir):
    sid = _add(runner, "msg")
    prefix = sid[:4]
    result = runner.invoke(main, ["delete", prefix, "--yes"])
    assert result.exit_code == 0
    assert storage.load_entries() == []


# ---------------------------------------------------------------------------
# Confirmation prompt
# ---------------------------------------------------------------------------


def test_delete_abort_on_n(runner, data_dir):
    sid = _add(runner, "kept entry")
    result = runner.invoke(main, ["delete", sid], input="n\n")
    assert result.exit_code == 0
    assert "Aborted" in result.output
    # Entry must still exist
    entries = storage.load_entries()
    assert len(entries) == 1
    assert entries[0].message == "kept entry"


def test_delete_confirm_on_y(runner, data_dir):
    sid = _add(runner, "go away")
    result = runner.invoke(main, ["delete", sid], input="y\n")
    assert result.exit_code == 0
    assert "Entry deleted" in result.output
    assert storage.load_entries() == []


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_delete_not_found(runner):
    result = runner.invoke(main, ["delete", "deadbeef", "--yes"])
    assert result.exit_code == 1
    assert "No entry found" in result.output


def test_delete_empty_id(runner):
    result = runner.invoke(main, ["delete", "", "--yes"])
    assert result.exit_code == 1
    assert "ID is required" in result.output
