import dataclasses
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from devlog.models import Entry

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_DATA_DIR = Path.home() / ".devlog"
ENTRIES_FILE_NAME = "entries.json"
BACKUPS_DIR_NAME = "backups"

# Tag validation: must match the rules enforced by cli._validate_tags
_TAG_RE = re.compile(r"^[a-z0-9\-]+$")
_MAX_TAG_LENGTH = 32


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
            "Run 'devlog repair' or delete the file to reset."
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


# ---------------------------------------------------------------------------
# Validation (for `devlog repair` / `devlog doctor`)
# ---------------------------------------------------------------------------


@dataclass
class Issue:
    """A single problem found in the on-disk entry store.

    Attributes:
        kind:       short category slug (e.g. ``"missing_field"``,
                    ``"bad_tag"``, ``"bad_timestamp"``, ``"duplicate_id"``).
        entry_id:   id of the offending entry when known, otherwise None.
        index:      0-based index in the on-disk ``entries`` list, or -1
                    if the issue applies to the payload as a whole.
        field:      the offending field name when applicable, otherwise None.
        message:    human-readable description safe to print to the user.
    """
    kind: str
    message: str
    entry_id: Optional[str] = None
    index: int = -1
    field: Optional[str] = None


def _is_valid_iso_timestamp(value: object) -> bool:
    """Return True if *value* parses as a ``YYYY-MM-DDTHH:MM:SSZ`` timestamp.

    Accepts the canonical form produced by ``cli.add``; anything else is
    rejected. We intentionally do not try to be lenient here — invalid
    timestamps are exactly what `devlog repair` should surface.
    """
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        # ``fromisoformat`` accepts a ``+HH:MM`` suffix in py3.7+, so we
        # translate the trailing ``Z`` to ``+00:00`` for the parse.
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    return True


def _validate_tag_values(tags: object) -> list[str]:
    """Return the list of *tags* (assumed to be a list of strings).

    Validation matches ``cli._validate_tags``: each tag must match
    ``^[a-z0-9-]+$`` and be at most 32 characters. This is a *strict*
    re-check — we do not normalise — so users with hand-edited files
    can see what needs fixing.
    """
    if not isinstance(tags, list):
        return [f"tags field must be a list (got {type(tags).__name__})"]
    problems: list[str] = []
    for t in tags:
        if not isinstance(t, str):
            problems.append(f"tag must be a string (got {type(t).__name__})")
            continue
        if not _TAG_RE.fullmatch(t):
            problems.append(f'tag "{t}" has invalid characters')
            continue
        if len(t) > _MAX_TAG_LENGTH:
            problems.append(f'tag "{t}" exceeds {_MAX_TAG_LENGTH} chars')
    return problems


def validate_entries(data: object) -> list[Issue]:
    """Inspect a parsed JSON payload and return a list of issues.

    The function is pure: it does not touch the filesystem. It accepts
    anything (including ``None``) and reports each problem as a separate
    ``Issue`` instance. A return value of ``[]`` means the payload is
    structurally valid and ready to be loaded.

    Validation rules:

      - Top level must be a dict containing an ``"entries"`` key.
      - ``"entries"`` must be a list.
      - Each item must be a dict with: ``id`` (non-empty str),
        ``message`` (str), ``created_at`` (parseable ISO 8601 UTC), and
        ``tags`` (list of valid tag strings). ``updated_at`` is
        optional but, if present, must also be a parseable timestamp.
      - All ``id`` values must be unique within the payload.

    Args:
        data: the value returned by ``json.load``.

    Returns:
        list[Issue]: a (possibly empty) list of issues found. Order is
        deterministic so callers can render stable reports.
    """
    issues: list[Issue] = []

    if not isinstance(data, dict):
        issues.append(
            Issue(kind="bad_root", message="root must be a JSON object")
        )
        return issues

    if "entries" not in data:
        issues.append(
            Issue(kind="missing_field", field="entries", message='missing "entries" key')
        )
        return issues

    raw_entries = data["entries"]
    if not isinstance(raw_entries, list):
        issues.append(
            Issue(
                kind="bad_field",
                field="entries",
                message='"entries" must be a list',
            )
        )
        return issues

    seen_ids: dict[str, int] = {}
    for i, item in enumerate(raw_entries):
        prefix = f"entry[{i}]"
        if not isinstance(item, dict):
            issues.append(
                Issue(
                    kind="bad_item",
                    index=i,
                    message=f"{prefix} must be a JSON object",
                )
            )
            continue

        # id
        entry_id = item.get("id")
        if not isinstance(entry_id, str) or not entry_id:
            issues.append(
                Issue(
                    kind="missing_field",
                    index=i,
                    field="id",
                    message=f"{prefix} missing or empty 'id'",
                )
            )
        else:
            if entry_id in seen_ids:
                issues.append(
                    Issue(
                        kind="duplicate_id",
                        index=i,
                        entry_id=entry_id,
                        message=(
                            f"{prefix} id '{entry_id[:8]}' is a duplicate of "
                            f"entry[{seen_ids[entry_id]}]"
                        ),
                    )
                )
            else:
                seen_ids[entry_id] = i

        # message
        message = item.get("message")
        if not isinstance(message, str):
            issues.append(
                Issue(
                    kind="missing_field",
                    index=i,
                    field="message",
                    entry_id=entry_id if isinstance(entry_id, str) else None,
                    message=f"{prefix} missing or non-string 'message'",
                )
            )

        # created_at
        created_at = item.get("created_at")
        if created_at is None:
            issues.append(
                Issue(
                    kind="missing_field",
                    index=i,
                    field="created_at",
                    entry_id=entry_id if isinstance(entry_id, str) else None,
                    message=f"{prefix} missing 'created_at'",
                )
            )
        elif not _is_valid_iso_timestamp(created_at):
            issues.append(
                Issue(
                    kind="bad_timestamp",
                    index=i,
                    field="created_at",
                    entry_id=entry_id if isinstance(entry_id, str) else None,
                    message=f"{prefix} 'created_at' is not a valid ISO 8601 UTC timestamp",
                )
            )

        # updated_at (optional)
        updated_at = item.get("updated_at", None)
        if updated_at is not None and not _is_valid_iso_timestamp(updated_at):
            issues.append(
                Issue(
                    kind="bad_timestamp",
                    index=i,
                    field="updated_at",
                    entry_id=entry_id if isinstance(entry_id, str) else None,
                    message=f"{prefix} 'updated_at' is not a valid ISO 8601 UTC timestamp",
                )
            )

        # tags
        tags = item.get("tags", [])
        tag_problems = _validate_tag_values(tags)
        for tp in tag_problems:
            issues.append(
                Issue(
                    kind="bad_tag",
                    index=i,
                    field="tags",
                    entry_id=entry_id if isinstance(entry_id, str) else None,
                    message=f"{prefix} {tp}",
                )
            )

    return issues


# ---------------------------------------------------------------------------
# Backup helpers (for `devlog backup` / `devlog restore`)
# ---------------------------------------------------------------------------


def get_backups_dir() -> Path:
    """Return the absolute path to the backups directory.

    The backups directory sits next to the entries file: ``<data_dir>/backups``.
    The directory is *not* created here — callers should create it
    lazily.
    """
    return get_storage_path().parent / BACKUPS_DIR_NAME


def default_backup_filename(now: Optional[datetime] = None) -> str:
    """Return a timestamped backup filename (e.g. ``entries-20260129-153045.json``).

    Args:
        now: optional datetime to use; defaults to ``datetime.now(UTC)``.
    """
    from datetime import timezone
    if now is None:
        now = datetime.now(tz=timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return f"entries-{now.strftime('%Y%m%d-%H%M%S')}.json"