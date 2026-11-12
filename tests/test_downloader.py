from __future__ import annotations

import ipaddress

import pytest

from mediabot.downloader import DownloadError, _base_options, _is_tiktok_url, find_url, validate_public_url


def test_find_url_strips_common_trailing_punctuation() -> None:
    assert find_url("شاهد https://example.org/watch?v=123.") == "https://example.org/watch?v=123"
    assert find_url("no link here") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "http://localhost/admin",
    "http://127.0.0.1/a",
    "http://192.168.1.1/",
    "file:///etc/passwd",
    "https://user:password@example.com/secret",
    "https://example.org:not-a-port/path",
    "http://[::1/path",
])
async def test_rejects_local_credential_or_malformed_urls(url: str) -> None:
    with pytest.raises(DownloadError):
        await validate_public_url(url)


@pytest.mark.asyncio
async def test_rejects_non_global_dns_result(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_getaddrinfo(*args: object, **kwargs: object) -> list[tuple]:
        return [(0, 0, 0, "", ("10.0.0.3", 443))]

    monkeypatch.setattr("asyncio.BaseEventLoop.getaddrinfo", fake_getaddrinfo)
    with pytest.raises(DownloadError, match="رفضه لأسباب أمنية"):
        await validate_public_url("https://public.example/path")


def test_ipaddress_module_used_by_validation() -> None:
    assert not ipaddress.ip_address("127.0.0.1").is_global


def test_tiktok_impersonation_is_scoped_to_tiktok_only() -> None:
    assert _is_tiktok_url("https://www.tiktok.com/@user/video/123")
    assert _is_tiktok_url("https://vm.tiktok.com/abc/")
    assert not _is_tiktok_url("https://youtube.com/watch?v=123")

    tiktok_options = _base_options(None, "https://www.tiktok.com/@user/video/123")
    other_options = _base_options(None, "https://youtube.com/watch?v=123")
    assert tiktok_options["impersonate"] == "chrome"
    assert tiktok_options["http_headers"]["Referer"] == "https://www.tiktok.com/"
    assert "impersonate" not in other_options
    assert "http_headers" not in other_options
