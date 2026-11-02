"""SQLite storage backend for devlog.

Opt-in via DEVLOG_BACKEND=sqlite. Provides:
- Concurrent access safety (no clobbering between processes)
- Scales beyond 100k entries (JSON backend degrades there)
- ACID transactions
"""

from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
from pathlib import Path

from devlog.models import Entry
from devlog.storage import (
    Issue,
    build_repair_plan,
    ensure_storage_dir,
    validate_entries,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SQLITE_DB_NAME = "entries.db"
SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def get_sqlite_path() -> Path:
    """Return the absolute path to the SQLite database file."""
    if "DEVLOG_DATA_DIR" in os.environ:
        return Path(os.environ["DEVLOG_DATA_DIR"]) / SQLITE_DB_NAME
    return Path.home() / ".devlog" / SQLITE_DB_NAME


def get_backend_name() -> str:
    """Return the active backend name: 'json' or 'sqlite'."""
    return os.environ.get("DEVLOG_BACKEND", "json").lower()


def is_sqlite_backend() -> bool:
    """Check if SQLite backend is active."""
    return get_backend_name() == "sqlite"


# ---------------------------------------------------------------------------
# Connection & schema
# ---------------------------------------------------------------------------


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
    conn.executescript("""
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
    """)
    
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
# Public API (mirrors storage.py)
# ---------------------------------------------------------------------------


def load_entries() -> list[Entry]:
    """Load all entries from SQLite, newest first."""
    conn = _get_connection()
    try:
        _init_schema(conn)
        cursor = conn.execute(
            "SELECT id, message, created_at, updated_at, tags FROM entries ORDER BY created_at DESC"
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


def add_entry(new_entry: Entry) -> None:
    """Append a single entry to SQLite."""
    conn = _get_connection()
    try:
        _init_schema(conn)
        with conn:
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
    """Replace an entry in SQLite by id."""
    conn = _get_connection()
    try:
        _init_schema(conn)
        with conn:
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
    """Remove an entry from SQLite by id."""
    conn = _get_connection()
    try:
        _init_schema(conn)
        with conn:
            cursor = conn.execute("DELETE FROM entries WHERE id=?", (entry_id,))
            return cursor.rowcount > 0
    finally:
        conn.close()


def find_entry_by_id(entries: list[Entry], id_prefix: str) -> Entry | None:
    """Find an entry by exact id or unique short-id prefix.

    This function takes an in-memory list (for API compatibility with the
    JSON backend) but for SQLite we could also query directly. The current
    approach loads all entries first, which is fine for moderate sizes.
    For very large journals, we could add a direct query version.
    """
    from devlog.storage import find_entry_by_id as json_find_entry_by_id
    return json_find_entry_by_id(entries, id_prefix)


def find_entry_id_prefix_matches(entries: list[Entry], id_prefix: str) -> list[Entry]:
    """Return all entries whose id starts with the given prefix (case-insensitive)."""
    from devlog.storage import find_entry_id_prefix_matches as json_find_entry_id_prefix_matches
    return json_find_entry_id_prefix_matches(entries, id_prefix)


# ---------------------------------------------------------------------------
# Validation & Repair (reusing JSON logic where possible)
# ---------------------------------------------------------------------------


def validate_entries_sqlite(data: object) -> list[Issue]:
    """Validate entries from a SQLite export (for import validation)."""
    # Reuse the JSON validator by converting to the expected format
    if isinstance(data, dict) and "entries" in data:
        return validate_entries(data)
    return [Issue(kind="bad_root", message="root must be a JSON object with 'entries' key")]


def build_repair_plan_sqlite(raw: object) -> tuple[list[Entry], list[Issue]]:
    """Build a repair plan from a raw payload (for import)."""
    return build_repair_plan(raw)


def read_raw_entries_sqlite() -> tuple[object, bool]:
    """Return all entries from SQLite as a JSON-compatible payload.
    
    Returns a tuple of (payload_dict, recovered_flag) for compatibility
    with the JSON backend's read_raw_entries().
    """
    entries = load_entries()
    payload = {"entries": [dataclasses.asdict(e) for e in entries]}
    return payload, False


# ---------------------------------------------------------------------------
# Migration helpers
# ---------------------------------------------------------------------------


def migrate_from_json(json_entries: list[Entry]) -> None:
    """Migrate entries from JSON backend to SQLite."""
    if not json_entries:
        return
    conn = _get_connection()
    try:
        _init_schema(conn)
        with conn:
            for e in json_entries:
                conn.execute(
                    "INSERT OR REPLACE INTO entries (id, message, created_at, updated_at, tags) VALUES (?, ?, ?, ?, ?)",
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


def export_to_json() -> list[Entry]:
    """Export all SQLite entries as a list of Entry (for backup/migration)."""
    return load_entries()


# ---------------------------------------------------------------------------
# Vacuum / maintenance
# ---------------------------------------------------------------------------


def vacuum() -> None:
    """Run VACUUM to reclaim space and defragment."""
    conn = _get_connection()
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()


def get_db_size() -> int:
    """Return the database file size in bytes."""
    path = get_sqlite_path()
    if path.exists():
        return path.stat().st_size
    return 0


def get_entry_count() -> int:
    """Return the total number of entries."""
    conn = _get_connection()
    try:
        _init_schema(conn)
        cursor = conn.execute("SELECT COUNT(*) FROM entries")
        return cursor.fetchone()[0]
    finally:
        conn.close()