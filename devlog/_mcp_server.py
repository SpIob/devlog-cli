"""Model Context Protocol server for devlog (``devlog mcp``).

Exposes the journal to AI agents over stdio using newline-delimited
JSON-RPC 2.0, the same wire format the official Python/TypeScript MCP
SDKs speak. Implemented with the stdlib only: the protocol surface is
small (initialize, tools/list, tools/call, ping), and staying
dependency-free keeps the base install lean.

Tools (all backed by the storage layer, so both backends work):
- ``list_entries``   — recent entries, optional tag filter and limit
- ``search_entries`` — case-insensitive substring search
- ``add_entry``      — append an entry (message required, tags optional)
- ``get_stats``      — totals, date range, top tags

Every response is a JSON string in a single text content block;
tool failures use the MCP ``isError`` convention instead of crashing
the server loop.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "devlog"

# Track the application version (single source of truth in ui.py).
from devlog.ui import VERSION as SERVER_VERSION  # noqa: E402

# JSON-RPC / MCP error codes
_PARSE_ERROR = -32700
_METHOD_NOT_FOUND = -32601
_INVALID_REQUEST = -32603


# ---------------------------------------------------------------------------
# Tool implementations (storage-backed; no CLI/render dependency)
# ---------------------------------------------------------------------------


def _tool_list_entries(arguments: dict) -> str:
    from devlog import _filter, storage

    limit = arguments.get("limit")
    tag = arguments.get("tag")
    entries = storage.load_entries()
    filtered = _filter.filter_pipeline(
        entries, tags=(tag,) if tag else (), sort=True
    )
    if isinstance(limit, int) and limit > 0:
        filtered = filtered[:limit]
    payload = [
        {
            "id": e.id,
            "message": e.message,
            "created_at": e.created_at,
            "tags": e.tags,
        }
        for e in filtered
    ]
    return json.dumps(payload, ensure_ascii=False)


def _tool_search_entries(arguments: dict) -> str:
    from devlog import storage

    query = str(arguments.get("query", ""))
    limit = arguments.get("limit")
    if not isinstance(limit, int) or limit <= 0:
        limit = 20
    needle = query.lower()
    results = [
        {
            "id": e.id,
            "message": e.message,
            "created_at": e.created_at,
            "tags": e.tags,
        }
        for e in storage.load_entries()
        if needle in e.message.lower()
    ][:limit]
    return json.dumps(results, ensure_ascii=False)


def _tool_add_entry(arguments: dict) -> str:
    import uuid
    from datetime import datetime, timezone

    from devlog import storage
    from devlog.models import Entry

    message = arguments.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("add_entry requires a non-empty 'message' string")
    tags = arguments.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise ValueError("'tags' must be a list of strings")

    from devlog import _tagops

    for tag in tags:
        if not _tagops._is_valid_tag(tag):
            raise ValueError(
                f"Invalid tag '{tag}': must match {_tagops.TAG_RE.pattern} "
                f"(max {_tagops.MAX_TAG_LENGTH} chars)"
            )

    entry = Entry(
        id=str(uuid.uuid4()),
        message=message,
        tags=list(tags),
        created_at=datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    storage.add_entry(entry)
    return json.dumps(
        {"added": True, "id": entry.id, "short_id": entry.short_id},
        ensure_ascii=False,
    )


def _tool_get_stats(_arguments: dict) -> str:
    from devlog import storage

    entries = storage.load_entries()
    valid = [e for e in entries if e.created_dt.year > 1970]
    total = len(valid)
    first = min((e.created_at for e in valid), default=None)
    last = max((e.created_at for e in valid), default=None)
    tag_counts: dict[str, int] = {}
    for e in valid:
        for tag in e.tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    top_tags = sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    return json.dumps(
        {
            "total": total,
            "first": first,
            "last": last,
            "top_tags": [{"tag": t, "count": c} for t, c in top_tags],
        },
        ensure_ascii=False,
    )


TOOLS: dict[str, Callable[[dict], str]] = {
    "list_entries": _tool_list_entries,
    "search_entries": _tool_search_entries,
    "add_entry": _tool_add_entry,
    "get_stats": _tool_get_stats,
}

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "list_entries",
        "description": (
            "List journal entries, newest first. Optional 'tag' filter "
            "(AND match) and 'limit' (max entries)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tag": {"type": "string", "description": "Filter by tag"},
                "limit": {"type": "integer", "description": "Max entries to return"},
            },
        },
    },
    {
        "name": "search_entries",
        "description": (
            "Search entry messages with a case-insensitive substring "
            "query. Returns matching entries newest-first."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text to search for"},
                "limit": {"type": "integer", "description": "Max results (default 20)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "add_entry",
        "description": (
            "Add a journal entry. 'message' is required; 'tags' is an "
            "optional list of lowercase-hyphen tags."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Entry text"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags to attach",
                },
            },
            "required": ["message"],
        },
    },
    {
        "name": "get_stats",
        "description": (
            "Journal summary: total entries, first/last timestamps, and "
            "the most-used tags with counts."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# ---------------------------------------------------------------------------
# JSON-RPC protocol handling
# ---------------------------------------------------------------------------


def _tool_response(msg_id, text: str, *, is_error: bool = False) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "result": {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        },
    }


def handle_message(message: object) -> dict | None:
    """Handle one parsed JSON-RPC message; ``None`` for notifications."""
    if not isinstance(message, dict) or not isinstance(message.get("method"), str):
        return {
            "jsonrpc": "2.0",
            "id": message.get("id") if isinstance(message, dict) else None,
            "error": {"code": _INVALID_REQUEST, "message": "Invalid request"},
        }
    method = message["method"]
    msg_id = message.get("id")
    params = message.get("params") or {}

    if method.startswith("notifications/"):
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOL_SCHEMAS}}
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        handler = TOOLS.get(name) if isinstance(name, str) else None
        if handler is None:
            return _tool_response(
                msg_id, f"Unknown tool: {name!r}", is_error=True
            )
        try:
            return _tool_response(msg_id, handler(arguments))
        except Exception as exc:
            return _tool_response(msg_id, f"Tool error: {exc}", is_error=True)
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": _METHOD_NOT_FOUND, "message": f"Method not found: {method}"},
    }


def handle_line(line: str) -> str | None:
    """Parse, handle, and serialize one stdio line; ``None`` for notifications."""
    try:
        message = json.loads(line)
    except json.JSONDecodeError:
        return json.dumps(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": _PARSE_ERROR, "message": "Parse error"},
            }
        )
    if not isinstance(message, dict):
        return json.dumps(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": _PARSE_ERROR, "message": "Parse error"},
            }
        )
    response = handle_message(message)
    return json.dumps(response, ensure_ascii=False) if response is not None else None


def serve() -> None:
    """Run the server loop: newline-delimited JSON-RPC over stdio until EOF."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        response = handle_line(line)
        if response is not None:
            print(response, flush=True)
