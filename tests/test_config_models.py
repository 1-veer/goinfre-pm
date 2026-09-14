from pathlib import Path

import pytest

from goinfre_pm.config import ConfigurationError, load_packages, migrate_legacy_config
from goinfre_pm.models import Package, architecture_matches, validate_package_id


def test_configuration_parsing(tmp_path: Path) -> None:
    config = tmp_path / "packages.toml"
    config.write_text(
        """[[package]]
id = "safe-tool"
name = "Safe Tool"
description = "A fixture"
category = "Developer Tools"
url = "https://example.invalid/tool.tar.gz"
source_type = "tar"
architectures = ["x86_64"]
executables = ["bin/tool"]
desktop = false
terminal = true
""",
        encoding="utf-8",
    )
    package = load_packages(config)[0]
    assert package.identifier == "safe-tool"
    assert package.executable_candidates == ("bin/tool",)
    assert package.terminal is True


def test_curated_catalog_includes_verified_developer_tools() -> None:
    catalog = Path(__file__).parents[1] / "packages.toml"
    packages = {package.identifier: package for package in load_packages(catalog)}

    assert packages["antigravity"].executable_candidates == (
        "Antigravity/antigravity",
        "Antigravity/bin/antigravity",
    )
    assert packages["stremio"].enabled is False
    expected = {"lazygit", "bat", "fd", "fzf", "shellcheck", "github-cli", "git-delta"}
    assert expected <= packages.keys()
    assert all(not packages[identifier].desktop for identifier in expected)
    assert packages["kitty"].executable_candidates == ("bin/kitty",)
    assert packages["kitty"].asset_pattern == r"^kitty-.*-x86_64\.txz$"
    assert packages["postman"].executable_candidates[0] == "app/Postman"


@pytest.mark.parametrize("identifier", ["../escape", "bad/name", "BadName", "a..b", "-leading", "trailing-"])
def test_invalid_package_identifiers_are_rejected(identifier: str) -> None:
    with pytest.raises(ValueError):
        validate_package_id(identifier)


def test_duplicate_package_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "packages.toml"
    config.write_text(
        """[[package]]
id = "same"
url = "https://example.invalid/a"
[[package]]
id = "same"
url = "https://example.invalid/b"
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError):
        load_packages(config)


def test_invalid_boolean_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "packages.toml"
    config.write_text(
        '[[package]]\nid = "tool"\nurl = "https://example.invalid/tool"\nenabled = "false"\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="enabled must be a boolean"):
        load_packages(config)


def test_invalid_post_install_item_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "packages.toml"
    config.write_text(
        '[[package]]\nid = "tool"\nurl = "https://example.invalid/tool"\npost_install = ["chmod"]\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="post_install action must be a table"):
        load_packages(config)


def test_architecture_matching_aliases() -> None:
    assert architecture_matches(("amd64",), "x86_64")
    assert architecture_matches(("arm64",), "aarch64")
    assert architecture_matches(("any",), "riscv64")
    assert not architecture_matches(("aarch64",), "x86_64")


def test_legacy_migration_disables_shell_commands(tmp_path: Path) -> None:
    old = tmp_path / "packages.conf"
    new = tmp_path / "packages.toml"
    old.write_text("safe https://example.invalid/tool.tar.gz echo dangerous\n", encoding="utf-8")
    warnings = migrate_legacy_config(old, new)
    assert "post-install command was disabled" in warnings[0]
    assert "echo dangerous" not in new.read_text(encoding="utf-8")
    assert load_packages(new)[0].identifier == "safe"


def test_loading_legacy_path_migrates_automatically(tmp_path: Path) -> None:
    old = tmp_path / "packages.conf"
    old.write_text("tool https://example.invalid/tool.zip\n", encoding="utf-8")
    packages = load_packages(old)
    assert packages[0].identifier == "tool"
    assert (tmp_path / "packages.toml").is_file()
