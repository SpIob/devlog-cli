"""Tests for the ``devlog digest`` and ``devlog ask`` commands.

The extractive digest and answer formatting run Ollama-free; LLM-mode
tests point ``DEVLOG_OLLAMA_HOST`` at the shared fake server (real
HTTP round-trips) via the ``ai_on`` fixture.
"""

from __future__ import annotations

import json

import pytest

from devlog.cli import main


@pytest.fixture()
def ai_on(monkeypatch, ollama_server):
    """Enable LLM mode and point Ollama at the shared fake server."""
    monkeypatch.setenv("DEVLOG_AI", "1")
    monkeypatch.setenv("DEVLOG_OLLAMA_HOST", ollama_server.url)
    return ollama_server


@pytest.fixture()
def week_entries(seed_entry, data_dir, runner):
    """Two entries inside the default week window (today, yesterday).

    Depends on ``data_dir`` so in-process seeding and the CLI runner
    share the same DEVLOG_DATA_DIR (the runner overrides os.environ
    during invoke with its own tmp_path env dict).
    """
    from datetime import datetime, timedelta, timezone

    now = datetime.now(tz=timezone.utc)
    today_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    yesterday_iso = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    seed_entry("e1", "Shipped the export command", today_iso, ["export"])
    seed_entry("e2", "Fixed a timezone bug", yesterday_iso, ["bug"])


# ---------------------------------------------------------------------------
# devlog digest --week (extractive)
# ---------------------------------------------------------------------------


class TestDigestExtractive:
    def test_renders_week_digest_with_entry_messages(self, runner, week_entries):
        result = runner.invoke(main, ["digest", "--week"])
        assert result.exit_code == 0, result.output
        assert "Shipped the export command" in result.output
        assert "Fixed a timezone bug" in result.output
        assert "#export" in result.output

    def test_quiet_prints_plain_extractive_text(self, runner, week_entries):
        result = runner.invoke(main, ["digest", "--week", "--quiet"])
        assert result.exit_code == 0, result.output
        assert "Shipped the export command" in result.output

    def test_json_outputs_single_summary_object(self, runner, week_entries):
        result = runner.invoke(main, ["digest", "--week", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["entry_count"] == 2
        assert "Shipped the export command" in payload["digest"]
        assert set(payload) == {"week_start", "week_end", "entry_count", "digest"}

    def test_empty_week_prints_info_and_exits_zero(self, runner):
        result = runner.invoke(main, ["digest", "--week"])
        assert result.exit_code == 0, result.output
        assert "No entries" in result.output

    def test_works_under_active_backend(self, runner, week_entries):
        # Backend-agnostic by design: CI runs the whole suite once per
        # backend (DEVLOG_BACKEND=json / sqlite), so this asserts the
        # digest path on whichever backend is active.
        result = runner.invoke(main, ["digest", "--week", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["entry_count"] == 2


# ---------------------------------------------------------------------------
# devlog digest --week --llm (local LLM summary)
# ---------------------------------------------------------------------------


class TestDigestLLM:
    def test_llm_flag_sends_digest_to_ollama_and_prints_reply(
        self, runner, week_entries, ai_on
    ):
        result = runner.invoke(main, ["digest", "--week", "--llm"])
        assert result.exit_code == 0, result.output
        assert "You shipped the importers." in result.output
        assert ai_on.last_path == "/api/generate"
        sent = ai_on.last_payload
        assert sent is not None
        # The extractive digest is the prompt body.
        assert "Shipped the export command" in sent["prompt"]

    def test_llm_quiet_prints_only_the_reply(self, runner, week_entries, ai_on):
        result = runner.invoke(main, ["digest", "--week", "--llm", "--quiet"])
        assert result.exit_code == 0, result.output
        assert "You shipped the importers." in result.output

    def test_model_flag_reaches_ollama(self, runner, week_entries, ai_on):
        result = runner.invoke(main, ["digest", "--week", "--llm", "--model", "qwen2.5"])
        assert result.exit_code == 0, result.output
        sent = ai_on.last_payload
        assert sent is not None
        assert sent["model"] == "qwen2.5"

    def test_llm_with_server_down_fails_cleanly(self, runner, week_entries, monkeypatch):
        monkeypatch.setenv("DEVLOG_AI", "1")
        monkeypatch.setenv("DEVLOG_OLLAMA_HOST", "http://127.0.0.1:1")
        result = runner.invoke(main, ["digest", "--week", "--llm"])
        assert result.exit_code == 1
        assert "Ollama" in result.output

    def test_no_llm_without_flag_or_env(self, runner, week_entries, ollama_server):
        # Neither --llm nor DEVLOG_AI: no Ollama traffic at all.
        result = runner.invoke(main, ["digest", "--week"])
        assert result.exit_code == 0, result.output
        assert ollama_server.request_count == 0


# ---------------------------------------------------------------------------
# devlog ask "<query>" (local embedding search)
# ---------------------------------------------------------------------------


class TestAsk:
    def test_ask_returns_ranked_matches_and_builds_index(
        self, runner, week_entries, ai_on, data_dir
    ):
        result = runner.invoke(main, ["ask", "what did I ship?"])
        assert result.exit_code == 0, result.output
        assert "what did I ship?" in result.output
        # Both entry messages were embedded into the index.
        assert (data_dir / "embeddings.json").exists()

    def test_ask_json_outputs_structured_results(self, runner, week_entries, ai_on):
        result = runner.invoke(main, ["ask", "ship", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["query"] == "ship"
        assert len(payload["results"]) == 2
        first = payload["results"][0]
        assert set(first) == {"id", "message", "created_at", "score"}
        scores = [r["score"] for r in payload["results"]]
        assert scores == sorted(scores, reverse=True)

    def test_ask_top_limits_results(self, runner, week_entries, ai_on):
        result = runner.invoke(main, ["ask", "ship", "--json", "--top", "1"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert len(payload["results"]) == 1

    def test_ask_on_empty_journal_prints_info(self, runner, ai_on):
        result = runner.invoke(main, ["ask", "anything"])
        assert result.exit_code == 0, result.output
        assert "No entries" in result.output

    def test_ask_with_server_down_fails_cleanly(self, runner, week_entries, monkeypatch):
        monkeypatch.setenv("DEVLOG_OLLAMA_HOST", "http://127.0.0.1:1")
        result = runner.invoke(main, ["ask", "anything"])
        assert result.exit_code == 1
        assert "Ollama" in result.output

    def test_ask_works_under_active_backend(self, runner, week_entries, ai_on):
        # Backend-agnostic: verified under both CI backend passes.
        result = runner.invoke(main, ["ask", "ship", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert len(payload["results"]) == 2


# ---------------------------------------------------------------------------
# Registration & discoverability
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_digest_and_ask_appear_in_root_banner(self, runner):
        result = runner.invoke(main, [])
        assert result.exit_code == 0, result.output
        assert "digest" in result.output
        assert "ask" in result.output
