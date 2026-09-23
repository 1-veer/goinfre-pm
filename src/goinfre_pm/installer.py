from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import socket
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import uuid

from . import integration as integration_module
from .downloader import DownloadCancelled, download
from .extractor import extract_download
from .integration import (
    find_executable,
    integrate,
    remove_integration,
    remove_integration_by_identifier,
    validate_user_data_path,
)
from .models import InstalledPackage, Package, validate_package_id
from .storage import Layout, LocalStateStore, StateStore, available_space, verify_install_root

Event = tuple[str, object]


@dataclass(frozen=True)
class InstallationCheck:
    status: str
    executable: Path | None = None
    reason: str = ""

    @property
    def payload_present(self) -> bool:
        return self.status in {"installed", "repairable"}

    @property
    def healthy(self) -> bool:
        return self.status == "installed"


@dataclass(frozen=True)
class CleanupItem:
    path: Path
    kind: str
    size: int


@dataclass(frozen=True)
class CleanupReport:
    items: tuple[CleanupItem, ...]

    @property
    def total_bytes(self) -> int:
        return sum(item.size for item in self.items)

    @property
    def count(self) -> int:
        return len(self.items)


@dataclass(frozen=True)
class LeavePostReport:
    bytes_removed: int
    integrations_removed: int
    root_removed: bool


@dataclass(frozen=True)
class PostStorageReport:
    """A read-only preview of manager-owned data on the current post."""

    total_bytes: int
    entries: int

    @property
    def has_data(self) -> bool:
        return self.entries > 0


class OperationBusyError(RuntimeError):
    """Another process currently owns this install root's operation lock."""


def _current_boot_id() -> str:
    """Return a Linux boot identifier so roaming goinfre locks are post-local."""
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _linux_process_start(pid: int) -> str:
    """Return Linux's per-boot process start tick, or an empty string."""
    try:
        # Field 22 follows the parenthesized process name. Splitting at the
        # final closing parenthesis also handles spaces or parentheses in it.
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(") ", 1)[1].split()
        return fields[19]
    except (OSError, IndexError):
        return ""


def _cleanup_entry_size(path: Path) -> int:
    try:
        if path.is_symlink():
            return path.lstat().st_size
        if path.is_dir():
            return directory_size(path)
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def _is_stale_app_workdir(name: str) -> bool:
    if not name.startswith("."):
        return False
    for marker in (".stage-", ".backup-"):
        if marker not in name:
            continue
        identifier, token = name[1:].rsplit(marker, 1)
        try:
            validate_package_id(identifier)
        except ValueError:
            return False
        return len(token) == 32 and all(character in "0123456789abcdef" for character in token)
    return False


def cleanup_report(layout: Layout, now: float | None = None, log_retention_days: int = 30) -> CleanupReport:
    """Find only disposable files in manager-owned storage directories."""
    now = time.time() if now is None else now
    cutoff = now - log_retention_days * 24 * 60 * 60
    items: list[CleanupItem] = []
    if layout.downloads.is_dir():
        for path in layout.downloads.iterdir():
            items.append(CleanupItem(path, "temporary download", _cleanup_entry_size(path)))
    if layout.apps.is_dir():
        for path in layout.apps.iterdir():
            if _is_stale_app_workdir(path.name):
                items.append(CleanupItem(path, "stale installation workdir", _cleanup_entry_size(path)))
    if layout.logs.is_dir():
        for path in layout.logs.iterdir():
            try:
                validate_package_id(path.stem)
                old_enough = path.suffix == ".log" and path.lstat().st_mtime < cutoff
            except (OSError, ValueError):
                old_enough = False
            if old_enough:
                items.append(CleanupItem(path, "old log", _cleanup_entry_size(path)))
    return CleanupReport(tuple(sorted(items, key=lambda item: str(item.path))))


def _validate_cleanup_item(layout: Layout, item: CleanupItem) -> None:
    approved = (
        item.kind == "temporary download" and item.path.parent == layout.downloads
        or item.kind == "stale installation workdir"
        and item.path.parent == layout.apps
        and _is_stale_app_workdir(item.path.name)
        or item.kind == "old log" and item.path.parent == layout.logs and item.path.suffix == ".log"
    )
    if not approved or item.path.name in {"", ".", ".."}:
        raise RuntimeError(f"Refusing unsafe cleanup target: {item.path}")


class OperationLock:
    """Root-local process lock preventing overlapping payload mutations."""

    def __init__(self, layout: Layout) -> None:
        self.path = layout.runtime / "operation.lock"
        self.status_path = layout.runtime / "operation-status.json"
        self.token = uuid.uuid4().hex
        self.hostname = socket.gethostname()
        self.boot_id = _current_boot_id()
        self.process_start = _linux_process_start(os.getpid())
        self.acquired = False

    def __enter__(self) -> "OperationLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _attempt in range(2):
            try:
                descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                if self._remove_stale():
                    continue
                raise OperationBusyError("Another GoinfrePM operation is already active for this install root")
            payload = json.dumps(
                {
                    "pid": os.getpid(),
                    "token": self.token,
                    "created_at": time.time(),
                    "hostname": self.hostname,
                    "boot_id": self.boot_id,
                    "process_start": self.process_start,
                }
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            self.acquired = True
            return self
        raise RuntimeError("Could not acquire the GoinfrePM operation lock")

    def publish_status(self, **status: object) -> None:
        """Atomically expose small, non-sensitive progress for another TUI."""
        if not self.acquired:
            return
        payload = {
            "token": self.token,
            "pid": os.getpid(),
            "hostname": self.hostname,
            "boot_id": self.boot_id,
            "updated_at": time.time(),
            **status,
        }
        temporary: Path | None = None
        try:
            descriptor, temporary_text = tempfile.mkstemp(
                prefix=".operation-status-",
                dir=self.status_path.parent,
            )
            temporary = Path(temporary_text)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.status_path)
        except (OSError, TypeError, ValueError):
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def peer_status(self) -> dict[str, object] | None:
        """Read progress only when it belongs to the process owning the lock."""
        try:
            lock_value = json.loads(self.path.read_text(encoding="utf-8"))
            status = json.loads(self.status_path.read_text(encoding="utf-8"))
            if not isinstance(lock_value, dict) or not isinstance(status, dict):
                return None
            if not lock_value.get("token") or status.get("token") != lock_value.get("token"):
                return None
            return status
        except (OSError, json.JSONDecodeError, TypeError):
            return None

    def _remove_status(self, token: object) -> None:
        try:
            status = json.loads(self.status_path.read_text(encoding="utf-8"))
            if isinstance(status, dict) and status.get("token") == token:
                self.status_path.unlink()
        except (OSError, json.JSONDecodeError, TypeError):
            pass

    def _remove_stale(self) -> bool:
        try:
            original = self.path.stat()
            value = json.loads(self.path.read_text(encoding="utf-8"))
            pid = int(value.get("pid", 0))
        except FileNotFoundError:
            return True
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # A competing process may have created the file but not yet written
            # its metadata. Never remove that fresh lock during this tiny gap.
            try:
                original = self.path.stat()
                if time.time() - original.st_mtime < 5:
                    return False
            except FileNotFoundError:
                return True
            except OSError:
                return False
            pid = 0
            value = {}

        recorded_hostname = str(value.get("hostname", ""))
        recorded_boot_id = str(value.get("boot_id", ""))
        try:
            created_at = float(value.get("created_at", 0))
        except (TypeError, ValueError):
            created_at = 0
        recorded_process_start = str(value.get("process_start", ""))

        # Goinfre follows the user between school posts, but PIDs do not. A
        # lock from a different hostname or Linux boot is always stale even if
        # an unrelated process on this post happens to reuse the same PID.
        different_post = bool(
            recorded_hostname
            and self.hostname
            and recorded_hostname != self.hostname
            or recorded_boot_id
            and self.boot_id
            and recorded_boot_id != self.boot_id
        )
        legacy_lock_expired = bool(
            not recorded_hostname
            and not recorded_boot_id
            and created_at > 0
            and time.time() - created_at >= 2 * 60 * 60
        )

        pid_still_owns_lock = True
        if recorded_process_start and pid > 0:
            live_process_start = _linux_process_start(pid)
            if live_process_start and live_process_start != recorded_process_start:
                pid_still_owns_lock = False

        if not different_post and not legacy_lock_expired and pid > 0 and pid_still_owns_lock:
            try:
                os.kill(pid, 0)
                return False
            except PermissionError:
                return False
            except ProcessLookupError:
                pass
        try:
            current = self.path.stat()
            if (current.st_ino, current.st_mtime_ns) != (original.st_ino, original.st_mtime_ns):
                return False
            self.path.unlink()
            self._remove_status(value.get("token"))
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False

    def __exit__(self, _error_type: object, _error: object, _traceback: object) -> None:
        if not self.acquired:
            return
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if value.get("token") == self.token:
                self._remove_status(self.token)
                self.path.unlink()
        except (OSError, json.JSONDecodeError):
            pass
        self.acquired = False


def directory_size(root: Path) -> int:
    """Return regular-file bytes without following links outside an install."""
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except OSError:
            continue
    return total


class PackageManager:
    def __init__(
        self,
        layout: Layout,
        packages: list[Package],
        state: StateStore | None = None,
        installations: LocalStateStore | None = None,
    ) -> None:
        self.layout = layout
        self.packages = {package.identifier: package for package in packages}
        self.state = state or StateStore()
        self.layout.create()
        self.installations = installations or LocalStateStore(layout)
        self._migrate_legacy_state()

    def package(self, identifier: str) -> Package:
        try:
            return self.packages[identifier]
        except KeyError as exc:
            raise RuntimeError(f"Unknown package: {identifier}") from exc

    def installation(self, identifier: str) -> InstallationCheck:
        package = self.package(identifier)
        live = self.layout.apps / identifier
        if not live.is_dir():
            return InstallationCheck("missing", reason="application payload is absent from this post")
        executable = find_executable(live, package)
        if executable is None or not self._valid_executable(live, executable):
            return InstallationCheck("missing", reason="application executable is missing or invalid")
        record = self.installations.read()["installed"].get(identifier)
        if not isinstance(record, dict):
            return InstallationCheck("repairable", executable, "root-local installation record is missing")
        recorded = Path(str(record.get("executable", ""))).expanduser()
        try:
            same_executable = recorded.resolve() == executable.resolve()
        except OSError:
            same_executable = False
        command = integration_module.USER_BIN / identifier
        try:
            command_ok = command.is_symlink() and command.resolve(strict=True) == executable.resolve(strict=True)
        except OSError:
            command_ok = False
        desktop_ok = self._desktop_launcher_ok(package, executable)
        if not same_executable or not command_ok or not desktop_ok:
            return InstallationCheck("repairable", executable, "launcher or local installation metadata needs repair")
        return InstallationCheck("installed", executable)

    def installed(self, identifier: str) -> bool:
        return self.installation(identifier).payload_present

    def launch(self, identifier: str) -> int:
        package = self.package(identifier)
        condition = self.installation(identifier)
        executable = condition.executable
        live = self.layout.apps / identifier
        if executable is None or not self._valid_executable(live, executable):
            raise RuntimeError(f"{package.name} cannot be launched because its executable is missing; reinstall it")
        process = subprocess.Popen(
            [str(executable)],
            cwd=str(executable.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        return process.pid

    def cleanup_report(self) -> CleanupReport:
        return cleanup_report(self.layout)

    def cleanup(self) -> CleanupReport:
        """Remove a freshly revalidated set of safe temporary files."""
        with OperationLock(self.layout):
            report = cleanup_report(self.layout)
            for item in report.items:
                path = item.path
                _validate_cleanup_item(self.layout, item)
                if path.is_symlink() or path.is_file():
                    path.unlink(missing_ok=True)
                elif path.is_dir():
                    shutil.rmtree(path)
            return report

    def _leave_post_targets(self) -> tuple[Path, ...]:
        """Return validated immediate children that GoinfrePM may remove."""
        verify_install_root(self.layout.root)
        root = self.layout.root.resolve()
        if root in {Path("/"), Path.home().resolve()}:
            raise RuntimeError(f"Refusing unsafe cleanup root: {root}")

        owned = (
            self.layout.apps,
            self.layout.downloads,
            self.layout.logs,
            root / "venv",
            self.layout.runtime,
        )
        for path in owned:
            if path.parent.resolve() != root or path.is_symlink():
                raise RuntimeError(f"Refusing unsafe post cleanup target: {path}")
        return owned

    def post_storage_report(self) -> PostStorageReport:
        """Measure removable post-local data without following symlinks."""
        owned = self._leave_post_targets()
        entries = 0
        for path in owned:
            if path.is_dir():
                try:
                    entries += sum(1 for _item in path.iterdir())
                except OSError:
                    continue
            elif path.exists():
                entries += 1
        return PostStorageReport(
            total_bytes=sum(_cleanup_entry_size(path) for path in owned),
            entries=entries,
        )

    def leave_post(self) -> LeavePostReport:
        """Remove this post's GoinfrePM storage without touching roaming preferences."""
        root = self.layout.root.resolve()
        owned = self._leave_post_targets()
        bytes_removed = sum(_cleanup_entry_size(path) for path in owned)
        integrations_removed = 0
        with OperationLock(self.layout):
            installation_data = self.installations.read().get("installed", {})
            identifiers = set(self.packages)
            if isinstance(installation_data, dict):
                for identifier in installation_data:
                    if not isinstance(identifier, str):
                        continue
                    try:
                        identifiers.add(validate_package_id(identifier))
                    except ValueError:
                        continue
            for identifier in sorted(identifiers):
                integrations_removed += len(remove_integration_by_identifier(identifier))

            for path in owned:
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.exists():
                    raise RuntimeError(f"Storage target is not a directory: {path}")

        root_removed = False
        try:
            root.rmdir()
            root_removed = True
        except OSError:
            # A custom root may contain unrelated user files. They are never
            # deleted merely to make the directory disappear.
            pass
        return LeavePostReport(bytes_removed, integrations_removed, root_removed)

    def package_status(self, identifier: str) -> str:
        condition = self.installation(identifier)
        if condition.healthy:
            return "installed"
        if condition.status == "repairable":
            return "repairable"
        setup = set(self.state.read().get("setup_packages", []))
        return "needs_restore" if identifier in setup else "not_installed"

    @staticmethod
    def _valid_executable(live: Path, executable: Path) -> bool:
        try:
            executable.resolve(strict=True).relative_to(live.resolve(strict=True))
            mode = executable.stat().st_mode
            return stat.S_ISREG(mode) and os.access(executable, os.X_OK)
        except (OSError, ValueError):
            return False

    @staticmethod
    def _desktop_launcher_ok(package: Package, executable: Path) -> bool:
        if not package.desktop:
            return True
        launcher = integration_module.DESKTOP_DIR / f"{package.identifier}.desktop"
        try:
            expected = next(
                line for line in integration_module.desktop_entry(package, executable).splitlines()
                if line.startswith("Exec=")
            )
            return expected in launcher.read_text(encoding="utf-8").splitlines()
        except (OSError, StopIteration, UnicodeError):
            return False

    def _migrate_legacy_state(self) -> None:
        preferences = self.state.read()
        if not preferences.get("legacy_migration_pending"):
            return
        legacy = preferences.get("legacy_installed", {})
        if isinstance(legacy, dict):
            for identifier, value in legacy.items():
                if identifier not in self.packages or not isinstance(value, dict):
                    continue
                package = self.packages[identifier]
                live = self.layout.apps / identifier
                executable = find_executable(live, package) if live.is_dir() else None
                if executable is None or not self._valid_executable(live, executable):
                    continue
                previous = InstalledPackage.from_dict(identifier, value)
                previous.executable = str(executable)
                self.installations.set_installed(previous)
        self.state.finish_legacy_migration()

    @staticmethod
    def _apply_actions(package: Package, staging: Path) -> None:
        for action in package.post_install:
            source = staging / action.source
            target = staging / action.target if action.target else staging
            try:
                source.resolve(strict=False).relative_to(staging.resolve())
                target.resolve(strict=False).relative_to(staging.resolve())
            except ValueError as exc:
                raise RuntimeError(f"Unsafe post-install path for {package.identifier}") from exc
            if action.action == "chmod":
                if not source.is_file():
                    raise RuntimeError(f"Post-install chmod source is missing: {action.source}")
                mode = int(action.mode or "755", 8)
                if mode & ~0o777:
                    raise RuntimeError(f"Unsafe chmod mode: {action.mode}")
                source.chmod(mode)
            elif action.action == "symlink":
                if not source.exists() or not action.target:
                    raise RuntimeError("Structured symlink action requires existing source and target")
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() or target.is_symlink():
                    target.unlink()
                target.symlink_to(os.path.relpath(source, target.parent))

    def install(
        self,
        identifier: str,
        cancel: threading.Event | None = None,
        progress_callback: Callable[[float], None] | None = None,
        transfer_callback: Callable[[int, int | None, float, float | None], None] | None = None,
    ) -> Iterator[Event]:
        with OperationLock(self.layout):
            package = self.package(identifier)
            condition = self.installation(identifier)
            if condition.healthy:
                raise RuntimeError(
                    f"{package.name} is already installed; use update or explicit reinstall instead"
                )
            if condition.status == "repairable":
                raise RuntimeError(
                    f"{package.name} already has a payload that needs repair; "
                    "use repair, update, or explicit reinstall instead"
                )
            yield from self._install(identifier, cancel, progress_callback, transfer_callback, "Installed")

    def update(
        self,
        identifier: str,
        cancel: threading.Event | None = None,
        progress_callback: Callable[[float], None] | None = None,
        transfer_callback: Callable[[int, int | None, float, float | None], None] | None = None,
    ) -> Iterator[Event]:
        with OperationLock(self.layout):
            package = self.package(identifier)
            if not self.installed(identifier):
                raise RuntimeError(f"{package.name} is not installed; use the install command instead")
            yield from self._install(identifier, cancel, progress_callback, transfer_callback, "Updated")

    def reinstall(
        self,
        identifier: str,
        cancel: threading.Event | None = None,
        progress_callback: Callable[[float], None] | None = None,
        transfer_callback: Callable[[int, int | None, float, float | None], None] | None = None,
    ) -> Iterator[Event]:
        with OperationLock(self.layout):
            package = self.package(identifier)
            if not self.installed(identifier):
                raise RuntimeError(f"{package.name} is not installed; use the install command instead")
            yield from self._install(identifier, cancel, progress_callback, transfer_callback, "Reinstalled")

    def _install(
        self,
        identifier: str,
        cancel: threading.Event | None = None,
        progress_callback: Callable[[float], None] | None = None,
        transfer_callback: Callable[[int, int | None, float, float | None], None] | None = None,
        completed_action: str = "Installed",
    ) -> Iterator[Event]:
        package = self.package(identifier)
        if not package.enabled:
            reason = package.notes or "this catalog entry is no longer supported"
            raise RuntimeError(f"{package.name} is unavailable: {reason}")
        if not package.compatible:
            raise RuntimeError(f"{package.name} does not support this machine architecture")
        verify_install_root(self.layout.root)
        self.layout.create()
        free_space = available_space(self.layout.root)
        minimum = 100 * 1024 * 1024
        known_peak = (package.download_size or 0) + (package.installed_size or 0)
        required = max(minimum, known_peak)
        if free_space < required:
            raise RuntimeError(
                f"Not enough goinfre space for {package.name}: "
                f"{required / 1024**2:.1f} MiB known peak required, {free_space / 1024**2:.1f} MiB available"
            )
        log_file = self.layout.logs / f"{identifier}.log"

        def log(message: str) -> None:
            timestamp = datetime.now().strftime("%H:%M:%S")
            with log_file.open("a", encoding="utf-8") as handle:
                handle.write(f"{timestamp} {message}\n")

        with tempfile.TemporaryDirectory(prefix=f"{identifier}-", dir=self.layout.downloads) as operation_text:
            operation = Path(operation_text)
            staging = self.layout.apps / f".{identifier}.stage-{uuid.uuid4().hex}"
            backup = self.layout.apps / f".{identifier}.backup-{uuid.uuid4().hex}"
            live = self.layout.apps / identifier
            old_moved = False
            promoted = False
            download_started = time.monotonic()
            last_transfer_emit = 0.0
            try:
                yield ("log", f"Downloading {package.name}")

                def on_progress(done: int, total: int | None) -> None:
                    nonlocal last_transfer_emit
                    elapsed = max(time.monotonic() - download_started, 0.001)
                    speed = done / elapsed
                    eta = (total - done) / speed if total is not None and speed > 0 else None
                    now = time.monotonic()
                    if now - last_transfer_emit >= 0.2 or (total is not None and done == total):
                        log(f"download {done}/{total or '?'} bytes")
                        if transfer_callback:
                            transfer_callback(done, total, speed, eta)
                        if total and progress_callback:
                            progress_callback(min(40.0, done / total * 40.0))
                        last_transfer_emit = now

                archive, version = download(package, operation, on_progress, log, cancel)
                if cancel is not None and cancel.is_set():
                    raise DownloadCancelled("Installation cancelled")
                yield ("progress", 45)
                extract_download(archive, staging, package.source_type, operation)
                if cancel is not None and cancel.is_set():
                    raise DownloadCancelled("Installation cancelled")
                self._apply_actions(package, staging)
                yield ("progress", 75)
                executable = find_executable(staging, package)
                if executable is None:
                    raise RuntimeError(f"Could not locate an executable for {package.name}; check executable candidates")
                executable_relative = executable.relative_to(staging)
                if live.exists():
                    os.replace(live, backup)
                    old_moved = True
                os.replace(staging, live)
                promoted = True
                executable = live / executable_relative
                launchers = integrate(package, executable, live)
                installed_size = directory_size(live)
                record = InstalledPackage(
                    identifier=identifier,
                    version=version,
                    source=package.url,
                    executable=str(executable),
                    launchers=launchers,
                    installed_at=datetime.now(timezone.utc).isoformat(),
                    download_size=archive.stat().st_size,
                    installed_size=installed_size,
                )
                self.installations.set_installed(record)
                if backup.exists():
                    shutil.rmtree(backup, ignore_errors=True)
                yield ("progress", 100)
                yield ("log", f"{completed_action} {package.name} {version}")
            except BaseException:
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
                if promoted and live.exists():
                    shutil.rmtree(live, ignore_errors=True)
                if old_moved and backup.exists():
                    os.replace(backup, live)
                    try:
                        restored_executable = find_executable(live, package)
                        if restored_executable is not None:
                            integrate(package, restored_executable, live)
                    except (OSError, RuntimeError):
                        pass
                elif promoted:
                    try:
                        remove_integration(package)
                    except (OSError, RuntimeError):
                        pass
                raise

    def repair(self, identifier: str) -> list[str]:
        with OperationLock(self.layout):
            return self._repair(identifier)

    def _repair(self, identifier: str) -> list[str]:
        package = self.package(identifier)
        live = self.layout.apps / identifier
        if not live.is_dir():
            raise RuntimeError(f"{package.name} is not installed")
        executable = find_executable(live, package)
        if executable is None:
            raise RuntimeError(f"No executable found for {package.name}")
        launchers = integrate(package, executable, live)
        state = self.installations.read()
        existing = state.get("installed", {}).get(identifier, {})
        record = InstalledPackage(
            identifier=identifier,
            version=str(existing.get("version", package.version)),
            source=str(existing.get("source", package.url)),
            executable=str(executable),
            launchers=launchers,
            installed_at=str(existing.get("installed_at", "")),
            download_size=existing.get("download_size") if isinstance(existing.get("download_size"), int) else None,
            installed_size=directory_size(live),
        )
        self.installations.set_installed(record)
        return launchers

    def remove(
        self,
        identifier: str,
        remove_cache: bool = False,
        remove_config: bool = False,
        keep_setup: bool = False,
    ) -> Iterator[Event]:
        package = self.package(identifier)
        cache_target: Path | None = None
        config_targets: list[Path] = []
        if remove_cache:
            cache_target = validate_user_data_path(Path.home() / ".cache" / identifier, package, cache=True)
        if remove_config:
            if not package.remove_user_config:
                raise RuntimeError(f"Configuration removal is not enabled for {package.name}")
            config_targets = [
                validate_user_data_path(Path(configured), package, cache=False)
                for configured in package.config_paths
            ]
        with OperationLock(self.layout):
            live = self.layout.apps / identifier
            if live.parent.resolve() != self.layout.apps.resolve() or live.name != identifier:
                raise RuntimeError(f"Unsafe application removal target: {live}")
            setup_was_selected = identifier in set(self.state.read().get("setup_packages", []))
            if setup_was_selected and not keep_setup:
                # Forget first so a successful removal can never be unexpectedly
                # restored. If the root mutation fails, restore the preference.
                self.state.set_setup_package(identifier, False)
            try:
                state = self.installations.read()
                record = state.get("installed", {}).get(identifier, {})
                remove_integration(package, record.get("launchers", []) if isinstance(record, dict) else [])
                if live.is_dir():
                    shutil.rmtree(live)
                    yield ("log", f"Removed application files for {package.name}")
                if cache_target is not None and cache_target.exists():
                    shutil.rmtree(cache_target) if cache_target.is_dir() else cache_target.unlink()
                    yield ("log", f"Removed cache {cache_target}")
                for target in config_targets:
                    if target.exists():
                        shutil.rmtree(target) if target.is_dir() else target.unlink()
                        yield ("log", f"Removed user configuration {target}")
                self.installations.remove(identifier)
            except BaseException:
                if setup_was_selected and not keep_setup:
                    self.state.set_setup_package(identifier, True)
                raise

    def restore(
        self,
        cancel: threading.Event | None = None,
        identifiers: list[str] | None = None,
        progress_callback: Callable[[float], None] | None = None,
        transfer_callback: Callable[[int, int | None, float, float | None], None] | None = None,
        wait_timeout: float = 30.0,
    ) -> Iterator[Event]:
        verify_install_root(self.layout.root)
        raw_identifiers = self.state.read().get("setup_packages", []) if identifiers is None else identifiers
        setup = set(self.state.read().get("setup_packages", []))
        identifiers = [item for item in raw_identifiers if isinstance(item, str) and item in setup]
        lock = OperationLock(self.layout)
        wait_started = time.monotonic()
        total_wait_started = wait_started
        last_wait_notice = wait_started
        waiting_reported = False
        last_peer_update = 0.0
        while True:
            if cancel is not None and cancel.is_set():
                for identifier in identifiers:
                    yield ("cancelled", identifier)
                return
            try:
                lock.__enter__()
                break
            except OperationBusyError:
                if not waiting_reported:
                    yield (
                        "waiting",
                        "Auto Setup is already running elsewhere. Keep this window open; "
                        "no second terminal is needed.",
                    )
                    waiting_reported = True
                peer_status = lock.peer_status()
                if peer_status is not None:
                    try:
                        peer_update = float(peer_status.get("updated_at", 0))
                    except (TypeError, ValueError):
                        peer_update = 0
                    if peer_update > last_peer_update:
                        last_peer_update = peer_update
                        wait_started = time.monotonic()
                        yield ("peer_progress", peer_status)
                elif time.monotonic() - last_wait_notice >= 1:
                    last_wait_notice = time.monotonic()
                    waited = max(1, round(last_wait_notice - total_wait_started))
                    yield (
                        "wait_progress",
                        f"Auto Setup is running in another process · waiting {waited}s",
                    )
                if time.monotonic() - wait_started >= max(0.0, wait_timeout):
                    raise OperationBusyError(
                        "GoinfrePM is still finishing another task on this post"
                    )
                time.sleep(0.5)
        try:
            total = len(identifiers)
            for index, identifier in enumerate(identifiers, 1):
                if cancel is not None and cancel.is_set():
                    for remaining in identifiers[index - 1:]:
                        yield ("cancelled", remaining)
                    break
                yield ("package", (identifier, index, total))
                if identifier not in self.packages:
                    yield ("skipped", (identifier, "not present in this catalog"))
                    continue
                package = self.packages[identifier]
                if not package.enabled or not package.compatible:
                    yield ("skipped", (identifier, "unavailable or incompatible"))
                    continue
                lock.publish_status(
                    operation="restore",
                    phase="checking",
                    package=identifier,
                    package_name=package.name,
                    index=index,
                    total=total,
                    progress=0,
                )
                condition = self.installation(identifier)
                if condition.healthy:
                    if waiting_reported:
                        yield ("ready", identifier)
                        yield (
                            "log",
                            f"{package.name} was completed by another GoinfrePM session",
                        )
                    else:
                        yield ("skipped", (identifier, "already installed here"))
                elif condition.status == "repairable":
                    try:
                        lock.publish_status(
                            operation="restore",
                            phase="repairing",
                            package=identifier,
                            package_name=package.name,
                            index=index,
                            total=total,
                            progress=50,
                        )
                        self._repair(identifier)
                        yield ("restored", identifier)
                        yield ("log", f"Repaired {package.name} without downloading it again")
                    except Exception as exc:
                        yield ("failed", (identifier, str(exc)))
                else:
                    try:
                        def shared_progress(value: float) -> None:
                            phase = "downloading" if value <= 40 else "extracting" if value <= 75 else "finishing"
                            lock.publish_status(
                                operation="restore",
                                phase=phase,
                                package=identifier,
                                package_name=package.name,
                                index=index,
                                total=total,
                                progress=value,
                            )
                            if progress_callback is not None:
                                progress_callback(value)

                        def shared_transfer(
                            done: int,
                            size: int | None,
                            speed: float,
                            eta: float | None,
                        ) -> None:
                            lock.publish_status(
                                operation="restore",
                                phase="downloading",
                                package=identifier,
                                package_name=package.name,
                                index=index,
                                total=total,
                                progress=min(40.0, done / size * 40.0) if size else 0,
                                downloaded=done,
                                download_total=size,
                                speed=speed,
                                eta=eta,
                            )
                            if transfer_callback is not None:
                                transfer_callback(done, size, speed, eta)

                        for event in self._install(
                            identifier,
                            cancel,
                            shared_progress,
                            shared_transfer,
                            completed_action="Restored",
                        ):
                            yield event
                        yield ("restored", identifier)
                    except DownloadCancelled:
                        yield ("cancelled", identifier)
                        for remaining in identifiers[index:]:
                            yield ("cancelled", remaining)
                        break
                    except Exception as exc:
                        yield ("failed", (identifier, str(exc)))
        finally:
            lock.__exit__(None, None, None)
