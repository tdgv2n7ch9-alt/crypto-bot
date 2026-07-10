"""
backtest/download_history.py -- ночная сессия #3, Блок A.1. Скачивает историю Bybit
(1h/4h/1d) по списку символов, кэширует в backtest/data/<SYMBOL>_<interval>.csv.gz.
Чекпоинт после каждого (символ, интервал) в backtest/data/_checkpoint.json -- при
обрыве продолжает с того места, не перекачивая уже готовое. Публичный REST Bybit, без
ключей, только чтение.

Формат csv.gz (заголовок): t,o,h,l,c,v -- t в миллисекундах UTC, хронологический порядок.

Глубина истории: запрашиваем MAX_MONTHS_BACK месяцев назад, но Bybit может отдавать
меньше для молодых/делистнутых пар -- фактическая глубина по каждому символу
записывается в чекпоинт (bars_count, first_ts) для честной отчётности в
HISTORICAL_BACKTEST.md, не подгоняется под ожидание.
"""
import gzip
import csv
import io
import json
import os
import time
from datetime import datetime, timezone

import requests

BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline"
INTERVAL_MAP = {"1h": "60", "4h": "240", "1d": "D"}
MAX_MONTHS_BACK = 12
RATE_LIMIT_SEC = 0.15
BARS_PER_CALL = 1000

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CHECKPOINT_FILE = os.path.join(DATA_DIR, "_checkpoint.json")
PROGRESS_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_progress.log")


def _load_checkpoint() -> dict:
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_checkpoint(cp: dict):
    tmp = f"{CHECKPOINT_FILE}.tmp{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(cp, f)
    os.replace(tmp, CHECKPOINT_FILE)


def _log_progress(msg: str):
    line = f"{datetime.now(timezone.utc).isoformat()} -- {msg}"
    print(line)
    try:
        with open(PROGRESS_LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _fetch_page(symbol: str, biv: str, start_ms: int, end_ms: int) -> list:
    try:
        r = requests.get(BYBIT_KLINE_URL, params={
            "category": "linear", "symbol": f"{symbol.upper()}USDT",
            "interval": biv, "start": start_ms, "end": end_ms, "limit": BARS_PER_CALL,
        }, timeout=15)
        r.raise_for_status()
        d = r.json()
        if d.get("retCode") != 0:
            return []
        rows = list(reversed(d.get("result", {}).get("list", [])))
        return [{"t": int(row[0]), "o": row[1], "h": row[2], "l": row[3], "c": row[4], "v": row[5]}
                for row in rows]
    except Exception as e:
        _log_progress(f"  {symbol} fetch_page error: {e}")
        return []


def download_symbol_interval(symbol: str, interval: str) -> dict:
    """Пагинация назад чанками по BARS_PER_CALL от текущего момента до MAX_MONTHS_BACK
    месяцев назад. Возвращает {"bars": N, "first_ts": ms|None, "last_ts": ms|None}."""
    biv = INTERVAL_MAP[interval]
    end_ms = int(time.time() * 1000)
    cutoff_ms = end_ms - MAX_MONTHS_BACK * 30 * 24 * 3600 * 1000
    all_rows = []
    cursor_end = end_ms
    empty_pages = 0
    while cursor_end > cutoff_ms and empty_pages < 2:
        rows = _fetch_page(symbol, biv, cutoff_ms, cursor_end)
        time.sleep(RATE_LIMIT_SEC)
        if not rows:
            empty_pages += 1
            break
        all_rows = rows + all_rows
        cursor_end = rows[0]["t"] - 1
        if len(rows) < BARS_PER_CALL:
            break  # достигли начала доступной истории раньше cutoff

    if not all_rows:
        return {"bars": 0, "first_ts": None, "last_ts": None}

    # дедуп по t (пагинация может дать перекрытие на границах чанков)
    seen = {}
    for row in all_rows:
        seen[row["t"]] = row
    ordered = [seen[t] for t in sorted(seen.keys())]

    path = os.path.join(DATA_DIR, f"{symbol}_{interval}.csv.gz")
    with gzip.open(path, "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "o", "h", "l", "c", "v"])
        for row in ordered:
            w.writerow([row["t"], row["o"], row["h"], row["l"], row["c"], row["v"]])

    return {"bars": len(ordered), "first_ts": ordered[0]["t"], "last_ts": ordered[-1]["t"]}


def run(symbols: list, intervals=("1h", "4h", "1d")):
    os.makedirs(DATA_DIR, exist_ok=True)
    cp = _load_checkpoint()
    total_pairs = len(symbols) * len(intervals)
    done_count = 0
    _log_progress(f"START: {len(symbols)} символов x {len(intervals)} интервалов = {total_pairs} пар, "
                   f"уже в чекпоинте: {sum(len(v) for v in cp.values())}")

    for i, symbol in enumerate(symbols, 1):
        cp.setdefault(symbol, {})
        for interval in intervals:
            if interval in cp[symbol]:
                done_count += 1
                continue
            try:
                stats = download_symbol_interval(symbol, interval)
            except Exception as e:
                _log_progress(f"  {symbol}/{interval} EXCEPTION: {e}")
                stats = {"bars": 0, "first_ts": None, "last_ts": None, "error": str(e)}
            cp[symbol][interval] = stats
            _save_checkpoint(cp)
            done_count += 1

        if i % 10 == 0 or i == len(symbols):
            _log_progress(f"PROGRESS: {i}/{len(symbols)} символов обработано "
                          f"({done_count}/{total_pairs} пар символ-интервал)")

    _log_progress(f"DONE: {len(symbols)} символов обработано.")
    return cp


if __name__ == "__main__":
    import sys
    syms_file = sys.argv[1] if len(sys.argv) > 1 else "/tmp/backtest_symbols.json"
    with open(syms_file) as f:
        symbols = json.load(f)
    run(symbols)
