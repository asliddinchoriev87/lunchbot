from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    database_path: str
    timezone: str
    reminder_times: tuple[str, ...]
    openai_api_key: str | None
    openai_model: str
    payment_recipients: tuple[str, ...]
    log_level: str

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

        recipients = tuple(
            item.strip()
            for item in os.getenv("PAYMENT_RECIPIENT_NAMES", "").split(",")
            if item.strip()
        )
        reminder_times = tuple(
            item.strip()
            for item in os.getenv("REMINDER_TIMES", "10:20,10:35,10:50").split(",")
            if item.strip()
        )
        return cls(
            telegram_bot_token=token,
            database_path=os.getenv("DATABASE_PATH", "data/lunchbot.db"),
            timezone=os.getenv("TIMEZONE", "Asia/Tashkent"),
            reminder_times=reminder_times,
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5-mini"),
            payment_recipients=recipients,
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
