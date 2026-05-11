"""Tests for the `devlog search` command."""

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
# No matches
# ---------------------------------------------------------------------------


def test_search_no_match(runner):
    _add(runner, "Deploy pipeline")
    result = runner.invoke(main, ["search", "xyzzy"])
    assert result.exit_code == 0
    assert 'No entries matched "xyzzy"' in result.output


# ---------------------------------------------------------------------------
# Matching & highlighting
# ---------------------------------------------------------------------------


def test_search_finds_entry(runner):
    _add(runner, "Fixed the null pointer bug")
    result = runner.invoke(main, ["search", "null"])
    assert result.exit_code == 0
    assert "null" in result.output.lower()


def test_search_case_insensitive(runner):
    _add(runner, "Fixed Auth Module")
    result = runner.invoke(main, ["search", "auth"])
    assert result.exit_code == 0
    assert "Showing 1 of 1" in result.output


def test_search_highlighting_markup(runner):
    _add(runner, "Refactor auth service")
    result = runner.invoke(main, ["search", "auth"])
    assert result.exit_code == 0
    assert "auth" in result.output.lower()


# ---------------------------------------------------------------------------
# Tag filter combined with search
# ---------------------------------------------------------------------------


def test_search_with_tag_filter(runner):
    _add(runner, "Auth refactor", "backend")
    _add(runner, "Auth UI update", "frontend")
    result = runner.invoke(main, ["search", "auth", "-t", "backend"])
    assert "Auth refactor" in result.output
    assert "Auth UI update" not in result.output


# ---------------------------------------------------------------------------
# Limit
# ---------------------------------------------------------------------------


def test_search_limit(runner):
    for i in range(10):
        _add(runner, f"Fix bug {i}")
    result = runner.invoke(main, ["search", "Fix", "--limit", "3"])
    assert "Showing 3 of 10" in result.output


def test_search_invalid_limit(runner):
    result = runner.invoke(main, ["search", "anything", "--limit", "-1"])
    assert result.exit_code == 1
    assert "--limit must be a positive integer" in result.output


# ---------------------------------------------------------------------------
# Quiet mode
# ---------------------------------------------------------------------------


def test_search_quiet(runner):
    _add(runner, "Cache invalidation fix")
    result = runner.invoke(main, ["search", "Cache", "--quiet"])
    assert result.exit_code == 0
    obj = json.loads(result.output.strip().splitlines()[0])
    assert "Cache" in obj["message"]