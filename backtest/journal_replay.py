"""
Реплей накопленной истории signal_journal -- win-rate/avg-R/max-drawdown в разбивке по
источнику и по рыночному режиму. Только чтение (`signal_journal.get_closed_records()`),
никаких новых источников данных и никакого влияния на сигнальный цикл.

Max drawdown считается на равити-кривой в единицах R: каждая закрытая сделка вносит
`actual_r` в накопленную сумму (условно "рискуем 1 единицу на сделку"), drawdown -- откат
от локального максимума этой кривой. Это упрощение (реальный риск на сделку варьируется
по mem-коин-флагу/DCA, тут не учтено) -- честно указано в отчёте, не выдаётся за точный
$-PnL.
"""
from datetime import datetime

import signal_journal


def _sort_key(rec):
    return rec.get("outcome_ts") or rec.get("ts") or 0


def _day_of_week_ru(rec) -> str:
    """День недели по record["timestamp"] (строка "YYYY-MM-DD HH:MM:SS", TZ бота уже
    учтён при записи -- см. signal_journal.log_signal). "н/д", если поля нет/не парсится."""
    ts = rec.get("timestamp")
    if not ts:
        return "н/д"
    try:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return "н/д"
    days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
    return days[dt.weekday()]


def _session_bucket(rec) -> str:
    """Killzone-сессия по record["timestamp"], ТЕКУЩИЕ (не пропатченные) часы
    bot.get_killzone_status() -- см. patches/01-killzone-hours/ для предложенного
    исправления. Здесь намеренно используются часы, ДЕЙСТВОВАВШИЕ на момент сигнала
    (честная ретроспектива по факту, а не задним числом применённые новые часы)."""
    ts = rec.get("timestamp")
    if not ts:
        return "н/д"
    try:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return "н/д"
    h = dt.hour
    if 1 <= h < 4:
        return "Asia (01-04)"
    if 10 <= h < 12:
        return "London (10-12)"
    if 16 <= h < 18:
        return "NY (16-18)"
    if 18 <= h < 19:
        return "London Close (18-19)"
    if 23 <= h < 24:
        return "NY Close (23-00)"
    return "Dead Zone (вне killzone)"


def max_drawdown_r(records: list) -> float:
    """records -- уже отсортированы хронологически. Возвращает макс. просадку в R
    (положительное число, 0 если равити-кривая монотонно растёт)."""
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in records:
        equity += r["actual_r"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return round(max_dd, 2)


def _metrics_for(records: list) -> dict:
    if not records:
        return {"total": 0, "win_rate": None, "avg_r": None, "max_dd_r": None}
    wins = [r for r in records if r["outcome"] != "SL_HIT"]
    win_rate = round(len(wins) / len(records) * 100, 1)
    avg_r = round(sum(r["actual_r"] for r in records) / len(records), 2)
    records_sorted = sorted(records, key=_sort_key)
    return {
        "total": len(records), "win_rate": win_rate, "avg_r": avg_r,
        "max_dd_r": max_drawdown_r(records_sorted),
    }


def run_backtest() -> dict:
    """Возвращает {"overall", "by_source", "by_regime", "by_direction", "by_grade",
    "by_day_of_week", "by_session"} -- метрики по всей накопленной истории закрытых-с-
    исходом сигналов. Разрезы по direction/grade/day_of_week/session добавлены в ночной
    сессии (см. patches/, BACKTEST_REPORT.md v2) -- по тем же полям, что уже пишутся в
    log_signal(), доп. источников данных не требуют."""
    records = signal_journal.get_closed_records()

    by_source, by_regime, by_direction, by_grade, by_dow, by_session = {}, {}, {}, {}, {}, {}
    for r in records:
        by_source.setdefault(r["source"], []).append(r)
        by_regime.setdefault(signal_journal.regime_label(r), []).append(r)
        by_direction.setdefault(r.get("direction") or "н/д", []).append(r)
        by_grade.setdefault(r.get("grade") or "н/д (не сохранён)", []).append(r)
        by_dow.setdefault(_day_of_week_ru(r), []).append(r)
        by_session.setdefault(_session_bucket(r), []).append(r)

    return {
        "overall": _metrics_for(records),
        "by_source": {k: _metrics_for(v) for k, v in by_source.items()},
        "by_regime": {k: _metrics_for(v) for k, v in by_regime.items()},
        "by_direction": {k: _metrics_for(v) for k, v in by_direction.items()},
        "by_grade": {k: _metrics_for(v) for k, v in by_grade.items()},
        "by_day_of_week": {k: _metrics_for(v) for k, v in by_dow.items()},
        "by_session": {k: _metrics_for(v) for k, v in by_session.items()},
    }


def render_report_md(result: dict) -> str:
    lines = ["# BACKTEST_REPORT.md — реплей накопленной истории signal_journal", ""]
    lines.append(
        "Только чтение уже случившихся исходов (`run_tracker`, реальные срабатывания "
        "TP/SL по live-ценам) — не симуляция на исторических свечах, не прогноз. "
        "Max drawdown — упрощённая R-эквити-кривая (риск=1 единица на сделку "
        "независимо от реального сайзинга), не $-PnL. Сгенерировано автоматически, "
        "не редактировать руками — перегенерировать через `python3 -m backtest.journal_replay`."
    )
    lines.append("")

    o = result["overall"]
    lines.append("## Итого по всей истории")
    if o["total"] == 0:
        lines.append("Нет закрытых сигналов с исходом (`actual_r` не None) — недостаточно "
                      "данных для отчёта. Не выдумываю метрики на пустых данных.")
    else:
        lines.append(f"- Сделок: {o['total']}")
        lines.append(f"- Win rate: {o['win_rate']}%")
        lines.append(f"- Средний R: {o['avg_r']:+.2f}")
        lines.append(f"- Max drawdown: {o['max_dd_r']:.2f}R")
    lines.append("")

    sections = (
        ("По источнику сигнала", "by_source"),
        ("По направлению", "by_direction"),
        ("По рыночному режиму", "by_regime"),
        ("По грейду", "by_grade"),
        ("По дню недели", "by_day_of_week"),
        ("По killzone-сессии (текущие, непропатченные часы)", "by_session"),
    )
    for title, key in sections:
        lines.append(f"## {title}")
        groups = result.get(key, {})
        if not groups:
            lines.append("Нет данных.")
            lines.append("")
            continue
        lines.append("| Группа | Сделок | Win rate | Средний R | Max DD (R) |")
        lines.append("|---|---|---|---|---|")
        for name, m in sorted(groups.items(), key=lambda kv: -kv[1]["total"]):
            lines.append(f"| {name} | {m['total']} | {m['win_rate']}% | "
                          f"{m['avg_r']:+.2f} | {m['max_dd_r']:.2f} |")
        lines.append("")

    # Топ-3 факторов, где методика зарабатывает/теряет -- только по группам с достаточным
    # числом сделок (MIN_GROUP_N), чтобы не строить выводы на 1-2 сделках. Ранжируем по
    # avg_r, не по win_rate (см. expectancy-логика, win_rate без R:R не показателен).
    MIN_GROUP_N = 5
    all_groups = []
    for title, key in sections:
        for name, m in result.get(key, {}).items():
            if m["total"] >= MIN_GROUP_N and m["avg_r"] is not None:
                all_groups.append((f"{title}: {name}", m))
    if all_groups:
        all_groups.sort(key=lambda x: -x[1]["avg_r"])
        lines.append(f"## Топ-3 факторов (только группы с {MIN_GROUP_N}+ сделками)")
        lines.append("")
        lines.append(f"**Где методика зарабатывает больше всего (по среднему R):**")
        for name, m in all_groups[:3]:
            lines.append(f"- {name} — {m['total']} сделок, средний R {m['avg_r']:+.2f}, win rate {m['win_rate']}%")
        lines.append("")
        lines.append(f"**Где методика теряет больше всего (по среднему R):**")
        for name, m in list(reversed(all_groups))[:3]:
            lines.append(f"- {name} — {m['total']} сделок, средний R {m['avg_r']:+.2f}, win rate {m['win_rate']}%")
        lines.append("")
        lines.append(
            f"_Честно: при {MIN_GROUP_N}+ сделках на группу это всё ещё далеко от "
            "статистической значимости (см. ниже) — не ранжирование для решений, "
            "просто самая заметная разница в уже накопленных данных на сегодня._"
        )
        lines.append("")

    lines.append(
        "## Как читать\n\n"
        "Маленькая выборка (см. общий счётчик сделок выше) — выводы не статистически "
        "значимы при малом N (индустриальный ориентир — 30+ для предварительных, 100+ "
        "для надёжных выводов, см. INSIGHTS.md 2026-07-10). По дню недели/сессии данные "
        "покрывают только несколько дней истории (когда были открыты первые позиции) — "
        "не все дни недели/сессии вообще представлены, честно видно по пустым строкам "
        "выше. Грейд не сохранён ни на одной записи (`grade: null` у всех AUTO-сигналов "
        "на сегодня) — разрез технически есть в коде, но реальных данных для него пока "
        "нет. Не использовать эти цифры для изменения торговой логики без накопления "
        "существенно большей истории и отдельного явного решения владельца."
    )
    return "\n".join(lines)


if __name__ == "__main__":
    # Требует init()/загруженного журнала -- запускать в окружении, где signal_journal
    # уже что-то загрузил (напр. локальный signal_journal.json), иначе отчёт будет честно
    # пустым, не с выдуманными данными.
    import os
    if os.path.exists(signal_journal.JOURNAL_FILE):
        signal_journal._load()
    result = run_backtest()
    report = render_report_md(result)
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "BACKTEST_REPORT.md")
    with open(out_path, "w") as f:
        f.write(report)
    print(f"Written {out_path}")
    print(f"Overall: {result['overall']}")
