"""
pytest для Пакет 17 -- tools/backtest_common.py (чистые функции, тот же паттерн,
что tests/test_backtest_engine.py: HistoricalStore заполняется вручную в
памяти, без сети/реальных исторических файлов).
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import backtest.engine as eng
import backtest_common as bc


def _candle(t, o, h, l, c, v=100.0):
    return {"timestamp": t, "open": o, "high": h, "low": l, "close": c, "vol": v}


def _store_with_1h(symbol, candles):
    store = eng.HistoricalStore(data_dir="/nonexistent")
    store._candles[(symbol, "1h")] = candles
    store._ts[(symbol, "1h")] = [c["timestamp"] for c in candles]
    return store


# ── killzone_status_at() -- та же таблица часов, что bot.get_killzone_status() ──

def test_killzone_london_open_is_good():
    dt = datetime(2026, 7, 13, 10 - 3, 0, tzinfo=timezone.utc)  # 10:00 UTC+3 -> Лондон Open
    kz = bc.killzone_status_at(dt)
    assert kz["is_good"] is True
    assert "Лондон Open" in kz["active"]["name"]


def test_killzone_dead_zone_is_not_good():
    dt = datetime(2026, 7, 13, 21 - 3, 0, tzinfo=timezone.utc)  # 21:00 UTC+3 -> вне зон
    kz = bc.killzone_status_at(dt)
    assert kz["is_good"] is False
    assert kz["active"]["quality"] == "D"


def test_killzone_ny_open_is_good():
    dt = datetime(2026, 7, 13, 15 - 3, 0, tzinfo=timezone.utc)  # 15:00 UTC+3 -> NY Open
    kz = bc.killzone_status_at(dt)
    assert kz["is_good"] is True
    assert "NY Open" in kz["active"]["name"]


def test_killzone_next_zone_has_in_min():
    dt = datetime(2026, 7, 13, 21 - 3, 0, tzinfo=timezone.utc)  # Dead Zone, следующая -- NY Close (23:00)
    kz = bc.killzone_status_at(dt)
    assert kz["next"] is not None
    assert kz["next"]["in_min"] > 0


# ── simulate_execution_72h() -- окно 72ч, ОТЛИЧАЕТСЯ от engine.py's 14 дней ──

def test_simulate_72h_tp1_hit_within_window():
    candles = [_candle(i * 3600_000, 100, 101, 99, 100) for i in range(10)]
    candles[5]["high"] = 106  # TP1(105) сработал на 5-м часу, НЕ доходя до TP2(110)
    store = _store_with_1h("TESTX", candles)
    result = bc.simulate_execution_72h(store, "TESTX", "long", entry=100, sl=95,
                                        tp1=105, tp2=110, tp3=115, start_ms=0)
    assert result["outcome"] == "TP1_HIT"
    assert result["actual_r"] > 0


def test_simulate_72h_sl_priority_on_same_candle():
    candles = [_candle(0, 100, 106, 94, 100)]  # одна свеча бьёт и SL, и TP1
    store = _store_with_1h("TESTX", candles)
    result = bc.simulate_execution_72h(store, "TESTX", "long", entry=100, sl=95,
                                        tp1=105, tp2=110, tp3=115, start_ms=0)
    assert result["outcome"] == "SL_HIT"
    assert result["actual_r"] == -1.0


def test_simulate_72h_expires_before_engine_py_14day_default():
    """Ключевое отличие от backtest/engine.py::simulate_execution() (14 дней):
    сделка, которая была бы ещё "активна" на 14-дневном окне, ЗДЕСЬ уже EXPIRED
    на часе 80 (>72ч) -- ни TP, ни SL не достигнуты в пределах 72ч."""
    hours = 80
    candles = [_candle(i * 3600_000, 100, 101, 99, 100) for i in range(hours)]
    candles[75]["high"] = 106  # TP1 сработал бы, но ПОСЛЕ 72ч-дедлайна
    store = _store_with_1h("TESTX", candles)
    result = bc.simulate_execution_72h(store, "TESTX", "long", entry=100, sl=95,
                                        tp1=105, tp2=110, tp3=115, start_ms=0)
    assert result["outcome"] == "EXPIRED"
    assert result["actual_r"] == 0.0

    # тот же датасет, но через штатный 14-дневный engine.py -- TP1 ДОЛЖЕН
    # сработать (в пределах 336ч), подтверждая, что расхождение -- по окну,
    # не по логике поиска уровня.
    result_14d = eng.simulate_execution(store, "TESTX", "long", entry=100, sl=95,
                                         tp1=105, tp2=110, tp3=115, start_ms=0)
    assert result_14d["outcome"] == "TP1_HIT"


def test_simulate_72h_expired_when_nothing_hit():
    candles = [_candle(i * 3600_000, 100, 101, 99, 100) for i in range(10)]
    store = _store_with_1h("TESTX", candles)
    result = bc.simulate_execution_72h(store, "TESTX", "long", entry=100, sl=95,
                                        tp1=105, tp2=110, tp3=115, start_ms=0)
    assert result["outcome"] == "EXPIRED"


# ── fetch_top_symbols_uppercase() -- срез "USDT"-суффикса на границе ───────

def test_fetch_top_symbols_uppercase_strips_usdt_suffix(monkeypatch):
    import whale_radar
    monkeypatch.setattr(whale_radar, "fetch_top_symbols",
                         lambda n: ["btcusdt", "ethusdt", "1000bonkusdt"])
    result = bc.fetch_top_symbols_uppercase(3)
    assert result == ["1000BONK", "BTC", "ETH"]


def test_fetch_top_symbols_uppercase_handles_empty(monkeypatch):
    import whale_radar
    monkeypatch.setattr(whale_radar, "fetch_top_symbols", lambda n: [])
    result = bc.fetch_top_symbols_uppercase(10)
    assert result == []
