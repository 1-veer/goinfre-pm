# GoinfrePM development progress

This file records recoverable checkpoints for the student-experience upgrade.

## Baseline

- Source: clean `1.3.0` at commit `f2dd3a3`.
- Tests: 51 passed on macOS before changes.
- Target remains Ubuntu 22.04 LTS x86_64 without sudo.

## Checkpoints

- [x] Audit current UI, storage state, package metadata, and test seams.
- [x] Add backward-compatible experience metadata and state primitives (60 tests passing).
- [x] Add onboarding, Starter Packs, basket, favorites, sorting, and responsive TUI screens (keyboard/modal/80×24 tests included).
- [x] Add visual Doctor and asynchronous cached update indicators.
- [x] Improve progress and completion summaries.
- [x] Validate a Spotify-controlled Ubuntu 22.04-compatible package (vendor URL, package metadata, ELF GLIBC symbols, and SHA-256 pin checked).
- [x] Update release metadata and documentation; run the full available release validation matrix.

## Current validation

- Release version synchronized at `1.4.1`.
- 78 Python tests pass on macOS, including 80×24 and 120×36 Textual pilots.
- All 43 enabled catalog endpoints resolve; Obsidian's recent-stable fallback resolves its amd64 Debian asset; disabled legacy Stremio was skipped.
- Packed npm contents are intentional (28 files), shell syntax and SVG parsing pass, and a clean isolated install plus idempotent rerun succeeded from paths containing spaces.
- Ubuntu 22.04 GUI launch validation remains a Linux-machine release check; Docker is installed on the Mac but its daemon is not running. `shellcheck` is not installed locally.

## 1.4.1 follow-up

- [x] Replace the dot/circle row controls with clear checkbox markers and stars only for favorites.
- [x] Label direct downloads as catalog-managed and failed GitHub checks as unavailable instead of “update unknown”.
- [x] Prevent normal install actions from replacing installed payloads; retain replacement only through the explicit update command.

## 1.5.0 My Setup and cross-post restore

### Baseline

- Source: clean `1.4.1` at commit `303fd75`.
- Baseline validation: 82 Python tests passed on macOS.
- Target remains Ubuntu 22.04 LTS x86_64 without sudo.

### Checkpoints

- [x] Audit roaming state, install-root state, TUI status logic, restore behavior, and npx argument forwarding.
- [x] Split schema-v3 roaming preferences from the root-local installation manifest.
- [x] Conservatively migrate only verified legacy payloads; do not enroll old desired packages into My Setup.
- [x] Add live payload/executable/integration classification and cross-post simulations.
- [x] Add atomic My Setup persistence, enable/disable controls, root-local locking, and selective restore events.
- [x] Add explicit reinstall with rollback and safe My Setup-aware removal behavior.
- [x] Add My Setup / Needs Restore TUI sections, keyboard controls, startup restore, cancellation, and summary states.
- [x] Complete documentation, release audit, isolated wheel installation smoke test, and full available validation matrix.

### Current validation

- Release metadata is synchronized at `1.5.0`.
- 109 Python tests pass on macOS, including two-post state, selective restore, cancellation, rollback, locking, CLI dispatch, mouse/keyboard My Setup controls, and 80×24 Textual pilots.
- Python compilation and 3.10 grammar parsing, shell syntax, npm tests, the 28-file npm dry-run package, SVG parsing, and an isolated 1.5.0 wheel build/install/`gpm version` smoke test pass.
- Bandit reports no medium/high findings; its three low findings are the intentional argument-array subprocesses for `dpkg-deb`, AppImage extraction, and `update-desktop-database`. `pip-audit` reports no known dependency vulnerabilities.
- `shellcheck` is unavailable locally. Docker is installed but its daemon is not running, so the full Ubuntu 22.04 installer and GUI launch matrix remains a release check on a school workstation.
- No commit, push, or npm publication has been performed.
