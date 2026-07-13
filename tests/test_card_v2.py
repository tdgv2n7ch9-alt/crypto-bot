"""
pytest для card_v2.py (Пакет 13, «Понятная карточка v2»). Все входные данные --
синтетические (уже "вычисленные" по спецификации -- этот модуль сам ничего не
считает, только форматирует), без сети/live-состояния. Golden-тесты формата
на 3 сценариях (сильный 80+/средний 60-79/слабый <40) -- явное требование
владельца при приёмке.
"""
import card_v2 as cv


# --- Блок 1: светофор ---

def test_traffic_light_entry_actual_in_zone():
    assert cv.compute_traffic_light(100, 95, 105) == cv.TRAFFIC_ENTRY_ACTUAL


def test_traffic_light_wait_price_outside_zone():
    assert cv.compute_traffic_light(120, 95, 105) == cv.TRAFFIC_WAIT_PRICE


def test_traffic_light_do_not_enter_when_invalidated():
    assert cv.compute_traffic_light(100, 95, 105, invalidated=True) == cv.TRAFFIC_DO_NOT_ENTER


def test_traffic_light_handles_reversed_zone_bounds():
    assert cv.compute_traffic_light(100, 105, 95) == cv.TRAFFIC_ENTRY_ACTUAL


# --- Блок 2: вердикт ---

def test_verdict_act_at_80():
    v = cv.compute_verdict(80)
    assert v["tier"] == cv.VERDICT_ACT
    assert v["label"] == "Действовать"


def test_verdict_reduced_60_to_79():
    v = cv.compute_verdict(65)
    assert v["tier"] == cv.VERDICT_REDUCED


def test_verdict_watch_close_40_to_59_has_detail():
    v = cv.compute_verdict(45, missing_confirmation="дождаться закрытия 4H свечи выше зоны")
    assert v["tier"] == cv.VERDICT_WATCH_CLOSE
    assert "4H" in v["detail"]


def test_verdict_watch_close_default_detail_when_not_given():
    v = cv.compute_verdict(50)
    assert v["detail"] == "ждём дополнительное подтверждение"


def test_verdict_observe_below_40():
    v = cv.compute_verdict(30)
    assert v["tier"] == cv.VERDICT_OBSERVE
    assert v["label"] == "Наблюдение"
    assert v["detail"] is None


def test_verdict_boundary_39_is_observe_40_is_watch_close():
    assert cv.compute_verdict(39)["tier"] == cv.VERDICT_OBSERVE
    assert cv.compute_verdict(40)["tier"] == cv.VERDICT_WATCH_CLOSE


# --- Блок 3: ЧТО ДЕЛАТЬ ---

def test_format_what_to_do_returns_separated_signal_and_prose_lines():
    entries = [(100.0, 50), (98.0, 30), (96.0, 20)]
    deposit_1000 = {1: {"risk_usd": 10.0}, 2: {"risk_usd": 20.0}, 3: {"risk_usd": 30.0}}
    tps = [{"price": 110.0, "sell_pct": 50, "stop_note": "б/у"}]
    result = cv.format_what_to_do("LONG", entries, sl=94.0, sl_risk_pct=6.0, tps=tps,
                                   deposit_1000=deposit_1000,
                                   invalidation_note="закрытие ниже 94", valid_until_note="24ч")
    assert "signal_lines" in result and "prose_lines" in result and "all_lines" in result
    assert any("SL 100%" in l for l in result["signal_lines"])
    assert any("НЕ входить" in l for l in result["prose_lines"])
    assert result["all_lines"] == result["signal_lines"] + [""] + result["prose_lines"]


def test_format_what_to_do_signal_lines_within_length_limit():
    entries = [(60000.0, 50), (59500.0, 30), (59000.0, 20)]
    deposit_1000 = {1: {"risk_usd": 10.0}, 2: {"risk_usd": 20.0}, 3: {"risk_usd": 30.0}}
    tps = [{"price": 62000.0, "sell_pct": 40, "stop_note": "б/у"},
           {"price": 64000.0, "sell_pct": 30, "stop_note": None}]
    result = cv.format_what_to_do("LONG", entries, sl=57000.0, sl_risk_pct=5.0, tps=tps,
                                   deposit_1000=deposit_1000,
                                   invalidation_note="закрытие ниже 57000",
                                   valid_until_note="24ч от публикации")
    assert cv.check_signal_line_lengths(result["signal_lines"]) == []


# --- Блок 4: ЗА/ПРОТИВ ---

def test_split_pros_cons_pads_to_minimum_two_cons():
    factors = [("Тренд по направлению", 15), ("RSI дивергенция против", -5)]
    split = cv.split_pros_cons(factors)
    assert len(split["cons"]) >= 2
    assert "RSI дивергенция против" in split["cons"]


def test_split_pros_cons_no_padding_when_enough_real_cons():
    factors = [("За 1", 10), ("Против 1", -5), ("Против 2", -3), ("Против 3", -1)]
    split = cv.split_pros_cons(factors)
    assert split["cons"] == ["Против 1", "Против 2", "Против 3"]
    for generic in cv.GENERIC_CONS_POOL:
        assert generic not in split["cons"]


def test_split_pros_cons_never_empty_cons_even_with_all_positive_factors():
    factors = [("За 1", 10), ("За 2", 5)]
    split = cv.split_pros_cons(factors)
    assert len(split["cons"]) >= 2


def test_split_pros_cons_pros_sorted_descending_and_capped():
    factors = [("A", 1), ("B", 9), ("C", 5), ("D", 20), ("E", 3), ("F", 2)]
    split = cv.split_pros_cons(factors, max_pros=3)
    assert split["pros"] == ["D", "B", "C"]


# --- Блок 6: капитал ---

def test_compute_capital_table_scales_with_deposit_and_risk_pct():
    table = cv.compute_capital_table(price=100, sl=95)
    assert table[1000][1]["risk_usd"] == 10.0
    assert table[10000][1]["risk_usd"] == 100.0
    assert table[1000][2]["risk_usd"] == 20.0


def test_compute_capital_table_position_usd_from_sl_distance():
    table = cv.compute_capital_table(price=100, sl=95)  # 5% дистанция до SL
    # risk_usd=10 при 1% на $1000, position = risk_usd / (sl_distance_pct/100) = 10/0.05=200
    assert table[1000][1]["position_usd"] == 200.0


def test_format_capital_block_shows_na_without_zone_capacity():
    table = cv.compute_capital_table(price=100, sl=95)
    lines = cv.format_capital_block(table)
    assert any("н/д" in l for l in lines)


def test_format_capital_block_shows_capacity_when_given():
    table = cv.compute_capital_table(price=100, sl=95)
    lines = cv.format_capital_block(table, zone_capacity_usd=50000)
    assert any("50,000" in l for l in lines)


# --- Типографика ---

def test_check_signal_line_lengths_flags_long_lines():
    lines = ["short", "x" * 40]
    assert cv.check_signal_line_lengths(lines) == [1]


def test_check_signal_line_lengths_empty_when_all_short():
    lines = ["short one", "another short"]
    assert cv.check_signal_line_lengths(lines) == []


# --- Golden-тесты формата (3 сценария, спецификация владельца) ---

def _build_card(score, factors, missing_confirmation=None, checklist_ok_count=5):
    checklist_items = [(f"Пункт {i}", i < checklist_ok_count) for i in range(1, 7)]
    strength_lines = cv.format_strength_block(score, checklist_items, missing_confirmation)

    entries = [(60000.0, 50), (59500.0, 30), (59000.0, 20)]
    sl = 57000.0
    sl_risk_pct = abs(entries[0][0] - sl) / entries[0][0] * 100
    capital_table = cv.compute_capital_table(entries[0][0], sl)
    deposit_1000 = {pct: capital_table[1000][pct] for pct in cv.RISK_PCTS}
    tps = [
        {"price": 62000.0, "sell_pct": 40, "stop_note": "б/у"},
        {"price": 64000.0, "sell_pct": 30, "stop_note": None},
        {"price": 67000.0, "sell_pct": 30, "stop_note": None},
    ]
    what_to_do = cv.format_what_to_do(
        "LONG", entries, sl, sl_risk_pct, tps, deposit_1000,
        invalidation_note="закрытие 4H-свечи ниже 57000",
        valid_until_note="24ч от публикации",
    )
    what_to_do_lines = what_to_do["all_lines"]

    pros_cons_lines = cv.format_pros_cons(factors)

    context_lines = cv.format_context(
        higher_tf_trend="1D бычий",
        btc_label="BTC нейтрально-бычий, 4H восходящий",
        events_lines=[],
        rug_line="",
        liq_lines=["🗺 Ликвидации рядом (±1%): $2,300,000 -- ретроспектива недавних ликвидаций, не прогноз"],
    )

    capital_lines = cv.format_capital_block(capital_table)

    timing_lines = cv.format_timing(
        killzone_active=True, killzone_name="NY Open", next_killzone_name=None,
        next_killzone_in_min=None, distance_to_zone_pct=0.3,
    )

    traffic = cv.compute_traffic_light(60000.0, 59000.0, 60500.0)
    text = cv.assemble_card_v2(traffic, "BTCUSDT", "LONG", strength_lines, what_to_do_lines,
                                pros_cons_lines, context_lines, capital_lines, timing_lines)
    return text, what_to_do["signal_lines"]


def test_golden_strong_setup_80_plus():
    factors = [("Тренд 1D по направлению", 15), ("EMA-стек бычий", 10),
               ("RSI дивергенция против направления", -8)]
    text, signal_lines = _build_card(score=85, factors=factors)
    assert "Сила сетапа: 85/100" in text
    assert "Действовать" in text
    assert "🟢 ВХОД АКТУАЛЕН" in text
    assert text.count("⚠️") >= 2  # минимум 2 "против"
    long_lines = cv.check_signal_line_lengths(signal_lines)
    assert long_lines == [], f"строки длиннее {cv.MAX_SIGNAL_LINE_CHARS} симв.: " \
                              f"{[signal_lines[i] for i in long_lines]}"


def test_golden_medium_setup_60_to_79():
    factors = [("Killzone активна", 8), ("Funding нейтральный", 3)]
    text, signal_lines = _build_card(score=65, factors=factors)
    assert "Сила сетапа: 65/100" in text
    assert "Уменьшенный объём" in text
    assert text.count("⚠️") >= 2
    assert cv.check_signal_line_lengths(signal_lines) == []


def test_golden_weak_setup_below_40_verdict_observe():
    factors = [("RSI дивергенция против направления", -10), ("Funding против позиции", -4)]
    text, signal_lines = _build_card(score=30, factors=factors, checklist_ok_count=2)
    assert "Сила сетапа: 30/100" in text
    assert "Наблюдение" in text
    assert text.count("⚠️") >= 2
    assert cv.check_signal_line_lengths(signal_lines) == []
