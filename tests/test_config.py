from __future__ import annotations

import pytest

from mediabot.config import Settings


def test_reads_configuration_and_creates_directories(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("OWNER_IDS", "42, 99")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TEMP_DIR", str(tmp_path / "tmp"))
    settings = Settings.from_env()
    assert settings.owner_ids == (42, 99)
    assert settings.max_file_mb == 48
    assert settings.max_source_file_mb == 256
    assert settings.max_concurrent_downloads == 1
    assert settings.data_dir.is_dir()
    assert settings.temp_dir.is_dir()


def test_bot_token_is_required(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    with pytest.raises(ValueError, match="BOT_TOKEN"):
        Settings.from_env()


def test_invalid_owner_ids_are_rejected(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("OWNER_IDS", "not-an-id")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    with pytest.raises(ValueError, match="integer IDs"):
        Settings.from_env()


def test_file_size_cannot_exceed_telegram_limit(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("MAX_FILE_MB", "51")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    with pytest.raises(ValueError, match="must not exceed 50"):
        Settings.from_env()


def test_source_limit_must_cover_one_upload_part(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("MAX_FILE_MB", "48")
    monkeypatch.setenv("MAX_SOURCE_FILE_MB", "40")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    with pytest.raises(ValueError, match="at least MAX_FILE_MB"):
        Settings.from_env()
