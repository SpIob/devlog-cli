"""devlog CLI — The ``import`` and ``export`` commands.
"""

from __future__ import annotations

import sys

import click

from devlog import (
    _io,
    storage,
    ui,
)

# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

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
    from devlog import _importers, _io

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
# export
# ---------------------------------------------------------------------------

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


def register(main) -> None:
    """Attach the transfer commands to *main*."""
    main.command()(import_cmd)
    main.command()(export)
