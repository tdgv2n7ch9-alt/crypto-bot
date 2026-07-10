"""
backtest/stress_slices.py -- ночная сессия #3, Блок H.2/H.3. Walk-forward по
кварталам + стресс-срезы (Азия-сессия, высоковолатильные дни BTC, мемы vs топ-50) --
ПОВЕРХ уже посчитанных сделок baseline-прогона (backtest/data/_historical_trades.json),
БЕЗ нового бэктеста (дёшево, переиспользует существующие данные).
"""
import bisect
import gzip
import csv
import os
from datetime import datetime, timezone

import backtest.historical_report as hr
import backtest.engine as eng

DATA_DIR = eng.DATA_DIR


def _quarter_bucket(start_ms: int) -> str:
    dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    q = (dt.month - 1) // 3 + 1
    return f"{dt.year}-Q{q}"


def build_walkforward(trades: list) -> dict:
    by_q = {}
    for t in trades:
        by_q.setdefault(_quarter_bucket(t["start_ms"]), []).append(t)
    return {k: hr._metrics_for(v) for k, v in sorted(by_q.items())}


def _load_btc_1h():
    path = os.path.join(DATA_DIR, "BTC_1h.csv.gz")
    rows = []
    with gzip.open(path, "rt", newline="") as f:
        for row in csv.DictReader(f):
            rows.append({"timestamp": int(row["t"]), "close": float(row["c"])})
    return rows


def _btc_24h_change_at(btc_1h: list, ts_list: list, as_of_ms: int) -> float:
    idx_now = bisect.bisect_right(ts_list, as_of_ms) - 1
    if idx_now < 24:
        return 0.0
    now_price = btc_1h[idx_now]["close"]
    then_price = btc_1h[idx_now - 24]["close"]
    if not then_price:
        return 0.0
    return (now_price - then_price) / then_price * 100


def build_stress_slices(trades: list, symbols_ranked: list) -> dict:
    top50 = set(symbols_ranked[:50])

    # Азия-сессия -- переиспользуем _session_bucket
    asia = [t for t in trades if hr._session_bucket(t["start_ms"]) == "Asia (01-04)"]
    non_asia = [t for t in trades if hr._session_bucket(t["start_ms"]) != "Asia (01-04)"]

    # Мемы (за пределами топ-50 по объёму на момент скачивания) vs топ-50
    top50_trades = [t for t in trades if t["symbol"] in top50]
    rest_trades = [t for t in trades if t["symbol"] not in top50]

    # Высоковолатильные дни BTC (|24h change BTC| > 5% на момент сигнала)
    btc_1h = _load_btc_1h()
    ts_list = [c["timestamp"] for c in btc_1h]
    high_vol, low_vol = [], []
    for t in trades:
        chg = _btc_24h_change_at(btc_1h, ts_list, t["start_ms"])
        (high_vol if abs(chg) > 5.0 else low_vol).append(t)

    return {
        "asia_session": hr._metrics_for(asia),
        "non_asia_session": hr._metrics_for(non_asia),
        "top50_symbols": hr._metrics_for(top50_trades),
        "rest_symbols_meme_proxy": hr._metrics_for(rest_trades),
        "high_vol_btc_days": hr._metrics_for(high_vol),
        "low_vol_btc_days": hr._metrics_for(low_vol),
    }


def render_walkforward_markdown(wf: dict) -> str:
    lines = ["# WALKFORWARD.md — устойчивость метрик по кварталам (ночная сессия #3, Блок H)", ""]
    lines.append(
        "Та же выборка (2864 сделки, `HISTORICAL_BACKTEST.md`, `fa_engine.build_full_"
        "analysis()` реплей боевой логики), разбитая по календарным кварталам — "
        "проверка: методика работает стабильно во времени или только в отдельные "
        "периоды (например, только в трендовом рынке)."
    )
    lines.append("")
    lines.append("| Квартал | Сделок | Win rate | Средний R | Expectancy | Max DD (R) | PF |")
    lines.append("|---|---|---|---|---|---|---|")
    for q, m in wf.items():
        if m["total"]:
            lines.append(f"| {q} | {m['total']} | {m['win_rate']}% | {m['avg_r']:+.3f} | "
                         f"{m['expectancy_r']:+.3f} | {m['max_dd_r']:.2f} | {m['profit_factor']} |")
    lines.append("")

    vals = [m["avg_r"] for m in wf.values() if m["total"] >= 20]
    if vals:
        spread = max(vals) - min(vals)
        lines.append(
            f"**Разброс среднего R по кварталам (только кварталы с 20+ сделками)**: "
            f"от {min(vals):+.3f} до {max(vals):+.3f} (размах {spread:.3f}R). "
            f"{'Все кварталы положительные' if min(vals) > 0 else 'ЕСТЬ отрицательные кварталы'} "
            f"— {'нет признаков, что весь результат держится на одном периоде' if min(vals) > 0 else 'методика НЕ одинаково стабильна во всех кварталах, см. таблицу выше'}."
        )
    lines.append("")
    lines.append(
        "Честно: 12 месяцев — не покрывает полный рыночный цикл (бык+медведь+боковик "
        "в равной мере), выводы по \"работает всегда или только в тренде\" ограничены "
        "тем, какие режимы вообще были представлены за этот год — не экстраполирую на "
        "режимы, которых не было в выборке."
    )
    return "\n".join(lines)


def render_stress_markdown(stress: dict) -> str:
    lines = ["# Стресс-срезы — где методика ломается (ночная сессия #3, Блок H, добавлено в PATCH_IMPACT.md-семью отчётов)", ""]
    lines.append("Та же выборка 2864 сделки, три независимых среза.")
    lines.append("")
    lines.append("## 1. Азия-сессия vs остальное время")
    lines.append("| Срез | Сделок | Win rate | Средний R |")
    lines.append("|---|---|---|")
    for name, m in (("Азия (01-04 UTC+3)", stress["asia_session"]),
                     ("вне Азии", stress["non_asia_session"])):
        if m["total"]:
            lines.append(f"| {name} | {m['total']} | {m['win_rate']}% | {m['avg_r']:+.3f} |")
    lines.append("")
    lines.append("## 2. Топ-50 по объёму vs остальные (proxy для \"мемкоины\")")
    lines.append(
        "Честно: НЕ настоящий мемкоин-флаг (тот требует rank/mcap данные, которых "
        "исторически нет в этом прогоне, см. допущение 1 `HISTORICAL_BACKTEST.md`) — "
        "приближение через позицию в списке 100 символов, отсортированном по объёму на "
        "момент скачивания (не исторически по каждой дате)."
    )
    lines.append("| Срез | Сделок | Win rate | Средний R |")
    lines.append("|---|---|---|")
    for name, m in (("топ-50 по объёму", stress["top50_symbols"]),
                     ("остальные 50 (meme-proxy)", stress["rest_symbols_meme_proxy"])):
        if m["total"]:
            lines.append(f"| {name} | {m['total']} | {m['win_rate']}% | {m['avg_r']:+.3f} |")
    lines.append("")
    lines.append("## 3. Высоковолатильные дни BTC (|24h change|>5%) vs спокойные")
    lines.append("| Срез | Сделок | Win rate | Средний R |")
    lines.append("|---|---|---|")
    for name, m in (("BTC 24h изменение >5% (в любую сторону)", stress["high_vol_btc_days"]),
                     ("BTC 24h изменение ≤5%", stress["low_vol_btc_days"])):
        if m["total"]:
            lines.append(f"| {name} | {m['total']} | {m['win_rate']}% | {m['avg_r']:+.3f} |")
    lines.append("")
    return "\n".join(lines)
