"""JSON file storage backend for devlog.

Default backend. Stores entries as a single JSON file with atomic writes.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import sys
from pathlib import Path

from devlog.models import Entry
from devlog.storage._base import (
    CorruptedStorageError,
    StoragePermissionError,
    _lock_file,
    _unlock_file,
    ensure_storage_dir,
    get_backups_dir,
    get_storage_path,
    pre_write_backup_destination,
    register_backend,
)

# ---------------------------------------------------------------------------
# Public API (called via devlog.storage facade)
# ---------------------------------------------------------------------------


def get_store_path() -> Path:
    """Return the path to the JSON store file."""
    return get_storage_path()


def corrupt_issue(_exc: Exception) -> dict:
    """Describe an unreadable JSON store for ``doctor``'s report.

    The JSON backend has exactly one corruption mode (the file does not
    parse), so the exception text is not repeated in the message — the
    report already carries the path.
    """
    return {
        "kind": "corrupt_json",
        "message": "entries.json is not valid JSON",
        "entry_id": None,
    }


def load_entries() -> list[Entry]:
    """Load all entries from the JSON file, in insertion order."""
    path = get_storage_path()

    if not path.exists():
        return []

    try:
        with path.open("r", encoding="utf-8") as fh:
            _lock_file(fh, exclusive=False)
            try:
                data = json.load(fh)
            finally:
                _unlock_file(fh)
    except PermissionError:
        raise StoragePermissionError(path, "read")
    except json.JSONDecodeError:
        raise CorruptedStorageError(path)
    except OSError:
        raise StoragePermissionError(path, "read")

    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise CorruptedStorageError(path)

    return [Entry.from_dict(item) for item in data["entries"]]


def save_entries(entries: list[Entry]) -> None:
    """Persist all entries to JSON atomically."""
    ensure_storage_dir()
    path = get_storage_path()
    tmp_path = path.with_suffix(".tmp")

    payload = {"entries": [dataclasses.asdict(e) for e in entries]}

    try:
        with tmp_path.open("w", encoding="utf-8") as fh:
            _lock_file(fh, exclusive=True)
            try:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                _unlock_file(fh)
    except (PermissionError, OSError):
        raise StoragePermissionError(path, "write")

    # Single-step undo support: keep the previous store as a pre-write
    # backup before the replace. A missing current file (first-ever
    # write) has no previous state to preserve — skip so the first add
    # doesn't litter backups/. Copy failures are deliberately
    # best-effort: undo is a convenience, never a gate on writing.
    if path.exists():
        try:
            backups_dir = get_backups_dir()
            backups_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, pre_write_backup_destination())
        except (OSError, PermissionError):
            pass

    try:
        os.replace(tmp_path, path)
    except (PermissionError, OSError):
        raise StoragePermissionError(path, "write")


def add_entry(new_entry: Entry) -> None:
    """Append a single entry to persistent storage."""
    entries = load_entries()
    entries.append(new_entry)
    save_entries(entries)


def update_entry(updated: Entry) -> bool:
    """Replace an entry in storage by id, preserving insertion order."""
    entries = load_entries()
    for i, e in enumerate(entries):
        if e.id == updated.id:
            entries[i] = updated
            save_entries(entries)
            return True
    return False


def delete_entry(entry_id: str) -> bool:
    """Remove an entry from storage by id."""
    entries = load_entries()
    new_entries = [e for e in entries if e.id != entry_id]
    if len(new_entries) == len(entries):
        return False
    save_entries(new_entries)
    return True


def _try_recover_entries_from_corrupt_json(path: Path) -> object | None:
    """Best-effort recovery when ``entries.json`` is not valid JSON.

    Walks the file from the end looking for the largest prefix that
    parses as valid JSON. The most common corruption is a trailing
    write that didn't finish (e.g. power loss mid-flush), so a
    right-trim usually recovers the previous good state.

    Returns:
        The parsed JSON object if recovery succeeded, else ``None``.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text:
        return None

    # Try right-truncation: only attempt the parse at positions that
    # end on a JSON-object/array boundary, walking backward from the
    # end. The first successful parse wins. Bounded by O(n) for
    # pathological files but typically just a handful of attempts.
    for end in range(len(text), 0, -1):
        candidate = text[:end]
        last = candidate.rstrip()[-1:] if candidate.rstrip() else ""
        if last not in ("}", "]"):
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def read_raw_entries() -> tuple[object, bool]:
    """Return the parsed JSON payload at the storage path, or raise.

    The second element of the returned tuple is ``True`` when the file
    was corrupt and we had to fall back to right-truncation to recover
    a usable JSON object. Callers use this to decide whether to
    rewrite the file even if no validation issues are reported (the
    trailing garbage would otherwise stay on disk).

    Raises:
        FileNotFoundError: when the file does not yet exist.
        CorruptedStorageError: when the file contains invalid JSON
            and no recoverable portion can be extracted.
        StoragePermissionError: when the file is unreadable.
    """
    path = get_storage_path()
    if not path.exists():
        raise FileNotFoundError(path)
    try:
        with path.open("r", encoding="utf-8") as fh:
            _lock_file(fh, exclusive=False)
            try:
                return json.load(fh), False
            finally:
                _unlock_file(fh)
    except PermissionError as exc:
        raise StoragePermissionError(path, "read") from exc
    except OSError as exc:
        raise StoragePermissionError(path, "read") from exc
    except json.JSONDecodeError:
        # Try to recover by truncating to the last valid JSON object
        # boundary. If that succeeds, return the recovered payload
        # (which may be missing trailing entries the user added since
        # the file was last flushed) so doctor/repair can still
        # surface whatever survived. If recovery fails, raise the
        # original-style corruption error.
        recovered = _try_recover_entries_from_corrupt_json(path)
        if recovered is not None:
            return recovered, True
        raise CorruptedStorageError(path) from None


# Register this backend
register_backend("json", sys.modules[__name__])
