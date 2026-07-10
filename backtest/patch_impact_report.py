"""
backtest/patch_impact_report.py -- ночная сессия #3, Блок B. Сравнивает baseline
(backtest/data/_historical_trades.json) с patched (backtest/data/_patched_trades.json,
патчи 01 killzone-hours + 02 RR-gate 2.0 как реальный гейт) + разбивку по
информационным тегам патчей 03/04/05 на самих patched-сделках.
"""
import backtest.historical_report as hr


def compare_overall(baseline_trades: list, patched_trades: list) -> dict:
    return {
        "baseline": hr._metrics_for(baseline_trades),
        "patched": hr._metrics_for(patched_trades),
    }


def patch_03_04_05_breakdown(patched_trades: list) -> dict:
    """Разбивка patched-сделок по тегам патчей 03 (breaker/mitigation), 04
    (дивергенция против направления), 05 (BPR-confluence) -- сравнивает средний R
    сделок С тегом против БЕЗ тега, внутри patched-набора."""
    out = {}

    breaker = [t for t in patched_trades if t.get("patch_tags", {}).get("breaker_mitigation") == "breaker"]
    mitigation = [t for t in patched_trades if t.get("patch_tags", {}).get("breaker_mitigation") == "mitigation"]
    neither_bm = [t for t in patched_trades if not t.get("patch_tags", {}).get("breaker_mitigation")]
    out["03_breaker_mitigation"] = {
        "breaker": hr._metrics_for(breaker),
        "mitigation": hr._metrics_for(mitigation),
        "neither": hr._metrics_for(neither_bm),
    }

    div_against = [t for t in patched_trades if t.get("patch_tags", {}).get("divergence_against")]
    div_clean = [t for t in patched_trades if not t.get("patch_tags", {}).get("divergence_against")]
    out["04_divergence"] = {
        "against_direction": hr._metrics_for(div_against),
        "clean": hr._metrics_for(div_clean),
    }

    bpr_yes = [t for t in patched_trades if t.get("patch_tags", {}).get("bpr_confluence")]
    bpr_no = [t for t in patched_trades if not t.get("patch_tags", {}).get("bpr_confluence")]
    out["05_bpr"] = {
        "confluence": hr._metrics_for(bpr_yes),
        "no_confluence": hr._metrics_for(bpr_no),
    }
    return out


def render_markdown(baseline_trades: list, patched_trades: list) -> str:
    overall = compare_overall(baseline_trades, patched_trades)
    tags = patch_03_04_05_breakdown(patched_trades)
    b, p = overall["baseline"], overall["patched"]

    lines = ["# PATCH_IMPACT.md — влияние 5 теневых патчей на исторический бэктест "
             "(ночная сессия #3, Блок B)", ""]
    lines.append(
        "Тот же движок (`backtest/engine.py`), тот же набор из 100 символов x ~12 "
        "месяцев, что и `HISTORICAL_BACKTEST.md` — единственное отличие: "
        "`backtest/engine_patched.py` включает патчи 01 (killzone-hours shadow) + "
        "02 (R:R-гейт 2.0 вместо 1.5) КАК РЕАЛЬНЫЙ ГЕЙТ (влияет, какие сделки вообще "
        "открываются), патчи 03/04/05 (breaker/mitigation, RSI-дивергенция, BPR) — "
        "как информационные теги на уже открытых patched-сделках (не гейтуют, тот же "
        "принцип, что и живой shadow-контур ночи #2)."
    )
    lines.append("")

    lines.append("## Боевая vs патченая (01+02 как гейт) — сводная таблица")
    lines.append("| Метрика | Боевая (baseline) | Патченая (01+02) | Δ |")
    lines.append("|---|---|---|---|")
    if b["total"] and p["total"]:
        lines.append(f"| Сделок | {b['total']} | {p['total']} | {p['total']-b['total']:+d} |")
        lines.append(f"| Win rate | {b['win_rate']}% | {p['win_rate']}% | {p['win_rate']-b['win_rate']:+.1f}pp |")
        lines.append(f"| Средний R | {b['avg_r']:+.3f} | {p['avg_r']:+.3f} | {p['avg_r']-b['avg_r']:+.3f} |")
        lines.append(f"| Expectancy | {b['expectancy_r']:+.3f} | {p['expectancy_r']:+.3f} | {p['expectancy_r']-b['expectancy_r']:+.3f} |")
        lines.append(f"| Max DD (R) | {b['max_dd_r']:.2f} | {p['max_dd_r']:.2f} | {p['max_dd_r']-b['max_dd_r']:+.2f} |")
        lines.append(f"| Profit factor | {b['profit_factor']} | {p['profit_factor']} | — |")
    else:
        lines.append("| Нет данных для сравнения | — | — | — |")
    lines.append("")

    lines.append(
        "### Патч 01 (killzone-hours) + Патч 02 (R:R-гейт 1.5→2.0) — совместный эффект\n\n"
        "**Честно**: оба патча включены ОДНОВРЕМЕННО в этом прогоне (killzone влияет на "
        "пункт 4 чеклиста Блока 5 `fa_engine`, RR-гейт напрямую на `rr_gate_pass`) — "
        "эффект каждого ПО ОТДЕЛЬНОСТИ здесь не разделён (это бы потребовал ещё 2 "
        "прогона — с каждым патчем изолированно — не сделано в рамках бюджета ночи, "
        "честно помечено как candidate для следующей сессии, если нужен более точный "
        "вердикт по каждому из двух отдельно)."
    )
    lines.append("")

    lines.append("## Патч 03 (Breaker vs Mitigation Block) — информационный тег на patched-сделках")
    d3 = tags["03_breaker_mitigation"]
    lines.append("| Тег | Сделок | Win rate | Средний R |")
    lines.append("|---|---|---|---|")
    for name, m in (("breaker", d3["breaker"]), ("mitigation", d3["mitigation"]), ("ни один (нет данных/не задето)", d3["neither"])):
        if m["total"]:
            lines.append(f"| {name} | {m['total']} | {m['win_rate']}% | {m['avg_r']:+.3f} |")
        else:
            lines.append(f"| {name} | 0 | — | — |")
    lines.append("")

    lines.append("## Патч 04 (RSI-дивергенция против направления) — информационный тег")
    d4 = tags["04_divergence"]
    lines.append("| Тег | Сделок | Win rate | Средний R |")
    lines.append("|---|---|---|---|")
    for name, m in (("классическая дивергенция ПРОТИВ направления", d4["against_direction"]),
                     ("без дивергенции против направления", d4["clean"])):
        if m["total"]:
            lines.append(f"| {name} | {m['total']} | {m['win_rate']}% | {m['avg_r']:+.3f} |")
        else:
            lines.append(f"| {name} | 0 | — | — |")
    lines.append("")

    lines.append("## Патч 05 (BPR confluence) — информационный тег")
    d5 = tags["05_bpr"]
    lines.append("| Тег | Сделок | Win rate | Средний R |")
    lines.append("|---|---|---|---|")
    for name, m in (("зона входа пересекает свежий BPR", d5["confluence"]),
                     ("без BPR-пересечения", d5["no_confluence"])):
        if m["total"]:
            lines.append(f"| {name} | {m['total']} | {m['win_rate']}% | {m['avg_r']:+.3f} |")
        else:
            lines.append(f"| {name} | 0 | — | — |")
    lines.append("")

    lines.append(
        "## Как читать\n\n"
        "Патчи 03/04/05 здесь — ЧИСТО измерительные теги на уже принятых решениях "
        "движка (не меняют, была ли сделка открыта) — сравнение \"с тегом vs без\" "
        "показывает КОРРЕЛЯЦИЮ, не проверенную причинно-следственную связь, и НЕ "
        "учитывает, что происходит, если бы эти патчи РЕАЛЬНО гейтовали (блокировали) "
        "часть сделок — это отдельный, более сложный эксперимент, не проведён в рамках "
        "бюджета ночи. Малые группы (n<20) — не основание для решения, только "
        "направление для дальнейшего накопления shadow-данных (см. `SHADOW_ANALYSIS.md`, "
        "живой контур ночи #2, который продолжает копить реальные данные)."
    )
    return "\n".join(lines)
