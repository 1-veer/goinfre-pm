# GoinfrePM 1.4 development progress

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
