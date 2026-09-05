import dataclasses
import json
import os
from dataclasses import dataclass
from datetime import date as _date, datetime, timezone
from pathlib import Path
from typing import List, Optional

from devlog import _iso
from devlog import _tagops
from devlog.models import Entry

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_DATA_DIR = Path.home() / ".devlog"
ENTRIES_FILE_NAME = "entries.json"
BACKUPS_DIR_NAME = "backups"

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
            f"Error: Cannot {action} to storage file at {path}. "
            "Check file permissions."
        )


class CorruptedStorageError(StorageError):
    def __init__(self, path: Path):
        super().__init__(
            f"Error: Storage file is corrupted at {path}. "
            "Restore from a backup with `devlog restore <file>`, or "
            "delete the file to start fresh."
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

    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise CorruptedStorageError(path)

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

    Note:
        On-disk ids are minted by ``uuid.uuid4()`` and are therefore
        always lowercase, so we only lowercase the *user-supplied*
        prefix once and compare against the stored ids directly. Doing
        ``e.id.lower()`` per entry is wasted work — on a 10k journal
        with 100 lookups that was 2M redundant ``str.lower()`` calls.
    """
    if not id_prefix:
        return None

    needle = id_prefix.lower()
    # Phase 1: exact match. UUIDs are lowercase; needle is too. If a
    # user's prefix happens to be a full UUID, the comparison is just
    # an O(n) string scan with no allocation per entry.
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


def find_entry_id_prefix_matches(entries: List[Entry], id_prefix: str) -> List[Entry]:
    """Return all entries whose id starts with the given prefix (case-insensitive)."""
    if not id_prefix:
        return []
    needle = id_prefix.lower()
    return [e for e in entries if e.id.startswith(needle)]


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


def _validate_tag_values(tags: object) -> list[str]:
    """Return the list of *tags* (assumed to be a list of strings).

    Validation matches ``cli._validate_tags``: each tag must match
    ``^[a-z0-9-]+$`` and be at most 32 characters. This is a *strict*
    re-check — we do not normalise — so users with hand-edited files
    can see what needs fixing.
    """
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
        elif not _iso.is_valid_iso_timestamp(created_at):
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
        if updated_at is not None and not _iso.is_valid_iso_timestamp(updated_at):
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
    if now is None:
        now = datetime.now(tz=timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return f"entries-{now.strftime('%Y%m%d-%H%M%S')}.json"


# ---------------------------------------------------------------------------
# Timezone helpers
# ---------------------------------------------------------------------------


def local_date_for(iso: str, tz) -> "date":
    """Return the local date for a stored UTC ISO 8601 timestamp.

    Args:
        iso: a timestamp string in ``YYYY-MM-DDTHH:MM:SSZ`` form.
        tz: a :class:`zoneinfo.ZoneInfo` (or ``None``, which falls back
            to UTC — i.e. returns the date portion of the stored string).

    Returns:
        A :class:`datetime.date` in the requested zone.

    Notes:
        Unparseable input returns the epoch date (``1970-01-01``) so
        callers can still sort/filter without raising. The repair
        command's stats path drops unparseable rows; this helper
        exists for the *read* side, where we'd rather show a row in a
        wrong bucket than crash.
    """
    if not iso:
        return _date(1970, 1, 1)
    try:
        dt = _iso.parse_utc_iso(iso)
    except (ValueError, TypeError, AttributeError):
        return _date(1970, 1, 1)
    if tz is not None:
        dt = dt.astimezone(tz)
    return dt.date()


def _resolve_zoneinfo(tz_name: str):
    """Resolve an IANA tz name to a ``ZoneInfo``, or raise ``ValueError``.

    Kept as a separate function so tests can patch the implementation
    without monkey-patching the stdlib import.

    Args:
        tz_name: a non-empty IANA name like ``"America/New_York"``.

    Raises:
        ValueError: when the name is not recognised by the system
            zoneinfo data (or the ``tzdata`` package on Windows).
    """
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    except ImportError as exc:  # pragma: no cover - 3.9+ always has it
        raise ValueError(
            "zoneinfo is not available on this Python build"
        ) from exc
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(str(exc)) from exc


def utc_now_iso() -> str:
    """Return the current UTC time as a ``YYYY-MM-DDTHH:MM:SSZ`` string.

    Centralised so every "stamp an entry updated_at" call site produces
    the same format and the on-disk contract has one definition.
    """
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Coercion, repair plan, and corrupt-file recovery.
#
# These live alongside the validator because they implement the
# *write-side* counterpart of ``validate_entries``: validation reports
# issues, the repair plan decides what to do about them, the coercion
# helper rebuilds a row from a raw dict, and the recovery helper makes
# the best of a half-written file.
# ---------------------------------------------------------------------------


def coerce_entry(item: dict) -> Entry | None:
    """Best-effort construction of an ``Entry`` from a parsed item.

    Returns ``None`` if any required field is missing or the wrong type.
    The returned ``Entry`` will *not* have a valid ``created_at`` (we
    use a placeholder) — the caller is expected to either fix or drop
    these rows. Used by ``devlog repair`` and ``devlog doctor``.
    """
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
    """Split a raw payload into ``(repaired_entries, remaining_issues)``.

    Strategy:

        * Entries that the validator cannot even build from the item
          (missing id, wrong root types, etc.) are dropped — counted as
          ``bad_item`` issues.
        * Entries with a bad ``created_at`` are dropped (a recoverable
          timestamp is not inferrable from context).
        * Entries with one or more invalid tags are *kept*, with the
          bad tags stripped. The ``bad_tag`` issue is still reported so
          the user knows which entries were touched.
        * Duplicate ids keep the *first* occurrence; subsequent ones are
          reported as ``duplicate_id`` and dropped.

    Args:
        raw: the value parsed from the JSON file.

    Returns:
        A 2-tuple ``(entries, issues)`` where ``entries`` is the list of
        ``Entry`` objects that should be persisted, and ``issues`` is
        the full list of problems found (including those the plan also
        fixes, so the user can see them).
    """
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
        # Hand-edited files (or migrations from older devlog versions)
        # can easily have a single bad tag among many good ones — the
        # entry itself is still useful, so we salvage it.
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
            return json.load(fh), False
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
        raise CorruptedStorageError(path)