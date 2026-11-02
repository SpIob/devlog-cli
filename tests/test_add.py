"""Tests for the `devlog add` command."""

from unittest.mock import patch

from devlog import storage
from devlog.cli import main
from devlog.storage import StoragePermissionError

# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_add_no_tags(runner):
    result = runner.invoke(main, ["add", "Fixed the login bug"])
    assert result.exit_code == 0
    assert "✔" in result.output
    assert "Fixed the login bug" in result.output


def test_add_with_tags(runner):
    result = runner.invoke(main, ["add", "Deploy pipeline", "-t", "CI", "-t", "backend"])
    assert result.exit_code == 0
    # tags normalised to lowercase
    assert "ci" in result.output
    assert "backend" in result.output


def test_add_tags_deduplicated(runner, tmp_path, monkeypatch):
    from devlog import storage

    # Align the in-process env with the runner's data dir so
    # storage.load_entries() reads what the CLI wrote (CliRunner's env
    # override only lasts for the invoke itself).
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    runner.invoke(main, ["add", "Dup tags", "-t", "backend", "-t", "Backend"])
    # Read via the storage API (backend-agnostic) rather than parsing
    # entries.json directly, so the test holds on both backends.
    entries = storage.load_entries()
    assert len(entries) == 1
    assert entries[0].tags.count("backend") == 1


def test_add_quiet(runner):
    result = runner.invoke(main, ["add", "Silent entry", "-q"])
    assert result.exit_code == 0
    assert result.output.strip() == ""


# ---------------------------------------------------------------------------
# Validation errors (exit 1)
# ---------------------------------------------------------------------------


def test_add_empty_message(runner):
    result = runner.invoke(main, ["add", ""])
    assert result.exit_code == 1
    assert "MESSAGE cannot be empty" in result.output


def test_add_rejects_whitespace_only_message(runner):
    """Whitespace-only messages should be rejected like empty ones.

    Regression: previously ``devlog add "   "`` was accepted and
    created a blank entry, polluting search/stats and contradicting
    the empty-string rejection at the same code path.
    """
    result = runner.invoke(main, ["add", "   "])
    assert result.exit_code == 1
    assert "MESSAGE cannot be empty" in result.output


def test_add_invalid_tag_chars(runner):
    result = runner.invoke(main, ["add", "Msg", "-t", "bad tag"])
    assert result.exit_code == 1
    assert "invalid characters" in result.output
    assert '"bad tag"' in result.output


def test_add_tag_too_long(runner):
    long_tag = "a" * 33
    result = runner.invoke(main, ["add", "Msg", "-t", long_tag])
    assert result.exit_code == 1
    assert "exceeds maximum length" in result.output


def test_add_too_many_tags(runner):
    tags = [f"tag{i}" for i in range(11)]
    args = ["add", "Msg"] + [arg for t in tags for arg in ("-t", t)]
    result = runner.invoke(main, args)
    assert result.exit_code == 1
    assert "Maximum 10 tags" in result.output


# ---------------------------------------------------------------------------
# Storage errors (exit 2)
# ---------------------------------------------------------------------------


def test_add_storage_permission_error(runner, tmp_path):
    import os

    if os.environ.get("DEVLOG_BACKEND", "json").lower() == "sqlite":
        # SQLite add path writes via sqlite_storage.add_entry (single
        # INSERT, no save_entries round-trip).
        target = "devlog.sqlite_storage.add_entry"
    else:
        target = "devlog.storage.save_entries"
    with patch(target) as mock_save:
        mock_save.side_effect = StoragePermissionError(
            tmp_path / "entries.json", "write"
        )
        result = runner.invoke(main, ["add", "Will fail"])
    assert result.exit_code == 2
    assert "Cannot write to storage file" in result.output


# ---------------------------------------------------------------------------
# --at (backfill)
# ---------------------------------------------------------------------------


def test_add_at_absolute_date(runner, tmp_path, monkeypatch):
    """`add --at YYYY-MM-DD` writes the entry with the supplied date at midnight UTC."""
    from devlog import storage
    # The runner fixture sets DEVLOG_DATA_DIR in the subprocess env,
    # but `storage.load_entries()` reads from the in-process
    # os.environ. Set it via monkeypatch so both paths agree.
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    result = runner.invoke(main, ["add", "backfilled", "--at", "2025-01-15"])
    assert result.exit_code == 0
    entries = storage.load_entries()
    assert len(entries) == 1
    # The on-disk representation is always UTC; the local-midnight form
    # is converted to UTC. With no DEVLOG_TZ the input is UTC.
    assert entries[0].created_at == "2025-01-15T00:00:00Z"
    assert entries[0].message == "backfilled"


def test_add_at_iso_timestamp(runner, tmp_path, monkeypatch):
    from devlog import storage
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    result = runner.invoke(main, ["add", "x", "--at", "2025-02-15T09:30:00Z"])
    assert result.exit_code == 0
    entries = storage.load_entries()
    assert entries[0].created_at == "2025-02-15T09:30:00Z"


def test_add_at_relative_hours(runner, tmp_path, monkeypatch):
    """`--at 2h` should be roughly 2 hours before now (UTC)."""
    from datetime import datetime, timedelta, timezone

    from devlog import storage
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    before = datetime.now(tz=timezone.utc)
    result = runner.invoke(main, ["add", "x", "--at", "2h"])
    assert result.exit_code == 0
    entries = storage.load_entries()
    ts = datetime.strptime(entries[0].created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    # ts should be 2h before `before`, give or take a couple of seconds.
    assert abs((before - ts) - timedelta(hours=2)) < timedelta(seconds=5)


def test_add_at_relative_minutes(runner, tmp_path, monkeypatch):
    """`--at 30m` should be roughly 30 minutes before now."""
    from datetime import datetime, timedelta, timezone

    from devlog import storage
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    before = datetime.now(tz=timezone.utc)
    result = runner.invoke(main, ["add", "x", "--at", "30m"])
    assert result.exit_code == 0
    entries = storage.load_entries()
    ts = datetime.strptime(entries[0].created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert abs((before - ts) - timedelta(minutes=30)) < timedelta(seconds=5)


def test_add_at_invalid(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    result = runner.invoke(main, ["add", "x", "--at", "not-a-date"])
    assert result.exit_code == 2
    assert "Invalid --at" in result.output


def test_add_at_with_local_tz(runner, tmp_path, monkeypatch):
    """With DEVLOG_TZ set, a naive timestamp is interpreted in that zone."""
    from devlog import storage
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DEVLOG_TZ", "America/New_York")
    # 2025-02-15 09:00 in NY (EST) is 14:00 UTC (winter, no DST yet).
    result = runner.invoke(main, ["add", "x", "--at", "2025-02-15T09:00:00"])
    assert result.exit_code == 0
    entries = storage.load_entries()
    assert entries[0].created_at == "2025-02-15T14:00:00Z"


def test_add_at_accepts_relative_days(runner, tmp_path, monkeypatch):
    """`--at Nd` and `--at Nw` are accepted as relative day/week forms.

    Regression: the ``--at`` parser previously only accepted ``Nh`` and
    ``Nm``, even though the matching ``--since/--until`` filters
    accept ``Nd``/``Nw``. The asymmetric behaviour confused users who
    tried to backdate a week-old entry.
    """
    from devlog import storage
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    result = runner.invoke(main, ["add", "x", "--at", "1d"])
    assert result.exit_code == 0
    result = runner.invoke(main, ["add", "x", "--at", "1w"])
    assert result.exit_code == 0
    entries = storage.load_entries()
    # Both entries should have timestamps within the last 8 days
    from datetime import datetime, timezone
    for e in entries:
        ts = datetime.strptime(e.created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        age = (datetime.now(tz=timezone.utc) - ts).total_seconds()
        assert 0 <= age <= 8 * 86400 + 5


# ---------------------------------------------------------------------------
# stdin support (Tier 1: `devlog add -`)
# ---------------------------------------------------------------------------


def test_add_stdin_basic(runner, tmp_path, monkeypatch):
    """`devlog add -` reads message from stdin."""
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    result = runner.invoke(main, ["add", "-"], input="Hello from stdin\n")
    assert result.exit_code == 0
    assert "Hello from stdin" in result.output

    entries = storage.load_entries()
    assert len(entries) == 1
    assert entries[0].message == "Hello from stdin\n"


def test_add_stdin_with_tags(runner, tmp_path, monkeypatch):
    """`devlog add - -t tag` works with stdin input."""
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    result = runner.invoke(main, ["add", "-", "-t", "stdin-tag"], input="Message from pipe\n")
    assert result.exit_code == 0
    entries = storage.load_entries()
    assert entries[0].message == "Message from pipe\n"
    assert entries[0].tags == ["stdin-tag"]


def test_add_stdin_multiline(runner, tmp_path, monkeypatch):
    """`devlog add -` preserves multiline input."""
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    result = runner.invoke(main, ["add", "-"], input="Line 1\nLine 2\nLine 3\n")
    assert result.exit_code == 0
    entries = storage.load_entries()
    assert entries[0].message == "Line 1\nLine 2\nLine 3\n"


def test_add_stdin_no_newline(runner, tmp_path, monkeypatch):
    """`devlog add -` works when input lacks trailing newline (e.g., git log)."""
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    result = runner.invoke(main, ["add", "-"], input="No newline at end")
    assert result.exit_code == 0
    entries = storage.load_entries()
    assert entries[0].message == "No newline at end"


def test_add_stdin_empty_rejected(runner, tmp_path, monkeypatch):
    """Empty stdin is rejected with a clear error."""
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    result = runner.invoke(main, ["add", "-"], input="")
    assert result.exit_code == 1
    assert "stdin is empty" in result.output


def test_add_stdin_whitespace_only_rejected(runner, tmp_path, monkeypatch):
    """Whitespace-only stdin is rejected."""
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    result = runner.invoke(main, ["add", "-"], input="   \n\t\n")
    assert result.exit_code == 1
    assert "stdin is empty" in result.output


def test_add_stdin_tty_error(monkeypatch, tmp_path):
    """`devlog add -` errors when stdin is empty (simulates TTY with no input).

    In real usage, running `devlog add -` without piping input makes stdin
    a TTY, which the command detects and rejects. CliRunner always
    provides a non-TTY StringIO, so we test the empty-stdin path which
    produces the same user-facing error message.
    """
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    from click.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(main, ["add", "-"], input="")
    assert result.exit_code == 1
    assert "stdin is empty" in result.output