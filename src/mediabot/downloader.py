from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import shutil
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import yt_dlp

from .config import Settings

log = logging.getLogger(__name__)
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


class DownloadError(Exception):
    """A safe, user-displayable failure from URL validation or media processing."""


@dataclass(slots=True)
class MediaInfo:
    title: str
    duration: int | None
    heights: list[int]


@dataclass(slots=True)
class DownloadedMedia:
    path: Path
    title: str
    duration: int | None


def find_url(text: str) -> str | None:
    match = URL_RE.search(text)
    if not match:
        return None
    return match.group(0).rstrip(".,;:!?)\u3002\u3001")


async def validate_public_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
    except ValueError:
        raise DownloadError("صيغة الرابط غير صالحة.") from None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise DownloadError("الرابط لازم يبدأ بـ http:// أو https:// ويكون كاملًا.")
    try:
        port = parsed.port
    except ValueError:
        raise DownloadError("رقم المنفذ في الرابط غير صالح.") from None
    if parsed.username or parsed.password:
        raise DownloadError("الروابط التي تحتوي بيانات دخول غير مسموح بها.")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal")):
        raise DownloadError("الرابط يشير إلى عنوان محلي وغير مسموح.")
    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            records = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(
                    host,
                    port or (443 if parsed.scheme == "https" else 80),
                    type=socket.SOCK_STREAM,
                ),
                timeout=5,
            )
        except (OSError, TimeoutError):
            raise DownloadError("تعذر الوصول إلى اسم الموقع. راجع الرابط وحاول مرة أخرى.") from None
        addresses = {ipaddress.ip_address(record[4][0]) for record in records}
    if not addresses or any(not address.is_global for address in addresses):
        raise DownloadError("هذا الرابط لا يشير إلى موقع عام، لذلك تم رفضه لأسباب أمنية.")


def _is_tiktok_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host == "tiktok.com" or host.endswith(".tiktok.com")


def _base_options(settings: Settings, url: str | None = None) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 4,
        "fragment_retries": 4,
        "extractor_retries": 3,
        "cachedir": False,
        "logger": _YtLogger(),
    }
    # TikTok is more aggressive about TLS/client fingerprinting than the other
    # extractors. Keep this isolated so YouTube and other platforms are unchanged.
    if url and _is_tiktok_url(url):
        opts["impersonate"] = "chrome"
        opts["http_headers"] = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.tiktok.com/",
            "Accept-Language": "en-US,en;q=0.9",
        }
        cookies = os.getenv("TIKTOK_COOKIES_FILE") or os.getenv("COOKIES_FILE")
        if cookies and Path(cookies).is_file():
            opts["cookiefile"] = cookies
    return opts


class _YtLogger:
    def debug(self, msg: str) -> None:
        if msg.startswith("[debug]"):
            log.debug("yt-dlp: %s", msg)

    def warning(self, msg: str) -> None:
        log.warning("yt-dlp reported a non-fatal warning")

    def error(self, msg: str) -> None:
        log.error("yt-dlp reported an extraction error")


def _extract_info_with_tiktok_fallback(url: str, opts: dict, *, download: bool):
    """Retry TikTok without curl-cffi impersonation if that layer raises an assertion."""
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=download)
    except AssertionError:
        if not _is_tiktok_url(url) or "impersonate" not in opts:
            raise
        fallback_opts = dict(opts)
        fallback_opts.pop("impersonate", None)
        log.warning("TikTok client impersonation failed; retrying with standard HTTPS headers")
        with yt_dlp.YoutubeDL(fallback_opts) as ydl:
            return ydl.extract_info(url, download=download)


def _inspect_sync(url: str, settings: Settings) -> MediaInfo:
    opts = _base_options(settings, url) | {"skip_download": True, "extract_flat": False}
    try:
        info = _extract_info_with_tiktok_fallback(url, opts, download=False)
    except Exception as exc:
        log.info("Media inspection failed for %s (%s)", "TikTok" if _is_tiktok_url(url) else "media URL", type(exc).__name__)
        if _is_tiktok_url(url):
            raise DownloadError(
                "تيك توك رفض طلب السيرفر أو لم يسمح باستخراج الفيديو. جرّب رابطًا عامًا، "
                "وإذا استمرت المشكلة أضف TIKTOK_COOKIES_FILE في Railway لملف cookies.txt صالح. "
                "قد يظل الحظر قائمًا بسبب IP السيرفر."
            ) from None
        raise DownloadError("لم أستطع قراءة الوسائط. قد يكون الرابط غير مدعوم أو خاصًا أو محميًا بتسجيل دخول.") from None
    if not isinstance(info, dict):
        raise DownloadError("لم أستطع العثور على وسائط قابلة للتنزيل في هذا الرابط.")
    duration = int(info["duration"]) if info.get("duration") else None
    if duration and duration > settings.max_duration_seconds:
        raise DownloadError(f"مدة الوسائط تتجاوز الحد المسموح ({settings.max_duration_seconds // 60} دقيقة).")
    formats = info.get("formats") or []
    heights = sorted({int(item["height"]) for item in formats if item.get("height") and item.get("vcodec") != "none"}, reverse=True)
    return MediaInfo(title=str(info.get("title") or "وسائط")[:180], duration=duration, heights=heights)


async def inspect_media(url: str, settings: Settings) -> MediaInfo:
    await validate_public_url(url)
    return await asyncio.to_thread(_inspect_sync, url, settings)


def _download_sync(url: str, mode: str, height: int | None, settings: Settings) -> DownloadedMedia:
    workdir = Path(tempfile.mkdtemp(prefix="job-", dir=settings.temp_dir))
    max_source_bytes = settings.max_source_file_mb * 1_000_000
    format_selector = "bestaudio/best" if mode == "audio" else (
        f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best[height<={height}]/best"
        if height else "bestvideo*+bestaudio/best"
    )
    opts = _base_options(settings, url) | {
        "format": format_selector,
        "outtmpl": str(workdir / "%(id)s.%(ext)s"),
        "max_filesize": max_source_bytes,
        "overwrites": True,
    }
    if mode == "audio":
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
    else:
        opts["merge_output_format"] = "mp4"
    try:
        info = _extract_info_with_tiktok_fallback(url, opts, download=True)
        title = str((info or {}).get("title") or "وسائط")[:180]
        duration = int(info["duration"]) if info and info.get("duration") else None
        candidates = [p for p in workdir.iterdir() if p.is_file() and not p.name.endswith((".part", ".ytdl"))]
        if not candidates:
            raise DownloadError("اكتمل الطلب دون إنشاء ملف قابل للإرسال.")
        path = max(candidates, key=lambda p: p.stat().st_mtime)
        if path.stat().st_size > max_source_bytes:
            raise DownloadError(
                f"حجم الملف الأصلي تجاوز الحد المسموح ({settings.max_source_file_mb} MB). اختر جودة أقل."
            )
        return DownloadedMedia(path=path, title=title, duration=duration)
    except DownloadError:
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    except Exception as exc:
        log.info("Media download failed (%s)", type(exc).__name__)
        shutil.rmtree(workdir, ignore_errors=True)
        message = str(exc).lower()
        if "ffmpeg" in message:
            raise DownloadError("تعذر تحويل الملف. تأكد من وجود FFmpeg في بيئة التشغيل.") from None
        if "file is larger" in message or "filesize" in message:
            raise DownloadError(
                f"حجم المصدر تجاوز الحد المسموح ({settings.max_source_file_mb} MB). اختر جودة أقل."
            ) from None
        raise DownloadError("فشل التنزيل. قد تكون المنصة قيّدت الطلب أو الرابط غير متاح للعامة الآن.") from None


async def download_media(url: str, mode: str, height: int | None, settings: Settings) -> DownloadedMedia:
    await validate_public_url(url)
    return await asyncio.to_thread(_download_sync, url, mode, height, settings)


def cleanup_media(path: Path) -> None:
    shutil.rmtree(path.parent, ignore_errors=True)
