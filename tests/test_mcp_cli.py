"""Tests for the ``devlog mcp`` CLI wrapper (:mod:`devlog._mcp_cli`).

The protocol handler and the stdio loop are covered by
``tests/test_mcp_server.py``. This file targets the thin Click command:
its ``--check`` JSON sanity branch and the default branch that hands
off to :func:`devlog._mcp_server.serve`.

``serve`` is never allowed to run for real here — a live stdio loop
would block the suite — so the default-branch test swaps the module
attribute for a recorder. The CLI imports ``_mcp_server`` lazily inside
the command body and calls ``_mcp_server.serve()`` off the module
object, so patching the module attribute (not a name imported into
``_mcp_cli``) is what takes effect.
"""

from __future__ import annotations

import json

from devlog import _mcp_server
from devlog.cli import main

# ---------------------------------------------------------------------------
# --check branch
# ---------------------------------------------------------------------------


def _check_payload(runner) -> tuple[dict, str]:
    """Invoke ``mcp --check`` and return (parsed first JSON line, raw output)."""
    result = runner.invoke(main, ["mcp", "--check"])
    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 1, f"expected a single stdout line, got: {result.output!r}"
    return json.loads(lines[0]), result.output


def test_check_exits_zero(runner):
    result = runner.invoke(main, ["mcp", "--check"])
    assert result.exit_code == 0, result.output
    assert result.exception is None


def test_check_prints_one_json_object(runner):
    payload, raw = _check_payload(runner)
    assert isinstance(payload, dict)
    # Exactly the four documented keys — no extras leaking from a
    # richer (or narrower) schema.
    assert set(payload) == {"server", "version", "protocol", "tools"}
    assert raw.strip().startswith("{")


def test_check_reports_server_name_and_version(runner):
    payload, _ = _check_payload(runner)
    assert payload["server"] == _mcp_server.SERVER_NAME
    assert payload["version"] == _mcp_server.SERVER_VERSION


def test_check_reports_protocol_version(runner):
    payload, _ = _check_payload(runner)
    assert payload["protocol"] == _mcp_server.PROTOCOL_VERSION


def test_check_lists_every_tool_name(runner):
    payload, _ = _check_payload(runner)
    expected = [t["name"] for t in _mcp_server.TOOL_SCHEMAS]
    assert payload["tools"] == expected
    # Pin the current mini-surface so a schema rename is a deliberate,
    # visible test change rather than a silent wire-protocol shift.
    assert expected == [
        "list_entries",
        "search_entries",
        "add_entry",
        "get_stats",
        "get_entry",
        "edit_entry",
        "delete_entry",
        "list_tags",
    ]


def test_check_does_not_start_the_server(runner, monkeypatch):
    calls = []

    def _recorder():
        calls.append(True)

    monkeypatch.setattr(_mcp_server, "serve", _recorder)
    result = runner.invoke(main, ["mcp", "--check"])
    assert result.exit_code == 0, result.output
    assert calls == []


# ---------------------------------------------------------------------------
# default branch
# ---------------------------------------------------------------------------


def test_default_invocation_calls_serve_once(runner, monkeypatch):
    calls = []

    def _recorder():
        calls.append("serve")

    monkeypatch.setattr(_mcp_server, "serve", _recorder)
    result = runner.invoke(main, ["mcp"])
    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert calls == ["serve"]
