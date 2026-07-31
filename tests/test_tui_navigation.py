import asyncio

from textual.widgets import DataTable, OptionList

from goinfre_pm import app as app_module
from goinfre_pm.models import Package


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

    async def scenario() -> None:
        app = app_module.GoinfrePMApp()
        async with app.run_test(size=(120, 36)) as pilot:
            categories = app.query_one(OptionList)
            table = app.query_one(DataTable)
            assert categories.has_focus
            await pilot.press("right")
            assert table.has_focus
            await pilot.press("down", "down")
            assert table.cursor_row == 2
            await pilot.press("space")
            assert packages[2].selected
            assert table.cursor_row == 2
            await pilot.press("left")
            assert categories.has_focus

    asyncio.run(scenario())
