"""
backtest/isolate_08.py -- изоляция shadow-патча 08 (Bulkowski chart_patterns.py:
флаги/H&S/треугольники) на истории, тот же метод, что backtest/isolate_03_04_05.py
для патчей 03/04/05 (владелец, Пакет 11 Ф2: "дают ли H&S/флаги/треугольники
дискриминацию исходов, как делали для 03/04/05").

Патч 08 НЕ участвует в открытии сделки (см. chart_patterns.py докстринг: "Бой не
трогать") -- как и 03/04/05, это ПОСТ-ФАКТУМ тег на уже открытой сделке (по 4h-
свечам на момент входа), не гейт. Тегируем уже закэшированный
`_historical_trades.json` (база, 2864 сделки, 100 символов x ~12 месяцев -- та же
база, что isolate_03_04_05.py) через ОТДЕЛЬНУЮ функцию тегирования (не трогает
existing patch_tags 03/04/05, если они уже есть в файле).
"""
import json
import os

import backtest.engine as eng
import backtest.historical_report as hr
import chart_patterns

BASE_TRADES_PATH = os.path.join(eng.DATA_DIR, "_historical_trades.json")
OUT_PATH = os.path.join(eng.DATA_DIR, "_isolated_08_trades.json")


def load_base_trades(path: str = BASE_TRADES_PATH) -> list:
    with open(path) as f:
        data = json.load(f)
    return data["trades"] if isinstance(data, dict) else data


def _tag_chart_patterns(store: eng.HistoricalStore, trade: dict) -> dict:
    """Тот же принцип, что engine_patched._tag_patch_factors() -- 4h-свечи ДО
    момента входа (без заглядывания вперёд), чистая информационная метка."""
    symbol = trade["symbol"]
    c4h_all = store.full_series(symbol, "4h")
    ts_4h = [c["timestamp"] for c in c4h_all]
    import bisect
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


def tag_existing_trades(trades: list, data_dir=eng.DATA_DIR) -> list:
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
    """Разбивка по КОНКРЕТНОМУ типу паттерна -- честно, каждая группа отдельно,
    малые группы не скрываются, а помечаются в render_markdown()."""
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


def run_isolation(base_trades_path: str = BASE_TRADES_PATH, out_path: str = OUT_PATH) -> dict:
    trades = load_base_trades(base_trades_path)
    trades = tag_existing_trades(trades)
    result = {
        "base_total": len(trades),
        "any_pattern": isolate_any_pattern(trades),
        "by_type": isolate_by_type(trades),
    }
    with open(out_path, "w") as f:
        json.dump({"trades": trades, "result": result}, f)
    return result


MIN_SAMPLE_FOR_VERDICT = 20


def render_markdown(result: dict) -> str:
    lines = [
        "## Изоляция патча 08 -- Bulkowski chart_patterns.py (Пакет 11 Ф2)",
        "",
        f"База -- {result['base_total']} сделок, 100 символов x ~12 месяцев (тот же "
        "исторический набор, что isolate_03_04_05.py). Паттерны определены ПОСТ-ФАКТУМ "
        "на 4h-свечах до момента входа (`chart_patterns.py`, не влияют на то, была ли "
        "сделка открыта -- владелец, Пакет 8 М3: 'Бой не трогать').",
        "",
        "### Любой паттерн vs без паттерна",
        "",
        "| Группа | Сделок | Win rate | Avg R | Expectancy | Max DD (R) | PF |",
        "|---|---|---|---|---|---|---|",
    ]
    for group_name, group_label in (("affected", "есть паттерн"), ("not_affected", "нет паттерна")):
        m = result["any_pattern"][group_name]
        if m["total"]:
            lines.append(f"| {group_label} | {m['total']} | {m['win_rate']}% | "
                          f"{m['avg_r']:+.3f} | {m['expectancy_r']:+.3f} | {m['max_dd_r']:.2f} | {m['profit_factor']} |")
        else:
            lines.append(f"| {group_label} | 0 | — | — | — | — | — |")

    lines += ["", "### По типу паттерна", "", "| Тип | Сделок | Win rate | Avg R | Expectancy | Max DD (R) | PF | Вывод |",
              "|---|---|---|---|---|---|---|---|"]
    labels = {
        "flag_bull": "Флаг (бычий)", "flag_bear": "Флаг (медвежий)",
        "hs_top": "Г-и-П (вершина)", "hs_bottom": "Г-и-П (дно, эвристика-зеркало)",
        "triangle_symmetrical": "Треугольник (симметричный)",
        "triangle_ascending": "Треугольник (восходящий)",
        "triangle_descending": "Треугольник (нисходящий)",
    }
    for key, label in labels.items():
        m = result["by_type"][key]
        if m["total"] == 0:
            lines.append(f"| {label} | 0 | — | — | — | — | — | нет сделок в выборке |")
        elif m["total"] < MIN_SAMPLE_FOR_VERDICT:
            lines.append(f"| {label} | {m['total']} | {m['win_rate']}% | {m['avg_r']:+.3f} | "
                          f"{m['expectancy_r']:+.3f} | {m['max_dd_r']:.2f} | {m['profit_factor']} | "
                          f"**неубедительно (n<{MIN_SAMPLE_FOR_VERDICT})** |")
        else:
            lines.append(f"| {label} | {m['total']} | {m['win_rate']}% | {m['avg_r']:+.3f} | "
                          f"{m['expectancy_r']:+.3f} | {m['max_dd_r']:.2f} | {m['profit_factor']} | "
                          f"n>={MIN_SAMPLE_FOR_VERDICT}, читаемо |")

    lines.append("")
    lines.append(
        "**Как читать**: ПОСТ-ФАКТУМ разбивка уже принятых сделок (патч 08 не гейтует "
        "вход). Группы с n<20 помечены неубедительными -- по ним НЕЛЬЗЯ делать вывод о "
        "дискриминирующей силе паттерна, только честно показать, что данных мало. "
        "Решение о переносе в бой -- отдельный шаг, не в рамках этого прогона."
    )
    return "\n".join(lines)


if __name__ == "__main__":
    result = run_isolation()
    print(json.dumps(result, indent=2, default=str))
    print()
    print(render_markdown(result))
