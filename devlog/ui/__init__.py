"""Rich rendering helpers for the devlog CLI.

This package centralises *all* Rich output so the command modules stay
argument-parsing layers and styling stays consistent across commands.

Conventions
-----------
- Errors are written to ``err_console`` (STDERR) in a red bordered panel
  with a ✘ icon.
- Warnings are written to ``err_console`` (STDERR) in a yellow bordered
  line with a ⚠ icon.
- Informational/empty-state lines are written to ``console`` (STDOUT)
  unbordered, dimmed, with a ℹ icon.
- Color is automatically disabled when the ``NO_COLOR`` environment
  variable is set (https://no-color.org) or when the target stream is
  not a TTY.

Layout
------
``_base``     consoles, theme shorthands, layout constants
``text``      plurals, error lines, truncation, highlighting
``layout``    terminal-width arithmetic and tables
``calendar``  activity heatmap (sparkline, month grid)
``panels``    per-entry and summary panels
``banner``    version / root banners
``progress``  export progress bar
"""

from __future__ import annotations

import sys

from rich.console import Console

from devlog.ui._base import (
    _COL_PADDING,
    _NARROW_TABLE_MIN_WIDTH,
    _TAGS_GROWTH_CAP,
    COL_DATE_WIDTH,
    COL_ID_WIDTH,
    COL_MESSAGE_MIN,
    COL_TAGS_MIN,
    DATE_DISPLAY_LEN,
    ID_DISPLAY_LEN,
    MAX_TERMINAL_WIDTH,
    MIN_TERMINAL_WIDTH,
    MSG_TRUNCATE_LEN,
    TAG_NONE,
    VERSION,
    Group,
    Padding,
    Panel,
    Progress,
    Table,
    Text,
    _bold,
    _color_enabled,
    _s,
    themes,
)
from devlog.ui.banner import (
    root_banner,
    version_banner,
)
from devlog.ui.calendar import (
    _HEATMAP_CHARS,
    _HEATMAP_TIERS,
    calendar_grid,
    sparkline,
)
from devlog.ui.layout import (
    _column_widths,
    _is_narrow_terminal,
    _raw_terminal_width,
    _terminal_width,
    command_table,
    entries_table,
    tags_table,
    theme_table,
)
from devlog.ui.panels import (
    backup_result,
    calendar_panel,
    delete_panel,
    doctor_report,
    edit_panel,
    entry_panel,
    repair_summary,
    show_panel,
    stats_panel,
)
from devlog.ui.progress import (
    export_progress,
)
from devlog.ui.text import (
    _ellipsis,
    _ellipsis_for_encoding,
    _left_truncate,
    _short_id,
    _tags_text,
    destructive_line,
    dry_run_line,
    format_dt,
    highlight_message,
    plural_noun,
    plural_s,
    pluralize,
    print_error,
    print_info,
    print_warning,
    smart_truncate,
    success_line,
)

# The real consoles are owned here rather than in ``_base``: swapping one
# for a buffer-backed Console (tests do this to capture output, and
# embedding code may too) is then a single, documented patch point.  The
# renderers in the sibling modules reach them through the late-bound
# references in :mod:`devlog.ui._base`.
console = Console(no_color=not _color_enabled(sys.stdout))
err_console = Console(stderr=True, no_color=not _color_enabled(sys.stderr))


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
    "_HEATMAP_CHARS",
    "_HEATMAP_TIERS",
    "_NARROW_TABLE_MIN_WIDTH",
    "_TAGS_GROWTH_CAP",
    "Group",
    "Padding",
    "Panel",
    "Progress",
    "Table",
    "Text",
    "_bold",
    "_column_widths",
    "_ellipsis",
    "_ellipsis_for_encoding",
    "_is_narrow_terminal",
    "_left_truncate",
    "_raw_terminal_width",
    "_s",
    "_short_id",
    "_tags_text",
    "_terminal_width",
    "backup_result",
    "calendar_grid",
    "calendar_panel",
    "command_table",
    "console",
    "delete_panel",
    "destructive_line",
    "doctor_report",
    "dry_run_line",
    "edit_panel",
    "entries_table",
    "entry_panel",
    "err_console",
    "export_progress",
    "format_dt",
    "highlight_message",
    "plural_noun",
    "plural_s",
    "pluralize",
    "print_error",
    "print_info",
    "print_warning",
    "repair_summary",
    "root_banner",
    "show_panel",
    "smart_truncate",
    "sparkline",
    "stats_panel",
    "success_line",
    "tags_table",
    "theme_table",
    "themes",
    "version_banner",
]
