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
  07 order-block-body -- ta_extra.detect_order_block() (аддитивна, Пакет 7 М1,
                          shadow-only вариант B из Пакета 5 М2/6 М1 -- живой
                          pro_analysis() геометрию НЕ меняет)
  08 chart-patterns   -- chart_patterns.py (Пакет 8 М3, находка Булковского --
                          флаги/голова-плечи/треугольники, ТОЛЬКО информационно:
                          не участвует в affected/discrepancy, не влияет на
                          shadow_rr_gate_pass или любой боевой скоринг)

Отдельно (Пакет 9 кусок 2, владелец "да" ТОЛЬКО на diff+shadow, не на переключение
live): log_ema_stack_shadow_async() -- накопление сравнения старой (2-EMA per-TF)
и новой (4-EMA-стек + подтверждение ценой, ta_extra.ema_context()) методологии
Multi-TF confluence на реальных промоушен-проверках pump_detector._try_promote_pump().
Использует bot.pro_analysis()'s "ema_stack_shadow" (посчитан там же, тоже НЕ
влияет на pro_score/direction/bull_pts/bear_pts -- см. bot.py docstring рядом).

Отдельно (Пакет 10 М2, владелец "да" -- shadow-патч 09, копить 3 суток/100
сигналов, НЕ live): log_send_scheduled_shadow_async() теперь дополнительно
переносит "oi_funding_ls_shadow" (посчитан внутри bot.real_full_analysis(),
формула 1-в-1 из fa_engine.py._oi_matrix()/_rocket_score() -- бэклог-баг
"AUTO-путь слеп к OI/funding/L-S", ENGINE_UNIFICATION.md §4 Блок 7) в
каждую send_scheduled-запись. Никак не влияет на rocket/promoted_live выше --
те уже полностью решены до вызова этой функции.

Пакет 11 М1 (2026-07-13, находка ночного цикла -- см. SHADOW_ANALYSIS.md
23:42 запись): _sync_to_github_sync()/_github_get_shadow_sync() исправлены --
раньше транзиентный сбой GET (сеть/парсинг) трактовался как "файл ещё не
существует", что могло привести к PUT без sha (422 на существующий файл, в
худшем случае риск затирания). Теперь ошибка GET явно отличается от
"файла нет" и прерывает синк. Плюс ретрай-catchup: каждый успешный синк
теперь подтягивает ВЕСЬ локальный хвост, не ушедший в GitHub с прошлых
неудачных попыток, а не только запись текущего вызова.
"""
import asyncio
import base64
import json
import logging
import os
import time

import requests

import chart_patterns
import signal_journal   # переиспользуем _github_configured/_validate_github_token/_github_headers/_github_api_base
import ta_extra

# Находка 2026-07-13 (владелец "да"): весь shadow-путь молчал 16+ часов, потому что
# ошибки уходили в print() (stdout) вместо log.error() -- невидимо в ограниченном
# буфере `railway logs`. Использует общий root-логгер, настроенный один раз в bot.py
# (logging.basicConfig) -- тот же формат/уровень, отдельного basicConfig здесь не надо.
log = logging.getLogger(__name__)

SHADOW_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "journal", "shadow_signals.json")
GITHUB_SHADOW_PATH = "journal/shadow_signals.json"
SR_MIN_RR_TP1_SHADOW = 2.0  # патч 02 -- см. patches/02-rr-gate/README.md, НЕ live-константа
DEAD_ZONE_SHADOW_SCORE_PENALTY = 10  # находка владельца 2026-07-11 (карточка EVAA):
# METHODOLOGY_CORE.md §8 -- сессия влияет на качество, но killzone quality=="D" (Dead
# Zone) никак не штрафовал pro_score reversal-кандидата. НЕ live-константа -- боевой
# pro_analysis()/_try_promote_pump() не трогаются, только shadow-запись (см.
# _build_pump_reversal_record ниже). Первое приближение, не откалибровано.

# Пакет "находки 1-2" (владелец "да", 2026-07-13) -- health-счётчик "последняя
# успешная shadow-запись N часов назад" для /stats и утренней сводки. Хранится
# только в памяти процесса (Railway ephemeral, честно НЕ переживает рестарт --
# после редеплоя счётчик стартует с None, пока не придёт первая новая запись
# ЭТОГО процесса; не выдаёт время до рестарта за реальное время последней
# записи, чтобы не создавать ложное ощущение свежести после каждого деплоя).
_last_send_scheduled_write_ts = None


def get_last_send_scheduled_write_ts():
    """None, если этот процесс ещё не записал ни одной send_scheduled shadow-
    записи с момента своего старта (см. докстринг _last_send_scheduled_write_ts
    -- честно, не подставляет старое значение с прошлого рестарта)."""
    return _last_send_scheduled_write_ts


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
        log.error(f"shadow_engine: atomic write to {path} failed ({e})")
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


def get_local_records() -> list:
    """Публичная обёртка над _load_local() -- Пакет 11, для внешних вызывающих
    (bot._startup_integrity_check и т.п.), чтобы не тянуть private-функцию через
    границу модуля."""
    return _load_local()


def _dedup_key(rec: dict):
    return (rec.get("symbol"), rec.get("ts"))


def _github_get_shadow_sync():
    """GET journal/shadow_signals.json. Три разных исхода, НЕ схлопнутые в один (найдено
    2026-07-12/13 -- см. SHADOW_ANALYSIS.md, запись 23:42: раньше "файла ещё нет" и
    "запрос не удался" возвращали одинаковый (None, None), из-за чего вызывающий код на
    транзиентном сбое GET считал удалённый файл ПУСТЫМ и пытался его PUT'нуть без sha --
    либо 422 (как и было в инциденте), либо в худшем случае затирание существующих
    записей, если бы GitHub такой PUT принял):
      - GitHub не настроен / невалидный токен -> (None, None) -- синк пропускается, не ошибка.
      - Файла действительно ещё нет (404) -> ([], None) -- пустой список, безопасно создавать.
      - Запрос не удался (сеть/парсинг/rate-limit) -> (False, None) -- ОШИБКА, не пустой
        файл; вызывающий код обязан прервать синк, а не создавать файл поверх существующего.

    НАХОДКА 2026-07-13 (owner "да" -- Находка 1, п.1-3): корневая причина 16+-часового
    молчания shadow-потока -- GitHub Contents API отдаёт `content` ТОЛЬКО для файлов
    <1MB; для файлов больше отдаёт `encoding: "none"`, `content: ""` -- реальный размер
    journal/shadow_signals.json на момент находки: 1 049 083 байт, пересёк порог.
    `json.loads("")` -- ровно ошибка "Expecting value: line 1 column 1 (char 0)", которая
    молча повторялась в логах КАЖДЫЙ раз (раньше в print(), см. находка 1). Подтверждено
    живьём прямым запросом к Contents API (не догадка). Файл ЦЕЛЕНАПРАВЛЕННО НЕ капается
    (в отличие от security_log.py -- см. докстринг наверху этого файла: "каждая запись --
    ценные данные для анализа надолго"), поэтому фикс -- не урезать файл, а переключиться
    на Git Blobs API (лимит 100MB, тот же `sha`, который Contents API уже отдаёт) для
    случая `encoding == "none"`, вместо капа/потери исторических данных.
    Синхронно -- вызывать только через run_in_executor из async-кода (см.
    signal_journal._github_get_file_sync, тот же паттерн)."""
    if not signal_journal._github_configured():
        return None, None
    token_issue = signal_journal._validate_github_token()
    if token_issue:
        log.error(f"shadow_engine: {token_issue}")
        return None, None
    try:
        r = requests.get(f"{signal_journal._github_api_base()}/contents/{GITHUB_SHADOW_PATH}",
                          headers=signal_journal._github_headers(), timeout=15)
        if r.status_code == 404:
            return [], None
        r.raise_for_status()
        data = r.json()
        if data.get("encoding") == "none":
            # Файл >1MB -- Contents API не отдаёт content, см. находку в докстринге выше.
            # Тот же sha уже есть -- фетчим блоб напрямую (лимит Git Blobs API 100MB).
            blob_r = requests.get(f"{signal_journal._github_api_base()}/git/blobs/{data['sha']}",
                                   headers=signal_journal._github_headers(), timeout=25)
            blob_r.raise_for_status()
            blob_data = blob_r.json()
            content = base64.b64decode(blob_data["content"]).decode()
        else:
            content = base64.b64decode(data["content"]).decode()
        payload = json.loads(content)
        records = payload.get("records", []) if isinstance(payload, dict) else []
        return records, data["sha"]
    except Exception as e:
        detail = getattr(getattr(e, "response", None), "text", "")
        log.error(f"shadow_engine: GitHub GET failed ({e} {detail[:300]})")
        return False, None


def _github_put_shadow_sync(records: list, sha):
    """PUT journal/shadow_signals.json. Возвращает новый sha при успехе, None при ошибке,
    "conflict" при 409 (sha устарел -- вызывающий должен перечитать и повторить)."""
    if not signal_journal._github_configured():
        return None
    token_issue = signal_journal._validate_github_token()
    if token_issue:
        log.error(f"shadow_engine: {token_issue}")
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
        log.error(f"shadow_engine: GitHub PUT failed ({e} {detail[:300]})")
        return None


def _sync_to_github_sync(new_record: dict = None) -> bool:
    """GET текущего состояния из GitHub, добавляет ВСЕ локальные записи, которых там ещё
    нет (не только new_record -- параметр оставлен для обратной совместимости вызовов и
    логов, сама функция берёт полный список из _load_local()), PUT. Один повтор при
    409-конфликте. Best-effort -- отсутствие GITHUB_TOKEN просто пропускает шаг, это не
    ошибка.

    Ретрай-catchup (найдено 2026-07-12/13, см. SHADOW_ANALYSIS.md запись 23:42): раньше
    функция пушила ТОЛЬКО новую запись текущего вызова -- если предыдущий вызов не
    смог синкнуться (сетевой сбой, см. ниже), его запись НИКОГДА не попадала в GitHub
    сама по себе, только если та же запись передавалась бы повторно явно (что не
    происходит -- каждый вызов даёт свою новую запись). Теперь на каждом успешном синке
    подтягивается весь локальный "хвост", не ушедший в GitHub с прошлых попыток.

    Также больше НЕ трактует ошибку GET как пустой файл (см. _github_get_shadow_sync) --
    транзиентный сбой прерывает синк без попытки PUT, чтобы не рисковать перезаписью
    существующих записей меньшим (локальным) списком."""
    for attempt in range(2):
        remote, sha = _github_get_shadow_sync()
        if remote is False:
            return False  # транзиентная ошибка GET -- НЕ пустой файл, синк прерван безопасно
        if remote is None and sha is None and not signal_journal._github_configured():
            return False  # GitHub не настроен -- локальная запись уже сделана, этого достаточно
        remote_records = remote or []
        remote_keys = {_dedup_key(r) for r in remote_records}
        local_records = _load_local()
        missing = [r for r in local_records if _dedup_key(r) not in remote_keys]
        if not missing:
            return True  # уже всё синхронизировано (включая new_record, если был передан)
        result = _github_put_shadow_sync(remote_records + missing, sha)
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
    price = result.get("price")

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

    # Патч 07: Order Block геометрия -- live (тело+фитиль, зеркало pro_analysis())
    # vs methodology (чистое тело, METHODOLOGY_CORE.md §18.1). Пакет 7 М1, владелец
    # "ДА" на вариант B из Пакета 5 М2/6 М1 -- живой pro_analysis() НЕ трогается,
    # это ТОЛЬКО shadow-сравнение той же зоны на тех же свечах.
    _ob_empty = {"bull": False, "bull_zone": None, "bear": False, "bear_zone": None}
    ob_result = {"live": dict(_ob_empty), "methodology": dict(_ob_empty)}
    if candles_4h and price is not None:
        try:
            ob_result = ta_extra.detect_order_block(candles_4h, price)
            live_ob = ob_result["live"]
            meth_ob = ob_result["methodology"]
            if live_ob["bull"] != meth_ob["bull"] or live_ob["bear"] != meth_ob["bear"]:
                affected.append("07-order-block-body")
                discrepancy.append(
                    f"order_block: live(bull={live_ob['bull']},bear={live_ob['bear']}) vs "
                    f"methodology-тело(bull={meth_ob['bull']},bear={meth_ob['bear']})"
                )
        except Exception as e:
            discrepancy.append(f"order_block geometry calc failed: {e}")

    # Патч 08: классические чарт-паттерны (Bulkowski, Пакет 8 М3, владелец --
    # "НОВЫЙ модуль, вывод в shadow-скоринг + отдельная строка в карточке ТА
    # (информационно). Бой не трогать"). ТОЛЬКО информационные поля -- намеренно
    # не участвует в affected/discrepancy выше (не считается "расхождением с боем",
    # т.к. в бою этих паттернов вообще нет) и не влияет на shadow_rr_gate_pass.
    chart_pat = {
        "flag": {"bull": False, "bear": False, "target": None},
        "head_shoulders": {"top": False, "bottom": False, "target": None},
        "triangle": {"type": None},
    }
    if candles_4h:
        try:
            flag_r = chart_patterns.detect_flag(candles_4h)
            chart_pat["flag"] = {"bull": flag_r["bull"], "bear": flag_r["bear"],
                                  "target": flag_r["target"]}
        except Exception as e:
            discrepancy.append(f"chart_patterns.detect_flag failed: {e}")
        try:
            hs_r = chart_patterns.detect_head_and_shoulders(candles_4h)
            chart_pat["head_shoulders"] = {"top": hs_r["top"], "bottom": hs_r["bottom"],
                                            "target": hs_r["target"]}
        except Exception as e:
            discrepancy.append(f"chart_patterns.detect_head_and_shoulders failed: {e}")
        try:
            tri_r = chart_patterns.detect_triangle(candles_4h)
            chart_pat["triangle"] = {"type": tri_r["type"]}
        except Exception as e:
            discrepancy.append(f"chart_patterns.detect_triangle failed: {e}")

    # amd_phase/smc_inducement по методологии (Пакет 5 М3, владелец "ДА" --
    # ТОЛЬКО shadow-скоринг, не бой, не патч 01-06 -- отдельные исследовательские
    # поля, не участвуют в affected/discrepancy выше, не решают "прошёл бы гейт").
    amd_methodology = {"phase": None, "nymidnight_price": None, "price_vs_nymidnight": None}
    inducement = {"inducement_swept": False, "detail": None}
    try:
        amd_methodology = ta_extra.classify_amd_phase(candles_4h)
    except Exception as e:
        discrepancy.append(f"amd_phase (methodology) calc failed: {e}")
    try:
        inducement = ta_extra.detect_inducement_sweep(candles_4h)
    except Exception as e:
        discrepancy.append(f"inducement calc failed: {e}")

    # Пакет 14 (владелец, 2026-07-13): параллельный 13-блочный вердикт --
    # ta_extra.build_13block_verdict(), уже полностью посчитан вызывающей стороной
    # (bot.real_full_analysis() -> result["tz13_shadow"], либо отсутствует для
    # fa_engine.build_full_analysis()-пути, который его не считает -- честно None,
    # не выдумываем). Верхнеуровневые tz13_* поля -- владелец, п.3: "score,
    # направление, зона, SL/TP", для удобного логирования/сравнения без раскопки
    # вложенности compute_shadow() уже даёт (те же имена, что fa_engine-путь).
    tz13 = result.get("tz13_shadow") or {}

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
        "amd_phase_methodology": amd_methodology,
        "inducement": inducement,
        "order_block_live": ob_result["live"],
        "order_block_methodology": ob_result["methodology"],
        "chart_patterns": chart_pat,
        "patches_affected": affected,
        "discrepancy": discrepancy,
        "live_journal_id": live_journal_id,
        "tz13_shadow": tz13,
        "tz13_score": tz13.get("score"),
        "tz13_direction": tz13.get("direction"),
        "tz13_setup_type": tz13.get("setup_type"),
        "tz13_entry_zone": tz13.get("entry_zone"),
        "tz13_sl": tz13.get("sl"),
        "tz13_tp1": tz13.get("tp1"), "tz13_tp2": tz13.get("tp2"), "tz13_tp3": tz13.get("tp3"),
    }


def _write_local(record: dict) -> bool:
    records = _load_local()
    records.append(record)
    return _atomic_write_json(SHADOW_FILE, {"schema_version": 1, "records": records})


def integrity_report(records: list) -> dict:
    """Пакет 11 (owner-запрос "целостность shadow-окон", находка ночного цикла с
    разрывом в GitHub-копии) -- чистая функция, без сети/файлов, проверяет уже
    загруженный список записей на честные структурные проблемы (не выдумывает
    "всё ок" -- тот же принцип, что и bot._startup_integrity_check):
      - schema_ok: каждая запись -- dict с непустыми symbol/ts.
      - duplicate_count/duplicate_keys: повторяющиеся (symbol, ts) -- при корректной
        работе _dedup_key() в _sync_to_github_sync() дублей быть не должно; находка
        означает баг где-то в цепочке записи, не в самом чек-скрипте.
      - out_of_order_count: сколько записей идут с ts МЕНЬШЕ предыдущей (записи
        пишутся последовательно по времени -- нарушение подряд подсказывает на
        неупорядоченный merge при синке).
      - total: общее количество записей.
    Пустой/None список -- честно total=0, без ошибок."""
    records = records or []
    total = len(records)
    schema_bad = [i for i, r in enumerate(records)
                  if not isinstance(r, dict) or not r.get("symbol") or r.get("ts") is None]
    seen = {}
    dup_keys = []
    for r in records:
        if not isinstance(r, dict):
            continue
        key = _dedup_key(r)
        seen[key] = seen.get(key, 0) + 1
    for key, count in seen.items():
        if count > 1:
            dup_keys.append({"key": key, "count": count})
    out_of_order = 0
    prev_ts = None
    for r in records:
        if not isinstance(r, dict):
            continue
        ts = r.get("ts")
        if ts is None:
            continue
        if prev_ts is not None and ts < prev_ts:
            out_of_order += 1
        prev_ts = ts
    return {
        "total": total,
        "schema_ok": len(schema_bad) == 0,
        "schema_bad_indices": schema_bad,
        "duplicate_count": len(dup_keys),
        "duplicate_keys": dup_keys[:20],  # честный кап на размер отчёта, не на сам подсчёт
        "out_of_order_count": out_of_order,
    }


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
        log.error(f"shadow_engine.log_shadow failed for {symbol}: {e}")
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
        log.error(f"shadow_engine.log_shadow_async: compute failed for {symbol}: {e}")
        return False
    ok_local = _write_local(record)
    if not ok_local:
        return False
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _sync_to_github_sync, record)
    except Exception as e:
        log.error(f"shadow_engine: GitHub sync failed (локальная запись уже сохранена): {e}")
    return True


def _adapt_send_scheduled_result(a: dict) -> dict:
    """Адаптер полей: `bot.real_full_analysis()` (используется `send_scheduled()`) отдаёт
    ПЛОСКИЙ словарь с другими именами полей, чем `fa_engine.build_full_analysis()`
    (используется `signal_loop.py`) -- `compute_shadow()` ожидает
    `result["block11_trade_plan"]` + `result["candles_4h"]`. Пакет 4 М2 (владелец,
    "ДА" -- подключить shadow_engine к send_scheduled). Ничего не пересчитывает --
    только переименовывает уже посчитанные поля в ожидаемую форму."""
    direction = "long" if a.get("is_long") else "short"
    return {
        "block11_trade_plan": {
            "direction": direction,
            "entry1": a.get("entry1"), "entry3": a.get("entry3"),
            "sl": a.get("sl"),
            "tp1": a.get("tp1"), "tp2": a.get("tp2"), "tp3": a.get("tp3"),
            "rr_tp1": a.get("rr_tp1"),
        },
        "candles_4h": a.get("candles_4h") or [],
        "price": a.get("price"),
        # Пакет 14 (владелец, 2026-07-13): tz13_shadow -- уже полностью посчитан
        # внутри real_full_analysis() (ta_extra.build_13block_verdict()), просто
        # прокидывается через адаптер к compute_shadow(), без пересчёта.
        "tz13_shadow": a.get("tz13_shadow"),
    }


async def log_send_scheduled_shadow_async(symbol: str, a: dict, bot_module,
                                           promoted_live: bool,
                                           gate_reasons: list = None,
                                           live_journal_id=None) -> bool:
    """Боевой путь -- вызывается из `bot.send_scheduled()` (Пакет 4 М2, владелец "ДА"):
    КАЖДЫЙ кандидат, дошедший до `real_full_analysis()` (совпало направление прескрина
    со структурой), прогоняется через тот же 5-патчевый теневой контур
    (`compute_shadow()`), что и `signal_loop.py` -- независимо от того, прошёл ли он
    боевые гейты `send_scheduled()`. Параллельная запись -- боевые гейты/рассылка
    подписчикам НИКОГДА не читают этот модуль и не меняются этим вызовом.
    `promoted_live`/`gate_reasons` -- честно фиксируют боевой исход рядом с теневым
    (в отличие от signal_loop-пути, где до shadow доходят только уже-отправленные
    сигналы -- здесь видны и отброшенные гейтом, что и было целью подключения).
    `live_journal_id` -- Пакет 7 М2 (владелец "ДА" -- связка shadow с исходами):
    для promoted-кандидатов `bot.send_scheduled()` теперь логирует journal-запись
    ДО вызова этой функции и передаёт сюда реальный id, чтобы
    `shadow_outcome_analysis.py` мог напрямую сопоставить shadow-запись с
    фактическим исходом сделки без ретроактивного матчинга по времени.

    Явное логирование входа/выхода (владелец "да", 2026-07-13 -- находка "поток
    молчал 16+ часов, ошибка ушла в print(), невидима в railway logs"): log.info
    на входе И на успешном выходе -- если в логах есть "started" без парного
    "OK"/"failed" рядом, само по себе укажет, на каком шаге теряется поток,
    даже без текста конкретного исключения."""
    log.info(f"shadow_engine.log_send_scheduled_shadow_async: started for {symbol}")
    try:
        adapted = _adapt_send_scheduled_result(a)
        record = compute_shadow(symbol, adapted, bot_module, live_journal_id=live_journal_id)
    except Exception as e:
        log.error(f"shadow_engine.log_send_scheduled_shadow_async: compute failed for {symbol}: {e}")
        return False
    record["source"] = "send_scheduled"
    record["promoted_live"] = promoted_live
    record["gate_reasons"] = gate_reasons or []
    # Пакет 10 М2 (владелец "да" -- shadow-патч 09, OI/funding/L-S для AUTO-пути):
    # уже посчитан внутри bot.real_full_analysis() (см. её докстринг), здесь только
    # переносится в запись -- копится минимум 3 суток/100 сигналов, затем отчёт.
    record["oi_funding_ls_shadow"] = a.get("oi_funding_ls_shadow")
    # Пакет 11 М1 (владелец "да" -- A/B тело-vs-фитиль, НЕ live): аналогично, уже
    # посчитан внутри bot.real_full_analysis(), здесь только переносится.
    record["bos_body_close_shadow"] = a.get("bos_body_close_shadow")
    # Пакет 11 М2 (ТЗ Блок 4 -- Order Block, ранее полностью отсутствовал в
    # real_full_analysis(), см. ENGINE_UNIFICATION.md §4): аналогично, переносится.
    record["order_block_shadow"] = a.get("order_block_shadow")
    ok_local = _write_local(record)
    if not ok_local:
        log.error(f"shadow_engine.log_send_scheduled_shadow_async: _write_local FAILED for {symbol}")
        return False
    global _last_send_scheduled_write_ts
    _last_send_scheduled_write_ts = time.time()
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _sync_to_github_sync, record)
    except Exception as e:
        log.error(f"shadow_engine: GitHub sync failed (локальная запись уже сохранена): {e}")
    log.info(f"shadow_engine.log_send_scheduled_shadow_async: OK for {symbol} "
             f"(oi_funding_ls_shadow={'set' if record.get('oi_funding_ls_shadow') else 'None'}, "
             f"bos_body_close_shadow={'set' if record.get('bos_body_close_shadow') else 'None'})")
    return True


def _build_pump_reversal_record(symbol: str, watch: dict, funding, oi_usd, oi_change_pct,
                                 promoted_live, kz_quality: str = None,
                                 pro_score: float = None) -> dict:
    """Ночная сессия #2, Блок 4: кандидат SHORT после подтверждённого разворота пампа
    (памп -> откат >= REVERSAL_DRAWDOWN_PCT% от пика с объёмом >= REVERSAL_VOL_MULT --
    см. pump_detector.py, эта проверка УЖЕ существует в живом коде, здесь только
    измерительное логирование поверх неё). Вызывается ПОСЛЕ уже существующего live-пути
    (алерт владельцу уже отправлен, `_try_promote_pump` уже отработал) -- не влияет ни
    на что боевое, чисто накопление данных для последующей оценки.

    `kz_quality`/`pro_score` -- добавлены 2026-07-11 (находка владельца на карточке
    EVAA, METHODOLOGY_CORE.md §8): killzone quality=="D" (Dead Zone) не штрафовал
    pro_score вообще. Считаем ЗДЕСЬ (shadow), не в pro_analysis()/_try_promote_pump()
    -- боевой гейт промоушена не меняется."""
    peak = watch.get("peak_price")
    last = watch.get("last_price")
    retrace_pct = round((peak - last) / peak * 100, 2) if peak else None
    is_dead_zone = kz_quality == "D"
    penalty = DEAD_ZONE_SHADOW_SCORE_PENALTY if is_dead_zone else 0
    pro_score_shadow_adjusted = (pro_score - penalty) if pro_score is not None else None
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
        "kz_quality": kz_quality,
        "dead_zone": is_dead_zone,
        "pro_score_live": pro_score,
        "dead_zone_score_penalty": penalty,
        "pro_score_shadow_adjusted": pro_score_shadow_adjusted,
    }


def log_pump_reversal_shadow(symbol: str, watch: dict, funding, oi_usd, oi_change_pct,
                              promoted_live, kz_quality: str = None,
                              pro_score: float = None) -> bool:
    """Синхронная версия -- см. _build_pump_reversal_record. Локальная запись только."""
    try:
        record = _build_pump_reversal_record(symbol, watch, funding, oi_usd, oi_change_pct,
                                              promoted_live, kz_quality, pro_score)
        return _write_local(record)
    except Exception as e:
        log.error(f"shadow_engine.log_pump_reversal_shadow failed for {symbol}: {e}")
        return False


async def log_pump_reversal_shadow_async(symbol: str, watch: dict, funding, oi_usd,
                                          oi_change_pct, promoted_live,
                                          kz_quality: str = None,
                                          pro_score: float = None) -> bool:
    """Боевой путь -- вызывается из pump_detector._confirm_pump_reversal(). Локальная
    запись + best-effort пуш в GitHub, тот же паттерн, что и log_shadow_async()."""
    try:
        record = _build_pump_reversal_record(symbol, watch, funding, oi_usd, oi_change_pct,
                                              promoted_live, kz_quality, pro_score)
    except Exception as e:
        log.error(f"shadow_engine.log_pump_reversal_shadow_async: build failed for {symbol}: {e}")
        return False
    ok_local = _write_local(record)
    if not ok_local:
        return False
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _sync_to_github_sync, record)
    except Exception as e:
        log.error(f"shadow_engine: GitHub sync failed for pump_reversal ({symbol}): {e}")
    return True


def _build_ema_stack_shadow_record(symbol: str, ema_stack_shadow: dict,
                                    promoted_live: bool, rr: float = None) -> dict:
    """Пакет 9 кусок 2 (владелец "да" на diff+shadow, НЕ на переключение live):
    накопление данных для сравнения старой (2-EMA per-TF, `bot.pro_analysis()`
    inline `tf_trend()`) и новой (4-EMA-стек + подтверждение ценой,
    `ta_extra.ema_context()` -- тот же детектор, что чинили в Баге 2 Памп-радара)
    методологии Multi-TF confluence на РЕАЛЬНЫХ промоушен-проверках
    `_try_promote_pump()`. Считается из уже готового `ema_stack_shadow`
    (посчитан внутри `pro_analysis()`, см. bot.py -- НЕ делает повторных сетевых
    вызовов). `promoted_live` -- фактическое боевое решение (не меняется этой
    записью). `would_promote_new` -- YES/NO по НОВОЙ формуле при тех же порогах
    (`PROMOTE_SCORE_THRESHOLD`/`PROMOTE_MIN_RR`) -- расхождение с `promoted_live`
    это и есть материальный кейс, который ищет диф."""
    from pump_detector import PROMOTE_SCORE_THRESHOLD, PROMOTE_MIN_RR
    score_new = ema_stack_shadow.get("pro_score_new")
    direction_new = ema_stack_shadow.get("direction_new")
    would_promote_new = bool(
        score_new is not None and direction_new == "short" and
        score_new >= PROMOTE_SCORE_THRESHOLD and (rr or 0) >= PROMOTE_MIN_RR
    )
    return {
        "ts": time.time(),
        "type": "ema_stack_shadow",
        "symbol": symbol,
        "promoted_live": promoted_live,
        "would_promote_new": would_promote_new,
        "diverges": would_promote_new != promoted_live,
        "rr": rr,
        **ema_stack_shadow,
    }


async def log_ema_stack_shadow_async(symbol: str, ema_stack_shadow: dict,
                                      promoted_live: bool, rr: float = None) -> bool:
    """Боевой путь -- вызывается из pump_detector._try_promote_pump() ПОСЛЕ того,
    как боевое решение о промоушене уже принято. Только накопление данных, не
    влияет ни на что боевое (см. докстринг _build_ema_stack_shadow_record).

    Владелец "да" 2026-07-13 -- различать в логах "события не было" (ema_stack_
    shadow=None, bot.pro_analysis() честно не дошла до расчёта) от "было, но
    упало" (dict с ключом "error" -- bot.pro_analysis() поймала исключение и
    промаркировала это явно, см. bot.py). Раньше оба случая тихо схлопывались
    в один и тот же return False без единой строки в логах."""
    if not ema_stack_shadow:
        log.info(f"shadow_engine.log_ema_stack_shadow_async: skip for {symbol} -- "
                  f"нет события (pro_analysis не дошла до ema_stack_shadow)")
        return False
    if ema_stack_shadow.get("error"):
        log.warning(f"shadow_engine.log_ema_stack_shadow_async: skip for {symbol} -- "
                    f"pro_analysis сообщила об ошибке: {ema_stack_shadow['error']}")
        return False
    try:
        record = _build_ema_stack_shadow_record(symbol, ema_stack_shadow, promoted_live, rr)
    except Exception as e:
        log.error(f"shadow_engine.log_ema_stack_shadow_async: build failed for {symbol}: {e}")
        return False
    ok_local = _write_local(record)
    if not ok_local:
        return False
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _sync_to_github_sync, record)
    except Exception as e:
        log.error(f"shadow_engine: GitHub sync failed for ema_stack_shadow ({symbol}): {e}")
    return True
