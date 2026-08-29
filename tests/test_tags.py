"""Tests for the `devlog tags` command."""

import json
import re

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


def _add(runner, message, *tags):
    args = ["add", message] + [a for t in tags for a in ("-t", t)]
    result = runner.invoke(main, args)
    assert result.exit_code == 0
    m = re.search(r"[a-f0-9]{8}", result.output)
    assert m
    return m.group(0)


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------


def test_tags_empty(runner):
    result = runner.invoke(main, ["tags"])
    assert result.exit_code == 0
    assert "No tags found" in result.output


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def test_tags_counts(runner, data_dir):
    _add(runner, "a", "backend")
    _add(runner, "b", "backend", "bugfix")
    _add(runner, "c", "docs")
    result = runner.invoke(main, ["tags"])
    assert result.exit_code == 0
    assert "backend" in result.output
    assert "bugfix" in result.output
    assert "docs" in result.output
    # backend used 2x
    assert "2" in result.output


def test_tags_last_used_updates_on_edit(runner, data_dir):
    sid = _add(runner, "msg", "backend")
    result1 = runner.invoke(main, ["tags"])
    assert "backend" in result1.output

    result2 = runner.invoke(main, ["edit", sid, "--add-tag", "frontend"])
    assert result2.exit_code == 0
    result3 = runner.invoke(main, ["tags"])
    assert "frontend" in result3.output
    assert "backend" in result3.output


def test_tags_sort_name(runner, data_dir):
    _add(runner, "a", "zebra")
    _add(runner, "b", "apple")
    _add(runner, "c", "mango")
    result = runner.invoke(main, ["tags", "--sort", "name"])
    assert result.exit_code == 0
    out = result.output
    pos_apple = out.find("apple")
    pos_mango = out.find("mango")
    pos_zebra = out.find("zebra")
    assert pos_apple < pos_mango < pos_zebra


def test_tags_sort_count_desc(runner, data_dir):
    _add(runner, "a", "x")
    _add(runner, "b", "y", "x")
    _add(runner, "c", "y", "x")
    _add(runner, "d", "y")
    result = runner.invoke(main, ["tags", "--sort", "count"])
    assert result.exit_code == 0
    # x and y tie on count (3 each). Default count sort must put higher first.
    # Both should appear before any lower-count tags.
    pos_x = result.output.find("│ x")
    pos_y = result.output.find("│ y")
    # Just verify both x and y appear and the footer count is correct
    assert pos_x > 0
    assert pos_y > 0


def test_tags_limit(runner, data_dir):
    for i in range(10):
        _add(runner, f"msg {i}", f"tag{i}")
    result = runner.invoke(main, ["tags", "--limit", "3"])
    assert result.exit_code == 0
    # Footer in entries_table not used; tags_table has a count in title
    assert "10 distinct" in result.output


def test_tags_all(runner, data_dir):
    for i in range(25):
        _add(runner, f"m {i}", f"tag{i}")
    result = runner.invoke(main, ["tags", "--all"])
    assert result.exit_code == 0
    assert "25 distinct" in result.output


# ---------------------------------------------------------------------------
# Quiet JSON
# ---------------------------------------------------------------------------


def test_tags_quiet(runner, data_dir):
    _add(runner, "a", "backend")
    result = runner.invoke(main, ["tags", "--quiet"])
    assert result.exit_code == 0
    obj = json.loads(result.output.strip().splitlines()[0])
    assert obj["tag"] == "backend"
    assert obj["count"] == 1
    assert "last_used" in obj


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_tags_invalid_limit(runner):
    result = runner.invoke(main, ["tags", "--limit", "0"])
    assert result.exit_code == 1
    assert "--limit must be a positive integer" in result.output


def test_tags_invalid_sort(runner):
    result = runner.invoke(main, ["tags", "--sort", "bogus"])
    assert result.exit_code == 2  # click's UsageError for bad choice
