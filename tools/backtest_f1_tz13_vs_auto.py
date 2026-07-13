"""
Пакет 17, Ф1 (владелец, ночной пакет 2026-07-13/14): бэктест-сверка tz13
(ta_extra.build_13block_verdict, Пакет 14) против текущего AUTO-пути
(bot.real_full_analysis() + гейты send_scheduled(), см. backtest/run_real_vs_old.py
для той же гейт-логики) на 100 символах Bybit (топ по объёму), 12 месяцев,
свечи Bybit. Оба движка сканируются НЕЗАВИСИМО и ПАРАЛЛЕЛЬНО (как в проде --
tz13 никогда не гейтует AUTO и наоборот) -- у каждого своё состояние
"активная сделка на символ", не общее.

Допущения симуляции (честно, для BACKTEST_F1_F3_REPORT.md):
1. `coin` (rank/mcap/объём) -- заглушки, НЕ историческая капитализация (та же
   заглушка, что и backtest/engine.py, см. её докстринг п.1); %change
   (1h/24h/7d/30d) -- реальные, из исторических свечей.
2. funding/OI/L-S ratio исторически недоступны в этом прогоне (не скачивались) --
   AUTO-путь получает нейтральные заглушки (тот же _OHLCPatcher, что и
   backtest/engine.py); tz13 получает funding=None/oi_change=None/oi_combo=None/
   ls_ratio=None -- Блок 7 (OI-матрица) tz13 честно "н/д" на ВСЕХ исторических
   сигналах этого прогона, чек-лист пункт "Funding не против позиции" по
   умолчанию засчитывается TRUE при отсутствии funding-данных (см.
   ta_extra.build_13block_verdict(), не выдумывается направление funding).
3. killzone -- ЕДИНСТВЕННЫЙ блок, где 72ч-контекст восстановлен честно: время
   суток не требует сети, backtest_common.killzone_status_at() дублирует
   bot.get_killzone_status() (bot.py НЕ трогается в этом пакете) на
   ИСТОРИЧЕСКИЙ момент времени скана, не на текущее время выполнения скрипта.
4. Скан-каденс: каждые SCAN_STEP_BARS 4h-баров (не каждый бар) -- тот же
   компромисс, что backtest/run_real_vs_old.py, явно указано.
5. Исполнение -- форвардный проход 1H-свечей, окно 72ч (backtest_common.
   simulate_execution_72h(), SL приоритетнее TP при совпадении в одной свече).
   ОТЛИЧАЕТСЯ от backtest/engine.py's 14-дневного боевого дефолта -- владелец
   явно запросил 72ч для ЭТОГО пакета (см. докстринг backtest_common.py).
6. Без комиссий и проскальзывания -- НЕ учитываются (владелец явно попросил
   написать это честно, не подгонять допущение под желаемый результат).
7. Без lookahead: свечи только строго до симулированного момента (тот же
   принцип HistoricalStore.window(), что и весь backtest/).
8. `coin`/вселенная -- ТЕКУЩИЙ топ-100 Bybit по объёму (whale_radar.
   fetch_top_symbols()), не историческая точка-в-времени вселенная --
   survivorship bias: символы, вышедшие из топ-100 за 12 месяцев (делистинг,
   падение объёма), в выборке НЕ представлены. Честно, не устранено в этом
   пакете.
"""
import bisect
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import backtest.engine as eng
import backtest_common as bc
import bot
import ta_extra

SCAN_STEP_BARS = 6      # ~раз в сутки на 4h-серии, тот же каденс, что run_real_vs_old.py
WARMUP_BARS = 60


def _auto_signal(coin: dict):
    """AUTO-путь: bot.real_full_analysis() + ПОЛНЫЙ набор гейтов send_scheduled()
    (rocket>=60+грейд A+/A/B, RR-гейт, counter-trend, suspicious/RSI-extreme) --
    та же логика, что backtest/run_real_vs_old.py::_new_signal(), но здесь
    дополнительно возвращает entry/sl/tp1-3 для симуляции исполнения (не
    только has_setup/direction для сравнения сигналов)."""
    try:
        a = bot.real_full_analysis(coin)
    except Exception as e:
        return None, {"error": str(e)}
    is_long = a.get("is_long")
    direction = "long" if is_long else "short"
    grade = bot._signal_grade(a, is_long)
    rsi_4h = a.get("rsi_4h", 50)
    gate_reasons = []
    if a.get("suspicious"):
        gate_reasons.append("suspicious_volume")
    if is_long and rsi_4h > bot.RSI_EXTREME_LONG:
        gate_reasons.append("rsi_extreme_long")
    if not is_long and rsi_4h < bot.RSI_EXTREME_SHORT:
        gate_reasons.append("rsi_extreme_short")
    if not (a.get("rocket", 0) >= 60 and grade in ("A+", "A", "B")):
        gate_reasons.append("rocket_or_grade")
    if bot._counter_trend_blocked(a, direction):
        gate_reasons.append("counter_trend")
    if not a.get("rr_gate_pass"):
        gate_reasons.append("rr_gate")
    has_setup = not gate_reasons
    meta = {"has_setup": has_setup, "direction": direction, "gate_reasons": gate_reasons,
            "rocket": a.get("rocket"), "grade": grade}
    if not has_setup:
        return None, meta
    trade = {
        "direction": direction, "entry": a.get("entry1") or a.get("price"),
        "sl": a.get("sl"), "tp1": a.get("tp1"), "tp2": a.get("tp2"), "tp3": a.get("tp3"),
        "rr_tp1": a.get("rr_tp1"),
    }
    return trade, meta


def _tz13_signal(candles_1h, candles_4h, candles_1d, price, as_of_dt):
    """tz13-путь: ta_extra.build_13block_verdict() НАПРЯМУЮ (чистая функция,
    свечи передаются явно -- monkeypatch bot.get_binance_ohlc не нужен для этого
    движка, в отличие от AUTO). funding/oi_change/oi_combo/ls_ratio -- честно
    None (допущение 2 в докстринге модуля)."""
    kz = bc.killzone_status_at(as_of_dt)
    try:
        out = ta_extra.build_13block_verdict(
            candles_1h, candles_4h, candles_1d, price,
            killzone_status=kz, funding=None, oi_change=None, oi_combo=None,
            ls_ratio=None, now_utc=as_of_dt)
    except Exception as e:
        return None, {"error": str(e)}
    b13 = out.get("block13_verdict", {})
    meta = {"has_setup": b13.get("has_setup"), "direction": out.get("direction"),
            "setup_type": out.get("setup_type"), "score": b13.get("score")}
    if not b13.get("has_setup"):
        return None, meta
    direction = out["direction"]
    ez = out.get("entry_zone") or {}
    entry = ez.get("hi") if direction == "long" else ez.get("lo")
    trade = {
        "direction": direction, "entry": entry, "sl": out.get("sl"),
        "tp1": out.get("tp1"), "tp2": out.get("tp2"), "tp3": out.get("tp3"),
    }
    return trade, meta


def _scan_auto(store, symbol, patcher, c4h, ts_4h):
    """Один активный AUTO-сигнал на символ единовременно (тот же принцип, что
    backtest/engine.py::scan_symbol()) -- после закрытия сделки скан
    перепрыгивает на реальное время её исхода (outcome_ts), не остаётся на
    баре открытия (иначе следующий скан смотрел бы на данные "из прошлого"
    относительно уже случившейся сделки)."""
    trades = []
    active = None
    i = WARMUP_BARS
    while i < len(c4h):
        bar = c4h[i]
        as_of_ms = bar["timestamp"] + 1
        if active is None:
            patcher.as_of_ms = as_of_ms
            coin = eng.build_synthetic_coin(symbol, store, as_of_ms, bar["close"])
            trade, meta = _auto_signal(coin)
            if trade and trade["sl"] and trade["tp1"]:
                active = {**trade, "start_ms": as_of_ms}
            i += SCAN_STEP_BARS
            continue
        outcome = bc.simulate_execution_72h(store, symbol, active["direction"], active["entry"],
                                             active["sl"], active["tp1"], active["tp2"],
                                             active["tp3"], active["start_ms"])
        trades.append({"symbol": symbol, "engine": "AUTO", **active, **outcome})
        active = None
        next_idx = bisect.bisect_right(ts_4h, outcome["outcome_ts"])
        i = max(next_idx, i + 1)
    return trades


def _scan_tz13(store, symbol, c4h, ts_4h):
    """Симметрично _scan_auto(), но для tz13 -- своё независимое состояние
    "активная сделка", НЕ разделяемое с AUTO (см. докстринг модуля: движки
    параллельны, никогда не гейтуют друг друга)."""
    from datetime import datetime, timezone as _tz
    trades = []
    active = None
    i = WARMUP_BARS
    while i < len(c4h):
        bar = c4h[i]
        as_of_ms = bar["timestamp"] + 1
        if active is None:
            as_of_dt = datetime.fromtimestamp(as_of_ms / 1000, tz=_tz.utc)
            c1h_win = store.window(symbol, "1h", as_of_ms, 100)
            c4h_win = store.window(symbol, "4h", as_of_ms, 200)
            c1d_win = store.window(symbol, "1d", as_of_ms, 200)
            if len(c4h_win) >= 20:
                trade, meta = _tz13_signal(c1h_win, c4h_win, c1d_win, bar["close"], as_of_dt)
                if trade and trade["sl"] and trade["tp1"]:
                    active = {**trade, "start_ms": as_of_ms, "setup_type": meta.get("setup_type")}
            i += SCAN_STEP_BARS
            continue
        outcome = bc.simulate_execution_72h(store, symbol, active["direction"], active["entry"],
                                             active["sl"], active["tp1"], active["tp2"],
                                             active["tp3"], active["start_ms"])
        trades.append({"symbol": symbol, "engine": "TZ13", **active, **outcome})
        active = None
        next_idx = bisect.bisect_right(ts_4h, outcome["outcome_ts"])
        i = max(next_idx, i + 1)
    return trades


def scan_symbol_dual(store: eng.HistoricalStore, symbol: str, patcher: eng._OHLCPatcher,
                      progress_cb=None) -> dict:
    """AUTO и tz13 сканируются НЕЗАВИСИМЫМИ проходами (каждый -- один трейд на
    символ единовременно В РАМКАХ СВОЕГО движка, тот же принцип, что
    backtest/engine.py::scan_symbol()) -- не общее состояние между движками
    (см. докстринг модуля: параллельны, никогда не гейтуют друг друга)."""
    c4h = store.full_series(symbol, "4h")
    if len(c4h) < WARMUP_BARS + SCAN_STEP_BARS:
        return {"auto_trades": [], "tz13_trades": []}
    ts_4h = [c["timestamp"] for c in c4h]

    auto_trades = _scan_auto(store, symbol, patcher, c4h, ts_4h)
    tz13_trades = _scan_tz13(store, symbol, c4h, ts_4h)

    if progress_cb:
        progress_cb(f"{symbol}: AUTO={len(auto_trades)} TZ13={len(tz13_trades)}")
    return {"auto_trades": auto_trades, "tz13_trades": tz13_trades}


def run(symbols: list, progress_log=None) -> dict:
    store = eng.HistoricalStore(eng.DATA_DIR)
    patcher = eng._OHLCPatcher(store)
    all_auto, all_tz13 = [], []
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
                result = scan_symbol_dual(store, symbol, patcher, progress_cb=_log)
            except Exception as e:
                _log(f"{symbol}: FATAL {type(e).__name__}: {e}")
                skipped.append(symbol)
                continue
            all_auto.extend(result["auto_trades"])
            all_tz13.extend(result["tz13_trades"])
            scanned.append(symbol)
            if n % 10 == 0 or n == len(symbols):
                _log(f"PROGRESS: {n}/{len(symbols)} символов, AUTO={len(all_auto)} TZ13={len(all_tz13)} сделок")

    return {"auto_trades": all_auto, "tz13_trades": all_tz13,
            "symbols_scanned": scanned, "symbols_skipped": skipped}


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    print(f"Ф1: получаем топ-{n} символов Bybit по объёму...")
    symbols = bc.fetch_top_symbols_uppercase(n)
    print(f"Получено {len(symbols)} символов: {symbols[:10]}...")

    print("Ф1: проверяем/докачиваем кэш свечей...")
    cache_result = bc.ensure_symbols_cached(symbols)

    out_dir = os.path.join(bc.REPO_ROOT, "output", "backtest_cache")
    os.makedirs(out_dir, exist_ok=True)

    print(f"Ф1: запускаем dual-скан {len(symbols)} символов...")
    t0 = time.time()
    result = run(symbols, progress_log=os.path.join(out_dir, "_f1_progress.log"))
    t1 = time.time()
    print(f"Готово за {round(t1-t0,1)}с: AUTO {len(result['auto_trades'])} сделок, "
          f"TZ13 {len(result['tz13_trades'])} сделок, "
          f"{len(result['symbols_scanned'])} символов просканировано, "
          f"{len(result['symbols_skipped'])} пропущено")

    result["cache_stats"] = {"downloaded": len(cache_result["downloaded"]),
                              "reused": len(cache_result["reused"]),
                              "missing": len(cache_result["missing"])}
    result["symbols_requested"] = symbols
    with open(os.path.join(out_dir, "f1_raw_trades.json"), "w") as f:
        json.dump(result, f)
    print(f"Сохранено: {os.path.join(out_dir, 'f1_raw_trades.json')}")
