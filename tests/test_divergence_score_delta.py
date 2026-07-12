"""
pytest для ta_extra.divergence_score_delta() -- Пакет 9 (владелец: "Патч 04
(RSI-дивергенция против) -- В БОЙ как ШТРАФ СКОРИНГА (не жёсткий фильтр):
минус к скорингу кандидата при дивергенции против направления, величину штрафа
взять из shadow-конфига. Порог гейта НЕ трогать"). См. PATCH_IMPACT.md,
раздел "РЕШЕНИЕ ВЛАДЕЛЬЦА по 03/04/05" для честной оговорки про величину
штрафа (в shadow-конфиге такого числа не было, подобрано заново по данным
изоляции).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ta_extra


def test_bearish_classical_against_long_gives_penalty():
    divergence = {"bearish_classical": True, "bearish_hidden": False,
                  "bullish_classical": False, "bullish_hidden": False}
    assert ta_extra.divergence_score_delta(divergence, "long") == -ta_extra.RSI_DIVERGENCE_AGAINST_PENALTY


def test_bullish_classical_against_short_gives_penalty():
    divergence = {"bearish_classical": False, "bearish_hidden": False,
                  "bullish_classical": True, "bullish_hidden": False}
    assert ta_extra.divergence_score_delta(divergence, "short") == -ta_extra.RSI_DIVERGENCE_AGAINST_PENALTY


def test_bearish_classical_does_not_penalize_short():
    """Дивергенция ЗА направление (медвежья классическая при шорте) -- не штраф."""
    divergence = {"bearish_classical": True, "bearish_hidden": False,
                  "bullish_classical": False, "bullish_hidden": False}
    assert ta_extra.divergence_score_delta(divergence, "short") == 0


def test_bullish_classical_does_not_penalize_long():
    divergence = {"bearish_classical": False, "bearish_hidden": False,
                  "bullish_classical": True, "bullish_hidden": False}
    assert ta_extra.divergence_score_delta(divergence, "long") == 0


def test_hidden_divergence_never_penalizes():
    """Скрытая дивергенция трактуется как подтверждение тренда (не контрарианская),
    штраф даёт только КЛАССИЧЕСКАЯ -- см. docstring detect_price_indicator_divergence()."""
    divergence = {"bearish_classical": False, "bearish_hidden": True,
                  "bullish_classical": False, "bullish_hidden": True}
    assert ta_extra.divergence_score_delta(divergence, "long") == 0
    assert ta_extra.divergence_score_delta(divergence, "short") == 0


def test_no_divergence_no_penalty():
    divergence = {"bearish_classical": False, "bearish_hidden": False,
                  "bullish_classical": False, "bullish_hidden": False}
    assert ta_extra.divergence_score_delta(divergence, "long") == 0
    assert ta_extra.divergence_score_delta(divergence, "short") == 0


def test_empty_or_none_divergence_no_penalty():
    assert ta_extra.divergence_score_delta({}, "long") == 0
    assert ta_extra.divergence_score_delta(None, "long") == 0


def test_penalty_constant_is_positive_int():
    """Константа хранится как положительное число (штраф вычитается в самой функции),
    легко перенастраиваемая точка (см. PATCH_IMPACT.md)."""
    assert isinstance(ta_extra.RSI_DIVERGENCE_AGAINST_PENALTY, int)
    assert ta_extra.RSI_DIVERGENCE_AGAINST_PENALTY > 0
