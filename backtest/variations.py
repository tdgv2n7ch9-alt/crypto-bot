"""
backtest/variations.py -- вариации управления позицией на исторических закрытых
сигналах (ночная сессия #2, Блок 9). Только чтение: Bybit public REST (история свечей
исполнения по времени сделки) + signal_journal.get_closed_records(). Ничего живого,
ничего не меняет в боевой логике -- реплей ПРОШЕДШИХ сделок по реальному ценовому пути,
не симуляция на будущее.

Почему нужен реплей по свечам, а не просто пересчёт по journal-полям: signal_journal
хранит только ОДИН терминальный исход на сделку (TP1_HIT/TP2_HIT/TP3_HIT/SL_HIT,
см. TERMINAL_STATUSES в signal_journal.py) -- трекер останавливается на первом
срабатывании. Это значит, что "что было бы, если закрыть 50% на TP1 и оставить
остаток" НЕЛЬЗЯ посчитать по journal-записи напрямую -- нет данных, что происходило с
ценой ПОСЛЕ уже записанного исхода. Единственный честный способ -- заново пройти
исторический ценовой путь по свечам за период [entered_ts, outcome_ts] и посмотреть,
какие уровни были задеты в каком порядке.

Ограничения (честно, не приукрашено):
- Разрешение 15м -- порядок TP/SL внутри ОДНОЙ 15-минутной свечи неизвестен (only OHLC,
  не тиковые данные) -- при внутрисвечной неопределённости берём КОНСЕРВАТИВНОЕ
  допущение (SL проверяется первым), это может НЕДООЦЕНИВАТЬ вариации с частичными
  закрытиями (реальный тик мог сначала дать TP, потом SL) -- как и в большинстве
  ретроспективных бэктестов без тиковых данных.
- Bybit historical kline может не покрывать делистнутые/очень старые пары -- сделки без
  свечей просто пропускаются (не выдумывается путь), см. `skipped` в отчёте.
"""
import time

import requests

import signal_journal

BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline"
KLINE_INTERVAL = "15"       # минут
RATE_LIMIT_SEC = 0.25       # уважительная пауза между запросами к публичному REST


def fetch_klines(symbol: str, start_ms: int, end_ms: int) -> list:
    """Список {"t","o","h","l","c"} хронологически за [start_ms, end_ms], либо []."""
    try:
        r = requests.get(BYBIT_KLINE_URL, params={
            "category": "linear", "symbol": f"{symbol.upper()}USDT",
            "interval": KLINE_INTERVAL, "start": start_ms, "end": end_ms, "limit": 1000,
        }, timeout=15)
        r.raise_for_status()
        d = r.json()
        if d.get("retCode") != 0:
            return []
        rows = list(reversed(d.get("result", {}).get("list", [])))
        return [{"t": int(row[0]), "o": float(row[1]), "h": float(row[2]),
                 "l": float(row[3]), "c": float(row[4])} for row in rows]
    except Exception:
        return []


def _hit(direction: str, candle: dict, level: float, is_tp: bool) -> bool:
    if direction == "long":
        return candle["h"] >= level if is_tp else candle["l"] <= level
    return candle["l"] <= level if is_tp else candle["h"] >= level


def _unrealized_r(direction: str, candle: dict, entry: float, risk: float) -> float:
    """Лучшая (в пользу сделки) цена внутри свечи, в R -- для порога "+1R достигнут"."""
    best = candle["h"] if direction == "long" else candle["l"]
    diff = (best - entry) if direction == "long" else (entry - best)
    return diff / risk


def replay_trade(rec: dict) -> dict:
    """Реплеит один закрытый сигнал по реальным 15м свечам. None, если свечи не
    получены/данных недостаточно -- НЕ выдумывает путь на пустых данных."""
    entered_ts = rec.get("entered_ts")
    outcome_ts = rec.get("outcome_ts")
    if not entered_ts or not outcome_ts:
        return None
    entry = rec.get("entered_price") or rec.get("price_at_signal")
    sl = rec.get("sl")
    tp1, tp2, tp3 = rec.get("tp1"), rec.get("tp2"), rec.get("tp3")
    if not entry or not sl:
        return None
    risk = abs(entry - sl)
    if risk <= 0:
        return None

    start_ms = int(entered_ts * 1000)
    end_ms = int(outcome_ts * 1000) + 6 * 3600 * 1000
    candles = fetch_klines(rec["symbol"], start_ms, end_ms)
    time.sleep(RATE_LIMIT_SEC)
    if not candles:
        return None

    direction = rec["direction"]

    def r_at(level):
        if level is None:
            return None
        diff = (level - entry) if direction == "long" else (entry - level)
        return round(diff / risk, 3)

    path = {"tp1": False, "tp2": False, "tp3": False, "sl": False,
            "sl_after_tp1": False, "plus1r_reached": False, "sl_after_plus1r": False}
    tp1_seen = False
    plus1r_seen = False
    for c in candles:
        # Внутрисвечная неопределённость (см. модульный докстринг) -- SL проверяется
        # первым при совпадении в одной свече, консервативное допущение.
        if _hit(direction, c, sl, is_tp=False):
            path["sl"] = True
            if tp1_seen:
                path["sl_after_tp1"] = True
            if plus1r_seen:
                path["sl_after_plus1r"] = True
            break
        if not plus1r_seen and _unrealized_r(direction, c, entry, risk) >= 1.0:
            plus1r_seen = True
            path["plus1r_reached"] = True
        if tp1 and not path["tp1"] and _hit(direction, c, tp1, is_tp=True):
            path["tp1"] = True
            tp1_seen = True
        if tp2 and not path["tp2"] and _hit(direction, c, tp2, is_tp=True):
            path["tp2"] = True
        if tp3 and not path["tp3"] and _hit(direction, c, tp3, is_tp=True):
            path["tp3"] = True
            break

    return {
        "symbol": rec["symbol"], "direction": direction,
        "actual_r_journal": rec["actual_r"], "actual_outcome_journal": rec["outcome"],
        "r_tp1": r_at(tp1), "r_tp2": r_at(tp2), "r_tp3": r_at(tp3),
        "path": path,
    }


def variation_baseline(replay: dict) -> float:
    """Базовая линия = РЕАЛЬНЫЙ исход из signal_journal (не реконструкция по свечам).

    ВАЖНАЯ ПОПРАВКА (найдена при первом прогоне, честно, не скрыто): изначально эта
    функция пыталась РЕКОНСТРУИРОВАТЬ терминальный исход по 15м-реплею тем же способом,
    что и `run_tracker._check_outcome()` (SL > TP3 > TP2 > TP1 по текущей цене) -- но
    это давало систематическое расхождение с journal (в 15 из 37 сделок): `run_tracker`
    опрашивает КАЖДЫЕ 30 СЕКУНД и останавливается НАВСЕГДА на первом же зафиксированном
    исходе (TP1_HIT/TP2_HIT/TP3_HIT/SL_HIT -- все они в TERMINAL_STATUSES, см.
    signal_journal.py), а мой 15-минутный реплей продолжает трассировать цену дальше и
    честно видит, что во МНОГИХ случаях цена после журнального TP1_HIT позже доходила и
    до TP2/TP3 -- это не баг реплея, а ре��льная находка (см. `extra_r_available` ниже),
    но её нельзя выдавать за "то же самое, что видел бы journal" -- поэтому база теперь
    просто = уже записанный `actual_r` из journal, без реконструкции."""
    return replay["actual_r_journal"]


def extra_r_available(replay: dict) -> float:
    """Сколько R было ЕЩЁ доступно в окне реплея после того, как journal уже
    зафиксировал terminal-исход (сигнал того, что 30-секундный опрос с мгновенной
    остановкой на первом попадании оставляет прибыль на столе). 0.0, если исход journal
    уже был максимальным реально достигнутым в окне реплея (включая SL-исходы, где
    "дальше" не считается уместным)."""
    p = replay["path"]
    if replay["actual_outcome_journal"] == "SL_HIT":
        return 0.0
    furthest = None
    if p["tp3"]:
        furthest = replay["r_tp3"]
    elif p["tp2"]:
        furthest = replay["r_tp2"]
    elif p["tp1"]:
        furthest = replay["r_tp1"]
    if furthest is None:
        return 0.0
    return round(max(0.0, furthest - replay["actual_r_journal"]), 3)


def variation_tp_split(replay: dict, tp1_frac: float) -> float:
    """tp1_frac на TP1, остаток едет дальше (к TP2/TP3, либо к SL если случился после TP1)."""
    p = replay["path"]
    if p["sl"] and not p["tp1"]:
        return -1.0
    if not p["tp1"]:
        return 0.0
    r_tp1 = replay["r_tp1"]
    if p["tp3"]:
        r_rest = replay["r_tp3"]
    elif p["tp2"]:
        r_rest = replay["r_tp2"]
    elif p["sl_after_tp1"]:
        r_rest = -1.0
    else:
        r_rest = r_tp1
    return round(tp1_frac * r_tp1 + (1 - tp1_frac) * r_rest, 3)


def variation_sl_to_be_after_tp1(replay: dict) -> float:
    """100% позиции, но SL переносится в безубыток (R=0 для остатка) сразу после TP1."""
    p = replay["path"]
    if p["sl"] and not p["tp1"]:
        return -1.0
    if not p["tp1"]:
        return 0.0
    if p["tp3"]:
        return replay["r_tp3"]
    if p["tp2"]:
        return replay["r_tp2"]
    if p["sl_after_tp1"]:
        return 0.0  # был бы закрыт по BE, не по изначальному SL
    return replay["r_tp1"]


def variation_sl_to_be_after_plus1r(replay: dict) -> float:
    """SL переносится в безубыток, как только НЕРЕАЛИЗОВАННЫЙ R достиг +1 (не
    обязательно на TP1 -- порог по цене, не по уровню)."""
    p = replay["path"]
    if p["sl"] and not p["plus1r_reached"]:
        return -1.0
    if not p["plus1r_reached"]:
        return 0.0
    if p["tp3"]:
        return replay["r_tp3"]
    if p["tp2"]:
        return replay["r_tp2"]
    if p["tp1"] and not p["sl_after_plus1r"]:
        return replay["r_tp1"]
    if p["sl_after_plus1r"]:
        return 0.0
    return 1.0  # +1R достигнут, ни один TP-уровень не задет, ни SL -- редкий край, R=1 как факт достигнутого порога


def run_variations(records=None) -> dict:
    """Прогоняет все закрытые сигналы через реплей + все вариации. Возвращает
    {"replayed": N, "skipped": N, "variations": {name: {"avg_r","total","n"}}}."""
    if records is None:
        records = signal_journal.get_closed_records()
    replays = []
    skipped = 0
    for rec in records:
        rep = replay_trade(rec)
        if rep is None:
            skipped += 1
            continue
        replays.append(rep)

    variations = {
        "baseline_journal": [variation_baseline(r) for r in replays],
        "tp_split_50_50": [variation_tp_split(r, 0.5) for r in replays],
        "tp_split_30_70": [variation_tp_split(r, 0.3) for r in replays],
        "sl_to_be_after_tp1": [variation_sl_to_be_after_tp1(r) for r in replays],
        "sl_to_be_after_plus1r": [variation_sl_to_be_after_plus1r(r) for r in replays],
    }
    out = {"replayed": len(replays), "skipped": skipped, "variations": {}}
    for name, rs in variations.items():
        n = len(rs)
        out["variations"][name] = {
            "n": n, "total_r": round(sum(rs), 2),
            "avg_r": round(sum(rs) / n, 3) if n else None,
        }

    extras = [extra_r_available(r) for r in replays]
    n_with_extra = sum(1 for e in extras if e > 0)
    out["extra_r_diagnostic"] = {
        "trades_with_more_available": n_with_extra,
        "total_trades": len(replays),
        "total_extra_r": round(sum(extras), 2),
        "note": (f"{n_with_extra}/{len(replays)} закрытых сделок: цена в окне реплея "
                 f"позже достигала более дальней цели, чем зафиксировал journal-трекер "
                 f"(30с опрос, мгновенная остановка на первом исходе) -- потенциально "
                 f"недополученный R, НЕ ошибка журнала, а свойство single-shot polling."),
    }
    return out, replays


if __name__ == "__main__":
    result, _ = run_variations()
    print(result)
