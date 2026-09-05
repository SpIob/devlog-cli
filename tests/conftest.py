"""Shared pytest fixtures for the devlog test suite.

Centralising the ``CliRunner``/seed/jsonl helpers here kills ~140 lines of
duplicated fixtures across the 22 test files. Every fixture targets an
*isolated* data directory via ``DEVLOG_DATA_DIR`` so tests never touch
the user's real journal.
"""

from __future__ import annotations

import io
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console

from devlog import storage
from devlog.cli import main
from devlog.models import Entry


def median_of(fn, *args, runs: int = 5) -> float:
    """Time *fn*(*args) ``runs`` times, return the median in seconds.

    Centralises the "sort + take index 2 of 5 runs" noise-reduction
    pattern that recurs in ``test_performance.py``. The median is
    robust to one slow outlier; pure min would over-fit to the
    best-case run.
    """
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn(*args)
        times.append(time.perf_counter() - t0)
    times.sort()
    return times[runs // 2]

# Re-export ANSI-stripping helper. Three test files previously inlined
# the same one-liner; now they all import from conftest.
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Remove Rich/terminal ANSI escape sequences from a string."""
    return ANSI_ESCAPE_RE.sub("", text)


def render_to_text(
    renderable,
    *,
    color: bool = False,
    width: int = 120,
    force_terminal: bool = False,
) -> str:
    """Render a Rich renderable to plain text, stripping ANSI by default.

    Replaces the ~17 occurrences across ``test_ui.py`` and
    ``test_ui_columns.py`` of:

        buf = io.StringIO()
        Console(file=buf, no_color=True, width=120).print(renderable)
        return _strip_ansi(buf.getvalue())

    Args:
        renderable: a Rich renderable (Panel, Table, Text, etc.).
        color: when True, ANSI escape codes are preserved. Default is
            ``False`` so substring assertions stay readable.
        width: terminal width for layout (default 120 — wide enough
            to keep tables on one line per row).
        force_terminal: forwarded to Rich's ``Console``. Off by
            default (matches the no-colour path).

    Returns:
        The rendered text with a trailing newline stripped.
    """
    buf = io.StringIO()
    Console(
        file=buf,
        no_color=not color,
        width=width,
        force_terminal=force_terminal,
    ).print(renderable)
    return buf.getvalue().rstrip("\n")


def capture_console(fn):
    """Swap ``ui.console`` for a buffer-backed Console, run *fn*, restore it.

    Replaces the 9-line ``saved = ui.console; ui.console = Console(...);
    try: fn(); finally: ui.console = saved`` pattern that recurs in
    3 banner tests in ``test_ui.py`` and 2 in ``test_ui_columns.py``.

    Args:
        fn: a zero-arg callable that prints through ``ui.console``.

    Returns:
        The captured, ANSI-stripped output as a string.
    """
    from devlog import ui as _ui

    saved = _ui.console
    buf = io.StringIO()
    _ui.console = Console(file=buf, no_color=True, width=120, force_terminal=False)
    try:
        fn()
    finally:
        _ui.console = saved
    return strip_ansi(buf.getvalue())


@pytest.fixture()
def write_entries():
    """Factory: write a raw ``{"entries": [...]}`` payload to a data dir.

    Generalises the ``_write_raw`` helper that ``test_repair.py`` had
    inlined; reusable by any test that needs to plant a malformed or
    custom store without going through ``storage.add_entry``.
    """
    def _write(data_dir: Path, payload: dict) -> None:
        path = data_dir / "entries.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    return _write


@pytest.fixture()
def runner(tmp_path: Path) -> CliRunner:
    """CliRunner wired to an isolated DEVLOG_DATA_DIR.

    The env dict is set on the runner so every ``runner.invoke(main, ...)``
    call automatically reads/writes from ``tmp_path``.
    """
    return CliRunner(env={"DEVLOG_DATA_DIR": str(tmp_path)})


@pytest.fixture()
def data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Set DEVLOG_DATA_DIR for in-process storage access; return the tmp Path."""
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def make_entry():
    """Factory: ``make_entry(id, msg, created_at=None, tags=None) -> Entry``."""
    def _make(entry_id: str, message: str, created_at: str | None = None, tags=None):
        return Entry(
            id=entry_id,
            message=message,
            tags=list(tags) if tags else [],
            created_at=created_at
            or datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    return _make


@pytest.fixture()
def seed_entry(make_entry):
    """Factory that injects an entry via ``storage.add_entry()``."""
    def _seed(entry_id: str, message: str, created_at: str | None = None, tags=None):
        storage.add_entry(make_entry(entry_id, message, created_at, tags))

    return _seed


@pytest.fixture()
def utc_iso():
    """Factory: format a datetime as a UTC ISO string."""
    def _fmt(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    return _fmt


@pytest.fixture()
def add_entry_cmd(runner: CliRunner):
    """Add an entry via the CLI and return its 8-char short id.

    Raises if the CLI invocation fails or the short id cannot be parsed
    from the output.
    """
    def _add(message: str, *tags: str) -> str:
        args = ["add", message]
        for t in tags:
            args += ["-t", t]
        result = runner.invoke(main, args)
        assert result.exit_code == 0, result.output
        m = re.search(r"[a-f0-9]{8}", result.output)
        assert m, f"could not parse short id from: {result.output!r}"
        return m.group(0)

    return _add


def first_json_obj(output: str) -> dict:
    """Return the first non-empty JSON line of CLI ``--quiet`` output.

    The quiet-JSON contract prints one JSON object per line. Older
    versions of the CLI occasionally emitted trailing blank lines, so we
    skip empties before parsing.
    """
    for line in output.splitlines():
        if line.strip():
            return json.loads(line)
    raise ValueError(f"no JSON object found in: {output!r}")


def first_json_line(output: str) -> str:
    """Return the first non-empty line of ``--quiet`` output (verbatim)."""
    for line in output.splitlines():
        if line.strip():
            return line
    raise ValueError(f"no non-empty line in: {output!r}")
