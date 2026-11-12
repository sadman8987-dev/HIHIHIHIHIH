from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _csv_ints(value: str) -> tuple[int, ...]:
    result: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if part:
            try:
                result.append(int(part))
            except ValueError as exc:
                raise ValueError(f"Expected comma-separated integer IDs; got {part!r}") from exc
    return tuple(result)


def _positive_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    owner_ids: tuple[int, ...]
    max_file_mb: int
    max_source_file_mb: int
    max_duration_seconds: int
    max_concurrent_downloads: int
    user_cooldown_seconds: int
    data_dir: Path
    temp_dir: Path
    log_level: str

    @classmethod
    def from_env(cls) -> Settings:
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise ValueError("BOT_TOKEN is required. Create a bot with @BotFather first.")
        data_dir = Path(os.getenv("DATA_DIR", "/data"))
        temp_dir = Path(os.getenv("TEMP_DIR", "/tmp/media-fetch-bot"))
        data_dir.mkdir(parents=True, exist_ok=True)
        temp_dir.mkdir(parents=True, exist_ok=True)
        max_file_mb = _positive_int("MAX_FILE_MB", 48)
        if max_file_mb > 50:
            raise ValueError("MAX_FILE_MB must not exceed 50 MB with the standard Telegram Bot API")
        max_source_file_mb = _positive_int("MAX_SOURCE_FILE_MB", 256)
        if max_source_file_mb < max_file_mb:
            raise ValueError("MAX_SOURCE_FILE_MB must be at least MAX_FILE_MB")
        return cls(
            bot_token=token,
            owner_ids=_csv_ints(os.getenv("OWNER_IDS", "")),
            max_file_mb=max_file_mb,
            max_source_file_mb=max_source_file_mb,
            max_duration_seconds=_positive_int("MAX_DURATION_SECONDS", 86400),
            max_concurrent_downloads=_positive_int("MAX_CONCURRENT_DOWNLOADS", 1),
            user_cooldown_seconds=_positive_int("USER_COOLDOWN_SECONDS", 8, minimum=0),
            data_dir=data_dir,
            temp_dir=temp_dir,
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
