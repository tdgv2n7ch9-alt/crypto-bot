"""
pytest для tools/mfe_mae_ab_report.py -- инструмент (владелец, 2026-07-22,
ночная очередь) готов к запуску, когда наберётся n>=15 закрытых убытков с
mfe_price/mae_price. Чистые функции на синтетике, без реального journal-файла.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import mfe_mae_ab_report as rpt


def _loss_rec(symbol="BTC", direction="long", entry=100.0, sl=90.0,
              mfe_price=None, mae_price=None, outcome="SL_HIT"):
    return {"symbol": symbol, "direction": direction, "outcome": outcome,
            "entered_price": entry, "sl": sl, "mfe_price": mfe_price, "mae_price": mae_price}


# ── classify ──

def test_classify_a_at_and_above_threshold():
    assert rpt.classify(1.0) == "A"
    assert rpt.classify(1.5) == "A"


def test_classify_b_below_threshold():
    assert rpt.classify(0.29) == "B"
    assert rpt.classify(-0.5) == "B"


def test_classify_middle_between_thresholds():
    assert rpt.classify(0.3) == "middle"
    assert rpt.classify(0.99) == "middle"


def test_classify_none_is_honest_none():
    assert rpt.classify(None) is None


# ── build_ab_rows ──

def test_build_ab_rows_classifies_long_win_scenario_as_a():
    # long, entry=100, sl=90 (risk=10), MFE до 112 -> mfe_r=(112-100)/10=1.2 -> A
    journal = {1: _loss_rec(entry=100.0, sl=90.0, mfe_price=112.0, mae_price=96.0)}
    rows = rpt.build_ab_rows(journal)
    assert len(rows) == 1
    assert rows[0]["classification"] == "A"
    assert rows[0]["mfe_r"] == 1.2


def test_build_ab_rows_classifies_short_as_b():
    # short, entry=100, sl=110 (risk=10), MFE до 98 -> mfe_r=(100-98)/10=0.2 -> B
    journal = {1: _loss_rec(direction="short", entry=100.0, sl=110.0,
                             mfe_price=98.0, mae_price=104.0)}
    rows = rpt.build_ab_rows(journal)
    assert rows[0]["classification"] == "B"
    assert rows[0]["mfe_r"] == 0.2


def test_build_ab_rows_skips_non_sl_hit():
    journal = {1: _loss_rec(mfe_price=112.0, mae_price=96.0, outcome="TP1_HIT")}
    assert rpt.build_ab_rows(journal) == []


def test_build_ab_rows_skips_missing_mfe_mae():
    journal = {1: _loss_rec(mfe_price=None, mae_price=None)}
    assert rpt.build_ab_rows(journal) == []


def test_build_ab_rows_skips_missing_entered_price():
    journal = {1: {"symbol": "BTC", "direction": "long", "outcome": "SL_HIT",
                   "entered_price": None, "sl": 90.0, "mfe_price": 112.0, "mae_price": 96.0}}
    assert rpt.build_ab_rows(journal) == []


def test_build_ab_rows_multiple_trades_preserves_journal_id():
    journal = {
        1: _loss_rec(symbol="AAA", entry=100.0, sl=90.0, mfe_price=112.0, mae_price=96.0),
        2: _loss_rec(symbol="BBB", entry=50.0, sl=45.0, mfe_price=49.0, mae_price=44.0),
    }
    rows = rpt.build_ab_rows(journal)
    ids = {r["journal_id"] for r in rows}
    assert ids == {1, 2}


# ── build_report / format_report ──

def test_report_not_ready_below_min_losses():
    journal = {1: _loss_rec(mfe_price=112.0, mae_price=96.0)}
    report = rpt.build_report(journal, min_losses=15)
    assert report["ready"] is False
    assert report["n"] == 1
    text = rpt.format_report(report)
    assert "1/15" in text
    assert "Недостаточно данных" in text


def test_report_ready_at_min_losses_threshold():
    journal = {i: _loss_rec(symbol=f"S{i}", entry=100.0, sl=90.0, mfe_price=112.0, mae_price=96.0)
               for i in range(15)}
    report = rpt.build_report(journal, min_losses=15)
    assert report["ready"] is True
    assert report["n"] == 15
    assert report["a_count"] == 15  # все mfe_r=1.2 -> A
    text = rpt.format_report(report)
    assert "100.0%" in text
    assert "Только кандидат" in text


def test_report_counts_a_b_middle_correctly():
    journal = {
        # A: mfe_r=1.2
        **{i: _loss_rec(symbol=f"A{i}", entry=100.0, sl=90.0, mfe_price=112.0, mae_price=96.0)
           for i in range(5)},
        # B: mfe_r=0.2
        **{i + 100: _loss_rec(symbol=f"B{i}", entry=100.0, sl=90.0, mfe_price=102.0, mae_price=95.0)
           for i in range(6)},
        # middle: mfe_r=0.5
        **{i + 200: _loss_rec(symbol=f"M{i}", entry=100.0, sl=90.0, mfe_price=105.0, mae_price=95.0)
           for i in range(4)},
    }
    report = rpt.build_report(journal, min_losses=15)
    assert report["n"] == 15
    assert report["ready"] is True
    assert report["a_count"] == 5
    assert report["b_count"] == 6
    assert report["middle_count"] == 4


# ── load_journal_records ──

def test_load_journal_records_missing_file_returns_empty(tmp_path):
    path = tmp_path / "signals.json"
    assert rpt.load_journal_records(str(path)) == {}


def test_load_journal_records_corrupt_file_returns_empty(tmp_path):
    path = tmp_path / "signals.json"
    path.write_text("{not valid json")
    assert rpt.load_journal_records(str(path)) == {}


def test_load_journal_records_reads_int_keyed_records(tmp_path):
    path = tmp_path / "signals.json"
    path.write_text('{"records": {"1": {"symbol": "BTC", "outcome": "SL_HIT"}}}')
    records = rpt.load_journal_records(str(path))
    assert records == {1: {"symbol": "BTC", "outcome": "SL_HIT"}}
