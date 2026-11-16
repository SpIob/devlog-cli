"""devlog CLI — Meta commands (shell completions).
"""

from __future__ import annotations

import click

from devlog import _completions

# ---------------------------------------------------------------------------
# completions
# ---------------------------------------------------------------------------

@click.argument(
    "shell",
    type=click.Choice(["bash", "zsh", "fish"], case_sensitive=False),
)
def completions(shell: str) -> None:
    """Print a shell completion script for the given shell."""
    _completions.completions(shell)


def register(main) -> None:
    """Attach the meta commands to *main*."""
    main.command()(completions)
