# devlog-cli

A terminal-based developer journal — log, search, filter, and export daily progress entries without leaving the command line.

---

## Demo

```
$ devlog add "Rewrote auth middleware to use JWT refresh tokens" -t backend -t security
✔ Entry added [id: a1b2c3d4]
  Date : 2025-05-11T10:22:00Z
  Tags : backend, security
  Note : Rewrote auth middleware to use JWT refresh tokens

$ devlog add "Drafted ADR for new caching layer" -t docs -t architecture
✔ Entry added [id: b2c3d4e5]
  Date : 2025-05-11T11:05:00Z
  Tags : architecture, docs
  Note : Drafted ADR for new caching layer

$ devlog add "Fixed flaky test in CI pipeline" -t bugfix -t devops
✔ Entry added [id: c3d4e5f6]
  Date : 2025-05-11T14:30:00Z
  Tags : bugfix, devops
  Note : Fixed flaky test in CI pipeline

$ devlog list
┌──────────┬──────────────────────┬─────────────────────┬──────────────────────────────────────────────┐
│ ID       │ Date                 │ Tags                │ Message                                      │
├──────────┼──────────────────────┼─────────────────────┼──────────────────────────────────────────────┤
│ c3d4e5f6 │ 2025-05-11 14:30 UTC │ bugfix, devops      │ Fixed flaky test in CI pipeline              │
│ b2c3d4e5 │ 2025-05-11 11:05 UTC │ architecture, docs  │ Drafted ADR for new caching layer            │
│ a1b2c3d4 │ 2025-05-11 10:22 UTC │ backend, security   │ Rewrote auth middleware to use JWT refresh…  │
└──────────┴──────────────────────┴─────────────────────┴──────────────────────────────────────────────┘
Showing 3 of 3 entries.

$ devlog search "auth"
┌──────────┬──────────────────────┬───────────────────┬──────────────────────────────────────────────┐
│ ID       │ Date                 │ Tags              │ Message                                      │
├──────────┼──────────────────────┼───────────────────┼──────────────────────────────────────────────┤
│ a1b2c3d4 │ 2025-05-11 10:22 UTC │ backend, security │ Rewrote **auth** middleware to use JWT refr… │
└──────────┴──────────────────────┴───────────────────┴──────────────────────────────────────────────┘
Showing 1 of 1 entries.
```

---

## Installation

**With pip:**

```bash
pip install .
```

**With pipx (recommended — keeps devlog isolated):**

```bash
pipx install .
```

After installation, the `devlog` command is available globally in your terminal.

---

## Quick Start

```bash
# Log what you did this morning
devlog add "Set up staging environment on Railway" -t devops

# Add an entry with multiple tags
devlog add "Fixed off-by-one error in pagination logic" -t bugfix -t backend

# Review today's work
devlog list

# Find everything related to your auth work
devlog search "auth"

# Narrow list output to a specific tag
devlog list -t bugfix

# Export everything to a Markdown file for your weekly review
devlog export -o ~/notes/week-20.md
```

---

## Command Reference

### `devlog add`

Add a new journal entry.

```
devlog add MESSAGE [OPTIONS]
```

| Argument / Option | Short | Type | Required | Default | Description |
|-------------------|-------|------|----------|---------|-------------|
| `MESSAGE` | — | string | Yes | — | The body of the journal entry. |
| `--tag` | `-t` | string | No | — | Attach a tag to the entry. Repeatable: `-t backend -t bugfix`. Tags are normalized to lowercase. Allowed characters: `[a-z0-9-]`. |
| `--quiet` | `-q` | flag | No | False | Suppress confirmation output. Exits silently with code 0 on success. |

**Notes:**

- Tags are normalized to lowercase and stripped of whitespace before storage.
- Duplicate tags on the same entry are silently deduplicated.
- Tags containing characters outside `[a-z0-9-]` (after normalization) are rejected with a clear error and exit code 1.
- Maximum 10 tags per entry; maximum 32 characters per tag.
- An empty message string exits with code 1.

**Examples:**

```bash
devlog add "Finished OAuth2 integration"
devlog add "Refactored database connection pool" -t backend -t refactor
devlog add "Deployed hotfix to production" -t devops -q
```

---

### `devlog list`

Display journal entries in a formatted table, newest first.

```
devlog list [OPTIONS]
```

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--tag` | `-t` | string | None | Filter by tag. Repeatable. Multiple flags use AND logic — entry must carry all specified tags. |
| `--limit` | `-n` | integer | 20 | Maximum number of entries to display. Must be a positive integer. |
| `--all` | — | flag | False | Override `--limit` and display every entry. |
| `--quiet` | `-q` | flag | False | Output one raw JSON object per line to STDOUT instead of the Rich table. Useful for scripting. |

**Notes:**

- Results are sorted newest first.
- Message text is truncated to 60 characters with a `…` suffix in table view.
- If no entries exist, prints `No entries found.` and exits with code 0.
- If filters are applied and nothing matches, prints `No entries match your filters.` and exits with code 0.

**Examples:**

```bash
devlog list
devlog list -t bugfix
devlog list -t backend -t auth --limit 5
devlog list --all
devlog list --quiet | jq '.tags'
```

---

### `devlog search`

Full-text search across all entry messages.

```
devlog search QUERY [OPTIONS]
```

| Argument / Option | Short | Type | Required | Default | Description |
|-------------------|-------|------|----------|---------|-------------|
| `QUERY` | — | string | Yes | — | Search term. Case-insensitive substring match against the message field. |
| `--tag` | `-t` | string | No | None | Narrow results to entries that also carry these tags. Repeatable; AND logic. |
| `--limit` | `-n` | integer | No | 20 | Maximum number of results to display. |
| `--quiet` | `-q` | flag | No | False | Output raw JSON lines to STDOUT instead of the Rich table. |

**Notes:**

- Matching substrings are highlighted in bold yellow in the Message column.
- If no results are found, prints `No entries matched "<query>".` and exits with code 0.
- Tag filters and the search term both apply simultaneously (AND logic).

**Examples:**

```bash
devlog search "null pointer"
devlog search "cache" -t backend
devlog search "deploy" --limit 10
devlog search "refactor" --quiet | jq '.'
```

---

### `devlog export`

Export all entries (or a filtered subset) to a Markdown file.

```
devlog export [OPTIONS]
```

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--output` | `-o` | path | `./devlog-export.md` | Destination file path. Existing files are overwritten without prompting. |
| `--tag` | `-t` | string | None | Export only entries that carry all specified tags. Repeatable; AND logic. |
| `--quiet` | `-q` | flag | False | Suppress the progress bar and confirmation. Prints only the output path on success. |

**Output format per entry:**

```markdown
## 2025-05-11 10:22 UTC — a1b2c3d4

Rewrote auth middleware to use JWT refresh tokens.

**Tags:** backend, security

---
```

**Notes:**

- A Rich progress bar is shown on STDERR during the write (suppress with `--quiet`).
- If no entries exist (or no entries match the tag filters), prints `Warning: No entries to export.` and exits with code 0.
- If the output path is not writable, exits with code 2 and a clear error message.

**Examples:**

```bash
devlog export
devlog export -o ~/notes/week-20.md
devlog export -t backend -o backend-log.md
devlog export --quiet -o /tmp/devlog.md
```

---

## Configuration

### `DEVLOG_DATA_DIR`

By default, devlog stores all entries at:

```
~/.devlog/entries.json
```

To store entries in a different location — for example, inside a synced folder or a project directory — set the `DEVLOG_DATA_DIR` environment variable. devlog will read and write `entries.json` inside whatever directory you specify.

```bash
# Store entries in a Dropbox-synced folder
export DEVLOG_DATA_DIR="$HOME/Dropbox/devlog"
devlog add "Entry goes here"
# → written to ~/Dropbox/devlog/entries.json

# Store entries in a project-local directory
export DEVLOG_DATA_DIR="$(pwd)/.devlog"
devlog list
```

The target directory is created automatically on first run if it does not exist.

You can make the variable permanent by adding it to your shell configuration:

```bash
# ~/.zshrc or ~/.bashrc
export DEVLOG_DATA_DIR="$HOME/Dropbox/devlog"
```

---

## Development Setup

```bash
# 1. Clone the repository
git clone https://github.com/SpIob/devlog-cli.git
cd devlog-cli

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install in editable mode with development dependencies
pip install -e .
pip install pytest

# 4. Verify the installation
devlog --version

# 5. Run the test suite
pytest
```

**Project layout:**

```
devlog-cli/
├── devlog/
│   ├── __init__.py
│   ├── cli.py          ← Click entry point, all command definitions
│   ├── storage.py      ← File I/O, JSON read/write, atomic write logic
│   └── models.py       ← Entry dataclass / TypedDict definition
├── tests/
│   ├── test_add.py
│   ├── test_list.py
│   ├── test_search.py
│   └── test_export.py
├── pyproject.toml
└── README.md
```

Tests use Click's `CliRunner` for full end-to-end command testing without touching the real filesystem. Each command has a happy-path test, an empty-state test, and at least one error-condition test.

---

## License

MIT License

Copyright (c) 2025

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.