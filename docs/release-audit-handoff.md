# GoinfrePM Ubuntu release-audit handoff

Read this file completely before changing or testing the project. It is a
checkpoint for continuing the final public-release audit on a real 1337/42
Ubuntu workstation.

## Objective

Determine whether GoinfrePM is safe, private, reliable, and ready to share with
students. Inspect and test the repository directly, fix safe in-scope problems,
add regression tests for fixes, and rerun the complete validation matrix. Do
not merely provide recommendations.

Privacy and user-data safety have priority over convenience. Do not publish a
release, force-push, rewrite Git history, run sudo, or authenticate to npm or
GitHub from the shared school workstation.

## Target environment

- Ubuntu 22.04.2 LTS on x86_64 1337/42 school computers
- No sudo and no expectation that `python3-venv`, system pip, or curl exists
- Application payloads and the npx Python environment belong in
  `/goinfre/$USER/goinfre-pm` or another explicitly selected goinfre root
- Only small preferences, launchers, icons, and desktop entries may live under
  the user's home directory
- The normal student entry point is `npx goinfre-pm`

## Current release state

- Current synchronized project version: `1.5.9`
- Last known main commit at handoff creation: `14cd182` (`Release GoinfrePM 1.5.9`)
- Public project identity: GoinfrePM / `gpm` / `goinfre_pm`
- Public author identity: `veer`
- Repository: `https://github.com/1-veer/goinfre-pm`
- Release metadata is centralized in `src/goinfre_pm/project.conf`

If code changes become necessary, use the next unused patch version and keep
`package.json`, `pyproject.toml`, `src/goinfre_pm/project.conf`, and the SVG
version synchronized. Do not change a release version merely because a test
was run.

## Important behavior already implemented

- Safe no-sudo `.deb`, AppImage, tar, zip, direct-binary, and GitHub-release
  installation paths
- HTTPS-only downloads with certificate verification, timeouts, cancellation,
  partial-file cleanup, and optional SHA-256 verification
- Tar and zip traversal protection, including link validation
- Atomic package replacement and rollback-friendly reinstall behavior
- Validated package identifiers and allowlisted integration/removal targets
- Root-local installation manifests so another post is not falsely reported as
  installed merely because roaming preferences exist
- Auto Setup stores only explicitly selected applications
- Auto Setup asks for consent at startup and lists missing applications
- After consent, that same modal becomes a live installer with per-package
  waiting, downloading, installing, ready, skipped, cancelled, or failed state
- Auto Setup waits for a genuine earlier operation instead of failing after an
  arbitrary 30-second timeout
- Stale locks from another post, Linux boot, dead PID, or clearly reused PID
  are discarded conservatively
- The old hidden login-autostart restore is retired and removed because it
  could race the visible interactive restore
- Safe manual `x` / `leave` cleanup removes only manager-owned post-local data
  while retaining the small roaming Auto Setup, theme, and favorites state
- Purple, green, blue, black, and red themes plus light/dark mode persist in the
  small roaming state
- The npx bootstrap does not install a permanent `gpm` command or modify shell
  startup files unless the explicit manager-install option is used
- Bootstrap dependency noise is written to
  `<goinfre-root>/logs/bootstrap.log`; students see a short friendly summary
  ending with `Made by VEER`

## Completed validation before the Ubuntu handoff

The complete macOS-compatible suite passed after release 1.5.9:

- 149 Python tests
- `python -m compileall`
- `npm test`
- `npm pack --dry-run`
- POSIX shell syntax with `sh -n install.sh`
- `git diff --check`
- Focused tests for same-modal Auto Setup progress
- The qBittorrent + VS Code + Zen cross-session regression
- Waiting beyond 30 seconds without a false lock failure
- Stale/reused lock detection
- Bootstrap-output test proving injected pip noise appears in the protected log
  and not in stdout or stderr

The npm allowlist contained only the intentional 28 release files. Tests,
Git metadata, caches, bytecode, and `docs/implementation-progress.md` were not
included in the npm tarball.

The current tracked working tree was searched for credential-like filenames,
API keys, npm/GitHub tokens, private keys, JWT-like values, personal names,
campus logins, email addresses, and local home paths. No such material was
found in the current tracked files or npm contents. A pattern-based scan of
historical file contents found no credential or private-key matches.

## Unresolved privacy decision

Older Git commit metadata and older historical versions of branding files may
contain the maintainer's previous real-name identity or email addresses. Do not
print those values into logs, this document, test output, or the final report.
Do not rewrite or force-push history automatically. Report the issue as a
separate privacy decision requiring explicit maintainer approval.

The npm tarball does not contain `.git`, so npm users cannot see Git commit
metadata. GitHub visitors and repository clones can inspect public history.

Before creating any new local commit on the Ubuntu machine, use only a public
noreply Git identity. Do not commit from the school machine unless the user
explicitly asks.

## Ubuntu acceptance matrix

Work from a clean public clone. Record commands and outcomes without exposing
home-directory contents, usernames, tokens, browser profiles, or unrelated
files.

### 1. Baseline and platform

- Confirm Ubuntu release, x86_64 architecture, Python 3.10+, Node, npm, npx,
  `dpkg-deb`, available goinfre space, and absence of sudo requirements
- Confirm the Git worktree is clean before tests
- Inspect all tracked files and the npm dry-run file list again
- Run the full automated suite, compilation, npm tests, shell syntax,
  `shellcheck` when installed, Bandit, and `pip-audit`
- Do not install audit tools globally; use a temporary virtual environment

### 2. Clean npx bootstrap

- Use a clean, explicitly scoped GoinfrePM test root
- Run the published/current `npx goinfre-pm` path without npm login
- Verify the Python environment is inside goinfre
- Verify no permanent `~/.local/bin/gpm` is created in normal npx mode
- Verify `.bashrc`, `.zshrc`, and Fish configuration are untouched
- Verify normal output is concise and pip details are stored in
  `<root>/logs/bootstrap.log` with restrictive permissions
- Run it a second time and confirm the existing environment is reused

### 3. TUI and Auto Setup

- Exercise keyboard and mouse navigation at approximately 80x24 and a larger
  terminal size
- Verify arrows move between categories and packages and Space preserves the
  highlighted row
- Verify search, selection basket, starter packs, themes, light/dark mode,
  Doctor, help, details, confirmations, resize behavior, and cancellation
- Add Zen Browser, Visual Studio Code, and qBittorrent to Auto Setup
- Clean the post, start again, approve the Auto Setup prompt, and verify the
  same modal visibly tracks all three packages to completion
- Confirm no `Another GoinfrePM operation is already active` false failure
- Confirm a second launch does not redownload healthy packages

### 4. Real applications

- Install and actually launch Zen Browser, Visual Studio Code, qBittorrent,
  Kitty, and Postman when network/time/storage permit
- Prefer existing tests and metadata checks for the rest of the catalog; do not
  download every large application merely to claim coverage
- Verify executable discovery, command symlinks, icons, desktop files, and
  launcher behavior
- Verify architecture rejection using a mocked or fixture ARM-only package;
  never install an ARM payload on x86_64
- Verify Zen uses the official Linux x86_64 archive and reuses the user's
  existing normal Zen profile/configuration rather than deleting it

### 5. Lifecycle and destructive operations

- Test install, repair, explicit reinstall, update, normal removal, and removal
  that forgets Auto Setup membership
- Confirm ordinary removal preserves application profiles and cache unless the
  user explicitly approves the documented optional deletion
- Confirm every deletion remains under an approved manager-owned directory
- Exercise `doctor`, interrupted-download cleanup, broken launcher repair, and
  root-local manifest recovery
- Exercise `x Clean & leave` and CLI `leave` using only the dedicated test root
- Confirm the selected goinfre payload/runtime is removed while roaming
  preferences and ordinary application profiles remain
- Launch again and verify Auto Setup offers to restore the missing apps

### 6. Catalog and network

- Parse every enabled package entry
- Resolve GitHub latest-release metadata without downloading full assets
- Check direct URLs with bounded timeouts and redirects
- Report stale, unreachable, suspicious, architecture-specific, or mutable
  unpinned URLs; do not silently replace them with unofficial mirrors
- Treat real GUI launch validation as package-specific evidence, not proof that
  every future upstream release will remain compatible

## Security review focus

- No `shell=True`, shell interpolation, sudo, global pip, disabled TLS, local
  file URLs, predictable shared temporary directories, or unrestricted archive
  extraction
- No arbitrary post-install shell commands
- No unvalidated package identifiers or deletion targets
- No following archive links outside staging
- No state writes that are non-atomic or world-readable when they contain local
  paths/preferences
- No telemetry, uploads, analytics, credential collection, or automatic npm or
  GitHub authentication
- Review every subprocess as an explicit argument array with a timeout where
  appropriate
- Review AppImage extraction as execution of an untrusted downloaded runtime;
  ensure this limitation remains documented and never present it as risk-free
- Review direct downloads without catalog SHA-256 pins as a supply-chain
  limitation and report the exact current behavior accurately

## Rules while testing

- Never use sudo or apt
- Never run `npm login`, `npm publish`, `git push --force`, or history-rewriting
  commands
- Never inspect or print unrelated user files, browser profiles, SSH keys,
  tokens, cookies, or system-wide student data
- Never use broad destructive paths, `$HOME`, `/`, globs, or unresolved
  variables as cleanup targets
- Use an explicit dedicated GoinfrePM test root and verify it before cleanup
- Preserve existing Zen and application profiles
- Do not claim a check passed if it was skipped or unavailable
- Keep fixes minimal, tested, and compatible with Ubuntu 22.04 Python 3.10

## Final report requirements

Report:

1. Whether the current source and npm package contain sensitive information
2. The separate Git-history privacy finding without repeating private values
3. Automated, security, network, and real-application checks that passed
4. Checks unavailable or intentionally not performed
5. Every code/documentation change made
6. Remaining supply-chain, AppImage, mutable-URL, and upstream compatibility
   limitations
7. Whether the release is ready to share, ready with caveats, or blocked
8. Exact safe next steps, without publishing anything automatically

