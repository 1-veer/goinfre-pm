from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import json
import re
import ssl
import threading
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
        response = urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context())
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


def resolve_github_release(package: Package, log: Log) -> tuple[str, str]:
    match = re.fullmatch(r"https://github\.com/([^/]+)/([^/]+)/?", package.url)
    if not match:
        raise RuntimeError(f"GitHub source must be a repository URL: {package.url}")
    owner, repo = match.groups()
    api = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    log(f"Resolving latest release from {owner}/{repo}")
    with _request(api, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))
    scored = [(_asset_score(str(asset.get("name", "")), package.asset_pattern), asset) for asset in data.get("assets", [])]
    usable = [item for item in scored if item[0] >= 0]
    if not usable:
        raise RuntimeError(f"No compatible release asset found for {package.identifier}/{current_architecture()}")
    _, chosen = max(usable, key=lambda item: item[0])
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
    with _request(url, timeout=30) as response:
        filename = _response_name(response, url)
        output = destination / filename
        total_header = response.headers.get("Content-Length")
        total = int(total_header) if total_header and total_header.isdigit() else None
        written = 0
        try:
            with output.open("xb") as handle:
                while True:
                    if cancel and cancel.is_set():
                        raise DownloadCancelled("Download cancelled")
                    chunk = response.read(128 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    written += len(chunk)
                    progress(written, total)
        except BaseException:
            output.unlink(missing_ok=True)
            raise
    if total is not None and written != total:
        output.unlink(missing_ok=True)
        raise RuntimeError(f"Incomplete download: expected {total} bytes, received {written}")
    log(f"Downloaded {filename} ({written / (1024 * 1024):.1f} MiB)")
    return output, version
