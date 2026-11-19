"""The devlog Click entry point.

``main`` is the root group; every command group is attached at import
time by calling its ``register()`` function, so ``devlog.cli:main``
stays stable for the console-script entry point.
"""

from __future__ import annotations

import os
import sys

import click

from devlog import (
    _ai_cli,
    _commands,
    _git_cli,
    _interactive,
    _mcp_cli,
    _theme_cli,
    ui,
)
from devlog.cli import (
    config,
    entries,
    maintenance,
    meta,
    stats,
    tags,
    templates,
    transfer,
    views,
)

# The command list is the single source of truth in devlog._commands;
# re-exported here for backwards compatibility (``devlog.cli.COMMANDS``).
COMMANDS = _commands.COMMANDS

__all__ = ["COMMANDS", "main"]


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _force_utf8_stdio() -> None:
    """Use UTF-8 for redirected stdout/stderr regardless of locale.

    Rich output (✔/✘/ℹ) and the ``--quiet`` JSON contract are UTF-8, but
    a *piped* stream inherits the locale codec — cp1252 on Western
    Windows, ASCII under ``LC_ALL=C`` — so the first glyph write either
    crashes with ``UnicodeEncodeError`` (Windows) or stalls the renderer
    (Rich retry under a POSIX C locale). Reconfigure redirected streams
    to UTF-8 before anything prints. Real consoles are left alone: Rich
    speaks the Win32 console API there, and POSIX terminals are already
    UTF-8.
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None or stream.isatty() or not hasattr(stream, "reconfigure"):
            continue
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if encoding == "utf8":
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):  # pragma: no cover - stream refuses
            pass


@click.group(invoke_without_command=True)
@click.option("--version", is_flag=True, help="Show version and exit.")
@click.option(
    "--interactive",
    "-i",
    is_flag=True,
    help="Launch the interactive REPL instead of printing the help banner.",
)
@click.pass_context
def main(ctx: click.Context, version: bool, interactive: bool) -> None:
    """devlog — a terminal-based developer journal."""
    _force_utf8_stdio()
    if version:
        ui.version_banner()
        ctx.exit(0)
        return
    if ctx.invoked_subcommand is None:
        if interactive or os.environ.get("DEVLOG_INTERACTIVE") == "1":
            # The "FORCE" env var exists solely for testing the REPL with
            # CliRunner (which provides a non-TTY stdin). Production users
            # should never need it.
            if not sys.stdin.isatty() and os.environ.get("DEVLOG_INTERACTIVE_FORCE") != "1":
                ui.print_error("Interactive mode requires a TTY.")
                sys.exit(1)
            _interactive._interactive_repl(main)
            ctx.exit(0)
            return
        ui.root_banner()
        ctx.exit(0)


# Attach every command group. Import-time registration keeps command
# lookup and ``--help`` ordering identical to the pre-split CLI.
_theme_cli.register(main)
_git_cli.register(main)
_ai_cli.register(main)
_mcp_cli.register(main)
entries.register(main)
tags.register(main)
views.register(main)
stats.register(main)
transfer.register(main)
maintenance.register(main)
meta.register(main)
config.register(main)
templates.register(main)
