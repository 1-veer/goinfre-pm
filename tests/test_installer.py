from pathlib import Path

import pytest

from goinfre_pm.installer import PackageManager
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
    state.set_installed(
        InstalledPackage("zen-browser", "latest", package.url, str(helper), [], "earlier"),
        install_root=layout.root,
    )
    monkeypatch.setattr("goinfre_pm.integration.USER_BIN", tmp_path / "bin")
    monkeypatch.setattr("goinfre_pm.integration.DESKTOP_DIR", tmp_path / "applications")
    monkeypatch.setattr("goinfre_pm.integration.ICON_DIR", tmp_path / "icons")

    PackageManager(layout, [package], state).repair("zen-browser")

    repaired = state.read()["installed"]["zen-browser"]
    assert repaired["executable"] == str(browser)
    assert (tmp_path / "bin" / "zen-browser").resolve() == browser.resolve()
    assert state.read()["desired"] == ["zen-browser"]
