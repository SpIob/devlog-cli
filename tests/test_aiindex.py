"""Tests for the embedding index (``devlog._aiindex``).

The index persists embeddings in the journal data dir and stays usable
under *both* storage backends. All Ollama traffic goes through the
conftest fake server — real HTTP round-trips, no mocks.
"""

from __future__ import annotations

import json

import pytest

from devlog import _aiindex
from devlog.models import Entry


def _entry(entry_id: str, message: str, iso: str, tags=None) -> Entry:
    return Entry(
        id=entry_id, message=message, created_at=iso, tags=list(tags or [])
    )


@pytest.fixture()
def _ollama_host(monkeypatch, ollama_server):
    """Point DEVLOG_OLLAMA_HOST at the shared fake server."""
    monkeypatch.setenv("DEVLOG_OLLAMA_HOST", ollama_server.url)
    return ollama_server


# ---------------------------------------------------------------------------
# Index file round-trip
# ---------------------------------------------------------------------------


class TestIndexFile:
    def test_save_then_load_round_trips_payload(self, data_dir):
        idx = _aiindex.EmbeddingIndex(
            model="nomic-embed-text",
            entries={"aaaaaaaa-1111": [0.1, 0.2], "bbbbbbbb-2222": [0.3, 0.4]},
        )
        _aiindex.save_index(data_dir, idx)
        loaded = _aiindex.load_index(data_dir)
        assert loaded is not None
        assert loaded.model == "nomic-embed-text"
        assert loaded.entries["aaaaaaaa-1111"] == [0.1, 0.2]

    def test_load_missing_file_returns_none(self, data_dir):
        assert _aiindex.load_index(data_dir) is None

    def test_load_corrupt_file_returns_none(self, data_dir):
        (data_dir / "embeddings.json").write_text("{not json", encoding="utf-8")
        assert _aiindex.load_index(data_dir) is None

    def test_load_wrong_shape_returns_none(self, data_dir):
        (data_dir / "embeddings.json").write_text(
            json.dumps({"wrong": "shape"}), encoding="utf-8"
        )
        assert _aiindex.load_index(data_dir) is None


# ---------------------------------------------------------------------------
# ensure_index: build once, reuse, rebuild incrementally
# ---------------------------------------------------------------------------


class TestEnsureIndex:
    def test_no_entries_builds_nothing(self, data_dir, _ollama_host):
        idx = _aiindex.ensure_index([], data_dir)
        assert idx is not None
        assert idx.entries == {}
        assert _ollama_host.request_count == 0

    def test_builds_and_persists_new_index(self, data_dir, _ollama_host):
        entries = [_entry("e1", "Shipped the export command", "2026-10-27T09:15:00Z")]
        idx = _aiindex.ensure_index(entries, data_dir)
        assert set(idx.entries) == {"e1"}
        assert _ollama_host.last_path == "/api/embed"
        sent = _ollama_host.last_payload
        assert sent is not None
        assert sent["model"] == "nomic-embed-text"
        # persisted
        assert _aiindex.load_index(data_dir) is not None

    def test_reuses_valid_index_without_ollama_calls(self, data_dir, _ollama_host):
        entries = [_entry("e1", "Shipped the export command", "2026-10-27T09:15:00Z")]
        _aiindex.ensure_index(entries, data_dir)
        assert _ollama_host.request_count == 1
        _aiindex.ensure_index(entries, data_dir)
        assert _ollama_host.request_count == 1  # unchanged: fully reused

    def test_missing_entries_are_embedded_incrementally(self, data_dir, _ollama_host):
        e1 = _entry("e1", "Shipped the export command", "2026-10-27T09:15:00Z")
        e2 = _entry("e2", "Fixed a timezone bug", "2026-10-28T09:15:00Z")
        _aiindex.ensure_index([e1], data_dir)
        calls_after_first = _ollama_host.request_count
        idx = _aiindex.ensure_index([e1, e2], data_dir)
        assert set(idx.entries) == {"e1", "e2"}
        assert _ollama_host.request_count > calls_after_first
        # The incremental call embedded only the missing entry.
        sent = _ollama_host.last_payload
        assert sent is not None
        assert sent["input"] == ["Fixed a timezone bug"]

    def test_stale_entries_are_pruned_from_loaded_index(self, data_dir, _ollama_host):
        e1 = _entry("e1", "one", "2026-10-27T09:15:00Z")
        _aiindex.ensure_index([e1], data_dir)
        # e2 replaces the journal; e1 no longer exists.
        e2 = _entry("e2", "two", "2026-10-28T09:15:00Z")
        idx = _aiindex.ensure_index([e2], data_dir)
        assert set(idx.entries) == {"e2"}

    def test_model_change_triggers_full_rebuild(self, data_dir, _ollama_host, monkeypatch):
        e1 = _entry("e1", "one", "2026-10-27T09:15:00Z")
        _aiindex.ensure_index([e1], data_dir)
        calls_first = _ollama_host.request_count
        monkeypatch.setenv("DEVLOG_EMBED_MODEL", "other-model")
        idx = _aiindex.ensure_index([e1], data_dir)
        assert idx.model == "other-model"
        assert _ollama_host.request_count > calls_first  # re-embedded

    def test_corrupt_index_file_is_rebuilt(self, data_dir, _ollama_host):
        (data_dir / "embeddings.json").write_text("{not json", encoding="utf-8")
        e1 = _entry("e1", "one", "2026-10-27T09:15:00Z")
        idx = _aiindex.ensure_index([e1], data_dir)
        assert set(idx.entries) == {"e1"}


# ---------------------------------------------------------------------------
# Search over the index
# ---------------------------------------------------------------------------


class TestSearchEntries:
    def test_ranking_puts_closer_match_first(self, data_dir, _ollama_host):
        # The fake server derives vectors from char-code sums: distinct
        # messages get distinct vectors, so ranking is meaningful.
        e1 = _entry("e1", "aaaaaaaa", "2026-10-27T09:15:00Z")
        e2 = _entry("e2", "zzzzzzzz", "2026-10-28T09:15:00Z")
        entries = [e1, e2]
        idx = _aiindex.ensure_index(entries, data_dir)
        ranked = _aiindex.search_entries("query", entries, idx)
        assert len(ranked) == 2
        scores = [s for _i, s in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_returns_id_score_pairs(self, data_dir, _ollama_host):
        e1 = _entry("e1", "message one", "2026-10-27T09:15:00Z")
        idx = _aiindex.ensure_index([e1], data_dir)
        ranked = _aiindex.search_entries("anything", [e1], idx)
        assert ranked[0][0] == "e1"
        assert isinstance(ranked[0][1], float)

    def test_limit_truncates(self, data_dir, _ollama_host):
        entries = [
            _entry(f"e{i}", f"message {i}", f"2026-10-2{i}T09:15:00Z") for i in range(3)
        ]
        idx = _aiindex.ensure_index(entries, data_dir)
        ranked = _aiindex.search_entries("query", entries, idx, top=2)
        assert len(ranked) == 2
