from __future__ import annotations

from pathlib import Path
import time

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import DescendantFocus, Resize
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Input, Label, OptionList, ProgressBar, RichLog, Static
from textual.widgets.option_list import Option

from .branding import DISPLAY_NAME, VERSION
from .config import load_packages
from .doctor import DoctorCheck, collect_doctor_checks
from .errors import error_text, write_crash_log
from .experience import STARTER_PACKS, StarterPack, apply_starter_pack, estimate_basket, human_size, sort_packages
from .installer import PackageManager
from .models import Package
from .storage import Layout, StateStore, available_space, is_writable_directory, persist_root, resolve_install_root
from .updates import UpdateInfo, check_package_update


class ConfirmModal(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

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

    def on_mount(self) -> None:
        self.query_one("#confirm", Button).focus()

    def focus_button(self, delta: int) -> None:
        """Move between modal buttons without leaking arrows to the app."""
        buttons = list(self.query(Button))
        if not buttons:
            return
        focused = self.focused
        index = buttons.index(focused) if focused in buttons else 0
        buttons[(index + delta) % len(buttons)].focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)


class PathModal(ModalScreen[Path | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

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
        raw_value = self.query_one(Input).value.strip()
        if not raw_value:
            self.query_one("#path-message", Static).update("[red]Enter a directory path.[/red]")
            return
        try:
            value = Path(raw_value).expanduser().resolve()
        except OSError as exc:
            self.query_one("#path-message", Static).update(f"[red]{error_text(exc)}[/red]")
            return
        if not is_writable_directory(value, create=True):
            self.query_one("#path-message", Static).update("[red]That directory is not writable.[/red]")
            return
        self.dismiss(value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class HelpModal(ModalScreen[None]):
    BINDINGS = [Binding("escape", "cancel", "Close", show=False)]

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Label("Keyboard shortcuts", classes="modal-title")
            yield Static(
                "↑/↓ or j/k navigate   ←/→ change pane   Space select\n"
                "i install   I/b review basket   r/R remove   a select visible\n"
                "t Starter Packs   f favorite   s sort   d Doctor\n"
                "/ search   p path   l logs   w welcome   ? help   q/Esc back"
            )
            with Horizontal(classes="modal-buttons"):
                yield Button("Close", variant="primary", id="close")

    def on_button_pressed(self, _event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class WelcomeModal(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "Close", show=False), Binding("enter", "close", "Continue", show=False)]

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal welcome-modal"):
            yield Label(f"Welcome to {DISPLAY_NAME}", classes="modal-title")
            yield Static(
                "Install large developer applications without sudo or filling your home quota.\n\n"
                f"Application payloads → [b]{self.root}[/b]\n"
                "Launchers and small state → ~/.local and ~/.config\n"
                "Existing application profiles and settings remain in their normal locations.\n\n"
                "Use ↑/↓ to browse, → to enter the package list, Space to select, "
                "t for Starter Packs, and b to review your basket."
            )
            with Horizontal(classes="modal-buttons"):
                yield Button("Start exploring", variant="primary", id="continue")

    def on_button_pressed(self, _event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class StarterPacksModal(ModalScreen[StarterPack | None]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("space", "apply", "Select pack", show=False),
    ]

    def __init__(self, packages: list[Package]) -> None:
        super().__init__()
        self.packages = {package.identifier: package for package in packages}

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal packs-modal"):
            yield Label("Starter Packs", classes="modal-title")
            yield Static("Choose a pack to add its applications to your basket. Nothing installs yet.", classes="modal-copy")
            yield OptionList(*(Option(pack.name) for pack in STARTER_PACKS), id="pack-list")
            yield Static(id="pack-details")
            with Horizontal(classes="modal-buttons"):
                yield Button("Add to basket", variant="primary", id="apply")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        listing = self.query_one("#pack-list", OptionList)
        listing.highlighted = 0
        listing.focus()
        self._show_pack(0)

    def _show_pack(self, index: int | None) -> None:
        if index is None or not 0 <= index < len(STARTER_PACKS):
            return
        pack = STARTER_PACKS[index]
        names = [self.packages[item].name for item in pack.package_ids if item in self.packages]
        self.query_one("#pack-details", Static).update(f"[b]{pack.description}[/b]\n\n" + "\n".join(f"  • {name}" for name in names))

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self._show_pack(event.option_index)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(STARTER_PACKS[event.option_index])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        else:
            self.action_apply()

    def action_apply(self) -> None:
        index = self.query_one("#pack-list", OptionList).highlighted
        if index is not None:
            self.dismiss(STARTER_PACKS[index])

    def action_cancel(self) -> None:
        self.dismiss(None)


class BasketModal(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, packages: list[Package], free_space: int, installed: dict[str, object] | None = None) -> None:
        super().__init__()
        self.packages = packages
        self.estimate = estimate_basket(packages, free_space, installed)

    def compose(self) -> ComposeResult:
        estimate = self.estimate
        download = human_size(estimate.known_download)
        installed = human_size(estimate.known_installed)
        if estimate.unknown_downloads:
            download += f" + {estimate.unknown_downloads} unknown"
        if estimate.unknown_installed:
            installed += f" + {estimate.unknown_installed} unknown"
        package_lines = "\n".join(f"  • {package.name}" for package in self.packages)
        space_line = (
            "[red]Not enough space for the known download and staging sizes.[/red]"
            if estimate.insufficient_space
            else f"Peak space remaining after known download + install: {human_size(estimate.remaining_after_known)}"
        )
        with Vertical(classes="modal basket-modal"):
            yield Label(f"Installation basket · {estimate.count} selected", classes="modal-title")
            yield Static(
                f"{package_lines}\n\nDownload: {download}\nInstalled: {installed}\n"
                f"{space_line}\n\n"
                "Unknown sizes are never guessed. Review the list before continuing."
            )
            with Horizontal(classes="modal-buttons"):
                yield Button("Install selected", variant="primary", id="install", disabled=estimate.insufficient_space)
                yield Button("Keep editing", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#cancel" if self.estimate.insufficient_space else "#install", Button).focus()

    def focus_button(self, delta: int) -> None:
        buttons = [button for button in self.query(Button) if not button.disabled]
        if not buttons:
            return
        focused = self.focused
        index = buttons.index(focused) if focused in buttons else 0
        buttons[(index + delta) % len(buttons)].focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "install")

    def action_cancel(self) -> None:
        self.dismiss(False)


class SortModal(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]
    OPTIONS = (("Name", "name"), ("Category", "category"), ("Installed first", "installed"), ("Largest known size", "size"), ("Updates first", "updates"))

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal sort-modal"):
            yield Label("Sort packages", classes="modal-title")
            yield OptionList(*(Option(label) for label, _key in self.OPTIONS), id="sort-list")

    def on_mount(self) -> None:
        listing = self.query_one(OptionList)
        listing.highlighted = 0
        listing.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.OPTIONS[event.option_index][1])

    def action_cancel(self) -> None:
        self.dismiss(None)


class SummaryModal(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "Close", show=False), Binding("enter", "close", "Close", show=False)]

    def __init__(self, title: str, summary: str) -> None:
        super().__init__()
        self.title_text = title
        self.summary = summary

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal summary-modal"):
            yield Label(self.title_text, classes="modal-title")
            yield Static(self.summary)
            with Horizontal(classes="modal-buttons"):
                yield Button("Close", variant="primary", id="close")

    def on_button_pressed(self, _event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class DoctorModal(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "Close", show=False)]

    def __init__(self, checks: list[DoctorCheck]) -> None:
        super().__init__()
        self.checks = checks

    def compose(self) -> ComposeResult:
        passed = sum(check.status == "ok" for check in self.checks)
        warnings = sum(check.status == "warning" for check in self.checks)
        failed = sum(check.status == "error" for check in self.checks)
        with Vertical(classes="modal doctor-modal"):
            yield Label(f"{DISPLAY_NAME} Doctor · {passed} pass · {warnings} warn · {failed} fail", classes="modal-title")
            yield DataTable(id="doctor-table", cursor_type="row", zebra_stripes=True)
            yield Static("Highlight a check to see the recommended action.", id="doctor-action")
            with Horizontal(classes="modal-buttons"):
                yield Button("Close", variant="primary", id="close")

    def on_mount(self) -> None:
        table = self.query_one("#doctor-table", DataTable)
        table.add_columns("", "Check", "Details")
        markers = {"ok": "[green]PASS[/green]", "warning": "[yellow]WARN[/yellow]", "error": "[red]FAIL[/red]"}
        for number, check in enumerate(self.checks):
            table.add_row(markers[check.status], check.name, check.detail, key=str(number))
        if self.checks:
            table.focus()
            self._show_action(0)

    def _show_action(self, row: int) -> None:
        if 0 <= row < len(self.checks):
            check = self.checks[row]
            self.query_one("#doctor-action", Static).update(check.action or "No action needed.")

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._show_action(event.cursor_row)

    def on_button_pressed(self, _event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class GoinfrePMApp(App[None]):
    CSS_PATH = "theme.tcss"
    TITLE = DISPLAY_NAME
    BINDINGS = [
        Binding("q", "quit", "Quit"), Binding("escape", "back", "Back", show=False),
        Binding("j", "down", "Down", show=False), Binding("k", "up", "Up", show=False),
        Binding("right", "focus_packages", "Packages", show=False, priority=True),
        Binding("left", "focus_categories", "Categories", show=False, priority=True),
        Binding("space", "toggle", "Select"), Binding("slash", "search", "Search"),
        Binding("i", "install_one", "Install"), Binding("shift+i", "install_selected", "Install selected"),
        Binding("r", "remove_one", "Remove"), Binding("shift+r", "remove_selected", "Remove selected"),
        Binding("a", "select_all", "Select all"), Binding("p", "path", "Path"),
        Binding("t", "starter_packs", "Packs"), Binding("b", "basket", "Basket"),
        Binding("f", "favorite", "Favorite"), Binding("s", "sort", "Sort"),
        Binding("d", "doctor", "Doctor"), Binding("w", "welcome", "Welcome", show=False),
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
        self.state = StateStore()
        self.manager = PackageManager(self.layout, self.packages, self.state)
        self.visible_packages = list(self.packages)
        self.busy = False
        self.category = "All"
        self.sort_key = "catalog"
        self.update_available: set[str] = set()
        self.update_info: dict[str, UpdateInfo] = {}
        self.startup_finished = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="header"):
            yield Static(f"{DISPLAY_NAME}  v{VERSION}", id="brand")
            yield Static("0 selected", id="basket-status")
            yield Static(id="storage")
        with Horizontal(id="body"):
            with Vertical(id="categories", classes="panel"):
                categories = list(dict.fromkeys(package.category for package in self.packages))
                yield OptionList(*(Option(item) for item in ["All", "Favorites", *categories, "Installed"]), id="category-list")
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
        yield Static(f"{DISPLAY_NAME}\nPreparing your workspace…", id="splash")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("", "Package", "Category", "Status", "Source", "Version")
        categories = self.query_one(OptionList)
        categories.highlighted = 0
        self._refresh()
        if self.size.width < 100:
            table.focus()
            self._set_active_pane("catalog")
        else:
            categories.focus()
            self._set_active_pane("categories")
        self.set_timer(0.25, self._finish_startup)
        self._check_updates()

    def _finish_startup(self) -> None:
        if self.startup_finished:
            return
        self.startup_finished = True
        splashes = list(self.query("#splash"))
        if splashes:
            splashes[0].remove()
        if not self.state.read().get("onboarding_complete", False):
            self.push_screen(WelcomeModal(self.layout.root), self._onboarding_closed)

    def _onboarding_closed(self, _result: None = None) -> None:
        self.state.set_onboarding_complete()

    def on_resize(self, event: Resize) -> None:
        """Keep the package table usable on narrow campus terminals."""
        width = event.size.width
        self.query_one("#categories").display = width >= 100
        self.query_one("#details").display = width >= 82
        self.query_one("#tasks").styles.height = 5 if width < 82 else (6 if width < 100 else 7)
        self.query_one("#brand").styles.width = 18 if width < 82 else 22
        self.query_one("#basket-status").styles.width = 24 if width < 82 else 34
        if width < 100 and self.query_one(OptionList).has_focus and self.visible_packages:
            self.action_focus_packages()

    def _set_active_pane(self, active: str | None) -> None:
        titles = {"categories": "CATEGORIES", "catalog": "PACKAGES", "details": "DETAILS"}
        for identifier, title in titles.items():
            panel = self.query_one(f"#{identifier}", Vertical)
            panel.border_title = f"{title}  •  ACTIVE" if identifier == active else title

    def on_descendant_focus(self, event: DescendantFocus) -> None:
        if event.widget.id == "category-list":
            self._set_active_pane("categories")
        elif event.widget.id == "package-table":
            self._set_active_pane("catalog")

    def _refresh(self, query: str = "", preserve_identifier: str | None = None) -> None:
        installed = self.manager.state.read().get("installed", {})
        needle = query.casefold()
        favorites = set(self.state.read().get("favorites", []))
        filtered = [package for package in self.packages if
            (package.enabled or package.identifier in installed)
            and
            (self.category == "All"
             or (self.category == "Installed" and package.identifier in installed)
             or (self.category == "Favorites" and package.identifier in favorites)
             or package.category == self.category)
            and (not needle or needle in f"{package.identifier} {package.name} {package.description}".casefold())]
        installed_sizes = {
            identifier: record["installed_size"]
            for identifier, record in installed.items()
            if isinstance(record, dict) and isinstance(record.get("installed_size"), int)
        }
        self.visible_packages = (
            sort_packages(filtered, self.sort_key, set(installed), self.update_available, installed_sizes)
            if self.sort_key != "catalog"
            else filtered
        )
        table = self.query_one(DataTable)
        table.clear()
        for package in self.visible_packages:
            favorite_marker = "[yellow]★[/yellow]" if package.identifier in favorites else "·"
            selection_marker = "[bold #c4b5fd]●[/bold #c4b5fd]" if package.selected else "○"
            marker = favorite_marker + selection_marker
            if package.identifier in installed:
                update = self.update_info.get(package.identifier)
                if not package.enabled:
                    status = "[yellow]Installed · unsupported[/yellow]"
                elif package.identifier in self.update_available:
                    status = "[yellow]Update available[/yellow]"
                elif update is None:
                    status = "Installed · checking"
                elif update.status == "unknown":
                    status = "[dim]Installed · update unknown[/dim]"
                else:
                    status = "[green]Installed[/green]"
            else:
                status = "[red]Incompatible[/red]" if not package.compatible else "Available"
            table.add_row(marker, package.name, package.category, status, package.source_type, package.version, key=package.identifier)
        if preserve_identifier:
            for row, package in enumerate(self.visible_packages):
                if package.identifier == preserve_identifier:
                    table.move_cursor(row=row, animate=False)
                    break
        self._details()
        try:
            free_bytes = available_space(self.layout.root)
            free_text = f"{free_bytes / 1024**3:.1f} GiB free"
        except OSError:
            free_bytes = 0
            free_text = "storage unavailable"
        self.query_one("#storage", Static).update(f"{self.layout.root}  •  {free_text}")
        selected = [package for package in self.packages if package.selected]
        estimate = estimate_basket(selected, free_bytes, installed)
        size_text = human_size(estimate.known_installed)
        if estimate.unknown_installed:
            size_text += f" + {estimate.unknown_installed} unknown"
        self.query_one("#basket-status", Static).update(f"{estimate.count} selected  •  {size_text}")

    def _current(self) -> Package | None:
        table = self.query_one(DataTable)
        if not self.visible_packages or table.cursor_row < 0 or table.cursor_row >= len(self.visible_packages):
            return None
        return self.visible_packages[table.cursor_row]

    def _details(self) -> None:
        package = self._current()
        if package is None:
            if self.category == "Favorites":
                message = "No favorites yet. Highlight a package and press f to keep it here."
            elif self.category == "Installed":
                message = "Nothing is installed yet. Choose All or a Starter Pack to begin."
            elif self.query_one("#search", Input).value:
                message = "No package matches this search. Try a name, category, or package ID."
            else:
                message = "No packages are available in this category."
            self.query_one("#details-body", Static).update(message)
            return
        live = self.layout.apps / package.identifier
        state = self.manager.state.read()
        record = state.get("installed", {}).get(package.identifier, {})
        record = record if isinstance(record, dict) else {}
        executable = record.get("executable", "—")
        size = record.get("installed_size")
        if not isinstance(size, int):
            size = package.installed_size
        size_text = human_size(size) if isinstance(size, int) else "unknown"
        update = self.update_info.get(package.identifier)
        if update is None:
            update_text = "checking" if package.identifier in state.get("installed", {}) else "not installed"
        elif update.status == "available":
            update_text = f"{update.latest_version} available"
        elif update.status == "current":
            update_text = "current"
        else:
            update_text = f"unknown ({update.reason})" if update.reason else "unknown"
        self.query_one("#details-body", Static).update(
            f"[b]{package.name}[/b]\n\n{package.description}\n\n"
            f"Category: {package.category}\nSource: {package.source_type}\nArchitecture: {', '.join(package.architectures)}\n"
            f"Stored: {live}\nExecutable: {executable}\nSize: {size_text}\n"
            f"Status: {'installed' if live.is_dir() else 'not installed'}\nUpdate: {update_text}\n{package.notes}"
        )

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "package-table" and not isinstance(self.screen, ModalScreen):
            self._details()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id == "category-list" and not isinstance(self.screen, ModalScreen):
            self.category = str(event.option.prompt)
            self._refresh()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            self._refresh(event.value)

    def action_down(self) -> None:
        if isinstance(self.screen, ModalScreen):
            action = getattr(self.screen.focused, "action_cursor_down", None)
            if callable(action):
                action()
            return
        categories = self.query_one(OptionList)
        if categories.has_focus:
            categories.action_cursor_down()
        else:
            self.query_one(DataTable).action_cursor_down()

    def action_up(self) -> None:
        if isinstance(self.screen, ModalScreen):
            action = getattr(self.screen.focused, "action_cursor_up", None)
            if callable(action):
                action()
            return
        categories = self.query_one(OptionList)
        if categories.has_focus:
            categories.action_cursor_up()
        else:
            self.query_one(DataTable).action_cursor_up()

    def action_focus_packages(self) -> None:
        if isinstance(self.screen, ModalScreen):
            focus_button = getattr(self.screen, "focus_button", None)
            if callable(focus_button):
                focus_button(1)
            return
        table = self.query_one(DataTable)
        if self.visible_packages:
            table.focus()
            self._set_active_pane("catalog")

    def action_focus_categories(self) -> None:
        if isinstance(self.screen, ModalScreen):
            focus_button = getattr(self.screen, "focus_button", None)
            if callable(focus_button):
                focus_button(-1)
            return
        categories = self.query_one(OptionList)
        if categories.display:
            categories.focus()
            self._set_active_pane("categories")

    def action_back(self) -> None:
        if not self.startup_finished:
            self._finish_startup()
            return
        search = self.query_one("#search", Input)
        if search.has_class("visible"):
            search.remove_class("visible")
            self.action_focus_packages()
        elif self.query_one(DataTable).has_focus:
            self.action_focus_categories()

    def action_quit(self) -> None:
        if self.busy:
            self.notify("Wait for the current operation to finish before quitting", severity="warning")
            return
        self.exit()

    def action_toggle(self) -> None:
        table = self.query_one(DataTable)
        if not table.has_focus:
            return
        package = self._current()
        if package and not self.busy:
            package.selected = not package.selected
            self._refresh(self.query_one("#search", Input).value, package.identifier)

    def action_select_all(self) -> None:
        if not self.busy:
            current = self._current()
            target = not all(package.selected for package in self.visible_packages)
            for package in self.visible_packages:
                package.selected = target
            self._refresh(preserve_identifier=current.identifier if current else None)

    def action_search(self) -> None:
        search = self.query_one("#search", Input)
        search.add_class("visible")
        search.focus()
        self._set_active_pane(None)

    def action_logs(self) -> None:
        self.query_one(RichLog).focus()
        self._set_active_pane(None)

    def action_help(self) -> None:
        self.push_screen(HelpModal())

    def action_welcome(self) -> None:
        if not self.busy:
            self.push_screen(WelcomeModal(self.layout.root))

    def action_starter_packs(self) -> None:
        if not self.busy:
            self.push_screen(StarterPacksModal(self.packages), self._starter_pack_selected)

    def _starter_pack_selected(self, pack: StarterPack | None) -> None:
        if pack is None:
            return
        installed = set(self.state.read().get("installed", {}))
        identifiers = tuple(identifier for identifier in apply_starter_pack(pack, self.packages) if identifier not in installed)
        for package in self.packages:
            if package.identifier in identifiers:
                package.selected = True
        self._refresh(preserve_identifier=self._current().identifier if self._current() else None)
        self.notify(f"{pack.name}: added {len(identifiers)} packages to the basket")

    def action_basket(self) -> None:
        if self.busy:
            self.notify("Another operation is active", severity="warning")
            return
        packages = [package for package in self.packages if package.selected]
        if not packages:
            self.notify("Your basket is empty", severity="warning")
            return
        installed = self.state.read().get("installed", {})
        try:
            free_space = available_space(self.layout.root)
        except OSError:
            self.notify("The install root is unavailable; run Doctor or choose another path", severity="error")
            return
        self.push_screen(BasketModal(packages, free_space, installed), lambda install: self._run_packages(packages, False) if install else None)

    def action_favorite(self) -> None:
        package = self._current()
        if package is None or self.busy:
            return
        favorites = set(self.state.read().get("favorites", []))
        favorite = package.identifier not in favorites
        self.state.set_favorite(package.identifier, favorite)
        self._refresh(self.query_one("#search", Input).value, package.identifier)
        self.notify(f"{package.name}: {'added to' if favorite else 'removed from'} favorites")

    def action_sort(self) -> None:
        if not self.busy:
            self.push_screen(SortModal(), self._sort_changed)

    def _sort_changed(self, key: str | None) -> None:
        if key:
            current = self._current()
            self.sort_key = key
            self._refresh(self.query_one("#search", Input).value, current.identifier if current else None)

    def action_doctor(self) -> None:
        if not self.busy:
            self.push_screen(DoctorModal(collect_doctor_checks(self.layout.root, self.packages, self.state)))

    @work(thread=True, exclusive=True, group="update-checks")
    def _check_updates(self) -> None:
        installed = self.state.read().get("installed", {})
        if not isinstance(installed, dict):
            return
        found: set[str] = set()
        results: dict[str, UpdateInfo] = {}
        for package in self.packages:
            record = installed.get(package.identifier)
            if not isinstance(record, dict):
                continue
            info = check_package_update(package, str(record.get("version", "")), self.state)
            results[package.identifier] = info
            if info.available:
                found.add(package.identifier)
        self.update_info = results
        self.update_available = found
        self.call_from_thread(self._refresh)

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
            self.manager = PackageManager(self.layout, self.packages, self.state)
            self._refresh()
            self.notify(f"Install root changed to {root}")

    def action_install_one(self) -> None:
        package = self._current()
        if package:
            self._run_packages([package], remove=False)

    def action_install_selected(self) -> None:
        self.action_basket()

    def action_remove_one(self) -> None:
        package = self._current()
        if package and not self.busy:
            self.push_screen(ConfirmModal("Remove application?", f"Remove {package.name} application files and integrations? User configuration will remain."), lambda ok: self._run_packages([package], True) if ok else None)

    def action_remove_selected(self) -> None:
        packages = [package for package in self.packages if package.selected]
        if packages and not self.busy:
            self.push_screen(ConfirmModal("Remove selected applications?", f"Remove {len(packages)} selected applications? User configuration will remain."), lambda ok: self._run_packages(packages, True) if ok else None)

    def _run_packages(self, packages: list[Package], remove: bool) -> None:
        if self.busy:
            self.notify("Another operation is active", severity="warning")
        elif not packages:
            self.notify("No packages selected", severity="warning")
        else:
            self.busy = True
            self._worker(packages, remove)

    @work(thread=True, exclusive=True)
    def _worker(self, packages: list[Package], remove: bool) -> None:
        started = time.monotonic()
        succeeded: list[Package] = []
        failed: list[tuple[Package, str]] = []
        try:
            for package in packages:
                try:
                    self.call_from_thread(self.query_one(ProgressBar).update, progress=0)
                    self.call_from_thread(self.query_one("#operation", Static).update, f"{'Removing' if remove else 'Installing'} {package.name}")
                    events = self.manager.remove(package.identifier) if remove else self.manager.install(
                        package.identifier,
                        progress_callback=lambda value: self.call_from_thread(
                            self.query_one(ProgressBar).update, progress=value
                        ),
                        transfer_callback=lambda done, total, speed, eta, item=package: self.call_from_thread(
                            self._show_transfer, item, done, total, speed, eta
                        ),
                    )
                    for kind, value in events:
                        if kind == "log":
                            self.call_from_thread(self.query_one(RichLog).write, str(value))
                        elif kind == "progress":
                            self.call_from_thread(self.query_one(ProgressBar).update, progress=float(value))
                    self.call_from_thread(self.notify, f"{package.name}: {'removed' if remove else 'installed'}")
                    succeeded.append(package)
                except Exception as exc:
                    message = error_text(exc)
                    failed.append((package, message))
                    self.call_from_thread(self.query_one(RichLog).write, f"[red]{package.name}: {message}[/red]")
                    self.call_from_thread(self.notify, f"{package.name}: {message}", severity="error")
        finally:
            self.busy = False
            self.call_from_thread(self.query_one("#operation", Static).update, "Ready")
            self.call_from_thread(self._refresh)
            elapsed = time.monotonic() - started
            self.call_from_thread(self._show_summary, succeeded, failed, remove, elapsed)

    def _show_transfer(self, package: Package, done: int, total: int | None, speed: float, eta: float | None) -> None:
        amount = human_size(done)
        total_text = human_size(total) if total is not None else "unknown"
        eta_text = f" · ETA {max(0, round(eta))}s" if eta is not None else ""
        self.query_one("#operation", Static).update(
            f"Downloading {package.name} · {amount}/{total_text} · {human_size(int(speed))}/s{eta_text}"
        )

    def _show_summary(
        self,
        succeeded: list[Package],
        failed: list[tuple[Package, str]],
        remove: bool,
        elapsed: float,
    ) -> None:
        for package in succeeded:
            package.selected = False
        action = "Removed" if remove else "Installed"
        lines = [
            f"{action}: {len(succeeded)}   Failed: {len(failed)}   Time: {elapsed:.1f}s",
            f"Location: {self.layout.apps}",
        ]
        if succeeded:
            lines.extend(("", "Completed:", *(f"  • {package.name}" for package in succeeded)))
        if failed:
            lines.extend(("", "Needs attention:", *(f"  • {package.name}: {message}" for package, message in failed)))
        self._refresh()
        self.push_screen(SummaryModal("Operation complete", "\n".join(lines)))

    def _handle_exception(self, error: Exception) -> None:
        """Replace Textual's terminal traceback with a concise recoverable report."""
        crash_log = write_crash_log(error)
        self._return_code = 1
        if self._exception is None:
            self._exception = error
            self._exception_event.set()
        suffix = f" Details saved to {crash_log}." if crash_log else ""
        self.panic(f"{DISPLAY_NAME} encountered an unexpected error: {error_text(error)}.{suffix}")


def run_tui() -> None:
    GoinfrePMApp().run()
