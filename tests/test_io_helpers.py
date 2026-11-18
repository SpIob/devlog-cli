"""Direct tests for the private helpers in :mod:`devlog._io`.

``tests/test_import.py`` and ``tests/test_export.py`` drive the CLI
happy paths. This file complements them by calling the helpers in
``devlog._io`` directly so the error/branch behaviour that the CLI's
own preamble (``click.Path`` validation, ``_importers.detect_format``)
otherwise shadows — sniffing, unreadable-row counting, dry-run
short-circuits, storage failures — is pinned too.

Fixtures (``runner``, ``data_dir``, ``make_entry``, ``seed_entry``,
``add_entry_cmd``) come from ``tests/conftest.py``; ``capture_console``
is imported for in-process ``ui.console`` assertions.
"""

from __future__ import annotations

import json

import pytest

from devlog import _io, storage
from devlog.cli import main
from devlog.models import Entry
from tests.conftest import capture_console

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _capture_errors(monkeypatch):
    """Swap ``_io.ui.print_error`` for a recorder; return the list it fills."""
    seen: list[str] = []
    monkeypatch.setattr(_io.ui, "print_error", lambda msg: seen.append(msg))
    return seen


def _markdown_block(message: str, short_id: str = "abcdef01", date: str = "2025-01-01 00:00 UTC") -> str:
    return f"## {date} — {short_id}\n\n{message}\n\n**Tags:** t\n\n---\n"


# ---------------------------------------------------------------------------
# _sniff_import_format
# ---------------------------------------------------------------------------


def test_sniff_json_extension_is_case_insensitive(tmp_path):
    p = tmp_path / "payload.JSON"
    p.write_text("{}", encoding="utf-8")
    assert _io._sniff_import_format(str(p)) == "json"


@pytest.mark.parametrize("suffix", [".md", ".markdown"])
def test_sniff_markdown_extensions(tmp_path, suffix):
    p = tmp_path / f"payload{suffix}"
    p.write_text("# Heading", encoding="utf-8")
    assert _io._sniff_import_format(str(p)) == "markdown"


def test_sniff_extensionless_json_from_first_char(tmp_path):
    p = tmp_path / "blob"
    p.write_text('{"entries": []}', encoding="utf-8")
    assert _io._sniff_import_format(str(p)) == "json"


def test_sniff_extensionless_markdown_from_first_char(tmp_path):
    # Leading whitespace/blank lines are skipped before sniffing.
    p = tmp_path / "blob"
    p.write_text("\n\n   \n# Heading\n", encoding="utf-8")
    assert _io._sniff_import_format(str(p)) == "markdown"


def test_sniff_unrecognisable_content_exits_2(tmp_path, monkeypatch):
    errors = _capture_errors(monkeypatch)
    p = tmp_path / "blob"
    p.write_text("just some prose, no markers", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        _io._sniff_import_format(str(p))
    assert exc.value.code == 2
    assert len(errors) == 1
    assert "Cannot auto-detect format" in errors[0]


def test_sniff_unreadable_path_exits_2(tmp_path, monkeypatch):
    # OSError branch: the file cannot be opened at all.
    errors = _capture_errors(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        _io._sniff_import_format(str(tmp_path / "missing"))
    assert exc.value.code == 2
    assert "Cannot auto-detect format" in errors[0]


# ---------------------------------------------------------------------------
# _read_import_payload
# ---------------------------------------------------------------------------


def test_read_payload_unreadable_path_exits_2(tmp_path, monkeypatch):
    errors = _capture_errors(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        _io._read_import_payload(str(tmp_path / "missing.json"), "json")
    assert exc.value.code == 2
    assert "Cannot read" in errors[0]


def test_read_payload_json_not_an_object_exits_2(tmp_path, monkeypatch):
    errors = _capture_errors(monkeypatch)
    p = tmp_path / "list.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        _io._read_import_payload(str(p), "json")
    assert exc.value.code == 2
    assert "expected an object" in errors[0]


def test_read_payload_counts_bad_rows_and_mints_missing_ids(tmp_path):
    p = tmp_path / "mixed.json"
    p.write_text(
        json.dumps(
            {
                "entries": [
                    "not a dict",
                    42,
                    # id-less but otherwise valid → uuid minted, kept
                    {"message": "no id field", "created_at": "2025-01-01T00:00:00Z", "tags": []},
                    # unknown kwarg → Entry(**item) raises, counted unreadable
                    {
                        "id": "boom",
                        "message": "extra kwarg",
                        "created_at": "2025-01-03T00:00:00Z",
                        "bogus": 1,
                    },
                    # fully specified → stable id preserved
                    {
                        "id": "keepme-1234",
                        "message": "has id",
                        "created_at": "2025-01-02T00:00:00Z",
                        "tags": ["x"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    candidates, unreadable = _io._read_import_payload(str(p), "json")
    assert unreadable == 3  # str row, int row, unknown-kwarg row
    assert [c.message for c in candidates] == ["no id field", "has id"]
    assert candidates[0].id  # uuid minted for the id-less row
    assert candidates[1].id == "keepme-1234"  # existing id preserved


# ---------------------------------------------------------------------------
# _dedup_against_existing
# ---------------------------------------------------------------------------


def test_dedup_mints_id_for_idless_candidate():
    existing = [Entry(id="e1", message="old", created_at="2025-01-01T00:00:00Z")]
    idless = Entry(id="", message="fresh", created_at="2025-01-02T00:00:00Z")
    to_add, skipped = _io._dedup_against_existing([idless], existing)
    assert skipped == 0
    assert len(to_add) == 1
    assert to_add[0].id  # a uuid was minted, not left blank


def test_dedup_skips_by_fingerprint_even_with_unknown_id():
    existing = [Entry(id="e1", message="dup msg", created_at="2025-01-01T00:00:00Z")]
    cand = Entry(
        id="totally-different-id",
        message="dup msg",
        created_at="2025-01-01T00:00:00Z",
    )
    to_add, skipped = _io._dedup_against_existing([cand], existing)
    assert to_add == []
    assert skipped == 1


# ---------------------------------------------------------------------------
# _emit_import_summary
# ---------------------------------------------------------------------------


def test_emit_import_summary_reports_unreadable_rows():
    out = capture_console(
        lambda: _io._emit_import_summary(2, 1, 3, dry_run=False)
    )
    assert "Imported 2 entries" in out
    assert "skip 1 duplicate." in out
    assert "Ignored 3 unreadable rows." in out


def test_emit_import_summary_dry_run_singular():
    out = capture_console(
        lambda: _io._emit_import_summary(1, 0, 0, dry_run=True)
    )
    assert "DRY RUN:" in out
    assert "would import 1 entry" in out
    assert "skip 0 duplicates." in out


# ---------------------------------------------------------------------------
# _parse_markdown_export
# ---------------------------------------------------------------------------


def test_parse_markdown_entry_without_tags_line():
    content = (
        "## 2025-05-11 10:22 UTC — a1b2c3d4\n\n"
        "Message with no tags line.\n\n"
        "---\n"
    )
    entries = _io._parse_markdown_export(content)
    assert len(entries) == 1
    assert entries[0].message == "Message with no tags line."
    assert entries[0].tags == []
    # No hidden comment → fall back to the heading's minute precision.
    assert entries[0].created_at == "2025-05-11T10:22:00Z"
    assert entries[0].id == "a1b2c3d4-imported"


def test_parse_markdown_full_timestamp_and_none_tags():
    content = (
        "## 2025-05-11 10:22 UTC — deadbeef\n\n"
        "Body text.\n\n"
        "**Tags:** (none)\n\n"
        "<!-- created_at: 2025-05-11T10:22:33Z -->\n\n"
        "---\n"
    )
    entries = _io._parse_markdown_export(content)
    assert len(entries) == 1
    assert entries[0].message == "Body text."
    assert entries[0].tags == []
    assert entries[0].created_at == "2025-05-11T10:22:33Z"


def test_parse_markdown_malformed_hidden_timestamp_falls_back():
    content = (
        "## 2025-05-11 10:22 UTC — deadbeef\n\n"
        "Body text.\n\n"
        "**Tags:** backend\n\n"
        "<!-- created_at: not-a-timestamp -->\n\n"
        "---\n"
    )
    entries = _io._parse_markdown_export(content)
    assert entries[0].created_at == "2025-05-11T10:22:00Z"
    assert entries[0].tags == ["backend"]


# ---------------------------------------------------------------------------
# _resolve_export_format
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("output", "fmt", "expected"),
    [
        ("out.json", "auto", "json"),  # extension implies json
        ("out.md", "auto", "markdown"),  # extension implies markdown
        ("out.txt", "auto", "markdown"),  # unknown extension → markdown
        (None, "auto", "markdown"),  # no output → markdown default
        (None, "json", "json"),  # explicit format, no output
        ("out.md", "json", "json"),  # explicit wins over extension
        ("out.json", "markdown", "markdown"),  # explicit wins over extension
        ("out.txt", "JSON", "json"),  # explicit format is case-insensitive
    ],
)
def test_resolve_export_format(output, fmt, expected):
    assert _io._resolve_export_format(output, fmt) == expected


# ---------------------------------------------------------------------------
# CLI: import dry-run / quiet / missing file
# ---------------------------------------------------------------------------


def test_cli_import_dry_run_writes_nothing(runner, data_dir, tmp_path):
    md = tmp_path / "imp.md"
    md.write_text(_markdown_block("would be imported"), encoding="utf-8")
    result = runner.invoke(main, ["import", str(md), "--dry-run"])
    assert result.exit_code == 0
    assert "DRY RUN:" in result.output
    assert "would import 1 entry" in result.output
    # Nothing was persisted.
    assert storage.load_entries() == []


def test_cli_import_dry_run_quiet_is_silent(runner, data_dir, tmp_path):
    md = tmp_path / "imp.md"
    md.write_text(_markdown_block("would be imported"), encoding="utf-8")
    result = runner.invoke(main, ["import", str(md), "--dry-run", "--quiet"])
    assert result.exit_code == 0
    assert result.output.strip() == ""  # no banner at all
    assert storage.load_entries() == []


def test_cli_import_missing_file_is_clean_exit_2(runner, data_dir, tmp_path):
    missing = tmp_path / "does-not-exist.json"
    result = runner.invoke(main, ["import", str(missing)])
    assert result.exit_code == 2
    assert "does not exist" in result.output
    assert "Traceback" not in result.output


def test_cli_json_import_reports_unreadable_rows(runner, data_dir, tmp_path):
    f = tmp_path / "mixed.json"
    f.write_text(
        json.dumps(
            {
                "entries": [
                    "garbage row",
                    {
                        "id": "ok-1",
                        "message": "good row",
                        "created_at": "2025-01-01T00:00:00Z",
                        "tags": [],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    result = runner.invoke(main, ["import", str(f)])
    assert result.exit_code == 0
    assert "Imported 1 entry" in result.output
    assert "Ignored 1 unreadable row." in result.output
    assert len(storage.load_entries()) == 1


# ---------------------------------------------------------------------------
# CLI: markdown export → import round-trip (fingerprint idempotency)
# ---------------------------------------------------------------------------


def test_markdown_export_import_round_trip_is_idempotent(
    runner, data_dir, tmp_path, add_entry_cmd
):
    """Export via the CLI, import it back, and assert the store is a no-op.

    Dedupe keys on ``(created_at, message)``: because the exporter embeds
    the full-precision timestamp in a hidden comment, the round-trip must
    not create duplicate rows — on the first import or any later one.
    """
    add_entry_cmd("round trip one", "backend")
    add_entry_cmd("round trip two", "frontend")

    out = tmp_path / "roundtrip.md"
    result = runner.invoke(
        main, ["export", "--format", "markdown", "--output", str(out)]
    )
    assert result.exit_code == 0
    assert "<!-- created_at:" in out.read_text(encoding="utf-8")

    before = {e.fingerprint for e in storage.load_entries()}
    assert len(before) == 2

    result = runner.invoke(main, ["import", str(out)])
    assert result.exit_code == 0
    after = {e.fingerprint for e in storage.load_entries()}
    assert after == before
    assert len(storage.load_entries()) == 2
    assert "No new entries to import" in result.output

    # A second import is likewise a no-op.
    result = runner.invoke(main, ["import", str(out)])
    assert result.exit_code == 0
    assert len(storage.load_entries()) == 2
    assert "No new entries to import" in result.output


# ---------------------------------------------------------------------------
# _io.import_cmd (direct): auto-sniff, storage failures, empty / unreadable
# ---------------------------------------------------------------------------


def test_io_import_cmd_auto_sniffs_and_dry_run_is_noop(data_dir, tmp_path):
    md = tmp_path / "auto.md"
    md.write_text(_markdown_block("auto sniffed"), encoding="utf-8")
    # fmt="auto" makes import_cmd call _sniff_import_format itself.
    _io.import_cmd(str(md), "auto", dry_run=True, quiet=True)
    assert storage.load_entries() == []


def test_io_import_cmd_load_failure_exits_2(data_dir, tmp_path, monkeypatch):
    md = tmp_path / "x.md"
    md.write_text(_markdown_block("body"), encoding="utf-8")

    def _boom():
        raise RuntimeError("storage exploded")

    monkeypatch.setattr(storage, "load_entries", _boom)
    errors = _capture_errors(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        _io.import_cmd(str(md), "markdown", dry_run=False, quiet=True)
    assert exc.value.code == 2
    assert errors == ["storage exploded"]


def test_io_import_cmd_save_failure_exits_2(data_dir, tmp_path, monkeypatch):
    md = tmp_path / "new.md"
    md.write_text(_markdown_block("brand new entry"), encoding="utf-8")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(storage, "save_entries", _boom)
    errors = _capture_errors(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        _io.import_cmd(str(md), "markdown", dry_run=False, quiet=True)
    assert exc.value.code == 2
    assert errors == ["disk full"]


def test_io_import_cmd_json_all_unreadable_reports_and_skips(data_dir, tmp_path):
    f = tmp_path / "junk.json"
    f.write_text(json.dumps({"entries": ["nope", 7]}), encoding="utf-8")
    out = capture_console(
        lambda: _io.import_cmd(str(f), "json", dry_run=False, quiet=False)
    )
    assert "No new entries to import (2 unreadable rows ignored)." in out
    assert storage.load_entries() == []


def test_io_import_cmd_json_empty_store_reports_nothing_to_do(data_dir, tmp_path):
    f = tmp_path / "empty.json"
    f.write_text(json.dumps({"entries": []}), encoding="utf-8")
    out = capture_console(
        lambda: _io.import_cmd(str(f), "json", dry_run=False, quiet=False)
    )
    assert "No entries to import." in out


# ---------------------------------------------------------------------------
# _io.export (direct): storage failure
# ---------------------------------------------------------------------------


def test_io_export_load_failure_exits_2(data_dir, monkeypatch):
    def _boom():
        raise RuntimeError("cannot load")

    monkeypatch.setattr(storage, "load_entries", _boom)
    errors = _capture_errors(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        _io.export(None, "markdown", (), None, None, True)
    assert exc.value.code == 2
    assert errors == ["cannot load"]
