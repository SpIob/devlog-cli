"""Command-line interface for devlog.

This module is intentionally thin: it parses arguments via Click,
delegates persistence to ``storage``, and delegates *all* rendering to
``ui``. Keeping rendering in one place is what guarantees a consistent
look-and-feel across commands.
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
    _completions,
    _dates,
    _filter,
    _interactive,
    _io,
    _iso,
    _tagops,
    _theme_cli,
    storage,
    ui,
)
from devlog.models import BY_CREATED_AT, Entry
from devlog.storage import StorageError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Single source of truth for the command list. Drives:
#   * `devlog root_banner` (printed when no subcommand is given)
#   * the bash / zsh / fish completion scripts (printed by
#     `devlog completions <shell>`)
# Each tuple is (name, one-line description) in display order.
# Subcommands of grouped commands (e.g. `theme list`, `theme show`)
# are listed as the parent name only; completion for the children
# is handled in the per-shell snippets.
COMMANDS: list[tuple[str, str]] = [
    ("add", "Add a new journal entry"),
    ("show", "Show a single entry by ID"),
    ("edit", "Edit an entry's message and/or tags"),
    ("delete", "Delete an entry by ID"),
    ("list", "List entries, newest first"),
    ("search", "Search entry messages"),
    ("today", "Show today's entries"),
    ("yesterday", "Show yesterday's entries"),
    ("week", "Show the last 7 days"),
    ("on", "Show entries for a specific date"),
    ("tail", "Show the N most recent entries"),
    ("tags", "List tags with usage counts"),
    ("tag", "Show or delete entries with a tag"),
    ("merge-tag", "Merge two tags across all entries"),
    ("rename-tag", "Rename a tag across all entries"),
    ("theme", "View or change the active color theme"),
    ("stats", "Summarize the journal"),
    ("digest", "Summarize recent journal activity (AI optional)"),
    ("ask", "Ask your journal questions via local embeddings (AI)"),
    ("mcp", "Expose the journal to AI agents (MCP server)"),
    ("calendar", "Show a year-grid heatmap of activity"),
    ("import", "Import entries from a JSON or Markdown file"),
    ("export", "Export entries to a Markdown or JSON file"),
    ("completions", "Print a shell completion script"),
    ("repair", "Inspect and repair the on-disk journal store"),
    ("backup", "Write a timestamped copy of the journal"),
    ("restore", "Restore the journal from a backup file"),
    ("doctor", "Check the journal store for corruption"),
]

console = ui.console
err_console = ui.err_console


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


# Module-level sort key — re-exported from :mod:`devlog.models` so it
# has a single source of truth across ``cli.py`` and ``_io.py``.
_BY_CREATED_AT = BY_CREATED_AT


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
# CLI entry point
# ---------------------------------------------------------------------------


@click.group(invoke_without_command=True)
@click.option("--version", is_flag=True, help="Show version and exit.")
@click.option(
    "--interactive", "-i", is_flag=True,
    help="Launch the interactive REPL instead of printing the help banner.",
)
@click.pass_context
def main(ctx: click.Context, version: bool, interactive: bool) -> None:
    """devlog — a terminal-based developer journal."""
    if version:
        ui.version_banner()
        ctx.exit(0)
        return
    if ctx.invoked_subcommand is None:
        if interactive or os.environ.get("DEVLOG_INTERACTIVE") == "1":
            # The "FORCE" env var exists solely for testing the REPL with
            # CliRunner (which provides a non-TTY stdin). Production users
            # should never need it.
            if (
                not sys.stdin.isatty()
                and os.environ.get("DEVLOG_INTERACTIVE_FORCE") != "1"
            ):
                ui.print_error("Interactive mode requires a TTY.")
                sys.exit(1)
            _interactive._interactive_repl(main)
            ctx.exit(0)
            return
        ui.root_banner()
        ctx.exit(0)


# The theme group lives in _theme_cli.py; attach it at import time so
# command lookup and help order stay identical to the pre-split CLI.
_theme_cli.register(main)

# The git group lives in _git_cli.py; attach it at import time
try:
    from devlog import _git_cli
    _git_cli.register(main)
except ImportError:
    pass  # _git_cli not available yet

# The AI commands (digest/ask) live in _ai_cli.py; attach at import time.
try:
    from devlog import _ai_cli
    _ai_cli.register(main)
except ImportError:
    pass  # _ai_cli not available yet

# The MCP server command lives in _mcp_cli.py; attach at import time.
try:
    from devlog import _mcp_cli
    _mcp_cli.register(main)
except ImportError:
    pass  # _mcp_cli not available yet


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


@main.command()
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


@main.command("list")
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


@main.command()
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


@main.command()
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
        parsed = _dates._parse_timestamp(at, tz=_dates._resolve_local_tz())
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


@main.command()
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


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@main.command()
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


# ---------------------------------------------------------------------------
# tags
# ---------------------------------------------------------------------------


@main.command()
@click.option(
    "--sort",
    type=click.Choice(["count", "name", "recent"], case_sensitive=False),
    default="count",
    show_default=True,
    help="Sort order for the tag list.",
)
@click.option(
    "--limit", "-n", type=int, default=50, show_default=True, help="Max tags to show."
)
@click.option("--all", "show_all", is_flag=True, help="Show all tags (overrides --limit).")
@click.option("--quiet", "-q", is_flag=True, help="Output raw JSON lines.")
def tags(sort: str, limit: int, show_all: bool, quiet: bool) -> None:
    """List all tags with usage count and last-used date."""
    if not show_all:
        _require_positive_int("--limit", limit)

    all_entries = _load_entries_or_exit()

    aggregates: dict[str, tuple[int, str]] = {}
    for entry in all_entries:
        last_seen = entry.updated_at or entry.created_at
        for tag in entry.tags:
            count, prev_last = aggregates.get(tag, (0, ""))
            aggregates[tag] = (count + 1, max(prev_last, last_seen))

    if not aggregates:
        if quiet:
            return
        ui.print_info("No tags found.")
        return

    rows = [(tag, count, last) for tag, (count, last) in aggregates.items()]

    if sort == "count":
        rows.sort(key=lambda r: (-r[1], r[0]))
    elif sort == "name":
        rows.sort(key=lambda r: r[0])
    else:  # recent
        rows.sort(key=lambda r: (-(_iso.safe_epoch(r[2]) if r[2] else 0), r[0]))

    total_tags = len(rows)
    shown = rows if show_all else rows[:limit]

    if quiet:
        for tag, count, last in shown:
            print(
                json.dumps(
                    {"tag": tag, "count": count, "last_used": last},
                    ensure_ascii=False,
                )
            )
        return

    table = ui.tags_table(shown, total_tags, len(all_entries))
    console.print(table)


# ---------------------------------------------------------------------------
# tag (per-tag page + delete)
# ---------------------------------------------------------------------------


@main.command()
@click.argument("name")
@click.option(
    "--delete",
    "delete_tag",
    is_flag=True,
    help="Remove the tag from every entry that carries it (no entries are deleted).",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="With --delete: skip the confirmation prompt.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="With --delete: show how many entries would be affected without writing.",
)
@click.option(
    "--limit",
    "-n",
    type=int,
    default=20,
    show_default=True,
    help="Show mode: max entries to list.",
)
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    help="Show mode: override --limit and list every entry.",
)
@click.option("--quiet", "-q", is_flag=True, help="Suppress output / switch to JSON.")
def tag(
    name: str,
    delete_tag: bool,
    yes: bool,
    dry_run: bool,
    limit: int,
    show_all: bool,
    quiet: bool,
) -> None:
    """Show entries with a tag, or remove a tag from every entry.

    Default mode lists every entry carrying NAME (newest first). With
    ``--delete``, the tag is removed from every entry that has it
    instead. Use ``--dry-run`` with ``--delete`` to preview the change,
    or ``--yes`` to skip the confirmation prompt.
    """
    if not name or not name.strip():
        ui.print_error("NAME cannot be empty.")
        sys.exit(1)

    # Validate the tag the same way `add` and `rename-tag` do, so users
    # can't smuggle in characters the storage layer would reject.
    norm_tag = _validate_tags_or_exit((name,))[0]

    if not show_all:
        _require_positive_int("--limit", limit)

    all_entries = _load_entries_or_exit()

    if delete_tag:
        _tag_delete_impl(
            all_entries,
            norm_tag,
            dry_run=dry_run,
            yes=yes,
            quiet=quiet,
        )
        return

    _tag_show_impl(
        all_entries, norm_tag, limit=limit, show_all=show_all, quiet=quiet
    )


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


# ---------------------------------------------------------------------------
# today
# ---------------------------------------------------------------------------


@main.command()
@click.option(
    "--limit", "-n", type=int, default=50, show_default=True, help="Max entries to show."
)
@click.option("--quiet", "-q", is_flag=True, help="Output raw JSON lines.")
def today(limit: int, quiet: bool) -> None:
    """Show entries created today, newest first.

    The "today" bucket is the local date in the ``DEVLOG_TZ`` zone when
    set, otherwise UTC. This matches how the rest of the CLI handles
    the env var.
    """
    _require_positive_int("--limit", limit)

    all_entries = _load_entries_or_exit()

    tz = _dates._resolve_local_tz()
    local_today = _dates.today_local_date(tz)
    today_entries = _dates._filter_by_local_window(all_entries, end_date=local_today, days=0, tz=tz)
    subtitle = local_today.strftime("%Y-%m-%d")

    today_entries.sort(key=_BY_CREATED_AT, reverse=True)

    total = len(today_entries)
    shown = today_entries[:limit]

    if quiet:
        _print_jsonl(shown)
        return

    if total == 0:
        ui.print_info("No entries yet today.")
        return

    title = f"Today · {subtitle} · {total} {ui.plural_noun(total, 'entry')}"
    table = ui.entries_table(shown, total, title=title)
    console.print(table)


# ---------------------------------------------------------------------------
# yesterday
# ---------------------------------------------------------------------------


@main.command()
@click.option(
    "--limit", "-n", type=int, default=50, show_default=True, help="Max entries to show."
)
@click.option("--quiet", "-q", is_flag=True, help="Output raw JSON lines.")
def yesterday(limit: int, quiet: bool) -> None:
    """Show entries created yesterday, newest first.

    Like ``today``, the bucket is computed in the ``DEVLOG_TZ`` zone
    when set, otherwise UTC.
    """
    _require_positive_int("--limit", limit)

    all_entries = _load_entries_or_exit()

    tz = _dates._resolve_local_tz()
    local_today = _dates.today_local_date(tz)
    local_yesterday = local_today - datetime.timedelta(days=1)
    yesterday_entries = _dates._filter_by_local_window(
        all_entries, end_date=local_yesterday, days=0, tz=tz
    )
    subtitle = local_yesterday.strftime("%Y-%m-%d")

    yesterday_entries.sort(key=_BY_CREATED_AT, reverse=True)
    total = len(yesterday_entries)
    shown = yesterday_entries[:limit]

    if quiet:
        _print_jsonl(shown)
        return

    if total == 0:
        ui.print_info("No entries yet yesterday.")
        return

    title = f"Yesterday · {subtitle} · {total} {ui.plural_noun(total, 'entry')}"
    table = ui.entries_table(shown, total, title=title)
    console.print(table)


# ---------------------------------------------------------------------------
# week
# ---------------------------------------------------------------------------


@main.command()
@click.option(
    "--limit", "-n", type=int, default=100, show_default=True, help="Max entries to show."
)
@click.option(
    "--quiet", "-q", is_flag=True, help="Output raw JSON lines."
)
@click.option(
    "--day",
    "anchor",
    default=None,
    help=(
        "Anchor day (YYYY-MM-DD). The week ends on this day (inclusive) and "
        "spans the 6 days before it. Defaults to today in the local zone "
        "(or UTC)."
    ),
)
def week(limit: int, quiet: bool, anchor: str | None) -> None:
    """Show entries from the last 7 days, newest first."""
    _require_positive_int("--limit", limit)

    all_entries = _load_entries_or_exit()

    tz = _dates._resolve_local_tz()
    if anchor:
        # Parse the anchor in the local zone when possible, then convert
        # to UTC. Falls back to UTC if no tz is set, mirroring
        # `_parse_date_bound` behaviour.
        try:
            anchor_dt = _dates._parse_date_bound(anchor, tz=tz)
        except click.BadParameter as exc:
            ui.print_error(str(exc))
            sys.exit(2)
        end_date = anchor_dt.astimezone(tz).date() if tz is not None else anchor_dt.date()
    else:
        end_date = _dates.today_local_date(tz)

    week_entries = _dates._filter_by_local_window(all_entries, end_date=end_date, days=6, tz=tz)
    start_date = end_date - datetime.timedelta(days=6)
    week_entries.sort(key=_BY_CREATED_AT, reverse=True)

    total = len(week_entries)
    shown = week_entries[:limit]

    if quiet:
        _print_jsonl(shown)
        return

    if total == 0:
        ui.print_info(
            f"No entries in the last 7 days ({start_date} to {end_date})."
        )
        return

    title = (
        f"Week · {start_date} → {end_date} · {total} "
        f"{ui.plural_noun(total, 'entry')}"
    )
    table = ui.entries_table(shown, total, title=title)
    console.print(table)


# ---------------------------------------------------------------------------
# on
# ---------------------------------------------------------------------------


@main.command()
@click.argument("date", required=True)
@click.option(
    "--limit", "-n", type=int, default=50, show_default=True, help="Max entries to show."
)
@click.option("--quiet", "-q", is_flag=True, help="Output raw JSON lines.")
def on(date: str, limit: int, quiet: bool) -> None:
    """Show entries for a specific date.

    DATE can be:
    - "today" / "yesterday" (local zone when DEVLOG_TZ is set)
    - YYYY-MM-DD (absolute date)
    - Nd (relative days ago, e.g. "3d")
    
    The bucket is computed in the DEVLOG_TZ zone when set, otherwise UTC.
    """
    _require_positive_int("--limit", limit)

    all_entries = _load_entries_or_exit()

    tz = _dates._resolve_local_tz()
    target_date = _dates._parse_on_date(date, tz=tz)
    
    if target_date is None:
        ui.print_error(f"Invalid date: {date}")
        sys.exit(2)

    on_entries = _dates._filter_by_local_window(all_entries, end_date=target_date, days=0, tz=tz)
    subtitle = target_date.strftime("%Y-%m-%d")

    on_entries.sort(key=_BY_CREATED_AT, reverse=True)
    total = len(on_entries)
    shown = on_entries[:limit]

    if quiet:
        _print_jsonl(shown)
        return

    if total == 0:
        ui.print_info(f"No entries on {subtitle}.")
        return

    title = f"On · {subtitle} · {total} {ui.plural_noun(total, 'entry')}"
    table = ui.entries_table(shown, total, title=title)
    console.print(table)


# ---------------------------------------------------------------------------
# calendar
# ---------------------------------------------------------------------------


@main.command()
@click.option(
    "--year",
    "year",
    type=int,
    default=None,
    help="Year to render. Defaults to the current local year (or UTC).",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Output a {YYYY-MM-DD: count} JSON map for the year.",
)
def calendar(year: int | None, quiet: bool) -> None:
    """Show a year-grid heatmap of journal activity."""
    tz = _dates._resolve_local_tz()
    if year is None:
        year = _dates.today_local_date(tz).year

    all_entries = _load_entries_or_exit()

    per_day: dict[str, int] = {}
    for entry in all_entries:
        local_d = storage.local_date_for(entry.created_at, tz)
        if local_d.year == year:
            key = local_d.strftime("%Y-%m-%d")
            per_day[key] = per_day.get(key, 0) + 1

    if quiet:
        print(json.dumps(per_day, ensure_ascii=False, sort_keys=True))
        return

    if not per_day:
        ui.print_info(f"No entries in {year}.")
        return

    panel = ui.calendar_panel(per_day, year=year, tz=tz)
    console.print(panel)


# ---------------------------------------------------------------------------
# tail
# ---------------------------------------------------------------------------


@main.command()
@click.argument("n", required=False, default=5, type=int)
@click.option("--tag", "-t", "tags", multiple=True, help="Filter by tag (AND).")
@click.option("--quiet", "-q", is_flag=True, help="Output raw JSON lines.")
def tail(n: int, tags: tuple[str, ...], quiet: bool) -> None:
    """Show the N most recent entries (default: 5)."""
    _require_positive_int("N", n)

    all_entries = _load_entries_or_exit()

    filtered = _filter.filter_pipeline(all_entries, tags=tags)
    shown = filtered[:n]
    total = len(filtered)

    if quiet:
        _print_jsonl(shown)
        return

    if total == 0:
        ui.print_info("No entries found.")
        return

    title = f"Tail · last {len(shown)} of {total}"
    table = ui.entries_table(shown, total, title=title)
    console.print(table)


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


@main.command()
@click.option("--since", default=None, help="Only include entries on/after this date (UTC).")
@click.option("--until", default=None, help="Only include entries on/before this date (UTC).")
@click.option("--quiet", "-q", is_flag=True, help="Output a single JSON summary.")
def stats(since: str | None, until: str | None, quiet: bool) -> None:
    """Show a summary of the journal: total entries, date range, top tags, and a 30-day sparkline."""
    all_entries = _load_entries_or_exit()

    all_entries = _filter.filter_pipeline(all_entries, since=since, until=until, sort=False)
    # Also drop entries whose created_at cannot be parsed at all, so
    # downstream formatting (which assumes a valid ISO timestamp) never
    # crashes. Other commands go through this filter implicitly via
    # --since/--until; `stats` without date bounds does not, so we apply
    # it unconditionally here.
    #
    # Performance note: previously this filtered first and then the
    # per-day loop parsed ``created_at`` a second time inside
    # ``storage.local_date_for``. We now parse once here and reuse the
    # resulting date — saves one ``parse_utc_iso`` call per entry
    # (10k+ syscalls on large journals).
    tz = _dates._resolve_local_tz()
    valid_entries: list[tuple[Entry, datetime.date]] = []
    for entry in all_entries:
        dt = _iso.try_parse_utc_iso(entry.created_at)
        if dt is None:
            continue
        local_d = dt.astimezone(tz).date() if tz is not None else dt.date()
        valid_entries.append((entry, local_d))
    if not valid_entries:
        ui.print_info("No entries to summarize.")
        return

    # Total & date range
    total = len(valid_entries)
    # Sort by the parsed dt to find first/last; this avoids a second
    # lexical sort of the ISO strings (which would still be correct
    # but slower than sorting datetimes).
    sorted_by_date = sorted(valid_entries, key=lambda ed: ed[1])
    first_iso = sorted_by_date[0][0].created_at
    last_iso = sorted_by_date[-1][0].created_at

    # Per-day counts for the last 30 days. We already parsed each
    # timestamp above, so just reuse the date — no second parse.
    per_day: dict[str, int] = {}
    for _entry, local_d in valid_entries:
        key = local_d.strftime("%Y-%m-%d")
        per_day[key] = per_day.get(key, 0) + 1

    last_30_days: list[tuple[str, int]] = []
    ref_date = _dates.today_local_date(tz)
    for i in range(29, -1, -1):
        d = ref_date - datetime.timedelta(days=i)
        iso = d.strftime("%Y-%m-%d")
        last_30_days.append((iso, per_day.get(iso, 0)))

    # Top tags
    from collections import Counter

    tag_counter: Counter = Counter()
    for entry, _ in valid_entries:
        for tag in entry.tags:
            tag_counter[tag] += 1
    top_tags = tag_counter.most_common(5)

    # Stats v2: streaks, tag co-occurrence, days-since-last. All are
    # computed from the already-parsed local dates, so they add no
    # extra timestamp parsing on large journals.
    local_dates = [local_d for _e, local_d in valid_entries]
    streaks = storage.compute_streaks(local_dates)
    top_tag_pairs = storage.compute_tag_cooccurrence([e for e, _ in valid_entries])
    days_since = storage.compute_days_since_last(local_dates)

    if quiet:
        print(
            json.dumps(
                {
                    "total": total,
                    "first": first_iso,
                    "last": last_iso,
                    "top_tags": [{"tag": t, "count": c} for t, c in top_tags],
                    "last_30_days": [
                        {"date": d, "count": c} for d, c in last_30_days
                    ],
                    "streaks": {"current": streaks[0], "longest": streaks[1]},
                    "top_tag_pairs": [
                        {"tags": list(pair), "count": c}
                        for pair, c in top_tag_pairs
                    ],
                    "days_since_last": days_since,
                },
                ensure_ascii=False,
            )
        )
        return

    # Render
    panel = ui.stats_panel(
        total=total,
        first_iso=first_iso,
        last_iso=last_iso,
        top_tags=top_tags,
        last_30_days=last_30_days,
        streaks=streaks,
        top_tag_pairs=top_tag_pairs,
        days_since=days_since,
        tz=tz,
        tz_label=str(tz) if tz is not None else "UTC",
    )
    console.print(panel)


# ---------------------------------------------------------------------------
# rename-tag
# ---------------------------------------------------------------------------


@main.command("rename-tag")
@click.argument("old")
@click.argument("new")
@click.option("--dry-run", is_flag=True, help="Show what would change; do not write.")
@click.option("--quiet", "-q", is_flag=True, help="Suppress the preview output.")
def rename_tag(old: str, new: str, dry_run: bool, quiet: bool) -> None:
    """Rename a tag across all entries (OLD → NEW)."""
    try:
        new_tag = _tagops._validate_new_tag(new)
    except click.UsageError as exc:
        ui.print_error(str(exc))
        sys.exit(1)

    old_normalized = old.strip().lower()
    if not old_normalized:
        ui.print_error("OLD tag cannot be empty.")
        sys.exit(1)

    if old_normalized == new_tag:
        ui.print_info(f'OLD and NEW are the same ("{new_tag}"). No changes made.')
        return

    all_entries = _load_entries_or_exit()

    affected = [e for e in all_entries if old_normalized in e.tags]

    if not affected:
        if not quiet:
            ui.print_info(f'No entries with tag "{old_normalized}".')
        return

    if dry_run:
        if not quiet:
            line = ui.dry_run_line(
                f"would update {len(affected)} {ui.plural_noun(len(affected), 'entry')}: "
            )
            line.append(f"{old_normalized} → {new_tag}", style="bold")
            console.print(line)
        return

    for entry in affected:
        _tagops._rewrite_tag_in_entry(
            entry, old_normalized, new_tag, now=storage.utc_now_iso()
        )

    try:
        storage.save_entries(all_entries)
    except StorageError as exc:
        _handle_storage_error(exc)
        return

    if not quiet:
        console.print(
            ui.success_line(
                f"Renamed {old_normalized} → {new_tag} in {len(affected)} {ui.plural_noun(len(affected), 'entry')}."
            )
        )


# ---------------------------------------------------------------------------
# merge-tag
# ---------------------------------------------------------------------------


@main.command("merge-tag")
@click.argument("old")
@click.argument("new")
@click.option("--dry-run", is_flag=True, help="Show what would change; do not write.")
@click.option("--quiet", "-q", is_flag=True, help="Suppress the success line.")
def merge_tag(old: str, new: str, dry_run: bool, quiet: bool) -> None:
    """Merge OLD into NEW across every entry.

    For each entry that has OLD, NEW is added (deduplicated) and OLD is
    removed. Entries that already carry NEW are still de-tagged with
    OLD but not double-tagged with NEW. Use ``--dry-run`` to preview.
    """
    try:
        new_tag = _tagops._validate_new_tag(new)
    except click.UsageError as exc:
        ui.print_error(str(exc))
        sys.exit(1)

    old_normalized = old.strip().lower()
    if not old_normalized:
        ui.print_error("OLD tag cannot be empty.")
        sys.exit(1)

    if old_normalized == new_tag:
        ui.print_info(f'OLD and NEW are the same ("{new_tag}"). No changes made.')
        return

    all_entries = _load_entries_or_exit()

    # Categorise affected entries. "Touched" = entry had OLD; "skipped"
    # = a subset that already had NEW (we still strip OLD from these,
    # but we don't double-add NEW).
    touched: list[Entry] = []
    already_had_new = 0
    for entry in all_entries:
        if old_normalized in entry.tags:
            touched.append(entry)
            if new_tag in entry.tags:
                already_had_new += 1

    if not touched:
        if not quiet:
            ui.print_info(f'No entries with tag "{old_normalized}".')
        return

    if dry_run:
        added = len(touched) - already_had_new
        if not quiet:
            line = ui.dry_run_line(
                f"would add {new_tag} to {added} {ui.plural_noun(added, 'entry')}, "
                f"remove {old_normalized} from {len(touched)} "
                f"{ui.plural_noun(len(touched), 'entry')}. "
            )
            if already_had_new:
                line.append(
                    f"({already_had_new} already had {new_tag}; no duplicate added.)",
                    style=ui._s("warning_text"),
                )
            console.print(line)
        return

    for entry in touched:
        _tagops._rewrite_tag_in_entry(
            entry, old_normalized, new_tag, now=storage.utc_now_iso()
        )

    try:
        storage.save_entries(all_entries)
    except StorageError as exc:
        _handle_storage_error(exc)
        return

    if not quiet:
        added = len(touched) - already_had_new
        line = ui.success_line(
            f"Merged \"{old_normalized}\" into \"{new_tag}\" across "
            f"{len(touched)} {ui.plural_noun(len(touched), 'entry')}"
        )
        if already_had_new:
            line.append(
                f" ({already_had_new} already had {new_tag}; skipped)",
                style=ui._s("success_text"),
            )
        line.append(".", style=ui._s("success_text"))
        console.print(line)


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------


@main.command()
@click.argument("path", type=click.Path(exists=True, dir_okay=False, readable=True))
@click.option(
    "--format",
    "-f",
    "fmt",
    type=click.Choice(
        ["auto", "json", "markdown", "jrnl", "dayone"], case_sensitive=False
    ),
    default="auto",
    show_default=True,
    help="Input format. 'auto' detects by file extension/content. 'jrnl' and 'dayone' import from those journal formats.",
)
@click.option("--dry-run", is_flag=True, help="Show what would be imported; do not write.")
@click.option("--quiet", "-q", is_flag=True, help="Suppress preview output.")
def import_cmd(path: str, fmt: str, dry_run: bool, quiet: bool) -> None:
    """Import entries from a JSON, Markdown, jrnl, or Day One export file."""
    from devlog import _importers, _io, storage, ui

    if fmt == "auto":
        fmt = _importers.detect_format(path)

    if fmt in ("jrnl", "dayone"):
        try:
            if fmt == "jrnl":
                candidates = _importers.import_jrnl_json(path)
            else:
                candidates = _importers.import_dayone_json(path)
        except (ValueError, OSError) as exc:
            ui.print_error(str(exc))
            sys.exit(2)
        unreadable_rows = 0
    elif fmt == "json":
        # Use JSON importer directly (not through _io.import_cmd to preserve exit codes)
        candidates, unreadable_rows = _io._read_import_payload(path, "json")
    else:
        # Use existing JSON/Markdown importer via _io
        _io.import_cmd(path, fmt, dry_run, quiet)
        return

    try:
        existing = storage.load_entries()
    except Exception as exc:
        ui.print_error(str(exc))
        sys.exit(2)

    to_add, skipped = _io._dedup_against_existing(candidates, existing)

    if dry_run:
        if not quiet:
            _io._emit_import_summary(len(to_add), skipped, unreadable_rows, dry_run=True)
        return

    if to_add:
        try:
            existing.extend(to_add)
            storage.save_entries(existing)
        except Exception as exc:
            ui.print_error(str(exc))
            sys.exit(2)

    if not quiet:
        if to_add:
            _io._emit_import_summary(len(to_add), skipped, unreadable_rows, dry_run=False)
        else:
            if skipped or unreadable_rows:
                parts = []
                if skipped:
                    parts.append(f"{skipped} duplicate{ui.plural_s(skipped)} skipped")
                if unreadable_rows:
                    parts.append(
                        f"{unreadable_rows} unreadable row{ui.plural_s(unreadable_rows)} ignored"
                    )
                ui.print_info(f"No new entries to import ({', '.join(parts)}).")
            else:
                ui.print_info("No entries to import.")


# ---------------------------------------------------------------------------
# repair / backup / restore / doctor
# ---------------------------------------------------------------------------


@main.command()
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show the repair plan without writing any changes.",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Skip the confirmation prompt (assumes yes).",
)
@click.option(
    "--backup/--no-backup",
    default=True,
    help="Write a timestamped backup before any repair (default: yes).",
)
@click.option("--quiet", "-q", is_flag=True, help="Suppress the summary panel.")
def repair(dry_run: bool, yes: bool, backup: bool, quiet: bool) -> None:
    """Inspect and repair the on-disk journal store.

    Validates every entry against the schema, then either reports the
    issues (``--dry-run``) or rewrites the store with malformed rows
    removed. The original store is preserved in ``backups/`` whenever a
    write happens unless ``--no-backup`` is set.
    """
    if storage._is_sqlite_backend():
        # SQLite backend: the store is a live database, not a flat file,
        # so the corrupt-JSON recovery path does not apply. Rows were
        # already validated at insert time; running the same validator
        # over the payload still catches hand-edited (post-migration)
        # drift, but there is no "repair" beyond dropping bad rows.
        from devlog.sqlite_storage import get_sqlite_path, read_raw_entries_sqlite

        if not get_sqlite_path().exists():
            if not quiet:
                ui.print_info("No journal yet — nothing to repair.")
            return

        raw, recovered = read_raw_entries_sqlite()
        kept, issues = storage.build_repair_plan(raw)
        raw_count = len(raw.get("entries", []))
        dropped = max(0, raw_count - len(kept))
        tags_stripped = sum(1 for i in issues if i.kind == "bad_tag")

        if not issues:
            if not quiet:
                ui.print_info("No issues found. Nothing to repair.")
            return

        backup_path: str | None = None
        if not dry_run and backup:
            try:
                storage.ensure_storage_dir()
                backups_dir = storage.get_backups_dir()
                backups_dir.mkdir(parents=True, exist_ok=True)
                backup_path = str(backups_dir / storage.default_backup_filename())
                with open(backup_path, "w", encoding="utf-8") as fh:
                    json.dump(raw, fh, indent=2, ensure_ascii=False)
            except (OSError, PermissionError) as exc:
                ui.print_error(f"Could not write backup: {exc}")
                sys.exit(2)

        if not dry_run:
            try:
                storage.save_entries(kept)
            except StorageError as exc:
                _handle_storage_error(exc)
                return

        if not quiet:
            console.print(
                ui.repair_summary(
                    issues=issues,
                    dropped=dropped,
                    kept=len(kept),
                    dry_run=dry_run,
                    backup_path=backup_path,
                    tags_stripped=tags_stripped,
                )
            )
        if not dry_run and dropped > 0:
            sys.exit(1)
        return

    # JSON backend: read the raw file (with right-truncation recovery).
    try:
        raw, recovered = storage.read_raw_entries()
    except FileNotFoundError:
        if not quiet:
            ui.print_info("No journal yet — nothing to repair.")
        return
    except storage.CorruptedStorageError as exc:
        # ``exc`` starts with "Error:" and ends with a period
        # ("...to reset."). Strip both so the final message is
        # `Cannot repair: Storage file is corrupted at …. Restore
        # from a backup with \`devlog restore\`.` with no doubled
        # punctuation.
        msg = str(exc).removeprefix("Error: ").rstrip(".")
        ui.print_error(
            f"Cannot repair: {msg}. Restore from a backup with `devlog restore`."
        )
        sys.exit(2)
    except storage.StoragePermissionError as exc:
        _handle_storage_error(exc)
        return

    kept, issues = storage.build_repair_plan(raw)
    raw_count = len(raw.get("entries", [])) if isinstance(raw, dict) else 0
    dropped = max(0, raw_count - len(kept))
    # Count how many tags the plan stripped (one per ``bad_tag`` issue
    # since each issue corresponds to a single offending tag string).
    tags_stripped = sum(1 for i in issues if i.kind == "bad_tag")
    # If the file was corrupt and we recovered via right-truncation,
    # the trailing garbage is still on disk even when the recovered
    # payload is perfectly valid. Force a rewrite in that case so
    # the on-disk file becomes well-formed JSON again.
    force_rewrite = recovered

    if not issues and not force_rewrite:
        if not quiet:
            ui.print_info("No issues found. Nothing to repair.")
        return

    backup_path: str | None = None
    if not dry_run and backup:
        try:
            storage.ensure_storage_dir()
            backups_dir = storage.get_backups_dir()
            backups_dir.mkdir(parents=True, exist_ok=True)
            backup_filename = storage.default_backup_filename()
            backup_path = str(backups_dir / backup_filename)

            with open(backup_path, "w", encoding="utf-8") as fh:
                # The recovered payload is a *valid* JSON object —
                # the raw on-disk bytes are still corrupt. Back up the
                # raw bytes so the user can recover anything we
                # trimmed off the end.
                if recovered:
                    raw_bytes = storage.get_storage_path().read_bytes()
                    fh.write(raw_bytes.decode("utf-8", errors="replace"))
                else:
                    json.dump(raw, fh, indent=2, ensure_ascii=False)
        except (OSError, PermissionError) as exc:
            ui.print_error(f"Could not write backup: {exc}")
            sys.exit(2)

    if not dry_run and not yes:
        # Prompt copy. We only mention "strip bad tag(s)" when the
        # plan will actually do that; pure entry drops keep the old
        # wording. The recovery case is the most user-visible, so it
        # always gets a dedicated prompt.
        if recovered and not issues:
            prompt = (
                "File has trailing corruption. Repair will trim the "
                "garbage and rewrite the journal. Continue?"
            )
        elif tags_stripped and not dropped:
            prompt = (
                f"Repair will strip {tags_stripped} bad tag"
                f"{ui.plural_s(tags_stripped)} from "
                f"{len(kept)} {ui.plural_noun(len(kept), 'entry')}. Continue?"
            )
        else:
            prompt = (
                f"Repair will drop {dropped} {ui.plural_noun(dropped, 'entry')}. Continue?"
            )
        click.confirm(prompt, default=False, abort=True)

    if not dry_run:
        try:
            storage.save_entries(kept)
        except StorageError as exc:
            _handle_storage_error(exc)
            return

    if not quiet:
        if recovered and not issues:
            console.print(
                ui.success_line(
                    "Trimmed trailing corruption; the recoverable "
                    "entries have been rewritten."
                )
            )
        console.print(
            ui.repair_summary(
                issues=issues,
                dropped=dropped,
                kept=len(kept),
                dry_run=dry_run,
                backup_path=backup_path,
                tags_stripped=tags_stripped,
            )
        )

    if not dry_run and dropped > 0:
        sys.exit(1)


@main.command()
@click.option(
    "--output",
    "-o",
    "output_path",
    type=click.Path(),
    default=None,
    help="Backup file path. Defaults to <data-dir>/backups/entries-TIMESTAMP.json.",
)
@click.option("--quiet", "-q", is_flag=True, help="Print only the backup path.")
def backup(output_path: str | None, quiet: bool) -> None:
    """Write a timestamped copy of the journal to the backups directory."""
    storage.ensure_storage_dir()
    entries = _load_entries_or_exit()

    if output_path:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
    else:
        backups_dir = storage.get_backups_dir()
        backups_dir.mkdir(parents=True, exist_ok=True)
        destination = backups_dir / storage.default_backup_filename()

    try:
        with destination.open("w", encoding="utf-8") as fh:
            json.dump(
                {"entries": [dataclasses.asdict(e) for e in entries]},
                fh,
                indent=2,
                ensure_ascii=False,
            )
    except (OSError, PermissionError) as exc:
        ui.print_error(f"Cannot write backup to {destination}: {exc}")
        sys.exit(2)

    if quiet:
        print(str(destination))
    else:
        console.print(ui.backup_result(str(destination), len(entries)))


@main.command()
@click.argument(
    "path",
    type=click.Path(exists=True, dir_okay=False, readable=True),
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Skip the confirmation prompt when the journal is non-empty.",
)
@click.option("--dry-run", is_flag=True, help="Validate the backup file without writing.")
@click.option("--quiet", "-q", is_flag=True, help="Suppress the summary output.")
def restore(path: str, yes: bool, dry_run: bool, quiet: bool) -> None:
    """Restore the journal from a backup file produced by `devlog backup`."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, PermissionError) as exc:
        ui.print_error(f"Cannot read {path}: {exc}")
        sys.exit(2)
    except json.JSONDecodeError as exc:
        ui.print_error(f"Backup file is not valid JSON: {exc}")
        sys.exit(2)

    issues = storage.validate_entries(data)
    # Reject backups whose root shape is unrecoverable (no `entries`
    # key, or `entries` is not a list). Per-row problems are tolerated
    # and reported via the same repair plan used by `devlog repair`.
    if any(iss.kind in ("bad_root", "bad_field") for iss in issues):
        ui.print_error("Backup file is structurally invalid; refusing to restore.")
        for iss in issues:
            ui.print_warning(f"  {iss.message}")
        sys.exit(2)

    # Apply the same repair plan to the backup so a hand-edited or
    # previously-broken backup can still be restored. Issues found in
    # the backup that the repair plan fixes are reported as warnings.
    new_entries, plan_issues = storage.build_repair_plan(data)
    skipped_issues = [
        iss for iss in plan_issues
        if iss.kind not in ("bad_root", "bad_field")
    ]

    if not new_entries:
        ui.print_info("Backup contains no valid entries; nothing to restore.")
        return

    if skipped_issues and not quiet:
        for iss in skipped_issues:
            short = (iss.entry_id[:8] + "…") if iss.entry_id and len(iss.entry_id) > 8 else (iss.entry_id or f"#{iss.index}")
            ui.print_warning(f"Skipped during restore: [{short}] {iss.message}")

    if storage._is_sqlite_backend():
        from devlog.sqlite_storage import get_sqlite_path

        current_path = get_sqlite_path()
        has_existing = current_path.exists()
    else:
        current_path = storage.get_storage_path()
        has_existing = current_path.exists()
    if has_existing and not yes and not dry_run:
        click.confirm(
            f"This will overwrite the current journal at {current_path}. Continue?",
            default=False,
            abort=True,
        )

    if dry_run:
        if not quiet:
            ui.print_info(
                f"DRY RUN: would restore {len(new_entries)} {ui.plural_noun(len(new_entries), 'entry')} from {path}."
            )
        return

    try:
        storage.save_entries(new_entries)
    except StorageError as exc:
        _handle_storage_error(exc)
        return

    if not quiet:
        line = ui.success_line(
            f"Restored {len(new_entries)} {ui.plural_noun(len(new_entries), 'entry')} from "
        )
        line.append(path, style="bold")
        console.print(line)


# ---------------------------------------------------------------------------
# undo
# ---------------------------------------------------------------------------


@main.command()
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
@click.option(
    "--keep",
    type=int,
    default=10,
    show_default=True,
    help="Total pre-write snapshots to retain after pruning (0 removes all).",
)
@click.option("--dry-run", is_flag=True, help="Show what would be restored; do not write.")
@click.option("--quiet", "-q", is_flag=True, help="Suppress the summary output.")
def undo(yes: bool, keep: int, quiet: bool, dry_run: bool) -> None:
    """Roll the journal back to its state before the last write.

    Every mutating command snapshots the previous store (JSON backend:
    a copy of entries.json; SQLite backend: a dump of the current rows)
    into ``backups/entries-*.pre.json`` before writing. ``undo`` restores
    the newest such snapshot — consuming it, so repeated ``undo`` calls
    step back through recent writes one at a time — then prunes older
    snapshots to ``--keep``. To redo, restore a backup (``devlog backup``
    files are never consumed).
    """
    snapshot = storage.latest_pre_write_backup()
    if snapshot is None:
        ui.print_error("Nothing to undo: no pre-write snapshot exists yet.")
        sys.exit(1)

    try:
        snapshot_entries = storage.load_pre_write_backup(snapshot)
    except storage.CorruptedStorageError:
        ui.print_error(f"Snapshot is unreadable: {snapshot}")
        sys.exit(2)

    if dry_run:
        if not quiet:
            ui.print_info(
                f"DRY RUN: would restore {len(snapshot_entries)} "
                f"{ui.plural_noun(len(snapshot_entries), 'entry')} from {snapshot}."
            )
        return

    if not yes and not quiet:
        snippet = (
            f' — "{snapshot_entries[-1].message[:40]}"'
            if snapshot_entries and snapshot_entries[-1].message
            else ""
        )
        prompt = (
            f"Restore journal to its state before the last write: "
            f"{len(snapshot_entries)} {ui.plural_noun(len(snapshot_entries), 'entry')}"
            f"{snippet}?"
        )
        if not click.confirm(prompt, default=False):
            ui.print_info("Aborted.")
            return

    # Snapshot inventory before the restore so the restore's own
    # pre-write snapshot can be removed afterwards. Without this, the
    # snapshot of the pre-undo state would become the newest snapshot
    # and a second `undo` would redo the just-undone write instead of
    # rewinding one step further.
    known = {p for _m, p in storage._list_pre_write_backups()}

    try:
        storage.save_entries(snapshot_entries)
    except StorageError as exc:
        _handle_storage_error(exc)
        return

    # Consume the restored snapshot plus any snapshot the restore itself
    # created (best-effort: leftovers are harmless, just a toggleable
    # redo state).
    try:
        snapshot.unlink()
        for _m, p in storage._list_pre_write_backups():
            if p not in known:
                p.unlink()
    except OSError:
        pass

    pruned = storage.prune_pre_write_backups(keep=max(0, keep))

    if not quiet:
        line = ui.success_line(
            f"Undone: journal restored to {len(snapshot_entries)} "
            f"{ui.plural_noun(len(snapshot_entries), 'entry')} from "
        )
        line.append(snapshot.name, style="bold")
        console.print(line)
        if pruned:
            ui.print_info(f"Pruned {pruned} old snapshot{ui.plural_s(pruned)}.")


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


@main.command()
@click.option("--quiet", "-q", is_flag=True, help="Output a single JSON health summary.")
def doctor(quiet: bool) -> None:
    """Check the journal store for corruption and report basic health stats."""
    if storage._is_sqlite_backend():
        from devlog.sqlite_storage import get_db_size, get_sqlite_path

        path = get_sqlite_path()
        report: dict = {
            "ok": True,
            "path": str(path),
            "exists": path.exists(),
            "writable": False,
            "size_bytes": get_db_size(),
            "entry_count": 0,
            "issues": [],
            "days_since_last": None,
            "top_messages": [],
        }

        writable, ok = _doctor_probe_writable(path)
        report["writable"] = writable
        report["ok"] = report["ok"] and ok

        if not path.exists():
            if not ok:
                sys.exit(2)
            _emit_doctor_report(report, quiet=quiet)
            return

        try:
            entries = storage.load_entries()
        except StorageError as exc:
            report["ok"] = False
            report["issues"] = [
                {
                    "kind": "corrupt_db",
                    "message": f"SQLite database is unreadable: {exc}",
                    "entry_id": None,
                }
            ]
            _emit_doctor_report(report, quiet=quiet)
            sys.exit(2)
            return  # unreachable

        report["entry_count"] = len(entries)
        if entries:
            report["days_since_last"] = _doctor_days_since_last(entries)
            report["top_messages"] = _doctor_top_messages(entries)

        _emit_doctor_report(report, quiet=quiet)
        if not report["ok"]:
            sys.exit(1)
        return

    # JSON backend
    path = storage.get_storage_path()
    report: dict = {
        "ok": True,
        "path": str(path),
        "exists": False,
        "writable": False,
        "size_bytes": 0,
        "entry_count": 0,
        "issues": [],
        "days_since_last": None,
        "top_messages": [],
    }

    writable, ok = _doctor_probe_writable(path)
    report["writable"] = writable
    report["ok"] = report["ok"] and ok

    if not path.exists():
        if not ok:
            sys.exit(2)
        _emit_doctor_report(report, quiet=quiet)
        return

    report["exists"] = True
    report["size_bytes"] = _doctor_probe_size(path)

    try:
        raw, _recovered = storage.read_raw_entries()
    except storage.CorruptedStorageError:
        report["ok"] = False
        report["issues"] = [
            {
                "kind": "corrupt_json",
                "message": "entries.json is not valid JSON",
                "entry_id": None,
            }
        ]
        _emit_doctor_report(report, quiet=quiet)
        sys.exit(2)
        return  # unreachable
    except storage.StoragePermissionError as exc:
        _handle_storage_error(exc)
        return

    issue_dicts, valid_entries, issues_ok = _doctor_collect_issues(raw)
    report["issues"] = issue_dicts
    report["entry_count"] = len(valid_entries)
    if not issues_ok:
        report["ok"] = False

    if valid_entries:
        report["days_since_last"] = _doctor_days_since_last(valid_entries)
        report["top_messages"] = _doctor_top_messages(valid_entries)

    _emit_doctor_report(report, quiet=quiet)
    if not report["ok"]:
        sys.exit(1)


# ---------------------------------------------------------------------------
# completions
# ---------------------------------------------------------------------------


@main.command()
@click.argument(
    "shell",
    type=click.Choice(["bash", "zsh", "fish"], case_sensitive=False),
)
def completions(shell: str) -> None:
    """Print a shell completion script for the given shell."""
    _completions.completions(shell)


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


@main.command()
@click.option(
    "--output",
    "-o",
    "output",
    type=click.Path(),
    default=None,
    help=(
        "Output file path. Default: <data-dir>/exports/devlog-YYYYMMDD-HHMMSS.<ext> "
        "where <ext> is md or json based on --format. "
        "Respects DEVLOG_DATA_DIR."
    ),
)
@click.option(
    "--format",
    "-f",
    "fmt",
    type=click.Choice(["auto", "markdown", "json"], case_sensitive=False),
    default="auto",
    show_default=True,
    help=(
        "Output format. 'auto' infers from the --output extension "
        "(.json, .md, .markdown) and falls back to Markdown."
    ),
)
@click.option("--tag", "-t", "tags", multiple=True, help="Filter by tag (AND).")
@click.option("--since", default=None, help="Only export entries on/after this date (UTC).")
@click.option("--until", default=None, help="Only export entries on/before this date (UTC).")
@click.option("--quiet", "-q", is_flag=True, help="Suppress progress output.")
def export(
    output: str | None,
    fmt: str,
    tags: tuple[str, ...],
    since: str | None,
    until: str | None,
    quiet: bool,
) -> None:
    """Export entries to a Markdown or JSON file."""
    _io.export(output, fmt, tags, since, until, quiet)
