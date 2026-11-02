"""Tests for the pure AI core (``devlog._aiops``).

These tests are Ollama-free: the extractive digest, cosine ranking, and
answer formatting are deterministic functions over ``Entry`` lists and
float vectors, so they run on every CI machine without a local model.
"""

from __future__ import annotations

import math
from datetime import date

import pytest

from devlog import _aiops
from devlog.models import Entry


def _entry(entry_id: str, message: str, iso: str, tags=None) -> Entry:
    return Entry(
        id=entry_id, message=message, created_at=iso, tags=list(tags or [])
    )


# ---------------------------------------------------------------------------
# Extractive weekly digest
# ---------------------------------------------------------------------------


class TestSummarizeWeekText:
    def test_groups_entries_by_local_day_in_chronological_order(self):
        entries = [
            _entry("e2", "Wrote docs", "2026-10-29T10:00:00Z", ["docs"]),
            _entry("e1", "Shipped the export command", "2026-10-27T09:15:00Z", ["export"]),
        ]
        text = _aiops.summarize_week_text(
            entries, start_date=date(2026, 10, 27), end_date=date(2026, 11, 2)
        )
        # Day headers appear in ascending date order...
        assert text.index("2026-10-27") < text.index("2026-10-29")
        # ...and each entry message appears under its day.
        assert "Shipped the export command" in text
        assert "Wrote docs" in text
        # Header states the window and total.
        assert "2026-10-27" in text and "2026-11-02" in text
        assert "2 entries" in text

    def test_entry_lines_include_local_time_and_tags(self):
        entries = [
            _entry("e1", "Fixed the bug", "2026-10-27T09:15:00Z", ["bug", "tz"])
        ]
        text = _aiops.summarize_week_text(
            entries, start_date=date(2026, 10, 27), end_date=date(2026, 10, 27)
        )
        assert "09:15" in text
        assert "#bug" in text and "#tz" in text

    def test_tag_totals_footer_counts_each_tag_once_per_entry(self):
        entries = [
            _entry("e1", "a", "2026-10-27T09:00:00Z", ["x", "y"]),
            _entry("e2", "b", "2026-10-28T09:00:00Z", ["x"]),
        ]
        text = _aiops.summarize_week_text(
            entries, start_date=date(2026, 10, 27), end_date=date(2026, 10, 28)
        )
        assert "x×2" in text
        assert "y×1" in text

    def test_empty_week_produces_explicit_no_entries_text(self):
        text = _aiops.summarize_week_text(
            [], start_date=date(2026, 10, 27), end_date=date(2026, 11, 2)
        )
        assert "No entries" in text

    def test_entries_outside_window_are_excluded(self):
        entries = [
            _entry("old", "ancient", "2026-01-01T00:00:00Z"),
            _entry("in", "inside", "2026-10-28T12:00:00Z"),
        ]
        text = _aiops.summarize_week_text(
            entries, start_date=date(2026, 10, 27), end_date=date(2026, 11, 2)
        )
        assert "inside" in text
        assert "ancient" not in text

    def test_respects_explicit_timezone_for_day_bucketing(self):
        # 2026-11-01T20:30:00Z is 2026-11-02 04:30 in UTC+8.
        entries = [_entry("e1", "Late night work", "2026-11-01T20:30:00Z")]
        text = _aiops.summarize_week_text(
            entries,
            start_date=date(2026, 11, 1),
            end_date=date(2026, 11, 2),
            tz=_tz_plus_8(),
        )
        assert "2026-11-02" in text
        assert "04:30" in text


def _tz_plus_8():
    from zoneinfo import ZoneInfo

    return ZoneInfo("Asia/Singapore")


# ---------------------------------------------------------------------------
# Cosine similarity + ranking
# ---------------------------------------------------------------------------


class TestCosineSimilarity:
    def test_identical_vectors_score_one(self):
        assert _aiops.cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors_score_zero(self):
        assert _aiops.cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors_score_minus_one(self):
        assert _aiops.cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_proportional_vectors_score_one_regardless_of_magnitude(self):
        assert _aiops.cosine_similarity([2.0, 4.0], [10.0, 20.0]) == pytest.approx(1.0)

    def test_zero_vector_scores_zero_without_crashing(self):
        assert _aiops.cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


class TestRankEntries:
    def test_returns_ids_sorted_by_similarity_descending(self):
        query = [1.0, 0.0]
        vectors = {
            "a": [0.9, 0.1],   # high
            "b": [0.0, 1.0],   # zero
            "c": [0.5, 0.5],   # middle
        }
        ranked = _aiops.rank_by_query(query, vectors)
        assert [item_id for item_id, _score in ranked] == ["a", "c", "b"]
        assert all(math.isnan(s) is False for _i, s in ranked)

    def test_top_limit_truncates_results(self):
        vectors = {k: [1.0, 0.0] for k in ("a", "b", "c")}
        ranked = _aiops.rank_by_query([1.0, 0.0], vectors, top=2)
        assert len(ranked) == 2


# ---------------------------------------------------------------------------
# Ask-answer formatting
# ---------------------------------------------------------------------------


class TestFormatAskAnswer:
    def test_includes_query_top_match_and_scores(self):
        entries = [_entry("aaaaaaaa-1111", "Implemented JWT auth", "2026-10-27T09:15:00Z")]
        ranked = [("aaaaaaaa-1111", 0.87)]
        text = _aiops.format_ask_answer("what did I do on auth?", ranked, entries)
        assert "what did I do on auth?" in text
        assert "Implemented JWT auth" in text
        assert "0.87" in text

    def test_no_matches_prints_explicit_no_results(self):
        text = _aiops.format_ask_answer("anything?", [], [])
        assert "No" in text

    def test_skips_ranked_ids_missing_from_entries(self):
        # A ranked id with no matching Entry (stale index) is dropped; the
        # next valid match is surfaced instead.
        entries = [
            _entry("aaaaaaaa-1111", "Implemented JWT auth", "2026-10-27T09:15:00Z"),
            _entry("bbbbbbbb-2222", "Unrelated", "2026-10-28T09:15:00Z"),
        ]
        ranked = [("deadbeef-dead", 0.9), ("aaaaaaaa-1111", 0.5)]
        text = _aiops.format_ask_answer("q", ranked, entries)
        assert "Implemented JWT auth" in text
        assert "deadbeef" not in text

    def test_all_ranked_ids_missing_prints_no_results(self):
        entries = [_entry("bbbbbbbb-2222", "Unrelated", "2026-10-27T09:15:00Z")]
        ranked = [("deadbeef-dead", 0.9)]
        text = _aiops.format_ask_answer("q", ranked, entries)
        assert "No" in text
