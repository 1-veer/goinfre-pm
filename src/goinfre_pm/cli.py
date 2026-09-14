from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys

from .branding import COMMAND, DISPLAY_NAME, SLUG, VERSION
from .config import ConfigurationError, load_packages
from .doctor import collect_doctor_checks
from .errors import error_text, write_crash_log
from .integration import AUTOSTART_DIR, USER_BIN
from .installer import PackageManager
from .storage import Layout, StateStore, available_space, is_writable_directory, persist_root, resolve_install_root, verify_install_root


def _human_size(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TiB"


def _root_or_error() -> Path:
    root = resolve_install_root()
    if root is None:
        raise RuntimeError(f"No writable goinfre root found. Choose one with `{COMMAND} path set DIRECTORY`.")
    return root


def _manager() -> PackageManager:
    root = _root_or_error()
    layout = Layout.at(root)
    layout.create()
    return PackageManager(layout, load_packages())


def _emit(events: object) -> None:
    for kind, value in events:  # type: ignore[union-attr]
        if kind == "log":
            print(value)
        elif kind == "progress" and sys.stdout.isatty():
            print(f"Progress: {value}%", end="\r" if value != 100 else "\n")


def _autostart_path() -> Path:
    return AUTOSTART_DIR / f"{SLUG}-restore.desktop"


def set_autostart(enabled: bool) -> None:
    path = _autostart_path()
    state = StateStore()
    data = state.read()
    if enabled:
        command = shutil.which(COMMAND) or str(USER_BIN / COMMAND)
        desktop_command = '"' + command.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'
        AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
        content = "\n".join([
            "[Desktop Entry]", "Type=Application", f"Name={DISPLAY_NAME} Restore",
            f"Exec={desktop_command} restore", "Terminal=false", "X-GNOME-Autostart-enabled=true", "",
        ])
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)
    else:
        path.unlink(missing_ok=True)
    data["autostart"] = enabled
    state.write(data)
    print(f"Autostart restore {'enabled' if enabled else 'disabled'}.")


def doctor() -> int:
    checks = collect_doctor_checks()
    for check in checks:
        label = {"ok": "OK", "warning": "WARN", "error": "FAIL"}[check.status]
        print(f"[{label}] {check.name}: {check.detail}")
        if check.action:
            print(f"       {check.action}")
    return 1 if any(check.status == "error" for check in checks) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=COMMAND, description=f"{DISPLAY_NAME} — no-sudo goinfre package manager")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("list", help="list packages")
    search = sub.add_parser("search", help="search packages")
    search.add_argument("query")
    for name in ("install", "update"):
        command = sub.add_parser(name, help=f"{name} packages")
        command.add_argument("packages", nargs="*")
        if name == "update":
            command.add_argument("--all", action="store_true")
    remove = sub.add_parser("remove", help="remove packages")
    remove.add_argument("packages", nargs="+")
    remove.add_argument("--purge-cache", action="store_true")
    remove.add_argument("--purge-config", action="store_true", help="explicitly remove allowlisted user configuration")
    sub.add_parser("repair", help="repair launchers and symlinks")
    sub.add_parser("restore", help="restore explicitly desired packages")
    path = sub.add_parser("path", help="show or set install path")
    path_sub = path.add_subparsers(dest="path_command")
    path_set = path_sub.add_parser("set", help="persist an explicit writable root")
    path_set.add_argument("directory", type=Path)
    auto = sub.add_parser("autostart", help="manage optional login restore")
    auto.add_argument("mode", choices=("enable", "disable"))
    sub.add_parser("doctor", help="diagnose installation")
    sub.add_parser("version", help="print version")
    return parser


def _print_packages(query: str = "") -> None:
    terms = query.casefold()
    packages = load_packages()
    state = StateStore().read().get("installed", {})
    for package in packages:
        if not package.enabled and package.identifier not in state:
            continue
        haystack = f"{package.identifier} {package.name} {package.description} {package.category}".casefold()
        if terms and terms not in haystack:
            continue
        status = "unsupported" if not package.enabled else ("installed" if package.identifier in state else "available")
        compatible = "" if package.compatible else " [incompatible]"
        print(f"{package.identifier:20} {status:10} {package.category:18} {package.name}{compatible}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command is None:
            root = resolve_install_root()
            if root is None:
                if not sys.stdin.isatty():
                    raise RuntimeError(f"No goinfre root found; run `{COMMAND} path set DIRECTORY` first")
                chosen = Path(input("Writable goinfre directory: ").strip()).expanduser().resolve()
                if not is_writable_directory(chosen, create=True):
                    raise RuntimeError(f"Path is not writable: {chosen}")
                persist_root(chosen)
            from .app import run_tui
            run_tui()
        elif args.command == "list":
            _print_packages()
        elif args.command == "search":
            _print_packages(args.query)
        elif args.command in {"install", "update"}:
            manager = _manager()
            identifiers = list(args.packages)
            if args.command == "update" and args.all:
                identifiers = list(manager.state.read().get("installed", {}).keys())
            if not identifiers:
                raise RuntimeError("Specify at least one package (or use update --all)")
            for identifier in identifiers:
                def cli_progress(value: float) -> None:
                    if sys.stdout.isatty():
                        print(f"Progress: {value:.0f}%", end="\r")

                def cli_transfer(done: int, total: int | None, speed: float, eta: float | None) -> None:
                    if not sys.stdout.isatty():
                        return
                    total_text = _human_size(total) if total is not None else "unknown"
                    eta_text = f", ETA {max(0, round(eta))}s" if eta is not None else ""
                    print(
                        f"Download: {_human_size(done)}/{total_text}, {_human_size(int(speed))}/s{eta_text}",
                        end="\r",
                    )

                _emit(manager.install(identifier, progress_callback=cli_progress, transfer_callback=cli_transfer))
        elif args.command == "remove":
            manager = _manager()
            for identifier in args.packages:
                _emit(manager.remove(identifier, args.purge_cache, args.purge_config))
        elif args.command == "repair":
            manager = _manager()
            installed = manager.state.read().get("installed", {})
            for identifier in installed:
                if identifier in manager.packages and manager.installed(identifier):
                    manager.repair(identifier)
                    print(f"Repaired {identifier}")
        elif args.command == "restore":
            _emit(_manager().restore())
        elif args.command == "path":
            if args.path_command == "set":
                root = args.directory.expanduser().resolve()
                if not is_writable_directory(root, create=True):
                    raise RuntimeError(f"Path is not writable: {root}")
                Layout.at(root).create()
                persist_root(root)
                print(f"Install root set to {root} ({_human_size(available_space(root))} free)")
            else:
                root = _root_or_error()
                print(f"{root}\n{_human_size(available_space(root))} free")
        elif args.command == "autostart":
            set_autostart(args.mode == "enable")
        elif args.command == "doctor":
            return doctor()
        elif args.command == "version":
            print(f"{DISPLAY_NAME} {VERSION}")
        return 0
    except (ConfigurationError, OSError, RuntimeError, ValueError) as exc:
        print(f"{COMMAND}: error: {error_text(exc)}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(f"\n{COMMAND}: cancelled", file=sys.stderr)
        return 130
    except Exception as exc:
        crash_log = write_crash_log(exc)
        print(f"{COMMAND}: unexpected error: {error_text(exc)}", file=sys.stderr)
        if crash_log:
            print(f"Details saved to {crash_log}", file=sys.stderr)
        return 1
