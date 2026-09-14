from pathlib import Path

from goinfre_pm.config import load_packages
from goinfre_pm.experience import STARTER_PACKS, apply_starter_pack, estimate_basket, human_size, sort_packages
from goinfre_pm.models import Package


def package(identifier: str, *, category: str = "Tools", download=None, installed=None) -> Package:
    return Package(
        identifier,
        identifier.replace("-", " ").title(),
        "fixture",
        category,
        f"https://example.invalid/{identifier}.tar.gz",
        source_type="tar",
        architectures=("any",),
        download_size=download,
        installed_size=installed,
    )


def test_starter_packs_only_return_available_compatible_packages() -> None:
    pack = next(item for item in STARTER_PACKS if item.identifier == "minimal-terminal")
    packages = [package("kitty"), package("neovim"), package("ripgrep")]
    assert apply_starter_pack(pack, packages) == ("kitty", "neovim", "ripgrep")


def test_every_starter_pack_references_enabled_catalog_packages() -> None:
    catalog = {package.identifier: package for package in load_packages(Path(__file__).parents[1] / "packages.toml")}
    assert {pack.identifier for pack in STARTER_PACKS} == {
        "42-c-cpp", "web-development", "minimal-terminal", "creative"
    }
    for pack in STARTER_PACKS:
        assert len(pack.package_ids) == len(set(pack.package_ids))
        assert all(identifier in catalog and catalog[identifier].enabled for identifier in pack.package_ids)


def test_basket_estimate_tracks_known_and_unknown_sizes() -> None:
    estimate = estimate_basket(
        [package("known", download=100, installed=250), package("unknown")],
        free_space=1000,
    )
    assert estimate.count == 2
    assert estimate.known_download == 100
    assert estimate.known_installed == 250
    assert estimate.unknown_downloads == 1
    assert estimate.unknown_installed == 1
    assert estimate.known_required == 350
    assert estimate.remaining_after_known == 650
    assert estimate.insufficient_space is False


def test_basket_flags_insufficient_known_peak_space() -> None:
    estimate = estimate_basket([package("large", download=600, installed=500)], free_space=1000)
    assert estimate.known_required == 1100
    assert estimate.remaining_after_known == 0
    assert estimate.insufficient_space is True


def test_sorting_is_stable_and_unknown_sizes_are_last() -> None:
    packages = [package("zulu", installed=10), package("alpha"), package("beta", installed=50)]
    assert [item.identifier for item in sort_packages(packages, "name")] == ["alpha", "beta", "zulu"]
    assert [item.identifier for item in sort_packages(packages, "size")] == ["beta", "zulu", "alpha"]
    assert [item.identifier for item in sort_packages(packages, "updates", updates={"zulu"})][0] == "zulu"
    assert [item.identifier for item in sort_packages(packages, "size", installed_sizes={"alpha": 100})][0] == "alpha"


def test_human_size() -> None:
    assert human_size(1024**2) == "1.0 MiB"
