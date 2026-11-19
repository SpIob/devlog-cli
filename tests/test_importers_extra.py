"""Extra coverage tests for ``devlog._importers``.

Complements ``tests/test_importers.py`` with the malformed-payload,
generic-import, format-detection fall-through and ``import_file``
dispatch branches. Public API surface exercised here:

* ``import_jrnl_json`` / ``import_dayone_json`` / ``import_generic_json``
* ``detect_format``
* ``import_file``

Timestamp expectations follow the module's normalisation: values are
run through ``datetime.fromisoformat`` (with ``Z`` -> ``+00:00``) and
re-emitted as ``YYYY-MM-DDTHH:MM:SSZ`` in UTC. Empty / unparseable
timestamps degrade to ``None`` from the private parser instead of
raising, and the importers fall back to "now".
"""

from __future__ import annotations

import json
import re

import pytest

from devlog._importers import (
    _parse_iso_timestamp,
    detect_format,
    import_dayone_json,
    import_file,
    import_generic_json,
    import_jrnl_json,
)

UTC_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _write(tmp_path, name, payload):
    """Write *payload* (an object -> JSON) to ``tmp_path/name`` as a real file."""
    f = tmp_path / name
    if isinstance(payload, str):
        f.write_text(payload)
    else:
        f.write_text(json.dumps(payload))
    return f


# ---------------------------------------------------------------------------
# _parse_iso_timestamp: empty / unparseable -> None, offsets normalised to UTC
# ---------------------------------------------------------------------------


def test_parse_iso_timestamp_empty_is_none():
    assert _parse_iso_timestamp("") is None


@pytest.mark.parametrize("bad", ["garbage", "2024-13-99", "not-a-date", "2024/01/15"])
def test_parse_iso_timestamp_unparseable_is_none(bad):
    assert _parse_iso_timestamp(bad) is None


def test_parse_iso_timestamp_normalises_offset_to_utc():
    assert _parse_iso_timestamp("2024-01-15T10:30:00+02:00") == "2024-01-15T08:30:00Z"
    assert _parse_iso_timestamp("2024-01-15T10:30:00Z") == "2024-01-15T10:30:00Z"
    # Naive timestamps are assumed UTC.
    assert _parse_iso_timestamp("2024-01-15T10:30:00") == "2024-01-15T10:30:00Z"


# ---------------------------------------------------------------------------
# _load_json_payload: malformed / wrong-shape files -> clean ValueError
# ---------------------------------------------------------------------------


def test_non_json_file_raises_value_error_naming_path(tmp_path):
    f = _write(tmp_path, "broken.json", "{not json")
    with pytest.raises(ValueError) as exc:
        import_jrnl_json(str(f))
    msg = str(exc.value)
    assert "Invalid JSON in" in msg
    assert str(f) in msg
    # Not a raw JSONDecodeError leaking through.
    assert not isinstance(exc.value, json.JSONDecodeError)


def test_json_without_entries_list_raises_value_error(tmp_path):
    f = _write(tmp_path, "wrong.json", {"foo": []})
    with pytest.raises(ValueError) as exc:
        import_dayone_json(str(f))
    msg = str(exc.value)
    assert "Invalid import file" in msg
    assert str(f) in msg
    assert "entries" in msg  # explains the expected shape


@pytest.mark.parametrize(
    "payload", [[], {}, {"entries": "nope"}, {"entries": 3}, {"entries": None}, 42, 3.5, True]
)
def test_non_object_or_bad_entries_shape_raises_value_error(tmp_path, payload):
    f = _write(tmp_path, "bad.json", payload)
    with pytest.raises(ValueError, match="Invalid import file"):
        import_generic_json(str(f))


def test_json_string_scalar_payload_raises_value_error(tmp_path):
    f = _write(tmp_path, "s.json", json.dumps("just a string"))
    with pytest.raises(ValueError, match="Invalid import file"):
        import_generic_json(str(f))
    assert detect_format(str(f)) == "json"  # expectation only, not exercised further


def test_empty_entries_list_is_valid_for_every_importer(tmp_path):
    f = _write(tmp_path, "empty.json", {"entries": []})
    assert import_jrnl_json(str(f)) == []
    assert import_dayone_json(str(f)) == []
    assert import_generic_json(str(f)) == []


# ---------------------------------------------------------------------------
# jrnl: missing/blank date, missing text, tag coercion
# ---------------------------------------------------------------------------


def test_jrnl_skips_entry_without_text(tmp_path):
    f = _write(
        tmp_path,
        "jrnl.json",
        {
            "entries": [
                {"date": "2024-01-01T00:00:00", "text": "", "starred": False},
                {"date": "2024-01-01T00:00:00", "starred": False},  # no text key
                {"date": "2024-01-02T00:00:00", "text": "kept", "starred": False},
            ]
        },
    )
    entries = import_jrnl_json(str(f))
    assert [e.message for e in entries] == ["kept"]


@pytest.mark.parametrize("missing", [{}, {"date": ""}, {"date": "garbage"}])
def test_jrnl_missing_or_blank_date_falls_back_to_now(tmp_path, missing):
    item = {"text": "no date", "starred": False, **missing}
    f = _write(tmp_path, "jrnl.json", {"entries": [item]})
    entries = import_jrnl_json(str(f))
    assert len(entries) == 1
    assert UTC_TS_RE.match(entries[0].created_at)


def test_jrnl_tag_string_coerced_to_list(tmp_path):
    f = _write(
        tmp_path,
        "jrnl.json",
        {
            "entries": [
                {"date": "2024-01-01T00:00:00", "text": "x", "tags": "Solo Tag", "starred": False}
            ]
        },
    )
    entries = import_jrnl_json(str(f))
    assert entries[0].tags == ["solo-tag"]


def test_jrnl_missing_tags_is_empty_list(tmp_path):
    f = _write(
        tmp_path,
        "jrnl.json",
        {"entries": [{"date": "2024-01-01T00:00:00", "text": "x", "starred": False}]},
    )
    assert import_jrnl_json(str(f))[0].tags == []


def test_jrnl_normalises_offset_timestamp(tmp_path):
    f = _write(
        tmp_path,
        "jrnl.json",
        {"entries": [{"date": "2024-01-15T10:30:00+02:00", "text": "x", "starred": False}]},
    )
    assert import_jrnl_json(str(f))[0].created_at == "2024-01-15T08:30:00Z"


# ---------------------------------------------------------------------------
# Day One: missing date, missing text, tag coercion, uuid default
# ---------------------------------------------------------------------------


def test_dayone_skips_entry_without_text(tmp_path):
    f = _write(
        tmp_path,
        "dayone.json",
        {
            "entries": [
                {"creationDate": "2024-01-01T00:00:00Z", "text": ""},
                {"creationDate": "2024-01-01T00:00:00Z"},
                {"creationDate": "2024-01-02T00:00:00Z", "text": "kept"},
            ]
        },
    )
    entries = import_dayone_json(str(f))
    assert [e.message for e in entries] == ["kept"]


@pytest.mark.parametrize("missing", [{}, {"creationDate": ""}, {"creationDate": "nope"}])
def test_dayone_missing_or_blank_date_falls_back_to_now(tmp_path, missing):
    item = {"text": "no date", **missing}
    f = _write(tmp_path, "dayone.json", {"entries": [item]})
    entries = import_dayone_json(str(f))
    assert len(entries) == 1
    assert UTC_TS_RE.match(entries[0].created_at)


def test_dayone_tag_string_coerced_to_list(tmp_path):
    f = _write(
        tmp_path,
        "dayone.json",
        {"entries": [{"creationDate": "2024-01-01T00:00:00Z", "text": "x", "tags": "Solo Tag"}]},
    )
    entries = import_dayone_json(str(f))
    assert entries[0].tags == ["solo-tag"]


def test_dayone_missing_tags_empty_and_uuid_minted(tmp_path):
    f = _write(
        tmp_path,
        "dayone.json",
        {"entries": [{"creationDate": "2024-01-01T00:00:00Z", "text": "x"}]},
    )
    entry = import_dayone_json(str(f))[0]
    assert entry.tags == []
    assert len(entry.id) >= 8


def test_dayone_normalises_offset_timestamp(tmp_path):
    f = _write(
        tmp_path,
        "dayone.json",
        {"entries": [{"creationDate": "2024-01-15T10:30:00+02:00", "text": "x"}]},
    )
    assert import_dayone_json(str(f))[0].created_at == "2024-01-15T08:30:00Z"


# ---------------------------------------------------------------------------
# import_generic_json
# ---------------------------------------------------------------------------


def test_generic_import_full_round_trip(tmp_path):
    f = _write(
        tmp_path,
        "generic.json",
        {
            "entries": [
                {
                    "id": "generic-1",
                    "message": "hello generic",
                    "created_at": "2024-01-15T10:30:00+02:00",
                    "tags": ["Work Item", "travel"],
                    "updated_at": "2024-01-16T00:00:00Z",
                }
            ]
        },
    )
    entries = import_generic_json(str(f))
    assert len(entries) == 1
    e = entries[0]
    assert e.id == "generic-1"
    assert e.message == "hello generic"
    assert e.created_at == "2024-01-15T08:30:00Z"
    assert e.tags == ["work-item", "travel"]
    assert e.updated_at == "2024-01-16T00:00:00Z"


def test_generic_skips_entry_without_message(tmp_path):
    f = _write(
        tmp_path,
        "generic.json",
        {
            "entries": [
                {"message": ""},
                {"id": "no-message"},
                {"message": "kept", "created_at": "2024-01-01T00:00:00Z"},
            ]
        },
    )
    entries = import_generic_json(str(f))
    assert [e.message for e in entries] == ["kept"]


def test_generic_missing_created_at_falls_back_to_now(tmp_path):
    f = _write(tmp_path, "generic.json", {"entries": [{"message": "no ts"}]})
    entries = import_generic_json(str(f))
    assert UTC_TS_RE.match(entries[0].created_at)


def test_generic_unparseable_created_at_kept_as_is(tmp_path):
    """A present-but-unparseable created_at is passed through verbatim."""
    f = _write(tmp_path, "generic.json", {"entries": [{"message": "x", "created_at": "raw-stamp"}]})
    assert import_generic_json(str(f))[0].created_at == "raw-stamp"


def test_generic_tag_string_coerced_to_list(tmp_path):
    f = _write(
        tmp_path,
        "generic.json",
        {"entries": [{"message": "x", "created_at": "2024-01-01T00:00:00Z", "tags": "One Tag"}]},
    )
    assert import_generic_json(str(f))[0].tags == ["one-tag"]


def test_generic_missing_tags_empty_list(tmp_path):
    f = _write(
        tmp_path,
        "generic.json",
        {"entries": [{"message": "x", "created_at": "2024-01-01T00:00:00Z"}]},
    )
    assert import_generic_json(str(f))[0].tags == []


def test_generic_missing_id_minted(tmp_path):
    f = _write(
        tmp_path,
        "generic.json",
        {"entries": [{"message": "x", "created_at": "2024-01-01T00:00:00Z"}]},
    )
    assert len(import_generic_json(str(f))[0].id) >= 8


def test_generic_updated_at_absent_is_none(tmp_path):
    f = _write(
        tmp_path,
        "generic.json",
        {"entries": [{"message": "x", "created_at": "2024-01-01T00:00:00Z"}]},
    )
    assert import_generic_json(str(f))[0].updated_at is None


def test_generic_updated_at_unparseable_kept_as_is(tmp_path):
    f = _write(
        tmp_path,
        "generic.json",
        {
            "entries": [
                {"message": "x", "created_at": "2024-01-01T00:00:00Z", "updated_at": "raw-updated"}
            ]
        },
    )
    assert import_generic_json(str(f))[0].updated_at == "raw-updated"


def test_generic_updated_at_normalised(tmp_path):
    f = _write(
        tmp_path,
        "generic.json",
        {
            "entries": [
                {
                    "message": "x",
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-15T10:30:00+02:00",
                }
            ]
        },
    )
    assert import_generic_json(str(f))[0].updated_at == "2024-01-15T08:30:00Z"


# ---------------------------------------------------------------------------
# detect_format: shape detection + extension fall-through
# ---------------------------------------------------------------------------


def test_detect_format_jrnl_shape(tmp_path):
    f = _write(
        tmp_path,
        "j.json",
        {"entries": [{"text": "hi", "date": "2024-01-01T00:00:00", "starred": False}]},
    )
    assert detect_format(str(f)) == "jrnl"


def test_detect_format_dayone_shape(tmp_path):
    f = _write(
        tmp_path, "d.json", {"entries": [{"creationDate": "2024-01-01T00:00:00Z", "text": "hi"}]}
    )
    assert detect_format(str(f)) == "dayone"


def test_detect_format_devlog_export_shape(tmp_path):
    f = _write(
        tmp_path,
        "x.json",
        {"entries": [{"id": "i", "message": "m", "created_at": "2024-01-01T00:00:00Z"}]},
    )
    assert detect_format(str(f)) == "json"


def test_detect_format_empty_entries_falls_through_to_extension(tmp_path):
    # entries present but empty -> no shape match -> extension decides.
    f_json = _write(tmp_path, "e.json", {"entries": []})
    assert detect_format(str(f_json)) == "json"
    f_md = _write(tmp_path, "e.md", {"entries": []})
    assert detect_format(str(f_md)) == "markdown"
    f_txt = _write(tmp_path, "e.txt", {"entries": []})
    assert detect_format(str(f_txt)) == "unknown"


def test_detect_format_valid_json_unknown_shape_by_extension(tmp_path):
    f_json = _write(tmp_path, "u.json", {"foo": 1})
    assert detect_format(str(f_json)) == "json"
    f_md = _write(tmp_path, "u.md", {"foo": 1})
    assert detect_format(str(f_md)) == "markdown"
    f_unknown = _write(tmp_path, "u.txt", {"foo": 1})
    assert detect_format(str(f_unknown)) == "unknown"


def test_detect_format_non_json_by_extension(tmp_path):
    f_md = _write(tmp_path, "n.md", "# just markdown")
    assert detect_format(str(f_md)) == "markdown"
    f_json = _write(tmp_path, "n.json", "not json")
    assert detect_format(str(f_json)) == "json"
    f_unknown = _write(tmp_path, "n.txt", "plain text")
    assert detect_format(str(f_unknown)) == "unknown"


def test_detect_format_json_array_payload_is_json(tmp_path):
    # A valid JSON array has no "entries" key -> extension fall-through.
    f = _write(tmp_path, "arr.json", "[1, 2, 3]")
    assert detect_format(str(f)) == "json"


# ---------------------------------------------------------------------------
# import_file: dispatch, explicit override, auto-detect, error path
# ---------------------------------------------------------------------------


def test_import_file_dispatch_jrnl(tmp_path):
    f = _write(
        tmp_path,
        "jrnl.json",
        {"entries": [{"date": "2024-01-01T00:00:00", "text": "from jrnl", "starred": False}]},
    )
    entries = import_file(str(f), "jrnl")
    assert [e.message for e in entries] == ["from jrnl"]


def test_import_file_dispatch_dayone(tmp_path):
    f = _write(
        tmp_path,
        "dayone.json",
        {"entries": [{"creationDate": "2024-01-01T00:00:00Z", "text": "from d1"}]},
    )
    entries = import_file(str(f), "dayone")
    assert [e.message for e in entries] == ["from d1"]


def test_import_file_dispatch_generic(tmp_path):
    f = _write(
        tmp_path,
        "generic.json",
        {"entries": [{"message": "from generic", "created_at": "2024-01-01T00:00:00Z"}]},
    )
    entries = import_file(str(f), "generic")
    assert [e.message for e in entries] == ["from generic"]


def test_import_file_auto_detects_jrnl(tmp_path):
    f = _write(
        tmp_path,
        "auto.json",
        {"entries": [{"date": "2024-01-01T00:00:00", "text": "auto", "starred": False}]},
    )
    assert [e.message for e in import_file(str(f))] == ["auto"]
    # explicit "auto" behaves like None
    assert [e.message for e in import_file(str(f), "auto")] == ["auto"]


def test_import_file_explicit_format_overrides_detection(tmp_path):
    # jrnl-shaped payload, but forced through the generic reader.
    f = _write(
        tmp_path,
        "jrnl.json",
        {"entries": [{"date": "2024-01-01T00:00:00", "text": "hi", "starred": False}]},
    )
    # detect_format says jrnl...
    assert detect_format(str(f)) == "jrnl"
    # ...but the generic reader only looks at "message", so this yields nothing.
    assert import_file(str(f), "generic") == []
    # and the jrnl reader yields the entry.
    assert [e.message for e in import_file(str(f), "jrnl")] == ["hi"]


def test_import_file_unknown_format_raises(tmp_path):
    f = _write(tmp_path, "x.txt", "irrelevant")
    with pytest.raises(ValueError, match="Unknown import format: bogus"):
        import_file(str(f), "bogus")


def test_import_file_auto_markdown_is_unknown_format(tmp_path):
    # detect_format can return "markdown", which import_file does not dispatch.
    f = _write(tmp_path, "x.md", "# heading")
    assert detect_format(str(f)) == "markdown"
    with pytest.raises(ValueError, match="Unknown import format: markdown"):
        import_file(str(f))


def test_import_file_auto_detected_devlog_export_has_no_reader(tmp_path):
    # A devlog-export-shaped file auto-detects to "json", for which
    # import_file has no branch -> clean ValueError (not a crash).
    f = _write(
        tmp_path,
        "d.json",
        {"entries": [{"id": "i", "message": "m", "created_at": "2024-01-01T00:00:00Z"}]},
    )
    assert detect_format(str(f)) == "json"
    with pytest.raises(ValueError, match="Unknown import format: json"):
        import_file(str(f))
