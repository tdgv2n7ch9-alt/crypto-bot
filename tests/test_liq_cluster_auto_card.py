"""
pytest для П-LiqCluster (владелец, ночное задание 14->15.07, Пакет 1 -- "да" на
§5 утреннего брифа 2026-07-14): строка кластеров ликвидаций в AUTO/точки-
карточках. NEXT_PACKAGE.md "Пункт 1" -- format_liquidation_cluster_line()
раньше вызывалась только для watchlist zone-touch алертов, не для
_build_signal_post(). Проверяет: (1) _fetch_auto_liq_line() строит зону из
entry1/entry3 корректно для long/short и не делает сеть напрямую (мокает
level_watch.format_liquidation_cluster_line); (2) _build_signal_post() вставляет
liq_line в текст карточки, когда передан, и не ломается без него (backward
compat -- существующие вызовы без liq_line не должны измениться в остальном).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot


def _run(coro):
    return asyncio.run(coro)


def _analysis(entry1=105.0, entry3=95.0, price=100.0):
    return {
        "price": price, "price_fresh": "", "rocket": 70, "rsi_4h": 45,
        "trend_4h": "bullish", "entry1": entry1, "entry3": entry3,
        "tp1": 110.0, "tp2": 115.0, "tp3": 120.0, "sl": 90.0, "rr": 2.0,
        "macd_bullish": True, "macd_bearish": False,
        "above_ema200": True, "above_ema50": True, "above_ema20": True,
        "st_label": "BUY", "smc_factors": [], "vol": 5_000_000, "rank": 50,
    }


# ── _fetch_auto_liq_line() ───────────────────────────────────────────────────

def test_fetch_auto_liq_line_builds_long_zone_entry3_lo_entry1_hi(monkeypatch):
    seen = {}

    def fake_format(symbol, zone, get_liq_data_fn):
        seen["symbol"] = symbol
        seen["zone"] = dict(zone)
        return "🗺 Ликвидации рядом (±1%): $500,000"

    monkeypatch.setattr(bot.level_watch, "format_liquidation_cluster_line", fake_format)
    a = _analysis(entry1=105.0, entry3=95.0)
    result = _run(bot._fetch_auto_liq_line("BTCUSDT", a, mode="long"))
    assert seen["symbol"] == "BTCUSDT"
    assert seen["zone"] == {"lo": 95.0, "hi": 105.0}
    assert result == "🗺 Ликвидации рядом (±1%): $500,000"


def test_fetch_auto_liq_line_builds_short_zone_entry1_lo_entry3_hi(monkeypatch):
    seen = {}

    def fake_format(symbol, zone, get_liq_data_fn):
        seen["zone"] = dict(zone)
        return "н/д"

    monkeypatch.setattr(bot.level_watch, "format_liquidation_cluster_line", fake_format)
    # SHORT: entry1 -- первый транш (ниже цены), entry3 -- дальний (выше) --
    # тот же конвеншен, что bot.py:6496 (e_lo,e_hi = a["entry1"],a["entry3"] для short).
    a = _analysis(entry1=95.0, entry3=105.0)
    _run(bot._fetch_auto_liq_line("ETHUSDT", a, mode="short"))
    assert seen["zone"] == {"lo": 95.0, "hi": 105.0}


def test_fetch_auto_liq_line_missing_entry_range_returns_na_without_calling_formatter(monkeypatch):
    called = []
    monkeypatch.setattr(bot.level_watch, "format_liquidation_cluster_line",
                         lambda *a, **kw: called.append(1))
    a = _analysis()
    del a["entry1"]
    result = _run(bot._fetch_auto_liq_line("BTCUSDT", a, mode="long"))
    assert "н/д" in result
    assert called == []


def test_fetch_auto_liq_line_swaps_lo_hi_if_inverted(monkeypatch):
    seen = {}

    def fake_format(symbol, zone, get_liq_data_fn):
        seen["zone"] = dict(zone)
        return "ok"

    monkeypatch.setattr(bot.level_watch, "format_liquidation_cluster_line", fake_format)
    # Намеренно "перевёрнутый" вход -- функция должна честно упорядочить lo<=hi,
    # не передавать зону с lo>hi дальше (find_liquidation_clusters_near_zone
    # молча даёт неверный результат на такой зоне).
    a = _analysis(entry1=90.0, entry3=100.0)  # long обычно entry3<entry1, здесь наоборот
    _run(bot._fetch_auto_liq_line("BTCUSDT", a, mode="long"))
    assert seen["zone"]["lo"] <= seen["zone"]["hi"]


def test_fetch_auto_liq_line_exception_in_formatter_returns_na_not_raise(monkeypatch):
    def boom(*a, **kw):
        raise ConnectionError("simulated network failure")
    monkeypatch.setattr(bot.level_watch, "format_liquidation_cluster_line", boom)
    a = _analysis()
    result = _run(bot._fetch_auto_liq_line("BTCUSDT", a, mode="long"))
    assert "н/д" in result


# ── _build_signal_post() liq_line insertion ──────────────────────────────────

def test_build_signal_post_includes_liq_line_when_provided():
    a = _analysis()
    text = bot._build_signal_post("BTCUSDT", a, {}, mode="long",
                                   liq_line="🗺 Ликвидации рядом (±1%): $1,200,000")
    assert "🗺 Ликвидации рядом (±1%): $1,200,000" in text


def test_build_signal_post_omits_liq_block_when_none():
    a = _analysis()
    text_without = bot._build_signal_post("BTCUSDT", a, {}, mode="long")
    text_with_empty = bot._build_signal_post("BTCUSDT", a, {}, mode="long", liq_line=None)
    assert text_without == text_with_empty
    assert "Ликвидации рядом" not in text_without


def test_build_signal_post_backward_compat_default_unchanged():
    """Существующие вызовы без liq_line (внутренние call sites bot.py передают
    голый symbol, "#{symbol}USDT" сам добавляет суффикс) не должны получить
    новый параметр по умолчанию, ломающий текст карточки."""
    a = _analysis()
    text = bot._build_signal_post("BTC", a, {}, mode="short")
    assert "#BTCUSDT" in text
