"""cftc_cot.py -- CFTC COT (Commitments of Traders, BTC CME futures) -- shadow-only
информационный источник (владелец, ночная очередь 2026-07-22/23, СОБРАН, ФЛАГ OFF).

По плану `CFTC_COT_INTEGRATION_PLAN.md` (живая проверка источника уже сделана там,
здесь -- реализация): `publicreporting.cftc.gov` Socrata REST, БЕЗ ключа, недельная
гранулярность (данные всегда на 8-14 дней устаревшие относительно текущего момента --
структурный потолок источника, не баг). BTC -- код `133741` в датасете "Traders in
Financial Futures, Futures-only, Combined" (`gpe5-46if`).

Флаг `CFTC_COT_ENABLED` (env, default False) -- при выключенном флаге ВСЕ функции
этого модуля -- честные no-op (`{"enabled": False}`/`None`), НИКАКИХ сетевых вызовов
не происходит вообще. Владелец, ДА, 2026-07-22/23: "флаг OFF по умолчанию" -- дефолт
намеренно False, не True, до отдельного явного включения владельцем.

Область действия -- ТОЛЬКО informational-поле, прикрепляемое к shadow-записи
(`shadow_engine.compute_shadow()` -> `cftc_cot_snapshot`), НЕ участвует в
`gate_reasons`/`promoted`/боевом чек-листе. Тот же принцип, что `amd_phase_
methodology`/остальные shadow-only патчи в том же файле.

Персистентность состояния -- `journal/cftc_cot_btc.json`, тот же паттерн
(атомарная запись через tmp+`os.replace`), что и `journal_persistence.py`."""
import json
import logging
import os
import time

import requests

log = logging.getLogger(__name__)

CFTC_COT_ENABLED = os.getenv("CFTC_COT_ENABLED", "false").strip().lower() == "true"

CFTC_DATASET_URL = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"
CFTC_BTC_CONTRACT_CODE = "133741"  # BTC, TFF Futures-only Combined -- см. INSIGHTS.md 2026-07-22
REFRESH_INTERVAL_SEC = 24 * 3600  # раз в сутки -- источник обновляется раз в неделю, чаще незачем

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "journal", "cftc_cot_btc.json")


def _to_float(value):
    """Socrata отдаёт числовые поля строками -- честный None при мусоре/отсутствии,
    не 0.0 (0.0 было бы ложным "позиция закрыта")."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_latest_report_sync() -> dict:
    """GET последнего отчёта CFTC COT для BTC. Best-effort -- {} при флаге OFF, сети
    недоступной, пустом ответе, либо любой ошибке (никогда не поднимает исключение
    наружу -- вызывающий код не должен падать из-за стороннего источника)."""
    if not CFTC_COT_ENABLED:
        return {}
    try:
        params = {
            "$where": f"cftc_contract_market_code='{CFTC_BTC_CONTRACT_CODE}'",
            "$limit": 1,
            "$order": "report_date_as_yyyy_mm_dd DESC",
        }
        r = requests.get(CFTC_DATASET_URL, params=params, timeout=15)
        r.raise_for_status()
        rows = r.json()
        return rows[0] if rows else {}
    except Exception as e:
        log.error(f"cftc_cot: fetch failed: {e}")
        return {}


def _compute_snapshot(report: dict) -> dict:
    """Сырые поля отчёта -> net leveraged-money (long-short) + % OI. `report` --
    пустой словарь -> честный {} (не выдумываем снимок из отсутствующих данных)."""
    if not report:
        return {}
    lev_long = _to_float(report.get("lev_money_positions_long"))
    lev_short = _to_float(report.get("lev_money_positions_short"))
    net = (lev_long - lev_short) if lev_long is not None and lev_short is not None else None
    return {
        "report_date": report.get("report_date_as_yyyy_mm_dd"),
        "lev_money_long": lev_long,
        "lev_money_short": lev_short,
        "lev_money_net": net,
        "open_interest_all": _to_float(report.get("open_interest_all")),
    }


def _load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception as e:
        log.error(f"cftc_cot: state load failed: {e}")
        return {}


def _save_state(state: dict) -> bool:
    tmp = f"{STATE_FILE}.tmp{os.getpid()}"
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)
        return True
    except Exception as e:
        log.error(f"cftc_cot: state write failed: {e}")
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


def refresh_if_stale_sync(now: float = None) -> dict:
    """Периодический джоб (планируется на джоб-раннере bot.py, см. CFTC_COT_
    INTEGRATION_PLAN.md -- НЕ подключено к scheduler в этом пакете, только сама
    функция готова). Раз в REFRESH_INTERVAL_SEC проверяет новый отчёт (по
    `report_date`, не перезаписывает без реального нового отчёта), сохраняет
    локально. Flag OFF -- честный no-op, состояние не трогается вообще."""
    if not CFTC_COT_ENABLED:
        return {}
    now = now if now is not None else time.time()
    state = _load_state()
    last_check = state.get("last_check_ts", 0)
    if now - last_check < REFRESH_INTERVAL_SEC:
        return state
    report = fetch_latest_report_sync()
    state["last_check_ts"] = now
    if report:
        snapshot = _compute_snapshot(report)
        if snapshot.get("report_date") != (state.get("snapshot") or {}).get("report_date"):
            state["snapshot"] = snapshot
            state["snapshot_saved_ts"] = now
    _save_state(state)
    return state


def get_shadow_snapshot(now: float = None) -> dict:
    """Informational-поле для прикрепления к сигналу (`shadow_engine.compute_
    shadow()`). НЕ вызывает сеть сама -- только читает уже сохранённое состояние
    (обновляется отдельно, см. `refresh_if_stale_sync`) + честно считает возраст
    снимка в днях (недельная гранулярность источника -- возраст в 8-14 дней это
    НОРМА этого источника, не признак поломки, см. докстринг модуля).
    Flag OFF -- {"enabled": False}, ничего больше не читает/не считает."""
    if not CFTC_COT_ENABLED:
        return {"enabled": False}
    now = now if now is not None else time.time()
    state = _load_state()
    snapshot = state.get("snapshot")
    if not snapshot:
        return {"enabled": True, "available": False}
    saved_ts = state.get("snapshot_saved_ts", now)
    age_days = round((now - saved_ts) / 86400, 1)
    return {"enabled": True, "available": True, "age_days": age_days, **snapshot}


async def refresh_cftc_cot_async(bot=None, run_in_executor_fn=None) -> dict:
    """Джоб-обёртка для scheduler.add_job (см. bot.py -- НЕ подключено в этом
    пакете, только функция готова к подключению). `run_in_executor_fn` -- для
    тестов, в проде обычный `loop.run_in_executor` (тот же паттерн, что во всех
    остальных мониторах проекта)."""
    if run_in_executor_fn is None:
        import asyncio
        loop = asyncio.get_event_loop()
        run_in_executor_fn = lambda fn, *a: loop.run_in_executor(None, fn, *a)
    return await run_in_executor_fn(refresh_if_stale_sync)
