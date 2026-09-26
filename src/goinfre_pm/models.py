from __future__ import annotations

from dataclasses import dataclass, field
import platform
import re
from typing import Any

PACKAGE_ID = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
CURRENT_INTEGRATION_VERSION = 2


def validate_package_id(value: str) -> str:
    if not PACKAGE_ID.fullmatch(value) or ".." in value or "/" in value or "\\" in value:
        raise ValueError(f"Invalid package identifier: {value!r}")
    return value


def current_architecture() -> str:
    machine = platform.machine().lower()
    return {"amd64": "x86_64", "x64": "x86_64", "arm64": "aarch64"}.get(machine, machine)


def architecture_matches(supported: tuple[str, ...], current: str | None = None) -> bool:
    current = current_architecture() if current is None else current.lower()
    aliases = {
        "x86_64": {"x86_64", "amd64", "x64"},
        "aarch64": {"aarch64", "arm64"},
    }
    normalized = aliases.get(current, {current})
    return "any" in supported or bool(normalized.intersection(a.lower() for a in supported))


@dataclass(frozen=True)
class PostInstallAction:
    action: str
    source: str = ""
    target: str = ""
    mode: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PostInstallAction":
        action = str(data.get("action", ""))
        if action not in {"chmod", "symlink"}:
            raise ValueError(f"Unsupported structured post-install action: {action!r}")
        return cls(action, str(data.get("source", "")), str(data.get("target", "")), str(data.get("mode", "")))


@dataclass
class Package:
    identifier: str
    name: str
    description: str
    category: str
    url: str
    source_type: str = "auto"
    architectures: tuple[str, ...] = ("x86_64",)
    executable_candidates: tuple[str, ...] = ()
    icon_candidates: tuple[str, ...] = ()
    desktop: bool = True
    terminal: bool = False
    version: str = "latest"
    remove_user_config: bool = False
    config_paths: tuple[str, ...] = ()
    asset_pattern: str = ""
    notes: str = ""
    enabled: bool = True
    download_size: int | None = None
    installed_size: int | None = None
    sha256: str = ""
    post_install: tuple[PostInstallAction, ...] = ()
    selected: bool = field(default=False, compare=False)

    def __post_init__(self) -> None:
        validate_package_id(self.identifier)
        if not self.url.startswith("https://"):
            raise ValueError(f"{self.identifier}: only HTTPS package sources are allowed")
        if self.source_type not in {"auto", "github", "deb", "appimage", "tar", "zip", "binary"}:
            raise ValueError(f"{self.identifier}: unsupported source type {self.source_type!r}")
        for label, value in (("download_size", self.download_size), ("installed_size", self.installed_size)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError(f"{self.identifier}: {label} must be a non-negative integer")
        if self.sha256 and not re.fullmatch(r"[0-9a-fA-F]{64}", self.sha256):
            raise ValueError(f"{self.identifier}: sha256 must contain exactly 64 hexadecimal characters")

    @property
    def compatible(self) -> bool:
        return architecture_matches(self.architectures)


@dataclass
class InstalledPackage:
    identifier: str
    version: str
    source: str
    executable: str
    launchers: list[str] = field(default_factory=list)
    installed_at: str = ""
    download_size: int | None = None
    installed_size: int | None = None
    integration_version: int = CURRENT_INTEGRATION_VERSION

    @classmethod
    def from_dict(cls, identifier: str, data: dict[str, Any]) -> "InstalledPackage":
        validate_package_id(identifier)
        return cls(
            identifier=identifier,
            version=str(data.get("version", "unknown")),
            source=str(data.get("source", "")),
            executable=str(data.get("executable", "")),
            launchers=[str(item) for item in data.get("launchers", [])],
            installed_at=str(data.get("installed_at", "")),
            download_size=int(data["download_size"]) if isinstance(data.get("download_size"), int) else None,
            installed_size=int(data["installed_size"]) if isinstance(data.get("installed_size"), int) else None,
            integration_version=(
                int(data["integration_version"])
                if isinstance(data.get("integration_version"), int)
                else 0
            ),
        )
