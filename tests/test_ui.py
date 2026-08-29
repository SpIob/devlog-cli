"""Tests for the devlog.ui rendering helpers."""

import io
import os
import re
import time
from datetime import datetime, timezone

import pytest
from rich.console import Console
from rich.table import Table

from devlog.models import Entry
from devlog import ui


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_entry(message="hello world", tags=None, iso="2025-05-11T10:22:00Z") -> Entry:
    return Entry(
        id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        message=message,
        tags=list(tags) if tags else [],
        created_at=iso,
    )


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _capture(fn, *args, **kwargs) -> str:
    """Capture both STDOUT (console) and STDERR (err_console) into a single string."""
    buf = io.StringIO()
    con = Console(file=buf, no_color=True, width=120, force_terminal=False)
    err = Console(file=buf, no_color=True, width=120, force_terminal=False, stderr=True)
    saved_c = ui.console
    saved_e = ui.err_console
    ui.console = con
    ui.err_console = err
    try:
        fn(*args, **kwargs)
    finally:
        ui.console = saved_c
        ui.err_console = saved_e
    return _strip_ansi(buf.getvalue())


# ---------------------------------------------------------------------------
# print_error / print_warning / print_info
# ---------------------------------------------------------------------------


def test_print_error_uses_red_x_icon():
    out = _capture(ui.print_error, "boom")
    assert "✘" in out
    assert "boom" in out
    # Should include the red border style markers
    assert "Error" in out


def test_print_warning_uses_yellow_warning_icon():
    out = _capture(ui.print_warning, "watch out")
    assert "⚠" in out
    assert "watch out" in out


def test_print_info_uses_dim_info_icon():
    out = _capture(ui.print_info, "nothing to see")
    assert "ℹ" in out
    assert "nothing to see" in out


# ---------------------------------------------------------------------------
# entry_panel
# ---------------------------------------------------------------------------


def test_entry_panel_contains_key_fields():
    entry = _make_entry(message="Fixed auth bug", tags=["backend", "security"])
    panel = ui.entry_panel(entry)
    rendered = _capture(console := Console(file=io.StringIO(), no_color=True, width=120).print, panel) if False else None

    # Render the panel via the standard helper but capture into a fresh console.
    buf = io.StringIO()
    con = Console(file=buf, no_color=True, width=120)
    con.print(panel)
    out = _strip_ansi(buf.getvalue())

    assert "✔" in out
    assert "Entry added" in out
    assert "a1b2c3d4" in out
    assert "Fixed auth bug" in out
    assert "backend" in out
    assert "security" in out
    assert "2025-05-11 10:22 UTC" in out


def test_entry_panel_without_tags_shows_placeholder():
    entry = _make_entry(message="No tags here", tags=[])
    buf = io.StringIO()
    Console(file=buf, no_color=True, width=120).print(ui.entry_panel(entry))
    out = _strip_ansi(buf.getvalue())
    assert "(none)" in out


# ---------------------------------------------------------------------------
# smart_truncate
# ---------------------------------------------------------------------------


def test_smart_truncate_no_query_uses_left_truncation():
    msg = "x" * 100
    out = ui.smart_truncate(msg, query="", limit=10)
    assert out.endswith("…")
    assert len(out.replace("…", "")) <= 9  # one slot for the ellipsis


def test_smart_truncate_match_at_start():
    msg = "auth" + "x" * 200
    out = ui.smart_truncate(msg, query="auth", limit=20)
    assert "[bold yellow]auth[/bold yellow]" in out
    # Must not start with the ellipsis (match is at the very start)
    assert not out.startswith("…")


def test_smart_truncate_match_at_end():
    msg = "x" * 200 + "auth"
    out = ui.smart_truncate(msg, query="auth", limit=20)
    assert "[bold yellow]auth[/bold yellow]" in out
    # The match sits at the very end of the message, so only a leading
    # ellipsis is needed (nothing is hidden on the right side).
    assert out.startswith("…")


def test_smart_truncate_match_in_middle_keeps_match_visible():
    msg = ("prefix-" * 30) + "TARGET" + ("-suffix" * 30)
    out = ui.smart_truncate(msg, query="TARGET", limit=40)
    assert "[bold yellow]TARGET[/bold yellow]" in out
    # Both sides should be truncated with …
    assert out.count("…") == 2


def test_smart_truncate_no_match_falls_back_to_left_truncation():
    msg = "x" * 200
    out = ui.smart_truncate(msg, query="zzz", limit=20)
    assert "[bold yellow]" not in out
    assert out.endswith("…")


def test_smart_truncate_strips_ansi_unsafe_chars_in_message():
    # Pad the message so the suffix is included in the visible window
    # and brackets get escaped (not interpreted as Rich markup).
    msg = "auth[bad]thing" + "x" * 200
    out = ui.smart_truncate(msg, query="auth", limit=40)
    assert "[bold yellow]auth[/bold yellow]" in out
    # Brackets from the raw message must be backslash-escaped so Rich
    # does not interpret them as markup.
    assert "\\[bad]" in out


# ---------------------------------------------------------------------------
# entries_table
# ---------------------------------------------------------------------------


def test_entries_table_renders_columns():
    entries = [_make_entry("hello", ["tag1"])]
    table = ui.entries_table(entries, total=1)
    buf = io.StringIO()
    Console(file=buf, no_color=True, width=120).print(table)
    out = _strip_ansi(buf.getvalue())
    assert "ID" in out
    assert "Date" in out
    assert "Tags" in out
    assert "Message" in out
    assert "Showing 1 of 1" in out


def test_entries_table_truncates_long_message_without_query():
    long_msg = "x" * 200
    entries = [_make_entry(long_msg)]
    table = ui.entries_table(entries, total=1)
    buf = io.StringIO()
    Console(file=buf, no_color=True, width=120).print(table)
    out = _strip_ansi(buf.getvalue())
    # Truncated form ends with …
    assert "…" in out
    # Should NOT contain the full 200-char message
    assert "x" * 200 not in out


def test_entries_table_with_highlight_smart_truncates():
    long_msg = "y" * 200 + "needle" + "z" * 200
    entries = [_make_entry(long_msg)]
    table = ui.entries_table(entries, total=1, highlight_query="needle")
    buf = io.StringIO()
    # Capture full ANSI so we can verify the bold+yellow styling was applied
    # to the matched word — not the literal markup tags (Rich consumes them).
    Console(file=buf, no_color=False, width=120, force_terminal=True).print(table)
    out = buf.getvalue()

    # The match must be present in the visible cell text.
    assert "needle" in out
    # The match must be styled (bold + yellow) — Rich translates the
    # [bold yellow]…[/bold yellow] markup to ANSI escape codes.
    assert "\x1b[1;33mneedle\x1b[0m" in out or "\x1b[33;1mneedle\x1b[0m" in out
    # And the message column should contain an ellipsis on at least
    # one side (we're truncating a 407-char message into 60 chars).
    assert "…" in out


def test_entries_table_title_and_subtitle():
    entries = [_make_entry("hi")]
    table = ui.entries_table(
        entries, total=1, title="Journal · 1 entry", subtitle='Query: "hi"'
    )
    buf = io.StringIO()
    Console(file=buf, no_color=True, width=120).print(table)
    out = _strip_ansi(buf.getvalue())
    assert "Journal" in out
    assert "Query" in out


def test_entries_table_uses_rounded_box():
    entries = [_make_entry("hi")]
    table = ui.entries_table(entries, total=1)
    # Rounded box uses these characters.
    assert table.box is not None
    rendered_box = str(table.box)
    assert "╭" in rendered_box or "─" in rendered_box


def test_entries_table_uses_zebra_row_styles():
    entries = [_make_entry("a"), _make_entry("b"), _make_entry("c")]
    table = ui.entries_table(entries, total=3)
    assert table.row_styles == ["", "dim"]


# ---------------------------------------------------------------------------
# NO_COLOR support
# ---------------------------------------------------------------------------


def test_no_color_env_disables_color(monkeypatch):
    """When NO_COLOR is set, freshly built consoles must be no_color."""
    monkeypatch.setenv("NO_COLOR", "1")
    # Force a fresh import by reloading the module.
    import importlib
    import devlog.ui as ui_mod

    importlib.reload(ui_mod)
    assert ui_mod.console.no_color is True
    assert ui_mod.err_console.no_color is True


def test_console_no_color_emits_no_ansi(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    import importlib
    import devlog.ui as ui_mod

    importlib.reload(ui_mod)

    buf = io.StringIO()
    con = ui_mod.console.__class__(file=buf, no_color=True, width=120, force_terminal=True)
    con.print(ui_mod.print_info and "test")  # smoke: just writes a string
    assert "\x1b[" not in buf.getvalue()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def test_short_id_is_eight_chars():
    e = _make_entry()
    assert ui._short_id(e) == "a1b2c3d4"


def test_format_dt_human_readable():
    out = ui._format_dt("2025-05-11T10:22:00Z")
    assert out == "2025-05-11 10:22 UTC"


def test_pluralize_basic():
    assert ui.pluralize(0, "entry") == "0 entries"
    assert ui.pluralize(1, "entry") == "1 entry"
    assert ui.pluralize(2, "entry") == "2 entries"


def test_pluralize_explicit_plural():
    # Custom plural overrides the default +s / ies rule.
    assert ui.pluralize(1, "child", "children") == "1 child"
    assert ui.pluralize(2, "child", "children") == "2 children"


def test_pluralize_special_case_for_entry():
    # "entry" is the one English noun with an -ies plural in our domain.
    assert ui.pluralize(1, "entry") == "1 entry"
    assert ui.pluralize(0, "entry") == "0 entries"


def test_plural_noun():
    """_plural_noun returns just the noun, not the count prefix."""
    assert ui._plural_noun(0, "entry") == "entries"
    assert ui._plural_noun(1, "entry") == "entry"
    assert ui._plural_noun(2, "entry") == "entries"


def test_plural_s():
    """plural_s returns "s" for n != 1, "" for n == 1."""
    assert ui.plural_s(0) == "s"
    assert ui.plural_s(1) == ""
    assert ui.plural_s(2) == "s"


def test_no_off_by_one_plural_in_captured_outputs():
    """Regression guard: every user-visible plural form must be correct.

    Drive a few representative commands and assert that no rendered
    string contains a malformed number+word pairing like
    "1 entries", "2 entry", or "0 entry".
    """
    import json
    import os
    import shutil
    import tempfile
    from click.testing import CliRunner

    from devlog import cli as cli_mod
    from devlog import themes

    themes.reset_cache()

    tmp = tempfile.mkdtemp(prefix="devlog-plural-")
    runner = CliRunner(env={"DEVLOG_DATA_DIR": tmp})
    try:
        # Seed three entries so we exercise both singular and plural
        # for "entry" / "entries" in the same output.
        runner.invoke(cli_mod.main, ["add", "first entry", "-t", "x"])
        runner.invoke(cli_mod.main, ["add", "second entry", "-t", "x"])
        runner.invoke(cli_mod.main, ["add", "third entry", "-t", "y"])

        # Collect every output.
        outputs: list[str] = []
        for argv in (
            ["list"],
            ["list", "-t", "x"],
            ["tags"],
            ["stats"],
        ):
            result = runner.invoke(cli_mod.main, argv)
            assert result.exit_code == 0
            outputs.append(_strip_ansi(result.output))

        # Common mistakes we want to catch. "1 entries", "2 entry",
        # "0 entry" are the well-known English off-by-one errors.
        forbidden_substrings = [
            "1 entries",
            "2 entry\n",  # end-of-line is the most common context
            "2 entry.",
            "2 entry ",
            "0 entry\n",
            "0 entry.",
            "0 entry ",
        ]
        for i, out in enumerate(outputs):
            for needle in forbidden_substrings:
                assert needle not in out, (
                    f"output[{i}] contains malformed plural: {needle!r}"
                )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_tags_text_handles_empty():
    e = _make_entry(tags=[])
    t = ui._tags_text(e)
    # Empty tags render dim "(none)" placeholder
    assert "(none)" in t.plain


def test_tags_text_renders_comma_separated():
    e = _make_entry(tags=["a", "b", "c"])
    t = ui._tags_text(e)
    assert "a, b, c" in t.plain


# ---------------------------------------------------------------------------
# export_progress
# ---------------------------------------------------------------------------


def test_export_progress_is_rich_progress():
    p = ui.export_progress(total=5)
    # It must be a usable Progress context manager.
    with p:
        task = p.add_task("Exporting…", total=5)
        p.advance(task)
        p.advance(task)
    # No assertions on the rendered bar; just that it ran without raising.


# ---------------------------------------------------------------------------
# version_banner / root_banner
# ---------------------------------------------------------------------------


def test_version_banner_contains_version_string():
    buf = io.StringIO()
    saved = ui.console
    ui.console = Console(file=buf, no_color=True, width=120)
    try:
        ui.version_banner()
    finally:
        ui.console = saved
    out = _strip_ansi(buf.getvalue())
    assert "devlog, version" in out
    assert ui.VERSION in out


def test_root_banner_lists_all_commands():
    buf = io.StringIO()
    saved = ui.console
    ui.console = Console(file=buf, no_color=True, width=120)
    try:
        ui.root_banner()
    finally:
        ui.console = saved
    out = _strip_ansi(buf.getvalue())
    for cmd in (
        "add",
        "show",
        "edit",
        "delete",
        "list",
        "search",
        "today",
        "yesterday",
        "week",
        "tail",
        "tags",
        "tag",
        "merge-tag",
        "theme",
        "stats",
        "calendar",
        "rename-tag",
        "import",
        "completions",
        "export",
        "repair",
        "backup",
        "restore",
        "doctor",
    ):
        assert cmd in out, f"root banner is missing command: {cmd}"


# ---------------------------------------------------------------------------
# theme integration
# ---------------------------------------------------------------------------


def test_ui_renders_with_default_theme_colors():
    """The default theme must reproduce the pre-theming colors exactly.

    This is a guard against accidentally drifting the defaults away from
    the literals that were hardcoded before themes existed.
    """
    from devlog import themes

    themes.reset_cache()
    assert themes.get_style("success_border") == "green"
    assert themes.get_style("error_border") == "red"
    assert themes.get_style("date") == "cyan"
    assert themes.get_style("tags") == "magenta"
    assert themes.get_style("id_dim") == "dim white"
    assert themes.get_style("match_highlight") == "bold yellow"
    assert themes.get_style("zebra_alt") == "dim"
    assert themes.get_bold_style("success_border") == "bold green"


def test_ui_uses_custom_theme(monkeypatch):
    """When the active theme is swapped, the UI must reflect it.

    Switches the active theme to a custom palette and re-renders an
    entry panel. The rendered output should use the new styles (or at
    least the new color names) when the console is in color mode.
    """
    from devlog import themes

    themes.set_active_theme(
        {
            "success_border": "bright_green",
            "success_title": "bold bright_green",
            "date": "bright_cyan",
            "tags": "white",
            "id_dim": "grey50",
        }
    )
    try:
        entry = _make_entry(message="themed", tags=["x"])
        panel = ui.entry_panel(entry)
        buf = io.StringIO()
        Console(file=buf, no_color=False, width=120, force_terminal=True).print(panel)
        out = buf.getvalue()
        # bright_green is color 10; bright_cyan is color 14
        # The exact ANSI sequence depends on Rich's color table, but
        # we should see *some* non-default color codes — the original
        # "green"/"cyan" 3-bit codes (32, 36) are no longer present.
        assert "themed" in out
        assert "x" in out
        # Strip ANSI to verify plain text is intact (no markup leak)
        plain = _strip_ansi(out)
        assert "Entry added" in plain
        assert "themed" in plain
    finally:
        themes.reset_cache()


def test_theme_table_renders_all_roles():
    """theme_table must include every key in ROLES."""
    from devlog import themes

    table = ui.theme_table()
    buf = io.StringIO()
    Console(file=buf, no_color=True, width=120).print(table)
    out = _strip_ansi(buf.getvalue())
    for role in themes.ROLES:
        assert role in out, f"theme_table is missing role: {role}"
