"""Saved views store for `devlog view` command.

A saved view is a named set of filters that can be recalled with
`devlog view run <name>` or `devlog view <name>`. Views are stored as
JSON in <data-dir>/views.json.

View structure:
{
  "my-bugs": {
    "description": "All backend bugs from last 30 days",
    "tags": ["bug", "backend"],
    "since": "30d",
    "until": null,
    "limit": 50,
    "sort": "created_at"
  }
}

Supported filter keys: tags, since, until, limit, sort (created_at/updated_at).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from devlog._store import AtomicStore

if TYPE_CHECKING:
    pass

VALID_FILTER_KEYS = {"tags", "since", "until", "limit", "sort"}


def _get_views_store() -> AtomicStore:
    return AtomicStore("views.json", default={})


def list_views() -> dict[str, dict[str, Any]]:
    """Return all saved views as a dict: name -> filter dict."""
    store = _get_views_store()
    data = store.load()
    # Validate each view
    result = {}
    for name, filters in data.items():
        if isinstance(filters, dict):
            # Keep only known keys
            clean = {k: v for k, v in filters.items() if k in VALID_FILTER_KEYS}
            if clean:
                result[name] = clean
    return result


def get_view(name: str) -> dict[str, Any] | None:
    """Get a single view by name."""
    views = list_views()
    return views.get(name)


def save_view(name: str, filters: dict[str, Any], description: str = "") -> None:
    """Save or update a view (atomic read-modify-write across processes)."""
    # Validate filter keys
    clean = {k: v for k, v in filters.items() if k in VALID_FILTER_KEYS}
    if description:
        clean["description"] = description
    store = _get_views_store()

    def _apply(data: dict[str, Any]) -> dict[str, Any]:
        data[name] = clean
        return data

    store.update(_apply)


def delete_view(name: str) -> bool:
    """Delete a view. Returns True if it existed."""
    store = _get_views_store()
    existed = False

    def _apply(data: dict[str, Any]) -> dict[str, Any]:
        nonlocal existed
        existed = name in data
        if existed:
            del data[name]
        return data

    store.update(_apply)
    return existed


def apply_view(
    entries: list[Any],
    view_name: str,
    *,
    default_limit: int | None = None,
) -> tuple[list[Any], dict[str, Any]]:
    """Apply a saved view to an entry list.

    Returns (filtered_entries, view_dict).
    """
    from devlog import _filter

    view = get_view(view_name)
    if view is None:
        raise ValueError(f"View not found: {view_name}")

    # Extract filter params
    tags = tuple(view.get("tags", [])) if view.get("tags") else ()
    since = view.get("since")
    until = view.get("until")
    limit = view.get("limit", default_limit)
    sort = view.get("sort", True)

    filtered = _filter.filter_pipeline(
        entries,
        tags=tags,
        since=since,
        until=until,
        sort=sort,
    )

    if isinstance(limit, int) and limit > 0:
        filtered = filtered[:limit]

    return filtered, view


def validate_view_dict(data: dict[str, Any]) -> list[str]:
    """Validate a view dict; return list of error messages (empty if valid)."""
    errors = []
    for k, v in data.items():
        if k not in VALID_FILTER_KEYS and k != "description":
            errors.append(f"Unknown filter key: {k}")
        if k == "tags" and not isinstance(v, (list, tuple)):
            errors.append("'tags' must be a list")
        if k == "limit" and not isinstance(v, int):
            errors.append("'limit' must be an integer")
        if k == "sort" and v not in (True, False, "created_at", "updated_at"):
            errors.append("'sort' must be boolean or 'created_at'/'updated_at'")
    return errors