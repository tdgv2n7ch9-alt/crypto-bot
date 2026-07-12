"""
rug_radar.py -- скоринг rug-риска (0-100) для кандидатов x100-сканера и Памп-радара
(Пакет 9, модуль RUG-RADAR, обучен на кейсе LAB 07.2026 -- см.
knowledge/METHODOLOGY_CORE.md §21). Аддитивный, ИНФОРМАЦИОННЫЙ модуль: строка
"⚠️ RUG-РИСК: XX/100" на карточках при score>=40 -- в скоринг промо/x100 НЕ
подаётся, никакой боевой логики/порогов не меняет (см. permanent boundary
CLAUDE.md -- торговую/сигнальную логику не менять без "да" владельца). Перевод в
бой (использование в фильтрации/скоринге) -- отдельный будущий "да" после
накопления статистики.

Источники данных -- ТОЛЬКО реально бесплатные, проверено живьём 2026-07-12:
  - FDV/MCap, circulating/total supply, tickers (список бирж) -- CoinGecko
    /coins/{id} (тот же бесплатный эндпоинт, что уже использует bot.py для
    get_binance_alltime_low()). Подтверждено живым вызовом: SOL -> fdv=$48.6B,
    mcap=$44.9B, 100 тикеров/59 бирж в ответе.
  - Возраст токена -- CoinGecko genesis_date, когда есть (часто null даже для
    старых монет -- честно НЕ гарантированное поле, проверено: DOGE/TRX дают
    дату, XRP -- null при том, что XRP старше обоих). Фоллбек -- atl_date
    (дата ATL) как ПРИБЛИЖЁННЫЙ proxy возраста: для молодых токенов ATL обычно
    фиксируется вскоре после листинга, но это НЕ настоящая дата запуска --
    честно помечается в результате как "approx".
  - Вертикальный рост + тонкий объём -- уже доступные поля coin["quote"]["USDT"]
    (percent_change_30d, volume_24h, market_cap), без дополнительных вызовов.

Детекторы, для которых бесплатных данных НЕТ (честно "н/д", не выдумано):
  - Концентрация топ-10 холдеров (>50% supply): Etherscan/BscScan
    `tokenholderlist` -- PRO-эндпоинт (Standard plan и выше), НЕ на бесплатном
    тарифе. Проверено web-поиском 2026-07-12 (docs.etherscan.io, увеличение
    порога с 1.7.2026: 1000 записей на Free-тир -- но сам endpoint для Free
    тира недоступен вообще, не только урезан). Возвращается "н/д" всегда,
    пока владелец не одобрит платный тир.
  - Крупные переводы токена проекта на биржи: зависит от Etherscan
    whale-tracking модуля (Пакет 9 М4), который НЕ построен в рамках этой
    задачи -- см. `detect_exchange_transfers` ниже, принимает необязательный
    внешний provider, без него -- "н/д", не заглушка с придуманными числами.
"""

RUG_RISK_WARN_THRESHOLD = 40   # порог показа строки на карточке
RUG_RISK_ALERT_THRESHOLD = 70  # порог красного предупреждения

FDV_MCAP_RATIO_WARN = 3.0      # навес разлоков: FDV/MCap > 3
FDV_MCAP_POINTS_MAX = 20

VERTICAL_GROWTH_PCT_30D = 200.0   # >200%/30д
THIN_VOLUME_RATIO_PCT = 15.0      # volume_24h/market_cap < 15% при таком росте = тонкий объём
VERTICAL_GROWTH_POINTS = 25

NARROW_LISTING_MAX_EXCHANGES = 2
YOUNG_TOKEN_MAX_DAYS = 183      # ~6 месяцев
AGE_LISTING_POINTS_MAX = 10

CONCENTRATION_POINTS_MAX = 30   # н/д сейчас -- Etherscan holder-list PRO-only
EXCHANGE_TRANSFER_POINTS_MAX = 15  # н/д сейчас -- зависит от Пакета 9 М4

MAX_SCORE_TODAY = FDV_MCAP_POINTS_MAX + VERTICAL_GROWTH_POINTS + AGE_LISTING_POINTS_MAX
# = 55 из теоретических 100 -- честно ниже 100, т.к. 2 из 5 детекторов "н/д"


def detect_concentration(holders_data=None) -> dict:
    """Топ-10 холдеров >50% supply -- н/д без Etherscan/BscScan PRO-тира
    (см. докстринг модуля). Принимает `holders_data` для будущей проводки,
    когда владелец одобрит платный API -- пока всегда None у вызывающей
    стороны, детектор честно возвращает "н/д"."""
    if holders_data is None:
        return {"available": False, "points": 0, "reason": "н/д -- Etherscan/BscScan tokenholderlist требует платный тир"}
    top10_pct = holders_data.get("top10_pct")
    if top10_pct is None:
        return {"available": False, "points": 0, "reason": "н/д -- данные о холдерах не получены"}
    triggered = top10_pct > 50.0
    points = CONCENTRATION_POINTS_MAX if triggered else 0
    return {"available": True, "points": points, "top10_pct": top10_pct,
            "reason": f"топ-10 холдеров {top10_pct:.1f}% supply" + (" (>50%)" if triggered else "")}


def detect_fdv_mcap_ratio(fdv: float, mcap: float) -> dict:
    """FDV/MCap > 3 -- навес будущих разлоков (unlock overhang)."""
    if not fdv or not mcap or mcap <= 0:
        return {"available": False, "points": 0, "reason": "н/д -- нет FDV/MCap"}
    ratio = fdv / mcap
    if ratio <= FDV_MCAP_RATIO_WARN:
        return {"available": True, "points": 0, "ratio": round(ratio, 2), "reason": f"FDV/MCap {ratio:.1f}x (норма)"}
    # линейная шкала: 3x -> 0, 6x и выше -> максимум
    points = min(FDV_MCAP_POINTS_MAX, round((ratio - FDV_MCAP_RATIO_WARN) / 3.0 * FDV_MCAP_POINTS_MAX))
    return {"available": True, "points": points, "ratio": round(ratio, 2),
            "reason": f"FDV/MCap {ratio:.1f}x (>{FDV_MCAP_RATIO_WARN:.0f}x -- навес разлоков)"}


def detect_vertical_growth_thin_volume(percent_change_30d: float, volume_24h: float, market_cap: float) -> dict:
    """Вертикальный рост >200%/30д на тонком объёме (volume_24h/mcap < 15%) --
    памп без органического объёма, классический паттерн перед LAB-style сливом."""
    if percent_change_30d is None or not market_cap or market_cap <= 0:
        return {"available": False, "points": 0, "reason": "н/д"}
    vol_ratio = (volume_24h / market_cap * 100) if volume_24h else 0
    vertical = percent_change_30d > VERTICAL_GROWTH_PCT_30D
    thin = vol_ratio < THIN_VOLUME_RATIO_PCT
    triggered = vertical and thin
    points = VERTICAL_GROWTH_POINTS if triggered else 0
    reason = f"+{percent_change_30d:.0f}%/30д, объём/MCap {vol_ratio:.1f}%"
    if triggered:
        reason += " (вертикальный рост на тонком объёме)"
    return {"available": True, "points": points, "percent_change_30d": round(percent_change_30d, 1),
            "volume_mcap_ratio_pct": round(vol_ratio, 2), "reason": reason}


def detect_exchange_transfers(transfer_data=None) -> dict:
    """Крупные переводы токена проекта на биржи -- н/д без Etherscan
    whale-tracking (Пакет 9 М4, не построен в рамках этой задачи). Принимает
    `transfer_data` для будущей проводки -- см. докстринг detect_concentration."""
    if transfer_data is None:
        return {"available": False, "points": 0, "reason": "н/д -- зависит от Etherscan whale-tracking (Пакет 9 М4)"}
    large_transfer_usd = transfer_data.get("large_transfer_usd_recent")
    if large_transfer_usd is None:
        return {"available": False, "points": 0, "reason": "н/д -- данных о переводах нет"}
    triggered = large_transfer_usd > 0
    points = EXCHANGE_TRANSFER_POINTS_MAX if triggered else 0
    return {"available": True, "points": points, "large_transfer_usd": large_transfer_usd,
            "reason": f"крупный перевод на биржу ${large_transfer_usd:,.0f}" if triggered else "переводов не найдено"}


def detect_age_and_narrow_listing(age_days, age_is_approx: bool, num_exchanges) -> dict:
    """Возраст токена <6 мес + листинг только на 1-2 биржах -- узкий рынок,
    легко скоординировать маркет-мейкинг (см. LAB-кейс: Bitget-инфраструктура)."""
    if age_days is None and num_exchanges is None:
        return {"available": False, "points": 0, "reason": "н/д"}
    young = age_days is not None and age_days < YOUNG_TOKEN_MAX_DAYS
    narrow = num_exchanges is not None and num_exchanges <= NARROW_LISTING_MAX_EXCHANGES
    triggered = young and narrow
    points = AGE_LISTING_POINTS_MAX if triggered else 0
    parts = []
    if age_days is not None:
        parts.append(f"возраст ~{age_days}д{' (approx по ATL)' if age_is_approx else ''}")
    if num_exchanges is not None:
        parts.append(f"{num_exchanges} бирж(а)")
    reason = ", ".join(parts) if parts else "н/д"
    if triggered:
        reason += " (молодой токен, узкий листинг)"
    return {"available": age_days is not None or num_exchanges is not None, "points": points, "reason": reason}


def compute_rug_risk(symbol: str, coin: dict, cg_detail: dict = None,
                      holders_data=None, transfer_data=None) -> dict:
    """Главная точка входа. `coin` -- та же структура, что get_all_coins()[i]
    (quote.USDT.market_cap/volume_24h/percent_change_30d). `cg_detail` --
    ответ CoinGecko /coins/{id} (fully_diluted_valuation, circulating/total
    supply, genesis_date, tickers) -- опционален, при отсутствии соответствующие
    детекторы честно "н/д". `holders_data`/`transfer_data` -- задел под будущие
    интеграции (Etherscan PRO / Пакет 9 М4), сейчас всегда None у вызывающей
    стороны бота."""
    q = (coin or {}).get("quote", {}).get("USDT", {})
    market_cap = q.get("market_cap")
    volume_24h = q.get("volume_24h")
    pct_30d = q.get("percent_change_30d")

    fdv = None
    age_days = None
    age_is_approx = False
    num_exchanges = None
    if cg_detail:
        md = cg_detail.get("market_data", {}) or {}
        fdv = (md.get("fully_diluted_valuation") or {}).get("usd")
        genesis = cg_detail.get("genesis_date")
        atl_date = (md.get("atl_date") or {}).get("usd")
        import datetime
        ref_date = genesis or (atl_date[:10] if atl_date else None)
        age_is_approx = genesis is None and atl_date is not None
        if ref_date:
            try:
                d = datetime.date.fromisoformat(ref_date)
                age_days = (datetime.date.today() - d).days
            except (ValueError, TypeError):
                age_days = None
        tickers = cg_detail.get("tickers")
        if tickers is not None:
            num_exchanges = len({t.get("market", {}).get("name") for t in tickers if t.get("market")})

    detectors = {
        "concentration": detect_concentration(holders_data),
        "fdv_mcap": detect_fdv_mcap_ratio(fdv, market_cap),
        "vertical_growth": detect_vertical_growth_thin_volume(pct_30d, volume_24h, market_cap),
        "exchange_transfers": detect_exchange_transfers(transfer_data),
        "age_listing": detect_age_and_narrow_listing(age_days, age_is_approx, num_exchanges),
    }
    score = sum(d["points"] for d in detectors.values())
    max_possible = sum([
        CONCENTRATION_POINTS_MAX if detectors["concentration"]["available"] else 0,
        FDV_MCAP_POINTS_MAX if detectors["fdv_mcap"]["available"] else 0,
        VERTICAL_GROWTH_POINTS if detectors["vertical_growth"]["available"] else 0,
        EXCHANGE_TRANSFER_POINTS_MAX if detectors["exchange_transfers"]["available"] else 0,
        AGE_LISTING_POINTS_MAX if detectors["age_listing"]["available"] else 0,
    ])
    reasons = [d["reason"] for d in detectors.values() if d["points"] > 0]
    return {
        "symbol": symbol,
        "score": score,
        "max_possible_score": max_possible,
        "score_out_of_100_note": "из 100 теоретических -- часть детекторов н/д, см. detectors",
        "reasons": reasons,
        "detectors": detectors,
        "warn": score >= RUG_RISK_WARN_THRESHOLD,
        "alert": score >= RUG_RISK_ALERT_THRESHOLD,
    }


def format_rug_risk_line(rug_risk: dict) -> str:
    """Строка для карточки x100/Памп-радара. Пустая строка, если score < WARN
    threshold (ничего не показываем -- не засорять карточку)."""
    if not rug_risk or rug_risk.get("score", 0) < RUG_RISK_WARN_THRESHOLD:
        return ""
    score = rug_risk["score"]
    reasons_str = "; ".join(rug_risk.get("reasons", [])[:3])
    if rug_risk.get("alert"):
        return f"🔴 RUG-РИСК: {score}/100 -- признаки инсайдерской схемы ({reasons_str})"
    return f"⚠️ RUG-РИСК: {score}/100 ({reasons_str})"


def fetch_coingecko_detail(bot_module, symbol: str) -> dict:
    """Best-effort фетч CoinGecko /coins/{id} для FDV/supply/genesis/tickers.
    Использует уже существующие bot_module._cg_slug()/_cg_get() (тот же
    паттерн, что get_binance_alltime_low()) -- НЕ новый API-клиент. Возвращает
    {} при любой ошибке (сеть/404/рейт-лимит) -- вызывающая сторона это уже
    ожидает через cg_detail=None в compute_rug_risk."""
    try:
        slug = bot_module._cg_slug(symbol)
        data = bot_module._cg_get(
            f"https://api.coingecko.com/api/v3/coins/{slug}",
            params={"localization": "false", "tickers": "true",
                    "community_data": "false", "developer_data": "false"},
            timeout=15,
        )
        return data or {}
    except Exception:
        return {}
