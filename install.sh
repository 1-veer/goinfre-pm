#!/bin/sh
# Safe, idempotent installer. "run" keeps the manager environment in goinfre;
# the default explicit install keeps a persistent command in the user's home.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BRANDING_FILE="$SCRIPT_DIR/src/goinfre_pm/project.conf"
[ -f "$BRANDING_FILE" ] || { printf '%s\n' "Missing project branding configuration: $BRANDING_FILE" >&2; exit 1; }
# project.conf contains only maintainer-controlled single-quoted assignments.
. "$BRANDING_FILE"

RUN_ONCE=0
if [ "${1:-}" = "run" ]; then
    RUN_ONCE=1
    shift
fi

if [ -t 1 ]; then
    VIOLET='\033[38;5;141m'; GREEN='\033[0;32m'; RED='\033[0;31m'; AMBER='\033[0;33m'; BOLD='\033[1m'; RESET='\033[0m'
else
    VIOLET=''; GREEN=''; RED=''; AMBER=''; BOLD=''; RESET=''
fi
info() { printf "%b[%s]%b %s\n" "$VIOLET" "$PROJECT_DISPLAY_NAME" "$RESET" "$1"; }
ok() { printf "%b[OK]%b %s\n" "$GREEN" "$RESET" "$1"; }
warn() { printf "%b[WARN]%b %s\n" "$AMBER" "$RESET" "$1" >&2; }
die() {
    printf "%b[ERROR]%b %s\n" "$RED" "$RESET" "$1" >&2
    if [ -n "${BOOTSTRAP_LOG:-}" ]; then
        printf 'Full setup details: %s\n' "$BOOTSTRAP_LOG" >&2
    fi
    exit 1
}
log_note() {
    if [ -n "${BOOTSTRAP_LOG:-}" ]; then
        printf '%s\n' "$1" >> "$BOOTSTRAP_LOG"
    fi
}

# Keep the comparatively small manager available when campus goinfre storage
# changes. Downloaded/extracted applications remain in the selected goinfre root.
MANAGER_HOME=$HOME/.local/share/$PROJECT_SLUG
MANAGER_VENV=$MANAGER_HOME/venv
MANAGER_RUNTIME=$MANAGER_HOME/runtime
LEGACY_AUTOSTART=$HOME/.config/autostart/$PROJECT_SLUG-restore.desktop

# Background restore was retired in 1.5.8 because it could race the visible
# Auto Setup. Remove only the exact manager-owned desktop entry.
if [ -e "$LEGACY_AUTOSTART" ] || [ -L "$LEGACY_AUTOSTART" ]; then
    rm -f "$LEGACY_AUTOSTART"
    info "Removed the old background Auto Setup entry"
fi

# Removing the local manager must still work if goinfre or a system extraction
# tool is unavailable. Application data is purged only for the exact, explicit
# two-argument form; a typo must never widen deletion scope.
PURGE_MANAGER_DATA=0
if [ "$RUN_ONCE" = "0" ] && [ "${1:-}" = "uninstall" ]; then
    if [ "$#" -eq 1 ]; then
        info "Removing the local package-manager runtime; application payloads and state are retained."
        rm -f "$HOME/.local/bin/$PROJECT_COMMAND"
        rm -rf "$MANAGER_HOME"
        ok "$PROJECT_DISPLAY_NAME runtime removed."
        exit 0
    elif [ "$#" -eq 2 ] && [ "$2" = "--purge-data" ]; then
        PURGE_MANAGER_DATA=1
    else
        die "Usage: ./install.sh uninstall [--purge-data]"
    fi
fi

prerequisite_help() {
    printf '%s\n' 'GoinfrePM only requires the system Python 3.10+ supplied by Ubuntu 22.04.' >&2
    printf '%s\n' 'It bootstraps pip privately and does not require sudo, python3-venv, or system pip.' >&2
    printf '%s\n' 'If python3 is unavailable, ask 1337/42 staff to restore the standard workstation Python.' >&2
}

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        prerequisite_help
        die "$2"
    fi
}

require_command python3 "Python 3.10 or newer is required."
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
    prerequisite_help
    die "Python 3.10 or newer is required."
fi

# Ubuntu may ship the stdlib venv module without ensurepip. A pinned pip wheel
# lets us create and repair a private environment without python3-venv, system
# pip, curl, sudo, or arbitrary bootstrap scripts.
PIP_BOOTSTRAP_VERSION=24.3.1
PIP_BOOTSTRAP_SHA256=3790624780082365f47549d032f3770eeb2b1e8bd1f7b2e02dace1afa361b4ed
PIP_BOOTSTRAP_URL=https://files.pythonhosted.org/packages/ef/7d/500c9ad20238fcfcb4cb9243eede163594d7020ce87bd9610c9e02771876/pip-24.3.1-py3-none-any.whl
PIP_BOOTSTRAP_DIR=$MANAGER_RUNTIME/bootstrap
PIP_BOOTSTRAP_WHEEL=$PIP_BOOTSTRAP_DIR/pip-$PIP_BOOTSTRAP_VERSION-py3-none-any.whl

pip_wheel_is_valid() {
    [ -f "$PIP_BOOTSTRAP_WHEEL" ] && python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$PIP_BOOTSTRAP_WHEEL" | grep -F -x "$PIP_BOOTSTRAP_SHA256" >/dev/null 2>&1
}

download_pip_wheel() {
    mkdir -p "$PIP_BOOTSTRAP_DIR"
    if pip_wheel_is_valid; then
        return
    fi
    rm -f "$PIP_BOOTSTRAP_WHEEL"
    log_note "Downloading and verifying the private pip bootstrap"
    if ! python3 - "$PIP_BOOTSTRAP_URL" "$PIP_BOOTSTRAP_WHEEL" "$PIP_BOOTSTRAP_SHA256" "$PROJECT_DISPLAY_NAME/$PROJECT_VERSION" >> "$BOOTSTRAP_LOG" 2>&1 <<'PY'
import hashlib
import os
from pathlib import Path
import secrets
import sys
from urllib.parse import urlparse
from urllib.request import Request, urlopen

url, destination_text, expected, user_agent = sys.argv[1:]
destination = Path(destination_text)
temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.tmp")
digest = hashlib.sha256()
total = 0
try:
    request = Request(url, headers={"User-Agent": user_agent})
    with urlopen(request, timeout=30) as response, temporary.open("xb") as output:
        if urlparse(response.geturl()).scheme != "https":
            raise RuntimeError("pip bootstrap redirected away from HTTPS")
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 10 * 1024 * 1024:
                raise RuntimeError("pip bootstrap exceeded the 10 MiB safety limit")
            digest.update(chunk)
            output.write(chunk)
    if digest.hexdigest() != expected:
        raise RuntimeError("pip bootstrap SHA-256 verification failed")
    os.replace(temporary, destination)
except Exception:
    temporary.unlink(missing_ok=True)
    raise
PY
    then
        die "Could not securely download the private pip bootstrap from PyPI. Check the network and retry."
    fi
    pip_wheel_is_valid || die "The private pip bootstrap download failed verification. Retry when PyPI is reachable."
}

ensure_private_pip() {
    if "$MANAGER_VENV/bin/python" -m pip --version >/dev/null 2>&1; then
        return
    fi
    if [ "${SETUP_UPDATED:-0}" = "0" ]; then
        info "Preparing private Python tools (first launch only)"
    fi
    SETUP_UPDATED=1
    log_note "Bootstrapping pip $PIP_BOOTSTRAP_VERSION inside the private environment"
    download_pip_wheel
    PYTHONPATH=$PIP_BOOTSTRAP_WHEEL "$MANAGER_VENV/bin/python" -m pip install \
        --disable-pip-version-check --no-index "$PIP_BOOTSTRAP_WHEEL" >> "$BOOTSTRAP_LOG" 2>&1 \
        || die "Failed to bootstrap pip inside the private environment."
}

writable_dir() {
    [ -d "$1" ] && [ -w "$1" ] && [ -x "$1" ]
}

choose_root() {
    if [ -n "${GPM_INSTALL_ROOT:-}" ]; then
        base=$GPM_INSTALL_ROOT
        mkdir -p "$base" || die "Cannot create $base"
        GPM_ROOT=$base
    elif [ -f "$HOME/.config/$PROJECT_SLUG/config.json" ]; then
        configured_root=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("install_root", ""))' "$HOME/.config/$PROJECT_SLUG/config.json" 2>/dev/null || true)
        if [ -n "$configured_root" ] && writable_dir "$configured_root"; then
            GPM_ROOT=$configured_root
        else
            configured_root=
        fi
        if [ -z "$configured_root" ]; then
            choose_detected_root=1
        fi
    else
        choose_detected_root=1
    fi
    if [ "${choose_detected_root:-0}" = "1" ]; then
        if [ -n "${GOINFRE:-}" ] && writable_dir "$GOINFRE"; then
            GPM_ROOT=$GOINFRE/$PROJECT_SLUG
        elif [ -n "${USER:-}" ] && writable_dir "/goinfre/$USER"; then
            GPM_ROOT=/goinfre/$USER/$PROJECT_SLUG
        elif writable_dir "$HOME/goinfre"; then
            GPM_ROOT=$HOME/goinfre/$PROJECT_SLUG
        elif [ -t 0 ]; then
            printf "%bNo writable goinfre directory was detected.%b\n" "$AMBER" "$RESET"
            printf 'Enter a writable goinfre path: '
            IFS= read -r selected
            [ -n "$selected" ] || die "No path selected."
            mkdir -p "$selected" || die "Cannot create $selected"
            writable_dir "$selected" || die "Path is not writable: $selected"
            GPM_ROOT=$selected
        else
            die "No writable goinfre root found. Set GPM_INSTALL_ROOT or GOINFRE and retry."
        fi
    fi
    for storage_name in apps downloads runtime logs; do
        [ ! -L "$GPM_ROOT/$storage_name" ] || die "Refusing symlinked storage directory: $GPM_ROOT/$storage_name"
    done
    mkdir -p "$GPM_ROOT/apps" "$GPM_ROOT/downloads" "$GPM_ROOT/runtime" "$GPM_ROOT/logs" || die "Cannot create storage layout at $GPM_ROOT"
}

choose_root
GPM_ROOT=$(CDPATH= cd -- "$GPM_ROOT" && pwd -P) || die "Could not resolve the selected goinfre path."
case "$GPM_ROOT" in
    /|"$HOME") die "Refusing unsafe installation root: $GPM_ROOT" ;;
esac

# An npx launch never creates a permanent command or modifies shell startup
# files. Its reusable private Python environment stays on this post's goinfre.
if [ "$RUN_ONCE" = "1" ]; then
    MANAGER_VENV=$GPM_ROOT/venv
    MANAGER_RUNTIME=$GPM_ROOT/runtime
    PIP_BOOTSTRAP_DIR=$MANAGER_RUNTIME/bootstrap
    PIP_BOOTSTRAP_WHEEL=$PIP_BOOTSTRAP_DIR/pip-$PIP_BOOTSTRAP_VERSION-py3-none-any.whl
    [ ! -L "$MANAGER_VENV" ] || die "Refusing a symlinked goinfre Python environment: $MANAGER_VENV"
fi

if [ "$PURGE_MANAGER_DATA" = "1" ]; then
    info "Removing the manager and explicitly purging goinfre application data."
    rm -f "$HOME/.local/bin/$PROJECT_COMMAND"
    rm -rf "$MANAGER_HOME"
    warn "Purging application payloads because --purge-data was explicitly supplied."
    rm -rf "$GPM_ROOT/apps" "$GPM_ROOT/downloads" "$GPM_ROOT/runtime" "$GPM_ROOT/logs"
    ok "$PROJECT_DISPLAY_NAME runtime removed."
    exit 0
fi

FREE_KB=$(df -Pk "$GPM_ROOT" | awk 'NR==2 {print $4}')
case "$FREE_KB" in ''|*[!0-9]*) die "Could not determine free disk space for $GPM_ROOT" ;; esac
[ "$FREE_KB" -ge 262144 ] || die "At least 256 MiB of free goinfre space is required."

if [ ! -f "$SCRIPT_DIR/pyproject.toml" ] || [ ! -f "$SCRIPT_DIR/packages.toml" ]; then
    die "Run this installer from a complete local checkout of $PROJECT_REPOSITORY"
fi

mkdir -p "$MANAGER_RUNTIME"
BOOTSTRAP_LOG=$GPM_ROOT/logs/bootstrap.log
: > "$BOOTSTRAP_LOG" || die "Could not create the setup log."
chmod 600 "$BOOTSTRAP_LOG" 2>/dev/null || true
{
    printf '%s %s setup log\n' "$PROJECT_DISPLAY_NAME" "$PROJECT_VERSION"
    printf 'Started: '
    date -u '+%Y-%m-%dT%H:%M:%SZ'
    printf 'Install root: %s\n' "$GPM_ROOT"
    printf 'Available space: %s MiB\n\n' "$((FREE_KB / 1024))"
} >> "$BOOTSTRAP_LOG"

info "Preparing $PROJECT_DISPLAY_NAME $PROJECT_VERSION"
ok "Storage ready: $GPM_ROOT ($((FREE_KB / 1024)) MiB free)"
SETUP_UPDATED=0

if [ ! -x "$MANAGER_VENV/bin/python" ]; then
    info "Creating a private Python environment (first launch only)"
    SETUP_UPDATED=1
    log_note "Creating private Python environment: $MANAGER_VENV"
    python3 -m venv --without-pip "$MANAGER_VENV" >> "$BOOTSTRAP_LOG" 2>&1 \
        || die "Failed to create the private Python environment. Ask staff to restore the standard Ubuntu Python."
fi
ensure_private_pip

save_root() {
    mkdir -p "$HOME/.config/$PROJECT_SLUG" || die "Cannot create the small settings directory in your home."
    python3 -c 'import json,os,sys,tempfile; target=sys.argv[1]; fd,tmp=tempfile.mkstemp(prefix=".config.", dir=os.path.dirname(target)); f=os.fdopen(fd,"w",encoding="utf-8"); json.dump({"install_root":sys.argv[2]},f,indent=2); f.write("\n"); f.close(); os.replace(tmp,target)' "$HOME/.config/$PROJECT_SLUG/config.json" "$GPM_ROOT" \
        || die "Could not save the selected goinfre path in your home settings. Check write permissions."
}

if [ "$RUN_ONCE" = "1" ]; then
    REQUIREMENTS_HASH=$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$SCRIPT_DIR/requirements.txt")
    REQUIREMENTS_MARKER=$MANAGER_VENV/.gpm-requirements.sha256
    if [ ! -f "$REQUIREMENTS_MARKER" ] || [ "$(sed -n '1p' "$REQUIREMENTS_MARKER")" != "$REQUIREMENTS_HASH" ] \
        || ! "$MANAGER_VENV/bin/python" -c 'import textual' >/dev/null 2>&1 \
        || ! "$MANAGER_VENV/bin/python" -m pip check >/dev/null 2>&1; then
        info "Installing required components (first launch only)"
        SETUP_UPDATED=1
        log_note "Installing pinned Python dependencies from requirements.txt"
        rm -f "$REQUIREMENTS_MARKER"
        "$MANAGER_VENV/bin/python" -m pip install --disable-pip-version-check \
            --no-cache-dir --timeout 20 --retries 2 -r "$SCRIPT_DIR/requirements.txt" >> "$BOOTSTRAP_LOG" 2>&1 \
            || die "Could not install Python dependencies in goinfre. Check the network and retry."
        printf '%s\n' "$REQUIREMENTS_HASH" > "$REQUIREMENTS_MARKER"
    else
        log_note "Pinned Python dependencies are already ready"
    fi
    save_root
    GPM_PACKAGES_FILE=${GPM_PACKAGES_FILE:-$SCRIPT_DIR/packages.toml}
    PYTHONPATH=$SCRIPT_DIR/src${PYTHONPATH:+:$PYTHONPATH}
    export GPM_PACKAGES_FILE PYTHONPATH
    if [ "$SETUP_UPDATED" = "1" ]; then
        ok "Installed: private Python environment and $PROJECT_DISPLAY_NAME interface"
    else
        ok "$PROJECT_DISPLAY_NAME is ready (existing setup reused)"
    fi
    info "Full setup details: $BOOTSTRAP_LOG"
    printf "%b%s%b\n\n" "$BOLD$VIOLET" "$PROJECT_SIGNATURE" "$RESET"
    exec "$MANAGER_VENV/bin/python" -m "$PROJECT_MODULE" "$@"
fi

info "Installing pinned dependencies and project files"
log_note "Installing pinned dependencies and project files"
if ! "$MANAGER_VENV/bin/python" -m pip install --disable-pip-version-check \
    --no-cache-dir --timeout 20 --retries 2 --upgrade "$SCRIPT_DIR" >> "$BOOTSTRAP_LOG" 2>&1; then
    if "$MANAGER_VENV/bin/python" -c 'import textual' >/dev/null 2>&1; then
        warn "PyPI is unreachable; reusing dependencies and updating local project files only."
    else
        die "Could not download the Python dependencies from PyPI. Check the network and retry."
    fi
    SITE_PACKAGES=$("$MANAGER_VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
    "$MANAGER_VENV/bin/python" -c 'import pathlib,shutil,sys; source=pathlib.Path(sys.argv[1]).resolve(); base=pathlib.Path(sys.argv[2]).resolve(); target=base/sys.argv[3]; target.resolve(strict=False).relative_to(base); shutil.rmtree(target,ignore_errors=True); shutil.copytree(source,target)' "$SCRIPT_DIR/src/$PROJECT_MODULE" "$SITE_PACKAGES" "$PROJECT_MODULE" || die "Offline project update failed."
fi

cp "$SCRIPT_DIR/packages.toml" "$MANAGER_RUNTIME/packages.toml"
cp "$BRANDING_FILE" "$MANAGER_RUNTIME/project.conf"
save_root
mkdir -p "$HOME/.local/bin"

LAUNCHER=$HOME/.local/bin/$PROJECT_COMMAND
{
    printf '%s\n' '#!/bin/sh'
    printf '%s\n' "manager_home=\$HOME/.local/share/$PROJECT_SLUG"
    printf '%s\n' 'export GPM_PACKAGES_FILE=$manager_home/runtime/packages.toml'
    printf '%s\n' "exec \"\$manager_home/venv/bin/python\" -m $PROJECT_MODULE \"\$@\""
} > "$LAUNCHER"
chmod 755 "$LAUNCHER"
"$LAUNCHER" version >/dev/null 2>&1 || die "The new local launcher failed its startup check."

# Versions before 1.1.1 placed the manager itself in goinfre. Only reclaim
# those obsolete copies after the persistent launcher has passed startup.
if [ -d "$GPM_ROOT/venv" ]; then
    rm -rf "$GPM_ROOT/venv"
    ok "Removed obsolete manager runtime from goinfre"
fi
# Very old releases copied these two manager files into goinfre/runtime. The
# directory now owns post-local installation metadata, so never remove it.
rm -f "$GPM_ROOT/runtime/packages.toml" "$GPM_ROOT/runtime/project.conf"

append_path() {
    rcfile=$1
    line=$2
    marker=$3
    mkdir -p "$(dirname "$rcfile")"
    if [ -f "$rcfile" ] && grep -F "$marker" "$rcfile" >/dev/null 2>&1; then
        return
    fi
    if [ -f "$rcfile" ]; then
        cp "$rcfile" "$rcfile.gpm.bak"
    else
        : > "$rcfile"
    fi
    printf '\n%s\n%s\n' "$marker" "$line" >> "$rcfile"
    ok "Updated $rcfile (backup: $rcfile.gpm.bak when it previously existed)"
}

append_path "$HOME/.bashrc" 'export PATH="$HOME/.local/bin:$PATH"' '# Goinfre package manager PATH'
append_path "$HOME/.zshrc" 'export PATH="$HOME/.local/bin:$PATH"' '# Goinfre package manager PATH'
append_path "$HOME/.config/fish/config.fish" 'fish_add_path -g $HOME/.local/bin' '# Goinfre package manager PATH'

printf '\n%b%s %s installed%b\n' "$BOLD$VIOLET" "$PROJECT_DISPLAY_NAME" "$PROJECT_VERSION" "$RESET"
ok "Command: $LAUNCHER"
ok "Persistent manager: $MANAGER_HOME"
ok "Large storage: $GPM_ROOT"
printf 'Open a new terminal, then run: %b%s%b\n' "$BOLD" "$PROJECT_COMMAND" "$RESET"
printf 'Auto Setup runs visibly when you launch %b%s%b; background login restore is retired.\n' "$BOLD" "$PROJECT_COMMAND" "$RESET"
info "Full setup details: $BOOTSTRAP_LOG"
printf "%b%s%b\n" "$BOLD$VIOLET" "$PROJECT_SIGNATURE" "$RESET"
