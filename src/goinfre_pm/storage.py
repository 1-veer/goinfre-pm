from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import shutil
import tempfile
import threading
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
    runtime: Path
    logs: Path

    @classmethod
    def at(cls, root: Path) -> "Layout":
        root = root.expanduser().resolve()
        return cls(root, root / "apps", root / "downloads", root / "runtime", root / "logs")

    def create(self) -> None:
        for path in (self.root, self.apps, self.downloads, self.runtime, self.logs):
            if path.is_symlink():
                raise RuntimeError(f"Refusing symlinked storage directory: {path}")
            if path.exists() and not path.is_dir():
                raise RuntimeError(f"Storage path is not a directory: {path}")
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
    """Small roaming preferences stored in the user's persistent home."""

    def __init__(self, path: Path = STATE_FILE) -> None:
        self.path = path
        self._lock = threading.RLock()

    def read(self) -> dict[str, Any]:
        with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    return self.empty()
                old_schema = data.get("schema", 1)
                if not isinstance(old_schema, int):
                    old_schema = 1
                if old_schema < 3:
                    legacy = data.get("installed", {})
                    data["legacy_installed"] = legacy if isinstance(legacy, dict) else {}
                    data["legacy_desired"] = data.get("desired", []) if isinstance(data.get("desired"), list) else []
                    data.pop("installed", None)
                    data.pop("desired", None)
                    data["setup_enabled"] = False
                    data["setup_packages"] = []
                    data["legacy_migration_pending"] = True
                data["schema"] = 3
                if not isinstance(data.get("setup_enabled"), bool):
                    data["setup_enabled"] = False
                if not isinstance(data.get("setup_packages"), list):
                    data["setup_packages"] = []
                data["setup_packages"] = self._valid_identifiers(data["setup_packages"])
                if not isinstance(data.get("autostart"), bool):
                    data["autostart"] = False
                if not isinstance(data.get("favorites"), list):
                    data["favorites"] = []
                data["favorites"] = sorted({item for item in data["favorites"] if isinstance(item, str)})
                if not isinstance(data.get("onboarding_complete"), bool):
                    data["onboarding_complete"] = False
                if not isinstance(data.get("update_cache"), dict):
                    data["update_cache"] = {}
                if not isinstance(data.get("legacy_installed"), dict):
                    data["legacy_installed"] = {}
                if not isinstance(data.get("legacy_desired"), list):
                    data["legacy_desired"] = []
                if not isinstance(data.get("legacy_migration_pending"), bool):
                    data["legacy_migration_pending"] = False
                return data
            except (OSError, json.JSONDecodeError):
                return self.empty()

    @staticmethod
    def empty() -> dict[str, Any]:
        return {
            "schema": 3,
            "setup_enabled": False,
            "setup_packages": [],
            "autostart": False,
            "favorites": [],
            "onboarding_complete": False,
            "update_cache": {},
            "legacy_installed": {},
            "legacy_desired": [],
            "legacy_migration_pending": False,
        }

    @staticmethod
    def _valid_identifiers(values: list[Any]) -> list[str]:
        valid: set[str] = set()
        for item in values:
            if not isinstance(item, str):
                continue
            try:
                valid.add(validate_package_id(item))
            except ValueError:
                continue
        return sorted(valid)

    def write(self, data: dict[str, Any]) -> None:
        with self._lock:
            atomic_json_write(self.path, data)

    def set_setup_package(self, identifier: str, selected: bool) -> None:
        self.set_setup_packages([identifier], selected)

    def set_setup_packages(self, identifiers: list[str], selected: bool) -> None:
        identifiers = [validate_package_id(identifier) for identifier in identifiers]
        with self._lock:
            data = self.read()
            packages = set(data["setup_packages"])
            if selected:
                packages.update(identifiers)
            else:
                packages.difference_update(identifiers)
            data["setup_packages"] = sorted(packages)
            if selected and identifiers:
                data["setup_enabled"] = True
            self.write(data)

    def set_setup_enabled(self, enabled: bool) -> None:
        with self._lock:
            data = self.read()
            data["setup_enabled"] = bool(enabled)
            self.write(data)

    def finish_legacy_migration(self) -> None:
        with self._lock:
            data = self.read()
            data.pop("legacy_installed", None)
            data.pop("legacy_desired", None)
            data["legacy_migration_pending"] = False
            self.write(data)

    def set_favorite(self, identifier: str, favorite: bool) -> None:
        validate_package_id(identifier)
        with self._lock:
            data = self.read()
            favorites = set(data["favorites"])
            favorites.add(identifier) if favorite else favorites.discard(identifier)
            data["favorites"] = sorted(favorites)
            self.write(data)

    def set_onboarding_complete(self, complete: bool = True) -> None:
        with self._lock:
            data = self.read()
            data["onboarding_complete"] = bool(complete)
            self.write(data)

    def set_update_cache(self, identifier: str, value: dict[str, Any]) -> None:
        validate_package_id(identifier)
        with self._lock:
            data = self.read()
            data["update_cache"][identifier] = value
            self.write(data)


class LocalStateStore:
    """Installation records that live beside payloads in one goinfre root."""

    def __init__(self, layout: Layout, path: Path | None = None) -> None:
        self.root = layout.root.expanduser().resolve()
        self.path = path or layout.runtime / "installed.json"
        self._lock = threading.RLock()

    def empty(self) -> dict[str, Any]:
        return {"schema": 1, "install_root": str(self.root), "installed": {}}

    def read(self) -> dict[str, Any]:
        with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return self.empty()
            if not isinstance(data, dict) or data.get("install_root") != str(self.root):
                return self.empty()
            installed = data.get("installed", {})
            if not isinstance(installed, dict):
                installed = {}
            return {"schema": 1, "install_root": str(self.root), "installed": installed}

    def write(self, data: dict[str, Any]) -> None:
        with self._lock:
            normalized = self.empty()
            installed = data.get("installed", {})
            normalized["installed"] = installed if isinstance(installed, dict) else {}
            atomic_json_write(self.path, normalized)

    @staticmethod
    def _record(record: InstalledPackage) -> dict[str, Any]:
        return {
            "version": record.version,
            "source": record.source,
            "executable": record.executable,
            "launchers": record.launchers,
            "installed_at": record.installed_at,
            "download_size": record.download_size,
            "installed_size": record.installed_size,
        }

    def set_installed(self, record: InstalledPackage) -> None:
        validate_package_id(record.identifier)
        with self._lock:
            data = self.read()
            data["installed"][record.identifier] = self._record(record)
            self.write(data)

    def remove(self, identifier: str) -> None:
        validate_package_id(identifier)
        with self._lock:
            data = self.read()
            data["installed"].pop(identifier, None)
            self.write(data)
