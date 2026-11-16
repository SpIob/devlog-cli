"""devlog CLI — The tag commands: ``tags``, ``tag``, ``rename-tag`` and
``merge-tag``.
"""

from __future__ import annotations

import json
import sys

import click

from devlog import (
    _iso,
    _tagops,
    storage,
    ui,
)
from devlog.cli._shared import (
    _handle_storage_error,
    _load_entries_or_exit,
    _require_positive_int,
    _tag_delete_impl,
    _tag_show_impl,
    _validate_tags_or_exit,
)
from devlog.models import Entry
from devlog.storage import StorageError

# Console instances (ui re-exports) so command bodies read `console.print`
console = ui.console
err_console = ui.err_console


# ---------------------------------------------------------------------------
# tags
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# rename-tag
# ---------------------------------------------------------------------------

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


def register(main) -> None:
    """Attach the tags commands to *main*."""
    main.command()(tags)
    main.command()(tag)
    main.command("rename-tag")(rename_tag)
    main.command("merge-tag")(merge_tag)
