"""
pytest для Пакет 17, Ф2 -- tools/backtest_f2_isolate_patch08.py (чистые функции
изоляции: _any_pattern/isolate_any_pattern/isolate_by_type -- работают на уже
тегированных сделках, не требуют сети/реальных исторических файлов)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import backtest_f2_isolate_patch08 as f2


def _trade(actual_r, start_ms=0, **tags):
    default_tags = {"flag_bull": False, "flag_bear": False, "hs_top": False,
                     "hs_bottom": False, "triangle_type": None}
    default_tags.update(tags)
    return {"symbol": "TEST", "actual_r": actual_r, "start_ms": start_ms,
            "chart_pattern_tags": default_tags}


def test_any_pattern_true_when_any_tag_set():
    assert f2._any_pattern({"flag_bull": True, "flag_bear": False, "hs_top": False,
                             "hs_bottom": False, "triangle_type": None}) is True
    assert f2._any_pattern({"flag_bull": False, "flag_bear": False, "hs_top": False,
                             "hs_bottom": False, "triangle_type": "symmetrical"}) is True


def test_any_pattern_false_when_nothing_set():
    assert f2._any_pattern({"flag_bull": False, "flag_bear": False, "hs_top": False,
                             "hs_bottom": False, "triangle_type": None}) is False


def test_isolate_any_pattern_splits_correctly():
    trades = [
        _trade(1.0, flag_bull=True),
        _trade(-1.0, flag_bull=True),
        _trade(0.5),   # без паттерна
    ]
    result = f2.isolate_any_pattern(trades)
    assert result["affected"]["total"] == 2
    assert result["not_affected"]["total"] == 1


def test_isolate_by_type_groups_exclusively_by_tag():
    trades = [
        _trade(1.0, flag_bull=True),
        _trade(1.5, hs_top=True),
        _trade(-1.0, triangle_type="ascending"),
        _trade(2.0, triangle_type="ascending"),
    ]
    result = f2.isolate_by_type(trades)
    assert result["flag_bull"]["total"] == 1
    assert result["hs_top"]["total"] == 1
    assert result["triangle_ascending"]["total"] == 2
    assert result["triangle_descending"]["total"] == 0
    assert result["flag_bear"]["total"] == 0


def test_min_sample_for_verdict_is_30_per_owner_pakket17():
    """Владелец, Пакет 17: 'если n<30 по паттерну -- недостаточно' -- явно
    ДРУГОЙ порог, чем backtest/isolate_08.py (Пакет 11, n<20)."""
    assert f2.MIN_SAMPLE_FOR_VERDICT == 30


def test_render_markdown_marks_small_groups_insufficient():
    """n<30 по конкретному типу паттерна -- честная пометка "недостаточно",
    не выдаётся за читаемый вывод (владелец, Пакет 17)."""
    trades = [_trade(1.0, hs_top=True) for _ in range(5)]  # уже тегированы, 5 < 30
    result = {
        "base_total": 5,
        "any_pattern": f2.isolate_any_pattern(trades),
        "by_type": f2.isolate_by_type(trades),
    }
    md = f2.render_markdown(result)
    assert "недостаточно (n<30)" in md
    assert "Г-и-П (вершина) | 5 |" in md


def test_render_markdown_handles_empty_trades():
    result = {"base_total": 0, "any_pattern": {"affected": {"total": 0}, "not_affected": {"total": 0}},
              "by_type": {k: {"total": 0} for k in f2.LABELS}}
    md = f2.render_markdown(result)
    assert "нет в этом прогоне" in md
