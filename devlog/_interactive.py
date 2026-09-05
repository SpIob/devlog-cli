"""Interactive REPL for devlog.

Extracted from cli.py to keep the main CLI module focused on Click wiring.
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

import click
from click.testing import CliRunner

from devlog import ui

if TYPE_CHECKING:
    from devlog.cli import main as MainType


def _interactive_repl(main_cli) -> None:
    """A minimal line-based REPL for browsing and quick adds.

    Supported commands at the prompt:
        add <message> [-t tag1 -t tag2 ...]   → add an entry
        s <query>                              → search the journal
        l [-t tag] [-n N]                      → list entries
        tags                                    → show tag counts
        today                                   → show today's entries
        stats                                   → show summary
        show <id>                               → show one entry
        help                                    → show available commands
        q | quit | exit                         → leave the REPL

    Each successful action is followed by the standard Rich output. The
    REPL keeps running until the user quits or an EOFError is raised
    (e.g. Ctrl-D).
    """
    from rich.prompt import Prompt

    console = ui.console

    console.print(
        f"[{ui._s('banner_command')}]devlog interactive[/{ui._s('banner_command')}]  ·  "
        "type [bold]help[/bold] for commands, [bold]q[/bold] to quit"
    )

    while True:
        try:
            line = Prompt.ask(
                f"[{ui._s('prompt_border')}]devlog>[/{ui._s('prompt_border')}]"
            ).strip()
        except (EOFError, KeyboardInterrupt):
            console.print()  # newline
            return

        if not line:
            continue
        if line in ("q", "quit", "exit"):
            return
        if line in ("h", "help", "?"):
            _print_repl_help(main_cli)
            continue

        # Dispatch by re-invoking the CLI in-process. Easier than re-implementing.
        try:
            # Use Click's standalone command invocation. `mix_stderr=False`
            # was removed in Click 8.2+; the default is `False` now.
            runner = CliRunner()
            # Split shell-style arguments.
            try:
                argv = shlex.split(line)
            except ValueError as exc:
                ui.print_error(str(exc))
                continue
            if not argv:
                continue
            result = runner.invoke(main_cli, argv, catch_exceptions=False)
            if result.exit_code != 0 and result.output:
                # A failed sub-command. Prefix its output with the
                # error panel so the user can tell at a glance that
                # what follows is *not* a success state. Without
                # this, a typo like ``lis`` would dump the Click
                # usage block and the user would have to read it
                # carefully to know it was a failure.
                ui.print_error(
                    f"Command {argv[0]!r} failed (exit {result.exit_code})."
                )
                console.print(result.output, highlight=False)
            elif result.output:
                console.print(result.output, highlight=False)
        except SystemExit:
            # Click's sys.exit() bubbles up here; swallow so the REPL keeps going.
            pass
        except Exception as exc:  # noqa: BLE001
            ui.print_error(str(exc))


def _print_repl_help(main_cli) -> None:
    """Print a one-line description of every command available in the REPL.

    Generated from ``main.list_commands`` so it stays in sync as new
    commands are added. Includes sub-commands of grouped commands
    (``theme``) by name. Aliases (``list``, ``search``) are also
    surfaced as the REPL accepts both forms.
    """
    # Pull the short docstring (first line) for each command and the
    # commands themselves. ``list_commands`` returns every registered
    # command, including those behind a group like ``theme``.
    cmds: list[tuple[str, str]] = []
    for name in sorted(main_cli.list_commands(None)):
        cmd = main_cli.get_command(None, name)
        if cmd is None:
            continue
        if isinstance(cmd, click.Group):
            sub = ", ".join(sorted(cmd.list_commands(None)))
            cmds.append((name, f"{cmd.short_help or ''} (sub: {sub})".strip()))
        else:
            cmds.append((name, (cmd.short_help or "").strip()))

    aliases = [
        ("l", "alias for list"),
        ("s", "alias for search"),
        ("h", "alias for help"),
        ("q", "leave the REPL"),
        ("w", "alias for week"),
        ("y", "alias for yesterday"),
    ]

    console = ui.console
    console.print(ui.command_table(cmds + aliases))