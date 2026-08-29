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


def test_rename_tag_rejects_invalid_new_without_silent_noop(runner, data_dir):
    """An invalid NEW tag (uppercase, spaces, oversize) must error out
    rather than silently no-op via the 'OLD and NEW are the same' path.

    Regression test: previously, `rename-tag x INFRA` would lowercase
    NEW to `infra`, compare to OLD, and emit the misleading
    'OLD and NEW are the same' message while changing nothing.
    """
    _seed("e1111111-1111-1111-1111-111111111111", "msg", tags=["x"])
    result = runner.invoke(main, ["rename-tag", "x", "INFRA"])
    assert result.exit_code == 1
    assert "invalid characters" in result.output
    # Entry tag list untouched.
    assert storage.load_entries()[0].tags == ["x"]

    result = runner.invoke(main, ["rename-tag", "x", "bad tag"])
    assert result.exit_code == 1
    assert "invalid characters" in result.output

    long_new = "a" * 33
    result = runner.invoke(main, ["rename-tag", "x", long_new])
    assert result.exit_code == 1
    assert "exceeds maximum length" in result.output


def test_import_reports_unreadable_rows(runner, data_dir, tmp_path):
    """JSON imports must surface a count of unreadable rows instead of
    silently swallowing them as 'No entries to import.'

    Regression: a JSON file containing a mix of valid and broken
    entries used to print the same message as a totally empty file,
    hiding the data-quality issue from the user.
    """
    src = tmp_path / "mixed.json"
    src.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "11111111-1111-1111-1111-111111111111",
                        "message": "good",
                        "tags": [],
                        "created_at": "2026-08-29T00:00:00Z",
                        "updated_at": None,
                    },
                    "not a dict",
                    {"id": "x", "missing": "fields"},
                ]
            }
        )
    )
    result = runner.invoke(main, ["import", str(src)])
    assert result.exit_code == 0
    # The single valid row was imported.
    assert "Imported 1 entry" in result.output
    # And the unreadable rows are reported.
    assert "Ignored 2 unreadable rows" in result.output





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
    # Stable ids in the source are preserved across import.
    assert entries[0].id == "json-1"


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


def test_import_sniffs_format_for_extensionless_file(runner, data_dir, tmp_path):
    """An extensionless file is auto-detected by content (leading `{`
    → JSON, leading `#` → Markdown). Useful for stdin pipes."""
    # JSON content, no extension.
    src_json = tmp_path / "no-ext-data"
    src_json.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "sniff-1",
                        "message": "sniffed from {",
                        "tags": [],
                        "created_at": "2025-01-01T00:00:00Z",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    result = runner.invoke(main, ["import", str(src_json)])
    assert result.exit_code == 0
    assert "Imported 1 entry" in result.output

    # Markdown content, no extension.
    src_md = tmp_path / "no-ext-notes"
    src_md.write_text(
        "## 2025-01-01 00:00 UTC — 12345678\n\n"
        "sniffed from #\n\n"
        "**Tags:** (none)\n\n"
        "---\n",
        encoding="utf-8",
    )
    result = runner.invoke(main, ["import", str(src_md)])
    assert result.exit_code == 0
    assert "Imported 1 entry" in result.output

    # Unrecognised content with no extension errors helpfully.
    src_garbage = tmp_path / "garbage"
    src_garbage.write_text("not markdown, not json", encoding="utf-8")
    result = runner.invoke(main, ["import", str(src_garbage)])
    assert result.exit_code == 2
    assert "Cannot auto-detect format" in result.output


def test_import_preserves_stable_ids(runner, data_dir, tmp_path):
    """When a JSON import carries a non-empty `id`, devlog preserves it
    instead of minting a fresh uuid. This makes re-imports and
    backup → restore → import round-trips idempotent at the id level.

    Regression: previously, every imported entry got a brand-new uuid,
    so a JSON export → import cycle silently changed every short id.
    """
    from devlog import storage as _storage

    src = tmp_path / "src.json"
    src.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "stable-aaaa-bbbb-cccc-dddddddddddd",
                        "message": "keep my id",
                        "tags": ["x"],
                        "created_at": "2025-01-01T00:00:00Z",
                        "updated_at": None,
                    },
                    {
                        # No id at all → devlog mints one.
                        "message": "i have no id",
                        "tags": [],
                        "created_at": "2025-01-02T00:00:00Z",
                        "updated_at": None,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    result = runner.invoke(main, ["import", str(src)])
    assert result.exit_code == 0
    entries = _storage.load_entries()
    by_msg = {e.message: e for e in entries}
    assert by_msg["keep my id"].id == "stable-aaaa-bbbb-cccc-dddddddddddd"
    assert by_msg["i have no id"].id  # minted, non-empty

    # Re-import the same file: every row is now a duplicate so the
    # store is unchanged.
    before = sorted(e.id for e in entries)
    result = runner.invoke(main, ["import", str(src)])
    assert "No new entries to import" in result.output
    assert "2 duplicate" in result.output
    after = sorted(e.id for e in _storage.load_entries())
    assert before == after


# ---------------------------------------------------------------------------
# completions
# ---------------------------------------------------------------------------


def test_completions_bash(runner):
    result = runner.invoke(main, ["completions", "bash"])
    assert result.exit_code == 0
    assert "_devlog_completion" in result.output
    assert "complete -F" in result.output
    # Regression: 'theme' was missing from the bash command list.
    assert "theme" in result.output
    # And 'theme' sub-commands are offered after `devlog theme `.
    assert 'COMPREPLY=($(compgen -W "list show set path"' in result.output


def test_completions_zsh(runner):
    result = runner.invoke(main, ["completions", "zsh"])
    assert result.exit_code == 0
    assert "#compdef devlog" in result.output
    assert "'add" in result.output
    # Regression: 'theme' was missing from the zsh command list.
    assert "'theme:" in result.output


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
    # Every command group + its sub-commands should be discoverable.
    for cmd in [
        "add", "show", "edit", "delete", "list", "search", "today",
        "tail", "tags", "stats", "rename-tag", "import", "completions",
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
