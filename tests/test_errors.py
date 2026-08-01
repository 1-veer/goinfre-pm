from pathlib import Path

from goinfre_pm import cli
from goinfre_pm.errors import error_text, write_crash_log


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
