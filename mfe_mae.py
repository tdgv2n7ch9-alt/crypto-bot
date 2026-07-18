"""
mfe_mae.py -- MFE/MAE (Maximum Favorable/Adverse Excursion) для live journal-сделок
и ретроспективы по закрытым shadow-исходам (владелец, ДА 2026-07-18, очередь п.6).

Проблема: shadow/journal-записи хранят entry/SL/TPn и итоговый бинарный исход
(TP/SL_HIT), но не путь цены МЕЖДУ входом и исходом -- невозможно узнать,
насколько близко сделка была к TP до итогового SL (плохой вход по времени, не
по направлению), или дошла до SL почти сразу без единого движения в плюс
(структурно неверный вход). MFE/MAE в R-мультиплах закрывает этот пробел.

Два независимых источника данных для одной и той же математики:
  1. Live-трекер (`update_running_mfe_mae`) -- вызывается из
     `signal_journal.run_tracker()` на каждом 30с-тике по уже имеющейся
     live-цене (live_prices), без новых сетевых вызовов. Замораживается
     автоматически: run_tracker() перестаёт трогать запись, как только статус
     становится терминальным.
  2. Ретроспектива (`retro_mfe_mae_for_closed`) -- для СТАРЫХ закрытых записей
     без running-данных: докачивает историчные 1m Bybit-свечи за окно
     [ts, outcome_ts] (`fetch_bybit_1m_candles_sync`, тот же паттерн, что
     `_get_ohlc_bybit` в bot.py, но с start/end для конкретного прошлого окна
     и пагинацией сверх 1000-барного лимита Bybit за один запрос).
"""
import logging
import time

import requests

log = logging.getLogger(__name__)

BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline"
_MAX_PAGES = 50  # защита от бесконечного цикла -- 50*1000мин = ~34 дня с запасом


def _mfe_mae_r(direction: str, entry: float, sl: float, best_price: float, worst_price: float):
    """R-мультиплы через risk=|entry-sl|. mae_r -- всегда >=0 (расстояние в
    неблагоприятную сторону, не подписанное значение) -- удобнее для сравнения/
    сортировки, чем смешение знаков mfe/mae в одной шкале."""
    risk = abs(entry - sl)
    if risk == 0:
        return None, None
    if direction == "long":
        mfe = (best_price - entry) / risk
        mae = (entry - worst_price) / risk
    elif direction == "short":
        mfe = (entry - best_price) / risk
        mae = (worst_price - entry) / risk
    else:
        return None, None
    return round(mfe, 3), round(max(0.0, mae), 3)


def compute_mfe_mae_from_candles(direction: str, entry: float, sl: float, candles: list):
    """`candles` -- список dict с ключами "high"/"low" (порядок не важен, окно
    уже отфильтровано вызывающей стороной). Возвращает {"mfe_r", "mae_r",
    "best_price", "worst_price"} либо None при недостатке данных (не выдумывает
    число на пустом входе)."""
    if not candles or entry is None or sl is None or direction not in ("long", "short"):
        return None
    if direction == "long":
        best_price = max(c["high"] for c in candles)
        worst_price = min(c["low"] for c in candles)
    else:
        best_price = min(c["low"] for c in candles)
        worst_price = max(c["high"] for c in candles)
    mfe_r, mae_r = _mfe_mae_r(direction, entry, sl, best_price, worst_price)
    if mfe_r is None:
        return None
    return {"mfe_r": mfe_r, "mae_r": mae_r, "best_price": best_price, "worst_price": worst_price}


def update_running_mfe_mae(direction: str, entered_price, current_price,
                            prev_mfe_price=None, prev_mae_price=None):
    """Один живой тик (одна текущая цена, не свечи) -- обновляет running
    best/worst price (НЕ R-мультипл -- R считается из этих цен отдельно через
    running_mfe_mae_r(), чтобы risk=|entry-sl| не пересчитывался на каждый тик
    из потенциально устаревшего sl). Возвращает (new_mfe_price, new_mae_price)
    для сохранения в journal-записи. Честный no-op (возвращает prev как есть),
    если entered_price/current_price отсутствуют -- ещё не вошли в сделку."""
    if entered_price is None or current_price is None:
        return prev_mfe_price, prev_mae_price
    if direction == "long":
        new_mfe = current_price if prev_mfe_price is None else max(prev_mfe_price, current_price)
        new_mae = current_price if prev_mae_price is None else min(prev_mae_price, current_price)
    elif direction == "short":
        new_mfe = current_price if prev_mfe_price is None else min(prev_mfe_price, current_price)
        new_mae = current_price if prev_mae_price is None else max(prev_mae_price, current_price)
    else:
        return prev_mfe_price, prev_mae_price
    return new_mfe, new_mae


def running_mfe_mae_r(direction: str, entered_price, sl, mfe_price, mae_price):
    """Переводит накопленные running mfe_price/mae_price (см.
    update_running_mfe_mae) в R-мультиплы -- вызывать при чтении/показе, не на
    каждом тике записи. None, если чего-то не хватает (сделка ещё не вошла,
    либо ни одного тика с ценой ещё не было)."""
    if entered_price is None or sl is None or mfe_price is None or mae_price is None:
        return None, None
    return _mfe_mae_r(direction, entered_price, sl, mfe_price, mae_price)


def fetch_bybit_1m_candles_sync(symbol: str, start_ts: float, end_ts: float, timeout: int = 10) -> list:
    """Историчные 1m-свечи Bybit linear-перпетуала `symbol` (без USDT-суффикса)
    за [start_ts, end_ts] (unix секунды). Пагинация вперёд по 1000-барным чанкам
    (лимит Bybit за запрос) -- окна дольше ~16.6ч требуют нескольких запросов.
    Синхронная функция -- вызывать через run_in_executor из async-кода. Пустой
    список при отказе/отсутствии данных (не выдумывает свечи)."""
    all_candles = []
    seen_ts_ms = set()
    cur_start_ms = int(start_ts * 1000)
    end_ms = int(end_ts * 1000)
    for _ in range(_MAX_PAGES):
        if cur_start_ms >= end_ms:
            break
        try:
            r = requests.get(BYBIT_KLINE_URL, params={
                "category": "linear", "symbol": f"{symbol.upper()}USDT",
                "interval": "1", "start": cur_start_ms, "end": end_ms, "limit": 1000,
            }, timeout=timeout)
            r.raise_for_status()
            rows = r.json().get("result", {}).get("list", [])
        except Exception as e:
            log.error(f"mfe_mae: Bybit kline fetch failed for {symbol}: {e}")
            break
        if not rows:
            break
        rows = list(reversed(rows))  # Bybit отдаёт новые бары первыми
        new_rows = 0
        for row in rows:
            ts_ms = int(row[0])
            if ts_ms in seen_ts_ms:
                continue
            seen_ts_ms.add(ts_ms)
            all_candles.append({
                "ts": ts_ms / 1000, "open": float(row[1]), "high": float(row[2]),
                "low": float(row[3]), "close": float(row[4]),
            })
            new_rows += 1
        if new_rows == 0:
            break
        cur_start_ms = int(rows[-1][0]) + 60_000  # минута после последней полученной
    return sorted(all_candles, key=lambda c: c["ts"])


def retro_mfe_mae_for_closed(shadow_records: list, journal_records: dict,
                              fetch_candles_fn=None) -> list:
    """Для каждой closed (promoted_live + сопоставленной с journal-исходом)
    shadow-записи -- MFE/MAE через историчные 1m-свечи за окно [entered_ts,
    outcome_ts] (не signal ts -- вход мог случиться позже сигнала, окно должно
    начинаться с реального входа в позицию, не с момента карточки).
    `fetch_candles_fn` -- DI для тестов (по умолчанию fetch_bybit_1m_candles_sync).
    Пропускает (не включает в результат) записи с недостающими полями -- честно,
    не выдумывает результат на неполных данных."""
    import shadow_outcome_analysis as soa
    fetch = fetch_candles_fn or fetch_bybit_1m_candles_sync
    results = []
    for rec in shadow_records:
        if not rec.get("promoted_live"):
            continue
        m = soa.match_shadow_to_journal(rec, journal_records)
        if not m["matched"] or m["outcome"] not in soa.OUTCOME_STATUSES:
            continue
        jrec = journal_records.get(m["journal_id"])
        if not jrec:
            continue
        entered_ts = jrec.get("entered_ts")
        outcome_ts = jrec.get("outcome_ts")
        entered_price = jrec.get("entered_price")
        sl = rec.get("sl")
        direction = rec.get("direction")
        if not (entered_ts and outcome_ts and entered_price and sl and direction):
            continue
        candles = fetch(rec["symbol"], entered_ts, outcome_ts)
        stat = compute_mfe_mae_from_candles(direction, entered_price, sl, candles)
        if stat is None:
            continue
        results.append({
            "symbol": rec["symbol"], "journal_id": m["journal_id"],
            "outcome": m["outcome"], "direction": direction,
            "entered_price": entered_price, "sl": sl,
            "entered_ts": entered_ts, "outcome_ts": outcome_ts,
            "candles_used": len(candles), **stat,
        })
    return results
