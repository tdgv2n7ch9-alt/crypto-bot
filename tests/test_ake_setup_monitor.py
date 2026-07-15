"""
pytest для ake_setup_monitor.py (владелец, СРОЧНЫЙ наряд вне очереди, 2026-07-15,
владелец В ПОЗИЦИИ шорт): 5 независимых триггеров с дедупом "1 раз до сброса >2%".
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ake_setup_monitor as asm


def _run(coro):
    return asyncio.run(coro)


def _candle(ts, o, h, l, c):
    return {"ts": ts, "o": o, "h": h, "l": l, "c": c}


def _fresh_state_file(monkeypatch, tmp_path):
    monkeypatch.setattr(asm, "STATE_FILE", str(tmp_path / "ake_state.json"))
    monkeypatch.setattr(asm, "BSC_EVENTS_FILE", str(tmp_path / "bsc_events_missing.json"))


def _seed_state(ts):
    return {**asm._default_state(), "last_closed_15m_ts": ts}


def test_first_run_only_bookmarks(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    monkeypatch.setattr(asm, "get_klines", lambda interval, limit=3, dead_sources=None: ([_candle(1000, 0.0006, 0.0006, 0.0006, 0.0006)], "bybit"))
    sent = []
    result = _run(asm.check_ake_setup(bot=None, send_system_fn=lambda *a, **k: sent.append(a)))
    assert result == []
    assert sent == []


def test_a1_test_alert_fires_on_touch(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    asm._save_state(_seed_state(1000))

    def fake_klines(interval, limit=3, dead_sources=None):
        if interval == "15":
            return [_candle(2000, 0.00068, 0.00071, 0.00067, 0.00069)], "bybit"  # high >= 0.0007
        return [], "bybit"
    monkeypatch.setattr(asm, "get_klines", fake_klines)

    sent = []
    async def fake_send(bot, text, critical=False):
        sent.append((text, critical))
    result = _run(asm.check_ake_setup(bot=None, send_system_fn=fake_send))
    assert "test_0007" in result
    assert sent[0][1] is True
    assert "0.0007" in sent[0][0]


def test_a1_does_not_refire_without_2pct_reset(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    state = _seed_state(1000)
    state["armed"]["test_0007"] = False  # уже сработал недавно
    asm._save_state(state)

    def fake_klines(interval, limit=3, dead_sources=None):
        if interval == "15":
            return [_candle(2000, 0.00069, 0.00071, 0.00068, 0.0007)], "bybit"  # снова касание, но БЕЗ отхода >2%
        return [], "bybit"
    monkeypatch.setattr(asm, "get_klines", fake_klines)

    result = _run(asm.check_ake_setup(bot=None, send_system_fn=lambda *a, **k: None))
    assert "test_0007" not in result


def test_a1_refires_after_2pct_reset(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    state = _seed_state(1000)
    state["armed"]["test_0007"] = False
    asm._save_state(state)

    def fake_klines(interval, limit=3, dead_sources=None):
        if interval == "15":
            return [
                _candle(2000, 0.00069, 0.00069, 0.000682, 0.000685),  # отход >2% ниже 0.0007 -> сброс
                _candle(2900, 0.00069, 0.00071, 0.00068, 0.0007),      # новое касание -> должно сработать
            ], "bybit"
        return [], "bybit"
    monkeypatch.setattr(asm, "get_klines", fake_klines)

    sent = []
    async def fake_send(bot, text, critical=False):
        sent.append((text, critical))
    result = _run(asm.check_ake_setup(bot=None, send_system_fn=fake_send))
    assert "test_0007" in result


def test_a2_confirm_below_fires_on_close(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    asm._save_state(_seed_state(1000))

    def fake_klines(interval, limit=3, dead_sources=None):
        if interval == "15":
            return [_candle(2000, 0.00071, 0.00071, 0.00068, 0.00069)], "bybit"  # close < 0.0007
        return [], "bybit"
    monkeypatch.setattr(asm, "get_klines", fake_klines)

    sent = []
    async def fake_send(bot, text, critical=False):
        sent.append((text, critical))
    result = _run(asm.check_ake_setup(bot=None, send_system_fn=fake_send))
    assert "confirm_below_0007" in result
    assert "0.000246" in sent[-1][0] or any("0.000246" in t for t, c in sent)


def test_a3_low_sweep_wick_vs_close_labeled(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    asm._save_state(_seed_state(1000))

    def fake_klines(interval, limit=3, dead_sources=None):
        if interval == "15":
            return [_candle(2000, 0.00052, 0.00053, 0.00049, 0.00051)], "bybit"  # wick below 0.0005065, close above
        return [], "bybit"
    monkeypatch.setattr(asm, "get_klines", fake_klines)

    sent = []
    async def fake_send(bot, text, critical=False):
        sent.append((text, critical))
    result = _run(asm.check_ake_setup(bot=None, send_system_fn=fake_send))
    assert "low_sweep" in result
    text = [t for t, c in sent if "лой" in t][0]
    assert "фитилём" in text


def test_a4_invalidation_fires_independently_of_other_stages(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    asm._save_state(_seed_state(1000))

    def fake_klines(interval, limit=3, dead_sources=None):
        if interval == "15":
            return [], "bybit"
        return [_candle(5000, 0.00074, 0.00076, 0.00073, 0.00075)], "bybit"  # 1H close > 0.00073
    monkeypatch.setattr(asm, "get_klines", fake_klines)

    sent = []
    async def fake_send(bot, text, critical=False):
        sent.append((text, critical))
    result = _run(asm.check_ake_setup(bot=None, send_system_fn=fake_send))
    assert "invalidation" in result
    assert any(c is True for t, c in sent)


def test_a5_targets_fire_independently(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    asm._save_state(_seed_state(1000))

    def fake_klines(interval, limit=3, dead_sources=None):
        if interval == "15":
            return [_candle(2000, 0.00051, 0.00052, 0.0005, 0.000505)], "bybit"  # low touches both 0.000558 and 0.000506
        return [], "bybit"
    monkeypatch.setattr(asm, "get_klines", fake_klines)

    sent = []
    async def fake_send(bot, text, critical=False):
        sent.append((text, critical))
    result = _run(asm.check_ake_setup(bot=None, send_system_fn=fake_send))
    assert "target_558" in result
    assert "target_506" in result


def test_wallet_status_line_honest_when_no_events_file(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    line = asm._wallets_status_line()
    assert "#1 без движения" in line
    assert "#2 без движения" in line


def test_wallet_status_line_detects_recent_transfer(monkeypatch, tmp_path):
    events_file = tmp_path / "bsc_events.json"
    events_file.write_text(json.dumps([
        {"from": asm.WALLET_1, "ts": time.time() - 3600},  # 1ч назад -- внутри 24ч
    ]))
    monkeypatch.setattr(asm, "BSC_EVENTS_FILE", str(events_file))
    line = asm._wallets_status_line()
    assert "#1 двигался" in line
    assert "#2 без движения" in line


def test_all_alerts_are_critical_true(monkeypatch, tmp_path):
    """Владелец: "критичность максимальная, все алерты -> оба канала" -- регресс-замок."""
    _fresh_state_file(monkeypatch, tmp_path)
    asm._save_state(_seed_state(1000))

    def fake_klines(interval, limit=3, dead_sources=None):
        if interval == "15":
            return [_candle(2000, 0.00069, 0.00071, 0.00068, 0.00069)], "bybit"
        return [], "bybit"
    monkeypatch.setattr(asm, "get_klines", fake_klines)

    sent = []
    async def fake_send(bot, text, critical=False):
        sent.append(critical)
    _run(asm.check_ake_setup(bot=None, send_system_fn=fake_send))
    assert all(c is True for c in sent)


# --- Распространение фикса #240: run_in_executor, dead-source tracking, honest notify ---

def test_get_klines_dead_source_not_retried_within_tick(monkeypatch):
    calls = {"bybit": 0, "bingx": 0}

    def fake_bybit(interval, limit):
        calls["bybit"] += 1
        raise RuntimeError("bybit down")

    def fake_bingx(interval, limit):
        calls["bingx"] += 1
        return [], "bingx"

    monkeypatch.setattr(asm, "_fetch_klines_bybit", fake_bybit)
    monkeypatch.setattr(asm, "_fetch_klines_bingx", fake_bingx)

    dead_sources = set()
    asm.get_klines("15", asm.CANDLE_LIMIT, dead_sources)
    asm.get_klines("60", asm.CANDLE_LIMIT, dead_sources)  # тот же тик
    assert calls["bybit"] == 1
    assert calls["bingx"] == 2


def test_check_ake_setup_uses_run_in_executor(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    asm._save_state(_seed_state(1000))

    executor_calls = []

    async def fake_run_in_executor(fn, *args):
        executor_calls.append(fn.__name__)
        return fn(*args)

    def _mk(name, real):
        real.__name__ = name
        return real

    monkeypatch.setattr(asm, "get_klines",
                         _mk("get_klines", lambda interval, limit=3, dead_sources=None: ([], "bybit")))

    _run(asm.check_ake_setup(bot=None, send_system_fn=lambda *a, **k: None,
                              run_in_executor_fn=fake_run_in_executor))
    assert executor_calls.count("get_klines") >= 1


def test_check_ake_setup_honest_notify_on_total_source_failure(monkeypatch, tmp_path):
    """Владелец В ПОЗИЦИИ -- если все источники свечей отказали, монитор обязан
    честно сообщить, не молчать."""
    _fresh_state_file(monkeypatch, tmp_path)
    asm._save_state(_seed_state(1000))

    def fake_klines(interval, limit=3, dead_sources=None):
        raise RuntimeError("все источники недоступны")
    monkeypatch.setattr(asm, "get_klines", fake_klines)

    sent = []
    async def fake_send(bot, text, critical=False):
        sent.append((text, critical))

    _run(asm.check_ake_setup(bot=None, send_system_fn=fake_send))
    assert len(sent) == 1
    assert sent[0][1] is True
    assert "источники свечей" in sent[0][0]


def test_check_ake_setup_notify_not_spammed_every_tick(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    state = _seed_state(1000)
    state["last_source_down_notify_ts"] = time.time()
    asm._save_state(state)

    def fake_klines(interval, limit=3, dead_sources=None):
        raise RuntimeError("все источники недоступны")
    monkeypatch.setattr(asm, "get_klines", fake_klines)

    sent = []
    async def fake_send(bot, text, critical=False):
        sent.append((text, critical))

    _run(asm.check_ake_setup(bot=None, send_system_fn=fake_send))
    assert sent == []
