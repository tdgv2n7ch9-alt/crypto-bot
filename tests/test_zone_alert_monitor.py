"""
pytest для zone_alert_monitor.py (владелец, наряды KAITOUSDT SHORT / AVAXUSDT LONG,
2026-07-15) -- generic конфиг-driven движок. Покрывает: touch/close_below/close_above
триггеры, дедуп "1 раз до сброса >2%", профильную строку на первый алерт, и
распространение фикса #240 (run_in_executor, dead-source tracking, honest notify).
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import zone_alert_monitor as zam


def _run(coro):
    return asyncio.run(coro)


def _candle(ts, o, h, l, c):
    return {"ts": ts, "o": o, "h": h, "l": l, "c": c, "v": 0.0}


def _fresh_state_file(monkeypatch, tmp_path, symbol="TESTUSDT"):
    monkeypatch.setattr(zam, "_state_file", lambda sym: str(tmp_path / f"{sym.lower()}_state.json"))


TRIGGERS = [
    {"name": "touch1", "type": "touch", "level": 1.0, "timeframe": "15", "text": "touch1 fired"},
    {"name": "inval", "type": "close_below", "level": 0.8, "timeframe": "60", "text": "inval fired"},
]


def test_first_run_only_bookmarks(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    candles15 = [_candle(1000, 1.0, 1.01, 0.99, 1.0)]
    candles60 = [_candle(1000, 1.0, 1.01, 0.99, 1.0)]

    def fake_klines(symbol, tf, limit=40, dead_sources=None):
        return (candles15 if tf == "15" else candles60), "bybit"
    monkeypatch.setattr(zam, "get_klines", fake_klines)

    sent = []
    async def fake_send(bot, text, critical=False):
        sent.append((text, critical))

    result = _run(zam.check_zone("TESTUSDT", TRIGGERS, "profile-line", bot=None, send_system_fn=fake_send))
    assert result == []
    assert sent == []
    state = zam._load_state("TESTUSDT", ["touch1", "inval"])
    assert state["last_closed_15m_ts"] == 1000
    assert state["last_closed_1h_ts"] == 1000


def test_touch_trigger_fires_and_includes_profile_line_once(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    zam._save_state("TESTUSDT", {**zam._default_state(["touch1", "inval"]),
                                   "last_closed_15m_ts": 1000, "last_closed_1h_ts": 1000})

    def fake_klines(symbol, tf, limit=40, dead_sources=None):
        if tf == "15":
            return [_candle(2000, 0.99, 1.02, 0.98, 1.0)], "bybit"  # low<=1.0<=high -- touch
        return [], "bybit"
    monkeypatch.setattr(zam, "get_klines", fake_klines)

    sent = []
    async def fake_send(bot, text, critical=False):
        sent.append((text, critical))

    result = _run(zam.check_zone("TESTUSDT", TRIGGERS, "profile-line", bot=None, send_system_fn=fake_send))
    assert result == ["touch1"]
    assert sent[0][1] is True
    assert "profile-line" in sent[0][0]
    state = zam._load_state("TESTUSDT", ["touch1", "inval"])
    assert state["profile_sent"] is True


def test_close_below_fires_for_invalidation(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    zam._save_state("TESTUSDT", {**zam._default_state(["touch1", "inval"]),
                                   "last_closed_15m_ts": 1000, "last_closed_1h_ts": 1000})

    def fake_klines(symbol, tf, limit=40, dead_sources=None):
        if tf == "60":
            return [_candle(5000, 0.82, 0.83, 0.78, 0.79)], "bybit"  # close < 0.8
        return [], "bybit"
    monkeypatch.setattr(zam, "get_klines", fake_klines)

    sent = []
    async def fake_send(bot, text, critical=False):
        sent.append((text, critical))

    result = _run(zam.check_zone("TESTUSDT", TRIGGERS, "", bot=None, send_system_fn=fake_send))
    assert result == ["inval"]


def test_dedup_no_refire_without_2pct_reset(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    state = zam._default_state(["touch1", "inval"])
    state["armed"]["touch1"] = False
    state["last_closed_15m_ts"] = 1000
    state["last_closed_1h_ts"] = 1000
    zam._save_state("TESTUSDT", state)

    def fake_klines(symbol, tf, limit=40, dead_sources=None):
        if tf == "15":
            return [_candle(2000, 0.99, 1.005, 0.995, 1.0)], "bybit"  # снова касание, без отхода >2%
        return [], "bybit"
    monkeypatch.setattr(zam, "get_klines", fake_klines)

    result = _run(zam.check_zone("TESTUSDT", TRIGGERS, "", bot=None, send_system_fn=lambda *a, **k: None))
    assert "touch1" not in result


def test_dedup_refires_after_2pct_reset(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    state = zam._default_state(["touch1", "inval"])
    state["armed"]["touch1"] = False
    state["last_closed_15m_ts"] = 1000
    state["last_closed_1h_ts"] = 1000
    zam._save_state("TESTUSDT", state)

    def fake_klines(symbol, tf, limit=40, dead_sources=None):
        if tf == "15":
            return [
                _candle(2000, 1.02, 1.03, 1.021, 1.025),  # отход >2% выше 1.0 -- сброс
                _candle(2900, 0.99, 1.01, 0.98, 1.0),       # новое касание
            ], "bybit"
        return [], "bybit"
    monkeypatch.setattr(zam, "get_klines", fake_klines)

    sent = []
    async def fake_send(bot, text, critical=False):
        sent.append((text, critical))

    result = _run(zam.check_zone("TESTUSDT", TRIGGERS, "", bot=None, send_system_fn=fake_send))
    assert "touch1" in result


# --- Распространение фикса #240: run_in_executor, dead-source tracking, honest notify ---

def test_get_klines_dead_source_not_retried_within_tick(monkeypatch):
    calls = {"bybit": 0, "bingx": 0}

    def fake_bybit(symbol, interval, limit):
        calls["bybit"] += 1
        raise RuntimeError("bybit down")

    def fake_bingx(symbol, interval, limit):
        calls["bingx"] += 1
        return [], "bingx"

    monkeypatch.setattr(zam, "_fetch_klines_bybit", fake_bybit)
    monkeypatch.setattr(zam, "_fetch_klines_bingx", fake_bingx)

    dead_sources = set()
    zam.get_klines("TESTUSDT", "15", 40, dead_sources)
    zam.get_klines("TESTUSDT", "60", 40, dead_sources)  # тот же тик
    assert calls["bybit"] == 1
    assert calls["bingx"] == 2


def test_check_zone_uses_run_in_executor(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    zam._save_state("TESTUSDT", {**zam._default_state(["touch1", "inval"]),
                                   "last_closed_15m_ts": 1000, "last_closed_1h_ts": 1000})

    executor_calls = []

    async def fake_run_in_executor(fn, *args):
        executor_calls.append(fn.__name__)
        return fn(*args)

    def _mk(name, real):
        real.__name__ = name
        return real

    monkeypatch.setattr(zam, "get_klines",
                         _mk("get_klines", lambda symbol, tf, limit=40, dead_sources=None: ([], "bybit")))

    _run(zam.check_zone("TESTUSDT", TRIGGERS, "", bot=None, send_system_fn=lambda *a, **k: None,
                         run_in_executor_fn=fake_run_in_executor))
    assert executor_calls.count("get_klines") == len({"15", "60"})


def test_check_zone_honest_notify_when_all_timeframes_fail(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    zam._save_state("TESTUSDT", {**zam._default_state(["touch1", "inval"]),
                                   "last_closed_15m_ts": 1000, "last_closed_1h_ts": 1000})

    def fake_klines(symbol, tf, limit=40, dead_sources=None):
        raise RuntimeError("все источники недоступны")
    monkeypatch.setattr(zam, "get_klines", fake_klines)

    sent = []
    async def fake_send(bot, text, critical=False):
        sent.append((text, critical))

    result = _run(zam.check_zone("TESTUSDT", TRIGGERS, "", bot=None, send_system_fn=fake_send))
    assert result == []
    assert len(sent) == 1
    assert sent[0][1] is True
    assert "источники свечей" in sent[0][0]


def test_check_zone_notify_not_spammed_every_tick(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    state = {**zam._default_state(["touch1", "inval"]),
             "last_closed_15m_ts": 1000, "last_closed_1h_ts": 1000,
             "last_source_down_notify_ts": time.time()}
    zam._save_state("TESTUSDT", state)

    def fake_klines(symbol, tf, limit=40, dead_sources=None):
        raise RuntimeError("все источники недоступны")
    monkeypatch.setattr(zam, "get_klines", fake_klines)

    sent = []
    async def fake_send(bot, text, critical=False):
        sent.append((text, critical))

    _run(zam.check_zone("TESTUSDT", TRIGGERS, "", bot=None, send_system_fn=fake_send))
    assert sent == []


def test_check_all_zones_iterates_registry(monkeypatch, tmp_path):
    """check_all_zones -- один heartbeat на весь движок, проходит по KAITO+AVAX
    из zone_alert_configs.py."""
    _fresh_state_file(monkeypatch, tmp_path)

    calls = []

    async def fake_check_zone(symbol, triggers, profile_line, bot, send_system_fn=None,
                               scalp_ctx=None, run_in_executor_fn=None):
        calls.append(symbol)
        return []

    monkeypatch.setattr(zam, "check_zone", fake_check_zone)
    result = _run(zam.check_all_zones(bot=None, send_system_fn=lambda *a, **k: None))
    assert "KAITOUSDT" in calls
    assert "AVAXUSDT" in calls
    assert result == {"KAITOUSDT": [], "AVAXUSDT": []}


def test_check_all_zones_one_symbol_failure_does_not_block_others(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)

    async def flaky_check_zone(symbol, triggers, profile_line, bot, send_system_fn=None,
                                scalp_ctx=None, run_in_executor_fn=None):
        if symbol == "KAITOUSDT":
            raise RuntimeError("boom")
        return ["ok"]

    monkeypatch.setattr(zam, "check_zone", flaky_check_zone)
    result = _run(zam.check_all_zones(bot=None, send_system_fn=lambda *a, **k: None))
    assert result["KAITOUSDT"] == []  # честный skip, не падение всего джоба
    assert result["AVAXUSDT"] == ["ok"]
