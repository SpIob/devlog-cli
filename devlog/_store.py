"""Atomic JSON store for small data files (config, views, templates, etc.).

Provides a simple, thread-safe way to persist small JSON objects in the
data directory. All writes are atomic (temp file + os.replace) and use
the same cross-platform locking as the main storage layer.

Locking contract:
    * A single :meth:`load` / :meth:`save` call is atomic per call
      (readers never see a torn file), but load-then-save is *not* a
      transaction — another process can write between the two calls.
    * :meth:`update` and the :meth:`locked` context manager hold the
      canonical cross-process write lock
      (:func:`storage.storage_write_lock`) on a stable ``<name>.lock``
      sidecar (never replaced, so the lock survives ``os.replace``)
      across the whole read-modify-write. Nesting is re-entrant within
      a process; cross-process writers wait up to
      ``DEVLOG_WRITE_LOCK_TIMEOUT`` and then raise
      :class:`storage.StorageBusyError`.

The sidecar is excluded from ``git sync`` commits (the ``*.lock``
pathspec rule in ``_gitops``) like the main journal's lock.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from devlog.storage._base import (
    get_data_dir,
    storage_write_lock,
)


class AtomicStore:
    """A small JSON file with atomic writes and optional locking.

    Use for config, saved views, templates, and similar metadata that
    doesn't need the full Entry model / storage facade.

    Example:
        store = AtomicStore("my-config.json", default={})
        store.update(lambda data: {**data, "key": "value"})

    For multi-step read-modify-write sequences that must not interleave
    with other processes, wrap them in ``with store.locked():``.
    """

    def __init__(
        self,
        filename: str,
        default: dict[str, Any] | None = None,
        lock: bool = True,
    ) -> None:
        """Initialize the store.

        Args:
            filename: File name (no path) inside the data directory.
            default: Default content if file doesn't exist.
            lock: Whether :meth:`update`/:meth:`locked` serialize
                read-modify-write across processes (default True).
        """
        self._path = get_data_dir() / filename
        self._default = default or {}
        self._lock = lock

    @property
    def path(self) -> Path:
        """The on-disk JSON file this store manages."""
        return self._path

    def locked(self):
        """Context manager holding the cross-process read-modify-write lock.

        Delegates to :func:`storage.storage_write_lock` — re-entrant
        within a process (nested ``locked()``/``update()`` on the same
        path skip the second flock instead of deadlocking on it);
        cross-process writers wait up to ``DEVLOG_WRITE_LOCK_TIMEOUT``
        and then raise :class:`storage.StorageBusyError`. A no-op
        (``nullcontext``) when the store was built with ``lock=False``.
        """
        if not self._lock:
            return nullcontext()
        return storage_write_lock(self._path)

    def load(self) -> dict[str, Any]:
        """Load and return the stored JSON object.

        Returns the default dict if the file doesn't exist or is invalid.
        """
        if not self._path.exists():
            return dict(self._default)

        try:
            with open(self._path, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                return dict(self._default)
            return data
        except (json.JSONDecodeError, OSError, PermissionError):
            return dict(self._default)

    def save(self, data: dict[str, Any]) -> None:
        """Atomically write *data* to the store file.

        The write itself is a temp-file + ``os.replace`` (readers see
        the old or the new file, never a torn one). Callers that base
        *data* on a previous :meth:`load` must hold :meth:`locked` (or
        use :meth:`update`) or they can lose a concurrent update.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)

        # Write to temp file first
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self._path.parent,
            delete=False,
            prefix=f".{self._path.name}.",
            suffix=".tmp",
        ) as tf:
            json.dump(data, tf, indent=2, ensure_ascii=False)
            temp_name = tf.name

        try:
            os.replace(temp_name, self._path)
        finally:
            # Cleanup temp on any failure
            try:
                os.unlink(temp_name)
            except OSError:
                pass

    def update(self, fn: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        """Atomically load, apply *fn*, and save — under the read-modify-write lock.

        Returns the new data. This is the only way to merge into the
        stored dict without racing a concurrent writer.
        """
        with self.locked():
            data = self.load()
            new_data = fn(data)
            self.save(new_data)
        return new_data

    def clear(self) -> None:
        """Delete the store file."""
        try:
            self._path.unlink()
        except (OSError, PermissionError):
            pass
