"""Точка входа: Telegram-бот, который отвечает только владельцу и передаёт запросы в Claude."""

import asyncio
import logging

import anthropic
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ChatAction
from aiogram.filters import Command
from aiogram.types import Message

from .claude_client import ClaudeChat
from .config import load_settings

TELEGRAM_LIMIT = 4000  # у Telegram лимит 4096 символов на сообщение

log = logging.getLogger("bot")


def split_text(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        parts.append(text)
    return parts


def build_router(owner_id: int, chat: ClaudeChat | None) -> Router:
    router = Router()
    # Бот реагирует только на сообщения владельца, остальных молча игнорирует.
    router.message.filter(F.from_user.id == owner_id)

    @router.message(Command("start", "help"))
    async def cmd_start(message: Message) -> None:
        await message.answer(
            "Привет! Я ваш помощник.\n\n"
            "Просто напишите вопрос или задачу, и я передам её Claude.\n"
            "/reset — начать диалог заново\n"
            "/ping — проверить, что бот работает"
        )

    @router.message(Command("ping"))
    async def cmd_ping(message: Message) -> None:
        await message.answer("pong ✅")

    @router.message(Command("reset"))
    async def cmd_reset(message: Message) -> None:
        if chat:
            chat.reset()
        await message.answer("История диалога очищена.")

    @router.message(F.text)
    async def on_text(message: Message, bot: Bot) -> None:
        if chat is None:
            await message.answer(
                "Ключ Anthropic не задан (ANTHROPIC_API_KEY в .env), поэтому Claude пока недоступен. "
                "Связь с Telegram работает ✅"
            )
            return
        await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        try:
            answer = await chat.ask(message.text)
        except anthropic.RateLimitError:
            answer = "Claude сейчас перегружен (лимит запросов). Попробуйте через минуту."
        except anthropic.PermissionDeniedError:
            log.exception("Claude API: доступ запрещён")
            answer = (
                "Anthropic отклонил запрос (403). Обычно это значит, что запрос пришёл "
                "из неподдерживаемой страны: нужен зарубежный прокси в ANTHROPIC_PROXY."
            )
        except anthropic.APIStatusError as e:
            log.exception("Ошибка Claude API")
            answer = f"Ошибка Claude API ({e.status_code}). Подробности в логах сервера."
        except anthropic.APIConnectionError:
            log.exception("Нет связи с Claude API")
            answer = "Не удалось связаться с Claude. Проверьте интернет на сервере."
        for part in split_text(answer):
            await message.answer(part)

    return router


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = load_settings()
    chat = None
    if settings.anthropic_api_key:
        chat = ClaudeChat(
            api_key=settings.anthropic_api_key,
            model=settings.claude_model,
            effort=settings.claude_effort,
            history_limit=settings.history_limit,
            proxy=settings.anthropic_proxy,
        )
    else:
        log.warning("ANTHROPIC_API_KEY не задан: бот работает без Claude")
    session = AiohttpSession(proxy=settings.telegram_proxy) if settings.telegram_proxy else None
    bot = Bot(token=settings.telegram_token, session=session)
    dp = Dispatcher()
    dp.include_router(build_router(settings.owner_id, chat))

    me = await bot.get_me()
    log.info("Бот @%s запущен, владелец: %s", me.username, settings.owner_id)
    status = "Бот запущен ✅" if chat else "Бот запущен ✅ (без Claude: не задан ключ Anthropic)"
    await bot.send_message(settings.owner_id, status)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
