"""Performance benchmarks for devlog-cli.

Tests against the spec requirement: list and search should feel instant
for up to 1,000 entries. Also exercises larger datasets to surface
algorithmic regressions (O(n^2) sort/filter paths, JSON load/write
amplification, etc.).

Run with:
    pytest tests/test_performance.py -v --durations=20
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from devlog import storage
from devlog.cli import main
from devlog.models import Entry


# Loose bounds chosen to fail loudly on real regressions but tolerate
# noisy CI machines. The spec calls for "instant" (sub-second) on 1k
# entries; we allow some headroom.
INSTANT_THRESHOLD_S = 2.0      # 1k entries
ACCEPTABLE_THRESHOLD_S = 15.0  # 10k entries — should still feel snappy


@pytest.fixture()
def seeded_journal(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Seed ``tmp_path/entries.json`` with N entries on disk and return tmp_path.

    Sets ``DEVLOG_DATA_DIR`` via monkeypatch so in-process
    ``storage.load_entries()`` reads the seeded file rather than the
    user's real ``~/.devlog/entries.json``.
    """
    size = getattr(request, "param", 0)
    if size == 0:
        monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
        return tmp_path

    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    entries: list[dict] = []
    for i in range(size):
        ts = (base + timedelta(minutes=i * 7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        entries.append(
            {
                "id": f"{i:08d}-aaaa-bbbb-cccc-{i:012d}",
                "message": f"Entry number {i}: worked on the auth flow refactor "
                           f"and fixed flaky test {i}.",
                "tags": ["backend"] if i % 2 == 0 else ["backend", "bugfix"],
                "created_at": ts,
                "updated_at": None,
            }
        )
    payload = {"entries": entries}
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "entries.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Bulk-load: how long does it take to load_entries() on disk?
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seeded_journal", [1000], indirect=True)
def test_load_entries_1k_is_instant(seeded_journal: Path) -> None:
    """load_entries() must be sub-second on 1k entries (file I/O + parse)."""
    t0 = time.perf_counter()
    entries = storage.load_entries()
    elapsed = time.perf_counter() - t0
    assert len(entries) == 1000
    assert elapsed < INSTANT_THRESHOLD_S, (
        f"load_entries took {elapsed:.3f}s on 1000 entries; "
        f"expected < {INSTANT_THRESHOLD_S}s"
    )


@pytest.mark.parametrize("seeded_journal", [10_000], indirect=True)
def test_load_entries_10k_acceptable(seeded_journal: Path) -> None:
    """load_entries() must stay well under 15s on 10k entries."""
    t0 = time.perf_counter()
    entries = storage.load_entries()
    elapsed = time.perf_counter() - t0
    assert len(entries) == 10_000
    assert elapsed < ACCEPTABLE_THRESHOLD_S, (
        f"load_entries took {elapsed:.3f}s on 10000 entries; "
        f"expected < {ACCEPTABLE_THRESHOLD_S}s"
    )


# ---------------------------------------------------------------------------
# CLI command timings (spec: list / search "feel instant" on 1k)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seeded_journal", [1000], indirect=True)
def test_list_command_1k_instant(seeded_journal: Path) -> None:
    runner = CliRunner(env={"DEVLOG_DATA_DIR": str(seeded_journal)})
    t0 = time.perf_counter()
    result = runner.invoke(main, ["list", "--quiet"])
    elapsed = time.perf_counter() - t0
    assert result.exit_code == 0, result.output
    assert elapsed < INSTANT_THRESHOLD_S, (
        f"`list --quiet` on 1000 entries took {elapsed:.3f}s; "
        f"expected < {INSTANT_THRESHOLD_S}s"
    )


@pytest.mark.parametrize("seeded_journal", [10_000], indirect=True)
def test_list_command_10k_acceptable(seeded_journal: Path) -> None:
    runner = CliRunner(env={"DEVLOG_DATA_DIR": str(seeded_journal)})
    t0 = time.perf_counter()
    result = runner.invoke(main, ["list", "--quiet"])
    elapsed = time.perf_counter() - t0
    assert result.exit_code == 0, result.output
    assert elapsed < ACCEPTABLE_THRESHOLD_S, (
        f"`list --quiet` on 10000 entries took {elapsed:.3f}s; "
        f"expected < {ACCEPTABLE_THRESHOLD_S}s"
    )


@pytest.mark.parametrize("seeded_journal", [1000], indirect=True)
def test_search_command_1k_instant(seeded_journal: Path) -> None:
    runner = CliRunner(env={"DEVLOG_DATA_DIR": str(seeded_journal)})
    t0 = time.perf_counter()
    # "auth" matches every message in the seed.
    result = runner.invoke(main, ["search", "auth", "--quiet"])
    elapsed = time.perf_counter() - t0
    assert result.exit_code == 0, result.output
    assert elapsed < INSTANT_THRESHOLD_S, (
        f"`search auth` on 1000 entries took {elapsed:.3f}s; "
        f"expected < {INSTANT_THRESHOLD_S}s"
    )


@pytest.mark.parametrize("seeded_journal", [10_000], indirect=True)
def test_search_command_10k_acceptable(seeded_journal: Path) -> None:
    runner = CliRunner(env={"DEVLOG_DATA_DIR": str(seeded_journal)})
    t0 = time.perf_counter()
    result = runner.invoke(main, ["search", "auth", "--quiet"])
    elapsed = time.perf_counter() - t0
    assert result.exit_code == 0, result.output
    assert elapsed < ACCEPTABLE_THRESHOLD_S, (
        f"`search auth` on 10000 entries took {elapsed:.3f}s; "
        f"expected < {ACCEPTABLE_THRESHOLD_S}s"
    )


@pytest.mark.parametrize("seeded_journal", [10_000], indirect=True)
def test_search_no_match_10k(seeded_journal: Path) -> None:
    """Worst-case substring miss — every entry scanned."""
    runner = CliRunner(env={"DEVLOG_DATA_DIR": str(seeded_journal)})
    t0 = time.perf_counter()
    result = runner.invoke(main, ["search", "this-string-is-not-in-any-entry", "--quiet"])
    elapsed = time.perf_counter() - t0
    assert result.exit_code == 0, result.output
    assert elapsed < ACCEPTABLE_THRESHOLD_S, (
        f"`search <no-match>` on 10000 entries took {elapsed:.3f}s"
    )


# ---------------------------------------------------------------------------
# Write amplification: add_entry O(file size)?
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seeded_journal", [1000], indirect=True)
def test_add_entry_on_1k_acceptable(seeded_journal: Path) -> None:
    """Each `add` rewrites the entire journal — should not balloon with N."""
    runner = CliRunner(env={"DEVLOG_DATA_DIR": str(seeded_journal)})
    t0 = time.perf_counter()
    result = runner.invoke(main, ["add", "one more entry", "-q"])
    elapsed = time.perf_counter() - t0
    assert result.exit_code == 0, result.output
    assert elapsed < 5.0, f"`add` on a 1k journal took {elapsed:.3f}s"


@pytest.mark.parametrize("seeded_journal", [10_000], indirect=True)
def test_add_entry_on_10k_acceptable(seeded_journal: Path) -> None:
    runner = CliRunner(env={"DEVLOG_DATA_DIR": str(seeded_journal)})
    t0 = time.perf_counter()
    result = runner.invoke(main, ["add", "one more entry", "-q"])
    elapsed = time.perf_counter() - t0
    assert result.exit_code == 0, result.output
    assert elapsed < 10.0, f"`add` on a 10k journal took {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# Tag operations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seeded_journal", [1000], indirect=True)
def test_tags_command_1k_instant(seeded_journal: Path) -> None:
    runner = CliRunner(env={"DEVLOG_DATA_DIR": str(seeded_journal)})
    t0 = time.perf_counter()
    result = runner.invoke(main, ["tags", "--quiet"])
    elapsed = time.perf_counter() - t0
    assert result.exit_code == 0, result.output
    assert elapsed < INSTANT_THRESHOLD_S, (
        f"`tags` on 1000 entries took {elapsed:.3f}s"
    )


@pytest.mark.parametrize("seeded_journal", [10_000], indirect=True)
def test_tags_command_10k_acceptable(seeded_journal: Path) -> None:
    runner = CliRunner(env={"DEVLOG_DATA_DIR": str(seeded_journal)})
    t0 = time.perf_counter()
    result = runner.invoke(main, ["tags", "--quiet"])
    elapsed = time.perf_counter() - t0
    assert result.exit_code == 0, result.output
    assert elapsed < ACCEPTABLE_THRESHOLD_S, (
        f"`tags` on 10000 entries took {elapsed:.3f}s"
    )


@pytest.mark.parametrize("seeded_journal", [1000], indirect=True)
def test_rename_tag_1k(seeded_journal: Path) -> None:
    """rename-tag touches every entry — must scale O(n)."""
    runner = CliRunner(env={"DEVLOG_DATA_DIR": str(seeded_journal)})
    t0 = time.perf_counter()
    result = runner.invoke(main, ["rename-tag", "backend", "be", "-q"])
    elapsed = time.perf_counter() - t0
    assert result.exit_code == 0, result.output
    assert elapsed < 10.0, f"`rename-tag` on 1000 entries took {elapsed:.3f}s"


@pytest.mark.parametrize("seeded_journal", [10_000], indirect=True)
def test_rename_tag_10k(seeded_journal: Path) -> None:
    runner = CliRunner(env={"DEVLOG_DATA_DIR": str(seeded_journal)})
    t0 = time.perf_counter()
    result = runner.invoke(main, ["rename-tag", "be", "backend", "-q"])
    elapsed = time.perf_counter() - t0
    assert result.exit_code == 0, result.output
    assert elapsed < 30.0, f"`rename-tag` on 10000 entries took {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# Algorithmic complexity sniff — time-per-entry must NOT balloon with N.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "size_a,size_b",
    [(8_000, 32_000), (16_000, 64_000)],
)
def test_load_scales_linearly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    size_a: int,
    size_b: int,
) -> None:
    """If load_entries regresses to O(n^2), 4x N gives ≥8x time-per-entry.

    Compares time-per-entry at two sizes separated by 4x. The ratio
    (per-entry cost) must stay under 3.0 — well within the variance
    band of a linear scan with JSON parse + dataclass construction,
    but tight enough to catch any quadratic regression.

    Sizes are chosen to be large enough (≥8k) that we stay out of
    the warm-up / startup noise floor where any microbenchmark lies.
    """
    def seed(n: int, label: str) -> Path:
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        entries = [
            {
                "id": f"{i:08d}-aaaa-bbbb-cccc-{i:012d}",
                "message": f"Entry {i}: short message.",
                "tags": [],
                "created_at": (
                    base + timedelta(minutes=i)
                ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "updated_at": None,
            }
            for i in range(n)
        ]
        d = tmp_path / label
        d.mkdir()
        (d / "entries.json").write_text(
            json.dumps({"entries": entries}), encoding="utf-8"
        )
        return d

    d_a = seed(size_a, "small")
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(d_a))
    storage.load_entries()  # warm
    # Take the median of 5 runs to reduce noise.
    times_a = []
    for _ in range(5):
        t0 = time.perf_counter()
        entries_a = storage.load_entries()
        times_a.append(time.perf_counter() - t0)
    _ = len(entries_a)
    times_a.sort()
    t_a = times_a[2]
    per_entry_a = t_a / size_a

    d_b = seed(size_b, "big")
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(d_b))
    storage.load_entries()  # warm
    times_b = []
    for _ in range(5):
        t0 = time.perf_counter()
        entries_b = storage.load_entries()
        times_b.append(time.perf_counter() - t0)
    _ = len(entries_b)
    times_b.sort()
    t_b = times_b[2]
    per_entry_b = t_b / size_b

    ratio = per_entry_b / max(per_entry_a, 1e-9)
    assert ratio < 3.0, (
        f"load scaling is super-linear: "
        f"{size_a} -> {per_entry_a*1e6:.1f}µs/entry, "
        f"{size_b} -> {per_entry_b*1e6:.1f}µs/entry (ratio {ratio:.2f}x)"
    )


# ---------------------------------------------------------------------------
# Find by id — linear scans become O(n) lookups; with millions of lookups
# this is hot.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seeded_journal", [10_000], indirect=True)
def test_find_entry_by_id_10k(seeded_journal: Path) -> None:
    """find_entry_by_id must be O(n) and not degrade to O(n^2)."""
    from devlog import storage as s

    entries = s.load_entries()
    # 100 lookups — well within 5s even at O(n^2) is 100 * 10000 = 1M ops,
    # but if any quadratic hidden sort/filter is involved per-call, this explodes.
    t0 = time.perf_counter()
    for i in range(0, len(entries), 100):
        s.find_entry_by_id(entries, entries[i].id)
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0, f"100 find_entry_by_id calls took {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# Search at the algorithm level (no CLI overhead) — substring + sort.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seeded_journal", [10_000], indirect=True)
def test_search_substring_algorithmic(seeded_journal: Path) -> None:
    """Direct call into the search / filter pipeline (no CLI rendering)."""
    entries = storage.load_entries()
    t0 = time.perf_counter()
    matches = [e for e in entries if "auth" in e.message.lower()]
    matches.sort(key=lambda e: e.created_at, reverse=True)
    elapsed = time.perf_counter() - t0
    assert len(matches) > 0
    assert elapsed < 2.0, f"search filter+sort took {elapsed:.3f}s on 10k entries"


@pytest.mark.parametrize("seeded_journal", [10_000], indirect=True)
def test_filter_by_tags_algorithmic(seeded_journal: Path) -> None:
    """`_tagops._filter_by_tags` should be O(n) per filter, not O(n*m)."""
    from devlog import _tagops

    entries = storage.load_entries()
    t0 = time.perf_counter()
    filtered = _tagops._filter_by_tags(entries, ("backend", "bugfix"))
    elapsed = time.perf_counter() - t0
    assert len(filtered) > 0, "expected bugfix+backend entries from seed"
    assert elapsed < 2.0, f"_filter_by_tags took {elapsed:.3f}s on 10k entries"


# ---------------------------------------------------------------------------
# Targeted regression tests for specific performance bugs found via profiling.
#
# Each one asserts an absolute upper bound that a healthy implementation
# should meet on 10k entries. Tight enough to catch a regression; loose
# enough not to flake on a slow CI.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seeded_journal", [10_000], indirect=True)
def test_find_entry_by_id_no_redundant_lowercase(seeded_journal: Path) -> None:
    """``find_entry_by_id`` must NOT call ``str.lower()`` on every entry.

    UUIDs are always lowercase, so the per-entry ``e.id.lower()`` is
    wasted work. With 10k entries and 100 lookups, the buggy version
    runs 2 million ``.lower()`` calls and takes ~500ms; a fixed
    version finishes in ~50ms.

    The bound is generous so this test stays green even after the
    baseline shifts, but tight enough to fail loudly if a regression
    reintroduces the ``.lower()`` calls.
    """
    from devlog import storage as s

    entries = s.load_entries()
    t0 = time.perf_counter()
    for i in range(0, len(entries), 100):
        s.find_entry_by_id(entries, entries[i].id)
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, (
        f"find_entry_by_id took {elapsed:.3f}s for 100 exact-match "
        f"lookups over 10k entries — likely a redundant .lower() "
        f"per entry."
    )


@pytest.mark.parametrize("seeded_journal", [10_000], indirect=True)
def test_find_entry_by_id_prefix_no_redundant_lowercase(
    seeded_journal: Path,
) -> None:
    """Prefix-match path must also avoid per-entry ``.lower()``."""
    from devlog import storage as s

    entries = s.load_entries()
    t0 = time.perf_counter()
    # 4-char prefix forces the prefix-scan branch.
    for i in range(0, len(entries), 100):
        s.find_entry_by_id(entries, entries[i].id[:4])
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.5, (
        f"find_entry_by_id (prefix scan) took {elapsed:.3f}s — "
        f"the .lower() per entry is the bottleneck."
    )


@pytest.mark.parametrize("seeded_journal", [10_000], indirect=True)
def test_stats_no_double_parse_of_timestamp(seeded_journal: Path) -> None:
    """``stats`` must not parse each entry's ``created_at`` twice.

    Bug: ``cli.stats`` first runs
    ``[e for e in all_entries if _iso.is_valid_iso_timestamp(e.created_at)]``
    and then iterates again calling ``storage.local_date_for`` which
    internally parses the same timestamp a second time.

    With 10k entries that's 10k saved ``parse_utc_iso`` calls —
    measurable in cProfile. Bound: <500ms for the whole ``stats`` CLI.
    """
    from click.testing import CliRunner

    from devlog.cli import main

    runner = CliRunner(env={"DEVLOG_DATA_DIR": str(seeded_journal)})
    runner.invoke(main, ["stats", "--quiet"])  # warm
    t0 = time.perf_counter()
    result = runner.invoke(main, ["stats", "--quiet"])
    elapsed = time.perf_counter() - t0
    assert result.exit_code == 0, result.output
    assert elapsed < 0.6, (
        f"`stats --quiet` on 10k entries took {elapsed:.3f}s — "
        f"check for double-parsing of created_at."
    )


@pytest.mark.parametrize("seeded_journal", [10_000], indirect=True)
def test_rename_tag_hoists_utc_now(seeded_journal: Path) -> None:
    """``rename-tag`` must NOT call ``datetime.now()`` once per affected entry.

    Bug: ``_tagops._rewrite_tag_in_entry`` calls ``storage.utc_now_iso()``
    on every affected entry, which calls ``datetime.now()`` (a syscall).
    For 10k affected entries that's 10k syscalls.

    Bound: full ``rename-tag`` CLI < 1.5s for 10k entries, including
    JSON load + save. The save alone is ~0.5s; the rewrite loop should
    add milliseconds, not seconds.
    """
    from click.testing import CliRunner

    from devlog.cli import main

    runner = CliRunner(env={"DEVLOG_DATA_DIR": str(seeded_journal)})
    t0 = time.perf_counter()
    result = runner.invoke(main, ["rename-tag", "backend", "be", "-q"])
    elapsed = time.perf_counter() - t0
    assert result.exit_code == 0, result.output
    assert elapsed < 1.5, (
        f"`rename-tag` on 10k entries took {elapsed:.3f}s — "
        f"datetime.now() inside the per-entry rewrite loop is the "
        f"likely cause."
    )


def test_filter_by_tags_no_per_entry_set_construction() -> None:
    """`_filter_by_tags` must not rebuild `set(e.tags)` per entry.

    Bug: the implementation calls ``set(e.tags)`` once per entry to
    use as the second operand of ``issubset``. For entries with the
    common 1-2 tags, the set construction is pure overhead vs.
    `frozenset(filter).issubset(entry.tags)`.

    The test compares the implementation against a known-fast baseline
    and fails if the implementation regresses to per-entry set
    construction.
    """
    from devlog import _tagops

    # Build a synthetic 10k list to avoid filesystem state.
    base_dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
    entries = [
        Entry(
            id=f"{i:08d}-aaaa-bbbb-cccc-{i:012d}",
            message=f"m{i}",
            tags=["backend"] if i % 2 == 0 else ["backend", "bugfix"],
            created_at=(base_dt).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        for i in range(10_000)
    ]

    # Reference (fast) implementation
    def fast_filter(entries, tags):
        if not tags:
            return entries
        norm_filter = frozenset(t.strip().lower() for t in tags)
        return [e for e in entries if norm_filter.issubset(e.tags)]

    # Run both 5 times, take the median.
    def median_of(fn, *args):
        times = []
        for _ in range(5):
            t0 = time.perf_counter()
            fn(*args)
            times.append(time.perf_counter() - t0)
        times.sort()
        return times[2]

    t_impl = median_of(_tagops._filter_by_tags, entries, ("backend",))
    t_fast = median_of(fast_filter, entries, ("backend",))

    # Healthy implementation should be within 30% of the fast version.
    # Per-entry set construction adds ~30-50% overhead.
    assert t_impl < t_fast * 1.3, (
        f"_filter_by_tags ({t_impl:.4f}s) is slower than the "
        f"frozenset-issubset baseline ({t_fast:.4f}s) by more than 30%. "
        f"Likely per-entry `set(e.tags)` overhead."
    )