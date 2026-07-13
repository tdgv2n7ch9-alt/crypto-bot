"""
pytest для НОЧЬ#3 Н4/Н8 (владелец): shadow_engine.contour_readiness_summary()/
ema_stack_readiness_summary() -- компактная таблица готовности по контурам
(n/порог/готово-да-нет-сколько-осталось), переиспользуется утренней сводкой
и MORNING_BRIEF. Синтетические записи (не читает реальный
journal/shadow_signals.json), чтобы тест был детерминирован.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shadow_engine as se


def test_contour_readiness_empty_records():
    result = se.contour_readiness_summary([])
    assert result["tz13"] == {"n": 0, "threshold": 100, "ready": False, "remaining": 100}
    assert result["patch05_bpr"] == {"n": 0, "threshold": 200, "ready": False, "remaining": 200}
    assert result["patch09_oi"] == {"n": 0, "threshold": 100, "ready": False, "remaining": 100}


def test_contour_readiness_counts_only_valid_records():
    records = [
        {"tz13_score": 3, "bpr_zone_count": 5, "oi_funding_ls_shadow": {"total_delta": 1}},
        {"tz13_score": None, "bpr_zone_count": None, "oi_funding_ls_shadow": None},
        {"oi_funding_ls_shadow": {"error": "network fail"}},
    ]
    result = se.contour_readiness_summary(records)
    assert result["tz13"]["n"] == 1
    assert result["patch05_bpr"]["n"] == 1
    assert result["patch09_oi"]["n"] == 1  # error-запись не считается


def test_contour_readiness_marks_ready_at_threshold():
    records = [{"tz13_score": i} for i in range(100)]
    result = se.contour_readiness_summary(records)
    assert result["tz13"]["n"] == 100
    assert result["tz13"]["ready"] is True
    assert result["tz13"]["remaining"] == 0


def test_contour_readiness_below_threshold_remaining_positive():
    records = [{"tz13_score": i} for i in range(30)]
    result = se.contour_readiness_summary(records)
    assert result["tz13"]["ready"] is False
    assert result["tz13"]["remaining"] == 70


def test_ema_stack_readiness_no_records():
    fix_ts = 1000.0
    result = se.ema_stack_readiness_summary([], now_ts=fix_ts + 3600, fix_ts=fix_ts,
                                              window_sec=72 * 3600)
    assert result["n"] == 0
    assert result["ready"] is False
    assert result["elapsed_hours"] == 1.0
    assert result["window_hours"] == 72.0


def test_ema_stack_readiness_counts_records_after_fix_only():
    fix_ts = 1000.0
    records = [
        {"type": "ema_stack_shadow", "ts": fix_ts + 10},
        {"type": "ema_stack_shadow", "ts": fix_ts - 10},  # до починки -- не считается
        {"type": "pump_reversal_shadow", "ts": fix_ts + 20},  # другой тип -- не считается
    ]
    result = se.ema_stack_readiness_summary(records, now_ts=fix_ts + 3600, fix_ts=fix_ts,
                                              window_sec=72 * 3600)
    assert result["n"] == 1


def test_ema_stack_readiness_ready_after_window_closes():
    fix_ts = 1000.0
    window_sec = 72 * 3600
    result = se.ema_stack_readiness_summary([], now_ts=fix_ts + window_sec + 1,
                                              fix_ts=fix_ts, window_sec=window_sec)
    assert result["ready"] is True
    assert result["elapsed_hours"] == 72.0  # ограничено окном, не растёт дальше


def test_ema_stack_readiness_uses_real_default_fix_ts():
    """Живая сверка: дефолтный fix_ts (без явной передачи) не падает и
    возвращает разумные структуры на реальном файле-обёртке."""
    result = se.ema_stack_readiness_summary([])
    assert "n" in result and "ready" in result and "elapsed_hours" in result
