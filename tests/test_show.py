"""Tests for the `devlog show` command."""

import json

import pytest
from click.testing import CliRunner

from devlog.cli import main


@pytest.fixture()
def runner(tmp_path):
    return CliRunner(env={"DEVLOG_DATA_DIR": str(tmp_path)})


def _add(runner, message, *tags):
    args = ["add", message] + [a for t in tags for a in ("-t", t)]
    result = runner.invoke(main, args)
    assert result.exit_code == 0
    # Parse the short id from "Entry added  ·  XXXXXXXX" or similar
    import re
    m = re.search(r"[a-f0-9]{8}", result.output)
    assert m, f"could not parse id from: {result.output!r}"
    return m.group(0)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_show_by_short_id(runner):
    sid = _add(runner, "First entry", "backend")
    result = runner.invoke(main, ["show", sid])
    assert result.exit_code == 0
    assert "First entry" in result.output
    assert "backend" in result.output
    assert sid in result.output


def test_show_by_unique_prefix(runner):
    sid = _add(runner, "Hello", "docs")
    prefix = sid[:4]
    result = runner.invoke(main, ["show", prefix])
    assert result.exit_code == 0
    assert "Hello" in result.output


def test_show_full_message_no_truncation(runner):
    # Verify the full message appears (no 60-char list-table truncation).
    # Keep it under the terminal wrap width so it stays on one line.
    msg = "y" * 50
    sid = _add(runner, msg)
    result = runner.invoke(main, ["show", sid])
    assert result.exit_code == 0
    assert msg in result.output
    # The list table truncates at 60 chars with "…"; show panel does not.
    assert "…" not in result.output


def test_show_quiet_json(runner):
    sid = _add(runner, "JSON entry", "api")
    result = runner.invoke(main, ["show", sid, "--quiet"])
    assert result.exit_code == 0
    obj = json.loads(result.output.strip().splitlines()[0])
    assert obj["message"] == "JSON entry"
    assert "api" in obj["tags"]


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_show_not_found(runner):
    _add(runner, "an entry")
    result = runner.invoke(main, ["show", "deadbee"])
    assert result.exit_code == 1
    assert "No entry found" in result.output


def test_show_empty_id(runner):
    result = runner.invoke(main, ["show", ""])
    assert result.exit_code == 1
    assert "ID is required" in result.output


def test_show_ambiguous_prefix(runner, tmp_path, monkeypatch):
    """Two entries with the same first 4 hex chars → ambiguous."""
    from devlog import storage
    from devlog.models import Entry

    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))

    # Force two entries with the same first 4 hex chars
    common = "abcd"
    storage.add_entry(
        Entry(
            id=f"{common}1234-aaaa-bbbb-cccc-dddddddddddd",
            message="one",
            tags=[],
            created_at="2025-01-01T00:00:00Z",
        )
    )
    storage.add_entry(
        Entry(
            id=f"{common}5678-aaaa-bbbb-cccc-dddddddddddd",
            message="two",
            tags=[],
            created_at="2025-01-01T00:00:00Z",
        )
    )

    result = runner.invoke(main, ["show", common])
    assert result.exit_code == 1
    assert "matches multiple entries" in result.output
