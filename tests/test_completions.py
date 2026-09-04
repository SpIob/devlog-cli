"""Tests for the `devlog completions` command."""

import pytest

from devlog.cli import main


# ---------------------------------------------------------------------------
# completions
# ---------------------------------------------------------------------------


def test_completions_bash(runner):
    result = runner.invoke(main, ["completions", "bash"])
    assert result.exit_code == 0
    assert "_devlog_completion()" in result.output
    assert "complete -F _devlog_completion devlog" in result.output


def test_completions_zsh(runner):
    result = runner.invoke(main, ["completions", "zsh"])
    assert result.exit_code == 0
    assert "#compdef devlog" in result.output
    assert "_describe 'command' commands" in result.output


def test_completions_fish(runner):
    result = runner.invoke(main, ["completions", "fish"])
    assert result.exit_code == 0
    assert "complete -c devlog -f" in result.output


def test_completions_invalid_shell(runner):
    result = runner.invoke(main, ["completions", "invalid"])
    assert result.exit_code != 0
    # Click's Choice validator catches this with a default error message.
    assert "not one of" in result.output or "Unsupported shell" in result.output


def test_completions_include_all_commands(runner):
    """All commands from COMMANDS should appear in bash completion."""
    result = runner.invoke(main, ["completions", "bash"])
    assert "add" in result.output
    assert "show" in result.output
    assert "edit" in result.output
    assert "delete" in result.output
    assert "list" in result.output
    assert "search" in result.output
    assert "today" in result.output
    assert "yesterday" in result.output
    assert "week" in result.output
    assert "tail" in result.output
    assert "tags" in result.output
    assert "tag" in result.output
    assert "merge-tag" in result.output
    assert "rename-tag" in result.output
    assert "theme" in result.output
    assert "stats" in result.output
    assert "calendar" in result.output
    assert "import" in result.output
    assert "export" in result.output
    assert "completions" in result.output
    assert "repair" in result.output
    assert "backup" in result.output
    assert "restore" in result.output
    assert "doctor" in result.output