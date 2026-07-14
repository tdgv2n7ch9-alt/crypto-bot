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
import json
import os
import time

MATCH_WINDOW_SEC = 10 * 60  # широкий запас -- оба лога происходят в одном цикле функции
OUTCOME_STATUSES = {"TP1_HIT", "TP2_HIT", "TP3_HIT", "SL_HIT"}

# П-Отчёт исходов (владелец, ночное задание 14->15.07, Пакет 2) -- таблица
# закрытых исходов по контурам в MORNING_BRIEF/08:30-сводку. Тот же путь на
# диске, что shadow_engine.SHADOW_FILE читает для journal/shadow_signals.json --
# GitHub-синхронизированная копия journal/signals.json, безопасная для
# standalone-скриптов (tools/morning_brief.py запускается ОТДЕЛЬНЫМ процессом
# от живого бота -- signal_journal._journal (in-memory) там пуст, если явно не
# грузить с диска; см. tools/morning_brief.py докстринг про "persist-источники,
# не in-memory состояние живого процесса").
JOURNAL_SIGNALS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "journal", "signals.json")

CONTOUR_PRESENCE_FIELDS = {
    "tz13": lambda r: r.get("tz13_score") is not None,
    "patch05_bpr": lambda r: r.get("bpr_zone_count") is not None,
    "patch09_oi": lambda r: (r.get("oi_funding_ls_shadow") is not None
                              and "error" not in (r.get("oi_funding_ls_shadow") or {})),
}
CONTOUR_LABELS = {"live": "Live (все сделки)", "tz13": "tz13",
                   "patch05_bpr": "Патч 05 (BPR)", "patch09_oi": "Патч 09 (OI/funding/L-S)"}


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


def load_journal_records_from_disk(path: str = None) -> dict:
    """Читает journal/signals.json (GitHub-синхронизированная копия,
    безопасная для отдельного от живого бота процесса) напрямую с диска --
    тот же принцип, что daily_metrics.shadow_vs_live_today() для
    shadow_signals.json, НЕ полагается на in-memory signal_journal._journal
    (пуст в отдельном процессе, если не грузить явно). Возвращает {id: record}
    (int-ключи, как ожидает match_shadow_to_journal). Файла нет/битый JSON --
    честно пустой словарь, не исключение."""
    path = path or JOURNAL_SIGNALS_PATH
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {int(k): v for k, v in (data.get("records") or {}).items()}
    except Exception:
        return {}


def _trade_r_multiple(shadow_rec: dict, outcome: str):
    """R-мультипл закрытой сделки по уже сохранённым в shadow-записи entry/sl/
    tpN (никаких новых сетевых вызовов/пересчётов) -- SL_HIT дефинировано как
    ровно -1R (стандартная конвенция), TP1/2/3_HIT -- фактическое отношение
    reward/risk на момент построения сигнала. `entry` берётся серединой
    entry_lo/entry_hi (тот же диапазон входа, что и П-LiqCluster зона). None,
    если данных не хватает (не выдумывает R на неполной записи)."""
    direction = shadow_rec.get("direction")
    entry_lo, entry_hi = shadow_rec.get("entry_lo"), shadow_rec.get("entry_hi")
    sl = shadow_rec.get("sl")
    if entry_lo is None or entry_hi is None or sl is None or direction not in ("long", "short"):
        return None
    entry = (entry_lo + entry_hi) / 2
    risk = abs(entry - sl)
    if risk == 0:
        return None
    if outcome == "SL_HIT":
        return -1.0
    tp = {"TP1_HIT": shadow_rec.get("tp1"), "TP2_HIT": shadow_rec.get("tp2"),
          "TP3_HIT": shadow_rec.get("tp3")}.get(outcome)
    if tp is None:
        return None
    reward = (tp - entry) if direction == "long" else (entry - tp)
    return round(reward / risk, 3)


def _profit_factor(matches: list) -> dict:
    """Profit Factor = валовая прибыль / |валовый убыток| в R-мультиплах.
    Честные крайние случаи: 0 сделок с известным R -> pf=None (не выдумывает);
    0 убыточных, но есть прибыльные -> pf=None + pf_label="∞" (математически
    не определён, но НЕ "0 -- нет прибыли", разные вещи)."""
    r_values = [r for m in matches
                if (r := _trade_r_multiple(m["shadow"], m["match"]["outcome"])) is not None]
    n_r = len(r_values)
    gross_profit = sum(r for r in r_values if r > 0)
    gross_loss = -sum(r for r in r_values if r < 0)
    if n_r == 0:
        return {"n_r": 0, "pf": None, "pf_label": "н/д"}
    if gross_loss == 0:
        pf_label = "∞ (убыточных сделок нет)" if gross_profit > 0 else "н/д"
        return {"n_r": n_r, "pf": None, "pf_label": pf_label}
    pf = round(gross_profit / gross_loss, 2)
    return {"n_r": n_r, "pf": pf, "pf_label": f"{pf:.2f}"}


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


def closed_outcomes_by_contour(shadow_records: list, journal_records: dict,
                                min_outcomes: int = 20) -> dict:
    """П-Отчёт исходов (владелец, ночное задание 14->15.07, Пакет 2) -- таблица
    закрытых исходов по контурам (tz13/П05/П09 + live), для MORNING_BRIEF и
    08:30-сводки: закрыто всего/WR%/PF/сколько до min_outcomes=20.

    Определение "по контуру" (честно, не самоочевидно): базовая выборка --
    ВСЕ promoted_live=True shadow-записи, сопоставленные с журнальной записью
    с известным терминальным исходом (та же выборка, что
    build_live_vs_shadow_comparison() строит для "live_all"). Контурные строки
    (tz13/П05/П09) -- ПОДМНОЖЕСТВО этой же выборки, отфильтрованное по
    присутствию соответствующего поля в shadow-записи (тот же признак, что
    shadow_engine.contour_readiness_summary() использует для n) -- отвечает на
    вопрос "как отработали РЕАЛЬНО ОТПРАВЛЕННЫЕ сделки, для которых этот
    контур тоже был посчитан", НЕ "как отработал бы контур, если бы решал
    сам" (контуры tz13/П05/П09 сейчас НЕ влияют на promoted/gate, честно не
    выдаём их как самостоятельный результат)."""
    matched = []
    for rec in shadow_records:
        if not rec.get("promoted_live"):
            continue
        m = match_shadow_to_journal(rec, journal_records)
        if not m["matched"] or m["outcome"] not in OUTCOME_STATUSES:
            continue
        matched.append({"shadow": rec, "match": m})

    def _row(subset):
        wr = _win_rate(subset)
        pf = _profit_factor(subset)
        n = len(subset)
        return {"closed": n, "win_rate_pct": wr["win_rate_pct"],
                "pf": pf["pf"], "pf_label": pf["pf_label"],
                "ready": n >= min_outcomes, "remaining": max(0, min_outcomes - n)}

    result = {"min_outcomes": min_outcomes, "live": _row(matched)}
    for key, pred in CONTOUR_PRESENCE_FIELDS.items():
        result[key] = _row([m for m in matched if pred(m["shadow"])])
    return result


def closed_outcomes_report(min_outcomes: int = 20, shadow_records: list = None,
                            journal_records: dict = None) -> dict:
    """Обёртка над closed_outcomes_by_contour(), сама грузит данные с диска --
    ЕДИНАЯ точка входа для вызывающих (tools/morning_brief.py и
    morning_metrics.py оба зовут ЭТУ функцию, не дублируют wiring). `records`-
    параметры -- для тестов на синтетике, без реальных файлов на диске."""
    import shadow_engine
    if shadow_records is None:
        shadow_records = shadow_engine.get_local_records()
    if journal_records is None:
        journal_records = load_journal_records_from_disk()
    return closed_outcomes_by_contour(shadow_records, journal_records, min_outcomes=min_outcomes)


def format_closed_outcomes_lines(report: dict) -> list:
    """Строки таблицы для Markdown/Telegram (используется и MORNING_BRIEF.md,
    и 08:30 Telegram-сводкой -- один форматтер, два потребителя)."""
    lines = ["| Контур | Закрыто | WR% | PF | До гейта (min 20) |",
             "|---|---|---|---|---|"]
    for key in ("live", "tz13", "patch05_bpr", "patch09_oi"):
        row = report[key]
        label = CONTOUR_LABELS[key]
        wr = f"{row['win_rate_pct']}%" if row["win_rate_pct"] is not None else "н/д"
        gate = "готово" if row["ready"] else f"осталось {row['remaining']}"
        lines.append(f"| {label} | {row['closed']} | {wr} | {row['pf_label']} | {gate} |")
    return lines
