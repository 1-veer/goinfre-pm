from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
import os
import shutil
import tempfile
import threading
import uuid

from .downloader import download
from .extractor import extract_download
from .integration import find_executable, integrate, remove_integration, validate_user_data_path
from .models import InstalledPackage, Package
from .storage import Layout, StateStore, available_space, verify_install_root

Event = tuple[str, object]


class PackageManager:
    def __init__(self, layout: Layout, packages: list[Package], state: StateStore | None = None) -> None:
        self.layout = layout
        self.packages = {package.identifier: package for package in packages}
        self.state = state or StateStore()

    def package(self, identifier: str) -> Package:
        try:
            return self.packages[identifier]
        except KeyError as exc:
            raise RuntimeError(f"Unknown package: {identifier}") from exc

    def installed(self, identifier: str) -> bool:
        return (self.layout.apps / identifier).is_dir()

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
    ) -> Iterator[Event]:
        package = self.package(identifier)
        if not package.enabled:
            reason = package.notes or "this catalog entry is no longer supported"
            raise RuntimeError(f"{package.name} is unavailable: {reason}")
        if not package.compatible:
            raise RuntimeError(f"{package.name} does not support this machine architecture")
        verify_install_root(self.layout.root)
        self.layout.create()
        if available_space(self.layout.root) < 100 * 1024 * 1024:
            raise RuntimeError("Less than 100 MiB is available in the selected goinfre root")
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
            try:
                yield ("log", f"Downloading {package.name}")

                def on_progress(done: int, total: int | None) -> None:
                    log(f"download {done}/{total or '?'} bytes")
                    if total and progress_callback:
                        progress_callback(min(40.0, done / total * 40.0))

                archive, version = download(package, operation, on_progress, log, cancel)
                yield ("progress", 45)
                extract_download(archive, staging, package.source_type, operation)
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
                executable = live / executable_relative
                launchers = integrate(package, executable, live)
                if backup.exists():
                    shutil.rmtree(backup)
                record = InstalledPackage(
                    identifier=identifier,
                    version=version,
                    source=package.url,
                    executable=str(executable),
                    launchers=launchers,
                    installed_at=datetime.now(timezone.utc).isoformat(),
                )
                self.state.set_installed(record, install_root=self.layout.root)
                yield ("progress", 100)
                yield ("log", f"Installed {package.name} {version}")
            except BaseException:
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
                if old_moved and backup.exists():
                    if live.exists():
                        shutil.rmtree(live, ignore_errors=True)
                    os.replace(backup, live)
                raise

    def repair(self, identifier: str) -> list[str]:
        package = self.package(identifier)
        live = self.layout.apps / identifier
        if not live.is_dir():
            raise RuntimeError(f"{package.name} is not installed")
        executable = find_executable(live, package)
        if executable is None:
            raise RuntimeError(f"No executable found for {package.name}")
        launchers = integrate(package, executable, live)
        state = self.state.read()
        existing = state.get("installed", {}).get(identifier, {})
        record = InstalledPackage(
            identifier=identifier,
            version=str(existing.get("version", package.version)),
            source=str(existing.get("source", package.url)),
            executable=str(executable),
            launchers=launchers,
            installed_at=str(existing.get("installed_at", "")),
        )
        self.state.set_installed(record, desired=identifier in state.get("desired", []), install_root=self.layout.root)
        return launchers

    def remove(self, identifier: str, remove_cache: bool = False, remove_config: bool = False) -> Iterator[Event]:
        package = self.package(identifier)
        live = self.layout.apps / identifier
        if live.parent.resolve() != self.layout.apps.resolve() or live.name != identifier:
            raise RuntimeError(f"Unsafe application removal target: {live}")
        state = self.state.read()
        record = state.get("installed", {}).get(identifier, {})
        remove_integration(package, record.get("launchers", []) if isinstance(record, dict) else [])
        if live.is_dir():
            shutil.rmtree(live)
            yield ("log", f"Removed application files for {package.name}")
        if remove_cache:
            cache = Path.home() / ".cache" / identifier
            safe = validate_user_data_path(cache, package, cache=True)
            if safe.exists():
                shutil.rmtree(safe) if safe.is_dir() else safe.unlink()
                yield ("log", f"Removed cache {safe}")
        if remove_config:
            if not package.remove_user_config:
                raise RuntimeError(f"Configuration removal is not enabled for {package.name}")
            for configured in package.config_paths:
                safe = validate_user_data_path(Path(configured), package, cache=False)
                if safe.exists():
                    shutil.rmtree(safe) if safe.is_dir() else safe.unlink()
                    yield ("log", f"Removed user configuration {safe}")
        self.state.remove(identifier)

    def restore(self) -> Iterator[Event]:
        desired = self.state.read().get("desired", [])
        for identifier in desired:
            if identifier in self.packages and self.packages[identifier].enabled and not self.installed(identifier):
                yield from self.install(identifier)
