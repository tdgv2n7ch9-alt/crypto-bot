"""
BEST TRADE — Whale Radar, Блок 1 (сбор данных).

Цель (см. METHODOLOGY_CORE.md §5 Ликвидность): крупные лимитные ордера и крупные
рыночные сделки в стакане — это зоны ликвидности, где вероятны выносы стопов и
развороты. Этот модуль их ЛОВИТ И ЛОГИРУЕТ, ничего не решает и никуда не шлёт
боевых сигналов (Блок 2/3 — отдельно, после этого блока).

Транспорт: Bybit public WS `wss://stream.bybit.com/v5/public/linear`, тот же URL и
тот же паттерн подключения/реконнекта/keepalive, что уже используется в
`pump_detector.py` (Binance fstream недоступен из облачных ASN Railway — см.
докстринг `pump_detector.py`, поэтому Bybit, не Binance, с самого начала).

Топики на символ:
  - `orderbook.200.{SYMBOL}` — снапшот + дельты книги (200 уровней/сторона).
    Дельты применяются к локальному состоянию книги на КАЖДОЕ сообщение (иначе
    книга разъедется), но полное сканирование книги на предмет "кита" запускается
    не на каждое сообщение (может прилетать по многу раз в секунду на 50 символах
    разом), а периодически, см. `WHALE_SCAN_INTERVAL_SEC`.
  - `publicTrade.{SYMBOL}` — поток исполненных сделок, каждая проверяется на
    крупный номинал сразу (это уже дискретное разовое событие, не срез книги).

Rate-limit: единственный REST-вызов — `/v5/market/tickers` раз в
`SYMBOL_REFRESH_SEC` для выбора топ-N по обороту (1 запрос на цикл обновления,
далеко от общего лимита Bybit 500 запросов/5с). Сам WS не считается в этот лимит.

Персистентность: `data/whale/whale_events-YYYY-MM-DD.jsonl`, ротация по UTC-дате.
Честно: файловая система Railway эфемерна (тот же нюанс, что у `shadow_signals.json`,
см. `SHADOW_MODE.md`) — при передеплое лог обнулится. Это НЕ решается в Блоке 1
(спека явно просит только "персистентный лог с ротацией", не GitHub-синк) — если
нужна выживаемость между деплоями, потребуется тот же паттерн GitHub Contents API,
что уже используют `signal_journal.py`/`shadow_engine.py` — отдельное решение.

Память: книга ограничена глубиной подписки (200 уровней/сторона/символ) — не растёт
неограниченно. Список известных "китовых" уровней на символ/сторону — по
построению маленький (единицы-десятки записей), устаревшие вычищаются, когда
уровень пропадает из книги.

Пороги ниже — именованные константы, калибровка v2 (решение владельца 2026-07-11
после первого живого смоука Блока 1, см. `WHALE_RADAR_NOTES.md`): для ордеров книги
— AND-условие ≥$100K абсолютного минимума И ≥8×медианы уровней СТОРОНЫ книги
символа; для сделок (`publicTrade`) — то же AND-условие ≥$75K И ≥5×медианы недавних
сделок символа (скользящее окно `TRADE_WINDOW_SIZE`, а не глобальная медиана книги —
сделки и уровни книги разные распределения). Оба относительных порога отключаются
(остаётся только абсолютный) при малой выборке — <5 уровней на стороне книги или
<`TRADE_MEDIAN_MIN_COUNT` сделок в окне, — медиана на таком малом N нестабильна.
`WHALE_SCAN_INTERVAL_SEC` по-прежнему первое приближение, не пересматривался.

Это НОВЫЙ, изолированный модуль. НЕ импортируется из `bot.py`/`signal_loop.py`/
`fa_engine.py` в Блоке 1 — то есть не запущен как часть боевого процесса, пока
владелец не подтвердит интеграцию (см. PROGRESS.md, отдельный пункт).
"""

import asyncio
import json
import os
import statistics
import time
from collections import deque
from datetime import datetime, timezone

import requests
import websockets

BYBIT_WS_URL = "wss://stream.bybit.com/v5/public/linear"
BYBIT_TICKERS_URL = "https://api.bybit.com/v5/market/tickers"

TOP_N_SYMBOLS = 50
ORDERBOOK_DEPTH = 200
SYMBOL_REFRESH_SEC = 6 * 3600      # как часто пересобирать топ-N по обороту

WHALE_ORDER_MIN_NOTIONAL_USD = 100_000  # откалибровано владельцем 2026-07-11 после смоука Блока 1
WHALE_ORDER_MEDIAN_MULT = 8.0           # (было 50K/x5 -- шумело на BTC/ETH, см. WHALE_RADAR_NOTES.md)
WHALE_TRADE_MIN_NOTIONAL_USD = 75_000
WHALE_TRADE_MEDIAN_MULT = 5.0
TRADE_WINDOW_SIZE = 200            # скользящее окно последних сделок символа для медианы
TRADE_MEDIAN_MIN_COUNT = 10        # меньше сделок в окне -- медиана нестабильна, только абсолютный порог
SPOOF_MAX_LIFETIME_SEC = 60
SPOOF_APPROACH_PCT = 0.15          # цена подошла к уровню ближе чем на X% перед исчезновением

WHALE_SCAN_INTERVAL_SEC = 5        # период полного сканирования книги на "китов" (не на каждую дельту)

BYBIT_SUB_BATCH_SIZE = 10
BYBIT_PING_INTERVAL_SEC = 20
WATCHDOG_TIMEOUT_SEC = 60
RECONNECT_SLEEP_SEC = 5

EVENTS_DIR = "data/whale"
STABLE_BASES = {"BUSD", "USDC", "TUSD", "FDUSD"}


# ── Топ-N по обороту (REST, разово + периодический рефреш) ──────────────────

def fetch_top_symbols(n: int = TOP_N_SYMBOLS) -> list:
    """Топ-N USDT-linear перпетуалов Bybit по 24ч обороту (turnover24h), один REST-вызов
    `/v5/market/tickers?category=linear` — отдаёт весь список сразу, сортировка локальная.
    Возвращает список символов в нижнем регистре (той же конвенции, что и остальной проект,
    см. `pump_detector._discover_bybit_usdt_perp_symbols()`)."""
    try:
        r = requests.get(BYBIT_TICKERS_URL, params={"category": "linear"}, timeout=15)
        r.raise_for_status()
        rows = r.json().get("result", {}).get("list", [])
    except Exception as e:
        print(f"Whale Radar: fetch_top_symbols failed ({type(e).__name__}: {e})")
        return []

    filtered = []
    for row in rows:
        symbol = row.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        if symbol[:-4].upper() in STABLE_BASES:
            continue
        try:
            turnover = float(row.get("turnover24h", 0) or 0)
        except (TypeError, ValueError):
            continue
        if turnover <= 0:
            continue
        filtered.append((symbol.lower(), turnover))

    filtered.sort(key=lambda x: x[1], reverse=True)
    return [sym for sym, _ in filtered[:n]]


# ── Локальное состояние книги (чистые функции — тестируемые без сети) ───────

def new_book() -> dict:
    """Пустая книга: {'bid': {price: size}, 'ask': {price: size}}."""
    return {"bid": {}, "ask": {}}


def apply_orderbook_message(book: dict, payload: dict) -> None:
    """Применяет snapshot/delta-сообщение Bybit orderbook.* к локальной книге IN-PLACE.
    Формат Bybit v5: data.b/data.a — списки [price_str, size_str], size=="0" -> удалить
    уровень. snapshot полностью заменяет соответствующую сторону, delta — точечно
    применяет изменения к уже существующей."""
    msg_type = payload.get("type")
    data = payload.get("data") or {}
    is_snapshot = msg_type == "snapshot"

    for side_key, side_name in (("b", "bid"), ("a", "ask")):
        levels = data.get(side_key)
        if levels is None:
            continue
        if is_snapshot:
            book[side_name] = {}
        target = book[side_name]
        for level in levels:
            try:
                price = float(level[0])
                size = float(level[1])
            except (TypeError, ValueError, IndexError):
                continue
            if size <= 0:
                target.pop(price, None)
            else:
                target[price] = size


def book_notionals(book_side: dict) -> dict:
    """price -> notional USD (price*size) для одной стороны книги."""
    return {price: price * size for price, size in book_side.items()}


def classify_whale_levels(book_side: dict,
                           min_notional: float = WHALE_ORDER_MIN_NOTIONAL_USD,
                           median_mult: float = WHALE_ORDER_MEDIAN_MULT) -> dict:
    """Возвращает {price: notional_usd} для уровней, прошедших ОБА порога: абсолютный
    (>= min_notional) И относительный (>= median_mult * медиана номинала по стороне).
    Медиана считается по ТЕКУЩИМ уровням этой стороны (после исключения нулевых) —
    честно: с малым числом уровней (< 5) медиана нестабильна, для таких сторон
    относительный порог не применяется (используется только абсолютный), чтобы не
    ловить ложные "киты" на разреженной книге."""
    notionals = book_notionals(book_side)
    if not notionals:
        return {}
    values = list(notionals.values())
    if len(values) >= 5:
        median = statistics.median(values)
        threshold = max(min_notional, median * median_mult)
    else:
        threshold = min_notional
    return {price: usd for price, usd in notionals.items() if usd >= threshold}


def notional_usd(price: float, size: float) -> float:
    return price * size


# ── Кластеризация уровней в зоны (Блок 2) ────────────────────────────────────

CLUSTER_TOLERANCE_PCT = 0.15   # уровни в пределах X% друг от друга -> одна зона; ПЕРВОЕ
                                # ПРИБЛИЖЕНИЕ, не откалибровано на реальном распределении


def cluster_levels(levels: dict, tolerance_pct: float = CLUSTER_TOLERANCE_PCT) -> list:
    """Группирует близкие ценовые уровни ОДНОЙ стороны книги в зоны с суммарным $.
    `levels`: {price: notional_usd} (обычно — выход `classify_whale_levels()`, уже
    только китовые уровни). Жадный проход по отсортированным ценам: очередной уровень
    входит в текущую зону, если он в пределах `tolerance_pct` от ПОСЛЕДНЕЙ цены уже
    добавленной в зону (не от центра зоны — иначе широкая зона могла бы "растягиваться"
    без ограничения на шаг между соседями). Возвращает список зон, отсортированный по
    цене, каждая: {"price_lo", "price_hi", "mid", "total_usd", "level_count"}."""
    if not levels:
        return []
    prices = sorted(levels.keys())
    zones = []
    current = [prices[0]]
    for p in prices[1:]:
        prev = current[-1]
        if prev > 0 and abs(p - prev) / prev * 100 <= tolerance_pct:
            current.append(p)
        else:
            zones.append(current)
            current = [p]
    zones.append(current)

    out = []
    for zone_prices in zones:
        total_usd = sum(levels[p] for p in zone_prices)
        out.append({
            "price_lo": zone_prices[0],
            "price_hi": zone_prices[-1],
            "mid": sum(zone_prices) / len(zone_prices),
            "total_usd": round(total_usd, 2),
            "level_count": len(zone_prices),
        })
    return out


def is_whale_trade(recent_notionals: list, notional: float,
                    min_notional: float = WHALE_TRADE_MIN_NOTIONAL_USD,
                    median_mult: float = WHALE_TRADE_MEDIAN_MULT,
                    min_count: int = TRADE_MEDIAN_MIN_COUNT) -> bool:
    """Та же логика AND (абсолютный И относительный порог), что `classify_whale_levels`,
    но для потока отдельных сделок символа, а не среза книги. `recent_notionals` —
    окно ПРЕДЫДУЩИХ сделок этого символа (текущая сделка НЕ должна быть в нём —
    иначе крупная сделка сама завышает свою же медиану-порог). Меньше `min_count`
    сделок в окне — медиана нестабильна, используется только абсолютный порог
    (тот же принцип, что у `classify_whale_levels` при <5 уровнях книги)."""
    if len(recent_notionals) >= min_count:
        median = statistics.median(recent_notionals)
        threshold = max(min_notional, median * median_mult)
    else:
        threshold = min_notional
    return notional >= threshold


# ── Жизненный цикл китовых уровней (появился/сдвинулся/исчез/спуфинг) ──────

def _price_bucket(price: float) -> float:
    """Округление цены для сопоставления уровней между сканами (книга может дать
    price с шумом в последнем знаке на некоторых инструментах) — 8 значащих цифр
    достаточно, Bybit сам квантует цену тиком инструмента."""
    return round(price, 10)


def diff_whale_levels(prev: dict, curr: dict, now: float, last_price: float,
                       lifetimes: dict) -> list:
    """Сравнивает предыдущий и текущий снимок китовых уровней ОДНОЙ стороны одного
    символа, возвращает список событий жизненного цикла. `lifetimes`: price ->
    first_seen_ts, мутируется IN-PLACE (добавление новых / удаление исчезнувших).
    Не пытается угадать "сдвинулся" как отдельное событие уровня (стакан не даёт
    order-id, только цену/размер) — уровень на новой цене считается ПОЯВИВШИМСЯ,
    исчезновение с прежней цены — ИСЧЕЗ. Это осознанное упрощение для Блока 1,
    честно — не полноценный order-tracking по ID."""
    events = []
    prev_prices = set(prev.keys())
    curr_prices = set(curr.keys())

    for price in curr_prices - prev_prices:
        lifetimes[price] = now
        events.append({"event": "appeared", "price": price, "notional_usd": curr[price]})

    for price in curr_prices & prev_prices:
        if abs(curr[price] - prev[price]) / max(prev[price], 1.0) > 0.02:
            events.append({"event": "resized", "price": price, "notional_usd": curr[price],
                            "prev_notional_usd": prev[price]})

    for price in prev_prices - curr_prices:
        first_seen = lifetimes.pop(price, now)
        lifetime_sec = now - first_seen
        approached = (last_price is not None and price > 0
                      and abs(last_price - price) / price * 100 <= SPOOF_APPROACH_PCT)
        spoof_suspected = lifetime_sec < SPOOF_MAX_LIFETIME_SEC and approached
        events.append({"event": "disappeared", "price": price,
                        "notional_usd": prev[price], "lifetime_sec": round(lifetime_sec, 1),
                        "spoof_suspected": spoof_suspected})

    return events


# ── Персистентность (JSONL, ротация по UTC-дате) ─────────────────────────────

def _events_path(dt: datetime = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return os.path.join(EVENTS_DIR, f"whale_events-{dt.strftime('%Y-%m-%d')}.jsonl")


def append_event(event: dict) -> None:
    """Дописывает одно событие в текущий (по UTC-дате) whale_events-*.jsonl. Создаёт
    директорию/файл при необходимости. Не бросает исключений наружу — ошибка записи
    лога не должна ронять WS-цикл (тот же принцип двойного try/except, что у
    shadow_engine.py/pump_detector.py)."""
    try:
        os.makedirs(EVENTS_DIR, exist_ok=True)
        path = _events_path()
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"Whale Radar: append_event failed ({type(e).__name__}: {e})")


def make_order_event(symbol: str, side: str, price: float, evt: dict,
                      last_price: float) -> dict:
    distance_pct = None
    if last_price and price:
        distance_pct = round((price - last_price) / last_price * 100, 3)
    out = {
        "type": "whale_order",
        "ts": round(time.time(), 3),
        "symbol": symbol.upper(),
        "side": side,
        "price": price,
        "size_usd": round(evt.get("notional_usd", 0), 2),
        "event": evt["event"],
        "distance_pct": distance_pct,
    }
    if "lifetime_sec" in evt:
        out["lifetime_sec"] = evt["lifetime_sec"]
    if "spoof_suspected" in evt:
        out["spoof_suspected"] = evt["spoof_suspected"]
    if "prev_notional_usd" in evt:
        out["prev_size_usd"] = round(evt["prev_notional_usd"], 2)
    return out


def make_trade_event(symbol: str, side: str, price: float, size_usd: float,
                      ts_ms: int) -> dict:
    return {
        "type": "whale_trade",
        "ts": round(ts_ms / 1000, 3) if ts_ms else round(time.time(), 3),
        "symbol": symbol.upper(),
        "side": side,
        "price": price,
        "size_usd": round(size_usd, 2),
    }


# ── Живой сборщик (WS, состояние в памяти на процесс) ────────────────────────

class WhaleRadarState:
    """Состояние живого прогона: книги, известные китовые уровни/их первое появление,
    последняя цена на символ. Инкапсулировано в объект (не модульные globals), чтобы
    Блок 1 можно было прогнать локальным смоуком независимо от боевого процесса и
    без риска коллизии global-состояния, если модуль когда-то будет им дели́ться
    с pump_detector/signal_loop в одном event loop."""

    def __init__(self):
        self.books = {}            # symbol -> new_book()
        self.whale_levels = {}     # symbol -> {"bid": {price: usd}, "ask": {...}}
        self.lifetimes = {}        # symbol -> {"bid": {price: first_seen_ts}, "ask": {...}}
        self.last_price = {}       # symbol -> float
        self.trade_windows = {}    # symbol -> deque(maxlen=TRADE_WINDOW_SIZE) недавних notional$
        self.event_count = 0

    def ensure_symbol(self, symbol: str):
        if symbol not in self.books:
            self.books[symbol] = new_book()
            self.whale_levels[symbol] = {"bid": {}, "ask": {}}
            self.lifetimes[symbol] = {"bid": {}, "ask": {}}
            self.trade_windows[symbol] = deque(maxlen=TRADE_WINDOW_SIZE)

    def record_trade(self, symbol: str, notional: float) -> bool:
        """Классифицирует сделку по текущему (ДО добавления этой сделки) окну недавних
        сделок символа, затем добавляет её в окно. Порядок важен — иначе крупная
        сделка искажает свою же медиану-порог."""
        self.ensure_symbol(symbol)
        window = self.trade_windows[symbol]
        is_whale = is_whale_trade(list(window), notional)
        window.append(notional)
        return is_whale

    def get_zones(self, symbol: str, tolerance_pct: float = CLUSTER_TOLERANCE_PCT) -> dict:
        """Текущие китовые зоны символа (после кластеризации), по сторонам:
        {"bid": [zone, ...], "ask": [zone, ...]}, каждая зона дополнена "age_sec" —
        сколько живёт СТАРЕЙШИЙ из уровней, вошедших в зону (по `self.lifetimes`,
        которые заполняет `scan_symbol()`/`diff_whale_levels()`) — None, если ни один
        уровень зоны ещё не встречался в lifetimes (например, зона собрана напрямую
        в тесте, не через живой скан). Читает ПОСЛЕДНИЙ снимок `self.whale_levels`,
        обновляемый `scan_symbol()` не чаще, чем раз в `WHALE_SCAN_INTERVAL_SEC` —
        так что зоны могут отставать от книги на этот интервал, это ожидаемо (не race
        condition, компромисс цена/CPU, см. докстринг модуля). Символ без записей
        (ещё не сканировался) -> пустые списки, не ошибка."""
        levels = self.whale_levels.get(symbol, {"bid": {}, "ask": {}})
        lifetimes = self.lifetimes.get(symbol, {"bid": {}, "ask": {}})
        now = time.time()
        out = {}
        for side in ("bid", "ask"):
            zones = cluster_levels(levels.get(side, {}), tolerance_pct)
            side_lifetimes = lifetimes.get(side, {})
            for z in zones:
                ages = [now - ts for price, ts in side_lifetimes.items()
                        if z["price_lo"] <= price <= z["price_hi"]]
                z["age_sec"] = round(min(ages), 1) if ages else None
            out[side] = zones
        return out

    def scan_symbol(self, symbol: str, now: float) -> list:
        """Полное сканирование книги символа на китов, возвращает JSONL-готовые
        событийные dict'ы (не пишет файл — вызывающий решает, что делать)."""
        self.ensure_symbol(symbol)
        book = self.books[symbol]
        last_price = self.last_price.get(symbol)
        out = []
        for side in ("bid", "ask"):
            curr = classify_whale_levels(book[side])
            prev = self.whale_levels[symbol][side]
            events = diff_whale_levels(prev, curr, now, last_price, self.lifetimes[symbol][side])
            self.whale_levels[symbol][side] = curr
            for evt in events:
                out.append(make_order_event(symbol, side, evt["price"], evt, last_price))
        return out


async def _bybit_subscribe(ws, topics: list):
    for i in range(0, len(topics), BYBIT_SUB_BATCH_SIZE):
        batch = topics[i:i + BYBIT_SUB_BATCH_SIZE]
        await ws.send(json.dumps({"op": "subscribe", "args": batch}))
        await asyncio.sleep(0.1)


async def _bybit_ping_loop(ws):
    while True:
        await asyncio.sleep(BYBIT_PING_INTERVAL_SEC)
        try:
            await ws.send(json.dumps({"op": "ping"}))
        except Exception:
            return


async def _scan_loop(state: WhaleRadarState, symbols: list, log_fn=append_event):
    """Периодически (WHALE_SCAN_INTERVAL_SEC) сканирует книги всех символов на китов,
    логирует события. Отдельная корутина от приёма WS-сообщений — сканирование не
    блокирует обработку входящего потока."""
    while True:
        await asyncio.sleep(WHALE_SCAN_INTERVAL_SEC)
        now = time.time()
        for symbol in symbols:
            for event in state.scan_symbol(symbol, now):
                log_fn(event)
                state.event_count += 1


async def run_whale_radar(symbols: list = None, duration_sec: float = None,
                           log_fn=append_event, verbose: bool = True,
                           state: "WhaleRadarState" = None):
    """Главный цикл сбора: подписка orderbook.200.*/publicTrade.* по `symbols`
    (по умолчанию — топ-N по обороту), реконнект при разрыве/тишине, периодическое
    сканирование книг на китов + немедленное логирование крупных сделок.

    `duration_sec` — если задан, корутина завершается сама через это время (для
    смоук-тестов); None — работает бесконечно (Блок 2: боевой процесс запускает эту
    корутину как фоновую asyncio-задачу, см. `bot.py:_start_pump_detector`).

    `state` — если передан существующий `WhaleRadarState` (пустой или нет), функция
    мутирует ЕГО, а не создаёт новый — так вызывающий код (например, `bot.py`) может
    держать ссылку на состояние ДО того, как бесконечный цикл начнёт его наполнять,
    и читать текущие зоны (`state.get_zones(symbol)`) из другого места того же
    event loop, пока эта корутина работает в фоне."""
    if state is None:
        state = WhaleRadarState()
    if symbols is None:
        symbols = fetch_top_symbols()
    if not symbols:
        print("Whale Radar: пустой список символов, выхожу")
        return state

    topics = ([f"orderbook.{ORDERBOOK_DEPTH}.{s.upper()}" for s in symbols] +
              [f"publicTrade.{s.upper()}" for s in symbols])
    if verbose:
        print(f"Whale Radar: {len(symbols)} символов, {len(topics)} топиков, подключение")

    start_ts = time.time()
    scan_task = asyncio.create_task(_scan_loop(state, symbols, log_fn))
    try:
        while True:
            if duration_sec and time.time() - start_ts >= duration_sec:
                break
            ping_task = None
            try:
                async with websockets.connect(BYBIT_WS_URL, ping_interval=20) as ws:
                    if verbose:
                        print("Whale Radar: соединение установлено (Bybit)")
                    await _bybit_subscribe(ws, topics)
                    ping_task = asyncio.create_task(_bybit_ping_loop(ws))
                    while True:
                        if duration_sec and time.time() - start_ts >= duration_sec:
                            return state
                        try:
                            remaining = (duration_sec - (time.time() - start_ts)) if duration_sec else WATCHDOG_TIMEOUT_SEC
                            timeout = min(WATCHDOG_TIMEOUT_SEC, remaining) if duration_sec else WATCHDOG_TIMEOUT_SEC
                            if timeout <= 0:
                                return state
                            message = await asyncio.wait_for(ws.recv(), timeout=timeout)
                        except asyncio.TimeoutError:
                            if verbose:
                                print("Whale Radar: тишина, реконнект")
                            break
                        try:
                            payload = json.loads(message)
                        except Exception:
                            continue
                        topic = payload.get("topic", "")
                        if topic.startswith("orderbook."):
                            symbol = topic.split(".")[-1].lower()
                            state.ensure_symbol(symbol)
                            apply_orderbook_message(state.books[symbol], payload)
                        elif topic.startswith("publicTrade."):
                            symbol = topic.split(".", 1)[1].lower()
                            state.ensure_symbol(symbol)
                            for tr in (payload.get("data") or []):
                                try:
                                    price = float(tr.get("p"))
                                    size = float(tr.get("v"))
                                    side = tr.get("S", "?")
                                    ts_ms = int(tr.get("T", 0))
                                except (TypeError, ValueError):
                                    continue
                                state.last_price[symbol] = price
                                usd = notional_usd(price, size)
                                if state.record_trade(symbol, usd):
                                    evt = make_trade_event(symbol, side, price, usd, ts_ms)
                                    log_fn(evt)
                                    state.event_count += 1
                                    if verbose:
                                        print(f"Whale Radar: TRADE {symbol.upper()} {side} "
                                              f"${usd:,.0f} @ {price}")
            except Exception as e:
                if verbose:
                    print(f"Whale Radar: соединение разорвано ({type(e).__name__}: {e}), "
                          f"переподключение через {RECONNECT_SLEEP_SEC}с")
            finally:
                if ping_task:
                    ping_task.cancel()
            await asyncio.sleep(RECONNECT_SLEEP_SEC)
    finally:
        scan_task.cancel()
    return state


if __name__ == "__main__":
    import sys
    syms = sys.argv[1:] or ["btcusdt", "ethusdt", "solusdt"]
    dur = 90.0
    print(f"Whale Radar smoke test: {syms}, {dur}с")
    asyncio.run(run_whale_radar(symbols=syms, duration_sec=dur))
