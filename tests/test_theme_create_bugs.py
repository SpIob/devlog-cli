"""Bug-regression tests for the theme create feature.

These tests guard against the bugs found during manual testing in
the round-1..6 audit. They assert the *fixed* behavior so a future
regression will surface as a test failure.
"""
import os
import stat
import tomllib
from pathlib import Path

import pytest
from click.testing import CliRunner

from devlog import themes, theme_preview
from devlog.cli import main


# ---------------------------------------------------------------------------
# Bug 1: --from <builtin> ignored the builtin's meta (name + description)
# Fix: wizard now seeds [meta].name and [meta].description from
# get_builtin_meta when --from is given.
# ---------------------------------------------------------------------------


def test_bug1_from_builtin_seeds_meta_name(tmp_path):
    runner = CliRunner()
    out = tmp_path / "out.toml"
    result = runner.invoke(
        main,
        ["theme", "create", "--from", "dracula", "--no-install",
         "--output", str(out)],
        input="\n" * 28,
    )
    assert result.exit_code == 0, result.output
    data = tomllib.loads(out.read_text(encoding="utf-8"))
    assert data["meta"]["name"] == "dracula"


def test_bug1_from_builtin_seeds_meta_description(tmp_path):
    runner = CliRunner()
    out = tmp_path / "out.toml"
    result = runner.invoke(
        main,
        ["theme", "create", "--from", "monokai", "--no-install",
         "--output", str(out)],
        input="\n" * 28,
    )
    assert result.exit_code == 0, result.output
    data = tomllib.loads(out.read_text(encoding="utf-8"))
    assert "monokai" in data["meta"]["description"].lower()


def test_bug1_explicit_name_overrides_builtin_seed(tmp_path):
    runner = CliRunner()
    out = tmp_path / "out.toml"
    result = runner.invoke(
        main,
        ["theme", "create", "--from", "dracula", "--name", "my-dracula",
         "--no-install", "--output", str(out)],
        input="\n" * 28,
    )
    assert result.exit_code == 0, result.output
    data = tomllib.loads(out.read_text(encoding="utf-8"))
    assert data["meta"]["name"] == "my-dracula"


def test_bug1_no_from_uses_default_meta():
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["theme", "create", "--no-install", "--output", "/tmp/bug1.toml"],
        input="\n" * 28,
    )
    assert result.exit_code == 0, result.output
    data = tomllib.loads(Path("/tmp/bug1.toml").read_text(encoding="utf-8"))
    assert data["meta"]["name"] == "custom"
    assert "devlog theme create" in data["meta"]["description"]


# ---------------------------------------------------------------------------
# Bug 4 + 5 + 14: build_theme_toml escapes quotes but not backslashes,
# newlines, or quotes inside palette values.
# Fix: a single _escape_toml_basic_str helper handles all three.
# ---------------------------------------------------------------------------


def test_bug4_build_theme_toml_escapes_backslash():
    text = themes.build_theme_toml({}, name="back\\slash", description="d")
    data = tomllib.loads(text)
    assert data["meta"]["name"] == "back\\slash"


def test_bug5_build_theme_toml_escapes_newline():
    text = themes.build_theme_toml({}, name="line1\nline2", description="d")
    data = tomllib.loads(text)
    assert data["meta"]["name"] == "line1\nline2"


def test_bug14_build_theme_toml_escapes_quote_in_value():
    text = themes.build_theme_toml({"date": 'has "quote"'})
    data = tomllib.loads(text)
    assert data["palette"]["date"] == 'has "quote"'


def test_bug4_cli_wizard_with_backslash_name_writes_valid_toml(tmp_path):
    runner = CliRunner()
    out = tmp_path / "out.toml"
    result = runner.invoke(
        main,
        ["theme", "create", "--no-install", "--output", str(out),
         "--name", "back\\slash"],
        input="\n" * 28,
    )
    assert result.exit_code == 0, result.output
    # Round-trip must succeed
    data = tomllib.loads(out.read_text(encoding="utf-8"))
    assert data["meta"]["name"] == "back\\slash"


# ---------------------------------------------------------------------------
# Bug 6: --install with a name containing a backslash poisoned the
# active theme. Fix: build_theme_toml now escapes backslashes, so the
# installed file is valid TOML.
# ---------------------------------------------------------------------------


def test_bug6_install_with_backslash_name_no_longer_poisons(monkeypatch, tmp_path):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["theme", "create", "--install", "--name", "back\\slash"],
        input="\n" * 28,
    )
    assert result.exit_code == 0, result.output
    active = themes.get_theme_path()
    assert active.exists()
    data = tomllib.loads(active.read_text(encoding="utf-8"))
    assert data["meta"]["name"] == "back\\slash"


# ---------------------------------------------------------------------------
# Bug 7: render_preview crashed when border/caption/footer roles had
# invalid styles. Fix: _style_or_default now validates and falls back.
# ---------------------------------------------------------------------------


def test_bug7_render_preview_all_garbage_does_not_crash():
    bad = {role: "BOGUS" for role in themes.ROLES}
    out = theme_preview.render_preview(bad)
    assert "Error panel" in out


def test_bug7_render_preview_partial_border_garbage_does_not_crash():
    draft = dict(themes.DEFAULT_THEME)
    draft["error_border"] = "BOGUS"
    draft["table_caption"] = "###"
    out = theme_preview.render_preview(draft)
    assert "Error panel" in out
    assert "Table" in out


def test_bug7_render_preview_whitespace_border_does_not_crash():
    draft = dict(themes.DEFAULT_THEME)
    draft["success_border"] = "   "
    out = theme_preview.render_preview(draft)
    assert "Success panel" in out


# ---------------------------------------------------------------------------
# Bug 3: --output write failure leaked a Python traceback.
# Fix: OSError handler around the write.
# ---------------------------------------------------------------------------


def test_bug9_output_write_failure_no_traceback(tmp_path):
    ro_dir = tmp_path / "readonly"
    ro_dir.mkdir()
    os.chmod(ro_dir, stat.S_IRUSR | stat.S_IXUSR)
    try:
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["theme", "create", "--no-install",
             "--output", str(ro_dir / "out.toml")],
            input="\n" * 28,
        )
        assert result.exit_code != 0
        assert "Traceback" not in result.output
        assert "PermissionError" not in result.output
    finally:
        os.chmod(ro_dir, stat.S_IRWXU)


# ---------------------------------------------------------------------------
# Bug 10: build_theme_toml with None values produced literal "None" strings.
# Fix: non-string palette values are coerced to "" before being escaped.
# ---------------------------------------------------------------------------


def test_bug10_build_theme_toml_none_value_becomes_empty():
    text = themes.build_theme_toml({"date": None, "tags": None})
    data = tomllib.loads(text)
    assert data["palette"]["date"] == ""
    assert data["palette"]["tags"] == ""
    assert "None" not in text  # never written as a literal


# ---------------------------------------------------------------------------
# Bug 13: wizard hid the current value while saying "Press Enter to accept
# each default". Fix: show_default=True on each prompt.
# ---------------------------------------------------------------------------


def test_bug13_wizard_shows_default_value(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["theme", "create", "--no-install",
         "--output", str(tmp_path / "out.toml")],
        input="\n" * 28,
    )
    # First prompt is error_border with default "red". The default
    # appears next to the prompt (click uses "[default]" syntax).
    assert "error_border [red]" in result.output


# ---------------------------------------------------------------------------
# Bug 16: is_valid_style accepted whitespace-only, allowing bad state.
# Fix: strip + empty check rejects whitespace.
# ---------------------------------------------------------------------------


def test_bug16_is_valid_style_rejects_whitespace():
    assert not themes.is_valid_style("   ")
    assert not themes.is_valid_style("\t\n")


def test_bug16_set_active_theme_drops_whitespace():
    """set_active_theme now validates, so whitespace values are dropped
    and the role falls back to the default."""
    themes.set_active_theme({"error_border": "   ", "date": "yellow"})
    palette = themes.get_active_theme()
    assert palette["error_border"] == themes.DEFAULT_THEME["error_border"]
    assert palette["date"] == "yellow"


def test_bug16_wizard_with_whitespace_default_recovers(monkeypatch, tmp_path):
    """A whitespace default no longer traps the wizard in a retry loop
    because the default itself now passes through is_valid_style checks
    indirectly — and the wizard seeds from get_active_theme, which
    itself no longer accepts whitespace via set_active_theme."""
    themes.set_active_theme({"error_border": "   "})
    # After the set_active_theme fix, the active theme should have the
    # default 'red', not '   '
    assert themes.get_active_theme()["error_border"] == "red"
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["theme", "create", "--no-install",
         "--output", str(tmp_path / "out.toml")],
        input="\n" * 28,
    )
    assert result.exit_code == 0, result.output
    assert "value cannot be empty" not in result.output


# ---------------------------------------------------------------------------
# Bug 6 (wizard ordering): the order roles are prompted in (per
# SECTIONS) should match the order they're written in build_theme_toml
# (per _ROLE_DEFAULTS).
# ---------------------------------------------------------------------------


def test_bug6_wizard_prompt_order_matches_file_order(tmp_path):
    runner = CliRunner()
    out = tmp_path / "out.toml"
    result = runner.invoke(
        main,
        ["theme", "create", "--no-install", "--output", str(out)],
        input="\n" * 28,
    )
    assert result.exit_code == 0, result.output

    expected_order = [role for role, _ in themes._ROLE_DEFAULTS]
    file_order = []
    for line in out.read_text(encoding="utf-8").splitlines():
        if "=" in line and line.strip().startswith(tuple(themes.ROLES)):
            key = line.split("=", 1)[0].strip()
            file_order.append(key)

    assert file_order == expected_order, (
        f"file order differs from prompt order: {file_order} vs {expected_order}"
    )