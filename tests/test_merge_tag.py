"""Tests for the `devlog merge-tag OLD NEW` command."""

from __future__ import annotations

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


def _utc_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed(entry_id, message, created_at, tags=None):
    storage.add_entry(
        Entry(
            id=entry_id,
            message=message,
            tags=list(tags) if tags else [],
            created_at=created_at,
        )
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_merge_tag_adds_new_and_removes_old(runner, data_dir):
    now = datetime.now(tz=timezone.utc)
    _seed("11111111-aaaa-bbbb-cccc-111111111111", "a", _utc_iso(now), ["backend"])
    _seed("22222222-aaaa-bbbb-cccc-222222222222", "b", _utc_iso(now), ["backend", "extra"])
    _seed("33333333-aaaa-bbbb-cccc-333333333333", "c", _utc_iso(now), ["frontend"])
    result = runner.invoke(main, ["merge-tag", "backend", "devops"])
    assert result.exit_code == 0
    by_id = {e.id: e for e in storage.load_entries()}
    assert "backend" not in by_id["11111111-aaaa-bbbb-cccc-111111111111"].tags
    assert "devops" in by_id["11111111-aaaa-bbbb-cccc-111111111111"].tags
    assert "backend" not in by_id["22222222-aaaa-bbbb-cccc-222222222222"].tags
    assert "devops" in by_id["22222222-aaaa-bbbb-cccc-222222222222"].tags
    assert "extra" in by_id["22222222-aaaa-bbbb-cccc-222222222222"].tags
    # Frontend-only entry is untouched.
    assert by_id["33333333-aaaa-bbbb-cccc-333333333333"].tags == ["frontend"]


def test_merge_tag_sets_updated_at(runner, data_dir):
    now = datetime.now(tz=timezone.utc)
    _seed("44444444-aaaa-bbbb-cccc-444444444444", "x", _utc_iso(now), ["x"])
    runner.invoke(main, ["merge-tag", "x", "y"])
    entries = storage.load_entries()
    assert entries[0].updated_at is not None
    assert entries[0].tags == ["y"]


def test_merge_tag_dedupes_when_new_already_present(runner, data_dir):
    """An entry that already has NEW should not end up with two copies."""
    now = datetime.now(tz=timezone.utc)
    _seed(
        "55555555-aaaa-bbbb-cccc-555555555555",
        "both",
        _utc_iso(now),
        ["old", "new"],
    )
    result = runner.invoke(main, ["merge-tag", "old", "new"])
    assert result.exit_code == 0
    entries = storage.load_entries()
    assert entries[0].tags == ["new"]
    # The summary line should mention the skip.
    assert "already had" in result.output


def test_merge_tag_dry_run_does_not_write(runner, data_dir):
    now = datetime.now(tz=timezone.utc)
    _seed("66666666-aaaa-bbbb-cccc-666666666666", "x", _utc_iso(now), ["x"])
    result = runner.invoke(main, ["merge-tag", "x", "y", "--dry-run"])
    assert result.exit_code == 0
    assert "DRY RUN" in result.output
    # Storage must be unchanged.
    assert storage.load_entries()[0].tags == ["x"]


def test_merge_tag_dry_run_with_quiet_is_silent(runner, data_dir):
    """`merge-tag --dry-run --quiet` must produce no output."""
    now = datetime.now(tz=timezone.utc)
    _seed("66666666-aaaa-bbbb-cccc-666666666666", "x", _utc_iso(now), ["x"])
    result = runner.invoke(main, ["merge-tag", "x", "y", "--dry-run", "--quiet"])
    assert result.exit_code == 0
    assert result.output.strip() == ""


def test_merge_tag_quiet(runner, data_dir):
    now = datetime.now(tz=timezone.utc)
    _seed("77777777-aaaa-bbbb-cccc-777777777777", "x", _utc_iso(now), ["x"])
    result = runner.invoke(main, ["merge-tag", "x", "y", "--quiet"])
    assert result.exit_code == 0
    assert "Merged" not in result.output
    # But the storage is still updated.
    assert storage.load_entries()[0].tags == ["y"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_merge_tag_no_entries_with_old(runner, data_dir):
    now = datetime.now(tz=timezone.utc)
    _seed("88888888-aaaa-bbbb-cccc-888888888888", "x", _utc_iso(now), ["y"])
    result = runner.invoke(main, ["merge-tag", "z", "y"])
    assert result.exit_code == 0
    assert 'No entries with tag "z"' in result.output


def test_merge_tag_old_equals_new(runner, data_dir):
    now = datetime.now(tz=timezone.utc)
    _seed("99999999-aaaa-bbbb-cccc-999999999999", "x", _utc_iso(now), ["x"])
    result = runner.invoke(main, ["merge-tag", "x", "x"])
    assert result.exit_code == 0
    assert "OLD and NEW are the same" in result.output
    # Storage must be unchanged.
    assert storage.load_entries()[0].tags == ["x"]


def test_merge_tag_old_equals_new_after_normalization(runner, data_dir):
    """`merge-tag X x` → OLD normalises to 'x', NEW is 'x' → no-op."""
    now = datetime.now(tz=timezone.utc)
    _seed("99999999-aaaa-bbbb-cccc-999999999999", "x", _utc_iso(now), ["x"])
    result = runner.invoke(main, ["merge-tag", "X", "x"])
    assert result.exit_code == 0
    assert "OLD and NEW are the same" in result.output
    assert storage.load_entries()[0].tags == ["x"]


def test_merge_tag_invalid_new(runner, data_dir):
    result = runner.invoke(main, ["merge-tag", "x", "Bad Name"])
    assert result.exit_code == 1
    assert "invalid characters" in result.output


def test_merge_tag_invalid_new_too_long(runner, data_dir):
    long_tag = "a" * 33
    result = runner.invoke(main, ["merge-tag", "x", long_tag])
    assert result.exit_code == 1
    assert "exceeds maximum length" in result.output


def test_merge_tag_empty_old(runner, data_dir):
    result = runner.invoke(main, ["merge-tag", "", "y"])
    assert result.exit_code == 1


def test_merge_tag_empty_new(runner, data_dir):
    result = runner.invoke(main, ["merge-tag", "x", ""])
    assert result.exit_code == 1
    assert "NEW tag cannot be empty" in result.output


def test_merge_tag_preserves_other_tags(runner, data_dir):
    """Tags unrelated to OLD or NEW must be preserved verbatim."""
    now = datetime.now(tz=timezone.utc)
    _seed(
        "aaaaaaaa-aaaa-bbbb-cccc-aaaaaaaaaaaa",
        "x",
        _utc_iso(now),
        ["old", "alpha", "beta", "gamma"],
    )
    runner.invoke(main, ["merge-tag", "old", "new"])
    assert storage.load_entries()[0].tags == ["new", "alpha", "beta", "gamma"]
