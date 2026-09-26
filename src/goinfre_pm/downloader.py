from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import http.client
import json
import hashlib
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .branding import USER_AGENT
from .models import Package, current_architecture

Progress = Callable[[int, int | None], None]
Log = Callable[[str], None]


class DownloadCancelled(RuntimeError):
    pass


def _request(url: str, timeout: int = 30) -> urllib.response.addinfourl:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("Only remote HTTPS downloads are allowed")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream, application/json"})
    try:
        # The scheme is checked immediately above and again after redirects.
        response = urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context())  # nosec B310
        final = urllib.parse.urlparse(response.geturl())
        if final.scheme != "https" or not final.netloc:
            unsafe_url = response.geturl()
            response.close()
            raise RuntimeError(f"Refusing redirect to a non-HTTPS location: {unsafe_url}")
        return response
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} while downloading {parsed.netloc}") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise RuntimeError(f"TLS/network error for {parsed.netloc}: {reason}") from exc


def _response_name(response: urllib.response.addinfourl, fallback_url: str) -> str:
    disposition = response.headers.get("Content-Disposition", "")
    match = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", disposition, re.IGNORECASE)
    if match:
        candidate = urllib.parse.unquote(match.group(1)).strip('"')
    else:
        candidate = Path(urllib.parse.urlparse(response.geturl() or fallback_url).path).name
    candidate = Path(candidate).name
    return candidate if candidate not in {"", ".", ".."} else "download.bin"


def _asset_score(name: str, pattern: str) -> int:
    lower = name.lower()
    if lower.endswith((".sha256", ".sha512", ".sig", ".asc", ".zsync", ".txt")):
        return -1
    if pattern:
        try:
            return 100 if re.search(pattern, name, re.IGNORECASE) else -1
        except re.error as exc:
            raise RuntimeError(f"Invalid GitHub asset pattern {pattern!r}: {exc}") from exc
    arch = current_architecture()
    arch_terms = ("x86_64", "amd64", "x64") if arch == "x86_64" else ("aarch64", "arm64")
    all_arch_terms = {"x86_64", "amd64", "x64", "aarch64", "arm64"}
    named_arches = {term for term in all_arch_terms if term in lower}
    if named_arches and not named_arches.intersection(arch_terms):
        return -1
    score = 10 if "linux" in lower else 0
    score += 20 if any(term in lower for term in arch_terms) else 0
    score += 3 if lower.endswith((".appimage", ".tar.gz", ".tar.xz", ".deb", ".zip")) else 0
    return score


def _compatible_asset(release: dict[str, object], package: Package) -> dict[str, object] | None:
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        return None
    scored = [
        (_asset_score(str(asset.get("name", "")), package.asset_pattern), asset)
        for asset in assets
        if isinstance(asset, dict)
    ]
    usable = [item for item in scored if item[0] >= 0]
    return max(usable, key=lambda item: item[0])[1] if usable else None


def resolve_github_release(package: Package, log: Log) -> tuple[str, str]:
    match = re.fullmatch(r"https://github\.com/([^/]+)/([^/]+)/?", package.url)
    if not match:
        raise RuntimeError(f"GitHub source must be a repository URL: {package.url}")
    owner, repo = match.groups()
    api = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    log(f"Resolving latest release from {owner}/{repo}")
    with _request(api, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("GitHub returned invalid latest-release metadata")
    chosen = _compatible_asset(data, package)
    if chosen is None:
        # Some upstreams mark a mobile-only release as "latest" even though a
        # recent stable desktop release is still current (Obsidian does this).
        # Look backwards without ever accepting drafts or prereleases.
        log("Latest release has no compatible asset; checking recent stable releases")
        releases_api = f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=30"
        with _request(releases_api, timeout=20) as response:
            releases = json.loads(response.read().decode("utf-8"))
        if not isinstance(releases, list):
            raise RuntimeError("GitHub returned invalid release metadata")
        for release in releases:
            if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
                continue
            candidate = _compatible_asset(release, package)
            if candidate is not None:
                data, chosen = release, candidate
                break
    if chosen is None:
        raise RuntimeError(f"No compatible release asset found for {package.identifier}/{current_architecture()}")
    url = str(chosen.get("browser_download_url", ""))
    if not url.startswith("https://"):
        raise RuntimeError("GitHub returned an unsafe asset URL")
    version = str(data.get("tag_name", package.version))
    log(f"Selected {chosen.get('name', 'release asset')} ({version})")
    return url, version


def download(
    package: Package,
    destination: Path,
    progress: Progress | None = None,
    log: Log | None = None,
    cancel: threading.Event | None = None,
) -> tuple[Path, str]:
    log = log or (lambda _message: None)
    progress = progress or (lambda _done, _total: None)
    url, version = (resolve_github_release(package, log) if package.source_type == "github" else (package.url, package.version))
    destination.mkdir(parents=True, exist_ok=True)
    output: Path | None = None
    written = 0
    total: int | None = None
    last_error: BaseException | None = None
    # Campus mirrors and Wi-Fi occasionally accept a connection that then
    # stops delivering data. Reconnect automatically instead of requiring the
    # student to cancel and start the entire install again.
    for attempt in range(1, 4):
        if cancel and cancel.is_set():
            raise DownloadCancelled("Download cancelled")
        try:
            with _request(url, timeout=8) as response:
                filename = _response_name(response, url)
                output = destination / filename
                output.unlink(missing_ok=True)
                total_header = response.headers.get("Content-Length")
                total = int(total_header) if total_header and total_header.isdigit() else None
                written = 0
                started = time.monotonic()
                window_started = started
                window_bytes = 0
                with output.open("xb") as handle:
                    while True:
                        if cancel and cancel.is_set():
                            raise DownloadCancelled("Download cancelled")
                        chunk = response.read(128 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                        written += len(chunk)
                        window_bytes += len(chunk)
                        progress(written, total)
                        now = time.monotonic()
                        window_elapsed = now - window_started
                        if attempt < 3 and window_elapsed >= 30 and window_bytes / window_elapsed < 32 * 1024:
                            raise TimeoutError("download stayed below 32 KiB/s for 30 seconds")
                        if window_elapsed >= 30:
                            window_started, window_bytes = now, 0
            if total is not None and written != total:
                raise http.client.IncompleteRead(b"", total - written)
            break
        except DownloadCancelled:
            if output is not None:
                output.unlink(missing_ok=True)
            raise
        except (OSError, RuntimeError, TimeoutError, http.client.HTTPException, socket.timeout) as exc:
            last_error = exc
            if output is not None:
                output.unlink(missing_ok=True)
            if attempt == 3:
                raise RuntimeError(f"Download failed after 3 attempts: {exc}") from exc
            log(f"Download connection was too slow or interrupted; retrying ({attempt}/3)")
            progress(0, total)
            if cancel and cancel.wait(min(attempt, 2)):
                raise DownloadCancelled("Download cancelled") from exc
    if output is None:
        raise RuntimeError(f"Download failed: {last_error or 'no response'}")
    if package.sha256:
        try:
            digest = hashlib.sha256()
            with output.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            actual = digest.hexdigest()
            if actual.lower() != package.sha256.lower():
                raise RuntimeError(
                    f"Checksum mismatch for {filename}: expected {package.sha256.lower()}, received {actual}"
                )
        except BaseException:
            output.unlink(missing_ok=True)
            raise
        log(f"Verified SHA-256 for {filename}")
    log(f"Downloaded {filename} ({written / (1024 * 1024):.1f} MiB)")
    return output, version
