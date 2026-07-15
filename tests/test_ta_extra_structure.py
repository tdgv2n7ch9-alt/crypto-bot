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


# --- TP-лестница: минимальный шаг между соседними целями (владелец, кейс MOODENG
# 2026-07-13: TP1 0.0400262 vs TP2 0.04002969 -- разница 0.009%, неразличимо на
# карточке). TP1 НЕ трогается (боевой rr_gate_pass зависит только от него) ---

def test_moodeng_case_near_duplicate_tp_zones_get_separated():
    """Воспроизводит живой кейс: две TP-зоны с почти идентичным mid. TP1 --
    первая (не меняется), TP2 -- ОБЯЗАН отличаться от TP1 минимум на
    TP_LADDER_MIN_STEP_PCT, не быть тем же почти-дубликатом."""
    price = 0.0395
    zones = {
        "below": [_zone(0.038, 0.039)],
        "above": [
            _zone(0.0400262, 0.0400262),   # TP1 -- ближайшая зона
            _zone(0.04002969, 0.04002969), # почти дубликат TP1 -- ДОЛЖНА быть пропущена для TP2
            _zone(0.043, 0.043),           # достаточно далёкая зона -- валидный кандидат TP2
        ],
    }
    trade = ta_extra.build_trade_from_structure("long", price, zones)
    assert trade is not None
    assert trade["tp1"] == 0.0400262  # TP1 -- поведение НЕ изменилось
    step_pct = abs(trade["tp2"] - trade["tp1"]) / trade["entry1"] * 100
    assert step_pct >= ta_extra.TP_LADDER_MIN_STEP_PCT, "TP2 обязан отличаться от TP1 минимум на шаг"
    assert trade["tp2"] != 0.04002969, "почти-дубликат НЕ должен был быть выбран как TP2"
    assert trade["tp2"] == 0.043  # реальная разнесённая зона взята вместо дубликата


def test_tp_ladder_step_enforced_between_tp2_and_tp3_too():
    """Шаг проверяется и между TP2 и TP3, не только TP1/TP2."""
    price = 100.0
    zones = {
        "below": [_zone(90, 95)],
        "above": [
            _zone(110, 110),     # TP1
            _zone(120, 120),     # TP2 -- достаточно далеко от TP1
            _zone(120.05, 120.05),  # почти дубликат TP2 -- должна быть пропущена для TP3
            _zone(140, 140),     # валидный кандидат TP3
        ],
    }
    trade = ta_extra.build_trade_from_structure("long", price, zones)
    step_pct = abs(trade["tp3"] - trade["tp2"]) / trade["entry1"] * 100
    assert step_pct >= ta_extra.TP_LADDER_MIN_STEP_PCT
    assert trade["tp3"] == 140


def test_tp1_selection_unchanged_by_ladder_validator():
    """TP1 -- буквально та же зона, что и раньше (ближайшая), валидатор её не
    трогает -- боевой rr_gate_pass зависит только от TP1."""
    price = 100.0
    zones = {"below": [_zone(90, 95)], "above": [_zone(105, 110), _zone(105.1, 105.1)]}
    trade = ta_extra.build_trade_from_structure("long", price, zones)
    assert trade["tp1"] == _zone(105, 110)["mid"]


def test_tp_ladder_falls_back_to_fibonacci_when_no_separated_zone_left():
    """Если после TP1 не осталось разнесённых зон структуры -- Fibonacci-фоллбэк
    (уже существующее поведение, гарантированно разнесённое), не падение и не
    повтор TP1."""
    price = 100.0
    zones = {
        "below": [_zone(90, 95)],
        "above": [_zone(110, 110), _zone(110.05, 110.05)],  # только TP1 + почти-дубликат
    }
    trade = ta_extra.build_trade_from_structure("long", price, zones)
    assert trade["tp2"] != 110.05
    assert trade["tp1"] < trade["tp2"] < trade["tp3"]
    assert trade["tp_sources"][0] == "structure"
    assert trade["tp_sources"][1] == "fibonacci"
    assert trade["tp_sources"][2] == "fibonacci"


def test_tp_sources_all_structure_when_well_separated():
    price = 100.0
    zones = {"below": [_zone(90, 95)], "above": [_zone(105, 110), _zone(120, 120), _zone(140, 140)]}
    trade = ta_extra.build_trade_from_structure("long", price, zones)
    assert trade["tp_sources"] == ["structure", "structure", "structure"]


# --- Гарантия монотонности Fibonacci-фоллбэка (владелец, ДА 2026-07-15, живой
# кейс ZEC: TP2=687.33 дальше TP3=669.53 -- TP3 упал на фиксированный
# fib_mults[2]=5.0x риска, который оказался БЛИЖЕ структурной TP2 на 8x+
# риска). Фикс: фоллбэк = max(табличный_множитель, факт_дистанция_last + шаг) ---

def test_zec_case_far_structure_tp2_forces_fibonacci_tp3_beyond_it():
    """Точное воспроизведение живого бага: TP1 близко (структура), TP2 --
    структурная зона ДАЛЕКО (8x риска от entry1), TP3 -- ни одной зоны не
    осталось (фоллбэк). До фикса TP3 был бы fib(5.0x) = БЛИЖЕ TP2 (8x) --
    нарушение монотонности. После фикса TP3 обязан быть строго дальше TP2."""
    price = 100.0
    zones = {
        "below": [_zone(90, 95)],  # entry1=95, sl=90*(1-2.5%)=87.75, risk=7.25
        "above": [
            _zone(100, 100),     # TP1 -- близко (структура)
            _zone(153, 153),     # TP2 -- далеко, ровно 8.0x риска от entry1 (структура)
            # TP3: зон не осталось -- фоллбэк
        ],
    }
    trade = ta_extra.build_trade_from_structure("long", price, zones)
    assert trade is not None
    assert trade["tp_sources"] == ["structure", "structure", "fibonacci"]
    assert trade["tp1"] == 100
    assert trade["tp2"] == 153
    assert trade["tp3"] > trade["tp2"], "TP3 обязан быть строго дальше TP2 -- баг ZEC воспроизведён бы иначе"
    # Фактическое значение: next_mult = max(5.0, 8.0 + 1.0) = 9.0 -> tp3 = 95 + 7.25*9.0
    assert trade["tp3"] == 95 + 7.25 * 9.0


def test_generated_targets_always_ordered_and_rr_increases():
    """Тест владельца дословно: "сгенерённые цели всегда упорядочены, R:R
    возрастает" -- по нескольким сценариям (только структура/только фоллбэк/
    смешанные с конфликтной дальней зоной)."""
    scenarios = [
        # только фоллбэк (нет ни одной TP-зоны)
        {"below": [_zone(90, 95)], "above": []},
        # только структура, хорошо разнесена
        {"below": [_zone(90, 95)], "above": [_zone(105, 110), _zone(120, 120), _zone(140, 140)]},
        # структура TP1 + далёкая TP2 (8x) + фоллбэк TP3 -- конфликтный кейс (баг ZEC)
        {"below": [_zone(90, 95)], "above": [_zone(100, 100), _zone(153, 153)]},
        # структура TP1 + ОЧЕНЬ далёкая TP2 (20x риска) + фоллбэк TP3
        {"below": [_zone(90, 95)], "above": [_zone(100, 100), _zone(240, 240)]},
        # short-направление, аналогичный конфликтный кейс
        {"below": [_zone(60, 60)], "above": [_zone(105, 110)]},
    ]
    for zones in scenarios:
        for direction in ("long", "short"):
            entry_side = "below" if direction == "long" else "above"
            if not zones.get(entry_side):
                continue
            trade = ta_extra.build_trade_from_structure(direction, 100.0, zones)
            if trade is None:
                continue
            tp1, tp2, tp3 = trade["tp1"], trade["tp2"], trade["tp3"]
            entry1, sl = trade["entry1"], trade["sl"]
            d1 = abs(tp1 - entry1)
            d2 = abs(tp2 - entry1)
            d3 = abs(tp3 - entry1)
            assert d1 < d2 < d3, f"цели должны быть строго упорядочены по расстоянию: {zones} {direction}"
            assert trade["rr_tp1"] < trade["rr_tp2"] < trade["rr_tp3"], f"R:R должен строго возрастать: {zones} {direction}"


def test_fibonacci_fallback_unchanged_when_no_conflict():
    """Когда фактическая дистанция last НЕ превышает табличный множитель
    (обычный случай, зоны структуры не создают конфликт) -- фоллбэк даёт
    ТЕ ЖЕ числа, что и до фикса (max(табличное, ...) == табличное)."""
    price = 100.0
    zones = {"below": [_zone(90, 95)], "above": []}  # ни одной TP-зоны -- чистый фоллбэк с самого начала
    trade = ta_extra.build_trade_from_structure("long", price, zones)
    entry1, sl = trade["entry1"], trade["sl"]
    risk = abs(entry1 - sl)
    assert trade["tp1"] == entry1 + risk * 2.0
    assert trade["tp2"] == entry1 + risk * 3.2
    assert trade["tp3"] == entry1 + risk * 5.0


def test_rr_gate_pass_unaffected_by_tp_ladder_fix():
    """Мини-пакет, владелец: "боевой скоринг не трогает" -- rr_gate_pass зависит
    ТОЛЬКО от TP1/entry1/SL, которые валидатор лестницы не меняет. Один и тот
    же сценарий с/без near-duplicate TP2-зоны обязан давать ОДИНАКОВЫЙ
    rr_gate_pass/rr_tp1."""
    price = 100.0
    zones_clean = {"below": [_zone(99, 99.5)], "above": [_zone(130, 131)]}
    zones_with_duplicate = {"below": [_zone(99, 99.5)],
                             "above": [_zone(130, 131), _zone(130.01, 130.01)]}
    trade_clean = ta_extra.build_trade_from_structure("long", price, zones_clean)
    trade_dup = ta_extra.build_trade_from_structure("long", price, zones_with_duplicate)
    assert trade_clean["rr_tp1"] == trade_dup["rr_tp1"]
    assert trade_clean["rr_gate_pass"] == trade_dup["rr_gate_pass"]
    assert trade_clean["tp1"] == trade_dup["tp1"]
