from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import FIRST_EXCEPTION, Future, ThreadPoolExecutor, wait
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

CONNECT_TIMEOUT_SECONDS = 6
MAX_DOWNLOAD_ATTEMPTS = 5
SLOW_CONNECTION_WINDOW_SECONDS = 8
MIN_USEFUL_DOWNLOAD_RATE = 384 * 1024
LARGE_DOWNLOAD_REMAINDER = 16 * 1024 * 1024
PARALLEL_DOWNLOAD_THRESHOLD = 32 * 1024 * 1024
PARALLEL_DOWNLOAD_WORKERS = 4
PARALLEL_DOWNLOAD_CHUNK_SIZE = 8 * 1024 * 1024
RANGE_DOWNLOAD_ATTEMPTS = 6
MIN_RANGE_DOWNLOAD_RATE = 128 * 1024


class DownloadCancelled(RuntimeError):
    pass


class _ParallelDownloadUnavailable(RuntimeError):
    """The origin does not safely support a segmented range download."""


def _request(
    url: str,
    timeout: int = 30,
    extra_headers: dict[str, str] | None = None,
) -> urllib.response.addinfourl:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("Only remote HTTPS downloads are allowed")
    headers = {"User-Agent": USER_AGENT, "Accept": "application/octet-stream, application/json"}
    if extra_headers:
        headers.update(extra_headers)
    request = urllib.request.Request(url, headers=headers)
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


def _response_status(response: urllib.response.addinfourl) -> int:
    status = getattr(response, "status", None)
    if status is None:
        getcode = getattr(response, "getcode", None)
        status = getcode() if callable(getcode) else 200
    return int(status or 200)


def _is_github_release_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.hostname == "github.com" and "/releases/" in parsed.path and "/download/" in parsed.path


def _range_from_headers(response: urllib.response.addinfourl) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
    if not match:
        return None
    start, end, total = (int(value) for value in match.groups())
    return start, end, total


def _parallel_github_download(
    url: str,
    destination: Path,
    progress: Progress,
    log: Log,
    cancel: threading.Event | None,
) -> tuple[Path, int]:
    """Download an official GitHub release asset using bounded byte ranges."""
    if cancel and cancel.is_set():
        raise DownloadCancelled("Download cancelled")
    with _request(url, CONNECT_TIMEOUT_SECONDS, {"Range": "bytes=0-0"}) as probe:
        byte_range = _range_from_headers(probe)
        if _response_status(probe) != 206 or byte_range is None or byte_range[:2] != (0, 0):
            raise _ParallelDownloadUnavailable("GitHub asset did not accept a byte-range probe")
        total = byte_range[2]
        if total < PARALLEL_DOWNLOAD_THRESHOLD:
            raise _ParallelDownloadUnavailable("Asset is too small to benefit from a segmented download")
        final_url = probe.geturl()
        final_host = urllib.parse.urlparse(final_url).hostname or ""
        if not final_host.endswith(".githubusercontent.com"):
            raise _ParallelDownloadUnavailable("GitHub redirected the asset to an unexpected host")
        filename = _response_name(probe, url)
        if len(probe.read(2)) != 1:
            raise _ParallelDownloadUnavailable("GitHub returned an invalid byte-range probe")

    output = destination / filename
    output.unlink(missing_ok=True)
    with output.open("xb") as handle:
        handle.truncate(total)

    ranges: list[tuple[int, int]] = []
    start = 0
    while start < total:
        end = min(start + PARALLEL_DOWNLOAD_CHUNK_SIZE, total) - 1
        ranges.append((start, end))
        start = end + 1
    workers = min(PARALLEL_DOWNLOAD_WORKERS, len(ranges))

    state_lock = threading.Lock()
    stop = threading.Event()
    received = 0

    def fetch_range(range_start: int, range_end: int) -> None:
        nonlocal received
        position = range_start
        last_error: BaseException | None = None
        for attempt in range(1, RANGE_DOWNLOAD_ATTEMPTS + 1):
            if stop.is_set() or (cancel and cancel.is_set()):
                raise DownloadCancelled("Download cancelled")
            try:
                headers = {"Range": f"bytes={position}-{range_end}"}
                with _request(final_url, CONNECT_TIMEOUT_SECONDS, headers) as response:
                    actual_range = _range_from_headers(response)
                    expected_range = (position, range_end, total)
                    if _response_status(response) != 206 or actual_range != expected_range:
                        raise RuntimeError("GitHub returned an unexpected byte range")
                    window_started = time.monotonic()
                    window_bytes = 0
                    with output.open("r+b", buffering=0) as handle:
                        handle.seek(position)
                        while position <= range_end:
                            if stop.is_set() or (cancel and cancel.is_set()):
                                raise DownloadCancelled("Download cancelled")
                            remaining = range_end - position + 1
                            chunk = response.read(min(256 * 1024, remaining))
                            if not chunk:
                                raise http.client.IncompleteRead(b"", remaining)
                            if len(chunk) > remaining:
                                raise RuntimeError("GitHub returned data outside the requested byte range")
                            handle.write(chunk)
                            position += len(chunk)
                            window_bytes += len(chunk)
                            with state_lock:
                                received += len(chunk)
                            now = time.monotonic()
                            window_elapsed = now - window_started
                            if (
                                attempt < RANGE_DOWNLOAD_ATTEMPTS
                                and window_elapsed >= SLOW_CONNECTION_WINDOW_SECONDS
                                and window_bytes / window_elapsed < MIN_RANGE_DOWNLOAD_RATE
                            ):
                                rate_kib = window_bytes / window_elapsed / 1024
                                raise TimeoutError(
                                    f"range connection averaged only {rate_kib:.0f} KiB/s for "
                                    f"{SLOW_CONNECTION_WINDOW_SECONDS} seconds"
                                )
                            if window_elapsed >= SLOW_CONNECTION_WINDOW_SECONDS:
                                window_started, window_bytes = now, 0
                return
            except DownloadCancelled:
                raise
            except (OSError, RuntimeError, TimeoutError, http.client.HTTPException, socket.timeout) as exc:
                last_error = exc
                if attempt == RANGE_DOWNLOAD_ATTEMPTS:
                    raise RuntimeError(
                        f"GitHub range {range_start}-{range_end} failed after "
                        f"{RANGE_DOWNLOAD_ATTEMPTS} attempts: {exc}"
                    ) from exc
                retry_delay = min(attempt * 0.5, 2.0)
                if stop.wait(retry_delay) or (cancel and cancel.is_set()):
                    raise DownloadCancelled("Download cancelled") from exc
        raise RuntimeError(f"GitHub range download failed: {last_error or 'unknown error'}")

    log(
        f"Using {workers} secure connections across {len(ranges)} verified chunks "
        "for this GitHub release download"
    )
    progress(0, total)
    futures: set[Future[None]] = set()
    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="gpm-download") as executor:
            futures = {executor.submit(fetch_range, range_start, range_end) for range_start, range_end in ranges}
            pending = set(futures)
            while pending:
                if cancel and cancel.is_set():
                    stop.set()
                finished, pending = wait(pending, timeout=0.1, return_when=FIRST_EXCEPTION)
                with state_lock:
                    current = received
                progress(current, total)
                for future in finished:
                    error = future.exception()
                    if error is not None:
                        stop.set()
                        for remaining_future in pending:
                            remaining_future.cancel()
                        raise error
        progress(total, total)
        return output, total
    except BaseException:
        stop.set()
        output.unlink(missing_ok=True)
        raise


def _finish_download(package: Package, output: Path, version: str, written: int, log: Log) -> tuple[Path, str]:
    if package.sha256:
        try:
            digest = hashlib.sha256()
            with output.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            actual = digest.hexdigest()
            if actual.lower() != package.sha256.lower():
                raise RuntimeError(
                    f"Checksum mismatch for {output.name}: "
                    f"expected {package.sha256.lower()}, received {actual}"
                )
        except BaseException:
            output.unlink(missing_ok=True)
            raise
        log(f"Verified SHA-256 for {output.name}")
    log(f"Downloaded {output.name} ({written / (1024 * 1024):.1f} MiB)")
    return output, version


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
    if _is_github_release_url(url):
        try:
            parallel_output, parallel_size = _parallel_github_download(url, destination, progress, log, cancel)
            return _finish_download(package, parallel_output, version, parallel_size, log)
        except _ParallelDownloadUnavailable as exc:
            log(f"Segmented GitHub download unavailable ({exc}); using one connection")
        except DownloadCancelled:
            raise
        except (OSError, RuntimeError, TimeoutError, http.client.HTTPException, socket.timeout) as exc:
            raise RuntimeError(
                "GitHub's release server could not complete the segmented download after retries. "
                "No application files were changed; retry the installation."
            ) from exc
    output: Path | None = None
    filename = "download.bin"
    written = 0
    total: int | None = None
    last_error: BaseException | None = None
    # Campus mirrors and Wi-Fi occasionally accept a connection that then
    # stops delivering data. Reconnect automatically instead of requiring the
    # student to cancel and start the entire install again.
    for attempt in range(1, MAX_DOWNLOAD_ATTEMPTS + 1):
        if cancel and cancel.is_set():
            raise DownloadCancelled("Download cancelled")
        try:
            headers = {"Range": f"bytes={written}-"} if written else None
            request_args = (url, CONNECT_TIMEOUT_SECONDS, headers) if headers else (url, CONNECT_TIMEOUT_SECONDS)
            with _request(*request_args) as response:
                response_name = _response_name(response, url)
                if output is None:
                    filename = response_name
                    output = destination / filename
                    output.unlink(missing_ok=True)
                status = getattr(response, "status", None)
                if status is None:
                    getcode = getattr(response, "getcode", None)
                    status = getcode() if callable(getcode) else 200
                content_range = response.headers.get("Content-Range", "")
                range_match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+|\*)", content_range)
                resumed = bool(written and status == 206 and range_match and int(range_match.group(1)) == written)
                if written and not resumed:
                    log("Download server did not support resume; restarting this transfer")
                    written = 0
                if not resumed:
                    output.unlink(missing_ok=True)
                total_header = response.headers.get("Content-Length")
                response_length = int(total_header) if total_header and total_header.isdigit() else None
                if resumed and range_match and range_match.group(3).isdigit():
                    total = int(range_match.group(3))
                elif response_length is not None:
                    total = written + response_length
                window_started = time.monotonic()
                window_bytes = 0
                with output.open("ab" if resumed else "xb") as handle:
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
                        remaining = None if total is None else max(total - written, 0)
                        large_transfer = remaining is None or remaining >= LARGE_DOWNLOAD_REMAINDER
                        if (
                            attempt < MAX_DOWNLOAD_ATTEMPTS
                            and large_transfer
                            and window_elapsed >= SLOW_CONNECTION_WINDOW_SECONDS
                            and window_bytes / window_elapsed < MIN_USEFUL_DOWNLOAD_RATE
                        ):
                            rate_kib = window_bytes / window_elapsed / 1024
                            raise TimeoutError(
                                f"connection averaged only {rate_kib:.0f} KiB/s for "
                                f"{SLOW_CONNECTION_WINDOW_SECONDS} seconds"
                            )
                        if window_elapsed >= SLOW_CONNECTION_WINDOW_SECONDS:
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
            if output is not None and total is not None and written == total:
                log("The connection closed after the complete payload was received; continuing safely")
                break
            if attempt == MAX_DOWNLOAD_ATTEMPTS:
                if output is not None:
                    output.unlink(missing_ok=True)
                raise RuntimeError(f"Download failed after {MAX_DOWNLOAD_ATTEMPTS} attempts: {exc}") from exc
            preserved_mib = written / (1024 * 1024)
            log(
                "Download connection was too slow or interrupted; "
                f"reconnecting and keeping {preserved_mib:.1f} MiB already received "
                f"({attempt}/{MAX_DOWNLOAD_ATTEMPTS})"
            )
            if cancel and cancel.wait(0.25):
                raise DownloadCancelled("Download cancelled") from exc
    if output is None:
        raise RuntimeError(f"Download failed: {last_error or 'no response'}")
    return _finish_download(package, output, version, written, log)
