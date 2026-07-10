"""backtest/run_historical.py -- запускает engine.run_backtest() по всем скачанным
символам, сохраняет сырые сделки в backtest/data/_historical_trades.json (не в git,
см. .gitignore) для последующего анализа/отчёта. Ночная сессия #3, Блок A.3.

Использование: `python3 -m backtest.run_historical [symbols_file] [out_file]`
(оба аргумента опциональны, по умолчанию -- исходные 100 символов/_historical_trades.json,
см. Блок H -- прогон на 200 символах использует отдельные файлы, не перезаписывает
Блок A)."""
import json
import os
import sys
import time

import backtest.engine as eng

DATA_DIR = eng.DATA_DIR
PROGRESS_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_progress.log")


def main():
    symbols_file = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DATA_DIR, "_symbols.json")
    out_file = sys.argv[2] if len(sys.argv) > 2 else os.path.join(DATA_DIR, "_historical_trades.json")

    with open(symbols_file) as f:
        symbols = json.load(f)

    t0 = time.time()
    with open(PROGRESS_LOG, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} -- BACKTEST START: {len(symbols)} символов\n")

    result = eng.run_backtest(symbols, progress_log=PROGRESS_LOG)

    with open(out_file, "w") as f:
        json.dump(result, f)

    elapsed = time.time() - t0
    with open(PROGRESS_LOG, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} -- BACKTEST DONE: "
                f"{len(result['trades'])} сделок, {len(result['symbols_scanned'])} символов "
                f"просканировано, {len(result['symbols_skipped'])} пропущено, "
                f"{elapsed:.0f}с\n")
    print(f"DONE: {len(result['trades'])} trades, {elapsed:.0f}s")


if __name__ == "__main__":
    main()
