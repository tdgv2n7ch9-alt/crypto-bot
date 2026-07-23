"""
pytest -- владелец, ДА, 2026-07-23 (FIXLIST_INTERFACE.md п.1, живая находка,
railway logs traceback'и): РЫНОК/Зоны/Мои разметки "молча мёртвые" кнопки --
telegram.error.BadRequest: Can't parse entities. Корень:
(1) _mv2_render_rynok() интерполирует str(исключения) напрямую в Markdown-
    текст fallback-строки ("BTC-контекст: н/д (...)") -- HTTPError от
    CoinGecko несёт полный URL с "_" (vs_currency, market_cap_desc), непарные
    подчёркивания ломают парсинг.
(2) _zones_screen_text() интерполирует "source"/"updated" из watch_zones.json
    (свободный текст ручной разметки) без санитайзера в заголовок экрана.
Тест проверяет РЕАЛЬНОЕ поведение (не мок sanitize-вызова) -- строит текст с
намеренно "грязным" вводом и проверяет, что результат безопасен для
Markdown-парсинга (нет непарных спецсимволов), тем же методом, что и
существующие тесты event_radar.py/morning_metrics.py в этом проекте.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot


def _run(coro):
    return asyncio.run(coro)


class _FakeQuery:
    def __init__(self):
        self.edited = []

    async def edit_message_text(self, text, parse_mode=None, reply_markup=None):
        self.edited.append(text)


# ── _mv2_render_rynok() -- exception text sanitized before interpolation ──

def test_rynok_sanitizes_btc_context_exception_message(monkeypatch):
    def boom():
        raise RuntimeError("HTTPError: 429 for url: https://api.coingecko.com/api/v3/coins/"
                            "markets?vs_currency=usd&price_change_percentage=1h_24h_7d")
    monkeypatch.setattr(bot, "get_btc_market_context", boom)
    monkeypatch.setattr(bot.onchain_metrics, "format_onchain_card_text", lambda sym: "on-chain OK")

    q = _FakeQuery()
    _run(bot._mv2_render_rynok(q))
    assert len(q.edited) == 1
    text = q.edited[0]
    # непарные "_" из URL были бы нечётным числом -- санитайзер превращает их в пробелы
    assert "vs_currency" not in text
    assert "н/д" in text


def test_rynok_sanitizes_onchain_exception_message(monkeypatch):
    monkeypatch.setattr(bot, "get_btc_market_context", lambda: {"ok": True, "btc_price": 60000, "btc_ch1h": 0,
                                                                   "btc_ch24h": 0, "dominance": 50,
                                                                   "trend_1h": "n", "trend_4h": "n", "trend_1d": "n"})

    def boom(sym):
        raise RuntimeError("bad_field_name error at path/to[thing]")
    monkeypatch.setattr(bot.onchain_metrics, "format_onchain_card_text", boom)

    q = _FakeQuery()
    _run(bot._mv2_render_rynok(q))
    text = q.edited[0]
    assert "bad_field_name" not in text
    assert "н/д" in text


def test_rynok_sanitizes_successful_onchain_text_too(monkeypatch):
    """Живые onchain-данные тоже проходят через санитайзер (не только фоллбек
    исключения) -- защита от будущих источников свободного текста там же."""
    monkeypatch.setattr(bot, "get_btc_market_context", lambda: {"ok": True, "btc_price": 60000, "btc_ch1h": 0,
                                                                   "btc_ch24h": 0, "dominance": 50,
                                                                   "trend_1h": "n", "trend_4h": "n", "trend_1d": "n"})
    monkeypatch.setattr(bot.onchain_metrics, "format_onchain_card_text",
                         lambda sym: "some_weird_field value")
    q = _FakeQuery()
    _run(bot._mv2_render_rynok(q))
    text = q.edited[0]
    assert "some_weird_field" not in text


# ── _zones_screen_text() -- source/updated sanitized ──

def test_zones_screen_sanitizes_dirty_source_field(monkeypatch):
    monkeypatch.setattr(bot.level_watch, "load_watch_zones",
                         lambda: {"updated": "2026-07-23", "source": "analyst_note_v2"})
    monkeypatch.setattr(bot, "get_top500", lambda: [])
    monkeypatch.setattr(bot, "_zones_collect_all", lambda: [])
    monkeypatch.setattr(bot, "_load_spot_plans", lambda: {})

    text, has_more = _run(bot._zones_screen_text())
    assert "analyst_note_v2" not in text


def test_zones_screen_clean_source_unaffected(monkeypatch):
    monkeypatch.setattr(bot.level_watch, "load_watch_zones",
                         lambda: {"updated": "2026-07-23", "source": "владелец"})
    monkeypatch.setattr(bot, "get_top500", lambda: [])
    monkeypatch.setattr(bot, "_zones_collect_all", lambda: [])
    monkeypatch.setattr(bot, "_load_spot_plans", lambda: {})

    text, has_more = _run(bot._zones_screen_text())
    assert "владелец" in text
