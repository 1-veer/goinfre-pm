from __future__ import annotations

import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_npm_package_contains_complete_local_installer() -> None:
    metadata = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert metadata["name"] == "goinfre-pm"
    assert metadata["bin"]["goinfre-pm"] == "npm/bin/goinfre-pm.js"
    assert metadata["bin"]["gpm"] == "npm/bin/goinfre-pm.js"
    for required in (
        "npm/",
        "src/goinfre_pm/*.py",
        "src/goinfre_pm/*.tcss",
        "src/goinfre_pm/project.conf",
        "install.sh",
        "packages.toml",
        "pyproject.toml",
        "requirements.txt",
        "docs/tui-screenshot-placeholder.svg",
    ):
        assert required in metadata["files"]


def test_release_versions_stay_synchronized() -> None:
    metadata = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    branding = (ROOT / "src/goinfre_pm/project.conf").read_text(encoding="utf-8")

    assert f'version = "{metadata["version"]}"' in pyproject
    assert f"PROJECT_VERSION='{metadata['version']}'" in branding
    assert metadata["author"] == "veer"
    assert 'authors = [{ name = "veer" }]' in pyproject
    assert "PROJECT_AUTHOR='veer'" in branding
    assert "PROJECT_SIGNATURE='Made by VEER'" in branding


def test_npm_bootstrap_uses_argument_arrays_and_never_invokes_sudo() -> None:
    bootstrap = (ROOT / "npm/bin/goinfre-pm.js").read_text(encoding="utf-8")

    assert 'run("sh", [installer]' in bootstrap
    assert 'shell: false' in bootstrap
    assert 'shell: true' not in bootstrap
    assert re.search(r'(?:run|spawnSync)\(\s*["\']sudo["\']', bootstrap) is None
    assert 'run("sh", [installer, "uninstall"]' in bootstrap
    assert 'run("sh", [installer, "run", ...args]' in bootstrap
    assert 'const installManager = installIndex !== -1 || forceIndex !== -1' in bootstrap
    assert '"--install-manager"' in bootstrap
    assert "sudo apt" not in bootstrap
    assert 'works("curl"' not in bootstrap
    assert 'works("dpkg-deb"' not in bootstrap
    assert 'finish(run(launcher, args' in bootstrap
    assert 'args.includes("--no-restore")' not in bootstrap
