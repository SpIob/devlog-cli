# devlog-cli

A terminal-based developer journal — log, search, filter, and export daily progress entries without leaving the command line.

---

## Demo

```
$ devlog add "Rewrote auth middleware to use JWT refresh tokens" -t backend -t security
╭─ ✔ Entry added  ·  a1b2c3d4 ─────────────────────────────────────────────────────────────────────╮
│ Date  : 2025-05-11 10:22 UTC                                                                     │
│ Tags  : backend, security                                                                        │
│ Note  : Rewrote auth middleware to use JWT refresh tokens                                        │
│                                                                                                  │
│ Run `devlog list` to see all entries.                                                            │
╰──────────────────────────────────────────────────────────────────────────────────────────────────╯

$ devlog list
                          Journal · 3 entries
╭──────────┬───────────────────────┬──────────────────────┬────────────────────────────────────────╮
│ ID       │ Date                  │ Tags                 │ Message                                │
├──────────┼───────────────────────┼──────────────────────┼────────────────────────────────────────┤
│ c3d4e5f6 │ 2025-05-11 14:30 UTC  │ bugfix, devops       │ Fixed flaky test in CI pipeline        │
│ b2c3d4e5 │ 2025-05-11 11:05 UTC  │ architecture, docs   │ Drafted ADR for new caching layer      │
│ a1b2c3d4 │ 2025-05-11 10:22 UTC  │ backend, security    │ Rewrote auth middleware to use JWT     │
│          │                       │                      │ refresh tokens                         │
├──────────┼───────────────────────┼──────────────────────┼────────────────────────────────────────┤
│          │                       │                      │ Showing 3 of 3 entries.                │
╰──────────┴───────────────────────┴──────────────────────┴────────────────────────────────────────╯

$ devlog search "auth"
                           Journal · 1 match
                          Query: "auth"
╭──────────┬───────────────────────┬──────────────────────┬────────────────────────────────────────╮
│ ID       │ Date                  │ Tags                 │ Message                                │
├──────────┼───────────────────────┼──────────────────────┼────────────────────────────────────────┤
│ a1b2c3d4 │ 2025-05-11 10:22 UTC  │ backend, security    │ Rewrote auth middleware to use JWT     │
│          │                       │                      │ refresh token…                         │
├──────────┼───────────────────────┼──────────────────────┼────────────────────────────────────────┤
│          │                       │                      │ Showing 1 of 1 entry.                  │
╰──────────┴───────────────────────┴──────────────────────┴────────────────────────────────────────╯
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

# Show the full text of a single entry by ID
devlog show a1b2c3d4

# Edit a typo in an entry's message
devlog edit a1b2c3d4 -m "Corrected message text"

# Delete an entry (prompts for confirmation; use -y to skip)
devlog delete a1b2c3d4 -y

# See which tags you use most
devlog tags

# See what you logged today
devlog today

# See the 10 most recent entries
devlog tail 10

# Get a quick journal summary with a 30-day sparkline
devlog stats

# Rename a tag across every entry
devlog rename-tag backend devops

# Customize the colors (optional)
devlog theme show > ~/.devlog/theme.toml
$EDITOR ~/.devlog/theme.toml
devlog theme set ~/.devlog/theme.toml

# Import entries from an exported Markdown file
devlog import ~/notes/old-log.md

# Launch the interactive REPL
devlog --interactive

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
| `--since` | — | string | None | Only show entries on or after this date. See [Date filters](#date-filters). |
| `--until` | — | string | None | Only show entries on or before this date. See [Date filters](#date-filters). |
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
devlog list --since 2025-01-01 --until 2025-12-31
devlog list --since 7d
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
| `--since` | — | string | No | None | Only show entries on or after this date. See [Date filters](#date-filters). |
| `--until` | — | string | No | None | Only show entries on or before this date. See [Date filters](#date-filters). |
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
devlog search "refactor" --since 7d
devlog search "refactor" --quiet | jq '.'
```

---

### `devlog show`

Display a single entry in full detail (no message truncation).

```
devlog show ID
```

| Argument / Option | Short | Type | Required | Default | Description |
|-------------------|-------|------|----------|---------|-------------|
| `ID` | — | string | Yes | — | The 8-char short id, the full UUID, or a unique prefix of either. |
| `--quiet` | `-q` | flag | No | False | Output a single raw JSON line instead of the panel. |

**Notes:**

- If the prefix matches more than one entry, the command exits with code 1 and lists the candidate short ids.
- The full message is shown (no 60-char truncation).
- `Updated` displays `—` for entries that have never been edited, otherwise the timestamp of the most recent edit.

**Examples:**

```bash
devlog show a1b2c3d4
devlog show a1b2
devlog show a1b2c3d4-e5f6-7890-abcd-ef1234567890 --quiet
```

---

### `devlog edit`

Edit an entry's message and/or tags in place. Preserves the original `created_at` and sets `updated_at` on success.

```
devlog edit ID [OPTIONS]
```

| Argument / Option | Short | Type | Required | Default | Description |
|-------------------|-------|------|----------|---------|-------------|
| `ID` | — | string | Yes | — | The 8-char short id, the full UUID, or a unique prefix. |
| `--message` | `-m` | string | No | — | Replace the message text. |
| `--tag` | `-t` | string, repeatable | No | — | Replace the tag set with this list. Existing tags are discarded. |
| `--add-tag` | — | string, repeatable | No | — | Append tags to the existing set. Duplicates are silently dropped. |
| `--remove-tag` | — | string, repeatable | No | — | Remove tags from the existing set. |
| `--quiet` | `-q` | flag | No | False | Suppress confirmation output. |

**Notes:**

- If no flag is passed, the entry's current message is opened in `$VISUAL` / `$EDITOR` (falling back to `nano` or `vi` if neither is set). Save and exit to apply the edit; abort to discard. This requires a TTY; in a non-interactive shell (CI, scripts, output captured to a file), `edit` with no flags errors out instead of dumping terminal escapes. Set `DEVLOG_ALLOW_EDITOR_IN_PIPE=1` to opt in to the old behaviour.
- An empty `--message` is rejected with the same `MESSAGE cannot be empty` error as `devlog add`. The editor path is allowed to produce an empty body, since the user may want to save a blank note on purpose.
- No-op edits (the result equals the existing entry) print `No changes.` and exit 0 without touching the file.
- Tag flags combine: `--tag` runs first, then `--add-tag` adds, then `--remove-tag` removes.
- Validation rules for tag characters and length match `devlog add`.

**Examples:**

```bash
devlog edit a1b2c3d4 -m "Corrected typo in the original entry"
devlog edit a1b2c3d4 --add-tag urgent
devlog edit a1b2c3d4 --remove-tag docs --add-tag refactor
devlog edit a1b2c3d4     # opens $EDITOR with the current message
```

---

### `devlog delete`

Delete an entry by id.

```
devlog delete ID [OPTIONS]
```

| Argument / Option | Short | Type | Required | Default | Description |
|-------------------|-------|------|----------|---------|-------------|
| `ID` | — | string | Yes | — | The 8-char short id, the full UUID, or a unique prefix. |
| `--yes` | `-y` | flag | No | False | Skip the confirmation prompt. |
| `--quiet` | `-q` | flag | No | False | Suppress the success panel. |

**Notes:**

- By default, the command asks for confirmation (`[y/N]`). Decline to abort without changes; confirm to delete and render a red-bordered panel.
- Deletion is atomic — the JSON file is rewritten via the same temp-file + `os.replace()` pattern as every other write.

**Examples:**

```bash
devlog delete a1b2c3d4       # confirm interactively
devlog delete a1b2c3d4 -y    # skip prompt
devlog delete a1b2 -y        # delete by unique prefix
```

---

### `devlog tags`

List all distinct tags with their usage count and the most recent date they were used.

```
devlog tags [OPTIONS]
```

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--sort` | — | choice | `count` | One of `count` (most-used first), `name` (alphabetical), or `recent` (most recently used first). |
| `--limit` | `-n` | integer | 50 | Maximum number of tags to show. |
| `--all` | — | flag | False | Override `--limit` and show every tag. |
| `--quiet` | `-q` | flag | False | Output `{tag, count, last_used}` JSON lines instead of the table. |

**Notes:**

- `Last used` is the more recent of the entry's `created_at` and `updated_at`, so editing a tag keeps its timestamp fresh.
- Empty state: prints `No tags found.` and exits 0.

**Examples:**

```bash
devlog tags
devlog tags --sort name
devlog tags --limit 10
devlog tags --quiet | jq '.tag'
```

---

### `devlog theme`

View or change the active color theme. Themes live in `~/.devlog/theme.toml` (or `$DEVLOG_DATA_DIR/theme.toml` when set) and override individual color roles. See the [Themes](#themes) section for the full list of roles and a starter file.

```
devlog theme SUBCOMMAND
```

| Subcommand | Description |
|------------|-------------|
| `devlog theme list` | Print every role and its current style as a two-column table. |
| `devlog theme show [ROLE]` | Print the value of a single role, or dump a starter `theme.toml` (all roles commented out) to STDOUT. |
| `devlog theme set PATH` | Validate and install a theme file as the active theme. Unknown roles are dropped with a warning. |
| `devlog theme path` | Print the absolute path to the active theme file. |

**Examples:**

```bash
# See every color role
devlog theme list

# Read a single role's value
devlog theme show date
# → cyan

# Generate a starter theme file you can edit
devlog theme show > ~/my-theme.toml
$EDITOR ~/my-theme.toml   # uncomment and tweak the roles you want to change
devlog theme set ~/my-theme.toml

# Find the active theme path (useful for `ln -s` workflows)
devlog theme path
# → /Users/you/.devlog/theme.toml
```

**Notes:**

- A missing or malformed `theme.toml` is non-fatal: devlog warns once on STDERR and falls back to the built-in default palette.
- Unknown role keys in the file are dropped with a per-key warning, not a hard error.
- Changes take effect on the next `devlog` invocation.

---

### `devlog today`

Show entries created today (UTC), newest first.

```
devlog today [OPTIONS]
```

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--limit` | `-n` | integer | 50 | Maximum entries to show. |
| `--quiet` | `-q` | flag | False | Output raw JSON lines instead of a table. |

**Notes:**

- "Today" is computed in UTC. If you log entries late in your local timezone, they may appear in a different day's bucket.
- Empty state: prints `No entries yet today.` and exits 0.

**Examples:**

```bash
devlog today
devlog today --limit 5
devlog today --quiet
```

---

### `devlog tail`

Show the N most recent entries (default 5), newest first.

```
devlog tail [N] [OPTIONS]
```

| Argument / Option | Short | Type | Default | Description |
|-------------------|-------|------|---------|-------------|
| `N` | — | integer | 5 | Number of entries to show. |
| `--tag` | `-t` | string, repeatable | None | Filter by tag (AND logic). |
| `--quiet` | `-q` | flag | False | Output raw JSON lines. |

**Examples:**

```bash
devlog tail        # last 5
devlog tail 20
devlog tail 10 -t bugfix
```

---

### `devlog stats`

Show a one-glance summary of the journal: totals, date range, top tags, and a 30-day sparkline of entries per day.

```
devlog stats [OPTIONS]
```

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--quiet` | `-q` | flag | False | Output a single JSON object with the summary. |
| `--since` | — | string | None | Only include entries on or after this date. See [Date filters](#date-filters). |
| `--until` | — | string | None | Only include entries on or before this date. See [Date filters](#date-filters). |

**Notes:**

- The sparkline is an ASCII bar (`▁`–`█`) representing entries per day for the last 30 UTC days.
- Empty state: prints `No entries to summarize.` and exits 0.

**Example output:**

```
╭─ Journal Stats ─────────────────────────────────────────╮
│                                                       │
│  Total     : 142                                      │
│  First     : 2025-04-12 09:14 UTC                     │
│  Last      : 2026-08-29 11:02 UTC                     │
│  Span      : 139 days                                 │
│  Avg/day   : 1.02                                     │
│                                                       │
│  Top 5 tags                                           │
│    backend : 41                                       │
│    bugfix  : 28                                       │
│    docs    : 17                                       │
│    devops  : 12                                       │
│    refactor: 9                                        │
│                                                       │
│  Last 30 days (each ▏ = 1 entry)                      │
│  ▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁█▁▁█▁█                     │
╰───────────────────────────────────────────────────────╯
```

**Examples:**

```bash
devlog stats
devlog stats --quiet | jq '.total, .top_tags'
```

---

### `devlog rename-tag`

Rename a tag across every entry in the journal (bulk replace).

```
devlog rename-tag OLD NEW [OPTIONS]
```

| Argument / Option | Short | Type | Required | Default | Description |
|-------------------|-------|------|----------|---------|-------------|
| `OLD` | — | string | Yes | — | The existing tag to replace. |
| `NEW` | — | string | Yes | — | The new tag name. Validated with the same rules as `devlog add`. |
| `--dry-run` | — | flag | No | False | Show the count of affected entries without writing. |
| `--quiet` | `-q` | flag | No | False | Suppress the success line. |

**Notes:**

- Every entry that contains `OLD` is rewritten in place. The entry's `created_at` is preserved and `updated_at` is set to the current UTC time.
- If an entry already carries `NEW`, the result is deduplicated so no entry ends up with the same tag twice.
- If no entries carry `OLD`, prints `No entries with tag "<old>".` and exits 0.
- `NEW` is validated against the same character/length rules as `devlog add` and rejected with a clear error if it would be invalid. (Older versions used to silently no-op when the lowercased NEW happened to equal OLD — that path is fixed.)

**Examples:**

```bash
devlog rename-tag backend devops
devlog rename-tag bugfix fix --dry-run
```

---

### `devlog import`

Import entries from a JSON file (the native `entries.json` shape) or from a Markdown file produced by `devlog export`.

```
devlog import PATH [OPTIONS]
```

| Argument / Option | Short | Type | Required | Default | Description |
|-------------------|-------|------|----------|---------|-------------|
| `PATH` | — | path | Yes | — | Input file. Must exist and be readable. |
| `--format` | `-f` | choice | `auto` | One of `auto`, `json`, `markdown`. `auto` picks by file extension. |
| `--dry-run` | — | flag | False | Show what would be imported without writing. |
| `--quiet` | `-q` | flag | False | Suppress the summary line. |

**Notes:**

- Re-importing the same file is safe and idempotent: entries are recognised by their existing `id` (when present in the source) or by their `(created_at, message)` fingerprint, so the second import is a no-op.
- Stable `id` values in the source JSON are preserved across re-imports, so a `devlog export` → `devlog import` round-trip keeps short ids stable for cross-referencing. If the source row omits `id`, devlog mints a fresh one.
- Unreadable rows in a JSON import (non-dict items or entries missing required fields) are reported in the summary line as `Ignored N unreadable rows.`
- Auto-detect sniffs the first non-blank character when the file extension is missing, so pipes and extensionless files work: leading `{` → JSON, leading `#` → Markdown.
- If the file format cannot be detected and `--format` was not given, exits with code 2.
- Malformed JSON or markdown that contains no `## …` headings exits with code 2.

**Examples:**

```bash
devlog import ~/notes/old-log.md
devlog import /tmp/entries.json --format json
devlog import /tmp/entries.json --dry-run
```

---

### `devlog completions`

Print a shell completion script to STDOUT. Source or install it according to your shell's conventions.

```
devlog completions {bash|zsh|fish}
```

**Examples:**

```bash
# bash
devlog completions bash > ~/.bash_completion.d/devlog

# zsh (place in a directory on $fpath)
devlog completions zsh > ~/.zsh/completions/_devlog

# fish
devlog completions fish > ~/.config/fish/completions/devlog.fish
```

---

### `devlog --interactive`

Launch a minimal line-based REPL for browsing and adding entries without leaving the prompt.

```
devlog --interactive
devlog -- -i
DEVLOG_INTERACTIVE=1 devlog
```

**REPL commands** (run `help` at the prompt to see the up-to-date list — every command in the CLI is available, plus a few aliases):

| Command | What it does |
|---------|--------------|
| `add <message> [-t tag ...]` | Add a new entry. |
| `l \| list [-t tag] [-n N]` | List entries. |
| `s \| search <query>` | Search entry messages. |
| `show <id>` | Show a single entry. |
| `edit <id> [-m msg] [-t tag]` | Edit an entry. |
| `delete <id> [-y]` | Delete an entry. |
| `today` | Show today's entries. |
| `tail [N]` | Show the N most recent entries. |
| `tags` | List tags with usage counts. |
| `stats` | Summarize the journal. |
| `theme` | View or change the active color theme (sub: `list`, `show`, `set`, `path`). |
| `rename-tag <old> <new>` | Rename a tag. |
| `import <path>` | Import entries. |
| `completions <shell>` | Print a shell completion script. |
| `export [-o path]` | Export to Markdown or JSON. |
| `repair [-y] [--dry-run]` | Inspect and repair the on-disk store. |
| `backup [-o path]` | Write a timestamped backup. |
| `restore <path>` | Restore from a backup file. |
| `doctor` | Check store health. |
| `h \| help` | Show the REPL help. |
| `q \| quit \| exit` | Leave the REPL. |

**Notes:**

- Requires a TTY. Outside a TTY the command exits with code 1 and a clear error message. Set `DEVLOG_INTERACTIVE_FORCE=1` to bypass the TTY check (useful in tests, scripts, or remote shells).
- Any other subcommand argument is dispatched to the underlying CLI in-process.

**Examples:**

```bash
devlog --interactive
DEVLOG_INTERACTIVE=1 devlog
```

---

### `devlog export`

Export all entries (or a filtered subset) to a Markdown or JSON file.

```
devlog export [OPTIONS]
```

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--output` | `-o` | path | `<data-dir>/exports/devlog-YYYYMMDD-HHMMSS.<ext>` | Destination file path. Existing files are overwritten without prompting. When omitted, files land in `$DEVLOG_DATA_DIR/exports/` (or `~/.devlog/exports/`) rather than the current working directory, so `cd`-ing around doesn't pollute the cwd. |
| `--format` | `-f` | `auto` \| `markdown` \| `json` | `auto` | Output format. `auto` infers from the `--output` extension (`.json` → JSON, `.md`/`.markdown` → Markdown, anything else → Markdown). Explicit `--format` wins over the extension. |
| `--tag` | `-t` | string | None | Export only entries that carry all specified tags. Repeatable; AND logic. |
| `--since` | — | string | None | Only export entries on or after this date. See [Date filters](#date-filters). |
| `--until` | — | string | None | Only export entries on or before this date. See [Date filters](#date-filters). |
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
- If no entries exist (or no entries match the tag filters), prints `Warning: No entries to export.` and exits with code 0. **No file is created in that case**, so you can safely re-run after emptying the journal.
- If the output path is not writable, exits with code 2 and a clear error message.

**Examples:**

```bash
devlog export
devlog export -o ~/notes/week-20.md
devlog export -t backend -o backend-log.md
devlog export --since 2025-01-01 --until 2025-12-31 -o year.md
devlog export --quiet -o /tmp/devlog.md
```

---

### `devlog repair`

Inspect the on-disk journal and, optionally, rewrite it to drop malformed rows. Useful when `devlog doctor` reports validation issues, or when the file has been hand-edited and broken.

```
devlog repair [OPTIONS]
```

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--dry-run` | — | flag | False | Show the issues and what would be dropped, without writing. |
| `--yes` | `-y` | flag | False | Skip the "drop N entries — continue?" confirmation. |
| `--backup` / `--no-backup` | — | flag | `--backup` | Write a timestamped backup to `<data-dir>/backups/` before the rewrite. |
| `--quiet` | `-q` | flag | False | Suppress the summary panel. |

**Notes:**

- A backup is written *before* the file is rewritten, so you can always undo a repair with `devlog restore <backup-file>`.
- Validation rules match the storage contract: every entry must have a non-empty `id` and `message`, a parseable `created_at` (ISO 8601 UTC), and tags that match `^[a-z0-9-]+$` with at most 32 characters.
- Duplicate ids keep the first occurrence; subsequent duplicates are reported and dropped.
- Exit code is `0` when nothing was dropped, `1` when entries were dropped (or when issues were found in dry-run mode? — no, dry-run is always `0`).
- A corrupted JSON file (invalid syntax) is not repairable; the command exits with code 2 and tells you to restore from a backup.

**Examples:**

```bash
# See what's wrong, without writing
devlog repair --dry-run

# Drop invalid rows, writing a backup first
devlog repair -y

# Run the repair non-interactively from a script
devlog repair -y --no-backup
```

---

### `devlog backup`

Write a timestamped copy of the journal. The default destination is `<data-dir>/backups/entries-YYYYMMDD-HHMMSS.json`. The file is a normal `entries.json` — it can be inspected, edited, and round-tripped through `devlog restore`.

```
devlog backup [OPTIONS]
```

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--output` | `-o` | path | `<data-dir>/backups/entries-TIMESTAMP.json` | Backup file path. Parent directories are created if they don't exist. |
| `--quiet` | `-q` | flag | False | Print only the backup path (no success panel). |

**Notes:**

- The backup is just a copy of `entries.json` (the JSON shape that `devlog` reads on every startup). It can be inspected, edited, and used as input to `devlog restore`.
- Backups are placed under `<data-dir>/backups/` by default; that directory is created lazily on first use.
- Empty journals can be backed up too — the resulting file is `{"entries": []}`.

**Examples:**

```bash
# Default: timestamped file under the data dir
devlog backup

# Custom path
devlog backup -o ~/archives/devlog-$(date +%F).json

# Scripting: capture the path, then move the file
devlog backup --quiet
```

---

### `devlog restore`

Replace the current journal with the contents of a backup file. Per-row issues in the backup are skipped with a warning, so a hand-edited or partially-corrupted backup can still be partially restored.

```
devlog restore PATH [OPTIONS]
```

| Argument / Option | Short | Type | Required | Default | Description |
|-------------------|-------|------|----------|---------|-------------|
| `PATH` | — | path | Yes | — | Backup file. Must exist and be readable. |
| `--yes` | `-y` | flag | No | False | Skip the "overwrite current journal?" confirmation. |
| `--dry-run` | — | flag | No | False | Validate the backup and report what would be restored, without writing. |
| `--quiet` | `-q` | flag | No | False | Suppress the success line. |

**Notes:**

- A non-empty journal prompts for confirmation by default; pass `-y` to skip the prompt in scripts.
- If the backup is structurally invalid (root is not a JSON object, or `entries` is not a list), the command refuses to restore and exits with code 2.
- Per-row issues (bad tags, unparseable timestamps, duplicate ids) are reported as warnings and skipped; the rest is restored.
- Restoring from an empty backup empties the journal — use `--dry-run` first if you're unsure.

**Examples:**

```bash
devlog restore ~/archives/devlog-2025-01-15.json
devlog restore backups/entries-20260829-080713.json -y
devlog restore some-backup.json --dry-run
```

---

### `devlog doctor`

Check the journal store for corruption and report a quick health snapshot: file path, size, entry count, days since the most recent entry, validation issues, and the three longest messages.

```
devlog doctor [OPTIONS]
```

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--quiet` | `-q` | flag | False | Output a single JSON object with the report. |

**Notes:**

- Exit code: `0` when the store is clean, `1` when validation issues are found (run `devlog repair` to fix), `2` when the file is corrupt or unwritable.
- "Days since last entry" is computed in UTC.
- The probe writes a one-byte file inside the data dir to confirm write access; it removes the file before exiting.

**Example output:**

```
╭─ 🩺 Doctor  ·  all clear ────────────────────────────────────────────────────╮
│ Path  : /Users/you/.devlog/entries.json                                       │
│ Exists: yes                                                                   │
│ Size  : 4.2 KB                                                                │
│ Writable: yes                                                                 │
│ Entries: 142                                                                  │
│ Last entry: today                                                             │
│                                                                               │
│ ✔ No validation issues.                                                       │
│                                                                               │
│ Longest messages:                                                             │
│   • a1b2c3d4… — 248 chars                                                     │
│   • c3d4e5f6… — 192 chars                                                     │
│   • 9f8e7d6c… — 165 chars                                                     │
╰───────────────────────────────────────────────────────────────────────────────╯
```

**Examples:**

```bash
devlog doctor
devlog doctor --quiet | jq '{ok, entry_count, issues: (.issues | length)}'
```

---

## Date filters

`devlog list`, `devlog search`, `devlog export`, and `devlog stats` all accept `--since` and `--until` flags. Bounds are inclusive: `--until 2025-01-15` includes entries written on January 15th.

| Format | Meaning |
|--------|---------|
| `YYYY-MM-DD` | That date at 00:00 UTC. As an upper bound, includes the whole day. |
| `YYYY-MM-DDTHH:MM` | ISO 8601, treated as UTC if no offset. |
| `YYYY-MM-DDTHH:MM:SSZ` | ISO 8601 with explicit UTC. |
| `YYYY-MM-DD HH:MM` | Space-separated form, same semantics as `T`. |
| `today` | Today at 00:00 UTC. |
| `yesterday` | Yesterday at 00:00 UTC. |
| `Nd` | N days ago at 00:00 UTC (e.g. `7d`, `30d`). |
| `Nw` | N weeks ago at 00:00 UTC (e.g. `1w`, `2w`). |

Both bounds may be combined. Entries with unparseable timestamps are silently dropped when any bound is set, since a date filter is meaningless for them.

**Examples:**

```bash
# Everything logged in 2025
devlog list --since 2025-01-01 --until 2025-12-31

# Last 7 days, as JSON
devlog list --since 7d --quiet

# Stats for Q1
devlog stats --since 2025-01-01 --until 2025-03-31

# Export last month
devlog export --since 30d -o month.md
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

### `DEVLOG_INTERACTIVE_FORCE`

Set to `1` to bypass the TTY check that normally gates `devlog --interactive`. Useful in tests, scripts, or remote shells. The check still applies to `devlog edit <id>` with no flags unless `DEVLOG_ALLOW_EDITOR_IN_PIPE=1` is also set.

### `DEVLOG_ALLOW_EDITOR_IN_PIPE`

Set to `1` to allow `devlog edit <id>` with no flags to spawn `$EDITOR` even when stdin is not a TTY. The default is to error out, since editors like `nano`/`vi` print screen-clear sequences that pollute captured output. Set this when scripting edits via the editor.

### Themes

devlog's colors are themable. A user theme lives at `~/.devlog/theme.toml` (or `$DEVLOG_DATA_DIR/theme.toml` when set) and overrides individual color roles. Roles you omit use the built-in defaults, so the file is always optional.

```bash
# See the path to your active theme file
devlog theme path
# → /Users/you/.devlog/theme.toml

# See every role and its current value
devlog theme list

# See a single role
devlog theme show date
# → cyan

# Dump a starter theme.toml to STDOUT (all roles commented out, safe template)
devlog theme show > my-theme.toml

# Install a theme file
devlog theme set ~/dotfiles/devlog-theme.toml
# → Theme installed at /Users/you/.devlog/theme.toml (17 roles).
```

The file format is standard TOML with one `[palette]` section. Each key is a *role* (the UI element to color) and each value is a [Rich style string](https://rich.readthedocs.io/en/stable/appendix/colors.html) — a named color, a hex code, a 256-color index, or a composite like `"bold yellow"`.

```toml
# ~/.devlog/theme.toml
[palette]
date             = "bright_cyan"   # default: "cyan"
tags             = "white"         # default: "magenta"
success_border   = "bright_green"  # default: "green"
match_highlight  = "bold magenta"  # default: "bold yellow"
id_dim           = "grey50"        # default: "dim white"
```

The full list of roles:

| Role | Used for | Default |
|---|---|---|
| `error_border` | red panel border on errors | `red` |
| `error_text` | red error text and ✘ icon | `red` |
| `warning_text` | yellow warning text and ⚠ icon | `yellow` |
| `info_text` | dim ℹ info line | `dim` |
| `success_border` | green border on `add` success panel | `green` |
| `success_title` | green title + ✔ icon on `add` success | `bold green` |
| `show_border` | cyan border on `show` and stats panels | `cyan` |
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

Notes:

- Box styles, icons, and layout are not themable in this version. The theming layer is intentionally limited to colors.
- A malformed `theme.toml` is non-fatal: devlog prints one warning to STDERR (`Warning: theme file at … is invalid; using default theme.`) and renders with the defaults.
- Unknown role keys are dropped with a warning per key.
- `NO_COLOR=1` and non-TTY output always suppress color regardless of the theme.
- Changes take effect on the next devlog invocation; no daemon or reload needed.

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
│   ├── ui.py           ← Rich rendering helpers (panels, tables, errors)
│   ├── themes.py       ← Theme loader and role contract
│   ├── storage.py      ← File I/O, JSON read/write, atomic write logic
│   └── models.py       ← Entry dataclass / TypedDict definition
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

Tests use Click's `CliRunner` for full end-to-end command testing without touching the real filesystem. Each command has a happy-path test, an empty-state test, and at least one error-condition test.

---

## License

MIT License

Copyright (c) 2025

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.