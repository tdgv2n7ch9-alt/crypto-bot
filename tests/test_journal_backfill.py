"""
pytest для backtest/journal_backfill.replay_record() -- чистая функция (candles уже
переданы, ничего не фетчит) -- ретроспективный пересчёт зависших PENDING/ENTERED/
EXPIRED записей journal/signals.json реальными свечами (задача владельца в очереди
между Этапом 3 и Этапом 4 АПГРЕЙД 11.07).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

from backtest import journal_backfill as jb


def _candle(ts_sec, o, h, l, c):
    return {"timestamp": int(ts_sec * 1000), "open": o, "high": h, "low": l, "close": c, "vol": 1.0}


def _pending_long(ts=1000.0, entry_lo=90.0, entry_hi=100.0, sl=80.0, tp1=110.0, tp2=120.0, tp3=130.0):
    return {"status": "PENDING", "direction": "long", "ts": ts,
            "entry_lo": entry_lo, "entry_hi": entry_hi, "sl": sl,
            "tp1": tp1, "tp2": tp2, "tp3": tp3,
            "entered_ts": None, "entered_price": None}


def test_pending_never_touched_within_72h_stays_expired_no_change():
    rec = _pending_long(ts=0.0)
    candles = [_candle(1000, 200, 205, 195, 202)]  # цена далеко выше зоны входа
    updates = jb.replay_record(rec, candles, now_ts=100000.0)
    assert updates == {}


def test_pending_touches_entry_then_hits_tp1():
    rec = _pending_long(ts=0.0)
    candles = [
        _candle(100, 105, 106, 104, 105),      # выше зоны, ещё не вошли
        _candle(200, 98, 99, 95, 96),            # касание зоны 90-100 (low=95<=100, high=99>=90)
        _candle(300, 96, 111, 95, 108),          # пробивает TP1=110 (high=111)
    ]
    updates = jb.replay_record(rec, candles, now_ts=100000.0)
    assert updates["status"] == "TP1_HIT"
    assert updates["outcome"] == "TP1_HIT"
    assert updates["entered_price"] == 100.0  # entry_hi для long
    assert updates["actual_r"] is not None
    assert updates["actual_r"] > 0


def test_pending_touches_entry_then_hits_sl():
    rec = _pending_long(ts=0.0)
    candles = [
        _candle(200, 98, 99, 95, 96),           # вход
        _candle(300, 96, 97, 75, 80),            # пробивает SL=80 (low=75)
    ]
    updates = jb.replay_record(rec, candles, now_ts=100000.0)
    assert updates["status"] == "SL_HIT"
    assert updates["actual_r"] < 0


def test_sl_priority_over_tp_in_same_candle():
    """Одна свеча пробивает и SL, и TP -- SL приоритетнее (тот же принцип, что
    signal_journal._check_outcome, консервативная честная статистика)."""
    rec = _pending_long(ts=0.0)
    candles = [
        _candle(200, 98, 99, 95, 96),            # вход
        _candle(300, 96, 115, 75, 100),          # огромная свеча: high=115 (>tp1=110), low=75 (<sl=80)
    ]
    updates = jb.replay_record(rec, candles, now_ts=100000.0)
    assert updates["status"] == "SL_HIT"


def test_pending_entered_within_window_but_still_open_becomes_entered():
    """Зона тронута в пределах 72ч, но ни TP ни SL пока не пробиты -- статус должен
    поменяться PENDING -> ENTERED (а не остаться PENDING), т.к. по факту рынок уже
    вошёл в зону."""
    rec = _pending_long(ts=0.0)
    candles = [_candle(200, 98, 99, 95, 96)]  # только касание, дальше данных нет
    updates = jb.replay_record(rec, candles, now_ts=100000.0)
    assert updates["status"] == "ENTERED"
    assert updates["entered_price"] == 100.0


def test_pending_touch_after_72h_deadline_is_ignored():
    """Касание зоны ПОСЛЕ 72ч с момента сигнала не должно считаться валидным входом --
    то же правило, что живой PENDING_EXPIRE_SEC."""
    rec = _pending_long(ts=0.0)
    late_ts = 73 * 3600  # за пределами 72ч
    candles = [_candle(late_ts, 98, 99, 95, 96)]
    updates = jb.replay_record(rec, candles, now_ts=late_ts + 1000)
    # Зона не тронута В ОКНЕ -> либо expire (если PENDING и now-ts>72h), либо {}
    assert updates.get("status") in (None, "EXPIRED")
    if "status" in updates:
        assert updates["status"] == "EXPIRED"


def test_pending_past_72h_with_no_touch_gets_expired():
    rec = _pending_long(ts=0.0)
    candles = [_candle(1000, 200, 205, 195, 202)]
    updates = jb.replay_record(rec, candles, now_ts=73 * 3600)
    assert updates == {"status": "EXPIRED", "outcome": "EXPIRED", "outcome_ts": 73 * 3600}


def test_pending_still_within_72h_with_no_touch_stays_open_no_change():
    rec = _pending_long(ts=0.0)
    candles = [_candle(1000, 200, 205, 195, 202)]
    updates = jb.replay_record(rec, candles, now_ts=10 * 3600)  # ещё внутри 72ч
    assert updates == {}


def test_expired_record_touched_within_original_window_resurrected():
    """EXPIRED-запись (живой трекер её так пометил из-за нехватки WS-данных), но
    реальные свечи показывают, что зона БЫЛА тронута в пределах исходных 72ч --
    честно пересчитываем, не оставляем ошибочный EXPIRED."""
    rec = _pending_long(ts=0.0)
    rec["status"] = "EXPIRED"
    candles = [
        _candle(1000, 98, 99, 95, 96),           # вход внутри 72ч (1000с << 72ч)
        _candle(2000, 96, 111, 95, 108),          # TP1
    ]
    updates = jb.replay_record(rec, candles, now_ts=100000.0)
    assert updates["status"] == "TP1_HIT"


def test_entered_record_walks_forward_from_entered_ts():
    rec = {"status": "ENTERED", "direction": "long", "ts": 0.0,
           "entry_lo": 90.0, "entry_hi": 100.0, "sl": 80.0,
           "tp1": 110.0, "tp2": 120.0, "tp3": 130.0,
           "entered_ts": 500.0, "entered_price": 97.5}
    candles = [
        _candle(200, 98, 99, 95, 96),    # ДО входа -- не должно учитываться
        _candle(600, 97, 98, 96, 97),    # после входа, ничего не пробито
        _candle(700, 97, 121, 96, 118),  # пробивает TP2=120
    ]
    updates = jb.replay_record(rec, candles, now_ts=100000.0)
    assert updates["status"] == "TP2_HIT"
    assert updates["entered_price"] == 97.5  # сохранена реальная live-цена входа, не пересчитана


def test_entered_record_no_data_before_entry_returns_empty():
    rec = {"status": "ENTERED", "direction": "long", "ts": 0.0,
           "entry_lo": 90.0, "entry_hi": 100.0, "sl": 80.0,
           "tp1": 110.0, "tp2": None, "tp3": None,
           "entered_ts": 999999.0, "entered_price": 97.5}
    candles = [_candle(200, 98, 99, 95, 96)]  # вся история раньше entered_ts
    updates = jb.replay_record(rec, candles, now_ts=100000.0)
    assert updates == {}


def test_entered_missing_entered_ts_returns_empty():
    rec = {"status": "ENTERED", "direction": "long", "ts": 0.0,
           "entry_lo": 90.0, "entry_hi": 100.0, "sl": 80.0,
           "tp1": 110.0, "tp2": None, "tp3": None,
           "entered_ts": None, "entered_price": None}
    updates = jb.replay_record(rec, [_candle(200, 98, 99, 95, 96)], now_ts=100000.0)
    assert updates == {}


def test_short_direction_sl_and_tp_logic_inverted():
    rec = {"status": "PENDING", "direction": "short", "ts": 0.0,
           "entry_lo": 95.0, "entry_hi": 105.0, "sl": 115.0,
           "tp1": 85.0, "tp2": 75.0, "tp3": 65.0,
           "entered_ts": None, "entered_price": None}
    candles = [
        _candle(200, 100, 106, 99, 101),   # касание зоны 95-105
        _candle(300, 99, 100, 84, 90),      # short: TP1=85 -> low<=85 пробивает
    ]
    updates = jb.replay_record(rec, candles, now_ts=100000.0)
    assert updates["status"] == "TP1_HIT"
    assert updates["entered_price"] == 95.0  # entry_lo для short
    assert updates["actual_r"] > 0
