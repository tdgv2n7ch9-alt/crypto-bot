"""
BEST TRADE — Фаза C, каркас on-chain метрик («Пакетный ритм» пакет 2, М5).

ЧЕСТНАЯ НАХОДКА (Truth Protocol -- проверено WebFetch на docs.glassnode.com/
basic-api/api и studio.glassnode.com/pricing, 2026-07-11): **у Glassnode НЕТ
по-настоящему бесплатного API-тира.**
- docs.glassnode.com/basic-api/api: программный доступ начинается с Advanced
  ($49/мес) -- даёт только ограниченный "Light API" (14 дней истории, только
  дневное разрешение (1d), 50 запросов/день, без bulk-эндпоинтов).
- studio.glassnode.com/pricing: та же страница утверждает, что Advanced НЕ
  включает API вовсе -- доступ только на Professional (custom pricing,
  "опциональный add-on").
Источники расходятся в деталях (какой именно платный тир открывает API), но
СОГЛАСНЫ в главном: тира с $0 и программным доступом нет ни на одной странице.
Это расходится с посылкой задачи "Glassnode free tier" -- честно зафиксировано,
не подгоняю под ожидание.

Возможная бесплатная альтернатива (проверена частично, НЕ до конца, НЕ
интегрирована): BGeometrics (bitcoin-data.com / portal.bitcoin-data.com) --
подтверждённый $0/мес тир (10 запросов/час, 15/день, история 4 года), сайт
заявляет SOPR/MVRV/NVT в своём API, но НЕ подтверждено, какие метрики именно
входят в БЕСПЛАТНЫЙ тир против платных (Advanced+) -- страница пейволла явно
показывает "Data M2" только с Advanced, про SOPR/MVRV/NVT/Puell/LTH-STH по
тирам умалчивает. Puell Multiple и LTH/STH supply вообще не упомянуты на
главной странице API. Решение по источнику -- за владельцем (Уровень 3).

Этот модуль -- ТОЛЬКО каркас: конфигурация источника через переменные
окружения (не хардкод), честное "источник не настроен" состояние для
карточки On-Chain (тот же принцип честности, что уже был у прежней заглушки
"Раздел в разработке — реального источника данных... пока нет"), хук для
shadow-скоринга (аддитивно, боевой скоринг НИГДЕ не трогает). Реальный фетч
данных НЕ подключён -- ждёт решения владельца по источнику.
"""
import os
import requests

SUPPORTED_METRICS = ("sopr", "mvrv", "nvt", "puell", "lth_sth_supply")

ONCHAIN_DATA_SOURCE = os.getenv("ONCHAIN_DATA_SOURCE", "").strip()  # "" -- не настроен
ONCHAIN_API_KEY = os.getenv("ONCHAIN_API_KEY", "").strip()

_KNOWN_SOURCES = {
    "glassnode": "требует платной подписки (Advanced $49/мес или Professional) -- "
                 "не подключён без явного решения владельца о бюджете",
    "bgeometrics": "вероятно бесплатен (подтверждён $0/мес тир), но покрытие "
                   "SOPR/MVRV/NVT/Puell/LTH-STH по тирам не до конца проверено -- "
                   "фетчер не реализован в этом пакете",
}


def is_configured() -> bool:
    """Честно: настроен -- значит указан И известный источник, И ключ. Пустая
    строка (дефолт) -- НЕ настроен, не притворяемся, что что-то есть."""
    return bool(ONCHAIN_DATA_SOURCE and ONCHAIN_API_KEY)


def get_onchain_metrics(symbol: str = "BTC") -> dict:
    """Возвращает {"ok": False, "reason": ...} честно, пока источник не
    настроен и фетчер не реализован -- НЕ выдумывает нули/цифры вместо данных
    (тот же принцип честности, что вся остальная работа этого пакета).
    Каркас готов принять реальный фетчер после решения владельца по источнику
    -- эта функция единственная точка входа, которую нужно будет заменить."""
    if not ONCHAIN_DATA_SOURCE:
        return {"ok": False, "reason": "источник on-chain данных не настроен -- "
                                        "решение по Glassnode (платный) / BGeometrics "
                                        "(вероятно бесплатный, не до конца проверен) / "
                                        "другому источнику ждёт владельца"}
    if ONCHAIN_DATA_SOURCE not in _KNOWN_SOURCES:
        return {"ok": False, "reason": f"источник '{ONCHAIN_DATA_SOURCE}' не распознан "
                                        f"(известны: {', '.join(_KNOWN_SOURCES)})"}
    if not ONCHAIN_API_KEY:
        return {"ok": False, "reason": f"источник '{ONCHAIN_DATA_SOURCE}' задан, но "
                                        f"ONCHAIN_API_KEY не установлен"}
    # TODO (следующий пакет, после решения владельца по источнику): реальный
    # фетчер для ONCHAIN_DATA_SOURCE. Каркас, не реализация -- см. докстринг модуля.
    return {"ok": False, "reason": f"источник '{ONCHAIN_DATA_SOURCE}' настроен, но "
                                    f"фетчер для него ещё не реализован (каркас Этапа М5)"}


def shadow_score_adjustment(metrics: dict) -> dict:
    """Хук для shadow-скоринга (Фаза C, аддитивно) -- боевой скоринг НИГДЕ не
    читает этот модуль. Пока честно возвращает "нет данных" -- формула
    поправки скоринга по SOPR/MVRV/NVT/Puell/LTH-STH ещё не спроектирована,
    ждёт и решения по источнику, и отдельного шага дизайна формулы."""
    if not metrics.get("ok"):
        return {"available": False, "adjustment": 0, "reason": metrics.get("reason")}
    return {"available": False, "adjustment": 0,
            "reason": "метрики получены, но формула shadow-скоринга ещё не спроектирована"}


## ── Пакет 3 М2: реальные бесплатные источники (не Glassnode) ──
##
## Владелец (Пакет 3 задание): "рынок реальных бесплатных источников:
## blockchain.com charts API, mempool.space, DeFiLlama, alternative.me F&G".
## Каждый эндпоинт проверен ЖИВЬЁМ через curl 2026-07-11 ДО написания кода
## ниже (сырые ответы см. в PROGRESS.md, чекпоинт Пакета 3 М2):
## - https://mempool.space/api/v1/fees/recommended -- 200, JSON с комиссиями
## - https://mempool.space/api/mempool -- 200, count/vsize/total_fee
## - https://mempool.space/api/v1/difficulty-adjustment -- 200
## - https://api.blockchain.info/charts/hash-rate?timespan=2days&format=json -- 200
## - https://api.blockchain.info/charts/difficulty?...  -- 200
## - https://api.blockchain.info/charts/miners-revenue?... -- 200
## - https://api.llama.fi/v2/historicalChainTvl -- 200, глобальный TVL всех сетей
## - https://stablecoins.llama.fi/stablecoincharts/all -- 200, supply стейблкоинов
## - https://api.alternative.me/fng/?limit=1 -- 200 (уже используется в bot.py
##   в других карточках -- здесь отдельный самостоятельный фетч для On-Chain)
## Все НЕ требуют API-ключа. Etherscan (ETH whale-трекинг) -- НЕ в скоупе этого
## пакета (владелец отложил его на следующий пакет ещё в ответах к Пакету 2).
## SOPR/MVRV/NVT/Puell/LTH-STH supply остаются недоступны бесплатно -- см.
## KNOWLEDGE_GAPS.md.

_HTTP_TIMEOUT = 8


def _safe_get_json(url: str, timeout: int = _HTTP_TIMEOUT) -> dict:
    """Общий безопасный GET+JSON -- честный {"ok": False, "reason": ...} при
    любой сетевой/HTTP/парсинг ошибке, никогда не выдумывает данные."""
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return {"ok": True, "data": resp.json()}
    except Exception as e:
        return {"ok": False, "reason": f"{url} -- {type(e).__name__}: {e}"}


def fetch_mempool_fees() -> dict:
    """mempool.space -- рекомендуемые комиссии сети BTC (sat/vB). Бесплатно,
    без ключа -- проверено живьём 2026-07-11."""
    r = _safe_get_json("https://mempool.space/api/v1/fees/recommended")
    if not r["ok"]:
        return r
    d = r["data"]
    return {"ok": True,
            "fastest_sat_vb": d.get("fastestFee"),
            "half_hour_sat_vb": d.get("halfHourFee"),
            "hour_sat_vb": d.get("hourFee"),
            "economy_sat_vb": d.get("economyFee")}


def fetch_mempool_congestion() -> dict:
    """mempool.space -- текущий размер мемпула BTC (кол-во неподтв.
    транзакций, суммарный vsize). Бесплатно, без ключа."""
    r = _safe_get_json("https://mempool.space/api/mempool")
    if not r["ok"]:
        return r
    d = r["data"]
    return {"ok": True, "tx_count": d.get("count"), "vsize_bytes": d.get("vsize")}


def fetch_mempool_difficulty_adjustment() -> dict:
    """mempool.space -- прогресс до следующей корректировки сложности BTC."""
    r = _safe_get_json("https://mempool.space/api/v1/difficulty-adjustment")
    if not r["ok"]:
        return r
    d = r["data"]
    return {"ok": True,
            "progress_pct": d.get("progressPercent"),
            "estimated_change_pct": d.get("difficultyChange"),
            "remaining_blocks": d.get("remainingBlocks")}


def _last_chart_value(chart_response: dict, label: str) -> dict:
    if not chart_response["ok"]:
        return chart_response
    values = chart_response["data"].get("values") or []
    if not values:
        return {"ok": False, "reason": f"{label}: пустой values[] в ответе"}
    return {"ok": True, "value": values[-1].get("y"), "unit": chart_response["data"].get("unit")}


def fetch_blockchain_hashrate() -> dict:
    """blockchain.info charts -- хешрейт сети BTC, последняя точка ряда за
    2 дня. Бесплатно, без ключа. Единица берётся из самого ответа API
    (поле "unit"), не хардкодится."""
    r = _last_chart_value(_safe_get_json(
        "https://api.blockchain.info/charts/hash-rate?timespan=2days&format=json"),
        "blockchain.info hash-rate")
    if not r["ok"]:
        return r
    return {"ok": True, "hashrate": r["value"], "unit": r["unit"]}


def fetch_blockchain_difficulty() -> dict:
    """blockchain.info charts -- текущая сложность сети BTC."""
    r = _last_chart_value(_safe_get_json(
        "https://api.blockchain.info/charts/difficulty?timespan=2days&format=json"),
        "blockchain.info difficulty")
    if not r["ok"]:
        return r
    return {"ok": True, "difficulty": r["value"]}


def fetch_blockchain_miners_revenue() -> dict:
    """blockchain.info charts -- суточный доход майнеров BTC, USD."""
    r = _last_chart_value(_safe_get_json(
        "https://api.blockchain.info/charts/miners-revenue?timespan=2days&format=json"),
        "blockchain.info miners-revenue")
    if not r["ok"]:
        return r
    return {"ok": True, "usd_per_day": r["value"]}


def fetch_defillama_global_tvl() -> dict:
    """DeFiLlama -- суммарный TVL всех сетей (USD), последняя точка ряда.
    Не привязано к конкретному символу -- рынок DeFi в целом."""
    r = _safe_get_json("https://api.llama.fi/v2/historicalChainTvl")
    if not r["ok"]:
        return r
    values = r["data"]
    if not values:
        return {"ok": False, "reason": "DeFiLlama historicalChainTvl: пустой список"}
    return {"ok": True, "tvl_usd": values[-1].get("tvl")}


def fetch_defillama_stablecoins() -> dict:
    """DeFiLlama -- суммарный circulating supply стейблкоинов (USD),
    последняя точка ряда."""
    r = _safe_get_json("https://stablecoins.llama.fi/stablecoincharts/all")
    if not r["ok"]:
        return r
    values = r["data"]
    if not values:
        return {"ok": False, "reason": "DeFiLlama stablecoincharts: пустой список"}
    last = values[-1]
    usd = (last.get("totalCirculatingUSD") or {}).get("peggedUSD")
    return {"ok": True, "stablecoin_supply_usd": usd}


def fetch_defillama_stablecoin_flow_30d() -> dict:
    """DeFiLlama -- изменение суммарного supply стейблкоинов за 30д (EVENT-RADAR
    М5, "stablecoin flows"). Тот же эндпоинт, что fetch_defillama_stablecoins()
    (уже отдаёт полный дневной ряд -- не нужен отдельный запрос), просто берём
    точку 30 записей назад вместо только последней. Честно ok=False, если в
    ряду меньше 31 точки (не выдумываем дельту без данных)."""
    r = _safe_get_json("https://stablecoins.llama.fi/stablecoincharts/all")
    if not r["ok"]:
        return r
    values = r["data"]
    if not values or len(values) < 31:
        return {"ok": False, "reason": "DeFiLlama stablecoincharts: меньше 31 точки в ряду"}
    now_usd = (values[-1].get("totalCirculatingUSD") or {}).get("peggedUSD")
    past_usd = (values[-31].get("totalCirculatingUSD") or {}).get("peggedUSD")
    if now_usd is None or past_usd is None:
        return {"ok": False, "reason": "DeFiLlama stablecoincharts: peggedUSD отсутствует в одной из точек"}
    flow_usd = now_usd - past_usd
    flow_pct = (flow_usd / past_usd * 100) if past_usd else None
    return {"ok": True, "now_usd": now_usd, "usd_30d_ago": past_usd,
            "flow_30d_usd": flow_usd, "flow_30d_pct": flow_pct}


def fetch_usdt_dominance() -> dict:
    """CoinGecko /global -- ТЕКУЩАЯ (не историческая) доля USDT в общей
    рыночной капитализации крипторынка. ЧЕСТНО: 30-дневный тренд USDT.D
    недоступен бесплатно -- историю глобальной капитализации CoinGecko отдаёт
    только на платном Pro-тире (проверено при разработке этой функции,
    /global возвращает только снэпшот на текущий момент, без исторического
    ряда). Возвращаем только текущее значение, не выдумываем тренд."""
    r = _safe_get_json("https://api.coingecko.com/api/v3/global")
    if not r["ok"]:
        return r
    data = (r["data"] or {}).get("data") or {}
    pct = (data.get("market_cap_percentage") or {}).get("usdt")
    if pct is None:
        return {"ok": False, "reason": "CoinGecko /global: market_cap_percentage.usdt отсутствует"}
    return {"ok": True, "usdt_dominance_pct": pct,
            "note": "только текущее значение -- 30д тренд недоступен бесплатно"}


def get_liquidity_summary() -> dict:
    """EVENT-RADAR М5 (Пакет 12/13) -- сводка ликвидности рынка: 30д поток
    стейблкоинов (DeFiLlama) + текущая доминация USDT (CoinGecko). Каждый
    источник деградирует независимо -- отказ одного не валит другой. НЕ
    включает liquidation heatmap (тот уже существует отдельно в
    derivatives_extra.compute_liquidation_heatmap()/bot.get_liq_data() --
    привязан к конкретному символу и деривативному OKX-эндпоинту, здесь
    только рыночные метрики без привязки к символу, тот же принцип
    разделения, что get_free_onchain_snapshot()."""
    stablecoin_flow = fetch_defillama_stablecoin_flow_30d()
    usdt_dominance = fetch_usdt_dominance()
    any_ok = stablecoin_flow.get("ok") or usdt_dominance.get("ok")
    return {"ok": any_ok, "stablecoin_flow_30d": stablecoin_flow,
            "usdt_dominance": usdt_dominance}


def fetch_fear_greed() -> dict:
    """alternative.me -- Fear & Greed Index. Тот же публичный бесплатный
    эндпоинт, что уже используется в других карточках bot.py (Обзор,
    Институционал) -- здесь отдельный независимый фетч для On-Chain
    карточки, без ключа."""
    r = _safe_get_json("https://api.alternative.me/fng/?limit=1")
    if not r["ok"]:
        return r
    entries = r["data"].get("data") or [{}]
    entry = entries[0]
    val = entry.get("value")
    try:
        val = int(val)
    except (TypeError, ValueError):
        val = None
    return {"ok": True, "value": val, "classification": entry.get("value_classification")}


def get_free_onchain_snapshot(symbol: str = "BTC") -> dict:
    """Единая точка входа для реальных бесплатных источников (Пакет 3 М2).
    market -- метрики рынка в целом (F&G, DeFi TVL, стейблкоины), не
    привязаны к символу. btc_chain -- метрики сети BTC (хешрейт, сложность,
    доход майнеров, комиссии, congestion) -- только когда symbol == "BTC",
    для остальных символов честно None (у нас нет бесплатного источника
    ончейн-метрик сети ETH/др. в этом пакете -- Etherscan отложен). Отказ
    одного источника не валит остальные -- честная частичная деградация."""
    market = {
        "fear_greed": fetch_fear_greed(),
        "defillama_tvl": fetch_defillama_global_tvl(),
        "defillama_stablecoins": fetch_defillama_stablecoins(),
    }
    btc_chain = None
    if symbol.upper() == "BTC":
        btc_chain = {
            "hashrate": fetch_blockchain_hashrate(),
            "difficulty": fetch_blockchain_difficulty(),
            "miners_revenue": fetch_blockchain_miners_revenue(),
            "mempool_fees": fetch_mempool_fees(),
            "mempool_congestion": fetch_mempool_congestion(),
            "difficulty_adjustment": fetch_mempool_difficulty_adjustment(),
        }
    any_ok = any(v.get("ok") for v in market.values())
    if btc_chain is not None:
        any_ok = any_ok or any(v.get("ok") for v in btc_chain.values())
    return {"ok": any_ok, "symbol": symbol.upper(), "market": market, "btc_chain": btc_chain}


def shadow_score_adjustment_free(snapshot: dict) -> dict:
    """Хук для shadow-скоринга на бесплатных источниках (Фаза C, аддитивно)
    -- боевой скоринг НИГДЕ не читает этот модуль. Формула поправки по
    hashrate/TVL/F&G ЕЩЁ НЕ СПРОЕКТИРОВАНА (отдельный шаг дизайна, не в этом
    пакете) -- честно возвращает "нет формулы", не выдумывает вес."""
    if not snapshot.get("ok"):
        return {"available": False, "adjustment": 0, "reason": "снэпшот не получен"}
    return {"available": False, "adjustment": 0,
            "reason": "данные получены, но формула shadow-скоринга по бесплатным "
                      "источникам ещё не спроектирована"}


def format_onchain_card_text(symbol: str = "BTC") -> str:
    """Текст для карточки "🔗 On-Chain" (bot.py, callback_data="onchain_info").
    Пакет 3 М2: реальные бесплатные данные (mempool.space, blockchain.info,
    DeFiLlama, alternative.me) вместо заглушки. SOPR/MVRV/NVT/Puell/LTH-STH
    остаются недоступны бесплатно -- см. KNOWLEDGE_GAPS.md, честно указано
    внизу карточки. Отказ отдельных источников не прячется -- перечисляется."""
    snap = get_free_onchain_snapshot(symbol)
    sym = snap["symbol"]
    lines = [f"🔗 On-Chain — {sym}"]

    m = snap["market"]
    fg = m["fear_greed"]
    if fg.get("ok") and fg.get("value") is not None:
        lines.append(f"Fear & Greed: {fg['value']}/100 ({fg.get('classification')})")
    tvl = m["defillama_tvl"]
    if tvl.get("ok") and tvl.get("tvl_usd") is not None:
        lines.append(f"DeFi TVL (все сети): ${tvl['tvl_usd']:,.0f}")
    sc = m["defillama_stablecoins"]
    if sc.get("ok") and sc.get("stablecoin_supply_usd") is not None:
        lines.append(f"Стейблкоины (supply): ${sc['stablecoin_supply_usd']:,.0f}")

    failed = [k for k, v in m.items() if not v.get("ok")]

    if snap["btc_chain"] is not None:
        bc = snap["btc_chain"]
        hr = bc["hashrate"]
        if hr.get("ok") and hr.get("hashrate") is not None:
            lines.append(f"Хешрейт BTC: {hr['hashrate']:.3e} {hr.get('unit') or ''}")
        di = bc["difficulty"]
        if di.get("ok") and di.get("difficulty") is not None:
            lines.append(f"Сложность BTC: {di['difficulty']:.3e}")
        mr = bc["miners_revenue"]
        if mr.get("ok") and mr.get("usd_per_day") is not None:
            lines.append(f"Доход майнеров: ${mr['usd_per_day']:,.0f}/сутки")
        fees = bc["mempool_fees"]
        if fees.get("ok"):
            lines.append(f"Комиссии сети: {fees.get('fastest_sat_vb')} sat/vB (быстро) / "
                          f"{fees.get('economy_sat_vb')} sat/vB (эконом)")
        cong = bc["mempool_congestion"]
        if cong.get("ok") and cong.get("tx_count") is not None:
            lines.append(f"Мемпул: {cong['tx_count']} неподтв. транзакций")
        da = bc["difficulty_adjustment"]
        if da.get("ok") and da.get("estimated_change_pct") is not None:
            change = da["estimated_change_pct"]
            sign = "+" if change >= 0 else ""
            lines.append(f"След. пересчёт сложности: {da.get('progress_pct', 0):.1f}% пройдено, "
                          f"ожид. {sign}{change:.2f}%")
        failed += [k for k, v in bc.items() if not v.get("ok")]
    else:
        lines.append(f"(Хешрейт/сложность/комиссии/мемпул — только для BTC; "
                      f"для {sym} этих метрик в этом пакете нет)")

    if failed:
        lines.append(f"⚠️ Не удалось получить: {', '.join(failed)}")

    lines.append("SOPR / MVRV / NVT / Puell / LTH-STH supply — недоступны бесплатно, "
                  "см. KNOWLEDGE_GAPS.md.")
    return "\n".join(lines)
