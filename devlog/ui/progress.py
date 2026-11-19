"""devlog.ui.progress — The export progress bar."""

from __future__ import annotations

from rich.progress import (
    BarColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
)

from devlog.ui._base import (
    err_console,
)

# ---------------------------------------------------------------------------
# Export progress
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Export progress
# ---------------------------------------------------------------------------


def export_progress(total: int) -> Progress:
    """Return a configured Rich ``Progress`` for the export command.

    The progress bar writes to STDERR so it never pollutes the output
    file when the user pipes STDOUT.

    Args:
        total: number of entries to write.

    Returns:
        A ``Progress`` context manager. Caller must use it as
        ``with export_progress(n) as progress: ...``.
    """
    return Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=err_console,
    )
