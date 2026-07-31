# GoinfrePM

GoinfrePM is a terminal package manager for Ubuntu-based 1337/42 workstations.
It downloads and extracts large applications into writable goinfre storage and
keeps only small state, launchers, icons, and command links in the home
directory. It does not install system packages or require administrator access.

> Project identity is centralized in
> `src/goinfre_pm/project.conf`. Replace the repository placeholder there before
> publishing a fork.

![TUI screenshot placeholder](docs/tui-screenshot-placeholder.svg)

## Why use it

School home-directory quotas are small, while `/goinfre` (or the campus-provided
`~/goinfre` link) is intended for larger temporary data. GoinfrePM keeps app
payloads and its Python environment there, while integrating applications with
the desktop and shell without changing the operating system.

## Storage layout

The root is selected in this order: writable `$GOINFRE`, writable
`/goinfre/$USER`, an existing writable `$HOME/goinfre`, or a path you select.
The project directory name is appended to automatically detected base paths.
No large-payload fallback is silently created in ordinary home storage.

```text
<selected-root>/
├── apps/       # extracted applications
├── downloads/  # operation-scoped temporary downloads
├── runtime/    # catalog and runtime metadata
├── venv/       # pinned Python environment
└── logs/       # per-package logs
```

Small files live under `~/.config/goinfre-pm`, `~/.local/bin`,
`~/.local/share/applications`, and `~/.local/share/icons`.

## Prerequisites and installation

You need Ubuntu 22.04 or similar, Python 3.10+, its `venv` module, `curl`,
`dpkg`, network access to PyPI, and at least 256 MiB free for installation
(applications need more). From a local checkout:

```sh
chmod +x install.sh
./install.sh
```

The installer prefers these local files, creates the virtual environment in
goinfre, installs pinned dependencies, creates `~/.local/bin/gpm`, and adds
`~/.local/bin` to Bash, Zsh, and Fish configuration exactly once. Existing
shell files are backed up before modification. Open a new terminal and run:

```sh
gpm
```

For a manually chosen non-interactive root:

```sh
GPM_INSTALL_ROOT="/path with spaces/goinfre-pm" ./install.sh
```

Running `./install.sh` again updates the existing environment and launcher.
The repository URL is currently the intentional publishing placeholder shown
in the centralized project configuration.

## TUI controls

| Key | Action |
|---|---|
| `↑`/`↓`, `j`/`k` | Navigate packages |
| `Space` | Select or deselect |
| `/` | Search |
| `Enter` | Refresh/view details |
| `i` / `I` | Install highlighted / selected |
| `r` / `R` | Confirm and remove highlighted / selected |
| `a` | Select/deselect visible packages |
| `p` | Choose and persist install root |
| `l` | Focus detailed logs |
| `?` | Show help |
| `q`, `Esc` | Quit when no modal is active |

The layout hides lower-priority navigation/details panels at small terminal
widths and remains usable around 80×24. Mutating actions are blocked while a
worker is active. Removal confirmations leave user configuration intact.

## Command-line interface

```text
gpm
gpm list
gpm search <query>
gpm install <package>...
gpm remove <package>... [--purge-cache] [--purge-config]
gpm update <package>...
gpm update --all
gpm repair
gpm restore
gpm path
gpm path set <directory>
gpm autostart enable
gpm autostart disable
gpm doctor
gpm version
```

`restore` installs only identifiers explicitly recorded as desired after a
successful installation. Automatic login restore is off by default; enabling
it creates one manager-owned desktop autostart entry. `repair` recreates missing
command links and desktop integration for recorded applications.

## Package catalog

`packages.toml` uses one `[[package]]` table per package:

```toml
[[package]]
id = "example-tool"
name = "Example Tool"
description = "What this package does."
category = "Developer Tools"
url = "https://github.com/example/tool"
source_type = "github"
asset_pattern = 'linux-x86_64\.tar\.gz$'
architectures = ["x86_64"]
executables = ["bin/example-tool"]
icons = ["share/icons/example.png"]
desktop = false
terminal = true
version = "latest"
```

Supported source types cover Debian archives (`dpkg -x`), AppImages, tar.gz,
tgz, tar.xz, tar.bz2, ZIP, direct executables, and latest GitHub release assets.
Identifiers must be lowercase, path-safe, and unique. Architecture is checked
before download. GitHub `asset_pattern` should narrowly select the correct Linux
asset. Direct pinned URLs in the catalog carry review notes when they may be
stale.

Optional post-install behavior is limited to structured `chmod` and internal
`symlink` actions. Legacy configuration migration is available through
`goinfre_pm.config.migrate_legacy_config`; legacy shell fragments are reported
and disabled, never executed.

## Updating and uninstalling

Update this checkout, then rerun `./install.sh`. Update application payloads
with `gpm update NAME` or `gpm update --all`; replacement is staged and rolled
back if integration fails.

```sh
./install.sh uninstall
```

That removes the manager runtime and `gpm` launcher but retains applications
and state. `./install.sh uninstall --purge-data` also removes application
payload directories from the selected root. Shell PATH lines and small state
are deliberately retained for safety and possible reinstall.

## Security model and limitations

- Downloads require HTTPS with normal certificate validation, timeouts, and
  operation-specific temporary directories.
- Tar and ZIP extraction rejects absolute paths, traversal, special devices,
  unsafe links, and ZIP symlinks.
- Installs are staged and atomically swapped; failed updates restore the prior
  payload where possible.
- Application removal only targets the exact validated app directory and
  manager-owned integration filenames. Configuration purge needs an explicit
  CLI flag, package opt-in, and an allowlisted path that clearly matches the
  package.
- Download signatures are not yet verified. HTTPS protects transport but does
  not replace publisher signatures or hashes.
- Extracted apps may still depend on system libraries unavailable on a campus
  image. GoinfrePM cannot supply privileged OS packages.
- GitHub asset matching and pinned vendor URLs can become stale. Review catalog
  notes and upstream release pages before adding or refreshing a package.

Run `gpm doctor` to inspect the root, permissions, free space, Python, external
commands, PATH, state, integrations, catalog validity, and architecture.

## Troubleshooting

- **No root found:** mount or create the campus goinfre location, then run
  `gpm path set "/your/writable/goinfre-pm"`.
- **Command not found:** open a new shell or temporarily run
  `export PATH="$HOME/.local/bin:$PATH"`.
- **Application does not launch:** inspect `<root>/logs/<package>.log`, run
  `gpm doctor`, then `gpm repair`.
- **Stale package URL:** update only that package entry after verifying the
  publisher and architecture.

## Contributing, license, and attribution

Add tests for backend behavior, keep package URLs HTTPS-only, document pinned
assets, and run the checks below before submitting a change. The
`.github/workflows/ubuntu-22.04.yml` workflow is the authoritative platform
contract: it runs these checks, a clean installer smoke test, `gpm doctor`, and
the Textual UI on Ubuntu 22.04 with Python 3.10.

```sh
python -m compileall .
python -m pytest
shellcheck install.sh
```

The project is MIT-licensed; see `LICENSE`. `NOTICE.md` records the cautious
provenance of the supplied code and historical inspiration without claiming
that all inherited work was originally written by the current maintainer.
