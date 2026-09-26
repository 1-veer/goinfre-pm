from __future__ import annotations

from pathlib import Path, PurePosixPath
import os
import shutil
import signal
import stat
# Subprocesses below always use explicit argument arrays and never a shell.
import subprocess  # nosec B404
import tarfile
import tempfile
import zipfile
import threading
import time

from .downloader import DownloadCancelled


class UnsafeArchiveError(RuntimeError):
    pass


def _safe_relative(name: str, strip: str = "") -> Path | None:
    pure = PurePosixPath(name.replace("\\", "/"))
    if pure.is_absolute() or ".." in pure.parts:
        raise UnsafeArchiveError(f"Unsafe archive path: {name!r}")
    parts = list(pure.parts)
    if strip and parts and parts[0] == strip:
        parts = parts[1:]
    if not parts or parts == ["."]:
        return None
    return Path(*parts)


def _inside(base: Path, path: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _top_level(names: list[str]) -> str:
    parsed = [PurePosixPath(name.replace("\\", "/")).parts for name in names]
    # A shared root is removable only when it is a real wrapper directory.
    # Never strip a single leaf member before its type and safety are checked.
    if not parsed or any(len(parts) < 2 for parts in parsed):
        return ""
    roots = {parts[0] for parts in parsed}
    return next(iter(roots)) if len(roots) == 1 else ""


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise DownloadCancelled("Installation cancelled")


def _copy_cancelled(source, output, cancel: threading.Event | None) -> None:
    while True:
        _check_cancel(cancel)
        chunk = source.read(1024 * 1024)
        if not chunk:
            return
        output.write(chunk)


def safe_extract_zip(archive: Path, destination: Path, cancel: threading.Event | None = None) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as handle:
        infos = handle.infolist()
        strip = _top_level([item.filename for item in infos])
        for item in infos:
            _check_cancel(cancel)
            relative = _safe_relative(item.filename, strip)
            if relative is None:
                continue
            target = destination / relative
            if not _inside(destination, target):
                raise UnsafeArchiveError(f"Archive member escapes destination: {item.filename!r}")
            mode = item.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise UnsafeArchiveError(f"ZIP symlinks are not allowed: {item.filename!r}")
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with handle.open(item) as source, target.open("wb") as output:
                _copy_cancelled(source, output, cancel)
            if mode:
                target.chmod(mode & 0o777)


def safe_extract_tar(archive: Path, destination: Path, cancel: threading.Event | None = None) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as handle:
        members = handle.getmembers()
        strip = _top_level([item.name for item in members])
        deferred_links: list[tuple[tarfile.TarInfo, Path]] = []
        for member in members:
            _check_cancel(cancel)
            relative = _safe_relative(member.name, strip)
            if relative is None:
                continue
            target = destination / relative
            if not _inside(destination, target):
                raise UnsafeArchiveError(f"Archive member escapes destination: {member.name!r}")
            if member.ischr() or member.isblk() or member.isfifo() or member.isdev():
                raise UnsafeArchiveError(f"Special archive members are not allowed: {member.name!r}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(member.mode & 0o777)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = handle.extractfile(member)
                if source is None:
                    raise UnsafeArchiveError(f"Cannot read archive member: {member.name!r}")
                with source, target.open("wb") as output:
                    _copy_cancelled(source, output, cancel)
                target.chmod(member.mode & 0o777)
            elif member.issym() or member.islnk():
                deferred_links.append((member, target))
            else:
                raise UnsafeArchiveError(f"Unsupported archive member: {member.name!r}")
        for member, target in deferred_links:
            _check_cancel(cancel)
            target.parent.mkdir(parents=True, exist_ok=True)
            link = PurePosixPath(member.linkname.replace("\\", "/"))
            if link.is_absolute():
                raise UnsafeArchiveError(f"Unsafe archive link: {member.name!r} -> {member.linkname!r}")
            if member.issym():
                link_target = (target.parent / Path(*link.parts)).resolve(strict=False)
                if not _inside(destination, link_target):
                    raise UnsafeArchiveError(f"Archive link escapes destination: {member.name!r}")
                target.symlink_to(member.linkname)
            else:
                # Tar hard-link names are archive-root relative, unlike
                # symlink targets, which are relative to the link's parent.
                linked_relative = _safe_relative(member.linkname, strip)
                if linked_relative is None:
                    raise UnsafeArchiveError(f"Invalid hard link target: {member.linkname!r}")
                link_target = (destination / linked_relative).resolve(strict=False)
                if not _inside(destination, link_target):
                    raise UnsafeArchiveError(f"Archive link escapes destination: {member.name!r}")
                if not link_target.is_file():
                    raise UnsafeArchiveError(f"Invalid hard link target: {member.linkname!r}")
                os.link(link_target, target)


def _process_error(args: list[str], output: bytes) -> RuntimeError:
    lines = output.decode("utf-8", errors="replace").strip().splitlines()
    detail = " | ".join(lines[-3:]) if lines else "the extractor returned an error"
    return RuntimeError(f"Could not extract the downloaded package: {detail}")


def _terminate_process_group(process: subprocess.Popen, force: bool = False) -> None:
    """Stop an extractor and every child it spawned (notably dpkg-deb's tar)."""
    try:
        os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
    except (OSError, ProcessLookupError):
        if process.poll() is None:
            process.kill() if force else process.terminate()


def _run_cancellable(args: list[str], *, cancel: threading.Event | None, cwd: Path | None = None, stdout=None) -> None:
    # Capture command output in a file rather than a pipe: extractors can be
    # verbose enough to fill a pipe, and nothing may write directly over the
    # Textual screen.
    with tempfile.TemporaryFile() as capture:
        output_target = capture if stdout is None else stdout
        process = subprocess.Popen(  # nosec B603
            args,
            cwd=cwd,
            stdout=output_target,
            stderr=capture,
            start_new_session=True,
        )
        started = time.monotonic()
        try:
            while process.poll() is None:
                if cancel is not None and cancel.wait(0.1):
                    _terminate_process_group(process)
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        _terminate_process_group(process, force=True)
                        process.wait()
                    raise DownloadCancelled("Installation cancelled")
                if time.monotonic() - started > 180:
                    _terminate_process_group(process, force=True)
                    process.wait()
                    raise RuntimeError("Package extraction timed out after 180 seconds")
            if process.returncode:
                capture.seek(0)
                raise _process_error(args, capture.read())
        finally:
            if process.poll() is None:
                _terminate_process_group(process, force=True)
                process.wait()


def extract_deb(archive: Path, destination: Path, cancel: threading.Event | None = None) -> None:
    dpkg_deb = shutil.which("dpkg-deb")
    if dpkg_deb is None:
        raise RuntimeError("dpkg-deb is required to extract .deb packages")
    with archive.open("rb") as handle:
        if handle.read(8) != b"!<arch>\n":
            raise RuntimeError("The downloaded file is not a valid Debian package")
    # The executable comes from PATH and every dynamic value is one array item.
    _run_cancellable([dpkg_deb, "-x", str(archive), str(destination)], cancel=cancel)


def extract_appimage(archive: Path, destination: Path, work: Path, cancel: threading.Event | None = None) -> None:
    archive.chmod(archive.stat().st_mode | 0o111)
    # Executing the AppImage runtime is the format's extraction interface; no
    # shell is involved and the operation remains inside unique staging.
    _run_cancellable([str(archive), "--appimage-extract"], cancel=cancel, cwd=work, stdout=subprocess.DEVNULL)
    root = work / "squashfs-root"
    if not root.is_dir():
        raise RuntimeError("AppImage did not produce squashfs-root")
    shutil.copytree(root, destination, dirs_exist_ok=True, symlinks=True)


def extract_download(
    archive: Path,
    destination: Path,
    source_type: str,
    work: Path,
    cancel: threading.Event | None = None,
) -> None:
    lower = archive.name.lower()
    if source_type == "deb" or lower.endswith(".deb"):
        extract_deb(archive, destination, cancel)
    elif source_type == "appimage" or lower.endswith(".appimage"):
        try:
            extract_appimage(archive, destination, work, cancel)
        except DownloadCancelled:
            raise
        except (OSError, subprocess.SubprocessError, RuntimeError):
            destination.mkdir(parents=True, exist_ok=True)
            target = destination / "AppRun"
            shutil.copy2(archive, target)
            target.chmod(0o755)
    elif source_type == "zip" or lower.endswith(".zip"):
        safe_extract_zip(archive, destination, cancel)
    elif source_type == "tar" or lower.endswith((".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tar")):
        safe_extract_tar(archive, destination, cancel)
    else:
        destination.mkdir(parents=True, exist_ok=True)
        _check_cancel(cancel)
        target = destination / archive.name
        shutil.copy2(archive, target)
        target.chmod(target.stat().st_mode | 0o111)
