"""
pytest для Пакет 18, п.3 (владелец, кейс LAB DUMP 20:55: rug-строка ушла
пустой из-за 429 на CoinGecko в момент отправки алерта). get_cached_rug_line()
-- для символов watch_zones читает часовой кэш вместо живого фетча в момент
алерта; для символов вне watch_zones поведение НЕ меняется (живой фетч,
как раньше). refresh_watch_zones_rug_cache() -- почасовая job, наполняющая
кэш best-effort по каждому символу отдельно.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import asyncio
import bot


def _reset_cache():
    bot._RUG_LINE_CACHE.clear()


def test_symbol_outside_watch_zones_always_live_fetch(monkeypatch):
    """Символ, которого нет в watch_zones.json -- поведение не меняется:
    прямой вызов format_watchlist_rug_line() на каждый раз, кэш не трогается."""
    _reset_cache()
    monkeypatch.setattr(bot, "_watch_zone_symbol_set", lambda: {"BTCUSDT"})
    calls = []

    def _fake_live(symbol, coin):
        calls.append(symbol)
        return f"live:{symbol}"

    monkeypatch.setattr(bot, "format_watchlist_rug_line", _fake_live)

    r1 = bot.get_cached_rug_line("ETHUSDT", {})
    r2 = bot.get_cached_rug_line("ETHUSDT", {})

    assert r1 == "live:ETHUSDT"
    assert r2 == "live:ETHUSDT"
    assert calls == ["ETHUSDT", "ETHUSDT"]  # каждый раз живой фетч, кэш не участвует
    assert "ETHUSDT" not in bot._RUG_LINE_CACHE


def test_watch_zone_symbol_reads_from_warm_cache_without_live_fetch(monkeypatch):
    """Кэш уже прогрет -- вызов НЕ должен обращаться к живому фетчу вообще
    (это и есть фикс кейса LAB: алерт не ждёт сеть и не ловит 429)."""
    _reset_cache()
    monkeypatch.setattr(bot, "_watch_zone_symbol_set", lambda: {"LABUSDT"})
    bot._RUG_LINE_CACHE["LABUSDT"] = (time.time(), "🛑 RUG-RADAR: 55 — cached")

    calls = []
    monkeypatch.setattr(bot, "format_watchlist_rug_line",
                         lambda s, c: calls.append(s) or "SHOULD_NOT_BE_CALLED")

    result = bot.get_cached_rug_line("LABUSDT", {})
    assert result == "🛑 RUG-RADAR: 55 — cached"
    assert calls == []


def test_watch_zone_symbol_cold_cache_does_one_off_live_fetch_and_fills_cache(monkeypatch):
    """Символ добавлен в watch_zones между часовыми прогонами -- честный
    разовый живой фетч (не показывать пустоту до следующего прогона),
    результат сразу попадает в кэш."""
    _reset_cache()
    monkeypatch.setattr(bot, "_watch_zone_symbol_set", lambda: {"ONDOUSDT"})
    monkeypatch.setattr(bot, "format_watchlist_rug_line", lambda s, c: "🛑 fresh")

    result = bot.get_cached_rug_line("ONDOUSDT", {})
    assert result == "🛑 fresh"
    assert "ONDOUSDT" in bot._RUG_LINE_CACHE
    assert bot._RUG_LINE_CACHE["ONDOUSDT"][1] == "🛑 fresh"


def test_refresh_job_populates_cache_for_all_watch_zone_symbols(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(bot, "_watch_zone_symbol_set", lambda: {"AVAXUSDT", "SOLUSDT"})
    monkeypatch.setattr(bot, "get_top500", lambda: [
        {"symbol": "AVAXUSDT", "quote": {"USDT": {"market_cap": 1e9, "volume_24h": 1e7,
                                                    "percent_change_30d": 5}}},
    ])
    monkeypatch.setattr(bot, "format_watchlist_rug_line", lambda s, c: f"line:{s}")

    asyncio.run(bot.refresh_watch_zones_rug_cache())

    assert set(bot._RUG_LINE_CACHE.keys()) == {"AVAXUSDT", "SOLUSDT"}
    assert bot._RUG_LINE_CACHE["AVAXUSDT"][1] == "line:AVAXUSDT"
    assert bot._RUG_LINE_CACHE["SOLUSDT"][1] == "line:SOLUSDT"


def test_refresh_job_one_symbol_failure_does_not_break_others(monkeypatch):
    """Best-effort: ошибка/429 на одном символе не должна ронять весь прогон
    -- следующий символ всё равно кэшируется."""
    _reset_cache()
    monkeypatch.setattr(bot, "_watch_zone_symbol_set", lambda: {"BADUSDT", "GOODUSDT"})
    monkeypatch.setattr(bot, "get_top500", lambda: [])

    def _flaky(symbol, coin):
        if symbol == "BADUSDT":
            raise RuntimeError("429")
        return "ok"

    monkeypatch.setattr(bot, "format_watchlist_rug_line", _flaky)

    asyncio.run(bot.refresh_watch_zones_rug_cache())

    assert "BADUSDT" not in bot._RUG_LINE_CACHE
    assert bot._RUG_LINE_CACHE["GOODUSDT"][1] == "ok"


def test_refresh_job_noop_when_watch_zones_empty(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(bot, "_watch_zone_symbol_set", lambda: set())
    asyncio.run(bot.refresh_watch_zones_rug_cache())
    assert bot._RUG_LINE_CACHE == {}


def test_check_watchlist_symbol_form_matches_watch_zone_set(monkeypatch):
    """Регрессия: check_watchlist_alerts_from_level_watch() отдаёт al["symbol"]
    БЕЗ суффикса USDT (см. её докстринг -- base_sym), а watch_zones.json хранит
    ключи С суффиксом ("AVAXUSDT"). Если _watch_zone_symbol_set() однажды
    забудет снять суффикс, get_cached_rug_line() для check_watchlist() будет
    ВСЕГДА мимо кэша -- ровно тот баг, что был найден и исправлен в этом
    пакете при первом прогоне тестов."""
    _reset_cache()
    real_config = {"AVAXUSDT": [{"side": "LONG", "lo": 1, "hi": 2}], "updated": "x", "source": "y"}
    monkeypatch.setattr(bot.level_watch, "load_watch_zones", lambda: real_config)

    zone_set = bot._watch_zone_symbol_set()
    assert zone_set == {"AVAX"}

    calls = []
    monkeypatch.setattr(bot, "format_watchlist_rug_line",
                         lambda s, c: calls.append(s) or "🛑 x")
    bot._RUG_LINE_CACHE["AVAX"] = (time.time(), "🛑 cached")

    # al["symbol"] приходит БЕЗ USDT -- как из check_watchlist_alerts_from_level_watch
    result = bot.get_cached_rug_line("AVAX", {})
    assert result == "🛑 cached"
    assert calls == []  # кэш реально попал в дело, живой фетч не вызывался


def test_watch_zone_symbol_set_reads_real_journal_file():
    """Живая сверка (не мок): текущий watch_zones.json действительно
    содержит символы -- страховка, что _watch_zone_symbol_set() не тихо
    ломается на реальном файле. Базовый символ без USDT (см. докстринг
    _watch_zone_symbol_set -- матчится с тем, что реально передают
    вызывающие: check_watchlist через al["symbol"], Whale/Supertrend через
    coin["symbol"], Памп-радар через pump_detector)."""
    zones = bot._watch_zone_symbol_set()
    assert isinstance(zones, set)
    assert len(zones) > 0
    assert "updated" not in zones and "source" not in zones
    assert not any(s.endswith("USDT") for s in zones)
    assert "BTC" in zones  # BTCUSDT в watch_zones.json на момент Пакета 18
