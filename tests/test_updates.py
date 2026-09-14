from pathlib import Path

from goinfre_pm.models import Package
from goinfre_pm.storage import StateStore
from goinfre_pm.updates import check_package_update


def package() -> Package:
    return Package(
        "tool",
        "Tool",
        "fixture",
        "Developer Tools",
        "https://github.com/example/tool",
        source_type="github",
        architectures=("any",),
        asset_pattern=r"linux\.tar\.gz$",
    )


def test_update_result_is_cached(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    calls = 0

    def resolver(_package, _log):
        nonlocal calls
        calls += 1
        return "https://example.invalid/tool.tar.gz", "v2"

    first = check_package_update(package(), "v1", store, now=100, resolver=resolver)
    second = check_package_update(package(), "v1", store, now=101, resolver=resolver)
    assert first.available and second.available
    assert calls == 1


def test_offline_update_check_is_unknown_and_cached(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")

    def offline(_package, _log):
        raise RuntimeError("offline")

    result = check_package_update(package(), "v1", store, now=100, resolver=offline)
    assert result.status == "unknown"
    assert result.reason == "offline"
    assert store.read()["update_cache"]["tool"]["status"] == "unknown"


def test_pinned_direct_package_is_not_guessed(tmp_path: Path) -> None:
    direct = package()
    direct.source_type = "deb"
    result = check_package_update(direct, "1.0", StateStore(tmp_path / "state.json"), now=100)
    assert result.status == "unknown"


def test_corrupt_or_future_cache_is_refreshed(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    data = store.read()
    data["update_cache"] = {
        "tool": {"status": "available", "installed_version": "v1", "latest_version": "v9", "checked_at": "bad"}
    }
    store.write(data)
    calls = 0

    def resolver(_package, _log):
        nonlocal calls
        calls += 1
        return "https://example.invalid/tool.tar.gz", "v1"

    assert check_package_update(package(), "v1", store, now=100, resolver=resolver).status == "current"
    data = store.read()
    data["update_cache"]["tool"]["checked_at"] = 999
    store.write(data)
    assert check_package_update(package(), "v1", store, now=101, resolver=resolver).status == "current"
    assert calls == 2
