"""Command-line interface for devlog.

This module is intentionally thin: it parses arguments via Click,
delegates persistence to ``storage``, and delegates *all* rendering to
``ui``. Keeping rendering in one place is what guarantees a consistent
look-and-feel across commands.
"""

import datetime
import json
import os
import re
import sys
import uuid
from typing import Tuple

import click
from rich.text import Text

from devlog import storage
from devlog.models import Entry
from devlog.storage import StorageError
from devlog import ui

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TAG_RE = re.compile(r"^[a-z0-9\-]+$")
MAX_TAG_LENGTH = 32
MAX_TAGS = 10

console = ui.console
err_console = ui.err_console


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_tags(raw_tags: Tuple[str, ...]) -> list[str]:
    """Validate and normalise a sequence of raw tag strings.

    Validation rules (in order):
        1. Normalise: strip whitespace, lower-case.
        2. Character set: only a-z, 0-9, hyphen.
        3. Maximum tag length: 32 characters.
        4. Maximum distinct tags per entry: 10.

    Args:
        raw_tags: tuple of raw tag strings as provided by the user.

    Returns:
        list[str]: deduplicated, normalised tags.

    Raises:
        click.UsageError: on any validation failure.
    """
    seen: list[str] = []
    seen_set: set[str] = set()

    for original in raw_tags:
        norm = original.strip().lower()

        if not TAG_RE.fullmatch(norm):
            raise click.UsageError(
                f'Tag "{original}" contains invalid characters. '
                "Use lowercase letters, numbers, and hyphens only."
            )

        if len(norm) > MAX_TAG_LENGTH:
            raise click.UsageError(
                f'Tag "{original}" exceeds maximum length of '
                f"{MAX_TAG_LENGTH} characters."
            )

        if norm not in seen_set:
            seen_set.add(norm)
            seen.append(norm)

    if len(seen) > MAX_TAGS:
        raise click.UsageError(
            f"Maximum {MAX_TAGS} tags per entry. You provided {len(seen)}."
        )

    return seen


def _filter_by_tags(entries: list[Entry], tags: Tuple[str, ...]) -> list[Entry]:
    """Filter entries so that all provided tags are present (AND logic).

    Args:
        entries: full list of Entry objects.
        tags:    raw tag strings to filter by (normalised internally).

    Returns:
        list[Entry]: entries that carry every requested tag.
    """
    if not tags:
        return entries
    norm_filter = {t.strip().lower() for t in tags}
    return [e for e in entries if norm_filter.issubset(set(e.tags))]


def _handle_storage_error(exc: StorageError) -> None:
    """Render a storage error and exit with code 2.

    Args:
        exc: the StorageError (or subclass) that was raised.
    """
    ui.print_error(str(exc).removeprefix("Error: "))
    sys.exit(2)


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
            _interactive_repl()
            ctx.exit(0)
            return
        ui.root_banner()
        ctx.exit(0)


# ---------------------------------------------------------------------------
# Interactive REPL (`devlog --interactive`)
# ---------------------------------------------------------------------------


def _interactive_repl() -> None:
    """A minimal line-based REPL for browsing and quick adds.

    Supported commands at the prompt:
        add <message> [-t tag1 -t tag2 ...]   → add an entry
        s <query>                              → search the journal
        l [-t tag] [-n N]                      → list entries
        tags                                    → show tag counts
        today                                   → show today's entries
        stats                                   → show summary
        show <id>                               → show one entry
        help                                    → show available commands
        q | quit | exit                         → leave the REPL

    Each successful action is followed by the standard Rich output. The
    REPL keeps running until the user quits or an EOFError is raised
    (e.g. Ctrl-D).
    """
    from rich.prompt import Prompt

    console.print(
        "[bold cyan]devlog interactive[/bold cyan]  ·  "
        "type [bold]help[/bold] for commands, [bold]q[/bold] to quit"
    )

    while True:
        try:
            line = Prompt.ask("[bold magenta]devlog>[/bold magenta]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()  # newline
            return

        if not line:
            continue
        if line in ("q", "quit", "exit"):
            return
        if line in ("h", "help", "?"):
            _print_repl_help()
            continue

        # Dispatch by re-invoking the CLI in-process. Easier than re-implementing.
        try:
            # Use Click's standalone command invocation.
            import click.testing

            runner = click.testing.CliRunner(mix_stderr=False)
            # Split shell-style arguments.
            try:
                import shlex
                argv = shlex.split(line)
            except ValueError as exc:
                ui.print_error(str(exc))
                continue
            if not argv:
                continue
            result = runner.invoke(main, argv, catch_exceptions=False)
            if result.output:
                console.print(result.output, highlight=False)
        except SystemExit:
            # Click's sys.exit() bubbles up here; swallow so the REPL keeps going.
            pass
        except Exception as exc:  # noqa: BLE001
            ui.print_error(str(exc))


def _print_repl_help() -> None:
    lines = [
        "Available commands:",
        "  add <message> [-t tag ...]   Add a new journal entry",
        "  l | list [-t tag] [-n N]     List entries, newest first",
        "  s | search <query>           Search entry messages",
        "  show <id>                    Show a single entry",
        "  edit <id> [-m msg] [-t tag]  Edit an entry",
        "  delete <id> [-y]             Delete an entry",
        "  today                        Show today's entries",
        "  tail [N]                     Show the N most recent entries",
        "  tags                         List tags with usage counts",
        "  stats                        Summarize the journal",
        "  rename-tag <old> <new>       Rename a tag across all entries",
        "  import <path>                Import entries from a file",
        "  export [-o path]             Export entries to a Markdown file",
        "  h | help                     Show this help",
        "  q | quit | exit              Leave the REPL",
    ]
    for line in lines:
        console.print(line)


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


@main.command()
@click.argument("message")
@click.option("--tag", "-t", multiple=True, help="Attach tags (repeatable).")
@click.option("--quiet", "-q", is_flag=True, help="Suppress output.")
def add(message: str, tag: Tuple[str, ...], quiet: bool) -> None:
    """Add a new journal entry."""
    if not message:
        ui.print_error("MESSAGE cannot be empty.")
        sys.exit(1)

    try:
        norm_tags = _validate_tags(tag)
    except click.UsageError as exc:
        ui.print_error(str(exc))
        sys.exit(1)

    ts = datetime.datetime.now(tz=datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
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
@click.option("--quiet", "-q", is_flag=True, help="Output raw JSON lines.")
def list_entries(
    tags: Tuple[str, ...], limit: int, show_all: bool, quiet: bool
) -> None:
    """List journal entries, newest first."""
    if not show_all and limit <= 0:
        ui.print_error("--limit must be a positive integer.")
        sys.exit(1)

    try:
        all_entries = storage.load_entries()
    except StorageError as exc:
        _handle_storage_error(exc)
        return  # unreachable; silences type-checker

    filtered = _filter_by_tags(all_entries, tags)
    filtered.sort(key=lambda e: e.created_at, reverse=True)

    total = len(filtered)
    shown = filtered if show_all else filtered[:limit]

    if quiet:
        import dataclasses

        for entry in shown:
            print(json.dumps(dataclasses.asdict(entry), ensure_ascii=False))
        return

    if total == 0:
        if tags:
            ui.print_info("No entries match your filters.")
        else:
            ui.print_info("No entries found.")
        return

    title = f"Journal · {total} entr{'y' if total == 1 else 'ies'}"
    table = ui.entries_table(shown, total, title=title)
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
@click.option("--quiet", "-q", is_flag=True, help="Output raw JSON lines.")
def search(query: str, tags: Tuple[str, ...], limit: int, quiet: bool) -> None:
    """Search entry messages for QUERY (case-insensitive substring)."""
    if limit <= 0:
        ui.print_error("--limit must be a positive integer.")
        sys.exit(1)

    try:
        all_entries = storage.load_entries()
    except StorageError as exc:
        _handle_storage_error(exc)
        return

    filtered = _filter_by_tags(all_entries, tags)
    matched = [e for e in filtered if query.lower() in e.message.lower()]
    matched.sort(key=lambda e: e.created_at, reverse=True)

    total = len(matched)
    shown = matched[:limit]

    if quiet:
        import dataclasses

        for entry in shown:
            print(json.dumps(dataclasses.asdict(entry), ensure_ascii=False))
        return

    if total == 0:
        ui.print_info(f'No entries matched "{query}".')
        return

    match_word = "match" if total == 1 else "matches"
    title = f"Journal · {total} {match_word}"
    subtitle = f'Query: "{query}"'
    table = ui.entries_table(
        shown, total, highlight_query=query, title=title, subtitle=subtitle
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

    try:
        all_entries = storage.load_entries()
    except StorageError as exc:
        _handle_storage_error(exc)
        return

    match = storage.find_entry_by_id(all_entries, id)
    if match is None:
        # Distinguish "not found" from "ambiguous"
        candidates = storage.find_entry_id_prefix_matches(all_entries, id)
        if len(candidates) > 1:
            short_ids = ", ".join(e.id[: ui.ID_DISPLAY_LEN] for e in candidates)
            ui.print_error(
                f'ID prefix "{id}" matches multiple entries: {short_ids}. '
                "Use a longer prefix."
            )
        else:
            ui.print_error(f'No entry found with id "{id}".')
        sys.exit(1)

    if quiet:
        import dataclasses

        print(json.dumps(dataclasses.asdict(match), ensure_ascii=False))
        return

    console.print(ui.show_panel(match))


# ---------------------------------------------------------------------------
# edit
# ---------------------------------------------------------------------------


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
def edit(
    id: str,
    new_message: str | None,
    set_tags: Tuple[str, ...],
    add_tags: Tuple[str, ...],
    remove_tags: Tuple[str, ...],
    quiet: bool,
) -> None:
    """Edit an entry's message and/or tags in place."""
    if not id:
        ui.print_error("ID is required.")
        sys.exit(1)

    try:
        all_entries = storage.load_entries()
    except StorageError as exc:
        _handle_storage_error(exc)
        return

    match = storage.find_entry_by_id(all_entries, id)
    if match is None:
        candidates = storage.find_entry_id_prefix_matches(all_entries, id)
        if len(candidates) > 1:
            short_ids = ", ".join(e.id[: ui.ID_DISPLAY_LEN] for e in candidates)
            ui.print_error(
                f'ID prefix "{id}" matches multiple entries: {short_ids}. '
                "Use a longer prefix."
            )
        else:
            ui.print_error(f'No entry found with id "{id}".')
        sys.exit(1)

    has_flags = (
        new_message is not None
        or bool(set_tags)
        or bool(add_tags)
        or bool(remove_tags)
    )

    if not has_flags:
        # No flags → spawn $VISUAL / $EDITOR / fallback chain
        edited_message = _edit_in_editor(match.message)
        if edited_message is None:
            ui.print_error("Editor exited abnormally; no changes saved.")
            sys.exit(2)
        new_message = edited_message

    # Compute new tags
    current_tags = list(match.tags)
    if set_tags:
        # Validate set_tags (replaces the set entirely)
        try:
            current_tags = _validate_tags(set_tags)
        except click.UsageError as exc:
            ui.print_error(str(exc))
            sys.exit(1)
    if add_tags:
        try:
            additions = _validate_tags(add_tags)
        except click.UsageError as exc:
            ui.print_error(str(exc))
            sys.exit(1)
        for t in additions:
            if t not in current_tags:
                current_tags.append(t)
    if remove_tags:
        to_remove = {t.strip().lower() for t in remove_tags}
        current_tags = [t for t in current_tags if t not in to_remove]

    # Determine the final message
    final_message = new_message if new_message is not None else match.message

    # No-op detection
    if final_message == match.message and current_tags == match.tags:
        ui.print_info("No changes.")
        return

    updated = Entry(
        id=match.id,
        message=final_message,
        tags=current_tags,
        created_at=match.created_at,
        updated_at=datetime.datetime.now(tz=datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
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
        with open(tmp_path, "r", encoding="utf-8") as fh:
            return fh.read().rstrip("\n")
    except FileNotFoundError:
        return None
    except (OSError, IOError):
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
def delete(id: str, yes: bool, quiet: bool) -> None:
    """Delete an entry by ID."""
    if not id:
        ui.print_error("ID is required.")
        sys.exit(1)

    try:
        all_entries = storage.load_entries()
    except StorageError as exc:
        _handle_storage_error(exc)
        return

    match = storage.find_entry_by_id(all_entries, id)
    if match is None:
        candidates = storage.find_entry_id_prefix_matches(all_entries, id)
        if len(candidates) > 1:
            short_ids = ", ".join(e.id[: ui.ID_DISPLAY_LEN] for e in candidates)
            ui.print_error(
                f'ID prefix "{id}" matches multiple entries: {short_ids}. '
                "Use a longer prefix."
            )
        else:
            ui.print_error(f'No entry found with id "{id}".')
        sys.exit(1)

    if not yes:
        short = match.id[: ui.ID_DISPLAY_LEN]
        snippet = match.message[:40] + ("…" if len(match.message) > 40 else "")
        prompt = f'Delete entry {short} ("{snippet}")?'
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
    if not show_all and limit <= 0:
        ui.print_error("--limit must be a positive integer.")
        sys.exit(1)

    try:
        all_entries = storage.load_entries()
    except StorageError as exc:
        _handle_storage_error(exc)
        return

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
        rows.sort(key=lambda r: (-(int(_iso_to_epoch(r[2])) if r[2] else 0), r[0]))

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


def _iso_to_epoch(iso: str) -> int:
    """Convert a stored ISO 8601 UTC string to a POSIX epoch int."""
    dt = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return int(dt.timestamp())


# ---------------------------------------------------------------------------
# today
# ---------------------------------------------------------------------------


@main.command()
@click.option(
    "--limit", "-n", type=int, default=50, show_default=True, help="Max entries to show."
)
@click.option("--quiet", "-q", is_flag=True, help="Output raw JSON lines.")
def today(limit: int, quiet: bool) -> None:
    """Show entries created today (UTC), newest first."""
    if limit <= 0:
        ui.print_error("--limit must be a positive integer.")
        sys.exit(1)

    try:
        all_entries = storage.load_entries()
    except StorageError as exc:
        _handle_storage_error(exc)
        return

    today_iso_date = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y-%m-%d")
    today_entries = [e for e in all_entries if e.created_at.startswith(today_iso_date)]
    today_entries.sort(key=lambda e: e.created_at, reverse=True)

    total = len(today_entries)
    shown = today_entries[:limit]

    if quiet:
        import dataclasses
        for entry in shown:
            print(json.dumps(dataclasses.asdict(entry), ensure_ascii=False))
        return

    if total == 0:
        ui.print_info("No entries yet today.")
        return

    title = f"Today · {total} entr{'y' if total == 1 else 'ies'}"
    subtitle = today_iso_date
    table = ui.entries_table(shown, total, title=title, subtitle=subtitle)
    console.print(table)


# ---------------------------------------------------------------------------
# tail
# ---------------------------------------------------------------------------


@main.command()
@click.argument("n", required=False, default=5, type=int)
@click.option("--tag", "-t", "tags", multiple=True, help="Filter by tag (AND).")
@click.option("--quiet", "-q", is_flag=True, help="Output raw JSON lines.")
def tail(n: int, tags: Tuple[str, ...], quiet: bool) -> None:
    """Show the N most recent entries (default: 5)."""
    if n <= 0:
        ui.print_error("N must be a positive integer.")
        sys.exit(1)

    try:
        all_entries = storage.load_entries()
    except StorageError as exc:
        _handle_storage_error(exc)
        return

    filtered = _filter_by_tags(all_entries, tags)
    filtered.sort(key=lambda e: e.created_at, reverse=True)
    shown = filtered[:n]
    total = len(filtered)

    if quiet:
        import dataclasses
        for entry in shown:
            print(json.dumps(dataclasses.asdict(entry), ensure_ascii=False))
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
@click.option("--quiet", "-q", is_flag=True, help="Output a single JSON summary.")
def stats(quiet: bool) -> None:
    """Show a summary of the journal: total entries, date range, top tags, and a 30-day sparkline."""
    try:
        all_entries = storage.load_entries()
    except StorageError as exc:
        _handle_storage_error(exc)
        return

    if not all_entries:
        ui.print_info("No entries to summarize.")
        return

    # Total & date range
    total = len(all_entries)
    sorted_by_date = sorted(all_entries, key=lambda e: e.created_at)
    first_iso = sorted_by_date[0].created_at
    last_iso = sorted_by_date[-1].created_at

    # Per-day counts for the last 30 days
    today = datetime.datetime.now(tz=datetime.timezone.utc).date()
    per_day: dict[str, int] = {}
    for entry in all_entries:
        day = entry.created_at[:10]
        per_day[day] = per_day.get(day, 0) + 1
    last_30_days: list[tuple[str, int]] = []
    for i in range(29, -1, -1):
        d = today - datetime.timedelta(days=i)
        iso = d.strftime("%Y-%m-%d")
        last_30_days.append((iso, per_day.get(iso, 0)))
    sparkline_data = [count for _, count in last_30_days]

    # Top tags
    from collections import Counter

    tag_counter: Counter = Counter()
    for entry in all_entries:
        for tag in entry.tags:
            tag_counter[tag] += 1
    top_tags = tag_counter.most_common(5)

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
                },
                ensure_ascii=False,
            )
        )
        return

    # Render
    from rich.padding import Padding
    from rich.text import Text

    first_str = ui._format_dt(first_iso)
    last_str = ui._format_dt(last_iso)

    # Compute active-days span for an average-per-day
    try:
        first_dt = datetime.datetime.fromisoformat(first_iso.replace("Z", "+00:00"))
        last_dt = datetime.datetime.fromisoformat(last_iso.replace("Z", "+00:00"))
        span_days = max(1, (last_dt.date() - first_dt.date()).days + 1)
    except ValueError:
        span_days = 1
    avg_per_day = total / span_days

    body_rows: list = [
        Text(),
        _stats_row("Total", str(total)),
        _stats_row("First", first_str),
        _stats_row("Last", last_str),
        _stats_row("Span", f"{span_days} day{'s' if span_days != 1 else ''}"),
        _stats_row("Avg/day", f"{avg_per_day:.2f}"),
        Text(),
        Text("Top 5 tags", style="bold"),
    ]
    for tag, count in top_tags:
        body_rows.append(_stats_row(f"  {tag}", str(count)))

    body_rows.append(Text())
    body_rows.append(Text("Last 30 days (each ▏ = 1 entry)", style="bold"))
    body_rows.append(Text(_ascii_sparkline(sparkline_data), style="cyan"))

    panel = ui.Panel(
        Padding(ui.Group(*body_rows), (0, 1)),
        border_style="cyan",
        title="Journal Stats",
        title_align="left",
    )
    console.print(panel)


def _ascii_sparkline(values: list[int]) -> str:
    """Build a compact horizontal sparkline using block characters.

    Each day is one cell. Vertical height is determined by the max value.
    """
    if not values:
        return ""
    max_v = max(values) or 1
    # Use 4 height levels of block characters
    blocks = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    out = []
    for v in values:
        idx = min(len(blocks) - 1, int((v / max_v) * len(blocks)))
        out.append(blocks[idx])
    return "".join(out)


def _stats_row(label: str, value: str) -> Text:
    """Build a stats row with the same dim-label style used by entry panels."""
    t = Text()
    t.append(f"{label:<10}", style="dim")
    t.append(": ")
    t.append(value)
    return t


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
    # Validate NEW with the same rules as add
    try:
        new_normalized = _validate_tags((new,))
    except click.UsageError as exc:
        ui.print_error(str(exc))
        sys.exit(1)
    new_tag = new_normalized[0]
    old_normalized = old.strip().lower()
    if not old_normalized:
        ui.print_error("OLD tag cannot be empty.")
        sys.exit(1)

    if old_normalized == new_tag:
        ui.print_info(f'OLD and NEW are the same ("{new_tag}"). No changes made.')
        return

    try:
        all_entries = storage.load_entries()
    except StorageError as exc:
        _handle_storage_error(exc)
        return

    affected: list[Entry] = []
    for entry in all_entries:
        if old_normalized in entry.tags:
            affected.append(entry)

    if not affected:
        if not quiet:
            ui.print_info(f'No entries with tag "{old_normalized}".')
        return

    if dry_run:
        line = Text()
        line.append("DRY RUN: ", style="bold yellow")
        line.append(
            f"would update {len(affected)} entr{'y' if len(affected) == 1 else 'ies'}: ",
            style="yellow",
        )
        line.append(f"{old_normalized} → {new_tag}", style="bold")
        console.print(line)
        return

    # Apply in-memory and persist
    now_iso = datetime.datetime.now(tz=datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    for entry in affected:
        new_tags: list[str] = []
        for t in entry.tags:
            if t == old_normalized:
                # Replace with new_tag (dedup against any existing new_tag)
                if new_tag not in new_tags:
                    new_tags.append(new_tag)
            elif t == new_tag:
                # Keep an existing new_tag, dedup if we've already added it
                if new_tag not in new_tags:
                    new_tags.append(new_tag)
            else:
                new_tags.append(t)
        entry.tags = new_tags
        entry.updated_at = now_iso

    try:
        storage.save_entries(all_entries)
    except StorageError as exc:
        _handle_storage_error(exc)
        return

    if not quiet:
        line = Text()
        line.append("✔ ", style="bold green")
        line.append(
            f"Renamed {old_normalized} → {new_tag} in {len(affected)} entr{'y' if len(affected) == 1 else 'ies'}.",
            style="green",
        )
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
    type=click.Choice(["auto", "json", "markdown"], case_sensitive=False),
    default="auto",
    show_default=True,
    help="Input format. 'auto' detects by file extension (.json, .md).",
)
@click.option("--dry-run", is_flag=True, help="Show what would be imported; do not write.")
@click.option("--quiet", "-q", is_flag=True, help="Suppress preview output.")
def import_cmd(path: str, fmt: str, dry_run: bool, quiet: bool) -> None:
    """Import entries from a JSON or Markdown export file."""
    if fmt == "auto":
        lower = path.lower()
        if lower.endswith(".json"):
            fmt = "json"
        elif lower.endswith(".md") or lower.endswith(".markdown"):
            fmt = "markdown"
        else:
            ui.print_error(
                f'Cannot auto-detect format for "{path}". Use --format=json or --format=markdown.'
            )
            sys.exit(2)

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
        raw_entries = payload.get("entries", [])
        candidates: list[Entry] = []
        for item in raw_entries:
            try:
                e = Entry(**item)
            except (TypeError, ValueError):
                continue
            candidates.append(e)
    else:
        candidates = _parse_markdown_export(content)

    try:
        existing = storage.load_entries()
    except StorageError as exc:
        _handle_storage_error(exc)
        return

    # Idempotency: skip entries that already exist (by id, or by created_at+message fingerprint)
    existing_ids = {e.id for e in existing}
    existing_fps = {(e.created_at, e.message) for e in existing}

    to_add: list[Entry] = []
    skipped = 0
    for cand in candidates:
        if cand.id in existing_ids:
            skipped += 1
            continue
        if (cand.created_at, cand.message) in existing_fps:
            skipped += 1
            continue
        # Always mint a fresh id for new entries
        cand.id = str(uuid.uuid4())
        to_add.append(cand)
        existing_ids.add(cand.id)
        existing_fps.add((cand.created_at, cand.message))

    if dry_run:
        line = Text()
        line.append("DRY RUN: ", style="bold yellow")
        line.append(
            f"would import {len(to_add)} entr{'y' if len(to_add) == 1 else 'ies'}, skip {skipped} duplicate{'s' if skipped != 1 else ''}.",
            style="yellow",
        )
        console.print(line)
        return

    if to_add:
        try:
            existing.extend(to_add)
            storage.save_entries(existing)
        except StorageError as exc:
            _handle_storage_error(exc)
            return

    if not quiet:
        if to_add:
            line = Text()
            line.append("✔ ", style="bold green")
            line.append(
                f"Imported {len(to_add)} entr{'y' if len(to_add) == 1 else 'ies'}, skipped {skipped} duplicate{'s' if skipped != 1 else ''}.",
                style="green",
            )
            console.print(line)
        else:
            # No new imports — surface the skip count so the user knows it was a no-op, not a bug.
            if skipped:
                ui.print_info(
                    f"No new entries to import ({skipped} duplicate{'s' if skipped != 1 else ''} skipped)."
                )
            else:
                ui.print_info("No entries to import.")


def _parse_markdown_export(content: str) -> list[Entry]:
    """Parse the markdown format produced by `devlog export`.

    Each entry block looks like:
        ## 2025-05-11 10:22 UTC — a1b2c3d4

        Message body.

        **Tags:** backend, security

        ---
    """
    import re as _re

    # Heading pattern: "## YYYY-MM-DD HH:MM UTC — XXXXXXXX"
    heading_re = _re.compile(
        r"^##\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s+UTC)\s+—\s+([a-f0-9]+)\s*$",
        _re.MULTILINE,
    )
    tags_re = _re.compile(r"^\*\*Tags:\*\*\s*(.+?)\s*$", _re.MULTILINE)
    sep_re = _re.compile(r"^---\s*$", _re.MULTILINE)

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

        # Convert "YYYY-MM-DD HH:MM UTC" → "YYYY-MM-DDTHH:MM:00Z"
        # (the heading only carries minute precision; seconds default to 00)
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
    shell = shell.lower()
    if shell == "bash":
        print(_BASH_COMPLETION)
    elif shell == "zsh":
        print(_ZSH_COMPLETION)
    elif shell == "fish":
        print(_FISH_COMPLETION)
    else:
        ui.print_error(f'Unsupported shell "{shell}".')
        sys.exit(1)


_BASH_COMPLETION = """# bash completion for devlog
# Source this file or copy it into ~/.bash_completion.d/
_devlog_completion() {
    local cur prev words cword
    _init_completion || return
    local commands="add show edit delete list search today tail tags stats rename-tag import completions export"
    if [[ ${cword} -eq 1 ]]; then
        COMPREPLY=($(compgen -W "${commands}" -- "${cur}"))
        return
    fi
    case "${words[1]}" in
        edit|delete|show) COMPREPLY=($(compgen -W "$(devlog list --quiet 2>/dev/null | python3 -c 'import sys,json
for line in sys.stdin: print(json.loads(line)["id"][:8])')" -- "${cur}")) ;;
        list|search|tail|export) COMPREPLY=($(compgen -W "--tag --limit --all --quiet" -- "${cur}")) ;;
    esac
}
complete -F _devlog_completion devlog
"""

_ZSH_COMPLETION = """#compdef devlog
# zsh completion for devlog
_devlog() {
    local -a commands
    commands=(
        'add:Add a new journal entry'
        'show:Show a single entry by ID'
        'edit:Edit an entry message and/or tags'
        'delete:Delete an entry by ID'
        'list:List entries, newest first'
        'search:Search entry messages'
        'today:Show today entries'
        'tail:Show the N most recent entries'
        'tags:List tags with usage counts'
        'stats:Summarize the journal'
        'rename-tag:Rename a tag across all entries'
        'import:Import entries from a JSON or Markdown file'
        'completions:Print a shell completion script'
        'export:Export entries to a Markdown file'
    )
    _describe 'command' commands
}
_devlog "$@"
"""

_FISH_COMPLETION = """# fish completion for devlog
complete -c devlog -f
complete -c devlog -n "__fish_use_subcommand" -a "add" -d "Add a new journal entry"
complete -c devlog -n "__fish_use_subcommand" -a "show" -d "Show a single entry by ID"
complete -c devlog -n "__fish_use_subcommand" -a "edit" -d "Edit an entry"
complete -c devlog -n "__fish_use_subcommand" -a "delete" -d "Delete an entry"
complete -c devlog -n "__fish_use_subcommand" -a "list" -d "List entries, newest first"
complete -c devlog -n "__fish_use_subcommand" -a "search" -d "Search entry messages"
complete -c devlog -n "__fish_use_subcommand" -a "today" -d "Show today's entries"
complete -c devlog -n "__fish_use_subcommand" -a "tail" -d "Show the N most recent entries"
complete -c devlog -n "__fish_use_subcommand" -a "tags" -d "List tags with usage counts"
complete -c devlog -n "__fish_use_subcommand" -a "stats" -d "Summarize the journal"
complete -c devlog -n "__fish_use_subcommand" -a "rename-tag" -d "Rename a tag"
complete -c devlog -n "__fish_use_subcommand" -a "import" -d "Import entries from a file"
complete -c devlog -n "__fish_use_subcommand" -a "completions" -d "Print a completion script"
complete -c devlog -n "__fish_use_subcommand" -a "export" -d "Export entries to Markdown"
"""


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


@main.command()
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default="./devlog-export.md",
    show_default=True,
    help="Output file path.",
)
@click.option("--tag", "-t", "tags", multiple=True, help="Filter by tag (AND).")
@click.option("--quiet", "-q", is_flag=True, help="Suppress progress output.")
def export(output: str, tags: Tuple[str, ...], quiet: bool) -> None:
    """Export entries to a Markdown file."""
    try:
        all_entries = storage.load_entries()
    except StorageError as exc:
        _handle_storage_error(exc)
        return

    filtered = _filter_by_tags(all_entries, tags)
    filtered.sort(key=lambda e: e.created_at, reverse=True)

    if not filtered:
        ui.print_warning("Warning: No entries to export.")
        sys.exit(0)

    def _entry_md(entry: Entry) -> str:
        short_id = entry.id[: ui.ID_DISPLAY_LEN]
        date_str = ui._format_dt(entry.created_at)
        tags_str = ", ".join(entry.tags) if entry.tags else ui.TAG_NONE
        return (
            f"## {date_str} — {short_id}\n\n"
            f"{entry.message}\n\n"
            f"**Tags:** {tags_str}\n\n"
            "---\n"
        )

    try:
        if quiet:
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
            entry_word = "entry" if len(filtered) == 1 else "entries"
            line = Text()  # local alias to keep imports tidy
            line.append("✔ ", style="bold green")
            line.append(
                f"Exported {len(filtered)} {entry_word} to ",
                style="green",
            )
            line.append(output, style="bold")
            err_console.print(line)
    except (PermissionError, OSError):
        ui.print_error(f"Cannot write to {output}. Check the path and permissions.")
        sys.exit(2)
