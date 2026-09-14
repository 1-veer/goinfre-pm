import asyncio

from textual.widgets import Button, DataTable, Input, OptionList

from goinfre_pm import app as app_module
from goinfre_pm.models import Package
from goinfre_pm.storage import StateStore


def _packages() -> list[Package]:
    return [
        Package(
            identifier=identifier,
            name=name,
            description="Test package",
            category=category,
            url=f"https://example.invalid/{identifier}.tar.gz",
            source_type="tar",
            architectures=("any",),
            executable_candidates=(identifier,),
            download_size=size,
            installed_size=size * 2,
        )
        for identifier, name, category, size in (
            ("vscodium", "VSCodium", "Editors and IDEs", 100),
            ("kitty", "Kitty", "Developer Tools", 200),
            ("github-cli", "GitHub CLI", "Developer Tools", 300),
            ("lazygit", "lazygit", "Developer Tools", 400),
        )
    ]


def test_arrow_focus_and_space_preserve_package(monkeypatch, tmp_path) -> None:
    packages = [
        Package(
            identifier=f"tool-{number}",
            name=f"Tool {number}",
            description="Test package",
            category="Developer Tools",
            url=f"https://example.invalid/tool-{number}.tar.gz",
            source_type="tar",
            executable_candidates=(f"tool-{number}",),
        )
        for number in range(3)
    ]
    root = tmp_path / "goinfre-pm"
    monkeypatch.setattr(app_module, "resolve_install_root", lambda: root)
    monkeypatch.setattr(app_module, "load_packages", lambda: packages)
    monkeypatch.setattr(app_module, "StateStore", lambda: StateStore(tmp_path / "state.json"))

    async def scenario() -> None:
        app = app_module.GoinfrePMApp()
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.3)
            if isinstance(app.screen, app_module.WelcomeModal):
                await pilot.press("enter")
                await pilot.pause()
            categories = app.query_one(OptionList)
            table = app.query_one(DataTable)
            assert categories.has_focus
            assert app.query_one("#categories").border_title == "CATEGORIES  •  ACTIVE"
            await pilot.press("right")
            assert table.has_focus
            assert app.query_one("#catalog").border_title == "PACKAGES  •  ACTIVE"
            assert app.query_one("#categories").border_title == "CATEGORIES"
            await pilot.press("down", "down")
            assert table.cursor_row == 2
            await pilot.press("space")
            assert packages[2].selected
            assert table.cursor_row == 2
            await pilot.press("left")
            assert categories.has_focus
            assert app.query_one("#categories").border_title == "CATEGORIES  •  ACTIVE"

            await pilot.press("slash")
            assert app.query_one("#search", Input).has_class("visible")
            await pilot.press("escape")
            assert not app.query_one("#search", Input).has_class("visible")
            assert table.has_focus

            app.busy = True
            await pilot.press("q")
            assert app.is_running
            app.busy = False

            table.focus()
            await pilot.press("r")
            assert isinstance(app.screen, app_module.ConfirmModal)
            assert app.screen.query_one("#confirm", Button).has_focus
            await pilot.press("right")
            assert app.screen.query_one("#cancel", Button).has_focus
            await pilot.press("enter")
            await pilot.pause()
            assert not isinstance(app.screen, app_module.ConfirmModal)
            assert app.query_one(DataTable)
            assert app.is_running

    asyncio.run(scenario())


def test_onboarding_is_skippable_and_only_shown_once(monkeypatch, tmp_path) -> None:
    state = StateStore(tmp_path / "state.json")
    monkeypatch.setattr(app_module, "resolve_install_root", lambda: tmp_path / "goinfre-pm")
    monkeypatch.setattr(app_module, "load_packages", _packages)
    monkeypatch.setattr(app_module, "StateStore", lambda: state)

    async def scenario() -> None:
        first = app_module.GoinfrePMApp()
        async with first.run_test(size=(120, 36)) as pilot:
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(first.screen, app_module.WelcomeModal)
            await pilot.press("escape")
            await pilot.pause()
            assert state.read()["onboarding_complete"] is True

        second = app_module.GoinfrePMApp()
        async with second.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.3)
            assert not isinstance(second.screen, app_module.WelcomeModal)

    asyncio.run(scenario())


def test_packs_basket_favorites_sort_and_doctor_are_keyboard_accessible(monkeypatch, tmp_path) -> None:
    packages = _packages()
    state = StateStore(tmp_path / "state.json")
    state.set_onboarding_complete()
    monkeypatch.setattr(app_module, "resolve_install_root", lambda: tmp_path / "goinfre-pm")
    monkeypatch.setattr(app_module, "load_packages", lambda: packages)
    monkeypatch.setattr(app_module, "StateStore", lambda: state)

    async def scenario() -> None:
        app = app_module.GoinfrePMApp()
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.3)

            await pilot.press("t")
            assert isinstance(app.screen, app_module.StarterPacksModal)
            await pilot.press("enter")
            await pilot.pause()
            assert {package.identifier for package in packages if package.selected} == {
                "vscodium", "kitty", "github-cli", "lazygit"
            }
            assert state.read()["installed"] == {}

            await pilot.press("b")
            assert isinstance(app.screen, app_module.BasketModal)
            assert app.screen.estimate.known_download == 1000
            await pilot.press("right")
            assert app.screen.query_one("#cancel", Button).has_focus
            await pilot.press("enter")
            await pilot.pause()
            assert not isinstance(app.screen, app_module.BasketModal)

            await pilot.press("right", "f")
            current = app._current()
            assert current is not None
            assert state.read()["favorites"] == [current.identifier]
            app.category = "Favorites"
            app._refresh()
            assert [package.identifier for package in app.visible_packages] == [current.identifier]

            app.category = "All"
            app._refresh()
            await pilot.press("s")
            assert isinstance(app.screen, app_module.SortModal)
            await pilot.press("down", "enter")
            await pilot.pause()
            assert app.sort_key == "category"

            await pilot.press("d")
            assert isinstance(app.screen, app_module.DoctorModal)
            doctor_table = app.screen.query_one(DataTable)
            assert doctor_table.has_focus
            await pilot.press("down")
            assert doctor_table.cursor_row == 1
            await pilot.press("escape")

    asyncio.run(scenario())


def test_progress_and_completion_summary(monkeypatch, tmp_path) -> None:
    packages = _packages()
    state = StateStore(tmp_path / "state.json")
    state.set_onboarding_complete()
    monkeypatch.setattr(app_module, "resolve_install_root", lambda: tmp_path / "goinfre-pm")
    monkeypatch.setattr(app_module, "load_packages", lambda: packages)
    monkeypatch.setattr(app_module, "StateStore", lambda: state)

    async def scenario() -> None:
        app = app_module.GoinfrePMApp()
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.3)
            package = packages[0]
            package.selected = True
            app._show_transfer(package, 1024, 4096, 512, 6.0)
            operation = str(app.query_one("#operation").renderable)
            assert "1.0 KiB/4.0 KiB" in operation
            assert "ETA 6s" in operation

            app._show_summary([package], [], False, 1.2)
            assert isinstance(app.screen, app_module.SummaryModal)
            assert package.selected is False
            await pilot.press("enter")

    asyncio.run(scenario())


def test_responsive_layout_keeps_catalog_usable(monkeypatch, tmp_path) -> None:
    state = StateStore(tmp_path / "state.json")
    state.set_onboarding_complete()
    monkeypatch.setattr(app_module, "resolve_install_root", lambda: tmp_path / "goinfre-pm")
    monkeypatch.setattr(app_module, "load_packages", _packages)
    monkeypatch.setattr(app_module, "StateStore", lambda: state)

    async def scenario() -> None:
        narrow = app_module.GoinfrePMApp()
        async with narrow.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.3)
            assert not narrow.query_one("#categories").display
            assert not narrow.query_one("#details").display
            assert narrow.query_one(DataTable).display
            assert narrow.query_one(DataTable).has_focus
            await pilot.press("t")
            packs = narrow.screen.query_one(".packs-modal")
            assert packs.region.height <= 24
            assert packs.region.width <= 80
            await pilot.press("escape", "d")
            doctor = narrow.screen.query_one(".doctor-modal")
            assert doctor.region.height <= 24
            assert doctor.region.width <= 80
            await pilot.press("escape")

        wide = app_module.GoinfrePMApp()
        async with wide.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.3)
            assert wide.query_one("#categories").display
            assert wide.query_one("#details").display

    asyncio.run(scenario())
