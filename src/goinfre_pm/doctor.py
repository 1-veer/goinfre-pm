from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import platform
import shutil
import sys

from .config import ConfigurationError, load_packages
from .experience import human_size
from .integration import DESKTOP_DIR, USER_BIN, find_executable
from .models import Package, validate_package_id
from .storage import Layout, LocalStateStore, StateStore, available_space, is_writable_directory, resolve_install_root


@dataclass(frozen=True)
class DoctorCheck:
    status: str
    name: str
    detail: str
    action: str = ""


def collect_doctor_checks(
    root: Path | None = None,
    packages: list[Package] | None = None,
    state: StateStore | None = None,
    installations: LocalStateStore | None = None,
) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    root = resolve_install_root() if root is None else root
    if root is None:
        checks.append(DoctorCheck("error", "Goinfre path", "Not configured", "Run `gpm path set DIRECTORY`."))
    else:
        checks.append(DoctorCheck("ok", "Goinfre path", str(root)))
        writable = is_writable_directory(root)
        checks.append(DoctorCheck("ok" if writable else "error", "Root permissions", "Writable" if writable else "Not writable", "Choose another path with `gpm path set DIRECTORY`." if not writable else ""))
        try:
            free = available_space(root)
            status = "error" if free < 100 * 1024**2 else ("warning" if free < 1024**3 else "ok")
            action = "Free goinfre space before installing applications." if status != "ok" else ""
            checks.append(DoctorCheck(status, "Available space", human_size(free), action))
        except OSError as exc:
            checks.append(DoctorCheck("error", "Available space", str(exc), "Check that the goinfre volume is mounted."))
    python_ok = sys.version_info >= (3, 10)
    checks.append(DoctorCheck("ok" if python_ok else "error", "Python", platform.python_version(), "Ask campus staff to restore Python 3.10+." if not python_ok else ""))

    if packages is None:
        try:
            packages = load_packages()
            checks.append(DoctorCheck("ok", "Package catalog", f"{len(packages)} entries"))
        except ConfigurationError as exc:
            packages = []
            checks.append(DoctorCheck("error", "Package catalog", str(exc), "Restore packages.toml from the release."))
    else:
        checks.append(DoctorCheck("ok", "Package catalog", f"{len(packages)} entries"))

    needs_dpkg = any(package.enabled and package.source_type == "deb" for package in packages)
    dpkg_deb = shutil.which("dpkg-deb")
    checks.append(DoctorCheck("ok" if dpkg_deb or not needs_dpkg else "error", "Debian extractor", dpkg_deb or "dpkg-deb not found", "Ask campus staff to restore the standard Ubuntu dpkg tools." if needs_dpkg and not dpkg_deb else ""))
    in_path = str(USER_BIN) in os.environ.get("PATH", "").split(os.pathsep)
    checks.append(DoctorCheck("ok" if in_path else "error", "Command PATH", "~/.local/bin is available" if in_path else "~/.local/bin is missing", "Open a new terminal or add ~/.local/bin to PATH." if not in_path else ""))
    incompatible = [package.identifier for package in packages if package.enabled and not package.compatible]
    checks.append(DoctorCheck("ok" if not incompatible else "error", "Architecture", "All enabled packages compatible" if not incompatible else ", ".join(incompatible), "GoinfrePM targets Ubuntu 22.04 x86_64." if incompatible else ""))

    state_data = (state or StateStore()).read()
    installed: dict[str, object] = {}
    layout = Layout.at(root) if root is not None else None
    if layout is not None:
        installed = (installations or LocalStateStore(layout)).read().get("installed", {})
    broken_executables: list[str] = []
    broken_commands: list[str] = []
    missing_desktop: list[str] = []
    package_map = {package.identifier: package for package in packages}
    identifiers = set(installed)
    if layout is not None and layout.apps.is_dir():
        for child in layout.apps.iterdir():
            try:
                identifiers.add(validate_package_id(child.name))
            except ValueError:
                continue
    if isinstance(installed, dict) and layout is not None:
        for identifier in sorted(identifiers):
            record = installed.get(identifier, {})
            if not isinstance(record, dict):
                broken_executables.append(str(identifier))
                continue
            package = package_map.get(str(identifier))
            live = layout.apps / str(identifier)
            executable = find_executable(live, package) if package and live.is_dir() else None
            if executable is None or not executable.is_file() or not os.access(executable, os.X_OK):
                broken_executables.append(str(identifier))
                if not (USER_BIN / str(identifier)).exists():
                    broken_commands.append(str(identifier))
                if package and package.desktop and not (DESKTOP_DIR / f"{identifier}.desktop").is_file():
                    missing_desktop.append(str(identifier))
                continue
            command = USER_BIN / str(identifier)
            try:
                command_ok = command.is_symlink() and command.resolve(strict=True) == executable.resolve(strict=True)
            except OSError:
                command_ok = False
            if not command_ok:
                broken_commands.append(str(identifier))
            if package and package.desktop and not (DESKTOP_DIR / f"{identifier}.desktop").is_file():
                missing_desktop.append(str(identifier))
    checks.append(DoctorCheck("ok" if not broken_executables else "error", "Executables", "Intact" if not broken_executables else ", ".join(broken_executables), "Run `gpm repair` or reinstall affected packages." if broken_executables else ""))
    checks.append(DoctorCheck("ok" if not broken_commands else "error", "Command links", "Intact" if not broken_commands else ", ".join(broken_commands), "Run `gpm repair`." if broken_commands else ""))
    checks.append(DoctorCheck("ok" if not missing_desktop else "warning", "Desktop launchers", "Intact" if not missing_desktop else ", ".join(missing_desktop), "Run `gpm repair`." if missing_desktop else ""))
    setup = state_data.get("setup_packages", [])
    missing_setup: list[str] = []
    if isinstance(setup, list) and layout is not None:
        for identifier in setup:
            package = package_map.get(str(identifier))
            live = layout.apps / str(identifier)
            executable = find_executable(live, package) if package and live.is_dir() else None
            if executable is None or not os.access(executable, os.X_OK):
                missing_setup.append(str(identifier))
    setup_enabled = bool(state_data.get("setup_enabled"))
    setup_detail = f"{len(setup) if isinstance(setup, list) else 0} selected; startup prompt {'enabled' if setup_enabled else 'disabled'}"
    setup_status = "warning" if missing_setup else "ok"
    setup_action = f"Run `gpm setup restore` for: {', '.join(missing_setup)}" if missing_setup else ""
    checks.append(DoctorCheck(setup_status, "Auto Setup", setup_detail, setup_action))
    return checks
