from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from .downloader import DownloadError

log = logging.getLogger(__name__)


def split_media(path: Path, mode: str, duration: int | None, max_bytes: int) -> list[Path]:
    """Return playable chunks no larger than max_bytes; keep small files intact."""
    if path.stat().st_size <= max_bytes:
        return [path]
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise DownloadError("الملف أكبر من الحد، ولا يتوفر FFmpeg لتقسيمه. ثبّت FFmpeg أو اختر جودة أقل.")
    duration = duration or _probe_duration(path)
    if not duration or duration <= 0:
        raise DownloadError("تعذر تحديد مدة الملف الكبير لتقسيمه. اختر جودة أقل أو ملفًا أقصر.")

    output_dir = path.parent / "parts"
    output_dir.mkdir(exist_ok=True)
    seconds = max(2.0, duration * max_bytes * 0.72 / path.stat().st_size)

    # Stream-copy first to preserve the selected source quality. If keyframe spacing
    # or container limitations prevent safe chunks, retry with forced keyframes.
    copied = _try_split(ffmpeg, path, output_dir, mode, seconds, max_bytes, duration, reencode=False)
    if copied:
        return copied
    encoded = _try_split(ffmpeg, path, output_dir, mode, seconds, max_bytes, duration, reencode=True)
    if encoded:
        return encoded
    raise DownloadError(
        "لم أستطع إنشاء أجزاء أصغر من الحد المسموح. جرّب جودة أقل أو فيديو أقصر."
    )


def _probe_duration(path: Path) -> int | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return max(1, round(float(result.stdout.strip())))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _try_split(
    ffmpeg: str,
    source: Path,
    output_dir: Path,
    mode: str,
    initial_seconds: float,
    max_bytes: int,
    duration: int,
    reencode: bool,
) -> list[Path] | None:
    seconds = initial_seconds
    attempts = 7 if reencode else 5
    timeout = max(300, min(1800, duration * 2))
    for _ in range(attempts):
        _clear_parts(output_dir)
        pattern = str(output_dir / "part_%03d.mp4") if mode == "video" else str(output_dir / "part_%03d.mp3")
        command = _build_command(ffmpeg, source, pattern, mode, seconds, reencode, max_bytes)
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        parts = sorted(output_dir.glob("part_*"))
        if result.returncode == 0 and parts and all(part.stat().st_size <= max_bytes for part in parts):
            return parts
        largest = max((part.stat().st_size for part in parts), default=0)
        if not parts:
            log.info("FFmpeg did not create chunks (reencode=%s)", reencode)
            return None
        if largest > max_bytes:
            ratio = max_bytes / largest
            seconds = max(1.0, seconds * min(0.72, ratio * 0.80))
        elif result.returncode != 0:
            log.info("FFmpeg chunking attempt failed (reencode=%s)", reencode)
            return None
    _clear_parts(output_dir)
    return None


def _build_command(
    ffmpeg: str,
    source: Path,
    pattern: str,
    mode: str,
    seconds: float,
    reencode: bool,
    max_bytes: int,
) -> list[str]:
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source)]
    if mode == "audio":
        command.extend(["-map", "0:a:0", "-vn"])
        if reencode:
            audio_rate = max(64_000, min(192_000, int(max_bytes * 8 * 0.65 / seconds)))
            command.extend(["-c:a", "libmp3lame", "-b:a", str(audio_rate)])
        else:
            command.extend(["-c:a", "copy"])
        command.extend([
            "-f", "segment", "-segment_time", f"{seconds:.3f}",
            "-reset_timestamps", "1", "-segment_format", "mp3", pattern,
        ])
        return command

    command.extend(["-map", "0:v:0", "-map", "0:a:0?"])
    if reencode:
        video_rate = max(128_000, int(max_bytes * 8 * 0.68 / seconds) - 128_000)
        command.extend([
            "-c:v", "libx264", "-preset", "veryfast", "-b:v", str(video_rate),
            "-maxrate", str(int(video_rate * 1.10)), "-bufsize", str(video_rate * 2),
            "-pix_fmt", "yuv420p", "-force_key_frames", f"expr:gte(t,n_forced*{seconds:.3f})",
            "-c:a", "aac", "-b:a", "128k",
        ])
    else:
        command.extend(["-c", "copy"])
    command.extend([
        "-f", "segment", "-segment_time", f"{seconds:.3f}",
        "-reset_timestamps", "1", "-segment_format", "mp4",
        "-segment_format_options", "movflags=+faststart", pattern,
    ])
    return command


def _clear_parts(output_dir: Path) -> None:
    for part in output_dir.glob("part_*"):
        if part.is_file():
            part.unlink(missing_ok=True)
