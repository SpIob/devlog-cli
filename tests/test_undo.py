"""Tests for `devlog undo` and the pre-write snapshot mechanism.

The snapshot capture lives in the storage backends (JSON: a copy of
entries.json; SQLite: a dump of current rows), so the behavioral tests
here run under *both* backends via the shared fixtures. Assertions on
snapshot files themselves are JSON-backend-specific and skipped under
``DEVLOG_BACKEND=sqlite``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner

from devlog import storage
from devlog.cli import main
from devlog.models import Entry
from tests.conftest import active_backend


def _utc_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed(entry_id: str, message: str, created_at: datetime) -> None:
    storage.add_entry(
        Entry(
            id=entry_id,
            message=message,
            tags=[],
            created_at=_utc_iso(created_at),
        )
    )


@pytest.fixture()
def runner(tmp_path):
    return CliRunner(env={"DEVLOG_DATA_DIR": str(tmp_path)})


@pytest.fixture()
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    return tmp_path


def _ids(entries: list[Entry]) -> set[str]:
    # Sets, not ordered lists: the JSON backend preserves insertion
    # order while SQLite returns newest-first, and both are correct.
    return {e.id for e in entries}


def _distinct_times(n: int) -> list[datetime]:
    base = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    return [base + timedelta(minutes=i) for i in range(n)]


# ---------------------------------------------------------------------------
# Snapshot capture
# ---------------------------------------------------------------------------


def test_first_write_creates_no_snapshot(runner, data_dir):
    """Nothing existed before the first write, so there is no snapshot."""
    _seed("11111111-aaaa-bbbb-cccc-111111111111", "first", _distinct_times(1)[0])
    assert storage.latest_pre_write_backup() is None


def test_second_write_snapshots_previous_state(runner, data_dir):
    times = _distinct_times(2)
    _seed("11111111-aaaa-bbbb-cccc-111111111111", "a", times[0])
    _seed("22222222-aaaa-bbbb-cccc-222222222222", "b", times[1])
    snapshot = storage.latest_pre_write_backup()
    assert snapshot is not None
    snapshot_entries = storage.load_pre_write_backup(snapshot)
    assert _ids(snapshot_entries) == {"11111111-aaaa-bbbb-cccc-111111111111"}


@pytest.mark.skipif(
    active_backend() == "sqlite",
    reason="asserts entries.json copy semantics (JSON backend only)",
)
def test_json_snapshot_is_copy_of_entries_json(runner, data_dir):
    """The JSON snapshot must be the *previous* entries.json content."""
    times = _distinct_times(2)
    _seed("11111111-aaaa-bbbb-cccc-111111111111", "a", times[0])
    before = json.loads((data_dir / "entries.json").read_text())
    _seed("22222222-aaaa-bbbb-cccc-222222222222", "b", times[1])
    snapshot = storage.latest_pre_write_backup()
    payload = json.loads(snapshot.read_text())
    assert payload == before


def test_snapshot_never_clobbers_existing(runner, data_dir):
    """Consecutive snapshot *writes* must never share a path.

    Windows clock ticks are coarser than a microsecond, so raw ``%f``
    timestamps can repeat; the destination helper therefore never
    returns an existing path (it bumps ``-1``, ``-2``, …). The test
    mirrors the production pattern: allocate, write, repeat — a path
    reused across writes would shrink the set below 50.
    """
    backups_dir = storage.get_backups_dir()
    backups_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for _ in range(50):
        p = storage.pre_write_backup_destination()
        p.write_text('{"entries": []}', encoding="utf-8")
        paths.append(p)
    assert len(set(paths)) == 50


# ---------------------------------------------------------------------------
# undo behavior
# ---------------------------------------------------------------------------


def test_undo_without_snapshot_exits_1(runner):
    result = runner.invoke(main, ["undo", "-y"])
    assert result.exit_code == 1
    assert "Nothing to undo" in result.output


def test_undo_reverts_last_add(runner, data_dir):
    times = _distinct_times(2)
    _seed("11111111-aaaa-bbbb-cccc-111111111111", "a", times[0])
    _seed("22222222-aaaa-bbbb-cccc-222222222222", "b", times[1])

    result = runner.invoke(main, ["undo", "-y"])
    assert result.exit_code == 0, result.output
    assert _ids(storage.load_entries()) == {"11111111-aaaa-bbbb-cccc-111111111111"}


def test_undo_chains_instead_of_toggling(runner, data_dir):
    """A second `undo` must step back one more write, not redo the last one.

    Without consuming the restored snapshot, the undo's own pre-write
    snapshot (of the pre-undo state) would become the newest snapshot
    and the next `undo` would toggle the journal back and forth.
    """
    times = _distinct_times(3)
    _seed("11111111-aaaa-bbbb-cccc-111111111111", "a", times[0])
    _seed("22222222-aaaa-bbbb-cccc-222222222222", "b", times[1])
    _seed("33333333-aaaa-bbbb-cccc-333333333333", "c", times[2])

    r1 = runner.invoke(main, ["undo", "-y"])
    assert r1.exit_code == 0, r1.output
    assert _ids(storage.load_entries()) == {
        "11111111-aaaa-bbbb-cccc-111111111111",
        "22222222-aaaa-bbbb-cccc-222222222222",
    }

    r2 = runner.invoke(main, ["undo", "-y"])
    assert r2.exit_code == 0, r2.output
    assert _ids(storage.load_entries()) == {"11111111-aaaa-bbbb-cccc-111111111111"}

    # All snapshots consumed by the two undos.
    assert storage.latest_pre_write_backup() is None


def test_undo_dry_run_writes_nothing(runner, data_dir):
    times = _distinct_times(2)
    _seed("11111111-aaaa-bbbb-cccc-111111111111", "a", times[0])
    _seed("22222222-aaaa-bbbb-cccc-222222222222", "b", times[1])

    result = runner.invoke(main, ["undo", "--dry-run"])
    assert result.exit_code == 0
    assert "DRY RUN" in result.output
    assert _ids(storage.load_entries()) == {
        "11111111-aaaa-bbbb-cccc-111111111111",
        "22222222-aaaa-bbbb-cccc-222222222222",
    }


def test_undo_confirm_prompt_aborts(runner, data_dir):
    times = _distinct_times(2)
    _seed("11111111-aaaa-bbbb-cccc-111111111111", "a", times[0])
    _seed("22222222-aaaa-bbbb-cccc-222222222222", "b", times[1])

    result = runner.invoke(main, ["undo"], input="n\n")
    assert result.exit_code == 0
    assert "Aborted." in result.output
    assert len(storage.load_entries()) == 2


def test_undo_survives_corrupt_snapshot(runner, data_dir):
    """A corrupt newest snapshot is an error, not a crash."""
    times = _distinct_times(2)
    _seed("11111111-aaaa-bbbb-cccc-111111111111", "a", times[0])
    _seed("22222222-aaaa-bbbb-cccc-222222222222", "b", times[1])
    snapshot = storage.latest_pre_write_backup()
    snapshot.write_text("{not json", encoding="utf-8")

    result = runner.invoke(main, ["undo", "-y"])
    assert result.exit_code == 2
    assert "unreadable" in result.output
    # The live store is untouched.
    assert len(storage.load_entries()) == 2


def test_undo_keeps_manual_backups(runner, data_dir):
    """`devlog backup` files are never consumed by undo."""
    times = _distinct_times(2)
    _seed("11111111-aaaa-bbbb-cccc-111111111111", "a", times[0])
    r = runner.invoke(main, ["backup", "-q"])
    manual_path = data_dir / r.output.strip()
    assert manual_path.exists()
    _seed("22222222-aaaa-bbbb-cccc-222222222222", "b", times[1])

    result = runner.invoke(main, ["undo", "-y"])
    assert result.exit_code == 0
    assert manual_path.exists()


def test_undo_prunes_to_keep_window(runner, data_dir):
    times = _distinct_times(2)
    _seed("11111111-aaaa-bbbb-cccc-111111111111", "a", times[0])
    _seed("22222222-aaaa-bbbb-cccc-222222222222", "b", times[1])
    # Pad the snapshot directory past the default window.
    backups_dir = storage.get_backups_dir()
    backups_dir.mkdir(parents=True, exist_ok=True)
    for i in range(15):
        p = backups_dir / f"entries-20260101-0000{i:02d}-000000.pre.json"
        p.write_text(json.dumps({"entries": []}), encoding="utf-8")

    result = runner.invoke(main, ["undo", "-y"])
    assert result.exit_code == 0, result.output
    remaining = storage._list_pre_write_backups()
    assert len(remaining) <= 10


# ---------------------------------------------------------------------------
# Storage helper unit tests
# ---------------------------------------------------------------------------


def test_list_pre_write_backups_orders_by_name_within_mtime(data_dir):
    backups_dir = storage.get_backups_dir()
    backups_dir.mkdir(parents=True, exist_ok=True)
    names = [
        "entries-20260101-000001-000100.pre.json",
        "entries-20260101-000001-000200.pre.json",
        "entries-20260101-000001-000300.pre.json",
    ]
    for name in names:
        p = backups_dir / name
        p.write_text('{"entries": []}', encoding="utf-8")
        # Identical mtime: ordering must fall back to the filename.
        stamp = datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc).timestamp()
        os.utime(p, (stamp, stamp))

    paths = [p.name for _m, p in storage._list_pre_write_backups()]
    assert paths == list(reversed(names))


def test_prune_removes_oldest_beyond_keep(data_dir):
    backups_dir = storage.get_backups_dir()
    backups_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(12):
        p = backups_dir / f"entries-20260101-0000{i:02d}-000000.pre.json"
        p.write_text('{"entries": []}', encoding="utf-8")
        paths.append(p)

    removed = storage.prune_pre_write_backups(keep=10)
    assert removed == 2
    remaining = {p.name for _m, p in storage._list_pre_write_backups()}
    assert len(remaining) == 10
    # The two oldest (lexicographically first) are gone.
    assert "entries-20260101-000000-000000.pre.json" not in remaining
    assert "entries-20260101-000001-000000.pre.json" not in remaining


def test_prune_keep_zero_removes_all(data_dir):
    backups_dir = storage.get_backups_dir()
    backups_dir.mkdir(parents=True, exist_ok=True)
    (backups_dir / "entries-20260101-000000-000000.pre.json").write_text(
        '{"entries": []}', encoding="utf-8"
    )
    assert storage.prune_pre_write_backups(keep=0) == 1
    assert storage._list_pre_write_backups() == []


def test_load_pre_write_backup_rejects_bad_shape(data_dir):
    backups_dir = storage.get_backups_dir()
    backups_dir.mkdir(parents=True, exist_ok=True)
    bad = backups_dir / "entries-20260101-000000-000000.pre.json"
    bad.write_text('{"foo": []}', encoding="utf-8")
    with pytest.raises(storage.CorruptedStorageError):
        storage.load_pre_write_backup(bad)
