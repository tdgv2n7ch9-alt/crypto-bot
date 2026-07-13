"""
pytest для bot.check_watchlist_alerts_from_level_watch() -- "пятый движок"
унификация (владелец, 2026-07-13, см. ENGINE_UNIFICATION.md): zone-touch
алерты подписчикам теперь берут границы из journal/watch_zones.json
(level_watch.load_watch_zones()), а не из хардкода WATCHLIST_ZONES. Импорт
bot.py требует BOT_TOKEN в окружении (модуль не подключается к Telegram при
импорте, только при main()).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot
import level_watch


def _coin(symbol, price):
    return {"symbol": symbol, "quote": {"USDT": {"price": price}}}


def test_reads_boundaries_from_watch_zones_json_not_hardcode(monkeypatch, tmp_path):
    """Ключевой тест унификации: даже если WATCHLIST_ZONES говорит одно,
    новая функция берёт границы ИСКЛЮЧИТЕЛЬНО из watch_zones.json."""
    cfg = {
        "updated": "2026-07-13", "source": "Королев 13.07",
        "BTCUSDT": [{"side": "LONG", "lo": 61840.9, "hi": 62285.0, "note": "тест"}],
    }
    monkeypatch.setattr(level_watch, "load_watch_zones", lambda *a, **kw: cfg)
    coins = [_coin("BTC", 62000.0)]  # внутри новой зоны, вне старой WATCHLIST_ZONES (62000-63000 была до фикса)
    alerts = bot.check_watchlist_alerts_from_level_watch(coins)
    assert len(alerts) == 1
    assert alerts[0]["lo"] == 61840.9
    assert alerts[0]["hi"] == 62285.0
    assert alerts[0]["symbol"] == "BTC"
    assert alerts[0]["bias"] == "LONG"


def test_price_outside_zone_no_alert(monkeypatch):
    cfg = {"updated": "d", "source": "s",
           "BTCUSDT": [{"side": "LONG", "lo": 61840.9, "hi": 62285.0}]}
    monkeypatch.setattr(level_watch, "load_watch_zones", lambda *a, **kw: cfg)
    coins = [_coin("BTC", 70000.0)]
    assert bot.check_watchlist_alerts_from_level_watch(coins) == []


def test_info_zones_skipped_not_alerted(monkeypatch):
    cfg = {"updated": "d", "source": "s",
           "BTCUSDT": [{"side": "INFO", "lo": 63239.3, "hi": 63239.3, "note": "broken"}]}
    monkeypatch.setattr(level_watch, "load_watch_zones", lambda *a, **kw: cfg)
    coins = [_coin("BTC", 63239.3)]
    assert bot.check_watchlist_alerts_from_level_watch(coins) == []


def test_multiple_zones_per_symbol_only_matching_one_alerts(monkeypatch):
    cfg = {"updated": "d", "source": "s", "BTCUSDT": [
        {"side": "LONG", "lo": 61840.9, "hi": 62285.0, "note": "zone1"},
        {"side": "SHORT", "lo": 66925.0, "hi": 67130.9, "note": "zone2"},
    ]}
    monkeypatch.setattr(level_watch, "load_watch_zones", lambda *a, **kw: cfg)
    coins = [_coin("BTC", 62000.0)]
    alerts = bot.check_watchlist_alerts_from_level_watch(coins)
    assert len(alerts) == 1
    assert alerts[0]["note"] == "zone1"


def test_symbol_not_in_coins_skipped_gracefully(monkeypatch):
    cfg = {"updated": "d", "source": "s",
           "NOSUCHUSDT": [{"side": "LONG", "lo": 1, "hi": 2}]}
    monkeypatch.setattr(level_watch, "load_watch_zones", lambda *a, **kw: cfg)
    assert bot.check_watchlist_alerts_from_level_watch([_coin("BTC", 62000.0)]) == []


def test_meta_keys_updated_source_not_treated_as_symbols(monkeypatch):
    cfg = {"updated": "2026-07-13", "source": "Королев 13.07",
           "BTCUSDT": [{"side": "LONG", "lo": 61840.9, "hi": 62285.0}]}
    monkeypatch.setattr(level_watch, "load_watch_zones", lambda *a, **kw: cfg)
    alerts = bot.check_watchlist_alerts_from_level_watch([_coin("BTC", 62000.0)])
    assert all(a["symbol"] != "updated" and a["symbol"] != "source" for a in alerts)


def test_source_field_propagated_from_config(monkeypatch):
    cfg = {"updated": "2026-07-13", "source": "Королев 13.07",
           "BTCUSDT": [{"side": "LONG", "lo": 61840.9, "hi": 62285.0}]}
    monkeypatch.setattr(level_watch, "load_watch_zones", lambda *a, **kw: cfg)
    alerts = bot.check_watchlist_alerts_from_level_watch([_coin("BTC", 62000.0)])
    assert alerts[0]["source"] == "Королев 13.07"


def test_zones_unified_flag_switches_source(monkeypatch):
    """Флаг ZONES_UNIFIED=False -- мгновенный откат на старый WATCHLIST_ZONES
    (без редеплоя, только смена значения переменной окружения на Railway)."""
    monkeypatch.setattr(bot, "ZONES_UNIFIED", True)
    called = {"new": False, "old": False}
    monkeypatch.setattr(bot, "check_watchlist_alerts_from_level_watch",
                         lambda coins: called.__setitem__("new", True) or [])
    monkeypatch.setattr(bot, "check_watchlist_alerts",
                         lambda coins: called.__setitem__("old", True) or [])
    monkeypatch.setattr(bot, "watchlist_alerted", {})

    import asyncio

    class _FakeBot:
        async def send_message(self, *a, **kw):
            pass

    asyncio.run(bot.check_watchlist(_FakeBot(), {123}, []))
    assert called["new"] is True
    assert called["old"] is False

    called["new"] = called["old"] = False
    monkeypatch.setattr(bot, "ZONES_UNIFIED", False)
    asyncio.run(bot.check_watchlist(_FakeBot(), {123}, []))
    assert called["new"] is False
    assert called["old"] is True


def test_zones_set_style_config_change_reflected_without_restart(monkeypatch):
    """load_watch_zones() читает диск на КАЖДЫЙ вызов (см. level_watch.py) --
    /zones_set-подобное изменение подхватывается немедленно, без рестарта
    процесса. Тест эмулирует это через monkeypatch двух последовательных
    конфигов на одном and том же объекте-функции."""
    state = {"cfg": {"updated": "d1", "source": "s1",
                      "BTCUSDT": [{"side": "LONG", "lo": 100, "hi": 200}]}}
    monkeypatch.setattr(level_watch, "load_watch_zones", lambda *a, **kw: state["cfg"])

    coins = [_coin("BTC", 150.0)]
    alerts_before = bot.check_watchlist_alerts_from_level_watch(coins)
    assert len(alerts_before) == 1
    assert alerts_before[0]["lo"] == 100

    # эмулируем /zones_set -- конфиг поменялся "на диске"
    state["cfg"] = {"updated": "d2", "source": "s2",
                     "BTCUSDT": [{"side": "LONG", "lo": 140, "hi": 160}]}
    alerts_after = bot.check_watchlist_alerts_from_level_watch(coins)
    assert len(alerts_after) == 1
    assert alerts_after[0]["lo"] == 140
