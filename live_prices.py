"""
BEST TRADE — Live Price Feed

CoinGecko лагает на минуты против биржи (сверено с BingX) — для точек входа/SL/TP
это критично. Здесь хранится актуальная цена, обновляемая из Binance Futures WS
(через pump_detector.py — тот же WS-коннекшен на kline-стрим, без второго
подключения к Binance). CoinGecko остаётся источником для исторических данных
(OHLC, ATH, % за 7/30/90д), где лаг некритичен.

pump_detector.py вызывает update_price() на каждый WS-тик (включая ещё не
закрытую свечу). bot.py вызывает resolve_price()/get_live_price() при рендере
карточек.
"""

import time

FRESH_AFTER_SEC = 10    # < 10с с последнего WS-тика — помечаем "(live)"
STALE_AFTER_SEC = 60    # > 60с — считаем протухшей, используем CoinGecko-фоллбек

_last_price = {}         # SYMBOL (без USDT) -> {"price": float, "ts": float}
_dynamic_requests = {}   # SYMBOL -> ts последнего запроса подписки


def update_price(symbol: str, price: float):
    """Вызывается из pump_detector.py на каждый WS-тик (kline), не только на закрытии свечи."""
    if price is None or price <= 0:
        return
    sym = symbol.upper().replace("USDT", "")
    _last_price[sym] = {"price": float(price), "ts": time.time()}


def get_live_price(symbol: str):
    """(price, age_sec) или (None, None), если живых данных ещё нет."""
    sym = symbol.upper().replace("USDT", "")
    d = _last_price.get(sym)
    if not d:
        return None, None
    return d["price"], time.time() - d["ts"]


def freshness_label(age_sec) -> str:
    if age_sec is None:
        return "(отложенная — нет WS)"
    if age_sec < FRESH_AFTER_SEC:
        return "(live)"
    return f"(отложенная, ~{age_sec:.0f}с)"


def request_subscription(symbol: str):
    """Символ вне топ-20 WS-подписки: просим pump_detector.py динамически
    подписаться на него при ближайшем цикле переподключения."""
    sym = symbol.upper().replace("USDT", "")
    _dynamic_requests[sym] = time.time()


def pending_subscriptions() -> list:
    """Для pump_detector.py: символы, запросившие подписку за последние 10 минут."""
    now = time.time()
    return [s for s, ts in _dynamic_requests.items() if now - ts < 600]


def resolve_price(symbol: str, cg_fallback_price: float):
    """Единая точка выбора цены для карточек bot.py.

    Возвращает (price, freshness_label). Если live-цена из WS не старше
    STALE_AFTER_SEC — используем её; иначе честно берём CoinGecko-фоллбек с
    пометкой задержки и просим динамическую подписку на будущее (не блокируем
    текущий рендер ожиданием)."""
    price, age = get_live_price(symbol)
    if price is not None and age is not None and age <= STALE_AFTER_SEC:
        return price, freshness_label(age)
    request_subscription(symbol)
    label = "(отложенная — нет WS)" if price is None else f"(отложенная, ~{age:.0f}с)"
    return cg_fallback_price, label
