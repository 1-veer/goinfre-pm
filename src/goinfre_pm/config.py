from __future__ import annotations

from pathlib import Path
import os
import shlex
import warnings
from typing import Any

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

from .models import Package, PostInstallAction
from .branding import SLUG


class ConfigurationError(ValueError):
    pass


def default_packages_file() -> Path:
    override = os.environ.get("GPM_PACKAGES_FILE")
    if override:
        return Path(override).expanduser()
    project_file = Path(__file__).resolve().parents[2] / "packages.toml"
    if project_file.exists():
        return project_file
    return Path.home() / ".config" / SLUG / "packages.toml"


def _strings(data: dict[str, Any], key: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    value = data.get(key, list(default))
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigurationError(f"{key} must be an array of strings")
    return tuple(value)


def load_packages(path: Path | None = None) -> list[Package]:
    path = default_packages_file() if path is None else path
    legacy = path if path.suffix == ".conf" else path.with_suffix(".conf")
    if path.suffix == ".conf" or (not path.exists() and legacy.exists()):
        migrated = legacy.with_suffix(".toml")
        migration_warnings = migrate_legacy_config(legacy, migrated)
        for message in migration_warnings:
            warnings.warn(message, RuntimeWarning, stacklevel=2)
        path = migrated
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"Cannot read {path}: {exc}") from exc
    entries = data.get("package", [])
    if not isinstance(entries, list):
        raise ConfigurationError("packages.toml must contain [[package]] tables")
    packages: list[Package] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ConfigurationError("Every package entry must be a table")
        try:
            identifier = str(entry["id"])
            if identifier in seen:
                raise ConfigurationError(f"Duplicate package identifier: {identifier}")
            actions_raw = entry.get("post_install", [])
            if not isinstance(actions_raw, list):
                raise ConfigurationError(f"{identifier}: post_install must be an array of tables")
            package = Package(
                identifier=identifier,
                name=str(entry.get("name", identifier)),
                description=str(entry.get("description", "")),
                category=str(entry.get("category", "Developer Tools")),
                url=str(entry["url"]),
                source_type=str(entry.get("source_type", "auto")),
                architectures=_strings(entry, "architectures", ("x86_64",)),
                executable_candidates=_strings(entry, "executables"),
                icon_candidates=_strings(entry, "icons"),
                desktop=bool(entry.get("desktop", True)),
                terminal=bool(entry.get("terminal", False)),
                version=str(entry.get("version", "latest")),
                remove_user_config=bool(entry.get("remove_user_config", False)),
                config_paths=_strings(entry, "config_paths"),
                asset_pattern=str(entry.get("asset_pattern", "")),
                notes=str(entry.get("notes", "")),
                post_install=tuple(PostInstallAction.from_dict(item) for item in actions_raw),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigurationError(f"Invalid package entry: {exc}") from exc
        seen.add(package.identifier)
        packages.append(package)
    return packages


def migrate_legacy_config(source: Path, destination: Path) -> list[str]:
    """Convert the legacy whitespace format; shell fragments are quarantined.

    The returned warnings must be shown to the user. No legacy command is ever
    executed or copied as an active post-install action.
    """
    entries: list[dict[str, str]] = []
    warnings: list[str] = []
    for number, raw in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        try:
            fields = shlex.split(raw)
        except ValueError as exc:
            warnings.append(f"line {number}: skipped ({exc})")
            continue
        if len(fields) < 2:
            warnings.append(f"line {number}: skipped (missing URL)")
            continue
        identifier, url = fields[:2]
        if len(fields) > 2:
            warnings.append(f"{identifier}: legacy shell post-install command was disabled")
        entries.append({"id": identifier.lower(), "url": url})
    lines = ["# Migrated legacy package definitions. Review metadata before use.", ""]
    for entry in entries:
        lines.extend([
            "[[package]]",
            f'id = "{entry["id"]}"',
            f'name = "{entry["id"]}"',
            'description = "Migrated package; metadata needs review."',
            'category = "Developer Tools"',
            f'url = "{entry["url"]}"',
            'source_type = "auto"',
            'architectures = ["x86_64"]',
            "",
        ])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines), encoding="utf-8")
    return warnings
