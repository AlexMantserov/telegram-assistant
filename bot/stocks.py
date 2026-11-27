"""Идеи по акциям Мосбиржи по команде /stocks.

Новости: РБК, Финам и Smart-Lab. Цена — последняя сделка TQBR на iss.moex.com.
Если источник не ответил, он пропускается одной строкой, подборка всё равно уходит.
Нет цены — название остаётся, с пометкой что цена недоступна.
Ключ Anthropic не используется. Ежедневной рассылки нет.
"""

import asyncio
import datetime as dt
import html
import json
import logging
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import aiohttp
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import Message

log = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")

HEADERS = {
    "User-Agent": "telegram-assistant/1.0",
    "Accept": "text/html,application/xhtml+xml,application/xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru",
}
MOEX_URL = (
    "https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities.json"
    "?iss.meta=off&securities.columns=SECID,SHORTNAME,SECNAME&marketdata.columns=SECID,LAST"
)
RBC_PAGE = "https://www.rbc.ru/finances/"
RBC_RSS = "https://rssexport.rbc.ru/rbcnews/news/30/full.rss"
FINAM_RSS = "https://www.finam.ru/analysis/conews/rsspoint/"
SMART_PAGE = "https://smart-lab.ru/news/"
SMART_RSS = "https://smart-lab.ru/rss/"
KOMMERSANT_RSS = "https://www.kommersant.ru/RSS/section-finance.xml"

RBC_GAP = "РБК недоступен, для этого пробела взяты новости «Коммерсанта»."
RBC_GAP_EMPTY = "РБК недоступен, запасной российский источник тоже не открылся."
FINAM_GAP = "Финам недоступен, этот источник пропущен."
SMART_GAP = "Smart-Lab недоступен, этот источник пропущен."
MOEX_GAP = "Мосбиржа (iss.moex.com) недоступна, этот источник пропущен."
DISCLAIMER = "⚠️ Это не сигнал к покупке."

# Дополнительные имена: короткое имя на бирже часто сокращено (СевСт, Сургнфгз).
# quoted=True — только в кавычках, чтобы не ловить обычные слова («лента», «полюс»).
EXTRA: dict[str, tuple[tuple[str, bool], ...]] = {
    "SBER": (("сбер", False), ("сбербанк", False)),
    "GAZP": (("газпром", False),),
    "LKOH": (("лукойл", False),),
    "GMKN": (("норникель", False), ("норильский никель", False)),
    "NVTK": (("новатэк", False),),
    "ROSN": (("роснефть", False),),
    "TATN": (("татнефть", False),),
    "SNGS": (("сургутнефтегаз", False),),
    "CHMF": (("северсталь", False),),
    "NLMK": (("нлмк", False),),
    "MAGN": (("ммк", False), ("магнитогорский", False)),
    "MGNT": (("магнит", True),),
    "MTSS": (("мтс", False),),
    "VTBR": (("втб", False),),
    "PLZL": (("полюс", True),),
    "YDEX": (("яндекс", False),),
    "T": (("т-банк", False), ("т-технологии", False), ("тинькофф", False)),
    "OZON": (("ozon", False), ("озон", True)),
    "X5": (("x5", False), ("икс 5", False)),
    "HEAD": (("хэдхантер", False), ("headhunter", False)),
    "SMLT": (("самолет", True), ("гк самолет", False)),
    "LSRG": (("лср", False), ("группа лср", False)),
    "LENT": (("лента", True),),
    "PHOR": (("фосагро", False),),
    "AFLT": (("аэрофлот", False),),
    "DOMRF": (("дом.рф", False),),
    "GEMC": (("юмг", False),),
    "LEAS": (("европлан", False),),
    "CNRU": (("циан", False),),
    "POSI": (("группа позитив", False),),
    "ASTR": (("группа астра", False),),
    "VKCO": (("вконтакте", False),),
    "MOEX": (("московская биржа", False),),
    "IRAO": (("интер рао", False),),
    "ALRS": (("алроса", False),),
    "RUAL": (("русал", False),),
    "FLOT": (("совкомфлот", False),),
    "SVCB": (("совкомбанк", False),),
    "RTKM": (("ростелеком", False),),
    "HYDR": (("русгидро", False),),
    "FEES": (("россети", False),),
    "PIKK": (("пик", True),),
    "SIBN": (("газпром нефть", False), ("газпромнефть", False)),
    "TRNFP": (("транснефть", False),),
    "UPRO": (("юнипро", False),),
    "AFKS": (("афк система", False), ("система", True)),
    "MTLR": (("мечел", False),),
    "NMTP": (("нмтп", False),),
    "BANE": (("башнефть", False),),
    "SGZH": (("сегежа", False),),
    "FESH": (("fesco", False),),
    "SELG": (("селигдар", False),),
    "UGLD": (("югк", False),),
    "CBOM": (("мкб", False),),
    "BSPB": (("банк санкт-петербург", False),),
    "RENI": (("ренессанс страхование", False),),
    "MDMG": (("мать и дитя", False),),
    "BELU": (("новабев", False),),
    "WUSH": (("вуш", False), ("whoosh", False)),
    "FIXR": (("фикс прайс", False), ("fix price", False)),
    "ETLN": (("эталон груп", False),),
    "AQUA": (("инарктика", False),),
}

# Короткое биржевое имя, которое само по себе обычное слово.
QUOTED_ONLY = {"магнит", "лента", "полюс", "самолет", "озон", "система", "астра", "пик"}
# Не брать автоматическое имя: слишком общее или это сам индекс, а не бумага.
DENY_AUTO = QUOTED_ONLY | {
    "мосбиржа", "энергия", "холдинг", "капитал", "группа", "банк", "россия",
    "рынок", "акции", "нефть", "газ", "золото", "серебро", "индекс",
    "позитив", "ренессанс",
}

NEGATIVE = (
    "убыт", "падени", "упал", "обвал", "делистинг", "дефолт", "банкрот",
    "санкц", "сократ", "штраф", "мошеннич", "арест", "уход с биржи",
)
CATALYST = (
    "прибыл", "выручк", "дивиденд", "мсфо", "рсбу", "отчет", "отчёт",
    "контракт", "поставк", "сделк", "выкуп", "байбек", "рекорд",
    "добыч", "спг", "месторожд", "целев", "потенциал", "рекоменд",
    "повыс", "интеграц",
)

_ENDINGS = "ом|ем|ой|ей|ов|ев|ам|ям|ах|ях|ью|ии|а|я|у|ю|е|ы|и"
_BUYOUT = re.compile(
    r"цен\w{0,4}\s+выкупа(?:[^0-9]{0,80}?)(\d+(?:[ \u00a0]\d{3})*(?:[.,]\d+)?)",
    re.IGNORECASE,
)
_TARGET = re.compile(
    r"целев\w{0,8}\s+цен\w{0,6}(?:[^0-9]{0,80}?)(\d+(?:[ \u00a0]\d{3})*(?:[.,]\d+)?)",
    re.IGNORECASE,
)
_YIELD = re.compile(
    r"(?:дивиденд\w{0,12}(?:\s+\w+){0,4}\s+доходност\w{0,6}|\bдд)\s*[~:≈]?\s*(\d+(?:[.,]\d+)?)\s*%",
    re.IGNORECASE,
)
_UPSIDE = re.compile(
    r"(?:потенциал(?:\s+роста)?|апсайд)(?:[^0-9%]{0,20}?)(\d+(?:[.,]\d+)?)\s*%",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Stock:
    ticker: str
    name: str
    price: float | None


@dataclass(frozen=True)
class NewsItem:
    source: str
    title: str
    url: str
    summary: str = ""


@dataclass(frozen=True)
class Signal:
    """Число из текста новости, а не оценка бота."""

    kind: str  # buyout, target, yield, upside
    number: float
    horizon: str


@dataclass
class Idea:
    ticker: str
    name: str
    price: float | None
    growth_percent: float | None
    horizon: str
    reason: str
    sources: list[str]
    growth_note: str
    score: int = 0


@dataclass
class _Hit:
    item: NewsItem
    signal: Signal | None = None
    in_title: bool = False
    catalyst: bool = False


@dataclass
class _Alias:
    ticker: str
    length: int
    pattern: re.Pattern
    text: str = ""


def norm(text: str) -> str:
    text = html.unescape(text or "")
    text = text.lower().replace("ё", "е").replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _num(raw: str) -> float:
    return float(raw.replace(" ", "").replace("\u00a0", "").replace(",", "."))


def _year(number: float) -> bool:
    return number.is_integer() and 1900 <= number <= 2100


def _horizon(text: str, default: str) -> str:
    low = norm(text)
    if "до конца года" in low:
        return "до конца года"
    if re.search(r"\b(12 месяцев|на год|за год|годовых)\b", low):
        return "12 месяцев"
    if re.search(r"\b(полгода|6 месяцев)\b", low):
        return "6 месяцев"
    if re.search(r"\b(квартал|3 месяца)\b", low):
        return "3 месяца"
    return default


def signal_from_text(text: str) -> Signal | None:
    """Цена выкупа, целевая, дивидендная доходность или потенциал — если это прямо написано."""
    low = norm(text)
    for match in _BUYOUT.finditer(low):
        number = _num(match.group(1))
        if _year(number) or not 1 <= number <= 1_000_000:
            continue
        return Signal("buyout", number, "до выкупа")
    for match in _TARGET.finditer(low):
        number = _num(match.group(1))
        if _year(number) or not 1 <= number <= 1_000_000:
            continue
        return Signal("target", number, _horizon(low, "6 месяцев"))
    match = _YIELD.search(low)
    if match:
        number = _num(match.group(1))
        if 0 < number <= 40:
            return Signal("yield", number, "12 месяцев")
    match = _UPSIDE.search(low)
    if match:
        number = _num(match.group(1))
        if 0 < number <= 100:
            return Signal("upside", number, _horizon(low, "6 месяцев"))
    return None


def growth_from_signal(signal: Signal, price: float | None) -> tuple[float, str, str] | None:
    if signal.kind in ("buyout", "target"):
        if price is None or price <= 0:
            return None
        percent = (signal.number / price - 1) * 100
        if not 0 < percent <= 100:
            return None
        note = "к цене выкупа из новости" if signal.kind == "buyout" else "к целевой цене из новости"
        return percent, signal.horizon, note
    if signal.kind == "yield":
        return signal.number, signal.horizon, "дивидендная доходность из новости"
    if signal.kind == "upside":
        return signal.number, signal.horizon, "потенциал из новости"
    return None


def orientir(title: str) -> tuple[float, str, str]:
    low = norm(title)
    if any(word in low for word in ("дивиденд", "прибыл", "рекорд", "контракт", "выручк", "мсфо", "рсбу")):
        return 8.0, "6 месяцев", "ориентир по тону новости"
    return 5.0, "3 месяца", "ориентир по тону новости"


def _negative(title: str) -> bool:
    low = norm(title)
    return any(word in low for word in NEGATIVE)


def _catalyst(title: str) -> bool:
    low = norm(title)
    return any(word in low for word in CATALYST)


def one_sentence(title: str) -> str:
    text = re.sub(r"\s+", " ", html.unescape(title or "")).strip()
    text = re.sub(r"^[^\w«\"А-Яа-яA-Za-z]+", "", text)
    text = re.sub(r"\s+[|—-]\s+(Финам|РБК|Smart-Lab|Коммерсантъ).*$", "", text, flags=re.IGNORECASE)
    # «руб.» и «млрд.» — не конец фразы.
    shielded = re.sub(
        r"\b(руб|млрд|млн|тыс|трлн|г|гг)\.",
        lambda m: m.group(1) + "§",
        text,
        flags=re.IGNORECASE,
    )
    sentence = re.split(r"(?<=[.!?])\s+", shielded, maxsplit=1)[0].strip().replace("§", ".")
    if len(sentence) > 220:
        cut = sentence[:217].rsplit(" ", 1)[0]
        sentence = (cut or sentence[:217]).rstrip(" ,;:") + "…"
    if sentence and sentence[-1] not in ".!?…":
        sentence += "."
    return sentence


def _last_word_pattern(word: str) -> str:
    hyphen = re.fullmatch(r"([а-я]+)-([а-я]+)", word)
    if hyphen:
        return re.escape(hyphen.group(1) + "-") + _last_word_pattern(hyphen.group(2))
    if not re.fullmatch(r"[а-я]+", word):
        return re.escape(word)
    if word.endswith("ь") and len(word) >= 4:
        return re.escape(word[:-1]) + r"(?:ью|и|ь)"
    if word.endswith(("а", "я")) and len(word) >= 5:
        return re.escape(word[:-1]) + r"(?:ой|ей|ою|ею|а|я|ы|и|у|ю|е)"
    if len(word) >= 4:
        return re.escape(word) + r"(?:" + _ENDINGS + r")?"
    return re.escape(word)


def _alias_pattern(alias: str, quoted: bool) -> re.Pattern:
    parts = [_last_word_pattern(part) if i == len(alias.split()) - 1 else re.escape(part)
             for i, part in enumerate(alias.split())]
    body = r"\s+".join(parts)
    if quoted:
        raw = r"[«\"]\s*" + body + r"\s*[»\"]"
    else:
        raw = r"(?<![0-9a-zа-я-])" + body + r"(?![0-9a-zа-я-])"
    return re.compile(raw)


def _looks_like_name(name: str) -> bool:
    if name in DENY_AUTO or len(name) < 5:
        return False
    if not re.search(r"[аеёиоуыэюя]", name):
        return False
    compact = name.replace(" ", "").replace("-", "")
    if re.search(r"[бвгджзклмнпрстфхцчшщ]{4,}", compact):
        return False
    return all(len(token) >= 4 or "-" in token for token in name.split())


def _clean_short(short: str) -> str:
    name = norm(short)
    name = re.sub(r"^i(?=[а-яa-z])", "", name)
    name = re.sub(r"[\s\-]+(ао|ап|п)$", "", name).strip(" -")
    return re.sub(r"\s+", " ", name).strip()


def _prettify(name: str) -> str:
    letters = re.sub(r"[^A-Za-zА-ЯЁа-яё]", "", name)
    if letters.isupper() and len(letters) <= 5:
        return name
    if letters.isupper():
        return name.capitalize()
    return name


# Короткое имя на бирже иногда нечитаемо: Газпрнефть, СевСт, Сургнфгз.
PRETTY = {
    "SIBN": "Газпром нефть",
    "CHMF": "Северсталь",
    "SNGS": "Сургутнефтегаз",
    "TATN": "Татнефть",
    "RTKM": "Ростелеком",
    "TRNFP": "Транснефть",
    "BANE": "Башнефть",
    "MTSS": "МТС",
    "GMKN": "Норникель",
    "IRAO": "Интер РАО",
    "T": "Т-Технологии",
    "LSRG": "Группа ЛСР",
    "AFKS": "АФК Система",
    "MAGN": "ММК",
    "NLMK": "НЛМК",
    "VTBR": "ВТБ",
    "YDEX": "Яндекс",
    "NVTK": "Новатэк",
    "PHOR": "ФосАгро",
    "ALRS": "АЛРОСА",
    "HYDR": "РусГидро",
    "DOMRF": "ДОМ.РФ",
    "X5": "ИКС 5",
    "BSPB": "Банк Санкт-Петербург",
}


def display_name(secid: str, short: str, secname: str) -> str:
    if secid in PRETTY:
        return PRETTY[secid]
    match = re.search(r"[«\"]([^»\"]{2,40})[»\"]", secname or "")
    if match:
        name = re.sub(r"\s+", " ", match.group(1)).strip().replace("Нор.Никель", "Норникель")
        if name and norm(name) not in DENY_AUTO:
            return _prettify(name)
    name = re.sub(r"^i(?=[А-ЯЁA-Z])", "", (short or "").strip())
    name = re.sub(r"[\s\-]+[аА][оОпП]$", "", name).strip()
    return _prettify(name)


def is_fund(secid: str, short: str, secname: str) -> bool:
    if secid.startswith("RU"):
        return True
    blob = f"{short} {secname}".lower()
    return any(mark in blob for mark in ("etf", "бпиф", "зпиф", "ипиф"))


def is_preferred(short: str, secname: str) -> bool:
    if re.search(r"(ап\.?|акц\.пр\.?)$", (secname or "").lower().strip()):
        return True
    return bool(re.search(r"(-п|-ап| ап)$", (short or "").lower().strip()))


def _add_alias(bucket: dict[str, set[tuple[str, bool]]], ticker: str, alias: str, quoted: bool) -> None:
    alias = norm(alias)
    if len(alias) < 2:
        return
    bucket.setdefault(ticker, set()).add((alias, quoted))


def aliases_for(secid: str, short: str, secname: str, preferred: bool) -> set[tuple[str, bool]]:
    bucket: dict[str, set[tuple[str, bool]]] = {}
    for alias, quoted in EXTRA.get(secid, ()):
        _add_alias(bucket, secid, alias, quoted)
    if not preferred:
        cleaned = _clean_short(short)
        if cleaned in QUOTED_ONLY:
            _add_alias(bucket, secid, cleaned, True)
        elif _looks_like_name(cleaned):
            _add_alias(bucket, secid, cleaned, False)
        for match in re.finditer(r"[«\"]([^»\"]{2,40})[»\"]", secname or ""):
            quoted_name = norm(match.group(1)).replace(".", "")
            # Одно слово из кавычек. «Санкт-Петербург» и прочие длинные имена с дефисом
            # оставляем ручному списку, иначе город становится тикером.
            if " " in quoted_name or "-" in quoted_name or quoted_name in DENY_AUTO:
                continue
            if _looks_like_name(quoted_name):
                _add_alias(bucket, secid, quoted_name, False)
            elif 3 <= len(quoted_name) <= 6 and quoted_name.isalpha() and not re.search(r"[аеёиоуыэюя]", quoted_name):
                # Короткий тикер вроде НЛМК, без гласных, чтобы не поймать слово «банк».
                _add_alias(bucket, secid, quoted_name, False)
    return bucket.get(secid, set())


def parse_moex(payload: dict) -> dict[str, Stock]:
    """Бумаги TQBR, кроме фондов. Нет положительной цены LAST — цена None."""
    securities = payload["securities"]
    marketdata = payload["marketdata"]
    sec_cols = securities["columns"]
    md_cols = marketdata["columns"]
    last_at = md_cols.index("LAST")
    id_at = md_cols.index("SECID")
    prices: dict[str, float] = {}
    for row in marketdata["data"]:
        price = row[last_at]
        if isinstance(price, (int, float)) and not isinstance(price, bool) and price > 0:
            prices[row[id_at]] = float(price)
    i_id, i_short, i_name = (sec_cols.index(name) for name in ("SECID", "SHORTNAME", "SECNAME"))
    stocks: dict[str, Stock] = {}
    for row in securities["data"]:
        secid = row[i_id]
        if not secid:
            continue
        short, secname = row[i_short] or "", row[i_name] or ""
        if is_fund(secid, short, secname):
            continue
        stocks[secid] = Stock(secid, display_name(secid, short, secname), prices.get(secid))
    return stocks


def compile_aliases(meta: dict[str, tuple[set[tuple[str, bool]], bool]]) -> list[_Alias]:
    compiled: list[_Alias] = []
    for ticker, (pairs, _preferred) in meta.items():
        for alias, quoted in pairs:
            compiled.append(_Alias(ticker, len(alias), _alias_pattern(alias, quoted), alias))
    compiled.sort(key=lambda item: item.length, reverse=True)
    return compiled


def match_tickers(text: str, compiled: list[_Alias], ticker_re: re.Pattern | None) -> set[str]:
    folded = norm(text)
    occupied: list[tuple[int, int]] = []
    found: set[str] = set()
    for alias in compiled:
        for match in alias.pattern.finditer(folded):
            span = match.span()
            if any(span[0] < end and span[1] > start for start, end in occupied):
                continue
            occupied.append(span)
            found.add(alias.ticker)
            break
    if ticker_re is not None:
        found.update(ticker_re.findall(text or ""))
    return found


def _canon_url(url: str) -> str:
    return (url or "").split("?", 1)[0].rstrip("/")


def _dedupe(items: list[NewsItem]) -> list[NewsItem]:
    seen: set[str] = set()
    result: list[NewsItem] = []
    for item in items:
        key = _canon_url(item.url) or norm(item.title)
        if not item.title or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def parse_rss(xml_text: str, source: str) -> list[NewsItem]:
    if not xml_text or "<item" not in xml_text.lower():
        return []
    # В обрезанной ленте может не закрыться CDATA — дочитываем только целые item.
    items: list[NewsItem] = []
    for chunk in re.findall(r"<item\b[^>]*>.*?</item>", xml_text, flags=re.IGNORECASE | re.DOTALL):
        title = _xml_text(chunk, "title")
        link = _xml_text(chunk, "link")
        summary = _xml_text(chunk, "description")
        if not title or not link:
            continue
        summary = re.sub(r"<[^>]+>", " ", summary)
        summary = re.sub(r"\s+", " ", html.unescape(summary)).strip()
        items.append(NewsItem(source, re.sub(r"\s+", " ", title).strip(), link.strip(), summary[:800]))
    return _dedupe(items)


def _xml_text(chunk: str, tag: str) -> str:
    match = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", chunk, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    raw = match.group(1).strip()
    raw = re.sub(r"^<!\[CDATA\[", "", raw)
    raw = re.sub(r"\]\]>$", "", raw)
    return html.unescape(raw).strip()


def parse_rbc_html(page: str) -> list[NewsItem]:
    if not page:
        return []
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        page,
        flags=re.DOTALL,
    )
    items: list[NewsItem] = []
    if match:
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            payload = None
        if payload is not None:
            _walk_rbc(payload, items)
    return _dedupe(items)


def _rbc_url_ok(url: str) -> bool:
    if "editorial.rbc.ru" in url:
        return False
    if any(part in url for part in ("/politics/", "/society/", "/sport/", "/education/", "/auto/", "/style/")):
        return False
    return any(part in url for part in ("/finances/", "/quote/", "/economics/", "/business/", "/rbcfreenews/"))


def _walk_rbc(node: object, items: list[NewsItem]) -> None:
    if isinstance(node, dict):
        url = node.get("url")
        title = node.get("title")
        if isinstance(url, str) and isinstance(title, str) and node.get("id") and _rbc_url_ok(url):
            codes: list[str] = []
            for ticker in node.get("tickers") or []:
                if not isinstance(ticker, dict) or ticker.get("type") != "share":
                    continue
                code = str(ticker.get("title") or "")
                if re.fullmatch(r"[A-Z][A-Z0-9]{1,5}", code):
                    codes.append(code)
            anons = node.get("anons") or node.get("subtitle") or ""
            summary = str(anons or "")
            if codes:
                summary = (summary + " " + " ".join(codes)).strip()
            items.append(NewsItem("РБК", str(title).strip(), url, summary[:800]))
        for value in node.values():
            _walk_rbc(value, items)
    elif isinstance(node, list):
        for value in node:
            _walk_rbc(value, items)


def parse_smartlab_html(page: str) -> list[NewsItem]:
    if not page:
        return []
    items: list[NewsItem] = []
    for href, title in re.findall(r'href="(/(?:news|blog)/[^"#?]+)"[^>]*>([^<]{15,300})', page):
        title = re.sub(r"\s+", " ", html.unescape(title)).strip()
        if len(title) < 20:
            continue
        items.append(NewsItem("Smart-Lab", title, "https://smart-lab.ru" + href))
    return _dedupe(items)


def build_ideas(
    stocks: dict[str, Stock],
    alias_meta: dict[str, tuple[set[tuple[str, bool]], bool]],
    news: list[NewsItem],
    count: int,
) -> list[Idea]:
    compiled = compile_aliases(alias_meta)
    latin = [ticker for ticker in stocks if re.fullmatch(r"[A-Z]{4,6}", ticker)]
    ticker_re = re.compile(r"(?<![A-Z0-9])(" + "|".join(latin) + r")(?![A-Z0-9])") if latin else None
    grouped: dict[str, list[_Hit]] = {}
    for item in news:
        if _negative(item.title):
            continue
        blob = f"{item.title} {item.summary}"
        for ticker in match_tickers(blob, compiled, ticker_re):
            if ticker not in stocks:
                continue
            title_hit = bool(match_tickers(item.title, compiled, ticker_re) & {ticker})
            grouped.setdefault(ticker, []).append(
                _Hit(item, signal_from_text(blob), title_hit, _catalyst(item.title))
            )
    ranked: list[tuple[int, bool, str]] = []
    for ticker, hits in grouped.items():
        sources = {hit.item.source for hit in hits}
        extracted = any(hit.signal is not None for hit in hits)
        catalyst = any(hit.catalyst for hit in hits)
        score = 5 * len(sources) + min(len(hits), 5)
        if extracted:
            score += 12
        if catalyst:
            score += 4
        if any(hit.in_title for hit in hits):
            score += 2
        strong = extracted or catalyst or len(sources) >= 2 or len(hits) >= 2
        ranked.append((score, strong, ticker))
    ranked.sort(key=lambda row: (row[1], row[0]), reverse=True)
    # Сначала новости с цифрой, поводом или повторным упоминанием, потом остальные — до count.
    ideas: list[Idea] = []
    for score, _strong, ticker in ranked:
        if len(ideas) >= count:
            break
        idea = _idea_from_hits(stocks[ticker], grouped[ticker], score)
        if idea is not None:
            ideas.append(idea)
    return ideas


def _idea_from_hits(stock: Stock, hits: list[_Hit], score: int) -> Idea | None:
    def rank(hit: _Hit) -> tuple[int, int, int]:
        return (1 if hit.signal else 0, 1 if hit.catalyst else 0, 1 if hit.in_title else 0)

    chosen = max(hits, key=rank)
    growth = None
    signaled = [hit for hit in hits if hit.signal is not None]
    for hit in sorted(signaled, key=rank, reverse=True):
        growth = growth_from_signal(hit.signal, stock.price) if hit.signal else None
        if growth is not None:
            chosen = hit
            break
    if signaled and growth is None and stock.price is not None:
        # В новости есть цель или выкуп, но они не выше последней цены.
        return None
    if growth is None:
        absolute = next(
            (hit for hit in sorted(signaled, key=rank, reverse=True)
             if hit.signal and hit.signal.kind in ("buyout", "target")),
            None,
        )
        if absolute is not None and absolute.signal is not None and stock.price is None:
            # Цель из новости есть, LAST нет — не подставляем ориентир вместо расчёта.
            chosen = absolute
            kind = "выкупа" if absolute.signal.kind == "buyout" else "цели"
            percent, horizon, note = (
                None,
                absolute.signal.horizon,
                f"в новости цена {kind} {_money(absolute.signal.number)}, сравнить не с чем",
            )
        else:
            percent, horizon, note = orientir(chosen.item.title)
    else:
        percent, horizon, note = growth
    reason = one_sentence(chosen.item.title)
    if not reason:
        return None
    links: list[str] = []
    for hit in sorted(hits, key=rank, reverse=True):
        url = _canon_url(hit.item.url)
        if url and url not in links:
            links.append(url)
        if len(links) == 3:
            break
    return Idea(stock.ticker, stock.name, stock.price, percent, horizon, reason, links, note, score)


def _money(price: float) -> str:
    digits = 2 if price >= 10 else 4
    text = f"{price:,.{digits}f}".replace(",", " ").replace(".", ",")
    return text + " ₽"


def _pct(value: float) -> str:
    if abs(value - round(value)) < 0.05:
        return f"{value:+.0f}%"
    return f"{value:+.1f}%".replace(".", ",")


def format_digest(ideas: list[Idea], when: dt.datetime, gaps: list[str], asked: int) -> str:
    lines = [f"📈 Идеи по акциям на {when.strftime('%d.%m.%Y')}", ""]
    notes = [gap for gap in gaps if gap]
    for gap in notes:
        lines.append(gap)
    if notes:
        lines.append("")
    if not ideas:
        lines.append("В свежих новостях не нашлось бумаг для подборки.")
        lines.append("")
        lines.append(DISCLAIMER)
        return "\n".join(lines)
    for index, idea in enumerate(ideas, start=1):
        price = "цена недоступна" if idea.price is None else _money(idea.price)
        lines.append(f"{index}. {idea.name} ({idea.ticker}) — {price}")
        if idea.growth_percent is None:
            lines.append(f"   Рост: {idea.growth_note}")
        else:
            lines.append(f"   Рост: {_pct(idea.growth_percent)} ({idea.growth_note})")
        lines.append(f"   Срок: {idea.horizon}")
        lines.append(f"   Почему: {idea.reason}")
        if idea.sources:
            lines.append("   Источники: " + " ".join(idea.sources))
        lines.append("")
    if len(ideas) < asked:
        lines.append(f"В свежих новостях нашлось {len(ideas)} из {asked}.")
        lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


async def _fetch(session: aiohttp.ClientSession, url: str, timeout: float) -> str | None:
    try:
        async with session.get(url, headers=HEADERS, allow_redirects=True,
                                timeout=aiohttp.ClientTimeout(total=timeout)) as response:
            if response.status != 200:
                log.warning("Источник %s ответил %s", url, response.status)
                return None
            return await response.text()
    except Exception as exc:
        log.warning("Источник не открылся: %s (%s)", url, type(exc).__name__)
        return None


def _feed_ok(text: str | None) -> bool:
    """Лента действительно открылась, а не страница-заглушка и не обрыв связи."""
    if not text:
        return False
    folded = text.lower()
    return "<rss" in folded or "<feed" in folded or "<channel" in folded or "<item" in folded


async def load_news(session: aiohttp.ClientSession) -> tuple[list[NewsItem], list[str]]:
    rbc_rss_task = asyncio.create_task(_fetch(session, RBC_RSS, 8))
    rbc_page, finam, smart_page, smart_rss = await asyncio.gather(
        _fetch(session, RBC_PAGE, 18),
        _fetch(session, FINAM_RSS, 18),
        _fetch(session, SMART_PAGE, 18),
        _fetch(session, SMART_RSS, 18),
    )
    rbc = parse_rbc_html(rbc_page or "")
    if len(rbc) >= 5:
        rbc_rss_task.cancel()
        try:
            await rbc_rss_task
        except asyncio.CancelledError:
            pass
    else:
        rbc = _dedupe(rbc + parse_rss((await rbc_rss_task) or "", "РБК"))
    news: list[NewsItem] = []
    gaps: list[str] = []
    if rbc:
        news.extend(rbc)
    else:
        fallback = parse_rss(await _fetch(session, KOMMERSANT_RSS, 18) or "", "Коммерсантъ")
        news.extend(fallback)
        gaps.append(RBC_GAP if fallback else RBC_GAP_EMPTY)
        log.warning("РБК не отдал новости, запасной источник: %s материалов", len(fallback))
    if _feed_ok(finam):
        news.extend(parse_rss(finam or "", "Финам"))
    else:
        gaps.append(FINAM_GAP)
        log.warning("Финам не ответил, источник пропущен")
    smart_items: list[NewsItem] = []
    if smart_page is not None:
        smart_items.extend(parse_smartlab_html(smart_page))
    smart_feed = _feed_ok(smart_rss)
    if smart_feed:
        smart_items.extend(parse_rss(smart_rss or "", "Smart-Lab"))
    if smart_page is None and not smart_feed:
        gaps.append(SMART_GAP)
        log.warning("Smart-Lab не ответил, источник пропущен")
    else:
        news.extend(smart_items)
    return news, gaps


def _cap_token(part: str) -> str:
    core = re.sub(r"[^0-9a-zа-я]", "", part)
    if core and not re.search(r"[аеёиоуыэюяaeiouy]", core):
        return part.upper()
    return part[:1].upper() + part[1:]


def _offline_name(ticker: str) -> str:
    if ticker in PRETTY:
        return PRETTY[ticker]
    pairs = EXTRA.get(ticker, ())
    names = [alias for alias, quoted in pairs if not quoted and alias]
    if not names:
        names = [alias for alias, quoted in pairs if alias]
    if not names:
        return ticker
    alias = max(names, key=len)
    return " ".join(_cap_token(part) for part in alias.split(" "))


def _offline_board() -> tuple[dict[str, Stock], dict]:
    """Имена из списка алиасов, когда Мосбиржа не ответила. Цен нет."""
    stocks: dict[str, Stock] = {}
    meta: dict[str, tuple[set[tuple[str, bool]], bool]] = {}
    for ticker in EXTRA:
        stocks[ticker] = Stock(ticker, _offline_name(ticker), None)
        meta[ticker] = (aliases_for(ticker, "", "", False), False)
    return stocks, meta


def _meta_from_payload(payload: dict, stocks: dict[str, Stock]) -> dict:
    meta: dict[str, tuple[set[tuple[str, bool]], bool]] = {}
    sec_cols = payload["securities"]["columns"]
    i_id, i_short, i_name = (sec_cols.index(name) for name in ("SECID", "SHORTNAME", "SECNAME"))
    by_id = {row[i_id]: row for row in payload["securities"]["data"]}
    for ticker in stocks:
        row = by_id.get(ticker)
        if row is None:
            meta[ticker] = (set(), False)
            continue
        short, secname = row[i_short] or "", row[i_name] or ""
        preferred = is_preferred(short, secname)
        meta[ticker] = (aliases_for(ticker, short, secname, preferred), preferred)
    return meta


async def load_stocks(session: aiohttp.ClientSession) -> tuple[dict[str, Stock], dict, str | None]:
    raw = None
    for attempt in range(2):
        raw = await _fetch(session, MOEX_URL, 25)
        if raw:
            break
        log.warning("Повторный запрос цен Мосбиржи (%s)", attempt + 1)
    if not raw:
        log.warning("Мосбиржа не ответила, подборка без цен")
        stocks, meta = _offline_board()
        return stocks, meta, MOEX_GAP
    try:
        payload = json.loads(raw)
        stocks = parse_moex(payload)
        meta = _meta_from_payload(payload, stocks)
    except (KeyError, ValueError, TypeError, IndexError) as exc:
        log.warning("Ответ Мосбиржи не разобран (%s), источник пропущен", type(exc).__name__)
        stocks, meta = _offline_board()
        return stocks, meta, MOEX_GAP
    if not stocks:
        log.warning("Мосбиржа не отдала бумаги TQBR, источник пропущен")
        stocks, meta = _offline_board()
        return stocks, meta, MOEX_GAP
    return stocks, meta, None


async def build_digest(count: int, session: aiohttp.ClientSession | None = None,
                        now: dt.datetime | None = None) -> str:
    own = session is None
    if session is None:
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20), trust_env=False)
    try:
        stocks_result, news_result = await asyncio.gather(
            load_stocks(session),
            load_news(session),
        )
    finally:
        if own:
            await session.close()
    stocks, meta, moex_gap = stocks_result
    news, news_gaps = news_result
    gaps = ([moex_gap] if moex_gap else []) + list(news_gaps)
    ideas = build_ideas(stocks, meta, news, count)
    when = now or dt.datetime.now(MSK)
    log.info("Подборка /stocks: %s", ", ".join(idea.ticker for idea in ideas) or "пусто")
    return format_digest(ideas, when, gaps, count)


class StocksDigest:
    def __init__(self, bot: Bot, owner_id: int, count: int):
        self.bot = bot
        self.owner_id = owner_id
        self.count = count if count > 0 else 5
        self._lock = asyncio.Lock()

    async def build(self) -> str:
        async with self._lock:
            return await build_digest(self.count)

    async def send(self) -> None:
        try:
            text = await self.build()
        except Exception as exc:
            log.exception("Не удалось подготовить подборку по акциям")
            text = f"⚠️ Не удалось подготовить подборку по акциям: {exc}"
        for offset in range(0, len(text), 4000):
            await self.bot.send_message(
                self.owner_id, text[offset : offset + 4000], disable_web_page_preview=True
            )

    def router(self) -> Router:
        router = Router()
        router.message.filter(F.from_user.id == self.owner_id)

        @router.message(Command("stocks"))
        async def cmd_stocks(message: Message) -> None:
            await message.answer("Собираю подборку по новостям и ценам Мосбиржи…")
            await self.send()

        return router
