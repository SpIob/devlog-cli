"""devlog.ui.banner — The ``--version`` and root help banners.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.text import Text

from devlog.ui._base import (
    VERSION,
    _s,
    console,
)
from devlog.ui.layout import (
    command_table,
)

# ---------------------------------------------------------------------------
# Version banner / root help
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Version banner / root help
# ---------------------------------------------------------------------------

def version_banner() -> None:
    """Print a styled version line for ``--version``.

    The previous design wrapped the version in a horizontal ``Rule``,
    but the rule carried no information and overflowed on narrow
    terminals. A single, prominent version line is enough. The
    "devlog" brand mark is styled with the same ``banner_version``
    theme role as the version number so users can recolor the
    whole brand in one place via their theme file.
    """
    console.print(
        Text("devlog", style=_s("banner_version"))
        + Text(", version ", style="bold")
        + Text(VERSION, style=_s("banner_version"))
    )


def root_banner(commands: Sequence[tuple[str, str]] | None = None) -> None:
    """Print a friendly banner when ``devlog`` is invoked with no subcommand.

    Replaces the raw Click help text with a banner + 2-column command
    table and a hint about ``--help``.

    Args:
        commands: optional list of ``(name, description)`` tuples. When
            omitted, falls back to ``devlog._commands.COMMANDS`` so the
            single source of truth lives in one place.
    """
    if commands is None:
        from devlog._commands import COMMANDS as _COMMANDS
        commands = _COMMANDS

    console.print(
        Text("devlog", style="bold")
        + Text(f"  ·  v{VERSION}", style=_s("banner_version"))
        + Text("  ·  a terminal-based developer journal", style="dim")
    )

    console.print(command_table(commands))

    console.print(
        Text("Run ", style="dim")
        + Text("devlog <command> --help", style="bold")
        + Text(" for details on a specific command.", style="dim")
    )
