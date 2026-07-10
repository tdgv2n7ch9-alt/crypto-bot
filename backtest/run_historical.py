"""backtest/run_historical.py -- запускает engine.run_backtest() по всем скачанным
символам, сохраняет сырые сделки в backtest/data/_historical_trades.json (не в git,
см. .gitignore) для последующего анализа/отчёта. Ночная сессия #3, Блок A.3."""
import json
import os
import time

import backtest.engine as eng

DATA_DIR = eng.DATA_DIR
OUT_FILE = os.path.join(DATA_DIR, "_historical_trades.json")
PROGRESS_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_progress.log")


def main():
    with open(os.path.join(DATA_DIR, "_symbols.json")) as f:
        symbols = json.load(f)

    t0 = time.time()
    with open(PROGRESS_LOG, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} -- BACKTEST START: {len(symbols)} символов\n")

    result = eng.run_backtest(symbols, progress_log=PROGRESS_LOG)

    with open(OUT_FILE, "w") as f:
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
