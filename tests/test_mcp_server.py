"""Tests for the MCP server (``devlog mcp``).

The protocol handler is tested as a pure function (dict in, dict out);
the stdio loop is tested end-to-end by feeding newline-delimited
JSON-RPC through piped streams. Storage-facing tools run against the
real (isolated) backend, so the suite verifies them under both JSON
and SQLite CI passes.
"""

from __future__ import annotations

import io
import json

from devlog import _mcp_server, storage


def _rpc(method: str, params=None, msg_id: int | None = 1) -> dict:
    msg: dict = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def _call_tool(name: str, arguments: dict | None = None, msg_id: int = 1) -> dict:
    return _rpc("tools/call", {"name": name, "arguments": arguments or {}}, msg_id)


def _result_text(response: dict) -> str:
    return response["result"]["content"][0]["text"]


# ---------------------------------------------------------------------------
# Protocol: initialize / notifications / errors
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_initialize_returns_protocol_version_and_server_info(self):
        response = _mcp_server.handle_message(
            _rpc("initialize", {"protocolVersion": "2024-11-05"})
        )
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 1
        result = response["result"]
        assert result["protocolVersion"] == "2024-11-05"
        assert result["serverInfo"]["name"] == "devlog"
        assert "tools" in result["capabilities"]

    def test_ping_returns_empty_result(self):
        response = _mcp_server.handle_message(_rpc("ping", msg_id=7))
        assert response["id"] == 7
        assert response["result"] == {}

    def test_unknown_method_returns_method_not_found(self):
        response = _mcp_server.handle_message(_rpc("resources/list", msg_id=3))
        assert response["error"]["code"] == -32601

    def test_notification_returns_none(self):
        assert (
            _mcp_server.handle_message(
                {"jsonrpc": "2.0", "method": "notifications/initialized"}
            )
            is None
        )

    def test_malformed_json_line_returns_parse_error(self):
        response = _mcp_server.handle_line("{not json")
        parsed = json.loads(response)
        assert parsed["error"]["code"] == -32700

    def test_batch_or_nonobject_line_returns_parse_error(self):
        assert json.loads(_mcp_server.handle_line("[1,2,3]"))["error"]["code"] == -32700

    def test_missing_method_returns_invalid_request(self):
        response = _mcp_server.handle_message({"jsonrpc": "2.0", "id": 2})
        assert response["error"]["code"] == -32603


# ---------------------------------------------------------------------------
# tools/list
# ---------------------------------------------------------------------------


class TestToolsList:
    def test_lists_the_four_journal_tools(self):
        response = _mcp_server.handle_message(_rpc("tools/list"))
        names = [t["name"] for t in response["result"]["tools"]]
        assert set(names) == {"list_entries", "search_entries", "add_entry", "get_stats"}

    def test_every_tool_has_a_schema_and_description(self):
        response = _mcp_server.handle_message(_rpc("tools/list"))
        for tool in response["result"]["tools"]:
            assert tool["description"]
            assert tool["inputSchema"]["type"] == "object"


# ---------------------------------------------------------------------------
# tools/call against the real storage layer
# ---------------------------------------------------------------------------


class TestListEntriesTool:
    def test_returns_json_entry_list(self, seed_entry):
        seed_entry("e1", "Shipped the export command", "2026-10-27T09:15:00Z", ["export"])
        response = _mcp_server.handle_message(_call_tool("list_entries"))
        assert "error" not in response
        entries = json.loads(_result_text(response))
        assert len(entries) == 1
        assert entries[0]["message"] == "Shipped the export command"

    def test_tag_filter_applies(self, seed_entry):
        seed_entry("e1", "one", "2026-10-27T09:15:00Z", ["a"])
        seed_entry("e2", "two", "2026-10-27T10:15:00Z", ["b"])
        response = _mcp_server.handle_message(
            _call_tool("list_entries", {"tag": "a"})
        )
        entries = json.loads(_result_text(response))
        assert [e["id"] for e in entries] == ["e1"]

    def test_limit_applies(self, seed_entry):
        for i in range(3):
            seed_entry(f"e{i}", f"msg {i}", f"2026-10-2{i}T09:15:00Z")
        response = _mcp_server.handle_message(
            _call_tool("list_entries", {"limit": 2})
        )
        assert len(json.loads(_result_text(response))) == 2


class TestSearchEntriesTool:
    def test_finds_case_insensitive_substring(self, seed_entry):
        seed_entry("e1", "Shipped the EXPORT command", "2026-10-27T09:15:00Z")
        response = _mcp_server.handle_message(
            _call_tool("search_entries", {"query": "export"})
        )
        results = json.loads(_result_text(response))
        assert len(results) == 1
        assert results[0]["id"] == "e1"

    def test_no_matches_returns_empty_list(self, seed_entry):
        seed_entry("e1", "unrelated", "2026-10-27T09:15:00Z")
        response = _mcp_server.handle_message(
            _call_tool("search_entries", {"query": "zzz"})
        )
        assert json.loads(_result_text(response)) == []


class TestAddEntryTool:
    def test_adds_entry_and_persists(self, seed_entry):
        response = _mcp_server.handle_message(
            _call_tool("add_entry", {"message": "Added via MCP", "tags": ["mcp"]})
        )
        assert "error" not in response
        result = json.loads(_result_text(response))
        assert result["added"] is True
        entries = storage.load_entries()
        assert len(entries) == 1
        assert entries[0].message == "Added via MCP"
        assert entries[0].tags == ["mcp"]

    def test_missing_message_reports_tool_error(self):
        response = _mcp_server.handle_message(_call_tool("add_entry", {}))
        assert response["result"]["isError"] is True
        assert "message" in response["result"]["content"][0]["text"]

    def test_invalid_tag_reports_tool_error(self):
        response = _mcp_server.handle_message(
            _call_tool("add_entry", {"message": "x", "tags": ["BAD TAG!"]})
        )
        assert response["result"]["isError"] is True


class TestGetStatsTool:
    def test_returns_totals_and_range(self, seed_entry):
        seed_entry("e1", "one", "2026-10-27T09:15:00Z", ["a"])
        seed_entry("e2", "two", "2026-10-28T09:15:00Z", ["a", "b"])
        response = _mcp_server.handle_message(_call_tool("get_stats"))
        stats = json.loads(_result_text(response))
        assert stats["total"] == 2
        assert stats["first"] == "2026-10-27T09:15:00Z"
        assert stats["last"] == "2026-10-28T09:15:00Z"
        assert {"tag": "a", "count": 2} in stats["top_tags"]

    def test_empty_journal_gives_zero_total(self):
        response = _mcp_server.handle_message(_call_tool("get_stats"))
        assert json.loads(_result_text(response))["total"] == 0


class TestUnknownTool:
    def test_unknown_tool_name_reports_tool_error(self):
        response = _mcp_server.handle_message(_call_tool("nope"))
        assert response["result"]["isError"] is True


# ---------------------------------------------------------------------------
# stdio transport (end-to-end through the real loop)
# ---------------------------------------------------------------------------


class TestServe:
    def test_serve_processes_lines_until_eof(self, monkeypatch, seed_entry):
        seed_entry("e1", "hello world", "2026-10-27T09:15:00Z")
        lines = "\n".join(
            [
                json.dumps(_rpc("initialize", {"protocolVersion": "2024-11-05"}, 1)),
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                json.dumps(_call_tool("get_stats", {}, 2)),
                "{not json",
            ]
        )
        fake_in = io.StringIO(lines + "\n")
        fake_out = io.StringIO()
        monkeypatch.setattr(_mcp_server.sys, "stdin", fake_in)
        monkeypatch.setattr(_mcp_server.sys, "stdout", fake_out)

        _mcp_server.serve()

        outputs = [
            json.loads(line) for line in fake_out.getvalue().splitlines() if line.strip()
        ]
        # 3 responses: initialize, tools/call, parse error (no response for
        # the notification).
        assert len(outputs) == 3
        assert outputs[0]["result"]["serverInfo"]["name"] == "devlog"
        assert json.loads(outputs[1]["result"]["content"][0]["text"])["total"] == 1
        assert outputs[2]["error"]["code"] == -32700


# ---------------------------------------------------------------------------
# CLI registration
# ---------------------------------------------------------------------------


def test_mcp_command_is_registered(runner):
    from devlog.cli import main

    result = runner.invoke(main, ["mcp", "--help"])
    assert result.exit_code == 0, result.output
    assert "MCP" in result.output or "mcp" in result.output.lower()
