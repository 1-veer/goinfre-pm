from pathlib import Path
import json
import threading

from goinfre_pm.models import InstalledPackage
from goinfre_pm.storage import StateStore, atomic_json_write, resolve_install_root


def test_install_root_prefers_environment(monkeypatch, tmp_path: Path) -> None:
    base = tmp_path / "campus-storage"
    base.mkdir()
    monkeypatch.setenv("GOINFRE", str(base))
    monkeypatch.setattr("goinfre_pm.storage.Path.home", lambda: tmp_path / "home")
    settings = tmp_path / "missing-settings.json"
    resolved = resolve_install_root(settings_file=settings)
    assert resolved == (base / "goinfre-pm").resolve()


def test_configured_missing_root_does_not_mask_environment(monkeypatch, tmp_path: Path) -> None:
    settings = tmp_path / "config.json"
    settings.write_text(json.dumps({"install_root": str(tmp_path / "gone")}), encoding="utf-8")
    base = tmp_path / "goinfre"
    base.mkdir()
    monkeypatch.setenv("GOINFRE", str(base))
    assert resolve_install_root(settings_file=settings) == (base / "goinfre-pm").resolve()


def test_atomic_state_read_write(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = StateStore(path)
    record = InstalledPackage("tool", "1.2", "https://example.invalid/tool", "/tmp/tool", ["/tmp/tool.desktop"], "now")
    store.set_installed(record)
    data = store.read()
    assert data["desired"] == ["tool"]
    assert data["installed"]["tool"]["version"] == "1.2"
    store.remove("tool")
    assert store.read()["desired"] == []


def test_atomic_json_never_leaves_temporary_file(tmp_path: Path) -> None:
    target = tmp_path / "value.json"
    atomic_json_write(target, {"value": 42})
    assert json.loads(target.read_text(encoding="utf-8")) == {"value": 42}
    assert list(tmp_path.iterdir()) == [target]


def test_schema_one_state_migrates_without_losing_installations(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"schema": 1, "desired": ["tool"], "installed": {"tool": {"version": "1"}}}), encoding="utf-8")
    data = StateStore(path).read()
    assert data["schema"] == 2
    assert data["desired"] == ["tool"]
    assert data["installed"]["tool"]["version"] == "1"
    assert data["favorites"] == []
    assert data["onboarding_complete"] is False


def test_favorites_and_onboarding_are_persisted_atomically(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    store.set_favorite("kitty", True)
    store.set_favorite("postman", True)
    store.set_favorite("kitty", False)
    store.set_onboarding_complete()
    assert store.read()["favorites"] == ["postman"]
    assert store.read()["onboarding_complete"] is True


def test_concurrent_favorite_updates_do_not_lose_state(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    threads = [threading.Thread(target=store.set_favorite, args=(f"tool-{number}", True)) for number in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert store.read()["favorites"] == [f"tool-{number}" for number in (0, 1, 10, 11, 2, 3, 4, 5, 6, 7, 8, 9)]
