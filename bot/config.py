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


def _proxy(name: str) -> str | None:
    value = (os.getenv(name) or os.getenv("PROXY_URL") or "").strip()
    # "none" позволяет отключить прокси для одного сервиса при заданном PROXY_URL
    return None if value.lower() in ("", "none") else value


def _scheme(url: str | None, old: str, new: str) -> str | None:
    if url and url.lower().startswith(old):
        return new + url[len(old):]
    return url


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    owner_id: int
    anthropic_api_key: str | None
    claude_model: str
    claude_effort: str
    history_limit: int
    telegram_proxy: str | None
    anthropic_proxy: str | None


def load_settings() -> Settings:
    return Settings(
        telegram_token=_require("TELEGRAM_BOT_TOKEN"),
        owner_id=int(_require("OWNER_TELEGRAM_ID")),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "").strip() or None,
        claude_model=os.getenv("CLAUDE_MODEL", "claude-opus-5-5"),
        claude_effort=os.getenv("CLAUDE_EFFORT", "medium"),
        history_limit=int(os.getenv("HISTORY_LIMIT", "20")),
        # PROXY_URL — общий прокси; TELEGRAM_PROXY / ANTHROPIC_PROXY переопределяют его.
        # Адреса сайтов должен определять сам прокси (удалённый DNS), иначе запросы с
        # российского сервера не проходят. aiogram передаёт имя хоста прокси сам, но
        # понимает только socks5://, а клиенту Claude нужен явный socks5h://.
        telegram_proxy=_scheme(_proxy("TELEGRAM_PROXY"), "socks5h://", "socks5://"),
        anthropic_proxy=_scheme(_proxy("ANTHROPIC_PROXY"), "socks5://", "socks5h://"),
    )
