"""
Пакет 17 -- сборка output/BACKTEST_F1_F3_REPORT.md из результатов Ф1 (tz13 vs
AUTO), Ф2 (изоляция Патча 08) и Ф3 (rug-скоринг топ-300). Владелец: "БЕЗ
рекомендаций 'включать/не включать' -- решения мои по цифрам." -- этот
скрипт печатает ТОЛЬКО цифры/допущения/честные н/д, ни одного вывода вида
"стоит перенести в бой".

Запуск ПОСЛЕ Ф1/Ф2/Ф3: python3 tools/backtest_f1_f3_report.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backtest.historical_report as hr

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(REPO_ROOT, "output", "backtest_cache")
REPORT_PATH = os.path.join(REPO_ROOT, "output", "BACKTEST_F1_F3_REPORT.md")

F1_PATH = os.path.join(CACHE_DIR, "f1_raw_trades.json")
F2_PATH = os.path.join(CACHE_DIR, "f2_isolate_08_result.json")
F3_PATH = os.path.join(CACHE_DIR, "f3_rug_scan_raw.json")


def _load(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def _setup_type_distribution(trades: list) -> dict:
    dist = {}
    for t in trades:
        st = t.get("setup_type") or "н/д (не определён)"
        dist[st] = dist.get(st, 0) + 1
    return dist


def render_f1_section(f1_data) -> str:
    lines = ["## Ф1 -- tz13 (ta_extra.build_13block_verdict) vs AUTO (bot.real_full_analysis)", ""]
    if f1_data is None:
        lines.append("**SKIPPED -- файл `f1_raw_trades.json` не найден (прогон не выполнялся "
                      "или не завершился в отведённое время).**")
        return "\n".join(lines)

    lines += [
        "### Допущения симуляции (честно)",
        "",
        "1. `coin` (rank/mcap/объём) -- заглушки, НЕ историческая капитализация; "
        "%change (1h/24h/7d/30d) -- реальные, из исторических Bybit-свечей.",
        "2. funding/OI/L-S ratio исторически недоступны -- AUTO получает нейтральные "
        "заглушки, tz13 получает `None` (Блок 7 OI-матрица tz13 честно \"н/д\" на "
        "ВСЕХ сигналах этого прогона).",
        "3. killzone -- восстановлен на исторический момент времени скана (не текущее "
        "время выполнения скрипта), логика продублирована read-only из "
        "`bot.get_killzone_status()` (bot.py не менялся).",
        "4. Скан-каденс: каждые 6 закрытых 4h-баров (~раз в сутки), не каждый бар.",
        "5. Исполнение -- форвардный проход 1H-свечей, окно **72 часа** (SL приоритетнее "
        "TP при совпадении в одной свече).",
        "6. **Без комиссий и проскальзывания.**",
        "7. Без lookahead: свечи только строго до симулированного момента.",
        "8. Вселенная -- ТЕКУЩИЙ топ-100 Bybit по объёму (не историческая точка-в-"
        "времени вселенная) -- survivorship bias не устранён.",
        "",
        f"Символов запрошено: {len(f1_data.get('symbols_requested', []))}, "
        f"просканировано: {len(f1_data['symbols_scanned'])}, "
        f"пропущено (нет данных): {len(f1_data['symbols_skipped'])}.",
        "",
        f"Кэш свечей: переиспользовано {f1_data.get('cache_stats', {}).get('reused', 'н/д')} пар "
        f"(symbol,interval) из существующего `backtest/data/`, докачано "
        f"{f1_data.get('cache_stats', {}).get('downloaded', 'н/д')}, недоступно "
        f"{f1_data.get('cache_stats', {}).get('missing', 'н/д')}.",
        "",
    ]

    for engine_key, engine_label in (("auto_trades", "AUTO"), ("tz13_trades", "TZ13")):
        trades = f1_data.get(engine_key, [])
        m = hr._metrics_for(trades)
        lines.append(f"### {engine_label}")
        lines.append("")
        if m["total"] == 0:
            lines.append("Сделок не найдено -- нечего анализировать. Не выдумываю метрики "
                          "на пустых данных.")
            lines.append("")
            continue
        lines.append(f"- Сделок: {m['total']}")
        lines.append(f"- Win rate: {m['win_rate']}%")
        lines.append(f"- Средний R: {m['avg_r']:+.3f}")
        lines.append(f"- Profit factor: {m['profit_factor']}")
        lines.append(f"- Expectancy: {m['expectancy_r']:+.3f}R на сделку")
        lines.append(f"- Max drawdown: {m['max_dd_r']:.2f}R")
        outcomes = {k: sum(1 for t in trades if t["outcome"] == k)
                    for k in ("TP1_HIT", "TP2_HIT", "TP3_HIT", "SL_HIT", "EXPIRED")}
        lines.append(f"- Исходы: TP1={outcomes['TP1_HIT']} TP2={outcomes['TP2_HIT']} "
                      f"TP3={outcomes['TP3_HIT']} SL={outcomes['SL_HIT']} EXPIRED={outcomes['EXPIRED']}")
        if engine_key == "tz13_trades":
            dist = _setup_type_distribution(trades)
            lines.append("- Распределение по типу сетапа:")
            for st, n in sorted(dist.items(), key=lambda kv: -kv[1]):
                lines.append(f"  - {st}: {n}")
        lines.append("")

    lines.append(
        "**Честно про асимметрию объёма сигналов**: AUTO применяет полный набор боевых "
        "гейтов (rocket>=60 + грейд A+/A/B + R:R-гейт + counter-trend + RSI-экстремум) -- "
        "TZ13 применяет только чек-лист >=4/6 + R:R-гейт. Разное число сделок между "
        "движками ОЖИДАЕМО из-за разных порогов входа, не является само по себе "
        "признаком превосходства одного над другим -- решение по цифрам за владельцем."
    )
    return "\n".join(lines)


def render_f2_section(f2_data) -> str:
    if f2_data is None:
        return ("## Ф2 -- изоляция Патча 08 (Bulkowski chart_patterns.py) на AUTO-сделках\n\n"
                 "**SKIPPED -- файл `f2_isolate_08_result.json` не найден (прогон не "
                 "выполнялся, либо Ф1 AUTO-выборка была пуста).**")

    import tools.backtest_f2_isolate_patch08 as f2mod
    return f2mod.render_markdown(f2_data, engine_label="AUTO")


def render_f3_section(f3_data) -> str:
    lines = ["## Ф3 -- rug-скоринг топ-300 CoinGecko", ""]
    if f3_data is None:
        lines.append("**SKIPPED -- файл `f3_rug_scan_raw.json` не найден (прогон не "
                      "выполнялся или упал на самом старте, например 429 на bulk-вызове "
                      "вселенной без успешного ретрая).**")
        return "\n".join(lines)

    import tools.backtest_f3_rug_scan as f3mod
    md = f3mod.render_markdown(f3_data)
    # убираем первую строку заголовка (# RUG_WATCHLIST.md ...) -- этот раздел уже
    # внутри общего отчёта со своим заголовком уровня ##
    body_lines = md.split("\n")
    if body_lines and body_lines[0].startswith("# "):
        body_lines = body_lines[2:]
    lines.append("\n".join(body_lines))
    return "\n".join(lines)


def build_report() -> str:
    f1_data = _load(F1_PATH)
    f2_data = _load(F2_PATH)
    f3_data = _load(F3_PATH)

    lines = [
        "# BACKTEST_F1_F3_REPORT.md -- Пакет 17 (ночной пакет, офлайн, bot.py не менялся)",
        "",
        "Владелец: \"Ф1-Ф3 бэктесты, офлайн, bot.py не трогать, деплой не требуется\". "
        "Этот отчёт содержит ТОЛЬКО цифры/допущения/честные н/д -- БЕЗ рекомендаций "
        "\"включать/не включать\" (владелец: \"решения мои по цифрам\").",
        "",
        "---",
        "",
        render_f1_section(f1_data),
        "",
        "---",
        "",
        render_f2_section(f2_data),
        "",
        "---",
        "",
        render_f3_section(f3_data),
        "",
        "---",
        "",
        "## Итоговый статус секций",
        "",
        f"- Ф1 (tz13 vs AUTO): {'ГОТОВО' if f1_data else 'SKIPPED -- файл не найден'}",
        f"- Ф2 (изоляция Патча 08): {'ГОТОВО' if f2_data else 'SKIPPED -- файл не найден'}",
        f"- Ф3 (rug-скоринг топ-300): {'ГОТОВО' if f3_data else 'SKIPPED -- файл не найден'}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    report = build_report()
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        f.write(report)
    print(f"Отчёт сохранён: {REPORT_PATH}")
    print(f"Длина: {len(report)} символов")
