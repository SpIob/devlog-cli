"""devlog.ui._base — Shared foundation: the stdout/stderr consoles, the theme style
shorthands, colour detection, and the layout constants.
"""

from __future__ import annotations

import os

from rich.console import Console, Group
from rich.padding import Padding
from rich.panel import Panel
from rich.progress import (
    Progress,
)
from rich.table import Table
from rich.text import Text

from devlog import __version__ as VERSION
from devlog import themes

__all__ = [
    "COL_DATE_WIDTH",
    "COL_ID_WIDTH",
    "COL_MESSAGE_MIN",
    "COL_TAGS_MIN",
    "DATE_DISPLAY_LEN",
    "ID_DISPLAY_LEN",
    "MAX_TERMINAL_WIDTH",
    "MIN_TERMINAL_WIDTH",
    "MSG_TRUNCATE_LEN",
    "TAG_NONE",
    "VERSION",
    "_COL_PADDING",
    "_NARROW_TABLE_MIN_WIDTH",
    "_TAGS_GROWTH_CAP",
    "Console",
    "Group",
    "Padding",
    "Panel",
    "Progress",
    "Table",
    "Text",
    "_bold",
    "_color_enabled",
    "_s",
    "console",
    "err_console",
    "themes",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MSG_TRUNCATE_LEN = 60
ID_DISPLAY_LEN = 8
DATE_DISPLAY_LEN = 20  # "YYYY-MM-DD HH:MM UTC" (4+1+2+1+2+1+2+1+2+1+3)

# Column widths are *content* widths (excluding the (0, 1) padding each
# cell adds). The widths passed to Table.add_column() are bumped by
# ``_COL_PADDING`` so the content area matches the constants below.
_COL_PADDING = 2  # default Rich cell padding is (0, 1) on each side
# Maximum number of extra chars to give the Tags column before the
# extra room goes to Message. Set high so tags effectively get half
# the surplus space (the `half_surplus` logic), allowing all tags
# to be visible on wide terminals.
_TAGS_GROWTH_CAP = 100

# Width budget at 80-col terminals. Columns 1-3 sum to ~46 chars (incl. padding).
COL_ID_WIDTH = ID_DISPLAY_LEN  # 8
COL_DATE_WIDTH = DATE_DISPLAY_LEN  # 20
COL_TAGS_MIN = 10
COL_MESSAGE_MIN = 27  # 27 chars is the smallest a journal message can be
                       # read at a glance; below this the table feels
                       # claustrophobic and the smart-truncated match
                       # often falls off the right edge.
# Minimum terminal width we bother to lay out for. Below this, the table
# cannot fit the full ID + date columns and Rich would start hiding
# columns — the user is told to widen their terminal instead.
MIN_TERMINAL_WIDTH = 80
MAX_TERMINAL_WIDTH = 160

# Below :data:`MIN_TERMINAL_WIDTH` the 4-column table is reduced to
# 3 columns (dropping Tags) by :func:`entries_table`. This constant is
# the *minimum* width at which the 3-column table still fits the full
# 8-char short id + 20-char date + 27-char message; below it, the date
# is shortened. 8 + 20 + 27 + 6 padding + 4 box = 65.
_NARROW_TABLE_MIN_WIDTH = 65

TAG_NONE = "(none)"


# ---------------------------------------------------------------------------
# Console construction (NO_COLOR + non-TTY respected)
# ---------------------------------------------------------------------------

def _color_enabled(stream) -> bool:
    """Return True when ANSI color should be emitted on *stream*."""
    if "NO_COLOR" in os.environ:
        return False
    try:
        return bool(stream.isatty())
    except (ValueError, AttributeError):
        return False

# ---------------------------------------------------------------------------
# Console references
# ---------------------------------------------------------------------------
#
# The real ``Console`` objects are owned by the package facade
# (``devlog.ui``) so that swapping one stays a *single* patch point —
# tests capture output by assigning ``ui.console``/``ui.err_console``,
# and embedding code may do the same. These references forward attribute
# access to whatever the facade currently holds, so the renderers in the
# sibling modules always write to the active console.


class _ConsoleRef:
    """Late-bound reference to a console owned by :mod:`devlog.ui`."""

    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        self._name = name

    def _target(self) -> Console:
        from devlog import ui

        return getattr(ui, self._name)

    def __getattr__(self, item: str):
        return getattr(self._target(), item)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<console ref devlog.ui.{self._name}>"

    def __enter__(self):
        return self._target().__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._target().__exit__(exc_type, exc_val, exc_tb)


console = _ConsoleRef("console")
err_console = _ConsoleRef("err_console")


def _s(role: str) -> str:
    """Shorthand for :func:`devlog.themes.get_style`."""
    return themes.get_style(role)


def _bold(role: str) -> str:
    """Shorthand for :func:`devlog.themes.get_bold_style`."""
    return themes.get_bold_style(role)
