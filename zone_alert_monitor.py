"""
zone_alert_monitor.py -- generic, конфиг-driven движок для срочных зон-нарядов
владельца (KAITOUSDT 2026-07-15, AVAXUSDT 2026-07-15, и далее). Один движок вместо
бесплатного копирования bank_setup_monitor.py/ake_setup_monitor.py под каждый новый
символ -- владелец добавляет зоны быстрее, чем оправдан ручной bespoke-модуль на
каждую.

Конфиг на символ -- список триггеров (see TriggerConfig ниже), тип каждого:
  "touch"           -- low<=level<=high на любом баре (касание, направление-агнотично)
  "close_below"     -- close < level (закреп ниже, для инвалидации/подтверждения)
  "close_above"     -- close > level (закреп выше)
Таймфрейм триггера -- "15" или "60" (для 1H-инвалидаций).

Дедуп -- тот же паттерн "1 раз до сброса >2%", что в ake_setup_monitor.py.
Профильная строка (капа/Liq-MCap/unlock/holders) -- добавляется ТОЛЬКО к первому
когда-либо отправленному алерту символа (state["profile_sent"]).
Скальп -- опционально на триггер (scalp_direction != None) через scalp_evidence.py.
"""
import json
import logging
import os
import time

import requests

log = logging.getLogger(__name__)

RESET_MOVE_PCT = 2.0
POLL_INTERVAL_SEC = 60
REQUEST_TIMEOUT_SEC = 10  # владелец, критический регресс bsc_wallet_monitor 2026-07-15
# (#240) -- жёсткий потолок на КАЖДЫЙ сетевой вызов, тот же паттерн распространён сюда
SOURCE_DOWN_NOTIFY_INTERVAL_SEC = 15 * 60  # честный [SYS] раз в 15 мин при отказе
# ВСЕХ источников для символа, не молчать и не спамить на каждый минутный тик

_JOURNAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "journal")

BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline"
BINGX_KLINE_URL = "https://open-api.bingx.com/openApi/swap/v3/quote/klines"


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
        log.error(f"zone_alert_monitor: atomic write to {path} failed ({e})")
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return False


def _state_file(symbol: str) -> str:
    return os.path.join(_JOURNAL_DIR, f"zone_alert_state_{symbol.lower()}.json")


TF_STATE_KEY = {"15": "last_closed_15m_ts", "60": "last_closed_1h_ts", "D": "last_closed_1d_ts"}


def _default_state(trigger_names: list) -> dict:
    return {
        "armed": {t: True for t in trigger_names},
        "last_closed_15m_ts": None,
        "last_closed_1h_ts": None,
        "last_closed_1d_ts": None,
        "profile_sent": False,
    }


def _load_state(symbol: str, trigger_names: list) -> dict:
    try:
        with open(_state_file(symbol)) as f:
            state = json.load(f)
        for t in trigger_names:
            state.setdefault("armed", {}).setdefault(t, True)
        return state
    except Exception:
        return _default_state(trigger_names)


def _save_state(symbol: str, state: dict) -> None:
    _atomic_write_json(_state_file(symbol), state)


def _bingx_symbol(symbol: str) -> str:
    base = symbol.replace("USDT", "")
    return f"{base}-USDT"


def _fetch_klines_bybit(symbol: str, interval: str, limit: int):
    r = requests.get(BYBIT_KLINE_URL, params={
        "category": "linear", "symbol": symbol, "interval": interval, "limit": limit,
    }, timeout=REQUEST_TIMEOUT_SEC)
    d = r.json()
    if d.get("retCode") != 0:
        raise RuntimeError(f"bybit kline error: {d.get('retMsg')}")
    rows = list(reversed(d["result"]["list"]))
    return [{"ts": int(row[0]), "o": float(row[1]), "h": float(row[2]),
              "l": float(row[3]), "c": float(row[4]), "v": float(row[5])} for row in rows]


def _fetch_klines_bingx(symbol: str, interval: str, limit: int):
    bingx_interval = {"15": "15m", "60": "1h", "D": "1d"}[interval]
    r = requests.get(BINGX_KLINE_URL, params={
        "symbol": _bingx_symbol(symbol), "interval": bingx_interval, "limit": limit,
    }, timeout=REQUEST_TIMEOUT_SEC)
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"bingx kline error: {d.get('msg')}")
    rows = list(reversed(d.get("data", [])))
    return [{"ts": int(row["time"]), "o": float(row["open"]), "h": float(row["high"]),
              "l": float(row["low"]), "c": float(row["close"]), "v": float(row.get("volume", 0))}
             for row in rows]


def get_klines(symbol: str, interval: str, limit: int = 40, dead_sources: set = None):
    """Bybit первичный, BingX -- резерв. `dead_sources` -- опциональный set,
    разделяемый между несколькими вызовами В ОДНОМ ТИКЕ (check_zone вызывает её
    по разу на каждый нужный таймфрейм) -- владелец, критический регресс
    bsc_wallet_monitor 2026-07-15 (#240): источник, отказавший один раз в этом
    тике, не повторяется на следующем вызове в том же тике."""
    if dead_sources is None:
        dead_sources = set()
    if "bybit" not in dead_sources:
        try:
            return _fetch_klines_bybit(symbol, interval, limit), "bybit"
        except Exception as e:
            log.info(f"zone_alert_monitor: bybit klines {symbol}({interval}) failed: {e}, "
                     f"помечаю мёртвым до конца тика, пробую bingx")
            dead_sources.add("bybit")
    if "bingx" not in dead_sources:
        try:
            return _fetch_klines_bingx(symbol, interval, limit), "bingx"
        except Exception as e:
            log.info(f"zone_alert_monitor: bingx klines {symbol}({interval}) failed: {e}, "
                     f"помечаю мёртвым до конца тика")
            dead_sources.add("bingx")
    raise RuntimeError(f"zone_alert_monitor: все источники ({sorted(dead_sources)}) "
                        f"отказали для {symbol}({interval})")


async def _notify_source_down(symbol: str, state: dict, bot, send_system_fn) -> None:
    """Честное [SYS]-уведомление при отказе ВСЕХ источников для символа,
    rate-limited раз в SOURCE_DOWN_NOTIFY_INTERVAL_SEC (тот же паттерн, что
    bsc_wallet_monitor #240)."""
    last_notify = state.get("last_source_down_notify_ts", 0)
    if time.time() - last_notify < SOURCE_DOWN_NOTIFY_INTERVAL_SEC:
        return
    try:
        await send_system_fn(bot, f"⚠️ {symbol}-зона: все источники свечей (Bybit/BingX) "
                                   f"отказали -- проверка триггеров временно недоступна, "
                                   f"повторные попытки продолжаются", critical=True)
    except Exception as e:
        log.error(f"zone_alert_monitor: не удалось отправить honest down-notify для {symbol}: {e}")
    state["last_source_down_notify_ts"] = time.time()


def _check_trigger_condition(ttype: str, candle: dict, level: float) -> bool:
    if ttype == "touch":
        return candle["l"] <= level <= candle["h"]
    if ttype == "close_below":
        return candle["c"] < level
    if ttype == "close_above":
        return candle["c"] > level
    raise ValueError(f"unknown trigger type: {ttype}")


def _reset_condition(ttype: str, candle: dict, level: float) -> bool:
    """Сброс дедупа -- цена отошла >2% от уровня в сторону, откуда пришла (даёт
    возможность НОВОГО срабатывания того же триггера при повторном подходе)."""
    if ttype == "touch":
        return (candle["c"] >= level * (1 + RESET_MOVE_PCT / 100) or
                candle["c"] <= level * (1 - RESET_MOVE_PCT / 100))
    if ttype == "close_below":
        return candle["c"] >= level * (1 + RESET_MOVE_PCT / 100)
    if ttype == "close_above":
        return candle["c"] <= level * (1 - RESET_MOVE_PCT / 100)
    return False


def _check_and_fire(state: dict, name: str, condition: bool, reset_condition: bool) -> bool:
    armed = state["armed"].get(name, True)
    if condition and armed:
        state["armed"][name] = False
        return True
    if reset_condition and not armed:
        state["armed"][name] = True
    return False


async def check_zone(symbol: str, triggers: list, profile_line: str, bot, send_system_fn=None,
                      scalp_ctx: dict = None, run_in_executor_fn=None) -> list:
    """`triggers` -- список dict: {"name","type","level","timeframe","direction",
    "text","scalp_direction"(опц.)}. `profile_line` -- строка, добавляемая ТОЛЬКО к
    первому алерту. `scalp_ctx` -- {"get_killzone_status_fn","get_liq_data_fn",
    "get_oi_change_fn"} для вызова scalp_evidence, None -- скальп отключён.

    Владелец, критический регресс bsc_wallet_monitor 2026-07-15 (#240) --
    `run_in_executor_fn` распространяет тот же фикс сюда: блокирующие
    `requests`-вызовы (через get_klines) идут через executor."""
    if send_system_fn is None:
        import bot as bot_module
        send_system_fn = bot_module.send_system
    if run_in_executor_fn is None:
        import asyncio
        loop = asyncio.get_event_loop()
        run_in_executor_fn = lambda fn, *a: loop.run_in_executor(None, fn, *a)

    trigger_names = [t["name"] for t in triggers]
    state = _load_state(symbol, trigger_names)
    sent = []

    tf_needed = sorted({t.get("timeframe", "15") for t in triggers})
    klines_by_tf = {}
    dead_sources = set()  # разделяется между вызовами get_klines в ЭТОМ тике (все tf)
    any_ok = False
    for tf in tf_needed:
        try:
            klines_by_tf[tf], _src = await run_in_executor_fn(get_klines, symbol, tf, 40, dead_sources)
            any_ok = True
        except Exception as e:
            log.error(f"zone_alert_monitor: {symbol} klines({tf}) -- ВСЕ источники отказали: {e}")
            klines_by_tf[tf] = []

    if not any_ok and tf_needed:
        await _notify_source_down(symbol, state, bot, send_system_fn)
        _save_state(symbol, state)
        return sent

    is_first_run = state.get("last_closed_15m_ts") is None and "15" in klines_by_tf and klines_by_tf["15"]
    if is_first_run:
        for tf in tf_needed:
            if klines_by_tf.get(tf):
                state[TF_STATE_KEY.get(tf, "last_closed_1h_ts")] = klines_by_tf[tf][-1]["ts"]
        _save_state(symbol, state)
        log.info(f"zone_alert_monitor: {symbol} первый запуск, старт с ts={state['last_closed_15m_ts']}")
        return sent

    for t in triggers:
        tf = t.get("timeframe", "15")
        candles = klines_by_tf.get(tf, [])
        last_ts_key = TF_STATE_KEY.get(tf, "last_closed_1h_ts")
        last_ts = state.get(last_ts_key)
        new_candles = [c for c in candles if last_ts is None or c["ts"] > last_ts]

        for c in new_candles:
            fired = _check_and_fire(
                state, t["name"],
                _check_trigger_condition(t["type"], c, t["level"]),
                _reset_condition(t["type"], c, t["level"]),
            )
            if fired:
                text = t["text"]
                if not state.get("profile_sent") and profile_line:
                    text = f"{text}\n{profile_line}"
                    state["profile_sent"] = True
                scalp_dir = t.get("scalp_direction")
                if scalp_dir and scalp_ctx:
                    try:
                        import scalp_evidence
                        scalp_line = scalp_evidence.build_scalp_line(
                            symbol, scalp_dir, t["level"], klines_by_tf.get("15", []),
                            scalp_ctx["get_killzone_status_fn"], scalp_ctx["get_liq_data_fn"],
                            scalp_ctx["get_oi_change_fn"],
                        )
                        if scalp_line:
                            text = f"{text}\n{scalp_line}"
                    except Exception as e:
                        log.info(f"zone_alert_monitor: scalp eval failed for {symbol}/{t['name']}: {e}")
                await send_system_fn(bot, text, critical=True)
                sent.append(t["name"])

        if new_candles:
            state[last_ts_key] = new_candles[-1]["ts"]

    _save_state(symbol, state)
    return sent


def _zone_registry() -> list:
    """Все символы, за которыми следит этот движок -- владелец, наряды KAITOUSDT
    SHORT и AVAXUSDT LONG, 2026-07-15. Один heartbeat на весь движок (не по
    символу) -- количество символов будет расти, отдельная запись в
    _job_expected_interval_sec на каждый не масштабируется. scalp_ctx=None --
    scalp_evidence.py ещё не проверена живьём (отдельная задача владельца),
    строка скальпа просто не добавляется к алертам, пока не будет отдельного "да"."""
    import zone_alert_configs as cfg
    return [
        (cfg.KAITO_SYMBOL, cfg.build_kaito_triggers(), cfg.KAITO_PROFILE_LINE),
        (cfg.AVAX_SYMBOL, cfg.build_avax_triggers(), ""),
        (cfg.GRAM_SYMBOL, cfg.build_gram_triggers(), cfg.GRAM_PROFILE_LINE),
    ]


async def check_all_zones(bot, send_system_fn=None, run_in_executor_fn=None) -> dict:
    """Джоб (scheduler.add_job, interval POLL_INTERVAL_SEC): проходит по всем
    зарегистрированным зонам-нарядам владельца (_zone_registry()). Возвращает
    {symbol: [сработавшие триггеры]} для тестов/логов."""
    result = {}
    for symbol, triggers, profile_line in _zone_registry():
        try:
            result[symbol] = await check_zone(
                symbol, triggers, profile_line, bot,
                send_system_fn=send_system_fn, run_in_executor_fn=run_in_executor_fn,
            )
        except Exception as e:
            log.error(f"zone_alert_monitor: {symbol} check_zone упал: {e}")
            result[symbol] = []
    return result
