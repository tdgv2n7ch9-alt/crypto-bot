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
import signal_journal


def _sort_key(rec):
    return rec.get("outcome_ts") or rec.get("ts") or 0


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
    """Возвращает {"overall": {...}, "by_source": {...}, "by_regime": {...}} -- метрики
    по всей накопленной истории закрытых-с-исходом сигналов."""
    records = signal_journal.get_closed_records()

    by_source = {}
    for r in records:
        by_source.setdefault(r["source"], []).append(r)

    by_regime = {}
    for r in records:
        reg = signal_journal.regime_label(r)
        by_regime.setdefault(reg, []).append(r)

    return {
        "overall": _metrics_for(records),
        "by_source": {k: _metrics_for(v) for k, v in by_source.items()},
        "by_regime": {k: _metrics_for(v) for k, v in by_regime.items()},
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

    for title, key in (("По источнику сигнала", "by_source"), ("По рыночному режиму", "by_regime")):
        lines.append(f"## {title}")
        groups = result[key]
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

    lines.append(
        "## Как читать\n\n"
        "Маленькая выборка (см. общий счётчик сделок выше) — выводы не статистически "
        "значимы при малом N (индустриальный ориентир — 30+ для предварительных, 100+ "
        "для надёжных выводов, см. INSIGHTS.md 2026-07-10). Не использовать эти цифры для "
        "изменения торговой логики без накопления существенно большей истории и "
        "отдельного явного решения владельца."
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
