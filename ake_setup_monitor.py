"""
ake_setup_monitor.py -- единый алерт-пакет AKEUSDT (владелец В ПОЗИЦИИ шорт, СРОЧНЫЙ
наряд вне очереди, 2026-07-15, критичность максимальная -- все алерты в оба канала).

5 независимых триггеров (A1-A5), каждый с дедупом "1 раз до сброса условия" (владелец:
"повторное касание после отхода >2% = новый алерт") -- см. _LevelState/_check_trigger().
A4 (инвалидация) НЕ останавливает остальные триггеры -- владелец уже в позиции,
управление позицией продолжается независимо от разворота тезиса на будущее.

Источник свечей -- тот же, что bank_setup_monitor.py (Bybit первичный, BingX резерв).
Статус кошельков #1/#2 (справочная строка) -- берётся из journal/bsc_wallet_events.json,
которую уже пишет bsc_wallet_monitor.py (задача #226), без дублирования RPC-вызовов.
"""
import json
import logging
import os
import time

import requests

log = logging.getLogger(__name__)

AKE_SYMBOL = "AKEUSDT"
LEVEL_0007 = 0.0007
INVALIDATION_LEVEL = 0.00073
LOW_SWEEP_LEVEL = 0.0005065
MID_TARGET = 0.000506
INTERIM_TARGET = 0.000558

RESET_MOVE_PCT = 2.0  # владелец: "отход >2% = новый алерт" -- дедуп-сброс
POLL_INTERVAL_SEC = 60
CANDLE_LIMIT = 3
REQUEST_TIMEOUT_SEC = 10  # владелец, критический регресс bsc_wallet_monitor 2026-07-15
# (#240) -- жёсткий потолок на КАЖДЫЙ сетевой вызов, тот же паттерн распространён сюда
SOURCE_DOWN_NOTIFY_INTERVAL_SEC = 15 * 60  # честный [SYS] раз в 15 мин при отказе
# ВСЕХ источников -- владелец В ПОЗИЦИИ, "не молчать и не копить"

_JOURNAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "journal")
STATE_FILE = os.path.join(_JOURNAL_DIR, "ake_setup_state.json")
BSC_EVENTS_FILE = os.path.join(_JOURNAL_DIR, "bsc_wallet_events.json")

BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline"
BINGX_KLINE_URL = "https://open-api.bingx.com/openApi/swap/v3/quote/klines"

WALLET_1 = "0x27333Bd8c321a263B0565e69eea3b736b9d1f42c"
WALLET_2 = "0xD229b65d50E412cC3C394233E7a53A1DAc4dA457"

TRIGGERS = ("test_0007", "confirm_below_0007", "low_sweep", "invalidation", "target_558", "target_506")


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
        log.error(f"ake_setup_monitor: atomic write to {path} failed ({e})")
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return False


def _default_state() -> dict:
    return {
        "armed": {t: True for t in TRIGGERS},  # True = готов сработать, False = в cooldown до сброса >2%
        "last_closed_15m_ts": None,
        "last_closed_1h_ts": None,
    }


def _load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
        for t in TRIGGERS:
            state.setdefault("armed", {}).setdefault(t, True)
        return state
    except Exception:
        return _default_state()


def _save_state(state: dict) -> None:
    _atomic_write_json(STATE_FILE, state)


def _fetch_klines_bybit(interval: str, limit: int):
    r = requests.get(BYBIT_KLINE_URL, params={
        "category": "linear", "symbol": AKE_SYMBOL, "interval": interval, "limit": limit,
    }, timeout=REQUEST_TIMEOUT_SEC)
    d = r.json()
    if d.get("retCode") != 0:
        raise RuntimeError(f"bybit kline error: {d.get('retMsg')}")
    rows = list(reversed(d["result"]["list"]))
    return [{"ts": int(row[0]), "o": float(row[1]), "h": float(row[2]),
              "l": float(row[3]), "c": float(row[4])} for row in rows]


def _fetch_klines_bingx(interval: str, limit: int):
    bingx_interval = {"15": "15m", "60": "1h"}[interval]
    r = requests.get(BINGX_KLINE_URL, params={
        "symbol": "AKE-USDT", "interval": bingx_interval, "limit": limit,
    }, timeout=REQUEST_TIMEOUT_SEC)
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"bingx kline error: {d.get('msg')}")
    rows = list(reversed(d.get("data", [])))
    return [{"ts": int(row["time"]), "o": float(row["open"]), "h": float(row["high"]),
              "l": float(row["low"]), "c": float(row["close"])} for row in rows]


def get_klines(interval: str, limit: int = CANDLE_LIMIT, dead_sources: set = None):
    """Bybit первичный, BingX -- резерв. `dead_sources` -- опциональный set,
    разделяемый между несколькими вызовами В ОДНОМ ТИКЕ (владелец, критический
    регресс bsc_wallet_monitor 2026-07-15, #240 -- источник, отказавший один раз
    в этом тике, не повторяется на следующем вызове в том же тике)."""
    if dead_sources is None:
        dead_sources = set()
    if "bybit" not in dead_sources:
        try:
            return _fetch_klines_bybit(interval, limit), "bybit"
        except Exception as e:
            log.info(f"ake_setup_monitor: bybit klines ({interval}) failed: {e}, "
                     f"помечаю мёртвым до конца тика, пробую bingx")
            dead_sources.add("bybit")
    if "bingx" not in dead_sources:
        try:
            return _fetch_klines_bingx(interval, limit), "bingx"
        except Exception as e:
            log.info(f"ake_setup_monitor: bingx klines ({interval}) failed: {e}, "
                     f"помечаю мёртвым до конца тика")
            dead_sources.add("bingx")
    raise RuntimeError(f"ake_setup_monitor: все источники ({sorted(dead_sources)}) "
                        f"отказали для interval={interval}")


async def _notify_source_down(state: dict, bot, send_system_fn) -> None:
    """Честное [SYS]-уведомление при отказе ВСЕХ источников, rate-limited раз в
    SOURCE_DOWN_NOTIFY_INTERVAL_SEC -- владелец В ПОЗИЦИИ, "не молчать и не копить",
    но и не спамить на каждый минутный тик."""
    last_notify = state.get("last_source_down_notify_ts", 0)
    if time.time() - last_notify < SOURCE_DOWN_NOTIFY_INTERVAL_SEC:
        return
    try:
        await send_system_fn(bot, "⚠️ AKE-сетап-монитор: все источники свечей "
                                   "(Bybit/BingX) отказали -- проверка триггеров "
                                   "временно недоступна, повторные попытки "
                                   "продолжаются", critical=True)
    except Exception as e:
        log.error(f"ake_setup_monitor: не удалось отправить honest down-notify: {e}")
    state["last_source_down_notify_ts"] = time.time()


def _wallet_moved_24h(wallet: str) -> bool:
    """Честно читает journal/bsc_wallet_events.json (пишет bsc_wallet_monitor.py,
    задача #226) -- любой Transfer FROM этого кошелька за последние 24ч = "двигался".
    Файл может не существовать (поллер ещё не набрал событий) -- честно False, не
    выдумывает движение."""
    try:
        with open(BSC_EVENTS_FILE) as f:
            events = json.load(f)
    except Exception:
        return False
    cutoff = time.time() - 24 * 3600
    return any(e.get("from", "").lower() == wallet.lower() and e.get("ts", 0) >= cutoff for e in events)


def _wallets_status_line() -> str:
    m1 = "двигался" if _wallet_moved_24h(WALLET_1) else "без движения"
    m2 = "двигался" if _wallet_moved_24h(WALLET_2) else "без движения"
    return f"👛 Кошельки автора за 24ч: #1 {m1}, #2 {m2}"


def _check_trigger(state: dict, name: str, condition: bool, reset_condition: bool) -> bool:
    """Дедуп "1 раз до сброса >2%": возвращает True, если алерт должен сработать
    ПРЯМО СЕЙЧАС (condition met И armed). После срабатывания armed=False, пока не
    выполнится reset_condition (цена отошла >2% от уровня в противоположную сторону)."""
    armed = state["armed"].get(name, True)
    if condition and armed:
        state["armed"][name] = False
        return True
    if reset_condition and not armed:
        state["armed"][name] = True
    return False


def format_test_alert() -> str:
    return (f"⚠️ AKE у ключевого уровня автора 0.0007 -- следи за закрепом. "
            f"Твой SL должен быть ≥{INVALIDATION_LEVEL}\n{_wallets_status_line()}")


FINAL_TARGET = 0.000246


def format_confirm_alert() -> str:
    return (f"✅ AKE: условия автора выполнены -- закреп <{LEVEL_0007}. "
            f"План: SL за {INVALIDATION_LEVEL}, цели {MID_TARGET} → {FINAL_TARGET}\n"
            f"{_wallets_status_line()}")


def format_low_sweep_alert(kind: str) -> str:
    return (f"🩸 AKE: лой {LOW_SWEEP_LEVEL} снят ({kind}) -- по сценарию автора возможен "
            f"вынос вверх перед продолжением. Частичная фиксация по плану\n{_wallets_status_line()}")


def format_invalidation_alert() -> str:
    return (f"🚫 AKE: сетап автора инвалидирован -- закреп выше {INVALIDATION_LEVEL}. "
            f"Позиция под стопом, не отодвигать\n{_wallets_status_line()}")


def format_target_alert(level: float) -> str:
    return f"🎯 AKE: цель {level} достигнута -- зона частичной фиксации\n{_wallets_status_line()}"


async def check_ake_setup(bot, send_system_fn=None, run_in_executor_fn=None) -> list:
    """Владелец, критический регресс bsc_wallet_monitor 2026-07-15 (#240) --
    `run_in_executor_fn` распространяет тот же фикс сюда (владелец В ПОЗИЦИИ,
    этот монитор -- главный алерт-пакет по сделке)."""
    if send_system_fn is None:
        import bot as bot_module
        send_system_fn = bot_module.send_system
    if run_in_executor_fn is None:
        import asyncio
        loop = asyncio.get_event_loop()
        run_in_executor_fn = lambda fn, *a: loop.run_in_executor(None, fn, *a)

    state = _load_state()
    sent = []
    dead_sources = set()  # разделяется между вызовами get_klines в ЭТОМ тике (15м + 1H)

    try:
        candles_15m, src15 = await run_in_executor_fn(get_klines, "15", CANDLE_LIMIT, dead_sources)
    except Exception as e:
        log.error(f"ake_setup_monitor: ВСЕ источники 15м-свечей отказали: {e}")
        await _notify_source_down(state, bot, send_system_fn)
        _save_state(state)
        return sent

    last_ts = state.get("last_closed_15m_ts")
    if last_ts is None:
        state["last_closed_15m_ts"] = candles_15m[-1]["ts"] if candles_15m else None
        _save_state(state)
        log.info(f"ake_setup_monitor: первый запуск, источник={src15}, старт с ts={state['last_closed_15m_ts']}")
        return sent

    new_15m = [c for c in candles_15m if c["ts"] > last_ts]
    for c in new_15m:
        # A1: тест 0.0007 (касание) -- сброс, когда цена отошла >2% НИЖЕ уровня
        if _check_trigger(state, "test_0007", c["h"] >= LEVEL_0007,
                           c["c"] <= LEVEL_0007 * (1 - RESET_MOVE_PCT / 100)):
            await send_system_fn(bot, format_test_alert(), critical=True)
            sent.append("test_0007")

        # A2: закреп ниже 0.0007 (закрытие) -- сброс, когда цена отошла >2% ВЫШЕ уровня
        if _check_trigger(state, "confirm_below_0007", c["c"] < LEVEL_0007,
                           c["c"] >= LEVEL_0007 * (1 + RESET_MOVE_PCT / 100)):
            await send_system_fn(bot, format_confirm_alert(), critical=True)
            sent.append("confirm_below_0007")

        # A3: снятие лоя 0.0005065 (фитиль ИЛИ закрытие) -- сброс >2% выше уровня
        wick_break = c["l"] <= LOW_SWEEP_LEVEL
        close_break = c["c"] <= LOW_SWEEP_LEVEL
        if _check_trigger(state, "low_sweep", wick_break,
                           c["c"] >= LOW_SWEEP_LEVEL * (1 + RESET_MOVE_PCT / 100)):
            kind = "закрытием" if close_break else "фитилём"
            await send_system_fn(bot, format_low_sweep_alert(kind), critical=True)
            sent.append("low_sweep")

        # A5: промежуточные цели (касание) -- сброс >2% выше уровня (цель снизу)
        if _check_trigger(state, "target_558", c["l"] <= INTERIM_TARGET,
                           c["c"] >= INTERIM_TARGET * (1 + RESET_MOVE_PCT / 100)):
            await send_system_fn(bot, format_target_alert(INTERIM_TARGET), critical=True)
            sent.append("target_558")
        if _check_trigger(state, "target_506", c["l"] <= MID_TARGET,
                           c["c"] >= MID_TARGET * (1 + RESET_MOVE_PCT / 100)):
            await send_system_fn(bot, format_target_alert(MID_TARGET), critical=True)
            sent.append("target_506")

        state["last_closed_15m_ts"] = c["ts"]

    # A4: инвалидация -- закрытие 1H выше 0.00073, сброс >2% ниже уровня
    try:
        candles_1h, _ = await run_in_executor_fn(get_klines, "60", CANDLE_LIMIT, dead_sources)
    except Exception as e:
        log.info(f"ake_setup_monitor: 1H-свечи для инвалидации недоступны: {e}")
        candles_1h = []
    last_1h_ts = state.get("last_closed_1h_ts")
    new_1h = [c for c in candles_1h if last_1h_ts is None or c["ts"] > last_1h_ts]
    for c in new_1h:
        if _check_trigger(state, "invalidation", c["c"] > INVALIDATION_LEVEL,
                           c["c"] <= INVALIDATION_LEVEL * (1 - RESET_MOVE_PCT / 100)):
            await send_system_fn(bot, format_invalidation_alert(), critical=True)
            sent.append("invalidation")
        state["last_closed_1h_ts"] = c["ts"]

    _save_state(state)
    return sent
