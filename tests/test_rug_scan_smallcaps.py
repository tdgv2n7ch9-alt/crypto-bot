"""
pytest для tools/rug_scan_smallcaps.py (НОЧЬ#3, Н6, владелец) -- покрывает
чистые/детерминированные функции: control_trio_check(), render_markdown().
Сетевые вызовы (CoinGecko через fetch_universe/fetch_real_cg_detail) НЕ
тестируются здесь -- уже покрыты сборкой в backtest_f3_rug_scan.py, эта пара
функций в rug_scan_smallcaps.py их только переиспользует без изменений.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import rug_scan_smallcaps as rss


def _result(symbol, rank, score, reasons=None):
    return {"symbol": symbol, "name": symbol, "rank": rank, "score": score,
            "max_possible_score": 100, "reasons": reasons or [],
            "detectors": {}, "warn": score >= 40, "alert": score >= 60,
            "cg_detail_source": "real"}


# ── control_trio_check() ──

def test_control_trio_finds_first_three_candidates_present(monkeypatch):
    monkeypatch.setattr(rss, "CONTROL_TRIO_CANDIDATES", ["AAVE", "MKR", "SNX", "CRV"])
    results = [_result("AAVE", 180, 0), _result("SNX", 220, 5), _result("CRV", 300, 0)]
    out = rss.control_trio_check(results)
    assert out["count"] == 3
    assert [f["symbol"] for f in out["trio"]] == ["AAVE", "SNX", "CRV"]
    assert out["all_pass"] is True


def test_control_trio_flags_false_positive(monkeypatch):
    monkeypatch.setattr(rss, "CONTROL_TRIO_CANDIDATES", ["AAVE", "MKR", "SNX"])
    results = [_result("AAVE", 180, 45), _result("MKR", 200, 0), _result("SNX", 220, 0)]
    out = rss.control_trio_check(results)
    assert out["all_pass"] is False
    aave = next(f for f in out["trio"] if f["symbol"] == "AAVE")
    assert aave["pass"] is False and aave["warn"] is True


def test_control_trio_honest_when_none_found(monkeypatch):
    monkeypatch.setattr(rss, "CONTROL_TRIO_CANDIDATES", ["AAVE", "MKR"])
    out = rss.control_trio_check([_result("RANDOMCOIN", 300, 0)])
    assert out["count"] == 0
    assert out["trio"] == []
    assert out["all_pass"] is None


def test_control_trio_stops_at_three_even_with_more_candidates(monkeypatch):
    monkeypatch.setattr(rss, "CONTROL_TRIO_CANDIDATES", ["A", "B", "C", "D", "E"])
    results = [_result(s, 200 + i, 0) for i, s in enumerate(["A", "B", "C", "D", "E"])]
    out = rss.control_trio_check(results)
    assert out["count"] == 3
    assert [f["symbol"] for f in out["trio"]] == ["A", "B", "C"]


# ── render_markdown() ──

def _base_data(results):
    return {"results": results, "universe_count": len(results), "full_universe_count": 500,
            "rank_lo": 150, "rank_hi": 500, "real_detail_count": 1, "detail_calls_used": 1}


def test_render_markdown_includes_warn_and_yellow_sections(monkeypatch):
    monkeypatch.setattr(rss, "CONTROL_TRIO_CANDIDATES", ["AAVE"])
    results = [
        _result("AAVE", 180, 0),
        _result("SHADYCOIN", 210, 45, reasons=["fdv_mcap высокий"]),
        _result("MIDCOIN", 250, 32, reasons=["vertical_growth"]),
    ]
    md = rss.render_markdown(_base_data(results))
    assert "SHADYCOIN" in md.split("Score >= 40")[1].split("Жёлтая зона")[0]
    assert "MIDCOIN" in md.split("Жёлтая зона")[1]
    assert "SHADYCOIN" not in md.split("Жёлтая зона")[1]


def test_render_markdown_honest_empty_sections(monkeypatch):
    monkeypatch.setattr(rss, "CONTROL_TRIO_CANDIDATES", ["AAVE"])
    results = [_result("CLEANCOIN", 300, 0)]
    md = rss.render_markdown(_base_data(results))
    assert "нет монет с score >= 40" in md
    assert "нет монет в диапазоне" in md


def test_render_markdown_reports_control_trio_not_found(monkeypatch):
    monkeypatch.setattr(rss, "CONTROL_TRIO_CANDIDATES", ["NOPE1", "NOPE2"])
    md = rss.render_markdown(_base_data([_result("SOMECOIN", 300, 0)]))
    assert "проверка не выполнена" in md


def test_render_markdown_reports_partial_control_trio(monkeypatch):
    monkeypatch.setattr(rss, "CONTROL_TRIO_CANDIDATES", ["AAVE", "MKR", "SNX"])
    md = rss.render_markdown(_base_data([_result("AAVE", 180, 0)]))
    assert "найдено только 1/3" in md
