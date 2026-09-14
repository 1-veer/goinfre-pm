from __future__ import annotations

from pathlib import Path
import os
import re
import shutil
import subprocess

from .models import Package, validate_package_id

USER_BIN = Path.home() / ".local" / "bin"
DESKTOP_DIR = Path.home() / ".local" / "share" / "applications"
ICON_DIR = Path.home() / ".local" / "share" / "icons" / "hicolor" / "256x256" / "apps"
AUTOSTART_DIR = Path.home() / ".config" / "autostart"


def _within(base: Path, candidate: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(base.resolve())
        return True
    except ValueError:
        return False


def find_executable(package_dir: Path, package: Package) -> Path | None:
    """Find the configured executable, including beneath one archive wrapper.

    Upstream archives commonly contain a versioned top-level directory. Safe
    extraction intentionally preserves it, so configured paths are also matched
    as path suffixes instead of falling back to an unrelated helper executable.
    """
    for relative in package.executable_candidates:
        candidate = package_dir / relative
        if _within(package_dir, candidate) and candidate.is_file():
            return candidate
        wanted = Path(relative).parts
        matches: list[Path] = []
        for nested in package_dir.rglob(Path(relative).name):
            if not nested.is_file() or not _within(package_dir, nested):
                continue
            actual = nested.relative_to(package_dir).parts
            if len(actual) >= len(wanted) and actual[-len(wanted):] == wanted:
                matches.append(nested)
        if matches:
            return min(matches, key=lambda item: len(item.relative_to(package_dir).parts))
    preferred = {package.identifier.lower(), package.name.lower().replace(" ", "-"), "apprun"}
    candidates: list[tuple[int, Path]] = []
    for candidate in package_dir.rglob("*"):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            executable = os.access(candidate, os.X_OK)
        except OSError:
            continue
        if not executable:
            continue
        lower = candidate.name.lower()
        score = (20 if lower in preferred else 0) + (5 if "bin" in candidate.parts else 0) - len(candidate.parts)
        candidates.append((score, candidate))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def find_icon(package_dir: Path, package: Package) -> Path | None:
    for relative in package.icon_candidates:
        candidate = package_dir / relative
        if _within(package_dir, candidate) and candidate.is_file() and candidate.suffix.lower() in {".png", ".svg"}:
            return candidate
    images = [item for item in package_dir.rglob("*") if item.is_file() and item.suffix.lower() in {".png", ".svg"}]
    return max(images, key=lambda item: item.stat().st_size, default=None)


def _desktop_exec(path: Path) -> str:
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def desktop_entry(package: Package, executable: Path, icon: Path | None = None) -> str:
    safe_name = package.name.replace("\n", " ").replace("\r", " ")
    safe_description = package.description.replace("\n", " ").replace("\r", " ")
    exec_value = _desktop_exec(executable)
    desktop_categories = {
        "Browsers": "Network;WebBrowser;",
        "Editors and IDEs": "Development;IDE;",
        "Developer Tools": "Development;",
        "Communication": "Network;Chat;",
        "Media": "AudioVideo;Player;",
    }.get(package.category, "Utility;")
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={safe_name}",
        f"Comment={safe_description}",
        f"Exec={exec_value}",
        f"Terminal={'true' if package.terminal else 'false'}",
        f"Icon={icon or package.identifier}",
        f"Categories={desktop_categories}",
        "StartupNotify=true",
    ]
    return "\n".join(lines) + "\n"


def _owned_path(base: Path, identifier: str, suffix: str = "") -> Path:
    validate_package_id(identifier)
    target = base / f"{identifier}{suffix}"
    if target.parent.resolve() != base.expanduser().resolve():
        raise RuntimeError(f"Unsafe integration path: {target}")
    return target


def integrate(package: Package, executable: Path, package_dir: Path | None = None) -> list[str]:
    executable.chmod(executable.stat().st_mode | 0o111)
    USER_BIN.mkdir(parents=True, exist_ok=True)
    symlink = _owned_path(USER_BIN, package.identifier)
    if symlink.exists() or symlink.is_symlink():
        symlink.unlink()
    symlink.symlink_to(executable)
    created = [str(symlink)]
    if package.desktop:
        icon_source = find_icon(package_dir or executable.parent, package)
        icon_target: Path | None = None
        if icon_source:
            ICON_DIR.mkdir(parents=True, exist_ok=True)
            icon_target = _owned_path(ICON_DIR, package.identifier, icon_source.suffix.lower())
            shutil.copy2(icon_source, icon_target)
            created.append(str(icon_target))
        DESKTOP_DIR.mkdir(parents=True, exist_ok=True)
        launcher = _owned_path(DESKTOP_DIR, package.identifier, ".desktop")
        launcher.write_text(desktop_entry(package, executable, icon_target), encoding="utf-8")
        launcher.chmod(0o755)
        created.append(str(launcher))
        desktop_database = shutil.which("update-desktop-database")
        if desktop_database:
            subprocess.run([desktop_database, str(DESKTOP_DIR)], timeout=30, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return created


def remove_integration(package: Package, recorded: list[str] | None = None) -> list[str]:
    allowed_bases = (USER_BIN.resolve(), DESKTOP_DIR.resolve(), ICON_DIR.resolve())
    known = [
        _owned_path(USER_BIN, package.identifier),
        _owned_path(DESKTOP_DIR, package.identifier, ".desktop"),
        _owned_path(ICON_DIR, package.identifier, ".png"),
        _owned_path(ICON_DIR, package.identifier, ".svg"),
    ]
    for text in recorded or []:
        path = Path(text).expanduser()
        if any(_within(base, path) for base in allowed_bases):
            known.append(path)
    removed: list[str] = []
    for path in dict.fromkeys(known):
        if path.is_symlink() or path.is_file():
            path.unlink()
            removed.append(str(path))
    return removed


def validate_user_data_path(path: Path, package: Package, cache: bool = False) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    bases = [Path.home() / ".cache"] if cache else [Path.home() / ".config", Path.home() / ".local" / "share"]
    if not any(_within(base, resolved) and resolved != base.resolve() for base in bases):
        raise RuntimeError(f"Refusing unsafe user-data deletion path: {resolved}")
    leaf = resolved.name.lower()
    tokens = {package.identifier.lower(), re.sub(r"[^a-z0-9]", "", package.name.lower())}
    if not any(token and (token in leaf or token in re.sub(r"[^a-z0-9]", "", leaf)) for token in tokens):
        raise RuntimeError(f"User-data path does not clearly belong to {package.identifier}: {resolved}")
    return resolved
