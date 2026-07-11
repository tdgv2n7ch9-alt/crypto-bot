"""
backtest/isolate_03_04_05.py -- «Пакетный ритм», пакет 2, М4. Изоляция патчей
03 (breaker/mitigation), 04 (RSI-дивергенция против направления), 05 (BPR
confluence) -- по прямому запросу владельца, СТРОГО КАК ОНИ ОПРЕДЕЛЕНЫ в
shadow_engine.py (см. shadow_engine.py:239-281), без изобретения новых правил
гейта. В частности патч 03 -- breaker И mitigation ВМЕСТЕ, одна объединённая
метка "affected" (совпадает с тем, как shadow_engine.py решает, попадает ли
"03-breaker-mitigation" в список affected -- `breaker.get("type")` truthy,
неважно, "breaker" или "mitigation").

Технически: НЕ три отдельных прогона движка. Патчи 03/04/05 не участвуют в
принятии решения "открыть сделку" (в отличие от 01/02, которые меняют чек-лист/
R:R-гейт ДО открытия) -- они считаются ПОСЛЕ, на уже открытой сделке, по
свечам на момент входа. Бэктест детерминирован (нет RNG, тот же исторический
период) -- три прогона базового (без 01/02) сценария дали бы БАЙТ-В-БАЙТ
идентичный список сделок трижды. Вместо трёх бесполезных повторных прогонов:
ОДИН раз тегируем уже закэшированный `_historical_trades.json` (база, 2864
сделки, тот же файл, что дал числа "База (без патчей)" в PATCH_IMPACT.md) --
`engine_patched.tag_existing_trades()` -- и строим ТРИ отдельные изолированные
таблицы (по одной на патч), каждая: "affected" (патч бы сработал) vs "не
affected" -- честно посчитанные на ЧИСТОЙ базе, не смешанные с 01/02 (та
проблема уже была явно отмечена как ограничение в исходном PATCH_IMPACT.md
"Как читать").
"""
import json
import os

import backtest.engine as eng
import backtest.engine_patched as engp
import backtest.historical_report as hr

BASE_TRADES_PATH = os.path.join(eng.DATA_DIR, "_historical_trades.json")
OUT_PATH = os.path.join(eng.DATA_DIR, "_isolated_03_04_05_trades.json")


def load_base_trades(path: str = BASE_TRADES_PATH) -> list:
    with open(path) as f:
        data = json.load(f)
    return data["trades"] if isinstance(data, dict) else data


def isolate_patch_03(trades: list) -> dict:
    """breaker И mitigation ВМЕСТЕ -- одна affected-группа (как в shadow_engine.py,
    не разделяем на два подтипа -- владелец явно запретил изобретать новое правило)."""
    affected = [t for t in trades if t.get("patch_tags", {}).get("breaker_mitigation")]
    not_affected = [t for t in trades if not t.get("patch_tags", {}).get("breaker_mitigation")]
    return {"affected": hr._metrics_for(affected), "not_affected": hr._metrics_for(not_affected)}


def isolate_patch_04(trades: list) -> dict:
    affected = [t for t in trades if t.get("patch_tags", {}).get("divergence_against")]
    not_affected = [t for t in trades if not t.get("patch_tags", {}).get("divergence_against")]
    return {"affected": hr._metrics_for(affected), "not_affected": hr._metrics_for(not_affected)}


def isolate_patch_05(trades: list) -> dict:
    affected = [t for t in trades if t.get("patch_tags", {}).get("bpr_confluence")]
    not_affected = [t for t in trades if not t.get("patch_tags", {}).get("bpr_confluence")]
    return {"affected": hr._metrics_for(affected), "not_affected": hr._metrics_for(not_affected)}


def run_isolation(base_trades_path: str = BASE_TRADES_PATH, out_path: str = OUT_PATH) -> dict:
    trades = load_base_trades(base_trades_path)
    trades = engp.tag_existing_trades(trades)
    result = {
        "base_total": len(trades),
        "patch_03": isolate_patch_03(trades),
        "patch_04": isolate_patch_04(trades),
        "patch_05": isolate_patch_05(trades),
    }
    with open(out_path, "w") as f:
        json.dump({"trades": trades, "result": result}, f)
    return result


def render_markdown(result: dict) -> str:
    lines = [
        "## Изоляция 03/04/05 -- раздельный эффект (2026-07-11, «Пакетный ритм» пакет 2, М4)",
        "",
        f"База (без патчей 01/02) -- {result['base_total']} сделок, тот же набор 100 символов "
        "x ~12 месяцев. Патчи 03/04/05 гейтуются СТРОГО как определены в shadow_engine.py "
        "(без изобретения новых правил) -- 03 объединяет breaker+mitigation в одну метку "
        "affected, 04 -- дивергенция против направления, 05 -- BPR confluence.",
        "",
        "| Патч | Группа | Сделок | Win rate | Avg R | Expectancy | Max DD (R) | PF |",
        "|---|---|---|---|---|---|---|---|",
    ]
    labels = {
        "patch_03": "03 (breaker+mitigation)",
        "patch_04": "04 (RSI-дивергенция против)",
        "patch_05": "05 (BPR confluence)",
    }
    for key in ("patch_03", "patch_04", "patch_05"):
        for group_name, group_label in (("affected", "affected"), ("not_affected", "не affected")):
            m = result[key][group_name]
            if m["total"]:
                lines.append(f"| {labels[key]} | {group_label} | {m['total']} | {m['win_rate']}% | "
                              f"{m['avg_r']:+.3f} | {m['expectancy_r']:+.3f} | {m['max_dd_r']:.2f} | {m['profit_factor']} |")
            else:
                lines.append(f"| {labels[key]} | {group_label} | 0 | — | — | — | — | — |")
    lines.append("")
    lines.append(
        "**Как читать**: это ПОСТ-ФАКТУМ разбивка уже принятых сделок (03/04/05 не влияют "
        "на то, была ли сделка открыта -- см. докстринг модуля), не гейт, реально блокирующий "
        "вход. Малые группы (n<20) -- не основание для решения. Решение о переносе в бой -- "
        "отдельный шаг ПОСЛЕ этих цифр, не в рамках этого прогона."
    )
    return "\n".join(lines)


if __name__ == "__main__":
    result = run_isolation()
    print(json.dumps(result, indent=2, default=str))
    print()
    print(render_markdown(result))
