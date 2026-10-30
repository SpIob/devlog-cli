from __future__ import annotations

import dataclasses
import datetime as _dt
from dataclasses import dataclass, field
from operator import attrgetter

from devlog import _iso

# Pre-built sort key for ``Entry.created_at`` — single source of truth.
# A C-level ``attrgetter`` outperforms a fresh ``lambda e: e.created_at``
# per call by ~20% on a 10k-entry sort.
BY_CREATED_AT = attrgetter("created_at")


@dataclass
class Entry:
    id: str
    message: str
    created_at: str  # ISO 8601 UTC string
    tags: list[str] = field(default_factory=list)
    updated_at: str | None = None  # ISO 8601 UTC string, or None

    @classmethod
    def from_dict(cls, item: dict) -> Entry:
        """Build an :class:`Entry` from a stored JSON object, tolerantly.

        This is the deserialization counterpart of ``dataclasses.asdict``:
        unknown keys (fields added by a *newer* devlog version) are
        ignored instead of raising, so a future schema can never make
        every command crash on ``Entry(**item)``. The three identity
        fields — ``id``, ``message``, ``created_at`` — remain required;
        a missing one raises ``ValueError`` naming the field.

        Args:
            item: a dict as found in the ``entries`` array of
                ``entries.json``.

        Returns:
            Entry: the reconstructed entry.

        Raises:
            TypeError: if *item* is not a dict.
            ValueError: if a required field is missing (the message
                names the first missing field).
        """
        if not isinstance(item, dict):
            raise TypeError(
                f"Entry payload must be a dict, got {type(item).__name__}"
            )
        missing = [k for k in ("id", "message", "created_at") if k not in item]
        if missing:
            raise ValueError(f"Entry is missing required field(s): {', '.join(missing)}")
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in item.items() if k in known})

    @property
    def short_id(self) -> str:
        """The 8-char short id used for human-facing displays.

        Mirrors :data:`devlog.ui.ID_DISPLAY_LEN` so the value is the same
        one the UI renders.
        """
        return self.id[:8]

    @property
    def created_dt(self) -> _dt.datetime:
        """The ``created_at`` string parsed back to a UTC ``datetime``.

        Returns the epoch (1970-01-01 UTC) when the timestamp is
        unparseable, so callers that only need ordering/comparison
        never crash on a corrupt store.
        """
        return _parse_iso(self.created_at)

    @property
    def updated_dt(self) -> _dt.datetime | None:
        """The ``updated_at`` string parsed to a UTC ``datetime``, or ``None``."""
        if not self.updated_at:
            return None
        return _parse_iso(self.updated_at)

    @property
    def fingerprint(self) -> tuple[str, str]:
        """``(created_at, message)`` — the idempotency key used by ``import``."""
        return (self.created_at, self.message)


def _parse_iso(iso: str) -> _dt.datetime:
    """Parse a stored ``YYYY-MM-DDTHH:MM:SSZ`` string to a UTC datetime.

    Returns the epoch on any parse failure so callers can still sort or
    bucket without raising. This is the read-side counterpart of
    :func:`devlog._iso.is_valid_iso_timestamp`: the validator
    reports bad rows, the reader degrades gracefully.
    """
    dt = _iso.try_parse_utc_iso(iso)
    if dt is None:
        return _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)
    return dt
