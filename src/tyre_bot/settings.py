from __future__ import annotations

from datetime import time
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки из окружения и .env с префиксом TYRE_BOT_ (пример — .env.example)."""

    model_config = SettingsConfigDict(env_prefix="TYRE_BOT_", env_file=".env", extra="ignore")

    max_token: str = ""
    admin_chat_id: int | None = None  # чат мастеров: туда приходят новые записи и отмены
    database: Path = Path("data/bookings.sqlite3")
    posts: int = 2  # сколько машин обслуживается одновременно
    opens: time = time(9, 0)
    closes: time = time(20, 0)
    days_ahead: int = 7
    timezone: str = "Europe/Moscow"
