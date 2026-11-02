"""Pure text/similarity core for the ``devlog[ai]`` features.

Everything here is deterministic and dependency-free so it runs on any
CI machine: the extractive weekly digest, cosine similarity over
embedding vectors, and the ``ask`` answer formatting. The Ollama HTTP
plumbing lives in :mod:`devlog._ollama`; the CLI in
:mod:`devlog._ai_cli`.
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import date, datetime

from devlog.models import Entry

# ---------------------------------------------------------------------------
# Extractive weekly digest
# ---------------------------------------------------------------------------


def _local_dt(entry: Entry, tz) -> datetime:
    """Parse ``entry.created_at`` to a UTC datetime, then shift to *tz*.

    Falls back to the epoch (1970-01-01) for unparseable timestamps —
    the read-side convention shared with ``Entry.created_dt`` — so a
    corrupt row can never crash a digest.
    """
    dt = entry.created_dt
    if tz is not None:
        dt = dt.astimezone(tz)
    return dt


def summarize_week_text(
    entries: list[Entry],
    *,
    start_date: date,
    end_date: date,
    tz=None,
) -> str:
    """Build an extractive weekly-digest text from *entries*.

    Entries are bucketed by local day (in *tz*, or UTC when ``None``),
    sorted chronologically, and rendered under ``YYYY-MM-DD`` headers.
    Entries whose local date falls outside ``[start_date, end_date]``
    are excluded, so a mis-filtered caller cannot corrupt the digest.
    Tags are listed per entry and totalled in a footer. This text is
    both the human-facing extractive digest and the prompt body handed
    to the local LLM when one is configured.
    """
    header = f"Weekly digest: {start_date.isoformat()} to {end_date.isoformat()}"
    in_window: list[Entry] = []
    for entry in entries:
        local_d = _local_dt(entry, tz).date()
        if start_date <= local_d <= end_date:
            in_window.append(entry)
    entries = in_window
    header += f" · {len(entries)} " + ("entry" if len(entries) == 1 else "entries")
    if not entries:
        return f"{header}\n\nNo entries in this period."

    by_day: dict[date, list[tuple[datetime, Entry]]] = {}
    for entry in entries:
        local = _local_dt(entry, tz)
        by_day.setdefault(local.date(), []).append((local, entry))

    tag_counts: Counter = Counter()
    for entry in entries:
        for tag in sorted(set(entry.tags)):
            tag_counts[tag] += 1

    lines = [header, ""]
    for day in sorted(by_day):
        lines.append(f"## {day.isoformat()}")
        for local, entry in sorted(by_day[day], key=lambda pair: pair[0]):
            stamp = local.strftime("%H:%M")
            tags = " ".join(f"#{t}" for t in sorted(set(entry.tags)))
            lines.append(f"- {stamp} {entry.message}" + (f" ({tags})" if tags else ""))
        lines.append("")

    if tag_counts:
        totals = " ".join(f"{tag}×{count}" for tag, count in tag_counts.most_common())
        lines.append(f"Tags: {totals}")
    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# Cosine similarity + ranking
# ---------------------------------------------------------------------------


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors; 0.0 for zero vectors."""
    if len(a) != len(b):
        raise ValueError("vectors must have the same length")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def rank_by_query(
    query_vec: list[float],
    vectors: dict[str, list[float]],
    top: int | None = None,
) -> list[tuple[str, float]]:
    """Rank ``{id: vector}`` against *query_vec*, best first.

    Returns ``(id, score)`` pairs sorted by descending cosine similarity.
    *top* truncates the result when given.
    """
    scored = [
        (item_id, cosine_similarity(query_vec, vec)) for item_id, vec in vectors.items()
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    if top is not None:
        scored = scored[:top]
    return scored


# ---------------------------------------------------------------------------
# Ask-answer formatting
# ---------------------------------------------------------------------------


def format_ask_answer(
    query: str,
    ranked: list[tuple[str, float]],
    entries: list[Entry],
) -> str:
    """Render the ``ask`` answer: query echo + best matching entries.

    Ranked ids with no matching entry (e.g. a stale embedding index) are
    skipped; when nothing resolves the text says so explicitly.
    """
    header = f"Q: {query}"
    by_id = {e.id: e for e in entries}
    resolved = [(entry_id, score) for entry_id, score in ranked if entry_id in by_id]
    if not resolved:
        return f"{header}\n\nNo matching entries found."
    lines = [header, ""]
    for entry_id, score in resolved[:5]:
        entry = by_id[entry_id]
        lines.append(f"- {entry.created_at} · {entry.message} (score {score:.2f})")
    return "\n".join(lines)
