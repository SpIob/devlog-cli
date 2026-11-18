"""Storage layer base: exceptions, paths, backend registry, shared helpers.

This module has NO dependencies on backend implementations (_json, _sqlite).
All backend-specific code lives in those modules and is loaded via the
registry in devlog.storage.__init__.
"""

from __future__ import annotations

import itertools
import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from devlog import _iso, _tagops
from devlog.models import Entry

# ---------------------------------------------------------------------------
# File locking - cross-platform
# ---------------------------------------------------------------------------

if sys.platform == "win32":
    import msvcrt

    def _lock_file(fh, exclusive: bool) -> None:
        """Lock byte 0 of the file (Windows)."""
        pos = fh.tell()
        fh.seek(0)
        try:
            if exclusive:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBRLCK, 1)
        finally:
            fh.seek(pos)

    def _unlock_file(fh) -> None:
        """Unlock byte 0 of the file (Windows)."""
        pos = fh.tell()
        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            fh.seek(pos)

    def _try_lock_file(fh) -> bool:
        """Try to exclusively lock byte 0 without blocking (Windows).

        Returns True when the lock was acquired, False when another
        process holds it.
        """
        pos = fh.tell()
        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
        finally:
            fh.seek(pos)
else:
    import fcntl

    def _lock_file(fh, exclusive: bool) -> None:
        """Lock a file handle (Unix)."""
        if exclusive:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        else:
            fcntl.flock(fh.fileno(), fcntl.LOCK_SH)

    def _unlock_file(fh) -> None:
        """Unlock a file handle (Unix)."""
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def _try_lock_file(fh) -> bool:
        """Try an exclusive non-blocking flock (Unix).

        Returns True when the lock was acquired, False when another
        process holds it.
        """
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False


# ---------------------------------------------------------------------------
# Backend registry
# ---------------------------------------------------------------------------

_BACKENDS: dict[str, object] = {}
_DEFAULT_BACKEND = "json"


def register_backend(name: str, module: object) -> None:
    """Register a storage backend module."""
    _BACKENDS[name] = module


def get_backend_name() -> str:
    """Return the active backend name: 'json' or 'sqlite'."""
    return os.environ.get("DEVLOG_BACKEND", _DEFAULT_BACKEND).lower()


def _is_sqlite_backend() -> bool:
    """Check if SQLite backend is active."""
    return get_backend_name() == "sqlite"


def _get_backend_module():
    """Return the active backend module (lazy load on first use)."""
    name = get_backend_name()
    if name not in _BACKENDS:
        # Import side-effect registers backends
        if name == "json":
            from devlog.storage import _json  # noqa: F401
        elif name == "sqlite":
            from devlog.storage import _sqlite  # noqa: F401
        else:
            raise ValueError(f"Unknown backend: {name}")
    return _BACKENDS[name]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_DATA_DIR = Path.home() / ".devlog"
ENTRIES_FILE_NAME = "entries.json"
BACKUPS_DIR_NAME = "backups"
SQLITE_DB_NAME = "entries.db"

# Tag validation: re-exported from the canonical home in
# :mod:`devlog._tagops` so any module that already imports
# ``storage.TAG_RE`` / ``storage.MAX_TAG_LENGTH`` keeps working.
TAG_RE = _tagops.TAG_RE
MAX_TAG_LENGTH = _tagops.MAX_TAG_LENGTH


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class StorageError(Exception):
    pass


class StoragePermissionError(StorageError):
    def __init__(self, path: Path, action: str):
        super().__init__(
            f"Error: Cannot {action} to storage file at {path}. Check file permissions."
        )


class CorruptedStorageError(StorageError):
    def __init__(self, path: Path):
        super().__init__(
            f"Error: Storage file is corrupted at {path}. "
            "Restore from a backup with `devlog restore <file>`, or "
            "delete the file to start fresh."
        )


class StorageBusyError(StorageError):
    def __init__(self, path: Path):
        super().__init__(
            f"Error: The journal store is busy (another devlog process is "
            f"writing to {path}). Try again in a moment."
        )


# ---------------------------------------------------------------------------
# Cross-process write lock
# ---------------------------------------------------------------------------

# In-process guards for :func:`storage_write_lock`.
#
# - ``_WRITE_RLOCK``: same-thread re-entrancy + mutual exclusion between
#   threads of one process (flock() on a second fd of the same file within
#   one process would deadlock against the first — flock locks are per
#   open-file-description).
# - ``_HELD_LOCKS``: counts lock paths already flocked by this process, so
#   a nested acquisition through a different code path (e.g. the facade
#   wrapping a backend call) skips the second flock instead of
#   deadlocking on it.
_WRITE_RLOCK = threading.RLock()
_HELD_LOCKS: dict[str, int] = {}
_HELD_LOCKS_GUARD = threading.Lock()


@contextmanager
def storage_write_lock(path: Path, timeout: float | None = None):
    """Serialize read-modify-write store mutations across processes.

    A mutating command must hold this lock from its ``load`` through its
    ``save``. The plain ``flock`` inside ``save_entries`` only guards the
    temp file (which is per-process), so two concurrent ``add_entry``
    calls could otherwise both read the same base state and the last
    ``os.replace`` would silently drop the other's entry.

    Re-entrant within a process (both same-thread and via a second fd of
    the same sidecar); cross-process mutual exclusion comes from an
    exclusive non-blocking ``flock`` on a ``<store>.lock`` sidecar,
    retried until *timeout* elapses.

    Args:
        path: the store file path (``entries.json`` or ``entries.db``).
            The lock sidecar is ``<path.name>.lock`` in the same directory.
        timeout: seconds to wait for the cross-process lock before
            raising :class:`StorageBusyError`. Defaults to the
            ``DEVLOG_WRITE_LOCK_TIMEOUT`` env var (float seconds),
            else 10.

    Raises:
        StorageBusyError: when the lock could not be acquired in time,
            or the lock sidecar cannot be created.
    """
    if timeout is None:
        try:
            timeout = float(os.environ.get("DEVLOG_WRITE_LOCK_TIMEOUT", "10"))
        except (TypeError, ValueError):
            timeout = 10.0

    lock_path = path.parent / (path.name + ".lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StoragePermissionError(path, "write") from exc

    # Same-thread nested calls take the RLock fast path; other threads in
    # this process queue here instead of fighting over the flock fd.
    if not _WRITE_RLOCK.acquire(timeout=timeout):
        raise StorageBusyError(path)
    key = str(lock_path)
    fh = None
    took_flock = False
    try:
        with _HELD_LOCKS_GUARD:
            already_held = _HELD_LOCKS.get(key, 0)
            _HELD_LOCKS[key] = already_held + 1

        if already_held == 0:
            deadline = time.monotonic() + timeout
            try:
                fh = open(lock_path, "a+")
            except OSError as exc:
                raise StoragePermissionError(path, "write") from exc
            while not _try_lock_file(fh):
                if time.monotonic() >= deadline:
                    raise StorageBusyError(path)
                time.sleep(0.05)
            took_flock = True
        try:
            yield
        finally:
            if took_flock:
                _unlock_file(fh)
    finally:
        with _HELD_LOCKS_GUARD:
            remaining = _HELD_LOCKS.get(key, 0) - 1
            if remaining <= 0:
                _HELD_LOCKS.pop(key, None)
            else:
                _HELD_LOCKS[key] = remaining
        if fh is not None:
            fh.close()
        _WRITE_RLOCK.release()


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


def get_sqlite_path() -> Path:
    """Return the absolute path to the SQLite database file."""
    if "DEVLOG_DATA_DIR" in os.environ:
        return Path(os.environ["DEVLOG_DATA_DIR"]) / SQLITE_DB_NAME
    return Path.home() / ".devlog" / SQLITE_DB_NAME


def get_data_dir() -> Path:
    """Return the directory in which journal artifacts live.

    Respects ``DEVLOG_DATA_DIR`` like :func:`get_storage_path`; defaults
    to ``~/.devlog``. Used by commands that need to write files
    alongside ``entries.json`` (e.g. ``devlog export`` writing to
    ``<data-dir>/exports/``).
    """
    if "DEVLOG_DATA_DIR" in os.environ:
        return Path(os.environ["DEVLOG_DATA_DIR"])
    return DEFAULT_DATA_DIR


def ensure_storage_dir() -> None:
    """Create the storage directory (and any parents) if it does not exist."""
    # JSON path parent == data dir
    get_storage_path().parent.mkdir(parents=True, exist_ok=True)


def get_backups_dir() -> Path:
    """Return the absolute path to the backups directory.

    The backups directory sits next to the entries file: ``<data_dir>/backups``.
    The directory is *not* created here — callers should create it
    lazily.
    """
    return get_data_dir() / BACKUPS_DIR_NAME


# ---------------------------------------------------------------------------
# Find helpers (backend-agnostic, operate on in-memory list)
# ---------------------------------------------------------------------------


def find_entry_by_id(entries: list[Entry], id_prefix: str) -> Entry | None:
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
    # Phase 1: exact match. UUIDs are lowercase; needle is too.
    for e in entries:
        if e.id == needle:
            return e
    # Phase 2: unique short prefix. Re-scan with startswith().
    matches: list[Entry] = []
    for e in entries:
        if e.id.startswith(needle):
            matches.append(e)
            if len(matches) > 1:
                return None
    return matches[0] if len(matches) == 1 else None


def find_entry_id_prefix_matches(entries: list[Entry], id_prefix: str) -> list[Entry]:
    """Return all entries whose id starts with the given prefix (case-insensitive)."""
    if not id_prefix:
        return []
    needle = id_prefix.lower()
    return [e for e in entries if e.id.startswith(needle)]


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Backup helpers (for `devlog backup` / `devlog restore`)
# ---------------------------------------------------------------------------


def default_backup_filename(now: datetime | None = None) -> str:
    """Return a timestamped backup filename (e.g. ``entries-20260129-153045.json``)."""
    if now is None:
        now = datetime.now(tz=timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return f"entries-{now.strftime('%Y%m%d-%H%M%S')}.json"


# ---------------------------------------------------------------------------
# Single-step undo helpers (for `devlog undo`)
# ---------------------------------------------------------------------------

PRE_WRITE_BACKUP_SUFFIX = ".pre.json"


def default_pre_write_backup_filename(now: datetime | None = None) -> str:
    """Return a timestamped pre-write snapshot filename."""
    if now is None:
        now = datetime.now(tz=timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return f"entries-{now.strftime('%Y%m%d-%H%M%S-%f')}{PRE_WRITE_BACKUP_SUFFIX}"


def pre_write_backup_destination() -> Path:
    """Return a free snapshot path: timestamped, never clobbering."""
    backups_dir = get_backups_dir()
    candidate = backups_dir / default_pre_write_backup_filename()
    n = 0
    while candidate.exists():
        n += 1
        candidate = backups_dir / default_pre_write_backup_filename().replace(
            PRE_WRITE_BACKUP_SUFFIX, f"-{n}{PRE_WRITE_BACKUP_SUFFIX}"
        )
    return candidate


def _list_pre_write_backups() -> list[tuple[datetime, Path]]:
    """Return ``(mtime, path)`` for every pre-write snapshot, newest first."""
    backups_dir = get_backups_dir()
    if not backups_dir.is_dir():
        return []
    out: list[tuple[datetime, Path]] = []
    for p in backups_dir.glob("entries-*.pre.json"):
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        out.append((mtime, p))
    # Name as tiebreaker: the %f suffix is zero-padded, so within an
    # identical mtime the lexicographic order is still chronological.
    out.sort(key=lambda mp: (mp[0], mp[1].name), reverse=True)
    return out


def load_pre_write_backup(path: Path) -> list[Entry]:
    """Load entries from a pre-write snapshot produced by ``save_entries``.

    Snapshots are written by the same serialization as the live store,
    so they are loaded with the tolerant ``Entry.from_dict`` path.
    The ``entries`` key is required (a snapshot without it is not a
    snapshot); per-row problems are tolerated via ``from_dict``.

    Raises:
        CorruptedStorageError: when the file is not valid JSON or has
            no ``entries`` list.
    """
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, PermissionError) as exc:
        raise CorruptedStorageError(path) from exc
    except json.JSONDecodeError as exc:
        raise CorruptedStorageError(path) from exc
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise CorruptedStorageError(path)
    return [Entry.from_dict(item) for item in data["entries"]]


def latest_pre_write_backup() -> Path | None:
    """Return the newest pre-write snapshot path, or ``None`` if none exist."""
    backups = _list_pre_write_backups()
    return backups[0][1] if backups else None


def prune_pre_write_backups(keep: int = 10) -> int:
    """Delete the oldest pre-write snapshots beyond *keep*; return the count."""
    if keep < 0:
        keep = 0
    backups = _list_pre_write_backups()
    removed = 0
    for _mtime, p in backups[keep:]:
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed


# ---------------------------------------------------------------------------
# Stats v2 helpers (for `devlog stats`)
# ---------------------------------------------------------------------------


def compute_streaks(dates: list) -> tuple[int, int]:
    """Return ``(current, longest)`` consecutive-day entry streaks."""
    if not dates:
        return 0, 0
    unique = sorted(set(dates))
    today = datetime.now(tz=timezone.utc).date()

    longest = 1
    run = 1
    for prev, curr in itertools.pairwise(unique):
        if (curr - prev).days == 1:
            run += 1
            longest = max(longest, run)
        else:
            run = 1
    # Current streak: count backwards from today (or yesterday, for the
    # common "haven't written yet today" case — yesterday's streak is
    # still alive until the day is over).
    anchor = (
        today
        if today in unique
        else (today - timedelta(days=1) if (today - timedelta(days=1)) in unique else None)
    )
    current = 0
    if anchor is not None:
        current = 1
        d = anchor
        while (d - timedelta(days=1)) in unique:
            d -= timedelta(days=1)
            current += 1
    return current, longest


def compute_tag_cooccurrence(
    entries: list[Entry], top: int = 5
) -> list[tuple[tuple[str, str], int]]:
    """Return the most common tag pairs, most frequent first."""
    counter: dict[tuple[str, str], int] = {}
    for e in entries:
        tags = sorted(set(e.tags))
        for i, a in enumerate(tags):
            for b in tags[i + 1 :]:
                pair = (a, b)
                counter[pair] = counter.get(pair, 0) + 1
    ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:top]


def compute_days_since_last(dates: list) -> int | None:
    """Return whole days between today (UTC) and the most recent entry date."""
    if not dates:
        return None
    today = datetime.now(tz=timezone.utc).date()
    return (today - max(dates)).days


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
    entry_id: str | None = None
    index: int = -1
    field: str | None = None


def _validate_tag_values(tags: object) -> list[str]:
    """Return the list of *tags* (assumed to be a list of strings)."""
    if not isinstance(tags, list):
        return ["tags must be a list"]
    problems = []
    for t in tags:
        if not isinstance(t, str):
            problems.append("tag must be a string")
            continue
        if not TAG_RE.fullmatch(t):
            problems.append(f'tag "{t}" has invalid characters')
            continue
        if len(t) > MAX_TAG_LENGTH:
            problems.append(f'tag "{t}" exceeds {MAX_TAG_LENGTH} chars')
    return problems


def validate_entries(data: object) -> list[Issue]:
    """Inspect a parsed JSON payload and return a list of issues."""
    issues: list[Issue] = []

    if not isinstance(data, dict):
        issues.append(Issue(kind="bad_root", message="root must be a JSON object"))
        return issues

    if "entries" not in data:
        issues.append(Issue(kind="missing_field", field="entries", message='missing "entries" key'))
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
        elif not _iso.is_valid_iso_timestamp(created_at):
            issues.append(
                Issue(
                    kind="bad_timestamp",
                    index=i,
                    field="created_at",
                    entry_id=entry_id if isinstance(entry_id, str) else None,
                    message=(f"{prefix} 'created_at' is not a valid ISO 8601 UTC timestamp"),
                )
            )

        # updated_at (optional)
        updated_at = item.get("updated_at", None)
        if updated_at is not None and not _iso.is_valid_iso_timestamp(updated_at):
            issues.append(
                Issue(
                    kind="bad_timestamp",
                    index=i,
                    field="updated_at",
                    entry_id=entry_id if isinstance(entry_id, str) else None,
                    message=(f"{prefix} 'updated_at' is not a valid ISO 8601 UTC timestamp"),
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
# Coercion, repair plan, and corrupt-file recovery.
# ---------------------------------------------------------------------------


def coerce_entry(item: dict) -> Entry | None:
    """Best-effort construction of an ``Entry`` from a parsed item."""
    try:
        eid = item["id"]
        message = item.get("message", "")
        created_at = item.get("created_at", "")
        tags = item.get("tags", [])
        updated_at = item.get("updated_at")
    except (KeyError, TypeError):
        return None
    if not isinstance(eid, str) or not eid:
        return None
    if not isinstance(message, str):
        return None
    if not isinstance(tags, list):
        return None
    if not isinstance(created_at, str):
        return None
    if updated_at is not None and not isinstance(updated_at, str):
        return None
    norm_tags = [t for t in tags if isinstance(t, str)]
    return Entry(
        id=eid,
        message=message,
        tags=norm_tags,
        created_at=created_at,
        updated_at=updated_at,
    )


def build_repair_plan(raw: object) -> tuple[list[Entry], list[Issue]]:
    """Split a raw payload into ``(repaired_entries, remaining_issues)``."""
    issues = validate_entries(raw)

    if not isinstance(raw, dict) or not isinstance(raw.get("entries"), list):
        return [], issues

    kept: list[Entry] = []
    seen_ids: set[str] = set()
    for _i, item in enumerate(raw["entries"]):
        entry = coerce_entry(item) if isinstance(item, dict) else None
        if entry is None:
            continue  # already covered by `bad_item` / `missing_field` issue
        if entry.id in seen_ids:
            continue  # duplicate; issue already reported
        # Re-check created_at so we drop malformed-but-buildable rows.
        if not entry.created_at or not _iso.is_valid_iso_timestamp(entry.created_at):
            continue
        # Strip any individual bad tags rather than dropping the entry.
        cleaned_tags = [t for t in entry.tags if _tagops._is_valid_tag(t)]
        if len(cleaned_tags) != len(entry.tags):
            entry = Entry(
                id=entry.id,
                message=entry.message,
                tags=cleaned_tags,
                created_at=entry.created_at,
                updated_at=entry.updated_at,
            )
        if entry.updated_at is not None and not _iso.is_valid_iso_timestamp(entry.updated_at):
            entry = Entry(
                id=entry.id,
                message=entry.message,
                tags=entry.tags,
                created_at=entry.created_at,
                updated_at=None,
            )
        kept.append(entry)
        seen_ids.add(entry.id)
    return kept, issues


# Raw-payload reading is backend-specific (the JSON backend can recover
# from a truncated file; SQLite reads its own table), so `read_raw_entries`
# and `_try_recover_entries_from_corrupt_json` live in devlog.storage._json
# and are dispatched by the facade.
