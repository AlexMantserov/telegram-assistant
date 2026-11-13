"""Работа с почтовым ящиком по IMAP (чтение, перенос в спам) и SMTP (ответы).

Все функции синхронные: вызывайте их через asyncio.to_thread.
"""

import email
import imaplib
import logging
import re
import smtplib
import ssl
import time
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

log = logging.getLogger(__name__)

TEXT_LIMIT = 4000  # сколько символов текста письма храним

# Названия папок в IMAP закодированы в modified UTF-7: "&BCEEPwQwBDw-" — это «Спам».
SPAM_NAMES = ("spam", "junk", "&bcEepwqwbdw-")
SENT_NAMES = ("sent", "&bb4eqgqebdcemaqdbdc-")  # «Отправленные»


@dataclass
class Mail:
    uid: int
    sender: str  # адрес
    sender_name: str
    subject: str
    text: str
    message_id: str
    references: str
    spam_flag: str = ""  # непусто, если спам-фильтр сервера пометил письмо в заголовках
    spam_headers: str = ""  # все заголовки спам-фильтра (для команды /lastmail)

    @property
    def sender_display(self) -> str:
        return f"{self.sender_name} <{self.sender}>" if self.sender_name else self.sender


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&[a-z]+;", " ", text)
    return text


def _body_text(msg: email.message.Message) -> str:
    plain, html = None, None
    for part in msg.walk():
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")
        if ctype == "text/plain" and plain is None:
            plain = text
        elif ctype == "text/html" and html is None:
            html = text
    text = plain if plain and plain.strip() else _html_to_text(html or "")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return text[:TEXT_LIMIT]


def _spam_flag(msg: email.message.Message) -> str:
    """Признак спама, который фильтр почтового сервера ставит в служебные заголовки."""
    for header in ("X-Spam-Flag", "X-Spam", "X-Spam-Status"):
        value = (msg.get(header) or "").strip()
        if value.lower().startswith(("yes", "true", "spam")):
            return f"{header}: {value.split(',')[0]}"
    return ""


def parse_mail(uid: int, raw: bytes) -> Mail:
    msg = email.message_from_bytes(raw)
    name, addr = parseaddr(_decode(msg.get("From")))
    return Mail(
        uid=uid,
        sender=addr.lower(),
        sender_name=name,
        subject=_decode(msg.get("Subject")),
        text=_body_text(msg),
        message_id=(msg.get("Message-ID") or "").strip(),
        references=(msg.get("References") or "").strip(),
        spam_flag=_spam_flag(msg),
        spam_headers="; ".join(
            f"{k}: {str(v)[:80]}" for k, v in msg.items() if "spam" in k.lower()
        ),
    )


class MailClient:
    def __init__(
        self,
        address: str,
        password: str,
        imap_host: str,
        imap_port: int,
        smtp_host: str,
        smtp_port: int,
        spam_folder: str | None = None,
    ):
        self.address = address
        self._password = password
        self._imap_host, self._imap_port = imap_host, imap_port
        self._smtp_host, self._smtp_port = smtp_host, smtp_port
        self._spam_folder = spam_folder
        self._sent_folder: str | None = None

    # ---------- IMAP ----------

    def _connect(self) -> imaplib.IMAP4_SSL:
        conn = imaplib.IMAP4_SSL(self._imap_host, self._imap_port, timeout=60)
        conn.login(self.address, self._password)
        return conn

    def check_login(self) -> None:
        conn = self._connect()
        conn.logout()

    def fetch_new(self, last_uid: int | None, uidvalidity: int | None):
        """Новые письма во «Входящих» с UID больше last_uid.

        Возвращает (письма, новый last_uid, uidvalidity). При первом запуске
        (или если сервер сменил нумерацию) старые письма не обрабатываются.
        Письма остаются непрочитанными (BODY.PEEK).
        """
        conn = self._connect()
        try:
            conn.select("INBOX", readonly=True)
            current_validity = int(conn.untagged_responses.get("UIDVALIDITY", [b"0"])[0])
            _, data = conn.uid("SEARCH", None, "ALL")
            all_uids = [int(x) for x in (data[0] or b"").split()]
            max_uid = max(all_uids, default=0)

            if last_uid is None or uidvalidity != current_validity:
                return [], max_uid, current_validity

            mails = []
            for uid in (u for u in all_uids if u > last_uid):
                _, msg_data = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
                raw = next(
                    (part[1] for part in msg_data if isinstance(part, tuple)), None
                )
                if raw:
                    mails.append(parse_mail(uid, raw))
            return mails, max(max_uid, last_uid), current_validity
        finally:
            conn.logout()

    def recent(self, count: int = 5) -> list[Mail]:
        """Последние письма во «Входящих» (не помечает прочитанными)."""
        conn = self._connect()
        try:
            conn.select("INBOX", readonly=True)
            _, data = conn.uid("SEARCH", None, "ALL")
            uids = [int(x) for x in (data[0] or b"").split()][-count:]
            mails = []
            for uid in reversed(uids):
                _, msg_data = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
                raw = next((part[1] for part in msg_data if isinstance(part, tuple)), None)
                if raw:
                    mails.append(parse_mail(uid, raw))
            return mails
        finally:
            conn.logout()

    def _folders(self, conn: imaplib.IMAP4_SSL) -> list[tuple[str, str]]:
        """Список папок: (флаги, имя)."""
        _, data = conn.list()
        result = []
        for line in data or []:
            line = line.decode(errors="replace") if isinstance(line, bytes) else str(line)
            m = re.match(r'\((?P<flags>[^)]*)\)\s+(?:"[^"]*"|NIL)\s+(?P<name>.+)$', line)
            if m:
                result.append((m["flags"], m["name"].strip().strip('"')))
        return result

    def _find_folder(self, conn, flag: str, names: tuple[str, ...]) -> str | None:
        folders = self._folders(conn)
        for flags, name in folders:
            if flag.lower() in flags.lower():
                return name
        for flags, name in folders:
            if name.lower().split("/")[-1].split(".")[-1] in names:
                return name
        return None

    def spam_folder(self) -> str | None:
        if not self._spam_folder:
            conn = self._connect()
            try:
                self._spam_folder = self._find_folder(conn, "\\Junk", SPAM_NAMES)
            finally:
                conn.logout()
        return self._spam_folder

    def move_to_spam(self, uid: int) -> None:
        folder = self.spam_folder()
        if not folder:
            raise RuntimeError("Не найдена папка «Спам». Укажите её в MAIL_SPAM_FOLDER.")
        conn = self._connect()
        try:
            conn.select("INBOX")
            quoted = f'"{folder}"'
            if "MOVE" in conn.capabilities:
                typ, data = conn.uid("MOVE", str(uid), quoted)
            else:
                typ, data = conn.uid("COPY", str(uid), quoted)
                if typ == "OK":
                    conn.uid("STORE", str(uid), "+FLAGS", "(\\Deleted)")
                    conn.expunge()
            if typ != "OK":
                raise RuntimeError(f"Сервер не перенёс письмо: {data}")
        finally:
            conn.logout()

    # ---------- SMTP ----------

    def send_reply(self, mail: Mail, body: str) -> None:
        msg = EmailMessage()
        msg["From"] = self.address
        msg["To"] = mail.sender
        subject = mail.subject or ""
        msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=self.address.split("@")[-1])
        if mail.message_id:
            msg["In-Reply-To"] = mail.message_id
            msg["References"] = f"{mail.references} {mail.message_id}".strip()
        msg.set_content(body)

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(self._smtp_host, self._smtp_port, context=context, timeout=60) as smtp:
            smtp.login(self.address, self._password)
            smtp.send_message(msg)

        # Кладём копию в «Отправленные», чтобы ответ был виден в веб-почте.
        try:
            conn = self._connect()
            try:
                if not self._sent_folder:
                    self._sent_folder = self._find_folder(conn, "\\Sent", SENT_NAMES)
                if self._sent_folder:
                    conn.append(
                        f'"{self._sent_folder}"',
                        "\\Seen",
                        imaplib.Time2Internaldate(time.time()),
                        msg.as_bytes(),
                    )
            finally:
                conn.logout()
        except Exception:
            log.warning("Ответ отправлен, но копия в «Отправленные» не сохранилась", exc_info=True)
