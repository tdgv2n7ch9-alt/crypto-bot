"""Чистые функции backtest/engine.py на синтетических фикстурах (без сети, без
реальных исторических файлов -- HistoricalStore заполняется вручную в памяти)."""
import backtest.engine as eng


def _candle(t, o, h, l, c, v=100.0):
    return {"timestamp": t, "open": o, "high": h, "low": l, "close": c, "vol": v}


def test_hit_long_tp_and_sl():
    c = {"high": 105, "low": 95}
    assert eng._hit("long", c, 104, is_tp=True) is True
    assert eng._hit("long", c, 106, is_tp=True) is False
    assert eng._hit("long", c, 96, is_tp=False) is True
    assert eng._hit("long", c, 94, is_tp=False) is False


def test_hit_short_tp_and_sl():
    c = {"high": 105, "low": 95}
    assert eng._hit("short", c, 96, is_tp=True) is True
    assert eng._hit("short", c, 94, is_tp=True) is False
    assert eng._hit("short", c, 104, is_tp=False) is True


def test_pct_change_basic():
    ts = [0, 3600_000, 7200_000, 10800_000]  # 0h,1h,2h,3h
    closes = [100.0, 110.0, 121.0, 133.1]
    # as_of = 3h mark (idx 3, price 133.1), 1h back -> idx2 price 121.0
    pct = eng._pct_change(closes, ts, 10800_000 + 1, 1)
    assert abs(pct - ((133.1 - 121.0) / 121.0 * 100)) < 1e-6


def test_pct_change_insufficient_history_returns_zero():
    ts = [0, 3600_000]
    closes = [100.0, 105.0]
    # запрашиваем 24ч назад, истории не хватает
    pct = eng._pct_change(closes, ts, 3600_000 + 1, 24)
    assert pct == 0.0


def test_pct_change_empty_returns_zero():
    assert eng._pct_change([], [], 1000, 1) == 0.0


class _FakeStore:
    """Минимальная замена HistoricalStore.full_series для simulate_execution-тестов."""
    def __init__(self, candles_1h):
        self._candles = candles_1h

    def full_series(self, symbol, interval):
        assert interval == "1h"
        return self._candles


def test_simulate_execution_tp1_hit_long():
    candles = [
        _candle(0, 100, 101, 99, 100),
        _candle(3600_000, 100, 106, 100, 105),  # touches tp1=105
    ]
    store = _FakeStore(candles)
    result = eng.simulate_execution(store, "X", "long", entry=100, sl=95,
                                     tp1=105, tp2=110, tp3=115, start_ms=0)
    assert result["outcome"] == "TP1_HIT"
    assert abs(result["actual_r"] - 1.0) < 1e-6  # (105-100)/(100-95) = 1.0


def test_simulate_execution_sl_priority_over_tp_same_candle():
    # свеча одновременно задевает и SL(95), и TP1(105) -- SL приоритетнее (допущение 5)
    candles = [_candle(0, 100, 106, 94, 100)]
    store = _FakeStore(candles)
    result = eng.simulate_execution(store, "X", "long", entry=100, sl=95,
                                     tp1=105, tp2=110, tp3=115, start_ms=0)
    assert result["outcome"] == "SL_HIT"
    assert result["actual_r"] == -1.0


def test_simulate_execution_short_direction():
    candles = [_candle(3600_000, 100, 101, 94, 95)]  # touches tp1=95 for short
    store = _FakeStore(candles)
    result = eng.simulate_execution(store, "X", "short", entry=100, sl=105,
                                     tp1=95, tp2=90, tp3=85, start_ms=0)
    assert result["outcome"] == "TP1_HIT"
    assert abs(result["actual_r"] - 1.0) < 1e-6  # (100-95)/(105-100)=1.0


def test_simulate_execution_expired_when_no_level_hit():
    candles = [_candle(i * 3600_000, 100, 100.5, 99.5, 100) for i in range(5)]
    store = _FakeStore(candles)
    old_max = eng.MAX_HOLD_HOURS
    eng.MAX_HOLD_HOURS = 0.5  # форсируем истечение почти сразу для теста
    try:
        result = eng.simulate_execution(store, "X", "long", entry=100, sl=90,
                                         tp1=110, tp2=120, tp3=130, start_ms=0)
    finally:
        eng.MAX_HOLD_HOURS = old_max
    assert result["outcome"] == "EXPIRED"
    assert result["actual_r"] == 0.0


def test_simulate_execution_tp3_hit_takes_priority_over_tp1_in_same_scan():
    # свеча сразу прыгает выше tp3 (гэп) -- функция должна вернуть TP3, не TP1
    candles = [_candle(0, 100, 116, 100, 115)]
    store = _FakeStore(candles)
    result = eng.simulate_execution(store, "X", "long", entry=100, sl=95,
                                     tp1=105, tp2=110, tp3=115, start_ms=0)
    assert result["outcome"] == "TP3_HIT"


def test_historical_store_window_no_lookahead():
    store = eng.HistoricalStore(data_dir="/nonexistent")
    store._candles[("X", "1h")] = [_candle(i * 1000, 1, 1, 1, 1) for i in range(10)]
    store._ts[("X", "1h")] = [i * 1000 for i in range(10)]
    # as_of ровно на границе бара 5 (timestamp=5000) -- бар с t=5000 НЕ должен войти
    # (только строго ДО as_of_ms), иначе lookahead-bias
    window = store.window("X", "1h", as_of_ms=5000, limit=100)
    assert len(window) == 5
    assert window[-1]["timestamp"] == 4000


def test_historical_store_window_respects_limit():
    store = eng.HistoricalStore(data_dir="/nonexistent")
    store._candles[("X", "1h")] = [_candle(i * 1000, 1, 1, 1, 1) for i in range(20)]
    store._ts[("X", "1h")] = [i * 1000 for i in range(20)]
    window = store.window("X", "1h", as_of_ms=20000, limit=5)
    assert len(window) == 5
    assert window[-1]["timestamp"] == 19000
