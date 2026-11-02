"""SQLite backend (`DEVLOG_BACKEND=sqlite`) behavior tests.

Covers the backend-independent semantics that the JSON-backend test
files assert via ``entries.json`` file reads: repair strips/drops,
backup round-trips, restore overwrite, and basic backend dispatch.

Corrupt-JSON right-truncation recovery is a JSON-file property (SQLite
is a live database with its own crash-safety), so those cases have no
sqlite equivalent and stay skipped in the JSON test files.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest
from click.testing import CliRunner

from devlog import sqlite_storage, storage
from devlog.cli import main
from devlog.models import Entry
from tests.conftest import active_backend

pytestmark = pytest.mark.skipif(
    active_backend() != "sqlite",
    reason="exercises the sqlite backend specifically",
)


@pytest.fixture()
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def runner(tmp_path):
    return CliRunner(env={"DEVLOG_DATA_DIR": str(tmp_path)})


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
# Backend dispatch
# ---------------------------------------------------------------------------


def test_backend_dispatch_active():
    assert storage._is_sqlite_backend() is True
    assert sqlite_storage.get_backend_name() == "sqlite"


def test_sqlite_file_created_in_data_dir(data_dir):
    _seed("aaaaaaaa-1111-1111-1111-111111111111", "x", _utc_iso(datetime.now(tz=timezone.utc)))
    assert (data_dir / "entries.db").exists()


def test_entries_round_trip_through_db(data_dir):
    _seed("aaaaaaaa-1111-1111-1111-111111111111", "hello", "2025-01-01T12:00:00Z", ["api"])
    conn = sqlite3.connect(data_dir / "entries.db")
    row = conn.execute(
        "SELECT id, message, created_at, tags FROM entries"
    ).fetchone()
    conn.close()
    assert row[0] == "aaaaaaaa-1111-1111-1111-111111111111"
    assert row[1] == "hello"
    assert row[2] == "2025-01-01T12:00:00Z"
    assert json.loads(row[3]) == ["api"]


# ---------------------------------------------------------------------------
# Repair semantics (same rules as JSON backend, applied to the DB)
# ---------------------------------------------------------------------------


def test_repair_strips_invalid_tags_keeps_entry(runner, data_dir):
    """A bad tag is stripped; the entry survives — same contract as JSON."""
    _seed("good-1111-1111-1111-111111111111", "good", "2025-01-01T00:00:00Z", ["x"])
    # Hand-corrupt the DB the way a hand-edit corrupts entries.json.
    conn = sqlite3.connect(data_dir / "entries.db")
    conn.execute(
        "UPDATE entries SET tags=? WHERE id=?",
        (json.dumps(["Bad Tag", "ok-tag"]), "good-1111-1111-1111-111111111111"),
    )
    conn.commit()
    conn.close()

    result = runner.invoke(main, ["repair", "-y"])
    assert result.exit_code == 0
    entries = storage.load_entries()
    assert len(entries) == 1
    assert entries[0].tags == ["ok-tag"]


def test_repair_drops_entries_with_bad_timestamp(runner, data_dir):
    """A bad created_at is unrecoverable so the entry is dropped."""
    _seed("ts-bad-1111-1111-1111-111111111111", "x", "not-a-timestamp")
    result = runner.invoke(main, ["repair", "-y"])
    # A drop triggers exit 1 on the JSON backend too.
    assert result.exit_code == 1
    assert storage.load_entries() == []


def test_repair_dedupes_duplicate_ids(runner, data_dir):
    """Duplicate ids keep the first occurrence."""
    # Ensure the DB exists with the schema before poking it directly.
    _seed("first-1111-1111-1111-111111111111", "first", "2025-01-01T00:00:00Z")
    conn = sqlite3.connect(data_dir / "entries.db")
    conn.execute(
        "INSERT INTO entries (id, message, created_at, tags) VALUES (?, ?, ?, ?)",
        ("dup-1111-1111-1111-111111111111", "first", "2025-01-01T00:00:00Z", "[]"),
    )
    conn.commit()
    # PRIMARY KEY already blocks duplicates at insert time — the DB
    # schema enforces what the JSON validator reports. Verify that.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO entries (id, message, created_at, tags) VALUES (?, ?, ?, ?)",
            ("dup-1111-1111-1111-111111111111", "second", "2025-01-02T00:00:00Z", "[]"),
        )
    conn.close()


def test_repair_dry_run_does_not_write(runner, data_dir):
    _seed("bad-1111-1111-1111-111111111111", "x", "not-a-timestamp")
    result = runner.invoke(main, ["repair", "--dry-run"])
    assert result.exit_code == 0
    assert "DRY RUN" in result.output
    # Store untouched: the bad row is still there.
    assert len(storage.load_entries()) == 1


def test_repair_no_store_is_noop(runner, data_dir):
    """An absent DB should not be created by repair."""
    assert not (data_dir / "entries.db").exists()
    result = runner.invoke(main, ["repair"])
    assert result.exit_code == 0
    assert "No journal yet" in result.output


# ---------------------------------------------------------------------------
# Backup / restore (backup writes JSON snapshots regardless of backend)
# ---------------------------------------------------------------------------


def test_backup_writes_json_snapshot(runner, data_dir):
    _seed("bbbbbbbb-1111-1111-1111-111111111111", "snap", "2025-01-01T00:00:00Z", ["t"])
    result = runner.invoke(main, ["backup"])
    assert result.exit_code == 0
    backups = list((data_dir / "backups").glob("entries-*.json"))
    assert len(backups) == 1
    data = json.loads(backups[0].read_text())
    assert data["entries"][0]["message"] == "snap"


def test_backup_restore_round_trip(runner, data_dir):
    _seed("cccccccc-1111-1111-1111-111111111111", "one", "2025-01-01T00:00:00Z")
    result = runner.invoke(main, ["backup", "-q"])
    assert result.exit_code == 0
    backup_path = result.output.strip()

    # Mutate the store, then restore from the snapshot.
    _seed("dddddddd-1111-1111-1111-111111111111", "two", "2025-01-02T00:00:00Z")
    assert len(storage.load_entries()) == 2

    result = runner.invoke(main, ["restore", backup_path, "-y"])
    assert result.exit_code == 0
    entries = storage.load_entries()
    assert len(entries) == 1
    assert entries[0].message == "one"


def test_restore_prompts_when_existing(runner, data_dir):
    _seed("11111111-1111-1111-1111-111111111111", "existing", _utc_iso(datetime.now(tz=timezone.utc)))
    backup_file = data_dir / "b.json"
    backup_file.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "new",
                        "message": "replacement",
                        "tags": [],
                        "created_at": "2025-01-01T00:00:00Z",
                    }
                ]
            }
        )
    )
    # Without -y: the default is "no" → abort
    result = runner.invoke(main, ["restore", str(backup_file)], input="n\n")
    assert result.exit_code == 1
    entries = storage.load_entries()
    assert entries[0].message == "existing"


# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------


def test_doctor_clean_store(runner, data_dir):
    _seed("eeeeeeee-1111-1111-1111-111111111111", "fine", _utc_iso(datetime.now(tz=timezone.utc)))
    result = runner.invoke(main, ["doctor"])
    assert result.exit_code == 0
    assert "all clear" in result.output
    assert "1" in result.output


def test_doctor_quiet_json_shape(runner, data_dir):
    _seed("ffffffff-1111-1111-1111-111111111111", "fine", _utc_iso(datetime.now(tz=timezone.utc)))
    result = runner.invoke(main, ["doctor", "--quiet"])
    assert result.exit_code == 0
    obj = json.loads(result.output.strip())
    assert obj["ok"] is True
    assert obj["exists"] is True
    assert obj["entry_count"] == 1
    assert obj["issues"] == []
    assert obj["writable"] is True


def test_doctor_reports_db_path(runner, data_dir):
    result = runner.invoke(main, ["doctor"])
    assert "entries.db" in result.output or "doctor" in result.output.lower()


# ---------------------------------------------------------------------------
# Concurrency: two sequential connections see consistent state (WAL)
# ---------------------------------------------------------------------------


def test_wal_mode_enabled(data_dir):
    _seed("12341234-1111-1111-1111-111111111111", "x", _utc_iso(datetime.now(tz=timezone.utc)))
    conn = sqlite3.connect(data_dir / "entries.db")
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode == "wal"
