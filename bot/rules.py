"""Правила обработки писем из Google Таблицы.

Формат листа (первая строка — заголовки):
  Вкл | От кого содержит | Тема содержит | Текст содержит | Действие | Ответ | Отправлять сразу

Условия: несколько вариантов через «;» (подходит любой). Все заполненные условия
в строке должны выполняться. Пустые условия не проверяются, но хотя бы одно нужно.
Действия: уведомить, спам, ответить, пропустить. Правила проверяются сверху вниз,
срабатывает первое подходящее.
"""

import logging
import re
from dataclasses import dataclass

import gspread

from .mail_client import Mail

log = logging.getLogger(__name__)

HEADER = [
    "Вкл",
    "От кого содержит",
    "Тема содержит",
    "Текст содержит",
    "Действие",
    "Ответ",
    "Отправлять сразу",
    "Комментарий",
]

EXAMPLES = [
    ["нет", "@важный-клиент.ru", "", "", "уведомить", "", "", "Пример: письма от клиента — сразу в Telegram"],
    ["нет", "", "заказ; счёт; оплата", "", "уведомить", "", "", "Пример: слова в теме"],
    ["нет", "", "", "казино; выигрыш; viagra", "спам", "", "", "Пример: спам по словам в тексте"],
    [
        "нет",
        "",
        "прайс; цены",
        "",
        "ответить",
        "Здравствуйте, {имя}! Прайс-лист во вложении на сайте: https://... С уважением, ...",
        "нет",
        "Пример: черновик ответа придёт в Telegram на подтверждение",
    ],
]

ACTIONS = {"уведомить", "спам", "ответить", "пропустить"}
YES = {"да", "yes", "1", "+", "true", "вкл"}


def _variants(cell: str) -> list[str]:
    return [v.strip().lower() for v in cell.replace("\n", ";").split(";") if v.strip()]


@dataclass
class Rule:
    row: int
    senders: list[str]
    subjects: list[str]
    texts: list[str]
    action: str
    reply: str
    auto_send: bool
    comment: str

    def matches(self, mail: Mail) -> bool:
        checks = [
            (self.senders, f"{mail.sender} {mail.sender_name}".lower()),
            (self.subjects, mail.subject.lower()),
            (self.texts, mail.text.lower()),
        ]
        used = [(variants, value) for variants, value in checks if variants]
        return bool(used) and all(any(v in value for v in variants) for variants, value in used)

    def describe(self) -> str:
        return self.comment or f"строка {self.row}"


class RulesSheet:
    def __init__(self, key_file: str, sheet_id: str):
        self._key_file = key_file
        self._sheet_id = sheet_id
        self.rules: list[Rule] = []
        self.errors: list[str] = []

    @property
    def url(self) -> str:
        return f"https://docs.google.com/spreadsheets/d/{self._sheet_id}"

    def service_email(self) -> str:
        import json

        with open(self._key_file, encoding="utf-8") as f:
            return json.load(f).get("client_email", "")

    def load(self) -> list[Rule]:
        """Читает правила из таблицы (синхронно). Пустой лист заполняет шаблоном."""
        ws = gspread.service_account(filename=self._key_file).open_by_key(self._sheet_id).sheet1
        values = ws.get_all_values()
        if not any(any(c.strip() for c in row) for row in values):
            ws.update([HEADER] + EXAMPLES, "A1")
            ws.format("A1:H1", {"textFormat": {"bold": True}})
            ws.freeze(rows=1)
            values = [HEADER] + EXAMPLES

        rules, errors = [], []
        for i, row in enumerate(values[1:], start=2):
            row = (row + [""] * len(HEADER))[: len(HEADER)]
            enabled, sender, subject, text, action, reply, auto, comment = (c.strip() for c in row)
            if not any(row) or enabled.lower() not in YES:
                continue
            action = action.lower()
            if action not in ACTIONS:
                errors.append(f"строка {i}: непонятное действие «{action}»")
                continue
            if not (sender or subject or text):
                errors.append(f"строка {i}: не заполнено ни одно условие")
                continue
            if action == "ответить" and not reply:
                errors.append(f"строка {i}: для «ответить» нужен текст в столбце «Ответ»")
                continue
            rules.append(
                Rule(
                    row=i,
                    senders=_variants(sender),
                    subjects=_variants(subject),
                    texts=_variants(text),
                    action=action,
                    reply=reply,
                    auto_send=auto.lower() in YES,
                    comment=comment,
                )
            )
        self.rules, self.errors = rules, errors
        return rules

    def match(self, mail: Mail) -> Rule | None:
        return next((r for r in self.rules if r.matches(mail)), None)


def fill_reply(template: str, mail: Mail) -> str:
    """Подставляет {имя} и {тема}. Если имени нет, убирает «, {имя}»."""
    text = template.replace("{тема}", mail.subject)
    name = mail.sender_name.split()[0] if mail.sender_name else ""
    if name:
        return text.replace("{имя}", name)
    return re.sub(r",?\s*\{имя\}", "", text)
