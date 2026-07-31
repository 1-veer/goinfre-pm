from io import BytesIO
from pathlib import Path
import os
import tarfile
import zipfile

import pytest

from goinfre_pm.extractor import UnsafeArchiveError, safe_extract_tar, safe_extract_zip


def test_safe_zip_extraction_strips_single_root(tmp_path: Path) -> None:
    archive = tmp_path / "safe.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("tool/bin/run", b"hello")
        handle.writestr("tool/readme.txt", b"readme")
    destination = tmp_path / "out"
    safe_extract_zip(archive, destination)
    assert (destination / "bin" / "run").read_bytes() == b"hello"


@pytest.mark.parametrize("name", ["../escape", "/absolute", "safe/../../escape", "..\\escape"])
def test_zip_traversal_is_rejected(tmp_path: Path, name: str) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr(name, b"bad")
    with pytest.raises(UnsafeArchiveError):
        safe_extract_zip(archive, tmp_path / "out")


def test_zip_symlink_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "link.zip"
    info = zipfile.ZipInfo("link")
    info.create_system = 3
    info.external_attr = (0o120777 << 16)
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr(info, "target")
    with pytest.raises(UnsafeArchiveError):
        safe_extract_zip(archive, tmp_path / "out")


def test_safe_tar_extraction(tmp_path: Path) -> None:
    archive = tmp_path / "safe.tar.gz"
    payload = b"#!/bin/sh\n"
    with tarfile.open(archive, "w:gz") as handle:
        info = tarfile.TarInfo("tool/bin/run")
        info.size = len(payload)
        info.mode = 0o755
        handle.addfile(info, BytesIO(payload))
    destination = tmp_path / "out"
    safe_extract_tar(archive, destination)
    executable = destination / "bin" / "run"
    assert executable.read_bytes() == payload
    assert os.access(executable, os.X_OK)


def test_tar_traversal_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "bad.tar"
    with tarfile.open(archive, "w") as handle:
        info = tarfile.TarInfo("../escape")
        info.size = 3
        handle.addfile(info, BytesIO(b"bad"))
    with pytest.raises(UnsafeArchiveError):
        safe_extract_tar(archive, tmp_path / "out")


def test_tar_unsafe_symlink_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "bad-link.tar"
    with tarfile.open(archive, "w") as handle:
        info = tarfile.TarInfo("tool/link")
        info.type = tarfile.SYMTYPE
        info.linkname = "../../outside"
        handle.addfile(info)
    with pytest.raises(UnsafeArchiveError):
        safe_extract_tar(archive, tmp_path / "out")
