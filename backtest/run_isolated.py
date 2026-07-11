"""backtest/run_isolated.py -- решение #1 топ-5 владельцу (NIGHT3_SUMMARY.md), выполнено
2026-07-11 по прямому запросу: изолирует эффект патчей 01 (killzone-hours) и 02
(rr-gate 2.0) ПО ОТДЕЛЬНОСТИ на тех же 100 символах, что и совместный прогон
PATCH_IMPACT.md -- честно не разделённый в ночную сессию #3 (см. её докстринг).
Запускает оба прогона ПОСЛЕДОВАТЕЛЬНО (не параллельно -- каждый уже загружает CPU
полностью на самих себя, параллельный запуск не ускорил бы, только усложнил бы
чтение прогресс-лога) и сохраняет каждый в отдельный файл."""
import json
import os
import time

import backtest.engine as eng
import backtest.engine_patched as ep

DATA_DIR = eng.DATA_DIR
PROGRESS_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_progress.log")


def _run_one(label: str, symbols: list, apply_kz: bool, apply_rr: bool, out_file: str):
    t0 = time.time()
    with open(PROGRESS_LOG, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} -- ISOLATED BACKTEST START "
                f"[{label}]: {len(symbols)} символов (killzone={apply_kz}, rr_gate={apply_rr})\n")
    result = ep.run_backtest_patched(symbols, apply_killzone_patch=apply_kz,
                                      apply_rr_gate_patch=apply_rr, progress_log=None)
    with open(out_file, "w") as f:
        json.dump(result, f)
    elapsed = time.time() - t0
    with open(PROGRESS_LOG, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} -- ISOLATED BACKTEST DONE "
                f"[{label}]: {len(result['trades'])} сделок, {elapsed:.0f}с\n")
    print(f"DONE [{label}]: {len(result['trades'])} trades, {elapsed:.0f}s")


def main():
    with open(os.path.join(DATA_DIR, "_symbols.json")) as f:
        symbols = json.load(f)

    _run_one("01-killzone-only", symbols, apply_kz=True, apply_rr=False,
              out_file=os.path.join(DATA_DIR, "_isolated_01_killzone_trades.json"))
    _run_one("02-rrgate-only", symbols, apply_kz=False, apply_rr=True,
              out_file=os.path.join(DATA_DIR, "_isolated_02_rrgate_trades.json"))


if __name__ == "__main__":
    main()
