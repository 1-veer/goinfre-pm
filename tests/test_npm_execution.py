from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


def run_bootstrap(*args: str) -> dict[str, object]:
    if NODE is None:
        pytest.skip("Node.js is not available")
    result = subprocess.run(
        [NODE, str(ROOT / "tests" / "npm_bootstrap_harness.js"), str(ROOT), json.dumps(args)],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return json.loads(result.stdout)


def test_npx_default_runs_without_invoking_persistent_installer_or_gpm() -> None:
    result = run_bootstrap("list")
    calls = result["calls"]
    assert result["exitCode"] == 0
    assert [call["command"] for call in calls] == ["python3", "sh"]
    assert calls[1]["args"][-2:] == ["run", "list"]


def test_uninstall_word_is_forwarded_as_a_normal_command() -> None:
    result = run_bootstrap("uninstall")
    calls = result["calls"]
    assert [call["command"] for call in calls] == ["python3", "sh"]
    assert calls[1]["args"][-2:] == ["run", "uninstall"]


def test_permanent_manager_requires_explicit_flag() -> None:
    result = run_bootstrap("--install-manager", "list")
    calls = result["calls"]
    assert result["exitCode"] == 0
    assert [call["command"] for call in calls] == ["python3", "/test-only-home/.local/bin/gpm", "sh", "/test-only-home/.local/bin/gpm"]
    assert Path(calls[2]["args"][-1]).name == "install.sh"
    assert calls[3]["args"] == ["list"]


def test_uninstall_manager_remains_explicit() -> None:
    result = run_bootstrap("--uninstall-manager")
    calls = result["calls"]
    assert result["exitCode"] == 0
    assert len(calls) == 1
    assert calls[0]["command"] == "sh"
    assert calls[0]["args"][-1] == "uninstall"
