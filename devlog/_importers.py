"""Importers for other journal formats (jrnl, Day One, etc.)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from devlog.models import Entry


def _parse_iso_timestamp(ts: str) -> str | None:
    """Parse various ISO timestamp formats to UTC Z-suffixed string."""
    if not ts:
        return None
    try:
        # Try parsing as ISO format
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        pass
    return None


def import_jrnl_json(path: str) -> list[Entry]:
    """Import entries from a jrnl JSON export file.

    jrnl exports have format:
    {
      "entries": [
        {
          "date": "2024-01-15T10:30:00",
          "text": "Entry text\n\nMore text",
          "tags": ["tag1", "tag2"],
          "starred": false
        }
      ]
    }
    """
    from devlog.models import Entry

    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)

    entries = []
    for item in data.get("entries", []):
        # jrnl uses "text" for message, "date" for timestamp
        message = item.get("text", "")
        if not message:
            continue

        created_at = _parse_iso_timestamp(item.get("date", ""))
        if not created_at:
            # Fallback to current time
            created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        tags = item.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]

        # Add starred as a tag
        if item.get("starred"):
            tags.append("starred")

        entry = Entry(
            id=str(uuid.uuid4()),
            message=message,
            tags=[t.lower().replace(" ", "-") for t in tags],
            created_at=created_at,
        )
        entries.append(entry)

    return entries


def import_dayone_json(path: str) -> list[Entry]:
    """Import entries from a Day One JSON export file.

    Day One exports have format:
    {
      "entries": [
        {
          "creationDate": "2024-01-15T10:30:00Z",
          "text": "Entry text",
          "tags": ["tag1", "tag2"],
          "uuid": "optional-uuid"
        }
      ]
    }
    """
    from devlog.models import Entry

    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)

    entries = []
    for item in data.get("entries", []):
        message = item.get("text", "")
        if not message:
            continue

        created_at = _parse_iso_timestamp(item.get("creationDate", ""))
        if not created_at:
            created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        tags = item.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]

        # Use Day One UUID if available, otherwise generate new
        entry_id = item.get("uuid", str(uuid.uuid4()))

        entry = Entry(
            id=entry_id,
            message=message,
            tags=[t.lower().replace(" ", "-") for t in tags],
            created_at=created_at,
        )
        entries.append(entry)

    return entries


def import_generic_json(path: str) -> list[Entry]:
    """Import entries from a generic JSON file with entries array.

    Expected format:
    {
      "entries": [
        {
          "id": "optional",
          "message": "Entry text",
          "created_at": "ISO timestamp",
          "tags": ["tag1", "tag2"],
          "updated_at": "optional ISO timestamp"
        }
      ]
    }
    """
    from devlog.models import Entry

    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)

    entries = []
    for item in data.get("entries", []):
        message = item.get("message", "")
        if not message:
            continue

        created_at = item.get("created_at")
        if not created_at:
            created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            created_at = _parse_iso_timestamp(created_at) or created_at

        tags = item.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]

        entry_id = item.get("id", str(uuid.uuid4()))
        updated_at = item.get("updated_at")
        if updated_at:
            updated_at = _parse_iso_timestamp(updated_at) or updated_at

        entry = Entry(
            id=entry_id,
            message=message,
            tags=[t.lower().replace(" ", "-") for t in tags],
            created_at=created_at,
            updated_at=updated_at,
        )
        entries.append(entry)

    return entries


def detect_format(path: str) -> str:
    """Detect the format of an import file."""
    with open(path, encoding="utf-8") as fh:
        content = fh.read(2048)  # Read first 2KB for detection

    lower = path.lower()
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        # Not valid JSON: fall back to the extension so .md files keep
        # working and a broken .json file surfaces the JSON parser's
        # error message (instead of "unknown format").
        if lower.endswith(".json"):
            return "json"
        if lower.endswith(".md") or lower.endswith(".markdown"):
            return "markdown"
        return "unknown"

    # Check for jrnl format
    if "entries" in data:
        first = data["entries"][0] if data["entries"] else {}
        if "text" in first and "date" in first and "starred" in first:
            return "jrnl"
        if "creationDate" in first and "text" in first:
            return "dayone"
        if "message" in first or "id" in first:
            # This looks like a devlog JSON export
            return "json"

    # Check file extension as fallback
    if lower.endswith(".json"):
        return "json"
    if lower.endswith(".md") or lower.endswith(".markdown"):
        return "markdown"

    return "unknown"


def import_file(path: str, format: str | None = None) -> list[Entry]:
    """Import entries from a file, auto-detecting format if not specified."""
    if format is None or format == "auto":
        format = detect_format(path)

    if format == "jrnl":
        return import_jrnl_json(path)
    elif format == "dayone":
        return import_dayone_json(path)
    elif format == "generic":
        return import_generic_json(path)
    else:
        raise ValueError(f"Unknown import format: {format}")