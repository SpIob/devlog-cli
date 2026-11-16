"""Compatibility shim: re-exports from devlog.storage._sqlite with legacy API.

This module exists for backward compatibility with code that imports
``from devlog import sqlite_storage``. The actual implementation lives in
``devlog.storage._sqlite``.
"""

import dataclasses

from devlog.models import Entry
from devlog.storage._base import (
    build_repair_plan,
    find_entry_by_id,
    find_entry_id_prefix_matches,
    validate_entries,
)
from devlog.storage._sqlite import (
    _get_connection,
    _init_schema,
    add_entry,
    delete_entry,
    load_entries,
    save_entries,
    update_entry,
)

__all__ = [
    "add_entry",
    "build_repair_plan_sqlite",
    "delete_entry",
    "export_to_json",
    "find_entry_by_id",
    "find_entry_id_prefix_matches",
    "get_backend_name",
    "get_db_size",
    "get_entry_count",
    "get_sqlite_path",
    "is_sqlite_backend",
    "load_entries",
    "migrate_from_json",
    "read_raw_entries_sqlite",
    "save_entries",
    "update_entry",
    "vacuum",
    "validate_entries_sqlite",
]


def get_sqlite_path():
    """Return the absolute path to the SQLite database file."""
    from devlog.storage._base import get_sqlite_path as _get_sqlite_path

    return _get_sqlite_path()


def get_backend_name() -> str:
    """Return the active backend name: 'json' or 'sqlite'."""
    import os

    return os.environ.get("DEVLOG_BACKEND", "json").lower()


def is_sqlite_backend() -> bool:
    """Check if SQLite backend is active."""
    return get_backend_name() == "sqlite"


def read_raw_entries_sqlite() -> tuple[object, bool]:
    """Return all entries from SQLite as a JSON-compatible payload."""
    entries = load_entries()
    payload = {"entries": [dataclasses.asdict(e) for e in entries]}
    return payload, False


def validate_entries_sqlite(data: object):
    """Validate entries from a SQLite export (for import validation)."""
    if isinstance(data, dict) and "entries" in data:
        return validate_entries(data)
    from devlog.storage._base import Issue

    return [Issue(kind="bad_root", message="root must be a JSON object with 'entries' key")]


def build_repair_plan_sqlite(raw: object):
    """Build a repair plan from a raw payload (for import)."""
    return build_repair_plan(raw)


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
                        __import__("json").dumps(e.tags, ensure_ascii=False),
                    ),
                )
    finally:
        conn.close()


def export_to_json() -> list[Entry]:
    """Export all SQLite entries as a list of Entry (for backup/migration)."""
    return load_entries()


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
