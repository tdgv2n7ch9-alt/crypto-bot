"""
tools/amd_filter_shadow_report.py -- владелец, 2026-07-22: shadow-валидация
кандидата "не входить в AMD-фазе accumulation" (гипотеза сошлась из 3
источников: RETRO_WR_DIAGNOSIS.md, LOSS_REVIEW.md, RETRO_METHODOLOGY_
REVIEW.md). READ-ONLY -- считает WR/PF baseline vs "без accumulation-
входов" на ПОЛНОЙ доступной истории закрытых сделок (не только узкой
n=17/n=22 выборке), плюс confusion-разбивку (сколько лоссов/wins фильтр
затрагивает). НИЧЕГО не применяет к живому чек-листу/гейтам -- только
кандидат-вердикт для владельца.

Матчинг shadow->journal: `is_full_shadow_record()` (полная compute_shadow()
запись, не Фаза-B auxiliary) + `match_shadow_to_journal()` + дедуп в ДВА
шага -- (1) `_dedup_by_trade()` (несколько shadow-снимков одной ещё не
закрытой сделки, тот же принцип, что уже применяется в бою), (2)
`full_analysis`/`signal_loop` дубли ОДНОЙ сделки (см. `MFE_MAE_SCOPE.md` --
`fa_engine.build_full_analysis()` самологируется, `signal_loop` логирует
ещё раз тот же план) -- без шага (2) один и тот же реальный исход считался
бы дважды под двумя journal_id.

Запуск: python3 tools/amd_filter_shadow_report.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shadow_engine
import shadow_outcome_analysis as soa

OUTCOME_STATUSES = {"TP1_HIT", "TP2_HIT", "TP3_HIT", "SL_HIT"}
AUTO_SOURCES = ("TOP_LONG_AUTO", "TOP_SHORT_AUTO")
MIN_OUTCOMES_STANDARD = 20  # тот же порог, что min_outcomes=20 везде в проекте


def match_and_dedup(shadow_records: list, journal_records: dict) -> dict:
    """{journal_id: {"shadow": rec, "match": match_result}} -- полностью
    дедуплицированный набор РЕАЛЬНЫХ различных закрытых сделок (любой
    исход, не только SL_HIT). Без фильтра по `promoted_live` -- signal_loop-
    путь (`log_shadow_async()`) никогда не проставляет это поле в самой
    записи (в отличие от `log_send_scheduled_shadow_async()`), фильтр по
    нему исключил бы ВСЕ signal_loop-сделки ложно (живая находка при
    построении этого отчёта, 2026-07-22)."""
    matched = []
    for rec in shadow_records:
        if not soa.is_full_shadow_record(rec):
            continue
        m = soa.match_shadow_to_journal(rec, journal_records)
        if not m["matched"] or m["outcome"] not in OUTCOME_STATUSES:
            continue
        matched.append({"shadow": rec, "match": m})

    deduped = soa._dedup_by_trade(matched)
    by_jid = {m["match"]["journal_id"]: m for m in deduped}

    # Шаг 2: full_analysis/signal_loop -- одна и та же сделка, два journal_id.
    sl_ids = [jid for jid in by_jid if journal_records[jid].get("source") == "signal_loop"]
    fa_ids = [jid for jid in by_jid if journal_records[jid].get("source") == "full_analysis"]
    other_ids = [jid for jid in by_jid
                 if journal_records[jid].get("source") not in ("signal_loop", "full_analysis")]

    paired_fa = set()
    canonical_sl = []
    for jid1 in sl_ids:
        r1 = journal_records[jid1]
        for jid2 in fa_ids:
            if jid2 in paired_fa:
                continue
            r2 = journal_records[jid2]
            if (r1.get("symbol") == r2.get("symbol") and r1.get("direction") == r2.get("direction")
                    and r1.get("entry_lo") == r2.get("entry_lo") and r1.get("sl") == r2.get("sl")
                    and r1.get("tp1") == r2.get("tp1")
                    and abs((r1.get("ts") or 0) - (r2.get("ts") or 0)) < 60):
                paired_fa.add(jid2)
                canonical_sl.append(jid1)
                break
    unpaired_sl = [jid for jid in sl_ids if jid not in canonical_sl]
    unpaired_fa = [jid for jid in fa_ids if jid not in paired_fa]

    final_ids = set(canonical_sl) | set(unpaired_sl) | set(unpaired_fa) | set(other_ids)
    return {jid: by_jid[jid] for jid in final_ids}


def build_rows(journal_records: dict, matched_by_jid: dict) -> list:
    rows = []
    for jid, m in matched_by_jid.items():
        jr = journal_records[jid]
        sh = m["shadow"]
        amd_phase = (sh.get("amd_phase_methodology") or {}).get("phase")
        outcome = jr.get("outcome")
        rows.append({
            "journal_id": jid, "symbol": jr.get("symbol"), "source": jr.get("source"),
            "amd_phase": amd_phase, "outcome": outcome,
            "is_win": outcome != "SL_HIT", "actual_r": jr.get("actual_r"),
        })
    return rows


def wr_pf(rows: list) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0, "wr": None, "pf": None}
    wins = [r["actual_r"] for r in rows if r["is_win"] and r["actual_r"] is not None]
    losses = [r["actual_r"] for r in rows if not r["is_win"] and r["actual_r"] is not None]
    gain, loss = sum(wins), abs(sum(losses))
    pf = round(gain / loss, 2) if loss > 0 else (float("inf") if gain > 0 else 0.0)
    wr = round(sum(1 for r in rows if r["is_win"]) / n * 100, 1)
    return {"n": n, "wr": wr, "pf": pf}


def phase_breakdown(rows: list) -> dict:
    out = {}
    for r in rows:
        key = r["amd_phase"] or "None/н-д"
        out.setdefault(key, []).append(r)
    return {k: wr_pf(v) for k, v in out.items()}


def build_report(rows: list, target_phase: str = "accumulation") -> dict:
    """Основной вердикт-кандидат: baseline vs без target_phase, confusion
    (сколько wins/losses фильтр отрезает), плюс тот же разрез для AUTO-only
    подмножества (та же популяция, что live/patch05_bpr PF в бою)."""
    filtered_out = [r for r in rows if r["amd_phase"] == target_phase]
    kept = [r for r in rows if r["amd_phase"] != target_phase]
    auto_rows = [r for r in rows if r["source"] in AUTO_SOURCES]
    auto_filtered_out = [r for r in auto_rows if r["amd_phase"] == target_phase]
    auto_kept = [r for r in auto_rows if r["amd_phase"] != target_phase]

    return {
        "target_phase": target_phase,
        "baseline_all": wr_pf(rows),
        "phase_only": wr_pf(filtered_out),
        "filtered_all": wr_pf(kept),
        "removed_wins": sum(1 for r in filtered_out if r["is_win"]),
        "removed_losses": sum(1 for r in filtered_out if not r["is_win"]),
        "phase_breakdown": phase_breakdown(rows),
        "auto_baseline": wr_pf(auto_rows),
        "auto_phase_only": wr_pf(auto_filtered_out),
        "auto_filtered": wr_pf(auto_kept),
    }


def main():
    shadow_records = shadow_engine.get_local_records()
    journal_records = soa.load_journal_records_from_disk()
    matched = match_and_dedup(shadow_records, journal_records)
    rows = build_rows(journal_records, matched)
    report = build_report(rows)

    print(f"Всего сопоставленных различных сделок: {len(rows)}")
    print()
    print("=== Разбивка по AMD-фазе ===")
    for phase, stats in sorted(report["phase_breakdown"].items(),
                                key=lambda kv: -kv[1]["n"]):
        print(f"  {phase:20s} n={stats['n']:3d} WR={stats['wr']}% PF={stats['pf']}")
    print()
    print("=== Baseline (все) ===", report["baseline_all"])
    print("=== Только accumulation ===", report["phase_only"])
    print("=== Без accumulation ===", report["filtered_all"])
    print(f"Фильтр отрезал бы: {report['removed_losses']} лоссов, "
          f"{report['removed_wins']} побед")
    print()
    print("=== AUTO-only baseline ===", report["auto_baseline"])
    print("=== AUTO-only accumulation ===", report["auto_phase_only"])
    print("=== AUTO-only без accumulation ===", report["auto_filtered"])


if __name__ == "__main__":
    main()
