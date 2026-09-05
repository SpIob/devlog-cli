"""Tests for the `devlog --interactive` REPL."""

import pytest
from click.testing import CliRunner

from devlog.cli import main
from devlog import storage
from devlog.models import Entry


# ---------------------------------------------------------------------------
# interactive REPL
# ---------------------------------------------------------------------------


def test_interactive_no_tty_errors(runner, tmp_path):
    """Without a TTY, --interactive should error out cleanly."""
    result = runner.invoke(main, ["--interactive"])
    assert result.exit_code == 1
    assert "Interactive mode requires a TTY" in result.output


def test_interactive_quit_via_stdin(monkeypatch, tmp_path):
    """When stdin is a TTY (mocked), 'q' should exit cleanly."""
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DEVLOG_INTERACTIVE_FORCE", "1")  # bypass TTY check for tests

    from rich import prompt as _rp
    monkeypatch.setattr(_rp.Prompt, "ask", lambda *a, **kw: "q")

    result = CliRunner().invoke(main, ["--interactive"])
    assert result.exit_code == 0


def test_interactive_help(monkeypatch, tmp_path):
    """'help' at the prompt should list the available commands."""
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DEVLOG_INTERACTIVE_FORCE", "1")

    from rich import prompt as _rp
    responses = iter(["help", "q"])
    monkeypatch.setattr(_rp.Prompt, "ask", lambda *a, **kw: next(responses))

    result = CliRunner().invoke(main, ["--interactive"])
    assert result.exit_code == 0
    # Help shows the available commands (either explicit "Available commands" or
    # the command listing itself)
    assert "Available commands" in result.output or "add" in result.output
    # Every command group + its sub-commands should be discoverable.
    for cmd in [
        "add", "show", "edit", "delete", "list", "search", "today",
        "yesterday", "week", "tail", "tags", "tag", "merge-tag", "stats",
        "calendar", "rename-tag", "import", "completions",
        "export", "repair", "backup", "restore", "doctor", "theme",
    ]:
        assert cmd in result.output, f"REPL help missing command: {cmd}"


def test_interactive_env_var(monkeypatch, tmp_path):
    """DEVLOG_INTERACTIVE=1 enables interactive mode (still requires TTY unless FORCE)."""
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DEVLOG_INTERACTIVE", "1")
    # No FORCE → still hit the TTY check (which CliRunner's stdin fails).

    result = CliRunner().invoke(main, [])
    assert result.exit_code == 1
    assert "Interactive mode requires a TTY" in result.output


def test_interactive_add_dispatches_to_cli(data_dir, monkeypatch):
    """The REPL must invoke sub-commands through Click's CliRunner.

    Regression: CliRunner(mix_stderr=False) was removed in Click 8.2+;
    every REPL sub-command used to crash with a TypeError.
    """
    monkeypatch.setenv("DEVLOG_INTERACTIVE_FORCE", "1")

    from rich import prompt as _rp
    # Quoted message so shlex doesn't split it across argv tokens.
    responses = iter(['add "hello from repl" -t repltest', "q"])
    monkeypatch.setattr(_rp.Prompt, "ask", lambda *a, **kw: next(responses))

    # No `env=` here so the inner CliRunner() inside the REPL inherits
    # DEVLOG_DATA_DIR from monkeypatch-set os.environ.
    result = CliRunner().invoke(main, ["--interactive"])
    assert result.exit_code == 0
    # No TypeError leak
    assert "TypeError" not in result.output
    assert "mix_stderr" not in result.output
    # Sub-command actually ran
    entries = storage.load_entries()
    assert len(entries) == 1
    assert entries[0].message == "hello from repl"
    assert entries[0].tags == ["repltest"]


def test_interactive_failed_command_prints_error_panel(monkeypatch, tmp_path):
    """A failing sub-command must surface an error panel so the user
    can distinguish it from successful output at a glance.

    Regression: previously, a typo like ``lis`` would dump the Click
    usage block with no visual cue that it was a failure, making the
    REPL confusing.
    """
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DEVLOG_INTERACTIVE_FORCE", "1")

    from rich import prompt as _rp
    # ``lis`` is not a real command. Click will exit 2 with usage text.
    responses = iter(["lis", "q"])
    monkeypatch.setattr(_rp.Prompt, "ask", lambda *a, **kw: next(responses))

    result = CliRunner().invoke(main, ["--interactive"])
    assert result.exit_code == 0  # the REPL itself does not exit
    # The error panel must be present, prefixed to the failed output.
    assert "✘" in result.output
    assert "failed" in result.output
    # The original Click usage text must still appear below the panel.
    assert "Usage" in result.output or "No such command" in result.output