"""Atomic JSON store for small data files (config, views, templates, etc.).

Provides a simple, thread-safe way to persist small JSON objects in the
data directory. All writes are atomic (temp file + os.replace) and use
the same cross-platform locking as the main storage layer.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from devlog.storage._base import (
    _lock_file,
    _unlock_file,
    get_data_dir,
)

if TYPE_CHECKING:
    pass


class AtomicStore:
    """A small JSON file with atomic writes and optional locking.

    Use for config, saved views, templates, and similar metadata that
    doesn't need the full Entry model / storage facade.

    Example:
        store = AtomicStore("my-config.json", default={})
        data = store.load()
        data["key"] = "value"
        store.save(data)
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
            lock: Whether to use cross-process file locking (default True).
        """
        self._path = get_data_dir() / filename
        self._default = default or {}
        self._lock = lock

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
        """Atomically write *data* to the store file."""
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
            if self._lock:
                # Lock the target file for the replace
                with open(self._path, "a") as lf:
                    _lock_file(lf, exclusive=True)
            os.replace(temp_name, self._path)
        finally:
            if self._lock:
                try:
                    with open(self._path, "a") as lf:
                        _unlock_file(lf)
                except (OSError, PermissionError):
                    pass
            # Cleanup temp on any failure
            try:
                os.unlink(temp_name)
            except OSError:
                pass

    def update(self, fn: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        """Load, apply *fn*, save atomically. Returns the new data."""
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


# Convenience: a lock context manager for rare cases needing manual locking
@__import__("contextlib").contextmanager
def _locked_file(path: Path, exclusive: bool = True):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    with open(path, "a") as fh:
        _lock_file(fh, exclusive)
        try:
            yield fh
        finally:
            _unlock_file(fh)