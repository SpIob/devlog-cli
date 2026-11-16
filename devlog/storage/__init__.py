"""devlog storage facade — unified API over JSON and SQLite backends.

This module is the public interface. It re-exports everything from the base
layer and dispatches mutating/loading operations to the active backend
(json or sqlite, selected via DEVLOG_BACKEND env var).
"""

from __future__ import annotations

from pathlib import Path

from devlog._time import _resolve_zoneinfo, local_date_for, utc_now_iso

# Trigger backend registration on first import
from devlog.storage import _json, _sqlite  # noqa: F401

# Import all public API from the base layer (shared across backends)
from devlog.storage._base import (
    MAX_TAG_LENGTH,
    PRE_WRITE_BACKUP_SUFFIX,
    TAG_RE,
    CorruptedStorageError,
    Issue,
    StorageError,
    StoragePermissionError,
    _get_backend_module,
    _is_sqlite_backend,
    _list_pre_write_backups,
    _validate_tag_values,
    build_repair_plan,
    coerce_entry,
    compute_days_since_last,
    compute_streaks,
    compute_tag_cooccurrence,
    default_backup_filename,
    default_pre_write_backup_filename,
    ensure_storage_dir,
    find_entry_by_id,
    find_entry_id_prefix_matches,
    get_backend_name,
    get_backups_dir,
    get_data_dir,
    get_sqlite_path,
    get_storage_path,
    latest_pre_write_backup,
    load_pre_write_backup,
    pre_write_backup_destination,
    prune_pre_write_backups,
    register_backend,
    validate_entries,
)


# Dispatch functions that route to the active backend
def load_entries() -> list:
    """Load all entries from the active backend."""
    return _get_backend_module().load_entries()


def save_entries(entries: list) -> None:
    """Persist all entries to the active backend atomically."""
    return _get_backend_module().save_entries(entries)


def add_entry(new_entry) -> None:
    """Append a single entry to the active backend."""
    return _get_backend_module().add_entry(new_entry)


def update_entry(updated) -> bool:
    """Replace an entry in the active backend by id."""
    return _get_backend_module().update_entry(updated)


def delete_entry(entry_id: str) -> bool:
    """Remove an entry from the active backend by id."""
    return _get_backend_module().delete_entry(entry_id)


def read_raw_entries() -> tuple[object, bool]:
    """Return the raw storage payload from the active backend.

    The second element is True when the payload had to be recovered
    from a partially-written store (JSON backend only).

    Raises:
        FileNotFoundError: when the store does not exist yet.
        CorruptedStorageError: when the store is unreadable and no
            recoverable portion can be extracted (JSON backend only).
    """
    return _get_backend_module().read_raw_entries()


def get_store_path() -> Path:
    """Return the on-disk path of the active backend's store.

    ``<data-dir>/entries.json`` on the JSON backend,
    ``<data-dir>/entries.db`` on SQLite. Commands that need to report
    or stat the active store use this instead of branching on the
    backend.
    """
    return _get_backend_module().get_store_path()


__all__ = [
    "MAX_TAG_LENGTH",
    "PRE_WRITE_BACKUP_SUFFIX",
    "TAG_RE",
    "CorruptedStorageError",
    "Issue",
    "StorageError",
    "StoragePermissionError",
    "_is_sqlite_backend",
    "_list_pre_write_backups",
    "_resolve_zoneinfo",
    "_validate_tag_values",
    "add_entry",
    "build_repair_plan",
    "coerce_entry",
    "compute_days_since_last",
    "compute_streaks",
    "compute_tag_cooccurrence",
    "default_backup_filename",
    "default_pre_write_backup_filename",
    "delete_entry",
    "ensure_storage_dir",
    "find_entry_by_id",
    "find_entry_id_prefix_matches",
    "get_backend_name",
    "get_backups_dir",
    "get_data_dir",
    "get_sqlite_path",
    "get_storage_path",
    "get_store_path",
    "latest_pre_write_backup",
    "load_entries",
    "load_pre_write_backup",
    "local_date_for",
    "pre_write_backup_destination",
    "prune_pre_write_backups",
    "read_raw_entries",
    "register_backend",
    "save_entries",
    "update_entry",
    "utc_now_iso",
    "validate_entries",
]
