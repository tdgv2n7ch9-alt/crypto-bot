"""
НОЧЬ#3, Н6 (владелец): rug-скоринг монет ранга 150-500 CoinGecko по mcap ->
output/RUG_WATCHLIST_SMALLCAPS.md. Батчи + честное "н/д" на 429, контрольная
тройка (не BTC/ETH/SOL -- те вне диапазона 150-500 -- а известные established
mid-cap проекты) без ложных срабатываний.

Переиспользует ГОТОВОЕ из tools/backtest_f3_rug_scan.py (Пакет 17 Ф3, тот же
владелец, тот же CoinGecko free-tier урок про 429 на per-coin вызовах, см. его
докстринг) -- НЕ дублирует bulk-fetch/detail-budget логику:
  - fetch_universe(top_n) -- bulk /coins/markets, постранично, честно меньше
    top_n при 429 на странице.
  - _synthetic_cg_detail(coin) / fetch_real_cg_detail(slug) -- та же схема
    "synthetic FDV из bulk на 100% выборки + budgeted real per-coin detail
    поверх throttle" (EXTRA_DETAIL_PACE_SEC/DETAIL_CALL_BUDGET/MAX_CONSECUTIVE_429
    те же константы, тот же модуль).

top_n=500 -> локальная фильтрация по rank в [RANK_LO, RANK_HI] -- один bulk-
фетч (2 страницы по 250) вместо отдельного постраничного механизма с offset,
дешевле и без дублирования уже отлаженного кода.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import rug_radar
from backtest_f3_rug_scan import (
    EXTRA_DETAIL_PACE_SEC, DETAIL_CALL_BUDGET, MAX_CONSECUTIVE_429,
    YELLOW_ZONE_LO, YELLOW_ZONE_HI,
    fetch_universe, fetch_real_cg_detail, _synthetic_cg_detail,
)

RANK_LO = 150
RANK_HI = 500

# Известные established mid-cap проекты (крупные листинги, годы истории) --
# контрольная тройка на ложные срабатывания. НЕ BTC/ETH/SOL (см. Пакет 9
# калибровку exchange_transfers) -- те вне диапазона 150-500 по рангу,
# бессмысленно проверять здесь. Кандидатов больше 3 -- ранг каждого дрейфует
# со временем, честно берём первых 3, которые реально попали в выборку
# ЭТОГО прогона, не подгоняем список под ожидаемый результат.
CONTROL_TRIO_CANDIDATES = ["AAVE", "MKR", "SNX", "CRV", "COMP", "GRT", "ENS", "LDO", "INJ", "RUNE"]


def run(rank_lo: int = RANK_LO, rank_hi: int = RANK_HI, log=print) -> dict:
    log(f"[Н6] fetch universe (bulk, top {rank_hi})...")
    full_universe = fetch_universe(rank_hi)
    universe = [c for c in full_universe if rank_lo <= c["rank"] <= rank_hi]
    log(f"[Н6] вселенная {len(full_universe)} монет всего, в диапазоне рангов "
        f"{rank_lo}-{rank_hi}: {len(universe)}")

    detail_calls_used = 0
    consecutive_429 = 0
    real_detail_count = 0
    results = []

    for i, coin in enumerate(universe):
        cg_detail = None
        got_real = False
        if (detail_calls_used < DETAIL_CALL_BUDGET and consecutive_429 < MAX_CONSECUTIVE_429):
            detail_calls_used += 1
            time.sleep(EXTRA_DETAIL_PACE_SEC)
            real_detail = fetch_real_cg_detail(coin["slug"])
            if real_detail:
                cg_detail = real_detail
                got_real = True
                real_detail_count += 1
                consecutive_429 = 0
            else:
                consecutive_429 += 1
        if cg_detail is None:
            cg_detail = _synthetic_cg_detail(coin)

        coin_shape = {"quote": {"USDT": {
            "market_cap": coin["market_cap"], "volume_24h": coin["volume_24h"],
            "percent_change_30d": coin["pct_30d"],
        }}}
        rug = rug_radar.compute_rug_risk(coin["symbol"], coin_shape, cg_detail=cg_detail)
        results.append({
            "symbol": coin["symbol"], "name": coin["name"], "rank": coin["rank"],
            "score": rug["score"], "max_possible_score": rug["max_possible_score"],
            "reasons": rug["reasons"], "detectors": rug["detectors"],
            "warn": rug["warn"], "alert": rug["alert"],
            "cg_detail_source": "real" if got_real else ("synthetic_fdv" if cg_detail else "none"),
        })
        if (i + 1) % 25 == 0:
            log(f"[Н6] {i+1}/{len(universe)} обработано, real cg_detail: {real_detail_count}, "
                f"budget использован: {detail_calls_used}/{DETAIL_CALL_BUDGET}, "
                f"consecutive_429: {consecutive_429}")

    log(f"[Н6] Готово: {len(results)} монет, real cg_detail получен для {real_detail_count}, "
        f"detail-бюджет использован {detail_calls_used}/{DETAIL_CALL_BUDGET}")
    return {
        "results": results, "universe_count": len(universe),
        "full_universe_count": len(full_universe),
        "rank_lo": rank_lo, "rank_hi": rank_hi,
        "real_detail_count": real_detail_count, "detail_calls_used": detail_calls_used,
    }


def control_trio_check(results: list) -> dict:
    """Контрольная тройка -- первые 3 из CONTROL_TRIO_CANDIDATES, реально
    найденные в этом прогоне (в диапазоне 150-500). Честно меньше 3, если
    ранг кандидатов в этот раз ушёл за пределы диапазона -- не подставляем
    другие символы задним числом."""
    by_sym = {r["symbol"]: r for r in results}
    found = []
    for sym in CONTROL_TRIO_CANDIDATES:
        r = by_sym.get(sym)
        if r is not None:
            found.append({"symbol": sym, "rank": r["rank"], "score": r["score"],
                           "warn": r["warn"], "pass": not r["warn"]})
        if len(found) >= 3:
            break
    return {"trio": found, "all_pass": all(f["pass"] for f in found) if found else None,
            "count": len(found)}


def render_markdown(data: dict) -> str:
    results = data["results"]
    warn = sorted([r for r in results if r["score"] >= 40], key=lambda r: -r["score"])
    yellow = sorted([r for r in results if YELLOW_ZONE_LO <= r["score"] < 40], key=lambda r: -r["score"])
    trio = control_trio_check(results)

    lines = [
        f"# RUG_WATCHLIST_SMALLCAPS.md -- Н6 rug-скоринг ранг {data['rank_lo']}-{data['rank_hi']} "
        "CoinGecko (НОЧЬ#3, владелец)",
        "",
        f"Вселенная: bulk-фетч топ-{data['rank_hi']} ({data['full_universe_count']} монет получено), "
        f"после фильтра по рангу {data['rank_lo']}-{data['rank_hi']}: {data['universe_count']} монет. "
        f"Real per-coin `cg_detail` получен для {data['real_detail_count']} монет "
        f"(бюджет {data['detail_calls_used']}/{DETAIL_CALL_BUDGET} использован) -- остальные "
        "оценены по синтетическому `cg_detail` из bulk FDV либо без FDV вообще (детектор честно "
        "'unavailable'), тот же принцип, что Ф3 (Пакет 17).",
        "",
        "## Контрольная тройка (established mid-cap, не должно быть ложных срабатываний)",
        "",
        "| Символ | Ранг | Score | WARN (>=40) | Результат |",
        "|---|---|---|---|---|",
    ]
    if not trio["trio"]:
        lines.append("| — | — | — | — | **ни один кандидат из CONTROL_TRIO_CANDIDATES не попал "
                      "в диапазон этого прогона -- проверка не выполнена** |")
    else:
        for f in trio["trio"]:
            status = "✅ PASS" if f["pass"] else "❌ FAIL (ложный WARN)"
            lines.append(f"| {f['symbol']} | {f['rank']} | {f['score']} | {f['warn']} | {status} |")
        if trio["count"] < 3:
            lines.append(f"\n_Честно: найдено только {trio['count']}/3 кандидатов в этом диапазоне "
                          "рангов в этом прогоне (ранг остальных кандидатов дрейфовал за пределы)._")
    lines.append("")

    lines += [f"## Score >= 40 (WARN) -- {len(warn)} монет", "",
              "| Символ | Ранг | Score | Детекторы (причины) | cg_detail |",
              "|---|---|---|---|---|"]
    if not warn:
        lines.append("| — | — | — | нет монет с score >= 40 в этом прогоне | — |")
    else:
        for r in warn:
            lines.append(f"| {r['symbol']} | {r['rank']} | {r['score']} | "
                          f"{'; '.join(r['reasons']) or '—'} | {r['cg_detail_source']} |")
    lines.append("")

    lines += [f"## Жёлтая зона {YELLOW_ZONE_LO}-{YELLOW_ZONE_HI} -- {len(yellow)} монет", "",
              "| Символ | Ранг | Score | Детекторы (причины) | cg_detail |",
              "|---|---|---|---|---|"]
    if not yellow:
        lines.append(f"| — | — | — | нет монет в диапазоне {YELLOW_ZONE_LO}-{YELLOW_ZONE_HI} "
                      "в этом прогоне | — |")
    else:
        for r in yellow:
            lines.append(f"| {r['symbol']} | {r['rank']} | {r['score']} | "
                          f"{'; '.join(r['reasons']) or '—'} | {r['cg_detail_source']} |")
    lines.append("")

    lines.append(
        "**Честно про покрытие детекторов**: `concentration` (holders) -- НИКОГДА не "
        "заполнен (нужен Etherscan, не делался для всей выборки, тот же выбор, что Ф3). "
        "`fdv_mcap`/`vertical_growth_thin_volume` -- заполнены для ВСЕХ монет (bulk-данные). "
        "`age_listing`/`exchange_transfers` -- только для "
        f"{data['real_detail_count']}/{data['universe_count']} монет, получивших real "
        "`cg_detail`. Score -- честная сумма из ДОСТУПНЫХ детекторов, не притворяется "
        "полными 100 для всех монет."
    )
    return "\n".join(lines)


if __name__ == "__main__":
    rank_hi = int(sys.argv[1]) if len(sys.argv) > 1 else RANK_HI
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    data = run(rank_hi=rank_hi)
    t1 = time.time()
    print(f"Готово за {round(t1-t0,1)}с")
    md = render_markdown(data)
    with open(os.path.join(out_dir, "RUG_WATCHLIST_SMALLCAPS.md"), "w") as f:
        f.write(md)
    print(f"Записано: {os.path.join(out_dir, 'RUG_WATCHLIST_SMALLCAPS.md')}")
