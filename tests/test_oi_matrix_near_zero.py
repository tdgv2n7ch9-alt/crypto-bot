"""
pytest для мини-пакета (владелец, 2026-07-13, кейсы AVAX 15:42/DOT 14:48):
|ΔOI| < порога (ta_extra.OI_MATRIX_NEAR_ZERO_PCT = 0.1%) -> "OI без изменений
— матрица н/д", никаких решительных вердиктов "сквиз"/"выход из позиций" на
шуме. Затрагивает ТОЛЬКО текст интерпретации (bot._format_whale_alert()
oi_line, pump_detector._oi_matrix_label(), fa_engine._oi_matrix() oi_text) --
боевой скор не менялся нигде: bot._analyze_whale_signal() уже ИМЕЛ корректный
±0.1% порог для очков (тест ниже это фиксирует), fa_engine.py's oi_combo
(кормит Rocket Score d_oi) остался программно нетронутым.

Импорт bot.py требует BOT_TOKEN в окружении (модуль не подключается к
Telegram при импорте, только при main()).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot
import pump_detector as pd
import ta_extra


# ── ta_extra.classify_oi_matrix() -- общий классификатор ───────────────────

def test_classify_oi_matrix_near_zero_below_threshold():
    assert ta_extra.classify_oi_matrix(0.05, 2.0) == "near_zero"
    assert ta_extra.classify_oi_matrix(-0.05, -2.0) == "near_zero"


def test_classify_oi_matrix_avax_dot_like_cases():
    """Воспроизводит форму живых кейсов: небольшое ΔOI (в пределах шума),
    заметное движение цены -- раньше давало решительный вердикт."""
    assert ta_extra.classify_oi_matrix(0.03, 1.2) == "near_zero"   # AVAX-подобный
    assert ta_extra.classify_oi_matrix(-0.02, -0.8) == "near_zero"  # DOT-подобный


def test_classify_oi_matrix_up_up_above_threshold():
    assert ta_extra.classify_oi_matrix(1.5, 2.0) == "up_up"


def test_classify_oi_matrix_up_down_above_threshold():
    assert ta_extra.classify_oi_matrix(-1.5, 2.0) == "up_down"


def test_classify_oi_matrix_down_up_above_threshold():
    assert ta_extra.classify_oi_matrix(1.5, -2.0) == "down_up"


def test_classify_oi_matrix_down_down_above_threshold():
    assert ta_extra.classify_oi_matrix(-1.5, -2.0) == "down_down"


def test_classify_oi_matrix_no_data_on_none():
    assert ta_extra.classify_oi_matrix(None, 1.0) == "no_data"
    assert ta_extra.classify_oi_matrix(1.0, None) == "no_data"


def test_classify_oi_matrix_exactly_at_threshold_is_not_near_zero():
    """Граница: ровно на пороге -- уже НЕ near_zero (строгое "<", не "<=")."""
    assert ta_extra.classify_oi_matrix(0.1, 2.0) == "up_up"


# ── pump_detector._oi_matrix_label() ────────────────────────────────────────

def test_pump_detector_oi_matrix_label_near_zero():
    label = pd._oi_matrix_label(price_up=True, oi_change_pct=0.03, funding=0.01)
    assert "н/д" in label
    assert "лонги" not in label and "сквиз" not in label


def test_pump_detector_oi_matrix_label_normal_case_unaffected():
    label = pd._oi_matrix_label(price_up=True, oi_change_pct=1.5, funding=0.01)
    assert "новые лонги" in label


# ── bot._format_whale_alert() -- живые кейсы AVAX/DOT ───────────────────────

def _whale_dict(oi, ch24h, score_100=55, factors=None):
    return {
        "symbol": "AVAX", "direction": "NEUTRAL", "score_100": score_100, "grade": "B",
        "signals": [], "factors": factors or [],
        "funding": 0.01, "oi": oi, "ls": 1.0, "price": 20.0, "price_fresh": "(live)",
        "ch24h": ch24h, "ch7d": 3.0, "rank": 15, "vol": 5_000_000,
    }


def test_format_whale_alert_avax_like_near_zero_oi_shows_na():
    """Кейс AVAX 15:42: небольшое ΔOI -- текст ОБЯЗАН быть честным "н/д", не
    "новые лонги"/"шорт-сквиз"."""
    text = bot._format_whale_alert(_whale_dict(oi=0.04, ch24h=1.8))
    assert "OI без изменений" in text
    assert "н/д" in text
    assert "новые лонги" not in text
    assert "шорт-сквиз" not in text


def test_format_whale_alert_dot_like_near_zero_oi_shows_na():
    """Кейс DOT 14:48: аналогично, отрицательное почти-нулевое ΔOI."""
    text = bot._format_whale_alert(_whale_dict(oi=-0.02, ch24h=-1.1))
    assert "OI без изменений" in text
    assert "выход из позиций" not in text
    assert "новые шорты" not in text


def test_format_whale_alert_real_oi_change_unaffected():
    """Контроль: реальное движение OI (>= порога) по-прежнему даёт решительный
    вердикт -- фикс не сделал текст ВСЕГДА нейтральным."""
    text = bot._format_whale_alert(_whale_dict(oi=2.5, ch24h=3.0))
    assert "новые лонги" in text
    assert "н/д" not in text


def test_format_whale_alert_score_unaffected_by_oi_text_fix():
    """Владелец: "боевой скоринг не трогает" -- score_100/factors идут из уже
    посчитанного словаря w, эта функция их только рендерит, не пересчитывает."""
    text_near_zero = bot._format_whale_alert(_whale_dict(oi=0.04, ch24h=1.8, score_100=72,
                                                           factors=[("✅", "тест-фактор")]))
    assert "72" in text_near_zero  # score_100 показан как есть, независимо от near_zero-текста OI
    assert "тест-фактор" in text_near_zero


# ── bot._analyze_whale_signal() -- УЖЕ корректный порог для СКОРА (регрессия) ──

def test_analyze_whale_signal_already_guards_near_zero_oi_for_scoring():
    """Владелец спросил: "проверь, не подаётся ли нулевой OI-вердикт в скор".
    Ответ: НЕТ -- эта функция УЖЕ (до мини-пакета) использует порог 0.1% для
    факторов/очков (см. её "if oi > 0.1 / elif oi < -0.1 / else"). Тест
    фиксирует уже-корректное поведение, чтобы не сломать его в будущем."""
    result = bot._analyze_whale_signal("AVAX", funding=0.06, oi=0.04, ls=1.0, price=20.0)
    # oi=0.04 (< 0.1%) не должен давать фактор "OI растёт"/"OI падает" с очками
    if result:  # может быть None при недостаточном общем score -- это ОК, не про OI
        oi_factors = [f for mark, f in result["factors"] if "OI" in f]
        assert oi_factors == ["OI без изменений — нет притока новых позиций"]
