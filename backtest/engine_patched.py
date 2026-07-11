"""
backtest/engine_patched.py -- ночная сессия #3, Блок B. Второй прогон
backtest.engine с 5 теневыми патчами (ночь #2, Блок 1) ВКЛЮЧЕНЫ.

Что реально ВКЛЮЧАЕТСЯ (влияет на гейт -- меняет, какие сделки проходят):
  01 killzone-hours: bot.get_killzone_status -> bot.get_killzone_status_shadow
     (меняет пункт 4 чеклиста Блока 5 fa_engine -- "killzone активна или близко")
  02 rr-gate 2.0:    ta_extra.SR_MIN_RR_TP1 -> 2.0 (было 1.5, live НЕ меняется --
     монkeypatch только на время патченого прогона)

Что считается ИНФОРМАЦИОННО на каждой сделке ОБОИХ прогонов (не гейтует, как и в
shadow_engine.py живой ночи #2):
  03 breaker/mitigation, 04 RSI-дивергенция, 05 BPR -- те же функции ta_extra.py,
  добавленные ночью #2 Блок 1, применяются к свечам на момент входа сделки.

Живой код (bot.py/ta_extra.py) НЕ изменяется -- всё через monkeypatch на время
прогона, откатывается в finally.
"""
import backtest.engine as eng
import bot
import ta_extra


def _tag_patch_factors(store: eng.HistoricalStore, trade: dict) -> dict:
    """Патчи 03/04/05 -- информационные теги на уже сгенерированной сделке (не влияют
    на то, была ли сделка открыта). Использует 4h-свечи на момент входа."""
    symbol = trade["symbol"]
    c4h_all = store.full_series(symbol, "4h")
    ts_4h = [c["timestamp"] for c in c4h_all]
    import bisect
    idx = bisect.bisect_left(ts_4h, trade["start_ms"])
    candles = c4h_all[max(0, idx - 100):idx]
    tags = {"breaker_mitigation": None, "divergence_against": False, "bpr_confluence": False}
    if len(candles) < 10:
        return tags
    try:
        b = ta_extra.classify_breaker_or_mitigation(candles, trade["direction"])
        tags["breaker_mitigation"] = b.get("type")
    except Exception:
        pass
    try:
        d = ta_extra.detect_price_indicator_divergence(candles)
        against = ((trade["direction"] == "long" and d.get("bearish_classical")) or
                   (trade["direction"] == "short" and d.get("bullish_classical")))
        tags["divergence_against"] = bool(against)
    except Exception:
        pass
    try:
        zones = ta_extra.detect_bpr_zones(candles)
        entry = trade["entry"]
        for z in zones[:5]:
            if z["lo"] <= entry <= z["hi"]:
                tags["bpr_confluence"] = True
                break
    except Exception:
        pass
    return tags


def run_backtest_patched(symbols: list, data_dir=eng.DATA_DIR, progress_log=None,
                          apply_killzone_patch: bool = True,
                          apply_rr_gate_patch: bool = True) -> dict:
    """Прогоняет run_backtest с патчами 01 (killzone-hours)/02 (rr-gate 2.0) включёнными
    по отдельности или вместе -- ночная сессия #3 не разделила эффект (PATCH_IMPACT.md,
    честно помечено как candidate), решение #1 топ-5 владельцу, выполнено 2026-07-11
    по прямому запросу. `apply_killzone_patch`/`apply_rr_gate_patch=False` отключает
    патч для ЭТОГО прогона -- возвращает те же поля, что и eng.run_backtest, плюс
    "patch_tags" на каждой сделке (03/04/05, информационно, как раньше, независимо от
    флагов 01/02)."""
    orig_kz = bot.get_killzone_status
    orig_rr_gate = ta_extra.SR_MIN_RR_TP1
    try:
        if apply_killzone_patch:
            bot.get_killzone_status = bot.get_killzone_status_shadow
        if apply_rr_gate_patch:
            ta_extra.SR_MIN_RR_TP1 = 2.0
        result = eng.run_backtest(symbols, data_dir=data_dir, progress_log=progress_log)
    finally:
        bot.get_killzone_status = orig_kz
        ta_extra.SR_MIN_RR_TP1 = orig_rr_gate

    store = eng.HistoricalStore(data_dir)
    for t in result["trades"]:
        t["patch_tags"] = _tag_patch_factors(store, t)
    return result
