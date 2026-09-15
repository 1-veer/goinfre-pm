import asyncio

from textual.command import CommandPalette
from textual.widgets import Button, DataTable, Input, OptionList

from goinfre_pm import app as app_module
from goinfre_pm.models import InstalledPackage, Package
from goinfre_pm.storage import Layout, LocalStateStore, StateStore


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


def test_installed_packages_are_not_offered_to_install(monkeypatch, tmp_path) -> None:
    packages = _packages()
    root = tmp_path / "goinfre-pm"
    installed_path = root / "apps" / packages[0].identifier
    installed_path.mkdir(parents=True)
    executable = installed_path / packages[0].identifier
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    state = StateStore(tmp_path / "state.json")
    state.set_onboarding_complete()
    LocalStateStore(Layout.at(root)).set_installed(
        InstalledPackage(
            packages[0].identifier,
            "1.0",
            packages[0].url,
            str(executable),
            [],
            "earlier",
        ),
    )
    user_bin = tmp_path / "bin"
    user_bin.mkdir()
    (user_bin / packages[0].identifier).symlink_to(executable)
    monkeypatch.setattr("goinfre_pm.integration.USER_BIN", user_bin)
    monkeypatch.setattr(app_module, "resolve_install_root", lambda: root)
    monkeypatch.setattr(app_module, "load_packages", lambda: packages)
    monkeypatch.setattr(app_module, "StateStore", lambda: state)

    async def scenario() -> None:
        app = app_module.GoinfrePMApp()
        calls: list[tuple[list[Package], str]] = []
        app._run_packages = lambda selected, remove: calls.append((selected, remove))  # type: ignore[method-assign]
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.3)
            await pilot.press("right", "i")
            assert isinstance(app.screen, app_module.PackageActionModal)
            assert calls == []
            await pilot.press("right", "enter")
            await pilot.pause()
            assert calls == [([packages[0]], "reinstall")]

            table = app.query_one(DataTable)
            installed_marker = str(table.get_row(packages[0].identifier)[0])
            available_marker = str(table.get_row(packages[1].identifier)[0])
            assert "○" not in installed_marker + available_marker
            assert "●" not in installed_marker + available_marker
            assert "☐" in available_marker

            packages[0].selected = True
            packages[1].selected = True
            app.action_basket()
            assert isinstance(app.screen, app_module.BasketModal)
            assert app.screen.packages == [packages[1]]
            await pilot.press("escape")

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


def test_my_setup_keyboard_section_and_marker(monkeypatch, tmp_path) -> None:
    packages = _packages()
    state = StateStore(tmp_path / "state.json")
    state.set_onboarding_complete()
    monkeypatch.setattr(app_module, "resolve_install_root", lambda: tmp_path / "goinfre-pm")
    monkeypatch.setattr(app_module, "load_packages", lambda: packages)
    monkeypatch.setattr(app_module, "StateStore", lambda: state)

    async def scenario() -> None:
        app = app_module.GoinfrePMApp(auto_restore=False)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.3)
            await pilot.press("right", "m")
            assert state.read()["setup_packages"] == [packages[0].identifier]
            assert state.read()["setup_enabled"] is True
            marker = str(app.query_one(DataTable).get_row(packages[0].identifier)[0])
            assert "◆" in marker

            app.category = "Auto Setup"
            app._refresh()
            assert [package.identifier for package in app.visible_packages] == [packages[0].identifier]
            assert "Not installed here" in str(app.query_one(DataTable).get_row(packages[0].identifier)[3])
            categories = app.query_one(OptionList)
            assert all(
                str(categories.get_option_at_index(index).prompt) != "Missing Here"
                for index in range(categories.option_count)
            )
            assert str(app.query_one("#setup-toggle-button", Button).label) == "Remove from Auto Setup"

            await pilot.click("#setup-toggle-button")
            await pilot.pause()
            assert state.read()["setup_packages"] == []

    asyncio.run(scenario())


def test_ctrl_p_themes_and_creator_are_persistent(monkeypatch, tmp_path) -> None:
    state = StateStore(tmp_path / "state.json")
    state.set_onboarding_complete()
    monkeypatch.setattr(app_module, "resolve_install_root", lambda: tmp_path / "goinfre-pm")
    monkeypatch.setattr(app_module, "load_packages", _packages)
    monkeypatch.setattr(app_module, "StateStore", lambda: state)

    async def scenario() -> None:
        first = app_module.GoinfrePMApp(auto_restore=False)
        async with first.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.3)
            assert first.has_class("theme-purple")
            assert "Made by VEER" in str(first.query_one("#creator").renderable)
            commands = list(first.get_system_commands(first.screen))
            theme_commands = [command for command in commands if command.title.startswith("Theme:")]
            assert {command.title.split()[1] for command in theme_commands} == {
                "Purple",
                "Green",
                "Blue",
                "Black",
                "Red",
            }

            await pilot.press("ctrl+p")
            await pilot.pause()
            assert isinstance(first.screen, CommandPalette)
            await pilot.press("escape")

            for theme in ("green", "blue", "black", "red", "purple", "green"):
                first.set_ui_theme(theme)
                assert first.has_class(f"theme-{theme}")
                assert state.read()["theme"] == theme

        second = app_module.GoinfrePMApp(auto_restore=False)
        async with second.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.3)
            assert second.ui_theme == "green"
            assert second.has_class("theme-green")

    asyncio.run(scenario())


def test_auto_setup_asks_before_restoring_and_no_restore_skips_it(monkeypatch, tmp_path) -> None:
    packages = _packages()[:2]
    state = StateStore(tmp_path / "state.json")
    state.set_onboarding_complete()
    state.set_setup_packages([package.identifier for package in packages], True)
    monkeypatch.setattr(app_module, "resolve_install_root", lambda: tmp_path / "goinfre-pm")
    monkeypatch.setattr(app_module, "load_packages", lambda: packages)
    monkeypatch.setattr(app_module, "StateStore", lambda: state)

    async def scenario() -> None:
        restored: list[list[str]] = []

        def fake_restore(_cancel, identifiers, **_callbacks):
            restored.append(list(identifiers))
            total = len(identifiers)
            for index, identifier in enumerate(identifiers, 1):
                yield ("package", (identifier, index, total))
                yield ("log", f"restored {identifier}")
                yield ("restored", identifier)

        declined = app_module.GoinfrePMApp(auto_restore=True)
        declined.manager.restore = fake_restore  # type: ignore[method-assign]
        async with declined.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.4)
            assert restored == []
            assert isinstance(declined.screen, app_module.AutoSetupPromptModal)
            prompt = str(declined.screen.query_one("#auto-setup-prompt-items").renderable)
            assert all(package.name in prompt for package in packages)
            assert declined.screen.query_one("#install", Button).has_focus
            await pilot.press("right")
            assert declined.screen.query_one("#cancel", Button).has_focus
            await pilot.press("enter")
            await pilot.pause()
            assert restored == []
            assert "skipped for this launch" in str(declined.query_one("#operation").renderable)

        accepted = app_module.GoinfrePMApp(auto_restore=True)
        accepted.manager.restore = fake_restore  # type: ignore[method-assign]
        async with accepted.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.4)
            assert isinstance(accepted.screen, app_module.AutoSetupPromptModal)
            await pilot.press("enter")
            await pilot.pause(0.6)
            assert restored == [[package.identifier for package in packages]]
            assert isinstance(accepted.screen, app_module.SummaryModal)
            assert "Auto Setup restore complete" in str(accepted.screen.query_one(".modal-title").renderable)

        skipped: list[object] = []
        no_restore = app_module.GoinfrePMApp(auto_restore=False)
        no_restore.manager.restore = lambda *_args: skipped.append(True) or iter(())  # type: ignore[method-assign]
        async with no_restore.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.5)
            assert skipped == []
            assert not isinstance(no_restore.screen, app_module.AutoSetupPromptModal)
            assert not isinstance(no_restore.screen, app_module.SummaryModal)

        state.set_setup_enabled(False)
        disabled: list[object] = []
        paused = app_module.GoinfrePMApp(auto_restore=True)
        paused.manager.restore = lambda *_args, **_kwargs: disabled.append(True) or iter(())  # type: ignore[method-assign]
        async with paused.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.5)
            assert disabled == []
            assert not isinstance(paused.screen, app_module.AutoSetupPromptModal)
            assert not isinstance(paused.screen, app_module.SummaryModal)

    asyncio.run(scenario())


def test_cancel_key_and_my_setup_removal_choice_are_keyboard_safe(monkeypatch, tmp_path) -> None:
    packages = _packages()[:1]
    state = StateStore(tmp_path / "state.json")
    state.set_onboarding_complete()
    state.set_setup_package(packages[0].identifier, True)
    root = tmp_path / "goinfre-pm"
    executable = root / "apps" / packages[0].identifier / packages[0].identifier
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    LocalStateStore(Layout.at(root)).set_installed(
        InstalledPackage(packages[0].identifier, "1", packages[0].url, str(executable))
    )
    monkeypatch.setattr(app_module, "resolve_install_root", lambda: root)
    monkeypatch.setattr(app_module, "load_packages", lambda: packages)
    monkeypatch.setattr(app_module, "StateStore", lambda: state)

    async def scenario() -> None:
        app = app_module.GoinfrePMApp(auto_restore=False)
        calls: list[tuple[list[Package], str, bool]] = []
        app._run_packages = (  # type: ignore[method-assign]
            lambda selected, operation, keep_setup=False: calls.append((selected, operation, keep_setup))
        )
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.3)
            app.busy = True
            await pilot.press("c")
            assert app.cancel_event.is_set()
            app.busy = False

            await pilot.press("right", "r")
            assert isinstance(app.screen, app_module.RemovalChoiceModal)
            assert app.screen.query_one("#forget", Button).has_focus
            await pilot.press("right", "enter")
            await pilot.pause()
            assert calls == [(packages, "remove", True)]
            assert app.is_running

    asyncio.run(scenario())


def test_failed_restore_is_visible_for_the_current_session(monkeypatch, tmp_path) -> None:
    packages = _packages()[:1]
    state = StateStore(tmp_path / "state.json")
    state.set_onboarding_complete()
    state.set_setup_package(packages[0].identifier, True)
    monkeypatch.setattr(app_module, "resolve_install_root", lambda: tmp_path / "goinfre-pm")
    monkeypatch.setattr(app_module, "load_packages", lambda: packages)
    monkeypatch.setattr(app_module, "StateStore", lambda: state)

    async def scenario() -> None:
        app = app_module.GoinfrePMApp(auto_restore=True)

        def failed_restore(_cancel, identifiers, **_callbacks):
            identifier = identifiers[0]
            yield ("package", (identifier, 1, 1))
            yield ("failed", (identifier, "offline"))

        app.manager.restore = failed_restore  # type: ignore[method-assign]
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.4)
            assert isinstance(app.screen, app_module.AutoSetupPromptModal)
            await pilot.press("enter")
            await pilot.pause(0.6)
            assert app.runtime_status[packages[0].identifier] == "failed"
            table = app.screen_stack[0].query_one(DataTable)
            assert "Auto-install failed" in str(table.get_row(packages[0].identifier)[3])
            assert isinstance(app.screen, app_module.SummaryModal)

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
            assert app.manager.installations.read()["installed"] == {}

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

            app._show_summary([package], [], "install", 1.2)
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
            narrow.push_screen(
                app_module.AutoSetupPromptModal(
                    [(f"Application {index}", "not installed") for index in range(30)]
                )
            )
            await pilot.pause()
            prompt = narrow.screen.query_one(".auto-setup-prompt-modal")
            assert prompt.region.height <= 24
            assert prompt.region.width <= 80
            await pilot.press("escape")
            narrow.push_screen(app_module.PackageActionModal(_packages()[0], True))
            await pilot.pause()
            action = narrow.screen.query_one(".action-modal")
            assert action.region.height <= 24
            assert action.region.width <= 80
            await pilot.press("escape")
            narrow.push_screen(app_module.RemovalChoiceModal(1))
            await pilot.pause()
            removal = narrow.screen.query_one(".removal-modal")
            assert removal.region.height <= 24
            assert removal.region.width <= 80
            await pilot.press("escape")

        wide = app_module.GoinfrePMApp()
        async with wide.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.3)
            assert wide.query_one("#categories").display
            assert wide.query_one("#details").display

    asyncio.run(scenario())
