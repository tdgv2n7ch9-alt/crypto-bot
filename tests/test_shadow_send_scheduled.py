"""
pytest для shadow_engine._adapt_send_scheduled_result() / log_send_scheduled_shadow_async()
-- Пакет 4 М2 (владелец, "ДА"): подключение shadow_engine к send_scheduled(). Каждый
кандидат send_scheduled() (не только уже отправленные, как в signal_loop-пути) должен
прогоняться через теневой контур -- эти тесты проверяют, что адаптер полей корректно
переводит плоский формат real_full_analysis() в форму, которую ждёт compute_shadow(),
и что log_send_scheduled_shadow_async() честно фиксирует promoted_live/gate_reasons.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shadow_engine as se


def _fake_real_full_analysis_result(is_long=True, candles=None):
    return {
        "is_long": is_long,
        "entry1": 100.0, "entry3": 98.0,
        "sl": 96.0, "tp1": 104.0, "tp2": 108.0, "tp3": 112.0,
        "rr_tp1": 2.0,
        "candles_4h": candles if candles is not None else [
            {"open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000},
        ],
    }


class _FakeBotModule:
    def get_killzone_status(self):
        return {"active": {"quality": "A", "name": "London"}}

    def get_killzone_status_shadow(self):
        return {"active": {"quality": "A", "name": "London"}}


def test_adapt_maps_long_direction_and_fields():
    a = _fake_real_full_analysis_result(is_long=True)
    adapted = se._adapt_send_scheduled_result(a)
    b11 = adapted["block11_trade_plan"]
    assert b11["direction"] == "long"
    assert b11["entry1"] == 100.0
    assert b11["entry3"] == 98.0
    assert b11["sl"] == 96.0
    assert b11["tp1"] == 104.0
    assert b11["rr_tp1"] == 2.0
    assert adapted["candles_4h"] == a["candles_4h"]


def test_adapt_maps_short_direction():
    a = _fake_real_full_analysis_result(is_long=False)
    adapted = se._adapt_send_scheduled_result(a)
    assert adapted["block11_trade_plan"]["direction"] == "short"


def test_adapt_missing_candles_defaults_to_empty_list():
    a = _fake_real_full_analysis_result()
    del a["candles_4h"]
    adapted = se._adapt_send_scheduled_result(a)
    assert adapted["candles_4h"] == []


def test_log_send_scheduled_shadow_async_writes_local_record(monkeypatch):
    captured = {}

    def fake_write_local(record):
        captured["record"] = record
        return True

    async def fake_sync(*args, **kwargs):
        return True

    monkeypatch.setattr(se, "_write_local", fake_write_local)
    monkeypatch.setattr(se, "_sync_to_github_sync", lambda record: True)

    a = _fake_real_full_analysis_result(is_long=True)
    ok = asyncio.run(se.log_send_scheduled_shadow_async(
        "BTCUSDT", a, _FakeBotModule(), promoted_live=True, gate_reasons=[]))

    assert ok is True
    assert captured["record"]["source"] == "send_scheduled"
    assert captured["record"]["promoted_live"] is True
    assert captured["record"]["gate_reasons"] == []
    assert captured["record"]["symbol"] == "BTCUSDT"
    assert captured["record"]["direction"] == "long"


def test_log_send_scheduled_shadow_async_records_rejected_candidate(monkeypatch):
    captured = {}

    def fake_write_local(record):
        captured["record"] = record
        return True

    monkeypatch.setattr(se, "_write_local", fake_write_local)
    monkeypatch.setattr(se, "_sync_to_github_sync", lambda record: True)

    a = _fake_real_full_analysis_result(is_long=False)
    ok = asyncio.run(se.log_send_scheduled_shadow_async(
        "ETHUSDT", a, _FakeBotModule(), promoted_live=False,
        gate_reasons=["rr_gate", "rocket_or_grade"]))

    assert ok is True
    assert captured["record"]["promoted_live"] is False
    assert captured["record"]["gate_reasons"] == ["rr_gate", "rocket_or_grade"]


def test_log_send_scheduled_shadow_async_honest_false_on_local_write_failure(monkeypatch):
    monkeypatch.setattr(se, "_write_local", lambda record: False)
    a = _fake_real_full_analysis_result()
    ok = asyncio.run(se.log_send_scheduled_shadow_async(
        "BTCUSDT", a, _FakeBotModule(), promoted_live=True, gate_reasons=[]))
    assert ok is False


# --- Пакет 10 М2: oi_funding_ls_shadow передаётся через в запись ---

def test_log_send_scheduled_shadow_async_carries_oi_funding_ls_shadow(monkeypatch):
    captured = {}

    def fake_write_local(record):
        captured["record"] = record
        return True

    monkeypatch.setattr(se, "_write_local", fake_write_local)
    monkeypatch.setattr(se, "_sync_to_github_sync", lambda record: True)

    a = _fake_real_full_analysis_result(is_long=True)
    a["oi_funding_ls_shadow"] = {
        "oi_combo": "up_up", "d_oi": 6, "d_funding": 0, "d_ls": -3,
        "total_delta": 3, "rocket_old": 61, "rocket_would_be": 64,
    }
    asyncio.run(se.log_send_scheduled_shadow_async(
        "BTCUSDT", a, _FakeBotModule(), promoted_live=True, gate_reasons=[]))

    assert captured["record"]["oi_funding_ls_shadow"]["total_delta"] == 3
    assert captured["record"]["oi_funding_ls_shadow"]["rocket_would_be"] == 64


def test_log_send_scheduled_shadow_async_missing_oi_funding_ls_shadow_is_none(monkeypatch):
    captured = {}

    def fake_write_local(record):
        captured["record"] = record
        return True

    monkeypatch.setattr(se, "_write_local", fake_write_local)
    monkeypatch.setattr(se, "_sync_to_github_sync", lambda record: True)

    a = _fake_real_full_analysis_result(is_long=True)  # no oi_funding_ls_shadow key
    asyncio.run(se.log_send_scheduled_shadow_async(
        "BTCUSDT", a, _FakeBotModule(), promoted_live=True, gate_reasons=[]))

    assert captured["record"]["oi_funding_ls_shadow"] is None


# --- Пакет 11 М1: bos_body_close_shadow передаётся через в запись ---

def test_log_send_scheduled_shadow_async_carries_bos_body_close_shadow(monkeypatch):
    captured = {}

    def fake_write_local(record):
        captured["record"] = record
        return True

    monkeypatch.setattr(se, "_write_local", fake_write_local)
    monkeypatch.setattr(se, "_sync_to_github_sync", lambda record: True)

    a = _fake_real_full_analysis_result(is_long=True)
    a["bos_body_close_shadow"] = {
        "wick_only_type": "BOS_bull", "body_close_type": "invalid_break_wick_only",
        "disagree": True, "downgraded_to_invalid": True,
        "body_close_label": "SFP, не валидный слом структуры",
    }
    asyncio.run(se.log_send_scheduled_shadow_async(
        "BTCUSDT", a, _FakeBotModule(), promoted_live=True, gate_reasons=[]))

    assert captured["record"]["bos_body_close_shadow"]["disagree"] is True
    assert captured["record"]["bos_body_close_shadow"]["downgraded_to_invalid"] is True


def test_log_send_scheduled_shadow_async_missing_bos_body_close_shadow_is_none(monkeypatch):
    captured = {}

    def fake_write_local(record):
        captured["record"] = record
        return True

    monkeypatch.setattr(se, "_write_local", fake_write_local)
    monkeypatch.setattr(se, "_sync_to_github_sync", lambda record: True)

    a = _fake_real_full_analysis_result(is_long=True)  # no bos_body_close_shadow key
    asyncio.run(se.log_send_scheduled_shadow_async(
        "BTCUSDT", a, _FakeBotModule(), promoted_live=True, gate_reasons=[]))

    assert captured["record"]["bos_body_close_shadow"] is None


# --- Пакет 11 М2: order_block_shadow передаётся через в запись ---

def test_log_send_scheduled_shadow_async_carries_order_block_shadow(monkeypatch):
    captured = {}

    def fake_write_local(record):
        captured["record"] = record
        return True

    monkeypatch.setattr(se, "_write_local", fake_write_local)
    monkeypatch.setattr(se, "_sync_to_github_sync", lambda record: True)

    a = _fake_real_full_analysis_result(is_long=True)
    a["order_block_shadow"] = {
        "live": {"bull": True, "bull_zone": (100.0, 105.0), "bear": False, "bear_zone": None},
        "methodology": {"bull": False, "bull_zone": None, "bear": False, "bear_zone": None},
    }
    asyncio.run(se.log_send_scheduled_shadow_async(
        "BTCUSDT", a, _FakeBotModule(), promoted_live=True, gate_reasons=[]))

    assert captured["record"]["order_block_shadow"]["live"]["bull"] is True
    assert captured["record"]["order_block_shadow"]["methodology"]["bull"] is False


def test_log_send_scheduled_shadow_async_missing_order_block_shadow_is_none(monkeypatch):
    captured = {}

    def fake_write_local(record):
        captured["record"] = record
        return True

    monkeypatch.setattr(se, "_write_local", fake_write_local)
    monkeypatch.setattr(se, "_sync_to_github_sync", lambda record: True)

    a = _fake_real_full_analysis_result(is_long=True)  # no order_block_shadow key
    asyncio.run(se.log_send_scheduled_shadow_async(
        "BTCUSDT", a, _FakeBotModule(), promoted_live=True, gate_reasons=[]))

    assert captured["record"]["order_block_shadow"] is None


def test_log_send_scheduled_shadow_async_compute_failure_returns_false(monkeypatch):
    # bot_module без ожидаемых методов -- compute_shadow должен упасть внутри
    # try/except патча 01, но НЕ уронить всю функцию (уже проверено в compute_shadow
    # самим, здесь -- что верхнеуровневый except в _adapt/compute срабатывает при
    # полностью сломанном входе).
    def fake_write_local(record):
        return True
    monkeypatch.setattr(se, "_write_local", fake_write_local)
    monkeypatch.setattr(se, "_sync_to_github_sync", lambda record: True)

    ok = asyncio.run(se.log_send_scheduled_shadow_async(
        "BTCUSDT", None, _FakeBotModule(), promoted_live=True, gate_reasons=[]))
    assert ok is False


# --- Health-счётчик "последняя успешная shadow-запись" (владелец "да" 2026-07-13,
# находка "поток молчал 16+ часов незамеченным") -------------------------------------

def test_get_last_send_scheduled_write_ts_none_before_any_write(monkeypatch):
    monkeypatch.setattr(se, "_last_send_scheduled_write_ts", None)
    assert se.get_last_send_scheduled_write_ts() is None


def test_last_write_ts_updated_on_successful_write(monkeypatch):
    monkeypatch.setattr(se, "_last_send_scheduled_write_ts", None)
    monkeypatch.setattr(se, "_write_local", lambda record: True)
    monkeypatch.setattr(se, "_sync_to_github_sync", lambda record: True)

    before = time.time()
    a = _fake_real_full_analysis_result(is_long=True)
    ok = asyncio.run(se.log_send_scheduled_shadow_async(
        "BTCUSDT", a, _FakeBotModule(), promoted_live=True, gate_reasons=[]))
    after = time.time()

    assert ok is True
    ts = se.get_last_send_scheduled_write_ts()
    assert ts is not None
    assert before <= ts <= after


def test_last_write_ts_not_updated_on_compute_failure(monkeypatch):
    monkeypatch.setattr(se, "_last_send_scheduled_write_ts", None)
    monkeypatch.setattr(se, "_write_local", lambda record: True)
    monkeypatch.setattr(se, "_sync_to_github_sync", lambda record: True)

    ok = asyncio.run(se.log_send_scheduled_shadow_async(
        "BTCUSDT", None, _FakeBotModule(), promoted_live=True, gate_reasons=[]))

    assert ok is False
    assert se.get_last_send_scheduled_write_ts() is None


def test_last_write_ts_not_updated_on_local_write_failure(monkeypatch):
    monkeypatch.setattr(se, "_last_send_scheduled_write_ts", None)
    monkeypatch.setattr(se, "_write_local", lambda record: False)

    a = _fake_real_full_analysis_result(is_long=True)
    ok = asyncio.run(se.log_send_scheduled_shadow_async(
        "BTCUSDT", a, _FakeBotModule(), promoted_live=True, gate_reasons=[]))

    assert ok is False
    assert se.get_last_send_scheduled_write_ts() is None


# --- Пакет 14 (владелец, 2026-07-13): tz13_shadow (13-блочный вердикт) передаётся
# через в запись, тот же паттерн, что oi_funding_ls_shadow/bos_body_close_shadow/
# order_block_shadow выше -----------------------------------------------------

_FAKE_TZ13 = {
    "ok": True, "direction": "long", "score": 5, "setup_type": "SH-BOS-RTO",
    "entry_zone": {"lo": 95.0, "hi": 98.0}, "sl": 92.7,
    "tp1": 104.0, "tp2": 108.0, "tp3": 112.0,
}


def test_log_send_scheduled_shadow_async_carries_tz13_shadow(monkeypatch):
    captured = {}

    def fake_write_local(record):
        captured["record"] = record
        return True

    monkeypatch.setattr(se, "_write_local", fake_write_local)
    monkeypatch.setattr(se, "_sync_to_github_sync", lambda record: True)

    a = _fake_real_full_analysis_result(is_long=True)
    a["tz13_shadow"] = _FAKE_TZ13
    asyncio.run(se.log_send_scheduled_shadow_async(
        "BTCUSDT", a, _FakeBotModule(), promoted_live=True, gate_reasons=[]))

    rec = captured["record"]
    assert rec["tz13_shadow"] == _FAKE_TZ13
    assert rec["tz13_score"] == 5
    assert rec["tz13_direction"] == "long"
    assert rec["tz13_setup_type"] == "SH-BOS-RTO"
    assert rec["tz13_entry_zone"] == {"lo": 95.0, "hi": 98.0}
    assert rec["tz13_sl"] == 92.7
    assert rec["tz13_tp1"] == 104.0
    assert rec["tz13_tp2"] == 108.0
    assert rec["tz13_tp3"] == 112.0


def test_log_send_scheduled_shadow_async_missing_tz13_shadow_is_honest_empty(monkeypatch):
    """fa_engine.build_full_analysis()-путь (signal_loop.py) НЕ считает tz13_shadow
    -- честно {}/None, а не выдуманные значения."""
    captured = {}

    def fake_write_local(record):
        captured["record"] = record
        return True

    monkeypatch.setattr(se, "_write_local", fake_write_local)
    monkeypatch.setattr(se, "_sync_to_github_sync", lambda record: True)

    a = _fake_real_full_analysis_result(is_long=True)  # no tz13_shadow key
    asyncio.run(se.log_send_scheduled_shadow_async(
        "BTCUSDT", a, _FakeBotModule(), promoted_live=True, gate_reasons=[]))

    rec = captured["record"]
    assert rec["tz13_shadow"] == {}
    assert rec["tz13_score"] is None
    assert rec["tz13_direction"] is None
    assert rec["tz13_sl"] is None
