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

## 1.5.0 Auto Setup and cross-post restore

### Baseline

- Source: clean `1.4.1` at commit `303fd75`.
- Baseline validation: 82 Python tests passed on macOS.
- Target remains Ubuntu 22.04 LTS x86_64 without sudo.

### Checkpoints

- [x] Audit roaming state, install-root state, TUI status logic, restore behavior, and npx argument forwarding.
- [x] Split schema-v3 roaming preferences from the root-local installation manifest.
- [x] Conservatively migrate only verified legacy payloads; do not enroll old desired packages into Auto Setup.
- [x] Add live payload/executable/integration classification and cross-post simulations.
- [x] Add atomic Auto Setup persistence, enable/disable controls, root-local locking, and selective restore events.
- [x] Add explicit reinstall with rollback and safe Auto Setup-aware removal behavior.
- [x] Add the Auto Setup TUI section, keyboard controls, startup restore, cancellation, and summary states.
- [x] Complete documentation, release audit, isolated wheel installation smoke test, and full available validation matrix.

### Current validation

- Release metadata is synchronized at `1.5.0`.
- 109 Python tests pass on macOS, including two-post state, selective restore, cancellation, rollback, locking, CLI dispatch, mouse/keyboard Auto Setup controls, and 80×24 Textual pilots.
- Python compilation and 3.10 grammar parsing, shell syntax, npm tests, the 28-file npm dry-run package, SVG parsing, and an isolated 1.5.0 wheel build/install/`gpm version` smoke test pass.
- Bandit reports no medium/high findings; its three low findings are the intentional argument-array subprocesses for `dpkg-deb`, AppImage extraction, and `update-desktop-database`. `pip-audit` reports no known dependency vulnerabilities.
- `shellcheck` is unavailable locally. Docker is installed but its daemon is not running, so the full Ubuntu 22.04 installer and GUI launch matrix remains a release check on a school workstation.
- No commit, push, or npm publication has been performed.

## 1.5.1 themes, privacy, and clearer labels

- [x] Rename the user-facing setup label to Auto Setup and keep cross-post detection internal.
- [x] Remove the redundant missing-app navigation section.
- [x] Add persistent Purple, Green, Blue, Black, and Red choices to the built-in Ctrl+P palette.
- [x] Keep the theme in the tiny roaming state while installed manifests and payloads remain in goinfre.
- [x] Add the `Made by VEER` UI credit and replace public personal-name metadata with `veer`.
- [x] Complete the 1.5.1 regression and release validation matrix.

### 1.5.1 validation

- Release metadata is synchronized at `1.5.1`; public author metadata is `veer`.
- 111 Python tests pass on macOS, including Ctrl+P theme discovery, theme persistence, compact roaming state, Auto Setup, cross-post classification, and 80×24 Textual pilots.
- Python compilation and 3.10 grammar parsing, Textual CSS parsing, shell syntax, npm tests, the intentional 28-file npm dry-run package, SVG parsing, and whitespace checks pass.
- Bandit reports no medium/high findings, and `pip-audit` reports no known dependency vulnerabilities.
- `shellcheck` remains unavailable locally. The Ubuntu 22.04 GUI and package-launch matrix remains a release check on a school workstation.
- No commit, push, or npm publication has been performed.

## 1.5.2 consent, launch, and cleanup follow-up

- [x] Ask before installing Auto Setup apps, list every app that is not ready, and provide Install now / Not now choices.
- [x] Remove the redundant missing-app navigation section and use clear current-post status wording.
- [x] Add a verified, shell-free Launch action for installed desktop applications.
- [x] Add Doctor cleanup for interrupted downloads, stale staging/backup workdirs, and logs older than 30 days.
- [x] Reflow the installed-app actions into two responsive rows so no button is clipped.
- [x] Synchronize release metadata at `1.5.2` after npm made `1.5.1` immutable.

### 1.5.2 validation

- 115 Python tests pass on macOS, including Auto Setup consent, verified launching, allowlisted cleanup, and responsive 80×24 modal boundaries.
- Python compilation and 3.10 grammar parsing, shell syntax, npm tests, package dry-run, and whitespace checks pass.
- Bandit reports no medium/high findings.
- Ubuntu 22.04 GUI launch validation remains a release check on a school workstation.
- No commit, push, or npm publication has been performed.

## 1.5.4 cross-post Auto Setup lock fix

- [x] Record workstation and Linux boot identity in every payload-operation lock.
- [x] Discard a stale lock carried from another school post even when its PID was reused locally.
- [x] Present short-lived contention as “Preparing your Auto Setup…” instead of a failure.
- [x] Offer Retry / Not now after 30 seconds without marking packages as failed.
- [x] Recheck package health after acquiring the lock to avoid duplicate downloads.

### 1.5.4 validation

- 132 Python tests pass on macOS, including cross-post PID reuse, lock waiting, timeout, retry, and cancellation.
- Python compilation, shell syntax, npm tests, package dry-run, and whitespace checks pass.
- Shellcheck remains unavailable locally. Ubuntu 22.04 GUI validation remains a school-workstation release check.
- No commit, push, or npm publication has been performed.

## 1.5.5 concurrent Auto Setup handoff

- [x] Treat applications completed by another GoinfrePM session as ready, not skipped.
- [x] Show which applications were installed by this window and which completed while it waited.
- [x] Mirror the active session's current package, phase, and percentage in the waiting TUI.
- [x] Replace the misleading restore summary with a successful Auto Setup ready result.
- [x] Add a recommended, explicitly confirmed Leave this post cleanup in the TUI, command palette, and CLI.
- [x] Remove only manager-owned goinfre directories and integrations while preserving roaming setup and personal profiles.
- [x] Surface post-local usage in the header and keep Clean & leave visible in the footer.
- [x] Offer Clean & exit, Exit without cleaning, and Cancel from the normal quit flow.
- [x] Repeat the cleanup reminder after installs and Auto Setup restoration.

### 1.5.5 validation

- 140 Python tests pass on macOS, including cleanup previews, quit choices, explicit leave confirmation, and 80×24 modal boundaries.
- Python compilation, shell syntax, npm tests, the 28-file npm dry-run package, release-version consistency, branding search, and whitespace checks pass.
- Shellcheck remains unavailable locally. Ubuntu 22.04 GUI validation remains a school-workstation release check.
- No commit, push, or npm publication has been performed.

## 1.5.6 startup and cleanup lifecycle hardening

- [x] Ignore late storage-usage results after Textual has started tearing down its widgets.
- [x] Cancel the storage measurement worker before deleting the current post runtime.
- [x] Keep Clean & leave visible at approximately 80×24 by hiding the lower-priority basket summary.
- [x] Replace the static Auto Setup waiting line with a clear other-session message and elapsed wait updates.
- [x] Detect same-post stale locks when Linux has reused the recorded PID.

### 1.5.6 validation

- 143 Python tests pass on macOS, including late-worker teardown, narrow cleanup visibility, lock wait heartbeats, and same-post PID reuse.
- Python compilation, shell syntax, npm tests, the 28-file npm dry-run package, release-version consistency, branding search, and whitespace checks pass.
- Shellcheck remains unavailable locally. The Ubuntu 22.04 GUI remains a final school-workstation release check.
- No commit, push, or npm publication has been performed.

## 1.5.7 modal resize hardening

- [x] Resolve responsive widgets from the underlying package screen while a modal is open.
- [x] Treat missing main-screen nodes during startup and shutdown as a harmless lifecycle state.
- [x] Keep compact/fullscreen layout transitions working while the Leave this post dialog is active.

### 1.5.7 validation

- 144 Python tests pass on macOS, including the exact open-cleanup-modal and compact/fullscreen resize sequence.
- Python compilation, shell syntax, npm tests, the 28-file npm dry-run package, and whitespace checks pass.
- Shellcheck remains unavailable locally. Ubuntu 22.04 remains the final real-workstation validation target.
- No commit, push, or npm publication has been performed.
