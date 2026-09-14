from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable

from .downloader import resolve_github_release
from .models import Package
from .storage import StateStore

CACHE_SECONDS = 6 * 60 * 60


@dataclass(frozen=True)
class UpdateInfo:
    status: str
    installed_version: str
    latest_version: str = ""
    checked_at: int = 0
    reason: str = ""

    @property
    def available(self) -> bool:
        return self.status == "available"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "installed_version": self.installed_version,
            "latest_version": self.latest_version,
            "checked_at": self.checked_at,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "UpdateInfo":
        try:
            checked_at = int(value.get("checked_at", 0))
        except (TypeError, ValueError):
            checked_at = 0
        return cls(
            status=str(value.get("status", "unknown")),
            installed_version=str(value.get("installed_version", "")),
            latest_version=str(value.get("latest_version", "")),
            checked_at=checked_at,
            reason=str(value.get("reason", "")),
        )


def _same_version(first: str, second: str) -> bool:
    return first.strip().lower().lstrip("v") == second.strip().lower().lstrip("v")


def check_package_update(
    package: Package,
    installed_version: str,
    store: StateStore,
    *,
    force: bool = False,
    now: int | None = None,
    resolver: Callable[[Package, Callable[[str], None]], tuple[str, str]] = resolve_github_release,
) -> UpdateInfo:
    timestamp = int(time.time()) if now is None else now
    if package.source_type != "github" or installed_version.lower() in {"", "latest", "unknown"}:
        return UpdateInfo("unknown", installed_version, checked_at=timestamp, reason="version cannot be verified reliably")
    cached_raw = store.read().get("update_cache", {}).get(package.identifier)
    if not force and isinstance(cached_raw, dict):
        cached = UpdateInfo.from_dict(cached_raw)
        age = timestamp - cached.checked_at
        if cached.installed_version == installed_version and cached.checked_at > 0 and 0 <= age < CACHE_SECONDS:
            return cached
    try:
        _url, latest = resolver(package, lambda _message: None)
        status = "current" if _same_version(installed_version, latest) else "available"
        result = UpdateInfo(status, installed_version, latest, timestamp)
    except Exception as exc:
        result = UpdateInfo("unknown", installed_version, checked_at=timestamp, reason=str(exc))
    store.set_update_cache(package.identifier, result.as_dict())
    return result
