"""
dexscreener_reserve.py -- владелец, 2026-07-21: бесплатный BSC-источник
цены/ликвидности/потока для сетап-токенов `watch_zones`. Изолированный
модуль -- НЕ импортируется из живого сигнального пути, НЕ трогает
live-гейты/чек-лист. Все возможности -- за флагами, OFF по умолчанию.
Включение = отдельное явное "да" владельца после приёмки.

Endpoints (бесплатно, без ключа, https://api.dexscreener.com):
  - /tokens/v1/{chainId}/{tokenAddresses} -- БАТЧ до 30 адресов через запятую,
    1 вызов на весь watch_zones (лимит 300/мин) -- ОСНОВНОЙ путь.
  - /latest/dex/search?q={symbol} -- резолв symbol->pair, когда адреса нет.
  - /token-pairs/v1/{chainId}/{tokenAddress} -- ВСЕ пары токена (300/мин),
    нужен для честной АГРЕГИРОВАННОЙ ликвидности (сумма по всем BSC-пулам,
    не один пул -- манипуляция часто концентрирована в одном пуле, кейс
    AKE). `metas/*`-эндпоинты (нарративы) НЕ используются -- вне нашего слоя.

Вызывается ТОЛЬКО event-driven (при обновлении карточки/зоны сетап-токена
из watch_zones) -- НЕ поллинг, НЕ фоновая задача на интервале.

Три СТРОГО разделённых слоя применения (владелец, дословно):
  1. РЕЗЕРВ ЦЕНЫ -- `price_usd` как fallback к CoinGecko для BSC-микрокапов,
     где CoinGecko/DefiLlama слабы. За флагом ENABLE_DEXSCREENER_RESERVE.
  2. WASH-ФИЛЬТР -- кросс-чек (НЕ live-блок): Vol/MCap = volume.h24/marketCap,
     низкая ликвидность -> флаг "тонкая ликвидность" в лог/shadow. Сверяется
     с он-чейн Gini-расчётом (см. knowledge/COMPETITOR_ARKHAM.md, п.5 --
     тот же класс кросс-чека, разные источники).
  3. SHADOW-ПРИЗНАК -- `taker_imbalance` = (buys-sells)/(buys+sells) по h1/h24.
     Пишется ТОЛЬКО в shadow-журнал как признак-кандидат -- НЕ CVD (это
     количество транзакций, не объём), НЕ live-гейт, НЕ подтверждение входа.
     Копится до min_outcomes=20, доказывает вклад -- тогда отдельный вопрос
     владельцу (тот же протокол, что и остальные shadow-патчи проекта).

Запреты (владелец, дословно): silent except:pass НИГДЕ (каждый сбой сети/
парсинга -- явный log.error), httpx logger приглушён ДО первого сетевого
вызова (правило BOT_TOKEN -- та же дисциплина, даже если сам вызов requests-
based, не telegram.Bot). НЕ subscriber-facing -- redistribution сырых
DexScreener-данных подписчикам не делаем (коммерческое использование
разрешено термами сервиса, вопрос публичной витрины -- отдельный, будущий).
"""
import logging
import time

import requests

import shadow_engine

log = logging.getLogger(__name__)

# Владелец, 2026-07-21: включение = отдельное "да" после приёмки.
ENABLE_DEXSCREENER_RESERVE = False

DEXSCREENER_BASE = "https://api.dexscreener.com"
_MAX_BATCH_ADDRESSES = 30
THIN_LIQUIDITY_USD_THRESHOLD = 50_000  # первое приближение, не откалибровано


def _dex_get(url: str, timeout: int = 10):
    """Единая точка HTTP к DexScreener -- НЕТ silent except:pass, любой сбой
    (сеть/HTTP-статус/парсинг) явно уходит в log.error, возвращает None.
    httpx logger приглушён ДО вызова (правило BOT_TOKEN)."""
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f"dexscreener_reserve: запрос {url} не удался: {e}")
        return None


def fetch_batch_pairs(chain_id: str, token_addresses: list) -> dict:
    """/tokens/v1/{chainId}/{tokenAddresses} -- до 30 адресов ОДНИМ вызовом
    (ОСНОВНОЙ путь -- 1 вызов на весь watch_zones, не по одному на токен).
    Возвращает {адрес_lower: [pairs]} -- группировка по `baseToken.address`,
    т.к. эндпоинт отдаёт плоский список пар, не сгруппированный по адресу.
    Пустой dict при пустом входе или любой сетевой ошибке (уже в log.error)."""
    if not token_addresses:
        return {}
    addresses = token_addresses[:_MAX_BATCH_ADDRESSES]
    if len(token_addresses) > _MAX_BATCH_ADDRESSES:
        log.error(f"dexscreener_reserve: {len(token_addresses)} адресов > лимита "
                  f"{_MAX_BATCH_ADDRESSES}, обработаны только первые {_MAX_BATCH_ADDRESSES}")
    joined = ",".join(addresses)
    data = _dex_get(f"{DEXSCREENER_BASE}/tokens/v1/{chain_id}/{joined}")
    if not data:
        return {}
    pairs = data if isinstance(data, list) else []
    result = {}
    for pair in pairs:
        addr = (pair.get("baseToken") or {}).get("address", "").lower()
        if not addr:
            continue
        result.setdefault(addr, []).append(pair)
    return result


def resolve_symbol_to_pair(symbol: str) -> dict:
    """/latest/dex/search?q={symbol} -- резолв symbol->pair, когда адреса
    токена нет под рукой. Возвращает первую найденную пару (best-effort)
    либо None."""
    data = _dex_get(f"{DEXSCREENER_BASE}/latest/dex/search?q={symbol}")
    if not data:
        return None
    pairs = data.get("pairs") or []
    return pairs[0] if pairs else None


def fetch_all_pairs_for_token(chain_id: str, token_address: str) -> list:
    """/token-pairs/v1/{chainId}/{tokenAddress} -- ВСЕ пары токена, нужен
    для честной агрегированной ликвидности (см. aggregate_liquidity_usd)."""
    data = _dex_get(f"{DEXSCREENER_BASE}/token-pairs/v1/{chain_id}/{token_address}")
    if not data:
        return []
    return data if isinstance(data, list) else []


def aggregate_liquidity_usd(pairs: list) -> float:
    """Суммарная liquidity.usd по ВСЕМ пулам токена -- честнее одного пула
    для wash-фильтра (манипуляция часто концентрирована в одном пуле,
    один пул занижает реальную картину -- владелец, кейс AKE)."""
    total = 0.0
    for p in pairs:
        liq = (p.get("liquidity") or {}).get("usd")
        if liq:
            total += float(liq)
    return total


def extract_metrics(pair: dict) -> dict:
    """Извлекает нужные поля из одной DexScreener pair-записи. Честный н/д
    (None) на отсутствующем поле -- не выдумываем нули как данные."""
    liquidity = pair.get("liquidity") or {}
    volume = pair.get("volume") or {}
    txns = pair.get("txns") or {}
    price_change = pair.get("priceChange") or {}
    return {
        "price_usd": float(pair["priceUsd"]) if pair.get("priceUsd") else None,
        "liquidity_usd": float(liquidity["usd"]) if liquidity.get("usd") is not None else None,
        "volume_h24": float(volume["h24"]) if volume.get("h24") is not None else None,
        "market_cap": float(pair["marketCap"]) if pair.get("marketCap") is not None else None,
        "txns_h1": txns.get("h1"),
        "txns_h24": txns.get("h24"),
        "pair_created_at": pair.get("pairCreatedAt"),
        "price_change_h24": price_change.get("h24"),
    }


def compute_vol_mcap_ratio(metrics: dict) -> float:
    """Vol/MCap -- wash-фильтр (высокий оборот относительно капы = подозрительно).
    None при отсутствующем/нулевом mcap -- честный н/д, не деление на 0."""
    vol = metrics.get("volume_h24")
    mcap = metrics.get("market_cap")
    if not vol or not mcap:
        return None
    return vol / mcap


def is_thin_liquidity(liquidity_usd, threshold_usd: float = THIN_LIQUIDITY_USD_THRESHOLD) -> bool:
    """Флаг 'тонкая ликвидность' -- КРОСС-ЧЕК, НЕ live-гейт. Порог --
    первое приближение, не откалиброван. False (не "тонкая"), если данных
    нет вообще -- честно, не приписываем риск без основания."""
    if liquidity_usd is None:
        return False
    return liquidity_usd < threshold_usd


def compute_taker_imbalance(txns: dict):
    """(buys-sells)/(buys+sells) -- КАНДИДАТ-ПРИЗНАК для shadow-журнала.
    НЕ CVD (число транзакций, не объём в $), НЕ live-гейт, НЕ подтверждение
    входа. None при отсутствующих полях или buys+sells==0 -- честный н/д."""
    if not txns:
        return None
    buys = txns.get("buys")
    sells = txns.get("sells")
    if buys is None or sells is None:
        return None
    total = buys + sells
    if total == 0:
        return None
    return (buys - sells) / total


def cross_check_token(chain_id: str, token_address: str, symbol: str,
                       reference_price: float = None) -> dict:
    """Точка входа -- вызывается event-driven (обновление карточки/зоны
    сетап-токена watch_zones), НЕ поллинг. При ENABLE_DEXSCREENER_RESERVE=False
    -- немедленный возврат без единого сетевого вызова (мгновенный откат).

    Возвращает dict: price_usd, liquidity_usd_aggregate (сумма по всем
    пулам), pools_count, vol_mcap_ratio, thin_liquidity (wash-флаг,
    кросс-чек), taker_imbalance_h1/h24 (shadow-кандидат),
    price_discrepancy_pct (если передан reference_price -- сверка с нашим
    источником, напр. CoinGecko/on-chain). Честный н/д на любом
    отсутствующем поле."""
    if not ENABLE_DEXSCREENER_RESERVE:
        return {"enabled": False}

    all_pairs = fetch_all_pairs_for_token(chain_id, token_address)
    if not all_pairs:
        log.error(f"dexscreener_reserve: нет пар для {symbol} ({chain_id}:{token_address})")
        return {"enabled": True, "ok": False}

    # Самая ликвидная пара -- основной источник цены/txns-метрик;
    # агрегированная ликвидность -- по ВСЕМ парам (честнее одного пула).
    main_pair = max(all_pairs, key=lambda p: (p.get("liquidity") or {}).get("usd", 0) or 0)
    metrics = extract_metrics(main_pair)
    liquidity_usd_aggregate = aggregate_liquidity_usd(all_pairs)

    vol_mcap = compute_vol_mcap_ratio(metrics)
    thin_liquidity = is_thin_liquidity(liquidity_usd_aggregate)
    taker_imbalance_h1 = compute_taker_imbalance(metrics.get("txns_h1"))
    taker_imbalance_h24 = compute_taker_imbalance(metrics.get("txns_h24"))

    if thin_liquidity:
        log.info(f"dexscreener_reserve: {symbol} -- тонкая ликвидность "
                  f"(${liquidity_usd_aggregate:,.0f} по {len(all_pairs)} пулам)")

    price_discrepancy_pct = None
    if reference_price and metrics.get("price_usd"):
        price_discrepancy_pct = (metrics["price_usd"] - reference_price) / reference_price * 100

    log.info(f"dexscreener_reserve: кросс-чек {symbol} -- price=${metrics.get('price_usd')}, "
             f"liq_aggregate=${liquidity_usd_aggregate:,.0f} ({len(all_pairs)} пулов), "
             f"vol/mcap={vol_mcap}, taker_imbalance_h1={taker_imbalance_h1}, "
             f"taker_imbalance_h24={taker_imbalance_h24}"
             + (f", price_discrepancy={price_discrepancy_pct:+.2f}%" if price_discrepancy_pct is not None else ""))

    return {
        "enabled": True, "ok": True,
        "price_usd": metrics.get("price_usd"),
        "liquidity_usd_aggregate": liquidity_usd_aggregate,
        "pools_count": len(all_pairs),
        "vol_mcap_ratio": vol_mcap,
        "thin_liquidity": thin_liquidity,
        "taker_imbalance_h1": taker_imbalance_h1,
        "taker_imbalance_h24": taker_imbalance_h24,
        "price_discrepancy_pct": price_discrepancy_pct,
    }


def _build_taker_imbalance_shadow_record(symbol: str, taker_imbalance_h1,
                                          taker_imbalance_h24, chain_id: str,
                                          liquidity_usd_aggregate=None,
                                          vol_mcap_ratio=None) -> dict:
    """Тот же паттерн, что и остальные специализированные shadow-типы
    проекта (см. shadow_engine._build_pump_reversal_record) -- отдельный
    `type`, ПОЛНОСТЬЮ изолирован от боевого чеклиста/гейтов."""
    return {
        "ts": time.time(),
        "type": "dexscreener_taker_imbalance_shadow",
        "symbol": symbol,
        "chain_id": chain_id,
        "taker_imbalance_h1": taker_imbalance_h1,
        "taker_imbalance_h24": taker_imbalance_h24,
        "liquidity_usd_aggregate": liquidity_usd_aggregate,
        "vol_mcap_ratio": vol_mcap_ratio,
    }


def log_taker_imbalance_shadow(symbol: str, taker_imbalance_h1, taker_imbalance_h24,
                                chain_id: str = "bsc", liquidity_usd_aggregate=None,
                                vol_mcap_ratio=None) -> bool:
    """Синхронная версия -- локальная запись + best-effort синк, тот же паттерн,
    что shadow_engine.log_pump_reversal_shadow. Кандидат-признак, копится до
    min_outcomes=20, НЕ влияет ни на что боевое. Не поднимает исключение наружу."""
    try:
        record = _build_taker_imbalance_shadow_record(
            symbol, taker_imbalance_h1, taker_imbalance_h24, chain_id,
            liquidity_usd_aggregate, vol_mcap_ratio)
        ok = shadow_engine._write_local(record)
        if ok:
            try:
                shadow_engine._sync_to_github_sync(record)
            except Exception as e:
                log.error(f"dexscreener_reserve: GitHub sync упал для {symbol} "
                          f"(локальная запись уже сохранена): {e}")
        return ok
    except Exception as e:
        log.error(f"dexscreener_reserve.log_taker_imbalance_shadow упал для {symbol}: {e}")
        return False


async def log_taker_imbalance_shadow_async(symbol: str, taker_imbalance_h1, taker_imbalance_h24,
                                            chain_id: str = "bsc", liquidity_usd_aggregate=None,
                                            vol_mcap_ratio=None) -> bool:
    """Асинхронная версия для будущего вайринга в живой async-путь (не вызывается
    нигде сейчас -- модуль изолирован). Локальная запись + best-effort пуш в
    GitHub через run_in_executor, тот же паттерн, что log_pump_reversal_shadow_async."""
    import asyncio
    try:
        record = _build_taker_imbalance_shadow_record(
            symbol, taker_imbalance_h1, taker_imbalance_h24, chain_id,
            liquidity_usd_aggregate, vol_mcap_ratio)
    except Exception as e:
        log.error(f"dexscreener_reserve.log_taker_imbalance_shadow_async: build failed for {symbol}: {e}")
        return False
    loop = asyncio.get_event_loop()
    ok_local = await loop.run_in_executor(None, shadow_engine._write_local, record)
    if not ok_local:
        return False
    try:
        await loop.run_in_executor(None, shadow_engine._sync_to_github_sync, record)
    except Exception as e:
        log.error(f"dexscreener_reserve: GitHub sync failed for taker_imbalance ({symbol}): {e}")
    return True
