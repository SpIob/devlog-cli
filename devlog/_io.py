"""Import/export utilities for devlog.

Extracted from cli.py to keep the main CLI module focused on Click wiring.
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from operator import attrgetter
from typing import TYPE_CHECKING

from devlog import _dates
from devlog import _tagops
from devlog import ui
from devlog.models import Entry

if TYPE_CHECKING:
    from devlog import storage


# Pre-built sort key — see cli._BY_CREATED_AT for the rationale.
_BY_CREATED_AT = attrgetter("created_at")


def _sniff_import_format(path: str) -> str:
    """Detect ``"json"`` or ``"markdown"`` for an ``import`` file.

    The detection order matches the user-facing contract:

    1. File extension (``.json`` / ``.md`` / ``.markdown``).
    2. First non-blank byte of the file (``{`` → json, ``#`` → md).
    3. Failure → print a user-facing error and ``sys.exit(2)``.

    Args:
        path: the user-supplied path to the file to import.

    Returns:
        ``"json"`` or ``"markdown"``.
    """
    lower = path.lower()
    if lower.endswith(".json"):
        return "json"
    if lower.endswith(".md") or lower.endswith(".markdown"):
        return "markdown"
    # Try to sniff format from the first non-blank character so users
    # can pipe from stdin or import extensionless files.
    try:
        with open(path, "r", encoding="utf-8") as fh:
            sniff = fh.read(64)
    except OSError:
        sniff = ""
    stripped = sniff.lstrip()
    if stripped.startswith("{"):
        return "json"
    if stripped.startswith("#"):
        return "markdown"
    ui.print_error(
        f'Cannot auto-detect format for "{path}". '
        "Use --format=json or --format=markdown."
    )
    sys.exit(2)


def _read_import_payload(path: str, fmt: str) -> tuple[list[Entry], int]:
    """Parse a JSON or Markdown import file into ``(candidates, unreadable)``.

    Args:
        path: file to read.
        fmt:  ``"json"`` or ``"markdown"``.

    Returns:
        A 2-tuple ``(candidates, unreadable_rows)``. The JSON branch
        counts rows it could not coerce; the markdown parser either
        succeeds or returns zero (it does not surface partial failures).
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            content = fh.read()
    except (PermissionError, OSError) as exc:
        ui.print_error(f"Cannot read {path}: {exc}")
        sys.exit(2)

    if fmt == "json":
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            ui.print_error(f"Invalid JSON in {path}: {exc}")
            sys.exit(2)
        if not isinstance(payload, dict):
            ui.print_error(
                f"Invalid JSON in {path}: expected an object with an "
                "'entries' key, got "
                f"{type(payload).__name__}."
            )
            sys.exit(2)
        raw_entries = payload.get("entries", [])
        candidates: list[Entry] = []
        unreadable = 0
        for item in raw_entries:
            if not isinstance(item, dict):
                unreadable += 1
                continue
            # Mint a uuid up front when the source row is missing one.
            # This both preserves stable ids when present AND lets
            # `Entry(**item)` succeed on rows that omit `id`, instead
            # of silently dropping them as "unreadable".
            if "id" not in item or not item["id"]:
                item = {**item, "id": str(uuid.uuid4())}
            try:
                e = Entry(**item)
            except (TypeError, ValueError):
                unreadable += 1
                continue
            candidates.append(e)
        return candidates, unreadable

    return _parse_markdown_export(content), 0


def _dedup_against_existing(
    candidates: list[Entry], existing: list[Entry]
) -> tuple[list[Entry], int]:
    """Return ``(to_add, skipped)`` after removing entries already present.

    Idempotency: an entry is considered "already present" if either
    the id *or* the ``(created_at, message)`` fingerprint matches an
    existing entry. This makes re-imports and backup → restore → import
    round-trips no-ops at the row level.
    """
    seen_ids = {e.id for e in existing}
    seen_fps = {e.fingerprint for e in existing}
    to_add: list[Entry] = []
    skipped = 0
    for cand in candidates:
        if cand.id in seen_ids or cand.fingerprint in seen_fps:
            skipped += 1
            continue
        # Preserve a stable id from the source when present. Only mint
        # a fresh uuid if the source row is missing one.
        if not cand.id:
            cand.id = str(uuid.uuid4())
        to_add.append(cand)
        seen_ids.add(cand.id)
        seen_fps.add(cand.fingerprint)
    return to_add, skipped


def _emit_import_summary(
    to_add: int, skipped: int, unreadable: int, *, dry_run: bool
) -> None:
    """Print the import dry-run / success headline to the user.

    Both branches (dry-run yellow, success green) build the same
    ``"<verb> N entries, skip M duplicates"`` sentence with an optional
    trailing ``"Ignored K unreadable rows."`` segment, so the helper
    keeps the wording consistent across the two outputs.
    """
    verb = "would import" if dry_run else "Imported"
    line_factory = ui.dry_run_line if dry_run else ui.success_line
    parts = [
        f"{verb} {to_add} {ui._plural_noun(to_add, 'entry')}, ",
        f"skip {skipped} duplicate{ui.plural_s(skipped)}.",
    ]
    if unreadable:
        parts.append(
            f" Ignored {unreadable} unreadable row{ui.plural_s(unreadable)}."
        )
    console = ui.console
    console.print(line_factory("".join(parts)))


def _parse_markdown_export(content: str) -> list[Entry]:
    """Parse the markdown format produced by `devlog export`.

    Each entry block looks like:

        ## 2025-05-11 10:22 UTC — a1b2c3d4

        Message body.

        **Tags:** backend, security

        <!-- created_at: 2025-05-11T10:22:33Z -->

        ---

    The hidden ``<!-- created_at: ... -->`` line carries the full
    ISO 8601 timestamp (with seconds). Older exports (pre-fix for
    the round-trip seconds-loss bug) won't have it; in that case the
    parser falls back to the minute-precision timestamp in the
    heading.
    """
    import re as _re

    # Heading pattern: "## YYYY-MM-DD HH:MM UTC — XXXXXXXX"
    heading_re = _re.compile(
        r"^##\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s+UTC)\s+—\s+([a-f0-9]+)\s*$",
        _re.MULTILINE,
    )
    tags_re = _re.compile(r"^\*\*Tags:\*\*\s*(.+?)\s*$", _re.MULTILINE)
    sep_re = _re.compile(r"^---\s*$", _re.MULTILINE)
    # Hidden metadata: full-precision ISO timestamp embedded in a
    # comment so it survives the Markdown round-trip without
    # cluttering the human-readable view.
    created_at_re = _re.compile(
        r"^<!--\s*created_at:\s*(\S+)\s*-->\s*$", _re.MULTILINE
    )

    entries: list[Entry] = []
    matches = list(heading_re.finditer(content))
    for i, m in enumerate(matches):
        date_str, short_id = m.group(1), m.group(2)
        # Body runs from end-of-heading to next separator or next heading
        body_start = m.end()
        next_boundary = len(content)
        for j in range(i + 1, len(matches)):
            next_boundary = matches[j].start()
            break
        # Also look for the closest separator after body_start
        sep = sep_re.search(content, body_start)
        if sep and sep.start() < next_boundary:
            next_boundary = sep.start()
        block = content[body_start:next_boundary].strip()

        # Extract tags line and message
        tags_match = tags_re.search(block)
        if tags_match:
            tags_raw = tags_match.group(1)
            if tags_raw.lower() in ("(none)", "none", ""):
                tags = []
            else:
                tags = [t.strip().lower() for t in tags_raw.split(",") if t.strip()]
            message = block[: tags_match.start()].strip()
        else:
            tags = []
            message = block

        # Prefer the full-precision timestamp from the hidden comment
        # line (the round-trip-safe form). Fall back to the heading's
        # minute precision for older exports that lack the comment.
        created_at_match = created_at_re.search(block)
        if created_at_match and _re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", created_at_match.group(1)
        ):
            created_at = created_at_match.group(1)
        else:
            # Heading only carries minute precision; seconds default to 00.
            # Strip "UTC" first, then convert the remaining space to "T".
            no_tz = date_str.replace("UTC", "").strip()
            created_at = no_tz.replace(" ", "T") + ":00Z"

        entries.append(
            Entry(
                id=f"{short_id}-imported",
                message=message,
                tags=tags,
                created_at=created_at,
            )
        )
    return entries


def _resolve_export_format(output: str | None, fmt: str) -> str:
    """Pick the final export format.

    ``auto`` infers from the output extension. Unknown extensions fall
    back to Markdown (preserves pre-1.5 behavior for `devlog export -o
    foo.txt`). Explicit ``--format`` wins over the extension so users
    can ``-o out.txt -f json``.
    """
    if fmt != "auto":
        return "json" if fmt.lower() == "json" else "markdown"
    if output is None:
        return "markdown"
    lower = output.lower()
    if lower.endswith(".json"):
        return "json"
    return "markdown"


def import_cmd(path: str, fmt: str, dry_run: bool, quiet: bool) -> None:
    """Import entries from a JSON or Markdown export file."""
    if fmt == "auto":
        fmt = _sniff_import_format(path)

    candidates, unreadable_rows = _read_import_payload(path, fmt)

    try:
        from devlog import storage
        existing = storage.load_entries()
    except ImportError:
        # Handle case where storage is not available
        existing = []
    except Exception as exc:
        # This mirrors the _handle_storage_error behavior
        ui.print_error(str(exc))
        sys.exit(2)

    to_add, skipped = _dedup_against_existing(candidates, existing)

    if dry_run:
        if not quiet:
            _emit_import_summary(len(to_add), skipped, unreadable_rows, dry_run=True)
        return

    if to_add:
        try:
            from devlog import storage
            existing.extend(to_add)
            storage.save_entries(existing)
        except Exception as exc:
            ui.print_error(str(exc))
            sys.exit(2)

    if not quiet:
        if to_add:
            _emit_import_summary(len(to_add), skipped, unreadable_rows, dry_run=False)
        else:
            # No new imports — surface the skip count so the user knows
            # it was a no-op, not a bug.
            if skipped or unreadable_rows:
                parts = []
                if skipped:
                    parts.append(
                        f"{skipped} duplicate{ui.plural_s(skipped)} skipped"
                    )
                if unreadable_rows:
                    parts.append(
                        f"{unreadable_rows} unreadable row{ui.plural_s(unreadable_rows)} ignored"
                    )
                ui.print_info(f"No new entries to import ({', '.join(parts)}).")
            else:
                ui.print_info("No entries to import.")


def export(
    output: str | None,
    fmt: str,
    tags: tuple[str, ...],
    since: str | None,
    until: str | None,
    quiet: bool,
) -> None:
    """Export entries to a Markdown or JSON file."""
    import datetime
    import dataclasses
    import json

    from devlog import storage
    from devlog import ui
    from devlog.models import Entry

    try:
        all_entries = storage.load_entries()
    except Exception as exc:
        ui.print_error(str(exc))
        sys.exit(2)

    filtered = _tagops._filter_by_tags(all_entries, tags)
    since_dt, until_dt = _dates._parse_since_until(since, until)
    filtered = _dates._filter_by_date(filtered, since_dt, until_dt)
    filtered.sort(key=_BY_CREATED_AT, reverse=True)

    if not filtered:
        ui.print_warning("Warning: No entries to export.")
        sys.exit(0)

    # Resolve format. When --output is given, its extension wins unless
    # the user explicitly asked for a format. When --output is absent,
    # pick based on --format (auto → markdown by default).
    fmt_resolved = _resolve_export_format(output, fmt)

    # Resolve output path. If the user did not pass -o, write into
    # <data-dir>/exports/ instead of polluting the current working dir.
    if output is None:
        ts = datetime.datetime.now(tz=datetime.timezone.utc).strftime(
            "%Y%m%d-%H%M%S"
        )
        ext = "json" if fmt_resolved == "json" else "md"
        export_dir = storage.get_data_dir() / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        output = str(export_dir / f"devlog-{ts}.{ext}")

    def _entry_md(entry: Entry) -> str:
        short_id = entry.short_id
        date_str = ui._format_dt(entry.created_at)
        tags_str = ", ".join(entry.tags) if entry.tags else ui.TAG_NONE
        # The hidden `<!-- created_at: … -->` line preserves the
        # full-precision timestamp so a Markdown round-trip (export →
        # import) doesn't drift on the seconds field. The line is
        # invisible in rendered Markdown and ignored by the parser if
        # it is missing on older files.
        return (
            f"## {date_str} — {short_id}\n\n"
            f"{entry.message}\n\n"
            f"**Tags:** {tags_str}\n\n"
            f"<!-- created_at: {entry.created_at} -->\n\n"
            "---\n"
        )

    try:
        if fmt_resolved == "json":
            with open(output, "w", encoding="utf-8") as fh:
                json.dump(
                    {"entries": [dataclasses.asdict(e) for e in filtered]},
                    fh,
                    indent=2,
                    ensure_ascii=False,
                )
            if not quiet:
                line = ui.success_line(
                    f"Exported {len(filtered)} {ui._plural_noun(len(filtered), 'entry')} to "
                )
                line.append(output, style="bold")
                ui.err_console.print(line)
            else:
                print(output)
        elif quiet:
            with open(output, "w", encoding="utf-8") as fh:
                for entry in filtered:
                    fh.write(_entry_md(entry))
            print(output)
        else:
            with ui.export_progress(len(filtered)) as progress:
                task = progress.add_task("Exporting…", total=len(filtered))
                with open(output, "w", encoding="utf-8") as fh:
                    for entry in filtered:
                        fh.write(_entry_md(entry))
                        progress.advance(task)
            line = ui.success_line(
                f"Exported {len(filtered)} {ui._plural_noun(len(filtered), 'entry')} to "
            )
            line.append(output, style="bold")
            ui.err_console.print(line)
    except (PermissionError, OSError):
        ui.print_error(f"Cannot write to {output}. Check the path and permissions.")
        sys.exit(2)