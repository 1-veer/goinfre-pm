from pathlib import Path
import os

import pytest

from goinfre_pm.integration import (
    desktop_entry,
    find_executable,
    find_icon,
    integrate,
    remove_integration,
    validate_user_data_path,
)
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


def test_executable_discovery_handles_archive_wrapper_directory(tmp_path: Path) -> None:
    expected = tmp_path / "sample-tool-1.2.3" / "bin" / "sample"
    expected.parent.mkdir(parents=True)
    expected.write_text("#!/bin/sh\n", encoding="utf-8")
    expected.chmod(0o755)
    helper = tmp_path / "sample-tool-1.2.3" / "helper"
    helper.write_text("#!/bin/sh\n", encoding="utf-8")
    helper.chmod(0o755)
    assert find_executable(tmp_path, package()) == expected


def test_zen_archive_layout_selects_browser_not_helper(tmp_path: Path) -> None:
    root = tmp_path / "zen"
    browser = root / "zen"
    browser.parent.mkdir(parents=True)
    browser.write_text("browser", encoding="utf-8")
    browser.chmod(0o755)
    helper = root / "glxtest"
    helper.write_text("helper", encoding="utf-8")
    helper.chmod(0o755)
    zen = package(
        identifier="zen-browser",
        name="Zen Browser",
        executable_candidates=("zen/zen", "zen", "zen-bin"),
    )
    assert find_executable(tmp_path, zen) == browser


def test_configured_icon_handles_archive_wrapper_directory(tmp_path: Path) -> None:
    expected = tmp_path / "Postman" / "app" / "resources" / "app" / "assets" / "icon.png"
    expected.parent.mkdir(parents=True)
    expected.write_bytes(b"configured")
    unrelated = tmp_path / "Postman" / "app" / "resources" / "large-background.png"
    unrelated.write_bytes(b"x" * 10_000)

    postman = package(icon_candidates=("app/resources/app/assets/icon.png",))

    assert find_icon(tmp_path, postman) == expected


def test_icon_discovery_uses_matching_desktop_metadata(tmp_path: Path) -> None:
    desktop = tmp_path / "usr" / "share" / "applications" / "sample-tool.desktop"
    desktop.parent.mkdir(parents=True)
    desktop.write_text(
        "[Desktop Entry]\nName=Sample Tool\nExec=/usr/bin/sample\nIcon=sample-tool\n",
        encoding="utf-8",
    )
    expected = tmp_path / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps" / "sample-tool.png"
    expected.parent.mkdir(parents=True)
    expected.write_bytes(b"logo")
    unrelated = tmp_path / "resources" / "welcome-background.png"
    unrelated.parent.mkdir()
    unrelated.write_bytes(b"x" * 10_000)

    assert find_icon(tmp_path, package()) == expected


def test_icon_discovery_rejects_unrelated_large_artwork(tmp_path: Path) -> None:
    artwork = tmp_path / "resources" / "welcome-background.png"
    artwork.parent.mkdir()
    artwork.write_bytes(b"x" * 10_000)

    assert find_icon(tmp_path, package()) is None


def test_desktop_entry_quotes_paths_with_spaces(tmp_path: Path) -> None:
    executable = tmp_path / "path with spaces" / "sample"
    content = desktop_entry(package(), executable)
    assert f'Exec="{executable}"' in content
    assert "Terminal=false" in content
    assert "--profile" not in content
    assert "XDG_CONFIG_HOME" not in content
    assert "Categories=Development;" in content


def test_desktop_entry_uses_package_category(tmp_path: Path) -> None:
    executable = tmp_path / "browser"
    content = desktop_entry(package(category="Browsers"), executable)
    assert "Categories=Network;WebBrowser;" in content


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


def test_removal_does_not_trust_unrelated_recorded_launcher(monkeypatch, tmp_path: Path) -> None:
    user_bin = tmp_path / "bin"
    desktop = tmp_path / "applications"
    icons = tmp_path / "icons"
    monkeypatch.setattr("goinfre_pm.integration.USER_BIN", user_bin)
    monkeypatch.setattr("goinfre_pm.integration.DESKTOP_DIR", desktop)
    monkeypatch.setattr("goinfre_pm.integration.ICON_DIR", icons)
    unrelated = user_bin / "student-script"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("keep", encoding="utf-8")

    remove_integration(package(), [str(unrelated)])

    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_uninstall_path_validation_rejects_unrelated_data(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("goinfre_pm.integration.Path.home", lambda: tmp_path)
    with pytest.raises(RuntimeError):
        validate_user_data_path(tmp_path / ".config" / "unrelated", package())
    safe = validate_user_data_path(tmp_path / ".config" / "sample-tool", package())
    assert safe.name == "sample-tool"
