from pathlib import Path
import json
import threading

import pytest

from goinfre_pm.installer import PackageManager
from goinfre_pm.models import InstalledPackage, Package
from goinfre_pm.storage import (
    DEFAULT_UI_THEME,
    UI_THEMES,
    Layout,
    LocalStateStore,
    StateStore,
    atomic_json_write,
    resolve_install_root,
)


def package(identifier: str = "tool") -> Package:
    return Package(
        identifier,
        identifier.title(),
        "fixture",
        "Developer Tools",
        f"https://example.invalid/{identifier}.tar.gz",
        source_type="tar",
        architectures=("any",),
        executable_candidates=(identifier,),
        desktop=False,
    )


def test_install_root_prefers_environment(monkeypatch, tmp_path: Path) -> None:
    base = tmp_path / "campus-storage"
    base.mkdir()
    monkeypatch.setenv("GOINFRE", str(base))
    monkeypatch.setattr("goinfre_pm.storage.Path.home", lambda: tmp_path / "home")
    settings = tmp_path / "missing-settings.json"
    assert resolve_install_root(settings_file=settings) == (base / "goinfre-pm").resolve()


def test_configured_missing_root_does_not_mask_environment(monkeypatch, tmp_path: Path) -> None:
    settings = tmp_path / "config.json"
    settings.write_text(json.dumps({"install_root": str(tmp_path / "gone")}), encoding="utf-8")
    base = tmp_path / "goinfre"
    base.mkdir()
    monkeypatch.setenv("GOINFRE", str(base))
    assert resolve_install_root(settings_file=settings) == (base / "goinfre-pm").resolve()


def test_new_roaming_state_does_not_claim_installations_or_enable_setup(tmp_path: Path) -> None:
    data = StateStore(tmp_path / "state.json").read()
    assert data["schema"] == 3
    assert data["setup_packages"] == []
    assert data["setup_enabled"] is False
    assert data["theme"] == DEFAULT_UI_THEME
    assert "installed" not in data


def test_theme_preference_is_tiny_validated_and_persistent(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = StateStore(path)
    for theme in UI_THEMES:
        store.set_theme(theme)
        assert store.read()["theme"] == theme
    assert path.stat().st_size < 4096

    with pytest.raises(ValueError, match="Unknown UI theme"):
        store.set_theme("orange")
    data = store.read()
    data["theme"] = "invalid"
    store.write(data)
    assert store.read()["theme"] == DEFAULT_UI_THEME


def test_my_setup_add_remove_and_enable_are_atomic(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    store.set_setup_package("kitty", True)
    store.set_setup_package("postman", True)
    assert store.read()["setup_packages"] == ["kitty", "postman"]
    assert store.read()["setup_enabled"] is True
    store.set_setup_enabled(False)
    store.set_setup_package("kitty", False)
    assert store.read()["setup_packages"] == ["postman"]
    assert store.read()["setup_enabled"] is False


def test_atomic_json_never_leaves_temporary_file(tmp_path: Path) -> None:
    target = tmp_path / "value.json"
    atomic_json_write(target, {"value": 42})
    assert json.loads(target.read_text(encoding="utf-8")) == {"value": 42}
    assert list(tmp_path.iterdir()) == [target]


def test_old_state_is_not_silently_enrolled_in_my_setup(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"schema": 2, "desired": ["tool"], "installed": {"tool": {"version": "1"}}}),
        encoding="utf-8",
    )
    data = StateStore(path).read()
    assert data["schema"] == 3
    assert data["setup_packages"] == []
    assert data["setup_enabled"] is False
    assert data["legacy_migration_pending"] is True
    assert data["legacy_installed"]["tool"]["version"] == "1"


def test_verified_legacy_payload_migrates_to_root_local_manifest(tmp_path: Path) -> None:
    layout = Layout.at(tmp_path / "post-a" / "goinfre-pm")
    executable = layout.apps / "tool" / "tool"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    preferences_path = tmp_path / "home" / "state.json"
    preferences_path.parent.mkdir()
    preferences_path.write_text(
        json.dumps(
            {
                "schema": 2,
                "desired": ["tool"],
                "installed": {
                    "tool": {
                        "version": "1",
                        "source": "https://example.invalid/tool.tar.gz",
                        "executable": "/old/post/tool",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    preferences = StateStore(preferences_path)
    manager = PackageManager(layout, [package()], preferences)

    local = manager.installations.read()["installed"]
    assert local["tool"]["executable"] == str(executable)
    migrated = preferences.read()
    assert migrated["legacy_migration_pending"] is False
    assert migrated["setup_packages"] == []
    assert "legacy_installed" not in json.loads(preferences_path.read_text(encoding="utf-8"))


def test_stale_legacy_record_is_discarded_instead_of_claiming_installed(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"schema": 2, "installed": {"tool": {"version": "1", "executable": "/gone"}}}),
        encoding="utf-8",
    )
    manager = PackageManager(Layout.at(tmp_path / "post-b" / "goinfre-pm"), [package()], StateStore(path))
    assert manager.installations.read()["installed"] == {}
    assert manager.package_status("tool") == "not_installed"


def test_local_manifest_rejects_a_different_install_root(tmp_path: Path) -> None:
    first = Layout.at(tmp_path / "post-a" / "goinfre-pm")
    second = Layout.at(tmp_path / "post-b" / "goinfre-pm")
    shared_file = tmp_path / "installed.json"
    first_store = LocalStateStore(first, shared_file)
    first_store.set_installed(InstalledPackage("tool", "1", "source", "/post-a/tool"))
    assert LocalStateStore(second, shared_file).read()["installed"] == {}


def test_layout_rejects_symlinked_managed_directories(tmp_path: Path) -> None:
    layout = Layout.at(tmp_path / "goinfre-pm")
    outside = tmp_path / "outside"
    outside.mkdir()
    layout.root.mkdir()
    layout.apps.symlink_to(outside, target_is_directory=True)

    try:
        layout.create()
    except RuntimeError as exc:
        assert "symlinked storage directory" in str(exc)
    else:
        raise AssertionError("symlinked apps directory was accepted")


def test_favorites_and_onboarding_are_persisted_atomically(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    store.set_favorite("kitty", True)
    store.set_favorite("postman", True)
    store.set_favorite("kitty", False)
    store.set_onboarding_complete()
    assert store.read()["favorites"] == ["postman"]
    assert store.read()["onboarding_complete"] is True


def test_concurrent_setup_updates_do_not_lose_preferences(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    threads = [threading.Thread(target=store.set_setup_package, args=(f"tool-{number}", True)) for number in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert set(store.read()["setup_packages"]) == {f"tool-{number}" for number in range(12)}
