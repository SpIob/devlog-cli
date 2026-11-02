"""Tests for the tolerant deserializer ``Entry.from_dict``.

Contract (ROADMAP Tier 0): unknown keys in stored JSON objects must be
ignored rather than crashing every command via ``Entry(**item)``; the
three identity fields (``id``, ``message``, ``created_at``) stay
required. ``storage.load_entries()`` must route through this
constructor so future schema additions degrade gracefully.
"""

from __future__ import annotations

import pytest

from devlog import storage
from devlog.models import Entry
from tests.conftest import active_backend


class TestEntryFromDict:
    def test_builds_entry_from_valid_dict(self):
        item = {
            "id": "abcd1234",
            "message": "hello",
            "created_at": "2026-01-01T00:00:00Z",
            "tags": ["api"],
        }
        e = Entry.from_dict(item)
        assert e.id == "abcd1234"
        assert e.message == "hello"
        assert e.created_at == "2026-01-01T00:00:00Z"
        assert e.tags == ["api"]
        assert e.updated_at is None

    def test_updated_at_is_preserved(self):
        item = {
            "id": "abcd1234",
            "message": "hello",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
        }
        assert Entry.from_dict(item).updated_at == "2026-01-02T00:00:00Z"

    def test_unknown_keys_are_ignored(self):
        item = {
            "id": "abcd1234",
            "message": "hello",
            "created_at": "2026-01-01T00:00:00Z",
            "pinned": True,
            "project": "dep",
            "future_field": {"nested": 1},
        }
        e = Entry.from_dict(item)
        assert (e.id, e.message, e.created_at) == ("abcd1234", "hello", "2026-01-01T00:00:00Z")
        assert not hasattr(e, "pinned")

    @pytest.mark.parametrize("missing", ["id", "message", "created_at"])
    def test_missing_required_key_raises_value_error(self, missing):
        item = {
            "id": "abcd1234",
            "message": "hello",
            "created_at": "2026-01-01T00:00:00Z",
        }
        del item[missing]
        with pytest.raises(ValueError, match=missing):
            Entry.from_dict(item)

    def test_tags_default_to_empty_when_missing(self):
        item = {"id": "x", "message": "m", "created_at": "2026-01-01T00:00:00Z"}
        assert Entry.from_dict(item).tags == []

    def test_non_dict_input_raises_type_error(self):
        with pytest.raises(TypeError):
            Entry.from_dict(["not", "a", "dict"])


class TestLoadEntriesTolerant:
    @pytest.mark.skipif(
        active_backend() == "sqlite",
        reason="asserts entries.json on-disk semantics (JSON backend only)",
    )
    def test_load_entries_ignores_unknown_fields(self, data_dir, write_entries):
        """Future-schema entries load instead of crashing every command."""
        write_entries(
            data_dir,
            {
                "entries": [
                    {
                        "id": "abcd1234ef567890",
                        "message": "from the future",
                        "created_at": "2026-01-01T00:00:00Z",
                        "pinned": True,
                    }
                ]
            },
        )
        entries = storage.load_entries()
        assert len(entries) == 1
        assert entries[0].message == "from the future"
