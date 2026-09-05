"""Tag validation and manipulation utilities.

Centralises all tag-related logic that was previously in ``cli.py``.
"""

from __future__ import annotations

import re
from typing import Tuple

import click

from devlog import storage
from devlog.models import Entry

# Tag validation constants (also used by storage module)
TAG_RE = re.compile(r"^[a-z0-9\-]+$")
MAX_TAG_LENGTH = 32
MAX_TAGS = 10


def _validate_tags(raw_tags: tuple[str, ...]) -> list[str]:
    """Validate and normalise a sequence of raw tag strings.

    Validation rules (in order):
        1. Normalise: strip whitespace, lower-case.
        2. Character set: only a-z, 0-9, hyphen.
        3. Maximum tag length: 32 characters.
        4. Maximum distinct tags per entry: 10.

    Args:
        raw_tags: tuple of raw tag strings as provided by the user.

    Returns:
        list[str]: deduplicated, normalised tags.

    Raises:
        click.UsageError: on any validation failure.
    """
    seen: list[str] = []
    seen_set: set[str] = set()

    for original in raw_tags:
        norm = original.strip().lower()

        if not TAG_RE.fullmatch(norm):
            raise click.UsageError(
                f'Tag "{original}" contains invalid characters. '
                "Tags are normalised to lowercase before storage; the "
                "remaining characters must be a-z, 0-9, or hyphen."
            )

        if len(norm) > MAX_TAG_LENGTH:
            raise click.UsageError(
                f'Tag "{original}" exceeds maximum length of '
                f"{MAX_TAG_LENGTH} characters."
            )

        if norm not in seen_set:
            seen_set.add(norm)
            seen.append(norm)

    if len(seen) > MAX_TAGS:
        raise click.UsageError(
            f"Maximum {MAX_TAGS} tags per entry. You provided {len(seen)}."
        )

    return seen


def _filter_by_tags(entries: list[Entry], tags: tuple[str, ...]) -> list[Entry]:
    """Filter entries so that all provided tags are present (AND logic).

    Args:
        entries: full list of Entry objects.
        tags:    raw tag strings to filter by (normalised internally).

    Returns:
        list[Entry]: entries that carry every requested tag.

    Note:
        The filter set is built *once* as a :class:`frozenset` (cheap
        outside the comprehension) and we then call
        ``frozenset.issubset(entry.tags)``. The naive ``set(e.tags)``
        approach rebuilt the per-entry set on every iteration — small
        constant overhead, but measurable on 10k+ journals.
    """
    if not tags:
        return entries
    norm_filter = frozenset(t.strip().lower() for t in tags)
    return [e for e in entries if norm_filter.issubset(e.tags)]


def _validate_new_tag(raw: str) -> str:
    """Validate a user-supplied NEW tag and return its normalised form.

    Runs the same rules as :func:`_validate_tags` (a single-tag tuple)
    but with the more specific error messages the ``rename-tag`` /
    ``merge-tag`` commands have always emitted. Used by both commands so
    a bad NEW value fails fast with a clear error, before any storage
    I/O happens.

    Args:
        raw: the raw tag string as the user typed it.

    Returns:
        The normalised (stripped, lowercased) tag.

    Raises:
        click.UsageError: on empty / invalid-chars / over-length input.
    """
    if not raw or not raw.strip():
        raise click.UsageError("NEW tag cannot be empty.")
    # Pre-validate the *raw* (un-normalised) string so an invalid tag
    # like ``INFRA`` (uppercase) is rejected with a clear error rather
    # than silently normalised to ``infra`` and then triggering the
    # "OLD and NEW are the same" no-op path when the user actually
    # meant a different value. ``_validate_tags`` lowercases before
    # matching, so the regex check there would never see the
    # uppercase letters and would happily return ``infra``.
    if not TAG_RE.fullmatch(raw.strip()):
        raise click.UsageError(
            f'Tag "{raw}" contains invalid characters. '
            "Tags are normalised to lowercase before storage; the "
            "remaining characters must be a-z, 0-9, or hyphen."
        )
    if len(raw.strip()) > MAX_TAG_LENGTH:
        raise click.UsageError(
            f'Tag "{raw}" exceeds maximum length of {MAX_TAG_LENGTH} characters.'
        )
    norm = _validate_tags((raw,))
    return norm[0]


def _rewrite_tag_in_entry(
    entry: Entry, old: str, new: str, *, now: str | None = None
) -> None:
    """Replace every occurrence of ``old`` with ``new`` in ``entry.tags``.

    Handles dedup: if ``entry.tags`` already contains ``new``, a
    duplicate is never inserted (i.e. ``rename`` is idempotent and
    ``merge`` does not double-tag entries that already carry ``new``).
    Stamps ``entry.updated_at`` so the change is visible in ``tags``
    and ``stats``.

    Args:
        entry: the entry to mutate in place.
        old:   the tag to retire.
        new:   the tag to substitute (or add).
        now:   pre-computed UTC ``updated_at`` timestamp. Bulk callers
               (``rename-tag``, ``merge-tag``, ``tag --delete``) must
               pass the same value for every entry in the run so all
               affected rows share one ``datetime.now()`` syscall
               instead of one syscall per entry.
    """
    new_tags: list[str] = []
    for t in entry.tags:
        if t == old or t == new:
            if new not in new_tags:
                new_tags.append(new)
        else:
            new_tags.append(t)
    entry.tags = new_tags
    entry.updated_at = now if now is not None else storage.utc_now_iso()


def _is_valid_tag(tag: str) -> bool:
    """Check if a tag string is valid (matches TAG_RE and length)."""
    return bool(TAG_RE.fullmatch(tag)) and len(tag) <= MAX_TAG_LENGTH