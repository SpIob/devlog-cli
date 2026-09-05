"""Tests for the `devlog tag <name> [--delete]` command."""

from __future__ import annotations

import json
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
# Show mode
# ---------------------------------------------------------------------------


def test_tag_show_empty(runner, data_dir):
    result = runner.invoke(main, ["tag", "backend"])
    assert result.exit_code == 0
    assert 'No entries with tag "backend"' in result.output


def test_tag_show_lists_matching(runner, data_dir):
    now = datetime.now(tz=timezone.utc)
    _seed("11111111-aaaa-bbbb-cccc-111111111111", "be-one", _utc_iso(now), ["backend"])
    _seed("22222222-aaaa-bbbb-cccc-222222222222", "be-two", _utc_iso(now), ["backend"])
    _seed("33333333-aaaa-bbbb-cccc-333333333333", "fe-one", _utc_iso(now), ["frontend"])
    result = runner.invoke(main, ["tag", "backend"])
    assert result.exit_code == 0
    assert "be-one" in result.output
    assert "be-two" in result.output
    assert "fe-one" not in result.output


def test_tag_show_quiet_json(runner, data_dir):
    now = datetime.now(tz=timezone.utc)
    _seed("44444444-aaaa-bbbb-cccc-444444444444", "x", _utc_iso(now), ["x"])
    result = runner.invoke(main, ["tag", "x", "--quiet"])
    assert result.exit_code == 0
    lines = [ln for ln in result.output.splitlines() if ln.strip()]
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["message"] == "x"
    assert "x" in obj["tags"]


def test_tag_show_limit(runner, data_dir):
    now = datetime.now(tz=timezone.utc)
    for i in range(5):
        _seed(
            f"{i:08x}-aaaa-bbbb-cccc-dddddddddddd",
            f"msg {i}",
            _utc_iso(now),
            ["x"],
        )
    result = runner.invoke(main, ["tag", "x", "--limit", "2"])
    assert result.exit_code == 0
    # Only 2 entries shown; the table footer should reflect that.
    assert "Showing 2" in result.output


def test_tag_show_all(runner, data_dir):
    now = datetime.now(tz=timezone.utc)
    for i in range(5):
        _seed(
            f"{i:08x}-bbbb-bbbb-cccc-dddddddddddd",
            f"allmsg {i}",
            _utc_iso(now),
            ["x"],
        )
    result = runner.invoke(main, ["tag", "x", "--all"])
    assert result.exit_code == 0
    # 5 entries shown
    assert "Showing 5" in result.output


def test_tag_show_invalid_limit(runner, data_dir):
    result = runner.invoke(main, ["tag", "x", "--limit", "0"])
    assert result.exit_code == 1
    assert "--limit must be a positive integer" in result.output


def test_tag_show_invalid_name(runner, data_dir):
    result = runner.invoke(main, ["tag", "Bad Name"])
    assert result.exit_code == 1
    assert "invalid characters" in result.output


def test_tag_show_normalizes_case(runner, data_dir):
    """`tag BACKEND` should match the lowercase-stored `backend`."""
    now = datetime.now(tz=timezone.utc)
    _seed("55555555-aaaa-bbbb-cccc-555555555555", "be", _utc_iso(now), ["backend"])
    result = runner.invoke(main, ["tag", "BACKEND"])
    assert result.exit_code == 0
    assert "be" in result.output


# ---------------------------------------------------------------------------
# Delete mode
# ---------------------------------------------------------------------------


def test_tag_delete_removes_from_all_matching(runner, data_dir):
    now = datetime.now(tz=timezone.utc)
    _seed("66666666-aaaa-bbbb-cccc-666666666666", "be1", _utc_iso(now), ["backend"])
    _seed("77777777-aaaa-bbbb-cccc-777777777777", "be2", _utc_iso(now), ["backend", "extra"])
    _seed("88888888-aaaa-bbbb-cccc-888888888888", "fe", _utc_iso(now), ["frontend"])
    result = runner.invoke(main, ["tag", "backend", "--delete", "-y"])
    assert result.exit_code == 0
    # The first two entries must no longer have `backend`; `fe` is untouched.
    entries = storage.load_entries()
    by_id = {e.id: e for e in entries}
    assert "backend" not in by_id["66666666-aaaa-bbbb-cccc-666666666666"].tags
    assert "backend" not in by_id["77777777-aaaa-bbbb-cccc-777777777777"].tags
    # Other tags must be preserved.
    assert "extra" in by_id["77777777-aaaa-bbbb-cccc-777777777777"].tags
    assert by_id["88888888-aaaa-bbbb-cccc-888888888888"].tags == ["frontend"]


def test_tag_delete_sets_updated_at(runner, data_dir):
    now = datetime.now(tz=timezone.utc)
    _seed("99999999-aaaa-bbbb-cccc-999999999999", "x", _utc_iso(now), ["x"])
    result = runner.invoke(main, ["tag", "x", "--delete", "-y"])
    assert result.exit_code == 0
    entries = storage.load_entries()
    assert entries[0].updated_at is not None
    assert entries[0].tags == []


def test_tag_delete_no_op_when_absent(runner, data_dir):
    now = datetime.now(tz=timezone.utc)
    _seed("aaaaaaaa-aaaa-bbbb-cccc-aaaaaaaaaaaa", "x", _utc_iso(now), ["y"])
    result = runner.invoke(main, ["tag", "z", "--delete", "-y"])
    assert result.exit_code == 0
    assert 'No entries with tag "z"' in result.output


def test_tag_delete_dry_run_does_not_write(runner, data_dir):
    now = datetime.now(tz=timezone.utc)
    _seed("bbbbbbbb-aaaa-bbbb-cccc-bbbbbbbbbbbb", "x", _utc_iso(now), ["x"])
    result = runner.invoke(main, ["tag", "x", "--delete", "--dry-run"])
    assert result.exit_code == 0
    assert "DRY RUN" in result.output
    # Storage must still contain the tag.
    entries = storage.load_entries()
    assert "x" in entries[0].tags


def test_tag_delete_dry_run_with_quiet_is_silent(runner, data_dir):
    """`tag X --delete --dry-run --quiet` must produce no output.

    Otherwise `--quiet` is misleading: the user opted out of output
    and still got a multi-line summary.
    """
    now = datetime.now(tz=timezone.utc)
    _seed("dddddddd-aaaa-bbbb-cccc-dddddddddddd", "x", _utc_iso(now), ["x"])
    result = runner.invoke(main, ["tag", "x", "--delete", "--dry-run", "--quiet"])
    assert result.exit_code == 0
    assert result.output.strip() == ""


def test_tag_delete_quiet(runner, data_dir):
    now = datetime.now(tz=timezone.utc)
    _seed("cccccccc-aaaa-bbbb-cccc-cccccccccccc", "x", _utc_iso(now), ["x"])
    result = runner.invoke(main, ["tag", "x", "--delete", "-y", "--quiet"])
    assert result.exit_code == 0
    # No success line under --quiet.
    assert "Removed" not in result.output
    # Tag is still removed.
    entries = storage.load_entries()
    assert "x" not in entries[0].tags


def test_tag_delete_quiet_skips_prompt(runner, data_dir):
    """`--quiet` with `--delete` must skip the confirmation prompt entirely.

    Otherwise piped/scripted usage would hang on `click.confirm`.
    """
    now = datetime.now(tz=timezone.utc)
    _seed("eeeeeeee-aaaa-bbbb-cccc-eeeeeeeeeeee", "x", _utc_iso(now), ["x"])
    result = runner.invoke(main, ["tag", "x", "--delete", "--quiet"])
    assert result.exit_code == 0
    entries = storage.load_entries()
    assert "x" not in entries[0].tags


def test_tag_delete_yes_skips_prompt(runner, data_dir):
    """`--yes` must skip the confirmation prompt and apply the change."""
    now = datetime.now(tz=timezone.utc)
    _seed("ffffffff-aaaa-bbbb-cccc-ffffffffffff", "x", _utc_iso(now), ["x"])
    result = runner.invoke(main, ["tag", "x", "--delete", "--yes"])
    assert result.exit_code == 0
    entries = storage.load_entries()
    assert "x" not in entries[0].tags


def test_tag_delete_aborted_on_no(runner, data_dir):
    """Without `-y`, answering 'n' to the confirm prompt must abort."""
    now = datetime.now(tz=timezone.utc)
    _seed("abcdefab-aaaa-bbbb-cccc-abcdefabcdef", "x", _utc_iso(now), ["x"])
    result = runner.invoke(main, ["tag", "x", "--delete"], input="n\n")
    assert result.exit_code == 0
    assert "Aborted" in result.output
    # Tag must still be present.
    entries = storage.load_entries()
    assert "x" in entries[0].tags


def test_tag_delete_invalid_name(runner, data_dir):
    result = runner.invoke(main, ["tag", "Bad Name", "--delete"])
    assert result.exit_code == 1
    assert "invalid characters" in result.output


def test_tag_delete_empty_name(runner, data_dir):
    result = runner.invoke(main, ["tag", "", "--delete"])
    assert result.exit_code == 1
    # `click.argument` may itself reject this with a different message;
    # the assertion is just that we exit 1 and don't crash.
    assert result.exit_code == 1
