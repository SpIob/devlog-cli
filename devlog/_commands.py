"""Single source of truth for the devlog command list.

Used by:
- ``devlog.cli`` — drives the root banner.
- ``devlog/_completions.py`` — generates shell completion scripts.
"""

from __future__ import annotations

COMMANDS: list[tuple[str, str]] = [
    ("add", "Add a new journal entry"),
    ("show", "Show a single entry by ID"),
    ("edit", "Edit an entry's message and/or tags"),
    ("delete", "Delete an entry by ID"),
    ("list", "List entries, newest first"),
    ("search", "Search entry messages"),
    ("today", "Show today's entries"),
    ("yesterday", "Show yesterday's entries"),
    ("week", "Show the last 7 days"),
    ("on", "Show entries for a specific date"),
    ("tail", "Show the N most recent entries"),
    ("tags", "List tags with usage counts"),
    ("tag", "Show or delete entries with a tag"),
    ("merge-tag", "Merge two tags across all entries"),
    ("rename-tag", "Rename a tag across all entries"),
    ("theme", "View or change the active color theme"),
    ("git", "Git integration commands"),
    ("stats", "Summarize the journal"),
    ("digest", "Summarize recent journal activity (AI optional)"),
    ("ask", "Ask your journal questions via local embeddings (AI)"),
    ("mcp", "Expose the journal to AI agents (MCP server)"),
    ("calendar", "Show a year-grid heatmap of activity"),
    ("import", "Import entries from a JSON or Markdown file"),
    ("export", "Export entries to a Markdown or JSON file"),
    ("completions", "Print a shell completion script"),
    ("repair", "Inspect and repair the on-disk journal store"),
    ("backup", "Write a timestamped copy of the journal"),
    ("restore", "Restore the journal from a backup file"),
    ("undo", "Roll the journal back to its state before the last write"),
    ("doctor", "Check the journal store for corruption"),
    ("config", "View and manage devlog configuration"),
    ("template", "Manage entry templates for `devlog add --template`"),
    ("view", "Save and recall named filter combinations"),
]

__all__ = ["COMMANDS"]
