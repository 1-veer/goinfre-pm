from pathlib import Path
import os
import socket
import threading
import time

import pytest

from goinfre_pm import installer as installer_module, integration
from goinfre_pm.downloader import DownloadCancelled
from goinfre_pm.installer import InstallationCheck, OperationBusyError, OperationLock, PackageManager
from goinfre_pm.models import InstalledPackage, Package
from goinfre_pm.storage import Layout, StateStore


@pytest.fixture(autouse=True)
def confirmed_install_root(monkeypatch) -> None:
    monkeypatch.setattr("goinfre_pm.installer.verify_install_root", lambda _root: None)


def package(identifier: str) -> Package:
    return Package(
        identifier,
        identifier.title(),
        "fixture",
        "Developer Tools",
        f"https://example.invalid/{identifier}.tar.gz",
        source_type="tar",
        architectures=("any",),
        executable_candidates=(f"bin/{identifier}",),
        desktop=False,
    )


def healthy_install(manager: PackageManager, item: Package, user_bin: Path) -> Path:
    executable = manager.layout.apps / item.identifier / "bin" / item.identifier
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    user_bin.mkdir(parents=True, exist_ok=True)
    command = user_bin / item.identifier
    command.symlink_to(executable)
    manager.installations.set_installed(
        InstalledPackage(item.identifier, "1", item.url, str(executable), [str(command)], "now")
    )
    return executable


def test_roaming_setup_distinguishes_two_posts(monkeypatch, tmp_path: Path) -> None:
    item = package("tool")
    preferences = StateStore(tmp_path / "home" / "state.json")
    preferences.set_setup_package("tool", True)
    user_bin = tmp_path / "home" / "bin"
    monkeypatch.setattr(integration, "USER_BIN", user_bin)
    post_a = PackageManager(Layout.at(tmp_path / "post-a" / "goinfre-pm"), [item], preferences)
    healthy_install(post_a, item, user_bin)
    post_b = PackageManager(Layout.at(tmp_path / "post-b" / "goinfre-pm"), [item], preferences)

    assert post_a.package_status("tool") == "installed"
    assert post_b.package_status("tool") == "needs_restore"
    assert post_b.installations.read()["installed"] == {}


def test_missing_executable_is_needs_restore_and_broken_link_is_repairable(monkeypatch, tmp_path: Path) -> None:
    item = package("tool")
    preferences = StateStore(tmp_path / "state.json")
    preferences.set_setup_package("tool", True)
    user_bin = tmp_path / "bin"
    monkeypatch.setattr(integration, "USER_BIN", user_bin)
    manager = PackageManager(Layout.at(tmp_path / "goinfre-pm"), [item], preferences)
    executable = manager.layout.apps / "tool" / "bin" / "tool"
    executable.parent.mkdir(parents=True)
    manager.installations.set_installed(InstalledPackage("tool", "1", item.url, str(executable)))
    assert manager.package_status("tool") == "needs_restore"

    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    assert manager.package_status("tool") == "repairable"

    monkeypatch.setattr(manager, "_install", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("downloaded")))
    events = list(manager.restore())
    assert ("restored", "tool") in events
    assert manager.package_status("tool") == "installed"


def test_restore_skips_healthy_and_installs_only_missing(monkeypatch, tmp_path: Path) -> None:
    items = [package("healthy"), package("missing")]
    preferences = StateStore(tmp_path / "state.json")
    for item in items:
        preferences.set_setup_package(item.identifier, True)
    user_bin = tmp_path / "bin"
    monkeypatch.setattr(integration, "USER_BIN", user_bin)
    manager = PackageManager(Layout.at(tmp_path / "goinfre-pm"), items, preferences)
    healthy_install(manager, items[0], user_bin)
    installed: list[str] = []

    def fake_install(identifier, *_args, **_kwargs):
        installed.append(identifier)
        yield ("log", f"restored {identifier}")

    monkeypatch.setattr(manager, "_install", fake_install)
    events = list(manager.restore())

    assert installed == ["missing"]
    assert ("skipped", ("healthy", "already installed here")) in events
    assert ("restored", "missing") in events


def test_restore_continues_after_failure_and_honors_cancellation(monkeypatch, tmp_path: Path) -> None:
    items = [package("first"), package("second")]
    preferences = StateStore(tmp_path / "state.json")
    for item in items:
        preferences.set_setup_package(item.identifier, True)
    manager = PackageManager(Layout.at(tmp_path / "goinfre-pm"), items, preferences)

    def fake_install(identifier, *_args, **_kwargs):
        if identifier == "first":
            raise RuntimeError("fixture failure")
        yield ("log", f"restored {identifier}")

    monkeypatch.setattr(manager, "_install", fake_install)
    events = list(manager.restore())
    assert ("failed", ("first", "fixture failure")) in events
    assert ("restored", "second") in events

    cancelled = threading.Event()
    cancelled.set()
    cancelled_events = list(manager.restore(cancelled))
    assert cancelled_events == [("cancelled", "first"), ("cancelled", "second")]


def test_restore_cancels_current_download_and_all_remaining_packages(monkeypatch, tmp_path: Path) -> None:
    items = [package("first"), package("second")]
    preferences = StateStore(tmp_path / "state.json")
    preferences.set_setup_packages([item.identifier for item in items], True)
    manager = PackageManager(Layout.at(tmp_path / "goinfre-pm"), items, preferences)

    def cancelled_install(identifier, *_args, **_kwargs):
        if identifier == "first":
            raise DownloadCancelled("fixture cancellation")
        yield ("log", f"unexpected restore of {identifier}")

    monkeypatch.setattr(manager, "_install", cancelled_install)
    events = list(manager.restore())

    assert ("failed", ("first", "fixture cancellation")) not in events
    assert events[-2:] == [("cancelled", "first"), ("cancelled", "second")]


def test_restore_reports_insufficient_disk_without_installing(monkeypatch, tmp_path: Path) -> None:
    item = package("large")
    item.download_size = 80 * 1024**2
    item.installed_size = 150 * 1024**2
    preferences = StateStore(tmp_path / "state.json")
    preferences.set_setup_package("large", True)
    manager = PackageManager(Layout.at(tmp_path / "goinfre-pm"), [item], preferences)
    monkeypatch.setattr("goinfre_pm.installer.available_space", lambda _root: 10 * 1024**2)

    events = list(manager.restore())
    failures = [value for kind, value in events if kind == "failed"]
    assert len(failures) == 1
    assert failures[0][0] == "large"
    assert "Not enough goinfre space" in failures[0][1]
    assert manager.installations.read()["installed"] == {}


def test_restore_validates_the_selected_root_before_work(monkeypatch, tmp_path: Path) -> None:
    item = package("tool")
    preferences = StateStore(tmp_path / "state.json")
    preferences.set_setup_package("tool", True)
    manager = PackageManager(Layout.at(tmp_path / "goinfre-pm"), [item], preferences)
    monkeypatch.setattr(
        "goinfre_pm.installer.verify_install_root",
        lambda _root: (_ for _ in ()).throw(RuntimeError("root unavailable")),
    )
    monkeypatch.setattr(manager, "_install", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("started")))

    with pytest.raises(RuntimeError, match="root unavailable"):
        list(manager.restore())


def test_explicit_reinstall_failure_rolls_back_and_keeps_setup(monkeypatch, tmp_path: Path) -> None:
    item = package("tool")
    preferences = StateStore(tmp_path / "state.json")
    preferences.set_setup_package("tool", True)
    user_bin = tmp_path / "bin"
    monkeypatch.setattr(integration, "USER_BIN", user_bin)
    manager = PackageManager(Layout.at(tmp_path / "goinfre-pm"), [item], preferences)
    executable = healthy_install(manager, item, user_bin)
    monkeypatch.setattr("goinfre_pm.installer.verify_install_root", lambda _root: None)
    monkeypatch.setattr("goinfre_pm.installer.available_space", lambda _root: 1024**3)
    monkeypatch.setattr(
        "goinfre_pm.installer.download",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    with pytest.raises(RuntimeError, match="offline"):
        list(manager.reinstall("tool"))

    assert executable.is_file()
    assert preferences.read()["setup_packages"] == ["tool"]


def test_reinstall_rolls_back_after_promoting_a_broken_replacement(monkeypatch, tmp_path: Path) -> None:
    item = package("tool")
    preferences = StateStore(tmp_path / "state.json")
    preferences.set_setup_package("tool", True)
    user_bin = tmp_path / "bin"
    desktop_dir = tmp_path / "applications"
    icon_dir = tmp_path / "icons"
    monkeypatch.setattr(integration, "USER_BIN", user_bin)
    monkeypatch.setattr(integration, "DESKTOP_DIR", desktop_dir)
    monkeypatch.setattr(integration, "ICON_DIR", icon_dir)
    manager = PackageManager(Layout.at(tmp_path / "goinfre-pm"), [item], preferences)
    executable = healthy_install(manager, item, user_bin)
    executable.write_text("old payload", encoding="utf-8")
    original_record = manager.installations.read()["installed"]["tool"].copy()
    profile = tmp_path / "home" / ".config" / "tool" / "profile.json"
    profile.parent.mkdir(parents=True)
    profile.write_text("keep profile", encoding="utf-8")
    cache = tmp_path / "home" / ".cache" / "tool" / "cache.db"
    cache.parent.mkdir(parents=True)
    cache.write_text("keep cache", encoding="utf-8")
    archive = tmp_path / "tool.tar.gz"
    archive.touch()

    def fake_extract(_archive, destination, _source_type, _operation) -> None:
        replacement = destination / "bin" / "tool"
        replacement.parent.mkdir(parents=True)
        replacement.write_text("new payload", encoding="utf-8")
        replacement.chmod(0o755)

    monkeypatch.setattr("goinfre_pm.installer.available_space", lambda _root: 1024**3)
    monkeypatch.setattr("goinfre_pm.installer.download", lambda *_args, **_kwargs: (archive, "2"))
    monkeypatch.setattr("goinfre_pm.installer.extract_download", fake_extract)
    from goinfre_pm.installer import integrate as real_integrate

    def fail_after_integration(*args, **kwargs):
        real_integrate(*args, **kwargs)
        raise RuntimeError("desktop integration failed")

    monkeypatch.setattr("goinfre_pm.installer.integrate", fail_after_integration)

    with pytest.raises(RuntimeError, match="desktop integration failed"):
        list(manager.reinstall("tool"))

    assert executable.read_text(encoding="utf-8") == "old payload"
    assert manager.installations.read()["installed"]["tool"] == original_record
    assert profile.read_text(encoding="utf-8") == "keep profile"
    assert cache.read_text(encoding="utf-8") == "keep cache"
    assert preferences.read()["setup_packages"] == ["tool"]


def test_removal_defaults_to_forget_but_can_keep_setup(monkeypatch, tmp_path: Path) -> None:
    item = package("tool")
    preferences = StateStore(tmp_path / "state.json")
    preferences.set_setup_package("tool", True)
    user_bin = tmp_path / "bin"
    monkeypatch.setattr(integration, "USER_BIN", user_bin)
    manager = PackageManager(Layout.at(tmp_path / "goinfre-pm"), [item], preferences)
    healthy_install(manager, item, user_bin)
    list(manager.remove("tool", keep_setup=True))
    assert preferences.read()["setup_packages"] == ["tool"]

    healthy_install(manager, item, user_bin)
    list(manager.remove("tool"))
    assert preferences.read()["setup_packages"] == []


def test_normal_removal_preserves_user_configuration(monkeypatch, tmp_path: Path) -> None:
    item = package("tool")
    manager = PackageManager(Layout.at(tmp_path / "goinfre-pm"), [item], StateStore(tmp_path / "state.json"))
    user_bin = tmp_path / "bin"
    monkeypatch.setattr(integration, "USER_BIN", user_bin)
    healthy_install(manager, item, user_bin)
    profile = tmp_path / "home" / ".config" / "tool" / "profile.json"
    profile.parent.mkdir(parents=True)
    profile.write_text("keep", encoding="utf-8")
    cache = tmp_path / "home" / ".cache" / "tool" / "cache.db"
    cache.parent.mkdir(parents=True)
    cache.write_text("keep", encoding="utf-8")

    list(manager.remove("tool"))
    assert profile.read_text(encoding="utf-8") == "keep"
    assert cache.read_text(encoding="utf-8") == "keep"


def test_rejected_config_purge_does_not_partially_remove_the_application(monkeypatch, tmp_path: Path) -> None:
    item = package("tool")
    preferences = StateStore(tmp_path / "state.json")
    preferences.set_setup_package("tool", True)
    user_bin = tmp_path / "bin"
    monkeypatch.setattr(integration, "USER_BIN", user_bin)
    manager = PackageManager(Layout.at(tmp_path / "goinfre-pm"), [item], preferences)
    executable = healthy_install(manager, item, user_bin)

    with pytest.raises(RuntimeError, match="Configuration removal is not enabled"):
        list(manager.remove("tool", remove_config=True))

    assert executable.is_file()
    assert preferences.read()["setup_packages"] == ["tool"]
    assert "tool" in manager.installations.read()["installed"]


def test_root_local_operation_lock_rejects_overlap(tmp_path: Path) -> None:
    layout = Layout.at(tmp_path / "goinfre-pm")
    layout.create()
    with OperationLock(layout):
        with pytest.raises(RuntimeError, match="already active"):
            with OperationLock(layout):
                pass
    assert not (layout.runtime / "operation.lock").exists()


def test_operation_lock_discards_lock_from_another_post(tmp_path: Path) -> None:
    layout = Layout.at(tmp_path / "goinfre-pm")
    layout.create()
    lock_file = layout.runtime / "operation.lock"
    lock_file.write_text(
        '{"pid": %d, "token": "old", "created_at": %f, '
        '"hostname": "another-post", "boot_id": "another-boot"}'
        % (os.getpid(), time.time()),
        encoding="utf-8",
    )

    with OperationLock(layout):
        assert lock_file.exists()
    assert not lock_file.exists()


def test_restore_times_out_cleanly_when_real_operation_stays_active(tmp_path: Path) -> None:
    item = package("tool")
    preferences = StateStore(tmp_path / "state.json")
    preferences.set_setup_package("tool", True)
    manager = PackageManager(Layout.at(tmp_path / "goinfre-pm"), [item], preferences)

    with OperationLock(manager.layout):
        events = manager.restore(wait_timeout=0)
        assert next(events) == (
            "waiting",
            "An earlier GoinfrePM task is finishing on this post. Keep this "
            "window open; it will continue with anything still missing.",
        )
        with pytest.raises(OperationBusyError, match="still finishing another task"):
            next(events)


def test_restore_reports_wait_time_when_owner_has_no_progress(tmp_path: Path) -> None:
    item = package("tool")
    preferences = StateStore(tmp_path / "state.json")
    preferences.set_setup_package("tool", True)
    manager = PackageManager(Layout.at(tmp_path / "goinfre-pm"), [item], preferences)

    with OperationLock(manager.layout):
        events = manager.restore(wait_timeout=2)
        assert next(events)[0] == "waiting"
        kind, message = next(events)
        assert kind == "wait_progress"
        assert "1s" in str(message)
        assert "continue automatically" in str(message)


def test_default_restore_wait_does_not_expire_after_thirty_seconds(monkeypatch, tmp_path: Path) -> None:
    item = package("tool")
    preferences = StateStore(tmp_path / "state.json")
    preferences.set_setup_package("tool", True)
    manager = PackageManager(Layout.at(tmp_path / "goinfre-pm"), [item], preferences)

    with OperationLock(manager.layout):
        events = manager.restore()
        assert next(events)[0] == "waiting"
        monkeypatch.setattr(installer_module.time, "monotonic", lambda: 10**12)
        monkeypatch.setattr(installer_module.time, "sleep", lambda _seconds: None)
        kind, message = next(events)
        assert kind == "wait_progress"
        assert "continue automatically" in str(message)
        events.close()


def test_restore_waits_for_other_session_then_rechecks_installed_apps(monkeypatch, tmp_path: Path) -> None:
    item = package("tool")
    preferences = StateStore(tmp_path / "state.json")
    preferences.set_setup_package("tool", True)
    manager = PackageManager(Layout.at(tmp_path / "goinfre-pm"), [item], preferences)
    installed = False
    monkeypatch.setattr(manager, "installation", lambda _identifier: InstallationCheck("installed" if installed else "missing"))
    monkeypatch.setattr(manager, "_install", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("downloaded twice")))
    waiting = threading.Event()
    events: list[tuple[str, object]] = []

    def restore() -> None:
        for event in manager.restore():
            events.append(event)
            if event[0] == "waiting":
                waiting.set()

    with OperationLock(manager.layout) as owner:
        thread = threading.Thread(target=restore)
        thread.start()
        assert waiting.wait(2)
        assert not any(kind == "failed" for kind, _value in events)
        owner.publish_status(
            operation="restore",
            phase="downloading",
            package="tool",
            package_name="Tool",
            index=1,
            total=1,
            progress=23,
        )
        deadline = time.time() + 2
        while time.time() < deadline and not any(kind == "peer_progress" for kind, _value in events):
            time.sleep(0.02)
        peer_events = [value for kind, value in events if kind == "peer_progress"]
        assert peer_events
        assert peer_events[-1]["progress"] == 23
        installed = True
    thread.join(3)
    assert not thread.is_alive()
    assert ("ready", "tool") in events
    assert not any(kind == "skipped" for kind, _value in events)
    assert not (manager.layout.runtime / "operation-status.json").exists()


def test_restore_waits_for_earlier_apps_then_installs_last_missing_app(monkeypatch, tmp_path: Path) -> None:
    items = [package(identifier) for identifier in ("qbittorrent", "visual-studio-code", "zen-browser")]
    preferences = StateStore(tmp_path / "state.json")
    preferences.set_setup_packages([item.identifier for item in items], True)
    manager = PackageManager(Layout.at(tmp_path / "goinfre-pm"), items, preferences)
    installed = {"qbittorrent", "visual-studio-code"}
    monkeypatch.setattr(
        manager,
        "installation",
        lambda identifier: InstallationCheck("installed" if identifier in installed else "missing"),
    )

    def install(identifier, *_args, **_kwargs):
        installed.add(identifier)
        yield ("log", f"installed {identifier}")

    monkeypatch.setattr(manager, "_install", install)
    events: list[tuple[str, object]] = []
    waiting = threading.Event()

    def restore() -> None:
        for event in manager.restore():
            events.append(event)
            if event[0] == "waiting":
                waiting.set()

    with OperationLock(manager.layout):
        thread = threading.Thread(target=restore)
        thread.start()
        assert waiting.wait(2)
        assert thread.is_alive()
    thread.join(3)

    assert not thread.is_alive()
    assert installed == {"qbittorrent", "visual-studio-code", "zen-browser"}
    assert ("ready", "qbittorrent") in events
    assert ("ready", "visual-studio-code") in events
    assert ("restored", "zen-browser") in events
    assert not any(kind == "failed" for kind, _value in events)


def test_restore_can_cancel_while_waiting_for_other_session(tmp_path: Path) -> None:
    item = package("tool")
    preferences = StateStore(tmp_path / "state.json")
    preferences.set_setup_package("tool", True)
    manager = PackageManager(Layout.at(tmp_path / "goinfre-pm"), [item], preferences)
    cancelled = threading.Event()
    waiting = threading.Event()
    events: list[tuple[str, object]] = []

    def restore() -> None:
        for event in manager.restore(cancelled):
            events.append(event)
            if event[0] == "waiting":
                waiting.set()

    with OperationLock(manager.layout):
        thread = threading.Thread(target=restore)
        thread.start()
        assert waiting.wait(2)
        cancelled.set()
        thread.join(3)
        assert not thread.is_alive()
    assert ("cancelled", "tool") in events
    assert not any(kind == "failed" for kind, _value in events)


def test_new_lock_without_metadata_is_not_mistaken_for_stale(tmp_path: Path) -> None:
    layout = Layout.at(tmp_path / "goinfre-pm")
    layout.create()
    lock_file = layout.runtime / "operation.lock"
    lock_file.touch()
    with pytest.raises(RuntimeError, match="already active"):
        with OperationLock(layout):
            pass
    assert lock_file.exists()
    old = time.time() - 10
    lock_file.touch()
    os.utime(lock_file, (old, old))
    with OperationLock(layout):
        pass
    assert not lock_file.exists()


def test_operation_lock_discards_reused_pid_on_same_post(monkeypatch, tmp_path: Path) -> None:
    layout = Layout.at(tmp_path / "goinfre-pm")
    layout.create()
    lock_file = layout.runtime / "operation.lock"
    lock_file.write_text(
        '{"pid": %d, "token": "old", "created_at": %f, '
        '"hostname": "%s", "boot_id": "%s", "process_start": "old-start"}'
        % (os.getpid(), time.time(), socket.gethostname(), installer_module._current_boot_id()),
        encoding="utf-8",
    )
    monkeypatch.setattr(installer_module, "_linux_process_start", lambda _pid: "new-start")

    with OperationLock(layout):
        assert lock_file.exists()
    assert not lock_file.exists()


def test_legacy_lock_discards_pid_reused_by_unrelated_process(monkeypatch, tmp_path: Path) -> None:
    layout = Layout.at(tmp_path / "goinfre-pm")
    layout.create()
    lock_file = layout.runtime / "operation.lock"
    lock_file.write_text(
        '{"pid": %d, "token": "old", "created_at": %f, '
        '"hostname": "%s", "boot_id": "%s"}'
        % (os.getpid(), time.time(), socket.gethostname(), installer_module._current_boot_id()),
        encoding="utf-8",
    )
    monkeypatch.setattr(installer_module, "_linux_process_command", lambda _pid: "/usr/bin/sleep 300")

    with OperationLock(layout):
        assert lock_file.exists()
    assert not lock_file.exists()
