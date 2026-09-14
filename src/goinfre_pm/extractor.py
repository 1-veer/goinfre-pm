from __future__ import annotations

from pathlib import Path, PurePosixPath
import os
import shutil
import stat
# Subprocesses below always use explicit argument arrays and never a shell.
import subprocess  # nosec B404
import tarfile
import zipfile


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


def safe_extract_zip(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as handle:
        infos = handle.infolist()
        strip = _top_level([item.filename for item in infos])
        for item in infos:
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
                shutil.copyfileobj(source, output, length=1024 * 1024)
            if mode:
                target.chmod(mode & 0o777)


def safe_extract_tar(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as handle:
        members = handle.getmembers()
        strip = _top_level([item.name for item in members])
        deferred_links: list[tuple[tarfile.TarInfo, Path]] = []
        for member in members:
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
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                target.chmod(member.mode & 0o777)
            elif member.issym() or member.islnk():
                deferred_links.append((member, target))
            else:
                raise UnsafeArchiveError(f"Unsupported archive member: {member.name!r}")
        for member, target in deferred_links:
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


def extract_deb(archive: Path, destination: Path) -> None:
    dpkg_deb = shutil.which("dpkg-deb")
    if dpkg_deb is None:
        raise RuntimeError("dpkg-deb is required to extract .deb packages")
    # The executable comes from PATH and every dynamic value is one array item.
    subprocess.run([dpkg_deb, "-x", str(archive), str(destination)], check=True, timeout=180)


def extract_appimage(archive: Path, destination: Path, work: Path) -> None:
    archive.chmod(archive.stat().st_mode | 0o111)
    # Executing the AppImage runtime is the format's extraction interface; no
    # shell is involved and the operation remains inside unique staging.
    subprocess.run(
        [str(archive), "--appimage-extract"], cwd=work, check=True, timeout=180, stdout=subprocess.DEVNULL
    )
    root = work / "squashfs-root"
    if not root.is_dir():
        raise RuntimeError("AppImage did not produce squashfs-root")
    shutil.copytree(root, destination, dirs_exist_ok=True, symlinks=True)


def extract_download(archive: Path, destination: Path, source_type: str, work: Path) -> None:
    lower = archive.name.lower()
    if source_type == "deb" or lower.endswith(".deb"):
        extract_deb(archive, destination)
    elif source_type == "appimage" or lower.endswith(".appimage"):
        try:
            extract_appimage(archive, destination, work)
        except (OSError, subprocess.SubprocessError, RuntimeError):
            destination.mkdir(parents=True, exist_ok=True)
            target = destination / "AppRun"
            shutil.copy2(archive, target)
            target.chmod(0o755)
    elif source_type == "zip" or lower.endswith(".zip"):
        safe_extract_zip(archive, destination)
    elif source_type == "tar" or lower.endswith((".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tar")):
        safe_extract_tar(archive, destination)
    else:
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / archive.name
        shutil.copy2(archive, target)
        target.chmod(target.stat().st_mode | 0o111)
