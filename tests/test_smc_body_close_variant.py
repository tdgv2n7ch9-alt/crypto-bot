"""
pytest для ta_extra.smc_setup_type_body_close_variant() -- Пакет 11 М1, A/B тело-vs-фитиль
(владелец "да"): shadow-only альтернатива smc_setup_type() с гейтом на закрытие свечи за
уровнем (находка METHODOLOGY_CORE.md §1 -- конфликт "Урок 2. Structure.pdf" vs
Инструктора B, уже реализованного в живом smc_setup_type()). Тесты фиксируют оба случая:
где оба варианта СОГЛАСНЫ (закрытие за уровнем реально было) и где они РАСХОДЯТСЯ (пробой
только тенью).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ta_extra as te


def _c(o, h, l, cl):
    return {"open": o, "high": h, "low": l, "close": cl, "vol": 0}


# Восходящий зигзаг: L1(90)@2 H1(115)@6 L2(92)@10 H2(120)@14 L3(94)@18 H3(126)@22,
# последний пробой (idx22) закрывается ВЫШЕ старого хая (120) -- close=124.
_UPTREND_CLOSE_CONFIRMED = [
    _c(100, 101, 99, 100), _c(99, 100, 96, 97), _c(97, 98, 90, 95), _c(95, 99, 94, 98), _c(98, 102, 97, 101),
    _c(101, 108, 100, 107), _c(107, 115, 106, 112), _c(112, 113, 105, 108), _c(108, 109, 102, 104), _c(104, 105, 96, 98),
    _c(98, 99, 92, 95), _c(95, 100, 94, 99), _c(99, 105, 98, 104), _c(104, 112, 103, 110), _c(110, 120, 109, 118),
    _c(118, 119, 111, 113), _c(113, 114, 106, 108), _c(108, 109, 99, 101), _c(101, 102, 94, 96), _c(96, 101, 95, 100),
    _c(100, 107, 99, 106), _c(106, 115, 105, 113), _c(113, 126, 112, 124),
    _c(124, 125, 118, 120), _c(120, 121, 115, 117),
]

# То же самое, НО последний пробой (idx22) закрывается НИЖЕ старого хая (120) -- close=119,
# несмотря на то, что тень (126) пробила уровень -- "SFP", не BOS по критерию body-close.
_UPTREND_WICK_ONLY = [
    _c(100, 101, 99, 100), _c(99, 100, 96, 97), _c(97, 98, 90, 95), _c(95, 99, 94, 98), _c(98, 102, 97, 101),
    _c(101, 108, 100, 107), _c(107, 115, 106, 112), _c(112, 113, 105, 108), _c(108, 109, 102, 104), _c(104, 105, 96, 98),
    _c(98, 99, 92, 95), _c(95, 100, 94, 99), _c(99, 105, 98, 104), _c(104, 112, 103, 110), _c(110, 120, 109, 118),
    _c(118, 119, 111, 113), _c(113, 114, 106, 108), _c(108, 109, 99, 101), _c(101, 102, 94, 96), _c(96, 101, 95, 100),
    _c(100, 107, 99, 106), _c(106, 115, 105, 113), _c(113, 126, 112, 119),
    _c(119, 120, 113, 115), _c(115, 116, 110, 112),
]


def test_close_confirmed_break_both_variants_agree_bos():
    old = te.smc_setup_type(_UPTREND_CLOSE_CONFIRMED, "long")
    new = te.smc_setup_type_body_close_variant(_UPTREND_CLOSE_CONFIRMED, "long")
    assert old["type"] == "BOS_bull"
    assert new["type"] == "BOS_bull"
    assert new["aligned"] is True


def test_wick_only_break_variants_disagree():
    """Ключевой A/B-кейс: тело/фитиль расходятся -- старый движок видит BOS, новый
    (body-close) видит невалидный пробой."""
    old = te.smc_setup_type(_UPTREND_WICK_ONLY, "long")
    new = te.smc_setup_type_body_close_variant(_UPTREND_WICK_ONLY, "long")
    assert old["type"] == "BOS_bull"
    assert new["type"] == "invalid_break_wick_only"
    assert new["aligned"] is None
    assert "SFP" in new["label"]


def test_wick_only_variant_still_registers_range_correctly():
    """Range-детекция (равные хаи/лои) не зависит от body-close-гейта -- проверяем, что
    он не ломает уже согласованное поведение на не-break сценарии."""
    # 3 почти равных хая и 3 почти равных лоя (в пределах ZONE_WIDTH_MIN_PCT) -- range
    flat = [
        _c(100, 101, 99, 100), _c(99, 100, 96, 97), _c(97, 98, 90, 95), _c(95, 99, 94, 98), _c(98, 102, 97, 101),
        _c(101, 108, 100, 107), _c(107, 115.0, 106, 112), _c(112, 113, 105, 108), _c(108, 109, 102, 104), _c(104, 105, 96, 98),
        _c(98, 99, 90.1, 95), _c(95, 100, 94, 99), _c(99, 105, 98, 104), _c(104, 112, 103, 110), _c(110, 115.1, 109, 113),
        _c(113, 114, 106, 108), _c(108, 109, 99, 101), _c(101, 102, 94, 96), _c(96, 101, 90.15, 100),
        _c(100, 107, 99, 106), _c(106, 115.05, 105, 113), _c(105, 106, 100, 102),
        _c(102, 103, 96, 98), _c(98, 99, 93, 95),
    ]
    old = te.smc_setup_type(flat, "long")
    new = te.smc_setup_type_body_close_variant(flat, "long")
    assert old["type"] == new["type"] == "range"


def test_insufficient_swing_points_returns_none_type():
    short_series = [_c(100, 101, 99, 100)] * 5
    out = te.smc_setup_type_body_close_variant(short_series, "long")
    assert out["type"] is None


def test_no_bias_direction_labels_break_without_bos_choch():
    old = te.smc_setup_type(_UPTREND_CLOSE_CONFIRMED, None)
    new = te.smc_setup_type_body_close_variant(_UPTREND_CLOSE_CONFIRMED, None)
    assert old["type"] == "break_up"
    assert new["type"] == "break_up"
    assert new["aligned"] is None


def test_choch_against_bias_still_requires_close_confirmation():
    """bias=short на аптренд-пробой вверх -- CHoCH (против bias), и body-close-гейт
    применяется одинаково, независимо от направления bias."""
    old = te.smc_setup_type(_UPTREND_CLOSE_CONFIRMED, "short")
    new = te.smc_setup_type_body_close_variant(_UPTREND_CLOSE_CONFIRMED, "short")
    assert old["type"] == "CHoCH_bull"
    assert new["type"] == "CHoCH_bull"
    assert new["aligned"] is False

    old_wick = te.smc_setup_type(_UPTREND_WICK_ONLY, "short")
    new_wick = te.smc_setup_type_body_close_variant(_UPTREND_WICK_ONLY, "short")
    assert old_wick["type"] == "CHoCH_bull"
    assert new_wick["type"] == "invalid_break_wick_only"
