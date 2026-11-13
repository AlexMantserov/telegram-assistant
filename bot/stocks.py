"""Утренние идеи по акциям Мосбиржи: Claude анализирует новости, цены берутся с MOEX."""

import asyncio
import datetime as dt
import logging
from zoneinfo import ZoneInfo

import aiohttp
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import Message

from .claude_client import ClaudeChat

log = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")

SOURCES = ["https://www.rbc.ru/", "https://www.finam.ru/", "https://yandex.ru/finance"]

SYSTEM = (
    "Ты аналитик российского фондового рынка. Готовишь для владельца короткую утреннюю "
    "подборку идей по акциям, торгующимся на Московской бирже (режим TQBR). "
    "Перед выводами обязательно изучи свежие новости и аналитику минимум из трёх источников; "
    "среди них должны быть РБК, Финам и Яндекс Финансы (используй веб-поиск и загрузку страниц). "
    "Опирайся на события последних дней: отчётность, дивиденды, решения ЦБ, отраслевые новости, "
    "прогнозы аналитиков. Не выдумывай факты и цифры: если данных нет, не включай бумагу. "
    "Цены не указывай, их подставит программа с биржи. "
    "Когда закончишь исследование, вызови инструмент submit_ideas ровно один раз."
)

IDEA_SCHEMA = {
    "type": "object",
    "properties": {
        "ideas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Тикер на Мосбирже, например SBER"},
                    "name": {"type": "string", "description": "Название компании по-русски"},
                    "growth_percent": {"type": "number", "description": "Ожидаемый рост, %"},
                    "horizon": {"type": "string", "description": "Срок, например «3 месяца»"},
                    "reason": {"type": "string", "description": "Почему, 1–2 предложения"},
                    "sources": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Ссылки на материалы, на которые опирается идея",
                    },
                },
                "required": ["ticker", "name", "growth_percent", "horizon", "reason", "sources"],
                "additionalProperties": False,
            },
        },
        "market_summary": {"type": "string", "description": "Обстановка на рынке, 1–2 предложения"},
    },
    "required": ["ideas", "market_summary"],
    "additionalProperties": False,
}

TOOLS = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 15},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 15},
    {
        "name": "submit_ideas",
        "description": "Отправить итоговую подборку идей владельцу.",
        "strict": True,
        "eager_input_streaming": True,
        "input_schema": IDEA_SCHEMA,
    },
]


def _valid(data: object) -> bool:
    if not isinstance(data, dict) or not isinstance(data.get("ideas"), list):
        return False
    keys = {"ticker", "name", "growth_percent", "horizon", "reason", "sources"}
    return all(isinstance(i, dict) and keys <= i.keys() for i in data["ideas"])


async def research(chat: ClaudeChat, count: int) -> dict:
    """Просит Claude изучить новости и вернуть идеи (через инструмент submit_ideas)."""
    today = dt.datetime.now(MSK).strftime("%d.%m.%Y")
    messages: list[dict] = [
        {
            "role": "user",
            "content": (
                f"Сегодня {today}. Подбери {count} акций Мосбиржи для покупки. "
                f"Обязательные источники: {', '.join(SOURCES)}. "
                "Для каждой бумаги дай ожидаемый рост в % и срок, на который её стоит купить."
            ),
        }
    ]
    for _ in range(8):  # исследование может прерываться (pause_turn) — продолжаем
        async with chat._client.beta.messages.stream(
            model=chat._model,
            max_tokens=32000,
            system=SYSTEM,
            messages=messages,
            tools=TOOLS,
            output_config={"effort": "high"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            response = await stream.get_final_message()

        if response.stop_reason == "refusal":
            raise RuntimeError("Claude отказался готовить подборку.")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("Ответ Claude оборвался (max_tokens).")

        for block in response.content:
            if block.type == "tool_use" and block.name == "submit_ideas":
                if not _valid(block.input):
                    raise RuntimeError("Claude вернул подборку в неверном формате.")
                return block.input

        content = [b.model_dump(exclude_none=True) for b in response.content]
        if response.stop_reason == "pause_turn":
            # Сервер прервал длинное исследование: отправляем ход обратно, он продолжит сам.
            if messages[-1]["role"] == "assistant":
                messages[-1]["content"] = messages[-1]["content"] + content
            else:
                messages = messages + [{"role": "assistant", "content": content}]
            continue
        if messages[-1]["role"] == "assistant":
            content = messages[-1]["content"] + content
            messages = messages[:-1]
        # Закончил без вызова инструмента — напоминаем один раз.
        messages = messages + [
            {"role": "assistant", "content": content},
            {"role": "user", "content": "Оформи результат вызовом submit_ideas."},
        ]
    raise RuntimeError("Claude не завершил подборку.")


async def moex_prices(tickers: list[str]) -> dict[str, float]:
    """Текущие цены акций (TQBR) с Мосбиржи. Если торгов ещё нет — цена закрытия."""
    if not tickers:
        return {}
    url = "https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities.json"
    params = {
        "securities": ",".join(tickers),
        "iss.meta": "off",
        "iss.only": "securities,marketdata",
        "securities.columns": "SECID,PREVPRICE",
        "marketdata.columns": "SECID,LAST,MARKETPRICE",
    }
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
        async with s.get(url, params=params) as r:
            r.raise_for_status()
            data = await r.json()

    def rows(table: str) -> list[dict]:
        cols = data[table]["columns"]
        return [dict(zip(cols, row)) for row in data[table]["data"]]

    prev = {r["SECID"]: r.get("PREVPRICE") for r in rows("securities")}
    prices: dict[str, float] = {}
    for r in rows("marketdata"):
        price = r.get("LAST") or r.get("MARKETPRICE") or prev.get(r["SECID"])
        if price:
            prices[r["SECID"]] = float(price)
    for secid, p in prev.items():
        if p and secid not in prices:
            prices[secid] = float(p)
    return prices


def _money(x: float) -> str:
    return f"{x:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def format_ideas(result: dict, prices: dict[str, float]) -> str:
    today = dt.datetime.now(MSK).strftime("%d.%m.%Y")
    lines = [f"📈 Идеи по акциям на {today}", "", result.get("market_summary", "").strip(), ""]
    n = 0
    for idea in result["ideas"]:
        ticker = idea["ticker"].strip().upper()
        price = prices.get(ticker)
        if price is None:
            log.warning("Тикер %s не найден на Мосбирже, пропускаю", ticker)
            continue
        n += 1
        growth = idea["growth_percent"]
        target = price * (1 + growth / 100)
        lines.append(f"{n}. {idea['name']} ({ticker}) — {_money(price)}")
        lines.append(f"   Прогноз: {growth:+.0f}% за {idea['horizon']} (цель ≈ {_money(target)})")
        lines.append(f"   Почему: {idea['reason'].strip()}")
        if idea["sources"]:
            lines.append("   Источники: " + " ".join(idea["sources"][:3]))
        lines.append("")
    if not n:
        lines.append("Не удалось подтвердить ни одного тикера на Мосбирже.")
    lines.append(
        "⚠️ Подборку готовит ИИ по открытым новостям. Это не индивидуальная инвестиционная "
        "рекомендация: прогнозы могут не сбыться, решения принимайте сами."
    )
    return "\n".join(lines)


class StocksDigest:
    def __init__(self, bot: Bot, owner_id: int, chat: ClaudeChat | None, at: str, count: int):
        self.bot = bot
        self.owner_id = owner_id
        self.chat = chat
        self.hour, self.minute = (int(x) for x in at.split(":"))
        self.count = count
        self._lock = asyncio.Lock()

    async def build(self) -> str:
        if not self.chat:
            return "Для подборки по акциям нужен ключ Anthropic (ANTHROPIC_API_KEY в .env)."
        async with self._lock:
            result = await research(self.chat, self.count)
            tickers = [i["ticker"].strip().upper() for i in result["ideas"]]
            prices = await moex_prices(tickers)
            return format_ideas(result, prices)

    async def send(self) -> None:
        try:
            text = await self.build()
        except Exception as e:
            log.exception("Не удалось подготовить подборку по акциям")
            text = f"⚠️ Не удалось подготовить подборку по акциям: {e}"
        for i in range(0, len(text), 4000):
            await self.bot.send_message(self.owner_id, text[i : i + 4000], disable_web_page_preview=True)

    def _next_run(self, now: dt.datetime) -> dt.datetime:
        run = now.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
        if run <= now:
            run += dt.timedelta(days=1)
        while run.weekday() >= 5:  # суббота, воскресенье
            run += dt.timedelta(days=1)
        return run

    async def run_forever(self) -> None:
        while True:
            now = dt.datetime.now(MSK)
            run = self._next_run(now)
            log.info("Следующая подборка по акциям: %s", run.strftime("%d.%m %H:%M"))
            await asyncio.sleep((run - now).total_seconds())
            await self.send()

    def router(self) -> Router:
        router = Router()
        router.message.filter(F.from_user.id == self.owner_id)

        @router.message(Command("stocks"))
        async def cmd_stocks(message: Message) -> None:
            await message.answer("Готовлю подборку, это займёт несколько минут…")
            await self.send()

        return router
