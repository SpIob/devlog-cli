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
| `--since` |  | string | None | Only show entries on/after this date. See "Date filters" below. |
| `--until` |  | string | None | Only show entries on/before this date. See "Date filters" below. |
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
| `--since` | Option, string, default None. Only show entries on/after this date. |
| `--until` | Option, string, default None. Only show entries on/before this date. |
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

### Command 5 — `show`

**Purpose:** Display a single entry in full detail.  
**Usage:**  
`devlog show ID`

| Element | Detail |
|---------|--------|
| `ID` | Positional, required, string. Exact id, full UUID, or a unique prefix. |
| `--quiet` / `-q` | Flag. Output a single raw JSON line instead of the panel. |

**Behaviour:**

- Loads entries, finds the matching entry by exact id or unique prefix.
- Renders a cyan-bordered Rich panel with the full id, `Date` (cyan), `Updtd` (yellow when set, dim `—` otherwise), `Tags` (magenta), and the full message text (no truncation).

**Failure cases:**

| Condition | Output | Exit Code |
|-----------|--------|-----------|
| Empty / missing id | STDERR: `Error: ID is required.` | 1 |
| No match | STDERR: `Error: No entry found with id "<id>".` | 1 |
| Ambiguous prefix | STDERR: `Error: ID prefix "<id>" matches multiple entries: <list>. Use a longer prefix.` | 1 |
| Storage error | via `_handle_storage_error` | 2 |

---

### Command 6 — `edit`

**Purpose:** Edit an entry's message and/or tags in place.  
**Usage:**  
`devlog edit ID [OPTIONS]`

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--message` | `-m` | string | None | Replace the message. |
| `--tag` | `-t` | string, multiple | None | Replace the tag set with this list. |
| `--add-tag` | — | string, multiple | None | Append tags. |
| `--remove-tag` | — | string, multiple | None | Remove tags. |
| `--quiet` | `-q` | flag | False | Suppress the success panel. |

**Behaviour:**

- If no flag is passed, opens the current message in `$VISUAL` / `$EDITOR` (fallback to `nano`, then `vi`). Read back on save; abort on non-zero exit.
- Tag operations combine: `--tag` runs first (replaces), then `--add-tag` appends (deduplicated), then `--remove-tag` removes.
- New tag values are validated using the same rules as `add`.
- `id` and `created_at` are preserved; `updated_at` is set to the current UTC time on every successful edit.
- If the new state equals the old state, the command prints `No changes.` and exits 0 without writing.

**Failure cases:**

| Condition | Output | Exit Code |
|-----------|--------|-----------|
| Not found / ambiguous | same as `show` | 1 |
| Invalid tag | STDERR: `Error: Tag "..." contains invalid characters.` | 1 |
| Editor exits non-zero | STDERR: `Error: Editor exited abnormally; no changes saved.` | 2 |
| Storage error | via `_handle_storage_error` | 2 |
| No editor configured and no flags | STDERR: `Error: No editor configured. Set $VISUAL or $EDITOR, or use --message / --tag flags.` | 1 |

---

### Command 7 — `delete`

**Purpose:** Delete an entry by id.  
**Usage:**  
`devlog delete ID [OPTIONS]`

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--yes` | `-y` | flag | False | Skip the confirmation prompt. |
| `--quiet` | `-q` | flag | False | Suppress the red success panel. |

**Behaviour:**

- By default, prompts with `Delete entry XXXXXXXX ("message snippet")? [y/N]`. Decline to abort (prints `Aborted.`, exit 0). Confirm to delete.
- Renders a red-bordered panel with the deleted entry's id, date, tags, and struck-through message.
- Uses the same atomic JSON write as every other write.

**Failure cases:** same as `show`.

---

### Command 8 — `tags`

**Purpose:** List all distinct tags with their usage count and last-used date.  
**Usage:**  
`devlog tags [OPTIONS]`

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--sort` | — | choice | `count` | One of `count`, `name`, `recent`. |
| `--limit` | `-n` | integer | 50 | Maximum tags to show. |
| `--all` | — | flag | False | Override `--limit`. |
| `--quiet` | `-q` | flag | False | Output `{tag, count, last_used}` JSON lines. |

**Behaviour:**

- Aggregates all entries. For each tag, counts occurrences and tracks the most recent timestamp as `max(created_at, updated_at)`.
- Sorts by `--sort` (default: count descending, then tag name ascending).
- Footer: `Across N entries.`

**Failure cases:**

| Condition | Output | Exit Code |
|-----------|--------|-----------|
| `--limit` not positive | STDERR: `Error: --limit must be a positive integer.` | 1 |
| No tags | `No tags found.` | 0 |

---

### Command 8a — `today`

**Purpose:** Show entries created today (UTC), newest first.  
**Usage:**  
`devlog today [OPTIONS]`

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--limit` | `-n` | integer | 50 | Maximum entries to show. |
| `--quiet` | `-q` | flag | False | Output raw JSON lines. |

**Behaviour:** Filters entries whose `created_at` starts with today's UTC date (`YYYY-MM-DD`), sorts newest first, slices to `--limit`. Renders the same Rich table as `list` with a `Today · N entries` title and today's date as subtitle.

**Failure cases:**

| Condition | Output | Exit Code |
|-----------|--------|-----------|
| `--limit` not positive | STDERR: `Error: --limit must be a positive integer.` | 1 |
| No entries today | `No entries yet today.` | 0 |

---

### Command 8b — `tail`

**Purpose:** Show the N most recent entries (default 5), newest first.  
**Usage:**  
`devlog tail [N] [OPTIONS]`

| Argument / Option | Short | Type | Default | Description |
|-------------------|-------|------|---------|-------------|
| `N` | — | integer | 5 | Number of entries to show. |
| `--tag` | `-t` | string, multiple | None | Filter by tag (AND). |
| `--quiet` | `-q` | flag | False | Output raw JSON lines. |

**Behaviour:** Loads entries, applies tag filter (AND), sorts newest first, slices to `N`. Renders the same Rich table as `list` with a `Tail · last N of TOTAL` title.

**Failure cases:**

| Condition | Output | Exit Code |
|-----------|--------|-----------|
| `N` not positive | STDERR: `Error: N must be a positive integer.` | 1 |
| No entries | `No entries found.` | 0 |

---

### Command 8c — `stats`

**Purpose:** Summarize the journal: totals, date range, top 5 tags, last-30-days sparkline.  
**Usage:**  
`devlog stats [OPTIONS]`

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--quiet` | `-q` | flag | False | Output a single JSON summary. |
| `--since` | — | string | None | Only include entries on/after this date. See "Date filters" below. |
| `--until` | — | string | None | Only include entries on/before this date. See "Date filters" below. |

**Behaviour:** Loads all entries, computes:
- `Total` — entry count.
- `First` / `Last` — formatted timestamps of oldest/newest.
- `Span` — number of days from first to last (inclusive, minimum 1).
- `Avg/day` — `total / span_days`.
- `Top 5 tags` — most-used tags by occurrence.
- `Last 30 days` — ASCII sparkline (block characters `▁`–`█`) of entries per day for the last 30 UTC days, oldest left to newest right.

JSON quiet output includes `total`, `first`, `last`, `top_tags` (array of `{tag, count}`), and `last_30_days` (array of `{date, count}`).

**Failure cases:** No entries → `No entries to summarize.` Exit 0.

---

### Command 8d — `rename-tag`

**Purpose:** Rename a tag across every entry in storage.  
**Usage:**  
`devlog rename-tag OLD NEW [OPTIONS]`

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--dry-run` | — | flag | False | Show the count of affected entries without writing. |
| `--quiet` | `-q` | flag | False | Suppress the success line. |

**Behaviour:**

- Validates `NEW` using the same rules as `add` (lowercase, `[a-z0-9-]`, ≤ 32 chars).
- Loads all entries; for each entry containing `OLD`, replaces `OLD` with `NEW` and dedupes.
- Sets `updated_at = now` on every affected entry.
- Persists via the same atomic write as other commands.

**Failure cases:**

| Condition | Output | Exit Code |
|-----------|--------|-----------|
| Invalid `NEW` chars | `Error: Tag "..." contains invalid characters.` | 1 |
| No entries with `OLD` | `No entries with tag "...".` | 0 |
| `OLD == NEW` (after normalization) | `OLD and NEW are the same ("..."). No changes made.` | 0 |
| Storage error | via `_handle_storage_error` | 2 |

---

### Command 8e — `import`

**Purpose:** Import entries from a JSON or Markdown file.  
**Usage:**  
`devlog import PATH [OPTIONS]`

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--format` | `-f` | choice | `auto` | `auto`, `json`, or `markdown`. |
| `--dry-run` | — | flag | False | Show what would be imported without writing. |
| `--quiet` | `-q` | flag | False | Suppress the summary line. |

**Behaviour:**

- `auto` picks `json` for `*.json` and `markdown` for `*.md`/`.markdown`.
- **JSON:** expects the native shape `{"entries": [...]}`. Each entry is re-parsed as an `Entry`; a new `id` is always minted to avoid collisions.
- **Markdown:** parses the `devlog export` format. The heading `## YYYY-MM-DD HH:MM UTC — XXXXXXXX` provides `created_at` (UTC, second precision defaulted to `:00`) and a short id. The body is everything up to the `**Tags:**` line. Tags are split on `,`, lower-cased, stripped. `(none)` / `none` / empty → no tags.
- **Idempotency:** a candidate is skipped if its `id` already exists, or if `(created_at, message)` already exists in storage.

**Failure cases:**

| Condition | Output | Exit Code |
|-----------|--------|-----------|
| `PATH` doesn't exist | Click `Path` error | 2 |
| Auto-detect fails | `Error: Cannot auto-detect format for "<path>". Use --format=json or --format=markdown.` | 2 |
| Malformed JSON | `Error: Invalid JSON in <path>: <reason>.` | 2 |
| Unreadable file | `Error: Cannot read <path>: <reason>.` | 2 |
| Storage error | via `_handle_storage_error` | 2 |

---

### Command 8f — `completions`

**Purpose:** Print a shell completion script to STDOUT.  
**Usage:**  
`devlog completions {bash|zsh|fish}`

**Behaviour:** Prints one of three statically-defined completion scripts. Unknown shell → Click's `UsageError` (exit 2).

---

### Command 8g — `theme` (color theming)

**Purpose:** View or change the active color theme.  
**Usage:**  
`devlog theme {list|show|set|path}`

The active theme is a flat mapping of *role* names (each one a UI element) to Rich style strings. The roles are the single source of truth for every color that the renderer emits; box styles, icons, and layout are intentionally not themable in this version.

**File location:** `~/.devlog/theme.toml`, or `$DEVLOG_DATA_DIR/theme.toml` when set. The file is optional — without it, the built-in default palette is used and no warning is printed.

**Subcommands:**

| Subcommand | Behaviour |
|------------|-----------|
| `devlog theme list` | Print every role and its current style as a two-column Rich table. Exit 0. |
| `devlog theme show [ROLE]` | Without arg: dump a starter `theme.toml` (all roles commented out) to STDOUT. With arg: print the value of that single role. Unknown role → exit 1 with a red error panel. |
| `devlog theme set PATH` | Validate *PATH* as TOML, copy to the active theme path. Unknown roles in the file are dropped with a per-key warning on STDERR. Malformed TOML → exit 1 with a red error panel. |
| `devlog theme path` | Print the absolute path of the active theme file. Exit 0. |

**Role contract (the values users may set):**

| Role | Used for | Default |
|------|----------|---------|
| `error_border` | red border on `print_error` | `red` |
| `error_text` | red error text and ✘ icon | `red` |
| `warning_text` | yellow warning text and ⚠ icon | `yellow` |
| `info_text` | dim ℹ info line | `dim` |
| `success_border` | green border on `add` success | `green` |
| `success_title` | green title + ✔ icon on `add` success | `bold green` |
| `show_border` | cyan border on `show` and stats | `cyan` |
| `delete_border` | red border + ✘ on `delete` | `red` |
| `edit_border` | blue border + ✎ on `edit` | `blue` |
| `date` | cyan date cells in tables and panels | `cyan` |
| `updated` | yellow "Updtd" cells | `yellow` |
| `tags` | magenta tag cells | `magenta` |
| `id_dim` | dim short-id cells | `dim white` |
| `match_highlight` | yellow search-match highlight | `bold yellow` |
| `banner_version` | version number on `--version` | `bold cyan` |
| `banner_command` | command names in root help banner | `bold cyan` |
| `zebra_alt` | alternate-row dim style in `list` | `dim` |

**Style value format:** Any Rich style string is accepted — named colors (`"red"`, `"bright_cyan"`), hex (`"#ff8800"`), 256-color (`"color(208)"`), true-color triples (`"rgb((255,136,0))"`), or composites (`"bold yellow"`).

**File format (TOML):**

```toml
[palette]
date            = "bright_cyan"
tags            = "white"
success_border  = "bright_green"
match_highlight = "bold magenta"
```

**Error handling:**

| Condition | Output | Exit Code |
|-----------|--------|-----------|
| `theme.toml` does not exist | (silent — render with defaults) | 0 |
| `theme.toml` is malformed TOML | STDERR: `Warning: theme file at <path> is invalid (<detail>); using default theme.` Then render with defaults. | 0 |
| Unknown role key in file | STDERR: `Warning: theme role '<key>' is unknown and will be ignored.` (one line per key). The key is dropped; the rest of the file is applied. | 0 |
| `theme show <role>` with bad role | Red error panel: `Unknown role "<role>". Run \`devlog theme list\` to see valid roles.` | 1 |
| `theme set <path>` with bad TOML | Red error panel: `Theme file is invalid TOML: <detail>`. | 1 |

**Precedence rules:**

1. The `NO_COLOR` environment variable (any value) disables color entirely, regardless of theme. (Same rule as in section G. Color disable.)
2. A non-TTY output stream also disables color, regardless of theme.
3. The theme is loaded once per process; the cache is keyed on the path returned by `get_theme_path()`.

---

### Command 8h — `--interactive` (REPL)

**Purpose:** Launch an interactive REPL for browsing and adding entries without leaving the prompt.  
**Usage:**  
`devlog --interactive` or `DEVLOG_INTERACTIVE=1 devlog`

**Behaviour:**

- Requires a TTY. Outside a TTY, exits with code 1 and a clear message. (`DEVLOG_INTERACTIVE_FORCE=1` bypasses this for tests.)
- Reads lines via Rich's `Prompt.ask` until the user types `q`, `quit`, `exit`, or hits `Ctrl-D`/`Ctrl-C`.
- Each non-empty line is split with `shlex` and dispatched to the underlying `main` Click group in-process, so all subcommands work.
- The REPL help screen lists every supported command.

---

### Command 9 — `export`

**Purpose:** Export all entries (or a filtered subset) to a Markdown file.  
**Usage:**  
`devlog export [OPTIONS]`

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--output` | `-o` | path string | `./devlog-export.md` | Output file path. |
| `--tag` | `-t` | string, multiple | None | Export only entries matching all specified tags. |
| `--since` | — | string | None | Only export entries on/after this date. See "Date filters" below. |
| `--until` | — | string | None | Only export entries on/before this date. See "Date filters" below. |
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

### Command 10 — `repair`

**Purpose:** Validate the on-disk journal and rewrite it to drop malformed rows. Useful when `devlog doctor` reports validation issues or when the file has been hand-edited and broken.  
**Usage:**  
`devlog repair [OPTIONS]`

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--dry-run` | — | flag | False | Show issues and what would be dropped, without writing. |
| `--yes` | `-y` | flag | False | Skip the "drop N entries — continue?" confirmation. |
| `--backup` / `--no-backup` | — | flag | `--backup` | Write a timestamped backup to `<data-dir>/backups/` before the rewrite. |
| `--quiet` | `-q` | flag | False | Suppress the summary panel. |

**Behaviour:**

- Validates every entry in `entries.json` against the schema:
  - Required: `id` (non-empty string), `message` (string), `created_at` (parseable ISO 8601 UTC), `tags` (list of valid tag strings).
  - Optional: `updated_at` if present must be a parseable ISO 8601 UTC timestamp.
  - Each tag must match `^[a-z0-9-]+$` and be ≤ 32 chars.
  - All `id` values must be unique.
- Categorises problems as: `bad_root`, `missing_field`, `bad_field`, `bad_item`, `bad_timestamp`, `bad_tag`, `duplicate_id`.
- Builds a *repair plan*: drops unparseable rows, deduplicates ids (first occurrence wins), drops entries with bad tags or bad timestamps.
- Writes a backup (`<data-dir>/backups/entries-YYYYMMDD-HHMMSS.json`) of the original file when `--backup` is set and the rewrite actually happens.
- With `--dry-run`: prints the plan and the issues list. Exit 0.
- Without `--dry-run`: prompts to confirm (unless `-y` is set), then atomically rewrites the file via the same temp-file + `os.replace()` pattern.
- Exit code: `0` if nothing was dropped, `1` if any entries were dropped.
- A corrupted JSON file (invalid syntax) is not repairable: the command prints a clear error pointing to `devlog restore` and exits with code 2.

---

### Command 11 — `backup`

**Purpose:** Write a timestamped copy of the journal for safekeeping.  
**Usage:**  
`devlog backup [OPTIONS]`

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--output` | `-o` | path | `<data-dir>/backups/entries-YYYYMMDD-HHMMSS.json` | Backup file path. Parent dirs are created if missing. |
| `--quiet` | `-q` | flag | False | Print only the backup path (no success panel). |

**Behaviour:**

- The backup is a normal `entries.json` (the same shape the app reads on every startup). It can be inspected, edited, and round-tripped through `devlog restore`.
- Backups default to `<data-dir>/backups/`; that directory is created on first use.
- Empty journals can be backed up — the resulting file is `{"entries": []}`.

---

### Command 12 — `restore`

**Purpose:** Replace the current journal with the contents of a backup file.  
**Usage:**  
`devlog restore PATH [OPTIONS]`

| Argument / Option | Short | Type | Required | Default | Description |
|-------------------|-------|------|----------|---------|-------------|
| `PATH` | — | path | Yes | — | Backup file. Must exist and be readable. |
| `--yes` | `-y` | flag | No | False | Skip the "overwrite current journal?" confirmation. |
| `--dry-run` | — | flag | No | False | Validate the backup without writing. |
| `--quiet` | `-q` | flag | No | False | Suppress the success line. |

**Behaviour:**

- Reads and parses the backup file as JSON.
- If the root is not a JSON object, or `entries` is not a list, the command refuses to restore and exits with code 2.
- Applies the same repair plan as `devlog repair` to the loaded data: drops unparseable rows, deduplicates ids, drops bad tags. Per-row issues are reported as warnings and skipped.
- If the current journal is non-empty, prompts for confirmation unless `--yes` is set.
- Restoring from an empty backup empties the journal.

---

### Command 13 — `doctor`

**Purpose:** Check the journal store for corruption and report a quick health snapshot.  
**Usage:**  
`devlog doctor [OPTIONS]`

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--quiet` | `-q` | flag | False | Output a single JSON health report. |

**Behaviour:** Reports:
- `Path` — absolute path to `entries.json`.
- `Exists` / `Size` / `Writable` — file state.
- `Entries` — count of valid (loadable) entries.
- `Last entry` — days since the most recent entry (UTC), or `—` for an empty store.
- Validation issues (if any) — same categorisation as `devlog repair`.
- Top 3 longest messages by character count.
- A green "all clear" badge when clean, a yellow "attention" badge otherwise.

**Exit codes:**

| Condition | Exit |
|-----------|------|
| Store is clean | 0 |
| Validation issues found (run `devlog repair`) | 1 |
| Corrupt JSON / unwritable | 2 |

---

### Date filters

The `--since` and `--until` options on `list`, `search`, `export`, and `stats` accept the following forms:

| Format | Meaning |
|--------|---------|
| `YYYY-MM-DD` | That date at 00:00 UTC. As an upper bound, includes the whole day (00:00–23:59:59). |
| `YYYY-MM-DDTHH:MM[:SS]` | ISO 8601, treated as UTC if no offset. |
| `YYYY-MM-DDTHH:MM:SSZ` | ISO 8601 with explicit UTC. |
| `YYYY-MM-DD HH:MM[:SS]` | Space-separated form, same semantics as `T`. |
| `today` | Today at 00:00 UTC. |
| `yesterday` | Yesterday at 00:00 UTC. |
| `Nd` | N days ago at 00:00 UTC (e.g. `7d`, `30d`). |
| `Nw` | N weeks ago at 00:00 UTC (e.g. `1w`, `2w`). |

Both bounds are inclusive. Either or both may be combined. Entries whose `created_at` is unparseable are silently dropped when any bound is set.

Errors (unparseable input) surface a clear message via `click.BadParameter` and exit with code 2.

---

## C. Data Schema

### Entry Object

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "message": "Fixed the null pointer issue in the auth module",
  "tags": ["backend", "auth"],
  "created_at": "2025-05-11T10:22:00Z",
  "updated_at": null
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string (UUID4) | Yes | Unique identifier. Generated via `uuid.uuid4()`. |
| `message` | string | Yes | The journal entry body. No length limit enforced, but display truncates at 60 chars. |
| `tags` | array of strings | Yes (can be empty array) | Normalized lowercase tags. |
| `created_at` | string | Yes | UTC timestamp in ISO 8601 format: `YYYY-MM-DDTHH:MM:SSZ`. |
| `updated_at` | string or `null` | No | UTC timestamp in ISO 8601 format. Set by `devlog edit` whenever a successful edit changes the entry. `null` for entries that have never been edited. Backwards compatible — older files without this field load as `null`. |

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
│   ├── ui.py           ← Rich rendering helpers (panels, tables, errors)
│   ├── themes.py       ← Theme loader and role contract
│   ├── storage.py      ← All file I/O, JSON read/write, atomic write logic
│   └── models.py       ← Entry dataclass or TypedDict definition
├── tests/
│   ├── test_add.py
│   ├── test_list.py
│   ├── test_search.py
│   ├── test_export.py
│   ├── test_show.py
│   ├── test_edit.py
│   ├── test_delete.py
│   ├── test_tags.py
│   ├── test_today_tail_stats.py
│   ├── test_rename_import_completions_tui.py
│   ├── test_themes.py
│   ├── test_ui.py
│   ├── test_date_range.py
│   ├── test_repair.py
│   ├── test_backup_restore.py
│   └── test_doctor.py
├── pyproject.toml
└── README.md
```

---

## G. UX & Terminal Output Specification

### Color coding (consistent across all commands):

| Element | Color | Theme role |
|---------|-------|------------|
| Success messages | Green | `success_border` + `success_title` |
| Warnings | Yellow | `warning_text` |
| Errors | Red (on STDERR) | `error_border` + `error_text` |
| Dates / timestamps | Cyan | `date` |
| Updated-at timestamps | Yellow | `updated` |
| Tags | Magenta | `tags` |
| Entry IDs (full) | Dim white | `id_dim` |
| Highlighted search matches | Bold yellow | `match_highlight` |
| Edited-entry accents | Blue | `edit_border` |

All colors above are configurable via `devlog theme` and the `theme.toml` file. See Command 8g for the role contract and full override mechanism. Box styles, icons, and layout are intentionally not themable.

### Icon set:

| Icon | Meaning | Where used |
|------|---------|------------|
| ✔ | Success | `add` confirmation, `edit` confirmation, `export` completion |
| ✘ | Error / destructive | error panels, `delete` confirmation |
| ⚠ | Warning | empty-state warnings (e.g. `export` with no entries) |
| ℹ | Informational / empty | "No entries found", "No entries match your filters" |
| ✎ | Edit | `edit` confirmation title |

### `devlog` (no subcommand) output:

A styled banner with a 2-column command table, replacing the default
Click help block:

```
devlog  ·  a terminal-based developer journal
──────────────────────────────────────────────────────────────────────
  add         Add a new journal entry
  show        Show a single entry by ID
  edit        Edit an entry's message and/or tags
  delete      Delete an entry by ID
  list        List entries, newest first
  search      Search entry messages
  tags        List tags with usage counts
  export      Export entries to a Markdown file

Run `devlog <command> --help` for details on a specific command.
```

### `devlog --version` output:

```
devlog, version 1.0.0
──────────────────────────────────────────────────────────────────────
```

### `devlog add` success output:

Green-bordered Rich panel with a `✔ Entry added` title (short ID in dim).
Aligned rows for Date (cyan), Tags (magenta), Note. Dim italic footer
hint pointing the user to `devlog list`.

### `devlog list` output:

Rich table (rounded box, zebra row striping, bold header) with columns:
ID (8-char UUID prefix, dim white) | Date (cyan) | Tags (magenta, may
wrap) | Message (truncated to 60 chars with `…`). Table has a bold
title (`Journal · N entries`) and a bold footer (`Showing N of M
entries.`). Column widths are computed from the terminal width so the
table fits between 60 and 160 columns; on narrow terminals the Tags
column shrinks before the Message column does.

Empty state: dim `ℹ No entries found.` (or `ℹ No entries match your
filters.` if filters were applied). Exit 0.

### `devlog search` output:

Same table shape as `list`, with two additions:
- Title is `Journal · N match` / `Journal · N matches`.
- Caption (subtitle) is `Query: "<query>"` rendered in dim.
- The Message cell is **smart-truncated** around the first match: if
  the matched substring is within 60 visible characters it is
  highlighted with `[bold yellow]…[/bold yellow]` markup; if it would
  fall past the truncation point, the cell is built as
  `prefix…[bold yellow]match[/bold yellow]…suffix` so the hit is
  always visible.

Empty state: dim `ℹ No entries matched "<query>".`. Exit 0.

### `devlog export` output:

Rich progress bar on STDERR during write (description + bar +
`completed/total` + elapsed). On completion, a green `✔ Exported N
entries to <path>` confirmation line. Suppressible with `--quiet`
(only the path is printed to STDOUT).

### Error output:

Errors are rendered as red-bordered Rich panels titled `Error` with a
bold red `✘` icon and red body text, written to STDERR.

### Color disable:

Color is automatically disabled when:
- The `NO_COLOR` environment variable is set (any value) — see
  https://no-color.org.
- The output stream (STDOUT or STDERR) is not a TTY (e.g. when piped
  to `less`, a file, or another process).

This means scripts and CI logs that capture devlog output will receive
plain text without ANSI escape codes, while interactive terminal use
gets full styling.

### 80-character compatibility:

All table layouts must render without horizontal scroll at 80 columns.
Long messages wrap inside the Message column rather than overflowing
the table. Truncation with `…` is applied within the cell, not at the
column edge, so the table box never widens beyond the terminal.

---

## H. README Outline Specification

Claude C must produce a `README.md` containing the following sections in this order:

1. Title + one-line description
2. Terminal screenshot or ASCII demo — showing `add`, `list`, and `search` in action
3. Installation — `pip install .` and `pipx install .` variants
4. Quick Start — 5–8 lines showing the core daily workflow from first install to first export
5. Command Reference — Full table for every command with every option, argument, type, default, and description. One section per command.
6. Configuration — Document `DEVLOG_DATA_DIR` environment variable. Show example usage. Include the `Themes` subsection covering `theme.toml` location, format, the role list, and the `devlog theme list|show|set|path` subcommands.
7. Development Setup — Clone, create venv, install in editable mode (`pip install -e .`), run tests (`pytest`)
8. License — MIT