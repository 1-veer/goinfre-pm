#!/bin/sh
# Safe, idempotent installer. The manager persists locally; app payloads use goinfre.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BRANDING_FILE="$SCRIPT_DIR/src/goinfre_pm/project.conf"
[ -f "$BRANDING_FILE" ] || { printf '%s\n' "Missing project branding configuration: $BRANDING_FILE" >&2; exit 1; }
# project.conf contains only maintainer-controlled single-quoted assignments.
. "$BRANDING_FILE"

if [ -t 1 ]; then
    VIOLET='\033[38;5;141m'; GREEN='\033[0;32m'; RED='\033[0;31m'; AMBER='\033[0;33m'; BOLD='\033[1m'; RESET='\033[0m'
else
    VIOLET=''; GREEN=''; RED=''; AMBER=''; BOLD=''; RESET=''
fi
info() { printf "%b[%s]%b %s\n" "$VIOLET" "$PROJECT_DISPLAY_NAME" "$RESET" "$1"; }
ok() { printf "%b[OK]%b %s\n" "$GREEN" "$RESET" "$1"; }
warn() { printf "%b[WARN]%b %s\n" "$AMBER" "$RESET" "$1" >&2; }
die() { printf "%b[ERROR]%b %s\n" "$RED" "$RESET" "$1" >&2; exit 1; }

command -v python3 >/dev/null 2>&1 || die "Python 3 is required."
command -v curl >/dev/null 2>&1 || die "curl is required for dependency/network checks."
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || die "Python 3.10 or newer is required."
python3 -m venv --help >/dev/null 2>&1 || die "The Python venv module is unavailable."

# Keep the comparatively small manager available when campus goinfre storage
# changes. Downloaded/extracted applications remain in the selected goinfre root.
MANAGER_HOME=$HOME/.local/share/$PROJECT_SLUG
MANAGER_VENV=$MANAGER_HOME/venv
MANAGER_RUNTIME=$MANAGER_HOME/runtime

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
    mkdir -p "$GPM_ROOT/apps" "$GPM_ROOT/downloads" "$GPM_ROOT/logs" || die "Cannot create storage layout at $GPM_ROOT"
}

if [ "${1:-}" = "uninstall" ] && [ "${2:-}" != "--purge-data" ]; then
    info "Removing the local package-manager runtime; application payloads and state are retained."
    rm -f "$HOME/.local/bin/$PROJECT_COMMAND"
    rm -rf "$MANAGER_HOME"
    ok "$PROJECT_DISPLAY_NAME runtime removed."
    exit 0
fi

choose_root
case "$GPM_ROOT" in
    /|"$HOME") die "Refusing unsafe installation root: $GPM_ROOT" ;;
esac

if [ "${1:-}" = "uninstall" ]; then
    info "Removing the manager and explicitly purging goinfre application data."
    rm -f "$HOME/.local/bin/$PROJECT_COMMAND"
    rm -rf "$MANAGER_HOME"
    warn "Purging application payloads because --purge-data was explicitly supplied."
    rm -rf "$GPM_ROOT/apps" "$GPM_ROOT/downloads" "$GPM_ROOT/logs"
    ok "$PROJECT_DISPLAY_NAME runtime removed."
    exit 0
fi

FREE_KB=$(df -Pk "$GPM_ROOT" | awk 'NR==2 {print $4}')
case "$FREE_KB" in ''|*[!0-9]*) die "Could not determine free disk space for $GPM_ROOT" ;; esac
[ "$FREE_KB" -ge 262144 ] || die "At least 256 MiB of free goinfre space is required."

if [ ! -f "$SCRIPT_DIR/pyproject.toml" ] || [ ! -f "$SCRIPT_DIR/packages.toml" ]; then
    die "Run this installer from a complete local checkout of $PROJECT_REPOSITORY"
fi

info "Install root: $GPM_ROOT"
info "Available space: $((FREE_KB / 1024)) MiB"

mkdir -p "$MANAGER_RUNTIME"

if [ ! -x "$MANAGER_VENV/bin/python" ]; then
    info "Creating persistent Python environment in $MANAGER_VENV"
    python3 -m venv "$MANAGER_VENV" || die "Failed to create the virtual environment."
fi

if ! curl -fsSI --connect-timeout 8 --max-time 15 https://pypi.org/simple/textual/ >/dev/null 2>&1; then
    if ! "$MANAGER_VENV/bin/python" -c 'import textual' >/dev/null 2>&1; then
        die "PyPI is unreachable and Textual is not already installed in the persistent environment."
    fi
    warn "PyPI is unreachable; reusing the installed dependency set."
    SITE_PACKAGES=$("$MANAGER_VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
    "$MANAGER_VENV/bin/python" -c 'import pathlib,shutil,sys; source=pathlib.Path(sys.argv[1]).resolve(); base=pathlib.Path(sys.argv[2]).resolve(); target=base/sys.argv[3]; target.resolve(strict=False).relative_to(base); shutil.rmtree(target,ignore_errors=True); shutil.copytree(source,target)' "$SCRIPT_DIR/src/$PROJECT_MODULE" "$SITE_PACKAGES" "$PROJECT_MODULE" || die "Offline project update failed."
else
    info "Installing pinned dependencies and project files"
    "$MANAGER_VENV/bin/python" -m pip install --disable-pip-version-check --upgrade "$SCRIPT_DIR" || die "Python dependency installation failed."
fi

cp "$SCRIPT_DIR/packages.toml" "$MANAGER_RUNTIME/packages.toml"
cp "$BRANDING_FILE" "$MANAGER_RUNTIME/project.conf"
mkdir -p "$HOME/.config/$PROJECT_SLUG" "$HOME/.local/bin"
python3 -c 'import json,os,sys,tempfile; target=sys.argv[1]; fd,tmp=tempfile.mkstemp(prefix=".config.", dir=os.path.dirname(target)); f=os.fdopen(fd,"w",encoding="utf-8"); json.dump({"install_root":sys.argv[2]},f,indent=2); f.write("\n"); f.close(); os.replace(tmp,target)' "$HOME/.config/$PROJECT_SLUG/config.json" "$GPM_ROOT"

LAUNCHER=$HOME/.local/bin/$PROJECT_COMMAND
{
    printf '%s\n' '#!/bin/sh'
    printf '%s\n' "export GPM_PACKAGES_FILE='$MANAGER_RUNTIME/packages.toml'"
    printf '%s\n' "exec '$MANAGER_VENV/bin/python' -m $PROJECT_MODULE \"\$@\""
} > "$LAUNCHER"
chmod 755 "$LAUNCHER"
"$LAUNCHER" version >/dev/null 2>&1 || die "The new local launcher failed its startup check."

# Versions before 1.1.1 placed the manager itself in goinfre. Only reclaim
# those obsolete copies after the persistent launcher has passed startup.
if [ -d "$GPM_ROOT/venv" ] || [ -d "$GPM_ROOT/runtime" ]; then
    rm -rf "$GPM_ROOT/venv" "$GPM_ROOT/runtime"
    ok "Removed obsolete manager runtime from goinfre"
fi

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
printf 'Optional login restore: %b%s autostart enable%b\n' "$BOLD" "$PROJECT_COMMAND" "$RESET"
