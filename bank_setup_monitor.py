"""
bank_setup_monitor.py -- условный SHORT-сетап BANKUSDT (владелец, СРОЧНЫЙ наряд вне
очереди, 2026-07-15, сетап из разбора планирующего чата). Три алерта, все критические
(оба канала, send_system critical=True):

  1. CHoCH -- закрытие 15м свечи НИЖЕ последнего higher low (стартовый уровень 0.0505,
     автопересчёт при формировании нового HL выше -- см. _recompute_hl_after_new_high()).
  2. РЕТЕСТ -- после CHoCH, возврат цены к сломанному уровню (±0.5%) снизу.
  3. ИНВАЛИДАЦИЯ -- закрытие 1H СВЕЧИ выше 0.0553 (проверяется независимо от стадии
     CHoCH/ретеста, в любой момент, пока сетап не завершён/не инвалидирован).

Источник свечей: Bybit linear klines (проверено живьём с Railway, работает), BingX --
резерв на случай отказа Bybit. CoinGecko НЕ используется для этого монитора (429 на
момент проверки, да и не даёт klines по таймфреймам).
"""
import json
import logging
import os
import time

import requests

log = logging.getLogger(__name__)

BANK_SYMBOL = "BANKUSDT"
INITIAL_HL = 0.0505  # владелец -- стартовый уровень last higher low
INVALIDATION_LEVEL = 0.0553  # закрытие 1H выше -- сетап отменён
SL = 0.0553
TARGET_LO, TARGET_HI = 0.044, 0.046
RETEST_TOLERANCE_PCT = 0.5  # ретест = цена в пределах ±0.5% от сломанного уровня
UNLOCK_DATE_ISO = "2026-07-17"
UNLOCK_AMOUNT_USD = 2_080_000
UNLOCK_PCT_MCAP = 6

POLL_INTERVAL_SEC = 60
CANDLE_HISTORY_MAX = 200  # ~50ч на 15м -- с запасом для swing-пересчёта HL

_JOURNAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "journal")
STATE_FILE = os.path.join(_JOURNAL_DIR, "bank_setup_state.json")

BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline"
BINGX_KLINE_URL = "https://open-api.bingx.com/openApi/swap/v3/quote/klines"

STAGE_WATCHING_HL = "WATCHING_HL"
STAGE_WATCHING_RETEST = "WATCHING_RETEST"
STAGE_DONE = "DONE"
STAGE_INVALIDATED = "INVALIDATED"


def _atomic_write_json(path: str, obj) -> bool:
    tmp_path = f"{path}.tmp{os.getpid()}"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp_path, "w") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        return True
    except Exception as e:
        log.error(f"bank_setup_monitor: atomic write to {path} failed ({e})")
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return False


def _default_state() -> dict:
    return {
        "stage": STAGE_WATCHING_HL,
        "hl_level": INITIAL_HL,
        "broken_level": None,
        "last_closed_15m_ts": None,
        "last_closed_1h_ts": None,
        "candles_15m": [],  # [{"ts","o","h","l","c"}], хронологический порядок, capped
        "highest_high_since_hl": INITIAL_HL,
    }


def _load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return _default_state()


def _save_state(state: dict) -> None:
    _atomic_write_json(STATE_FILE, state)


def _fetch_klines_bybit(interval: str, limit: int = 5):
    r = requests.get(BYBIT_KLINE_URL, params={
        "category": "linear", "symbol": BANK_SYMBOL, "interval": interval, "limit": limit,
    }, timeout=10)
    d = r.json()
    if d.get("retCode") != 0:
        raise RuntimeError(f"bybit kline error: {d.get('retMsg')}")
    rows = d["result"]["list"]  # newest first
    rows = list(reversed(rows))  # oldest first
    return [{"ts": int(row[0]), "o": float(row[1]), "h": float(row[2]),
              "l": float(row[3]), "c": float(row[4])} for row in rows]


def _bingx_interval(tf: str) -> str:
    return {"15": "15m", "60": "1h"}[tf]


def _fetch_klines_bingx(interval: str, limit: int = 5):
    r = requests.get(BINGX_KLINE_URL, params={
        "symbol": "BANK-USDT", "interval": _bingx_interval(interval), "limit": limit,
    }, timeout=10)
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"bingx kline error: {d.get('msg')}")
    rows = d.get("data", [])  # newest first (BingX convention)
    rows = list(reversed(rows))
    return [{"ts": int(row["time"]), "o": float(row["open"]), "h": float(row["high"]),
              "l": float(row["low"]), "c": float(row["close"])} for row in rows]


def get_klines(interval: str, limit: int = 5):
    """Bybit первичный источник, BingX -- резерв. Честно пробрасывает исключение,
    если ОБА отказали -- вызывающий код обязан пропустить цикл, не выдумывать свечи."""
    try:
        return _fetch_klines_bybit(interval, limit), "bybit"
    except Exception as e:
        log.info(f"bank_setup_monitor: bybit klines ({interval}) failed: {e}, пробую bingx")
    return _fetch_klines_bingx(interval, limit), "bingx"


def _recompute_hl_after_new_high(candles_history: list, new_high_ts: int, old_hl_level: float) -> float:
    """Владелец: "если цена сделает новый HL выше -- пересчитай уровень автоматически
    (последний локальный лой перед новым хаем)". Реализация: минимум low среди свечей
    МЕЖДУ предыдущей точкой отсчёта (свеча, где был сформирован текущий hl_level или
    начало истории) и новой хай-свечой -- это и есть "последний откат перед новым хаем"."""
    relevant = [c for c in candles_history if c["ts"] <= new_high_ts]
    if not relevant:
        return old_hl_level
    return min(c["l"] for c in relevant)


def _unlock_reference_line() -> str:
    days_left = _days_until(UNLOCK_DATE_ISO)
    return (f"📅 Разлок BANK: {UNLOCK_DATE_ISO} (через {days_left} дн.), "
            f"${UNLOCK_AMOUNT_USD:,.0f} (~{UNLOCK_PCT_MCAP}% капы)")


def _days_until(date_iso: str) -> int:
    from datetime import date
    y, m, d = (int(x) for x in date_iso.split("-"))
    return (date(y, m, d) - date.today()).days


def format_choch_alert(hl_level: float) -> str:
    return (f"🔻 BANK: CHoCH -- закрытие 15м ниже {hl_level}\n"
            f"Слом структуры, ждём ретест уровня для входа.\n"
            f"{_unlock_reference_line()}")


def format_hl_update_alert(old_level: float, new_level: float) -> str:
    return (f"📈 BANK: новый higher low, уровень CHoCH пересчитан {old_level} -> {new_level}\n"
            f"{_unlock_reference_line()}")


def format_retest_alert(level: float) -> str:
    return (f"🎯 BANK ретест {level} -- условия входа шорт по плану: "
            f"SL {SL}, цель {TARGET_LO}-{TARGET_HI}, риск ≤1%\n"
            f"{_unlock_reference_line()}")


def format_invalidation_alert() -> str:
    return (f"🚫 BANK: сетап отменён, рост продолжается (закрытие 1H выше {INVALIDATION_LEVEL})\n"
            f"{_unlock_reference_line()}")


async def check_bank_setup(bot, send_system_fn=None) -> list:
    """Джоб (scheduler.add_job, interval POLL_INTERVAL_SEC). Возвращает список
    отправленных типов алертов (для тестов/логов), обычно []."""
    if send_system_fn is None:
        import bot as bot_module
        send_system_fn = bot_module.send_system

    state = _load_state()
    sent = []

    if state["stage"] in (STAGE_DONE, STAGE_INVALIDATED):
        return sent  # терминальная стадия -- монитор больше ничего не делает

    try:
        candles_15m, src15 = get_klines("15", limit=10)
    except Exception as e:
        log.error(f"bank_setup_monitor: ВСЕ источники 15м-свечей отказали: {e}")
        return sent

    # Только НОВЫЕ закрытые свечи с прошлого прогона
    last_ts = state.get("last_closed_15m_ts")
    new_candles = [c for c in candles_15m if last_ts is None or c["ts"] > last_ts]
    # Первый запуск: не обрабатываем историю, только помечаем текущий хвост как известный
    if last_ts is None:
        state["last_closed_15m_ts"] = candles_15m[-1]["ts"] if candles_15m else None
        state["candles_15m"] = candles_15m[-CANDLE_HISTORY_MAX:]
        _save_state(state)
        log.info(f"bank_setup_monitor: первый запуск, источник={src15}, старт с ts={state['last_closed_15m_ts']}")
        return sent

    for c in new_candles:
        state["candles_15m"].append(c)
    state["candles_15m"] = state["candles_15m"][-CANDLE_HISTORY_MAX:]

    for c in new_candles:
        hl_level = state["hl_level"]

        if state["stage"] == STAGE_WATCHING_HL:
            if c["c"] < hl_level:
                state["stage"] = STAGE_WATCHING_RETEST
                state["broken_level"] = hl_level
                await send_system_fn(bot, format_choch_alert(hl_level), critical=True)
                sent.append("choch")
            elif c["h"] > state["highest_high_since_hl"]:
                new_hl = _recompute_hl_after_new_high(state["candles_15m"], c["ts"], hl_level)
                state["highest_high_since_hl"] = c["h"]
                if new_hl > hl_level:
                    old = hl_level
                    state["hl_level"] = new_hl
                    await send_system_fn(bot, format_hl_update_alert(old, new_hl), critical=True)
                    sent.append("hl_update")

        elif state["stage"] == STAGE_WATCHING_RETEST:
            level = state["broken_level"]
            lo_band = level * (1 - RETEST_TOLERANCE_PCT / 100)
            if lo_band <= c["c"] <= level:
                state["stage"] = STAGE_DONE
                await send_system_fn(bot, format_retest_alert(level), critical=True)
                sent.append("retest")

        state["last_closed_15m_ts"] = c["ts"]
        if state["stage"] in (STAGE_DONE, STAGE_INVALIDATED):
            break

    # Инвалидация -- проверяется независимо от стадии (кроме уже терминальных),
    # закрытие 1H выше SL отменяет сетап в любой момент.
    if state["stage"] not in (STAGE_DONE, STAGE_INVALIDATED):
        try:
            candles_1h, _ = get_klines("60", limit=3)
        except Exception as e:
            log.info(f"bank_setup_monitor: 1H-свечи для инвалидации недоступны: {e}")
            candles_1h = []
        last_1h_ts = state.get("last_closed_1h_ts")
        new_1h = [c for c in candles_1h if last_1h_ts is None or c["ts"] > last_1h_ts]
        for c in new_1h:
            if c["c"] > INVALIDATION_LEVEL:
                state["stage"] = STAGE_INVALIDATED
                await send_system_fn(bot, format_invalidation_alert(), critical=True)
                sent.append("invalidation")
            state["last_closed_1h_ts"] = c["ts"]

    _save_state(state)
    return sent
