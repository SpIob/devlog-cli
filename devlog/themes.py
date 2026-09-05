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
(``"rgb(255,136,0)"``), or composites like ``"bold yellow"``.

When the file is missing, malformed, or contains unknown role keys, the
loader logs a single warning to STDERR and falls back to the defaults.
This is intentional: a broken theme must never break a working ``devlog``
invocation. Values that fail Rich's ``Style.parse`` are likewise rejected
and fall back to the default for that single role; this catches typos
like ``"bol yellow"`` without making the whole theme unusable.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping

from rich.style import Style

try:
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore[import, no-redef]


# ---------------------------------------------------------------------------
# Role contract
# ---------------------------------------------------------------------------


class ThemeValueError(ValueError):
    """Raised when a theme value is not a parseable Rich style string.

    The message identifies the offending role so the caller can surface
    it in a warning or as part of a batch validation error.
    """

    def __init__(self, role: str, value: object) -> None:
        self.role = role
        self.value = value
        super().__init__(f"theme role {role!r} has invalid style value: {value!r}")


def is_valid_style(value: str) -> bool:
    """Return True iff *value* parses as a Rich style string.

    Uses :class:`rich.style.Style` as the source of truth. Empty strings
    are rejected; a style with no foreground/background is rendered
    incorrectly by Rich and almost always indicates a user typo.
    """
    if not isinstance(value, str) or not value:
        return False
    try:
        Style.parse(value)
    except Exception:  # noqa: BLE001 - Rich raises a wide variety of errors
        return False
    return True


#: Every role that :mod:`devlog.ui` may request from the active theme.
#: A user file may set any subset; unknown keys are dropped with a warning.
#:
#: Single source of truth for theme data. ``ROLES`` and ``DEFAULT_THEME``
#: are derived from this list at import time, and the starter-file
#: template is generated from the same list by :func:`_theme_template`
#: so adding a role only requires editing one place.
_ROLE_DEFAULTS: list[tuple[str, str]] = [
    ("error_border",     "red"),
    ("error_text",       "red"),
    ("warning_text",     "yellow"),
    ("info_text",        "dim"),
    ("success_border",   "green"),
    ("success_text",     "green"),
    ("success_title",    "bold green"),
    ("show_border",      "cyan"),
    ("delete_border",    "red"),
    ("edit_border",      "blue"),
    ("date",             "cyan"),
    ("updated",          "yellow"),
    ("tags",             "magenta"),
    ("id_dim",           "dim white"),
    ("match_highlight",  "bold yellow"),
    ("banner_version",   "bold cyan"),
    ("banner_command",   "bold cyan"),
    ("prompt_border",    "magenta"),
    ("table_caption",    "dim"),
    ("table_footer",     "bold"),
    ("sparkline",        "cyan"),
    ("zebra_alt",        "dim"),
    ("heatmap_base",     "green"),
    ("heatmap_empty",    "grey15"),
    ("heatmap_l1",       "green"),
    ("heatmap_l2",       "color(34)"),
    ("heatmap_l3",       "color(40)"),
    ("heatmap_l4",       "color(46)"),
]


ROLES: frozenset[str] = frozenset(role for role, _ in _ROLE_DEFAULTS)


#: Logical groups of roles used by ``devlog theme list`` to make the
#: output easier to scan. Every role must appear in exactly one section;
#: a test guard (``test_sections_cover_every_role``) enforces this so
#: adding a role to :data:`_ROLE_DEFAULTS` without assigning it a section
#: is a test failure.
SECTIONS: dict[str, tuple[str, ...]] = {
    "Borders": (
        "error_border",
        "success_border",
        "show_border",
        "delete_border",
        "edit_border",
        "prompt_border",
    ),
    "Text": (
        "error_text",
        "warning_text",
        "info_text",
        "success_text",
        "success_title",
        "date",
        "updated",
        "tags",
        "id_dim",
        "match_highlight",
    ),
    "Banner": (
        "banner_version",
        "banner_command",
    ),
    "Tables": (
        "table_caption",
        "table_footer",
        "zebra_alt",
        "sparkline",
    ),
    "Heatmap": (
        "heatmap_base",
        "heatmap_empty",
        "heatmap_l1",
        "heatmap_l2",
        "heatmap_l3",
        "heatmap_l4",
    ),
}


#: Built-in default palette. These are the values previously hardcoded
#: in :mod:`devlog.ui`; rendering is byte-identical to the pre-theming
#: code when no user file is present.
DEFAULT_THEME: dict[str, str] = dict(_ROLE_DEFAULTS)


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


def get_theme_status() -> str:
    """Return a short string describing the on-disk state of the theme file.

    One of:
        * ``"default"`` — no file at :func:`get_theme_path`.
        * ``"ok"`` — file exists and parses as a valid theme.
        * ``"error:<reason>"`` — file exists but cannot be parsed or
          contains non-string palette values.

    Re-reads the file (does not consult the cache) so the returned status
    always reflects the current on-disk state, which is what the user
    sees in ``devlog theme list``'s footer.
    """
    path = get_theme_path()
    if not path.exists():
        return "default"
    try:
        raw = _parse_file(path)
    except tomllib.TOMLDecodeError as exc:
        return f"error: invalid TOML ({exc})"
    except (OSError, TypeError) as exc:
        return f"error: {exc}"
    for role in ROLES:
        if role in raw and not is_valid_style(raw[role]):
            return f"error: role {role!r} has invalid style {raw[role]!r}"
    return "ok"


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
        TypeError: a palette value is not a string.
    """
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    palette = data.get("palette", {})
    if not isinstance(palette, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in palette.items():
        if not isinstance(v, str):
            raise TypeError(
                f"palette key {k!r} has non-string value of type {type(v).__name__}"
            )
        out[str(k)] = v
    return out


def load_theme(warn_stream=None, *, strict: bool = False):
    """Return the active palette: defaults merged with user overrides.

    Behavior:
        * No theme file → return ``DEFAULT_THEME`` unchanged, silently.
        * Malformed TOML → log one warning and return ``DEFAULT_THEME``.
        * Unknown role keys → log one warning per key, drop them.
        * Missing role keys → inherit from ``DEFAULT_THEME``.
        * Invalid style values → log one warning per role and fall back
          to the default for that role only.

    Args:
        warn_stream: a writable stream for warnings (defaults to
            ``sys.stderr``). Pass a custom stream in tests.
        strict: when True, also collect warnings into a list and return
            ``(palette, warnings)`` instead of just the palette. The CLI
            uses this so it can fail loudly on bad input without
            sacrificing the "broken theme must never break devlog"
            guarantee for the default (non-strict) call path.

    Returns:
        * ``strict=False`` (default): a new ``dict`` containing every
          role from :data:`ROLES` resolved to a Rich style string.
        * ``strict=True``: a ``(palette, warnings)`` tuple where
          *warnings* is a list of human-readable messages.
    """
    if warn_stream is None:
        warn_stream = sys.stderr

    path = get_theme_path()
    if not path.exists():
        palette = dict(DEFAULT_THEME)
        return (palette, []) if strict else palette

    warnings: list[str] = []

    try:
        raw = _parse_file(path)
    except tomllib.TOMLDecodeError as exc:
        msg = (
            f"Warning: theme file at {path} is invalid ({exc}); "
            "using default theme."
        )
        print(msg, file=warn_stream)
        if strict:
            warnings.append(msg)
            return dict(DEFAULT_THEME), warnings
        return dict(DEFAULT_THEME)
    except (OSError, TypeError) as exc:
        msg = (
            f"Warning: cannot read theme file at {path} ({exc}); "
            "using default theme."
        )
        print(msg, file=warn_stream)
        if strict:
            warnings.append(msg)
            return dict(DEFAULT_THEME), warnings
        return dict(DEFAULT_THEME)

    # Unknown role keys are silently dropped here. The warning is
    # emitted at the *set* site (see `devlog.cli.theme_set`) so users
    # only see it once, at the moment they typed a bad role. This used
    # to spam stderr on every devlog invocation, which made the journal
    # unusable after a single typo.
    merged = dict(DEFAULT_THEME)
    for role in ROLES:
        if role in raw:
            if is_valid_style(raw[role]):
                merged[role] = raw[role]
            else:
                msg = (
                    f"Warning: theme role {role!r} has invalid style "
                    f"{raw[role]!r}; falling back to default."
                )
                print(msg, file=warn_stream)
                if strict:
                    warnings.append(msg)
    if strict:
        return merged, warnings
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


def _theme_template() -> str:
    """Build the starter ``theme.toml`` content from :data:`_ROLE_DEFAULTS`.

    Generated rather than hardcoded so the template can never drift
    from the actual role list — adding a role in :data:`_ROLE_DEFAULTS`
    automatically shows up here.
    """
    header = (
        "# devlog theme\n"
        "#\n"
        "# Override any role below to customize colors. Roles you omit use the\n"
        "# built-in default. Any Rich style string is accepted: named colors\n"
        "# (e.g. \"red\"), hex (\"#ff8800\"), 256-color (\"color(208)\"),\n"
        "# true-color (\"rgb(255,136,0)\"), or composites like \"bold yellow\".\n"
        "#\n"
        "# Run `devlog theme list` to see every role and its current value.\n"
        "\n"
        "[palette]\n"
    )
    body = "\n".join(
        f"# {role:<16} = \"{default}\"" for role, default in _ROLE_DEFAULTS
    )
    return header + body + "\n"


def build_theme_toml(
    palette: Mapping[str, str],
    *,
    name: str = "custom",
    description: str = "Custom theme",
) -> str:
    """Build a complete, uncommented ``theme.toml`` from *palette*.

    Inverse of :func:`_theme_template`. Shared by ``devlog theme export``
    and ``devlog theme create`` so both produce files that round-trip
    losslessly through ``devlog theme set <file>.toml``.

    Args:
        palette: a ``{role: style}`` mapping. Roles not present in
            *palette* fall back to the built-in defaults so the output
            is always complete (one line per known role).
        name: the ``[meta].name`` to embed.
        description: the ``[meta].description`` to embed.

    Returns:
        A TOML string ready to be written to disk or stdout.
    """
    safe_name = name.replace('"', '\\"')
    safe_desc = description.replace('"', '\\"')
    lines = [
        "# devlog theme",
        "",
        "[meta]",
        f'name = "{safe_name}"',
        f'description = "{safe_desc}"',
        "",
        "[palette]",
    ]
    for role, default in _ROLE_DEFAULTS:
        value = palette.get(role, default)
        lines.append(f'{role} = "{value}"')
    return "\n".join(lines) + "\n"


def export_template(
    palette: Mapping[str, str] | None = None,
    *,
    name: str = "exported",
    description: str = "Exported active theme",
) -> str:
    """Build a complete, uncommented ``theme.toml`` from *palette*.

    Thin wrapper around :func:`build_theme_toml` that defaults *palette*
    to the active theme. Used by ``devlog theme export`` to produce a
    file that round-trips losslessly through
    ``devlog theme set <exported.toml>``.

    Args:
        palette: a ``{role: style}`` mapping. Defaults to the active
            theme, which is the common case for ``theme export``.
        name: the ``[meta].name`` to embed in the export.
        description: the ``[meta].description`` to embed.

    Returns:
        A TOML string ready to be written to disk or stdout.
    """
    if palette is None:
        palette = get_active_theme()
    return build_theme_toml(palette, name=name, description=description)


def write_default_theme(destination) -> None:
    """Write a starter ``theme.toml`` to *destination*.

    All lines are commented out so the file is a no-op template that
    users uncomment to customize.

    Args:
        destination: a :class:`pathlib.Path` (file is written, parents
            created) or a writable stream (the template text is
            written directly to it).
    """
    text = _theme_template()
    if hasattr(destination, "write"):
        destination.write(text)
        return
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Installing a theme file
# ---------------------------------------------------------------------------


class ThemeInstallError(Exception):
    """Raised by :func:`install_theme_file` when an install cannot proceed.

    Carries a human-readable reason; the CLI prints it directly.
    """


def validate_source(raw: Mapping[str, str]) -> tuple[list[str], list[str]]:
    """Validate a parsed palette against the role contract.

    Returns:
        A ``(unknown_roles, invalid_roles)`` tuple of role names. The
        two lists are independent: a role cannot be both unknown and
        invalid (unknown roles are not validated).

    Used by ``devlog theme set`` and ``devlog theme use`` so they share
    a single source of truth for what "valid" means.
    """
    unknown = [k for k in raw if k not in ROLES]
    invalid = [k for k in raw if k in ROLES and not is_valid_style(raw[k])]
    return unknown, invalid


def install_theme_file(source: Path, destination: Path) -> dict[str, str]:
    """Copy *source* to *destination* and refresh the cache.

    Source must be readable. Destination's parent is created if
    missing. The destination is *not* validated; the caller is
    expected to call :func:`validate_source` first if it cares about
    the contents.

    Args:
        source: the file to copy from.
        destination: the path to copy to (typically :func:`get_theme_path`).

    Returns:
        The newly-active theme after the install completes.

    Raises:
        ThemeInstallError: the source cannot be read or the destination
            cannot be written.
    """
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise ThemeInstallError(f"cannot read {source}: {exc}") from exc

    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        destination.write_bytes(data)
    except OSError as exc:
        raise ThemeInstallError(f"cannot write {destination}: {exc}") from exc

    reset_cache()
    return get_active_theme()


# ---------------------------------------------------------------------------
# Bundled themes
# ---------------------------------------------------------------------------


class ThemeNotFoundError(KeyError):
    """Raised by :func:`get_builtin_theme_path` when no builtin matches."""


def _builtins_dir():
    """Return an :mod:`importlib.resources` Traversable for the builtins.

    Pulled out as a helper so tests can monkeypatch the location when
    the package is shipped as a zip or frozen executable.

    The builtins live in :mod:`devlog.builtins` (a sibling of this
    module) because :mod:`devlog.themes` is a module rather than a
    package, which prevents ``devlog.themes.builtins`` from being
    importable as a sub-package.
    """
    try:
        from importlib.resources import files
    except ImportError:  # pragma: no cover - Python < 3.9
        from importlib_resources import files  # type: ignore[no-redef]
    return files("devlog.builtins")


def list_builtin_themes() -> list[str]:
    """Return the sorted names of all bundled theme files.

    A name is the file stem of any ``*.toml`` under
    :mod:`devlog.themes.builtins` that contains a ``[palette]`` table.
    Non-palette files (e.g. ``__init__.py``) are filtered out.
    """
    names: list[str] = []
    for entry in _builtins_dir().iterdir():
        if not entry.name.endswith(".toml"):
            continue
        # Cheap check: read the file and confirm it has a [palette] section.
        # We avoid parsing with tomllib here because the builtins live
        # in package data and may not be present in editable installs
        # that haven't generated them yet.
        try:
            text = entry.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "[palette]" not in text:
            continue
        names.append(entry.name[: -len(".toml")])
    return sorted(names)


def get_builtin_theme_path(name: str) -> Path:
    """Return the on-disk path of the bundled theme *name*.

    Raises:
        ThemeNotFoundError: no builtin with that name exists.
    """
    entry = _builtins_dir() / f"{name}.toml"
    if not entry.is_file():
        raise ThemeNotFoundError(name)
    # Materialize to a real path so callers (and shutil.copy2) can
    # treat it like any other file.
    from importlib.resources import as_file

    with as_file(entry) as materialized:
        return Path(materialized)


def load_builtin_theme(name: str) -> dict[str, str]:
    """Parse and merge a bundled theme, just like :func:`load_theme`."""
    path = get_builtin_theme_path(name)
    raw = _parse_file(path)
    merged = dict(DEFAULT_THEME)
    for role in ROLES:
        if role in raw and is_valid_style(raw[role]):
            merged[role] = raw[role]
    return merged


def get_builtin_meta(name: str) -> dict[str, str]:
    """Return the ``[meta]`` table from a bundled theme.

    Falls back to ``{"name": name, "description": ""}`` when the file
    has no ``[meta]`` table, so callers always get a complete mapping.
    """
    path = get_builtin_theme_path(name)
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    meta = data.get("meta", {})
    if not isinstance(meta, dict):
        meta = {}
    out = {"name": name, "description": ""}
    for k, v in meta.items():
        if isinstance(v, str):
            out[str(k)] = v
    return out


__all__ = [
    "ROLES",
    "SECTIONS",
    "DEFAULT_THEME",
    "ThemeValueError",
    "ThemeInstallError",
    "ThemeNotFoundError",
    "is_valid_style",
    "validate_source",
    "install_theme_file",
    "get_theme_path",
    "get_theme_status",
    "load_theme",
    "get_active_theme",
    "set_active_theme",
    "reset_cache",
    "get_style",
    "get_bold_style",
    "write_default_theme",
    "build_theme_toml",
    "export_template",
    "list_builtin_themes",
    "get_builtin_theme_path",
    "load_builtin_theme",
    "get_builtin_meta",
]
