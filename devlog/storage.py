import dataclasses
import json
import os
from pathlib import Path
from typing import List

from devlog.models import Entry

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_DATA_DIR = Path.home() / ".devlog"
ENTRIES_FILE_NAME = "entries.json"


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class StorageError(Exception):
    pass


class StoragePermissionError(StorageError):
    def __init__(self, path: Path, action: str):
        super().__init__(
            f"Error: Cannot {action} to storage file at {path}. "
            "Check file permissions."
        )


class CorruptedStorageError(StorageError):
    def __init__(self, path: Path):
        super().__init__(
            f"Error: Storage file is corrupted at {path}. "
            "Back it up and delete it to reset, or restore from backup."
        )


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def get_storage_path() -> Path:
    """Return the absolute path to the entries JSON file.

    Uses the DEVLOG_DATA_DIR environment variable when set; otherwise falls
    back to ~/.devlog/entries.json.

    Returns:
        Path: absolute path to the storage file.
    """
    if "DEVLOG_DATA_DIR" in os.environ:
        return Path(os.environ["DEVLOG_DATA_DIR"]) / ENTRIES_FILE_NAME
    return DEFAULT_DATA_DIR / ENTRIES_FILE_NAME


def ensure_storage_dir() -> None:
    """Create the storage directory (and any parents) if it does not exist."""
    path = get_storage_path()
    path.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Read / write
# ---------------------------------------------------------------------------


def load_entries() -> List[Entry]:
    """Load all entries from disk.

    Returns an empty list when the file does not yet exist.

    Returns:
        List[Entry]: list of Entry dataclass instances.

    Raises:
        StoragePermissionError: if the file cannot be read due to permissions.
        CorruptedStorageError: if the file contains invalid JSON.
    """
    path = get_storage_path()

    if not path.exists():
        return []

    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except PermissionError:
        raise StoragePermissionError(path, "read")
    except json.JSONDecodeError:
        raise CorruptedStorageError(path)
    except OSError:
        raise StoragePermissionError(path, "read")

    return [Entry(**item) for item in data["entries"]]


def save_entries(entries: List[Entry]) -> None:
    """Persist all entries to disk atomically.

    Writes to a .tmp file first, then uses os.replace for an atomic swap so a
    crash mid-write cannot corrupt the real storage file.

    Args:
        entries: complete list of Entry objects to persist.

    Raises:
        StoragePermissionError: if the file or its directory cannot be written.
    """
    ensure_storage_dir()
    path = get_storage_path()
    tmp_path = path.with_suffix(".tmp")

    payload = {"entries": [dataclasses.asdict(e) for e in entries]}

    try:
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
    except (PermissionError, OSError):
        raise StoragePermissionError(path, "write")

    try:
        os.replace(tmp_path, path)
    except (PermissionError, OSError):
        raise StoragePermissionError(path, "write")


def add_entry(new_entry: Entry) -> None:
    """Append a single entry to persistent storage.

    Loads the current entries, appends the new one, and saves the full list.

    Args:
        new_entry: the Entry to persist.
    """
    entries = load_entries()
    entries.append(new_entry)
    save_entries(entries)


def find_entry_by_id(entries: List[Entry], id_prefix: str) -> Entry | None:
    """Find an entry by exact id or unique short-id prefix.

    Search order:
        1. Exact full id match.
        2. Unique prefix match (case-insensitive, must be unique among all entries).

    Args:
        entries:   list of Entry objects to search.
        id_prefix: the id or prefix provided by the user.

    Returns:
        The matching Entry, or ``None`` if no match / ambiguous.
    """
    if not id_prefix:
        return None

    needle = id_prefix.lower()
    exact = [e for e in entries if e.id.lower() == needle]
    if exact:
        return exact[0]

    matches = [e for e in entries if e.id.lower().startswith(needle)]
    if len(matches) == 1:
        return matches[0]
    return None


def find_entry_id_prefix_matches(entries: List[Entry], id_prefix: str) -> List[Entry]:
    """Return all entries whose id starts with the given prefix (case-insensitive)."""
    if not id_prefix:
        return []
    needle = id_prefix.lower()
    return [e for e in entries if e.id.lower().startswith(needle)]


def update_entry(updated: Entry) -> bool:
    """Replace an entry in storage by id, preserving insertion order.

    Args:
        updated: the new Entry state. Must have the same id as the existing entry.

    Returns:
        True if a matching entry was found and updated; False otherwise.

    Raises:
        StoragePermissionError: if the file cannot be written.
    """
    entries = load_entries()
    for i, e in enumerate(entries):
        if e.id == updated.id:
            entries[i] = updated
            save_entries(entries)
            return True
    return False


def delete_entry(entry_id: str) -> bool:
    """Remove an entry from storage by id.

    Args:
        entry_id: the full id of the entry to delete.

    Returns:
        True if an entry was removed; False if not found.

    Raises:
        StoragePermissionError: if the file cannot be written.
    """
    entries = load_entries()
    new_entries = [e for e in entries if e.id != entry_id]
    if len(new_entries) == len(entries):
        return False
    save_entries(new_entries)
    return True