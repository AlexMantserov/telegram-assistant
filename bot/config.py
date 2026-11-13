"""Настройки бота. Все секреты читаются из переменных окружения (файл .env)."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Не задана переменная окружения {name} (см. .env.example)")
    return value


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    owner_id: int
    anthropic_api_key: str
    claude_model: str
    claude_effort: str
    history_limit: int


def load_settings() -> Settings:
    return Settings(
        telegram_token=_require("TELEGRAM_BOT_TOKEN"),
        owner_id=int(_require("OWNER_TELEGRAM_ID")),
        anthropic_api_key=_require("ANTHROPIC_API_KEY"),
        claude_model=os.getenv("CLAUDE_MODEL", "claude-opus-5-5"),
        claude_effort=os.getenv("CLAUDE_EFFORT", "medium"),
        history_limit=int(os.getenv("HISTORY_LIMIT", "20")),
    )
