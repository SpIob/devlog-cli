"""Tests for the `devlog edit` command."""

import json
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
    """Set DEVLOG_DATA_DIR for direct storage calls in the test body."""
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
# Happy paths — flag-based edits
# ---------------------------------------------------------------------------


def test_edit_message_via_flag(runner, data_dir):
    sid = _add(runner, "Original message")
    result = runner.invoke(main, ["edit", sid, "--message", "New message"])
    assert result.exit_code == 0
    entries = storage.load_entries()
    assert len(entries) == 1
    assert entries[0].message == "New message"
    assert entries[0].updated_at is not None
    assert entries[0].updated_at >= entries[0].created_at


def test_edit_add_tags(runner, data_dir):
    sid = _add(runner, "msg", "backend")
    result = runner.invoke(main, ["edit", sid, "--add-tag", "urgent", "--add-tag", "backend"])
    assert result.exit_code == 0
    entries = storage.load_entries()
    assert set(entries[0].tags) == {"backend", "urgent"}


def test_edit_remove_tags(runner, data_dir):
    sid = _add(runner, "msg", "backend", "urgent", "docs")
    result = runner.invoke(main, ["edit", sid, "--remove-tag", "urgent"])
    assert result.exit_code == 0
    entries = storage.load_entries()
    assert set(entries[0].tags) == {"backend", "docs"}


def test_edit_set_tags_replaces(runner, data_dir):
    sid = _add(runner, "msg", "backend", "old")
    result = runner.invoke(main, ["edit", sid, "--tag", "frontend", "--tag", "new"])
    assert result.exit_code == 0
    entries = storage.load_entries()
    assert set(entries[0].tags) == {"frontend", "new"}


def test_edit_quiet(runner, data_dir):
    sid = _add(runner, "msg")
    result = runner.invoke(main, ["edit", sid, "--message", "updated", "--quiet"])
    assert result.exit_code == 0
    assert result.output.strip() == ""


# ---------------------------------------------------------------------------
# No-op
# ---------------------------------------------------------------------------


def test_edit_no_changes_reports_noop(runner):
    sid = _add(runner, "Same", "backend")
    result = runner.invoke(main, ["edit", sid, "--message", "Same", "--tag", "backend"])
    assert result.exit_code == 0
    assert "No changes" in result.output


def test_edit_no_op_with_quiet_suppresses_noop_message(runner, data_dir):
    """A no-op edit under --quiet must produce no output at all.

    Otherwise the user can never script `devlog edit -q -m X id` and
    tell success from no-op. The `No changes.` line is informational
    for an interactive user; under `-q` it must be silenced like every
    other success-path message.
    """
    sid = _add(runner, "Same", "backend")
    result = runner.invoke(
        main, ["edit", sid, "--message", "Same", "--tag", "backend", "--quiet"]
    )
    assert result.exit_code == 0
    assert "No changes" not in result.output
    assert result.output.strip() == ""


# ---------------------------------------------------------------------------
# Editor path
# ---------------------------------------------------------------------------


def test_edit_with_editor_existing_text(runner, data_dir, tmp_path, monkeypatch):
    """When no flags are passed, the editor is opened on the current message."""
    monkeypatch.setenv("DEVLOG_ALLOW_EDITOR_IN_PIPE", "1")  # drive editor in a pipe
    editor_script = tmp_path / "fake_editor.sh"
    editor_script.write_text("#!/bin/sh\necho 'edited via script' > \"$1\"\n")
    editor_script.chmod(0o755)
    monkeypatch.setenv("EDITOR", str(editor_script))

    sid = _add(runner, "original text")
    result = runner.invoke(main, ["edit", sid])
    assert result.exit_code == 0
    entries = storage.load_entries()
    assert entries[0].message == "edited via script"


def test_edit_editor_non_zero_exit(runner, data_dir, tmp_path, monkeypatch):
    """An editor that exits non-zero must surface an error and NOT save."""
    monkeypatch.setenv("DEVLOG_ALLOW_EDITOR_IN_PIPE", "1")  # drive editor in a pipe
    editor_script = tmp_path / "bad_editor.sh"
    editor_script.write_text("#!/bin/sh\nexit 1\n")
    editor_script.chmod(0o755)
    monkeypatch.setenv("EDITOR", str(editor_script))

    sid = _add(runner, "unchanged")
    result = runner.invoke(main, ["edit", sid])
    assert result.exit_code == 2
    assert "Editor exited abnormally" in result.output
    entries = storage.load_entries()
    assert entries[0].message == "unchanged"


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_edit_invalid_tag_chars(runner):
    sid = _add(runner, "msg")
    result = runner.invoke(main, ["edit", sid, "--tag", "bad tag"])
    assert result.exit_code == 1
    assert "invalid characters" in result.output


def test_edit_not_found(runner):
    result = runner.invoke(main, ["edit", "deadbeef", "--message", "x"])
    assert result.exit_code == 1
    assert "No entry found" in result.output


def test_edit_no_editor_configured(runner, data_dir, monkeypatch):
    """No $VISUAL / $EDITOR / fallback available → exit 1 with helpful msg."""
    import shutil
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setenv("DEVLOG_ALLOW_EDITOR_IN_PIPE", "1")  # bypass TTY guard
    monkeypatch.setattr(shutil, "which", lambda n: None)

    sid = _add(runner, "msg")
    result = runner.invoke(main, ["edit", sid])
    assert result.exit_code == 1
    assert "No editor configured" in result.output


def test_edit_rejects_empty_message_flag(runner, data_dir):
    """`edit -m ""` must be rejected (mirrors the `add` validation)."""
    sid = _add(runner, "original")
    result = runner.invoke(main, ["edit", sid, "-m", ""])
    assert result.exit_code == 1
    assert "MESSAGE cannot be empty" in result.output
    # Original message preserved.
    assert storage.load_entries()[0].message == "original"


def test_edit_rejects_whitespace_only_message_flag(runner, data_dir):
    """A whitespace-only -m is also rejected."""
    sid = _add(runner, "original")
    result = runner.invoke(main, ["edit", sid, "-m", "   "])
    assert result.exit_code == 1
    assert "MESSAGE cannot be empty" in result.output
    assert storage.load_entries()[0].message == "original"


def test_edit_editor_path_can_produce_empty_message(
    runner, data_dir, tmp_path, monkeypatch
):
    """When the user uses the editor (no -m flag), an empty body is
    allowed: a user might intentionally save a blank note via the
    editor. This guards against over-restricting the editor flow when
    adding the explicit -m validation.
    """
    monkeypatch.setenv("DEVLOG_ALLOW_EDITOR_IN_PIPE", "1")  # drive editor in a pipe
    editor_script = tmp_path / "blank_editor.sh"
    editor_script.write_text('#!/bin/sh\n: > "$1"\n')  # truncate
    editor_script.chmod(0o755)
    monkeypatch.setenv("EDITOR", str(editor_script))

    sid = _add(runner, "original")
    result = runner.invoke(main, ["edit", sid])
    assert result.exit_code == 0
    assert storage.load_entries()[0].message == ""


def test_edit_no_flags_in_non_tty_errors(runner, data_dir, monkeypatch):
    """`devlog edit <id>` with no flags in a non-TTY must error out
    cleanly instead of dumping terminal escapes from the editor.

    Regression: previously, in a non-TTY shell (CI, scripts, output
    captured to a file), `devlog edit` would silently spawn the
    fallback editor (nano/vi) which prints screen-clear sequences and
    fails, polluting the output.
    """
    # Ensure no opt-in env var is set.
    monkeypatch.delenv("DEVLOG_ALLOW_EDITOR_IN_PIPE", raising=False)
    # Belt-and-braces: no $VISUAL / $EDITOR either, so the message is
    # about TTY, not about a missing editor.
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)

    sid = _add(runner, "msg")
    result = runner.invoke(main, ["edit", sid])
    assert result.exit_code == 1
    assert "needs a TTY" in result.output


def test_edit_no_flags_in_non_tty_with_force_env_works(
    runner, data_dir, tmp_path, monkeypatch
):
    """Setting DEVLOG_ALLOW_EDITOR_IN_PIPE=1 lets scripts drive the
    editor in a pipe (the previous behavior).
    """
    monkeypatch.setenv("DEVLOG_ALLOW_EDITOR_IN_PIPE", "1")
    editor_script = tmp_path / "fake_editor.sh"
    editor_script.write_text('#!/bin/sh\necho "edited via pipe" > "$1"\n')
    editor_script.chmod(0o755)
    monkeypatch.setenv("EDITOR", str(editor_script))

    sid = _add(runner, "original")
    result = runner.invoke(main, ["edit", sid])
    assert result.exit_code == 0
    assert storage.load_entries()[0].message == "edited via pipe"


def test_edit_with_flag_in_non_tty_works(runner, data_dir):
    """Sanity: explicit -m / -t / etc. flags bypass the TTY guard, so
    scripts can edit entries without an interactive editor.
    """
    sid = _add(runner, "original")
    result = runner.invoke(main, ["edit", sid, "-m", "new text"])
    assert result.exit_code == 0
    assert storage.load_entries()[0].message == "new text"


# ---------------------------------------------------------------------------
# --at (backfill / re-time)
# ---------------------------------------------------------------------------


def test_edit_at_with_yes_changes_created_at(runner, data_dir):
    """`edit --at … --yes` changes created_at and updates updated_at."""
    sid = _add(runner, "msg")
    original_created = storage.load_entries()[0].created_at
    result = runner.invoke(
        main, ["edit", sid, "--at", "2025-01-15T09:00:00Z", "--yes"]
    )
    assert result.exit_code == 0
    entry = storage.load_entries()[0]
    assert entry.created_at == "2025-01-15T09:00:00Z"
    assert entry.created_at != original_created
    # updated_at is set to the new "now".
    assert entry.updated_at is not None
    # updated_at is recent (within the last 60 seconds).
    from datetime import datetime, timezone, timedelta
    upd = datetime.strptime(entry.updated_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert (datetime.now(tz=timezone.utc) - upd) < timedelta(seconds=60)


def test_edit_at_prompts_and_decline_aborts(runner, data_dir):
    """Without --yes, a `--at` change prompts; declining aborts."""
    from click.testing import CliRunner
    runner = CliRunner(env={"DEVLOG_DATA_DIR": str(data_dir)})
    sid = _add(runner, "msg")
    # Decline the prompt by feeding 'n' to stdin.
    result = runner.invoke(
        main, ["edit", sid, "--at", "2025-01-15T09:00:00Z"],
        input="n",
    )
    assert result.exit_code == 0
    assert "Aborted" in result.output
    # Storage must be unchanged.
    entry = storage.load_entries()[0]
    assert entry.created_at != "2025-01-15T09:00:00Z"


def test_edit_at_prompts_and_accept_writes(runner, data_dir):
    """Without --yes, accepting the prompt with 'y' writes the change."""
    sid = _add(runner, "msg")
    result = runner.invoke(
        main, ["edit", sid, "--at", "2025-01-15T09:00:00Z"],
        input="y",
    )
    assert result.exit_code == 0
    assert storage.load_entries()[0].created_at == "2025-01-15T09:00:00Z"


def test_edit_at_relative(runner, data_dir):
    """`--at 2h` works on edit too."""
    from datetime import datetime, timezone, timedelta
    sid = _add(runner, "msg")
    before = datetime.now(tz=timezone.utc)
    result = runner.invoke(main, ["edit", sid, "--at", "2h", "--yes"])
    assert result.exit_code == 0
    after = datetime.now(tz=timezone.utc)
    entry = storage.load_entries()[0]
    ts = datetime.strptime(entry.created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert abs((before - ts) - timedelta(hours=2)) < timedelta(seconds=5)


def test_edit_at_invalid(runner, data_dir):
    sid = _add(runner, "msg")
    result = runner.invoke(main, ["edit", sid, "--at", "garbage", "--yes"])
    assert result.exit_code == 2
    assert "Invalid --at" in result.output


def test_edit_at_with_message_and_tags(runner, data_dir):
    """`--at` combines with `-m` and `--add-tag` correctly."""
    sid = _add(runner, "msg", "x")
    result = runner.invoke(
        main,
        ["edit", sid, "-m", "new", "--add-tag", "y", "--at", "2025-01-15T09:00:00Z", "--yes"],
    )
    assert result.exit_code == 0
    entry = storage.load_entries()[0]
    assert entry.message == "new"
    assert "x" in entry.tags
    assert "y" in entry.tags
    assert entry.created_at == "2025-01-15T09:00:00Z"


def test_edit_at_same_value_is_noop(runner, data_dir):
    """If `--at` resolves to the same value the entry already has, the
    edit is a no-op (and the prompt is skipped)."""
    sid = _add(runner, "msg")
    current = storage.load_entries()[0].created_at
    # Pass the same instant back; no --yes needed because no real change.
    result = runner.invoke(main, ["edit", sid, "--at", current])
    assert result.exit_code == 0
    assert "No changes" in result.output
    # Storage unchanged.
    assert storage.load_entries()[0].created_at == current

