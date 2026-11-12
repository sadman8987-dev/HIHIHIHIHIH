from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from mediabot.splitter import split_media

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is required for media splitting tests")


def _run_ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_large_video_is_split_into_independent_bounded_mp4_files(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    _run_ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
        "-t", "12", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-b:v", "450k",
        "-g", "20", "-c:a", "aac", "-b:a", "64k", str(source),
    )
    limit = 220_000
    assert source.stat().st_size > limit

    parts = split_media(source, "video", 12, limit)

    assert len(parts) >= 2
    assert all(part.suffix == ".mp4" and part.stat().st_size <= limit for part in parts)
    for part in parts:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(part)],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert float(probe.stdout.strip()) > 0


def test_large_mp3_is_split_into_bounded_audio_files(tmp_path: Path) -> None:
    source = tmp_path / "source.mp3"
    _run_ffmpeg(
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
        "-t", "15", "-c:a", "libmp3lame", "-b:a", "192k", str(source),
    )
    limit = 100_000
    assert source.stat().st_size > limit

    parts = split_media(source, "audio", 15, limit)

    assert len(parts) >= 2
    assert all(part.suffix == ".mp3" and part.stat().st_size <= limit for part in parts)


def test_long_gop_video_uses_bounded_reencode_fallback(tmp_path: Path) -> None:
    source = tmp_path / "long-gop.mp4"
    _run_ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=500:sample_rate=44100",
        "-t", "12", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-b:v", "800k",
        "-g", "300", "-keyint_min", "300", "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", "96k", str(source),
    )
    limit = 150_000
    assert source.stat().st_size > limit

    parts = split_media(source, "video", 12, limit)

    assert len(parts) >= 2
    assert all(part.suffix == ".mp4" and part.stat().st_size <= limit for part in parts)
