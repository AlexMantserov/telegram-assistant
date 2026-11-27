"""Подборка /stocks не обрывается, если источник не ответил."""

import asyncio
import datetime as dt
import json
import unittest
from zoneinfo import ZoneInfo

from bot.stocks import (
    DISCLAIMER,
    FINAM_GAP,
    MOEX_GAP,
    RBC_GAP,
    RBC_GAP_EMPTY,
    SMART_GAP,
    NewsItem,
    Stock,
    aliases_for,
    build_digest,
    _offline_name,
    build_ideas,
    format_digest,
    parse_moex,
)

WHEN = dt.datetime(2026, 11, 27, 12, 0, tzinfo=ZoneInfo("Europe/Moscow"))
ABORT = "Не удалось подготовить подборку"


def _rss(*items: tuple[str, str]) -> str:
    body = "".join(
        f"<item><title>{title}</title><link>{link}</link><description></description></item>"
        for title, link in items
    )
    return f"<?xml version='1.0'?><rss><channel>{body}</channel></rss>"


class _Body:
    def __init__(self, status: int, body: str):
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Session:
    def __init__(self, routes: dict):
        self.routes = routes

    def get(self, url, headers=None, allow_redirects=True, timeout=None):
        matches = [key for key in self.routes if key in url]
        if not matches:
            return _Body(599, "")
        spec = self.routes[max(matches, key=len)]
        if isinstance(spec, BaseException):
            raise spec
        status, body = spec
        return _Body(status, body)


def _moex(*rows: tuple[str, str, str, float | None]) -> str:
    securities = [[secid, short, name] for secid, short, name, _price in rows]
    market = [[secid, price] for secid, _short, _name, price in rows]
    return json.dumps({
        "securities": {
            "columns": ["SECID", "SHORTNAME", "SECNAME"],
            "data": securities,
        },
        "marketdata": {"columns": ["SECID", "LAST"], "data": market},
    })


def _digest(routes: dict, count: int = 5) -> str:
    return asyncio.run(build_digest(count, session=_Session(routes), now=WHEN))


class DigestTests(unittest.TestCase):
    def test_parse_keeps_names_without_last_and_skips_funds(self):
        payload = json.loads(_moex(
            ("SBER", "Сбербанк", "Сбербанк", 301.2),
            ("GAZP", "ГАЗПРОМ ао", "Газпром", None),
            ("TMOS", "TMOS ETF", "БПИФ TMOS", 5.0),
        ))
        stocks = parse_moex(payload)
        self.assertEqual(set(stocks), {"SBER", "GAZP"})
        self.assertEqual(stocks["SBER"].price, 301.2)
        self.assertIsNone(stocks["GAZP"].price)
        self.assertEqual(stocks["GAZP"].name, "Газпром")

    def test_target_below_price_dropped_missing_price_kept(self):
        stocks = {
            "SBER": Stock("SBER", "Сбербанк", 300.0),
            "GAZP": Stock("GAZP", "Газпром", None),
        }
        meta = {
            "SBER": (aliases_for("SBER", "", "", False), False),
            "GAZP": (aliases_for("GAZP", "", "", False), False),
        }
        news = [
            NewsItem("Финам", "Сбербанк: целевая цена 100 рублей", "https://www.finam.ru/a", ""),
            NewsItem("Финам", "Газпром: целевая цена 200 рублей", "https://www.finam.ru/b", ""),
        ]
        ideas = {idea.ticker: idea for idea in build_ideas(stocks, meta, news, 5)}
        self.assertNotIn("SBER", ideas)
        self.assertIn("GAZP", ideas)
        self.assertIsNone(ideas["GAZP"].price)
        self.assertIsNone(ideas["GAZP"].growth_percent)
        self.assertIn("сравнить не с чем", ideas["GAZP"].growth_note)
        text = format_digest([ideas["GAZP"]], WHEN, [], 5)
        self.assertIn("Газпром (GAZP) — цена недоступна", text)
        self.assertIn("Рост: в новости цена цели", text)
        self.assertNotIn("+5%", text)

    def test_offline_name_when_exchange_is_down(self):
        self.assertEqual(_offline_name("SBER"), "Сбербанк")
        self.assertEqual(_offline_name("SMLT"), "ГК Самолет")
        self.assertEqual(_offline_name("NMTP"), "НМТП")
        self.assertEqual(_offline_name("MGNT"), "Магнит")

    def test_at_most_requested_count(self):
        tickers = {
            "SBER": "Сбербанк увеличил прибыль",
            "GAZP": "Газпром подписал контракт",
            "LKOH": "Лукойл увеличил прибыль",
            "GMKN": "Норникель увеличил прибыль",
            "NVTK": "Новатэк увеличил прибыль",
            "ROSN": "Роснефть увеличил прибыль",
        }
        stocks = {ticker: Stock(ticker, ticker, 100.0) for ticker in tickers}
        meta = {ticker: (aliases_for(ticker, "", "", False), False) for ticker in tickers}
        news = [
            NewsItem("Финам", title, f"https://www.finam.ru/{ticker}", "")
            for ticker, title in tickers.items()
        ]
        self.assertEqual(len(build_ideas(stocks, meta, news, 5)), 5)

    def test_moex_down_still_lists_finam_idea_without_price(self):
        text = _digest({
            "iss.moex.com": (500, ""),
            "www.rbc.ru": (500, ""),
            "rssexport.rbc.ru": (500, ""),
            "finam.ru": (200, _rss(("Сбербанк увеличил прибыль по МСФО", "https://www.finam.ru/sber"))),
            "smart-lab.ru/news/": (500, ""),
            "smart-lab.ru/rss/": (500, ""),
            "kommersant.ru": (500, ""),
        })
        self.assertNotIn(ABORT, text)
        self.assertNotIn("Не удалось получить цены LAST", text)
        self.assertIn(MOEX_GAP, text.splitlines())
        self.assertIn(RBC_GAP_EMPTY, text.splitlines())
        self.assertIn(SMART_GAP, text.splitlines())
        self.assertNotIn(FINAM_GAP, text)
        self.assertIn("Сбербанк (SBER) — цена недоступна", text)
        self.assertIn(DISCLAIMER, text)
        self.assertLessEqual(text.count("\n1. "), 1)

    def test_prices_missing_for_one_name_only(self):
        text = _digest({
            "iss.moex.com": (200, _moex(
                ("SBER", "Сбербанк", "Сбербанк", 301.2),
                ("GAZP", "ГАЗПРОМ ао", "Газпром", None),
            )),
            "www.rbc.ru": (200, ""),
            "rssexport.rbc.ru": (200, _rss((
                "Курс рубля остался почти без изменений на торгах",
                "https://www.rbc.ru/finances/1",
            ))),
            "finam.ru": (200, _rss(
                ("Сбербанк увеличил прибыль по МСФО", "https://www.finam.ru/sber"),
                ("Газпром подписал контракт на поставку", "https://www.finam.ru/gazp"),
            )),
            "smart-lab.ru/news/": (200, "<html></html>"),
            "smart-lab.ru/rss/": (500, ""),
        })
        self.assertNotIn(ABORT, text)
        self.assertNotIn(MOEX_GAP, text)
        self.assertNotIn(FINAM_GAP, text)
        self.assertNotIn(SMART_GAP, text)
        self.assertNotIn(RBC_GAP, text)
        self.assertIn("Сбербанк (SBER) — 301,20 ₽", text)
        self.assertIn("Газпром (GAZP) — цена недоступна", text)

    def test_all_sources_down_is_a_digest_not_an_abort(self):
        text = _digest({
            "iss.moex.com": TimeoutError("moex"),
            "www.rbc.ru": (500, ""),
            "rssexport.rbc.ru": (500, ""),
            "finam.ru": (200, "<html>blocked</html>"),
            "smart-lab.ru": (500, ""),
            "kommersant.ru": (500, ""),
        })
        lines = text.splitlines()
        self.assertNotIn(ABORT, text)
        self.assertIn(MOEX_GAP, lines)
        self.assertIn(RBC_GAP_EMPTY, lines)
        self.assertIn(FINAM_GAP, lines)
        self.assertIn(SMART_GAP, lines)
        self.assertIn("В свежих новостях не нашлось бумаг для подборки.", lines)
        self.assertNotRegex(text, r"(?m)^\d+\. ")

    def test_broken_moex_json_is_skipped(self):
        text = _digest({
            "iss.moex.com": (200, "not-json"),
            "www.rbc.ru": (500, ""),
            "rssexport.rbc.ru": (500, ""),
            "finam.ru": (200, _rss(("Сбербанк увеличил прибыль по МСФО", "https://www.finam.ru/sber"))),
            "smart-lab.ru": (500, ""),
            "kommersant.ru": (200, _rss((
                "Новатэк увеличил прибыль",
                "https://www.kommersant.ru/doc/1",
            ))),
        })
        self.assertNotIn(ABORT, text)
        self.assertIn(MOEX_GAP, text.splitlines())
        self.assertIn(RBC_GAP, text.splitlines())
        self.assertIn("Сбербанк (SBER) — цена недоступна", text)
        self.assertIn("Новатэк (NVTK) — цена недоступна", text)

    def test_news_down_does_not_invent_names(self):
        text = _digest({
            "iss.moex.com": (200, _moex(("SBER", "Сбербанк", "Сбербанк", 301.2))),
            "www.rbc.ru": (500, ""),
            "rssexport.rbc.ru": (500, ""),
            "finam.ru": (500, ""),
            "smart-lab.ru": (500, ""),
            "kommersant.ru": (500, ""),
        })
        self.assertNotIn(ABORT, text)
        self.assertNotIn(MOEX_GAP, text)
        self.assertNotIn("Сбербанк", text)
        self.assertNotRegex(text, r"(?m)^\d+\. ")


if __name__ == "__main__":
    unittest.main()
