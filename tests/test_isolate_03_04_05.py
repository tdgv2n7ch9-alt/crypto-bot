"""
pytest для backtest/isolate_03_04_05.py -- «Пакетный ритм» пакет 2, М4. Чистые
функции (тегирование через engine_patched.tag_existing_trades() замокано --
не тянуть реальные свечи HistoricalStore в юнит-тестах).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import isolate_03_04_05 as iso


def _trade(actual_r, outcome_ts=1000.0, tags=None):
    return {"actual_r": actual_r, "start_ms": outcome_ts, "outcome_ts": outcome_ts,
            "outcome": "TP1_HIT" if actual_r > 0 else "SL_HIT", "patch_tags": tags or {}}


def test_isolate_patch_03_combines_breaker_and_mitigation():
    trades = [
        _trade(1.0, tags={"breaker_mitigation": "breaker"}),
        _trade(0.5, tags={"breaker_mitigation": "mitigation"}),
        _trade(-1.0, tags={"breaker_mitigation": None}),
    ]
    result = iso.isolate_patch_03(trades)
    # breaker И mitigation ВМЕСТЕ -- обе affected-сделки в одной группе, не разделены
    assert result["affected"]["total"] == 2
    assert result["not_affected"]["total"] == 1


def test_isolate_patch_03_empty_string_type_not_falsely_affected():
    """breaker_mitigation=None (или отсутствует) -- честно НЕ affected, не выдумываем тип."""
    trades = [_trade(1.0, tags={}), _trade(-1.0, tags={"breaker_mitigation": None})]
    result = iso.isolate_patch_03(trades)
    assert result["affected"]["total"] == 0
    assert result["not_affected"]["total"] == 2


def test_isolate_patch_04_divergence_against():
    trades = [
        _trade(-1.0, tags={"divergence_against": True}),
        _trade(1.0, tags={"divergence_against": False}),
        _trade(1.0, tags={}),
    ]
    result = iso.isolate_patch_04(trades)
    assert result["affected"]["total"] == 1
    assert result["not_affected"]["total"] == 2


def test_isolate_patch_05_bpr_confluence():
    trades = [
        _trade(1.0, tags={"bpr_confluence": True}),
        _trade(1.0, tags={"bpr_confluence": False}),
    ]
    result = iso.isolate_patch_05(trades)
    assert result["affected"]["total"] == 1
    assert result["not_affected"]["total"] == 1


def test_run_isolation_end_to_end(monkeypatch, tmp_path):
    base_trades = [
        {"actual_r": 1.0, "start_ms": 1000.0, "outcome_ts": 1000.0, "outcome": "TP1_HIT"},
        {"actual_r": -1.0, "start_ms": 2000.0, "outcome_ts": 2000.0, "outcome": "SL_HIT"},
    ]
    base_path = tmp_path / "base.json"
    import json
    base_path.write_text(json.dumps({"trades": base_trades}))
    out_path = tmp_path / "out.json"

    def fake_tag(trades, data_dir=None):
        for t in trades:
            t["patch_tags"] = {"breaker_mitigation": "breaker", "divergence_against": False, "bpr_confluence": False}
        return trades

    monkeypatch.setattr(iso.engp, "tag_existing_trades", fake_tag)
    result = iso.run_isolation(base_trades_path=str(base_path), out_path=str(out_path))
    assert result["base_total"] == 2
    assert result["patch_03"]["affected"]["total"] == 2
    assert out_path.exists()


def test_render_markdown_includes_all_three_patches():
    result = {
        "base_total": 100,
        "patch_03": {"affected": {"total": 10, "win_rate": 50.0, "avg_r": 0.5, "expectancy_r": 0.5, "max_dd_r": 1.0, "profit_factor": 2.0},
                     "not_affected": {"total": 90, "win_rate": 40.0, "avg_r": 0.3, "expectancy_r": 0.3, "max_dd_r": 2.0, "profit_factor": 1.5}},
        "patch_04": {"affected": {"total": 0, "win_rate": None, "avg_r": None, "expectancy_r": None, "max_dd_r": None, "profit_factor": None},
                     "not_affected": {"total": 100, "win_rate": 45.0, "avg_r": 0.4, "expectancy_r": 0.4, "max_dd_r": 1.5, "profit_factor": 1.8}},
        "patch_05": {"affected": {"total": 5, "win_rate": 60.0, "avg_r": 0.8, "expectancy_r": 0.8, "max_dd_r": 0.5, "profit_factor": 3.0},
                     "not_affected": {"total": 95, "win_rate": 44.0, "avg_r": 0.35, "expectancy_r": 0.35, "max_dd_r": 1.8, "profit_factor": 1.7}},
    }
    md = iso.render_markdown(result)
    assert "03 (breaker+mitigation)" in md
    assert "04 (RSI-дивергенция против)" in md
    assert "05 (BPR confluence)" in md
    assert "| 04 (RSI-дивергенция против) | affected | 0 | — | — | — | — | — |" in md
