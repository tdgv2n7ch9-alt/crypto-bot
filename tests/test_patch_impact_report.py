"""backtest/patch_impact_report.py -- на детерминированных фикстурах."""
import backtest.patch_impact_report as pir


def _t(r, tags=None):
    return {"symbol": "A", "direction": "long", "actual_r": r, "start_ms": 0,
            "outcome": "TP1_HIT" if r > 0 else "SL_HIT", "entry": 100, "sl": 90,
            "tp1": 110, "tp2": 120, "tp3": 130, "rr_tp1": 2.0,
            "patch_tags": tags or {}}


def test_compare_overall_basic():
    baseline = [_t(1.0), _t(-1.0)]
    patched = [_t(2.0)]
    result = pir.compare_overall(baseline, patched)
    assert result["baseline"]["total"] == 2
    assert result["patched"]["total"] == 1
    assert result["patched"]["avg_r"] == 2.0


def test_patch_breakdown_separates_tags():
    patched = [
        _t(1.0, {"breaker_mitigation": "breaker"}),
        _t(-1.0, {"breaker_mitigation": "mitigation"}),
        _t(0.5, {}),
        _t(2.0, {"divergence_against": True}),
        _t(1.5, {"bpr_confluence": True}),
    ]
    breakdown = pir.patch_03_04_05_breakdown(patched)
    assert breakdown["03_breaker_mitigation"]["breaker"]["total"] == 1
    assert breakdown["03_breaker_mitigation"]["mitigation"]["total"] == 1
    assert breakdown["04_divergence"]["against_direction"]["total"] == 1
    assert breakdown["05_bpr"]["confluence"]["total"] == 1


def test_render_markdown_no_crash_empty():
    md = pir.render_markdown([], [])
    assert "PATCH_IMPACT.md" in md
    assert "Нет данных" in md


def test_render_markdown_with_data():
    baseline = [_t(1.0), _t(-1.0)]
    patched = [_t(2.0, {"breaker_mitigation": "breaker"})]
    md = pir.render_markdown(baseline, patched)
    assert "Боевая vs патченая" in md
    assert "breaker" in md
