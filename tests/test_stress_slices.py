"""backtest/stress_slices.py -- на детерминированных фикстурах."""
from datetime import datetime, timezone

import backtest.stress_slices as ss


def _ms(year, month, day=1, hour=0):
    return int(datetime(year, month, day, hour, tzinfo=timezone.utc).timestamp() * 1000)


def _t(symbol, r, ms):
    return {"symbol": symbol, "direction": "long", "actual_r": r, "start_ms": ms,
            "outcome": "TP1_HIT" if r > 0 else "SL_HIT", "entry": 100, "sl": 90,
            "tp1": 110, "tp2": 120, "tp3": 130, "rr_tp1": 2.0}


def test_quarter_bucket():
    assert ss._quarter_bucket(_ms(2026, 1)) == "2026-Q1"
    assert ss._quarter_bucket(_ms(2026, 4)) == "2026-Q2"
    assert ss._quarter_bucket(_ms(2026, 7)) == "2026-Q3"
    assert ss._quarter_bucket(_ms(2025, 12)) == "2025-Q4"


def test_build_walkforward_groups_by_quarter():
    trades = [_t("A", 1.0, _ms(2026, 1)), _t("A", 2.0, _ms(2026, 2)),
              _t("A", -1.0, _ms(2026, 5))]
    wf = ss.build_walkforward(trades)
    assert wf["2026-Q1"]["total"] == 2
    assert wf["2026-Q2"]["total"] == 1


def test_btc_24h_change_at_computes_correctly():
    btc_1h = [{"timestamp": i * 3600_000, "close": 100.0 + i} for i in range(48)]
    ts_list = [c["timestamp"] for c in btc_1h]
    # 24 bars back: price at idx X vs idx X-24
    as_of = 30 * 3600_000 + 1
    chg = ss._btc_24h_change_at(btc_1h, ts_list, as_of)
    # idx_now = bisect_right(ts_list, as_of)-1 = 30 (price 130), idx-24=6 (price 106)
    expected = (130.0 - 106.0) / 106.0 * 100
    assert abs(chg - expected) < 1e-6


def test_btc_24h_change_insufficient_history_returns_zero():
    btc_1h = [{"timestamp": i * 3600_000, "close": 100.0} for i in range(5)]
    ts_list = [c["timestamp"] for c in btc_1h]
    assert ss._btc_24h_change_at(btc_1h, ts_list, 3 * 3600_000) == 0.0


def test_render_walkforward_no_crash_empty():
    md = ss.render_walkforward_markdown({})
    assert "WALKFORWARD.md" in md
