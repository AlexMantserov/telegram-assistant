"""Настройки бота. Все секреты читаются из переменных окружения (файл .env)."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Не задана переменная окружения {name} (см. .env.example)")
    return value


def _optional(name: str) -> str | None:
    return os.getenv(name, "").strip() or None


def _sheet_id(value: str | None) -> str | None:
    """Принимает и ID таблицы, и полную ссылку на неё."""
    if value and "/d/" in value:
        return value.split("/d/", 1)[1].split("/", 1)[0]
    return value


def _spam_notify(value: str) -> str:
    """нет — ничего не писать; количество — только число; список (или да) — каждое письмо."""
    value = value.strip().lower()
    if value in ("нет", "no", "0", "false"):
        return "нет"
    if value in ("список", "да", "yes", "1", "true"):
        return "список"
    return "количество"


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
    mail_address: str | None
    mail_password: str | None
    mail_imap_host: str
    mail_imap_port: int
    mail_smtp_host: str
    mail_smtp_port: int
    mail_spam_folder: str | None
    mail_check_minutes: int
    rules_sheet_id: str | None
    rules_sheet_name: str
    mail_spam_by_headers: bool
    mail_spam_bar_min: int
    mail_spam_notify: str
    google_key_file: str
    data_dir: str


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
        mail_address=_optional("MAIL_ADDRESS"),
        mail_password=_optional("MAIL_PASSWORD"),
        mail_imap_host=os.getenv("MAIL_IMAP_HOST", "mail.hosting.reg.ru"),
        mail_imap_port=int(os.getenv("MAIL_IMAP_PORT", "993")),
        mail_smtp_host=os.getenv("MAIL_SMTP_HOST", "mail.hosting.reg.ru"),
        mail_smtp_port=int(os.getenv("MAIL_SMTP_PORT", "465")),
        mail_spam_folder=_optional("MAIL_SPAM_FOLDER"),
        mail_spam_by_headers=os.getenv("MAIL_SPAM_BY_HEADERS", "да").strip().lower() in ("да", "yes", "1", "true"),
        mail_spam_notify=_spam_notify(os.getenv("MAIL_SPAM_NOTIFY", "")),
        mail_spam_bar_min=int(os.getenv("MAIL_SPAM_BAR_MIN", "").strip() or 0),
        mail_check_minutes=int(os.getenv("MAIL_CHECK_MINUTES", "30")),
        rules_sheet_id=_sheet_id(_optional("RULES_SHEET_ID")),
        rules_sheet_name=os.getenv("RULES_SHEET_NAME", "").strip() or "Правила для почты",
        google_key_file=os.getenv("GOOGLE_KEY_FILE", os.path.join(ROOT, "google-key.json")),
        data_dir=os.getenv("DATA_DIR", os.path.join(ROOT, "data")),
    )
