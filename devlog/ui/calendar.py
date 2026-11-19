"""devlog.ui.calendar — The activity heatmap: ``sparkline`` and ``calendar_grid``."""

from __future__ import annotations

from rich.text import Text

from devlog.ui._base import (
    _s,
)

# Characters used by the heatmap. `█` for the busiest days, `▫` for
# the next tier, `▪` for the second-lightest, `·` for the lightest
# non-zero day, and a space for empty days. Two characters per day
# keeps the grid from collapsing visually on narrow terminals.
_HEATMAP_CHARS = [" ", "·", "▪", "▫", "█"]

# (upper-bound ratio, character, theme role) for the four non-zero
# heatmap tiers. Tiers are inclusive on the upper bound; the sentinel
# ``1.01`` ensures anything that survives the loop gets the top tier.
_HEATMAP_TIERS: tuple[tuple[float, str, str], ...] = (
    (0.25, "·", "heatmap_l1"),
    (0.50, "▪", "heatmap_l2"),
    (0.75, "▫", "heatmap_l3"),
    (1.01, "█", "heatmap_l4"),
)


# ---------------------------------------------------------------------------
# Stats panel (`devlog stats`)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Stats panel (`devlog stats`)
# ---------------------------------------------------------------------------


def sparkline(values: list[int]) -> str:
    """Build a compact horizontal sparkline using block characters.

    The character height encodes the *relative* value within the
    dataset: the largest value renders as the tallest block (``█``)
    and zero as the shortest (``▁``). Eight discrete levels are used,
    so a one-entry day and a 50-entry day are visually distinct but
    the bars never overflow the cell.

    Args:
        values: one integer per day, oldest first.

    Returns:
        A single string of block characters, one per day.
    """
    if not values:
        return ""
    max_v = max(values) or 1
    blocks = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    out = []
    for v in values:
        idx = min(len(blocks) - 1, int((v / max_v) * len(blocks)))
        out.append(blocks[idx])
    return "".join(out)


def calendar_grid(per_day: dict, *, year: int) -> Text:
    """Build a year-grid heatmap (53 weeks × 7 days) of entry counts.

    Each cell is one character wide and styled with the corresponding
    ``heatmap_*`` theme role. Days outside the requested *year* are
    rendered as a space; days in the year with no entries are also a
    space but use the ``heatmap_empty`` style for clarity.

    The grid is laid out Sunday-first (the international convention)
    and each column is one ISO week. The first column may be padded
    with leading spaces so the 1st of January falls on the correct
    weekday row.

    A single header line above the grid carries the short month name
    (``Jan``, ``Feb`` …), written at the week-column that contains the
    1st of that month — the same layout GitHub's contribution graph
    uses. Months are at least 4 columns apart and labels are 3 chars,
    so the labels never collide.

    Args:
        per_day: ``{YYYY-MM-DD: count}`` map. Dates outside *year* are
            ignored.
        year: the 4-digit year to render.

    Returns:
        A :class:`rich.text.Text` of the grid (one row per weekday,
        with a trailing newline per row, ready to print).
    """
    import calendar as _cal
    import datetime as _dt

    # Find the weekday of Jan 1 (0=Mon, 6=Sun). We render Sunday-first
    # so the first column is the week containing the first Sunday
    # on/before Jan 1.
    jan1 = _dt.date(year, 1, 1)
    dec31 = _dt.date(year, 12, 31)
    # `calendar.SUNDAY = 6` already; .firstweekday is what we want.
    firstweekday = _cal.SUNDAY  # always Sunday for this view
    first_col_pad = (jan1.weekday() - firstweekday) % 7  # 0 if Jan 1 is Sunday
    total_days = (dec31 - jan1).days + 1
    total_cells = first_col_pad + total_days
    num_weeks = (total_cells + 6) // 7  # ceil to whole weeks

    # Pre-compute the max for the relative scale.
    max_count = max(per_day.values()) if per_day else 0

    def _char_for(count: int) -> tuple[str, str]:
        """Map a count to (character, theme role)."""
        if count <= 0 or max_count <= 0:
            return " ", "heatmap_empty"
        ratio = count / max_count
        for threshold, char, role in _HEATMAP_TIERS:
            if ratio <= threshold:
                return char, role
        # Unreachable given the 1.01 sentinel, but keep a sane default.
        return "█", "heatmap_l4"

    grid = Text()
    # The display is a header line followed by 7 data rows (Sun..Sat),
    # one heatmap char per week-column. The header carries one 3-letter
    # month label per month, written at the week-column that contains
    # the 1st of that month — GitHub's contribution-graph layout. The
    # header is padded to ``num_weeks`` chars so the labels align with
    # the cells they describe; a 3-letter label may extend over
    # neighbouring cells, but the alignment stays correct because the
    # cell char for the 1st of the month is drawn in the same column.
    # Months are >= 4 columns apart (shortest month = 4 weeks), so
    # 3-char labels never collide; the shared char array keeps later
    # writes winning if one ever did.
    label_chars = list(" " * num_weeks)
    for month in range(1, 13):
        first = _dt.date(year, month, 1)
        week_col = (first_col_pad + (first - jan1).days) // 7
        label = first.strftime("%b")
        for i, ch in enumerate(label):
            if week_col + i < num_weeks:
                label_chars[week_col + i] = ch
    grid.append("".join(label_chars), style="dim")
    grid.append("\n")

    for weekday in range(7):
        # Data line.
        for week in range(num_weeks):
            day_offset = week * 7 + weekday - first_col_pad
            if day_offset < 0 or day_offset >= total_days:
                grid.append(" ", style=_s("heatmap_empty"))
            else:
                d = jan1 + _dt.timedelta(days=day_offset)
                key = d.strftime("%Y-%m-%d")
                count = per_day.get(key, 0)
                ch, role = _char_for(count)
                grid.append(ch, style=_s(role))
        grid.append("\n")
    return grid
