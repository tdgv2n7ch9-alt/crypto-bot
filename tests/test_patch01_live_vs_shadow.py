"""
pytest для backtest/patch01_live_vs_shadow.py -- «Пакетный ритм» пакет 2, М3.
Чистые функции классификации (никакой сети/файлов, кроме load_signals_since(),
которая тестируется отдельно с tmp_path).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import patch01_live_vs_shadow as p01


def _ts_at(hour, minute):
    """unix ts для 2026-07-11 <hour>:<minute> UTC+3 (совпадает с TZ_OFFSET_HOURS)."""
    from datetime import datetime, timezone, timedelta
    dt = datetime(2026, 7, 11, hour, minute, tzinfo=timezone(timedelta(hours=3)))
    return dt.timestamp()


# ── classify_old() / classify_new() ──

def test_old_hours_asia_narrow_window():
    """Asia -- quality "B", НИКОГДА не "good" (good = A+/A только) ни в одной
    версии часов -- тест проверяет ГРАНИЦУ зоны (matched vs Dead Zone), не good."""
    good, q, name = p01.classify_old(_ts_at(2, 0))  # 02:00 -- внутри старой Asia 01-04
    assert good is False
    assert name == "Asia"
    assert q == "B"


def test_old_hours_asia_outside_narrow_window():
    good, q, name = p01.classify_old(_ts_at(6, 0))  # 06:00 -- вне старой Asia (01-04)
    assert good is False
    assert q == "D"
    assert name is None


def test_new_hours_asia_wide_window():
    """06:00 -- внутри НОВОЙ Asia (00-08), вне старой (01-04) -- зона matched
    (не Dead Zone), но всё ещё НЕ good (Asia quality="B")."""
    good, q, name = p01.classify_new(_ts_at(6, 0))
    assert good is False
    assert name == "Asia"


def test_only_new_good_case():
    """09:30 -- ключевой сценарий патча: НОВЫЙ London Open начинается в 9:00
    (было 10:00) -- good по новым часам, Dead Zone по старым."""
    old_good, _, old_name = p01.classify_old(_ts_at(9, 30))
    new_good, _, new_name = p01.classify_new(_ts_at(9, 30))
    assert old_good is False
    assert old_name is None  # Dead Zone по старым (старый London Open с 10:00)
    assert new_good is True
    assert new_name == "London Open"


def test_both_good_london_open_overlap():
    """11:00 -- внутри London Open по ОБОИМ вариантам часов (10-12 старые, 9-12 новые)."""
    old_good, _, _ = p01.classify_old(_ts_at(11, 0))
    new_good, _, _ = p01.classify_new(_ts_at(11, 0))
    assert old_good is True and new_good is True


def test_only_old_good_ny_open_narrowed():
    """17:00 -- старые часы NY Open 16-18 (good), новые NY Open уже 14-16 (Dead Zone)."""
    old_good, _, _ = p01.classify_old(_ts_at(17, 0))
    new_good, _, _ = p01.classify_new(_ts_at(17, 0))
    assert old_good is True
    assert new_good is False


def test_neither_good_true_dead_zone():
    old_good, _, _ = p01.classify_old(_ts_at(21, 0))
    new_good, _, _ = p01.classify_new(_ts_at(21, 0))
    assert old_good is False and new_good is False


# ── load_signals_since() ──

def test_load_signals_since_filters_by_promotion_ts(tmp_path):
    path = tmp_path / "signals.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "records": {
            "1": {"symbol": "OLD", "ts": 100.0},
            "2": {"symbol": "NEW", "ts": 200.0},
        },
    }))
    result = p01.load_signals_since(promotion_ts=150.0, path=str(path))
    assert [r["symbol"] for r in result] == ["NEW"]


# ── build_report() / render_markdown() ──

def test_build_report_categorizes_correctly():
    signals = [
        {"symbol": "A", "ts": _ts_at(9, 30), "timestamp": "09:30"},  # only_new_good
        {"symbol": "B", "ts": _ts_at(11, 0), "timestamp": "11:00"},  # both_good
        {"symbol": "C", "ts": _ts_at(17, 0), "timestamp": "17:00"},  # only_old_good
        {"symbol": "D", "ts": _ts_at(21, 0), "timestamp": "21:00"},  # neither_good
    ]
    report = p01.build_report(signals, now_ts=p01.PATCH01_PROMOTION_TS + 3600)
    assert report["total_signals"] == 4
    assert report["only_new_good"] == 1
    assert report["both_good"] == 1
    assert report["only_old_good"] == 1
    assert report["neither_good"] == 1


def test_build_report_honestly_flags_partial_window():
    report = p01.build_report([], now_ts=p01.PATCH01_PROMOTION_TS + 3600)  # 1ч, не 24ч
    assert report["window_complete_24h"] is False
    assert report["window_hours"] == 1.0


def test_build_report_flags_complete_window():
    report = p01.build_report([], now_ts=p01.PATCH01_PROMOTION_TS + 25 * 3600)
    assert report["window_complete_24h"] is True


def test_render_markdown_includes_partial_window_warning():
    report = p01.build_report([], now_ts=p01.PATCH01_PROMOTION_TS + 3600)
    md = p01.render_markdown(report)
    assert "НЕПОЛНОЕ" in md


def test_render_markdown_no_crash_on_empty_signals():
    report = p01.build_report([], now_ts=p01.PATCH01_PROMOTION_TS + 3600)
    md = p01.render_markdown(report)
    assert "Сигналов создано за окно: 0" in md
