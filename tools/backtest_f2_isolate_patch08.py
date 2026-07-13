"""
Пакет 17, Ф2 (владелец, ночной пакет): изоляция Патча 08 (chart_patterns.py --
Bulkowski: флаги/Г-и-П/треугольники) на ТОЙ ЖЕ истории, что Ф1 (100 символов
Bybit, 12 месяцев). Метод -- byte-for-byte тот же, что уже был применён к этому
патчу раньше (backtest/isolate_08.py, Пакет 11 Ф2): паттерн определяется
ПОСТ-ФАКТУМ на 4h-свечах ДО момента входа (без заглядывания вперёд), тег на
уже принятой сделке -- патч НЕ гейтует, была ли сделка открыта (chart_patterns.py
докстринг: "Бой не трогать").

Применяется к AUTO-трейдам из Ф1 (bot.real_full_analysis() + гейты
send_scheduled()), а не к TZ13 -- потому что вопрос "даёт ли паттерн
дискриминацию исхода" операционно важен для БОЕВОГО пути (тот, что реально
шлёт сигналы подписчикам), не для параллельного shadow-движка. Честно указано
в отчёте, не скрыто.

Порог выборки для вывода: n>=30 (владелец, Пакет 17 -- явно другое число, чем
у backtest/isolate_08.py, MIN_SAMPLE_FOR_VERDICT=20 из Пакета 11 -- в ЭТОМ
пакете используется порог, который владелец запросил явно сейчас).
"""
import bisect
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backtest.engine as eng
import backtest.historical_report as hr
import chart_patterns

MIN_SAMPLE_FOR_VERDICT = 30   # владелец, Пакет 17: "если n<30 по паттерну -- недостаточно"


def _tag_chart_patterns(store: eng.HistoricalStore, trade: dict) -> dict:
    """Тот же принцип, что backtest/isolate_08.py::_tag_chart_patterns() -- 4h-
    свечи ДО момента входа (без заглядывания вперёд), чистая информационная
    метка, не влияет на то, была ли сделка открыта."""
    symbol = trade["symbol"]
    c4h_all = store.full_series(symbol, "4h")
    ts_4h = [c["timestamp"] for c in c4h_all]
    idx = bisect.bisect_left(ts_4h, trade["start_ms"])
    candles = c4h_all[max(0, idx - 100):idx]
    tags = {"flag_bull": False, "flag_bear": False,
            "hs_top": False, "hs_bottom": False,
            "triangle_type": None}
    if len(candles) < 10:
        return tags
    try:
        flag_r = chart_patterns.detect_flag(candles)
        tags["flag_bull"] = bool(flag_r.get("bull"))
        tags["flag_bear"] = bool(flag_r.get("bear"))
    except Exception:
        pass
    try:
        hs_r = chart_patterns.detect_head_and_shoulders(candles)
        tags["hs_top"] = bool(hs_r.get("top"))
        tags["hs_bottom"] = bool(hs_r.get("bottom"))
    except Exception:
        pass
    try:
        tri_r = chart_patterns.detect_triangle(candles)
        tags["triangle_type"] = tri_r.get("type")
    except Exception:
        pass
    return tags


def tag_trades(trades: list, data_dir=eng.DATA_DIR) -> list:
    store = eng.HistoricalStore(data_dir)
    for t in trades:
        t["chart_pattern_tags"] = _tag_chart_patterns(store, t)
    return trades


def _any_pattern(tags: dict) -> bool:
    return (tags["flag_bull"] or tags["flag_bear"] or
            tags["hs_top"] or tags["hs_bottom"] or
            tags["triangle_type"] is not None)


def isolate_any_pattern(trades: list) -> dict:
    affected = [t for t in trades if _any_pattern(t["chart_pattern_tags"])]
    not_affected = [t for t in trades if not _any_pattern(t["chart_pattern_tags"])]
    return {"affected": hr._metrics_for(affected), "not_affected": hr._metrics_for(not_affected)}


def isolate_by_type(trades: list) -> dict:
    groups = {
        "flag_bull": [t for t in trades if t["chart_pattern_tags"]["flag_bull"]],
        "flag_bear": [t for t in trades if t["chart_pattern_tags"]["flag_bear"]],
        "hs_top": [t for t in trades if t["chart_pattern_tags"]["hs_top"]],
        "hs_bottom": [t for t in trades if t["chart_pattern_tags"]["hs_bottom"]],
        "triangle_symmetrical": [t for t in trades if t["chart_pattern_tags"]["triangle_type"] == "symmetrical"],
        "triangle_ascending": [t for t in trades if t["chart_pattern_tags"]["triangle_type"] == "ascending"],
        "triangle_descending": [t for t in trades if t["chart_pattern_tags"]["triangle_type"] == "descending"],
    }
    return {name: hr._metrics_for(ts) for name, ts in groups.items()}


def run(trades: list, data_dir=eng.DATA_DIR) -> dict:
    trades = tag_trades(list(trades), data_dir=data_dir)
    return {
        "base_total": len(trades),
        "any_pattern": isolate_any_pattern(trades),
        "by_type": isolate_by_type(trades),
    }


LABELS = {
    "flag_bull": "Флаг (бычий)", "flag_bear": "Флаг (медвежий)",
    "hs_top": "Г-и-П (вершина)", "hs_bottom": "Г-и-П (дно, эвристика-зеркало)",
    "triangle_symmetrical": "Треугольник (симметричный)",
    "triangle_ascending": "Треугольник (восходящий)",
    "triangle_descending": "Треугольник (нисходящий)",
}


def render_markdown(result: dict, engine_label: str = "AUTO") -> str:
    lines = [
        f"## Ф2 -- изоляция Патча 08 (Bulkowski chart_patterns.py) на {engine_label}-сделках",
        "",
        f"База -- {result['base_total']} сделок {engine_label} (та же историческая выборка, "
        "что Ф1: 100 символов Bybit x 12 месяцев). Паттерны определены ПОСТ-ФАКТУМ на "
        "4h-свечах до момента входа (`chart_patterns.py`) -- не влияют на то, была ли "
        "сделка открыта (владелец, Пакет 8 М3: \"Бой не трогать\").",
        "",
    ]
    if result["base_total"] == 0:
        lines.append(f"**{engine_label}-сделок нет в этом прогоне -- изоляция не выполнена, "
                      "нечего анализировать (честно, не выдумываю метрики на пустых данных).**")
        return "\n".join(lines)

    lines += ["### Любой паттерн vs без паттерна", "",
              "| Группа | Сделок | Win rate | Avg R | Expectancy | Max DD (R) | PF |",
              "|---|---|---|---|---|---|---|"]
    for group_name, group_label in (("affected", "есть паттерн"), ("not_affected", "нет паттерна")):
        m = result["any_pattern"][group_name]
        if m["total"]:
            lines.append(f"| {group_label} | {m['total']} | {m['win_rate']}% | "
                          f"{m['avg_r']:+.3f} | {m['expectancy_r']:+.3f} | {m['max_dd_r']:.2f} | {m['profit_factor']} |")
        else:
            lines.append(f"| {group_label} | 0 | — | — | — | — | — |")

    lines += ["", "### По типу паттерна (n>=30 -- обязательное условие для вывода)", "",
              "| Тип | Сделок | Win rate | Avg R | Expectancy | Max DD (R) | PF | Вывод |",
              "|---|---|---|---|---|---|---|---|"]
    for key, label in LABELS.items():
        m = result["by_type"][key]
        if m["total"] == 0:
            lines.append(f"| {label} | 0 | — | — | — | — | — | нет сделок в выборке |")
        elif m["total"] < MIN_SAMPLE_FOR_VERDICT:
            lines.append(f"| {label} | {m['total']} | {m['win_rate']}% | {m['avg_r']:+.3f} | "
                          f"{m['expectancy_r']:+.3f} | {m['max_dd_r']:.2f} | {m['profit_factor']} | "
                          f"**недостаточно (n<{MIN_SAMPLE_FOR_VERDICT})** |")
        else:
            lines.append(f"| {label} | {m['total']} | {m['win_rate']}% | {m['avg_r']:+.3f} | "
                          f"{m['expectancy_r']:+.3f} | {m['max_dd_r']:.2f} | {m['profit_factor']} | "
                          f"n>={MIN_SAMPLE_FOR_VERDICT}, читаемо |")

    lines.append("")
    lines.append(
        "**Как читать**: ПОСТ-ФАКТУМ разбивка уже принятых сделок (патч 08 не гейтует "
        "вход). Группы с n<30 помечены недостаточными -- по ним НЕЛЬЗЯ делать вывод о "
        "дискриминирующей силе паттерна, только честно показать, что данных мало."
    )
    return "\n".join(lines)


if __name__ == "__main__":
    trades_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output", "backtest_cache", "f1_raw_trades.json")
    with open(trades_path) as f:
        f1_data = json.load(f)
    auto_trades = f1_data.get("auto_trades", [])
    result = run(auto_trades)
    out_path = os.path.join(os.path.dirname(trades_path), "f2_isolate_08_result.json")
    with open(out_path, "w") as f:
        json.dump(result, f)
    print(render_markdown(result))
    print(f"\nСохранено: {out_path}")
