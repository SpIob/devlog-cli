"""Tests for the `devlog export` command."""

from pathlib import Path
from unittest.mock import patch

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
# Happy path
# ---------------------------------------------------------------------------


def test_export_creates_markdown(runner, tmp_path):
    _add(runner, "Deploy to production", "ops")
    out = tmp_path / "out.md"
    result = runner.invoke(main, ["export", "--output", str(out), "--quiet"])
    assert result.exit_code == 0
    content = out.read_text(encoding="utf-8")
    assert "## " in content
    assert "Deploy to production" in content
    assert "**Tags:**" in content
    assert "---" in content


def test_export_separator_and_structure(runner, tmp_path):
    _add(runner, "First entry", "backend")
    _add(runner, "Second entry", "frontend")
    out = tmp_path / "two.md"
    runner.invoke(main, ["export", "--output", str(out), "--quiet"])
    content = out.read_text(encoding="utf-8")
    assert content.count("---") == 2
    assert content.count("**Tags:**") == 2


def test_export_no_tags_shows_none(runner, tmp_path):
    _add(runner, "Entry without tags")
    out = tmp_path / "notags.md"
    runner.invoke(main, ["export", "--output", str(out), "--quiet"])
    assert "(none)" in out.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------


def test_export_no_entries(runner, tmp_path):
    out = tmp_path / "empty.md"
    result = runner.invoke(main, ["export", "--output", str(out)])
    assert result.exit_code == 0
    assert "Warning: No entries to export" in result.output


# ---------------------------------------------------------------------------
# Tag filter
# ---------------------------------------------------------------------------


def test_export_tag_filter(runner, tmp_path):
    _add(runner, "Backend work", "backend")
    _add(runner, "Frontend work", "frontend")
    out = tmp_path / "filtered.md"
    runner.invoke(main, ["export", "--output", str(out), "-t", "backend", "--quiet"])
    content = out.read_text(encoding="utf-8")
    assert "Backend work" in content
    assert "Frontend work" not in content


# ---------------------------------------------------------------------------
# Quiet mode
# ---------------------------------------------------------------------------


def test_export_quiet_prints_path(runner, tmp_path):
    _add(runner, "Some entry")
    out = tmp_path / "quiet.md"
    result = runner.invoke(main, ["export", "--output", str(out), "--quiet"])
    assert result.exit_code == 0
    assert str(out) in result.output


# ---------------------------------------------------------------------------
# Permission error
# ---------------------------------------------------------------------------


def test_export_unwritable_path(runner, tmp_path):
    _add(runner, "Something")
    # Pass a path in a nonexistent deep directory to force an OSError
    bad_path = "/root/no_permission_here/devlog.md"
    result = runner.invoke(main, ["export", "--output", bad_path, "--quiet"])
    assert result.exit_code == 2
    assert "Cannot write to" in result.output