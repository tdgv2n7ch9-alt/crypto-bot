"""
card_format.py -- единый модуль форматирования цен по тику (владелец, П-Визуал v2,
задача #207).

Найдено живьём при подготовке этого модуля: в коде уже есть ДВА разных
форматтера цены с РАЗНЫМИ порогами точности для одних и тех же диапазонов --
`bot.fp()` (>=1000 -> ",.2f", >=1 -> ".4f", >=0.01 -> ".5f", иначе ".8f") и
`card_v2.default_price_fmt()` (>=1000 -> ",.0f", >=1 -> ",.2f", >=0.01 -> ".4f",
иначе ".6f"). Одна и та же цена показывается РАЗНЫМИ числами на разных
карточках -- прямая иллюстрация того, почему нужен один канонический модуль
(задача #207), а не что оба существующих форматтера "сломаны" по отдельности.

Оба старых форматтера -- эвристика ПО МАГНИТУДЕ цены (округли по диапазону,
в котором лежит число), не по РЕАЛЬНОМУ тику биржи (`priceFilter.tickSize`
инструмента). Магнитудная эвристика -- источник того же КЛАССА бага, что
нашли на ZEC-карточке (задача #211, TP2/TP3 из ta_extra) -- родственная, но
ОТДЕЛЬНАЯ проблема (там дело в расчёте целей, не в форматировании; здесь --
чисто display: магнитудная эвристика может дать МЕНЬШЕ знаков, чем требует
реальный тик конкретной монеты, теряя значащие цифры видимо для человека).

Канонический путь: `format_price(value, symbol=None)` -- если `symbol`
передан и тик реально известен (`get_tick_size()`, живой Bybit
instruments-info, кэш 24ч) -- число знаков берётся из тика; иначе честный
fallback на магнитудную эвристику (компромисс между двумя старыми
функциями, не изобретает новую точность произвольно).

Это ТОЛЬКО модуль форматирования (задача #207) -- единый шаблон карточки
(5 блоков, задача #208) и подключение во все карточки (задача #209) --
следующие отдельные шаги, здесь НЕ выполняются.
"""
import logging
import time

import requests

log = logging.getLogger(__name__)

BYBIT_INSTRUMENTS_URL = "https://api.bybit.com/v5/market/instruments-info"
REQUEST_TIMEOUT_SEC = 10
TICK_CACHE_TTL_SEC = 24 * 3600  # тик-сайз инструмента меняется исключительно
# редко -- избегаем лишнего сетевого вызова на каждый рендер карточки

_TICK_CACHE = {}  # symbol -> (tick_size: float, fetched_ts: float)


def _decimals_from_tick(tick_size: float) -> int:
    """0.10 -> 1, 0.000001 -> 6, 1.0/25.0 -> 0 (тик >=1 не подразумевает
    дробных цен, даже если формально имеет вид "1.0")."""
    if tick_size is None or tick_size <= 0:
        return 2
    s = f"{tick_size:.10f}".rstrip("0")
    if "." not in s:
        return 0
    return max(0, len(s.split(".")[1]))


def fetch_tick_size(symbol: str) -> float:
    """Живой запрос к Bybit instruments-info (linear). Честно возвращает None
    при отказе/отсутствии символа -- вызывающий код обязан фоллбэкнуться на
    магнитудную эвристику, НЕ выдумывать тик."""
    try:
        r = requests.get(BYBIT_INSTRUMENTS_URL, params={
            "category": "linear", "symbol": symbol,
        }, timeout=REQUEST_TIMEOUT_SEC)
        d = r.json()
        lst = d.get("result", {}).get("list", [])
        if not lst:
            return None
        tick = lst[0].get("priceFilter", {}).get("tickSize")
        return float(tick) if tick else None
    except Exception as e:
        log.info(f"card_format: fetch_tick_size({symbol}) failed: {e}")
        return None


def get_tick_size(symbol: str, fetch_fn=fetch_tick_size) -> float:
    """Кэш TICK_CACHE_TTL_SEC поверх fetch_tick_size(). `fetch_fn` -- для
    тестов (без реального сетевого вызова)."""
    cached = _TICK_CACHE.get(symbol)
    if cached and time.time() - cached[1] < TICK_CACHE_TTL_SEC:
        return cached[0]
    tick = fetch_fn(symbol)
    if tick is not None:
        _TICK_CACHE[symbol] = (tick, time.time())
    return tick


def _magnitude_decimals(value: float) -> int:
    """Честный fallback, когда тик недоступен (символ н/д на Bybit linear,
    сетевой отказ, символ не передан). Компромисс между bot.fp() и
    card_v2.default_price_fmt() -- не более и не менее точный, чем нужно,
    чтобы микрокапы (например $0.0120) не округлялись до "0" (та же находка,
    что уже зафиксирована в card_v2.default_price_fmt() докстринге)."""
    v = abs(value)
    if v >= 1000:
        return 2
    if v >= 1:
        return 4
    if v >= 0.01:
        return 5
    return 8


def format_price(value: float, symbol: str = None, tick_size: float = None,
                  get_tick_size_fn=get_tick_size) -> str:
    """Канонический форматтер цены -- единственная точка правды для всех
    карточек (после задачи #209). Приоритет источника точности:
    1. явно переданный `tick_size` (вызывающая сторона уже знает тик);
    2. `get_tick_size_fn(symbol)`, если передан `symbol`;
    3. честная магнитудная эвристика (`_magnitude_decimals`).

    Тысячи -- через запятую всегда (существующая конвенция обоих старых
    форматтеров, сохранена для визуальной совместимости)."""
    decimals = None
    if tick_size is not None:
        decimals = _decimals_from_tick(tick_size)
    elif symbol is not None:
        t = get_tick_size_fn(symbol)
        if t is not None:
            decimals = _decimals_from_tick(t)
    if decimals is None:
        decimals = _magnitude_decimals(value)
    return f"{value:,.{decimals}f}"
