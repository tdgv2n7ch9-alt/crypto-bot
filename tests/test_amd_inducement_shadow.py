"""
pytest для ta_extra.ny_midnight_price()/classify_amd_phase()/detect_inducement_sweep()
-- Пакет 5 М3 (владелец, "ДА" -- ТОЛЬКО shadow-скоринг, не бой). Проверяет чистую логику
NY-полночного якоря, AMD-фазы и упрощённой детекции inducement, плюс что новые поля
попадают в shadow_engine.compute_shadow() без влияния на affected/patches_affected.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ta_extra
import shadow_engine as se


def _candle(ts_ms, o, h, l, c, vol=0.0):
    return {"timestamp": ts_ms, "open": o, "high": h, "low": l, "close": c, "vol": vol}


def _series_4h(start_ts_ms, closes):
    """4h-свечи подряд начиная с start_ts_ms, close по списку, open=prev close."""
    out = []
    ts = start_ts_ms
    prev = closes[0] - 1
    for c in closes:
        out.append(_candle(ts, prev, max(prev, c) + 1, min(prev, c) - 1, c))
        prev = c
        ts += 4 * 3600 * 1000
    return out


# ── ny_midnight_price ──

def test_ny_midnight_price_none_on_empty_candles():
    assert ta_extra.ny_midnight_price([], dt.datetime.now(dt.timezone.utc)) is None


def test_ny_midnight_price_none_on_missing_now():
    assert ta_extra.ny_midnight_price([_candle(0, 1, 2, 0, 1)], None) is None


def test_ny_midnight_price_picks_candle_before_midnight():
    # NY midnight (EST, зима, UTC-5) 2026-01-15 00:00 America/New_York = 05:00 UTC
    now_utc = dt.datetime(2026, 1, 15, 10, 0, tzinfo=dt.timezone.utc)  # днём после полночи
    midnight_utc_ms = int(dt.datetime(2026, 1, 15, 5, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
    before = _candle(midnight_utc_ms - 3600_000, 100, 101, 99, 100.5)  # до полночи
    after = _candle(midnight_utc_ms + 3600_000, 200, 201, 199, 200.5)  # после полночи
    price = ta_extra.ny_midnight_price([before, after], now_utc)
    assert price == 100.5  # close свечи ДО полночи, не после


def test_ny_midnight_price_no_candidate_returns_none():
    now_utc = dt.datetime(2026, 1, 15, 10, 0, tzinfo=dt.timezone.utc)
    future_only = _candle(int(now_utc.timestamp() * 1000) + 999_999_999, 1, 2, 0, 1)
    assert ta_extra.ny_midnight_price([future_only], now_utc) is None


# ── classify_amd_phase ──

def test_classify_amd_phase_asia_hour_is_accumulation():
    # 03:00 UTC+3 = 00:00 UTC -- Азия (1-9 UTC+3)
    now_utc = dt.datetime(2026, 7, 12, 0, 0, tzinfo=dt.timezone.utc)
    result = ta_extra.classify_amd_phase([], now_utc)
    assert result["phase"] == "accumulation"


def test_classify_amd_phase_dead_zone_hour():
    # 23:00 UTC+3 -- вне всех окон (Asia/London/NY)
    now_utc = dt.datetime(2026, 7, 12, 20, 0, tzinfo=dt.timezone.utc)  # 23:00 UTC+3
    result = ta_extra.classify_amd_phase([], now_utc)
    assert result["phase"] == "dead_zone"


def test_classify_amd_phase_london_manipulation_uses_price_anchor():
    # 10:00 UTC+3 = 07:00 UTC -- Лондон manipulation-окно (9-13 UTC+3)
    now_utc = dt.datetime(2026, 7, 12, 7, 0, tzinfo=dt.timezone.utc)
    midnight_utc_ms = int(dt.datetime(2026, 7, 12, 4, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
    # NY midnight летом (EDT, UTC-4) = 04:00 UTC
    before = _candle(midnight_utc_ms - 3600_000, 100, 101, 99, 100.0)
    candles = [before]
    result = ta_extra.classify_amd_phase(candles, now_utc)
    assert result["phase"] in ("manipulation_bull", "manipulation_bear", "manipulation")
    assert result["nymidnight_price"] == 100.0


def test_classify_amd_phase_no_candles_no_price_anchor():
    now_utc = dt.datetime(2026, 7, 12, 7, 0, tzinfo=dt.timezone.utc)
    result = ta_extra.classify_amd_phase([], now_utc)
    assert result["nymidnight_price"] is None
    assert result["price_vs_nymidnight"] is None
    assert result["phase"] == "manipulation"  # нет якоря -- нейтральная ветка


def test_classify_amd_phase_defaults_now_when_not_given():
    # не должно падать без now_utc -- использует datetime.now()
    result = ta_extra.classify_amd_phase([])
    assert "phase" in result


# ── detect_inducement_sweep ──

def _candles_with_sweep_n_bars_ago(n_bars_ago):
    """Строит серию свечей так, чтобы detect_sweep() нашёл sweep_low примерно
    n_bars_ago баров назад от конца."""
    base = 100.0
    candles = []
    # достаточно баров для фрактала + запаса
    for i in range(30):
        candles.append(_candle(i * 3600_000, base, base + 1, base - 0.5, base + 0.3))
    # структурный минимум
    low_idx = 10
    candles[low_idx] = _candle(low_idx * 3600_000, base, base + 0.5, base - 5, base - 4.5)
    # свип этого минимума n_bars_ago баров назад от конца (30 баров всего)
    sweep_idx = 30 - n_bars_ago - 1
    if 0 <= sweep_idx < len(candles):
        candles[sweep_idx] = _candle(sweep_idx * 3600_000, base, base - 4, base - 6, base - 3)
    return candles


def test_detect_inducement_sweep_no_sweep_found():
    flat = [_candle(i * 3600_000, 100, 100.2, 99.8, 100) for i in range(20)]
    result = ta_extra.detect_inducement_sweep(flat)
    assert result["inducement_swept"] is False
    assert result["detail"] is None


def test_detect_inducement_sweep_reuses_detect_sweep(monkeypatch):
    fake_sweep = {"type": "sweep_low", "level": 95.0, "bars_ago": 5, "volume_confirmed": None}
    monkeypatch.setattr(ta_extra, "detect_sweep", lambda candles: fake_sweep)
    result = ta_extra.detect_inducement_sweep([{"dummy": 1}], min_bars_ago=3)
    assert result["inducement_swept"] is True
    assert result["detail"] == fake_sweep


def test_detect_inducement_sweep_too_recent_not_inducement(monkeypatch):
    fake_sweep = {"type": "sweep_low", "level": 95.0, "bars_ago": 1, "volume_confirmed": None}
    monkeypatch.setattr(ta_extra, "detect_sweep", lambda candles: fake_sweep)
    result = ta_extra.detect_inducement_sweep([{"dummy": 1}], min_bars_ago=3)
    assert result["inducement_swept"] is False
    assert result["detail"] == fake_sweep  # честно возвращает найденный sweep, просто не считает inducement


# ── compute_shadow() -- новые поля присутствуют, не влияют на patches_affected ──

class _FakeBotModule:
    def get_killzone_status(self):
        return {"active": {"quality": "A", "name": "London"}}

    def get_killzone_status_shadow(self):
        return {"active": {"quality": "A", "name": "London"}}


def test_compute_shadow_includes_amd_and_inducement_fields():
    result = {
        "block11_trade_plan": {
            "direction": "long", "entry1": 100, "entry3": 98, "sl": 96,
            "tp1": 104, "tp2": 108, "tp3": 112, "rr_tp1": 2.0,
        },
        "candles_4h": [
            {"timestamp": 0, "open": 99, "high": 101, "low": 98, "close": 100},
        ],
    }
    record = se.compute_shadow("BTCUSDT", result, _FakeBotModule())
    assert "amd_phase_methodology" in record
    assert "phase" in record["amd_phase_methodology"]
    assert "inducement" in record
    assert "inducement_swept" in record["inducement"]


def test_compute_shadow_amd_failure_does_not_break_record(monkeypatch):
    monkeypatch.setattr(ta_extra, "classify_amd_phase", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    result = {
        "block11_trade_plan": {"direction": "long", "entry1": 100, "entry3": 98, "sl": 96,
                                "tp1": 104, "tp2": 108, "tp3": 112, "rr_tp1": 2.0},
        "candles_4h": [{"timestamp": 0, "open": 99, "high": 101, "low": 98, "close": 100}],
    }
    record = se.compute_shadow("BTCUSDT", result, _FakeBotModule())
    assert record["amd_phase_methodology"]["phase"] is None
    assert any("amd_phase" in d for d in record["discrepancy"])


def test_compute_shadow_amd_inducement_not_in_patches_affected():
    result = {
        "block11_trade_plan": {"direction": "long", "entry1": 100, "entry3": 98, "sl": 96,
                                "tp1": 104, "tp2": 108, "tp3": 112, "rr_tp1": 2.0},
        "candles_4h": [{"timestamp": 0, "open": 99, "high": 101, "low": 98, "close": 100}],
    }
    record = se.compute_shadow("BTCUSDT", result, _FakeBotModule())
    for p in record["patches_affected"]:
        assert "amd" not in p and "inducement" not in p
