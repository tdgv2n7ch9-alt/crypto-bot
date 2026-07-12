"""
pytest для shadow_outcome_analysis.py -- Пакет 7 М2 (владелец "ДА": связка
shadow-записей с фактическими исходами реальных сигналов). Чистые функции, без
сети/файлового I/O -- journal/shadow records передаются как обычные dict/list.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shadow_outcome_analysis as soa


def _journal_rec(rid, symbol, direction, ts, outcome=None):
    return {"id": rid, "symbol": symbol, "direction": direction, "ts": ts, "outcome": outcome}


def _shadow_rec(symbol, direction, ts, promoted_live=True, live_journal_id=None,
                 shadow_rr_gate_pass=None):
    return {"symbol": symbol, "direction": direction, "ts": ts,
            "promoted_live": promoted_live, "live_journal_id": live_journal_id,
            "shadow_rr_gate_pass": shadow_rr_gate_pass}


# ── match_shadow_to_journal ──

def test_match_direct_id_hit():
    journal = {5: _journal_rec(5, "BTC", "long", 1000.0, outcome="TP1_HIT")}
    shadow = _shadow_rec("BTC", "long", 1000.5, live_journal_id=5)
    result = soa.match_shadow_to_journal(shadow, journal)
    assert result == {"matched": True, "method": "direct_id", "journal_id": 5, "outcome": "TP1_HIT"}


def test_match_direct_id_missing_falls_back_to_time_window():
    journal = {5: _journal_rec(5, "BTC", "long", 1000.0, outcome="TP1_HIT")}
    shadow = _shadow_rec("BTC", "long", 1000.5, live_journal_id=999)  # id не существует
    result = soa.match_shadow_to_journal(shadow, journal)
    # id=999 отсутствует в journal -> должен попробовать time-window и найти id=5 (близко по времени)
    assert result["matched"] is True
    assert result["method"] == "time_window"
    assert result["journal_id"] == 5


def test_match_time_window_picks_closest():
    journal = {
        1: _journal_rec(1, "ETH", "short", 1000.0, outcome="SL_HIT"),
        2: _journal_rec(2, "ETH", "short", 1300.0, outcome="TP2_HIT"),
    }
    shadow = _shadow_rec("ETH", "short", 1290.0)  # ближе к id=2 (300с) чем к id=1 (290с... проверим)
    result = soa.match_shadow_to_journal(shadow, journal)
    # |1290-1000|=290, |1290-1300|=10 -> id=2 ближе
    assert result["journal_id"] == 2
    assert result["outcome"] == "TP2_HIT"


def test_match_no_candidate_outside_window():
    journal = {1: _journal_rec(1, "SOL", "long", 1000.0, outcome="TP1_HIT")}
    shadow = _shadow_rec("SOL", "long", 1000.0 + soa.MATCH_WINDOW_SEC + 100)
    result = soa.match_shadow_to_journal(shadow, journal)
    assert result == {"matched": False, "method": None, "journal_id": None, "outcome": None}


def test_match_wrong_direction_not_matched():
    journal = {1: _journal_rec(1, "SOL", "short", 1000.0, outcome="TP1_HIT")}
    shadow = _shadow_rec("SOL", "long", 1000.0)  # то же время/символ, другое направление
    result = soa.match_shadow_to_journal(shadow, journal)
    assert result["matched"] is False


def test_match_missing_fields_no_crash():
    result = soa.match_shadow_to_journal({}, {1: _journal_rec(1, "BTC", "long", 1000.0)})
    assert result["matched"] is False


# ── build_live_vs_shadow_comparison ──

def test_comparison_not_ready_when_below_min_outcomes():
    journal = {1: _journal_rec(1, "BTC", "long", 1000.0, outcome="TP1_HIT")}
    shadow_records = [_shadow_rec("BTC", "long", 1000.0, live_journal_id=1)]
    result = soa.build_live_vs_shadow_comparison(shadow_records, journal, min_outcomes=20)
    assert result["ready"] is False
    assert result["total_matched"] == 1
    assert "20" in result["detail"]


def test_comparison_ignores_non_promoted_records():
    journal = {1: _journal_rec(1, "BTC", "long", 1000.0, outcome="TP1_HIT")}
    shadow_records = [_shadow_rec("BTC", "long", 1000.0, promoted_live=False, live_journal_id=1)]
    result = soa.build_live_vs_shadow_comparison(shadow_records, journal, min_outcomes=1)
    assert result["ready"] is False
    assert result["total_matched"] == 0


def test_comparison_ignores_pending_outcome():
    journal = {1: _journal_rec(1, "BTC", "long", 1000.0, outcome=None)}  # ещё PENDING
    shadow_records = [_shadow_rec("BTC", "long", 1000.0, live_journal_id=1)]
    result = soa.build_live_vs_shadow_comparison(shadow_records, journal, min_outcomes=1)
    assert result["total_matched"] == 0


def test_comparison_ready_computes_win_rates():
    journal = {}
    shadow_records = []
    # 3 сделки: 2 TP (win), 1 SL (loss); только 2 из них проходят shadow-гейт
    for i, (outcome, gate_pass) in enumerate([("TP1_HIT", True), ("SL_HIT", True), ("TP2_HIT", False)]):
        jid = i + 1
        journal[jid] = _journal_rec(jid, f"SYM{i}", "long", 1000.0 + i, outcome=outcome)
        shadow_records.append(_shadow_rec(f"SYM{i}", "long", 1000.0 + i, live_journal_id=jid,
                                           shadow_rr_gate_pass=gate_pass))
    result = soa.build_live_vs_shadow_comparison(shadow_records, journal, min_outcomes=3)
    assert result["ready"] is True
    assert result["total_matched"] == 3
    assert result["live_all"] == {"n": 3, "wins": 2, "win_rate_pct": 66.7}
    # shadow-подмножество: только id=1(TP1) и id=2(SL) прошли гейт -> 1 win из 2
    assert result["shadow_stricter_rr_gate"] == {"n": 2, "wins": 1, "win_rate_pct": 50.0}


def test_comparison_match_methods_breakdown():
    journal = {1: _journal_rec(1, "BTC", "long", 1000.0, outcome="TP1_HIT"),
               2: _journal_rec(2, "ETH", "short", 2000.0, outcome="SL_HIT")}
    shadow_records = [
        _shadow_rec("BTC", "long", 1000.0, live_journal_id=1),         # direct_id
        _shadow_rec("ETH", "short", 2000.0, live_journal_id=None),     # time_window
    ]
    result = soa.build_live_vs_shadow_comparison(shadow_records, journal, min_outcomes=2)
    assert result["match_methods"] == {"direct_id": 1, "time_window": 1}


# ── format_comparison_report ──

def test_format_report_not_ready():
    comparison = {"ready": False, "total_matched": 3, "min_outcomes": 20,
                  "detail": "недостаточно исходов для честного сравнения: 3/20"}
    text = soa.format_comparison_report(comparison, now_ts=1_700_000_000.0)
    assert "3/20" in text
    assert "%" not in text  # не выдумывает win-rate без данных


def test_format_report_ready_shows_percentages():
    comparison = {
        "ready": True, "total_matched": 25,
        "live_all": {"n": 25, "wins": 15, "win_rate_pct": 60.0},
        "shadow_stricter_rr_gate": {"n": 10, "wins": 7, "win_rate_pct": 70.0},
        "match_methods": {"direct_id": 20, "time_window": 5},
    }
    text = soa.format_comparison_report(comparison, now_ts=1_700_000_000.0)
    assert "60.0%" in text
    assert "70.0%" in text
    assert "25" in text


def test_format_report_ready_zero_shadow_subset_honest():
    comparison = {
        "ready": True, "total_matched": 20,
        "live_all": {"n": 20, "wins": 8, "win_rate_pct": 40.0},
        "shadow_stricter_rr_gate": {"n": 0, "wins": 0, "win_rate_pct": None},
        "match_methods": {"direct_id": 20, "time_window": 0},
    }
    text = soa.format_comparison_report(comparison, now_ts=1_700_000_000.0)
    assert "0 сделок" in text
