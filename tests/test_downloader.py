from io import BytesIO
import hashlib
import json
import threading

from goinfre_pm import downloader
from goinfre_pm.models import Package


class FakeResponse(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class DownloadResponse(FakeResponse):
    def __init__(self, payload: bytes, url: str = "https://example.invalid/tool.bin", status: int = 200, headers=None) -> None:
        super().__init__(payload)
        self.headers = headers or {"Content-Length": str(len(payload)), "Content-Disposition": 'attachment; filename="tool.bin"'}
        self._url = url
        self.status = status

    def geturl(self) -> str:
        return self._url


def _package() -> Package:
    return Package(
        "desktop-app",
        "Desktop App",
        "fixture",
        "Developer Tools",
        "https://github.com/example/desktop-app",
        source_type="github",
        asset_pattern=r"_amd64\.deb$",
        executable_candidates=("bin/app",),
    )


def test_github_resolver_uses_latest_matching_asset(monkeypatch) -> None:
    latest = {
        "tag_name": "v2",
        "assets": [{"name": "app_amd64.deb", "browser_download_url": "https://example.invalid/app.deb"}],
    }
    calls: list[str] = []

    def request(url: str, timeout: int = 30):
        calls.append(url)
        return FakeResponse(json.dumps(latest).encode())

    monkeypatch.setattr(downloader, "_request", request)
    assert downloader.resolve_github_release(_package(), lambda _message: None) == (
        "https://example.invalid/app.deb",
        "v2",
    )
    assert len(calls) == 1


def test_github_resolver_falls_back_to_recent_stable_desktop_release(monkeypatch) -> None:
    latest = {
        "tag_name": "mobile-v3",
        "assets": [{"name": "app.apk", "browser_download_url": "https://example.invalid/app.apk"}],
    }
    releases = [
        latest,
        {
            "tag_name": "desktop-v2",
            "draft": False,
            "prerelease": False,
            "assets": [{"name": "app_amd64.deb", "browser_download_url": "https://example.invalid/app.deb"}],
        },
    ]

    def request(url: str, timeout: int = 30):
        payload = releases if "?per_page=" in url else latest
        return FakeResponse(json.dumps(payload).encode())

    monkeypatch.setattr(downloader, "_request", request)
    assert downloader.resolve_github_release(_package(), lambda _message: None) == (
        "https://example.invalid/app.deb",
        "desktop-v2",
    )


def test_download_verifies_configured_sha256(monkeypatch, tmp_path) -> None:
    payload = b"verified application"
    package = Package(
        "tool", "Tool", "fixture", "Tools", "https://example.invalid/tool.bin",
        source_type="binary", architectures=("any",), sha256=hashlib.sha256(payload).hexdigest(),
    )
    monkeypatch.setattr(downloader, "_request", lambda *_args, **_kwargs: DownloadResponse(payload))

    output, _version = downloader.download(package, tmp_path)

    assert output.read_bytes() == payload


def test_download_removes_file_on_checksum_mismatch(monkeypatch, tmp_path) -> None:
    package = Package(
        "tool", "Tool", "fixture", "Tools", "https://example.invalid/tool.bin",
        source_type="binary", architectures=("any",), sha256="0" * 64,
    )
    monkeypatch.setattr(downloader, "_request", lambda *_args, **_kwargs: DownloadResponse(b"tampered"))

    try:
        downloader.download(package, tmp_path)
        raise AssertionError("checksum mismatch was accepted")
    except RuntimeError as exc:
        assert "Checksum mismatch" in str(exc)
    assert list(tmp_path.iterdir()) == []


def test_download_retries_an_interrupted_connection(monkeypatch, tmp_path) -> None:
    payload = b"recovered download"
    package = Package(
        "tool", "Tool", "fixture", "Tools", "https://example.invalid/tool.bin",
        source_type="binary", architectures=("any",),
    )
    calls = 0

    class InterruptedResponse(DownloadResponse):
        def read(self, _size=-1):
            raise TimeoutError("campus connection stalled")

    def request(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return InterruptedResponse(payload) if calls == 1 else DownloadResponse(payload)

    monkeypatch.setattr(downloader, "_request", request)
    messages: list[str] = []
    output, _version = downloader.download(package, tmp_path, log=messages.append)

    assert calls == 2
    assert output.read_bytes() == payload
    assert any("retrying" in message for message in messages)


def test_download_resumes_partial_transfer(monkeypatch, tmp_path) -> None:
    payload = b"abcdefghij"
    package = Package(
        "tool", "Tool", "fixture", "Tools", "https://example.invalid/tool.bin",
        source_type="binary", architectures=("any",),
    )
    calls: list[dict[str, str] | None] = []

    class InterruptedResponse(DownloadResponse):
        def __init__(self):
            super().__init__(payload, headers={"Content-Length": str(len(payload))})
            self.reads = 0

        def read(self, _size=-1):
            self.reads += 1
            if self.reads == 1:
                return payload[:4]
            raise TimeoutError("connection stalled")

    def request(_url, _timeout=30, headers=None):
        calls.append(headers)
        if len(calls) == 1:
            return InterruptedResponse()
        assert headers == {"Range": "bytes=4-"}
        return DownloadResponse(
            payload[4:],
            status=206,
            headers={"Content-Length": "6", "Content-Range": "bytes 4-9/10"},
        )

    monkeypatch.setattr(downloader, "_request", request)
    output, _version = downloader.download(package, tmp_path)

    assert output.read_bytes() == payload
    assert calls == [None, {"Range": "bytes=4-"}]


def test_download_honors_cancellation_before_connecting(monkeypatch, tmp_path) -> None:
    package = Package(
        "tool", "Tool", "fixture", "Tools", "https://example.invalid/tool.bin",
        source_type="binary", architectures=("any",),
    )
    cancelled = threading.Event()
    cancelled.set()
    monkeypatch.setattr(downloader, "_request", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()))

    try:
        downloader.download(package, tmp_path, cancel=cancelled)
        raise AssertionError("cancelled download was started")
    except downloader.DownloadCancelled:
        pass
    assert list(tmp_path.iterdir()) == []
