"""
backtest/run_real_vs_old.py -- Пакет 11, Ф1 (владелец): бэктест-сверка bot.real_full_analysis()
(новый движок) против fa_engine.build_full_analysis() (старый движок, уже реплеится в
backtest/engine.py) на РЕАЛЬНОЙ исторической истории (backtest/data/, Bybit 4h/1h/1d,
~12 месяцев на символ, см. BACKTEST_REAL_VS_OLD.md).

ВАЖНАЯ НАХОДКА (feasibility, до написания этого файла): bot.real_full_analysis() НЕ
имеет собственного параметра "исторический момент" -- он вызывает real_ta() ->
get_binance_ohlc() как есть. НО backtest/engine.py уже monkeypatch'ит именно
bot.get_binance_ohlc (и bot.get_funding_rate/_get_oi_change/_get_ls_ratio) на уровне
МОДУЛЯ bot -- то есть патч действует на ЛЮБОЙ вызов из bot.py, включая real_ta()/
real_full_analysis(), не только на fa_engine.build_full_analysis(). Плюс
build_synthetic_coin() уже строит РОВНО ту форму coin-словаря (quote.USDT.percent_change_*,
volume_24h, market_cap, symbol, cmc_rank), которую ожидает real_full_analysis(coin). Из
этого следует: real_full_analysis() реплеится через СУЩЕСТВУЮЩУЮ инфраструктуру engine.py
БЕЗ какого-либо нового движка/рефакторинга -- только новый скрипт сравнения. Смоук-тест
на BTC подтвердил: под патчем оба вызова -- ~2-3мс, без сети (see BACKTEST_REAL_VS_OLD.md).

Скан-каденс: каждые SCAN_STEP_BARS 4h-баров (не каждый бар -- компромисс между полнотой
и временем прогона в рамках одной сессии, честно указано в отчёте), НЕ симулирует
исполнение сделок (это НЕ P&L-бэктест, а сверка сигналов на одних и тех же исторических
точках) -- на каждой точке вызываются ОБА движка независимо, без активной позиции между
ними (в отличие от engine.scan_symbol(), где старый движок пропускает сканы при открытой
сделке -- здесь каждая точка сканируется всегда, чтобы сравнение было симметричным).
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtest.engine as eng
import bot
import fa_engine

SCAN_STEP_BARS = 6   # ~раз в сутки на 4h-серии
WARMUP_BARS = 60


def _old_signal(symbol, coin):
    try:
        old = fa_engine.build_full_analysis(symbol, coin)
    except Exception as e:
        return {"error": str(e)}
    b11 = old.get("block11_trade_plan", {}) if old.get("ok") else {}
    return {
        "has_setup": bool(b11.get("has_setup")),
        "direction": b11.get("direction"),
        "rr_tp1": b11.get("rr_tp1"),
    }


def _new_signal(coin):
    """has_setup здесь ЗЕРКАЛИТ полный набор гейтов send_scheduled() (не только
    rr_gate_pass -- первая версия сравнения ошибочно сравнивала old-engine's
    ПОЛНОСТЬЮ прогейченный has_setup с new-engine's ОДНИМ гейтом, что завышало
    "new-only" частоту в 15x -- см. BACKTEST_REAL_VS_OLD.md, честно зафиксировано
    как найденная и исправленная methodology-ошибка), для честного сравнения
    "promoted" vs "promoted"."""
    try:
        new = bot.real_full_analysis(coin)
    except Exception as e:
        return {"error": str(e)}
    is_long = new.get("is_long")
    direction = "long" if is_long else "short"
    grade = bot._signal_grade(new, is_long)
    rsi_4h = new.get("rsi_4h", 50)
    gate_reasons = []
    if new.get("suspicious"):
        gate_reasons.append("suspicious_volume")
    if is_long and rsi_4h > bot.RSI_EXTREME_LONG:
        gate_reasons.append("rsi_extreme_long")
    if not is_long and rsi_4h < bot.RSI_EXTREME_SHORT:
        gate_reasons.append("rsi_extreme_short")
    if not (new.get("rocket", 0) >= 60 and grade in ("A+", "A", "B")):
        gate_reasons.append("rocket_or_grade")
    if bot._counter_trend_blocked(new, direction):
        gate_reasons.append("counter_trend")
    if not new.get("rr_gate_pass"):
        gate_reasons.append("rr_gate")
    return {
        "has_setup": not gate_reasons,
        "direction": direction,
        "rr_tp1": new.get("rr_tp1"),
        "rocket": new.get("rocket"),
        "grade": grade,
        "gate_reasons": gate_reasons,
        "rr_gate_pass_only": bool(new.get("rr_gate_pass")),
        "bos_body_close_shadow": new.get("bos_body_close_shadow"),
        "structural_primitives_shadow": new.get("structural_primitives_shadow"),
    }


def scan_symbol_compare(store: eng.HistoricalStore, symbol: str, patcher: eng._OHLCPatcher,
                         progress_cb=None) -> list:
    c4h = store.full_series(symbol, "4h")
    if len(c4h) < WARMUP_BARS + SCAN_STEP_BARS:
        return []
    rows = []
    i = WARMUP_BARS
    while i < len(c4h):
        bar = c4h[i]
        as_of_ms = bar["timestamp"] + 1
        patcher.as_of_ms = as_of_ms
        price = bar["close"]
        coin = eng.build_synthetic_coin(symbol, store, as_of_ms, price)

        old = _old_signal(symbol, coin)
        new = _new_signal(coin)
        rows.append({"symbol": symbol, "ts": bar["timestamp"], "old": old, "new": new})
        i += SCAN_STEP_BARS
    if progress_cb:
        progress_cb(f"{symbol}: {len(rows)} точек сравнения")
    return rows


def run(symbols: list, data_dir=eng.DATA_DIR, progress_log=None) -> dict:
    store = eng.HistoricalStore(data_dir)
    patcher = eng._OHLCPatcher(store)
    all_rows = []
    scanned, skipped = [], []

    def _log(msg):
        line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} -- {msg}"
        print(line)
        if progress_log:
            try:
                with open(progress_log, "a") as f:
                    f.write(line + "\n")
            except Exception:
                pass

    with patcher:
        for n, symbol in enumerate(symbols, 1):
            if not store.available(symbol, "4h") or not store.available(symbol, "1h"):
                skipped.append(symbol)
                continue
            try:
                rows = scan_symbol_compare(store, symbol, patcher, progress_cb=_log)
            except Exception as e:
                _log(f"{symbol}: FATAL {e}")
                skipped.append(symbol)
                continue
            all_rows.extend(rows)
            scanned.append(symbol)
            if n % 10 == 0 or n == len(symbols):
                _log(f"PROGRESS: {n}/{len(symbols)} символов, {len(all_rows)} точек сравнения")

    return {"rows": all_rows, "symbols_scanned": scanned, "symbols_skipped": skipped}


if __name__ == "__main__":
    import glob
    all_syms = sorted({os.path.basename(p).split("_4h.csv.gz")[0]
                        for p in glob.glob(os.path.join(eng.DATA_DIR, "*_4h.csv.gz"))})
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    symbols = all_syms[:n]
    print(f"Символов доступно: {len(all_syms)}, запускаем на {len(symbols)}")
    t0 = time.time()
    result = run(symbols, progress_log="backtest/_real_vs_old_progress.log")
    t1 = time.time()
    print(f"Готово за {round(t1-t0,1)}с: {len(result['rows'])} точек сравнения, "
          f"{len(result['symbols_scanned'])} символов просканировано, "
          f"{len(result['symbols_skipped'])} пропущено (нет данных)")
    with open("backtest/_real_vs_old_raw.json", "w") as f:
        json.dump(result, f)
