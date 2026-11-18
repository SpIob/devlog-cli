"""devlog CLI — The maintenance commands: ``repair``, ``backup``,
``restore``, ``undo`` and ``doctor``.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import click

from devlog import (
    storage,
    ui,
)
from devlog.cli._shared import (
    _doctor_collect_issues,
    _doctor_days_since_last,
    _doctor_probe_size,
    _doctor_probe_writable,
    _doctor_top_messages,
    _emit_doctor_report,
    _handle_storage_error,
    _load_entries_or_exit,
)
from devlog.storage import StorageError

# Console instances (ui re-exports) so command bodies read `console.print`
console = ui.console
err_console = ui.err_console


# ---------------------------------------------------------------------------
# repair / backup / restore / doctor
# ---------------------------------------------------------------------------


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
    # Backend-agnostic: the facade reads the raw payload from whichever
    # store is active — the JSON file (with right-truncation recovery)
    # or the live SQLite table. An absent store is reported as "no
    # journal yet" and is never created as a side effect.
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
        ui.print_error(f"Cannot repair: {msg}. Restore from a backup with `devlog restore`.")
        sys.exit(2)
    except storage.StorageError as exc:
        # Any other storage failure (unreadable file, sqlite lock
        # timeout) is actionable on its own — handled message, exit 2.
        _handle_storage_error(exc)
        return

    kept, issues = storage.build_repair_plan(raw)
    raw_entries = raw.get("entries") if isinstance(raw, dict) else None
    raw_count = len(raw_entries) if isinstance(raw_entries, list) else 0
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
                    raw_bytes = storage.get_store_path().read_bytes()
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
            prompt = f"Repair will drop {dropped} {ui.plural_noun(dropped, 'entry')}. Continue?"
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
                    "Trimmed trailing corruption; the recoverable entries have been rewritten."
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
    skipped_issues = [iss for iss in plan_issues if iss.kind not in ("bad_root", "bad_field")]

    if not new_entries:
        ui.print_info("Backup contains no valid entries; nothing to restore.")
        return

    if skipped_issues and not quiet:
        for iss in skipped_issues:
            short = (
                (iss.entry_id[:8] + "…")
                if iss.entry_id and len(iss.entry_id) > 8
                else (iss.entry_id or f"#{iss.index}")
            )
            ui.print_warning(f"Skipped during restore: [{short}] {iss.message}")

    # Backend-agnostic: the facade names the active store (entries.json
    # or entries.db) so the overwrite warning points at the right file.
    current_path = storage.get_store_path()
    if current_path.exists() and not yes and not dry_run:
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


@click.option("--quiet", "-q", is_flag=True, help="Output a single JSON health summary.")
def doctor(quiet: bool) -> None:
    """Check the journal store for corruption and report basic health stats."""
    # Backend-agnostic: the facade names the active store, and each
    # backend describes its own failure mode via `corrupt_issue()`, so
    # this report shape is shared by the JSON and SQLite backends.
    path = storage.get_store_path()
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
    except storage.StoragePermissionError as exc:
        _handle_storage_error(exc)
        return
    except storage.StorageError as exc:
        # The active backend names its own corruption kind and message
        # ("corrupt_json" / "corrupt_db"); the report shape is shared.
        report["ok"] = False
        report["issues"] = [storage.corrupt_issue(exc)]
        _emit_doctor_report(report, quiet=quiet)
        sys.exit(2)

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


def register(main) -> None:
    """Attach the maintenance commands to *main*."""
    main.command()(repair)
    main.command()(backup)
    main.command()(restore)
    main.command()(undo)
    main.command()(doctor)
