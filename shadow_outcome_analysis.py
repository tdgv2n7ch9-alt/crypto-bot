"""
shadow_outcome_analysis.py -- Пакет 7 М2 (владелец "ДА"): связка shadow-записей
(journal/shadow_signals.json) с фактическими исходами реально отправленных сигналов
(journal/signals.json), для честного сравнения win-rate live vs shadow-гейтов.

Аддитивно и НЕ мутирует ни один из двух файлов -- ни shadow-записи (они
иммутабельны по дизайну, см. докстринг shadow_engine.py), ни signal_journal
(его обновляет только сам signal_journal.py по ходу отработки сделки). Это
analysis-time join: результат пересчитывается заново при каждом вызове, не
сохраняется как "исправленная" история.

Два способа связать shadow-запись с журнальной:
  1. Прямой `live_journal_id` -- для НОВЫХ записей (Пакет 7 М2, `bot.send_scheduled()`
     теперь логирует journal-запись ДО теневого лога для promoted-кандидатов и
     передаёт реальный id напрямую, см. `shadow_engine.log_send_scheduled_shadow_async`).
  2. Задним числом -- по (symbol, direction, окно времени) для записей ДО этого
     исправления, где `live_journal_id` отсутствует. Оба лога (shadow и journal)
     создаются в одном и том же цикле функции с разницей в секунды -- окно ниже
     даёт широкий запас на случай задержек между итерациями цикла.

Сравнивает ТОЛЬКО promoted_live=True записи с известным терминальным исходом
(TP1/TP2/TP3/SL) -- у не-promoted кандидатов реального сигнала не было, сравнивать
нечего (честный пробел уже отмечен в SHADOW_ANALYSIS.md INSIGHTS v2, Пакет 5 М5).
"""
import time

MATCH_WINDOW_SEC = 10 * 60  # широкий запас -- оба лога происходят в одном цикле функции
OUTCOME_STATUSES = {"TP1_HIT", "TP2_HIT", "TP3_HIT", "SL_HIT"}


def match_shadow_to_journal(shadow_record: dict, journal_records: dict) -> dict:
    """Сопоставляет ОДНУ shadow-запись с журнальной. Возвращает
    {"matched": bool, "method": "direct_id"|"time_window"|None,
    "journal_id": int|None, "outcome": str|None}. При неоднозначности (несколько
    журнальных записей того же symbol/direction в окне) берёт БЛИЖАЙШУЮ по
    времени -- не выдумывает совпадение при отсутствии кандидатов в окне."""
    jid = shadow_record.get("live_journal_id")
    if jid is not None and jid in journal_records:
        rec = journal_records[jid]
        return {"matched": True, "method": "direct_id", "journal_id": jid,
                "outcome": rec.get("outcome")}

    symbol = shadow_record.get("symbol")
    direction = shadow_record.get("direction")
    ts = shadow_record.get("ts")
    if symbol is None or direction is None or ts is None:
        return {"matched": False, "method": None, "journal_id": None, "outcome": None}

    best_id, best_dt = None, None
    for rid, rec in journal_records.items():
        if rec.get("symbol") != symbol or rec.get("direction") != direction:
            continue
        rts = rec.get("ts")
        if rts is None:
            continue
        dt = abs(rts - ts)
        if dt <= MATCH_WINDOW_SEC and (best_dt is None or dt < best_dt):
            best_id, best_dt = rid, dt

    if best_id is None:
        return {"matched": False, "method": None, "journal_id": None, "outcome": None}
    return {"matched": True, "method": "time_window", "journal_id": best_id,
            "outcome": journal_records[best_id].get("outcome")}


def _win_rate(matches: list) -> dict:
    n = len(matches)
    wins = sum(1 for m in matches if m["match"]["outcome"] != "SL_HIT")
    return {"n": n, "wins": wins,
            "win_rate_pct": round(wins / n * 100, 1) if n else None}


def build_live_vs_shadow_comparison(shadow_records: list, journal_records: dict,
                                     min_outcomes: int = 20) -> dict:
    """Строит сравнение win-rate: (a) все реально отправленные live-сигналы с
    известным исходом ("live_all"), (b) та же выборка, но ТОЛЬКО те, что
    дополнительно прошли бы более строгий shadow R:R-гейт
    (`shadow_rr_gate_pass=True`, см. patch 02) -- отвечает на вопрос "если бы
    live-гейт был строже, как изменился бы win-rate?".

    `ready=False` + честный `detail`, если исходов меньше `min_outcomes` --
    не выдумывает сравнение на недостаточной выборке (владелец: "показать
    первое сравнение, как только исходов наберётся >=20")."""
    matched = []
    for rec in shadow_records:
        if not rec.get("promoted_live"):
            continue
        m = match_shadow_to_journal(rec, journal_records)
        if not m["matched"] or m["outcome"] not in OUTCOME_STATUSES:
            continue
        matched.append({"shadow": rec, "match": m})

    total = len(matched)
    if total < min_outcomes:
        return {
            "ready": False, "total_matched": total, "min_outcomes": min_outcomes,
            "detail": f"недостаточно исходов для честного сравнения: {total}/{min_outcomes}",
        }

    live_all = _win_rate(matched)
    shadow_subset = [m for m in matched if m["shadow"].get("shadow_rr_gate_pass") is True]
    shadow_stricter = _win_rate(shadow_subset)

    return {
        "ready": True,
        "total_matched": total,
        "live_all": live_all,
        "shadow_stricter_rr_gate": shadow_stricter,
        "match_methods": {
            "direct_id": sum(1 for m in matched if m["match"]["method"] == "direct_id"),
            "time_window": sum(1 for m in matched if m["match"]["method"] == "time_window"),
        },
    }


def format_comparison_report(comparison: dict, now_ts: float = None) -> str:
    """Текст для SHADOW_ANALYSIS.md -- честный отчёт, включая случай
    недостаточных данных (не молчит, не выдумывает число)."""
    now = now_ts if now_ts is not None else time.time()
    header = f"## Live vs shadow win-rate (по фактическим исходам, {time.strftime('%Y-%m-%d %H:%M', time.gmtime(now))} UTC)\n\n"
    if not comparison.get("ready"):
        return (header +
                f"Недостаточно данных: {comparison['total_matched']}/{comparison['min_outcomes']} "
                f"исходов среди promoted-live сигналов с сопоставленной shadow-записью. "
                f"Сравнение появится честно, как только наберётся {comparison['min_outcomes']}.\n")
    la = comparison["live_all"]
    ss = comparison["shadow_stricter_rr_gate"]
    mm = comparison["match_methods"]
    lines = [header]
    lines.append(f"Сопоставлено записей с известным исходом (TP/SL): **{comparison['total_matched']}** "
                 f"(прямая связь: {mm['direct_id']}, по времени задним числом: {mm['time_window']}).\n")
    lines.append(f"- **Все live-сигналы**: {la['n']} сделок, win rate {la['win_rate_pct']}% ({la['wins']}/{la['n']})")
    if ss["n"] > 0:
        lines.append(f"- **Только прошедшие более строгий shadow R:R-гейт (>=2.0)**: {ss['n']} сделок, "
                     f"win rate {ss['win_rate_pct']}% ({ss['wins']}/{ss['n']})")
    else:
        lines.append("- **Только прошедшие shadow R:R-гейт**: 0 сделок в выборке -- "
                     "ни один live-сигнал с исходом пока не прошёл бы более строгий гейт, "
                     "сравнить не с чем на этой выборке.")
    return "\n".join(lines) + "\n"
