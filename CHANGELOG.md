# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.7.0] - 2026-11-02

Tier 2 — data & integrations (SQLite backend, git mode, competitor importers).

### Added

- **SQLite storage backend** — opt-in via `DEVLOG_BACKEND=sqlite`. Stores entries
  in `<data-dir>/entries.db` (WAL journal mode, 5s busy timeout, index on
  `created_at`, schema-version table for future migrations). `add` is a single
  INSERT instead of a full-file rewrite, so write cost no longer grows with
  journal size. All commands work unchanged; switch back by unsetting the var.
- **Cross-process file locking for the JSON backend** — shared locks on reads,
  exclusive locks + `fsync` before the atomic `os.replace` on writes
  (`flock` on POSIX, `msvcrt.locking` on Windows). Concurrent devlog processes
  can no longer clobber each other.
- **Git integration** — new `devlog git` command group:
  `devlog git status` (repo/branch/remote/dirty state, `--json` for scripts),
  `devlog git sync` (`git add -A` + commit of the repo containing the data
  dir), and `devlog git log [--oneline] [-n N]`. Keeps the journal inside an
  existing repo in sync + gives free history; see the README recipe.
- **`devlog add --from-commit <ref>`** — create an entry from a git commit:
  message from the subject line, tags from a conventional-commit scope
  (`feat(auth):` → `auth`) or `[tag]` prefix plus a `commit-<short-hash>` tag,
  `created_at` from the author date (normalised to UTC). Any MESSAGE argument
  becomes a note prefix; `-t` tags merge in front.
- **Competitor importers** — `devlog import --format jrnl|dayone` for jrnl and
  Day One JSON exports (auto-detected from file content when `--format auto`).
  Reuses the existing dedupe path: ids and `(created_at, message)`
  fingerprints are respected, so re-imports are no-ops. jrnl's `starred` flag
  becomes a `starred` tag; Day One uuids and tags are preserved (tags
  lowercased, spaces → hyphens).
- **`devlog on <date>`** — entries for a specific day: `today`, `yesterday`,
  `Nd` days ago, or `YYYY-MM-DD` (local-zone bucketing when `DEVLOG_TZ` is
  set). The missing sibling of `today`/`yesterday`/`week`.
- **Smarter search** — multiple space-separated terms now AND together,
  `"quoted phrases"` match as a single term, and `--regex` interprets the
  query as a case-insensitive regular expression (invalid patterns exit 2).
  Plain single-term substring search is unchanged and still the default.
- **`--at Nd` / `--at Nw`** — `add --at` and `edit --at` now accept relative
  day/week forms, matching what `--since`/`--until` already supported.

### Changed

- `doctor` and `repair` are backend-aware: on SQLite they report/repair the
  `entries.db` store (corrupt-JSON right-truncation recovery remains a
  JSON-backend concept); `restore`'s overwrite prompt now triggers when the
  active backend's store exists, not just `entries.json`.

## [1.6.1] - 2026-10-30

### Changed

- **PyPI distribution renamed `devlog-cli` → `devlog-tool`.** PyPI blocked the
  original name at pending-publisher registration ("too similar to an existing
  project" — `devlog`, `devlogs`, and `dev-log-cli` all exist, the last one is
  also a developer journaling CLI). Nothing had been published yet, so this is
  a free rename: the installed **command stays `devlog`** and the Python
  package stays `devlog` — only the pip name changes. Install with
  `pipx install devlog-tool`. The GitHub repo remains `SpIob/devlog-cli`.

## [1.6.0] - 2026-10-30

Tier 0 — hygiene and release infrastructure (additive, low risk).

### Added

- MIT `LICENSE` file (text previously lived only in the README).
- This changelog.
- GitHub Actions CI: pytest matrix (3.10–3.14 × ubuntu/macos/windows),
  benchmark job, and a ruff gate; badge in the README.
- Release workflow publishing to PyPI via trusted publishing (OIDC — no
  API token stored) and attaching the sdist/wheel to a GitHub Release.
- Ruff configuration in `pyproject.toml` (E/F/I/UP/FA/RUF); the whole
  codebase and test suite are lint-clean.
- `Entry.from_dict()` — tolerant deserialization used by
  `storage.load_entries()` (see Fixed).

### Changed

- Raised the minimum Python version from 3.9 to 3.10 (3.9 is end-of-life;
  the package previously failed to import on 3.9 anyway due to PEP 604
  annotations in `cli.py` / `storage.py`).
- Split the `theme` command group (~470 lines) out of `cli.py` into
  `devlog/_theme_cli.py`; no behavior change.
- Packaging: bundled theme TOMLs are now declared as package data.
  They were silently missing from built wheels, so `devlog theme list`,
  `theme use`, and every other theme command crashed on any
  pip-installed copy while working in source checkouts.

### Fixed

- Tolerant deserialization: `storage.load_entries()` now builds entries via
  `Entry.from_dict()`, which ignores unknown JSON keys instead of crashing
  every command (`Entry(**item)` raised on any field added by a future
  devlog version). Missing required fields (`id`, `message`,
  `created_at`) still raise, now with a message naming the field.
- Latent undefined-name in `storage.local_date_for` annotation (`date` →
  `_date`).
- All pyflakes findings: unused imports across `_io`, `_dates`,
  `_interactive`, `_tagops`, `ui`, `conftest`, and test modules;
  placeholder f-strings in `ui.py` / `cli.py`.
- README: removed a leftover editing note from the `repair` section.

## [1.5.0] - 2026-10-30

Baseline for this changelog; see the repository history for everything up to
this point. Highlights of the 1.x line:

- Full CRUD (`add/list/search/show/edit/delete`) with relative dates
  (`7d`, `2h`), `DEVLOG_TZ` local-timezone bucketing, and idempotent
  markdown/JSON import-export round-trips.
- Analytics: `stats` with sparkline, `calendar` heatmap, `tags` overview,
  `tag`/`merge-tag`/`rename-tag` operations.
- Store health: `repair` (with corrupt-file right-truncation recovery),
  `backup`, `restore`, `doctor`.
- 8 bundled themes plus an interactive `theme create` wizard (11 theme
  subcommands), shell completions for bash/zsh/fish, and a REPL.
- Performance: 10k-entry stress-tested (`list` 91ms, `search` 103ms,
  `stats` 104ms, `export` 114ms) with atomic writes everywhere.

[Unreleased]: https://github.com/SpIob/devlog-cli/compare/v1.7.0...HEAD
[1.7.0]: https://github.com/SpIob/devlog-cli/compare/v1.6.1...v1.7.0
[1.6.1]: https://github.com/SpIob/devlog-cli/compare/v1.6.0...v1.6.1
[1.6.0]: https://github.com/SpIob/devlog-cli/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/SpIob/devlog-cli/releases/tag/v1.5.0
