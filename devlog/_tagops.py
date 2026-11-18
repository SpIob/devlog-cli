"""Tag validation and manipulation utilities.

Centralises all tag-related logic that was previously in ``cli.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import click

from devlog._time import utc_now_iso
from devlog.models import Entry

# Tag validation constants (also used by storage module)
TAG_RE = re.compile(r"^[a-z0-9\-]+$")
MAX_TAG_LENGTH = 32
MAX_TAGS = 10


def _validate_tags(raw_tags: tuple[str, ...]) -> list[str]:
    """Validate and normalise raw tag strings for STORAGE.

    This is the write-path validator (``add`` / ``edit`` / ``tag``).
    Validation rules (in order):

        1. Normalise: strip whitespace, lower-case.
        2. Split comma groups: ``-t a,b`` contributes two distinct tags.
           A stored tag is a single name — a comma-joined string would
           violate the documented ``[a-z0-9-]`` charset and be
           unmatchable by the query syntax (where ``a,b`` means OR).
        3. Reject ``!``: it is NOT-query syntax and only meaningful in
           filters (:func:`parse_tag_query`); it is not a valid stored
           tag character.
        4. Character set per tag: ``a-z0-9-`` only.
        5. Maximum tag length: 32 characters per tag.
        6. Maximum distinct tags per entry: 10, counted AFTER splitting.

    Compound-query syntax (``-t a,b`` OR, ``-t !x`` NOT) is parsed by
    :func:`parse_tag_query` on the *filter* side only.

    Args:
        raw_tags: tuple of raw tag strings as provided by the user.

    Returns:
        list[str]: deduplicated, normalised, split tags.

    Raises:
        click.UsageError: on any validation failure.
    """
    seen: list[str] = []
    seen_set: set[str] = set()

    for original in raw_tags:
        norm = original.strip().lower()

        if "!" in norm:
            raise click.UsageError(
                f'Tag "{original}" contains "!" — that is query syntax '
                "(NOT) and is not valid in a stored tag. "
                "Allowed characters: a-z, 0-9, hyphen."
            )

        parts = [p.strip() for p in norm.split(",") if p.strip()]
        if not parts:
            raise click.UsageError(f'Tag "{original}" is empty.')

        for part in parts:
            if not TAG_RE.fullmatch(part):
                raise click.UsageError(
                    f'Tag "{part}" contains invalid characters. '
                    "Tags are normalised to lowercase before storage; "
                    "the remaining characters must be a-z, 0-9, or hyphen."
                )
            if len(part) > MAX_TAG_LENGTH:
                raise click.UsageError(
                    f'Tag "{part}" exceeds maximum length of '
                    f"{MAX_TAG_LENGTH} characters per tag."
                )
            if part not in seen_set:
                seen_set.add(part)
                seen.append(part)

    if len(seen) > MAX_TAGS:
        raise click.UsageError(
            f"Maximum {MAX_TAGS} tags per entry. You provided {len(seen)}."
        )

    return seen


# ---------------------------------------------------------------------------
# Compound tag queries
# ---------------------------------------------------------------------------


@dataclass
class TagQuery:
    """Parsed tag query with AND/OR/NOT logic.

    Attributes:
        all_of:   All of these tags must be present (AND).
        any_of:   At least one of these tags must be present (OR).
        none_of:  None of these tags may be present (NOT).
    """

    all_of: frozenset[str]
    any_of: frozenset[str]
    none_of: frozenset[str]

    @staticmethod
    def empty() -> TagQuery:
        return TagQuery(frozenset(), frozenset(), frozenset())

    def matches(self, entry_tags: set[str]) -> bool:
        """Check if entry_tags satisfy the query."""
        if self.all_of and not self.all_of.issubset(entry_tags):
            return False
        if self.any_of and not (self.any_of & entry_tags):
            return False
        if self.none_of and (self.none_of & entry_tags):
            return False
        return True


def parse_tag_query(raw_tags: tuple[str, ...]) -> TagQuery:
    """Parse raw tag strings into a structured TagQuery.

    Syntax:
        - Plain tag (e.g., "bug") → AND (must have)
        - Comma-separated (e.g., "bug,feature") → OR (any of)
        - Prefix "!" (e.g., "!wip") → NOT (must not have)
        - Can mix: "-t bug -t feature,refactor -t !wip"

    Args:
        raw_tags: Tuple of raw tag strings from CLI (each -t flag value).

    Returns:
        TagQuery with all_of, any_of, none_of frozensets.
    """
    all_of: set[str] = set()
    any_of: set[str] = set()
    none_of: set[str] = set()

    for raw in raw_tags:
        if not raw or not raw.strip():
            continue
        # Split on comma for OR groups
        parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
        for part in parts:
            if part.startswith("!"):
                tag = part[1:]
                if not _is_valid_tag(tag):
                    raise click.UsageError(
                        f"Invalid tag in NOT clause: {part}. "
                        f"Tags must match {TAG_RE.pattern} (max {MAX_TAG_LENGTH} chars)."
                    )
                none_of.add(tag)
            else:
                if not _is_valid_tag(part):
                    raise click.UsageError(
                        f"Tag \"{part}\" contains invalid characters. "
                        f"Tags are normalised to lowercase; allowed: a-z, 0-9, hyphen."
                    )
                if len(parts) > 1:
                    any_of.add(part)
                else:
                    all_of.add(part)

    return TagQuery(
        all_of=frozenset(all_of),
        any_of=frozenset(any_of),
        none_of=frozenset(none_of),
    )


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


def _filter_by_tag_query(entries: list[Entry], query: TagQuery) -> list[Entry]:
    """Filter entries using a parsed TagQuery (AND/OR/NOT)."""
    if query.all_of == frozenset() and query.any_of == frozenset() and query.none_of == frozenset():
        return entries
    out = []
    for e in entries:
        entry_tags = set(e.tags)
        if query.matches(entry_tags):
            out.append(e)
    return out


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
    entry.updated_at = now if now is not None else utc_now_iso()


def _is_valid_tag(tag: str) -> bool:
    """Check if a tag string is valid (matches TAG_RE and length)."""
    return bool(TAG_RE.fullmatch(tag)) and len(tag) <= MAX_TAG_LENGTH