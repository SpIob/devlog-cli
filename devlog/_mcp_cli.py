"""The ``devlog mcp`` command: run the MCP server over stdio.

Kept separate from :mod:`devlog._mcp_server` so the protocol module
stays import-clean (no Click dependency) and can be embedded elsewhere.
"""

from __future__ import annotations

import click


def register(main) -> None:
    """Attach the ``mcp`` command to *main*."""

    @main.command()
    @click.option(
        "--check",
        is_flag=True,
        default=False,
        help="Print server info as JSON and exit (sanity check).",
    )
    def mcp(check: bool) -> None:
        """Expose this journal to AI agents over the Model Context Protocol (stdio)."""
        from devlog import _mcp_server

        if check:
            import json

            print(
                json.dumps(
                    {
                        "server": _mcp_server.SERVER_NAME,
                        "version": _mcp_server.SERVER_VERSION,
                        "protocol": _mcp_server.PROTOCOL_VERSION,
                        "tools": [t["name"] for t in _mcp_server.TOOL_SCHEMAS],
                    },
                    ensure_ascii=False,
                )
            )
            return
        _mcp_server.serve()
