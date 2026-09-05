# devlog-cli — Design Notes (Historical)

> **Status:** Historical artifact. This document captured the v1.0
> build contract. The current, authoritative command reference lives
> in [`README.md`](README.md). Sections kept here (Edge Cases,
> Non-Functional Requirements, UX Specification, "What's New in 1.5")
> are design rationale and contract notes that don't fit cleanly
> into a user-facing manual; per-command details, schema, tag
> constraints, and the project layout have moved to README.

**Version:** 1.0
**Author:** Senior Planner
**Status:** Final — ready for Claude B implementation

---

## A. Executive Summary

`devlog-cli` is a terminal-based developer journal tool that lets a
solo developer log, search, filter, and export daily progress entries
from the command line without opening a browser, editor, or external
app. Entries are stored locally as a JSON flat file, require no
account or internet connection, and are accessible instantly from any
terminal session. The tool is designed around the four core commands
`add`, `list`, `search`, and `export` (and a growing family of
siblings — `show`, `edit`, `delete`, `tags`, `today`, `week`,
`stats`, `calendar`, `tag`, `merge-tag`, `rename-tag`, `theme`,
`backup`, `restore`, `repair`, `doctor`, `completions`, `import`,
`export`, `--interactive`) and prioritises speed, clarity, and
zero-friction daily use. It solves the problem of developers losing
track of what they worked on by making the act of logging a single
fast command rather than a context-switch to another application.

---

## E. Edge Cases & Error Handling

| Scenario | Required Behaviour |
|----------|-------------------|
| Empty state | `list`, `search`, `export` all exit 0 with a clear human-readable message. No traceback, no crash. |
| Empty message | `add ""` exits with code 1. STDERR: `Error: MESSAGE cannot be empty.` |
| Duplicate tags | Silently deduplicated. No warning shown. |
| Malformed JSON | Load function wraps `json.load()` in `try/except json.JSONDecodeError`. Exits code 2 with: `Error: Storage file is corrupted at <path>. …` No traceback exposed to user. |
| File permissions | Wrap all file open calls. On `PermissionError`, exit code 2 with path-specific message. |
| Concurrent writes | Use atomic write pattern: write to `entries.json.tmp`, then `os.replace()` to swap. Prevents partial writes. Does not implement full locking — acceptable for a personal tool. |
| Large datasets (10k+ entries) | `list` and `search` default to `--limit 20`. `--all` is explicit opt-in. No pagination UI in v1 — limit is the mechanism. |
| Special characters / emoji | JSON handles UTF-8 natively. `json.dumps()` must use `ensure_ascii=False`. Rich handles Unicode display. |
| Unwritable export path | Catch `PermissionError` and `OSError`. Exit code 2 with clear message. |
| Storage directory missing | Auto-create with `Path.mkdir(parents=True, exist_ok=True)` on first run. |
| Half-written `entries.json` (e.g. power loss mid-flush) | `devlog repair` walks the file from the end, right-truncates to the last valid JSON object, and rewrites. Backup is taken first; the original (corrupt) bytes are preserved in `backups/`. |

---

## F. Non-Functional Requirements

| Requirement | Specification |
|-------------|---------------|
| Performance | `list` and `search` must feel instant for up to 1,000 entries. All operations are in-memory after a single file read — no query optimisation needed at this scale. |
| Python version | 3.9+ |
| Dependencies | `click>=8.0`, `rich>=13.0`, `tomli>=1.1; python_version < '3.11'`, `tzdata`. No other third-party dependencies. `uuid`, `json`, `os`, `pathlib`, `tempfile`, `datetime` are all stdlib. |
| Package structure | Single package named `devlog` with `cli.py` as the entry point. Installable via `pip install .` or `pipx install .`. Entry point defined in `pyproject.toml` as `devlog = "devlog.cli:main"`. |
| Testing | `pytest` with Click's `CliRunner`. Every command needs: one happy-path test, one empty-state test, one error-condition test. Tests live in `tests/`. Performance benchmarks tagged `@pytest.mark.benchmark` are skipped by default and run with `pytest -m benchmark`. |

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

All colors above are configurable via `devlog theme` and the
`theme.toml` file. Box styles, icons, and layout are intentionally not
themable.

### Icon set:

| Icon | Meaning | Where used |
|------|---------|------------|
| ✔ | Success | `add` confirmation, `edit` confirmation, `export` completion |
| ✘ | Error / destructive | error panels, `delete` confirmation |
| ⚠ | Warning | empty-state warnings (e.g. `export` with no entries) |
| ℹ | Informational / empty | "No entries found", "No entries match your filters" |
| ✎ | Edit | `edit` confirmation title |

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

## I. What's New in 1.5

This drop is **additive and non-breaking** — every v1.4 invocation
behaves identically when the new env vars and options are not used.
On-disk format and the public CLI surface remain backwards compatible.

### Local timezone (`DEVLOG_TZ`)

- New env var. IANA name (`America/New_York`, `Europe/Berlin`, …).
  Validated via `zoneinfo`; the `tzdata` package provides IANA data
  on Windows and as a fallback.
- When set, `today` / `yesterday` / `week` / `stats` / `calendar`
  bucket by local date. `--since` / `--until` and `Nd` / `Nw` are
  interpreted at local midnight. `stats` renders `First` / `Last` with
  the zone's key suffix.
- On-disk `created_at` remains UTC.
- Bad zone name → red error and exit 1 (no silent fallback).

### New commands

- `devlog yesterday` — yesterday's entries, local-tz bucketed.
- `devlog week [--day YYYY-MM-DD]` — last 7 days, anchored on the
  supplied day or today.
- `devlog tag <name> [--delete]` — per-tag page (show every entry
  with the tag) or strip the tag from every entry. New
  `--delete --dry-run` previews the change.
- `devlog merge-tag OLD NEW` — bulk merge of two tags. `OLD` is
  removed from every entry that has it; `NEW` is added (deduplicated).
  Skip-count reported in the summary.
- `devlog calendar [--year YYYY]` — year-grid heatmap (53 weeks × 7
  days) of entry counts, using new `heatmap_*` theme roles.
- `devlog repair / backup / restore / doctor` — store-health toolkit.
- `devlog theme set / use / builtins / reset / edit / diff / export /
  create` — full theme workflow.
- `devlog completions {bash,zsh,fish}` — shell completion scripts
  driven by the `COMMANDS` table in `cli.py`.

### `add` / `edit` `--at`

- New `--at` option on `add` and `edit`. Accepts absolute
  (`YYYY-MM-DD`, `YYYY-MM-DDTHH:MM`, `…Z`) and relative (`Nh`, `Nm`
  ago) timestamps. With `DEVLOG_TZ` set, naive inputs are
  interpreted in that zone.
- `edit --at` prompts for confirmation unless `--yes` is also passed.
  The `--at` change counts as a real edit (a no-op `No changes.` is
  not printed when only the timestamp changes).

### Other

- New `tzdata` runtime dependency.
- New theme roles: `heatmap_empty`, `heatmap_l1`, `heatmap_l2`,
  `heatmap_l3`, `heatmap_l4`. Defaults are graded greens
  (`grey15` / `green` / `color(34)` / `color(40)` / `color(46)`).
- `devlog` root banner now lists `yesterday`, `week`, `tag`,
  `merge-tag`, and `calendar`.
- New `theme.toml` / `devlog theme` system: bundled themes
  (`default`, `dracula`, `gruvbox`, `high_contrast`, `monokai`,
  `nord`, `one_dark`, `solarized`) plus an interactive
  `devlog theme create` wizard.
