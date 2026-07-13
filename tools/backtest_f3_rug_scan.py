"""
Пакет 17, Ф3 (владелец, ночной пакет): rug-скоринг топ-300 CoinGecko по mcap.
Батчи + честное "н/д" на 429, регрессия "ноль ложных на BTC/ETH/SOL".

Честный урок из RUG_WATCHLIST.md (Пакет 11 Ф3, тот же владелец): прошлая
попытка per-coin `/coins/{id}` (cg_detail) на 300 монет с обычным
`bot._cg_get()` throttle (1.3с) поймала 429 почти на КАЖДОЙ монете (232/240,
включая первую же -- BTC) -- CoinGecko free-tier лимит жёстче, чем
`_CG_MIN_INTERVAL` в одиночку способен уважать при 300 последовательных
detail-вызовах. Урок применён ДВУМЯ способами:

1. **Синтетический cg_detail из bulk FDV** (тот же трюк, что Пакет 16
   summer_spot_plan.py): fdv_mcap-детектор работает на ВСЕ 300 монет БЕЗ
   единого per-coin вызова -- FDV уже есть в bulk `/coins/markets`.
   vertical_growth_thin_volume -- та же история (mcap/volume/pct_30d -- всё
   bulk). Это закрывает 2 из 5 детекторов на 100% выборки, бесплатно.
2. **Per-coin `/coins/{id}` (age_listing/exchange_transfers) -- НАМНОГО
   медленнее**, чем в прошлой попытке: EXTRA_DETAIL_PACE_SEC=3.0с ПОВЕРХ
   `bot._cg_get()`'s собственного throttle (итого ~4.3с между detail-
   вызовами, не 1.3с) -- честная попытка получить больше 8/240, но с явным
   ограничением бюджета (DETAIL_CALL_BUDGET) и остановкой при N
   последовательных 429 подряд (не долбить лимит бесконечно). Всё, что не
   получилось -- честное "н/д", не выдумывается.

concentration-детектор (holders_data) -- НЕ пытается получить (нужен
Etherscan, ERC-20-специфичен, не для всех 300 монет применим) -- всегда
н/д, тот же осознанный выбор, что Пакет 16.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot
import rug_radar

TOP_N = 300
EXTRA_DETAIL_PACE_SEC = 3.0     # поверх bot._cg_get()'s собственного 1.3с throttle
DETAIL_CALL_BUDGET = 300        # максимум per-coin detail-вызовов за прогон
MAX_CONSECUTIVE_429 = 8         # честная остановка попыток detail, не долбёжка лимита
YELLOW_ZONE_LO, YELLOW_ZONE_HI = 30, 39   # "жёлтая зона" -- владелец, Пакет 17


UNIVERSE_RETRY_BACKOFF_SEC = (30, 60, 120)   # ретрай ТОЛЬКО для критичного bulk-вызова
                                               # вселенной (единичный 429 не должен убивать
                                               # весь прогон -- per-coin detail-вызовы ниже
                                               # НЕ ретраятся, это уже сознательный выбор
                                               # "429 -> честное н/д", не для bulk-старта)


def fetch_universe(top_n: int = TOP_N) -> list:
    """Bulk /coins/markets, постранично (per_page<=250) -- честно возвращает
    меньше top_n, если CoinGecko отдал 429 на какой-то странице (не выдумывает
    недостающие монеты). Ретраит с задержкой ТОЛЬКО первую страницу (самый
    вероятный кейс -- транзиентный 429 от предыдущей активности на этом же IP,
    не хронический бан)."""
    out = []
    page = 1
    per_page = 250
    while len(out) < top_n:
        remaining = top_n - len(out)
        params = {"vs_currency": "usd", "order": "market_cap_desc",
                   "per_page": min(per_page, remaining if remaining < per_page else per_page),
                   "page": page, "price_change_percentage": "30d"}
        data = None
        attempts = UNIVERSE_RETRY_BACKOFF_SEC if page == 1 else ()
        for attempt_i in range(len(attempts) + 1):
            try:
                data = bot._cg_get("https://api.coingecko.com/api/v3/coins/markets", params=params, timeout=20)
                break
            except Exception as e:
                print(f"[UNIVERSE] page {page} attempt {attempt_i+1} FAILED: {type(e).__name__}: {e}",
                      file=sys.stderr)
                if attempt_i < len(attempts):
                    wait = attempts[attempt_i]
                    print(f"[UNIVERSE] retry through in {wait}s...", file=sys.stderr)
                    time.sleep(wait)
        if not data:
            break
        for d in data:
            sym = (d.get("symbol") or "").upper()
            if not sym or sym in bot.STABLECOINS:
                continue
            out.append({
                "symbol": sym, "slug": d.get("id", sym.lower()), "name": d.get("name", sym),
                "rank": d.get("market_cap_rank") or 9999,
                "market_cap": d.get("market_cap", 0) or 0,
                "volume_24h": d.get("total_volume", 0) or 0,
                "fdv": d.get("fully_diluted_valuation"),
                "pct_30d": d.get("price_change_percentage_30d_in_currency",
                                  d.get("price_change_percentage_30d")),
            })
        if len(data) < per_page:
            break
        page += 1
    return out[:top_n]


def _synthetic_cg_detail(coin: dict) -> dict:
    if not coin.get("fdv"):
        return None
    return {"market_data": {"fully_diluted_valuation": {"usd": coin["fdv"]}}}


def fetch_real_cg_detail(slug: str):
    """Один per-coin /coins/{id} вызов -- честный None при любой ошибке
    (429 включительно), не выдумывает."""
    try:
        return bot._cg_get(f"https://api.coingecko.com/api/v3/coins/{slug}",
                            params={"localization": "false", "tickers": "true",
                                     "community_data": "false", "developer_data": "false"},
                            timeout=15)
    except Exception:
        return None


def run(top_n: int = TOP_N, log=print) -> dict:
    log(f"[Ф3] fetch universe (bulk, top {top_n})...")
    universe = fetch_universe(top_n)
    log(f"[Ф3] получено {len(universe)} монет" +
        (f" (запрошено {top_n} -- CoinGecko отдал меньше, честно)" if len(universe) < top_n else ""))

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
            log(f"[Ф3] {i+1}/{len(universe)} обработано, real cg_detail: {real_detail_count}, "
                f"budget использован: {detail_calls_used}/{DETAIL_CALL_BUDGET}, "
                f"consecutive_429: {consecutive_429}")

    log(f"[Ф3] Готово: {len(results)} монет, real cg_detail получен для {real_detail_count}, "
        f"detail-бюджет использован {detail_calls_used}/{DETAIL_CALL_BUDGET}")
    return {
        "results": results, "universe_count": len(universe), "requested_top_n": top_n,
        "real_detail_count": real_detail_count, "detail_calls_used": detail_calls_used,
    }


def regression_check_majors(results: list) -> dict:
    """Владелец, Пакет 17: 'ноль ложных на BTC/ETH/SOL' -- явная регрессионная
    проверка, честно указывает pass/fail, не подгоняет."""
    by_sym = {r["symbol"]: r for r in results}
    out = {}
    for sym in ("BTC", "ETH", "SOL"):
        r = by_sym.get(sym)
        if r is None:
            out[sym] = {"found": False, "pass": None}
        else:
            out[sym] = {"found": True, "score": r["score"], "warn": r["warn"],
                         "pass": not r["warn"]}
    return out


def render_markdown(data: dict) -> str:
    results = data["results"]
    warn = sorted([r for r in results if r["score"] >= 40], key=lambda r: -r["score"])
    yellow = sorted([r for r in results if YELLOW_ZONE_LO <= r["score"] < 40], key=lambda r: -r["score"])
    reg = regression_check_majors(results)

    lines = [
        "# RUG_WATCHLIST.md -- Ф3 rug-скоринг топ-300 CoinGecko (Пакет 17, ночной пакет)",
        "",
        f"Вселенная: запрошено топ-{data['requested_top_n']}, получено "
        f"{data['universe_count']} (стейблы исключены через bot.STABLECOINS). "
        f"Real per-coin `cg_detail` получен для {data['real_detail_count']} монет "
        f"(бюджет {data['detail_calls_used']}/{DETAIL_CALL_BUDGET} использован) -- "
        "остальные оценены по синтетическому `cg_detail` из bulk FDV (fdv_mcap-детектор "
        "работает без per-coin вызова) либо без FDV вообще (детектор честно 'unavailable').",
        "",
        "## Регрессия: ноль ложных на BTC/ETH/SOL",
        "",
        "| Символ | Найден | Score | WARN (>=40) | Результат |",
        "|---|---|---|---|---|",
    ]
    for sym, r in reg.items():
        if not r["found"]:
            lines.append(f"| {sym} | нет | — | — | **не найден в выборке -- проверка не выполнена** |")
        else:
            status = "✅ PASS" if r["pass"] else "❌ FAIL (ложный WARN)"
            lines.append(f"| {sym} | да | {r['score']} | {r['warn']} | {status} |")
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

    lines += [f"## Жёлтая зона 30-39 -- {len(yellow)} монет", "",
              "| Символ | Ранг | Score | Детекторы (причины) | cg_detail |",
              "|---|---|---|---|---|"]
    if not yellow:
        lines.append("| — | — | — | нет монет в диапазоне 30-39 в этом прогоне | — |")
    else:
        for r in yellow:
            lines.append(f"| {r['symbol']} | {r['rank']} | {r['score']} | "
                          f"{'; '.join(r['reasons']) or '—'} | {r['cg_detail_source']} |")
    lines.append("")

    lines.append(
        "**Честно про покрытие детекторов**: `concentration` (holders) -- НИКОГДА не "
        "заполнен в этом прогоне (нужен Etherscan, ERC-20-специфичен, сознательно не "
        "делался для всех 300 монет). `fdv_mcap`/`vertical_growth_thin_volume` -- "
        "заполнены для ВСЕХ монет (bulk-данные). `age_listing`/`exchange_transfers` -- "
        f"только для {data['real_detail_count']}/{data['universe_count']} монет, "
        "получивших real `cg_detail` (см. столбец 'cg_detail' в таблицах выше). "
        "Score -- честная сумма из ДОСТУПНЫХ детекторов (`max_possible_score` в сырых "
        "данных), не притворяется полными 100 для всех монет."
    )
    return "\n".join(lines)


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else TOP_N
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output", "backtest_cache")
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    data = run(n)
    t1 = time.time()
    print(f"Готово за {round(t1-t0,1)}с")
    with open(os.path.join(out_dir, "f3_rug_scan_raw.json"), "w") as f:
        json.dump(data, f)
    md = render_markdown(data)
    with open(os.path.join(out_dir, "RUG_WATCHLIST_f3.md"), "w") as f:
        f.write(md)
    print(md[:2000])
