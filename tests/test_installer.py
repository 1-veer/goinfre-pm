from pathlib import Path

import pytest

from goinfre_pm import integration
from goinfre_pm.installer import OperationLock, PackageManager
from goinfre_pm.models import InstalledPackage, Package
from goinfre_pm.storage import Layout, StateStore


def test_disabled_package_cannot_be_installed(tmp_path: Path) -> None:
    package = Package(
        identifier="retired",
        name="Retired Tool",
        description="Unsupported",
        category="Developer Tools",
        url="https://example.invalid/retired.tar.gz",
        source_type="tar",
        enabled=False,
        notes="requires unavailable system libraries",
    )
    manager = PackageManager(Layout.at(tmp_path / "goinfre-pm"), [package], StateStore(tmp_path / "state.json"))

    with pytest.raises(RuntimeError, match="unavailable.*system libraries"):
        next(manager.install("retired"))


def test_install_rejects_an_already_installed_package(monkeypatch, tmp_path: Path) -> None:
    package = Package(
        identifier="tool",
        name="Tool",
        description="Fixture",
        category="Developer Tools",
        url="https://example.invalid/tool.tar.xz",
        source_type="tar",
        architectures=("any",),
        desktop=False,
    )
    layout = Layout.at(tmp_path / "goinfre-pm")
    executable = layout.apps / "tool" / "tool"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    manager = PackageManager(layout, [package], StateStore(tmp_path / "state.json"))
    user_bin = tmp_path / "bin"
    user_bin.mkdir()
    (user_bin / "tool").symlink_to(executable)
    monkeypatch.setattr(integration, "USER_BIN", user_bin)
    manager.installations.set_installed(
        InstalledPackage("tool", "1", package.url, str(executable), [str(user_bin / "tool")])
    )

    with pytest.raises(RuntimeError, match="already installed.*update.*reinstall"):
        next(manager.install("tool"))


def test_install_rejects_a_repairable_payload_with_actionable_choices(tmp_path: Path) -> None:
    package = Package(
        identifier="tool",
        name="Tool",
        description="Fixture",
        category="Developer Tools",
        url="https://example.invalid/tool.tar.xz",
        source_type="tar",
        architectures=("any",),
        executable_candidates=("bin/tool",),
        desktop=False,
    )
    executable = tmp_path / "goinfre-pm" / "apps" / "tool" / "bin" / "tool"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    manager = PackageManager(
        Layout.at(tmp_path / "goinfre-pm"),
        [package],
        StateStore(tmp_path / "state.json"),
    )

    with pytest.raises(RuntimeError, match="needs repair.*repair.*update.*reinstall"):
        next(manager.install("tool"))


def test_launch_uses_verified_executable_without_a_shell(monkeypatch, tmp_path: Path) -> None:
    package = Package(
        identifier="tool",
        name="Tool",
        description="Fixture",
        category="Developer Tools",
        url="https://example.invalid/tool.tar.xz",
        source_type="tar",
        architectures=("any",),
        executable_candidates=("bin/tool",),
        desktop=False,
    )
    layout = Layout.at(tmp_path / "goinfre-pm")
    executable = layout.apps / "tool" / "bin" / "tool"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    manager = PackageManager(layout, [package], StateStore(tmp_path / "state.json"))

    calls: list[tuple[list[str], dict[str, object]]] = []

    class Process:
        pid = 4242

    def fake_popen(arguments, **options):
        calls.append((arguments, options))
        return Process()

    monkeypatch.setattr("goinfre_pm.installer.subprocess.Popen", fake_popen)

    assert manager.launch("tool") == 4242
    assert calls[0][0] == [str(executable)]
    assert calls[0][1]["cwd"] == str(executable.parent)
    assert calls[0][1]["start_new_session"] is True
    assert "shell" not in calls[0][1]


def test_update_rejects_a_package_that_is_not_installed(tmp_path: Path) -> None:
    package = Package(
        identifier="tool",
        name="Tool",
        description="Fixture",
        category="Developer Tools",
        url="https://example.invalid/tool.tar.xz",
        source_type="tar",
        architectures=("any",),
    )
    manager = PackageManager(Layout.at(tmp_path / "goinfre-pm"), [package], StateStore(tmp_path / "state.json"))

    with pytest.raises(RuntimeError, match="not installed.*install command"):
        next(manager.update("tool"))


def test_known_peak_size_is_checked_before_download(monkeypatch, tmp_path: Path) -> None:
    package = Package(
        identifier="large-tool",
        name="Large Tool",
        description="Fixture",
        category="Developer Tools",
        url="https://example.invalid/large.tar.xz",
        source_type="tar",
        architectures=("any",),
        download_size=80 * 1024**2,
        installed_size=150 * 1024**2,
    )
    layout = Layout.at(tmp_path / "goinfre-pm")
    layout.create()
    manager = PackageManager(layout, [package], StateStore(tmp_path / "state.json"))
    monkeypatch.setattr("goinfre_pm.installer.verify_install_root", lambda _root: None)
    monkeypatch.setattr("goinfre_pm.installer.available_space", lambda _root: 200 * 1024**2)

    with pytest.raises(RuntimeError, match="230.0 MiB known peak required"):
        next(manager.install("large-tool"))


def test_repair_corrects_wrapped_executable_and_state(monkeypatch, tmp_path: Path) -> None:
    package = Package(
        identifier="zen-browser",
        name="Zen Browser",
        description="Browser",
        category="Browsers",
        url="https://example.invalid/zen.tar.xz",
        source_type="tar",
        executable_candidates=("zen/zen", "zen"),
    )
    layout = Layout.at(tmp_path / "goinfre-pm")
    layout.create()
    browser = layout.apps / "zen-browser" / "zen" / "zen"
    browser.parent.mkdir(parents=True)
    browser.write_text("browser", encoding="utf-8")
    browser.chmod(0o755)
    helper = browser.parent / "glxtest"
    helper.write_text("helper", encoding="utf-8")
    helper.chmod(0o755)

    state = StateStore(tmp_path / "state.json")
    manager = PackageManager(layout, [package], state)
    manager.installations.set_installed(
        InstalledPackage("zen-browser", "latest", package.url, str(helper), [], "earlier"),
    )
    monkeypatch.setattr("goinfre_pm.integration.USER_BIN", tmp_path / "bin")
    monkeypatch.setattr("goinfre_pm.integration.DESKTOP_DIR", tmp_path / "applications")
    monkeypatch.setattr("goinfre_pm.integration.ICON_DIR", tmp_path / "icons")

    manager.repair("zen-browser")

    repaired = manager.installations.read()["installed"]["zen-browser"]
    assert repaired["executable"] == str(browser)
    assert (tmp_path / "bin" / "zen-browser").resolve() == browser.resolve()
    assert state.read()["setup_packages"] == []


def test_first_install_is_cleaned_up_when_integration_fails(monkeypatch, tmp_path: Path) -> None:
    package = Package(
        identifier="tool",
        name="Tool",
        description="Fixture",
        category="Developer Tools",
        url="https://example.invalid/tool.tar.xz",
        source_type="tar",
        architectures=("any",),
        executable_candidates=("bin/tool",),
        desktop=False,
    )
    layout = Layout.at(tmp_path / "goinfre-pm")
    state = StateStore(tmp_path / "state.json")
    manager = PackageManager(layout, [package], state)
    archive = tmp_path / "tool.tar.xz"
    archive.touch()

    def fake_extract(_archive, destination, _source_type, _work) -> None:
        executable = destination / "bin" / "tool"
        executable.parent.mkdir(parents=True)
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o755)

    monkeypatch.setattr("goinfre_pm.installer.verify_install_root", lambda _root: None)
    monkeypatch.setattr("goinfre_pm.installer.available_space", lambda _root: 1024**3)
    monkeypatch.setattr("goinfre_pm.installer.download", lambda *_args, **_kwargs: (archive, "test"))
    monkeypatch.setattr("goinfre_pm.installer.extract_download", fake_extract)
    monkeypatch.setattr("goinfre_pm.integration.USER_BIN", tmp_path / "bin")
    monkeypatch.setattr("goinfre_pm.integration.DESKTOP_DIR", tmp_path / "applications")
    monkeypatch.setattr("goinfre_pm.integration.ICON_DIR", tmp_path / "icons")

    from goinfre_pm.installer import integrate as real_integrate

    def failing_integrate(*args, **kwargs):
        real_integrate(*args, **kwargs)
        raise RuntimeError("home quota exhausted")

    monkeypatch.setattr("goinfre_pm.installer.integrate", failing_integrate)

    with pytest.raises(RuntimeError, match="quota"):
        list(manager.install("tool"))

    assert not (layout.apps / "tool").exists()
    assert not (tmp_path / "bin" / "tool").exists()
    assert manager.installations.read()["installed"] == {}


def test_failed_download_does_not_remove_existing_install(monkeypatch, tmp_path: Path) -> None:
    package = Package(
        identifier="tool",
        name="Tool",
        description="Fixture",
        category="Developer Tools",
        url="https://example.invalid/tool.tar.xz",
        source_type="tar",
        architectures=("any",),
        executable_candidates=("bin/tool",),
        desktop=False,
    )
    layout = Layout.at(tmp_path / "goinfre-pm")
    executable = layout.apps / "tool" / "bin" / "tool"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    user_bin = tmp_path / "bin"
    user_bin.mkdir()
    launcher = user_bin / "tool"
    launcher.symlink_to(executable)
    manager = PackageManager(layout, [package], StateStore(tmp_path / "state.json"))

    monkeypatch.setattr("goinfre_pm.installer.verify_install_root", lambda _root: None)
    monkeypatch.setattr("goinfre_pm.installer.available_space", lambda _root: 1024**3)
    monkeypatch.setattr("goinfre_pm.installer.download", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr("goinfre_pm.integration.USER_BIN", user_bin)

    with pytest.raises(RuntimeError, match="offline"):
        list(manager.update("tool"))

    assert executable.is_file()
    assert launcher.resolve() == executable.resolve()
