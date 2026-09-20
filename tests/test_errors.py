from pathlib import Path

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
    calls: list[bool] = []
    monkeypatch.setattr(cli, "resolve_install_root", lambda: tmp_path / "goinfre-pm")
    monkeypatch.setattr(app_module, "run_tui", lambda auto_restore=True: calls.append(auto_restore))

    assert cli.main(["--no-restore"]) == 0
    assert calls == [False]
    assert cli.main(["version"]) == 0
    assert calls == [False]


def test_autostart_requires_a_real_persistent_command(monkeypatch, tmp_path, capsys) -> None:
    state = StateStore(tmp_path / "state.json")
    monkeypatch.setattr(cli, "StateStore", lambda: state)
    monkeypatch.setattr(cli, "AUTOSTART_DIR", tmp_path / "autostart")
    monkeypatch.setattr(cli, "USER_BIN", tmp_path / "bin")
    ephemeral = tmp_path / "npm-cache" / "gpm"
    ephemeral.parent.mkdir()
    ephemeral.write_text("#!/bin/sh\n", encoding="utf-8")
    ephemeral.chmod(0o755)
    monkeypatch.setenv("PATH", str(ephemeral.parent))

    assert cli.main(["autostart", "enable"]) == 1
    assert "--install-manager" in capsys.readouterr().err
    assert state.read()["autostart"] is False
    assert not (tmp_path / "autostart").exists()


def test_autostart_accepts_explicit_local_manager(monkeypatch, tmp_path) -> None:
    state = StateStore(tmp_path / "state.json")
    launcher = tmp_path / "bin" / "gpm"
    launcher.parent.mkdir()
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)
    monkeypatch.setattr(cli, "StateStore", lambda: state)
    monkeypatch.setattr(cli, "AUTOSTART_DIR", tmp_path / "autostart")
    monkeypatch.setattr(cli, "USER_BIN", launcher.parent)

    assert cli.main(["autostart", "enable"]) == 0
    desktop = tmp_path / "autostart" / "goinfre-pm-restore.desktop"
    assert f'Exec="{launcher}" restore' in desktop.read_text(encoding="utf-8")
    assert state.read()["autostart"] is True


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
