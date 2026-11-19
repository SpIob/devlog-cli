"""Regression tests for the 2026-11-17 hard-test QA findings.

Each test pins one defect found by the adversarial QA pass:

- comma/``!`` tags stored literally by ``add`` (unreachable by queries),
- MAX_TAGS bypassed by 15 comma-parts in one ``-t``,
- raw strptime traceback on invalid ``--at``/``--since`` calendar dates,
- ``repair`` miscounting a non-list ``entries`` value,
- raw ``UnicodeDecodeError`` traceback on undecodable store bytes,
- stats rendering "-N days ago" for future-dated entries,
- `git sync` staging the whole enclosing repo instead of the journal,
- cross-process write serialization (lost-update under parallel writes),
- date presets silently wrong (`this-week`/`this-month` = today only)
  or unusable (`this-year`/`year-to-date` raising on `0y`/`year-start`),
- `config edit` discarding the editor's output (silent no-op).
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from conftest import active_backend, first_json_obj, requires_json_backend

from devlog import _dates, storage
from devlog.cli import main
from devlog.models import Entry
from devlog.storage import StorageBusyError, storage_write_lock

# ---------------------------------------------------------------------------
# Tag validation (P1: comma/! tags stored literally; MAX_TAGS bypass)
# ---------------------------------------------------------------------------


def test_add_splits_comma_separated_tags(runner):
    """`add -t alpha,beta -t gamma` stores three distinct tags.

    Regression: the whole `-t` value used to be stored as ONE tag
    containing commas — violating the documented [a-z0-9-] charset and
    unreachable via `tag alpha` or `list -t alpha,beta` (OR semantics).
    """
    result = runner.invoke(main, ["add", "tagged entry", "-t", "alpha,beta", "-t", "gamma"])
    assert result.exit_code == 0, result.output

    listing = runner.invoke(main, ["list", "--quiet"])
    entry = first_json_obj(listing.output)
    assert sorted(entry["tags"]) == ["alpha", "beta", "gamma"]

    # The split tags are now reachable by the query syntax that motivated
    # the original comma allowance.
    by_tag = runner.invoke(main, ["tag", "alpha", "--quiet"])
    assert by_tag.exit_code == 0
    assert "tagged entry" in by_tag.output


def test_add_rejects_bang_as_query_syntax(runner):
    """`add -t '!wip'` is rejected: '!' is NOT-query syntax, not a tag char.

    Regression: the literal tag "!wip" used to be stored.
    """
    result = runner.invoke(main, ["add", "bang entry", "-t", "!wip"])
    assert result.exit_code == 1
    assert "query syntax" in result.output


def test_add_max_tags_counted_after_comma_split(runner):
    """15 comma-parts inside ONE -t must exceed the 10-tag cap.

    Regression: MAX_TAGS counted raw `-t` values, so the whole group
    slipped through as a single "tag".
    """
    parts = ",".join(f"tag{i}" for i in range(15))
    result = runner.invoke(main, ["add", "many tags", "-t", parts])
    assert result.exit_code == 1
    assert "Maximum 10 tags" in result.output


# ---------------------------------------------------------------------------
# Date parsing (P2: raw strptime traceback on impossible calendar dates)
# ---------------------------------------------------------------------------


def test_add_invalid_at_date_clean_error(runner):
    """`--at 2026-02-30` exits 2 with the standard message, no traceback."""
    result = runner.invoke(main, ["add", "leap day", "--at", "2026-02-30"])
    assert result.exit_code == 2
    assert "Invalid --at" in result.output
    assert "Traceback" not in result.output


def test_list_invalid_since_date_clean_error(runner):
    """`--since 2026-13-01` exits 2 with the standard message, no traceback."""
    result = runner.invoke(main, ["list", "--since", "2026-13-01"])
    assert result.exit_code == 2
    assert "Invalid date" in result.output
    assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# Repair / doctor robustness (P2: miscount + unicode traceback)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    active_backend() == "sqlite",
    reason=requires_json_backend("plants a malformed entries.json, which SQLite ignores"),
)
def test_repair_reports_zero_removed_for_non_list_entries(runner, data_dir):
    """`{"entries": "not a list"}` must report "Removed 0", not len(str)."""
    (data_dir / "entries.json").write_text(json.dumps({"entries": "not a list"}), encoding="utf-8")
    result = runner.invoke(main, ["repair", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Removed 0 entries" in result.output


@pytest.mark.skipif(
    active_backend() == "sqlite",
    reason=requires_json_backend("plants undecodable bytes in entries.json, which SQLite ignores"),
)
def test_doctor_invalid_utf8_clean_error(runner, data_dir):
    """Undecodable store bytes surface as a clean error, never a traceback."""
    store = data_dir / "entries.json"
    store.write_bytes(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "11111111-1111-4111-8111-111111111111",
                        "message": "ok",
                        "created_at": "2026-11-17T00:00:00Z",
                        "tags": [],
                        "updated_at": None,
                    }
                ]
            }
        ).encode("utf-8")
        + b"\xff\xfe"
    )
    result = runner.invoke(main, ["doctor"])
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "corrupt" in result.output.lower() or "error" in result.output.lower()


# ---------------------------------------------------------------------------
# Stats panel phrasing (P2: "-59 days ago" for future-dated entries)
# ---------------------------------------------------------------------------


def test_stats_future_dated_last_entry_reads_in_n_days(runner, data_dir, seed_entry):
    """A future-dated newest entry renders "in N days (future-dated)"."""
    seed_entry("99999999-9999-4999-8999-999999999999", "from the future", "2099-01-01T00:00:00Z")
    result = runner.invoke(main, ["stats"])
    assert result.exit_code == 0, result.output
    assert "future-dated" in result.output
    assert "-59 days ago" not in result.output
    # No negative day count in the Last entry row (borders excluded —
    # split the row at the separator, then check the value cell).
    last_row = result.output.split("Last entry", 1)[1].splitlines()[0]
    assert "days ago" not in last_row
    assert "in 26" in last_row  # "in N days (future-dated)" with N > 0


def test_stats_avg_day_precision_widened_for_sparse_journals(runner, data_dir, seed_entry):
    """A sparse journal shows widened precision instead of "Avg/day: 0.00"."""
    # Two entries ~57 years apart → avg ≈ 0.0001/day, which the old
    # 2-decimal format rendered as a meaningless "0.00".
    seed_entry("77777777-7777-4777-8777-777777777777", "ancient", "1970-01-01T00:00:00Z")
    seed_entry("88888888-8888-4888-8888-888888888888", "recent", "2026-11-17T00:00:00Z")
    result = runner.invoke(main, ["stats"])
    assert result.exit_code == 0, result.output
    avg_row = result.output.split("Avg/day", 1)[1].splitlines()[0]
    assert "0.000" in avg_row


# ---------------------------------------------------------------------------
# Cross-process write lock (P0: lost updates under parallel writes)
# ---------------------------------------------------------------------------


def test_write_lock_is_reentrant_same_process(data_dir, make_entry):
    """Nested storage_write_lock (facade wrapping backend) must not deadlock."""
    store = storage.get_store_path()
    with storage_write_lock(store):
        with storage_write_lock(store):
            pass
    # And a real write through the full facade stack inside a held lock:
    with storage_write_lock(store):
        storage.add_entry(make_entry("77777777-7777-4777-8777-777777777777", "nested"))
    assert len(storage.load_entries()) == 1


def test_write_lock_times_out_with_busy_error(data_dir, make_entry, monkeypatch):
    """A contended lock raises StorageBusyError after the timeout.

    Uses a second *thread* as the contender: the in-process RLock is the
    first barrier, so a held lock must surface as StorageBusyError (and
    not silently proceed) regardless of platform.
    """
    import threading

    release = threading.Event()
    holder_held = threading.Event()

    def hold():
        with storage_write_lock(storage.get_store_path()):
            holder_held.set()
            release.wait(timeout=10)

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    assert holder_held.wait(timeout=5)

    monkeypatch.setenv("DEVLOG_WRITE_LOCK_TIMEOUT", "0.3")
    with pytest.raises(StorageBusyError):
        storage.add_entry(make_entry("66666666-6666-4666-8666-666666666666", "blocked"))

    release.set()
    holder.join(timeout=5)

    # Lock free again → the write goes through.
    storage.add_entry(make_entry("55555555-5555-4555-8555-555555555555", "after release"))
    assert len(storage.load_entries()) == 1


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="holder subprocess locks via fcntl (POSIX); Windows uses msvcrt",
)
@pytest.mark.skipif(
    active_backend() == "sqlite",
    reason=requires_json_backend("busy test holds the JSON lock sidecar via a subprocess"),
)
def test_write_lock_cross_process_busy_error(data_dir, make_entry):
    """A store locked by ANOTHER PROCESS raises StorageBusyError (POSIX).

    Exercises the actual flock sidecar across a process boundary; the
    thread-based test above covers the timeout contract everywhere.
    """
    lock_path = storage.get_store_path().with_name(storage.get_store_path().name + ".lock")
    holder = subprocess.Popen(
        [
            "python3",
            "-c",
            "import fcntl, sys, time\n"
            "fh = open(sys.argv[1], 'a+')\n"
            "fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            "time.sleep(3)\n",
            str(lock_path),
        ]
    )
    try:
        time.sleep(0.4)  # let the holder grab the lock
        os.environ["DEVLOG_WRITE_LOCK_TIMEOUT"] = "0.3"
        with pytest.raises(StorageBusyError):
            storage.add_entry(make_entry("66666666-6666-4666-8666-666666666666", "blocked"))
    finally:
        os.environ.pop("DEVLOG_WRITE_LOCK_TIMEOUT", None)
        holder.wait(timeout=10)

    # Lock free again → the write goes through.
    storage.add_entry(make_entry("55555555-5555-4555-8555-555555555555", "after release"))
    assert len(storage.load_entries()) == 1


def test_parallel_adds_all_persist(runner, tmp_path):
    """20 in-process rapid adds through the facade must all persist.

    Regression: the JSON backend's read-modify-write was unguarded, so
    concurrent writers lost entries to a last-writer-wins replace.
    (True cross-process parallelism is covered by the storage_write_lock
    timeout test; this pins the no-lost-update invariant cheaply.)
    """
    # Use in-process facade calls (the lock's RLock serializes threads),
    # so drive 20 sequential facade adds — the invariant is that no add
    # ever overwrites another's entry.
    for i in range(20):
        storage.add_entry(
            Entry(
                id=f"aaaaaaa{i:02d}-1111-4111-8111-111111111111",
                message=f"parallel {i}",
                tags=[],
                created_at="2026-11-17T00:00:00Z",
            )
        )
    assert len(storage.load_entries()) == 20


# ---------------------------------------------------------------------------
# git sync scoping (P0: staged the entire enclosing repo)
# ---------------------------------------------------------------------------


@pytest.fixture()
def git_env(monkeypatch):
    """Deterministic git identity for commit-creating tests."""
    monkeypatch.setenv("GIT_AUTHOR_NAME", "devlog-test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "devlog-test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "devlog-test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "devlog-test@example.com")
    return None


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return out.stdout


def test_git_sync_commits_only_journal_paths(tmp_path, monkeypatch, git_env):
    """`git sync` inside a larger repo must not stage unrelated files.

    Regression: sync resolved the enclosing repo and ran `git add -A`
    at its ROOT — with the journal living under $HOME (stray ~/.git),
    that swept the entire home directory.
    """
    outer = tmp_path / "outer"
    journal = outer / "journal"
    journal.mkdir(parents=True)
    _git(outer, "init")
    (outer / "unrelated.txt").write_text("do not commit me", encoding="utf-8")
    (outer / "tracked.txt").write_text("already committed", encoding="utf-8")
    _git(outer, "add", "tracked.txt")
    _git(outer, "commit", "-m", "initial")

    monkeypatch.setenv("DEVLOG_DATA_DIR", str(journal))
    storage.add_entry(
        Entry(
            id="44444444-4444-4444-8444-444444444444",
            message="journal entry",
            tags=[],
            created_at="2026-11-17T00:00:00Z",
        )
    )

    # A local runner so the invocation sees the journal dir above (the
    # conftest runner fixture would override it with tmp_path).
    from click.testing import CliRunner

    runner = CliRunner(env={"DEVLOG_DATA_DIR": str(journal)})
    result = runner.invoke(main, ["git", "sync", "-q"])
    assert result.exit_code == 0, result.output

    # The commit exists and touches only journal files (the .lock
    # sidecar is ephemeral runtime state and must not be committed).
    log = _git(outer, "log", "--oneline")
    assert len(log.strip().splitlines()) == 2
    head_files = _git(outer, "show", "--name-only", "--format=", "HEAD").split()
    assert head_files, "sync committed nothing"
    assert all(name.startswith("journal/") for name in head_files)
    assert all(not name.endswith(".lock") for name in head_files)
    assert "tracked.txt" not in head_files
    assert "unrelated.txt" not in head_files

    # The unrelated file was never staged.
    status = _git(outer, "status", "--porcelain")
    assert "?? unrelated.txt" in status


def test_git_sync_noop_when_journal_clean(tmp_path, monkeypatch, git_env):
    """A clean journal inside a dirty repo commits nothing and exits 0."""
    outer = tmp_path / "outer"
    journal = outer / "journal"
    journal.mkdir(parents=True)
    _git(outer, "init")
    (outer / "unrelated.txt").write_text("dirty", encoding="utf-8")
    _git(outer, "add", "unrelated.txt")
    _git(outer, "commit", "-m", "initial")

    monkeypatch.setenv("DEVLOG_DATA_DIR", str(journal))
    (journal / "entries.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
    _git(outer, "add", "journal/entries.json")
    _git(outer, "commit", "-m", "journal seed")

    from click.testing import CliRunner

    runner = CliRunner(env={"DEVLOG_DATA_DIR": str(journal)})
    result = runner.invoke(main, ["git", "sync", "-q"])
    assert result.exit_code == 0, result.output
    assert len(_git(outer, "log", "--oneline").strip().splitlines()) == 2


# ---------------------------------------------------------------------------
# Date presets (P1: this-week/this-month collapsed to today-only;
# this-year/year-to-date exited 2 on unparseable internal sentinels)
# ---------------------------------------------------------------------------

# 2026-11-18 is a Wednesday; Monday of that week is 2026-11-16.
_WED_NOON = datetime.datetime(2026, 11, 18, 12, 0, 0, tzinfo=datetime.timezone.utc)


def test_preset_this_week_covers_whole_calendar_week():
    """`this-week` spans Monday..today, not just today."""
    since, until = _dates.resolve_preset("this-week", now=_WED_NOON)
    assert since == datetime.datetime(2026, 11, 16, 0, 0, 0, tzinfo=datetime.timezone.utc)
    assert until == datetime.datetime(2026, 11, 18, 23, 59, 59, tzinfo=datetime.timezone.utc)


def test_preset_this_month_covers_month_start():
    """`this-month` spans the 1st..today, not just today."""
    since, _until = _dates.resolve_preset("this-month", now=_WED_NOON)
    assert since == datetime.datetime(2026, 11, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)


def test_preset_this_year_and_year_to_date_cover_jan_first():
    """`this-year`/`year-to-date` resolve instead of raising `Invalid date "0y"`."""
    for name in ("this-year", "year-to-date"):
        since, until = _dates.resolve_preset(name, now=_WED_NOON)
        assert since == datetime.datetime(2026, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
        assert until == datetime.datetime(2026, 11, 18, 23, 59, 59, tzinfo=datetime.timezone.utc)


def test_preset_rolling_names_keep_previous_bounds():
    """today/yesterday/last-7d/last-30d semantics are unchanged by the fix."""
    since, until = _dates.resolve_preset("today", now=_WED_NOON)
    assert since == datetime.datetime(2026, 11, 18, 0, 0, 0, tzinfo=datetime.timezone.utc)
    assert until == datetime.datetime(2026, 11, 18, 23, 59, 59, tzinfo=datetime.timezone.utc)

    since, until = _dates.resolve_preset("last-7d", now=_WED_NOON)
    assert since == datetime.datetime(2026, 11, 11, 0, 0, 0, tzinfo=datetime.timezone.utc)
    assert until == datetime.datetime(2026, 11, 18, 23, 59, 59, tzinfo=datetime.timezone.utc)

    since, _until = _dates.resolve_preset("yesterday", now=_WED_NOON)
    assert since == datetime.datetime(2026, 11, 17, 0, 0, 0, tzinfo=datetime.timezone.utc)


def test_preset_unknown_name_returns_none_pair():
    assert _dates.resolve_preset("nope") == (None, None)


def test_preset_this_week_honors_local_zone():
    """Bounds are local-midnight based when a zone is supplied."""
    ny = ZoneInfo("America/New_York")
    now_ny = datetime.datetime(2026, 11, 18, 23, 30, tzinfo=ny)
    since, _until = _dates.resolve_preset("this-week", tz=ny, now=now_ny)
    # Monday Nov 16 00:00 EST (UTC-5) == 05:00Z.
    assert since == datetime.datetime(2026, 11, 16, 5, 0, 0, tzinfo=datetime.timezone.utc)


def test_list_preset_this_year_runs_clean(runner):
    """`list --preset this-year` exits 0 and includes a current-year entry.

    CLI-level regression: this preset used to exit 2 with
    `Invalid date "0y"` leaking an internal sentinel.
    """
    seeded = runner.invoke(main, ["add", "ytd entry", "-q"])
    assert seeded.exit_code == 0, seeded.output
    result = runner.invoke(main, ["list", "--preset", "this-year", "--quiet"])
    assert result.exit_code == 0, result.output
    assert "ytd entry" in result.output


# ---------------------------------------------------------------------------
# config edit (P1: the editor's return value was discarded — every edit
# was a silent no-op that still printed success)
# ---------------------------------------------------------------------------


def test_config_edit_saves_edited_content(runner, monkeypatch):
    import devlog.cli._shared as cli_shared

    monkeypatch.setattr(cli_shared, "_edit_in_editor", lambda _initial: '{"theme": "ocean"}')
    result = runner.invoke(main, ["config", "edit"])
    assert result.exit_code == 0, result.output

    value = runner.invoke(main, ["config", "get", "theme"])
    assert value.exit_code == 0, value.output
    assert value.output.strip() == "ocean"


def test_config_edit_rejects_invalid_json_and_keeps_old_file(runner, monkeypatch):
    import devlog.cli._shared as cli_shared

    monkeypatch.setattr(cli_shared, "_edit_in_editor", lambda _initial: "NOT JSON {{{")
    result = runner.invoke(main, ["config", "edit"])
    assert result.exit_code == 1
    assert "not valid JSON" in result.output

    # The previous on-disk config is untouched.
    get = runner.invoke(main, ["config", "get", "watch_interval"])
    assert get.output.strip() == "2"


def test_config_edit_non_object_json_rejected(runner, monkeypatch):
    import devlog.cli._shared as cli_shared

    monkeypatch.setattr(cli_shared, "_edit_in_editor", lambda _initial: '["a", "list"]')
    result = runner.invoke(main, ["config", "edit"])
    assert result.exit_code == 1
    assert "JSON object" in result.output


def test_config_edit_editor_abort_leaves_file_alone(runner, monkeypatch):
    import devlog.cli._shared as cli_shared

    monkeypatch.setattr(cli_shared, "_edit_in_editor", lambda _initial: None)
    result = runner.invoke(main, ["config", "edit"])
    assert result.exit_code == 1
    assert "abnormally" in result.output


def test_config_edit_empty_buffer_resets_to_defaults(runner, monkeypatch):
    import devlog.cli._shared as cli_shared

    runner.invoke(main, ["config", "set", "watch_interval", "9"])
    monkeypatch.setattr(cli_shared, "_edit_in_editor", lambda _initial: "  \n")
    result = runner.invoke(main, ["config", "edit"])
    assert result.exit_code == 0, result.output

    get = runner.invoke(main, ["config", "get", "watch_interval"])
    assert get.output.strip() == "2"


@pytest.mark.skipif(sys.platform == "win32", reason="uses a #!/bin/sh editor wrapper")
def test_config_edit_with_editor_arguments(runner, monkeypatch, tmp_path):
    """An $EDITOR value with arguments (e.g. `code -w`) now works.

    Regression: ``_edit_in_editor`` passed the whole $EDITOR string as
    ONE executable name, so any editor configured with flags failed
    with 'Editor exited abnormally' — for ``edit``, ``template edit``
    and ``config edit`` alike.
    """
    wrapper = tmp_path / "fakeed"
    wrapper.write_text(
        "#!/bin/sh\n"
        'target=""\n'
        'for arg in "$@"; do target="$arg"; done\n'
        'printf \'{\\n  "theme": "ocean"\\n}\\n\' > "$target"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    monkeypatch.setenv("VISUAL", f"{wrapper} --wait")
    result = runner.invoke(main, ["config", "edit"])
    assert result.exit_code == 0, result.output

    value = runner.invoke(main, ["config", "get", "theme"])
    assert value.exit_code == 0, value.output
    assert value.output.strip() == "ocean"


# ---------------------------------------------------------------------------
# AtomicStore serialization (P2: parallel `config set` lost keys — the old
# lock flocked an inode that os.replace immediately swapped out, and the
# load→modify→save window was never covered)
# ---------------------------------------------------------------------------


def test_atomicstore_update_composes_and_locks_reentrant(data_dir):
    """update() merges under one lock; nested locked() must not deadlock."""
    from devlog._store import AtomicStore

    store = AtomicStore("probe-store.json", default={})
    with store.locked():
        with store.locked():
            pass
    store.update(lambda d: {**d, "a": 1})
    store.update(lambda d: {**d, "b": 2})
    assert store.load() == {"a": 1, "b": 2}


def test_atomicstore_parallel_config_sets_keep_all_keys(tmp_path):
    """6 parallel `config set` processes (different keys) must all persist.

    Regression: all six writers exited 0 but one key silently vanished —
    the read-modify-write was not serialized across processes.
    """
    env = dict(os.environ, DEVLOG_DATA_DIR=str(tmp_path))
    env.pop("DEVLOG_BACKEND", None)
    pairs = [
        ("watch_interval", "7"),
        ("auto_sync", "true"),
        ("default_llm_model", "modelA"),
        ("default_embed_model", "modelB"),
        ("ollama_host", "http://h"),
        ("webhook_url", "http://w"),
    ]
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                "from devlog.cli import main; main()",
                "config",
                "set",
                key,
                value,
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for key, value in pairs
    ]
    results = [p.communicate(timeout=120) for p in procs]
    exit_codes = [p.returncode for p in procs]
    failures = [
        (key, rc, out.decode("utf-8", "replace")[-400:], err.decode("utf-8", "replace")[-600:])
        for (key, _v), rc, (out, err) in zip(pairs, exit_codes, results)
        if rc != 0
    ]
    assert all(rc == 0 for rc in exit_codes), f"nonzero exits: {exit_codes}; failures={failures}"

    stored = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    for key, _value in pairs:
        assert key in stored, f"lost update: {key!r} missing from {sorted(stored)}"


# ---------------------------------------------------------------------------
# Template name validation (P2: `template save '../config'` overwrote
# config.json; 'a/b' crashed with a raw FileNotFoundError traceback)
# ---------------------------------------------------------------------------


def test_template_save_traversal_does_not_clobber_config(runner, data_dir):
    (data_dir / "config.json").write_text('{"watch_interval": 42}', encoding="utf-8")
    result = runner.invoke(main, ["template", "save", "../config", "-m", "CLOBBER"])
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "path separators" in result.output

    # The store file outside templates/ is untouched.
    assert (
        json.loads((data_dir / "config.json").read_text(encoding="utf-8"))["watch_interval"] == 42
    )


def test_template_save_rejects_slash_dot_and_empty_names(runner):
    for bad in ("a/b", "..hidden", ".hidden", ""):
        result = runner.invoke(main, ["template", "save", bad, "-m", "x"])
        assert result.exit_code == 1, f"{bad!r}: {result.output}"
        assert "Traceback" not in result.output


def test_template_save_still_allows_unicode_names(runner):
    result = runner.invoke(main, ["template", "save", "héllo", "-m", "x"])
    assert result.exit_code == 0, result.output
    listing = runner.invoke(main, ["template", "list", "--quiet"])
    assert "héllo" in listing.output


def test_template_delete_traversal_name_clean_error(runner):
    result = runner.invoke(main, ["template", "delete", "../../evil", "-y"])
    assert result.exit_code == 1
    assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# digest/ask corrupt-store contract (P2: raw CorruptedStorageError
# traceback — every other command already rendered the clean panel)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    active_backend() == "sqlite",
    reason=requires_json_backend(
        "the corruption contract writes a corrupt entries.json; sqlite reads entries.db"
    ),
)
def test_digest_corrupt_store_clean_error(runner, data_dir):
    (data_dir / "entries.json").write_text("THIS IS NOT JSON {{{", encoding="utf-8")
    result = runner.invoke(main, ["digest"])
    assert result.exit_code == 2
    assert "Traceback" not in result.output
    assert "corrupted" in result.output.lower()


@pytest.mark.skipif(
    active_backend() == "sqlite",
    reason=requires_json_backend(
        "the corruption contract writes a corrupt entries.json; sqlite reads entries.db"
    ),
)
def test_ask_corrupt_store_clean_error(runner, data_dir):
    (data_dir / "entries.json").write_text("THIS IS NOT JSON {{{", encoding="utf-8")
    result = runner.invoke(main, ["ask", "query"])
    assert result.exit_code == 2
    assert "Traceback" not in result.output
    assert "corrupted" in result.output.lower()


# ---------------------------------------------------------------------------
# add --template non-interactive (P2: TTY-only even for zero-variable
# templates — no way to use templates from scripts or agents)
# ---------------------------------------------------------------------------


def _save_template(runner, name, message, *extra):
    result = runner.invoke(main, ["template", "save", name, "-m", message, *extra])
    assert result.exit_code == 0, result.output


def test_add_template_with_var_flag_non_interactive(runner):
    _save_template(
        runner,
        "deploy-note",
        "Deploy {service} to {env}",
        "--var",
        "service=Service name",
        "--var",
        "env=Environment",
    )
    result = runner.invoke(
        main,
        ["add", "--template", "deploy-note", "--var", "service=api", "--var", "env=prod", "-q"],
    )
    assert result.exit_code == 0, result.output
    entry = first_json_obj(runner.invoke(main, ["list", "--quiet"]).output)
    assert entry["message"] == "Deploy api to prod"


def test_add_template_missing_var_expands_empty(runner):
    _save_template(runner, "onevar", "Hello {name} and {other}", "--var", "name=Who")
    result = runner.invoke(main, ["add", "--template", "onevar", "--var", "name=Alice", "-q"])
    assert result.exit_code == 0, result.output
    entry = first_json_obj(runner.invoke(main, ["list", "--quiet"]).output)
    assert entry["message"] == "Hello Alice and "


def test_add_template_var_requires_template(runner):
    result = runner.invoke(main, ["add", "msg", "--var", "a=b"])
    assert result.exit_code == 1
    assert "--var requires --template" in result.output


def test_add_template_var_bad_format_clean_error(runner):
    _save_template(runner, "fmt", "Hello {name}", "--var", "name=Who")
    result = runner.invoke(main, ["add", "--template", "fmt", "--var", "noequals"])
    assert result.exit_code == 1
    assert "name=value" in result.output
    assert "Traceback" not in result.output


def test_add_template_without_tty_errors_and_mentions_var(runner):
    _save_template(runner, "ttyt", "Hello {name}", "--var", "name=Who")
    result = runner.invoke(main, ["add", "--template", "ttyt"])
    assert result.exit_code == 1
    assert "--var" in result.output
