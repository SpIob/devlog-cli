from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import List, Optional

from devlog import _iso


@dataclass
class Entry:
    id: str
    message: str
    created_at: str  # ISO 8601 UTC string
    tags: List[str] = field(default_factory=list)
    updated_at: Optional[str] = None  # ISO 8601 UTC string, or None

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
