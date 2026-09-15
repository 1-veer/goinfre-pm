from pathlib import Path

from goinfre_pm import cli, doctor
from goinfre_pm.doctor import DoctorCheck, collect_doctor_checks
from goinfre_pm.models import InstalledPackage, Package
from goinfre_pm.storage import Layout, LocalStateStore, StateStore


def test_doctor_reports_broken_integrations_with_actions(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "goinfre-pm"
    root.mkdir()
    user_bin = tmp_path / "home" / ".local" / "bin"
    desktop_dir = tmp_path / "home" / ".local" / "share" / "applications"
    monkeypatch.setattr(doctor, "USER_BIN", user_bin)
    monkeypatch.setattr(doctor, "DESKTOP_DIR", desktop_dir)
    monkeypatch.setenv("PATH", str(user_bin))
    package = Package(
        "tool", "Tool", "fixture", "Developer Tools", "https://example.invalid/tool.tar.gz",
        source_type="tar", architectures=("any",), executable_candidates=("tool",),
    )
    state = StateStore(tmp_path / "state.json")
    installations = LocalStateStore(Layout.at(root))
    installations.set_installed(
        InstalledPackage("tool", "1", package.url, str(root / "apps" / "tool"), [], "now"),
    )

    checks = collect_doctor_checks(root, [package], state, installations)
    by_name = {check.name: check for check in checks}

    assert by_name["Command PATH"].status == "ok"
    assert by_name["Executables"].status == "error"
    assert "gpm repair" in by_name["Executables"].action
    assert by_name["Command links"].status == "error"
    assert by_name["Desktop launchers"].status == "warning"


def test_cli_doctor_keeps_machine_readable_output(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "collect_doctor_checks",
        lambda: [
            DoctorCheck("ok", "Storage", "Writable"),
            DoctorCheck("warning", "Space", "Low", "Remove unused apps."),
        ],
    )

    assert cli.doctor() == 0
    output = capsys.readouterr().out
    assert "[OK] Storage: Writable" in output
    assert "[WARN] Space: Low" in output
    assert "Remove unused apps." in output
