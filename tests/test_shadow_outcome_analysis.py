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


# ── П-Отчёт исходов (Пакет 2, ночное задание 14->15.07) ──────────────────────

def _closed_shadow_rec(symbol, direction, ts, outcome, jid, entry_lo=95.0, entry_hi=105.0,
                        sl=90.0, tp1=110.0, tp2=115.0, tp3=120.0, tz13_score=None,
                        bpr_zone_count=None, oi_funding_ls_shadow=None):
    """Shadow-запись + сразу journal-запись с тем же исходом (direct_id match) --
    удобная фикстура для closed_outcomes_by_contour(), которая, в отличие от
    build_live_vs_shadow_comparison(), также использует entry/sl/tpN для R-мультипла."""
    shadow = {"symbol": symbol, "direction": direction, "ts": ts, "promoted_live": True,
              "live_journal_id": jid, "entry_lo": entry_lo, "entry_hi": entry_hi, "sl": sl,
              "tp1": tp1, "tp2": tp2, "tp3": tp3, "tz13_score": tz13_score,
              "bpr_zone_count": bpr_zone_count, "oi_funding_ls_shadow": oi_funding_ls_shadow}
    journal = {jid: {"id": jid, "symbol": symbol, "direction": direction, "ts": ts, "outcome": outcome}}
    return shadow, journal


# ── _trade_r_multiple ──

def test_trade_r_multiple_sl_hit_is_minus_one():
    rec = {"direction": "long", "entry_lo": 95.0, "entry_hi": 105.0, "sl": 90.0}
    assert soa._trade_r_multiple(rec, "SL_HIT") == -1.0


def test_trade_r_multiple_tp1_long():
    # entry=(95+105)/2=100, risk=100-90=10, tp1=110 -> reward=10 -> R=1.0
    rec = {"direction": "long", "entry_lo": 95.0, "entry_hi": 105.0, "sl": 90.0, "tp1": 110.0}
    assert soa._trade_r_multiple(rec, "TP1_HIT") == 1.0


def test_trade_r_multiple_short_direction_mirrors():
    # SHORT: entry=100, risk=|100-110|=10, tp1=90 -> reward=(100-90)=10 -> R=1.0
    rec = {"direction": "short", "entry_lo": 95.0, "entry_hi": 105.0, "sl": 110.0, "tp1": 90.0}
    assert soa._trade_r_multiple(rec, "TP1_HIT") == 1.0


def test_trade_r_multiple_missing_data_returns_none():
    assert soa._trade_r_multiple({"direction": "long"}, "TP1_HIT") is None
    assert soa._trade_r_multiple({"direction": "long", "entry_lo": 1, "entry_hi": 2, "sl": None},
                                  "TP1_HIT") is None


def test_trade_r_multiple_unknown_direction_returns_none():
    rec = {"direction": "sideways", "entry_lo": 95.0, "entry_hi": 105.0, "sl": 90.0}
    assert soa._trade_r_multiple(rec, "SL_HIT") is None


# ── _profit_factor ──

def test_profit_factor_mixed_wins_and_losses():
    matches = [
        {"shadow": {"direction": "long", "entry_lo": 95.0, "entry_hi": 105.0, "sl": 90.0, "tp1": 110.0},
         "match": {"outcome": "TP1_HIT"}},   # R=+1.0
        {"shadow": {"direction": "long", "entry_lo": 95.0, "entry_hi": 105.0, "sl": 90.0},
         "match": {"outcome": "SL_HIT"}},    # R=-1.0
    ]
    result = soa._profit_factor(matches)
    assert result["n_r"] == 2
    assert result["pf"] == 1.0  # gross_profit=1.0 / gross_loss=1.0


def test_profit_factor_no_losses_is_infinite_not_zero():
    matches = [
        {"shadow": {"direction": "long", "entry_lo": 95.0, "entry_hi": 105.0, "sl": 90.0, "tp1": 110.0},
         "match": {"outcome": "TP1_HIT"}},
    ]
    result = soa._profit_factor(matches)
    assert result["pf"] is None
    assert "∞" in result["pf_label"]


def test_profit_factor_empty_is_na():
    result = soa._profit_factor([])
    assert result["pf"] is None
    assert result["pf_label"] == "н/д"


# ── closed_outcomes_by_contour ──

def test_closed_outcomes_by_contour_live_counts_all_matched():
    s1, j1 = _closed_shadow_rec("BTC", "long", 1000.0, "TP1_HIT", 1)
    s2, j2 = _closed_shadow_rec("ETH", "long", 1001.0, "SL_HIT", 2)
    journal = {**j1, **j2}
    report = soa.closed_outcomes_by_contour([s1, s2], journal, min_outcomes=20)
    assert report["live"]["closed"] == 2
    assert report["live"]["win_rate_pct"] == 50.0
    assert report["live"]["ready"] is False
    assert report["live"]["remaining"] == 18


def test_closed_outcomes_by_contour_ready_when_threshold_met():
    shadows, journal = [], {}
    for i in range(20):
        s, j = _closed_shadow_rec(f"SYM{i}", "long", 1000.0 + i, "TP1_HIT", i)
        shadows.append(s)
        journal.update(j)
    report = soa.closed_outcomes_by_contour(shadows, journal, min_outcomes=20)
    assert report["live"]["closed"] == 20
    assert report["live"]["ready"] is True
    assert report["live"]["remaining"] == 0


def test_closed_outcomes_by_contour_filters_by_contour_presence():
    s1, j1 = _closed_shadow_rec("BTC", "long", 1000.0, "TP1_HIT", 1, tz13_score=75)
    s2, j2 = _closed_shadow_rec("ETH", "long", 1001.0, "SL_HIT", 2, tz13_score=None)
    journal = {**j1, **j2}
    report = soa.closed_outcomes_by_contour([s1, s2], journal, min_outcomes=20)
    assert report["live"]["closed"] == 2
    assert report["tz13"]["closed"] == 1  # только запись с tz13_score
    assert report["patch05_bpr"]["closed"] == 0


def test_closed_outcomes_by_contour_non_promoted_excluded():
    s, j = _closed_shadow_rec("BTC", "long", 1000.0, "TP1_HIT", 1)
    s["promoted_live"] = False
    report = soa.closed_outcomes_by_contour([s], j, min_outcomes=20)
    assert report["live"]["closed"] == 0


def test_closed_outcomes_by_contour_oi_shadow_error_excluded():
    s, j = _closed_shadow_rec("BTC", "long", 1000.0, "TP1_HIT", 1,
                               oi_funding_ls_shadow={"error": "network fail"})
    report = soa.closed_outcomes_by_contour([s], j, min_outcomes=20)
    assert report["patch09_oi"]["closed"] == 0  # error-запись не считается присутствием


# ── format_closed_outcomes_lines ──

def test_format_closed_outcomes_lines_has_all_four_rows():
    report = soa.closed_outcomes_by_contour([], {}, min_outcomes=20)
    lines = soa.format_closed_outcomes_lines(report)
    text = "\n".join(lines)
    assert "Live (все сделки)" in text
    assert "tz13" in text
    assert "Патч 05 (BPR)" in text
    assert "Патч 09 (OI/funding/L-S)" in text
    assert "н/д" in text  # WR% при 0 закрытых


# ── load_journal_records_from_disk ──

def test_load_journal_records_from_disk_missing_file_returns_empty(tmp_path):
    result = soa.load_journal_records_from_disk(str(tmp_path / "nope.json"))
    assert result == {}


def test_load_journal_records_from_disk_reads_records(tmp_path):
    import json
    path = tmp_path / "signals.json"
    path.write_text(json.dumps({"schema_version": 1, "next_id": 3,
                                 "records": {"1": {"symbol": "BTC"}, "2": {"symbol": "ETH"}}}))
    result = soa.load_journal_records_from_disk(str(path))
    assert result == {1: {"symbol": "BTC"}, 2: {"symbol": "ETH"}}


def test_load_journal_records_from_disk_corrupt_file_returns_empty(tmp_path):
    path = tmp_path / "signals.json"
    path.write_text("{not valid json")
    result = soa.load_journal_records_from_disk(str(path))
    assert result == {}
