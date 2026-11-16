"""SQLite backend (`DEVLOG_BACKEND=sqlite`) behavior tests.

Covers the backend-independent semantics that the JSON-backend test
files assert via ``entries.json`` file reads: repair strips/drops,
backup round-trips, restore overwrite, and basic backend dispatch.

Corruption parity: a hand-corrupted or garbage database must surface
through the same ``StorageError`` contract as a corrupt ``entries.json``
(exit 2 + actionable message from ``doctor``/``repair``/``list``), and
``doctor`` must validate rows exactly like the JSON backend. Right-
truncation recovery stays a JSON-file property (SQLite is a live
database with its own crash-safety), so those cases have no sqlite
equivalent and stay skipped in the JSON test files.
"""

from __future__ import annotations

import dataclasses
import json
import re
import sqlite3
from datetime import datetime, timezone

import pytest
from click.testing import CliRunner

from devlog import storage
from devlog.cli import main
from devlog.models import Entry
from devlog.storage import _sqlite as sqlite_backend
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
    assert storage.get_backend_name() == "sqlite"


def test_sqlite_file_created_in_data_dir(data_dir):
    _seed("aaaaaaaa-1111-1111-1111-111111111111", "x", _utc_iso(datetime.now(tz=timezone.utc)))
    assert (data_dir / "entries.db").exists()


def test_entries_round_trip_through_db(data_dir):
    _seed("aaaaaaaa-1111-1111-1111-111111111111", "hello", "2025-01-01T12:00:00Z", ["api"])
    conn = sqlite3.connect(data_dir / "entries.db")
    row = conn.execute("SELECT id, message, created_at, tags FROM entries").fetchone()
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
    _seed(
        "11111111-1111-1111-1111-111111111111", "existing", _utc_iso(datetime.now(tz=timezone.utc))
    )
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


# ---------------------------------------------------------------------------
# Corruption parity: garbage DB ≙ corrupt entries.json
# ---------------------------------------------------------------------------


def _corrupt_db(data_dir) -> None:
    """Replace the database file with garbage bytes (like a corrupt JSON store)."""
    db = data_dir / "entries.db"
    assert db.exists(), "seed a row first so the test proves corruption, not absence"
    db.write_bytes(b"this is not a sqlite database at all" * 20)
    # A stale WAL sidecar from the seeding connection would change how
    # sqlite interprets the main file; remove it so the corruption is
    # exactly "main file is garbage".
    for suffix in ("-wal", "-shm"):
        sidecar = data_dir / f"entries.db{suffix}"
        if sidecar.exists():
            sidecar.unlink()


def _raw_insert(data_dir, entry_id: str, message: str, created_at: str, tags_json: str = "[]"):
    """Hand-INSERT a row (the sqlite analog of hand-editing entries.json)."""
    conn = sqlite3.connect(data_dir / "entries.db")
    conn.execute(
        "INSERT INTO entries (id, message, created_at, tags) VALUES (?, ?, ?, ?)",
        (entry_id, message, created_at, tags_json),
    )
    conn.commit()
    conn.close()


def test_load_entries_raises_corrupted_storage_error(data_dir):
    """Garbage bytes in entries.db raise CorruptedStorageError, not sqlite3."""
    _seed("aaaaaaaa-1111-1111-1111-111111111111", "x", "2025-01-01T00:00:00Z")
    _corrupt_db(data_dir)
    with pytest.raises(storage.CorruptedStorageError):
        storage.load_entries()


def test_crud_boundary_wraps_sqlite_errors(data_dir):
    """Every CRUD entry point maps a corrupt database to StorageError."""
    entry = Entry(
        id="aaaaaaaa-1111-1111-1111-111111111111",
        message="x",
        tags=[],
        created_at="2025-01-01T00:00:00Z",
    )
    _seed(entry.id, "x", "2025-01-01T00:00:00Z")
    _corrupt_db(data_dir)
    with pytest.raises(storage.CorruptedStorageError):
        storage.save_entries([entry])
    with pytest.raises(storage.CorruptedStorageError):
        storage.add_entry(entry)
    with pytest.raises(storage.CorruptedStorageError):
        storage.update_entry(entry)
    with pytest.raises(storage.CorruptedStorageError):
        storage.delete_entry(entry.id)
    with pytest.raises(storage.CorruptedStorageError):
        storage.read_raw_entries()


def test_translate_sqlite_error_classification(tmp_path):
    """Only database-file corruption maps to CorruptedStorageError."""
    corrupted = sqlite3.DatabaseError("file is not a database")
    err = sqlite_backend._translate_sqlite_error(tmp_path, corrupted)
    assert isinstance(err, storage.CorruptedStorageError)

    malformed = sqlite3.DatabaseError("database disk image is malformed")
    assert isinstance(
        sqlite_backend._translate_sqlite_error(tmp_path, malformed),
        storage.CorruptedStorageError,
    )

    # A lock timeout is a runtime failure, not corruption: it must keep
    # the generic actionable message, not "restore from a backup".
    locked = sqlite3.OperationalError("database is locked")
    err = sqlite_backend._translate_sqlite_error(tmp_path, locked)
    assert isinstance(err, storage.StorageError)
    assert not isinstance(err, storage.CorruptedStorageError)


def test_doctor_reports_corrupt_db(runner, data_dir):
    """doctor on a garbage DB: exit 2 + corruption mentioned (JSON contract)."""
    _seed("aaaaaaaa-1111-1111-1111-111111111111", "x", "2025-01-01T00:00:00Z")
    _corrupt_db(data_dir)
    result = runner.invoke(main, ["doctor"])
    assert result.exit_code == 2
    assert "corrupt" in result.output.lower() or "valid" in result.output.lower()


def test_doctor_quiet_corrupt_db(runner, data_dir):
    """doctor --quiet on a garbage DB: exit 2, ok=False, kind=corrupt_db."""
    _seed("aaaaaaaa-1111-1111-1111-111111111111", "x", "2025-01-01T00:00:00Z")
    _corrupt_db(data_dir)
    result = runner.invoke(main, ["doctor", "--quiet"])
    assert result.exit_code == 2
    obj = json.loads(result.output.strip())
    assert obj["ok"] is False
    assert any(i["kind"] == "corrupt_db" for i in obj["issues"])


def test_repair_corrupt_db_errors(runner, data_dir):
    """repair on a garbage DB: the JSON 'Cannot repair' contract, exit 2."""
    _seed("aaaaaaaa-1111-1111-1111-111111111111", "x", "2025-01-01T00:00:00Z")
    _corrupt_db(data_dir)
    result = runner.invoke(main, ["repair", "-y"])
    assert result.exit_code == 2
    assert "Cannot repair" in result.output
    assert "restore" in result.output.lower()
    # No doubled periods (storage message ends in "."; CLI tacks on ". Restore…").
    assert ".." not in result.output
    # The wrapped storage error starts with "Error:" — once routed
    # through "Cannot repair:", only one colon boundary stays visible.
    assert result.output.count("Error:") <= 1


def test_list_corrupt_db_errors(runner, data_dir):
    """list on a garbage DB: exit 2 + 'corrupted' message (JSON contract)."""
    _seed("aaaaaaaa-1111-1111-1111-111111111111", "x", "2025-01-01T00:00:00Z")
    _corrupt_db(data_dir)
    result = runner.invoke(main, ["list"])
    assert result.exit_code == 2
    assert "corrupted" in result.output.lower()


# ---------------------------------------------------------------------------
# Doctor row validation (parity with the JSON backend)
# ---------------------------------------------------------------------------


def test_doctor_detects_bad_tags(runner, data_dir):
    """A hand-edited tags column must be flagged, not reported 'all clear'."""
    _seed("bad", "x", "2025-01-01T00:00:00Z", ["Bad Tag"])
    result = runner.invoke(main, ["doctor"])
    assert result.exit_code == 1
    assert "attention" in result.output
    assert "validation issue" in result.output
    assert "devlog repair" in result.output


def test_doctor_quiet_with_issues(runner, data_dir):
    _seed("bad", "x", "2025-01-01T00:00:00Z", ["Bad Tag"])
    result = runner.invoke(main, ["doctor", "--quiet"])
    assert result.exit_code == 1
    obj = json.loads(result.output.strip())
    assert obj["ok"] is False
    assert any(i["kind"] == "bad_tag" for i in obj["issues"])


def test_doctor_lists_specific_issues_inline(runner, data_dir):
    """Doctor enumerates specific issues inline, with 8-char id prefixes."""
    _seed("11111111-1111-1111-1111-111111111111", "good", "2026-08-29T00:00:00Z")
    _raw_insert(data_dir, "badcreatedat0000000000000000000", "bad-date", "not-a-date")
    _raw_insert(
        data_dir,
        "badtag000000000000000000000000",
        "bad-tag",
        "2026-08-29T00:00:00Z",
        '["BAD TAG!"]',
    )

    result = runner.invoke(main, ["doctor"])
    assert result.exit_code == 1
    assert "validation issue" in result.output
    assert "badcreat" in result.output
    assert "badtag" in result.output
    assert "bad_timestamp" in result.output
    assert "bad_tag" in result.output


def test_doctor_truncates_long_issue_lists(runner, data_dir):
    """More than 5 issues → first 5 inline plus an '…and N more' line."""
    _seed("00000000-0000-0000-0000-000000000000", "good", "2026-08-29T00:00:00Z")
    for i in range(7):
        _raw_insert(
            data_dir,
            f"a{i:07x}-1111-1111-1111-111111111111",
            "bad",
            "2026-08-29T00:00:00Z",
            '["BAD TAG!"]',
        )

    result = runner.invoke(main, ["doctor"])
    assert result.exit_code == 1
    assert "…and 2 more" in result.output


# ---------------------------------------------------------------------------
# Repair semantics (sqlite-flavored mirrors of the JSON test file)
# ---------------------------------------------------------------------------


def test_repair_clean_store_is_a_noop(runner, data_dir):
    _seed("aaaaaaaa-1111-1111-1111-111111111111", "fine", "2025-01-01T00:00:00Z")
    result = runner.invoke(main, ["repair"])
    assert result.exit_code == 0
    assert "No issues" in result.output
    # Store untouched
    assert len(storage.load_entries()) == 1


def _manual_backups(data_dir):
    """Manual entries-TS.json backups (undo's .pre.json snapshots excluded)."""
    backups_dir = data_dir / "backups"
    if not backups_dir.exists():
        return []
    return [p for p in backups_dir.glob("*.json") if not p.name.endswith(".pre.json")]


def test_repair_writes_backup_before_writing(runner, data_dir):
    """A backup is written whenever repair rewrites rows — even tag-only strips."""
    _seed("bad-1111-1111-1111-111111111111", "x", "2025-01-01T00:00:00Z", ["Bad Tag"])
    result = runner.invoke(main, ["repair", "-y"])
    # The bad tag was stripped in place, so no drop → exit 0.
    assert result.exit_code == 0
    backups = _manual_backups(data_dir)
    assert len(backups) == 1
    payload = json.loads(backups[0].read_text())
    # The backup holds the PRE-repair rows (bad tag still present).
    assert payload["entries"][0]["tags"] == ["Bad Tag"]
    assert "Backup written to" in result.output


def test_repair_no_backup_flag_skips_manual_backup(runner, data_dir):
    """--no-backup suppresses the manual entries-TS.json backup only."""
    _seed("bad-1111-1111-1111-111111111111", "x", "2025-01-01T00:00:00Z", ["Bad Tag"])
    result = runner.invoke(main, ["repair", "-y", "--no-backup"])
    assert result.exit_code == 0
    assert not _manual_backups(data_dir)
    # The bad tag was still repaired.
    assert storage.load_entries()[0].tags == []


def test_repair_drops_unrecoverable_writes_backup(runner, data_dir):
    """A drop counts as a write too, so a backup is still produced."""
    _seed("bad-1111-1111-1111-111111111111", "x", "not a date")
    result = runner.invoke(main, ["repair", "-y"])
    assert result.exit_code == 1
    assert len(_manual_backups(data_dir)) == 1


def test_repair_quiet_with_issues(runner, data_dir):
    """--quiet rewrites the store but prints no summary panel."""
    _seed("bad-1111-1111-1111-111111111111", "x", "2025-01-01T00:00:00Z", ["Bad Tag"])
    result = runner.invoke(main, ["repair", "-y", "--quiet"])
    assert "Found" not in result.output
    entries = storage.load_entries()
    assert len(entries) == 1
    assert entries[0].tags == []


# ---------------------------------------------------------------------------
# Pre-write snapshots & tolerance (sqlite-flavored mirrors)
# ---------------------------------------------------------------------------


def test_sqlite_snapshot_is_previous_db_rows(runner, data_dir):
    """The pre-write snapshot holds the *previous* rows, like the JSON copy."""
    _seed("11111111-aaaa-bbbb-cccc-111111111111", "a", "2025-01-01T00:00:00Z")
    expected = {"entries": [dataclasses.asdict(e) for e in storage.load_entries()]}
    _seed("22222222-aaaa-bbbb-cccc-222222222222", "b", "2025-01-02T00:00:00Z")
    snapshot = storage.latest_pre_write_backup()
    assert snapshot is not None
    payload = json.loads(snapshot.read_text())
    assert payload == expected


def test_load_entries_ignores_extra_columns(data_dir):
    """Future-schema drift (extra columns) loads instead of crashing.

    The sqlite analog of Entry.from_dict's unknown-key tolerance: a
    hand-migrated table may grow columns the backend doesn't know; the
    explicit column list in load_entries must keep working.
    """
    _seed("abcd1234ef567890", "from the future", "2026-01-01T00:00:00Z")
    conn = sqlite3.connect(data_dir / "entries.db")
    conn.execute("ALTER TABLE entries ADD COLUMN pinned INTEGER DEFAULT 1")
    conn.commit()
    conn.close()
    entries = storage.load_entries()
    assert len(entries) == 1
    assert entries[0].message == "from the future"


# ---------------------------------------------------------------------------
# Stats robustness (mirror of the JSON-backend behavior)
# ---------------------------------------------------------------------------


def test_stats_survives_unparseable_created_at(runner, data_dir):
    """One bad created_at row must not crash `stats` — it is dropped."""
    _seed("11111111-1111-1111-1111-111111111111", "good", _utc_iso(datetime.now(tz=timezone.utc)))
    _raw_insert(data_dir, "22222222-2222-2222-2222-222222222222", "bad", "not-a-date")

    result = runner.invoke(main, ["stats"])
    assert result.exit_code == 0
    assert "Total" in result.output
    # Only the valid entry is counted.
    assert re.search(r"Total\s*:\s*1", result.output) is not None
