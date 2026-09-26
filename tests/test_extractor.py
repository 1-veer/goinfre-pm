from io import BytesIO
from pathlib import Path
import os
import tarfile
import threading
import zipfile

import pytest

from goinfre_pm import extractor
from goinfre_pm.extractor import UnsafeArchiveError, extract_download, safe_extract_tar, safe_extract_zip
from goinfre_pm.downloader import DownloadCancelled


def test_safe_zip_extraction_strips_single_root(tmp_path: Path) -> None:
    archive = tmp_path / "safe.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("tool/bin/run", b"hello")
        handle.writestr("tool/readme.txt", b"readme")
    destination = tmp_path / "out"
    safe_extract_zip(archive, destination)
    assert (destination / "bin" / "run").read_bytes() == b"hello"


def test_zip_extraction_honors_cancellation(tmp_path: Path) -> None:
    archive = tmp_path / "safe.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("tool/bin/run", b"hello")
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(DownloadCancelled):
        safe_extract_zip(archive, tmp_path / "out", cancelled)


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


def test_txz_is_dispatched_as_tar(tmp_path: Path) -> None:
    archive = tmp_path / "tool.txz"
    payload = b"#!/bin/sh\n"
    with tarfile.open(archive, "w:xz") as handle:
        info = tarfile.TarInfo("bin/tool")
        info.size = len(payload)
        info.mode = 0o755
        handle.addfile(info, BytesIO(payload))
        readme = tarfile.TarInfo("share/readme.txt")
        readme.size = 0
        handle.addfile(readme, BytesIO())
    destination = tmp_path / "out"
    extract_download(archive, destination, "auto", tmp_path)
    assert (destination / "bin" / "tool").read_bytes() == payload


def test_deb_uses_ubuntu_dpkg_deb_extractor(monkeypatch, tmp_path: Path) -> None:
    archive = tmp_path / "tool.deb"
    destination = tmp_path / "out"
    archive.touch()
    calls = []
    monkeypatch.setattr(extractor.shutil, "which", lambda command: "/usr/bin/dpkg-deb" if command == "dpkg-deb" else None)
    monkeypatch.setattr(extractor.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)))

    extractor.extract_deb(archive, destination)

    assert calls == [(["/usr/bin/dpkg-deb", "-x", str(archive), str(destination)], {"check": True, "timeout": 180})]


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


def test_tar_internal_relative_symlink_is_preserved(tmp_path: Path) -> None:
    archive = tmp_path / "safe-link.tar"
    payload = b"library"
    with tarfile.open(archive, "w") as handle:
        file_info = tarfile.TarInfo("tool/lib/library.so")
        file_info.size = len(payload)
        handle.addfile(file_info, BytesIO(payload))
        link_info = tarfile.TarInfo("tool/bin/library.so")
        link_info.type = tarfile.SYMTYPE
        link_info.linkname = "../lib/library.so"
        handle.addfile(link_info)

    destination = tmp_path / "out"
    safe_extract_tar(archive, destination)

    assert (destination / "bin" / "library.so").is_symlink()
    assert (destination / "bin" / "library.so").read_bytes() == payload


def test_tar_internal_hard_link_uses_archive_root(tmp_path: Path) -> None:
    archive = tmp_path / "safe-hard-link.tar"
    payload = b"binary"
    with tarfile.open(archive, "w") as handle:
        file_info = tarfile.TarInfo("tool/bin/real")
        file_info.size = len(payload)
        handle.addfile(file_info, BytesIO(payload))
        link_info = tarfile.TarInfo("tool/bin/alias")
        link_info.type = tarfile.LNKTYPE
        link_info.linkname = "tool/bin/real"
        handle.addfile(link_info)

    destination = tmp_path / "out"
    safe_extract_tar(archive, destination)

    assert (destination / "bin" / "alias").read_bytes() == payload
    assert os.stat(destination / "bin" / "alias").st_ino == os.stat(destination / "bin" / "real").st_ino
