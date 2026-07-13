"""
НОЧЬ#3 Н8 (владелец): генератор MORNING_BRIEF -- одна страница для чтения с
телефона. Шаблон на КАЖДУЮ ночь (не разово): запускается вручную из ночной
сессии -- `python3 tools/morning_brief.py` -- пишет
`output/MORNING_BRIEF_<дата>.md`.

Протокол правды: только факты из уже существующих persist-источников (диск,
не in-memory состояние живого процесса бота -- отдельный процесс их не
видит, честно не притворяемся, что видим) -- shadow_engine (contour/EMA
готовность), bot.author_zones_status_summary() (живой пересчёт по
watch_zones.json + текущей цене get_top500(), безопасно из отдельного
процесса), daily_metrics.level_watch_touches_today() (персистентный JSONL,
НЕ bot.watchlist_alerted -- тот кулдаун-словарь живёт только в памяти
живого бота, недоступен отдельному скрипту, см. НОЧЬ#3 Н4 оговорку в
morning_metrics.py), onchain_metrics.get_liquidity_summary() (живой
пересчёт), rug_radar.compute_rug_risk() по author-zone символам (без
cg_detail -- честно "н/д" по FDV/age-детекторам, не выдумывается), EVOLUTION.md
(последняя датированная запись). Ни одной оценки без цифры -- где цифры нет,
пишем "н/д", не догадку.
"""
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot
import daily_metrics
import onchain_metrics
import rug_radar
import shadow_engine

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(REPO_ROOT, "output")
EVOLUTION_MD_PATH = os.path.join(REPO_ROOT, "EVOLUTION.md")
MORNING_WINDOW_SEC = 12 * 3600
MARKET_SYMBOLS = ("BTC", "ETH")

ZONE_POSITION_LABEL = {
    "ЦЕНА В ЗОНЕ": "внутри",
    "ЖДЁМ ЦЕНУ": "ждём (ещё не дошла)",
    "ОТРАБОТАНА": "прошла зону насквозь",
}


def market_section(now_ts: float) -> list:
    """1) BTC/ETH цена + позиция относительно ВСЕХ author-зон (внутри/выше/
    ниже, дистанция %) + касания level-watch зон за ночь (персистентный
    JSONL, не in-memory кулдаун -- честно доступно отдельному процессу)."""
    lines = ["## 1) Рынок к утру", ""]
    try:
        summary = bot.author_zones_status_summary()
    except Exception as e:
        lines.append(f"н/д (ошибка author_zones_status_summary: {e})")
        summary = None

    if summary is not None:
        for target in MARKET_SYMBOLS:
            zones = [z for z in summary["zones"] if z["symbol"] == target]
            if not zones:
                lines.append(f"**{target}**: нет активных author-зон")
                continue
            price = next((z["price"] for z in zones if z.get("price")), None)
            price_txt = f"${price:,.2f}" if price else "н/д"
            lines.append(f"**{target}** ({price_txt}):")
            for z in zones:
                if z["status"] == "н/д (нет цены)":
                    lines.append(f"  - {z['side']} {z['lo']:,.2f}-{z['hi']:,.2f}: н/д (нет цены)")
                    continue
                pos = ZONE_POSITION_LABEL[z["status"]]
                lines.append(f"  - {z['side']} {z['lo']:,.2f}-{z['hi']:,.2f}: {pos}, "
                              f"дистанция {z['distance_pct']:.2f}%")

    lines.append("")
    lines.append("**Касания level-watch зон за ночь (12ч):**")
    try:
        touches = daily_metrics.level_watch_touches_today(now_ts=now_ts, window_sec=MORNING_WINDOW_SEC)
        if touches:
            symbols = sorted({t.get("symbol", "?") for t in touches})
            lines.append(f"  Всего: {len(touches)}, символы: {', '.join(symbols)}")
        else:
            lines.append("  Ни одного касания за ночь")
    except Exception as e:
        lines.append(f"  н/д (ошибка: {e})")
    return lines


def shadow_table_section() -> list:
    """2) Тень одной таблицей -- tz13/Патч05/Патч09/EMA-стек: n, готовность
    к решению (переиспользует shadow_engine.contour_readiness_summary()/
    ema_stack_readiness_summary(), НОЧЬ#3 Н4)."""
    lines = ["", "## 2) Тень одной таблицей", ""]
    contours = shadow_engine.contour_readiness_summary()
    labels = {"tz13": "tz13", "patch05_bpr": "Патч 05 (BPR)", "patch09_oi": "Патч 09 (OI/funding/L-S)"}
    lines.append("| Контур | n | Порог | Готов к решению |")
    lines.append("|---|---|---|---|")
    for key, label in labels.items():
        c = contours[key]
        ready_txt = "да" if c["ready"] else f"нет, осталось {c['remaining']}"
        lines.append(f"| {label} | {c['n']} | {c['threshold']} | {ready_txt} |")
    ema = shadow_engine.ema_stack_readiness_summary()
    ema_ready_txt = "да (окно закрыто)" if ema["ready"] else \
        f"нет, {ema['elapsed_hours']:.1f}/{ema['window_hours']:.0f}ч окна"
    lines.append(f"| EMA-стек | {ema['n']} | окно {ema['window_hours']:.0f}ч | {ema_ready_txt} |")
    return lines


def _latest_evolution_finding() -> str:
    """Библиотека: последняя датированная запись EVOLUTION.md (`## YYYY-...`
    заголовок) -- заголовок + первая содержательная строка. Заголовки без
    даты в начале (например "## Дальше (...)") пропускаются -- не находка
    ночи, а служебная секция."""
    if not os.path.exists(EVOLUTION_MD_PATH):
        return "н/д (EVOLUTION.md не найден)"
    with open(EVOLUTION_MD_PATH, encoding="utf-8") as f:
        text = f.read()
    headings = [(m.start(), m.group(1)) for m in re.finditer(r"^## (20\d\d-\S+.*)$", text, re.MULTILINE)]
    if not headings:
        return "н/д (нет датированных записей в EVOLUTION.md)"
    last_pos, last_heading = headings[-1]
    tail = text[last_pos:]
    body_lines = [ln.strip() for ln in tail.split("\n")[1:] if ln.strip()]
    first_line = body_lines[0] if body_lines else ""
    finding = f"{last_heading} -- {first_line}"
    if len(finding) > 220:
        finding = finding[:220].rsplit(" ", 1)[0] + "..."
    return finding


def _top_onchain_finding() -> str:
    """On-chain: живой пересчёт onchain_metrics.get_liquidity_summary() --
    факты с цифрами (стейблкоин-поток 30д %, USDT.D сейчас %), не оценка."""
    try:
        summary = onchain_metrics.get_liquidity_summary()
    except Exception as e:
        return f"н/д (ошибка get_liquidity_summary: {e})"
    flow = summary.get("stablecoin_flow_30d", {})
    dom = summary.get("usdt_dominance", {})
    parts = []
    if flow.get("ok") and flow.get("flow_30d_pct") is not None:
        pct = flow["flow_30d_pct"]
        sign = "+" if pct >= 0 else ""
        parts.append(f"стейблкоины 30д {sign}{pct:.1f}%")
    else:
        parts.append("стейблкоины 30д: н/д")
    if dom.get("ok") and dom.get("usdt_dominance_pct") is not None:
        parts.append(f"USDT.D {dom['usdt_dominance_pct']:.2f}%")
    else:
        parts.append("USDT.D: н/д")
    return "Ликвидность рынка: " + ", ".join(parts)


def _top_rugscan_finding() -> str:
    """Rug-скан: живой пересчёт rug_radar.compute_rug_risk() по author-zone
    символам (cg_detail=None -- FDV/age-детекторы честно н/д без доп.
    сетевого вызова, см. rug_radar.py докстринг). Максимальный score среди
    отсканированных -- факт с цифрой, не оценка."""
    try:
        items = bot._limitki_collect_zones()
    except Exception as e:
        return f"н/д (ошибка _limitki_collect_zones: {e})"
    if not items:
        return "Rug-скан: нет активных author-зон для скана"
    symbols = sorted({it["symbol"] for it in items})
    try:
        coin_map = {c["symbol"]: c for c in bot.get_top500()}
    except Exception as e:
        return f"н/д (ошибка get_top500: {e})"
    scored = []
    for sym in symbols:
        coin = coin_map.get(sym)
        if not coin:
            continue
        try:
            risk = rug_radar.compute_rug_risk(sym, coin)
            scored.append(risk)
        except Exception:
            continue
    if not scored:
        return f"Rug-скан: {len(symbols)} символов в author-зонах, ни один не оценён (нет цены в снапшоте)"
    top = max(scored, key=lambda r: r["score"])
    if top["score"] <= 0:
        return f"Rug-скан: {len(scored)} символов проверено, повышенного риска не найдено (max score 0)"
    reasons = "; ".join(top.get("reasons", [])[:2])
    return f"Rug-скан: {top['symbol']} максимальный score {top['score']} ({reasons}), проверено {len(scored)} символов"


def top_findings_section() -> list:
    """3) Топ-3 находки ночи (библиотека/он-чейн/rug-скан) -- одна строка
    на источник, живой пересчёт где возможно, иначе последняя запись
    EVOLUTION.md для библиотеки (сама транскрибация/чтение -- не пересчёт
    за секунды, это факт последней ночной сессии)."""
    lines = ["", "## 3) Топ-3 находки ночи", ""]
    lines.append(f"1. Библиотека: {_latest_evolution_finding()}")
    lines.append(f"2. Он-чейн: {_top_onchain_finding()}")
    lines.append(f"3. {_top_rugscan_finding()}")
    return lines


def open_questions_section() -> list:
    """4) Три вопроса владельцу на сегодня, по важности (деньги/сигнальная
    логика -- первым, см. CLAUDE.md "Железные границы"). Q1 -- динамический
    (по факту готовности shadow-контуров), Q2/Q3 -- ссылки на уже
    зафиксированные open items в PROGRESS.md/NEXT_PACKAGE.md, не выдумка."""
    lines = ["", "## 4) Вопросы владельцу на сегодня", ""]
    contours = shadow_engine.contour_readiness_summary()
    ready_names = {"tz13": "tz13", "patch05_bpr": "Патч 05 (BPR)", "patch09_oi": "Патч 09 (OI/funding/L-S)"}
    ready = [ready_names[k] for k, v in contours.items() if v["ready"]]
    if ready:
        q1 = (f"Контуры набрали порог для решения: {', '.join(ready)} (см. таблицу п.2, "
              f"точные n там). Переводить в бой или продолжать копить в тени?")
    else:
        q1 = "Ни один shadow-контур ещё не набрал порог решения (см. таблицу п.2) -- вопроса нет, продолжаем копить."
    lines.append(f"1. {q1}")
    lines.append("2. Пакет 18: остаток приёмки владельца (живой `/coin`/`/precision`/тап "
                  "\"⭐ ЛИМИТКИ\" в Telegram, разделы РЫНОК/РАДАРЫ/МОИ/СИСТЕМА) -- см. PROGRESS.md, "
                  "\"DoD Пакета 18\" -- ещё не подтверждено самим владельцем.")
    lines.append("3. Приоритет следующего пакета: порядок модулей в NEXT_PACKAGE.md "
                  "(\"Порядок/приоритет — за владельцем на приёмке\") не выбран.")
    return lines


def build_morning_brief(now_ts: float = None) -> str:
    now = now_ts if now_ts is not None else time.time()
    dt_local = datetime.fromtimestamp(now, tz=timezone(timedelta(hours=3)))
    date_str = dt_local.strftime("%d.%m.%Y")

    lines = [f"# MORNING BRIEF -- {date_str}", ""]
    lines += market_section(now)
    lines += shadow_table_section()
    lines += top_findings_section()
    lines += open_questions_section()
    return "\n".join(lines) + "\n"


def write_morning_brief(now_ts: float = None) -> str:
    """Пишет output/MORNING_BRIEF_<YYYY-MM-DD>.md, возвращает путь."""
    now = now_ts if now_ts is not None else time.time()
    dt_local = datetime.fromtimestamp(now, tz=timezone(timedelta(hours=3)))
    date_str = dt_local.strftime("%Y-%m-%d")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, f"MORNING_BRIEF_{date_str}.md")
    text = build_morning_brief(now_ts=now)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


if __name__ == "__main__":
    out_path = write_morning_brief()
    print(f"morning_brief.py: записано {out_path}")
