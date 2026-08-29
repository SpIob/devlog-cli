"""Tests for the shared date-range parser and filter (`--since` / `--until`)."""

import datetime
import json

import pytest
from click.testing import CliRunner

from devlog.cli import _filter_by_date, _parse_date_bound, main
from devlog import storage
from devlog.models import Entry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def runner(tmp_path):
    return CliRunner(env={"DEVLOG_DATA_DIR": str(tmp_path)})


@pytest.fixture()
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    return tmp_path


def _utc_iso(year, month, day, hour=12, minute=0, second=0):
    return datetime.datetime(year, month, day, hour, minute, second, tzinfo=datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _seed(entry_id, message, created_at, tags=None):
    storage.add_entry(
        Entry(
            id=entry_id,
            message=message,
            tags=list(tags) if tags else [],
            created_at=created_at,
        )
    )


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


def test_parse_date_only():
    dt = _parse_date_bound("2025-01-15")
    assert dt == datetime.datetime(2025, 1, 15, tzinfo=datetime.timezone.utc)


def test_parse_date_only_upper_inclusive():
    dt = _parse_date_bound("2025-01-15", is_upper=True)
    assert dt == datetime.datetime(2025, 1, 15, 23, 59, 59, tzinfo=datetime.timezone.utc)


def test_parse_iso_z():
    dt = _parse_date_bound("2025-01-15T10:30:00Z")
    assert dt == datetime.datetime(2025, 1, 15, 10, 30, 0, tzinfo=datetime.timezone.utc)


def test_parse_iso_with_offset_normalised_to_utc():
    dt = _parse_date_bound("2025-01-15T10:30:00+05:00")
    # 10:30 +05:00 == 05:30 UTC
    assert dt == datetime.datetime(2025, 1, 15, 5, 30, 0, tzinfo=datetime.timezone.utc)


def test_parse_space_separator():
    dt = _parse_date_bound("2025-01-15 10:30")
    assert dt == datetime.datetime(2025, 1, 15, 10, 30, 0, tzinfo=datetime.timezone.utc)


def test_parse_today():
    dt = _parse_date_bound("today")
    now_utc = datetime.datetime.now(tz=datetime.timezone.utc)
    assert dt.date() == now_utc.date()
    assert dt.hour == 0


def test_parse_yesterday():
    dt = _parse_date_bound("yesterday")
    now_utc = datetime.datetime.now(tz=datetime.timezone.utc)
    assert dt.date() == (now_utc - datetime.timedelta(days=1)).date()


def test_parse_relative_days():
    dt = _parse_date_bound("7d")
    now_utc = datetime.datetime.now(tz=datetime.timezone.utc)
    assert dt.date() == (now_utc - datetime.timedelta(days=7)).date()


def test_parse_relative_weeks():
    dt = _parse_date_bound("2w")
    now_utc = datetime.datetime.now(tz=datetime.timezone.utc)
    assert dt.date() == (now_utc - datetime.timedelta(weeks=2)).date()


def test_parse_empty_string_errors():
    with pytest.raises(Exception) as exc_info:
        _parse_date_bound("")
    assert "empty" in str(exc_info.value).lower() or "invalid" in str(exc_info.value).lower()


def test_parse_garbage_errors():
    with pytest.raises(Exception) as exc_info:
        _parse_date_bound("not-a-date")
    assert "Invalid date" in str(exc_info.value)


def test_parse_partial_date_errors():
    with pytest.raises(Exception) as exc_info:
        _parse_date_bound("2025-01")
    assert "Invalid date" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Filter tests
# ---------------------------------------------------------------------------


def _mk_entry(eid, when_iso):
    return Entry(id=eid, message=f"msg-{eid[:4]}", tags=[], created_at=when_iso)


def test_filter_no_bounds_returns_all():
    entries = [
        _mk_entry("11111111-1111-1111-1111-111111111111", _utc_iso(2024, 1, 1)),
        _mk_entry("22222222-2222-2222-2222-222222222222", _utc_iso(2025, 6, 1)),
    ]
    out = _filter_by_date(entries, None, None)
    assert out == entries


def test_filter_since_only():
    entries = [
        _mk_entry("11111111-1111-1111-1111-111111111111", _utc_iso(2024, 1, 1)),
        _mk_entry("22222222-2222-2222-2222-222222222222", _utc_iso(2025, 6, 1)),
    ]
    since = _parse_date_bound("2025-01-01")
    out = _filter_by_date(entries, since, None)
    assert len(out) == 1
    assert out[0].id.startswith("22222222")


def test_filter_until_only_inclusive():
    entries = [
        _mk_entry("11111111-1111-1111-1111-111111111111", _utc_iso(2024, 1, 1)),
        _mk_entry("22222222-2222-2222-2222-222222222222", _utc_iso(2025, 6, 1)),
        _mk_entry("33333333-3333-3333-3333-333333333333", _utc_iso(2025, 12, 31)),
    ]
    until = _parse_date_bound("2025-06-01", is_upper=True)
    out = _filter_by_date(entries, None, until)
    ids = {e.id for e in out}
    # 2025-06-01 is included (upper bound is end-of-day).
    assert "22222222-2222-2222-2222-222222222222" in ids
    # 2025-12-31 is after the upper bound.
    assert "33333333-3333-3333-3333-333333333333" not in ids
    # 2024-01-01 is before the upper bound — only an issue if a `since` were also set.
    assert "11111111-1111-1111-1111-111111111111" in ids


def test_filter_window_both_bounds():
    entries = [
        _mk_entry("11111111-1111-1111-1111-111111111111", _utc_iso(2024, 12, 31)),
        _mk_entry("22222222-2222-2222-2222-222222222222", _utc_iso(2025, 3, 15)),
        _mk_entry("33333333-3333-3333-3333-333333333333", _utc_iso(2025, 6, 1)),
    ]
    since = _parse_date_bound("2025-01-01")
    until = _parse_date_bound("2025-06-01", is_upper=True)
    out = _filter_by_date(entries, since, until)
    assert len(out) == 2
    assert {e.id for e in out} == {
        "22222222-2222-2222-2222-222222222222",
        "33333333-3333-3333-3333-333333333333",
    }


def test_filter_drops_unparseable_timestamps():
    entries = [
        _mk_entry("11111111-1111-1111-1111-111111111111", _utc_iso(2025, 3, 1)),
        Entry(id="bad", message="bad", tags=[], created_at="not-a-date"),
    ]
    out = _filter_by_date(entries, _parse_date_bound("2025-01-01"), None)
    assert len(out) == 1
    assert out[0].id.startswith("11111111")


# ---------------------------------------------------------------------------
# CLI integration: --since / --until on `list`
# ---------------------------------------------------------------------------


def test_list_since_filter(runner, data_dir):
    _seed("aaaaaaaa-1111-1111-1111-111111111111", "old", _utc_iso(2024, 1, 1))
    _seed("bbbbbbbb-2222-2222-2222-222222222222", "new", _utc_iso(2025, 6, 1))
    result = runner.invoke(main, ["list", "--since", "2025-01-01"])
    assert result.exit_code == 0
    assert "new" in result.output
    assert "old" not in result.output


def test_list_until_filter(runner, data_dir):
    _seed("aaaaaaaa-1111-1111-1111-111111111111", "old", _utc_iso(2024, 1, 1))
    _seed("bbbbbbbb-2222-2222-2222-222222222222", "new", _utc_iso(2025, 6, 1))
    result = runner.invoke(main, ["list", "--until", "2024-12-31"])
    assert result.exit_code == 0
    assert "old" in result.output
    assert "new" not in result.output


def test_list_window(runner, data_dir):
    _seed("aaaaaaaa-1111-1111-1111-111111111111", "before", _utc_iso(2024, 1, 1))
    _seed("bbbbbbbb-2222-2222-2222-222222222222", "inside", _utc_iso(2025, 6, 1))
    _seed("cccccccc-3333-3333-3333-333333333333", "after", _utc_iso(2026, 1, 1))
    result = runner.invoke(
        main,
        ["list", "--since", "2025-01-01", "--until", "2025-12-31"],
    )
    assert result.exit_code == 0
    assert "inside" in result.output
    assert "before" not in result.output
    assert "after" not in result.output


def test_list_invalid_since(runner, data_dir):
    result = runner.invoke(main, ["list", "--since", "garbage"])
    assert result.exit_code != 0
    assert "Invalid date" in result.output


def test_list_natural_today(runner, data_dir):
    today_iso = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _seed("aaaaaaaa-1111-1111-1111-111111111111", "today msg", today_iso)
    result = runner.invoke(main, ["list", "--since", "today"])
    assert result.exit_code == 0
    assert "today msg" in result.output


def test_list_relative_days(runner, data_dir):
    # Seed an entry 30 days ago — should NOT match a 7d filter
    old = (datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(days=30)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    recent = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _seed("aaaaaaaa-1111-1111-1111-111111111111", "old30", old)
    _seed("bbbbbbbb-2222-2222-2222-222222222222", "recent", recent)
    result = runner.invoke(main, ["list", "--since", "7d"])
    assert result.exit_code == 0
    assert "recent" in result.output
    assert "old30" not in result.output


# ---------------------------------------------------------------------------
# CLI integration: --since / --until on `search`
# ---------------------------------------------------------------------------


def test_search_since(runner, data_dir):
    _seed("aaaaaaaa-1111-1111-1111-111111111111", "auth pass", _utc_iso(2024, 1, 1))
    _seed("bbbbbbbb-2222-2222-2222-222222222222", "auth fail", _utc_iso(2025, 6, 1))
    result = runner.invoke(main, ["search", "auth", "--since", "2025-01-01"])
    assert result.exit_code == 0
    assert "auth fail" in result.output
    assert "auth pass" not in result.output


# ---------------------------------------------------------------------------
# CLI integration: --since / --until on `export`
# ---------------------------------------------------------------------------


def test_export_since(tmp_path, data_dir, monkeypatch):
    _seed("aaaaaaaa-1111-1111-1111-111111111111", "old export", _utc_iso(2024, 1, 1))
    _seed("bbbbbbbb-2222-2222-2222-222222222222", "new export", _utc_iso(2025, 6, 1))
    out_path = tmp_path / "out.md"
    runner = CliRunner(env={"DEVLOG_DATA_DIR": str(data_dir)})
    result = runner.invoke(
        main,
        ["export", "--output", str(out_path), "--since", "2025-01-01", "--quiet"],
    )
    assert result.exit_code == 0
    body = out_path.read_text(encoding="utf-8")
    assert "new export" in body
    assert "old export" not in body
