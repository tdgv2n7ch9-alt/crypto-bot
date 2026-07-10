"""backtest/run_patched.py -- Блок B: прогон engine_patched.run_backtest_patched()
на тех же символах/данных, сохраняет в backtest/data/_patched_trades.json."""
import json
import os
import time

import backtest.engine as eng
import backtest.engine_patched as ep

DATA_DIR = eng.DATA_DIR
OUT_FILE = os.path.join(DATA_DIR, "_patched_trades.json")
PROGRESS_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_progress.log")


def main():
    with open(os.path.join(DATA_DIR, "_symbols.json")) as f:
        symbols = json.load(f)

    t0 = time.time()
    with open(PROGRESS_LOG, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} -- PATCHED BACKTEST START: {len(symbols)} символов\n")

    result = ep.run_backtest_patched(symbols, progress_log=PROGRESS_LOG)

    with open(OUT_FILE, "w") as f:
        json.dump(result, f)

    elapsed = time.time() - t0
    with open(PROGRESS_LOG, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} -- PATCHED BACKTEST DONE: "
                f"{len(result['trades'])} сделок, {elapsed:.0f}с\n")
    print(f"DONE: {len(result['trades'])} trades, {elapsed:.0f}s")


if __name__ == "__main__":
    main()
