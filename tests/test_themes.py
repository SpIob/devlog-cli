"""Tests for the devlog.themes module."""

import io
import os
from pathlib import Path

import pytest
import tomllib

from devlog import themes


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
    """Defaults must reproduce the previously hardcoded colors verbatim."""
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
    }
    assert themes.DEFAULT_THEME == expected


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


def test_theme_cli_list(monkeypatch, tmp_path):
    from click.testing import CliRunner

    from devlog.cli import main

    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    themes.reset_cache()
    runner = CliRunner()
    result = runner.invoke(main, ["theme", "list"])
    assert result.exit_code == 0
    assert "Active theme" in result.output
    for role in ("date", "tags", "success_border"):
        assert role in result.output


def test_theme_cli_path(monkeypatch, tmp_path):
    from click.testing import CliRunner

    from devlog.cli import main

    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    runner = CliRunner()
    result = runner.invoke(main, ["theme", "path"])
    assert result.exit_code == 0
    assert "theme.toml" in result.output
    assert str(tmp_path / "d") in result.output


def test_theme_cli_show_default(monkeypatch, tmp_path):
    from click.testing import CliRunner

    from devlog.cli import main

    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    themes.reset_cache()
    runner = CliRunner()
    result = runner.invoke(main, ["theme", "show", "date"])
    assert result.exit_code == 0
    assert result.output.strip() == "cyan"


def test_theme_cli_show_unknown_role(monkeypatch, tmp_path):
    from click.testing import CliRunner

    from devlog.cli import main

    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    runner = CliRunner()
    result = runner.invoke(main, ["theme", "show", "not_a_real_role"])
    assert result.exit_code == 1
    assert "Unknown role" in result.output


def test_theme_cli_show_full_palette_is_starter_toml(monkeypatch, tmp_path):
    from click.testing import CliRunner

    from devlog.cli import main

    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    themes.reset_cache()
    runner = CliRunner()
    result = runner.invoke(main, ["theme", "show"])
    assert result.exit_code == 0
    # The full dump is a TOML template — the [palette] header must be
    # present and every role must be mentioned (commented or otherwise).
    assert "[palette]" in result.output
    for role in themes.ROLES:
        assert role in result.output


def test_theme_cli_set_installs_file(monkeypatch, tmp_path):
    from click.testing import CliRunner

    from devlog.cli import main

    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    themes.reset_cache()
    src = tmp_path / "my-theme.toml"
    src.write_text('[palette]\ndate = "red"\n', encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(main, ["theme", "set", str(src)])
    assert result.exit_code == 0
    assert "installed" in result.output.lower()

    installed = themes.get_theme_path()
    assert installed.exists()
    # Round-trip the active theme — the new value must be picked up
    themes.reset_cache()
    assert themes.get_active_theme()["date"] == "red"


def test_theme_cli_set_rejects_bad_toml(monkeypatch, tmp_path):
    from click.testing import CliRunner

    from devlog.cli import main

    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    themes.reset_cache()
    src = tmp_path / "bad.toml"
    src.write_text("this is not toml [[[", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(main, ["theme", "set", str(src)])
    assert result.exit_code == 1
    assert "invalid" in result.output.lower() or "error" in result.output.lower()


def test_theme_cli_set_warns_on_unknown_roles(monkeypatch, tmp_path):
    from click.testing import CliRunner

    from devlog.cli import main

    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "d"))
    themes.reset_cache()
    src = tmp_path / "mixed.toml"
    src.write_text(
        '[palette]\ndate = "red"\nmade_up = "blue"\n', encoding="utf-8"
    )
    runner = CliRunner()
    result = runner.invoke(main, ["theme", "set", str(src)])
    assert result.exit_code == 0
    assert "made_up" in result.output
    # And the unknown key was not actually installed
    themes.reset_cache()
    assert "made_up" not in themes.get_active_theme()
