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
    monkeypatch.setattr(asm, "get_klines", lambda interval, limit=3: ([_candle(1000, 0.0006, 0.0006, 0.0006, 0.0006)], "bybit"))
    sent = []
    result = _run(asm.check_ake_setup(bot=None, send_system_fn=lambda *a, **k: sent.append(a)))
    assert result == []
    assert sent == []


def test_a1_test_alert_fires_on_touch(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    asm._save_state(_seed_state(1000))

    def fake_klines(interval, limit=3):
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

    def fake_klines(interval, limit=3):
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

    def fake_klines(interval, limit=3):
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

    def fake_klines(interval, limit=3):
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

    def fake_klines(interval, limit=3):
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

    def fake_klines(interval, limit=3):
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

    def fake_klines(interval, limit=3):
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

    def fake_klines(interval, limit=3):
        if interval == "15":
            return [_candle(2000, 0.00069, 0.00071, 0.00068, 0.00069)], "bybit"
        return [], "bybit"
    monkeypatch.setattr(asm, "get_klines", fake_klines)

    sent = []
    async def fake_send(bot, text, critical=False):
        sent.append(critical)
    _run(asm.check_ake_setup(bot=None, send_system_fn=fake_send))
    assert all(c is True for c in sent)
