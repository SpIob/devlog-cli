"""Embedding index for ``devlog ask`` (local semantic search).

Persists ``{entry_id: vector}`` under ``<data_dir>/embeddings.json``
alongside the journal, tagged with the embedding model that produced
it. Incremental: only entries missing from a valid same-model index
are embedded; stale ids are pruned. Built on the storage-layer API
(``storage.load_entries`` shape) so both backends work unchanged —
the "migration point" the roadmap called out turns out not to need
schema changes at all, because the index lives in its own file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from devlog import _ollama
from devlog.models import Entry

INDEX_FILE_NAME = "embeddings.json"
DEFAULT_EMBED_MODEL = "nomic-embed-text"


@dataclass
class EmbeddingIndex:
    """Embedding vectors keyed by entry id, tagged with the model."""

    model: str
    entries: dict[str, list[float]] = field(default_factory=dict)


def _index_path(data_dir: Path) -> Path:
    return data_dir / INDEX_FILE_NAME


def save_index(data_dir: Path, index: EmbeddingIndex) -> None:
    """Persist *index* to ``<data_dir>/embeddings.json`` (atomic replace)."""
    import os

    data_dir.mkdir(parents=True, exist_ok=True)
    payload = {"model": index.model, "entries": index.entries}
    tmp_path = _index_path(data_dir).with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    os.replace(tmp_path, _index_path(data_dir))


def load_index(data_dir: Path) -> EmbeddingIndex | None:
    """Load the persisted index, or ``None`` when missing/corrupt/malformed."""
    path = _index_path(data_dir)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    model = payload.get("model")
    entries = payload.get("entries")
    if not isinstance(model, str) or not isinstance(entries, dict):
        return None
    return EmbeddingIndex(model=model, entries=entries)


def _embed_model() -> str:
    """Return the embedding model: ``DEVLOG_EMBED_MODEL`` or the default."""
    import os

    return os.environ.get("DEVLOG_EMBED_MODEL", DEFAULT_EMBED_MODEL)


def ensure_index(
    entries: list[Entry], data_dir: Path, *, host: str | None = None
) -> EmbeddingIndex:
    """Return a complete embedding index for *entries*, building as needed.

    Reuses a persisted same-model index; embeds only missing entries
    (in one Ollama call), prunes stale ids, and re-embeds everything
    when the model changed. Raises :class:`_ollama.OllamaError` when
    embeddings are needed but the server cannot be reached — callers
    decide how to degrade.
    """
    model = _embed_model()
    existing = load_index(data_dir)
    if existing is not None and existing.model != model:
        existing = None  # model changed: full rebuild

    current_ids = {e.id for e in entries}
    index = EmbeddingIndex(model=model, entries=dict(existing.entries) if existing else {})

    missing = [e for e in entries if e.id not in index.entries]
    if missing:
        vectors = _ollama.embed(
            [e.message for e in missing], model=model, host=host
        )
        for e, vec in zip(missing, vectors):
            index.entries[e.id] = vec

    stale = set(index.entries) - current_ids
    for entry_id in stale:
        del index.entries[entry_id]

    if missing or stale or existing is None:
        save_index(data_dir, index)
    return index


def search_entries(
    query: str,
    entries: list[Entry],
    index: EmbeddingIndex,
    *,
    top: int | None = None,
    host: str | None = None,
) -> list[tuple[str, float]]:
    """Rank *entries* against *query* by embedding cosine similarity.

    Requires *index* to already cover the entries (call
    :func:`ensure_index` first). One Ollama call embeds the query.
    """
    from devlog import _aiops

    query_vec = _ollama.embed([query], model=index.model, host=host)[0]
    vectors = {e.id: index.entries[e.id] for e in entries if e.id in index.entries}
    return _aiops.rank_by_query(query_vec, vectors, top=top)
