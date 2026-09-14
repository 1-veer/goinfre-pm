from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import Package


@dataclass(frozen=True)
class StarterPack:
    identifier: str
    name: str
    description: str
    package_ids: tuple[str, ...]


STARTER_PACKS = (
    StarterPack("42-c-cpp", "42 C/C++", "A focused setup for piscine and cursus projects.", ("vscodium", "kitty", "github-cli", "lazygit")),
    StarterPack("web-development", "Web Development", "Browser, editor, API, and GitHub essentials.", ("vscode", "firefox", "google-chrome", "postman", "github-cli")),
    StarterPack("minimal-terminal", "Minimal Terminal", "A fast keyboard-driven terminal toolchain.", ("kitty", "neovim", "ripgrep", "fd", "fzf", "bat")),
    StarterPack("creative", "Creative", "Tools for 3D work and image processing.", ("blender", "imagemagick")),
)


@dataclass(frozen=True)
class BasketEstimate:
    count: int
    known_download: int
    known_installed: int
    unknown_downloads: int
    unknown_installed: int
    known_required: int
    remaining_after_known: int
    insufficient_space: bool


def estimate_basket(
    packages: Iterable[Package],
    free_space: int,
    installed_metadata: dict[str, object] | None = None,
) -> BasketEstimate:
    selected = list(packages)
    installed_metadata = installed_metadata or {}

    def size_for(package: Package, field: str) -> int | None:
        configured = getattr(package, field)
        record = installed_metadata.get(package.identifier)
        recorded = record.get(field) if isinstance(record, dict) else None
        return configured if configured is not None else (recorded if isinstance(recorded, int) else None)

    download_sizes = [size_for(package, "download_size") for package in selected]
    installed_sizes = [size_for(package, "installed_size") for package in selected]
    known_download = sum(value or 0 for value in download_sizes)
    known_installed = sum(value or 0 for value in installed_sizes)
    known_required = known_download + known_installed
    return BasketEstimate(
        count=len(selected),
        known_download=known_download,
        known_installed=known_installed,
        unknown_downloads=sum(value is None for value in download_sizes),
        unknown_installed=sum(value is None for value in installed_sizes),
        known_required=known_required,
        # Downloads and staging trees coexist until atomic promotion, so the
        # useful projection is peak operation space rather than final payload.
        remaining_after_known=max(0, free_space - known_required),
        insufficient_space=known_required > free_space,
    )


def apply_starter_pack(pack: StarterPack, packages: Iterable[Package]) -> tuple[str, ...]:
    available = {package.identifier for package in packages if package.enabled and package.compatible}
    return tuple(identifier for identifier in pack.package_ids if identifier in available)


def sort_packages(
    packages: Iterable[Package],
    key: str,
    installed: set[str] | None = None,
    updates: set[str] | None = None,
    installed_sizes: dict[str, int] | None = None,
) -> list[Package]:
    installed = installed or set()
    updates = updates or set()
    installed_sizes = installed_sizes or {}
    values = list(packages)
    if key == "category":
        return sorted(values, key=lambda package: (package.category.casefold(), package.name.casefold()))
    if key == "installed":
        return sorted(values, key=lambda package: (package.identifier not in installed, package.name.casefold()))
    if key == "size":
        def known_size(package: Package) -> int | None:
            return package.installed_size if package.installed_size is not None else installed_sizes.get(package.identifier)

        return sorted(values, key=lambda package: (known_size(package) is None, -(known_size(package) or 0), package.name.casefold()))
    if key == "updates":
        return sorted(values, key=lambda package: (package.identifier not in updates, package.name.casefold()))
    return sorted(values, key=lambda package: package.name.casefold())


def human_size(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TiB"
