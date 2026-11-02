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


# ---------------------------------------------------------------------------
# Empty / whitespace-only query
# ---------------------------------------------------------------------------


def test_search_empty_query_errors(runner):
    """An empty QUERY is a user error, not "match every entry".

    `click.argument` does not reject an empty string at the parser
    level, so without an explicit guard `devlog search ""` returns
    every entry with the awkward subtitle ``Query: ""``. Treat it as
    a usage error instead so users get actionable feedback.
    """
    _add(runner, "alpha")
    _add(runner, "beta")
    result = runner.invoke(main, ["search", ""])
    assert result.exit_code == 1
    assert "QUERY" in result.output
    # The full entry list must not leak through.
    assert "alpha" not in result.output
    assert "beta" not in result.output


def test_search_whitespace_only_query_errors(runner):
    """A whitespace-only QUERY is also a usage error.

    Otherwise the user would see the equally-awkward
    ``No entries matched "   "`` when they intended to type a real
    query.
    """
    _add(runner, "alpha")
    result = runner.invoke(main, ["search", "   "])
    assert result.exit_code == 1
    assert "QUERY" in result.output


# ---------------------------------------------------------------------------
# AND semantics / quoted phrases (Tier 1)
# ---------------------------------------------------------------------------


def test_search_and_semantics(runner):
    """Multiple space-separated terms require ALL terms (AND)."""
    _add(runner, "Fixed auth bug in login")
    _add(runner, "Fixed auth module")
    _add(runner, "Fixed login page")
    # "auth login" should match only the first entry (has both terms)
    result = runner.invoke(main, ["search", "auth login"])
    assert result.exit_code == 0
    assert "auth bug in login" in result.output
    assert "auth module" not in result.output
    assert "login page" not in result.output
    assert "Showing 1 of" in result.output


def test_search_quoted_phrase(runner):
    """Quoted phrases match as a single term."""
    _add(runner, "Fixed the login bug")
    _add(runner, "Fixed login page")
    _add(runner, "Login authentication")
    # Exact phrase "login bug" should match only first entry
    result = runner.invoke(main, ["search", '"login bug"'])
    assert result.exit_code == 0
    assert "login bug" in result.output
    assert "login page" not in result.output
    assert "Login authentication" not in result.output
    assert "Showing 1 of" in result.output


def test_search_quoted_phrase_with_other_terms(runner):
    """Quoted phrase combined with other terms (AND)."""
    _add(runner, "Fixed the login bug in auth")
    _add(runner, "Fixed the login page")
    # "login bug" AND "auth" should match only first
    result = runner.invoke(main, ['search', '"login bug" auth'])
    assert result.exit_code == 0
    assert "login bug in auth" in result.output
    assert "login page" not in result.output


# ---------------------------------------------------------------------------
# Regex mode (Tier 1)
# ---------------------------------------------------------------------------


def test_search_regex_basic(runner):
    """--regex interprets query as a regex pattern."""
    _add(runner, "Fix bug #123")
    _add(runner, "Fix bug #456")
    _add(runner, "Fixed bug")
    result = runner.invoke(main, ["search", "--regex", r"^Fix bug #\d+$"])
    assert result.exit_code == 0
    assert "Fix bug #123" in result.output
    assert "Fix bug #456" in result.output
    assert "Fixed bug" not in result.output
    assert "Showing 2 of" in result.output


def test_search_regex_case_insensitive(runner):
    """--regex is case-insensitive."""
    _add(runner, "AUTH service")
    _add(runner, "auth module")
    _add(runner, "Auth UI")
    result = runner.invoke(main, ["search", "--regex", "auth"])
    assert result.exit_code == 0
    assert "Showing 3 of" in result.output


def test_search_regex_invalid(runner):
    """Invalid regex shows error and exits 2."""
    _add(runner, "test")
    result = runner.invoke(main, ["search", "--regex", "[invalid"])
    assert result.exit_code == 2
    assert "Invalid regex" in result.output