"""
shadow_engine.py -- теневой контур для 5 патчей ta_extra.py/bot.py (ночная сессия #2,
Блок 1). Считает АЛЬТЕРНАТИВНЫЙ (не боевой) расчёт сигнала на уже готовых данных живого
сигнала (result из fa_engine.build_full_analysis(), вызывается из signal_loop._send_alert
ПОСЛЕ того, как боевой сигнал уже отправлен и записан в signal_journal) и пишет только в
journal/shadow_signals.json. Ни разу не влияет на то, что отправляется подписчику/
владельцу -- см. SHADOW_MODE.md. Не делает новых сетевых вызовов для самого расчёта:
переиспользует candles_4h, уже полученные боевым расчётом. Единственная сеть здесь --
опциональная персистентность записи в GitHub (см. ниже).

Хранение: тот же паттерн, что и signal_journal.py -- локальный JSON (Railway ephemeral,
обнуляется при редеплое) ПЛЮС best-effort персистентность через GitHub Contents API
(journal/shadow_signals.json, отдельный от journal/signals.json путь в том же репо).
В отличие от signal_journal.py, записи здесь ИММУТАБЕЛЬНЫ (создаются один раз, не
обновляются по ходу сделки) -- конфликт при PUT (409) решается простым повтором
(re-GET + append + PUT), без last-write-wins по id мерджа, который нужен только для
изменяемых записей.

Любое исключение внутри log_shadow_async() гасится здесь же -- падение теневого расчёта
не может сломать боевой сигнал (вызывающая сторона в signal_loop.py дополнительно
оборачивает вызов в try/except, defense-in-depth).

Патчи, задействованные здесь (сами патчи НЕ вмержены в live-константы/поведение --
см. patches/*/README.md, `git diff` на bot.py/ta_extra.py чист от live-логики):
  01 killzone-hours   -- bot.get_killzone_status_shadow() (новая ф-ция, live
                          get_killzone_status() не тронута)
  02 rr-gate          -- SR_MIN_RR_TP1_SHADOW = 2.0 ниже (live ta_extra.SR_MIN_RR_TP1
                          остаётся 1.5)
  03 breaker/MB       -- ta_extra.classify_breaker_or_mitigation() (аддитивна)
  04 RSI-дивергенция  -- ta_extra.detect_price_indicator_divergence() (аддитивна)
  05 BPR              -- ta_extra.detect_bpr_zones() (аддитивна)
"""
import asyncio
import base64
import json
import os
import time

import requests

import signal_journal   # переиспользуем _github_configured/_validate_github_token/_github_headers/_github_api_base
import ta_extra

SHADOW_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "journal", "shadow_signals.json")
GITHUB_SHADOW_PATH = "journal/shadow_signals.json"
SR_MIN_RR_TP1_SHADOW = 2.0  # патч 02 -- см. patches/02-rr-gate/README.md, НЕ live-константа


def _atomic_write_json(path: str, obj) -> bool:
    """Тот же паттерн, что и signal_journal._atomic_write_json -- временный файл в той же
    директории + os.replace (атомарно на POSIX), крах процесса не оставляет битый JSON."""
    tmp_path = f"{path}.tmp{os.getpid()}"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp_path, "w") as f:
            json.dump(obj, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        return True
    except Exception as e:
        print(f"shadow_engine: atomic write to {path} failed ({e})")
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return False


def _load_local() -> list:
    if not os.path.exists(SHADOW_FILE):
        return []
    try:
        with open(SHADOW_FILE) as f:
            data = json.load(f)
        return data.get("records", []) if isinstance(data, dict) else []
    except Exception:
        return []


def _dedup_key(rec: dict):
    return (rec.get("symbol"), rec.get("ts"))


def _github_get_shadow_sync():
    """GET journal/shadow_signals.json. Возвращает (records list, sha) либо (None, None),
    если файла ещё нет / GitHub не настроен / запрос не удался. Синхронно -- вызывать
    только через run_in_executor из async-кода (см. signal_journal._github_get_file_sync,
    тот же паттерн)."""
    if not signal_journal._github_configured():
        return None, None
    token_issue = signal_journal._validate_github_token()
    if token_issue:
        print(f"shadow_engine: {token_issue}")
        return None, None
    try:
        r = requests.get(f"{signal_journal._github_api_base()}/contents/{GITHUB_SHADOW_PATH}",
                          headers=signal_journal._github_headers(), timeout=15)
        if r.status_code == 404:
            return None, None
        r.raise_for_status()
        data = r.json()
        content = base64.b64decode(data["content"]).decode()
        payload = json.loads(content)
        records = payload.get("records", []) if isinstance(payload, dict) else []
        return records, data["sha"]
    except Exception as e:
        detail = getattr(getattr(e, "response", None), "text", "")
        print(f"shadow_engine: GitHub GET failed ({e} {detail[:300]})")
        return None, None


def _github_put_shadow_sync(records: list, sha):
    """PUT journal/shadow_signals.json. Возвращает новый sha при успехе, None при ошибке,
    "conflict" при 409 (sha устарел -- вызывающий должен перечитать и повторить)."""
    if not signal_journal._github_configured():
        return None
    token_issue = signal_journal._validate_github_token()
    if token_issue:
        print(f"shadow_engine: {token_issue}")
        return None
    try:
        payload = {"schema_version": 1, "records": records}
        body = {
            "message": f"shadow: {len(records)} записей",
            "content": base64.b64encode(json.dumps(payload, ensure_ascii=False).encode()).decode(),
        }
        if sha:
            body["sha"] = sha
        r = requests.put(f"{signal_journal._github_api_base()}/contents/{GITHUB_SHADOW_PATH}",
                          headers=signal_journal._github_headers(), json=body, timeout=20)
        if r.status_code == 409:
            return "conflict"
        r.raise_for_status()
        return r.json()["content"]["sha"]
    except Exception as e:
        detail = getattr(getattr(e, "response", None), "text", "")
        print(f"shadow_engine: GitHub PUT failed ({e} {detail[:300]})")
        return None


def _sync_to_github_sync(new_record: dict) -> bool:
    """GET текущего состояния из GitHub, append новой записи (дедуп по symbol+ts), PUT.
    Один повтор при 409-конфликте (записи иммутабельны -- дедуп + повторный PUT решает
    конфликт без сложного merge). Best-effort -- отсутствие GITHUB_TOKEN просто пропускает
    шаг (см. _github_get_shadow_sync), это не ошибка."""
    for attempt in range(2):
        remote, sha = _github_get_shadow_sync()
        if remote is None and sha is None and not signal_journal._github_configured():
            return False  # GitHub не настроен -- локальная запись уже сделана, этого достаточно
        records = remote or []
        keys = {_dedup_key(r) for r in records}
        if _dedup_key(new_record) not in keys:
            records.append(new_record)
        result = _github_put_shadow_sync(records, sha)
        if result == "conflict":
            continue  # повтор со свежим sha
        return bool(result)
    return False


def compute_whale_confluence(classified_by_side: dict, whale_zones: dict) -> dict:
    """Патч 06 (Whale Radar, Блок 2): пересечение K-LVL POI-зон
    (`result['block4_poi']['classified_by_side']`, только `klvl=True` -- обычные S/R
    дали бы слишком много ложных пересечений) с текущими whale-зонами
    (`whale_radar.WhaleRadarState.get_zones(symbol)`). Чистая функция, без сети/I-O --
    тестируется без реального стакана. `below`-сторона POI сопоставляется с `bid`-
    зонами, `above` с `ask` НАПРЯМУЮ, без сверки с last_price: bid < ask всегда
    гарантировано движком биржи (иначе ордера были бы немедленно исполнены), так что
    bid-зоны структурно всегда ниже цены, ask -- всегда выше."""
    matches = []
    side_map = {"below": "bid", "above": "ask"}
    for poi_side, whale_side in side_map.items():
        klvl_zones = [z for z in (classified_by_side.get(poi_side) or []) if z.get("klvl")]
        if not klvl_zones:
            continue
        for wz in (whale_zones.get(whale_side) or []):
            for kz in klvl_zones:
                if wz["price_lo"] <= kz["hi"] and wz["price_hi"] >= kz["lo"]:
                    matches.append({
                        "poi_side": poi_side, "poi_lo": kz["lo"], "poi_hi": kz["hi"],
                        "poi_touches": kz.get("touches"),
                        "whale_side": whale_side, "whale_lo": wz["price_lo"],
                        "whale_hi": wz["price_hi"], "whale_usd": wz["total_usd"],
                    })
    return {"whale_klvl_confluence": bool(matches), "whale_klvl_matches": matches}


def compute_shadow(symbol: str, result: dict, bot_module, live_journal_id=None,
                    whale_zones: dict = None) -> dict:
    """Строит один shadow-рекорд по уже посчитанному result (fa_engine.build_full_analysis()).
    Каждый патч обёрнут в свой try/except -- падение одного не портит остальные поля.
    `whale_zones` — опционально, {"bid": [...], "ask": [...]} от
    `whale_radar.WhaleRadarState.get_zones(symbol)` (обычно прокинуто через
    `bot_module.get_whale_zones(symbol)`, см. `signal_loop._send_alert()`); None,
    если Whale Radar не запущен/недоступен — патч 06 тогда честно пропускается
    (не выдумывает confluence на отсутствующих данных)."""
    b11 = result.get("block11_trade_plan", {}) or {}
    direction = b11.get("direction")
    candles_4h = result.get("candles_4h") or []
    rr_tp1 = b11.get("rr_tp1")

    affected = []
    discrepancy = []

    # Патч 01: killzone hours
    try:
        kz_live = bot_module.get_killzone_status()
        kz_shadow = bot_module.get_killzone_status_shadow()
        live_good = kz_live.get("active", {}).get("quality") in ("A+", "A")
        shadow_good = kz_shadow.get("active", {}).get("quality") in ("A+", "A")
        if live_good != shadow_good:
            affected.append("01-killzone-hours")
            discrepancy.append(
                f"killzone: live={'good' if live_good else 'not-good'} "
                f"({kz_live.get('active', {}).get('name')}) vs "
                f"shadow={'good' if shadow_good else 'not-good'} "
                f"({kz_shadow.get('active', {}).get('name')})"
            )
    except Exception as e:
        discrepancy.append(f"killzone shadow calc failed: {e}")

    # Патч 02: R:R-гейт 2.0 (сравнение с уже посчитанным rr_tp1, без пересчёта сделки)
    shadow_gate_pass = None
    if rr_tp1 is not None:
        shadow_gate_pass = rr_tp1 >= SR_MIN_RR_TP1_SHADOW
        if not shadow_gate_pass:
            affected.append("02-rr-gate")
            discrepancy.append(
                f"rr_gate: R:R {rr_tp1} прошёл live-гейт (1.5), но НЕ прошёл бы shadow-гейт (2.0)"
            )

    # Патч 03: breaker/mitigation
    breaker = {"type": None}
    if candles_4h and direction:
        try:
            breaker = ta_extra.classify_breaker_or_mitigation(candles_4h, direction)
            if breaker.get("type"):
                affected.append("03-breaker-mitigation")
        except Exception as e:
            discrepancy.append(f"breaker/MB calc failed: {e}")

    # Патч 04: RSI-дивергенция (контрарианская трактовка -- классическая ПРОТИВ
    # направления сигнала считается настораживающей, см. ta_extra.py докстринг)
    divergence = {}
    if candles_4h:
        try:
            divergence = ta_extra.detect_price_indicator_divergence(candles_4h)
            against_direction = (
                (direction == "long" and divergence.get("bearish_classical")) or
                (direction == "short" and divergence.get("bullish_classical"))
            )
            if against_direction:
                affected.append("04-rsi-divergence")
                discrepancy.append("divergence: классическая дивергенция ПРОТИВ направления сигнала")
        except Exception as e:
            discrepancy.append(f"divergence calc failed: {e}")

    # Патч 05: BPR confluence с уже построенной зоной входа
    bpr_zones = []
    bpr_confluence = False
    if candles_4h:
        try:
            bpr_zones = ta_extra.detect_bpr_zones(candles_4h)
            entry_lo = b11.get("entry3") if direction == "long" else b11.get("entry1")
            entry_hi = b11.get("entry1") if direction == "long" else b11.get("entry3")
            if entry_lo is not None and entry_hi is not None:
                lo, hi = min(entry_lo, entry_hi), max(entry_lo, entry_hi)
                for z in bpr_zones[:5]:
                    if z["lo"] <= hi and z["hi"] >= lo:
                        bpr_confluence = True
                        break
            if bpr_confluence:
                affected.append("05-bpr")
                discrepancy.append("bpr: зона входа пересекается со свежим BPR (confluence)")
        except Exception as e:
            discrepancy.append(f"bpr calc failed: {e}")

    # Патч 06: Whale Radar confluence с K-LVL POI-зонами (Блок 2, 2026-07-11, решение
    # владельца -- "влияние на скоринг только shadow"). `whale_zones` приходит УЖЕ
    # посчитанным от вызывающей стороны (`bot_module.get_whale_zones(symbol)`, читает
    # живое состояние `whale_radar.WhaleRadarState`) -- сам compute_shadow() не делает
    # сетевых вызовов и не знает про whale_radar напрямую, только про готовые данные.
    whale_conf = {"whale_klvl_confluence": False, "whale_klvl_matches": []}
    if whale_zones is not None:
        try:
            classified = (result.get("block4_poi", {}) or {}).get("classified_by_side") or {}
            whale_conf = compute_whale_confluence(classified, whale_zones)
            if whale_conf["whale_klvl_confluence"]:
                affected.append("06-whale-confluence")
                discrepancy.append(
                    f"whale: {len(whale_conf['whale_klvl_matches'])} K-LVL зон(а) "
                    f"пересекается с whale-зоной(ами) в стакане"
                )
        except Exception as e:
            discrepancy.append(f"whale confluence calc failed: {e}")

    return {
        "ts": time.time(),
        "symbol": symbol,
        "direction": direction,
        "entry_lo": b11.get("entry3") if direction == "long" else b11.get("entry1"),
        "entry_hi": b11.get("entry1") if direction == "long" else b11.get("entry3"),
        "sl": b11.get("sl"),
        "tp1": b11.get("tp1"), "tp2": b11.get("tp2"), "tp3": b11.get("tp3"),
        "rr_tp1_live": rr_tp1,
        "shadow_rr_gate_pass": shadow_gate_pass,
        "breaker_mitigation": breaker.get("type"),
        "divergence": {k: v for k, v in divergence.items() if k != "detail"} if divergence else {},
        "bpr_zone_count": len(bpr_zones),
        "bpr_confluence": bpr_confluence,
        "whale_klvl_confluence": whale_conf["whale_klvl_confluence"],
        "whale_klvl_matches": whale_conf["whale_klvl_matches"],
        "patches_affected": affected,
        "discrepancy": discrepancy,
        "live_journal_id": live_journal_id,
    }


def _write_local(record: dict) -> bool:
    records = _load_local()
    records.append(record)
    return _atomic_write_json(SHADOW_FILE, {"schema_version": 1, "records": records})


def log_shadow(symbol: str, result: dict, bot_module, live_journal_id=None,
                whale_zones: dict = None) -> bool:
    """Синхронная версия -- считает shadow-рекорд и дописывает ТОЛЬКО в локальный файл
    (без GitHub-персистентности). Используется в тестах/смоуках и как фоллбек, если нет
    активного event loop. Для боевого пути из signal_loop.py используется
    log_shadow_async() ниже (та же локальная запись + best-effort пуш в GitHub)."""
    try:
        record = compute_shadow(symbol, result, bot_module, live_journal_id, whale_zones)
        return _write_local(record)
    except Exception as e:
        print(f"shadow_engine.log_shadow failed for {symbol}: {e}")
        return False


async def log_shadow_async(symbol: str, result: dict, bot_module, live_journal_id=None,
                            whale_zones: dict = None) -> bool:
    """Боевой путь (вызывается из signal_loop._send_alert). Один расчёт record (не
    дважды -- иначе локальная и GitHub-копии разошлись бы по ts), локальная запись
    всегда (быстро, без сети) + best-effort пуш В ТОЙ ЖЕ record в GitHub через
    run_in_executor (не блокирует event loop, не критичен -- Railway ephemeral, но
    локальная копия уже сохранена к этому моменту)."""
    try:
        record = compute_shadow(symbol, result, bot_module, live_journal_id, whale_zones)
    except Exception as e:
        print(f"shadow_engine.log_shadow_async: compute failed for {symbol}: {e}")
        return False
    ok_local = _write_local(record)
    if not ok_local:
        return False
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _sync_to_github_sync, record)
    except Exception as e:
        print(f"shadow_engine: GitHub sync failed (локальная запись уже сохранена): {e}")
    return True


def _build_pump_reversal_record(symbol: str, watch: dict, funding, oi_usd, oi_change_pct,
                                 promoted_live) -> dict:
    """Ночная сессия #2, Блок 4: кандидат SHORT после подтверждённого разворота пампа
    (памп -> откат >= REVERSAL_DRAWDOWN_PCT% от пика с объёмом >= REVERSAL_VOL_MULT --
    см. pump_detector.py, эта проверка УЖЕ существует в живом коде, здесь только
    измерительное логирование поверх неё). Вызывается ПОСЛЕ уже существующего live-пути
    (алерт владельцу уже отправлен, `_try_promote_pump` уже отработал) -- не влияет ни
    на что боевое, чисто накопление данных для последующей оценки."""
    peak = watch.get("peak_price")
    last = watch.get("last_price")
    retrace_pct = round((peak - last) / peak * 100, 2) if peak else None
    return {
        "ts": time.time(),
        "type": "pump_reversal_shadow",
        "symbol": symbol,
        "direction": "short",
        "peak_price": peak,
        "last_price": last,
        "retrace_pct": retrace_pct,
        "volume_mult": watch.get("volume_mult"),
        "z_score": watch.get("z_score"),
        "funding_pct": funding,
        "oi_usd": oi_usd,
        "oi_change_pct": oi_change_pct,
        "entry": watch.get("entry_lo"),
        "sl": watch.get("sl"),
        "tp1": watch.get("tp1"),
        "tp2": watch.get("tp2"),
        "promoted_live": promoted_live,
    }


def log_pump_reversal_shadow(symbol: str, watch: dict, funding, oi_usd, oi_change_pct,
                              promoted_live) -> bool:
    """Синхронная версия -- см. _build_pump_reversal_record. Локальная запись только."""
    try:
        record = _build_pump_reversal_record(symbol, watch, funding, oi_usd, oi_change_pct,
                                              promoted_live)
        return _write_local(record)
    except Exception as e:
        print(f"shadow_engine.log_pump_reversal_shadow failed for {symbol}: {e}")
        return False


async def log_pump_reversal_shadow_async(symbol: str, watch: dict, funding, oi_usd,
                                          oi_change_pct, promoted_live) -> bool:
    """Боевой путь -- вызывается из pump_detector._confirm_pump_reversal(). Локальная
    запись + best-effort пуш в GitHub, тот же паттерн, что и log_shadow_async()."""
    try:
        record = _build_pump_reversal_record(symbol, watch, funding, oi_usd, oi_change_pct,
                                              promoted_live)
    except Exception as e:
        print(f"shadow_engine.log_pump_reversal_shadow_async: build failed for {symbol}: {e}")
        return False
    ok_local = _write_local(record)
    if not ok_local:
        return False
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _sync_to_github_sync, record)
    except Exception as e:
        print(f"shadow_engine: GitHub sync failed for pump_reversal ({symbol}): {e}")
    return True
