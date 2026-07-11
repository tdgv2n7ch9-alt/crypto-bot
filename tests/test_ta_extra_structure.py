"""
pytest для ta_extra.build_trade_from_structure() -- чистая функция (вход/SL/TP от зон
структуры), уже используется fa_engine.py/real_full_analysis. Только чтение/проверка
существующей логики, ничего не меняется.

Запуск: pytest tests/ (требует pip install pytest, не входит в requirements.txt --
не нужен в проде на Railway, только для локальной разработки).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ta_extra


def _zone(lo, hi, sources=("1h", "4h"), touches=3):
    return {"lo": lo, "hi": hi, "mid": (lo + hi) / 2, "sources": list(sources), "touches": touches}


def test_long_entry_dca_50_30_20_ordering():
    """entry1 (50%, ближе к зоне зоны у цены) должен быть БЛИЖЕ к текущей цене, чем entry3
    (20%, дальний/агрессивный транш) -- порядок ДОЛЖЕН быть entry1 > entry3 для LONG
    (зона ниже цены, entry1 у верхней границы зоны = ближе к цене)."""
    price = 100.0
    zones = {"below": [_zone(90, 95)], "above": [_zone(105, 110)]}
    trade = ta_extra.build_trade_from_structure("long", price, zones)
    assert trade is not None
    assert trade["entry1"] > trade["entry3"], "entry1 (ближний) должен быть выше entry3 (дальний) для LONG"
    assert trade["entry1"] == 95  # верхняя граница зоны (ближе к цене)
    assert trade["entry3"] == 90  # нижняя граница зоны (дальше от цены)
    assert trade["sl"] < trade["entry3"], "SL должен быть ЗА зоной (ниже), не внутри"


def test_short_entry_dca_ordering_mirrors_long():
    price = 100.0
    zones = {"below": [_zone(90, 95)], "above": [_zone(105, 110)]}
    trade = ta_extra.build_trade_from_structure("short", price, zones)
    assert trade is not None
    assert trade["entry1"] < trade["entry3"], "entry1 (ближний) должен быть ниже entry3 (дальний) для SHORT"
    assert trade["entry1"] == 105  # нижняя граница зоны сопротивления (ближе к цене)
    assert trade["entry3"] == 110
    assert trade["sl"] > trade["entry3"], "SL должен быть ЗА зоной (выше), не внутри"


def test_rr_gate_pass_matches_threshold():
    """rr_gate_pass должен ТОЧНО соответствовать rr_tp1 >= SR_MIN_RR_TP1 -- не отдельная,
    рассинхронизированная логика."""
    price = 100.0
    # Узкая зона у цены (маленький риск) + далёкая зона выше (большая награда) -- высокий R:R
    zones_good = {"below": [_zone(99, 99.5)], "above": [_zone(130, 131)]}
    trade_good = ta_extra.build_trade_from_structure("long", price, zones_good)
    assert trade_good["rr_tp1"] >= ta_extra.SR_MIN_RR_TP1
    assert trade_good["rr_gate_pass"] is True

    # Широкая зона у цены (большой риск) + близкая зона выше (маленькая награда) -- низкий R:R
    zones_bad = {"below": [_zone(80, 99)], "above": [_zone(100.5, 101)]}
    trade_bad = ta_extra.build_trade_from_structure("long", price, zones_bad)
    assert trade_bad["rr_tp1"] < ta_extra.SR_MIN_RR_TP1
    assert trade_bad["rr_gate_pass"] is False


def test_no_entry_zone_returns_none():
    """Нет зоны с нужной стороны -- функция должна вернуть None (вызывающий код уходит в
    ATR-фоллбэк с честным rr_gate_pass=False), не падать и не выдумывать зону."""
    price = 100.0
    trade = ta_extra.build_trade_from_structure("long", price, {"below": [], "above": [_zone(105, 110)]})
    assert trade is None


def test_zero_or_negative_price_returns_none():
    zones = {"below": [_zone(90, 95)], "above": [_zone(105, 110)]}
    assert ta_extra.build_trade_from_structure("long", 0, zones) is None
    assert ta_extra.build_trade_from_structure("long", -5, zones) is None


def test_fib_fallback_when_fewer_than_3_tp_zones():
    """Меньше 3 TP-зон с противоположной стороны -- фоллбэк на Fibonacci-подобное
    расширение (2.0/3.2/5.0x риска), не падение и не пустой tp2/tp3."""
    price = 100.0
    zones = {"below": [_zone(90, 95)], "above": []}  # ни одной TP-зоны
    trade = ta_extra.build_trade_from_structure("long", price, zones)
    assert trade is not None
    assert trade["tp1"] is not None and trade["tp2"] is not None and trade["tp3"] is not None
    assert trade["tp1"] < trade["tp2"] < trade["tp3"], "TP должны расти по расстоянию"


# weighted_dca_entry() / rr_from_base() -- АПГРЕЙД 11.07 Этап 2.1, x100-сканер:
# честная единая база (средневзвешенный DCA-вход 50/30/20) вместо смеси
# "R:R от entry1, % от live-цены".

def test_weighted_dca_entry_between_entry1_and_entry3():
    price = 100.0
    zones = {"below": [_zone(90, 95)], "above": [_zone(105, 110)]}
    trade = ta_extra.build_trade_from_structure("long", price, zones)
    base = ta_extra.weighted_dca_entry(trade)
    assert trade["entry3"] < base < trade["entry1"]


def test_weighted_dca_entry_matches_manual_50_30_20():
    trade = {"entry1": 100.0, "entry2": 90.0, "entry3": 80.0}
    base = ta_extra.weighted_dca_entry(trade)
    assert base == 100.0 * 0.5 + 90.0 * 0.3 + 80.0 * 0.2


def test_rr_from_base_matches_entry1_when_base_is_entry1():
    """При base==entry1 rr_from_base() должен давать те же числа, что и rr_tp1/rr_tp2/rr_tp3
    внутри build_trade_from_structure() -- проверка, что формула идентична, только база явная."""
    price = 100.0
    zones = {"below": [_zone(90, 95)], "above": [_zone(105, 110)]}
    trade = ta_extra.build_trade_from_structure("long", price, zones)
    rr = ta_extra.rr_from_base(trade, trade["entry1"])
    assert rr["rr_tp1"] == trade["rr_tp1"]
    assert rr["rr_tp2"] == trade["rr_tp2"]
    assert rr["rr_tp3"] == trade["rr_tp3"]
    assert rr["rr_gate_pass"] == trade["rr_gate_pass"]


def test_rr_from_base_gate_respects_min_rr():
    trade = {"tp1": 110.0, "tp2": 120.0, "tp3": 130.0, "sl": 95.0}
    rr_pass = ta_extra.rr_from_base(trade, base=100.0, min_rr=1.5)
    assert rr_pass["rr_tp1"] == 2.0
    assert rr_pass["rr_gate_pass"] is True
    rr_fail = ta_extra.rr_from_base(trade, base=100.0, min_rr=3.0)
    assert rr_fail["rr_gate_pass"] is False
