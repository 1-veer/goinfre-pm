from pathlib import Path
from types import SimpleNamespace

from goinfre_pm import app as app_module, cli
from goinfre_pm.errors import error_text, write_crash_log
from goinfre_pm.models import Package
from goinfre_pm.storage import StateStore


def test_error_text_handles_empty_exception() -> None:
    assert error_text(RuntimeError()) == "RuntimeError"


def test_crash_log_contains_diagnostic(tmp_path: Path) -> None:
    log = tmp_path / "crash.log"

    try:
        raise LookupError("fixture failure")
    except LookupError as error:
        assert write_crash_log(error, log) == log

    content = log.read_text(encoding="utf-8")
    assert "LookupError: fixture failure" in content


def test_cli_contains_unexpected_errors(monkeypatch, capsys, tmp_path: Path) -> None:
    log = tmp_path / "crash.log"
    monkeypatch.setattr(cli, "_print_packages", lambda: (_ for _ in ()).throw(KeyError("broken fixture")))
    monkeypatch.setattr(cli, "write_crash_log", lambda _error: log)

    assert cli.main(["list"]) == 1
    error = capsys.readouterr().err
    assert "unexpected error" in error
    assert "broken fixture" in error
    assert "Traceback" not in error


def test_cli_handles_keyboard_interrupt(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "_print_packages", lambda: (_ for _ in ()).throw(KeyboardInterrupt()))

    assert cli.main(["list"]) == 130
    assert "cancelled" in capsys.readouterr().err


def test_cli_keeps_install_and_update_as_distinct_operations(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    class Manager:
        def install(self, identifier, **_kwargs):
            calls.append(("install", identifier))
            return iter(())

        def update(self, identifier, **_kwargs):
            calls.append(("update", identifier))
            return iter(())

        def reinstall(self, identifier, **_kwargs):
            calls.append(("reinstall", identifier))
            return iter(())

    manager = Manager()
    monkeypatch.setattr(cli, "_manager", lambda: manager)

    assert cli.main(["install", "tool"]) == 0
    assert cli.main(["update", "tool"]) == 0
    assert cli.main(["reinstall", "tool"]) == 0
    assert calls == [("install", "tool"), ("update", "tool"), ("reinstall", "tool")]


def test_no_restore_is_forwarded_only_to_interactive_tui(monkeypatch, tmp_path) -> None:
    calls: list[tuple[bool, bool]] = []
    state = StateStore(tmp_path / "state.json")
    monkeypatch.setattr(cli, "resolve_install_root", lambda: tmp_path / "goinfre-pm")
    monkeypatch.setattr(cli, "StateStore", lambda: state)
    monkeypatch.setattr(cli, "AUTOSTART_DIR", tmp_path / "autostart")
    monkeypatch.setattr(
        app_module,
        "run_tui",
        lambda auto_restore=True, retired_autostart=False: calls.append((auto_restore, retired_autostart)),
    )

    assert cli.main(["--no-restore"]) == 0
    assert calls == [(False, False)]
    assert cli.main(["version"]) == 0
    assert calls == [(False, False)]


def test_background_autostart_enable_is_retired(monkeypatch, tmp_path, capsys) -> None:
    state = StateStore(tmp_path / "state.json")
    monkeypatch.setattr(cli, "StateStore", lambda: state)
    monkeypatch.setattr(cli, "AUTOSTART_DIR", tmp_path / "autostart")

    assert cli.main(["autostart", "enable"]) == 1
    assert "background login restore was retired" in capsys.readouterr().err.casefold()
    assert state.read()["autostart"] is False
    assert not (tmp_path / "autostart").exists()


def test_interactive_startup_removes_legacy_autostart(monkeypatch, tmp_path) -> None:
    state = StateStore(tmp_path / "state.json")
    data = state.read()
    data["autostart"] = True
    state.write(data)
    autostart = tmp_path / "autostart" / "goinfre-pm-restore.desktop"
    autostart.parent.mkdir()
    autostart.write_text("legacy", encoding="utf-8")
    monkeypatch.setattr(cli, "StateStore", lambda: state)
    monkeypatch.setattr(cli, "AUTOSTART_DIR", autostart.parent)

    assert cli.retire_background_autostart() is True
    assert not autostart.exists()
    assert state.read()["autostart"] is False


def test_leave_cli_requires_confirmation_and_supports_explicit_yes(monkeypatch, tmp_path, capsys) -> None:
    calls: list[str] = []

    class Manager:
        layout = SimpleNamespace(root=tmp_path / "goinfre-pm")

        def leave_post(self):
            calls.append("leave")
            return SimpleNamespace(bytes_removed=4096, integrations_removed=2, root_removed=True)

    monkeypatch.setattr(cli, "_manager", Manager)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)

    assert cli.main(["leave"]) == 1
    assert calls == []
    assert "needs confirmation" in capsys.readouterr().err
    assert cli.main(["leave", "--yes"]) == 0
    assert calls == ["leave"]
    assert "Post cleaned" in capsys.readouterr().out


def test_restore_cli_aliases_dispatch_and_report_partial_failure(monkeypatch, tmp_path, capsys) -> None:
    calls: list[str] = []

    class Manager:
        def restore(self):
            calls.append("restore")
            return iter((("failed", ("tool", "offline")),))

    monkeypatch.setattr(cli, "_manager", Manager)
    monkeypatch.setattr(cli, "StateStore", lambda: StateStore(tmp_path / "state.json"))

    assert cli.main(["restore"]) == 1
    assert cli.main(["setup", "restore"]) == 1
    assert calls == ["restore", "restore"]
    assert capsys.readouterr().err.count("Failed tool: offline") == 2


def test_setup_cli_add_remove_enable_disable_are_persistent(monkeypatch, tmp_path, capsys) -> None:
    state = StateStore(tmp_path / "state.json")
    tool = Package(
        "tool",
        "Tool",
        "fixture",
        "Developer Tools",
        "https://example.invalid/tool.tar.gz",
        source_type="tar",
        architectures=("any",),
    )
    monkeypatch.setattr(cli, "StateStore", lambda: state)
    monkeypatch.setattr(cli, "load_packages", lambda: [tool])

    assert cli.main(["setup", "add", "tool", "unknown"]) == 1
    assert state.read()["setup_packages"] == []
    assert cli.main(["setup", "add", "tool"]) == 0
    assert state.read()["setup_packages"] == ["tool"]
    assert state.read()["setup_enabled"] is True
    assert cli.main(["setup", "disable"]) == 0
    assert state.read()["setup_enabled"] is False
    assert cli.main(["setup", "enable"]) == 0
    assert state.read()["setup_enabled"] is True
    assert cli.main(["setup", "remove", "tool"]) == 0
    assert state.read()["setup_packages"] == []
    state.set_setup_package("retired-tool", True)
    assert cli.main(["setup", "remove", "retired-tool"]) == 0
    assert state.read()["setup_packages"] == []
    assert "Auto Setup" in capsys.readouterr().out
