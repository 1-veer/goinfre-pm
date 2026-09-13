from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import shutil
import tempfile
from typing import Any

from .branding import SLUG
from .models import InstalledPackage, validate_package_id

CONFIG_DIR = Path.home() / ".config" / SLUG
SETTINGS_FILE = CONFIG_DIR / "config.json"
STATE_FILE = CONFIG_DIR / "state.json"


@dataclass(frozen=True)
class Layout:
    root: Path
    apps: Path
    downloads: Path
    logs: Path

    @classmethod
    def at(cls, root: Path) -> "Layout":
        root = root.expanduser().resolve()
        return cls(root, root / "apps", root / "downloads", root / "logs")

    def create(self) -> None:
        for path in (self.root, self.apps, self.downloads, self.logs):
            path.mkdir(parents=True, exist_ok=True)


def _with_slug(path: Path) -> Path:
    path = path.expanduser()
    return path if path.name == SLUG else path / SLUG


def is_writable_directory(path: Path, create: bool = False) -> bool:
    try:
        if create:
            path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir() or not os.access(path, os.W_OK | os.X_OK):
            return False
        probe = path / f".gpm-write-{os.getpid()}"
        probe.write_text("ok", encoding="ascii")
        probe.unlink()
        return True
    except OSError:
        return False


def read_settings(path: Path = SETTINGS_FILE) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def atomic_json_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def persist_root(root: Path, path: Path = SETTINGS_FILE) -> None:
    atomic_json_write(path, {"install_root": str(root.expanduser().resolve())})


def resolve_install_root(explicit: Path | None = None, settings_file: Path = SETTINGS_FILE) -> Path | None:
    """Resolve only known goinfre candidates; never invent a home fallback."""
    if explicit is not None:
        return explicit.expanduser().resolve()
    configured = read_settings(settings_file).get("install_root")
    if isinstance(configured, str) and configured:
        candidate = Path(configured).expanduser()
        if is_writable_directory(candidate):
            return candidate.resolve()
    env_root = os.environ.get("GOINFRE")
    if env_root:
        candidate = _with_slug(Path(env_root))
        if is_writable_directory(candidate, create=True):
            return candidate.resolve()
    user = os.environ.get("USER") or Path.home().name
    system_candidate = Path("/goinfre") / user / SLUG
    if is_writable_directory(system_candidate, create=True):
        return system_candidate.resolve()
    home_goinfre = Path.home() / "goinfre"
    if home_goinfre.exists():
        candidate = home_goinfre / SLUG
        if is_writable_directory(candidate, create=True):
            return candidate.resolve()
    return None


def verify_install_root(root: Path, allow_explicit: bool = False) -> None:
    resolved = root.expanduser().resolve()
    if not is_writable_directory(resolved):
        raise RuntimeError(f"Installation root is missing or not writable: {resolved}")
    configured = read_settings().get("install_root")
    env_root = os.environ.get("GOINFRE")
    recognized = (
        resolved.name == SLUG
        and ("goinfre" in {part.lower() for part in resolved.parts} or (env_root and resolved.is_relative_to(_with_slug(Path(env_root)).resolve())))
    )
    if not recognized and not allow_explicit and configured != str(resolved):
        raise RuntimeError(f"Refusing unconfirmed non-goinfre path: {resolved}. Use `gpm path set` first.")


def available_space(root: Path) -> int:
    return shutil.disk_usage(root).free


class StateStore:
    def __init__(self, path: Path = STATE_FILE) -> None:
        self.path = path

    def read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return self.empty()
            data.setdefault("desired", [])
            data.setdefault("installed", {})
            data.setdefault("autostart", False)
            return data
        except (OSError, json.JSONDecodeError):
            return self.empty()

    @staticmethod
    def empty() -> dict[str, Any]:
        return {"schema": 1, "desired": [], "installed": {}, "autostart": False}

    def write(self, data: dict[str, Any]) -> None:
        atomic_json_write(self.path, data)

    def set_installed(self, record: InstalledPackage, desired: bool = True, install_root: Path | None = None) -> None:
        data = self.read()
        if install_root is not None:
            data["install_root"] = str(install_root.expanduser().resolve())
        data["installed"][record.identifier] = {
            "version": record.version,
            "source": record.source,
            "executable": record.executable,
            "launchers": record.launchers,
            "installed_at": record.installed_at,
        }
        if desired and record.identifier not in data["desired"]:
            data["desired"].append(record.identifier)
            data["desired"].sort()
        self.write(data)

    def remove(self, identifier: str, keep_desired: bool = False) -> None:
        validate_package_id(identifier)
        data = self.read()
        data["installed"].pop(identifier, None)
        if not keep_desired:
            data["desired"] = [item for item in data["desired"] if item != identifier]
        self.write(data)
