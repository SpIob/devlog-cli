"""devlog CLI — Shared helpers for the devlog commands (load/exit glue,
validation, editor launching, and doctor report plumbing).
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import sys
from pathlib import Path

import click

from devlog import (
    _dates,
    _iso,
    _tagops,
    storage,
    ui,
)
from devlog.models import BY_CREATED_AT, Entry
from devlog.storage import StorageError

# Console instances (ui re-exports) so command bodies read `console.print`
console = ui.console
err_console = ui.err_console

# BY_CREATED_AT is re-exported under its historic private name
_BY_CREATED_AT = BY_CREATED_AT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _handle_storage_error(exc: StorageError) -> None:
    """Render a storage error and exit with code 2.

    Args:
        exc: the StorageError (or subclass) that was raised.
    """
    ui.print_error(str(exc).removeprefix("Error: "))
    sys.exit(2)


def _load_entries_or_exit() -> list[Entry]:
    """Load all entries, printing and exiting cleanly on a storage error.

    Centralises the 15+ identical ``try: storage.load_entries() except
    StorageError`` wrappers that previously preceded every command body.
    Returns the loaded list (always non-None) so callers can chain
    operations immediately.
    """
    try:
        return storage.load_entries()
    except StorageError as exc:
        _handle_storage_error(exc)
        return []


def _resolve_tz_or_exit():
    """Resolve the local timezone from DEVLOG_TZ, or exit with an error.

    Wraps :func:`devlog._dates._resolve_local_tz` so the CLI layer
    handles the ValueError raised on invalid zone names.
    """
    try:
        return _dates._resolve_local_tz()
    except ValueError as exc:
        ui.print_error(
            f'Invalid DEVLOG_TZ: {exc}. '
            "Use an IANA name like America/New_York or Europe/Berlin."
        )
        sys.exit(1)


def _resolve_entry_by_id_or_exit(
    all_entries: list[Entry], entry_id: str, *, ignore_missing: bool = False
) -> Entry | None:
    """Resolve an id (full or unique short prefix) to an Entry or exit.

    Args:
        all_entries: the in-memory entry list to search.
        entry_id:    the user-supplied id or unique short prefix.
        ignore_missing: when True, return ``None`` instead of exiting
            with code 1 if the id is not found. Ambiguous prefixes
            always exit 1, regardless of this flag.

    Returns:
        The matching :class:`Entry`, or ``None`` if ``ignore_missing``
        is set and no entry matched.

    Side effects:
        Calls :func:`sys.exit` on failure.
    """
    match = storage.find_entry_by_id(all_entries, entry_id)
    if match is not None:
        return match
    if ignore_missing:
        # Distinguish "not found" from "ambiguous" by probing the
        # prefix matcher. We only return None for the genuine
        # not-found case; ambiguity still exits.
        candidates = storage.find_entry_id_prefix_matches(all_entries, entry_id)
        if len(candidates) <= 1:
            return None
        short_ids = ", ".join(e.short_id for e in candidates)
        ui.print_error(
            f'ID prefix "{entry_id}" matches multiple entries: {short_ids}. '
            "Use a longer prefix."
        )
        sys.exit(1)
    # Distinguish "not found" from "ambiguous"
    candidates = storage.find_entry_id_prefix_matches(all_entries, entry_id)
    if len(candidates) > 1:
        short_ids = ", ".join(e.short_id for e in candidates)
        ui.print_error(
            f'ID prefix "{entry_id}" matches multiple entries: {short_ids}. '
            "Use a longer prefix."
        )
    else:
        ui.print_error(f'No entry found with id "{entry_id}".')
    sys.exit(1)


def _print_jsonl(entries) -> None:
    """Print *entries* as one JSON object per line (the ``--quiet`` contract)."""
    for entry in entries:
        print(json.dumps(dataclasses.asdict(entry), ensure_ascii=False))


def _require_positive_int(name: str, value: int) -> None:
    """Validate a positive-int CLI option; exit 1 with the same message CLI users already see.

    The default message is ``f"{name} must be a positive integer."`` which
    preserves the contract tests assert against (e.g. ``"--limit must be
    a positive integer."``).
    """
    if value <= 0:
        ui.print_error(f"{name} must be a positive integer.")
        sys.exit(1)


def _parse_search_query(query: str) -> list[str]:
    """Parse a search query into terms, respecting quoted phrases.

    - Split on whitespace
    - Quoted phrases ("...") are kept as a single term
    - Empty result for empty/whitespace-only input

    Args:
        query: raw query string from the user

    Returns:
        List of terms (strings) to match with AND logic
    """
    import shlex
    try:
        # shlex.split handles quoted strings properly
        return shlex.split(query)
    except ValueError:
        # Fallback for unbalanced quotes - treat as simple split
        return query.split()


# ---------------------------------------------------------------------------
# edit
# ---------------------------------------------------------------------------

def _validate_tags_or_exit(raw_tags: tuple[str, ...]) -> list[str]:
    """Run :func:`_tagops._validate_tags` and bail on ``click.UsageError``.

    Centralises the ``try/except → ui.print_error → sys.exit(1)`` dance
    that ``_edit_compute_tags`` (and the ``add``/``tag`` commands) used
    to inline.
    """
    try:
        return _tagops._validate_tags(raw_tags)
    except click.UsageError as exc:
        ui.print_error(str(exc))
        sys.exit(1)


def _edit_compute_tags(
    current: list[str],
    set_tags: tuple[str, ...],
    add_tags: tuple[str, ...],
    remove_tags: tuple[str, ...],
) -> list[str]:
    """Compute the new tag list from ``current`` plus the three merge ops.

    Order: ``--tag`` (set) replaces, then ``--add-tag`` unions, then
    ``--remove-tag`` subtracts. Tag-validation errors propagate to the
    caller as :class:`click.UsageError`.
    """
    out = list(current)
    if set_tags:
        out = _validate_tags_or_exit(set_tags)
    if add_tags:
        additions = _validate_tags_or_exit(add_tags)
        for t in additions:
            if t not in out:
                out.append(t)
    if remove_tags:
        to_remove = {t.strip().lower() for t in remove_tags}
        out = [t for t in out if t not in to_remove]
    return out


def _edit_resolve_at(
    match: Entry, at: str | None, *, yes: bool
) -> str:
    """Return the new ``created_at`` string after applying ``--at`` (if any).

    Prompts the user for confirmation when ``--at`` would change the
    timestamp; aborts (returns ``match.created_at`` unchanged) on "no"
    or when ``at`` is ``None``. On bad input, prints an error and
    exits with code 2.
    """
    if at is None:
        return match.created_at
    try:
        parsed = _dates._parse_timestamp(at, tz=_resolve_tz_or_exit())
    except click.BadParameter as exc:
        ui.print_error(str(exc))
        sys.exit(2)
    new_created_at = parsed.astimezone(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    if new_created_at != match.created_at and not yes:
        old_h = ui.format_dt(match.created_at)
        new_h = ui.format_dt(new_created_at)
        if not click.confirm(
            f"Change created_at of {match.short_id} from {old_h} to {new_h}?",
            default=False,
        ):
            ui.print_info("Aborted.")
            return match.created_at
    return new_created_at


def _edit_resolve_message(
    match: Entry, new_message: str | None, *, from_flag: bool
) -> str | None:
    """Pick the final message body for the edit, validating it.

    Returns the message to persist, or ``None`` to signal "the user
    asked for an empty body via the flag, which is rejected". The
    editor path is allowed to produce an empty body (the user might
    be saving a blank note on purpose); the flag path is not.
    """
    if from_flag and new_message is not None and not new_message.strip():
        ui.print_error("MESSAGE cannot be empty.")
        sys.exit(1)
    return new_message if new_message is not None else match.message


def _edit_in_editor(initial: str) -> str | None:
    """Open $VISUAL / $EDITOR on a temp file containing *initial* text.

    Returns the new file content if the editor exits 0 and the file is readable,
    or ``None`` if anything goes wrong.
    """
    import os as _os
    import subprocess
    import tempfile

    editor = _os.environ.get("VISUAL") or _os.environ.get("EDITOR")
    if not editor:
        # Fallback chain: nano → vi
        for candidate in ("nano", "vi"):
            if _resolve_in_path(candidate):
                editor = candidate
                break
    if not editor:
        ui.print_error(
            "No editor configured. Set $VISUAL or $EDITOR, or use --message / --tag flags."
        )
        sys.exit(1)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as tf:
        tf.write(initial)
        tmp_path = tf.name

    try:
        rc = subprocess.call([editor, tmp_path])
        if rc != 0:
            return None
        with open(tmp_path, encoding="utf-8") as fh:
            return fh.read().rstrip("\r\n")
    except FileNotFoundError:
        return None
    except OSError:
        return None
    finally:
        try:
            _os.unlink(tmp_path)
        except OSError:
            pass


def _resolve_in_path(name: str) -> str | None:
    import os as _os
    import shutil

    return shutil.which(name) or _os.path.isfile(name) or None


def _tag_delete_impl(
    all_entries: list[Entry],
    norm_tag: str,
    *,
    dry_run: bool,
    yes: bool,
    quiet: bool,
) -> None:
    """Implement ``devlog tag NAME --delete``.

    The mode flag lives on the parent ``tag`` command; this helper
    holds the body so the parent stays a thin dispatcher.
    """
    affected = [e for e in all_entries if norm_tag in e.tags]

    if not affected:
        if not quiet:
            ui.print_info(f'No entries with tag "{norm_tag}".')
        return

    if dry_run:
        if not quiet:
            console.print(
                ui.dry_run_line(
                    f"would remove tag \"{norm_tag}\" from {len(affected)} "
                    f"{ui.plural_noun(len(affected), 'entry')}."
                )
            )
        return

    if not yes and not quiet:
        prompt = (
            f"Remove tag \"{norm_tag}\" from {len(affected)} "
            f"{ui.plural_noun(len(affected), 'entry')}?"
        )
        if not click.confirm(prompt, default=False):
            ui.print_info("Aborted.")
            return

    now = storage.utc_now_iso()
    for entry in affected:
        entry.tags = [t for t in entry.tags if t != norm_tag]
        entry.updated_at = now

    try:
        storage.save_entries(all_entries)
    except StorageError as exc:
        _handle_storage_error(exc)
        return

    if not quiet:
        console.print(
            ui.success_line(
                f"Removed tag \"{norm_tag}\" from {len(affected)} "
                f"{ui.plural_noun(len(affected), 'entry')}."
            )
        )


def _tag_show_impl(
    all_entries: list[Entry],
    norm_tag: str,
    *,
    limit: int,
    show_all: bool,
    quiet: bool,
) -> None:
    """Implement ``devlog tag NAME`` (show mode)."""
    matching = [e for e in all_entries if norm_tag in e.tags]
    matching.sort(key=_BY_CREATED_AT, reverse=True)

    total = len(matching)
    shown = matching if show_all else matching[:limit]

    if quiet:
        _print_jsonl(shown)
        return

    if total == 0:
        ui.print_info(f'No entries with tag "{norm_tag}".')
        return

    # For display, remove the filtered tag from the tags list since
    # the user already knows every entry has this tag.
    shown_for_display = [
        Entry(
            id=e.id,
            message=e.message,
            tags=[t for t in e.tags if t != norm_tag],
            created_at=e.created_at,
            updated_at=e.updated_at,
        )
        for e in shown
    ]

    title = f'Tag · {norm_tag} · {total} {ui.plural_noun(total, "entry")}'
    table = ui.entries_table(shown_for_display, total, title=title)
    console.print(table)


def _doctor_probe_writable(path: Path) -> tuple[bool, bool]:
    """Test whether the data directory is writable.

    Returns ``(writable, ok)`` — the second flag is ``False`` when the
    directory is *not* writable, so the report can fail overall.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        probe = path.parent / ".devlog-doctor-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True, True
    except (OSError, PermissionError):
        return False, False


def _doctor_probe_size(path: Path) -> int:
    """Return the size of the entries file, or 0 on stat error."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _doctor_collect_issues(raw: object) -> tuple[list[dict], list[Entry], bool]:
    """Validate *raw* and turn the result into the JSON shape the report uses.

    Returns ``(issue_dicts, valid_entries, ok)``.
    """
    issues = storage.validate_entries(raw)
    issue_dicts = [
        {"kind": i.kind, "message": i.message, "entry_id": i.entry_id}
        for i in issues
    ]

    valid_entries: list[Entry] = []
    if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
        for item in raw["entries"]:
            entry = storage.coerce_entry(item) if isinstance(item, dict) else None
            if entry is not None and _iso.is_valid_iso_timestamp(entry.created_at):
                valid_entries.append(entry)

    return issue_dicts, valid_entries, not bool(issues)


def _doctor_days_since_last(entries: list[Entry]) -> int | None:
    """Return the number of days since the most recent entry, or ``None``.

    A non-negative int means "the latest entry is N days in the past".
    A negative int is a sentinel for "the latest entry is in the
    future" — the value is the raw day delta, but the renderer turns
    it into a friendlier "in N days" line. ``None`` means the entries
    are empty or their timestamps are unparseable.
    """
    if not entries:
        return None
    most_recent = max(entries, key=_BY_CREATED_AT)
    dt = _iso.try_parse_utc_iso(most_recent.created_at)
    if dt is None:
        return None
    return (datetime.datetime.now(tz=datetime.timezone.utc).date() - dt.date()).days


def _doctor_top_messages(entries: list[Entry]) -> list[tuple[str, int]]:
    """Return the 3 longest messages as ``(short_id, length)`` tuples."""
    by_length = sorted(entries, key=lambda e: len(e.message), reverse=True)[:3]
    return [(e.short_id + "…", len(e.message)) for e in by_length]


def _emit_doctor_report(report: dict, *, quiet: bool) -> None:
    """Print the doctor report as either a JSON line or a Rich panel."""
    if quiet:
        print(json.dumps(report, default=str))
    else:
        console.print(ui.doctor_report(report))
