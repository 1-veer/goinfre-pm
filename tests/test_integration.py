from pathlib import Path
import os

import pytest

from goinfre_pm.integration import desktop_entry, find_executable, integrate, remove_integration, validate_user_data_path
from goinfre_pm.models import Package


def package(**changes) -> Package:
    values = {
        "identifier": "sample-tool",
        "name": "Sample Tool",
        "description": "A safe sample",
        "category": "Developer Tools",
        "url": "https://example.invalid/sample.tar.gz",
        "source_type": "tar",
        "executable_candidates": ("bin/sample",),
    }
    values.update(changes)
    return Package(**values)


def test_executable_discovery_prefers_metadata(tmp_path: Path) -> None:
    expected = tmp_path / "bin" / "sample"
    expected.parent.mkdir()
    expected.write_text("#!/bin/sh\n", encoding="utf-8")
    fallback = tmp_path / "other"
    fallback.write_text("x", encoding="utf-8")
    fallback.chmod(0o755)
    assert find_executable(tmp_path, package()) == expected


def test_desktop_entry_quotes_paths_with_spaces(tmp_path: Path) -> None:
    executable = tmp_path / "path with spaces" / "sample"
    content = desktop_entry(package(), executable)
    assert f'Exec="{executable}"' in content
    assert "Terminal=false" in content


def test_generated_integration_and_removal(monkeypatch, tmp_path: Path) -> None:
    user_bin = tmp_path / "bin"
    desktop = tmp_path / "applications"
    icons = tmp_path / "icons"
    monkeypatch.setattr("goinfre_pm.integration.USER_BIN", user_bin)
    monkeypatch.setattr("goinfre_pm.integration.DESKTOP_DIR", desktop)
    monkeypatch.setattr("goinfre_pm.integration.ICON_DIR", icons)
    package_dir = tmp_path / "app"
    executable = package_dir / "bin" / "sample"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    created = integrate(package(), executable, package_dir)
    assert (user_bin / "sample-tool").is_symlink()
    assert (desktop / "sample-tool.desktop").is_file()
    assert str(desktop / "sample-tool.desktop") in created
    remove_integration(package(), created)
    assert not (user_bin / "sample-tool").exists()
    assert not (desktop / "sample-tool.desktop").exists()


def test_uninstall_path_validation_rejects_unrelated_data(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("goinfre_pm.integration.Path.home", lambda: tmp_path)
    with pytest.raises(RuntimeError):
        validate_user_data_path(tmp_path / ".config" / "unrelated", package())
    safe = validate_user_data_path(tmp_path / ".config" / "sample-tool", package())
    assert safe.name == "sample-tool"
