"""
Пакет 11 Ф3 (owner-запрос): массовый rug-скоринг топ-300 CoinGecko через
rug_radar.compute_rug_risk() с живым Etherscan. Одноразовый скрипт для
ночного анализа -- запускается через `railway run python3 rug_mass_scan.py`
(нужны реальные ETHERSCAN_API_KEY/GITHUB_TOKEN env vars). Read-only: не
меняет ничего боевого, только читает и печатает результат в stdout + пишет
JSON-дамп для последующей сборки RUG_WATCHLIST.md.

Троттлинг Etherscan уже встроен в etherscan_whale.py (_ETHERSCAN_MIN_INTERVAL
= 0.25с, глобальный лок) -- этот скрипт его не дублирует, только считает
фактическое количество вызовов по счётчику, который сам патчит внутрь модуля.
"""
import json
import sys
import time

import bot
import rug_radar
import etherscan_whale

# --- патчим fetch_token_transfers, чтобы посчитать реальное число живых вызовов ---
_call_count = {"n": 0}
_orig_fetch_token_transfers = etherscan_whale.fetch_token_transfers


def _counted_fetch_token_transfers(*args, **kwargs):
    _call_count["n"] += 1
    return _orig_fetch_token_transfers(*args, **kwargs)


etherscan_whale.fetch_token_transfers = _counted_fetch_token_transfers

BUDGET = 30000
TARGET_N = 300


def get_cg_detail(sym):
    try:
        slug = bot._cg_slug(sym)
        data = bot._cg_get(f"https://api.coingecko.com/api/v3/coins/{slug}",
                            params={"localization": "false", "tickers": "true",
                                    "community_data": "false", "developer_data": "false"},
                            timeout=8)
        return data or {}
    except Exception as e:
        return {"_error": str(e)}


def main():
    print("Fetching top coin list...", flush=True)
    coins = bot.get_top500()
    coins = coins[:TARGET_N]
    print(f"Got {len(coins)} coins (target {TARGET_N})", flush=True)

    results = []
    errors = []
    t0 = time.time()

    for i, coin in enumerate(coins):
        sym = coin.get("symbol")
        if _call_count["n"] >= BUDGET:
            print(f"BUDGET EXCEEDED at {i}/{len(coins)} coins, {_call_count['n']} calls -- stopping honestly", flush=True)
            break
        try:
            q = coin.get("quote", {}).get("USDT", {})
            price = q.get("price", 0) or 0
            cg_detail = get_cg_detail(sym)
            if cg_detail.get("_error"):
                errors.append({"symbol": sym, "stage": "cg_detail", "error": cg_detail["_error"]})
                cg_detail = None
            transfer_data = None
            if cg_detail and price:
                try:
                    transfer_data = etherscan_whale.fetch_transfer_data(cg_detail, price) or None
                except Exception as e:
                    errors.append({"symbol": sym, "stage": "transfer_data", "error": str(e)})
            risk = rug_radar.compute_rug_risk(sym, coin, cg_detail=cg_detail, transfer_data=transfer_data)
            results.append({"symbol": sym, "risk": risk})
        except Exception as e:
            errors.append({"symbol": sym, "stage": "compute_rug_risk", "error": str(e)})
        if (i + 1) % 25 == 0:
            elapsed = time.time() - t0
            print(f"  progress: {i+1}/{len(coins)} coins, {_call_count['n']} Etherscan calls, {elapsed:.0f}s elapsed", flush=True)
            # инкрементальный дамп -- чтобы partial-kill не терял уже посчитанное
            with open("rug_scan_dump.json", "w") as f:
                json.dump({"results": results, "errors": errors,
                           "etherscan_calls": _call_count["n"],
                           "coins_requested": len(coins), "coins_scored": len(results),
                           "partial": True}, f)

    elapsed = time.time() - t0
    print(f"\nDONE: {len(results)} scored, {len(errors)} errors, "
          f"{_call_count['n']} Etherscan calls, {elapsed:.0f}s total", flush=True)

    with open("rug_scan_dump.json", "w") as f:
        json.dump({"results": results, "errors": errors,
                   "etherscan_calls": _call_count["n"],
                   "coins_requested": len(coins), "coins_scored": len(results)}, f)
    print("Dumped to rug_scan_dump.json", flush=True)


if __name__ == "__main__":
    main()
