import datetime
import json
import re
import sys
import uuid
from typing import Tuple

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from devlog import storage
from devlog.models import Entry
from devlog.storage import (
    CorruptedStorageError,
    StorageError,
    StoragePermissionError,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VERSION = "1.0.0"
TAG_RE = re.compile(r"^[a-z0-9\-]+$")
MAX_TAG_LENGTH = 32
MAX_TAGS = 10
MSG_TRUNCATE_LEN = 60

console = Console()
err_console = Console(stderr=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_dt(iso: str) -> str:
    """Convert a stored ISO 8601 UTC string to a human-readable display form.

    Args:
        iso: datetime string in "YYYY-MM-DDTHH:MM:SSZ" format.

    Returns:
        str: formatted as "YYYY-MM-DD HH:MM UTC".
    """
    dt = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _truncate(msg: str, length: int = MSG_TRUNCATE_LEN) -> str:
    """Truncate a string to *length* chars, adding an ellipsis if needed.

    Args:
        msg:    the string to truncate.
        length: maximum character count (default 60).

    Returns:
        str: original string, or first (length-3) chars + "...".
    """
    if len(msg) > length:
        return msg[: length - 3] + "..."
    return msg


def _validate_tags(
    raw_tags: Tuple[str, ...],
) -> list[str]:
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
        click.UsageError: on any validation failure (message already formatted).
    """
    seen: list[str] = []
    seen_set: set[str] = set()

    for original in raw_tags:
        norm = original.strip().lower()

        if not TAG_RE.fullmatch(norm):
            raise click.UsageError(
                f'Error: Tag "{original}" contains invalid characters. '
                "Use lowercase letters, numbers, and hyphens only."
            )

        if len(norm) > MAX_TAG_LENGTH:
            raise click.UsageError(
                f'Error: Tag "{original}" exceeds maximum length of '
                f"{MAX_TAG_LENGTH} characters."
            )

        if norm not in seen_set:
            seen_set.add(norm)
            seen.append(norm)

    if len(seen) > MAX_TAGS:
        raise click.UsageError(
            f"Error: Maximum {MAX_TAGS} tags per entry. "
            f"You provided {len(seen)}."
        )

    return seen


def _highlight(message: str, query: str) -> str:
    """Return a Rich-markup string with query occurrences highlighted.

    The message is truncated to MSG_TRUNCATE_LEN *before* highlighting so that
    Rich markup tags are never split mid-way by truncation.

    Args:
        message: the raw entry message.
        query:   the search string (case-insensitive match).

    Returns:
        str: Rich markup string ready for console.print.
    """
    from rich.markup import escape  # local import to keep top-level clean

    from rich.markup import escape

    trunc = _truncate(message)
    escaped_q = escape(query)

    def replacer(match):
        return f"[bold yellow]{escape(match.group(0))}[/bold yellow]"

    highlighted = re.sub(
        re.escape(query),
        replacer,
        escape(trunc),
        flags=re.IGNORECASE,
    )
    return highlighted


def _entries_table(entries: list[Entry], total: int, *, highlight_query: str = "") -> Table:
    """Build a Rich Table for a list of entries.

    Args:
        entries:         the entries to render (already sliced to limit).
        total:           total matched count (before limit) for the footer.
        highlight_query: when non-empty, highlight this substring in messages.

    Returns:
        Table: a fully configured Rich Table instance.
    """
    table = Table(show_footer=True)
    table.add_column("ID", style="dim white", no_wrap=True)
    table.add_column("Date", style="cyan", no_wrap=True)
    table.add_column("Tags", style="magenta")
    table.add_column(
        "Message",
        footer=f"Showing {len(entries)} of {total} entries.",
        footer_style="default",
    )

    for entry in entries:
        short_id = entry.id[:8]
        date_str = _format_dt(entry.created_at)
        tags_str = ", ".join(entry.tags) if entry.tags else "(none)"

        if highlight_query:
            msg_cell = _highlight(entry.message, highlight_query)
        else:
            msg_cell = _truncate(entry.message)

        table.add_row(short_id, date_str, tags_str, msg_cell)

    return table


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
    """Print a storage error to stderr and exit with code 2.

    Args:
        exc: the StorageError (or subclass) that was raised.
    """
    err_console.print(str(exc))
    sys.exit(2)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


@click.group(invoke_without_command=True)
@click.option("--version", is_flag=True, help="Show version and exit.")
@click.pass_context
def main(ctx: click.Context, version: bool) -> None:
    """devlog — a terminal-based developer journal."""
    if version:
        click.echo(f"devlog, version {VERSION}")
        ctx.exit(0)
        return
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        ctx.exit(0)


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


@main.command()
@click.argument("message")
@click.option("--tag", "-t", multiple=True, help="Attach tags (repeatable).")
@click.option("--quiet", "-q", is_flag=True, help="Suppress output.")
def add(message: str, tag: Tuple[str, ...], quiet: bool) -> None:
    """Add a new journal entry."""
    # --- validate message ---------------------------------------------------
    if not message:
        err_console.print("Error: MESSAGE cannot be empty.")
        sys.exit(1)

    # --- validate tags -------------------------------------------------------
    try:
        norm_tags = _validate_tags(tag)
    except click.UsageError as exc:
        err_console.print(str(exc))
        sys.exit(1)

    # --- build entry ---------------------------------------------------------
    ts = datetime.datetime.now(tz=datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    entry = Entry(
        id=str(uuid.uuid4()),
        message=message,
        tags=norm_tags,
        created_at=ts,
    )

    # --- persist -------------------------------------------------------------
    try:
        storage.add_entry(entry)
    except StorageError as exc:
        _handle_storage_error(exc)

    # --- output --------------------------------------------------------------
    if not quiet:
        short_id = entry.id[:8]
        tags_display = ", ".join(entry.tags) if entry.tags else "(none)"
        body = (
            f"[green]✔[/green] Entry added [id: {short_id}]\n"
            f"  Date : {entry.created_at}\n"
            f"  Tags : {tags_display}\n"
            f"  Note : {entry.message}"
        )
        console.print(Panel(body, border_style="green"))


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
        err_console.print("Error: --limit must be a positive integer.")
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
            console.print("No entries match your filters.")
        else:
            console.print("No entries found.")
        return

    table = _entries_table(shown, total)
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
        err_console.print("Error: --limit must be a positive integer.")
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
        console.print(f'No entries matched "{query}".')
        return

    table = _entries_table(shown, total, highlight_query=query)
    console.print(table)


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
        err_console.print("Warning: No entries to export.")
        sys.exit(0)

    def _entry_md(entry: Entry) -> str:
        short_id = entry.id[:8]
        date_str = _format_dt(entry.created_at)
        tags_str = ", ".join(entry.tags) if entry.tags else "(none)"
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
            progress = Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                TimeElapsedColumn(),
                console=Console(stderr=True),
            )
            with progress:
                task = progress.add_task("Exporting…", total=len(filtered))
                with open(output, "w", encoding="utf-8") as fh:
                    for entry in filtered:
                        fh.write(_entry_md(entry))
                        progress.advance(task)

            err_console.print(
                f"[green]✔[/green] Exported {len(filtered)} entries to {output}"
            )
    except (PermissionError, OSError):
        err_console.print(
            f"Error: Cannot write to {output}. Check the path and permissions."
        )
        sys.exit(2)