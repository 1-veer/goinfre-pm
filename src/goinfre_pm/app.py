from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import threading
import time

from textual import work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import DescendantFocus, Resize
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Input, Label, OptionList, ProgressBar, RichLog, Static
from textual.widgets.option_list import Option

from .branding import DISPLAY_NAME, VERSION
from .config import load_packages
from .doctor import DoctorCheck, collect_doctor_checks
from .errors import error_text, write_crash_log
from .experience import STARTER_PACKS, StarterPack, apply_starter_pack, estimate_basket, human_size, sort_packages
from .installer import OperationBusyError, PackageManager
from .models import Package
from .storage import (
    DEFAULT_UI_THEME,
    UI_THEMES,
    Layout,
    StateStore,
    available_space,
    is_writable_directory,
    persist_root,
    resolve_install_root,
)
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


class PackageActionModal(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, package: Package, repairable: bool, launchable: bool = True) -> None:
        super().__init__()
        self.package = package
        self.repairable = repairable
        self.launchable = launchable

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal action-modal"):
            yield Label(self.package.name, classes="modal-title")
            yield Static(
                "This application already has a payload on this post. Choose an explicit action; "
                "your profile and cache will remain untouched.",
                classes="modal-copy",
            )
            with Vertical(classes="action-buttons"):
                with Horizontal(classes="action-button-row"):
                    if self.launchable:
                        yield Button("Launch", variant="primary", id="launch")
                    yield Button(
                        "Update",
                        variant="default" if self.launchable else "primary",
                        id="update",
                        disabled=not self.package.enabled or not self.package.compatible,
                    )
                    yield Button("Reinstall", id="reinstall", disabled=not self.package.enabled or not self.package.compatible)
                with Horizontal(classes="action-button-row"):
                    yield Button("Repair", id="repair", disabled=not self.repairable)
                    yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        next(button for button in self.query(Button) if not button.disabled).focus()

    def focus_button(self, delta: int) -> None:
        buttons = [button for button in self.query(Button) if not button.disabled]
        focused = self.focused
        index = buttons.index(focused) if focused in buttons else 0
        buttons[(index + delta) % len(buttons)].focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None if event.button.id == "cancel" else event.button.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class RemovalChoiceModal(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, count: int) -> None:
        super().__init__()
        self.count = count

    def compose(self) -> ComposeResult:
        noun = "package" if self.count == 1 else "packages"
        with Vertical(classes="modal removal-modal"):
            yield Label("Remove Auto Setup applications?", classes="modal-title")
            yield Static(
                f"{self.count} selected {noun} belong to Auto Setup. Removing them from Auto Setup prevents "
                "them being offered for installation next time; profiles and cache still remain.",
                classes="modal-copy",
            )
            with Horizontal(classes="modal-buttons action-buttons"):
                yield Button("Remove + forget", variant="error", id="forget")
                yield Button("Keep in Auto Setup", id="keep")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#forget", Button).focus()

    def focus_button(self, delta: int) -> None:
        buttons = list(self.query(Button))
        focused = self.focused
        index = buttons.index(focused) if focused in buttons else 0
        buttons[(index + delta) % len(buttons)].focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None if event.button.id == "cancel" else event.button.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


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
                "Enter open/launch   i install/actions   I/b basket   r/R remove\n"
                "a select visible\n"
                "m toggle Auto Setup   M add selection   c cancel operation\n"
                "t Starter Packs   f favorite   s sort   d Doctor   x leave-post cleanup\n"
                "/ search   p path   ^P themes/mode   l logs   w welcome   ? help   q exit   Esc back"
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
                "m to save an app in Auto Setup, ^P to choose a theme, t for Starter Packs, "
                "and b to review your basket.\n\nMade by VEER"
                "\n\nBefore leaving a shared post, press x to remove GoinfrePM data (recommended)."
            )
            with Horizontal(classes="modal-buttons"):
                yield Button("Start exploring", variant="primary", id="continue")

    def on_button_pressed(self, _event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class AutoSetupPromptModal(ModalScreen[bool]):
    """Ask before preparing missing Auto Setup applications on this post."""

    BINDINGS = [Binding("escape", "cancel", "Not now", show=False)]

    def __init__(self, packages: list[tuple[str, str]]) -> None:
        super().__init__()
        self.packages = packages

    def compose(self) -> ComposeResult:
        count = len(self.packages)
        noun = "app" if count == 1 else "apps"
        with Vertical(classes="modal auto-setup-prompt-modal"):
            yield Label("Set up this post?", classes="modal-title")
            yield Static(
                f"GoinfrePM found {count} {noun} from your Auto Setup that "
                f"{'is' if count == 1 else 'are'} not ready on this post:",
                classes="modal-copy",
            )
            with VerticalScroll(id="auto-setup-prompt-list"):
                yield Static(
                    "\n".join(f"• {name} — {reason}" for name, reason in self.packages),
                    id="auto-setup-prompt-items",
                )
            yield Static(
                "Install or repair them now? Choose Not now to continue without changing anything.",
                classes="modal-copy",
            )
            with Horizontal(classes="modal-buttons"):
                yield Button("Install now", variant="primary", id="install")
                yield Button("Not now", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#install", Button).focus()

    def focus_button(self, delta: int) -> None:
        buttons = list(self.query(Button))
        focused = self.focused
        index = buttons.index(focused) if focused in buttons else 0
        buttons[(index + delta) % len(buttons)].focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "install")

    def action_cancel(self) -> None:
        self.dismiss(False)


class AutoSetupBusyModal(ModalScreen[bool]):
    """Offer a calm retry when another real payload operation lasts too long."""

    BINDINGS = [Binding("escape", "cancel", "Not now", show=False)]

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal auto-setup-prompt-modal"):
            yield Label("Auto Setup is still preparing", classes="modal-title")
            yield Static(
                "GoinfrePM is finishing another task on this post. Nothing failed and no "
                "application was marked as installed. You can retry now or continue without "
                "changing anything.",
                classes="modal-copy",
            )
            with Horizontal(classes="modal-buttons"):
                yield Button("Retry", variant="primary", id="retry")
                yield Button("Not now", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#retry", Button).focus()

    def focus_button(self, delta: int) -> None:
        buttons = list(self.query(Button))
        focused = self.focused
        index = buttons.index(focused) if focused in buttons else 0
        buttons[(index + delta) % len(buttons)].focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "retry")

    def action_cancel(self) -> None:
        self.dismiss(False)


class LeavePostModal(ModalScreen[bool]):
    """Confirm complete removal of this post's GoinfrePM storage."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, root: Path, bytes_used: int | None = None) -> None:
        super().__init__()
        self.root = root
        self.bytes_used = bytes_used

    def compose(self) -> ComposeResult:
        reclaimable = (
            f"Reclaimable: [b]{human_size(self.bytes_used)}[/b]\n"
            if self.bytes_used is not None
            else ""
        )
        with Vertical(classes="modal auto-setup-prompt-modal"):
            yield Label("Clean this post before leaving?", classes="modal-title")
            yield Static(
                "Recommended on shared 1337/42 workstations. This permanently removes:\n"
                "• downloaded and installed application payloads\n"
                "• GoinfrePM downloads, logs, runtime files, and the goinfre Python environment\n"
                "• launchers and desktop entries created by GoinfrePM\n\n"
                f"Storage: [b]{self.root}[/b]\n"
                f"{reclaimable}\n"
                "Your Auto Setup, theme, favorites, and ordinary application profiles remain "
                "available for the next post. Personal configuration and cache are not deleted. "
                "GoinfrePM will close when cleanup finishes.",
                classes="modal-copy",
            )
            with Horizontal(classes="modal-buttons"):
                yield Button("Delete post data", variant="error", id="confirm")
                yield Button("Cancel", variant="primary", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def focus_button(self, delta: int) -> None:
        buttons = list(self.query(Button))
        focused = self.focused
        index = buttons.index(focused) if focused in buttons else 1
        buttons[(index + delta) % len(buttons)].focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)


class QuitPostModal(ModalScreen[str | None]):
    """Offer post cleanup at the moment a student normally quits."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, root: Path, bytes_used: int) -> None:
        super().__init__()
        self.root = root
        self.bytes_used = bytes_used

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal quit-post-modal"):
            yield Label("Leaving this post?", classes="modal-title")
            yield Static(
                f"GoinfrePM is using [b]{human_size(self.bytes_used)}[/b] on this post.\n"
                f"Storage: [b]{self.root}[/b]\n\n"
                "[b]Recommended:[/b] clean the post before leaving. This removes GoinfrePM "
                "applications and runtime files from this computer, while keeping your Auto "
                "Setup, theme, favorites, and ordinary application profiles.",
                classes="modal-copy",
            )
            with Horizontal(classes="modal-buttons quit-post-buttons"):
                yield Button("Clean & exit", variant="error", id="clean")
                yield Button("Exit without cleaning", variant="primary", id="exit")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        # Destructive cleanup is recommended but never the accidental default.
        self.query_one("#exit", Button).focus()

    def focus_button(self, delta: int) -> None:
        buttons = list(self.query(Button))
        focused = self.focused
        index = buttons.index(focused) if focused in buttons else 1
        buttons[(index + delta) % len(buttons)].focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None if event.button.id == "cancel" else event.button.id)

    def action_cancel(self) -> None:
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


class DoctorModal(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "close", "Close", show=False)]

    def __init__(self, checks: list[DoctorCheck], cleanup_bytes: int = 0, cleanup_count: int = 0) -> None:
        super().__init__()
        self.checks = checks
        self.cleanup_bytes = cleanup_bytes
        self.cleanup_count = cleanup_count

    def compose(self) -> ComposeResult:
        passed = sum(check.status == "ok" for check in self.checks)
        warnings = sum(check.status == "warning" for check in self.checks)
        failed = sum(check.status == "error" for check in self.checks)
        with Vertical(classes="modal doctor-modal"):
            yield Label(f"{DISPLAY_NAME} Doctor · {passed} pass · {warnings} warn · {failed} fail", classes="modal-title")
            yield DataTable(id="doctor-table", cursor_type="row", zebra_stripes=True)
            yield Static("Highlight a check to see the recommended action.", id="doctor-action")
            with Horizontal(classes="modal-buttons"):
                if self.cleanup_count:
                    yield Button(f"Clean {human_size(self.cleanup_bytes)}", variant="primary", id="clean")
                yield Button("Close", variant="primary" if not self.cleanup_count else "default", id="close")

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

    def focus_button(self, delta: int) -> None:
        buttons = list(self.query(Button))
        focused = self.focused
        index = buttons.index(focused) if focused in buttons else (-1 if delta > 0 else 0)
        buttons[(index + delta) % len(buttons)].focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss("clean" if event.button.id == "clean" else None)

    def action_close(self) -> None:
        self.dismiss(None)


class GoinfrePMApp(App[None]):
    CSS_PATH = "theme.tcss"
    TITLE = DISPLAY_NAME
    BINDINGS = [
        Binding("q", "quit", "Exit"), Binding("x", "leave_post", "Clean & leave"),
        Binding("escape", "back", "Back", show=False),
        Binding("j", "down", "Down", show=False), Binding("k", "up", "Up", show=False),
        Binding("right", "focus_packages", "Packages", show=False, priority=True),
        Binding("left", "focus_categories", "Categories", show=False, priority=True),
        Binding("space", "toggle", "Select"), Binding("slash", "search", "Search"),
        Binding("i", "install_one", "Install"), Binding("shift+i", "install_selected", "Install selected"),
        Binding("r", "remove_one", "Remove"), Binding("shift+r", "remove_selected", "Remove selected"),
        Binding("a", "select_all", "Select all"), Binding("p", "path", "Path"),
        Binding("t", "starter_packs", "Packs"), Binding("b", "basket", "Basket"),
        Binding("f", "favorite", "Favorite"), Binding("s", "sort", "Sort"),
        Binding("m", "setup_toggle", "Auto Setup"), Binding("shift+m", "setup_selected", "Save selected", show=False),
        Binding("c", "cancel_operation", "Cancel", show=False),
        Binding("d", "doctor", "Doctor"), Binding("w", "welcome", "Welcome", show=False),
        Binding("l", "logs", "Logs"), Binding("question_mark", "help", "Help"),
        Binding("enter", "primary", "Open", show=False),
    ]

    def __init__(self, auto_restore: bool = True) -> None:
        super().__init__()
        root = resolve_install_root()
        if root is None:
            raise RuntimeError("No install root configured")
        self.layout = Layout.at(root)
        self.layout.create()
        self.packages = load_packages()
        self.state = StateStore()
        self.ui_theme = str(self.state.read().get("theme", DEFAULT_UI_THEME))
        self.manager = PackageManager(self.layout, self.packages, self.state)
        self.auto_restore = auto_restore
        self.visible_packages = list(self.packages)
        self.busy = False
        self.cancel_event = threading.Event()
        self.runtime_status: dict[str, str] = {}
        self.category = "All"
        self.sort_key = "catalog"
        self.update_available: set[str] = set()
        self.update_info: dict[str, UpdateInfo] = {}
        self.startup_finished = False
        self.post_storage_bytes: int | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="header"):
            yield Static(f"{DISPLAY_NAME}  v{VERSION}", id="brand")
            yield Static("0 selected", id="basket-status")
            yield Static(id="storage")
        with Horizontal(id="body"):
            with Vertical(id="categories", classes="panel"):
                categories = list(dict.fromkeys(package.category for package in self.packages))
                yield OptionList(
                    *(Option(item) for item in ["All", "Auto Setup", "Favorites", *categories, "Installed"]),
                    id="category-list",
                )
                yield Static("Made by VEER", id="creator")
            with Vertical(id="catalog", classes="panel"):
                yield DataTable(id="package-table", cursor_type="row", zebra_stripes=True)
            with Vertical(id="details", classes="panel"):
                yield Static("Package details", id="details-title")
                yield Static(id="details-body")
                yield Button("Add to Auto Setup", id="setup-toggle-button")
        with Vertical(id="tasks"):
            yield Static("Ready", id="operation")
            yield ProgressBar(total=100, show_eta=False, id="progress")
            yield RichLog(id="logs", markup=True, max_lines=500)
        yield Input(placeholder="Search packages…", id="search")
        yield Static(f"{DISPLAY_NAME}\nPreparing your workspace…", id="splash")
        yield Footer()

    def on_mount(self) -> None:
        self.dark = self.state.read()["mode"] == "dark"
        self.set_ui_theme(self.ui_theme, persist=False)
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
        self.call_after_refresh(self._refresh_post_storage_usage)

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        yield from super().get_system_commands(screen)
        yield SystemCommand(
            "Clean this post before leaving (recommended)",
            "Remove GoinfrePM applications and runtime data from this shared workstation",
            self.action_leave_post,
        )
        for theme in UI_THEMES:
            label = theme.title()
            suffix = " (current)" if theme == self.ui_theme else ""
            yield SystemCommand(
                f"Theme: {label}{suffix}",
                f"Use the {label.lower()} GoinfrePM color theme",
                lambda selected=theme: self.set_ui_theme(selected),
            )

    def set_ui_theme(self, theme: str, persist: bool = True) -> None:
        if theme not in UI_THEMES:
            raise ValueError(f"Unknown UI theme: {theme}")
        for available in UI_THEMES:
            self.remove_class(f"theme-{available}")
        self.add_class(f"theme-{theme}")
        self.ui_theme = theme
        if persist:
            self.state.set_theme(theme)
            self.notify(f"{theme.title()} theme saved for future sessions")

    def action_toggle_dark(self) -> None:
        self.dark = not self.dark
        mode = "dark" if self.dark else "light"
        self.state.set_mode(mode)
        self.notify(f"{mode.title()} mode saved for future sessions")

    def _finish_startup(self) -> None:
        if self.startup_finished:
            return
        self.startup_finished = True
        splashes = list(self.query("#splash"))
        if splashes:
            splashes[0].remove()
        self.query_one(RichLog).write(
            "[dim]Recommended: press x before leaving this shared post to reclaim GoinfrePM storage.[/dim]"
        )
        if not self.state.read().get("onboarding_complete", False):
            self.push_screen(WelcomeModal(self.layout.root), self._onboarding_closed)
        else:
            self.set_timer(0.05, self._maybe_auto_restore)

    def _onboarding_closed(self, _result: None = None) -> None:
        self.state.set_onboarding_complete()
        self.set_timer(0.05, self._maybe_auto_restore)

    def _maybe_auto_restore(self) -> None:
        preferences = self.state.read()
        if not self.auto_restore or not preferences.get("setup_enabled") or self.busy:
            return
        setup = set(preferences.get("setup_packages", []))
        pending = [
            package for package in self.packages
            if package.identifier in setup
            and package.enabled
            and package.compatible
            and not self.manager.installation(package.identifier).healthy
        ]
        if not pending:
            return
        prompt_items = [
            (
                package.name,
                "needs launcher repair"
                if self.manager.installation(package.identifier).status == "repairable"
                else "not installed",
            )
            for package in pending
        ]
        self.push_screen(
            AutoSetupPromptModal(prompt_items),
            lambda accepted: self._auto_setup_choice(pending, accepted),
        )

    def _auto_setup_choice(self, pending: list[Package], accepted: bool) -> None:
        if not accepted:
            self.query_one("#operation", Static).update("Auto Setup skipped for this launch")
            self.query_one(RichLog).write("Auto Setup was not installed; no application files were changed.")
            return
        self.busy = True
        self.cancel_event = threading.Event()
        self._restore_worker(pending)

    def _show_auto_setup_retry(self, packages: list[Package]) -> None:
        self.push_screen(
            AutoSetupBusyModal(),
            lambda accepted: self._retry_auto_setup(packages, accepted),
        )

    def _retry_auto_setup(self, packages: list[Package], accepted: bool) -> None:
        if not accepted:
            self.query_one("#operation", Static).update("Auto Setup postponed for this launch")
            self.query_one(RichLog).write("Auto Setup was postponed; no application files were changed.")
            return
        if self.busy:
            self.notify("An operation is already running", severity="warning")
            return
        self.busy = True
        self.cancel_event = threading.Event()
        self.query_one("#operation", Static).update("Preparing your Auto Setup…")
        self._restore_worker(packages)

    def _main_screen(self) -> Screen | None:
        """Return the underlying package screen even while a modal is active."""
        for screen in self.screen_stack:
            if list(screen.query("#package-table")):
                return screen
        return None

    def on_resize(self, event: Resize) -> None:
        """Keep the package table usable on narrow campus terminals."""
        main_screen = self._main_screen()
        if not self.is_running or main_screen is None:
            return
        categories = list(main_screen.query("#categories"))
        details = list(main_screen.query("#details"))
        tasks = list(main_screen.query("#tasks"))
        brands = list(main_screen.query("#brand"))
        basket_statuses = list(main_screen.query("#basket-status"))
        category_lists = list(main_screen.query("#category-list"))
        if not all((categories, details, tasks, brands, basket_statuses)):
            return
        width = event.size.width
        categories[0].display = width >= 100
        details[0].display = width >= 82
        tasks[0].styles.height = 5 if width < 82 else (6 if width < 100 else 7)
        brands[0].styles.width = 18 if width < 82 else 22
        basket_statuses[0].styles.width = 24 if width < 82 else 34
        basket_statuses[0].display = width >= 120
        self._update_storage_label()
        if width < 100 and category_lists and category_lists[0].has_focus and self.visible_packages:
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
        if not self.is_running or not list(self.query("#package-table")):
            return
        installed = self.manager.installations.read().get("installed", {})
        preferences = self.state.read()
        setup = set(preferences.get("setup_packages", []))
        conditions = {package.identifier: self.manager.installation(package.identifier) for package in self.packages}
        needle = query.casefold()
        favorites = set(preferences.get("favorites", []))
        filtered = [package for package in self.packages if
            (package.enabled or conditions[package.identifier].payload_present or package.identifier in setup)
            and
            (self.category == "All"
             or (self.category == "Installed" and conditions[package.identifier].payload_present)
             or (self.category == "Auto Setup" and package.identifier in setup)
             or (self.category == "Favorites" and package.identifier in favorites)
             or package.category == self.category)
            and (not needle or needle in f"{package.identifier} {package.name} {package.description}".casefold())]
        installed_sizes = {
            identifier: record["installed_size"]
            for identifier, record in installed.items()
            if isinstance(record, dict) and isinstance(record.get("installed_size"), int)
        }
        self.visible_packages = (
            sort_packages(
                filtered,
                self.sort_key,
                {identifier for identifier, condition in conditions.items() if condition.payload_present},
                self.update_available,
                installed_sizes,
            )
            if self.sort_key != "catalog"
            else filtered
        )
        table = self.query_one(DataTable)
        table.clear()
        for package in self.visible_packages:
            setup_marker = "[bold #a855f7]◆[/bold #a855f7]" if package.identifier in setup else " "
            favorite_marker = "[yellow]★[/yellow]" if package.identifier in favorites else " "
            selection_marker = "[bold #c4b5fd]☑[/bold #c4b5fd]" if package.selected else "[dim]☐[/dim]"
            marker = f"{setup_marker}{favorite_marker}{selection_marker}"
            condition = conditions[package.identifier]
            runtime = self.runtime_status.get(package.identifier)
            if runtime == "restoring":
                status = "[bold #c4b5fd]Restoring…[/bold #c4b5fd]"
            elif runtime == "failed":
                status = "[red]Auto-install failed[/red]"
            elif condition.status == "repairable":
                status = "[yellow]Repair needed[/yellow]"
            elif condition.healthy:
                update = self.update_info.get(package.identifier)
                if not package.enabled:
                    status = "[yellow]Installed here · unsupported[/yellow]"
                elif package.identifier in self.update_available:
                    status = "[yellow]Update available[/yellow]"
                elif update is None:
                    status = "Installed here · checking" if package.source_type == "github" else "[green]Installed here[/green]"
                elif update.status == "unknown":
                    status = (
                        "[dim]Installed here · check unavailable[/dim]"
                        if package.source_type == "github"
                        else "[green]Installed here[/green]"
                    )
                else:
                    status = "[green]Installed here[/green]"
            elif package.identifier in setup:
                if not package.enabled:
                    status = "[red]Not installed here · unavailable[/red]"
                elif not package.compatible:
                    status = "[red]Not installed here · incompatible[/red]"
                else:
                    status = "[red]Not installed here[/red]"
            else:
                status = "[red]Incompatible[/red]" if not package.compatible else "Not installed"
            table.add_row(marker, package.name, package.category, status, package.source_type, package.version, key=package.identifier)
        if preserve_identifier:
            for row, package in enumerate(self.visible_packages):
                if package.identifier == preserve_identifier:
                    table.move_cursor(row=row, animate=False)
                    break
        self._details()
        free_bytes = self._update_storage_label()
        selected = [package for package in self.packages if package.selected]
        estimate = estimate_basket(selected, free_bytes, installed)
        size_text = human_size(estimate.known_installed)
        if estimate.unknown_installed:
            size_text += f" + {estimate.unknown_installed} unknown"
        self.query_one("#basket-status", Static).update(f"{estimate.count} selected  •  {size_text}")

    def _update_storage_label(self) -> int:
        try:
            free_bytes = available_space(self.layout.root)
            free_text = f"{free_bytes / 1024**3:.1f} GiB free"
        except OSError:
            free_bytes = 0
            free_text = "storage unavailable"
        usage = (
            f"{human_size(self.post_storage_bytes)} GPM"
            if self.post_storage_bytes is not None
            else "GPM usage checking"
        )
        main_screen = self._main_screen()
        storage_nodes = list(main_screen.query("#storage")) if main_screen is not None else []
        if not self.is_running or not storage_nodes:
            return free_bytes
        if self.size.width < 120:
            label = f"{self.layout.root}\n{free_text} · {usage} · [b]x Clean & leave[/b]"
        else:
            label = f"{self.layout.root}  •  {free_text}  ·  {usage}  ·  [b]x Clean & leave[/b]"
        storage_nodes[0].update(label)
        return free_bytes

    @work(thread=True, exclusive=True, group="storage-usage")
    def _refresh_post_storage_usage(self) -> None:
        manager = self.manager
        root = self.layout.root
        try:
            total_bytes = manager.post_storage_report().total_bytes
        except (OSError, RuntimeError):
            return
        if not self.is_running:
            return
        self.call_from_thread(self._show_post_storage_usage, root, total_bytes)

    def _show_post_storage_usage(self, root: Path, total_bytes: int) -> None:
        if not self.is_running or not self.is_mounted or root != self.layout.root:
            return
        self.post_storage_bytes = total_bytes
        self._update_storage_label()

    def _current(self) -> Package | None:
        table = self.query_one(DataTable)
        if not self.visible_packages or table.cursor_row < 0 or table.cursor_row >= len(self.visible_packages):
            return None
        return self.visible_packages[table.cursor_row]

    def _details(self) -> None:
        package = self._current()
        setup_button = self.query_one("#setup-toggle-button", Button)
        if package is None:
            setup_button.label = "Add to Auto Setup"
            setup_button.disabled = True
            if self.category == "Favorites":
                message = "No favorites yet. Highlight a package and press f to keep it here."
            elif self.category == "Auto Setup":
                message = "Auto Setup is empty. Add apps here to install them automatically after changing posts."
            elif self.category == "Installed":
                message = "Nothing is installed yet. Choose All or a Starter Pack to begin."
            elif self.query_one("#search", Input).value:
                message = "No package matches this search. Try a name, category, or package ID."
            else:
                message = "No packages are available in this category."
            self.query_one("#details-body", Static).update(message)
            return
        live = self.layout.apps / package.identifier
        preferences = self.state.read()
        setup = set(preferences.get("setup_packages", []))
        in_setup = package.identifier in setup
        setup_button.label = "Remove from Auto Setup" if in_setup else "Add to Auto Setup"
        setup_button.disabled = self.busy or (not in_setup and (not package.enabled or not package.compatible))
        state = self.manager.installations.read()
        record = state.get("installed", {}).get(package.identifier, {})
        record = record if isinstance(record, dict) else {}
        condition = self.manager.installation(package.identifier)
        executable = str(condition.executable) if condition.executable else "—"
        size = record.get("installed_size")
        if not isinstance(size, int):
            size = package.installed_size
        size_text = human_size(size) if isinstance(size, int) else "unknown"
        update = self.update_info.get(package.identifier)
        if not condition.payload_present:
            update_text = "not installed"
        elif package.source_type != "github":
            update_text = "catalog-managed (automatic upstream check not available)"
        elif update is None:
            update_text = "checking"
        elif update.status == "available":
            update_text = f"{update.latest_version} available"
        elif update.status == "current":
            update_text = "current"
        else:
            update_text = f"check unavailable ({update.reason})" if update.reason else "check unavailable"
        setup_text = "not selected"
        if package.identifier in setup:
            setup_text = "startup prompt enabled" if preferences.get("setup_enabled") else "saved; startup prompt paused"
        status_text = {
            "installed": "installed here",
            "repairable": "repair needed",
            "missing": "not installed; saved in Auto Setup" if package.identifier in setup else "not installed",
        }[condition.status]
        if self.runtime_status.get(package.identifier) == "restoring":
            status_text = "restoring"
        elif self.runtime_status.get(package.identifier) == "failed":
            status_text = "auto-install failed"
        self.query_one("#details-body", Static).update(
            f"[b]{package.name}[/b]\n\n{package.description}\n\n"
            f"Category: {package.category}\nSource: {package.source_type}\nArchitecture: {', '.join(package.architectures)}\n"
            f"Stored: {live}\nExecutable: {executable}\nSize: {size_text}\n"
            f"Status: {status_text}\nAuto Setup: {setup_text}\nUpdate: {update_text}\n{package.notes}"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "setup-toggle-button":
            self.action_setup_toggle()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "package-table" and not isinstance(self.screen, ModalScreen):
            self._details()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "package-table" and not isinstance(self.screen, ModalScreen):
            self.action_primary()

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
        try:
            report = self.manager.post_storage_report()
        except (OSError, RuntimeError):
            self.exit()
            return
        if not report.has_data:
            self.exit()
            return
        self.post_storage_bytes = report.total_bytes
        self.push_screen(
            QuitPostModal(self.layout.root, report.total_bytes),
            self._quit_post_choice,
        )

    def _quit_post_choice(self, choice: str | None) -> None:
        if choice == "clean":
            self._leave_post_confirmed(True)
        elif choice == "exit":
            self.exit()

    def action_cancel_operation(self) -> None:
        if not self.busy:
            self.notify("No operation is active", severity="warning")
            return
        self.cancel_event.set()
        self.query_one("#operation", Static).update("Cancelling after the current safe step…")
        self.notify("Cancellation requested", severity="warning")

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
        identifiers = tuple(
            identifier
            for identifier in apply_starter_pack(pack, self.packages)
            if not self.manager.installed(identifier)
        )
        for package in self.packages:
            if package.identifier in identifiers:
                package.selected = True
        self._refresh(preserve_identifier=self._current().identifier if self._current() else None)
        self.notify(f"{pack.name}: added {len(identifiers)} packages to the basket")

    def action_basket(self) -> None:
        if self.busy:
            self.notify("Another operation is active", severity="warning")
            return
        selected = [package for package in self.packages if package.selected]
        packages = [package for package in selected if not self.manager.installed(package.identifier)]
        if not packages:
            message = "Selected packages are already installed" if selected else "Your basket is empty"
            self.notify(message, severity="warning")
            return
        skipped = len(selected) - len(packages)
        if skipped:
            self.notify(f"Skipped {skipped} already-installed package{'s' if skipped != 1 else ''}", severity="warning")
        installed = self.manager.installations.read().get("installed", {})
        try:
            free_space = available_space(self.layout.root)
        except OSError:
            self.notify("The install root is unavailable; run Doctor or choose another path", severity="error")
            return
        self.push_screen(
            BasketModal(packages, free_space, installed),
            lambda install: self._run_packages(packages, "install") if install else None,
        )

    def action_favorite(self) -> None:
        package = self._current()
        if package is None or self.busy:
            return
        favorites = set(self.state.read().get("favorites", []))
        favorite = package.identifier not in favorites
        self.state.set_favorite(package.identifier, favorite)
        self._refresh(self.query_one("#search", Input).value, package.identifier)
        self.notify(f"{package.name}: {'added to' if favorite else 'removed from'} favorites")

    def action_setup_toggle(self) -> None:
        package = self._current()
        if package is None or self.busy:
            return
        setup = set(self.state.read().get("setup_packages", []))
        selected = package.identifier not in setup
        if selected and (not package.enabled or not package.compatible):
            self.notify(f"{package.name} cannot be added because it is unavailable or incompatible", severity="error")
            return
        self.state.set_setup_package(package.identifier, selected)
        if not selected:
            self.runtime_status.pop(package.identifier, None)
        self._refresh(self.query_one("#search", Input).value, package.identifier)
        self.notify(f"{package.name}: {'added to' if selected else 'removed from'} Auto Setup")

    def action_setup_selected(self) -> None:
        if self.busy:
            return
        packages = [
            package for package in self.packages
            if package.selected and package.enabled and package.compatible
        ]
        if not packages:
            self.notify("Select compatible packages first", severity="warning")
            return
        self.state.set_setup_packages([package.identifier for package in packages], True)
        current = self._current()
        self._refresh(preserve_identifier=current.identifier if current else None)
        self.notify(f"Added {len(packages)} package{'s' if len(packages) != 1 else ''} to Auto Setup")

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
            cleanup = self.manager.cleanup_report()
            self.push_screen(
                DoctorModal(
                    collect_doctor_checks(self.layout.root, self.packages, self.state, self.manager.installations),
                    cleanup.total_bytes,
                    cleanup.count,
                ),
                self._doctor_closed,
            )

    def action_leave_post(self) -> None:
        if self.busy:
            self.notify("Finish or cancel the current operation first", severity="warning")
            return
        try:
            self.post_storage_bytes = self.manager.post_storage_report().total_bytes
        except (OSError, RuntimeError):
            pass
        self.push_screen(
            LeavePostModal(self.layout.root, self.post_storage_bytes),
            self._leave_post_confirmed,
        )

    def _leave_post_confirmed(self, confirmed: bool) -> None:
        if not confirmed or self.busy:
            return
        self.workers.cancel_group(self, "storage-usage")
        self.busy = True
        self.query_one("#operation", Static).update("Cleaning this post…")
        self._leave_post_worker()

    @work(thread=True, exclusive=True, group="leave-post")
    def _leave_post_worker(self) -> None:
        try:
            report = self.manager.leave_post()
            summary = (
                f"Reclaimed: {human_size(report.bytes_removed)}\n"
                f"Removed integrations: {report.integrations_removed}\n"
                f"Storage root: {'removed' if report.root_removed else 'cleaned; unrelated files were kept'}\n\n"
                "Your Auto Setup, theme, favorites, profiles, and cache were preserved."
            )
            self.call_from_thread(self._show_leave_post_complete, summary)
        except Exception as exc:
            message = error_text(exc)
            self.call_from_thread(self.query_one(RichLog).write, f"[red]Post cleanup failed: {message}[/red]")
            self.call_from_thread(self.notify, f"Post cleanup failed: {message}", severity="error")
            self.call_from_thread(self.query_one("#operation", Static).update, "Ready")
        finally:
            self.busy = False

    def _show_leave_post_complete(self, summary: str) -> None:
        self.push_screen(
            SummaryModal("This post is clean", summary),
            lambda _result: self.exit(),
        )

    def _doctor_closed(self, action: str | None) -> None:
        if action == "clean" and not self.busy:
            self.busy = True
            self.query_one("#operation", Static).update("Cleaning temporary GoinfrePM files…")
            self._cleanup_worker()

    @work(thread=True, exclusive=True, group="cleanup")
    def _cleanup_worker(self) -> None:
        try:
            report = self.manager.cleanup()
            size = human_size(report.total_bytes)
            self.call_from_thread(
                self.query_one(RichLog).write,
                f"Cleaned {report.count} temporary item{'s' if report.count != 1 else ''} ({size}).",
            )
            self.call_from_thread(self.notify, f"Reclaimed {size}")
        except Exception as exc:
            message = error_text(exc)
            self.call_from_thread(self.query_one(RichLog).write, f"[red]Cleanup failed: {message}[/red]")
            self.call_from_thread(self.notify, f"Cleanup failed: {message}", severity="error")
        finally:
            self.busy = False
            self.call_from_thread(self.query_one("#operation", Static).update, "Ready")
            self.call_from_thread(self._refresh)
            self.call_from_thread(self._refresh_post_storage_usage)

    @work(thread=True, exclusive=True, group="update-checks")
    def _check_updates(self) -> None:
        installed = self.manager.installations.read().get("installed", {})
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
        package = self._current()
        if self.query_one(DataTable).has_focus and package and self.manager.installed(package.identifier) and not self.busy:
            self._open_installed_actions(package)
        else:
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
            self.post_storage_bytes = None
            self._refresh()
            self._refresh_post_storage_usage()
            self.notify(f"Install root changed to {root}")

    def action_install_one(self) -> None:
        package = self._current()
        if package and self.manager.installed(package.identifier):
            self._open_installed_actions(package)
        elif package:
            self._run_packages([package], "install")

    def _open_installed_actions(self, package: Package) -> None:
        condition = self.manager.installation(package.identifier)
        self.push_screen(
            PackageActionModal(
                package,
                condition.status == "repairable",
                package.desktop and condition.executable is not None,
            ),
            lambda action: self._installed_action(package, action),
        )

    def _installed_action(self, package: Package, action: str | None) -> None:
        if action == "launch":
            try:
                self.manager.launch(package.identifier)
                self.query_one(RichLog).write(f"Launched {package.name}.")
                self.notify(f"Launched {package.name}")
            except Exception as exc:
                message = error_text(exc)
                self.query_one(RichLog).write(f"[red]Could not launch {package.name}: {message}[/red]")
                self.notify(f"Could not launch {package.name}: {message}", severity="error")
        elif action in {"update", "reinstall", "repair"}:
            self._run_packages([package], action)

    def action_install_selected(self) -> None:
        self.action_basket()

    def action_remove_one(self) -> None:
        package = self._current()
        if package and not self.busy:
            if package.identifier in set(self.state.read().get("setup_packages", [])):
                self.push_screen(
                    RemovalChoiceModal(1),
                    lambda choice: self._remove_choice([package], choice),
                )
            else:
                self.push_screen(
                    ConfirmModal(
                        "Remove application?",
                        f"Remove {package.name} application files and integrations? User configuration will remain.",
                    ),
                    lambda ok: self._run_packages([package], "remove") if ok else None,
                )

    def action_remove_selected(self) -> None:
        packages = [package for package in self.packages if package.selected and self.manager.installed(package.identifier)]
        if packages and not self.busy:
            setup = set(self.state.read().get("setup_packages", []))
            if any(package.identifier in setup for package in packages):
                self.push_screen(RemovalChoiceModal(len(packages)), lambda choice: self._remove_choice(packages, choice))
            else:
                self.push_screen(
                    ConfirmModal(
                        "Remove selected applications?",
                        f"Remove {len(packages)} selected applications? User configuration will remain.",
                    ),
                    lambda ok: self._run_packages(packages, "remove") if ok else None,
                )
        elif not self.busy:
            self.notify("No installed packages selected", severity="warning")

    def _remove_choice(self, packages: list[Package], choice: str | None) -> None:
        if choice in {"forget", "keep"}:
            self._run_packages(packages, "remove", keep_setup=choice == "keep")

    def _run_packages(self, packages: list[Package], operation: str, keep_setup: bool = False) -> None:
        if operation == "install":
            packages = [package for package in packages if not self.manager.installed(package.identifier)]
        if self.busy:
            self.notify("Another operation is active", severity="warning")
        elif not packages:
            self.notify("No packages selected", severity="warning")
        else:
            self.busy = True
            self.cancel_event = threading.Event()
            self._worker(packages, operation, keep_setup)

    @work(thread=True, exclusive=True)
    def _worker(self, packages: list[Package], operation: str, keep_setup: bool = False) -> None:
        started = time.monotonic()
        succeeded: list[Package] = []
        failed: list[tuple[Package, str]] = []
        skipped: list[tuple[Package, str]] = []
        verbs = {"install": "Installing", "update": "Updating", "reinstall": "Reinstalling", "repair": "Repairing", "remove": "Removing"}
        try:
            for package in packages:
                if self.cancel_event.is_set():
                    skipped.append((package, "cancelled"))
                    continue
                try:
                    self.call_from_thread(self.query_one(ProgressBar).update, progress=0)
                    self.call_from_thread(self.query_one("#operation", Static).update, f"{verbs[operation]} {package.name}")
                    if operation == "remove":
                        events = self.manager.remove(package.identifier, keep_setup=keep_setup)
                    elif operation == "repair":
                        self.manager.repair(package.identifier)
                        events = iter((("log", f"Repaired {package.name}"),))
                    else:
                        method = {"install": self.manager.install, "update": self.manager.update, "reinstall": self.manager.reinstall}[operation]
                        events = method(
                            package.identifier,
                            cancel=self.cancel_event,
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
                    completed = {"install": "installed", "update": "updated", "reinstall": "reinstalled", "repair": "repaired", "remove": "removed"}[operation]
                    self.call_from_thread(self.notify, f"{package.name}: {completed}")
                    self.runtime_status.pop(package.identifier, None)
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
            self.call_from_thread(self._show_summary, succeeded, failed, operation, elapsed, skipped)

    @work(thread=True, exclusive=True)
    def _restore_worker(self, packages: list[Package]) -> None:
        started = time.monotonic()
        by_identifier = {package.identifier: package for package in packages}
        succeeded: list[Package] = []
        ready: list[Package] = []
        failed: list[tuple[Package, str]] = []
        skipped: list[tuple[Package, str]] = []
        current: list[Package | None] = [None]
        retry_needed = False
        try:
            events = self.manager.restore(
                self.cancel_event,
                list(by_identifier),
                progress_callback=lambda value: self.call_from_thread(
                    self.query_one(ProgressBar).update, progress=value
                ),
                transfer_callback=lambda done, total, speed, eta: self.call_from_thread(
                    self._show_transfer, current[0], done, total, speed, eta
                ) if current[0] is not None else None,
            )
            for kind, value in events:
                if kind == "waiting":
                    self.call_from_thread(self.query_one("#operation", Static).update, str(value))
                    self.call_from_thread(self.query_one(RichLog).write, str(value))
                elif kind == "wait_progress":
                    self.call_from_thread(self.query_one("#operation", Static).update, str(value))
                elif kind == "peer_progress":
                    status = value if isinstance(value, dict) else {}
                    identifier = str(status.get("package", ""))
                    package = by_identifier.get(identifier)
                    if package is not None:
                        changed_package = current[0] != package
                        current[0] = package
                        self.runtime_status[package.identifier] = "restoring"
                        if changed_package:
                            self.call_from_thread(self._refresh, preserve_identifier=package.identifier)
                    try:
                        progress = float(status.get("progress", 0))
                    except (TypeError, ValueError):
                        progress = 0
                    self.call_from_thread(
                        self.query_one(ProgressBar).update,
                        progress=max(0.0, min(100.0, progress)),
                    )
                    name = str(status.get("package_name", "")) or "Auto Setup"
                    phase = str(status.get("phase", "preparing")).replace("_", " ").title()
                    index = status.get("index")
                    total = status.get("total")
                    position = f" {index}/{total}" if index and total else ""
                    self.call_from_thread(
                        self.query_one("#operation", Static).update,
                        f"{phase}{position}: {name} · {progress:.0f}%",
                    )
                elif kind == "package":
                    identifier, index, total = value  # type: ignore[misc]
                    package = by_identifier.get(str(identifier))
                    if package:
                        current[0] = package
                        self.runtime_status[package.identifier] = "restoring"
                        self.call_from_thread(
                            self.query_one("#operation", Static).update,
                            f"Restoring Auto Setup — package {index} of {total}: {package.name}",
                        )
                        self.call_from_thread(self.query_one(ProgressBar).update, progress=0)
                        self.call_from_thread(self._refresh, preserve_identifier=package.identifier)
                elif kind == "log":
                    self.call_from_thread(self.query_one(RichLog).write, str(value))
                elif kind == "progress":
                    self.call_from_thread(self.query_one(ProgressBar).update, progress=float(value))
                elif kind == "restored":
                    package = by_identifier.get(str(value))
                    if package:
                        self.runtime_status.pop(package.identifier, None)
                        succeeded.append(package)
                elif kind == "ready":
                    package = by_identifier.get(str(value))
                    if package:
                        self.runtime_status.pop(package.identifier, None)
                        ready.append(package)
                elif kind in {"failed", "skipped"}:
                    identifier, reason = value  # type: ignore[misc]
                    package = by_identifier.get(str(identifier))
                    if package:
                        if kind == "failed":
                            self.runtime_status[package.identifier] = "failed"
                            failed.append((package, str(reason)))
                            self.call_from_thread(
                                self.query_one(RichLog).write,
                                f"[red]{package.name}: {reason}[/red]",
                            )
                        else:
                            self.runtime_status.pop(package.identifier, None)
                            skipped.append((package, str(reason)))
                elif kind == "cancelled":
                    package = by_identifier.get(str(value))
                    if package:
                        self.runtime_status.pop(package.identifier, None)
                        skipped.append((package, "cancelled"))
        except OperationBusyError:
            retry_needed = True
            self.call_from_thread(
                self.query_one(RichLog).write,
                "Auto Setup is still preparing. Choose Retry when the current task finishes.",
            )
        except Exception as exc:
            message = error_text(exc)
            for package in packages:
                if package not in succeeded and not any(item == package for item, _reason in failed):
                    self.runtime_status[package.identifier] = "failed"
                    failed.append((package, message))
            self.call_from_thread(self.query_one(RichLog).write, f"[red]Auto Setup restore: {message}[/red]")
        finally:
            self.busy = False
            self.call_from_thread(self.query_one("#operation", Static).update, "Ready")
            self.call_from_thread(self._refresh)
            elapsed = time.monotonic() - started
            if retry_needed:
                self.call_from_thread(self._show_auto_setup_retry, packages)
            else:
                self.call_from_thread(
                    self._show_summary,
                    succeeded,
                    failed,
                    "restore",
                    elapsed,
                    skipped,
                    ready,
                )

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
        operation: str,
        elapsed: float,
        skipped: list[tuple[Package, str]] | None = None,
        ready: list[Package] | None = None,
    ) -> None:
        skipped = skipped or []
        ready = ready or []
        for package in [*succeeded, *ready]:
            package.selected = False
        action = {
            "install": "Installed",
            "update": "Updated",
            "reinstall": "Reinstalled",
            "repair": "Repaired",
            "remove": "Removed",
            "restore": "Restored",
        }[operation]
        if operation == "restore":
            lines = [
                f"Ready: {len(succeeded) + len(ready)}   Failed: {len(failed)}   "
                f"Skipped: {len(skipped)}   Time: {elapsed:.1f}s",
                f"Location: {self.layout.apps}",
            ]
        else:
            lines = [
                f"{action}: {len(succeeded)}   Failed: {len(failed)}   "
                f"Skipped: {len(skipped)}   Time: {elapsed:.1f}s",
                f"Location: {self.layout.apps}",
            ]
        if succeeded:
            heading = "Installed or repaired:" if operation == "restore" else "Completed:"
            lines.extend(("", heading, *(f"  • {package.name}" for package in succeeded)))
        if ready:
            lines.extend(
                (
                    "",
                    "Completed by another GoinfrePM session:",
                    *(f"  • {package.name}" for package in ready),
                )
            )
        if failed:
            lines.extend(("", "Needs attention:", *(f"  • {package.name}: {message}" for package, message in failed)))
        if skipped:
            lines.extend(("", "Skipped:", *(f"  • {package.name}: {message}" for package, message in skipped)))
        if operation in {"install", "update", "reinstall", "restore"}:
            lines.extend(("", "Before leaving this shared post, press x to clean its GoinfrePM storage."))
        self._refresh()
        self._refresh_post_storage_usage()
        title = (
            "Auto Setup is ready"
            if operation == "restore" and not failed
            else "Auto Setup finished"
            if operation == "restore"
            else "Operation complete"
        )
        self.push_screen(SummaryModal(title, "\n".join(lines)))

    def _handle_exception(self, error: Exception) -> None:
        """Replace Textual's terminal traceback with a concise recoverable report."""
        crash_log = write_crash_log(error)
        self._return_code = 1
        if self._exception is None:
            self._exception = error
            self._exception_event.set()
        suffix = f" Details saved to {crash_log}." if crash_log else ""
        self.panic(f"{DISPLAY_NAME} encountered an unexpected error: {error_text(error)}.{suffix}")


def run_tui(auto_restore: bool = True) -> None:
    GoinfrePMApp(auto_restore=auto_restore).run()
