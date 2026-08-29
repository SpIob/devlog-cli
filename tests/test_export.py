"""Tests for the `devlog export` command."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from devlog.cli import main


@pytest.fixture()
def runner(tmp_path):
    return CliRunner(env={"DEVLOG_DATA_DIR": str(tmp_path)})


@pytest.fixture()
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    return tmp_path


def _add(runner, message, *tags):
    args = ["add", message] + [a for t in tags for a in ("-t", t)]
    runner.invoke(main, args)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_export_creates_markdown(runner, tmp_path):
    _add(runner, "Deploy to production", "ops")
    out = tmp_path / "out.md"
    result = runner.invoke(main, ["export", "--output", str(out), "--quiet"])
    assert result.exit_code == 0
    content = out.read_text(encoding="utf-8")
    assert "## " in content
    assert "Deploy to production" in content
    assert "**Tags:**" in content
    assert "---" in content


def test_export_separator_and_structure(runner, tmp_path):
    _add(runner, "First entry", "backend")
    _add(runner, "Second entry", "frontend")
    out = tmp_path / "two.md"
    runner.invoke(main, ["export", "--output", str(out), "--quiet"])
    content = out.read_text(encoding="utf-8")
    assert content.count("---") == 2
    assert content.count("**Tags:**") == 2


def test_export_no_tags_shows_none(runner, tmp_path):
    _add(runner, "Entry without tags")
    out = tmp_path / "notags.md"
    runner.invoke(main, ["export", "--output", str(out), "--quiet"])
    assert "(none)" in out.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------


def test_export_no_entries(runner, tmp_path):
    out = tmp_path / "empty.md"
    result = runner.invoke(main, ["export", "--output", str(out)])
    assert result.exit_code == 0
    assert "Warning: No entries to export" in result.output


# ---------------------------------------------------------------------------
# Tag filter
# ---------------------------------------------------------------------------


def test_export_tag_filter(runner, tmp_path):
    _add(runner, "Backend work", "backend")
    _add(runner, "Frontend work", "frontend")
    out = tmp_path / "filtered.md"
    runner.invoke(main, ["export", "--output", str(out), "-t", "backend", "--quiet"])
    content = out.read_text(encoding="utf-8")
    assert "Backend work" in content
    assert "Frontend work" not in content


# ---------------------------------------------------------------------------
# Quiet mode
# ---------------------------------------------------------------------------


def test_export_quiet_prints_path(runner, tmp_path):
    _add(runner, "Some entry")
    out = tmp_path / "quiet.md"
    result = runner.invoke(main, ["export", "--output", str(out), "--quiet"])
    assert result.exit_code == 0
    assert str(out) in result.output


# ---------------------------------------------------------------------------
# Permission error
# ---------------------------------------------------------------------------


def test_export_unwritable_path(runner, tmp_path):
    _add(runner, "Something")
    # Pass a path in a nonexistent deep directory to force an OSError
    bad_path = "/root/no_permission_here/devlog.md"
    result = runner.invoke(main, ["export", "--output", bad_path, "--quiet"])
    assert result.exit_code == 2
    assert "Cannot write to" in result.output


# ---------------------------------------------------------------------------
# Default path (W2.2)
# ---------------------------------------------------------------------------


def test_export_default_path_uses_data_dir(runner, data_dir):
    """With no -o, the export should land in <data-dir>/exports/,
    not in the current working directory. Previously it wrote to
    './devlog-export.md' regardless of DEVLOG_DATA_DIR.
    """
    monkeypath = Path(str(data_dir))
    _add(runner, "Default-path entry")

    # Capture cwd before the run to assert nothing was written there.
    cwd_before = list(Path.cwd().iterdir())

    result = runner.invoke(main, ["export", "--quiet"])
    assert result.exit_code == 0
    out_path = Path(result.output.strip())
    assert out_path.exists()
    assert out_path.parent.parent == monkeypath  # <data-dir>/exports/...
    assert out_path.name.startswith("devlog-")
    assert out_path.suffix == ".md"

    # Cwd was not polluted.
    assert list(Path.cwd().iterdir()) == cwd_before


# ---------------------------------------------------------------------------
# Format auto-detect (W2.3)
# ---------------------------------------------------------------------------


def test_export_json_extension_writes_json(runner, tmp_path):
    """-o foo.json writes a JSON file even though the legacy default
    was Markdown. Auto-detect by extension.
    """
    _add(runner, "JSON export target")
    out = tmp_path / "out.json"
    result = runner.invoke(main, ["export", "--output", str(out), "--quiet"])
    assert result.exit_code == 0
    assert out.exists()
    payload = json.loads(out.read_text())
    assert "entries" in payload
    assert isinstance(payload["entries"], list)
    assert payload["entries"][0]["message"] == "JSON export target"


def test_export_markdown_extension_writes_md(runner, tmp_path):
    _add(runner, "MD export target")
    out = tmp_path / "out.md"
    result = runner.invoke(main, ["export", "--output", str(out), "--quiet"])
    assert result.exit_code == 0
    content = out.read_text()
    assert "MD export target" in content
    assert "##" in content  # markdown heading
    assert "{ \"entries\":" not in content  # not JSON


def test_export_explicit_format_overrides_extension(runner, tmp_path):
    """--format json wins even with a .md extension."""
    _add(runner, "Override target")
    out = tmp_path / "out.md"
    result = runner.invoke(
        main, ["export", "--output", str(out), "--format", "json", "--quiet"]
    )
    assert result.exit_code == 0
    payload = json.loads(out.read_text())
    assert payload["entries"][0]["message"] == "Override target"


def test_export_unknown_extension_falls_back_to_markdown(runner, tmp_path):
    """Auto-detect with no recognised extension produces Markdown.
    Preserves pre-1.5 behaviour for arbitrary extensions like .txt.
    """
    _add(runner, "Unknown ext target")
    out = tmp_path / "out.txt"
    result = runner.invoke(main, ["export", "--output", str(out), "--quiet"])
    assert result.exit_code == 0
    content = out.read_text()
    assert "Unknown ext target" in content
    assert "##" in content


# ---------------------------------------------------------------------------
# Zero entries (W2.4)
# ---------------------------------------------------------------------------


def test_export_empty_with_custom_path_does_not_create_file(runner, tmp_path):
    """`devlog export -o path.md` on an empty store must NOT create
    the file. Previously the file was created (zero bytes) before the
    open()-but-no-write path was hit.
    """
    out = tmp_path / "should_not_exist.md"
    result = runner.invoke(main, ["export", "--output", str(out), "--quiet"])
    assert result.exit_code == 0
    assert not out.exists()


def test_export_empty_default_does_not_create_file(runner, data_dir):
    """`devlog export` (no -o) on an empty store must not create
    anything in <data-dir>/exports/."""
    result = runner.invoke(main, ["export", "--quiet"])
    assert result.exit_code == 0
    exports_dir = Path(str(data_dir)) / "exports"
    assert not exports_dir.exists()