"""Tests for the `devlog rename-tag` command."""

import pytest

from devlog.cli import main
from devlog import storage


# ---------------------------------------------------------------------------
# rename-tag
# ---------------------------------------------------------------------------


def test_rename_tag_happy(runner, data_dir, seed_entry):
    seed_entry("a1111111-1111-1111-1111-111111111111", "msg", tags=["backend"])
    seed_entry("a2222222-2222-2222-2222-222222222222", "msg2", tags=["backend", "bugfix"])
    result = runner.invoke(main, ["rename-tag", "backend", "devops"])
    assert result.exit_code == 0
    assert "Renamed" in result.output
    # Verify
    entries = storage.load_entries()
    for e in entries:
        assert "backend" not in e.tags
        assert "devops" in e.tags
        assert e.updated_at is not None


def test_rename_tag_dedupes_when_new_already_present(runner, data_dir, seed_entry):
    """If an entry already has NEW, removing OLD must not duplicate NEW."""
    seed_entry("b1111111-1111-1111-1111-111111111111", "msg", tags=["old", "new"])
    seed_entry("b2222222-2222-2222-2222-222222222222", "msg", tags=["old"])
    result = runner.invoke(main, ["rename-tag", "old", "new"])
    assert result.exit_code == 0
    entries = storage.load_entries()
    for e in entries:
        assert "old" not in e.tags
        assert e.tags.count("new") == 1


def test_rename_tag_no_op_when_old_eq_new(runner, data_dir, seed_entry):
    seed_entry("c1111111-1111-1111-1111-111111111111", "msg", tags=["foo"])
    result = runner.invoke(main, ["rename-tag", "foo", "foo"])
    assert result.exit_code == 0
    assert "OLD and NEW are the same" in result.output


def test_rename_tag_old_not_found(runner, data_dir, seed_entry):
    seed_entry("d1111111-1111-1111-1111-111111111111", "msg", tags=["foo"])
    result = runner.invoke(main, ["rename-tag", "missing", "bar"])
    assert result.exit_code == 0
    assert "No entries with tag" in result.output


def test_rename_tag_new_validation_errors(runner, data_dir):
    # NEW empty
    result = runner.invoke(main, ["rename-tag", "foo", ""])
    assert result.exit_code != 0
    assert "cannot be empty" in result.output
    # NEW invalid chars
    result = runner.invoke(main, ["rename-tag", "foo", "INVALID"])
    assert result.exit_code != 0
    assert "invalid characters" in result.output
    # NEW too long
    long_tag = "a" * 33
    result = runner.invoke(main, ["rename-tag", "foo", long_tag])
    assert result.exit_code != 0
    assert "exceeds maximum length" in result.output


def test_rename_tag_dry_run(runner, data_dir, seed_entry):
    seed_entry("e1111111-1111-1111-1111-111111111111", "msg", tags=["old"])
    result = runner.invoke(main, ["rename-tag", "old", "new", "--dry-run"])
    assert result.exit_code == 0
    assert "DRY RUN" in result.output
    entries = storage.load_entries()
    assert entries[0].tags == ["old"]


def test_rename_tag_quiet_suppresses_preview(runner, data_dir, seed_entry):
    seed_entry("f1111111-1111-1111-1111-111111111111", "msg", tags=["old"])
    result = runner.invoke(main, ["rename-tag", "old", "new", "--quiet"])
    assert result.exit_code == 0
    assert "DRY RUN" not in result.output
    assert "Renamed" not in result.output