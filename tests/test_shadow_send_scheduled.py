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
