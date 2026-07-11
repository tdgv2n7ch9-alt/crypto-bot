"""
pytest для bot._make_whale_log_fn() -- решение "слать ли алерт владельцу" для
события Whale Radar Блок 3 (крупная лимитка >=$200K в пределах 3% от цены на
активном сигнале). Персистентность (append_event) и решение об алерте проверяются
раздельно -- падение/пропуск одного не должно влиять на другое.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot


def _order_event(symbol="BTCUSDT", side="bid", size_usd=250_000.0, distance_pct=1.0,
                  event="appeared", event_type="whale_order"):
    return {
        "type": event_type, "symbol": symbol, "side": side, "price": 100.0,
        "size_usd": size_usd, "event": event, "distance_pct": distance_pct,
        "ts": time.time(),
    }


def _reset(monkeypatch):
    monkeypatch.setattr(bot, "TOP_LONG_SIGNALS", {}, raising=False)
    monkeypatch.setattr(bot, "TOP_SHORT_SIGNALS", {}, raising=False)
    bot._whale_alert_cooldown.clear()
    monkeypatch.setattr(bot.whale_radar, "append_event", lambda e: None)


def test_alert_skipped_below_min_usd(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(bot, "TOP_LONG_SIGNALS", {"BTC": {"status": "active"}})
    created = []
    monkeypatch.setattr(bot.asyncio, "create_task", lambda coro: created.append(coro))
    log_fn = bot._make_whale_log_fn(bot=None, owner_id=1)
    log_fn(_order_event(size_usd=100_000.0))
    assert created == []


def test_alert_skipped_beyond_distance(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(bot, "TOP_LONG_SIGNALS", {"BTC": {"status": "active"}})
    created = []
    monkeypatch.setattr(bot.asyncio, "create_task", lambda coro: created.append(coro))
    log_fn = bot._make_whale_log_fn(bot=None, owner_id=1)
    log_fn(_order_event(distance_pct=5.0))
    assert created == []


def test_alert_skipped_when_symbol_not_active(monkeypatch):
    _reset(monkeypatch)
    created = []
    monkeypatch.setattr(bot.asyncio, "create_task", lambda coro: created.append(coro))
    log_fn = bot._make_whale_log_fn(bot=None, owner_id=1)
    log_fn(_order_event())  # BTC not in any signal dict
    assert created == []


def test_alert_skipped_when_signal_not_status_active(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(bot, "TOP_LONG_SIGNALS", {"BTC": {"status": "watching"}})
    created = []
    monkeypatch.setattr(bot.asyncio, "create_task", lambda coro: created.append(coro))
    log_fn = bot._make_whale_log_fn(bot=None, owner_id=1)
    log_fn(_order_event())
    assert created == []


def test_alert_skipped_for_non_appeared_event(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(bot, "TOP_LONG_SIGNALS", {"BTC": {"status": "active"}})
    created = []
    monkeypatch.setattr(bot.asyncio, "create_task", lambda coro: created.append(coro))
    log_fn = bot._make_whale_log_fn(bot=None, owner_id=1)
    log_fn(_order_event(event="disappeared"))
    assert created == []


def test_alert_skipped_for_whale_trade_type(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(bot, "TOP_LONG_SIGNALS", {"BTC": {"status": "active"}})
    created = []
    monkeypatch.setattr(bot.asyncio, "create_task", lambda coro: created.append(coro))
    log_fn = bot._make_whale_log_fn(bot=None, owner_id=1)
    log_fn(_order_event(event_type="whale_trade"))
    assert created == []


def test_alert_fires_when_all_criteria_met_long_signal(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(bot, "TOP_LONG_SIGNALS", {"BTC": {"status": "active"}})
    created = []
    monkeypatch.setattr(bot.asyncio, "create_task", lambda coro: created.append(coro))
    log_fn = bot._make_whale_log_fn(bot=None, owner_id=1)
    log_fn(_order_event())
    assert len(created) == 1
    created[0].close()  # avoid "coroutine was never awaited" warning


def test_alert_fires_for_short_signal_too(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(bot, "TOP_SHORT_SIGNALS", {"BTC": {"status": "active"}})
    created = []
    monkeypatch.setattr(bot.asyncio, "create_task", lambda coro: created.append(coro))
    log_fn = bot._make_whale_log_fn(bot=None, owner_id=1)
    log_fn(_order_event())
    assert len(created) == 1
    created[0].close()


def test_alert_cooldown_suppresses_repeat(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(bot, "TOP_LONG_SIGNALS", {"BTC": {"status": "active"}})
    created = []
    monkeypatch.setattr(bot.asyncio, "create_task", lambda coro: created.append(coro))
    log_fn = bot._make_whale_log_fn(bot=None, owner_id=1)
    log_fn(_order_event())
    log_fn(_order_event())  # same (symbol, side), within cooldown window
    assert len(created) == 1
    created[0].close()


def test_persistence_always_called_even_when_alert_skipped(monkeypatch):
    _reset(monkeypatch)
    persisted = []
    monkeypatch.setattr(bot.whale_radar, "append_event", lambda e: persisted.append(e))
    log_fn = bot._make_whale_log_fn(bot=None, owner_id=1)
    log_fn(_order_event(size_usd=1.0))  # far below threshold -- no alert
    assert len(persisted) == 1
