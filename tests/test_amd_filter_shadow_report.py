"""
pytest для tools/amd_filter_shadow_report.py -- read-only shadow-валидация
AMD-accumulation-фильтра (владелец, 2026-07-22). Синтетика, без реальных
файлов.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import amd_filter_shadow_report as rpt


def _journal_rec(rid, symbol, direction, ts, outcome, source="TOP_LONG_AUTO", actual_r=None):
    return {"id": rid, "symbol": symbol, "direction": direction, "ts": ts,
            "outcome": outcome, "source": source, "actual_r": actual_r}


def _shadow_rec(symbol, direction, ts, live_journal_id, amd_phase, source=None, type_=None):
    rec = {"symbol": symbol, "direction": direction, "ts": ts,
           "promoted_live": True, "live_journal_id": live_journal_id,
           "amd_phase_methodology": {"phase": amd_phase}}
    if source is not None:
        rec["source"] = source
    if type_ is not None:
        rec["type"] = type_
    return rec


# ── match_and_dedup ──

def test_match_and_dedup_direct_id():
    journal = {1: _journal_rec(1, "BTC", "long", 1000.0, "TP1_HIT")}
    shadow = [_shadow_rec("BTC", "long", 1000.0, live_journal_id=1, amd_phase="accumulation")]
    result = rpt.match_and_dedup(shadow, journal)
    assert list(result.keys()) == [1]


def test_match_and_dedup_ignores_auxiliary_type_records():
    journal = {1: _journal_rec(1, "BTC", "long", 1000.0, "TP1_HIT")}
    shadow = [
        _shadow_rec("BTC", "long", 1000.0, live_journal_id=1, amd_phase="accumulation"),
        {"symbol": "BTC", "direction": "long", "ts": 1001.0, "type": "auto_onchain_shadow",
         "promoted_live": True, "live_journal_id": None},
    ]
    result = rpt.match_and_dedup(shadow, journal)
    assert list(result.keys()) == [1]


def test_match_and_dedup_signal_loop_without_promoted_live_field():
    """Живая находка (2026-07-22): signal_loop-путь НЕ ставит promoted_live
    вообще -- матчинг не должен требовать это поле truthy."""
    journal = {1: _journal_rec(1, "ETH", "short", 2000.0, "SL_HIT", source="signal_loop")}
    shadow = [{"symbol": "ETH", "direction": "short", "ts": 2000.0,
               "live_journal_id": 1, "amd_phase_methodology": {"phase": "manipulation_bear"}}]
    result = rpt.match_and_dedup(shadow, journal)
    assert list(result.keys()) == [1]


def test_match_and_dedup_collapses_full_analysis_signal_loop_duplicate():
    journal = {
        1: _journal_rec(1, "SOL", "long", 3000.0, "SL_HIT", source="signal_loop",
                         actual_r=-1.0),
    }
    journal[1]["entry_lo"] = 100.0
    journal[1]["sl"] = 90.0
    journal[1]["tp1"] = 120.0
    journal[2] = dict(journal[1])
    journal[2]["id"] = 2
    journal[2]["source"] = "full_analysis"
    journal[2]["ts"] = 3000.5

    shadow = [_shadow_rec("SOL", "long", 3000.0, live_journal_id=1, amd_phase="dead_zone")]
    result = rpt.match_and_dedup(shadow, journal)
    # только jid=1 (signal_loop) должен остаться -- у full_analysis (jid=2) нет
    # своей shadow-записи вообще (fa_engine не пишет в shadow_engine), дубль-пара
    # тут не формируется через match_and_dedup напрямую, а через отсутствие
    # отдельного shadow-совпадения для jid=2 -- функция просто не находит
    # для него запись, jid=2 не появится в результате.
    assert list(result.keys()) == [1]


# ── wr_pf / phase_breakdown / build_report ──

def _row(symbol, phase, is_win, actual_r, source="TOP_LONG_AUTO"):
    return {"journal_id": 0, "symbol": symbol, "source": source, "amd_phase": phase,
            "outcome": "TP1_HIT" if is_win else "SL_HIT", "is_win": is_win, "actual_r": actual_r}


def test_wr_pf_basic():
    rows = [_row("A", "accumulation", False, -1.0), _row("B", "manipulation_bull", True, 2.0)]
    stats = rpt.wr_pf(rows)
    assert stats["n"] == 2
    assert stats["wr"] == 50.0
    assert stats["pf"] == 2.0


def test_wr_pf_empty_is_honest_none():
    assert rpt.wr_pf([]) == {"n": 0, "wr": None, "pf": None}


def test_phase_breakdown_groups_correctly():
    rows = [
        _row("A", "accumulation", False, -1.0),
        _row("B", "accumulation", False, -1.0),
        _row("C", "manipulation_bull", True, 2.0),
        _row("D", None, True, 1.5),
    ]
    breakdown = rpt.phase_breakdown(rows)
    assert breakdown["accumulation"]["n"] == 2
    assert breakdown["accumulation"]["wr"] == 0.0
    assert breakdown["manipulation_bull"]["n"] == 1
    assert breakdown["None/н-д"]["n"] == 1


def test_build_report_confusion_and_auto_scope():
    rows = [
        _row("A", "accumulation", False, -1.0, source="TOP_LONG_AUTO"),
        _row("B", "accumulation", False, -1.0, source="signal_loop"),
        _row("C", "manipulation_bull", True, 2.0, source="TOP_LONG_AUTO"),
        _row("D", "manipulation_bull", False, -1.0, source="signal_loop"),
    ]
    report = rpt.build_report(rows, target_phase="accumulation")
    assert report["removed_losses"] == 2
    assert report["removed_wins"] == 0
    assert report["phase_only"]["n"] == 2
    assert report["filtered_all"]["n"] == 2
    # AUTO-only scope -- только A (accumulation) и C (manipulation_bull)
    assert report["auto_baseline"]["n"] == 2
    assert report["auto_phase_only"]["n"] == 1
    assert report["auto_filtered"]["n"] == 1
