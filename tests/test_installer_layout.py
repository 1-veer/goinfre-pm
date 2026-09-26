from pathlib import Path
import hashlib
import json
import os
import subprocess


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


def test_first_launch_has_a_visible_activity_indicator() -> None:
    installer = (Path(__file__).parents[1] / "install.sh").read_text(encoding="utf-8")

    assert "activity_start()" in installer
    assert "still working, please wait" in installer
    assert "Starting the interface — please wait" in installer
    assert '[ -t 1 ] || return 0' in installer


def test_installer_removes_only_the_retired_manager_autostart_entry() -> None:
    installer = (Path(__file__).parents[1] / "install.sh").read_text(encoding="utf-8")

    assert 'LEGACY_AUTOSTART=$HOME/.config/autostart/$PROJECT_SLUG-restore.desktop' in installer
    assert 'rm -f "$LEGACY_AUTOSTART"' in installer


def test_manager_update_preserves_root_local_installation_manifest() -> None:
    installer = (Path(__file__).parents[1] / "install.sh").read_text(encoding="utf-8")

    assert 'mkdir -p "$GPM_ROOT/apps" "$GPM_ROOT/downloads" "$GPM_ROOT/runtime" "$GPM_ROOT/logs"' in installer
    assert 'rm -rf "$GPM_ROOT/venv" "$GPM_ROOT/runtime"' not in installer
    assert 'rm -f "$GPM_ROOT/runtime/packages.toml" "$GPM_ROOT/runtime/project.conf"' in installer
    assert 'Refusing symlinked storage directory' in installer


def _fake_venv_python(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '#!/bin/sh\n'
        'if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then\n'
        '  if [ -n "${GPM_TEST_PIP_NOISE:-}" ]; then\n'
        '    printf "Collecting noisy-package\\nSuccessfully installed noisy-package\\n"\n'
        '  fi\n'
        '  exit 0\n'
        'fi\n'
        'if [ "$1" = "-c" ]; then exit 0; fi\n'
        'if [ -n "${GPM_TEST_RUN_LOG:-}" ]; then\n'
        '  printf "%s\\n" "$@" > "$GPM_TEST_RUN_LOG"\n'
        '  printf "packages=%s\\n" "$GPM_PACKAGES_FILE" >> "$GPM_TEST_RUN_LOG"\n'
        '  printf "pythonpath=%s\\n" "$PYTHONPATH" >> "$GPM_TEST_RUN_LOG"\n'
        'fi\n',
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_npx_run_mode_keeps_python_in_goinfre_without_creating_gpm(tmp_path: Path) -> None:
    project = Path(__file__).parents[1]
    home = tmp_path / "home"
    home.mkdir()
    (home / ".zshrc").write_text("# user's existing shell settings\n", encoding="utf-8")
    root = tmp_path / "goinfre with spaces" / "goinfre-pm"
    python = root / "venv" / "bin" / "python"
    _fake_venv_python(python)
    digest = hashlib.sha256((project / "requirements.txt").read_bytes()).hexdigest()
    (root / "venv" / ".gpm-requirements.sha256").write_text(digest + "\n", encoding="ascii")
    log = tmp_path / "run.log"
    env = {**os.environ, "HOME": str(home), "GPM_INSTALL_ROOT": "goinfre with spaces/goinfre-pm", "GPM_TEST_RUN_LOG": str(log)}

    result = subprocess.run(["sh", str(project / "install.sh"), "run", "list"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)

    assert result.returncode == 0, result.stderr
    assert (root / "venv").is_dir()
    assert not (home / ".local" / "bin" / "gpm").exists()
    assert not (home / ".local" / "share" / "goinfre-pm").exists()
    assert (home / ".zshrc").read_text(encoding="utf-8") == "# user's existing shell settings\n"
    assert not (home / ".bashrc").exists()
    assert not (home / ".config" / "fish" / "config.fish").exists()
    assert json.loads((home / ".config" / "goinfre-pm" / "config.json").read_text())["install_root"] == str(root)
    assert log.read_text().splitlines()[:3] == ["-m", "goinfre_pm", "list"]
    assert f"packages={project / 'packages.toml'}" in log.read_text()
    assert f"pythonpath={project / 'src'}" in log.read_text()


def test_npx_run_mode_keeps_dependency_noise_in_bootstrap_log(tmp_path: Path) -> None:
    project = Path(__file__).parents[1]
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "goinfre-pm"
    _fake_venv_python(root / "venv" / "bin" / "python")
    env = {
        **os.environ,
        "HOME": str(home),
        "GPM_INSTALL_ROOT": str(root),
        "GPM_TEST_PIP_NOISE": "1",
    }

    result = subprocess.run(
        ["sh", str(project / "install.sh"), "run", "version"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "Collecting noisy-package" not in result.stdout
    assert "Successfully installed noisy-package" not in result.stdout
    assert "Collecting noisy-package" not in result.stderr
    assert "Successfully installed noisy-package" not in result.stderr
    assert "Installing required components (first launch only)" in result.stdout
    assert "Installed: private Python environment and GoinfrePM interface" in result.stdout
    assert "Made by VEER" in result.stdout
    assert f"Full setup details: {root / 'logs' / 'bootstrap.log'}" in result.stdout
    details = (root / "logs" / "bootstrap.log").read_text(encoding="utf-8")
    assert "Collecting noisy-package" in details
    assert "Successfully installed noisy-package" in details


def test_explicit_installer_still_creates_persistent_gpm(tmp_path: Path) -> None:
    project = Path(__file__).parents[1]
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "goinfre with spaces" / "goinfre-pm"
    _fake_venv_python(home / ".local" / "share" / "goinfre-pm" / "venv" / "bin" / "python")
    env = {**os.environ, "HOME": str(home), "GPM_INSTALL_ROOT": str(root)}

    result = subprocess.run(["sh", str(project / "install.sh")], env=env, capture_output=True, text=True, timeout=30)

    assert result.returncode == 0, result.stderr
    assert (home / ".local" / "bin" / "gpm").is_file()
    assert (home / ".local" / "share" / "goinfre-pm" / "venv").is_dir()
    assert "# Goinfre package manager PATH" in (home / ".zshrc").read_text()


def test_run_mode_never_interprets_forwarded_uninstall_as_manager_removal(tmp_path: Path) -> None:
    project = Path(__file__).parents[1]
    home = tmp_path / "home"
    home.mkdir()
    old_launcher = home / ".local" / "bin" / "gpm"
    old_launcher.parent.mkdir(parents=True)
    old_launcher.write_text("keep", encoding="utf-8")
    old_runtime = home / ".local" / "share" / "goinfre-pm" / "keep"
    old_runtime.parent.mkdir(parents=True)
    old_runtime.write_text("keep", encoding="utf-8")
    root = tmp_path / "goinfre-pm"
    _fake_venv_python(root / "venv" / "bin" / "python")
    digest = hashlib.sha256((project / "requirements.txt").read_bytes()).hexdigest()
    (root / "venv" / ".gpm-requirements.sha256").write_text(digest + "\n", encoding="ascii")
    env = {**os.environ, "HOME": str(home), "GPM_INSTALL_ROOT": str(root)}

    result = subprocess.run(["sh", str(project / "install.sh"), "run", "uninstall"], env=env, capture_output=True, text=True, timeout=30)

    assert result.returncode == 0, result.stderr
    assert old_launcher.read_text(encoding="utf-8") == "keep"
    assert old_runtime.read_text(encoding="utf-8") == "keep"


def test_uninstall_typo_never_purges_manager_or_application_data(tmp_path: Path) -> None:
    project = Path(__file__).parents[1]
    home = tmp_path / "home"
    launcher = home / ".local" / "bin" / "gpm"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("keep", encoding="utf-8")
    runtime = home / ".local" / "share" / "goinfre-pm" / "keep"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("keep", encoding="utf-8")
    root = tmp_path / "goinfre-pm"
    payload = root / "apps" / "tool" / "keep"
    payload.parent.mkdir(parents=True)
    payload.write_text("keep", encoding="utf-8")
    env = {**os.environ, "HOME": str(home), "GPM_INSTALL_ROOT": str(root)}

    result = subprocess.run(
        ["sh", str(project / "install.sh"), "uninstall", "--purge-dtaa"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "Usage:" in result.stderr
    assert launcher.read_text(encoding="utf-8") == "keep"
    assert runtime.read_text(encoding="utf-8") == "keep"
    assert payload.read_text(encoding="utf-8") == "keep"
