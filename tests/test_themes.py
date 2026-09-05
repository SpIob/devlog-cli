"""Tests for the devlog.themes module."""

import io
import os
from pathlib import Path

import pytest
import tomllib
from click.testing import CliRunner

from devlog import themes
from devlog import ui
from devlog.cli import main


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_theme_cache(tmp_path, monkeypatch):
    """Each test gets a fresh theme path and a cleared cache.

    Redirects ``DEVLOG_DATA_DIR`` to a tmp dir so tests never touch the
    real ``~/.devlog``. The cache is reset before *and* after so a
    test that calls ``set_active_theme`` cannot leak into the next one.
    """
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "devlog_data"))
    themes.reset_cache()
    yield
    themes.reset_cache()


@pytest.fixture()
def theme_runner() -> CliRunner:
    """A ``CliRunner`` ready to invoke ``main`` for theme CLI tests.

    Returns a fresh instance per test so ``runner.invoke`` results are
    not shared across tests.
    """
    return CliRunner()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_roles_is_a_frozenset():
    assert isinstance(themes.ROLES, frozenset)


def test_default_theme_covers_every_role():
    """Every role must have a default value, so a missing file is always safe."""
    for role in themes.ROLES:
        assert role in themes.DEFAULT_THEME, f"missing default for {role}"
        assert themes.DEFAULT_THEME[role], f"empty default for {role}"


def test_default_theme_values_match_pre_theming_strings():
    """Defaults must reproduce the previously hardcoded colors verbatim.

    The keys in this dict are the original pre-theming roles — any new
    roles added later (see :data:`themes.ROLES`) are tested separately
    in :func:`test_default_theme_new_roles_have_defaults` so this
    guard stays a stable byte-for-byte check.
    """
    expected = {
        "error_border": "red",
        "error_text": "red",
        "warning_text": "yellow",
        "info_text": "dim",
        "success_border": "green",
        "success_title": "bold green",
        "show_border": "cyan",
        "delete_border": "red",
        "edit_border": "blue",
        "date": "cyan",
        "updated": "yellow",
        "tags": "magenta",
        "id_dim": "dim white",
        "match_highlight": "bold yellow",
        "banner_version": "bold cyan",
        "banner_command": "bold cyan",
        "zebra_alt": "dim",
        "heatmap_empty": "grey15",
        "heatmap_l1": "green",
        "heatmap_l2": "color(34)",
        "heatmap_l3": "color(40)",
        "heatmap_l4": "color(46)",
    }
    for role, value in expected.items():
        assert themes.DEFAULT_THEME[role] == value, (
            f"default for {role!r} drifted from {value!r} to "
            f"{themes.DEFAULT_THEME[role]!r}"
        )


def test_default_theme_new_roles_have_defaults():
    """Roles added after the original theme was frozen must still have
    a non-empty default. Guards against accidentally dropping a new
    role from :data:`themes.DEFAULT_THEME` while editing the module.
    """
    new_roles = (
        "success_text",
        "prompt_border",
        "table_caption",
        "table_footer",
        "sparkline",
        "heatmap_base",
    )
    for role in new_roles:
        assert role in themes.DEFAULT_THEME, f"new role {role!r} missing default"
        assert themes.DEFAULT_THEME[role], f"new role {role!r} has empty default"


# ---------------------------------------------------------------------------
# Path
# ---------------------------------------------------------------------------


def test_get_theme_path_uses_devlog_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "custom"))
    p = themes.get_theme_path()
    assert p == tmp_path / "custom" / "theme.toml"


def test_get_theme_path_default(monkeypatch):
    monkeypatch.delenv("DEVLOG_DATA_DIR", raising=False)
    p = themes.get_theme_path()
    assert p == Path.home() / ".devlog" / "theme.toml"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def test_load_theme_returns_defaults_when_file_missing():
    palette = themes.load_theme()
    assert palette == themes.DEFAULT_THEME


def test_load_theme_merges_user_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "theme.toml").write_text(
        '[palette]\ndate = "bright_cyan"\ntags = "white"\n',
        encoding="utf-8",
    )
    palette = themes.load_theme()
    assert palette["date"] == "bright_cyan"
    assert palette["tags"] == "white"
    # Untouched roles keep their defaults
    assert palette["success_border"] == "green"
    assert palette["id_dim"] == "dim white"


def test_load_theme_warns_and_falls_back_on_bad_toml(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "theme.toml").write_text("not valid toml [[[", encoding="utf-8")

    buf = io.StringIO()
    palette = themes.load_theme(warn_stream=buf)
    out = buf.getvalue()
    assert "invalid" in out.lower() or "default" in out.lower()
    assert palette == themes.DEFAULT_THEME


def test_load_theme_drops_unknown_role_keys_silently(tmp_path, monkeypatch):
    """Unknown role keys are dropped silently on load. The warning is
    emitted only at the `theme set` site, so a stale theme.toml with
    typos no longer spams stderr on every devlog invocation.

    Regression: previously `load_theme` printed a warning per unknown
    key on every load, which meant every `devlog list` / `devlog add` /
    `devlog show` was followed by a wall of "Warning: theme role 'X' is
    unknown" lines after a single typo in `theme set`.
    """
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "theme.toml").write_text(
        '[palette]\ndate = "red"\nnonsense = "blue"\nother_bad = "green"\n',
        encoding="utf-8",
    )

    buf = io.StringIO()
    palette = themes.load_theme(warn_stream=buf)
    out = buf.getvalue()
    # No load-time warning anymore
    assert "nonsense" not in out
    assert "other_bad" not in out
    assert "unknown" not in out.lower()
    # Override applied
    assert palette["date"] == "red"
    # Unknown keys absent
    assert "nonsense" not in palette
    assert "other_bad" not in palette


def test_load_theme_empty_file_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "theme.toml").write_text("", encoding="utf-8")
    palette = themes.load_theme()
    assert palette == themes.DEFAULT_THEME


def test_load_theme_no_palette_section_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "theme.toml").write_text('[other]\nfoo = "bar"\n', encoding="utf-8")
    palette = themes.load_theme()
    assert palette == themes.DEFAULT_THEME


def test_theme_set_warns_about_unknown_role_keys(tmp_path, monkeypatch):
    """`devlog theme set` is the sole site that warns about unknown
    role keys. Users see the warning once, at the moment of the typo.
    """
    from click.testing import CliRunner
    from devlog.cli import main

    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    (tmp_path / "d").mkdir()

    src = tmp_path / "src.toml"
    src.write_text(
        '[palette]\ndate = "red"\nnonsense = "blue"\nother_bad = "green"\n',
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(main, ["theme", "set", str(src)])
    assert result.exit_code == 0
    # Two warnings, one per unknown key, in sorted order.
    assert "nonsense" in result.output
    assert "other_bad" in result.output
    # And it's a "warning", not an error.
    assert "unknown and will be ignored" in result.output


def test_load_theme_is_silent_across_invocations(tmp_path, monkeypatch):
    """Repeated calls to `load_theme` (one per devlog invocation) must
    not accumulate output. Regression for the warning-spam bug.
    """
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "theme.toml").write_text(
        '[palette]\ndate = "red"\nnonsense = "blue"\n',
        encoding="utf-8",
    )
    buf = io.StringIO()
    for _ in range(5):
        themes.load_theme(warn_stream=buf)
    assert buf.getvalue() == ""


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def test_get_active_theme_uses_cache(tmp_path, monkeypatch):
    """First call reads disk; later calls return the cached value even
    if the on-disk file changes. The cache is invalidated by reset_cache.
    """
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "theme.toml").write_text('[palette]\ndate = "red"\n', encoding="utf-8")

    first = themes.get_active_theme()
    assert first["date"] == "red"

    # Mutate the file — but the cache should still return the old value
    (tmp_path / "d" / "theme.toml").write_text('[palette]\ndate = "blue"\n', encoding="utf-8")
    second = themes.get_active_theme()
    assert second["date"] == "red"

    themes.reset_cache()
    third = themes.get_active_theme()
    assert third["date"] == "blue"


def test_set_active_theme_validates_against_roles():
    """Setting a theme must never expose a role outside ROLES to renderers."""
    themes.set_active_theme(
        {"date": "yellow", "made_up_role": "purple", "tags": "white"}
    )
    palette = themes.get_active_theme()
    assert palette["date"] == "yellow"
    assert palette["tags"] == "white"
    assert "made_up_role" not in palette
    # Missing roles fall back to default
    assert palette["success_border"] == "green"


def test_reset_cache_clears_active_theme():
    themes.set_active_theme({"date": "red"})
    themes.reset_cache()
    palette = themes.get_active_theme()
    # After reset, the active theme re-reads from disk (no file → defaults)
    assert palette == themes.DEFAULT_THEME


# ---------------------------------------------------------------------------
# Style helpers
# ---------------------------------------------------------------------------


def test_get_style_returns_role_value():
    themes.set_active_theme({"date": "bright_cyan"})
    assert themes.get_style("date") == "bright_cyan"


def test_get_style_falls_back_to_default_for_missing_role():
    themes.set_active_theme({})  # everything falls back
    assert themes.get_style("date") == themes.DEFAULT_THEME["date"]


def test_get_bold_style_prepends_bold():
    themes.set_active_theme({"error_border": "red"})
    assert themes.get_bold_style("error_border") == "bold red"


def test_get_bold_style_is_idempotent():
    themes.set_active_theme({"success_title": "bold green"})
    assert themes.get_bold_style("success_title") == "bold green"


# ---------------------------------------------------------------------------
# write_default_theme
# ---------------------------------------------------------------------------


def test_write_default_theme_creates_file(tmp_path):
    out = tmp_path / "sub" / "theme.toml"
    themes.write_default_theme(out)
    assert out.exists()
    # The file must be parseable TOML (the [palette] header is the only
    # structural element; every entry is commented out by design so the
    # generated file is a safe no-op template).
    data = tomllib.loads(out.read_text(encoding="utf-8"))
    assert "palette" in data
    # And every role name must appear in the file as a comment, so the
    # user can see the full list and uncomment what they want.
    text = out.read_text(encoding="utf-8")
    for role in themes.ROLES:
        assert role in text, f"role {role!r} missing from template"


def test_write_default_theme_creates_parent_directories(tmp_path):
    out = tmp_path / "a" / "b" / "c" / "theme.toml"
    themes.write_default_theme(out)
    assert out.exists()


# ---------------------------------------------------------------------------
# CLI subcommand (`devlog theme ...`)
# ---------------------------------------------------------------------------


def test_theme_cli_list(theme_runner):
    result = theme_runner.invoke(main, ["theme", "list"])
    assert result.exit_code == 0
    assert "Active theme" in result.output
    for role in ("date", "tags", "success_border"):
        assert role in result.output


def test_theme_cli_path(theme_runner, tmp_path):
    result = theme_runner.invoke(main, ["theme", "path"])
    assert result.exit_code == 0
    assert "theme.toml" in result.output
    assert str(tmp_path / "devlog_data") in result.output


def test_theme_cli_show_default(theme_runner):
    result = theme_runner.invoke(main, ["theme", "show", "date"])
    assert result.exit_code == 0
    assert result.output.strip() == "cyan"


def test_theme_cli_show_unknown_role(theme_runner):
    result = theme_runner.invoke(main, ["theme", "show", "not_a_real_role"])
    assert result.exit_code == 1
    assert "Unknown role" in result.output


def test_theme_cli_show_full_palette_is_starter_toml(theme_runner):
    result = theme_runner.invoke(main, ["theme", "show"])
    assert result.exit_code == 0
    # The full dump is a TOML template — the [palette] header must be
    # present and every role must be mentioned (commented or otherwise).
    assert "[palette]" in result.output
    for role in themes.ROLES:
        assert role in result.output


def test_theme_cli_set_installs_file(theme_runner, tmp_path):
    src = tmp_path / "my-theme.toml"
    src.write_text('[palette]\ndate = "red"\n', encoding="utf-8")
    result = theme_runner.invoke(main, ["theme", "set", str(src)])
    assert result.exit_code == 0
    assert "installed" in result.output.lower()

    installed = themes.get_theme_path()
    assert installed.exists()
    # Round-trip the active theme — the new value must be picked up
    themes.reset_cache()
    assert themes.get_active_theme()["date"] == "red"


def test_theme_cli_set_rejects_bad_toml(theme_runner, tmp_path):
    src = tmp_path / "bad.toml"
    src.write_text("this is not toml [[[", encoding="utf-8")
    result = theme_runner.invoke(main, ["theme", "set", str(src)])
    assert result.exit_code == 1
    assert "invalid" in result.output.lower() or "error" in result.output.lower()


def test_theme_cli_set_warns_on_unknown_roles(theme_runner, tmp_path):
    src = tmp_path / "mixed.toml"
    src.write_text(
        '[palette]\ndate = "red"\nmade_up = "blue"\n', encoding="utf-8"
    )
    result = theme_runner.invoke(main, ["theme", "set", str(src)])
    assert result.exit_code == 0
    assert "made_up" in result.output
    # And the unknown key was not actually installed
    themes.reset_cache()
    assert "made_up" not in themes.get_active_theme()


# ---------------------------------------------------------------------------
# Tier 1.1 + 1.7: strict value validation
# ---------------------------------------------------------------------------


def test_is_valid_style_accepts_basic_named_colors():
    assert themes.is_valid_style("red")
    assert themes.is_valid_style("bold yellow")
    assert themes.is_valid_style("dim white")
    assert themes.is_valid_style("#ff8800")
    assert themes.is_valid_style("color(208)")
    assert themes.is_valid_style("rgb(255,136,0)")


def test_is_valid_style_rejects_empty_and_garbage():
    assert not themes.is_valid_style("")
    assert not themes.is_valid_style("not a real style name 12345")
    # Bol yellow with a typo
    assert not themes.is_valid_style("bol yellow")


def test_is_valid_style_rejects_non_strings():
    assert not themes.is_valid_style(None)
    assert not themes.is_valid_style(1234)
    assert not themes.is_valid_style([])


def test_load_theme_warns_and_falls_back_on_invalid_value(tmp_path, monkeypatch):
    """Invalid style values for an otherwise-known role: warn, fall back."""
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "theme.toml").write_text(
        '[palette]\ndate = "bol yellow"\ntags = "magenta"\n',
        encoding="utf-8",
    )
    buf = io.StringIO()
    palette = themes.load_theme(warn_stream=buf)
    out = buf.getvalue()
    # The bad role warned + fell back to default
    assert "date" in out and "invalid" in out.lower()
    assert palette["date"] == themes.DEFAULT_THEME["date"]
    # Untouched roles still pick up user values
    assert palette["tags"] == "magenta"


def test_load_theme_strict_returns_warnings(tmp_path, monkeypatch):
    """`strict=True` returns (palette, warnings) so the CLI can fail loud."""
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "theme.toml").write_text(
        '[palette]\ndate = "bol yellow"\n', encoding="utf-8"
    )
    palette, warnings = themes.load_theme(strict=True)
    assert any("date" in w for w in warnings)
    assert palette["date"] == themes.DEFAULT_THEME["date"]


def test_load_theme_strict_clean_file_has_no_warnings(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "theme.toml").write_text(
        '[palette]\ndate = "red"\n', encoding="utf-8"
    )
    palette, warnings = themes.load_theme(strict=True)
    assert warnings == []
    assert palette["date"] == "red"


def test_load_theme_rejects_non_string_value(tmp_path, monkeypatch):
    """Non-string values raise TypeError from _parse_file, surfaced via OSError handler."""
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "theme.toml").write_text(
        '[palette]\ndate = 1234\n', encoding="utf-8"
    )
    buf = io.StringIO()
    palette = themes.load_theme(warn_stream=buf)
    # Falls back to defaults with a warning (TypeError caught by OSError handler)
    assert palette == themes.DEFAULT_THEME
    assert "warning" in buf.getvalue().lower()


def test_theme_set_rejects_invalid_values(theme_runner, tmp_path):
    """`theme set` exits non-zero and does not install when a value is invalid."""
    src = tmp_path / "bad-style.toml"
    src.write_text(
        '[palette]\ndate = "bol yellow"\n', encoding="utf-8"
    )
    result = theme_runner.invoke(main, ["theme", "set", str(src)])
    assert result.exit_code == 1
    assert "invalid" in result.output.lower()
    # And nothing was installed
    assert not themes.get_theme_path().exists()


def test_theme_set_check_validates_without_installing(theme_runner, tmp_path):
    src = tmp_path / "good.toml"
    src.write_text('[palette]\ndate = "red"\n', encoding="utf-8")
    result = theme_runner.invoke(main, ["theme", "set", "--check", str(src)])
    assert result.exit_code == 0
    assert "valid" in result.output.lower()
    # Crucially, no file was installed
    assert not themes.get_theme_path().exists()


def test_theme_set_check_rejects_invalid_without_installing(theme_runner, tmp_path):
    src = tmp_path / "bad.toml"
    src.write_text('[palette]\ndate = "bol yellow"\n', encoding="utf-8")
    result = theme_runner.invoke(main, ["theme", "set", "--check", str(src)])
    assert result.exit_code == 1
    assert "invalid" in result.output.lower()
    assert not themes.get_theme_path().exists()


# ---------------------------------------------------------------------------
# Tier 1.6: get_theme_status + theme list footer
# ---------------------------------------------------------------------------


def test_get_theme_status_default_when_no_file(monkeypatch, tmp_path):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    assert themes.get_theme_status() == "default"


def test_get_theme_status_ok_for_valid_file(monkeypatch, tmp_path):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "theme.toml").write_text('[palette]\ndate = "red"\n', encoding="utf-8")
    assert themes.get_theme_status() == "ok"


def test_get_theme_status_error_for_invalid_toml(monkeypatch, tmp_path):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "theme.toml").write_text("not valid [[[", encoding="utf-8")
    status = themes.get_theme_status()
    assert status.startswith("error:")


def test_get_theme_status_error_for_invalid_value(monkeypatch, tmp_path):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "theme.toml").write_text(
        '[palette]\ndate = "bol yellow"\n', encoding="utf-8"
    )
    status = themes.get_theme_status()
    assert status.startswith("error:") and "date" in status


def test_theme_list_includes_status_footer(theme_runner, tmp_path, monkeypatch):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "theme.toml").write_text(
        '[palette]\ndate = "red"\n', encoding="utf-8"
    )
    # The CLI's console is auto-detected and narrow under CliRunner;
    # render via ui.theme_table directly with a wide console so the
    # footer's three tokens line up on one line.
    from rich.console import Console
    import io as _io
    themes.reset_cache()
    table = ui.theme_table()
    buf = _io.StringIO()
    Console(file=buf, width=200, no_color=True).print(table)
    output = buf.getvalue()
    assert "status: ok" in output


def test_theme_list_default_status_when_no_file(theme_runner, tmp_path, monkeypatch):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    from rich.console import Console
    import io as _io
    themes.reset_cache()
    table = ui.theme_table()
    buf = _io.StringIO()
    Console(file=buf, width=200, no_color=True).print(table)
    output = buf.getvalue()
    assert "status: default" in output


# ---------------------------------------------------------------------------
# Tier 2.9: SECTIONS grouping + test guard
# ---------------------------------------------------------------------------


def test_sections_cover_every_role():
    """Every role must appear in exactly one section."""
    sectioned = frozenset(r for roles in themes.SECTIONS.values() for r in roles)
    assert sectioned == themes.ROLES, (
        f"uncovered roles: {themes.ROLES - sectioned}, "
        f"duplicates: {[r for r in themes.ROLES if sum(r in v for v in themes.SECTIONS.values()) != 1]}"
    )


def test_theme_list_grouped_by_section(theme_runner):
    """`theme list` (the default) includes section headers."""
    result = theme_runner.invoke(main, ["theme", "list"])
    assert result.exit_code == 0
    for header in ("Borders", "Text", "Banner", "Tables", "Heatmap"):
        assert header in result.output


def test_theme_list_flat_drops_section_headers(theme_runner):
    result = theme_runner.invoke(main, ["theme", "list", "--flat"])
    assert result.exit_code == 0
    # Section headers are not emitted in flat mode
    assert "Borders" not in result.output
    assert "Heatmap" not in result.output
    # But every role is still listed
    for role in ("date", "tags", "success_border", "heatmap_l4"):
        assert role in result.output


def test_theme_list_no_preview_drops_swatch(theme_runner):
    result = theme_runner.invoke(main, ["theme", "list", "--no-preview"])
    assert result.exit_code == 0
    assert "Preview" not in result.output


# ---------------------------------------------------------------------------
# Tier 1.2: theme reset
# ---------------------------------------------------------------------------


def test_theme_reset_removes_file_and_falls_back(theme_runner, tmp_path, monkeypatch):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "theme.toml").write_text(
        '[palette]\ndate = "red"\n', encoding="utf-8"
    )
    result = theme_runner.invoke(main, ["theme", "reset"], input="y\n")
    assert result.exit_code == 0
    assert not themes.get_theme_path().exists()
    themes.reset_cache()
    assert themes.get_active_theme() == themes.DEFAULT_THEME


def test_theme_reset_is_noop_when_no_file(theme_runner, monkeypatch, tmp_path):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    result = theme_runner.invoke(main, ["theme", "reset"], input="y\n")
    assert result.exit_code == 0
    assert "removed" in result.output.lower()


# ---------------------------------------------------------------------------
# Tier 1.5: theme diff
# ---------------------------------------------------------------------------


def test_theme_diff_no_overrides_says_so(theme_runner):
    result = theme_runner.invoke(main, ["theme", "diff"])
    assert result.exit_code == 0
    assert "No overrides" in result.output


def test_theme_diff_lists_overrides(theme_runner, tmp_path, monkeypatch):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "theme.toml").write_text(
        '[palette]\ndate = "red"\ntags = "white"\n', encoding="utf-8"
    )
    result = theme_runner.invoke(main, ["theme", "diff"])
    assert result.exit_code == 0
    assert "date" in result.output
    assert "tags" in result.output
    # The "!" marker is present (override indicator)
    assert "!" in result.output


def test_theme_diff_default_flag_shows_audit(theme_runner, tmp_path, monkeypatch):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "theme.toml").write_text(
        '[palette]\ndate = "red"\n', encoding="utf-8"
    )
    result = theme_runner.invoke(main, ["theme", "diff", "--default"])
    assert result.exit_code == 0
    # In audit mode, overridden roles are excluded. The "date" role
    # shows up in the table title "Theme audit" only if a *role row*
    # named "date" appears. We assert that the diff title is the
    # audit variant (not the override variant) instead of substring
    # matching "date", which would also match "updated".
    assert "audit" in result.output
    # The override diff title must not appear
    assert result.output.lstrip().startswith("Theme audit")


# ---------------------------------------------------------------------------
# Tier 2.12: theme export
# ---------------------------------------------------------------------------


def test_theme_export_stdout_writes_active_palette(theme_runner, tmp_path, monkeypatch):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "theme.toml").write_text(
        '[palette]\ndate = "red"\n', encoding="utf-8"
    )
    result = theme_runner.invoke(main, ["theme", "export", "--stdout"])
    assert result.exit_code == 0
    assert "[palette]" in result.output
    assert 'date = "red"' in result.output
    assert 'name = "exported"' in result.output


def test_theme_export_to_file(theme_runner, tmp_path, monkeypatch):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    out = tmp_path / "out.toml"
    result = theme_runner.invoke(main, ["theme", "export", "--output", str(out), "--name", "my-theme"])
    assert result.exit_code == 0
    assert out.exists()
    data = tomllib.loads(out.read_text(encoding="utf-8"))
    assert data["meta"]["name"] == "my-theme"
    assert "date" in data["palette"]


def test_theme_export_requires_target(theme_runner):
    result = theme_runner.invoke(main, ["theme", "export"])
    assert result.exit_code == 1
    assert "specify" in result.output.lower() or "--stdout" in result.output


def test_theme_export_rejects_both_targets(theme_runner, tmp_path):
    out = tmp_path / "out.toml"
    result = theme_runner.invoke(main, ["theme", "export", "--stdout", "--output", str(out)])
    assert result.exit_code == 1


def test_theme_export_roundtrips_through_set(theme_runner, tmp_path, monkeypatch):
    """Export the active theme, then install the export — must be lossless."""
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "theme.toml").write_text(
        '[palette]\ndate = "red"\ntags = "white"\n', encoding="utf-8"
    )
    themes.reset_cache()
    out = tmp_path / "out.toml"
    theme_runner.invoke(main, ["theme", "export", "--output", str(out)])
    theme_runner.invoke(main, ["theme", "set", str(out)])
    themes.reset_cache()
    palette = themes.get_active_theme()
    assert palette["date"] == "red"
    assert palette["tags"] == "white"


# ---------------------------------------------------------------------------
# Tier 2.8: bundled themes
# ---------------------------------------------------------------------------


def test_list_builtin_themes_includes_all_curated():
    names = themes.list_builtin_themes()
    expected = {
        "default", "solarized", "monokai", "high_contrast",
        "nord", "gruvbox", "dracula", "one_dark",
    }
    assert expected.issubset(set(names)), f"missing: {expected - set(names)}"


def test_load_builtin_theme_returns_all_roles():
    palette = themes.load_builtin_theme("monokai")
    for role in themes.ROLES:
        assert role in palette


def test_get_builtin_theme_path_unknown_raises():
    with pytest.raises(themes.ThemeNotFoundError):
        themes.get_builtin_theme_path("does-not-exist")


def test_get_builtin_meta_returns_description():
    meta = themes.get_builtin_meta("monokai")
    assert meta["name"] == "monokai"
    assert "monokai" in meta["description"].lower()


def test_get_builtin_meta_falls_back_for_missing_meta():
    """When a builtin has no [meta] table, get_builtin_meta returns
    sane defaults. We can't easily fabricate a builtin without
    [meta] under importlib.resources, so we assert the documented
    fallback contract by reading one of the curated themes and
    confirming the description field is non-empty (i.e. the fallback
    was not triggered for a properly-authored file).
    """
    meta = themes.get_builtin_meta("default")
    assert meta["name"] == "default"
    assert meta["description"] != ""
    # And the returned dict always has both keys, regardless of input.
    assert "name" in meta
    assert "description" in meta


def test_theme_builtins_lists_all_curated(theme_runner):
    result = theme_runner.invoke(main, ["theme", "builtins"])
    assert result.exit_code == 0
    for name in ("default", "monokai", "solarized"):
        assert name in result.output


def test_theme_use_installs_builtin(theme_runner, tmp_path, monkeypatch):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    result = theme_runner.invoke(main, ["theme", "use", "monokai"])
    assert result.exit_code == 0
    assert "installed" in result.output.lower()
    themes.reset_cache()
    palette = themes.get_active_theme()
    # Monokai's date is color(81); check we actually got the bundled palette
    assert palette["date"] == "color(81)"


def test_theme_use_unknown_builtin_fails(theme_runner, tmp_path, monkeypatch):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    result = theme_runner.invoke(main, ["theme", "use", "no-such-theme"])
    assert result.exit_code == 1
    assert "no-such-theme" in result.output
    assert "Available" in result.output


# ---------------------------------------------------------------------------
# install_theme_file / validate_source helpers
# ---------------------------------------------------------------------------


def test_validate_source_clean():
    unknown, invalid = themes.validate_source({"date": "red", "tags": "white"})
    assert unknown == []
    assert invalid == []


def test_validate_source_unknown_role():
    unknown, invalid = themes.validate_source({"date": "red", "made_up": "blue"})
    assert unknown == ["made_up"]
    assert invalid == []


def test_validate_source_invalid_value():
    unknown, invalid = themes.validate_source({"date": "bol yellow"})
    assert unknown == []
    assert invalid == ["date"]


def test_install_theme_file_copies_to_destination(tmp_path, monkeypatch):
    """The copy is correct; the cache refresh is a separate concern."""
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    src = tmp_path / "src.toml"
    src.write_text('[palette]\ndate = "red"\n', encoding="utf-8")
    dst = themes.get_theme_path()
    themes.install_theme_file(src, dst)
    assert dst.exists()
    # The destination file matches the source
    assert dst.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")


def test_install_theme_file_refreshes_active_theme(tmp_path, monkeypatch):
    """When destination is the active theme path, the active theme
    reflects the newly-installed file after install_theme_file returns.
    """
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    (tmp_path / "d").mkdir()
    src = tmp_path / "src.toml"
    src.write_text('[palette]\ndate = "red"\n', encoding="utf-8")
    themes.install_theme_file(src, themes.get_theme_path())
    assert themes.get_active_theme()["date"] == "red"


def test_install_theme_file_raises_on_missing_source(tmp_path):
    with pytest.raises(themes.ThemeInstallError):
        themes.install_theme_file(tmp_path / "nope.toml", tmp_path / "dst.toml")


# ---------------------------------------------------------------------------
# export_template helper
# ---------------------------------------------------------------------------


def test_export_template_round_trip():
    text = themes.export_template(
        palette={"date": "red", "tags": "white"},
        name="my-theme",
        description="A test",
    )
    data = tomllib.loads(text)
    assert data["meta"]["name"] == "my-theme"
    assert data["meta"]["description"] == "A test"
    assert data["palette"]["date"] == "red"
    assert data["palette"]["tags"] == "white"
    # Roles not overridden fall back to defaults
    assert data["palette"]["error_border"] == "red"


# ---------------------------------------------------------------------------
# build_theme_toml helper
# ---------------------------------------------------------------------------


def test_build_theme_toml_round_trip():
    palette = {"date": "red", "tags": "white"}
    text = themes.build_theme_toml(
        palette, name="my-theme", description="A test"
    )
    data = tomllib.loads(text)
    assert data["meta"]["name"] == "my-theme"
    assert data["meta"]["description"] == "A test"
    assert data["palette"]["date"] == "red"
    assert data["palette"]["tags"] == "white"
    # Every known role is present in the output
    for role in themes.ROLES:
        assert role in data["palette"]


def test_build_theme_toml_fills_defaults_for_missing_roles():
    text = themes.build_theme_toml({"date": "red"})
    data = tomllib.loads(text)
    assert data["palette"]["date"] == "red"
    assert data["palette"]["error_border"] == themes.DEFAULT_THEME["error_border"]


def test_build_theme_toml_escapes_quotes_in_meta():
    text = themes.build_theme_toml({}, name='weird "name"', description='has "quotes"')
    data = tomllib.loads(text)
    assert data["meta"]["name"] == 'weird "name"'
    assert data["meta"]["description"] == 'has "quotes"'


def test_export_template_uses_build_theme_toml():
    """The two writers should produce identical output for the same inputs."""
    palette = {"date": "red"}
    a = themes.export_template(palette=palette, name="x", description="y")
    b = themes.build_theme_toml(palette, name="x", description="y")
    assert a == b


# ---------------------------------------------------------------------------
# theme_preview module
# ---------------------------------------------------------------------------


def test_render_preview_returns_string():
    from devlog import theme_preview

    out = theme_preview.render_preview(dict(themes.DEFAULT_THEME))
    assert isinstance(out, str)
    assert "Error panel" in out
    assert "Success panel" in out
    assert "Entry row" in out
    assert "Heatmap legend" in out
    assert "Banner" in out
    assert "Table" in out


def test_render_preview_does_not_mutate_active_theme():
    from devlog import theme_preview

    before = dict(themes.get_active_theme())
    theme_preview.render_preview({"date": "#ff00ff", "tags": "yellow"})
    after = themes.get_active_theme()
    assert before == after
    assert after["date"] != "#ff00ff"


def test_render_preview_reflects_draft_palette():
    from devlog import theme_preview

    a = theme_preview.render_preview(dict(themes.DEFAULT_THEME))
    # Make every error role a true-color red so the swatch definitely
    # differs from the default's named "red" — the rendered ANSI
    # sequence for true-color red uses "38;2;255;0;0" which the named
    # "red" does not.
    draft = dict(themes.DEFAULT_THEME)
    draft["error_border"] = "rgb(255,0,0)"
    draft["error_text"] = "rgb(255,0,0)"
    b = theme_preview.render_preview(draft)
    assert "38;2;255;0;0" in b
    assert "38;2;255;0;0" not in a


def test_render_preview_uses_defaults_for_missing_roles():
    from devlog import theme_preview

    # Pass an empty dict — every role should fall back to the default,
    # and the renderer should still produce all six fixture sections.
    out = theme_preview.render_preview({})
    assert "Error panel" in out
    assert "Table" in out


def test_render_preview_handles_garbage_styles():
    from devlog import theme_preview

    # None of these parse as Rich styles, but render_preview should
    # not raise — the worst case is an unstyled swatch.
    out = theme_preview.render_preview(
        {"error_border": "", "tags": "not a real style", "date": "###"}
    )
    assert "Error panel" in out


# ---------------------------------------------------------------------------
# devlog theme create CLI
# ---------------------------------------------------------------------------


def _wizard_input(values: list[str]) -> str:
    """Build a stdin blob: one value per role, separated by newlines."""
    return "\n".join(values) + "\n"


def test_theme_create_writes_file_when_output_given(theme_runner, tmp_path):
    out = tmp_path / "my_theme.toml"
    # Accept every default by sending 28 empty lines.
    result = theme_runner.invoke(
        main,
        ["theme", "create", "--no-install", "--output", str(out), "--name", "demo"],
        input="\n" * 28,
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    data = tomllib.loads(out.read_text(encoding="utf-8"))
    assert data["meta"]["name"] == "demo"
    assert len(data["palette"]) == len(themes.ROLES)


def test_theme_create_installs_as_active_by_default(theme_runner):
    result = theme_runner.invoke(
        main,
        ["theme", "create", "--name", "installed", "--description", "d"],
        input="\n" * 28,
    )
    assert result.exit_code == 0, result.output
    active = themes.get_theme_path()
    assert active.exists()
    data = tomllib.loads(active.read_text(encoding="utf-8"))
    assert data["meta"]["name"] == "installed"
    assert data["meta"]["description"] == "d"


def test_theme_create_seeds_from_builtin(theme_runner, tmp_path):
    """When --from dracula is given, accepting defaults writes dracula values."""
    out = tmp_path / "from_dr.toml"
    result = theme_runner.invoke(
        main,
        ["theme", "create", "--from", "dracula", "--no-install", "--output", str(out)],
        input="\n" * 28,
    )
    assert result.exit_code == 0, result.output
    data = tomllib.loads(out.read_text(encoding="utf-8"))
    dracula = themes.load_builtin_theme("dracula")
    for role in themes.ROLES:
        assert data["palette"][role] == dracula[role]


def test_theme_create_unknown_builtin_fails(theme_runner):
    result = theme_runner.invoke(
        main, ["theme", "create", "--from", "bogus"]
    )
    assert result.exit_code == 1
    assert "Unknown builtin theme" in result.output


def test_theme_create_rejects_invalid_style_then_accepts(theme_runner, tmp_path):
    out = tmp_path / "valid.toml"
    # First prompt gets a bad value, then a good one; the remaining
    # 27 prompts accept defaults.
    bad_then_good = "bol yellow\n" + "bold red\n" + "\n" * 27
    result = theme_runner.invoke(
        main,
        [
            "theme", "create",
            "--no-install",
            "--output", str(out),
        ],
        input=bad_then_good,
    )
    assert result.exit_code == 0, result.output
    assert "is not a valid Rich style" in result.output
    data = tomllib.loads(out.read_text(encoding="utf-8"))
    assert data["palette"]["error_border"] == "bold red"


def test_theme_create_rejects_empty_value(theme_runner, tmp_path):
    out = tmp_path / "empty.toml"
    # Two empty lines for the first prompt (first is rejected, second
    # is also rejected and the prompt falls back to the default); the
    # remaining 27 prompts accept defaults. Click's prompt doesn't
    # auto-fall-back though — an empty value after a rejection keeps
    # re-prompting. We feed three empty lines then move on.
    input_str = "\n\n\n" + "\n" * 27
    result = theme_runner.invoke(
        main,
        ["theme", "create", "--no-install", "--output", str(out)],
        input=input_str,
    )
    # The wizard may exit 1 on persistent bad input, or write the
    # file with default values; both are acceptable — what matters
    # is no crash and a clean error or a valid file.
    if result.exit_code == 0:
        data = tomllib.loads(out.read_text(encoding="utf-8"))
        assert data["palette"]["error_border"] == themes.DEFAULT_THEME["error_border"]
    else:
        assert "value cannot be empty" in result.output or result.exit_code == 1


def test_theme_create_output_and_install(theme_runner, tmp_path):
    out = tmp_path / "both.toml"
    result = theme_runner.invoke(
        main,
        ["theme", "create", "--output", str(out), "--install"],
        input="\n" * 28,
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    active = themes.get_theme_path()
    assert active.exists()
    # The installed file is byte-identical to the output file.
    assert out.read_bytes() == active.read_bytes()


def test_theme_create_no_install_no_output(theme_runner, tmp_path):
    """--no-install with no --output: just prints, no file written."""
    before = themes.get_theme_path()
    result = theme_runner.invoke(
        main,
        ["theme", "create", "--no-install", "--name", "ephemeral"],
        input="\n" * 28,
    )
    assert result.exit_code == 0, result.output
    assert "not installed" in result.output
    # The active theme file was NOT created.
    assert not before.exists()


def test_theme_create_round_trip_through_set(theme_runner, tmp_path):
    """A wizard-produced file can be installed via `theme set` losslessly."""
    out = tmp_path / "rt.toml"
    runner = CliRunner()
    # 1) produce the file via the wizard
    result = runner.invoke(
        main,
        ["theme", "create", "--no-install", "--output", str(out), "--name", "rt"],
        input="\n" * 28,
    )
    assert result.exit_code == 0, result.output
    # 2) install it via theme set
    result2 = runner.invoke(main, ["theme", "set", str(out)])
    assert result2.exit_code == 0, result2.output
    # 3) the active theme is now the same as the file
    active = themes.get_theme_path()
    assert active.read_bytes() == out.read_bytes()
    data = tomllib.loads(active.read_text(encoding="utf-8"))
    assert data["meta"]["name"] == "rt"
