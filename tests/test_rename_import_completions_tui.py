"""Tests for the `devlog rename-tag`, `devlog import`, `devlog completions`, and the interactive REPL."""

import json
import os
import re
import sys
from datetime import datetime, timezone

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


def _seed(entry_id, message, created_at="2025-01-01T12:00:00Z", tags=None):
    storage.add_entry(
        Entry(
            id=entry_id,
            message=message,
            tags=list(tags) if tags else [],
            created_at=created_at,
        )
    )


# ---------------------------------------------------------------------------
# rename-tag
# ---------------------------------------------------------------------------


def test_rename_tag_happy(runner, data_dir):
    _seed("a1111111-1111-1111-1111-111111111111", "msg", tags=["backend"])
    _seed("a2222222-2222-2222-2222-222222222222", "msg2", tags=["backend", "bugfix"])
    result = runner.invoke(main, ["rename-tag", "backend", "devops"])
    assert result.exit_code == 0
    assert "Renamed" in result.output
    # Verify
    entries = storage.load_entries()
    for e in entries:
        assert "backend" not in e.tags
        assert "devops" in e.tags
        assert e.updated_at is not None


def test_rename_tag_dedupes_when_new_already_present(runner, data_dir):
    """If an entry already has NEW, removing OLD must not duplicate NEW."""
    _seed(
        "b1111111-1111-1111-1111-111111111111",
        "msg",
        tags=["old", "new"],
    )
    result = runner.invoke(main, ["rename-tag", "old", "new"])
    assert result.exit_code == 0
    entries = storage.load_entries()
    assert entries[0].tags == ["new"]


def test_rename_tag_dry_run_does_not_write(runner, data_dir):
    _seed("c1111111-1111-1111-1111-111111111111", "msg", tags=["x"])
    result = runner.invoke(main, ["rename-tag", "x", "y", "--dry-run"])
    assert result.exit_code == 0
    assert "DRY RUN" in result.output
    # File must be unchanged
    entries = storage.load_entries()
    assert entries[0].tags == ["x"]
    assert entries[0].updated_at is None


def test_rename_tag_no_match(runner, data_dir):
    _seed("d1111111-1111-1111-1111-111111111111", "msg", tags=["a"])
    result = runner.invoke(main, ["rename-tag", "nonexistent", "x"])
    assert result.exit_code == 0
    assert "No entries with tag" in result.output


def test_rename_tag_invalid_new(runner):
    result = runner.invoke(main, ["rename-tag", "old", "bad tag"])
    assert result.exit_code == 1
    assert "invalid characters" in result.output


def test_rename_tag_same_old_and_new(runner, data_dir):
    _seed("e1111111-1111-1111-1111-111111111111", "msg", tags=["x"])
    result = runner.invoke(main, ["rename-tag", "x", "x"])
    assert result.exit_code == 0
    assert "OLD and NEW are the same" in result.output


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------


def test_import_json(runner, data_dir, tmp_path):
    src = tmp_path / "src.json"
    src.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "json-1",
                        "message": "from json",
                        "tags": ["a"],
                        "created_at": "2025-01-01T00:00:00Z",
                        "updated_at": None,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    result = runner.invoke(main, ["import", str(src)])
    assert result.exit_code == 0
    assert "Imported 1 entry" in result.output
    entries = storage.load_entries()
    assert len(entries) == 1
    assert entries[0].message == "from json"
    # New id must be a fresh UUID, not "json-1"
    assert entries[0].id != "json-1"


def test_import_markdown(runner, data_dir, tmp_path):
    md = tmp_path / "src.md"
    md.write_text(
        "## 2025-01-01 09:00 UTC — abc12345\n\n"
        "Hello world.\n\n"
        "**Tags:** backend, docs\n\n"
        "---\n",
        encoding="utf-8",
    )
    result = runner.invoke(main, ["import", str(md)])
    assert result.exit_code == 0
    entries = storage.load_entries()
    assert len(entries) == 1
    assert entries[0].message == "Hello world."
    assert set(entries[0].tags) == {"backend", "docs"}


def test_import_idempotent(runner, data_dir, tmp_path):
    _seed("f1111111-1111-1111-1111-111111111111", "existing", created_at="2025-01-01T00:00:00Z")
    md = tmp_path / "src.md"
    md.write_text(
        "## 2025-01-01 00:00 UTC — f1111111\n\n"
        "existing\n\n"
        "**Tags:** (none)\n\n"
        "---\n",
        encoding="utf-8",
    )
    result = runner.invoke(main, ["import", str(md)])
    assert result.exit_code == 0
    assert "1 duplicate" in result.output
    assert storage.load_entries()[0].message == "existing"


def test_import_dry_run(runner, data_dir, tmp_path):
    src = tmp_path / "src.json"
    src.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "json-x",
                        "message": "x",
                        "tags": [],
                        "created_at": "2025-01-01T00:00:00Z",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    result = runner.invoke(main, ["import", str(src), "--dry-run"])
    assert result.exit_code == 0
    assert "DRY RUN" in result.output
    assert storage.load_entries() == []


def test_import_malformed_json(runner, tmp_path):
    src = tmp_path / "bad.json"
    src.write_text("not json at all", encoding="utf-8")
    result = runner.invoke(main, ["import", str(src)])
    assert result.exit_code == 2
    assert "Invalid JSON" in result.output


def test_import_unreadable_path(runner):
    result = runner.invoke(main, ["import", "/nonexistent/path.json"])
    assert result.exit_code == 2  # click Path(exists=True) catches it


def test_import_auto_detect_format(runner, data_dir, tmp_path):
    """Without --format, .json should be auto-detected."""
    src = tmp_path / "auto.json"
    src.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "auto-1",
                        "message": "auto-detected",
                        "tags": [],
                        "created_at": "2025-01-01T00:00:00Z",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    result = runner.invoke(main, ["import", str(src)])
    assert result.exit_code == 0
    assert "Imported 1 entry" in result.output


# ---------------------------------------------------------------------------
# completions
# ---------------------------------------------------------------------------


def test_completions_bash(runner):
    result = runner.invoke(main, ["completions", "bash"])
    assert result.exit_code == 0
    assert "_devlog_completion" in result.output
    assert "complete -F" in result.output


def test_completions_zsh(runner):
    result = runner.invoke(main, ["completions", "zsh"])
    assert result.exit_code == 0
    assert "#compdef devlog" in result.output
    assert "'add" in result.output


def test_completions_fish(runner):
    result = runner.invoke(main, ["completions", "fish"])
    assert result.exit_code == 0
    assert "complete -c devlog" in result.output


def test_completions_invalid_shell(runner):
    result = runner.invoke(main, ["completions", "tcsh"])
    # Click's Choice raises UsageError → exit 2
    assert result.exit_code == 2


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
    if result.exit_code != 0:
        print("\n--- STDOUT ---\n" + result.output)
        if result.exception and not isinstance(result.exception, SystemExit):
            import traceback
            traceback.print_exception(type(result.exception), result.exception, result.exception.__traceback__)
    assert result.exit_code == 0


def test_interactive_help(monkeypatch, tmp_path):
    """'help' at the prompt should list the available commands."""
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DEVLOG_INTERACTIVE_FORCE", "1")

    from rich import prompt as _rp
    responses = iter(["help", "q"])
    monkeypatch.setattr(_rp.Prompt, "ask", lambda *a, **kw: next(responses))

    result = CliRunner().invoke(main, ["--interactive"])
    if result.exit_code != 0:
        print("\n--- STDOUT ---\n" + result.output)
        if result.exception and not isinstance(result.exception, SystemExit):
            import traceback
            traceback.print_exception(type(result.exception), result.exception, result.exception.__traceback__)
    assert result.exit_code == 0
    assert "Available commands" in result.output
    assert "rename-tag" in result.output
    assert "import" in result.output


def test_interactive_env_var(monkeypatch, tmp_path):
    """DEVLOG_INTERACTIVE=1 enables interactive mode (still requires TTY unless FORCE)."""
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DEVLOG_INTERACTIVE", "1")
    # No FORCE → still hit the TTY check (which CliRunner's stdin fails).

    result = CliRunner().invoke(main, [])
    assert result.exit_code == 1
    assert "Interactive mode requires a TTY" in result.output
