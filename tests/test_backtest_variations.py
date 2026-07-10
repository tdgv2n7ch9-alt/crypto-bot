"""Чистые функции backtest/variations.py (без сети -- replay_trade делает HTTP-запрос
к Bybit, здесь не тестируется, только логика variation_* на готовых replay-словарях)."""
import backtest.variations as v


def _replay(tp1_hit=False, tp2_hit=False, tp3_hit=False, sl_hit=False,
            sl_after_tp1=False, plus1r=False, sl_after_plus1r=False,
            r_tp1=1.0, r_tp2=2.0, r_tp3=3.0, actual_r_journal=0.0, outcome="TP1_HIT"):
    return {
        "symbol": "X", "direction": "long",
        "actual_r_journal": actual_r_journal, "actual_outcome_journal": outcome,
        "r_tp1": r_tp1, "r_tp2": r_tp2, "r_tp3": r_tp3,
        "path": {"tp1": tp1_hit, "tp2": tp2_hit, "tp3": tp3_hit, "sl": sl_hit,
                 "sl_after_tp1": sl_after_tp1, "plus1r_reached": plus1r,
                 "sl_after_plus1r": sl_after_plus1r},
    }


def test_variation_baseline_uses_journal_value_directly():
    rep = _replay(actual_r_journal=0.13)
    assert v.variation_baseline(rep) == 0.13


def test_extra_r_available_zero_when_journal_already_furthest():
    rep = _replay(tp1_hit=True, tp2_hit=True, tp3_hit=True, actual_r_journal=3.0, outcome="TP3_HIT")
    assert v.extra_r_available(rep) == 0.0


def test_extra_r_available_positive_when_price_went_further_after_journal_stop():
    # journal stopped at TP1 (0.13R declared) but replay shows price also reached TP2 (r_tp2=2.0)
    rep = _replay(tp1_hit=True, tp2_hit=True, actual_r_journal=0.13, r_tp1=1.0, r_tp2=2.0, outcome="TP1_HIT")
    assert v.extra_r_available(rep) == 2.0 - 0.13


def test_extra_r_available_zero_for_sl_outcome():
    rep = _replay(sl_hit=True, actual_r_journal=-1.0, outcome="SL_HIT")
    assert v.extra_r_available(rep) == 0.0


def test_tp_split_straight_sl_no_tp1():
    rep = _replay(sl_hit=True)
    assert v.variation_tp_split(rep, 0.5) == -1.0


def test_tp_split_tp1_then_sl_after():
    rep = _replay(tp1_hit=True, sl_hit=True, sl_after_tp1=True, r_tp1=1.0)
    # 50% locked at r_tp1=1.0, 50% rides and gets stopped at -1.0 -> blended 0.0
    assert v.variation_tp_split(rep, 0.5) == 0.0


def test_tp_split_tp1_then_tp2():
    rep = _replay(tp1_hit=True, tp2_hit=True, r_tp1=1.0, r_tp2=2.0)
    assert v.variation_tp_split(rep, 0.5) == 1.5
    assert round(v.variation_tp_split(rep, 0.3), 3) == round(0.3 * 1.0 + 0.7 * 2.0, 3)


def test_tp_split_tp1_only_no_further_data():
    rep = _replay(tp1_hit=True, r_tp1=1.0)
    # no TP2/TP3, no SL-after-TP1 -- remainder assumed closed at TP1 level (last known point)
    assert v.variation_tp_split(rep, 0.5) == 1.0


def test_sl_to_be_after_tp1_full_ride_to_tp3():
    rep = _replay(tp1_hit=True, tp2_hit=True, tp3_hit=True, r_tp3=3.0)
    assert v.variation_sl_to_be_after_tp1(rep) == 3.0


def test_sl_to_be_after_tp1_reverses_to_be():
    rep = _replay(tp1_hit=True, sl_hit=True, sl_after_tp1=True)
    assert v.variation_sl_to_be_after_tp1(rep) == 0.0


def test_sl_to_be_after_tp1_no_tp1_straight_sl():
    rep = _replay(sl_hit=True)
    assert v.variation_sl_to_be_after_tp1(rep) == -1.0


def test_sl_to_be_after_plus1r_never_reached_straight_sl():
    rep = _replay(sl_hit=True, plus1r=False)
    assert v.variation_sl_to_be_after_plus1r(rep) == -1.0


def test_sl_to_be_after_plus1r_reached_then_be():
    rep = _replay(plus1r=True, sl_hit=True, sl_after_plus1r=True)
    assert v.variation_sl_to_be_after_plus1r(rep) == 0.0


def test_sl_to_be_after_plus1r_reached_then_tp2():
    rep = _replay(plus1r=True, tp1_hit=True, tp2_hit=True, r_tp2=2.0)
    assert v.variation_sl_to_be_after_plus1r(rep) == 2.0


def test_hit_long_direction():
    candle = {"h": 105, "l": 95, "o": 100, "c": 102}
    assert v._hit("long", candle, 104, is_tp=True) is True
    assert v._hit("long", candle, 106, is_tp=True) is False
    assert v._hit("long", candle, 96, is_tp=False) is True


def test_hit_short_direction():
    candle = {"h": 105, "l": 95, "o": 100, "c": 98}
    assert v._hit("short", candle, 96, is_tp=True) is True
    assert v._hit("short", candle, 94, is_tp=True) is False
    assert v._hit("short", candle, 104, is_tp=False) is True
