"""
pytest для Пакет 18, п.6 (владелец, живые кейсы в ленте ТОЧКИ):
(а) AVAX "н/д" -- единственный источник цены (get_binance_24h) без фоллбека,
    а у SPOT-записей WATCHLIST_ZONES вообще нет числового "entry";
(б) ETH "1,610" -- при сбое живого фетча entry (старая цена входа) тихо
    показывался как будто текущая цена, без пометки;
(в) светофор "ETH 73 зелёный vs LINK 73 жёлтый" -- светофор кодирует
    положение цены относительно зоны входа, НЕ Rocket Score -- совпадение
    цвета со счётом случайно, легенда в шапке ленты снимает путаницу.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot
import live_prices


def _coin(symbol, price=100.0):
    return {"symbol": symbol, "slug": symbol.lower(),
            "quote": {"USDT": {"price": price}}}


def _no_fa(monkeypatch):
    async def _fake(symbol, coin):
        return None
    monkeypatch.setattr(bot, "_get_fa_engine_result_cached", _fake)


def test_spot_item_without_numeric_entry_gets_price_from_coingecko_fallback(monkeypatch):
    """Кейс AVAX: SPOT-запись WATCHLIST_ZONES не имеет "entry" -- раньше
    единственный источник цены (get_binance_24h) при сбое давал "н/д"
    без фоллбека. Теперь live_prices.resolve_price() с CoinGecko-фоллбеком
    из get_top500() -- цена не пропадает даже без живого WS-тика."""
    _no_fa(monkeypatch)
    monkeypatch.setattr(bot, "get_top500", lambda: [_coin("AVAX", price=21.5)])
    monkeypatch.setattr(live_prices, "get_live_price", lambda sym: (None, None))

    item = {"symbol": "AVAX", "direction": "LONG", "kind": "spot",
            "sig": {"note": "test"}}  # намеренно БЕЗ "entry" -- как реальный WATCHLIST_ZONES spot
    text = asyncio.run(bot._mv2_tochki_row_text(item))

    # цена (не "Сила" -- score честно н/д, т.к. fa_engine замокан на None,
    # это отдельная, не связанная с этим тестом честность) обязана быть
    # реальным числом, а не "н/д"
    price_part = text.split(" — ")[1]
    assert "н/д" not in price_part
    assert "21.5" in text or "21,5" in text


def test_no_live_and_no_coingecko_price_shows_honest_na(monkeypatch):
    """Если ДЕЙСТВИТЕЛЬНО нет ни WS, ни CoinGecko-цены -- честное "н/д",
    не выдумываем число."""
    _no_fa(monkeypatch)
    monkeypatch.setattr(bot, "get_top500", lambda: [])  # символа нет в top500
    monkeypatch.setattr(live_prices, "get_live_price", lambda sym: (None, None))

    item = {"symbol": "UNKNOWNCOIN", "direction": "LONG", "kind": "spot", "sig": {}}
    text = asyncio.run(bot._mv2_tochki_row_text(item))
    assert "н/д" in text


def test_futures_item_stale_entry_never_shown_as_current_price(monkeypatch):
    """Кейс ETH "1,610": фьючерс-сигнал с entry=1610, живой фетч не даёт
    актуальной цены -- CoinGecko-цена (реальная, свежая) ОБЯЗАНА показаться
    вместо entry, и никогда без пометки свежести."""
    _no_fa(monkeypatch)
    monkeypatch.setattr(bot, "get_top500", lambda: [_coin("ETH", price=1780.0)])
    monkeypatch.setattr(live_prices, "get_live_price", lambda sym: (None, None))

    item = {"symbol": "ETH", "direction": "SHORT", "kind": "fut",
            "sig": {"entry": 1610.0}}
    text = asyncio.run(bot._mv2_tochki_row_text(item))

    # реальная (CoinGecko-фоллбек) цена показана, а не голая entry-цифра
    assert "1780" in text or "1,780" in text
    # обязана быть пометка свежести (отложенная/live) -- не голое число
    assert "(" in text and ")" in text


def test_futures_item_with_live_ws_price_shows_live_label(monkeypatch):
    _no_fa(monkeypatch)
    monkeypatch.setattr(bot, "get_top500", lambda: [_coin("ETH", price=1780.0)])
    monkeypatch.setattr(live_prices, "get_live_price", lambda sym: (1795.0, 3.0))

    item = {"symbol": "ETH", "direction": "SHORT", "kind": "fut",
            "sig": {"entry": 1610.0}}
    text = asyncio.run(bot._mv2_tochki_row_text(item))

    assert "1795" in text or "1,795" in text
    assert "live" in text
    assert "1610" not in text and "1,610" not in text


def test_tochki_header_has_traffic_light_legend(monkeypatch):
    """Кейс 'ETH 73 зелёный vs LINK 73 жёлтый': светофор не про score --
    легенда в шапке ленты снимает путаницу."""
    monkeypatch.setattr(bot, "TOP_LONG_SIGNALS", {})
    monkeypatch.setattr(bot, "TOP_SHORT_SIGNALS", {})
    monkeypatch.setattr(bot, "SPOT_PORTFOLIO", {})

    class _FakeBot:
        async def edit_message_text(self, *a, **kw):
            pass

    class _Q:
        def __init__(self):
            self.texts = []

        async def edit_message_text(self, text, **kw):
            self.texts.append(text)

    q = _Q()
    asyncio.run(bot._mv2_render_tochki(q, 1, 1))
    assert q.texts
    text = q.texts[0]
    assert "не Rocket Score" in text
    assert "🟢" in text and "🟡" in text and "🔴" in text
