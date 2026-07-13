"""
Пакет 16 -- SUMMER_SPOT_PLAN.xlsx: летний спот-ранжир под сценарий владельца
(отскок -> плавное снижение -> летнее дно альтов).

Владелец, 2026-07-13: "handoff §15" как отдельный документ НЕ найден в репозитории
(проверено -- см. отчёт разведки в диалоге), методология ниже строится напрямую по
критериям из задачи Пакета 16 (владелец подтвердил: "работать по критериям из
вашего сообщения"), не из внешнего документа -- честно фиксирую источник.

Запуск: python3 tools/summer_spot_plan.py [--top N] [--out PATH]
Требует: pip install openpyxl requests (openpyxl НЕ в requirements.txt -- как
pytest, не нужен в проде на Railway, только для локальной генерации отчёта).

Источники данных (все бесплатные, см. RUG_WATCHLIST.md -- урок о CoinGecko free-
tier rate-limit катастрофе на per-coin /coins/{id} вызовах):
- CoinGecko /coins/markets, ОДИН bulk-вызов на topN монет (price/mcap/volume/rank/
  FDV/ATH%/7d/30d -- всё это уже есть в bulk-ответе, per-coin /coins/{id} НЕ
  вызывается вообще, поэтому катастрофа RUG_WATCHLIST.md здесь не повторяется).
- Binance klines (bot.get_binance_ohlc) -- дневные свечи 180д на монету, бесплатно,
  не CoinGecko-лимитировано: даёт И VRVP-эвristику, И честный 90д-импульс (bulk
  CoinGecko markets НЕ отдаёт 90д, только 1h/24h/7d/30d/14d/200d/1y -- см. docstring
  bot._fetch_coingecko_markets: "percent_change_90d" там честно захардкожен в 0.0).
- DeFiLlama /protocols + /overview/fees -- ДВА bulk-вызова НА ВЕСЬ прогон (не на
  монету), бесплатно, TVL/выручка по протоколам с совпадающим symbol/name.
- rug_radar.compute_rug_risk() -- БЕЗ cg_detail (та самая переменная, что убила
  RUG_WATCHLIST.md на per-coin /coins/{id}); вместо этого синтетический cg_detail
  собирается из уже полученного bulk FDV -- fdv_mcap-детектор работает без единого
  доп. HTTP-вызова. concentration/exchange_transfers/age_listing детекторы честно
  "unavailable" (нет holders_data/transfer_data/age -- это НЕ ошибка, это же самое
  поведение, что и everywhere else в проекте без Etherscan-ключа).
- level_watch.load_watch_zones() -- бонус "зона Королева" (LONG-зона в
  journal/watch_zones.json).
- journal/spot_plans.json -- AVAX/SUI спот-планы владельца ИСПОЛЬЗУЮТСЯ КАК ЕСТЬ
  (не пересчитываются структурно), остальной топ-20 строит лестницу через
  ta_extra.build_trade_from_structure() на живых Binance-свечах.

Скоринг -- полностью прозрачная аддитивная формула (см. score_coin()), документируется
на титульном листе XLSX, чтобы владелец мог проверить/оспорить каждую компоненту.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import requests

import bot
import level_watch
import rug_radar
import ta_extra

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPOT_PLANS_PATH = os.path.join(REPO_ROOT, "journal", "spot_plans.json")

TOP_N_DEFAULT = 150
TOP20_N = 20
BINANCE_PACE_SEC = 0.15   # пауза между Binance-запросами (klines) -- вежливость, не CoinGecko-лимит
DEFILLAMA_TIMEOUT = 20

# Ярусы по прямому указанию владельца (Пакет 16, п.3)
TIER_MAJORS = {"BTC", "ETH", "SOL"}
TIER_QUALITY = {"AAVE", "UNI", "LINK", "MORPHO", "ENA"}
TIER_BETA_NAMED = {"SUI", "AVAX", "WLD", "JASMY"}
TIER_TARGET_PCT = {"majors": 50, "quality": 35, "beta": 15}

DEFILLAMA_TVL_EXCLUDE_CATEGORIES = {"Bridge", "CEX", "Cross Chain Bridge", "Chain"}
DEFILLAMA_FEES_EXCLUDE_CATEGORIES = {"Bridge", "CEX", "Developer Tools", "DAO Service Provider"}


# ── 1. Вселенная монет (CoinGecko bulk, один вызов) ─────────────────────────

def fetch_universe(top_n: int = TOP_N_DEFAULT) -> list:
    """CoinGecko /coins/markets, один bulk-запрос (per_page=top_n, page=1) --
    исключает stablecoin/wrapped через bot.STABLECOINS (тот же список, что и
    остальной бот, покрывает и стейблы, и обёрнутые активы: WBTC/WETH/STETH/...).
    Честно возвращает меньше top_n, если CoinGecko отдал 429 (см. bot._cg_get) --
    не выдумывает недостающие монеты."""
    params = {
        "vs_currency": "usd", "order": "market_cap_desc",
        "per_page": min(top_n, 250), "page": 1,
        "price_change_percentage": "1h,24h,7d,30d",
    }
    try:
        data = bot._cg_get("https://api.coingecko.com/api/v3/coins/markets", params=params, timeout=20)
    except Exception as e:
        print(f"[UNIVERSE] CoinGecko markets FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return []
    if not data:
        return []
    out = []
    for d in data:
        sym = (d.get("symbol") or "").upper()
        if not sym or sym in bot.STABLECOINS:
            continue
        out.append({
            "symbol": sym, "slug": d.get("id", sym.lower()), "name": d.get("name", sym),
            "rank": d.get("market_cap_rank") or 9999,
            "price": d.get("current_price", 0) or 0,
            "market_cap": d.get("market_cap", 0) or 0,
            "fdv": d.get("fully_diluted_valuation"),
            "volume_24h": d.get("total_volume", 0) or 0,
            "ath": d.get("ath"),
            "ath_change_pct": d.get("ath_change_percentage"),
            "ch_1h": d.get("price_change_percentage_1h_in_currency"),
            "ch_24h": d.get("price_change_percentage_24h_in_currency", d.get("price_change_percentage_24h")),
            "ch_7d": d.get("price_change_percentage_7d_in_currency"),
            "ch_30d": d.get("price_change_percentage_30d_in_currency"),
        })
    return out[:top_n]


# ── 2. Binance daily klines -> 90д импульс + VRVP-эвристика ────────────────

def fetch_binance_profile(symbol: str) -> dict:
    """180 дневных свечей с Binance (бесплатно, не CoinGecko) -> честный 90д-импульс
    (bulk CoinGecko его не отдаёт -- см. докстринг модуля) + упрощённая VRVP-эвристика
    (объёмный профиль по 20 ценовым бакетам, НЕ биржевой order-book VRVP -- честно
    маркируется как эвристика на дневных close/volume, не тиковых данных)."""
    try:
        candles = bot.get_binance_ohlc(symbol, interval="1d", limit=180)
    except Exception as e:
        return {"ok": False, "reason": f"Binance klines error: {type(e).__name__}: {e}"}
    if not candles or len(candles) < 30:
        return {"ok": False, "reason": "Binance: нет пары или недостаточно свечей"}

    closes = [c["close"] for c in candles]
    now_price = closes[-1]
    ch_90d = None
    if len(closes) >= 91:
        ref = closes[-91]
        if ref:
            ch_90d = (now_price - ref) / ref * 100

    # VRVP-эвристика: 20 ценовых бакетов по всему диапазону 180д, объём накапливается
    # в бакет по close-цене свечи (упрощение -- честная эвристика на close, не на
    # полном intra-candle распределении цены).
    lo, hi = min(closes), max(closes)
    vrvp_zone = None
    if hi > lo:
        n_buckets = 20
        width = (hi - lo) / n_buckets
        bucket_vol = [0.0] * n_buckets
        for c in candles:
            idx = min(n_buckets - 1, int((c["close"] - lo) / width))
            bucket_vol[idx] += c.get("vol", 0) or 0
        max_idx = max(range(n_buckets), key=lambda i: bucket_vol[i])
        b_lo = lo + max_idx * width
        b_hi = b_lo + width
        vrvp_zone = {
            "lo": b_lo, "hi": b_hi,
            "price_inside": b_lo <= now_price <= b_hi,
            "price_below_pct": ((b_lo - now_price) / now_price * 100) if now_price < b_lo else 0,
        }

    return {"ok": True, "ch_90d": ch_90d, "vrvp": vrvp_zone, "candles": candles}


# ── 3. DeFiLlama -- TVL и выручка (2 bulk-вызова на весь прогон) ───────────

def fetch_defillama_protocols() -> list:
    try:
        r = requests.get("https://api.llama.fi/protocols", timeout=DEFILLAMA_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[DEFILLAMA] /protocols FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return []


def fetch_defillama_fees() -> list:
    try:
        r = requests.get(
            "https://api.llama.fi/overview/fees"
            "?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyRevenue",
            timeout=DEFILLAMA_TIMEOUT)
        r.raise_for_status()
        return r.json().get("protocols", [])
    except Exception as e:
        print(f"[DEFILLAMA] /overview/fees FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return []


def build_tvl_revenue_map(universe: list, protocols: list, fees: list) -> dict:
    """symbol -> {"tvl_usd": float|None, "revenue_30d_usd": float|None}. TVL --
    сумма всех протоколов DeFiLlama с совпадающим symbol, ИСКЛЮЧАЯ категории
    Bridge/CEX/Chain (иначе, например, AVAX получил бы TVL моста как "TVL AVAX" --
    вводящее в заблуждение число, не то же самое, что TVL DeFi-протокола). Выручка
    -- сумма total30d протоколов DeFiLlama fees API с совпадением по имени (fees API
    не отдаёт symbol, только name/displayName -- см. проверку в разведке пакета)."""
    result = {}
    for coin in universe:
        sym = coin["symbol"]
        name_lower = coin["name"].lower()

        tvl_matches = [p for p in protocols
                        if (p.get("symbol") or "").upper() == sym
                        and p.get("category") not in DEFILLAMA_TVL_EXCLUDE_CATEGORIES
                        and isinstance(p.get("tvl"), (int, float)) and p.get("tvl", 0) > 0]
        tvl_usd = sum(p["tvl"] for p in tvl_matches) if tvl_matches else None

        fee_matches = [p for p in fees
                        if name_lower in (p.get("name") or "").lower()
                        and p.get("category") not in DEFILLAMA_FEES_EXCLUDE_CATEGORIES
                        and isinstance(p.get("total30d"), (int, float)) and p.get("total30d", 0) > 0]
        revenue_30d = sum(p["total30d"] for p in fee_matches) if fee_matches else None

        result[sym] = {"tvl_usd": tvl_usd, "revenue_30d_usd": revenue_30d,
                        "applicable": bool(tvl_matches or fee_matches)}
    return result


# ── 4. Rug-скор без per-coin CoinGecko detail-вызовов ───────────────────────

def compute_rug_score(coin: dict) -> dict:
    """rug_radar.compute_rug_risk() без cg_detail-фетча (тот самый вызов, что убил
    RUG_WATCHLIST.md на 429). Синтетический cg_detail из уже полученного bulk FDV
    включает fdv_mcap-детектор без единого доп. HTTP-запроса; concentration/
    exchange_transfers/age_listing честно недоступны (нет holders/transfer/age
    данных в этом прогоне) -- не считаются в max_possible_score, не искажают итог."""
    coin_shape = {"quote": {"USDT": {
        "market_cap": coin["market_cap"], "volume_24h": coin["volume_24h"],
        "percent_change_30d": coin["ch_30d"],
    }}}
    cg_detail = None
    if coin.get("fdv"):
        cg_detail = {"market_data": {"fully_diluted_valuation": {"usd": coin["fdv"]}}}
    return rug_radar.compute_rug_risk(coin["symbol"], coin_shape, cg_detail=cg_detail)


# ── 5. Зона Королева (watch_zones.json) -- бонус ────────────────────────────

def has_korolev_long_zone(symbol: str, watch_zones: dict) -> bool:
    entries = watch_zones.get(f"{symbol}USDT", [])
    return any(e.get("side") == "LONG" for e in entries)


# ── 6. Скоринг -- полностью прозрачная аддитивная формула ──────────────────

def score_coin(coin: dict, profile: dict, tvl_rev: dict, rug: dict, korolev_bonus: bool) -> dict:
    """База 50, аддитивные дельты -- каждая компонента даёт (delta, explanation),
    как Rocket Score в fa_engine.py (тот же принцип прозрачности). Сценарий
    владельца -- ЛЕТНЕЕ ДНО альтов (отскок -> снижение -> дно), поэтому формула
    contrarian: глубокая просадка от ATH + всё ещё отрицательный 30д/90д импульс
    (монета в коррекционной/базовой фазе, не в разгоне) оцениваются ВЫШЕ, а не
    ниже -- это ранжир для набора позиции у дна, не momentum-скринер прорывов."""
    score = 50.0
    factors = []

    def add(delta, label):
        nonlocal score
        score += delta
        factors.append((label, delta))

    ath_pct = coin.get("ath_change_pct")
    if ath_pct is not None:
        if -90 <= ath_pct <= -40:
            add(12, f"Просадка от ATH {ath_pct:.0f}% -- зона интереса (не экстрим)")
        elif ath_pct < -90:
            add(4, f"Просадка от ATH {ath_pct:.0f}% -- экстремальная, риск структурной деградации")
        elif -40 < ath_pct <= -15:
            add(4, f"Просадка от ATH {ath_pct:.0f}% -- умеренная")
        else:
            add(-6, f"Просадка от ATH {ath_pct:.0f}% -- близко к хаям, не дно")
    else:
        factors.append(("ATH% -- н/д (CoinGecko не отдал)", 0))

    ch7 = coin.get("ch_7d")
    if ch7 is not None:
        if 0 <= ch7 <= 12:
            add(8, f"7д {ch7:+.1f}% -- признак раннего отскока")
        elif ch7 > 12:
            add(-4, f"7д {ch7:+.1f}% -- уже сильный импульс, риск догонять")
        else:
            add(-2, f"7д {ch7:+.1f}% -- ещё падает")
    else:
        factors.append(("7д импульс -- н/д", 0))

    ch30 = coin.get("ch_30d")
    if ch30 is not None:
        if ch30 < 0:
            add(6, f"30д {ch30:+.1f}% -- в коррекционной фазе (соответствует сценарию)")
        else:
            add(-3, f"30д {ch30:+.1f}% -- уже растёт 30д, не похоже на дно")
    else:
        factors.append(("30д импульс -- н/д", 0))

    ch90 = profile.get("ch_90d") if profile.get("ok") else None
    if ch90 is not None:
        if ch90 < -20:
            add(8, f"90д {ch90:+.1f}% -- продолжительная коррекция, ближе к циклическому дну")
        elif ch90 < 0:
            add(3, f"90д {ch90:+.1f}% -- слабоотрицательный тренд")
        else:
            add(-4, f"90д {ch90:+.1f}% -- растёт на 90д горизонте, не дно")
    else:
        factors.append(("90д импульс -- н/д (нет пары на Binance или мало истории)", 0))

    vrvp = profile.get("vrvp") if profile.get("ok") else None
    if vrvp:
        if vrvp["price_inside"]:
            add(10, "VRVP: цена внутри зоны максимального объёма 180д (зона накопления)")
        elif 0 < vrvp["price_below_pct"] <= 15:
            add(5, f"VRVP: цена в {vrvp['price_below_pct']:.0f}% ниже зоны макс. объёма -- рядом")
        else:
            factors.append(("VRVP: цена вне зоны макс. объёма", 0))
    else:
        factors.append(("VRVP -- н/д", 0))

    tvl_info = tvl_rev.get(coin["symbol"], {})
    if tvl_info.get("applicable"):
        tvl = tvl_info.get("tvl_usd")
        if tvl and coin["market_cap"] > 0:
            mcap_tvl_ratio = coin["market_cap"] / tvl
            if mcap_tvl_ratio < 3:
                add(6, f"MCap/TVL {mcap_tvl_ratio:.1f}x -- капитализация умеренна к TVL")
            elif mcap_tvl_ratio > 15:
                add(-4, f"MCap/TVL {mcap_tvl_ratio:.1f}x -- капитализация сильно опережает TVL")
            else:
                factors.append((f"MCap/TVL {mcap_tvl_ratio:.1f}x -- нейтрально", 0))
        if tvl_info.get("revenue_30d_usd"):
            add(5, f"Реальная выручка протокола за 30д: ${tvl_info['revenue_30d_usd']:,.0f}")
    else:
        factors.append(("TVL/выручка -- н/д (не DeFi-протокол или не найден в DeFiLlama)", 0))

    rug_score = rug.get("score", 0)
    if rug_score > 0:
        add(-rug_score * 0.3, f"Rug-скор {rug_score}/100 -- штраф пропорционально риску")

    if coin.get("fdv") and coin["market_cap"] > 0:
        fdv_mcap = coin["fdv"] / coin["market_cap"]
        if fdv_mcap <= 1.3:
            add(5, f"FDV/MCap {fdv_mcap:.2f}x -- малый навес будущей эмиссии")
        elif fdv_mcap >= 3:
            add(-8, f"FDV/MCap {fdv_mcap:.2f}x -- большой навес будущей эмиссии/разлоков")
        else:
            factors.append((f"FDV/MCap {fdv_mcap:.2f}x -- умеренно", 0))
    else:
        factors.append(("FDV/MCap -- н/д", 0))

    if coin["market_cap"] > 0:
        vol_mcap = coin["volume_24h"] / coin["market_cap"] * 100
        if 1 <= vol_mcap <= 15:
            add(5, f"Объём/MCap {vol_mcap:.1f}% -- здоровая ликвидность")
        elif vol_mcap < 0.3:
            add(-5, f"Объём/MCap {vol_mcap:.1f}% -- низкая ликвидность")
        elif vol_mcap > 40:
            add(-3, f"Объём/MCap {vol_mcap:.1f}% -- аномально высокий, риск манипуляции/шума")
        else:
            factors.append((f"Объём/MCap {vol_mcap:.1f}% -- нейтрально", 0))

    if korolev_bonus:
        add(6, "Есть активная LONG-зона Королева в watch_zones.json")

    return {"score": round(score, 1), "factors": factors, "rug": rug}


# ── 7. Тир (ярус) ────────────────────────────────────────────────────────

def assign_tier(symbol: str) -> str:
    if symbol in TIER_MAJORS:
        return "majors"
    if symbol in TIER_QUALITY:
        return "quality"
    if symbol in TIER_BETA_NAMED:
        return "beta"
    return "прочие"


# ── 8. DCA-лестница для топ-20 ──────────────────────────────────────────────

def load_spot_plans() -> dict:
    if not os.path.exists(SPOT_PLANS_PATH):
        return {}
    with open(SPOT_PLANS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def build_ladder_for_coin(coin: dict, profile: dict, spot_plans: dict) -> dict:
    """AVAX/SUI -- план владельца из journal/spot_plans.json КАК ЕСТЬ (не
    пересчитывается). Остальные -- структурная лестница через
    ta_extra.build_trade_from_structure() на живых Binance 1h/4h/1d свечах
    (direction="long" всегда -- это спот-план, не шорт)."""
    sym = coin["symbol"]
    plan_key = f"{sym}USDT"
    if plan_key in spot_plans:
        p = spot_plans[plan_key]
        return {
            "source": "владелец (journal/spot_plans.json)",
            "zone": p["zone"], "ladder": p["ladder"], "sl": p["sl"],
            "tp": p.get("tp"),
            "invalidation": p["invalidation"], "note": p.get("note", ""),
        }

    price = coin["price"]
    if not price or not profile.get("ok"):
        return {"source": "н/д", "zone": None, "ladder": None, "sl": None, "tp": None,
                "invalidation": None, "note": "нет структурных данных (Binance пара отсутствует)"}
    try:
        c1h = bot.get_binance_ohlc(sym, "1h", 100) or []
        c4h = bot.get_binance_ohlc(sym, "4h", 200) or []
        c1d = profile.get("candles") or []
        ema_ctx = ta_extra.ema_context(c1h, c4h)
        zones = ta_extra.find_sr_zones(c1h, c4h, c1d, price, ema_ctx=ema_ctx)
        trade = ta_extra.build_trade_from_structure("long", price, zones)
    except Exception as e:
        return {"source": "н/д", "zone": None, "ladder": None, "sl": None, "tp": None,
                "invalidation": None, "note": f"структурный расчёт не удался: {type(e).__name__}: {e}"}
    if not trade:
        return {"source": "н/д", "zone": None, "ladder": None, "sl": None, "tp": None,
                "invalidation": None, "note": "не найдено зоны поддержки для входа"}
    return {
        "source": "расчёт (ta_extra.build_trade_from_structure, живые Binance-свечи)",
        "zone": {"lo": trade["entry3"], "hi": trade["entry1"]},
        "ladder": [
            {"price": trade["entry1"], "pct": 50},
            {"price": trade["entry2"], "pct": 30},
            {"price": trade["entry3"], "pct": 20},
        ],
        "sl": trade["sl"],
        "tp": trade.get("tp1"),
        "invalidation": f"дневное закрытие ниже {ta_extra.smart_round(trade['sl'])}",
        "note": f"структурная зона, {trade['entry_zone']['touches']} касаний" if trade.get("entry_zone") else "",
    }


# ── 9. Основной прогон ──────────────────────────────────────────────────────

def run(top_n: int = TOP_N_DEFAULT, verbose: bool = True) -> dict:
    def log(msg):
        if verbose:
            print(msg, file=sys.stderr)

    log(f"[1/6] CoinGecko universe (top {top_n})...")
    universe = fetch_universe(top_n)
    log(f"       получено {len(universe)} монет" + (f" (запрошено {top_n} -- CoinGecko отдал меньше, честно)" if len(universe) < top_n else ""))

    log("[2/6] DeFiLlama TVL/revenue (2 bulk-вызова)...")
    protocols = fetch_defillama_protocols()
    fees = fetch_defillama_fees()
    tvl_rev = build_tvl_revenue_map(universe, protocols, fees)
    log(f"       protocols={len(protocols)} fees={len(fees)}")

    log("[3/6] level_watch.load_watch_zones()...")
    watch_zones = level_watch.load_watch_zones()

    log(f"[4/6] Binance-профиль (VRVP + 90д) на {len(universe)} монет, пауза {BINANCE_PACE_SEC}с...")
    profiles = {}
    for i, coin in enumerate(universe):
        profiles[coin["symbol"]] = fetch_binance_profile(coin["symbol"])
        if (i + 1) % 25 == 0:
            log(f"       {i+1}/{len(universe)}...")
        time.sleep(BINANCE_PACE_SEC)

    log("[5/6] Rug-скор + итоговый скоринг...")
    results = []
    rug_excluded = []
    for coin in universe:
        rug = compute_rug_score(coin)
        if rug.get("warn"):
            rug_excluded.append({"symbol": coin["symbol"], "name": coin["name"],
                                   "score": rug["score"], "reasons": rug.get("reasons", [])})
            continue
        profile = profiles.get(coin["symbol"], {"ok": False})
        korolev = has_korolev_long_zone(coin["symbol"], watch_zones)
        scored = score_coin(coin, profile, tvl_rev, rug, korolev)
        results.append({"coin": coin, "profile": profile, "tvl_rev": tvl_rev.get(coin["symbol"], {}),
                          "korolev": korolev, **scored,
                          "tier": assign_tier(coin["symbol"])})

    results.sort(key=lambda r: r["score"], reverse=True)
    for i, r in enumerate(results, 1):
        r["final_rank"] = i

    log("[6/6] DCA-лестницы для топ-20...")
    spot_plans = load_spot_plans()
    top20 = results[:TOP20_N]
    # Владелец явно попросил включить спот-планы владельца (AVAX/SUI/AAVE) "как есть"
    # -- если такая монета честно не попала в топ-20 по общему скору (пример: AVAX
    # ранг 24, найдено на живом прогоне 150 монет), лист DCA всё равно обязан её
    # показать -- иначе кураторский план владельца молча теряется. Добавляется
    # ДОПОЛНИТЕЛЬНО к топ-20, не вместо них, с честным рангом (не переставляется
    # в топ-20 по факту добавления).
    top20_symbols = {r["coin"]["symbol"] for r in top20}
    for r in results[TOP20_N:]:
        sym = r["coin"]["symbol"]
        if f"{sym}USDT" in spot_plans and sym not in top20_symbols:
            top20.append(r)
            top20_symbols.add(sym)
            log(f"       + {sym} (ранг {r['final_rank']}, вне топ-{TOP20_N}) -- добавлен, есть план владельца")
    for r in top20:
        r["ladder_info"] = build_ladder_for_coin(r["coin"], r["profile"], spot_plans)

    log(f"Готово: {len(results)} в ранжире, {len(rug_excluded)} исключено по rug-скору >= {rug_radar.RUG_RISK_WARN_THRESHOLD}.")
    return {
        "results": results, "rug_excluded": rug_excluded, "top20": top20,
        "universe_count": len(universe), "requested_top_n": top_n,
        "generated_at": bot.now_utc3() if hasattr(bot, "now_utc3") else time.strftime("%Y-%m-%d %H:%M"),
    }


def main():
    ap = argparse.ArgumentParser(description="Пакет 16: SUMMER_SPOT_PLAN.xlsx генератор")
    ap.add_argument("--top", type=int, default=TOP_N_DEFAULT)
    ap.add_argument("--out", type=str, default=os.path.join(REPO_ROOT, "output", "SUMMER_SPOT_PLAN.xlsx"))
    args = ap.parse_args()

    data = run(top_n=args.top)

    from summer_spot_plan_xlsx import write_workbook
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    write_workbook(data, args.out)
    print(f"Готово: {args.out}")


if __name__ == "__main__":
    main()
