# Product Specification Document — devlog-cli

**Version:** 1.0  
**Author:** Senior Planner  
**Status:** Final — ready for Claude B implementation

---

## A. Executive Summary

`devlog-cli` is a terminal-based developer journal tool that lets a solo developer log, search, filter, and export daily progress entries from the command line without opening a browser, editor, or external app. Entries are stored locally as a JSON flat file, require no account or internet connection, and are accessible instantly from any terminal session. The tool is designed around four core commands — `add`, `list`, `search`, and `export` — and prioritizes speed, clarity, and zero-friction daily use. It solves the problem of developers losing track of what they worked on by making the act of logging a single fast command rather than a context-switch to another application.

---

## B. Functional Requirements

### Root Command Behaviour

Running `devlog` with no subcommand, or `devlog --help`, must print the standard Click help block listing all available subcommands with one-line descriptions. Running `devlog --version` must print the version string in the format `devlog, version X.Y.Z` and exit with code 0.

### Command 1 — `add`

**Purpose:** Add a new journal entry.  
**Usage:**  
`devlog add MESSAGE [OPTIONS]`

| Element | Detail |
|---------|--------|
| `MESSAGE` | Positional, required, string. The body of the journal entry. |
| `--tag` / `-t` | Option, multiple=True, string. Attach one or more tags. Repeatable: `-t backend -t bugfix`. |
| `--quiet` / `-q` | Flag. Suppress confirmation output. Exit 0 silently on success. |

**Behaviour:**

- Generates a new entry with a UUID, the message text, a UTC ISO 8601 timestamp, and the provided tags.
- Appends the entry to the JSON storage file.
- Uses atomic write (temp file + `os.replace()`) to prevent corruption.
- Tags are normalized to lowercase and stripped of leading/trailing whitespace before storage.
- Duplicate tags on the same entry are silently deduplicated.
- Tags containing characters outside `[a-z0-9\-]` (after normalization) are rejected with a clear error.

**Success Output (STDOUT):**

```
✔ Entry added [id: a1b2c3d4]
  Date : 2025-05-11T10:22:00Z
  Tags : backend, bugfix
  Note : Fixed the null pointer issue in the auth module
```
*Rich panel, green checkmark, cyan date, magenta tags.*

**Failure cases:**

| Condition | Output | Exit Code |
|-----------|--------|-----------|
| Empty message string `""` | STDERR: `Error: MESSAGE cannot be empty.` | 1 |
| Invalid tag characters | STDERR: `Error: Tag "foo bar" contains invalid characters. Use lowercase letters, numbers, and hyphens only.` | 1 |
| Storage file unwritable | STDERR: `Error: Cannot write to storage file at <path>. Check file permissions.` | 2 |

---

### Command 2 — `list`

**Purpose:** Display journal entries in a Rich-formatted table.  
**Usage:**  
`devlog list [OPTIONS]`

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--tag` | `-t` | string, multiple | None | Filter by tag. Multiple flags = AND logic. |
| `--limit` | `-n` | integer | 20 | Max entries to show. |
| `--all` |  | flag | False | Override limit and show every entry. |
| `--quiet` | `-q` | flag | False | Output raw JSON lines to STDOUT instead of table. |

**Behaviour:**

- Loads entries from storage, applies tag filters (AND logic — entry must have ALL specified tags), then shows the most recent `--limit` entries first (newest at top).
- `--all` overrides `--limit` entirely.
- `--quiet` outputs one raw JSON object per line to STDOUT (machine-readable).

**Success Output (STDOUT):**  
Rich table with columns: ID (short) | Date | Tags | Message (truncated to 60 chars).

```
┌──────────┬──────────────────────┬───────────────┬──────────────────────────────────────────────┐
│ ID       │ Date                 │ Tags          │ Message                                      │
├──────────┼──────────────────────┼───────────────┼──────────────────────────────────────────────┤
│ a1b2c3d4 │ 2025-05-11 10:22 UTC │ backend, auth │ Fixed the null pointer issue in the auth ... │
└──────────┴──────────────────────┴───────────────┴──────────────────────────────────────────────┘
Showing 1 of 1 entries.
```

Empty state: Print to STDOUT: `No entries found.` (or `No entries match your filters.` if filters were applied). Exit 0.

**Failure cases:**

| Condition | Output | Exit Code |
|-----------|--------|-----------|
| Corrupted JSON file | STDERR: `Error: Storage file is corrupted at <path>. Run 'devlog repair' or delete the file to reset.` | 2 |
| `--limit` is not a positive integer | STDERR: `Error: --limit must be a positive integer.` | 1 |

---

### Command 3 — `search`

**Purpose:** Full-text search across all entry messages.  
**Usage:**  
`devlog search QUERY [OPTIONS]`

| Element | Detail |
|---------|--------|
| `QUERY` | Positional, required, string. Search term. Case-insensitive substring match against the message field. |
| `--tag` / `-t` | Option, multiple, string. Optionally narrow results to entries that also match these tags (AND logic). |
| `--limit` / `-n` | Option, integer, default 20. Max results to display. |
| `--quiet` / `-q` | Flag. Output raw JSON lines to STDOUT. |

**Behaviour:**

- Case-insensitive substring search on the message field.
- If `--tag` filters are also provided, both conditions must be satisfied (AND).
- Results are sorted newest first.
- Matched portion of the message is highlighted in bold yellow in the terminal output.

**Success Output (STDOUT):**  
Same Rich table format as `list`, with the matching substring highlighted in the Message column.  
Empty results: Print `No entries matched "<query>".` Exit 0.

**Failure cases:** Same as `list`.

---

### Command 4 — `export`

**Purpose:** Export all entries (or a filtered subset) to a Markdown file.  
**Usage:**  
`devlog export [OPTIONS]`

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--output` | `-o` | path string | `./devlog-export.md` | Output file path. |
| `--tag` | `-t` | string, multiple | None | Export only entries matching all specified tags. |
| `--quiet` | `-q` | flag | False | Suppress progress output. Print only the output path on success. |

**Behaviour:**

- Writes a Markdown file. Each entry becomes an H2 heading (date + short ID), followed by the message body, followed by a Tags line.
- Uses a Rich progress bar while writing (suppressible with `--quiet`).
- If the output file already exists, it is overwritten without prompting (by design — keep it simple).

**Markdown format per entry:**

```markdown
## 2025-05-11 10:22 UTC — a1b2c3d4

Fixed the null pointer issue in the auth module.

**Tags:** backend, auth

---
```

**Success Output (STDERR):**  
```
✔ Exported 42 entries to ./devlog-export.md
```

**Failure cases:**

| Condition | Output | Exit Code |
|-----------|--------|-----------|
| Output path unwritable | STDERR: `Error: Cannot write to <path>. Check the path and permissions.` | 2 |
| No entries exist | STDERR: `Warning: No entries to export.` Exit 0. | 0 |

---

## C. Data Schema

### Entry Object

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "message": "Fixed the null pointer issue in the auth module",
  "tags": ["backend", "auth"],
  "created_at": "2025-05-11T10:22:00Z"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string (UUID4) | Yes | Unique identifier. Generated via `uuid.uuid4()`. |
| `message` | string | Yes | The journal entry body. No length limit enforced, but display truncates at 60 chars. |
| `tags` | array of strings | Yes (can be empty array) | Normalized lowercase tags. |
| `created_at` | string | Yes | UTC timestamp in ISO 8601 format: `YYYY-MM-DDTHH:MM:SSZ`. |

### Storage File Structure

```json
{
  "entries": [
    { "id": "...", "message": "...", "tags": [], "created_at": "..." }
  ]
}
```
Top-level key is `"entries"`, value is an array of entry objects. New entries are appended to the end of the array.

### File Path Convention

- Default path: `~/.devlog/entries.json`
- Override via environment variable: `DEVLOG_DATA_DIR`. If set, the file lives at `$DEVLOG_DATA_DIR/entries.json`.
- The directory is created automatically on first run if it does not exist.

### Tag Constraints

| Constraint | Rule |
|------------|------|
| Allowed characters | `[a-z0-9\-]` only, after normalization |
| Case sensitivity | Always stored and compared lowercase |
| Max tags per entry | 10 |
| Max tag length | 32 characters |
| Duplicates | Silently deduplicated before storage |

---

## D. Tag System Design

- Tags are free-form strings. There is no predefined category list. The help text for `--tag` suggests common categories: `feature`, `bugfix`, `refactor`, `docs`, `devops`, `learning`.
- Tags are stored as a JSON array of lowercase strings within each entry object.
- Filtering logic: Multiple `--tag` flags use AND logic. An entry must carry every specified tag to appear in results. OR logic is not supported in v1.
- Invalid characters: If a tag contains characters outside `[a-z0-9\-]` (after lowercase normalization), the command exits with code 1 and a clear error message. No partial entry is written.
- Duplicates: If the same tag is specified twice in one `add` command, the duplicate is silently removed before storage. The user is not warned.

---

## E. Edge Cases & Error Handling

| Scenario | Required Behaviour |
|----------|-------------------|
| Empty state | `list`, `search`, `export` all exit 0 with a clear human-readable message. No traceback, no crash. |
| Empty message | `add ""` exits with code 1. STDERR: `Error: MESSAGE cannot be empty.` |
| Duplicate tags | Silently deduplicated. No warning shown. |
| Malformed JSON | Load function wraps `json.load()` in `try/except json.JSONDecodeError`. Exits code 2 with: `Error: Storage file is corrupted at <path>. Back it up and delete it to reset, or restore from backup.` No traceback exposed to user. |
| File permissions | Wrap all file open calls. On `PermissionError`, exit code 2 with path-specific message. |
| Concurrent writes | Use atomic write pattern: write to `entries.json.tmp`, then `os.replace()` to swap. Prevents partial writes. Does not implement full locking — acceptable for a personal tool. |
| Large datasets (10k+ entries) | `list` and `search` default to `--limit 20`. `--all` is explicit opt-in. No pagination UI in v1 — limit is the mechanism. |
| Special characters / emoji | JSON handles UTF-8 natively. `json.dumps()` must use `ensure_ascii=False`. Rich handles Unicode display. |
| Unwritable export path | Catch `PermissionError` and `OSError`. Exit code 2 with clear message. |
| Storage directory missing | Auto-create with `Path.mkdir(parents=True, exist_ok=True)` on first run. |

---

## F. Non-Functional Requirements

| Requirement | Specification |
|-------------|---------------|
| Performance | `list` and `search` must feel instant for up to 1,000 entries. All operations are in-memory after a single file read — no query optimisation needed at this scale. |
| Python version | 3.9+ |
| Dependencies | `click>=8.0`, `rich>=13.0`. No other third-party dependencies. `uuid`, `json`, `os`, `pathlib`, `tempfile`, `datetime` are all stdlib. |
| Package structure | Single package named `devlog` with `cli.py` as the entry point. Installable via `pip install .`. Entry point defined in `pyproject.toml` as `devlog = "devlog.cli:main"`. |
| Testing | `pytest` with Click's `CliRunner`. Every command needs: one happy-path test, one empty-state test, one error-condition test. Tests live in `tests/`. |

### Folder Layout

```
devlog-cli/
├── devlog/
│   ├── __init__.py
│   ├── cli.py          ← Click entry point, all command definitions
│   ├── storage.py      ← All file I/O, JSON read/write, atomic write logic
│   └── models.py       ← Entry dataclass or TypedDict definition
├── tests/
│   ├── test_add.py
│   ├── test_list.py
│   ├── test_search.py
│   └── test_export.py
├── pyproject.toml
└── README.md
```

---

## G. UX & Terminal Output Specification

### Color coding (consistent across all commands):

| Element | Color |
|---------|-------|
| Success messages | Green |
| Warnings | Yellow |
| Errors | Red (on STDERR) |
| Dates / timestamps | Cyan |
| Tags | Magenta |
| Entry IDs | Dim white |
| Highlighted search matches | Bold yellow |

### `devlog add` success output:

Rich panel with green border, checkmark, entry details laid out with labels.

### `devlog list` output:

Rich table. Columns: ID (8-char truncated UUID), Date (cyan), Tags (magenta, comma-separated), Message (truncated at 60 chars with `…`). Footer line: `Showing N of M entries.`

### `devlog search` output:

Same table as `list`. Matching substring in the Message column is wrapped in Rich's `[bold yellow]...[/bold yellow]` markup.

### `devlog export` output:

Rich progress bar on STDERR during write. On completion: green checkmark + output file path. Suppressible with `--quiet`.

### 80-character compatibility:

All table layouts must be tested at 80-char width. No column may force horizontal scroll at this width. Truncation is preferred over wrapping.

---

## H. README Outline Specification

Claude C must produce a `README.md` containing the following sections in this order:

1. Title + one-line description
2. Terminal screenshot or ASCII demo — showing `add`, `list`, and `search` in action
3. Installation — `pip install .` and `pipx install .` variants
4. Quick Start — 5–8 lines showing the core daily workflow from first install to first export
5. Command Reference — Full table for every command with every option, argument, type, default, and description. One section per command.
6. Configuration — Document `DEVLOG_DATA_DIR` environment variable. Show example usage.
7. Development Setup — Clone, create venv, install in editable mode (`pip install -e .`), run tests (`pytest`)
8. License — MIT