"""
pytest для Пакет 18, п.2 (владелец, кейс AVAX 15:17: "Rocket Score 50/100 при
всех компонентах 0" -- непонятно, что означает голое число). Фикс -- ТОЛЬКО
текст в fa_engine.render_full_analysis_card(): база 50 явно подписана,
показываются ТОЛЬКО ненулевые поправки, разложение обязано сходиться с
итогом (50 + сумма показанных дельт == score). Сама формула _rocket_score()
не тронута -- эти тесты гоняют только рендер карточки на синтетическом
result-фикстуре, не реальный расчёт.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fa_engine


def _minimal_result(score, factors, symbol="AVAX", price=20.0):
    """Минимальный result-фикстура для render_full_analysis_card() -- только
    поля, реально читаемые функцией (см. её тело), без сети/реальных данных."""
    return {
        "ok": True, "symbol": symbol, "price": price, "rank": 50,
        "price_fresh": "", "ch1h": 0, "ch24h": 0, "ch7d": 0,
        "block1_bias": {"bias": "NEUTRAL", "tf_agreement": "н/д", "detail": []},
        "block2_elliott": {"label": "н/д"},
        "block3_smc": {"label": "н/д"},
        "block4_poi": {"poi": []},
        "block5_checklist": {"score": 0, "items": []},
        "block6_liquidity": {},
        "block7_oi": {"ok": False, "error": "нет данных"},
        "block8_killzone": {"kz": {"active": {}}, "session_note": ""},
        "block9_phase": {"symbol_phase": {}, "btc_phase": {}},
        "block10_meme_risk": {"flagged": False},
        "block11_trade_plan": {"has_setup": False, "reason": "нет данных", "wait_for": "н/д"},
        "block12_rocket": {"score": score, "factors": factors},
        "block13_verdict": {"text": "н/д"},
    }


def test_rocket_score_all_zero_factors_shows_no_adjustments_text():
    """Живой кейс AVAX 15:17: score=50, ВСЕ факторы 0 (direction не определён) --
    карточка обязана честно сказать "поправок нет", не печатать 8 строк "0 ..."."""
    factors = [
        ("EMA-стек 4H: н/д", 0), ("Свип ликвидности (нет/за)", 0),
        ("RSI-дивергенция (нет/за)", 0), ("Elliott: н/д", 0),
        ("Чеклист 0/6", 0), ("Фаза рынка: н/д", 0),
        ("SMC-сетап: н/д", 0), ("OI-матрица: нет данных", 0),
    ]
    result = _minimal_result(50, factors)
    card = fa_engine.render_full_analysis_card(result)
    assert "Rocket Score: 50/100" in card
    assert "поправок нет" in card
    assert "направление не определено" in card
    # ни одной строки нулевой поправки не просочилось
    assert "  0 " not in card
    assert "+0 " not in card


def test_rocket_score_empty_factors_list_also_honest():
    result = _minimal_result(50, [])
    card = fa_engine.render_full_analysis_card(result)
    assert "поправок нет" in card


def test_rocket_score_nonzero_factors_reconcile_with_total():
    """50 (база) + сумма показанных дельт ОБЯЗАНА равняться итоговому score --
    прямая проверка разложения, не просто наличие текста."""
    factors = [
        ("EMA-стек 4H: bullish", 6), ("Свип ликвидности за", 4),
        ("RSI-дивергенция классическая (нет/за)", 0), ("Elliott score_delta: н/д", 0),
        ("Чеклист 4/6", 5), ("SMC-сетап рокет-фактор: BOS", 8),
        ("OI-матрица рокет-фактор: up_up", 6), ("Funding рокет-фактор: н/д", 0),
    ]
    score = 50 + sum(d for _, d in factors)
    result = _minimal_result(score, factors)
    card = fa_engine.render_full_analysis_card(result)
    assert f"Rocket Score: {score}/100" in card
    assert f"база 50, поправки: {sum(d for _, d in factors):+d}" in card
    # только ненулевые факторы показаны
    for label, delta in factors:
        if delta != 0:
            assert label in card
        else:
            assert label not in card


def test_rocket_score_negative_adjustments_reconcile():
    factors = [("Мемкоин/низкая ликвидность", -20), ("SMC-сетап: CHoCH против", -8)]
    score = 50 + sum(d for _, d in factors)
    result = _minimal_result(score, factors)
    card = fa_engine.render_full_analysis_card(result)
    assert f"Rocket Score: {score}/100" in card
    assert "поправки: -28" in card
    assert "-20 Мемкоин/низкая ликвидность" in card
    assert "-8 SMC-сетап: CHoCH против" in card
