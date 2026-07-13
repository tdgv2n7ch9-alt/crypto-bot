"""
pytest для НОЧЬ#3 Н1 (владелец): tools/night3_shadow_stats.py -- честные
срезы по shadow-контурам. Тесты на синтетических записях (не читают реальный
journal/shadow_signals.json, кроме одного smoke-теста ниже) -- проверяют
арифметику и честные н/д на пустых данных, а не конкретные живые числа
(те меняются каждую ночь).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import night3_shadow_stats as nss


def test_section_tz13_empty_is_honest_na():
    text, n = nss.section_tz13([])
    assert n == 0
    assert "н/д" in text or "нечего" in text


def test_section_tz13_computes_direction_agreement():
    records = [
        {"ts": nss.PACKET14_DEPLOY_TS + 10, "tz13_score": 3, "direction": "long",
         "tz13_direction": "long", "tz13_setup_type": "AMD", "tz13_shadow": {}},
        {"ts": nss.PACKET14_DEPLOY_TS + 20, "tz13_score": 2, "direction": "short",
         "tz13_direction": "long", "tz13_setup_type": "Sweep", "tz13_shadow": {}},
    ]
    text, n = nss.section_tz13(records)
    assert n == 2
    assert "50.0%" in text  # 1 из 2 совпало


def test_section_tz13_excludes_records_before_deploy():
    records = [
        {"ts": nss.PACKET14_DEPLOY_TS - 100, "tz13_score": 3, "direction": "long",
         "tz13_direction": "long"},
    ]
    text, n = nss.section_tz13(records)
    assert n == 0  # запись до деплоя Пакета 14 -- не считается


def test_section_patch05_bpr_empty_is_honest_na():
    text, n = nss.section_patch05_bpr([])
    assert n == 0
    assert "н/д" in text


def test_section_patch05_bpr_counts_confluence():
    records = [
        {"bpr_zone_count": 5, "bpr_confluence": True, "discrepancy": ["bpr: зона входа пересекается"]},
        {"bpr_zone_count": 3, "bpr_confluence": False, "discrepancy": []},
    ]
    text, n = nss.section_patch05_bpr(records)
    assert n == 2
    assert "50.0%" in text


def test_section_patch09_oi_empty_is_honest_na():
    text, n = nss.section_patch09_oi([])
    assert n == 0
    assert "н/д" in text


def test_section_patch09_oi_skips_error_records():
    records = [
        {"oi_funding_ls_shadow": {"total_delta": 5}},
        {"oi_funding_ls_shadow": {"error": "network fail"}},
    ]
    text, n = nss.section_patch09_oi(records)
    assert n == 1


def test_section_ema_stack_empty_is_honest_na_with_structural_reason():
    text, n = nss.section_ema_stack([])
    assert n == 0
    assert "н/д" in text
    assert "_confirm_pump_reversal" in text


def test_section_ema_stack_counts_records_in_window():
    records = [
        {"type": "ema_stack_shadow", "ts": nss.EMA_FIX_TS + 100, "symbol": "BTC",
         "pro_score_old": 50, "pro_score_new": 60, "direction_old": "long",
         "direction_new": "long", "diverges": False},
        {"type": "ema_stack_shadow", "ts": nss.EMA_FIX_TS - 100, "symbol": "ETH"},  # до починки -- не считается
    ]
    text, n = nss.section_ema_stack(records)
    assert n == 1
    assert "BTC" in text
    assert "ETH" not in text


def test_thresholds_reflected_honestly():
    """n < порога -- явный текст "НЕ достигнут", не молчание об этом."""
    text, n = nss.section_patch05_bpr([{"bpr_zone_count": 1}])
    assert "НЕ достигнут" in text


def test_smoke_against_real_shadow_file_does_not_crash():
    """Живая сверка: реальный journal/shadow_signals.json (если существует
    локально) не ломает ни одну секцию -- структурная регрессия, не про
    конкретные числа (те меняются каждую ночь)."""
    if not os.path.exists(nss.SHADOW_PATH):
        return
    records = nss.load_records()
    for fn in (nss.section_tz13, nss.section_patch05_bpr,
               nss.section_patch09_oi, nss.section_ema_stack):
        text, n = fn(records)
        assert isinstance(text, str) and len(text) > 0
        assert isinstance(n, int) and n >= 0
