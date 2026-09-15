from pathlib import Path


def test_launcher_uses_persistent_local_runtime() -> None:
    installer = (Path(__file__).parents[1] / "install.sh").read_text(encoding="utf-8")
    assert "MANAGER_HOME=$HOME/.local/share/$PROJECT_SLUG" in installer
    assert 'manager_home=\\$HOME/.local/share/$PROJECT_SLUG' in installer
    assert 'exec \\"\\$manager_home/venv/bin/python\\"' in installer
    assert "GPM_PACKAGES_FILE=$manager_home/runtime/packages.toml" in installer
    assert 'exec \'$GPM_ROOT/venv/bin/python\'' not in installer


def test_installer_repairs_ubuntu_venv_without_sudo() -> None:
    installer = (Path(__file__).parents[1] / "install.sh").read_text(encoding="utf-8")

    assert "python3 -m venv --without-pip" in installer
    assert "PIP_BOOTSTRAP_SHA256=" in installer
    assert "PYTHONPATH=$PIP_BOOTSTRAP_WHEEL" in installer
    assert '"$MANAGER_VENV/bin/python" -m pip --version' in installer
    assert "require_command curl" not in installer
    assert "require_command dpkg-deb" not in installer
    assert "sudo apt" not in installer


def test_manager_update_preserves_root_local_installation_manifest() -> None:
    installer = (Path(__file__).parents[1] / "install.sh").read_text(encoding="utf-8")

    assert 'mkdir -p "$GPM_ROOT/apps" "$GPM_ROOT/downloads" "$GPM_ROOT/runtime" "$GPM_ROOT/logs"' in installer
    assert 'rm -rf "$GPM_ROOT/venv" "$GPM_ROOT/runtime"' not in installer
    assert 'rm -f "$GPM_ROOT/runtime/packages.toml" "$GPM_ROOT/runtime/project.conf"' in installer
    assert 'Refusing symlinked storage directory' in installer
