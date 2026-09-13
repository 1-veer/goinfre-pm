from pathlib import Path


def test_launcher_uses_persistent_local_runtime() -> None:
    installer = (Path(__file__).parents[1] / "install.sh").read_text(encoding="utf-8")
    assert "MANAGER_HOME=$HOME/.local/share/$PROJECT_SLUG" in installer
    assert 'exec \'$MANAGER_VENV/bin/python\'' in installer
    assert "GPM_PACKAGES_FILE='$MANAGER_RUNTIME/packages.toml'" in installer
    assert 'exec \'$GPM_ROOT/venv/bin/python\'' not in installer
