"""Tests for the Ollama HTTP client (``devlog._ollama``).

These tests exercise real HTTP round-trips against the threaded
localhost fake-Ollama server from ``conftest.py`` — no mocks — so
request shapes, JSON parsing, error mapping, and timeouts are all
verified against actual sockets. The fake server keys its responses
off request content (an unknown model name 404s exactly like the real
Ollama server).
"""

from __future__ import annotations

import pytest

from devlog import _ollama


class TestGenerate:
    def test_posts_to_api_generate_and_returns_response_text(self, ollama_server):
        out = _ollama.generate("summarize this", model="llama3", host=ollama_server.url)
        assert out == "You shipped the importers."
        assert ollama_server.last_path == "/api/generate"
        sent = ollama_server.last_payload
        assert sent is not None
        assert sent["prompt"] == "summarize this"
        assert sent["model"] == "llama3"
        assert sent["stream"] is False

    def test_connection_refused_raises_ollama_unavailable(self):
        # Port 1 on localhost: nothing listens there.
        with pytest.raises(_ollama.OllamaUnavailable):
            _ollama.generate("hi", model="llama3", host="http://127.0.0.1:1")

    def test_http_error_status_raises_ollama_error(self, ollama_server):
        with pytest.raises(_ollama.OllamaError) as excinfo:
            _ollama.generate("x", model="missing-model", host=ollama_server.url)
        assert "404" in str(excinfo.value)


class TestEmbed:
    def test_posts_to_api_embed_and_returns_vectors(self, ollama_server):
        vectors = _ollama.embed(
            ["hello world"], model="nomic-embed-text", host=ollama_server.url
        )
        # One vector per input, all floats, deterministic across calls.
        assert len(vectors) == 1
        assert all(isinstance(x, float) for x in vectors[0])
        again = _ollama.embed(
            ["hello world"], model="nomic-embed-text", host=ollama_server.url
        )
        assert vectors == again
        assert ollama_server.last_path == "/api/embed"
        sent = ollama_server.last_payload
        assert sent is not None
        assert sent["input"] == ["hello world"]
        assert sent["model"] == "nomic-embed-text"

    def test_missing_embeddings_key_raises_ollama_error(self, ollama_server):
        with pytest.raises(_ollama.OllamaError):
            _ollama.embed(["__broken__"], model="nomic-embed-text", host=ollama_server.url)

    def test_connection_refused_raises_ollama_unavailable(self):
        with pytest.raises(_ollama.OllamaUnavailable):
            _ollama.embed(["x"], model="nomic-embed-text", host="http://127.0.0.1:1")


class TestErrorHierarchy:
    def test_ollama_unavailable_is_an_ollama_error(self):
        assert issubclass(_ollama.OllamaUnavailable, _ollama.OllamaError)


class TestDefaultHost:
    def test_defaults_to_local_ollama_port(self, monkeypatch):
        monkeypatch.delenv("DEVLOG_OLLAMA_HOST", raising=False)
        assert _ollama.default_host() == "http://127.0.0.1:11434"

    def test_env_var_overrides_host(self, monkeypatch):
        monkeypatch.setenv("DEVLOG_OLLAMA_HOST", "http://10.0.0.5:11434")
        assert _ollama.default_host() == "http://10.0.0.5:11434"


class TestProxyBypass:
    def test_request_ignores_proxy_env_vars(self, monkeypatch, ollama_server):
        # A proxied environment must not break localhost Ollama calls:
        # the opener is built with ProxyHandler({}) (no proxies).
        proxy = "http://192.0.2.1:9999"  # TEST-NET-1: nothing there
        for var in ("http_proxy", "HTTP_PROXY", "ALL_PROXY", "all_proxy"):
            monkeypatch.setenv(var, proxy)
        assert (
            _ollama.generate("ping", model="llama3", host=ollama_server.url)
            == "You shipped the importers."
        )
