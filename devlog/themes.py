"""Custom themes for the devlog UI.

A theme is a flat mapping of *role* names to Rich style strings. The roles
cover every color currently hardcoded in :mod:`devlog.ui`; when a role is
absent from the user's file the bundled default is used, so omitting a
key is always safe.

A user theme lives at ``~/.devlog/theme.toml`` (or
``$DEVLOG_DATA_DIR/theme.toml`` when set). The file is optional; without
it, the default theme is used and no warning is printed.

Example ``theme.toml``::

    [palette]
    success_border = "green"
    error_border   = "bright_red"
    tags           = "bright_magenta"
    date           = "cyan"

Any Rich style string is accepted: named colors, hex (``"#ff8800"``),
256-color indices (``"color(208)"``), true-color triples
(``"rgb((255,136,0))"``), or composites like ``"bold yellow"``.

When the file is missing, malformed, or contains unknown role keys, the
loader logs a single warning to STDERR and falls back to the defaults.
This is intentional: a broken theme must never break a working ``devlog``
invocation.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping

try:
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore[import, no-redef]


# ---------------------------------------------------------------------------
# Role contract
# ---------------------------------------------------------------------------

#: Every role that :mod:`devlog.ui` may request from the active theme.
#: A user file may set any subset; unknown keys are dropped with a warning.
ROLES: frozenset[str] = frozenset(
    {
        "error_border",
        "error_text",
        "warning_text",
        "info_text",
        "success_border",
        "success_title",
        "show_border",
        "delete_border",
        "edit_border",
        "date",
        "updated",
        "tags",
        "id_dim",
        "match_highlight",
        "banner_version",
        "banner_command",
        "zebra_alt",
    }
)


#: Built-in default palette. These are the values previously hardcoded
#: in :mod:`devlog.ui`; rendering is byte-identical to the pre-theming
#: code when no user file is present.
DEFAULT_THEME: dict[str, str] = {
    "error_border": "red",
    "error_text": "red",
    "warning_text": "yellow",
    "info_text": "dim",
    "success_border": "green",
    "success_title": "bold green",
    "show_border": "cyan",
    "delete_border": "red",
    "edit_border": "blue",
    "date": "cyan",
    "updated": "yellow",
    "tags": "magenta",
    "id_dim": "dim white",
    "match_highlight": "bold yellow",
    "banner_version": "bold cyan",
    "banner_command": "bold cyan",
    "zebra_alt": "dim",
}


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def get_theme_path() -> Path:
    """Return the absolute path to the user's theme file.

    Uses the ``DEVLOG_DATA_DIR`` environment variable when set, mirroring
    :func:`devlog.storage.get_storage_path` so both files live together.

    Returns:
        Path: absolute path to ``theme.toml`` (the file itself may not exist).
    """
    if "DEVLOG_DATA_DIR" in os.environ:
        return Path(os.environ["DEVLOG_DATA_DIR"]) / "theme.toml"
    return Path.home() / ".devlog" / "theme.toml"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _parse_file(path: Path) -> dict[str, str]:
    """Parse a TOML file and return the raw ``[palette]`` mapping.

    Args:
        path: the theme file to read.

    Returns:
        The ``palette`` table as a plain ``dict``. Empty when the file
        has no ``[palette]`` section. Unknown keys are kept here and
        filtered by :func:`load_theme` so the caller can warn per key.

    Raises:
        FileNotFoundError: the file does not exist.
        tomllib.TOMLDecodeError: the file is not valid TOML.
    """
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    palette = data.get("palette", {})
    if not isinstance(palette, dict):
        return {}
    return {str(k): str(v) for k, v in palette.items()}


def load_theme(warn_stream=None) -> dict[str, str]:
    """Return the active palette: defaults merged with user overrides.

    Behavior:
        * No theme file → return ``DEFAULT_THEME`` unchanged, silently.
        * Malformed TOML → log one warning and return ``DEFAULT_THEME``.
        * Unknown role keys → log one warning per key, drop them.
        * Missing role keys → inherit from ``DEFAULT_THEME``.

    Args:
        warn_stream: a writable stream for warnings (defaults to
            ``sys.stderr``). Pass a custom stream in tests.

    Returns:
        A new ``dict`` containing every role from :data:`ROLES` resolved
        to a Rich style string.
    """
    if warn_stream is None:
        warn_stream = sys.stderr

    path = get_theme_path()
    if not path.exists():
        return dict(DEFAULT_THEME)

    try:
        raw = _parse_file(path)
    except tomllib.TOMLDecodeError as exc:
        print(
            f"Warning: theme file at {path} is invalid ({exc}); "
            "using default theme.",
            file=warn_stream,
        )
        return dict(DEFAULT_THEME)
    except OSError as exc:
        print(
            f"Warning: cannot read theme file at {path} ({exc}); "
            "using default theme.",
            file=warn_stream,
        )
        return dict(DEFAULT_THEME)

    unknown = sorted(k for k in raw if k not in ROLES)
    for key in unknown:
        print(
            f"Warning: theme role '{key}' is unknown and will be ignored.",
            file=warn_stream,
        )

    merged = dict(DEFAULT_THEME)
    for role in ROLES:
        if role in raw:
            merged[role] = raw[role]
    return merged


# ---------------------------------------------------------------------------
# Active theme cache
# ---------------------------------------------------------------------------


_active_theme: dict[str, str] | None = None


def get_active_theme() -> dict[str, str]:
    """Return the cached active theme, loading on first access.

    The cache survives across calls within a single process so that
    per-render lookups stay O(1). Tests that mutate the active theme
    should call :func:`reset_cache` afterwards.
    """
    global _active_theme
    if _active_theme is None:
        _active_theme = load_theme()
    return _active_theme


def set_active_theme(theme: Mapping[str, str]) -> None:
    """Replace the cached active theme.

    Primarily for tests. The provided mapping is validated against
    :data:`ROLES` and any missing roles are filled from the defaults.
    """
    global _active_theme
    merged: dict[str, str] = dict(DEFAULT_THEME)
    for role in ROLES:
        if role in theme:
            merged[role] = theme[role]
    _active_theme = merged


def reset_cache() -> None:
    """Clear the active-theme cache. Next ``get_active_theme`` re-reads disk."""
    global _active_theme
    _active_theme = None


# ---------------------------------------------------------------------------
# Style helpers
# ---------------------------------------------------------------------------


_BOLD_PREFIX = "bold "


def get_style(role: str) -> str:
    """Return the Rich style string for *role* from the active theme.

    The lookup always succeeds because :func:`get_active_theme` fills
    any missing role from the defaults.

    Args:
        role: one of the keys in :data:`ROLES`.

    Returns:
        A Rich style string suitable for ``style=``, ``border_style=``,
        or inline ``[style]...[/style]`` markup.
    """
    return get_active_theme()[role]


def get_bold_style(role: str) -> str:
    """Return the bold variant of *role*'s style.

    Used for icon characters and titles where the current code pairs a
    plain color with ``"bold <color>"``. To keep the user file flat we
    synthesize the bold form on the fly by prepending ``"bold "`` to
    the role's base style. If the base style already starts with
    ``"bold "`` it is returned as-is (idempotent).

    Args:
        role: one of the keys in :data:`ROLES`.

    Returns:
        A Rich style string, e.g. ``"bold red"``.
    """
    base = get_style(role)
    if base.startswith(_BOLD_PREFIX):
        return base
    return _BOLD_PREFIX + base


# ---------------------------------------------------------------------------
# Writing the starter file
# ---------------------------------------------------------------------------


_THEME_TEMPLATE = """# devlog theme
#
# Override any role below to customize colors. Roles you omit use the
# built-in default. Any Rich style string is accepted: named colors
# (e.g. "red"), hex ("#ff8800"), 256-color ("color(208)"),
# true-color ("rgb((255,136,0))"), or composites like "bold yellow".
#
# Run `devlog theme list` to see every role and its current value.

[palette]
# error_border     = "red"
# error_text       = "red"
# warning_text     = "yellow"
# info_text        = "dim"
# success_border   = "green"
# success_title    = "bold green"
# show_border      = "cyan"
# delete_border    = "red"
# edit_border      = "blue"
# date             = "cyan"
# updated          = "yellow"
# tags             = "magenta"
# id_dim           = "dim white"
# match_highlight  = "bold yellow"
# banner_version   = "bold cyan"
# banner_command   = "bold cyan"
# zebra_alt        = "dim"
"""


def write_default_theme(destination) -> None:
    """Write a starter ``theme.toml`` to *destination*.

    All lines are commented out so the file is a no-op template that
    users uncomment to customize.

    Args:
        destination: a :class:`pathlib.Path` (file is written, parents
            created) or a writable stream (the template text is
            written directly to it).
    """
    text = _THEME_TEMPLATE
    if hasattr(destination, "write"):
        destination.write(text)
        return
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


__all__ = [
    "ROLES",
    "DEFAULT_THEME",
    "get_theme_path",
    "load_theme",
    "get_active_theme",
    "set_active_theme",
    "reset_cache",
    "get_style",
    "get_bold_style",
    "write_default_theme",
]
