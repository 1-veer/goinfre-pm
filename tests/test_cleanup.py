import os
import time
from pathlib import Path

import pytest

from goinfre_pm import integration
from goinfre_pm.installer import PackageManager, cleanup_report
from goinfre_pm.models import Package
from goinfre_pm.storage import Layout, StateStore


def test_cleanup_removes_only_manager_temporary_files(tmp_path: Path) -> None:
    layout = Layout.at(tmp_path / "goinfre-pm")
    layout.create()
    manager = PackageManager(layout, [], StateStore(tmp_path / "state.json"))

    download = layout.downloads / "interrupted-download"
    download.mkdir()
    (download / "archive.part").write_bytes(b"download")
    stage = layout.apps / f".tool.stage-{'a' * 32}"
    stage.mkdir()
    (stage / "partial").write_bytes(b"stage")
    backup = layout.apps / f".tool.backup-{'b' * 32}"
    backup.mkdir()
    (backup / "partial").write_bytes(b"backup")

    installed = layout.apps / "tool"
    installed.mkdir()
    (installed / "tool").write_bytes(b"installed")
    similar_but_invalid = layout.apps / ".tool.stage-not-a-valid-operation-id"
    similar_but_invalid.mkdir()

    old_log = layout.logs / "tool.log"
    old_log.write_bytes(b"old log")
    old_time = time.time() - 31 * 24 * 60 * 60
    os.utime(old_log, (old_time, old_time))
    recent_log = layout.logs / "recent.log"
    recent_log.write_bytes(b"recent log")

    external = tmp_path / "outside.txt"
    external.write_text("keep", encoding="utf-8")
    linked_download = layout.downloads / "outside-link"
    linked_download.symlink_to(external)

    report = cleanup_report(layout)
    assert {item.kind for item in report.items} == {
        "temporary download",
        "stale installation workdir",
        "old log",
    }
    assert report.count == 5
    assert report.total_bytes > 0

    removed = manager.cleanup()
    assert removed.count == 5
    assert not download.exists()
    assert not stage.exists()
    assert not backup.exists()
    assert not old_log.exists()
    assert not linked_download.exists()
    assert external.read_text(encoding="utf-8") == "keep"
    assert installed.is_dir()
    assert similar_but_invalid.is_dir()
    assert recent_log.is_file()


def test_cleanup_report_is_empty_for_normal_installed_content(tmp_path: Path) -> None:
    layout = Layout.at(tmp_path / "goinfre-pm")
    layout.create()
    installed = layout.apps / "application"
    installed.mkdir()
    (installed / "run").write_bytes(b"payload")
    (layout.logs / "application.log").write_text("recent", encoding="utf-8")

    report = cleanup_report(layout)

    assert report.count == 0
    assert report.total_bytes == 0


def test_leave_post_removes_owned_storage_and_preserves_roaming_state(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "goinfre-pm"
    layout = Layout.at(root)
    layout.create()
    state = StateStore(tmp_path / "state.json")
    state.set_setup_package("tool", True)
    state.set_theme("green")
    package = Package(
        "tool",
        "Tool",
        "fixture",
        "Developer Tools",
        "https://example.invalid/tool.tar.gz",
        source_type="tar",
        architectures=("any",),
    )
    user_bin = tmp_path / "bin"
    desktop = tmp_path / "applications"
    icons = tmp_path / "icons"
    monkeypatch.setattr(integration, "USER_BIN", user_bin)
    monkeypatch.setattr(integration, "DESKTOP_DIR", desktop)
    monkeypatch.setattr(integration, "ICON_DIR", icons)
    monkeypatch.setattr("goinfre_pm.installer.verify_install_root", lambda _root: None)

    for path in (user_bin / "tool", desktop / "tool.desktop", icons / "tool.png"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("owned", encoding="utf-8")
    (layout.apps / "tool").mkdir()
    (layout.apps / "tool" / "payload").write_bytes(b"payload")
    (root / "venv").mkdir()
    (root / "venv" / "python").write_bytes(b"runtime")
    unrelated = root / "student-notes.txt"
    unrelated.write_text("keep", encoding="utf-8")

    manager = PackageManager(layout, [package], state)
    preview = manager.post_storage_report()
    report = manager.leave_post()

    assert preview.has_data
    assert preview.entries >= 2
    assert preview.total_bytes == report.bytes_removed
    assert report.bytes_removed > 0
    assert report.integrations_removed == 3
    assert report.root_removed is False
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert not any(path.exists() for path in (layout.apps, layout.downloads, layout.runtime, layout.logs, root / "venv"))
    assert not any(path.exists() for path in (user_bin / "tool", desktop / "tool.desktop", icons / "tool.png"))
    assert state.read()["setup_packages"] == ["tool"]
    assert state.read()["theme"] == "green"


def test_post_storage_report_ignores_empty_manager_directories(monkeypatch, tmp_path: Path) -> None:
    layout = Layout.at(tmp_path / "goinfre-pm")
    layout.create()
    monkeypatch.setattr("goinfre_pm.installer.verify_install_root", lambda _root: None)

    report = PackageManager(layout, [], StateStore(tmp_path / "state.json")).post_storage_report()

    assert report.total_bytes == 0
    assert report.entries == 0
    assert not report.has_data


def test_leave_post_refuses_symlinked_owned_directory(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "goinfre-pm"
    layout = Layout.at(root)
    layout.create()
    manager = PackageManager(layout, [], StateStore(tmp_path / "state.json"))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("safe", encoding="utf-8")
    layout.downloads.rmdir()
    layout.downloads.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr("goinfre_pm.installer.verify_install_root", lambda _root: None)

    with pytest.raises(RuntimeError, match="unsafe post cleanup target"):
        manager.leave_post()
    assert (outside / "keep").read_text(encoding="utf-8") == "safe"
