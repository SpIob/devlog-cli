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
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console

from devlog import storage
from devlog.cli import main
from devlog.models import Entry

# ---------------------------------------------------------------------------
# Shared fake Ollama server (real HTTP, no mocks)
# ---------------------------------------------------------------------------


class _FakeOllama(BaseHTTPRequestHandler):
    """Minimal Ollama-compatible server: records requests, emits replies.

    Responses key off request content so tests never need per-test
    hooks: an unknown model name 404s exactly like the real server, and
    the sentinel input ``__broken__`` produces a malformed payload.
    """

    last_path: str | None = None
    last_payload: dict | None = None
    request_count: int = 0

    def log_message(self, format, *args):
        pass

    def _reply(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        _FakeOllama.last_path = self.path
        _FakeOllama.last_payload = payload
        _FakeOllama.request_count += 1
        if self.path == "/api/generate":
            if payload.get("model") == "missing-model":
                self._reply({"error": "model not found"}, status=404)
            else:
                self._reply({"response": "You shipped the importers.", "done": True})
        elif self.path == "/api/embed":
            if payload.get("input") == ["__broken__"]:
                self._reply({"unexpected": "shape"})
            else:
                # Deterministic pseudo-embedding: 4 dims derived from the text,
                # so tests can assert relative similarity without a real model.
                vectors = []
                for text in payload.get("input", []):
                    h = sum(ord(c) for c in str(text))
                    vectors.append([
                        ((h % 17) + 1) / 18.0,
                        ((h % 13) + 1) / 14.0,
                        ((h % 7) + 1) / 8.0,
                        1.0,
                    ])
                self._reply({"embeddings": vectors})
        else:
            self._reply({"error": "unknown path"}, status=404)


class _OllamaRecorder:
    """Test handle for the fake server: base URL + last-request accessors."""

    def __init__(self, url: str):
        self.url = url

    @property
    def last_path(self):
        return _FakeOllama.last_path

    @property
    def last_payload(self):
        return _FakeOllama.last_payload

    @property
    def request_count(self):
        return _FakeOllama.request_count


@pytest.fixture()
def ollama_server():
    """Yield a :class:`_OllamaRecorder` backed by a real localhost server."""
    _FakeOllama.request_count = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOllama)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield _OllamaRecorder(f"http://127.0.0.1:{server.server_address[1]}")
    server.shutdown()


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


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Force an isolated DEVLOG_DATA_DIR for every test.

    Autouse + function-scoped so an ambient ``DEVLOG_DATA_DIR`` (e.g.
    exported in the developer's shell) can never leak into the suite —
    neither polluting the user's real ``~/.devlog`` journal nor making
    theme-dependent assertions pass/fail on whatever theme happens to be
    installed there. Tests that need a specific dir override it via the
    ``runner``/``data_dir`` fixtures.
    """
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "isolated-data"))


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

# ---------------------------------------------------------------------------
# Backend-aware test helpers
# ---------------------------------------------------------------------------


def active_backend() -> str:
    """Return the storage backend under test: "json" (default) or "sqlite".

    Read once per call from the env so tests can branch or skip:
    tests that assert on-disk ``entries.json`` semantics (corrupt-file
    recovery, direct file reads, chmod-based permission errors) are
    JSON-backend-specific by nature and should skip under
    ``DEVLOG_BACKEND=sqlite``.
    """
    import os

    return os.environ.get("DEVLOG_BACKEND", "json").lower()


def requires_json_backend(reason: str = "asserts entries.json on-disk semantics"):
    """Pytestmark helper: skip the test when the sqlite backend is active.

    Usage::

        pytestmark = pytest.mark.skipif(
            active_backend() == "sqlite", reason=requires_json_backend()
        )

    (or per-test with the same skipif expression).
    """
    return f"JSON-backend only: {reason}"

