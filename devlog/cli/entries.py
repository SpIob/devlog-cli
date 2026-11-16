"""devlog CLI — The entry CRUD commands: ``add``, ``list``, ``search``,
``show``, ``edit`` and ``delete``.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import os
import sys
import uuid
from pathlib import Path

import click

from devlog import (
    _dates,
    _filter,
    storage,
    ui,
)
from devlog.cli._shared import (
    _edit_compute_tags,
    _edit_in_editor,
    _edit_resolve_at,
    _edit_resolve_message,
    _handle_storage_error,
    _load_entries_or_exit,
    _parse_search_query,
    _print_jsonl,
    _require_positive_int,
    _resolve_entry_by_id_or_exit,
    _validate_tags_or_exit,
)
from devlog.models import Entry
from devlog.storage import StorageError

# Console instances (ui re-exports) so command bodies read `console.print`
console = ui.console
err_console = ui.err_console


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------

@click.argument("message", required=False, default="")
@click.option(
    "--tag",
    "-t",
    multiple=True,
    help=(
        "Attach tags (repeatable). Tags are case-insensitive: "
        "uppercase letters are lowercased before storage. "
        "Allowed characters: a-z, 0-9, hyphen."
    ),
)
@click.option("--quiet", "-q", is_flag=True, help="Suppress output.")
@click.option(
    "--at",
    "at",
    default=None,
    help=(
        "Backdate the entry. Accepts an absolute timestamp "
        "(YYYY-MM-DD, YYYY-MM-DDTHH:MM, …Z) or a relative one "
        "(Nh / Nm / Nd / Nw ago). When DEVLOG_TZ is set, naive "
        "inputs are interpreted in that zone."
    ),
)
@click.option(
    "--from-commit",
    "from_commit",
    default=None,
    help=(
        "Create entry from a git commit. Prefills message, tags, and date "
        "from the commit. The message argument becomes a prefix/note."
    ),
)
def add(message: str, tag: tuple[str, ...], quiet: bool, at: str | None, from_commit: str | None) -> None:
    """Add a new journal entry.

    Pass `-` as the message to read from stdin (e.g., `git log -1 --format=%B | devlog add -`).
    Use `--from-commit HEAD` to create an entry from the latest git commit.
    """
    # Handle --from-commit
    if from_commit is not None:
        from devlog._gitops import create_entry_from_commit, get_git_repo_root
        
        # Find git repo
        cwd = Path.cwd()
        repo_root = get_git_repo_root(cwd)
        if repo_root is None:
            ui.print_error("Not in a git repository (or any parent directory).")
            sys.exit(1)
        
        entry = create_entry_from_commit(from_commit, repo_root)
        if entry is None:
            ui.print_error(f"Could not get info for commit '{from_commit}'.")
            sys.exit(1)
        
        # Use provided message as prefix/note when non-empty ("-" and
        # "" both mean "no prefix" — the commit message stands alone).
        # MESSAGE is optional: with no argument, the commit message
        # stands alone.
        if message not in ("-", ""):
            entry.message = f"{message}\n\n{entry.message}"
        
        # Merge tags
        norm_tags = _validate_tags_or_exit(tag)
        if norm_tags:
            entry.tags = list(dict.fromkeys(norm_tags + entry.tags))  # preserve order, dedupe
        
        # Override timestamp if --at is also provided
        if at is not None:
            try:
                ts_dt = _dates._parse_timestamp(at, tz=_dates._resolve_local_tz())
                ts = ts_dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                entry.created_at = ts
            except click.BadParameter as exc:
                ui.print_error(str(exc))
                sys.exit(2)
        
        # Ensure entry has an ID
        if not entry.id:
            entry.id = str(uuid.uuid4())
        
        try:
            storage.add_entry(entry)
        except StorageError as exc:
            _handle_storage_error(exc)
        
        if not quiet:
            console.print(ui.entry_panel(entry))
        return
    
    if not message or not message.strip():
        ui.print_error("MESSAGE cannot be empty.")
        sys.exit(1)

    # Read from stdin if message is "-"
    if message == "-":
        if sys.stdin.isatty():
            ui.print_error("stdin is a TTY; nothing to read. Provide a message or pipe input.")
            sys.exit(1)
        message = sys.stdin.read()
        if not message or not message.strip():
            ui.print_error("stdin is empty. Provide a message via pipe or redirect.")
            sys.exit(1)

    norm_tags = _validate_tags_or_exit(tag)

    if at is not None:
        try:
            ts_dt = _dates._parse_timestamp(at, tz=_dates._resolve_local_tz())
        except click.BadParameter as exc:
            ui.print_error(str(exc))
            sys.exit(2)
        # Always store UTC. The parse step may leave `ts_dt` in a local
        # zone; normalise here so the on-disk format is always Z-suffixed UTC.
        ts = ts_dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        ts = storage.utc_now_iso()
    entry = Entry(
        id=str(uuid.uuid4()),
        message=message,
        tags=norm_tags,
        created_at=ts,
    )

    try:
        storage.add_entry(entry)
    except StorageError as exc:
        _handle_storage_error(exc)

    if not quiet:
        console.print(ui.entry_panel(entry))


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

@click.option("--tag", "-t", "tags", multiple=True, help="Filter by tag (AND).")
@click.option(
    "--limit",
    "-n",
    type=int,
    default=20,
    show_default=True,
    help="Max entries to show.",
)
@click.option("--all", "show_all", is_flag=True, help="Show all entries (overrides --limit).")
@click.option("--since", default=None, help="Only show entries on/after this date (UTC).")
@click.option("--until", default=None, help="Only show entries on/before this date (UTC).")
@click.option("--quiet", "-q", is_flag=True, help="Output raw JSON lines.")
def list_entries(
    tags: tuple[str, ...],
    limit: int,
    show_all: bool,
    since: str | None,
    until: str | None,
    quiet: bool,
) -> None:
    """List journal entries, newest first."""
    if not show_all:
        _require_positive_int("--limit", limit)

    all_entries = _load_entries_or_exit()

    total_all = len(all_entries)
    filtered = _filter.filter_pipeline(all_entries, tags=tags, since=since, until=until)

    total_filtered = len(filtered)
    shown = filtered if show_all else filtered[:limit]

    if quiet:
        _print_jsonl(shown)
        return

    if total_filtered == 0:
        if tags or since or until:
            ui.print_info("No entries match your filters.")
        else:
            ui.print_info("No entries found.")
        return

    # Show filtered count, and total count when filters are applied
    has_filters = bool(tags or since or until)
    if has_filters:
        title = (
            f"Journal · {total_filtered} of {total_all} "
            f"{ui.plural_noun(total_all, 'entry')}"
        )
    else:
        title = f"Journal · {total_filtered} {ui.plural_noun(total_filtered, 'entry')}"
    table = ui.entries_table(shown, total_filtered, title=title)
    console.print(table)


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

@click.argument("query")
@click.option("--tag", "-t", "tags", multiple=True, help="Filter by tag (AND).")
@click.option(
    "--limit", "-n", type=int, default=20, show_default=True, help="Max entries to show."
)
@click.option("--since", default=None, help="Only show entries on/after this date (UTC).")
@click.option("--until", default=None, help="Only show entries on/before this date (UTC).")
@click.option("--regex", is_flag=True, help="Interpret QUERY as a regex pattern.")
@click.option("--quiet", "-q", is_flag=True, help="Output raw JSON lines.")
def search(
    query: str,
    tags: tuple[str, ...],
    limit: int,
    since: str | None,
    until: str | None,
    regex: bool,
    quiet: bool,
) -> None:
    """Search entry messages for QUERY.

    Default: case-insensitive substring match. Multiple space-separated
    terms require ALL terms (AND logic). Wrap a phrase in quotes to
    match it exactly. Use --regex for full regex patterns.
    """
    # `click.argument` does not reject empty strings at the parser
    # level, so without an explicit guard `devlog search ""` would
    # match every entry and emit the awkward subtitle `Query: ""`.
    # Treat empty / whitespace-only queries as a usage error instead.
    if not query or not query.strip():
        ui.print_error("QUERY cannot be empty.")
        sys.exit(1)

    _require_positive_int("--limit", limit)

    all_entries = _load_entries_or_exit()

    filtered = _filter.filter_pipeline(all_entries, tags=tags, since=since, until=until)

    if regex:
        import re
        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error as exc:
            ui.print_error(f"Invalid regex: {exc}")
            sys.exit(2)
        matched = [e for e in filtered if pattern.search(e.message)]
        # For regex, show the pattern in the subtitle
        display_query = f"regex: {query}"
    else:
        # Parse query: split on spaces, respecting quoted phrases
        terms = _parse_search_query(query)
        if not terms:
            ui.print_error("QUERY cannot be empty.")
            sys.exit(1)
        matched = [
            e
            for e in filtered
            if all(term.lower() in e.message.lower() for term in terms)
        ]
        display_query = query

    total = len(matched)
    shown = matched[:limit]

    if quiet:
        _print_jsonl(shown)
        return

    if total == 0:
        ui.print_info(f'No entries matched "{display_query}".')
        return

    match_word = "match" if total == 1 else "matches"
    title = f"Journal · {total} {match_word}"
    subtitle = f'Query: "{display_query}"'
    highlight = query if not regex else None
    table = ui.entries_table(
        shown, total, highlight_query=highlight or "", title=title, subtitle=subtitle
    )
    console.print(table)


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------

@click.argument("id")
@click.option("--quiet", "-q", is_flag=True, help="Output raw JSON line.")
def show(id: str, quiet: bool) -> None:
    """Show a single entry by ID (exact or unique short prefix)."""
    if not id:
        ui.print_error("ID is required.")
        sys.exit(1)

    all_entries = _load_entries_or_exit()

    match = _resolve_entry_by_id_or_exit(all_entries, id)

    if quiet:
        print(json.dumps(dataclasses.asdict(match), ensure_ascii=False))
        return

    console.print(ui.show_panel(match))


@click.argument("id")
@click.option("--message", "-m", "new_message", default=None, help="Replace the message.")
@click.option(
    "--tag", "-t", "set_tags", multiple=True, help="Replace tags with this set (AND replaces)."
)
@click.option(
    "--add-tag", "add_tags", multiple=True, help="Append tags (repeatable)."
)
@click.option(
    "--remove-tag", "remove_tags", multiple=True, help="Remove tags (repeatable)."
)
@click.option("--quiet", "-q", is_flag=True, help="Suppress output.")
@click.option(
    "--at",
    "at",
    default=None,
    help=(
        "Change created_at to this timestamp. Prompts for confirmation "
        "unless --yes is passed. Accepts absolute (YYYY-MM-DD, "
        "YYYY-MM-DDTHH:MM, …Z) and relative (Nh / Nm / Nd / Nw ago) inputs."
    ),
)
@click.option("--yes", "-y", "yes", is_flag=True, help="Skip the --at confirmation prompt.")
def edit(
    id: str,
    new_message: str | None,
    set_tags: tuple[str, ...],
    add_tags: tuple[str, ...],
    remove_tags: tuple[str, ...],
    quiet: bool,
    at: str | None,
    yes: bool,
) -> None:
    """Edit an entry's message and/or tags in place."""
    if not id:
        ui.print_error("ID is required.")
        sys.exit(1)

    all_entries = _load_entries_or_exit()

    match = _resolve_entry_by_id_or_exit(all_entries, id)

    has_flags = (
        new_message is not None
        or bool(set_tags)
        or bool(add_tags)
        or bool(remove_tags)
        or at is not None
    )

    if not has_flags:
        # No flags → spawn $VISUAL / $EDITOR / fallback chain. This
        # requires a TTY; in a non-interactive shell the editor will
        # blast terminal-control sequences to stdout and fail noisily.
        # Scripts that intentionally want the editor in a pipe can set
        # DEVLOG_ALLOW_EDITOR_IN_PIPE=1 to opt in.
        if not sys.stdin.isatty() and not os.environ.get(
            "DEVLOG_ALLOW_EDITOR_IN_PIPE"
        ):
            ui.print_error(
                "`devlog edit <id>` with no flags needs a TTY to open "
                "your editor. Pass --message, --tag, --add-tag, or "
                "--remove-tag to change the entry non-interactively, or "
                "set DEVLOG_ALLOW_EDITOR_IN_PIPE=1 to force the editor "
                "even when stdin is not a terminal."
            )
            sys.exit(1)
        edited_message = _edit_in_editor(match.message)
        if edited_message is None:
            ui.print_error("Editor exited abnormally; no changes saved.")
            sys.exit(2)
        new_message = edited_message
        message_from_flag = False
    else:
        message_from_flag = new_message is not None

    new_created_at = _edit_resolve_at(match, at, yes=yes)
    current_tags = _edit_compute_tags(list(match.tags), set_tags, add_tags, remove_tags)
    final_message = _edit_resolve_message(match, new_message, from_flag=message_from_flag)

    # No-op detection. A change to created_at counts as a real change,
    # so it forces the write through even when message + tags are equal.
    if (
        final_message == match.message
        and current_tags == match.tags
        and new_created_at == match.created_at
    ):
        if not quiet:
            ui.print_info("No changes.")
        return

    updated = Entry(
        id=match.id,
        message=final_message,
        tags=current_tags,
        created_at=new_created_at,
        updated_at=storage.utc_now_iso(),
    )

    try:
        ok = storage.update_entry(updated)
    except StorageError as exc:
        _handle_storage_error(exc)
        return

    if not ok:
        ui.print_error(f'Entry "{match.id}" disappeared during edit.')
        sys.exit(2)

    if not quiet:
        console.print(ui.edit_panel(updated))


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

@click.argument("id")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
@click.option("--quiet", "-q", is_flag=True, help="Suppress output panel.")
@click.option(
    "--ignore-missing",
    is_flag=True,
    help=(
        "Exit 0 (not 1) when the given ID does not exist. "
        "Useful in scripts where ``delete`` should be idempotent."
    ),
)
def delete(id: str, yes: bool, quiet: bool, ignore_missing: bool) -> None:
    """Delete an entry by ID."""
    if not id:
        ui.print_error("ID is required.")
        sys.exit(1)

    all_entries = _load_entries_or_exit()

    match = _resolve_entry_by_id_or_exit(all_entries, id, ignore_missing=ignore_missing)
    if match is None:
        # --ignore-missing and ID not present: succeed silently.
        if not quiet:
            ui.print_info(f'Entry "{id}" not present — nothing to delete.')
        return

    if not yes:
        short = match.short_id
        snippet = match.message[:40] + ("…" if len(match.message) > 40 else "")
        # Build a prompt that survives messages containing their own
        # double-quote characters. Wrapping the snippet in another pair
        # of quotes produces visually broken output like
        # `Delete entry 8da1ac20 ("He said "hi" to me")?`, so we use
        # a single-quote / dash prefix instead.
        prompt = f"Delete entry {short} \u2014 {snippet}?"
        if not click.confirm(prompt, default=False):
            ui.print_info("Aborted.")
            return

    try:
        ok = storage.delete_entry(match.id)
    except StorageError as exc:
        _handle_storage_error(exc)
        return

    if not ok:
        ui.print_error(f'Entry "{match.id}" disappeared during delete.')
        sys.exit(2)

    if not quiet:
        console.print(ui.delete_panel(match))


def register(main) -> None:
    """Attach the entries commands to *main*."""
    main.command()(add)
    main.command("list")(list_entries)
    main.command()(search)
    main.command()(show)
    main.command()(edit)
    main.command()(delete)
