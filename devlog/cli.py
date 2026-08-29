"""Command-line interface for devlog.

This module is intentionally thin: it parses arguments via Click,
delegates persistence to ``storage``, and delegates *all* rendering to
``ui``. Keeping rendering in one place is what guarantees a consistent
look-and-feel across commands.
"""

import datetime
import dataclasses
import json
import os
import re
import shutil
import sys
import uuid
from pathlib import Path
from typing import Tuple

import click
from rich.text import Text

try:
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore[import, no-redef]

from devlog import storage
from devlog import themes
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


# Supported --since / --until input forms. ``None`` means "no bound".
_RELATIVE_DAY_RE = re.compile(r"^(\d+)\s*d$")
_RELATIVE_WEEK_RE = re.compile(r"^(\d+)\s*w$")


def _parse_date_bound(value: str, *, is_upper: bool = False) -> datetime.datetime:
    """Parse a user-supplied date bound into a UTC ``datetime``.

    Accepted forms (case-insensitive, whitespace stripped):

        * ``YYYY-MM-DD``                 — date only, midnight UTC.
        * ``YYYY-MM-DDTHH:MM``           — ISO local form, treated as UTC.
        * ``YYYY-MM-DDTHH:MM:SS``        — ISO local form, treated as UTC.
        * ``YYYY-MM-DD HH:MM`` / ``...:SS`` — same, with a space separator.
        * ``YYYY-MM-DDTHH:MM:SSZ`` / with timezone — explicit UTC.
        * ``today``                      — today at midnight UTC.
        * ``yesterday``                  — yesterday at midnight UTC.
        * ``Nd`` / ``Nw``                — N days/weeks ago at midnight UTC
                                          (e.g. ``7d``).

    Args:
        value:    the raw string from the user.
        is_upper: when True, a date-only value is interpreted as the
                  *end* of that day (23:59:59) rather than midnight. This
                  lets ``--until 2025-01-15`` mean "include 2025-01-15".

    Returns:
        A timezone-aware ``datetime`` in UTC.

    Raises:
        click.BadParameter: on any unparseable input. The message lists
            the supported formats so users can self-correct.
    """
    if not value or not value.strip():
        raise click.BadParameter("date bound cannot be empty")

    raw = value.strip()

    # Natural phrases
    lower = raw.lower()
    if lower in ("today", "now"):
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        if is_upper and lower == "today":
            return now.replace(hour=23, minute=59, second=59, microsecond=0)
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if lower == "yesterday":
        d = datetime.datetime.now(tz=datetime.timezone.utc).date() - datetime.timedelta(days=1)
        dt = datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.timezone.utc)
        if is_upper:
            return dt.replace(hour=23, minute=59, second=59)
        return dt

    # Relative: 7d, 2w
    m = _RELATIVE_DAY_RE.match(lower)
    if m:
        days = int(m.group(1))
        d = datetime.datetime.now(tz=datetime.timezone.utc).date() - datetime.timedelta(days=days)
        dt = datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.timezone.utc)
        if is_upper:
            return dt.replace(hour=23, minute=59, second=59)
        return dt
    m = _RELATIVE_WEEK_RE.match(lower)
    if m:
        weeks = int(m.group(1))
        d = (
            datetime.datetime.now(tz=datetime.timezone.utc).date()
            - datetime.timedelta(weeks=weeks)
        )
        dt = datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.timezone.utc)
        if is_upper:
            return dt.replace(hour=23, minute=59, second=59)
        return dt

    # Date only: YYYY-MM-DD
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        dt = datetime.datetime.strptime(raw, "%Y-%m-%d").replace(
            tzinfo=datetime.timezone.utc
        )
        if is_upper:
            return dt.replace(hour=23, minute=59, second=59)
        return dt

    # Full ISO with optional Z / +00:00 / +HH:MM
    parseable = raw.replace(" ", "T")
    if parseable.endswith("Z"):
        parseable = parseable[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(parseable)
    except ValueError:
        raise click.BadParameter(
            f'Invalid date "{value}". Supported formats: '
            '"YYYY-MM-DD", "YYYY-MM-DDTHH:MM", "YYYY-MM-DDTHH:MM:SSZ", '
            '"today", "yesterday", "Nd" (N days ago), "Nw" (N weeks ago).'
        )
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    else:
        dt = dt.astimezone(datetime.timezone.utc)
    return dt


def _filter_by_date(
    entries: list[Entry],
    since: datetime.datetime | None,
    until: datetime.datetime | None,
) -> list[Entry]:
    """Return entries with ``created_at`` in ``[since, until]``.

    Both bounds are inclusive. ``None`` means "no bound on that side".
    Entries whose ``created_at`` is unparseable are dropped when either
    bound is supplied, since a date filter is meaningless for them.

    Args:
        entries: full list of Entry objects.
        since:   lower bound (inclusive) or None.
        until:   upper bound (inclusive) or None.

    Returns:
        list[Entry]: filtered, in original order.
    """
    if since is None and until is None:
        return entries

    def _within(entry: Entry) -> bool:
        try:
            ts = datetime.datetime.fromisoformat(
                entry.created_at.replace("Z", "+00:00")
            )
        except (ValueError, TypeError, AttributeError):
            return False
        if since is not None and ts < since:
            return False
        if until is not None and ts > until:
            return False
        return True

    return [e for e in entries if _within(e)]


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
        f"[{ui._s('banner_command')}]devlog interactive[/{ui._s('banner_command')}]  ·  "
        "type [bold]help[/bold] for commands, [bold]q[/bold] to quit"
    )

    while True:
        try:
            line = Prompt.ask(
                f"[{ui._s('tags')}]devlog>[/{ui._s('tags')}]"
            ).strip()
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
            # Use Click's standalone command invocation. `mix_stderr=False`
            # was removed in Click 8.2+; the default is `False` now.
            import click.testing

            runner = click.testing.CliRunner()
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
    """Print a one-line description of every command available in the REPL.

    Generated from ``main.list_commands`` so it stays in sync as new
    commands are added. Includes sub-commands of grouped commands
    (``theme``) by name. Aliases (``list``, ``search``) are also
    surfaced as the REPL accepts both forms.
    """
    # Pull the short docstring (first line) for each command and the
    # commands themselves. ``list_commands`` returns every registered
    # command, including those behind a group like ``theme``.
    cmds: list[tuple[str, str]] = []
    for name in sorted(main.list_commands(None)):
        cmd = main.get_command(None, name)
        if cmd is None:
            continue
        if isinstance(cmd, click.Group):
            sub = ", ".join(sorted(cmd.list_commands(None)))
            cmds.append((name, f"{cmd.short_help or ''} (sub: {sub})".strip()))
        else:
            cmds.append((name, (cmd.short_help or "").strip()))

    aliases = [
        ("l", "alias for list"),
        ("s", "alias for search"),
        ("h", "alias for help"),
        ("q", "leave the REPL"),
    ]

    console.print("Available commands:")
    width = max(len(name) for name, _ in cmds + aliases)
    for name, desc in cmds:
        console.print(f"  [bold]{name:<{width}}[/bold]  {desc}")
    for name, desc in aliases:
        console.print(f"  [bold]{name:<{width}}[/bold]  {desc}")


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
@click.option("--since", default=None, help="Only show entries on/after this date (UTC).")
@click.option("--until", default=None, help="Only show entries on/before this date (UTC).")
@click.option("--quiet", "-q", is_flag=True, help="Output raw JSON lines.")
def list_entries(
    tags: Tuple[str, ...],
    limit: int,
    show_all: bool,
    since: str | None,
    until: str | None,
    quiet: bool,
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
    since_dt = _parse_date_bound(since) if since else None
    until_dt = _parse_date_bound(until, is_upper=True) if until else None
    filtered = _filter_by_date(filtered, since_dt, until_dt)
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
@click.option("--since", default=None, help="Only show entries on/after this date (UTC).")
@click.option("--until", default=None, help="Only show entries on/before this date (UTC).")
@click.option("--quiet", "-q", is_flag=True, help="Output raw JSON lines.")
def search(
    query: str,
    tags: Tuple[str, ...],
    limit: int,
    since: str | None,
    until: str | None,
    quiet: bool,
) -> None:
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
    since_dt = _parse_date_bound(since) if since else None
    until_dt = _parse_date_bound(until, is_upper=True) if until else None
    filtered = _filter_by_date(filtered, since_dt, until_dt)
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

    # Determine the final message. If the user explicitly passed -m ""
    # via the CLI, reject it; the editor path is allowed to produce an
    # empty body (the user might be saving a blank note on purpose).
    if message_from_flag and new_message is not None and not new_message.strip():
        ui.print_error("MESSAGE cannot be empty.")
        sys.exit(1)
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
# theme
# ---------------------------------------------------------------------------


@main.group()
def theme() -> None:
    """View or change the active color theme."""


@theme.command("list")
def theme_list() -> None:
    """Print every theme role and its current style."""
    console.print(ui.theme_table())


@theme.command("show")
@click.argument("role", required=False)
def theme_show(role: str | None) -> None:
    """Print the active theme, or the value of a single ROLE.

    When ROLE is omitted, the full palette is dumped as a starter
    ``theme.toml`` to STDOUT (all lines commented out so it is a safe
    template to copy and edit).
    """
    palette = themes.get_active_theme()
    if role:
        if role not in themes.ROLES:
            ui.print_error(
                f'Unknown role "{role}". Run `devlog theme list` to see valid roles.'
            )
            sys.exit(1)
        print(palette[role])
        return
    themes.write_default_theme(sys.stdout)


@theme.command("set")
@click.argument(
    "source",
    type=click.Path(exists=True, dir_okay=False, readable=True),
)
def theme_set(source: str) -> None:
    """Install a theme file as the active theme.

    SOURCE is a path to a ``theme.toml`` file. Its contents are
    validated; unknown roles are ignored with a warning. On success
    the file is copied to the active theme path and the change takes
    effect for the next devlog invocation.
    """
    src = Path(source)
    dst = themes.get_theme_path()

    try:
        raw = themes._parse_file(src)  # noqa: SLF001 - intentional internal use
    except tomllib.TOMLDecodeError as exc:
        ui.print_error(f"Theme file is invalid TOML: {exc}")
        sys.exit(1)
    except OSError as exc:
        ui.print_error(f"Cannot read theme file: {exc}")
        sys.exit(1)

    unknown = sorted(k for k in raw if k not in themes.ROLES)
    for key in unknown:
        ui.print_warning(f"theme role '{key}' is unknown and will be ignored.")

    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(src, dst)
    except OSError as exc:
        ui.print_error(f"Cannot write theme file to {dst}: {exc}")
        sys.exit(1)

    themes.reset_cache()
    active = themes.get_active_theme()
    print(f"Theme installed at {dst} ({len(active)} roles).")


@theme.command("path")
def theme_path() -> None:
    """Print the path to the active theme file."""
    print(themes.get_theme_path())


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
@click.option("--since", default=None, help="Only include entries on/after this date (UTC).")
@click.option("--until", default=None, help="Only include entries on/before this date (UTC).")
@click.option("--quiet", "-q", is_flag=True, help="Output a single JSON summary.")
def stats(since: str | None, until: str | None, quiet: bool) -> None:
    """Show a summary of the journal: total entries, date range, top tags, and a 30-day sparkline."""
    try:
        all_entries = storage.load_entries()
    except StorageError as exc:
        _handle_storage_error(exc)
        return

    since_dt = _parse_date_bound(since) if since else None
    until_dt = _parse_date_bound(until, is_upper=True) if until else None
    all_entries = _filter_by_date(all_entries, since_dt, until_dt)
    # Also drop entries whose created_at cannot be parsed at all, so
    # downstream formatting (which assumes a valid ISO timestamp) never
    # crashes. Other commands go through this filter implicitly via
    # --since/--until; `stats` without date bounds does not, so we apply
    # it unconditionally here.
    all_entries = [e for e in all_entries if _is_valid_iso(e.created_at)]

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
    panel = ui.stats_panel(
        total=total,
        first_iso=first_iso,
        last_iso=last_iso,
        top_tags=top_tags,
        last_30_days=last_30_days,
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
    # Pre-validate NEW *as supplied* so an invalid tag (uppercase
    # letters, spaces, oversize) is rejected with a clear error rather
    # than silently collapsing to the same value as OLD and triggering
    # the "OLD and NEW are the same" no-op path. We deliberately check
    # the raw input here, not a normalised version: tags are stored
    # lowercased but the user's input is what we're validating.
    new_stripped = new.strip()
    if not new_stripped:
        ui.print_error("NEW tag cannot be empty.")
        sys.exit(1)
    try:
        if not TAG_RE.fullmatch(new_stripped):
            raise click.UsageError(
                f'Tag "{new}" contains invalid characters. '
                "Use lowercase letters, numbers, and hyphens only."
            )
        if len(new_stripped) > MAX_TAG_LENGTH:
            raise click.UsageError(
                f'Tag "{new}" exceeds maximum length of {MAX_TAG_LENGTH} characters.'
            )
    except click.UsageError as exc:
        ui.print_error(str(exc))
        sys.exit(1)

    # Now normalise through the shared validator (handles dedup etc.)
    new_normalized = _validate_tags((new,))
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
        line.append("DRY RUN: ", style=ui._bold("warning_text"))
        line.append(
            f"would update {len(affected)} entr{'y' if len(affected) == 1 else 'ies'}: ",
            style=ui._s("warning_text"),
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
        line.append("✔ ", style=ui._bold("success_title"))
        line.append(
            f"Renamed {old_normalized} → {new_tag} in {len(affected)} entr{'y' if len(affected) == 1 else 'ies'}.",
            style=ui._s("success_border"),
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
            # Try to sniff format from the first non-blank character so
            # users can pipe from stdin or import extensionless files.
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    sniff = fh.read(64)
            except OSError:
                sniff = ""
            stripped = sniff.lstrip()
            if stripped.startswith("{"):
                fmt = "json"
            elif stripped.startswith("#"):
                fmt = "markdown"
            else:
                ui.print_error(
                    f'Cannot auto-detect format for "{path}". '
                    "Use --format=json or --format=markdown."
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
        unreadable_rows = 0
        for item in raw_entries:
            if not isinstance(item, dict):
                unreadable_rows += 1
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
                unreadable_rows += 1
                continue
            candidates.append(e)
    else:
        candidates = _parse_markdown_export(content)
        unreadable_rows = 0  # markdown parser returns only valid entries

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
        # Preserve a stable id from the source when present. Only mint
        # a fresh uuid if the source row is missing one. This makes
        # re-imports and backup → restore → import round-trips
        # idempotent at the id level, so users can cross-reference
        # entries by short id.
        if not cand.id:
            cand.id = str(uuid.uuid4())
        to_add.append(cand)
        existing_ids.add(cand.id)
        existing_fps.add((cand.created_at, cand.message))

    if dry_run:
        line = Text()
        line.append("DRY RUN: ", style=ui._bold("warning_text"))
        line.append(
            f"would import {len(to_add)} {ui._plural_noun(len(to_add), 'entry')}, "
            f"skip {skipped} duplicate{ui.plural_s(skipped)}. "
            if to_add or skipped
            else "would import 0 entries, skip 0 duplicates. ",
            style=ui._s("warning_text"),
        )
        if unreadable_rows:
            line.append(
                f"Ignored {unreadable_rows} unreadable row{ui.plural_s(unreadable_rows)}.",
                style=ui._s("warning_text"),
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
            line.append("✔ ", style=ui._bold("success_title"))
            line.append(
                f"Imported {len(to_add)} {ui._plural_noun(len(to_add), 'entry')}, "
                f"skipped {skipped} duplicate{ui.plural_s(skipped)}. ",
                style=ui._s("success_border"),
            )
            if unreadable_rows:
                line.append(
                    f"Ignored {unreadable_rows} unreadable row{ui.plural_s(unreadable_rows)}.",
                    style=ui._s("success_border"),
                )
            console.print(line)
        else:
            # No new imports — surface the skip count so the user knows it was a no-op, not a bug.
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
# repair / backup / restore / doctor
# ---------------------------------------------------------------------------


# Reachable from `devlog repair`: the raw JSON file the validator reads.
# Kept module-level so the unit tests can target it directly.
def _read_raw_entries() -> object:
    """Return the parsed JSON payload at the storage path, or raise.

    Raises:
        FileNotFoundError: when the file does not yet exist.
        storage.CorruptedStorageError: when the file contains invalid JSON.
        storage.StoragePermissionError: when the file is unreadable.
    """
    import json as _json

    path = storage.get_storage_path()
    if not path.exists():
        raise FileNotFoundError(path)
    try:
        with path.open("r", encoding="utf-8") as fh:
            return _json.load(fh)
    except PermissionError as exc:
        raise storage.StoragePermissionError(path, "read") from exc
    except OSError as exc:
        raise storage.StoragePermissionError(path, "read") from exc
    except _json.JSONDecodeError as exc:
        raise storage.CorruptedStorageError(path) from exc


def _coerce_entry(item: dict) -> Entry | None:
    """Best-effort construction of an ``Entry`` from a parsed item.

    Returns ``None`` if any required field is missing or the wrong type.
    The returned ``Entry`` will *not* have a valid ``created_at`` (we
    use a placeholder) — the caller is expected to either fix or drop
    these rows. This helper is only used by ``devlog repair``.
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


def _build_repair_plan(raw: object) -> tuple[list[Entry], list[storage.Issue]]:
    """Split a raw payload into (repaired_entries, remaining_issues).

    Strategy:

        * Entries that the validator cannot even build from the item
          (missing id, wrong root types, etc.) are dropped — counted as
          ``bad_item`` issues.
        * Entries with valid shape but a bad ``created_at`` or bad tags
          are dropped — those issues are reported individually.
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
    issues = storage.validate_entries(raw)

    if not isinstance(raw, dict) or not isinstance(raw.get("entries"), list):
        return [], issues

    kept: list[Entry] = []
    seen_ids: set[str] = set()
    for _i, item in enumerate(raw["entries"]):
        entry = _coerce_entry(item) if isinstance(item, dict) else None
        if entry is None:
            continue  # already covered by `bad_item` / `missing_field` issue
        if entry.id in seen_ids:
            continue  # duplicate; issue already reported
        # Re-check created_at and tags so we drop malformed-but-buildable rows.
        if not entry.created_at or not _is_valid_iso(entry.created_at):
            continue
        if not all(_is_valid_tag(t) for t in entry.tags):
            continue
        if entry.updated_at is not None and not _is_valid_iso(entry.updated_at):
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


def _is_valid_iso(value: str) -> bool:
    return bool(storage._is_valid_iso_timestamp(value))


def _is_valid_tag(t: str) -> bool:
    return bool(storage._TAG_RE.fullmatch(t)) and len(t) <= storage._MAX_TAG_LENGTH


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

    Validates every entry in ``entries.json`` against the schema, then
    either reports the issues (``--dry-run``) or rewrites the file with
    the malformed rows removed. The original file is preserved in
    ``backups/`` whenever a write happens unless ``--no-backup`` is set.
    """
    try:
        raw = _read_raw_entries()
    except FileNotFoundError:
        if not quiet:
            ui.print_info("No journal yet — nothing to repair.")
        return
    except storage.CorruptedStorageError as exc:
        ui.print_error(
            f"Cannot repair: {exc}. Restore from a backup with `devlog restore`."
        )
        sys.exit(2)
    except storage.StoragePermissionError as exc:
        _handle_storage_error(exc)
        return

    kept, issues = _build_repair_plan(raw)
    raw_count = len(raw.get("entries", [])) if isinstance(raw, dict) else 0
    dropped = max(0, raw_count - len(kept))

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
            backup_filename = storage.default_backup_filename()
            backup_path = str(backups_dir / backup_filename)
            import json as _json

            with open(backup_path, "w", encoding="utf-8") as fh:
                _json.dump(raw, fh, indent=2, ensure_ascii=False)
        except (OSError, PermissionError) as exc:
            ui.print_error(f"Could not write backup: {exc}")
            sys.exit(2)

    if not dry_run and not yes:
        click.confirm(
            f"Repair will drop {dropped} entr{'y' if dropped == 1 else 'ies'}. Continue?",
            default=False,
            abort=True,
        )

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
    try:
        entries = storage.load_entries()
    except StorageError as exc:
        _handle_storage_error(exc)
        return

    if output_path:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
    else:
        backups_dir = storage.get_backups_dir()
        backups_dir.mkdir(parents=True, exist_ok=True)
        destination = backups_dir / storage.default_backup_filename()

    import json as _json

    try:
        with destination.open("w", encoding="utf-8") as fh:
            _json.dump(
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
    import json as _json

    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = _json.load(fh)
    except (OSError, PermissionError) as exc:
        ui.print_error(f"Cannot read {path}: {exc}")
        sys.exit(2)
    except _json.JSONDecodeError as exc:
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
    new_entries, plan_issues = _build_repair_plan(data)
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
                f"DRY RUN: would restore {len(new_entries)} entr{'y' if len(new_entries) == 1 else 'ies'} from {path}."
            )
        return

    try:
        storage.save_entries(new_entries)
    except StorageError as exc:
        _handle_storage_error(exc)
        return

    if not quiet:
        line = Text()
        line.append("✔ ", style=ui._bold("success_title"))
        line.append(
            f"Restored {len(new_entries)} entr{'y' if len(new_entries) == 1 else 'ies'} from ",
            style=ui._s("success_border"),
        )
        line.append(path, style="bold")
        console.print(line)


@main.command()
@click.option("--quiet", "-q", is_flag=True, help="Output a single JSON health summary.")
def doctor(quiet: bool) -> None:
    """Check the journal store for corruption and report basic health stats."""
    import json as _json
    from datetime import datetime, timezone

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

    # Writable check: can we create the parent dir?
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Try a tiny temp file inside the dir to confirm write access
        probe = path.parent / ".devlog-doctor-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        report["writable"] = True
    except (OSError, PermissionError):
        report["writable"] = False
        report["ok"] = False

    if not path.exists():
        if quiet:
            print(_json.dumps(report))
        else:
            report["issues"] = []
            console.print(ui.doctor_report(report))
        if not report["ok"]:
            sys.exit(2)
        return

    report["exists"] = True
    try:
        report["size_bytes"] = path.stat().st_size
    except OSError:
        report["size_bytes"] = 0

    try:
        raw = _read_raw_entries()
    except storage.CorruptedStorageError:
        report["ok"] = False
        report["issues"] = [
            {
                "kind": "corrupt_json",
                "message": "entries.json is not valid JSON",
                "entry_id": None,
            }
        ]
        if quiet:
            print(_json.dumps(report))
        else:
            console.print(ui.doctor_report(report))
        sys.exit(2)
        return  # unreachable
    except storage.StoragePermissionError as exc:
        _handle_storage_error(exc)
        return

    issues = storage.validate_entries(raw)
    report["issues"] = [
        {"kind": i.kind, "message": i.message, "entry_id": i.entry_id}
        for i in issues
    ]
    if issues:
        report["ok"] = False

    entries: list[Entry] = []
    if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
        for item in raw["entries"]:
            entry = _coerce_entry(item) if isinstance(item, dict) else None
            if entry is not None and _is_valid_iso(entry.created_at):
                entries.append(entry)
    report["entry_count"] = len(entries)

    if entries:
        entries.sort(key=lambda e: e.created_at, reverse=True)
        try:
            most_recent = datetime.fromisoformat(
                entries[0].created_at.replace("Z", "+00:00")
            )
            now = datetime.now(tz=timezone.utc)
            report["days_since_last"] = (now.date() - most_recent.date()).days
        except (ValueError, TypeError):
            report["days_since_last"] = None

        by_length = sorted(entries, key=lambda e: len(e.message), reverse=True)[:3]
        report["top_messages"] = [
            (e.id[:8] + "…", len(e.message)) for e in by_length
        ]

    if quiet:
        print(_json.dumps(report, default=str))
    else:
        console.print(ui.doctor_report(report))

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
    local commands="add show edit delete list search today tail tags stats theme rename-tag import completions export repair backup restore doctor"
    if [[ ${cword} -eq 1 ]]; then
        COMPREPLY=($(compgen -W "${commands}" -- "${cur}"))
        return
    fi
    case "${words[1]}" in
        edit|delete|show) COMPREPLY=($(compgen -W "$(devlog list --quiet 2>/dev/null | python3 -c 'import sys,json
for line in sys.stdin: print(json.loads(line)["id"][:8])')" -- "${cur}")) ;;
        list|search|tail|export) COMPREPLY=($(compgen -W "--tag --limit --all --quiet --since --until --format --output" -- "${cur}")) ;;
        theme) COMPREPLY=($(compgen -W "list show set path" -- "${cur}")) ;;
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
        'theme:View or change the active color theme'
        'stats:Summarize the journal'
        'rename-tag:Rename a tag across all entries'
        'import:Import entries from a file'
        'completions:Print a shell completion script'
        'export:Export entries to a Markdown file'
        'repair:Inspect and repair the on-disk journal store'
        'backup:Write a timestamped copy of the journal'
        'restore:Restore the journal from a backup file'
        'doctor:Check the journal store for corruption'
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
complete -c devlog -n "__fish_use_subcommand" -a "repair" -d "Inspect and repair the on-disk store"
complete -c devlog -n "__fish_use_subcommand" -a "backup" -d "Write a timestamped backup"
complete -c devlog -n "__fish_use_subcommand" -a "restore" -d "Restore from a backup file"
complete -c devlog -n "__fish_use_subcommand" -a "doctor" -d "Check store health"
"""


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
    tags: Tuple[str, ...],
    since: str | None,
    until: str | None,
    quiet: bool,
) -> None:
    """Export entries to a Markdown or JSON file."""
    try:
        all_entries = storage.load_entries()
    except StorageError as exc:
        _handle_storage_error(exc)
        return

    filtered = _filter_by_tags(all_entries, tags)
    since_dt = _parse_date_bound(since) if since else None
    until_dt = _parse_date_bound(until, is_upper=True) if until else None
    filtered = _filter_by_date(filtered, since_dt, until_dt)
    filtered.sort(key=lambda e: e.created_at, reverse=True)

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
        if fmt_resolved == "json":
            import dataclasses as _dc

            with open(output, "w", encoding="utf-8") as fh:
                json.dump(
                    {"entries": [_dc.asdict(e) for e in filtered]},
                    fh,
                    indent=2,
                    ensure_ascii=False,
                )
            if not quiet:
                line = Text()
                line.append("✔ ", style=ui._bold("success_title"))
                line.append(
                    f"Exported {len(filtered)} {ui.pluralize(len(filtered), 'entry')} to ",
                    style=ui._s("success_border"),
                )
                line.append(output, style="bold")
                err_console.print(line)
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
            line = Text()  # local alias to keep imports tidy
            line.append("✔ ", style=ui._bold("success_title"))
            line.append(
                f"Exported {len(filtered)} {ui.pluralize(len(filtered), 'entry')} to ",
                style=ui._s("success_border"),
            )
            line.append(output, style="bold")
            err_console.print(line)
    except (PermissionError, OSError):
        ui.print_error(f"Cannot write to {output}. Check the path and permissions.")
        sys.exit(2)


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
