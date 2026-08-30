"""Shared pytest fixtures for the devlog test suite.

Centralising the ``CliRunner``/seed/jsonl helpers here kills ~140 lines of
duplicated fixtures across the 22 test files. Every fixture targets an
*isolated* data directory via ``DEVLOG_DATA_DIR`` so tests never touch
the user's real journal.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from devlog import storage
from devlog.cli import main
from devlog.models import Entry


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
