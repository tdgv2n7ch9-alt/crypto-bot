"""
backtest/historical_report.py -- ночная сессия #3, Блок A.4. Считает метрики
(win-rate, avg R, expectancy, max DD, profit factor) + разрезы (символ, сессия,
направление, месяц) по результату backtest/run_historical.py
(backtest/data/_historical_trades.json). Только чтение уже посчитанных сделок,
ничего не меняет.

Честно: разрез "грейд" из запроса не включён -- backtest/engine.py в этом прогоне не
захватывал rocket_score/грейд на момент сигнала (только direction/entry/sl/tp1-3/
outcome/actual_r) -- кандидат для следующего прогона движка, не выдумываю данные,
которых нет.
"""
from collections import defaultdict
from datetime import datetime, timezone


def _session_bucket(start_ms: int) -> str:
    """Тот же принцип, что и backtest/journal_replay.py._session_bucket -- killzone-
    сессия по часу UTC+3 (TZ бота), ТЕКУЩИЕ (не пропатченные) часы."""
    dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    h = (dt.hour + 3) % 24  # UTC -> UTC+3 (Стамбул, TZ бота)
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


def _month_bucket(start_ms: int) -> str:
    dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m")


def max_drawdown_r(trades: list) -> float:
    """trades -- уже отсортированы хронологически по start_ms."""
    equity = peak = max_dd = 0.0
    for t in trades:
        equity += t["actual_r"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return round(max_dd, 2)


def _metrics_for(trades: list) -> dict:
    if not trades:
        return {"total": 0, "win_rate": None, "avg_r": None, "max_dd_r": None,
                "expectancy_r": None, "profit_factor": None}
    wins = [t for t in trades if t["actual_r"] > 0]
    losses = [t for t in trades if t["actual_r"] < 0]
    win_rate = round(len(wins) / len(trades) * 100, 1)
    avg_r = round(sum(t["actual_r"] for t in trades) / len(trades), 3)
    ordered = sorted(trades, key=lambda t: t["start_ms"])
    gross_win = sum(t["actual_r"] for t in wins)
    gross_loss = abs(sum(t["actual_r"] for t in losses))
    profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else None
    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = -gross_loss / len(losses) if losses else 0.0
    p = len(wins) / len(trades)
    expectancy = round(p * avg_win + (1 - p) * avg_loss, 3)
    return {
        "total": len(trades), "win_rate": win_rate, "avg_r": avg_r,
        "max_dd_r": max_drawdown_r(ordered), "expectancy_r": expectancy,
        "profit_factor": profit_factor,
    }


def build_report(trades: list, symbols_scanned: list, symbols_skipped: list) -> dict:
    by_symbol, by_session, by_direction, by_month = {}, {}, {}, {}
    for t in trades:
        by_symbol.setdefault(t["symbol"], []).append(t)
        by_session.setdefault(_session_bucket(t["start_ms"]), []).append(t)
        by_direction.setdefault(t["direction"], []).append(t)
        by_month.setdefault(_month_bucket(t["start_ms"]), []).append(t)

    symbol_metrics = {s: _metrics_for(ts) for s, ts in by_symbol.items()}
    symbol_ranked = sorted(
        ((s, m) for s, m in symbol_metrics.items() if m["total"] >= 3),
        key=lambda x: -x[1]["avg_r"])

    return {
        "overall": _metrics_for(trades),
        "by_session": {k: _metrics_for(v) for k, v in by_session.items()},
        "by_direction": {k: _metrics_for(v) for k, v in by_direction.items()},
        "by_month": {k: _metrics_for(v) for k, v in sorted(by_month.items())},
        "top_symbols": symbol_ranked[:10],
        "bottom_symbols": list(reversed(symbol_ranked))[:10],
        "symbols_scanned": len(symbols_scanned),
        "symbols_skipped": symbols_skipped,
        "outcome_breakdown": {
            k: sum(1 for t in trades if t["outcome"] == k)
            for k in ("TP1_HIT", "TP2_HIT", "TP3_HIT", "SL_HIT", "EXPIRED")
        },
    }


def render_markdown(report: dict, period_note: str) -> str:
    lines = ["# HISTORICAL_BACKTEST.md — исторический бэктест fa_engine (ночная сессия #3, Блок A)", ""]
    lines.append(
        "Реплей `fa_engine.build_full_analysis()` **как есть** (не переписан) по "
        "реальным историческим свечам Bybit (`backtest/engine.py`) — не симуляция "
        "правил задним числом, а прогон настоящего кода анализа через "
        "monkeypatch источника данных. Полный список допущений — в докстринге "
        "`backtest/engine.py`, кратко ниже."
    )
    lines.append("")
    lines.append(f"**Период данных**: {period_note}")
    lines.append("")
    lines.append(
        "## Допущения симуляции (честно, полный список)\n\n"
        "1. `coin` (rank/mcap/объём) — заглушки (не историческая капитализация); "
        "%change (1h/24h/7d/30d/90d) — реальные, из исторических свечей.\n"
        "2. funding/OI/L-S ratio/DXY/S&P/VIX — исторически недоступны в этом прогоне, "
        "нейтральные заглушки (могут НЕДООЦЕНИВАТЬ rocket-score относительно живого бота).\n"
        "3. Скан-каденс — каждый закрытый 4H-бар (не непрерывно).\n"
        "4. Одна активная сделка на символ единовременно.\n"
        "5. Исполнение по 1H-свечам, SL приоритетнее TP при совпадении в одной свече "
        "(консервативно).\n"
        "6. Без lookahead: свечи только строго до симулированного момента.\n"
        "7. Макс. удержание сделки — 14 дней, иначе EXPIRED (R=0).\n"
        "8. Разрез \"грейд\" НЕ включён — в этом прогоне engine.py не захватывал "
        "rocket_score/грейд на момент сигнала (кандидат для следующего прогона)."
    )
    lines.append("")

    o = report["overall"]
    lines.append("## Итого по всей истории")
    if o["total"] == 0:
        lines.append("Сделок не найдено — нечего анализировать. Не выдумываю метрики на пустых данных.")
    else:
        lines.append(f"- Сделок: {o['total']}")
        lines.append(f"- Символов просканировано: {report['symbols_scanned']}"
                     + (f" (пропущено {len(report['symbols_skipped'])}: недостаточно данных)"
                        if report['symbols_skipped'] else ""))
        lines.append(f"- Win rate: {o['win_rate']}%")
        lines.append(f"- Средний R: {o['avg_r']:+.3f}")
        lines.append(f"- Expectancy: {o['expectancy_r']:+.3f}R на сделку")
        lines.append(f"- Max drawdown: {o['max_dd_r']:.2f}R")
        lines.append(f"- Profit factor: {o['profit_factor']}")
        ob = report["outcome_breakdown"]
        lines.append(f"- Исходы: TP1={ob['TP1_HIT']} TP2={ob['TP2_HIT']} TP3={ob['TP3_HIT']} "
                     f"SL={ob['SL_HIT']} EXPIRED={ob['EXPIRED']}")
    lines.append("")

    for title, key in (("По killzone-сессии (текущие часы)", "by_session"),
                        ("По направлению", "by_direction"),
                        ("По месяцу", "by_month")):
        lines.append(f"## {title}")
        groups = report.get(key, {})
        if not groups:
            lines.append("Нет данных.")
            lines.append("")
            continue
        lines.append("| Группа | Сделок | Win rate | Средний R | Expectancy | Max DD (R) | PF |")
        lines.append("|---|---|---|---|---|---|---|")
        for name, m in sorted(groups.items(), key=lambda kv: -kv[1]["total"]):
            lines.append(f"| {name} | {m['total']} | {m['win_rate']}% | {m['avg_r']:+.3f} | "
                         f"{m['expectancy_r']:+.3f} | {m['max_dd_r']:.2f} | {m['profit_factor']} |")
        lines.append("")

    lines.append("## Топ-10 символов по среднему R (3+ сделок)")
    lines.append("| Символ | Сделок | Win rate | Средний R |")
    lines.append("|---|---|---|---|")
    for s, m in report["top_symbols"]:
        lines.append(f"| {s} | {m['total']} | {m['win_rate']}% | {m['avg_r']:+.3f} |")
    lines.append("")
    lines.append("## Анти-топ-10 символов по среднему R (3+ сделок)")
    lines.append("| Символ | Сделок | Win rate | Средний R |")
    lines.append("|---|---|---|---|")
    for s, m in report["bottom_symbols"]:
        lines.append(f"| {s} | {m['total']} | {m['win_rate']}% | {m['avg_r']:+.3f} |")
    lines.append("")

    lines.append(
        "## Как читать\n\n"
        "Это РЕПЛЕЙ логики (`fa_engine.build_full_analysis`, а не `real_full_analysis()`, "
        "используемой в AUTO-скане — см. `real_full_analysis_TZ_reconstructed.md`, "
        "раздел \"Важное уточнение\") на исторических данных с честно перечисленными "
        "допущениями выше — не гарантия будущих результатов и не идентичное "
        "воспроизведение боевого AUTO-сигнального пути. Не использовать для решений "
        "об изменении торговой логики без отдельного явного решения владельца."
    )
    return "\n".join(lines)
