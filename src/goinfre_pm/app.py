from __future__ import annotations

from pathlib import Path
import shutil

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Resize
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Input, Label, OptionList, ProgressBar, RichLog, Static
from textual.widgets.option_list import Option

from .branding import DISPLAY_NAME, VERSION
from .config import load_packages
from .installer import PackageManager
from .models import Package
from .storage import Layout, StateStore, available_space, is_writable_directory, persist_root, resolve_install_root


class ConfirmModal(ModalScreen[bool]):
    def __init__(self, title: str, message: str) -> None:
        super().__init__()
        self.title_text = title
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Label(self.title_text, classes="modal-title")
            yield Static(self.message)
            with Horizontal(classes="modal-buttons"):
                yield Button("Confirm", variant="error", id="confirm")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")


class PathModal(ModalScreen[Path | None]):
    def __init__(self, current: Path) -> None:
        super().__init__()
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Label("Change install root", classes="modal-title")
            yield Input(str(self.current), id="path-input")
            yield Static("Choose a writable goinfre location. This choice is persisted.", id="path-message")
            with Horizontal(classes="modal-buttons"):
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        value = Path(self.query_one(Input).value).expanduser().resolve()
        if not is_writable_directory(value, create=True):
            self.query_one("#path-message", Static).update("[red]That directory is not writable.[/red]")
            return
        self.dismiss(value)


class HelpModal(ModalScreen[None]):
    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Label("Keyboard shortcuts", classes="modal-title")
            yield Static("↑/↓ or j/k navigate   Space select   / search\nEnter details/action   i install   I selected install\nr remove   R selected remove   a select visible\np path   l logs   ? help   q/Esc quit")
            with Horizontal(classes="modal-buttons"):
                yield Button("Close", variant="primary", id="close")

    def on_button_pressed(self, _event: Button.Pressed) -> None:
        self.dismiss(None)


class GoinfrePMApp(App[None]):
    CSS_PATH = "theme.tcss"
    TITLE = DISPLAY_NAME
    BINDINGS = [
        Binding("q", "quit", "Quit"), Binding("escape", "quit", "Quit"),
        Binding("j", "down", "Down", show=False), Binding("k", "up", "Up", show=False),
        Binding("space", "toggle", "Select"), Binding("slash", "search", "Search"),
        Binding("i", "install_one", "Install"), Binding("shift+i", "install_selected", "Install selected"),
        Binding("r", "remove_one", "Remove"), Binding("shift+r", "remove_selected", "Remove selected"),
        Binding("a", "select_all", "Select all"), Binding("p", "path", "Path"),
        Binding("l", "logs", "Logs"), Binding("question_mark", "help", "Help"),
        Binding("enter", "primary", "Details", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        root = resolve_install_root()
        if root is None:
            raise RuntimeError("No install root configured")
        self.layout = Layout.at(root)
        self.layout.create()
        self.packages = load_packages()
        self.manager = PackageManager(self.layout, self.packages)
        self.visible_packages = list(self.packages)
        self.busy = False
        self.category = "All"

    def compose(self) -> ComposeResult:
        with Horizontal(id="header"):
            yield Static(f"{DISPLAY_NAME}  v{VERSION}", id="brand")
            yield Static(id="storage")
        with Horizontal(id="body"):
            with Vertical(id="categories", classes="panel"):
                yield OptionList(*(Option(item) for item in ["All", "Browsers", "Editors and IDEs", "Developer Tools", "Communication", "Media", "Installed"]))
            with Vertical(id="catalog", classes="panel"):
                yield DataTable(id="package-table", cursor_type="row", zebra_stripes=True)
            with Vertical(id="details", classes="panel"):
                yield Static("Package details", id="details-title")
                yield Static(id="details-body")
        with Vertical(id="tasks"):
            yield Static("Ready", id="operation")
            yield ProgressBar(total=100, show_eta=False, id="progress")
            yield RichLog(id="logs", markup=True, max_lines=500)
        yield Input(placeholder="Search packages…", id="search")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("", "Package", "Category", "Status", "Source", "Version")
        self.query_one(OptionList).highlighted = 0
        self._refresh()

    def on_resize(self, event: Resize) -> None:
        """Keep the package table usable on narrow campus terminals."""
        width = event.size.width
        self.query_one("#categories").display = width >= 100
        self.query_one("#details").display = width >= 82
        self.query_one("#tasks").styles.height = 5 if width < 82 else (6 if width < 100 else 7)

    def _refresh(self, query: str = "") -> None:
        installed = self.manager.state.read().get("installed", {})
        needle = query.casefold()
        self.visible_packages = [package for package in self.packages if
            (self.category == "All" or (self.category == "Installed" and package.identifier in installed) or package.category == self.category)
            and (not needle or needle in f"{package.identifier} {package.name} {package.description}".casefold())]
        table = self.query_one(DataTable)
        table.clear()
        for package in self.visible_packages:
            marker = "●" if package.selected else "○"
            status = "[green]Installed[/green]" if package.identifier in installed else ("[red]Incompatible[/red]" if not package.compatible else "Available")
            table.add_row(marker, package.name, package.category, status, package.source_type, package.version, key=package.identifier)
        self._details()
        free = available_space(self.layout.root)
        self.query_one("#storage", Static).update(f"{self.layout.root}  •  {free / 1024**3:.1f} GiB free")

    def _current(self) -> Package | None:
        table = self.query_one(DataTable)
        if not self.visible_packages or table.cursor_row < 0 or table.cursor_row >= len(self.visible_packages):
            return None
        return self.visible_packages[table.cursor_row]

    def _details(self) -> None:
        package = self._current()
        if package is None:
            self.query_one("#details-body", Static).update("No matching packages")
            return
        live = self.layout.apps / package.identifier
        executable = self.manager.state.read().get("installed", {}).get(package.identifier, {}).get("executable", "—")
        size = sum(path.stat().st_size for path in live.rglob("*") if path.is_file()) if live.is_dir() else 0
        self.query_one("#details-body", Static).update(
            f"[b]{package.name}[/b]\n\n{package.description}\n\n"
            f"Category: {package.category}\nSource: {package.source_type}\nArchitecture: {', '.join(package.architectures)}\n"
            f"Stored: {live}\nExecutable: {executable}\nSize: {size / 1024**2:.1f} MiB\n"
            f"Status: {'installed' if live.is_dir() else 'not installed'}\n{package.notes}"
        )

    def on_data_table_row_highlighted(self, _event: DataTable.RowHighlighted) -> None:
        self._details()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self.category = str(event.option.prompt)
        self._refresh()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            self._refresh(event.value)

    def action_down(self) -> None:
        self.query_one(DataTable).action_cursor_down()

    def action_up(self) -> None:
        self.query_one(DataTable).action_cursor_up()

    def action_toggle(self) -> None:
        package = self._current()
        if package and not self.busy:
            package.selected = not package.selected
            self._refresh(self.query_one("#search", Input).value)

    def action_select_all(self) -> None:
        if not self.busy:
            target = not all(package.selected for package in self.visible_packages)
            for package in self.visible_packages:
                package.selected = target
            self._refresh()

    def action_search(self) -> None:
        search = self.query_one("#search", Input)
        search.add_class("visible")
        search.focus()

    def action_logs(self) -> None:
        self.query_one(RichLog).focus()

    def action_help(self) -> None:
        self.push_screen(HelpModal())

    def action_primary(self) -> None:
        self._details()

    def action_path(self) -> None:
        if not self.busy:
            self.push_screen(PathModal(self.layout.root), self._path_changed)

    def _path_changed(self, root: Path | None) -> None:
        if root:
            persist_root(root)
            self.layout = Layout.at(root)
            self.layout.create()
            self.manager = PackageManager(self.layout, self.packages)
            self._refresh()
            self.notify(f"Install root changed to {root}")

    def action_install_one(self) -> None:
        package = self._current()
        if package:
            self._run_packages([package], remove=False)

    def action_install_selected(self) -> None:
        self._run_packages([package for package in self.packages if package.selected], remove=False)

    def action_remove_one(self) -> None:
        package = self._current()
        if package:
            self.push_screen(ConfirmModal("Remove application?", f"Remove {package.name} application files and integrations? User configuration will remain."), lambda ok: self._run_packages([package], True) if ok else None)

    def action_remove_selected(self) -> None:
        packages = [package for package in self.packages if package.selected]
        if packages:
            self.push_screen(ConfirmModal("Remove selected applications?", f"Remove {len(packages)} selected applications? User configuration will remain."), lambda ok: self._run_packages(packages, True) if ok else None)

    def _run_packages(self, packages: list[Package], remove: bool) -> None:
        if self.busy:
            self.notify("Another operation is active", severity="warning")
        elif not packages:
            self.notify("No packages selected", severity="warning")
        else:
            self._worker(packages, remove)

    @work(thread=True, exclusive=True)
    def _worker(self, packages: list[Package], remove: bool) -> None:
        self.busy = True
        try:
            for package in packages:
                self.call_from_thread(self.query_one("#operation", Static).update, f"{'Removing' if remove else 'Installing'} {package.name}")
                events = self.manager.remove(package.identifier) if remove else self.manager.install(
                    package.identifier,
                    progress_callback=lambda value: self.call_from_thread(
                        self.query_one(ProgressBar).update, progress=value
                    ),
                )
                for kind, value in events:
                    if kind == "log":
                        self.call_from_thread(self.query_one(RichLog).write, str(value))
                    elif kind == "progress":
                        self.call_from_thread(self.query_one(ProgressBar).update, progress=float(value))
                self.call_from_thread(self.notify, f"{package.name}: {'removed' if remove else 'installed'}")
        except Exception as exc:
            self.call_from_thread(self.query_one(RichLog).write, f"[red]{exc}[/red]")
            self.call_from_thread(self.notify, str(exc), severity="error")
        finally:
            self.busy = False
            self.call_from_thread(self.query_one("#operation", Static).update, "Ready")
            self.call_from_thread(self._refresh)


def run_tui() -> None:
    GoinfrePMApp().run()
