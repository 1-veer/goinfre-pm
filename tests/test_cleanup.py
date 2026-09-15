import os
import time
from pathlib import Path

from goinfre_pm.installer import PackageManager, cleanup_report
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
