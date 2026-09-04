"""Tests for the `devlog import` command."""

import json
import pytest

from devlog.cli import main
from devlog import storage
from devlog.models import Entry


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------


def test_import_json(runner, data_dir, tmp_path):
    # Create a valid JSON import file
    import_file = tmp_path / "import.json"
    import_file.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "a1111111-1111-1111-1111-111111111111",
                        "message": "imported one",
                        "tags": ["backend"],
                        "created_at": "2025-01-01T12:00:00Z",
                    },
                    {
                        "id": "a2222222-2222-2222-2222-222222222222",
                        "message": "imported two",
                        "tags": ["frontend"],
                        "created_at": "2025-01-02T12:00:00Z",
                    },
                ]
            }
        )
    )
    result = runner.invoke(main, ["import", str(import_file)])
    assert result.exit_code == 0
    assert "Imported 2 entries" in result.output
    entries = storage.load_entries()
    assert len(entries) == 2
    assert {e.message for e in entries} == {"imported one", "imported two"}


def test_import_markdown(runner, data_dir, tmp_path):
    import_file = tmp_path / "import.md"
    import_file.write_text(
        "## 2025-01-01 12:00 UTC — a1111111\n\n"
        "imported one\n\n"
        "**Tags:** backend\n\n"
        "---\n"
        "## 2025-01-02 12:00 UTC — a2222222\n\n"
        "imported two\n\n"
        "**Tags:** frontend\n\n"
        "---\n"
    )
    result = runner.invoke(main, ["import", str(import_file)])
    assert result.exit_code == 0
    assert "Imported 2 entries" in result.output
    entries = storage.load_entries()
    assert len(entries) == 2


def test_import_idempotent(runner, data_dir, tmp_path, seed_entry):
    # Pre-seed one entry
    seed_entry("existing-1111-1111-1111-111111111111", "existing", tags=["old"])
    import_file = tmp_path / "import.json"
    import_file.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "existing-1111-1111-1111-111111111111",
                        "message": "existing",
                        "tags": ["old"],
                        "created_at": "2025-01-01T12:00:00Z",
                    },
                    {
                        "id": "new-22222222-2222-2222-2222-222222222222",
                        "message": "imported",
                        "tags": ["new"],
                        "created_at": "2025-01-02T12:00:00Z",
                    },
                ]
            }
        )
    )
    result = runner.invoke(main, ["import", str(import_file)])
    assert result.exit_code == 0
    assert "Imported 1 entry" in result.output
    assert "skip 1 duplicate" in result.output


def test_import_dry_run(runner, data_dir, tmp_path):
    import_file = tmp_path / "import.json"
    import_file.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "a1111111-1111-1111-1111-111111111111",
                        "message": "would import",
                        "tags": ["backend"],
                        "created_at": "2025-01-01T12:00:00Z",
                    }
                ]
            }
        )
    )
    result = runner.invoke(main, ["import", str(import_file), "--dry-run"])
    assert result.exit_code == 0
    assert "DRY RUN" in result.output
    assert "would import 1 entry" in result.output
    entries = storage.load_entries()
    assert len(entries) == 0


def test_import_dry_run_with_quiet_is_silent(runner, data_dir, tmp_path):
    import_file = tmp_path / "import.json"
    import_file.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "a1111111-1111-1111-1111-111111111111",
                        "message": "would import",
                        "tags": ["backend"],
                        "created_at": "2025-01-01T12:00:00Z",
                    }
                ]
            }
        )
    )
    result = runner.invoke(main, ["import", str(import_file), "--dry-run", "--quiet"])
    assert result.exit_code == 0
    assert result.output.strip() == ""


def test_import_malformed_json(runner, data_dir, tmp_path):
    import_file = tmp_path / "bad.json"
    import_file.write_text("{not valid json")
    result = runner.invoke(main, ["import", str(import_file)])
    assert result.exit_code != 0
    assert "Invalid JSON" in result.output


def test_import_auto_detect_format(runner, data_dir, tmp_path):
    # .json extension → json
    import_file = tmp_path / "import.json"
    import_file.write_text(
        json.dumps(
            {"entries": [{"id": "x", "message": "json", "tags": [], "created_at": "2025-01-01T00:00:00Z"}]}
        )
    )
    result = runner.invoke(main, ["import", str(import_file), "--format", "auto", "--quiet"])
    assert result.exit_code == 0

    # .md extension → markdown
    import_file = tmp_path / "import.md"
    import_file.write_text("## 2025-01-01 00:00 UTC — x\n\nmsg\n\n**Tags:** t\n\n---\n")
    result = runner.invoke(main, ["import", str(import_file), "--format", "auto", "--quiet"])
    assert result.exit_code == 0

def test_import_sniffs_format_for_extensionless_file(runner, data_dir, tmp_path):
    import_file = tmp_path / "import"
    import_file.write_text(
        json.dumps(
            {"entries": [{"id": "x", "message": "json", "tags": [], "created_at": "2025-01-01T00:00:00Z"}]}
        )
    )
    result = runner.invoke(main, ["import", str(import_file), "--format", "auto", "--quiet"])
    assert result.exit_code == 0

    import_file = tmp_path / "import"
    import_file.write_text("## 2025-01-01 00:00 UTC — x\n\nmsg\n\n**Tags:** t\n\n---\n")
    result = runner.invoke(main, ["import", str(import_file), "--format", "auto", "--quiet"])
    assert result.exit_code == 0


def test_import_preserves_stable_ids(runner, data_dir, tmp_path):
    """Import should preserve IDs from the source file when present."""
    import_file = tmp_path / "import.json"
    stable_id = "stable-id-1234-5678-9012-123456789012"
    import_file.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": stable_id,
                        "message": "with stable id",
                        "tags": ["backend"],
                        "created_at": "2025-01-01T12:00:00Z",
                    }
                ]
            }
        )
    )
    result = runner.invoke(main, ["import", str(import_file), "--quiet"])
    assert result.exit_code == 0
    entries = storage.load_entries()
    assert entries[0].id == stable_id