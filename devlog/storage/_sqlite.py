"""SQLite storage backend for devlog.

Opt-in via DEVLOG_BACKEND=sqlite. Provides:
- Concurrent access safety (WAL mode, no clobbering between processes)
- Scales beyond 100k entries (JSON backend degrades there)
- ACID transactions
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import sys

from devlog.models import Entry
from devlog.storage._base import (
    ensure_storage_dir,
    get_backups_dir,
    get_sqlite_path,
    pre_write_backup_destination,
    register_backend,
)

# ---------------------------------------------------------------------------
# Connection & schema
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1


def _get_connection() -> sqlite3.Connection:
    """Get a SQLite connection with sensible defaults."""
    path = get_sqlite_path()
    ensure_storage_dir()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # Pragmas for safety and performance
    conn.execute("PRAGMA journal_mode=WAL")  # Better concurrency
    conn.execute("PRAGMA synchronous=NORMAL")  # Safe + fast
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")  # Wait up to 5s for locks
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    """Create tables and indexes if they don't exist."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY
        );

        CREATE TABLE IF NOT EXISTS entries (
            id TEXT PRIMARY KEY,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            tags TEXT NOT NULL DEFAULT '[]'
        );

        CREATE INDEX IF NOT EXISTS idx_entries_created_at
        ON entries(created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_entries_updated_at
        ON entries(updated_at DESC);
    """
    )

    # Ensure schema version is set
    cursor = conn.execute("SELECT version FROM schema_version")
    row = cursor.fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    elif row[0] < SCHEMA_VERSION:
        # Future: run migrations here
        conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))

    conn.commit()


def _row_to_entry(row: sqlite3.Row) -> Entry:
    """Convert a SQLite row to an Entry."""
    tags = json.loads(row["tags"]) if row["tags"] else []
    return Entry(
        id=row["id"],
        message=row["message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        tags=tags,
    )


# ---------------------------------------------------------------------------
# Public API (called via devlog.storage facade)
# ---------------------------------------------------------------------------


def load_entries() -> list[Entry]:
    """Load all entries from SQLite in insertion order (rowid)."""
    conn = _get_connection()
    try:
        _init_schema(conn)
        # ORDER BY rowid gives insertion order, matching JSON backend behavior
        cursor = conn.execute(
            "SELECT id, message, created_at, updated_at, tags FROM entries ORDER BY rowid"
        )
        return [_row_to_entry(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def save_entries(entries: list[Entry]) -> None:
    """Persist all entries to SQLite atomically (single transaction)."""
    conn = _get_connection()
    try:
        _init_schema(conn)
        with conn:
            # Single-step undo support: snapshot the pre-write rows into
            # backups/ (same convention as the JSON backend). Skip when
            # the table is empty (nothing to roll back to). Best-effort:
            # a snapshot failure must never block a write.
            row = conn.execute("SELECT COUNT(*) FROM entries").fetchone()
            if row[0] > 0:
                _snapshot_rows(conn)
            # Delete all and re-insert (simple, correct, fast enough for 100k+)
            conn.execute("DELETE FROM entries")
            for e in entries:
                conn.execute(
                    "INSERT INTO entries (id, message, created_at, updated_at, tags) VALUES (?, ?, ?, ?, ?)",
                    (
                        e.id,
                        e.message,
                        e.created_at,
                        e.updated_at,
                        json.dumps(e.tags, ensure_ascii=False),
                    ),
                )
    finally:
        conn.close()


def _snapshot_rows(conn: sqlite3.Connection) -> None:
    """Write the current rows to a pre-write backup in ``backups/``.

    Mirror of the JSON backend's pre-write copy in ``storage.save_entries``:
    same directory, same filename convention. Pure SELECT + file write, so
    it must run inside the caller's transaction to read consistent state.
    """
    rows = conn.execute("SELECT id, message, created_at, updated_at, tags FROM entries").fetchall()
    payload = {
        "entries": [
            {
                "id": r["id"],
                "message": r["message"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "tags": json.loads(r["tags"]) if r["tags"] else [],
            }
            for r in rows
        ]
    }
    backups_dir = get_backups_dir()
    backups_dir.mkdir(parents=True, exist_ok=True)
    dest = pre_write_backup_destination()
    dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def add_entry(new_entry: Entry) -> None:
    """Append a single entry to SQLite."""
    conn = _get_connection()
    try:
        _init_schema(conn)
        with conn:
            row = conn.execute("SELECT COUNT(*) FROM entries").fetchone()
            if row[0] > 0:
                _snapshot_rows(conn)
            conn.execute(
                "INSERT INTO entries (id, message, created_at, updated_at, tags) VALUES (?, ?, ?, ?, ?)",
                (
                    new_entry.id,
                    new_entry.message,
                    new_entry.created_at,
                    new_entry.updated_at,
                    json.dumps(new_entry.tags, ensure_ascii=False),
                ),
            )
    finally:
        conn.close()


def update_entry(updated: Entry) -> bool:
    """Replace an entry in SQLite by id.

    Only creates a pre-write snapshot if the entry actually exists.
    """
    conn = _get_connection()
    try:
        _init_schema(conn)
        with conn:
            # Check if the target entry exists BEFORE snapshotting
            cursor = conn.execute("SELECT 1 FROM entries WHERE id=?", (updated.id,))
            if cursor.fetchone() is None:
                return False
            row = conn.execute("SELECT COUNT(*) FROM entries").fetchone()
            if row[0] > 0:
                _snapshot_rows(conn)
            cursor = conn.execute(
                "UPDATE entries SET message=?, created_at=?, updated_at=?, tags=? WHERE id=?",
                (
                    updated.message,
                    updated.created_at,
                    updated.updated_at,
                    json.dumps(updated.tags, ensure_ascii=False),
                    updated.id,
                ),
            )
            return cursor.rowcount > 0
    finally:
        conn.close()


def delete_entry(entry_id: str) -> bool:
    """Remove an entry from SQLite by id.

    Only creates a pre-write snapshot if the entry actually exists.
    """
    conn = _get_connection()
    try:
        _init_schema(conn)
        with conn:
            # Check if the target entry exists BEFORE snapshotting
            cursor = conn.execute("SELECT 1 FROM entries WHERE id=?", (entry_id,))
            if cursor.fetchone() is None:
                return False
            row = conn.execute("SELECT COUNT(*) FROM entries").fetchone()
            if row[0] > 0:
                _snapshot_rows(conn)
            cursor = conn.execute("DELETE FROM entries WHERE id=?", (entry_id,))
            return cursor.rowcount > 0
    finally:
        conn.close()


def read_raw_entries() -> tuple[dict, bool]:
    """Return all entries from SQLite as a JSON-compatible payload.

    SQLite rows are committed atomically, so there is no partial-write
    state to recover from — the recovered flag is always False.
    """
    entries = load_entries()
    return {"entries": [dataclasses.asdict(e) for e in entries]}, False


# Register this backend
register_backend("sqlite", sys.modules[__name__])
