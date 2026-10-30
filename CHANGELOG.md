# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[Unreleased]: https://github.com/SpIob/devlog-cli/compare/v1.6.0...HEAD
[1.6.0]: https://github.com/SpIob/devlog-cli/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/SpIob/devlog-cli/releases/tag/v1.5.0
