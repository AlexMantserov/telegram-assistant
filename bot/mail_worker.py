"""Периодическая проверка почты: правила из таблицы, Claude для остального, сообщения в Telegram."""

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .claude_client import ClaudeChat, classify_mail
from .mail_client import Mail, MailClient
from .rules import RulesSheet, fill_reply

log = logging.getLogger(__name__)

PREVIEW = 600  # сколько символов письма показывать в Telegram


def _preview(mail: Mail) -> str:
    text = mail.text if len(mail.text) <= PREVIEW else mail.text[:PREVIEW] + "…"
    return f"От: {mail.sender_display}\nТема: {mail.subject or '(без темы)'}\n\n{text}"


@dataclass
class Draft:
    mail: Mail
    text: str


class MailWorker:
    def __init__(
        self,
        bot: Bot,
        owner_id: int,
        client: MailClient,
        rules: RulesSheet | None,
        claude: ClaudeChat | None,
        interval_minutes: int,
        state_file: str,
        spam_by_headers: bool = True,
    ):
        self.bot = bot
        self.owner_id = owner_id
        self.client = client
        self.rules = rules
        self.claude = claude
        self.interval = interval_minutes * 60
        self.state_file = state_file
        self.spam_by_headers = spam_by_headers
        self.drafts: dict[str, Draft] = {}
        self.editing: str | None = None  # id черновика, для которого ждём новый текст
        self._lock = asyncio.Lock()

    # ---------- состояние ----------

    def _load_state(self) -> dict:
        try:
            with open(self.state_file, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_state(self, state: dict) -> None:
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        tmp = self.state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, self.state_file)

    # ---------- цикл ----------

    async def run_forever(self) -> None:
        while True:
            try:
                await self.check()
            except Exception as e:
                log.exception("Ошибка при проверке почты")
                await self._say(f"⚠️ Ошибка при проверке почты: {e}")
            await asyncio.sleep(self.interval)

    async def check(self) -> str:
        async with self._lock:
            if self.rules:
                try:
                    await asyncio.to_thread(self.rules.load)
                    if self.rules.errors:
                        await self._say("⚠️ Ошибки в таблице правил:\n" + "\n".join(self.rules.errors))
                except Exception as e:
                    log.exception("Не удалось прочитать таблицу правил")
                    await self._say(f"⚠️ Не удалось прочитать таблицу правил: {e}")

            state = self._load_state()
            mails, last_uid, validity = await asyncio.to_thread(
                self.client.fetch_new, state.get("last_uid"), state.get("uidvalidity")
            )
            first_run = state.get("last_uid") is None
            spam: list[str] = []
            notified = 0
            for mail in mails:
                try:
                    notified += await self._process(mail, spam)
                except Exception as e:
                    log.exception("Ошибка при обработке письма %s", mail.uid)
                    await self._say(f"⚠️ Не удалось обработать письмо «{mail.subject}»: {e}")
                # Сохраняем после каждого письма, чтобы при сбое не обработать его повторно.
                self._save_state({"last_uid": mail.uid, "uidvalidity": validity})
            self._save_state({"last_uid": last_uid, "uidvalidity": validity})

            if spam:
                await self._say(f"🗑 Перенесено в спам ({len(spam)}):\n" + "\n".join(spam))
            if first_run:
                return "Почта подключена. Обрабатываю только письма, пришедшие с этого момента."
            return f"Новых писем: {len(mails)}, в спам: {len(spam)}, сообщений вам: {notified}."

    async def _process(self, mail: Mail, spam: list[str]) -> int:
        """Обрабатывает одно письмо. Возвращает 1, если написал владельцу."""
        rule = self.rules.match(mail) if self.rules else None
        # Правило «пропустить» или «уведомить» важнее пометки фильтра (например, для своих клиентов).
        if self.spam_by_headers and mail.spam_flag and not (rule and rule.action != "спам"):
            await asyncio.to_thread(self.client.move_to_spam, mail.uid)
            spam.append(f"• {mail.sender} — {mail.subject} (фильтр сервера: {mail.spam_flag})")
            return 0
        if rule:
            log.info("Письмо %s: правило в строке %s (%s)", mail.uid, rule.row, rule.action)
            if rule.action == "пропустить":
                return 0
            if rule.action == "спам":
                await asyncio.to_thread(self.client.move_to_spam, mail.uid)
                spam.append(f"• {mail.sender} — {mail.subject} (правило: {rule.describe()})")
                return 0
            if rule.action == "уведомить":
                await self._say(f"📧 Письмо по правилу «{rule.describe()}»\n\n{_preview(mail)}")
                return 1
            if rule.action == "ответить":
                reply = fill_reply(rule.reply, mail)
                if rule.auto_send:
                    await asyncio.to_thread(self.client.send_reply, mail, reply)
                    await self._say(f"✉️ Ответил автоматически (правило «{rule.describe()}»)\n\n{_preview(mail)}")
                else:
                    await self._offer_draft(mail, reply, f"правило «{rule.describe()}»")
                return 1

        if self.claude:
            result = await classify_mail(self.claude, mail.sender_display, mail.subject, mail.text)
            category, summary = result["category"], result["summary"]
            log.info("Письмо %s: Claude → %s", mail.uid, category)
            if category == "spam":
                await asyncio.to_thread(self.client.move_to_spam, mail.uid)
                spam.append(f"• {mail.sender} — {mail.subject} (решил Claude)")
                return 0
            if category == "important":
                await self._say(f"❗️ Важное письмо (по мнению Claude)\n{summary}\n\n{_preview(mail)}")
                return 1
        return 0

    # ---------- черновики ответов ----------

    def _draft_keyboard(self, draft_id: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Отправить", callback_data=f"mail:send:{draft_id}"),
                    InlineKeyboardButton(text="✏️ Изменить", callback_data=f"mail:edit:{draft_id}"),
                    InlineKeyboardButton(text="✖️ Пропустить", callback_data=f"mail:skip:{draft_id}"),
                ]
            ]
        )

    async def _offer_draft(self, mail: Mail, reply: str, reason: str) -> None:
        draft_id = uuid.uuid4().hex[:10]
        self.drafts[draft_id] = Draft(mail, reply)
        await self.bot.send_message(
            self.owner_id,
            f"📝 Черновик ответа ({reason})\n\n{_preview(mail)}\n\n— Ответ —\n{reply}",
            reply_markup=self._draft_keyboard(draft_id),
        )

    async def _say(self, text: str) -> None:
        for i in range(0, len(text), 4000):
            await self.bot.send_message(self.owner_id, text[i : i + 4000])

    def router(self) -> Router:
        router = Router()
        router.message.filter(F.from_user.id == self.owner_id)
        router.callback_query.filter(F.from_user.id == self.owner_id)

        @router.callback_query(F.data.startswith("mail:"))
        async def on_button(query: CallbackQuery) -> None:
            _, action, draft_id = query.data.split(":", 2)
            draft = self.drafts.get(draft_id)
            if not draft:
                await query.answer("Черновик устарел (бот перезапускался).", show_alert=True)
                return
            if action == "send":
                try:
                    await asyncio.to_thread(self.client.send_reply, draft.mail, draft.text)
                except Exception as e:
                    log.exception("Не удалось отправить ответ")
                    await query.answer(f"Ошибка отправки: {e}", show_alert=True)
                    return
                self.drafts.pop(draft_id, None)
                await query.message.edit_reply_markup(reply_markup=None)
                await query.message.reply(f"✅ Ответ отправлен на {draft.mail.sender}")
            elif action == "skip":
                self.drafts.pop(draft_id, None)
                await query.message.edit_reply_markup(reply_markup=None)
                await query.message.reply("Пропущено, ответ не отправлен.")
            elif action == "edit":
                self.editing = draft_id
                await query.message.reply("Пришлите новый текст ответа одним сообщением.")
            await query.answer()

        @router.message(Command("mail"))
        async def cmd_mail(message: Message) -> None:
            await message.answer("Проверяю почту…")
            try:
                await message.answer(await self.check())
            except Exception as e:
                log.exception("Ошибка при ручной проверке почты")
                await message.answer(f"⚠️ Ошибка: {e}")

        @router.message(Command("lastmail"))
        async def cmd_lastmail(message: Message) -> None:
            # «/lastmail спам» — показать письма из папки «Спам»
            in_spam = any(w in (message.text or "").lower() for w in ("спам", "spam"))
            try:
                if in_spam:
                    folder = await asyncio.to_thread(self.client.spam_folder)
                    if not folder:
                        await message.answer("Папка «Спам» не найдена.")
                        return
                    mails = await asyncio.to_thread(self.client.recent, 10, folder)
                    title = "Последние 10 писем в папке «Спам»:"
                else:
                    mails = await asyncio.to_thread(self.client.recent, 5)
                    title = "Последние 5 писем во «Входящих» и что о них думает спам-фильтр сервера:"
            except Exception as e:
                await message.answer(f"⚠️ Ошибка: {e}")
                return
            lines = [title]
            for m in mails:
                rule = self.rules.match(m) if self.rules else None
                verdict = "СПАМ по заголовкам" if m.spam_flag else "не помечено"
                lines.append(
                    f"\n• {m.sender} — {m.subject or '(без темы)'}\n"
                    f"  фильтр: {verdict}\n"
                    f"  заголовки: {m.spam_headers or 'нет'}\n"
                    f"  правило: {rule.action + ' (' + rule.describe() + ')' if rule else 'нет'}"
                )
            await self._say("\n".join(lines))

        @router.message(Command("folders"))
        async def cmd_folders(message: Message) -> None:
            try:
                names = await asyncio.to_thread(self.client.folder_names)
                spam = await asyncio.to_thread(self.client.spam_folder)
            except Exception as e:
                await message.answer(f"⚠️ Ошибка: {e}")
                return
            from .mail_client import utf7_decode

            await message.answer(
                "Папки ящика:\n" + "\n".join(f"• {n}" for n in names)
                + f"\n\nПапка для спама: {utf7_decode(spam) if spam else 'не найдена'}"
            )

        @router.message(Command("rules"))
        async def cmd_rules(message: Message) -> None:
            if not self.rules:
                await message.answer("Таблица правил не подключена (RULES_SHEET_ID в .env).")
                return
            try:
                rules = await asyncio.to_thread(self.rules.load)
            except Exception as e:
                await message.answer(
                    f"⚠️ Не удалось открыть таблицу: {e}\n\n"
                    f"Откройте доступ «Редактор» для {self.rules.service_email()}"
                )
                return
            lines = [f"Таблица: {self.rules.url}", f"Лист: {self.rules._sheet_name}", f"Включённых правил: {len(rules)}"]
            lines += [f"• строка {r.row}: {r.action} — {r.describe()}" for r in rules]
            if self.rules.errors:
                lines += ["", "Ошибки:"] + self.rules.errors
            await message.answer("\n".join(lines), disable_web_page_preview=True)

        @router.message(F.text, lambda m: self.editing is not None and not m.text.startswith("/"))
        async def on_edit_text(message: Message) -> None:
            draft = self.drafts.get(self.editing or "")
            draft_id, self.editing = self.editing, None
            if not draft:
                await message.answer("Черновик устарел.")
                return
            draft.text = message.text
            await message.answer(
                f"Новый текст ответа на «{draft.mail.subject}»:\n\n{draft.text}",
                reply_markup=self._draft_keyboard(draft_id),
            )

        return router

