"""Minimal Ollama HTTP client for the ``devlog[ai]`` commands.

Stdlib-only (``urllib``) so the base install never grows a dependency:
the ``devlog[ai]`` extra ships the *commands*; talking to the local
Ollama server needs nothing beyond the Python runtime. Errors map to
two types: :class:`OllamaUnavailable` (connection refused / timeout —
"is Ollama running?") and :class:`OllamaError` (HTTP error status,
malformed response — "the server answered but with something wrong").

The host defaults to ``http://127.0.0.1:11434`` and can be overridden
with ``DEVLOG_OLLAMA_HOST`` for remote/Odysseus-style setups.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_TIMEOUT_S = 120.0


class OllamaError(Exception):
    """The Ollama server answered with an error (or a malformed payload)."""


class OllamaUnavailable(OllamaError):
    """The Ollama server could not be reached at all."""


def default_host() -> str:
    """Return the Ollama host: ``DEVLOG_OLLAMA_HOST`` or the localhost default."""
    return os.environ.get("DEVLOG_OLLAMA_HOST", DEFAULT_HOST)


def _request(host: str, path: str, payload: dict, timeout: float) -> dict:
    """POST *payload* to ``{host}{path}`` and return the parsed JSON body."""
    url = host.rstrip("/") + path
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # Deliberately proxy-free: Ollama is a local/LAN server, so ambient
    # http_proxy/ALL_PROXY vars must never intercept these calls (a
    # corporate proxy would otherwise hang or 403 every AI command).
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        raise OllamaError(f"Ollama returned HTTP {exc.code} for {path}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise OllamaUnavailable(
            f"Cannot reach Ollama at {host} — is the server running? ({exc})"
        ) from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise OllamaError(f"Ollama returned malformed JSON for {path}") from exc


def generate(prompt: str, *, model: str, host: str | None = None, timeout: float = DEFAULT_TIMEOUT_S) -> str:
    """POST to ``/api/generate`` and return the assistant's reply text."""
    response = _request(
        host or default_host(),
        "/api/generate",
        {"model": model, "prompt": prompt, "stream": False},
        timeout,
    )
    if "response" not in response:
        raise OllamaError(f"Ollama response missing 'response' key: {response!r:.200}")
    return str(response["response"])


def embed(texts: list[str], *, model: str, host: str | None = None, timeout: float = DEFAULT_TIMEOUT_S) -> list[list[float]]:
    """POST to ``/api/embed`` and return one vector per input text."""
    response = _request(
        host or default_host(),
        "/api/embed",
        {"model": model, "input": texts},
        timeout,
    )
    if "embeddings" not in response:
        raise OllamaError(f"Ollama response missing 'embeddings' key: {response!r:.200}")
    return [list(map(float, vec)) for vec in response["embeddings"]]
