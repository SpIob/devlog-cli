"""Synthetic theme preview renderer for ``devlog theme create``.

Renders a fixed set of UI fixtures (error panel, success panel, entry
row, heatmap strip, banner, table caption + zebra row) styled with a
*draft* palette, so the user can see the result of their choices
without having to install the theme and re-run devlog.

The renderer is *pure*: it takes a ``{role: style}`` mapping and
returns a string. It does **not** mutate :mod:`devlog.themes`'s cached
active theme — that would be visible to the rest of the CLI process.
Instead it parses style strings with :class:`rich.style.Style` and
builds :class:`rich.text.Text` / :class:`rich.panel.Panel` objects
inline.
"""

from __future__ import annotations

from io import StringIO

from rich.box import ROUNDED
from rich.console import Console
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text

from . import themes


_HEADER_RULE = "─" * 60


def _resolve(role: str, draft: dict[str, str]) -> Style | None:
    """Return a parsed :class:`rich.style.Style` for *role* from *draft*.

    Falls back to the built-in default for any missing role. Returns
    ``None`` when the value is empty or unparseable, which lets the
    caller render the swatch without a style (Rich's "default" style).
    """
    value = draft.get(role) or themes.DEFAULT_THEME.get(role, "")
    if not value:
        return None
    try:
        return Style.parse(value)
    except Exception:  # noqa: BLE001 - Rich raises a wide variety
        return None


def _style_or_default(role: str, draft: dict[str, str]) -> str:
    """Return the style string for *role*, or ``"default"`` if missing."""
    return draft.get(role) or themes.DEFAULT_THEME.get(role, "") or "default"


def _section(title: str, body: str) -> str:
    """Return *body* prefixed with a centered section heading."""
    return f"{title}\n{_HEADER_RULE}\n{body}\n"


def _error_panel(draft: dict[str, str]) -> str:
    """Render a synthetic error panel to exercise error_border/text."""
    body = Text()
    body.append("✘ ", style=_resolve("error_text", draft) or "default")
    body.append(
        "could not parse entries.json: unexpected token at line 42",
        style=_resolve("error_text", draft) or "default",
    )
    panel = Panel(
        body,
        border_style=_style_or_default("error_border", draft),
        title="Error",
        title_align="left",
    )
    return _render(panel)


def _success_panel(draft: dict[str, str]) -> str:
    """Render a synthetic success panel to exercise success_* roles."""
    body = Text()
    body.append("Date  ", style="dim")
    body.append("2026-09-05 10:14", style=_resolve("date", draft) or "default")
    body.append("\nTags  ", style="dim")
    body.append("refactor, parser", style=_resolve("tags", draft) or "default")
    body.append("\nNote  ", style="dim")
    body.append("switched to the streaming JSON parser; 2x faster on large files")
    body.append("\n")
    body.append(
        "Use `devlog show a1b2c3d4` to view it again.",
        style="dim italic",
    )
    title = Text()
    title.append("✔ ", style=_resolve("success_title", draft) or "default")
    title.append("Entry added", style=_resolve("success_title", draft) or "default")
    title.append("  ·  a1b2c3d4", style="dim")
    panel = Panel(
        body,
        border_style=_style_or_default("success_border", draft),
        title=title,
        title_align="left",
    )
    return _render(panel)


def _entry_row(draft: dict[str, str]) -> str:
    """Render a synthetic list-style entry row to exercise date/tags/id_dim."""
    line = Text()
    line.append("2026-09-05 10:14", style=_resolve("date", draft) or "default")
    line.append("  ")
    line.append("a1b2c3d4", style=_resolve("id_dim", draft) or "default")
    line.append("  ")
    line.append(
        "refactored the storage layer to stream large entries",
        style="default",
    )
    line.append("  ")
    line.append("#refactor", style=_resolve("tags", draft) or "default")
    line.append("  ")
    line.append("(updated)", style=_resolve("updated", draft) or "default")
    return _render(line)


def _heatmap_strip(draft: dict[str, str]) -> str:
    """Render a one-line heatmap legend to exercise heatmap_* roles."""
    line = Text()
    line.append("less ", style="dim")
    for role in ("heatmap_l1", "heatmap_l2", "heatmap_l3", "heatmap_l4"):
        line.append("▪", style=_resolve(role, draft) or "default")
        line.append(" ", style="dim")
    line.append("more", style="dim")
    return _render(line)


def _banner_line(draft: dict[str, str]) -> str:
    """Render a synthetic banner line to exercise banner_* roles."""
    line = Text()
    line.append("devlog ", style=_resolve("banner_version", draft) or "default")
    line.append("0.18.0", style=_resolve("banner_version", draft) or "default")
    line.append("  ·  ", style="dim")
    line.append("theme create", style=_resolve("banner_command", draft) or "default")
    return _render(line)


def _table_strip(draft: dict[str, str]) -> str:
    """Render a small table snippet to exercise table_caption/zebra_alt/footer."""
    table = Table(
        box=ROUNDED,
        show_header=True,
        caption="Last 7 days",
        caption_style=_style_or_default("table_caption", draft),
        show_footer=True,
        footer_style=_style_or_default("table_footer", draft),
        expand=False,
    )
    table.add_column("Date", style=_resolve("date", draft) or "default", no_wrap=True)
    table.add_column("Note", style="default")
    table.add_column("Tags", style=_resolve("tags", draft) or "default", no_wrap=True)
    table.columns[0].footer = "3 entries"
    zebra = _style_or_default("zebra_alt", draft)
    table.add_row("2026-09-05", "refactor: storage layer", "refactor")
    table.add_row("2026-09-04", "fixed json parser edge case", "bug", style=zebra)
    table.add_row("2026-09-03", "drafted design doc", "writing")
    return _render(table)


def _render(renderable) -> str:
    """Render *renderable* to a string via an isolated StringIO console.

    Width is fixed at 80 so previews are deterministic and
    test-friendly. Color is left at the Rich default (auto) so the
    preview shows what the user would actually see, unless the
    surrounding environment forces ``no_color``.
    """
    buf = StringIO()
    console = Console(file=buf, width=80, force_terminal=False, color_system="truecolor")
    console.print(renderable)
    return buf.getvalue().rstrip("\n")


def render_preview(palette: dict[str, str]) -> str:
    """Return a multi-section preview of *palette* as a single string.

    The returned text is ready to be printed to the user's terminal —
    it includes section headings and the rendered fixtures for every
    role group. Used by ``devlog theme create`` between prompts so the
    user can confirm the palette before committing.

    Args:
        palette: a ``{role: style}`` mapping. Missing roles fall back
            to the built-in defaults. The function does not mutate
            :data:`devlog.themes._active_theme`; styles are parsed
            inline.

    Returns:
        A newline-joined preview string.
    """
    sections = [
        _section("Error panel", _error_panel(palette)),
        _section("Success panel", _success_panel(palette)),
        _section("Entry row", _entry_row(palette)),
        _section("Heatmap legend", _heatmap_strip(palette)),
        _section("Banner", _banner_line(palette)),
        _section("Table", _table_strip(palette)),
    ]
    return "\n".join(sections)


__all__ = ["render_preview"]
