"""
backtest/engine.py -- ночная сессия #3, Блок A.2. Реплей БОЕВОЙ логики генерации
сигналов (fa_engine.build_full_analysis(), КАК ЕСТЬ -- функция не копируется и не
переписывается) по исторически скачанным Bybit-свечам (backtest/download_history.py).
Единственная подмена -- источник свечей (monkeypatch bot.get_binance_ohlc на
исторические данные, окно строго ДО симулированного "текущего" момента, без
заглядывания вперёд) -- сама логика анализа/зон/грейда/R:R-гейта не трогается.

ДОПУЩЕНИЯ СИМУЛЯЦИИ (честно, для HISTORICAL_BACKTEST.md):
1. `coin` (rank/mcap/объём) -- НЕ историческое (история market-cap по дням не
   скачивалась, отдельная и тяжёлая задача) -- %change (1h/24h/7d/30d/90d) считаются
   РЕАЛЬНО из исторических свечей (честно), rank/mcap/vol24 -- заглушки
   (см. DEFAULT_RANK/MCAP/VOL ниже), что может немного исказить факторы rocket-score,
   завязанные на ранг/капитализацию (`fund_rank_top20` и т.п.).
2. funding/OI/DXY/S&P/VIX -- недоступны исторически в этом прогоне (не скачивались) --
   эти факторы в реплее нейтральны/отсутствуют, что может НЕДООЦЕНИВАТЬ rocket-score
   относительно живого бота (некоторые бонусы никогда не сработают).
3. Скан-каденс: каждый ЗАКРЫТЫЙ 4H-бар (не непрерывно, не 1h) -- компромисс между
   реализмом (живой бот сканирует чаще) и вычислительным бюджетом ночи, явный выбор.
4. Одна активная сделка на символ единовременно (как TOP_LONG/SHORT_SIGNALS в бою) --
   пока сделка открыта, символ не пересканируется.
5. Исполнение по 1H свечам: SL раньше TP при совпадении в одной свече (консервативно).
6. Никакого lookahead: `get_binance_ohlc(symbol, interval, limit)` в реплее ВСЕГДА
   отдаёт только свечи, ЗАКРЫВШИЕСЯ строго до текущего симулированного момента.
"""
import bisect
import csv
import gzip
import os
import time

import bot
import fa_engine
import live_prices

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

DEFAULT_RANK = 150      # заглушка -- нейтральный ранг (не топ-20/50/200 явно ни туда, ни сюда)
DEFAULT_MCAP = 500_000_000
DEFAULT_VOL24 = 20_000_000

MAX_HOLD_HOURS = 24 * 14   # 14 дней макс. удержания сделки в реплее (EXPIRED, как в live)


class HistoricalStore:
    """Кэш в памяти для .csv.gz из backtest/data/, с индексом timestamp для bisect."""

    def __init__(self, data_dir=DATA_DIR):
        self.data_dir = data_dir
        self._candles = {}   # (symbol, interval) -> list[dict]
        self._ts = {}        # (symbol, interval) -> list[int] (parallel to _candles)

    def _load(self, symbol: str, interval: str):
        key = (symbol, interval)
        if key in self._candles:
            return
        path = os.path.join(self.data_dir, f"{symbol}_{interval}.csv.gz")
        rows = []
        if os.path.exists(path):
            with gzip.open(path, "rt", newline="") as f:
                for row in csv.DictReader(f):
                    rows.append({
                        "timestamp": int(row["t"]), "open": float(row["o"]), "high": float(row["h"]),
                        "low": float(row["l"]), "close": float(row["c"]), "vol": float(row["v"]),
                    })
        self._candles[key] = rows
        self._ts[key] = [r["timestamp"] for r in rows]

    def available(self, symbol: str, interval: str) -> bool:
        self._load(symbol, interval)
        return len(self._candles[(symbol, interval)]) > 0

    def window(self, symbol: str, interval: str, as_of_ms: int, limit: int) -> list:
        """До `limit` свечей, closed строго ДО as_of_ms (без lookahead)."""
        self._load(symbol, interval)
        key = (symbol, interval)
        ts = self._ts[key]
        idx = bisect.bisect_left(ts, as_of_ms)
        start = max(0, idx - limit)
        return self._candles[key][start:idx]

    def full_series(self, symbol: str, interval: str) -> list:
        self._load(symbol, interval)
        return self._candles[(symbol, interval)]


def _pct_change(closes_by_ts: list, ts_list: list, as_of_ms: int, hours_back: float) -> float:
    """% изменение цены за `hours_back` часов до as_of_ms, по 1h-серии. 0.0, если
    недостаточно истории (честный дефолт, не выдумываем)."""
    if not closes_by_ts:
        return 0.0
    idx_now = bisect.bisect_left(ts_list, as_of_ms) - 1
    if idx_now < 0:
        return 0.0
    target_ms = as_of_ms - int(hours_back * 3600 * 1000)
    # Нужен ПОСЛЕДНИЙ бар AT-OR-BEFORE target_ms (ближайшая известная цена "hours_back
    # часов назад"), не первый бар ПОСЛЕ него -- bisect_right-1 даёт правый край, не
    # bisect_left напрямую (найдено и исправлено тестом test_pct_change_basic до прогона
    # бэктеста -- bisect_left давал систематически более "свежую", чем нужно, цену,
    # занижая эффективное окно и итоговый %change, не lookahead в будущее, но неточность).
    idx_then = bisect.bisect_right(ts_list, target_ms) - 1
    if idx_then >= len(closes_by_ts) or idx_then < 0:
        return 0.0
    then_price = closes_by_ts[idx_then]
    now_price = closes_by_ts[idx_now]
    if not then_price:
        return 0.0
    return round((now_price - then_price) / then_price * 100, 3)


def build_synthetic_coin(symbol: str, store: HistoricalStore, as_of_ms: int, price: float) -> dict:
    c1h = store.full_series(symbol, "1h")
    ts_1h = [c["timestamp"] for c in c1h]
    closes_1h = [c["close"] for c in c1h]
    return {
        "symbol": symbol, "slug": symbol.lower(), "cmc_rank": DEFAULT_RANK, "tags": [], "name": symbol,
        "quote": {"USDT": {
            "price": price, "volume_24h": DEFAULT_VOL24, "market_cap": DEFAULT_MCAP,
            "percent_change_1h": _pct_change(closes_1h, ts_1h, as_of_ms, 1),
            "percent_change_24h": _pct_change(closes_1h, ts_1h, as_of_ms, 24),
            "percent_change_7d": _pct_change(closes_1h, ts_1h, as_of_ms, 24 * 7),
            "percent_change_30d": _pct_change(closes_1h, ts_1h, as_of_ms, 24 * 30),
            "percent_change_90d": _pct_change(closes_1h, ts_1h, as_of_ms, 24 * 90),
        }},
    }


def _neutral_funding_rate(symbol):
    return {"rate": 0, "signal": "", "mark": 0, "basis": 0, "ok": False}


def _neutral_oi_change(symbol):
    return 0.0


def _neutral_ls_ratio(symbol):
    return 1.0


class _OHLCPatcher:
    """Monkeypatch bot.get_binance_ohlc (свечи -- на историю) + bot.get_funding_rate/
    _get_oi_change/_get_ls_ratio (на нейтральные заглушки -- см. допущение 2 в докстринге
    модуля: исторический funding/OI не скачивался, живые сетевые вызовы этих трёх функций
    были ЕДИНСТВЕННОЙ причиной ~1с на вызов build_full_analysis() в первом прогоне --
    без патча полный прогон занял бы десятки часов вместо часов). Сама
    fa_engine.build_full_analysis() не изменяется ни на строку."""

    def __init__(self, store: HistoricalStore):
        self.store = store
        self.as_of_ms = None
        self._orig = {}

    def __enter__(self):
        self._orig["get_binance_ohlc"] = bot.get_binance_ohlc
        self._orig["get_funding_rate"] = bot.get_funding_rate
        self._orig["_get_oi_change"] = bot._get_oi_change
        self._orig["_get_ls_ratio"] = bot._get_ls_ratio

        def patched_ohlc(symbol, interval="4h", limit=200):
            interval_key = interval.lower()
            if interval_key not in ("1h", "4h", "1d"):
                return self._orig["get_binance_ohlc"](symbol, interval, limit)
            return self.store.window(symbol, interval_key, self.as_of_ms, limit)

        bot.get_binance_ohlc = patched_ohlc
        bot.get_funding_rate = _neutral_funding_rate
        bot._get_oi_change = _neutral_oi_change
        bot._get_ls_ratio = _neutral_ls_ratio
        return self

    def __exit__(self, *exc):
        bot.get_binance_ohlc = self._orig["get_binance_ohlc"]
        bot.get_funding_rate = self._orig["get_funding_rate"]
        bot._get_oi_change = self._orig["_get_oi_change"]
        bot._get_ls_ratio = self._orig["_get_ls_ratio"]


def _hit(direction: str, candle: dict, level: float, is_tp: bool) -> bool:
    if direction == "long":
        return candle["high"] >= level if is_tp else candle["low"] <= level
    return candle["low"] <= level if is_tp else candle["high"] >= level


def simulate_execution(store: HistoricalStore, symbol: str, direction: str, entry: float,
                        sl: float, tp1: float, tp2: float, tp3: float, start_ms: int) -> dict:
    """Проходит 1H-свечи вперёд от start_ms, честно определяя первый достигнутый
    уровень (SL приоритетнее при совпадении в одной свече -- консервативно, допущение
    5 в докстринге модуля). MAX_HOLD_HOURS -- EXPIRED, если ни разу не сработало."""
    c1h = store.full_series(symbol, "1h")
    ts_1h = [c["timestamp"] for c in c1h]
    idx = bisect.bisect_left(ts_1h, start_ms)
    cutoff_ms = start_ms + MAX_HOLD_HOURS * 3600 * 1000
    risk = abs(entry - sl) or 1e-9

    def r_at(level):
        diff = (level - entry) if direction == "long" else (entry - level)
        return round(diff / risk, 3)

    for i in range(idx, len(c1h)):
        c = c1h[i]
        if c["timestamp"] > cutoff_ms:
            break
        if _hit(direction, c, sl, is_tp=False):
            return {"outcome": "SL_HIT", "actual_r": -1.0, "outcome_ts": c["timestamp"]}
        if tp3 and _hit(direction, c, tp3, is_tp=True):
            return {"outcome": "TP3_HIT", "actual_r": r_at(tp3), "outcome_ts": c["timestamp"]}
        if tp2 and _hit(direction, c, tp2, is_tp=True):
            return {"outcome": "TP2_HIT", "actual_r": r_at(tp2), "outcome_ts": c["timestamp"]}
        if tp1 and _hit(direction, c, tp1, is_tp=True):
            return {"outcome": "TP1_HIT", "actual_r": r_at(tp1), "outcome_ts": c["timestamp"]}
    return {"outcome": "EXPIRED", "actual_r": 0.0, "outcome_ts": cutoff_ms}


def scan_symbol(store: HistoricalStore, symbol: str, patcher: _OHLCPatcher,
                 scan_step_bars: int = 1, progress_cb=None) -> list:
    """Идёт по 4H-барам символа как по "часам скана" (допущение 3), вызывает
    fa_engine.build_full_analysis() КАК ЕСТЬ на каждом шаге (если нет активной сделки),
    открывает/закрывает сделки. Возвращает список записей закрытых сделок (формат близок
    к signal_journal, но отдельная, не путать со signal_journal.json)."""
    c4h = store.full_series(symbol, "4h")
    if len(c4h) < 60:
        return []

    trades = []
    active = None  # {"direction","entry","sl","tp1","tp2","tp3","start_ms"}
    i = 60  # варм-ап, чтобы у ta_extra/ema/rsi было достаточно истории на первом шаге

    while i < len(c4h):
        bar = c4h[i]
        as_of_ms = bar["timestamp"] + 1  # свеча закрылась -- сканируем сразу после закрытия

        if active is None:
            patcher.as_of_ms = as_of_ms
            price = bar["close"]
            coin = build_synthetic_coin(symbol, store, as_of_ms, price)
            try:
                result = fa_engine.build_full_analysis(symbol, coin)
            except Exception as e:
                if progress_cb:
                    progress_cb(f"  {symbol}@{as_of_ms}: exception {e}")
                i += scan_step_bars
                continue

            b11 = result.get("block11_trade_plan", {}) if result.get("ok") else {}
            if b11.get("has_setup"):
                direction = b11["direction"]
                active = {
                    "direction": direction, "entry": b11.get("entry1") or price,
                    "sl": b11["sl"], "tp1": b11["tp1"], "tp2": b11.get("tp2"),
                    "tp3": b11.get("tp3"), "rr_tp1": b11.get("rr_tp1"),
                    "start_ms": as_of_ms, "signal_bar_idx": i,
                }
        else:
            # активная сделка -- симулируем исполнение по 1H с момента входа ДО реального
            # исхода (не на каждом 4h-баре -- один синхронный проход). ВАЖНО: после этого
            # `i` обязан сдвинуться на 4h-бар, соответствующий РЕАЛЬНОМУ времени закрытия
            # сделки (outcome_ts), а не остаться на баре открытия -- иначе следующий скан
            # смотрел бы на данные "из прошлого" относительно уже случившейся сделки
            # (нашли и исправили эту ошибку до прогона, см. PROGRESS.md).
            outcome = simulate_execution(store, symbol, active["direction"], active["entry"],
                                          active["sl"], active["tp1"], active["tp2"],
                                          active["tp3"], active["start_ms"])
            trades.append({
                "symbol": symbol, "direction": active["direction"],
                "entry": active["entry"], "sl": active["sl"],
                "tp1": active["tp1"], "tp2": active["tp2"], "tp3": active["tp3"],
                "rr_tp1": active["rr_tp1"], "start_ms": active["start_ms"],
                **outcome,
            })
            active = None
            ts_4h = [c["timestamp"] for c in c4h]
            next_idx = bisect.bisect_right(ts_4h, outcome["outcome_ts"])
            i = max(next_idx, i + 1)  # минимум на 1 бар вперёд, чтобы не застрять
            continue

        i += scan_step_bars

    return trades


def run_backtest(symbols: list, data_dir=DATA_DIR, progress_log=None) -> dict:
    """Прогоняет scan_symbol по всем символам. Возвращает {"trades": [...],
    "symbols_scanned": N, "symbols_skipped_no_data": N}."""
    store = HistoricalStore(data_dir)
    all_trades = []
    skipped = []
    scanned = []

    def _log(msg):
        line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} -- {msg}"
        print(line)
        if progress_log:
            try:
                with open(progress_log, "a") as f:
                    f.write(line + "\n")
            except Exception:
                pass

    with _OHLCPatcher(store) as patcher:
        for n, symbol in enumerate(symbols, 1):
            if not store.available(symbol, "4h") or not store.available(symbol, "1h"):
                skipped.append(symbol)
                continue
            try:
                trades = scan_symbol(store, symbol, patcher, progress_cb=_log)
            except Exception as e:
                _log(f"{symbol}: FATAL {e}")
                skipped.append(symbol)
                continue
            all_trades.extend(trades)
            scanned.append(symbol)
            if n % 10 == 0 or n == len(symbols):
                _log(f"PROGRESS: {n}/{len(symbols)} символов, {len(all_trades)} сделок всего")

    return {"trades": all_trades, "symbols_scanned": scanned, "symbols_skipped": skipped}
