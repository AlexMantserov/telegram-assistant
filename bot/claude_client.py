"""Обёртка над Claude API: хранит короткую историю диалога и возвращает текст ответа."""

import json
import logging

import anthropic
import httpx2

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Ты личный ассистент владельца этого Telegram-бота. Отвечай по-русски, "
    "коротко и по делу. Ответ уходит в Telegram как обычный текст, поэтому "
    "не используй Markdown-разметку (звёздочки, решётки, таблицы)."
)


class ClaudeChat:
    def __init__(
        self,
        api_key: str,
        model: str,
        effort: str,
        history_limit: int,
        proxy: str | None = None,
    ):
        http_client = None
        if proxy:
            # Параметр proxy= у клиента SDK не работает, поэтому подключаем прокси через mounts.
            transport = httpx2.AsyncHTTPTransport(proxy=proxy)
            http_client = anthropic.DefaultAsyncHttpxClient(
                mounts={"http://": transport, "https://": transport}
            )
        self._client = anthropic.AsyncAnthropic(api_key=api_key, http_client=http_client)
        self._model = model
        self._effort = effort
        self._history_limit = history_limit
        self._history: list[dict] = []

    def reset(self) -> None:
        self._history.clear()

    async def ask(self, text: str) -> str:
        # История только дописывается (так требует API для блоков размышлений).
        # Когда она становится слишком длинной, начинаем новый диалог с чистого листа.
        if len(self._history) >= self._history_limit:
            self._history.clear()
        messages = self._history + [{"role": "user", "content": text}]
        response = await self._client.beta.messages.create(
            model=self._model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=messages,
            output_config={"effort": self._effort},
            # Если модель откажется отвечать, API сам повторит запрос на запасной модели.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )

        if response.stop_reason == "refusal":
            return "Claude отказался отвечать на этот запрос."

        answer = "".join(b.text for b in response.content if b.type == "text").strip()

        # Сохраняем ответ целиком (включая служебные блоки), чтобы следующий запрос
        # продолжал диалог без искажений истории.
        self._history = messages + [
            {
                "role": "assistant",
                "content": [b.model_dump(exclude_none=True) for b in response.content],
            }
        ]
        return answer or "(пустой ответ)"


MAIL_SYSTEM = (
    "Ты разбираешь входящую почту владельца небольшого бизнеса. Определи категорию письма: "
    "spam — реклама, рассылки, мошенничество, письма, которые владелец не ждёт; "
    "important — письма от людей, которые ждут ответа или действия (клиенты, партнёры, "
    "счета, заказы, проблемы); normal — всё остальное (уведомления сервисов, новости). "
    "В summary в одном-двух предложениях по-русски напиши, о чём письмо и что от владельца хотят."
)

MAIL_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": ["spam", "important", "normal"]},
        "summary": {"type": "string"},
    },
    "required": ["category", "summary"],
    "additionalProperties": False,
}


async def classify_mail(chat: "ClaudeChat", sender: str, subject: str, text: str) -> dict:
    """Категория письма от Claude: {"category": spam|important|normal, "summary": "..."}."""
    response = await chat._client.beta.messages.create(
        model=chat._model,
        max_tokens=2000,
        system=MAIL_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": f"От: {sender}\nТема: {subject}\n\n{text[:3000]}",
            }
        ],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": MAIL_SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        return {"category": "normal", "summary": "Claude отказался разбирать это письмо."}
    raw = next(b.text for b in response.content if b.type == "text")
    return json.loads(raw)
