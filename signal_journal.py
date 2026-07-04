"""
BEST TRADE — Signal Journal (paper-trading трекер)

Логирует каждый сгенерированный сигнал (ТОП ЛОНГ/ШОРТ/СПОТ, x100, Памп-радар) и
отслеживает его реальную отработку через live_prices (PENDING -> ENTERED ->
TPx_HIT/SL_HIT, либо EXPIRED без входа за 72ч) — только наблюдение, никакого влияния
на генерацию сигналов.

Хранение: JSON-файл в рабочей директории (Railway ephemeral -- при редеплое обнуляется)
+ in-memory, ПЛЮС персистентность через GitHub Contents API (journal/signals.json в том
же приватном репо, что и код) -- см. блок GitHub-персистентности ниже. При старте бот
подтягивает историю оттуда и мержит с локальной (last-write-wins по updated_ts), при
каждом закрытии сигнала и раз в час фоном -- коммитит изменения обратно (не чаще 1
коммита в 5 минут, батчем). Каждая запись несёт schema_version для будущей миграции
формата.
"""

import asyncio
import base64
import json
import os
import time
from datetime import datetime

import pytz
import requests

import live_prices

SCHEMA_VERSION = 1
JOURNAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signal_journal.json")
TZ = pytz.timezone("Europe/Istanbul")   # UTC+3, как остальной бот

PENDING_EXPIRE_SEC = 72 * 3600     # 72ч без входа в зону -- EXPIRED
TRACK_INTERVAL_SEC = 30

TERMINAL_STATUSES = {"TP1_HIT", "TP2_HIT", "TP3_HIT", "SL_HIT", "EXPIRED"}
OUTCOME_STATUSES = {"TP1_HIT", "TP2_HIT", "TP3_HIT", "SL_HIT"}  # исходы после входа (не EXPIRED)

_journal = {}      # id (int) -> record dict
_next_id = 1
_bot = None
_owner_chat_id = None

# --- GitHub-персистентность ---------------------------------------------------------
# .strip() -- частый источник "latin-1 codec can't encode" при отправке HTTP-заголовков:
# лишний пробел/перенос строки при копировании значения в Railway env vars.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "").strip()
GITHUB_REPO  = os.getenv("GITHUB_REPO", "").strip()
GITHUB_JOURNAL_PATH = "journal/signals.json"
GITHUB_COMMIT_MIN_INTERVAL_SEC = 5 * 60     # не чаще 1 коммита в 5 минут
GITHUB_SYNC_INTERVAL_SEC = 3600             # фоновый пресс раз в час

_github_sha = None       # sha последнего известного содержимого файла (для PUT/конфликтов)
_dirty = False           # есть несохранённые в GitHub изменения
_last_commit_ts = 0.0
_github_lock = None      # asyncio.Lock, создаётся лениво (нужен running loop)
_last_github_error = None   # текст последней ошибки GitHub GET/PUT (для диагностики через /journal_sync)


def _validate_github_token() -> str:
    """Заголовок Authorization должен быть latin-1/ASCII (так требует HTTP) -- если в
    GITHUB_TOKEN затесался не-ASCII символ (частый паст-артефакт: smart quote, BOM,
    неразрывный пробел из буфера обмена), requests падает с малопонятным
    'latin-1 codec can't encode...'. Возвращает понятное описание проблемы либо ''."""
    try:
        GITHUB_TOKEN.encode("ascii")
        return ""
    except UnicodeEncodeError as e:
        bad_char = GITHUB_TOKEN[e.start]
        return (f"GITHUB_TOKEN содержит не-ASCII символ на позиции {e.start} "
                f"('{bad_char}', U+{ord(bad_char):04X}) -- проверьте значение в Railway env vars, "
                f"похоже на артефакт копирования (smart quote / BOM / неразрывный пробел)")


def _github_configured() -> bool:
    return bool(GITHUB_TOKEN and GITHUB_OWNER and GITHUB_REPO)


def _github_api_base() -> str:
    return f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"


def _github_headers() -> dict:
    return {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}


def _github_get_file_sync():
    """GET journal/signals.json из репо. Возвращает (records_dict, sha) либо (None, None),
    если файла ещё нет или GitHub не настроен/недоступен. Синхронно (блокирующий HTTP) --
    вызывать только через run_in_executor из async-кода."""
    global _last_github_error
    if not _github_configured():
        return None, None
    token_issue = _validate_github_token()
    if token_issue:
        _last_github_error = token_issue
        print(f"Signal Journal: {token_issue}")
        return None, None
    try:
        r = requests.get(f"{_github_api_base()}/contents/{GITHUB_JOURNAL_PATH}",
                          headers=_github_headers(), timeout=15)
        if r.status_code == 404:
            _last_github_error = None
            return None, None
        r.raise_for_status()
        data = r.json()
        content = base64.b64decode(data["content"]).decode()
        payload = json.loads(content)
        records = {int(k): v for k, v in payload.get("records", {}).items()}
        _last_github_error = None
        return records, data["sha"]
    except Exception as e:
        detail = getattr(getattr(e, "response", None), "text", "")
        _last_github_error = f"GET failed: {e} {detail[:300]}"
        print(f"Signal Journal: GitHub load failed ({_last_github_error})")
        return None, None


def _github_put_file_sync(records: dict, sha):
    """PUT journal/signals.json (создаёт либо обновляет, если sha совпадает с текущим на
    GitHub). Возвращает новый sha при успехе, None при ошибке, "conflict" при 409 (sha
    устарел -- вызывающий должен перечитать sha и повторить). Синхронно -- см. выше."""
    global _last_github_error
    if not _github_configured():
        return None
    token_issue = _validate_github_token()
    if token_issue:
        _last_github_error = token_issue
        print(f"Signal Journal: {token_issue}")
        return None
    try:
        payload = {"schema_version": SCHEMA_VERSION, "records": records}
        body = {
            "message": f"journal: sync {len(records)} записей ({datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')})",
            "content": base64.b64encode(json.dumps(payload).encode()).decode(),
        }
        if sha:
            body["sha"] = sha
        r = requests.put(f"{_github_api_base()}/contents/{GITHUB_JOURNAL_PATH}",
                          headers=_github_headers(), json=body, timeout=20)
        if r.status_code == 409:
            _last_github_error = None
            return "conflict"
        r.raise_for_status()
        _last_github_error = None
        return r.json()["content"]["sha"]
    except Exception as e:
        detail = getattr(getattr(e, "response", None), "text", "")
        _last_github_error = f"PUT failed: {e} {detail[:300]}"
        print(f"Signal Journal: GitHub save failed ({_last_github_error})")
        return None


def _merge_records(local: dict, remote: dict) -> dict:
    """last-write-wins по id: для записей, встречающихся в обоих наборах, побеждает та,
    у которой позже updated_ts (либо ts как фолбэк для старых записей без этого поля).
    Записи, встречающиеся только в одном из наборов, сохраняются как есть."""
    merged = dict(local)
    for rid, rrec in remote.items():
        lrec = merged.get(rid)
        if lrec is None:
            merged[rid] = rrec
            continue
        l_ts = lrec.get("updated_ts", lrec.get("ts", 0))
        r_ts = rrec.get("updated_ts", rrec.get("ts", 0))
        if r_ts > l_ts:
            merged[rid] = rrec
    return merged


def _get_github_lock():
    global _github_lock
    if _github_lock is None:
        _github_lock = asyncio.Lock()
    return _github_lock


async def startup_sync():
    """Вызывается один раз при старте бота (после init()/_load()): подтягивает историю
    из GitHub и мержит с локальной (last-write-wins по id). Не бросает исключений наружу
    -- отсутствие/недоступность GitHub не должно мешать боту стартовать."""
    global _journal, _next_id, _github_sha
    if not _github_configured():
        print("Signal Journal: GITHUB_TOKEN/GITHUB_OWNER/GITHUB_REPO не заданы -- "
              "персистентность через GitHub отключена, история только локальная (ephemeral)")
        return
    try:
        loop = asyncio.get_event_loop()
        remote_records, sha = await loop.run_in_executor(None, _github_get_file_sync)
        _github_sha = sha
        if remote_records is None:
            print("Signal Journal: файл в GitHub ещё не создан -- будет создан при первом коммите")
            return
        before = len(_journal)
        _journal = _merge_records(_journal, remote_records)
        if _journal:
            _next_id = max(_next_id, max(_journal.keys()) + 1)
        _save()
        print(f"Signal Journal: загружено {len(remote_records)} записей из GitHub "
              f"(локально было {before}, после мержа {len(_journal)})")
    except Exception as e:
        print(f"Signal Journal: startup_sync failed ({e})")


async def _commit_to_github(force: bool = False):
    """Коммитит текущий _journal в GitHub, если есть несохранённые изменения (_dirty) и с
    последнего коммита прошло >= GITHUB_COMMIT_MIN_INTERVAL_SEC (либо force=True -- для
    часового фонового прохода). При конфликте sha (409) перечитывает файл и повторяет
    один раз. Не бросает исключений наружу -- ошибка сети не должна ронять бота."""
    global _dirty, _last_commit_ts, _github_sha
    if not _github_configured() or not _dirty:
        return
    now = time.time()
    if not force and (now - _last_commit_ts) < GITHUB_COMMIT_MIN_INTERVAL_SEC:
        return

    async with _get_github_lock():
        if not _dirty:  # другой вызов уже закоммитил, пока мы ждали лок
            return
        loop = asyncio.get_event_loop()
        records = dict(_journal)
        for attempt in range(2):
            result = await loop.run_in_executor(None, _github_put_file_sync, records, _github_sha)
            if result == "conflict":
                remote_records, sha = await loop.run_in_executor(None, _github_get_file_sync)
                _github_sha = sha
                if remote_records is not None:
                    records = _merge_records(records, remote_records)
                continue
            if result:  # новый sha -- успех
                _github_sha = result
                _dirty = False
                _last_commit_ts = now
            break


async def run_github_sync_loop():
    """Фоновый цикл: раз в час форсирует коммит в GitHub (если есть несохранённые
    изменения) -- подстраховка на случай, если событийные коммиты (при закрытии сигнала)
    были пропущены (краш, рейт-лимит и т.п.)."""
    while True:
        await asyncio.sleep(GITHUB_SYNC_INTERVAL_SEC)
        try:
            await _commit_to_github(force=True)
        except Exception as e:
            print(f"Signal Journal: run_github_sync_loop: {e}")


async def force_sync() -> dict:
    """Форсирует немедленный коммит в GitHub в обход 5-минутного рейт-лимита -- для
    owner-команды /journal_sync (проверка персистентности, или перед плановым редеплоем).
    success=True только если _dirty реально сброшен коммитом (а не просто "было нечего
    сохранять") -- либо not was_dirty (нечего было сохранять, это тоже штатный успех)."""
    if not _github_configured():
        return {"configured": False}
    was_dirty = _dirty
    await _commit_to_github(force=True)
    success = (not was_dirty) or (was_dirty and not _dirty)
    return {"configured": True, "was_dirty": was_dirty, "success": success,
            "records": len(_journal), "sha": _github_sha, "error": _last_github_error}


def init(bot, owner_chat_id):
    """Вызывается один раз при старте бота — нужен для owner-уведомлений об исходах."""
    global _bot, _owner_chat_id
    _bot = bot
    _owner_chat_id = owner_chat_id
    _load()


def _load():
    global _journal, _next_id
    if not os.path.exists(JOURNAL_FILE):
        return
    try:
        with open(JOURNAL_FILE, "r") as f:
            data = json.load(f)
        _journal = {int(k): v for k, v in data.get("records", {}).items()}
        _next_id = data.get("next_id", 1)
    except Exception as e:
        print(f"Signal Journal: load failed ({e}), starting fresh")
        _journal = {}
        _next_id = 1


def _save():
    try:
        with open(JOURNAL_FILE, "w") as f:
            json.dump({"schema_version": SCHEMA_VERSION, "next_id": _next_id,
                       "records": _journal}, f)
    except Exception as e:
        print(f"Signal Journal: save failed ({e})")


def log_signal(source: str, symbol: str, direction: str, price_at_signal: float,
               entry_lo: float, entry_hi: float, sl: float,
               tp1: float = None, tp2: float = None, tp3: float = None,
               rr: float = None, rocket_score=None,
               ema_stack=None, sweep=None, levels_source=None, grade=None) -> int:
    """Логирует новый сигнал, статус PENDING. direction: "long"/"short". Для скалярного
    входа (не зоны) передать одно и то же значение в entry_lo и entry_hi. Только
    наблюдение — вызывается ПОСЛЕ уже принятого решения сгенерировать сигнал, не влияет
    на него.

    ema_stack: снимок ta_extra.ema_context() на момент сигнала (или None), sweep: снимок
    ta_extra.detect_sweep() -- какой из них был актуален на момент сигнала (или None).
    Хранятся как есть (просто для последующего статистического анализа "улучшают ли эти
    факторы win rate" — сама отработка сигнала их не использует).

    levels_source: "structure" (find_sr_zones/build_trade_from_structure), "fallback_atr"
    (нет структуры вообще), или None (сигнал не через real_full_analysis, напр. авто-сканы
    с a_stub) -- позволяет позже сравнить win rate между источниками уровней.

    grade: "A+"/"A"/"B"/"C" (или None) -- грейд карточки на момент сигнала (см.
    bot._signal_grade), для разбивки win rate по грейдам в /journal."""
    global _next_id, _dirty
    rec_id = _next_id
    _next_id += 1
    now = time.time()
    rec = {
        "id": rec_id, "schema_version": SCHEMA_VERSION,
        "ts": now, "timestamp": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "updated_ts": now,
        "source": source, "symbol": symbol.upper().replace("USDT", ""),
        "direction": direction,
        "entry_lo": entry_lo, "entry_hi": entry_hi, "sl": sl,
        "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "rr": rr, "rocket_score": rocket_score,
        "ema_stack": ema_stack, "sweep": sweep, "levels_source": levels_source, "grade": grade,
        "price_at_signal": price_at_signal,
        "status": "PENDING",
        "entered_ts": None, "entered_price": None,
        "outcome": None, "outcome_ts": None, "outcome_level": None, "actual_r": None,
    }
    _journal[rec_id] = rec
    _dirty = True
    _save()
    return rec_id


def _touches_entry(price, entry_lo, entry_hi):
    lo, hi = (entry_lo, entry_hi) if entry_lo <= entry_hi else (entry_hi, entry_lo)
    return lo <= price <= hi


def _sl_hit(direction, price, sl):
    if sl is None:
        return False
    return price <= sl if direction == "long" else price >= sl


def _tp_hit(direction, price, tp):
    if tp is None:
        return False
    return price >= tp if direction == "long" else price <= tp


def _check_outcome(direction, price, sl, tp1, tp2, tp3):
    """Первый достигнутый уровень с момента входа: (status, level) либо (None, None).
    SL приоритетнее (консервативно, честная статистика), иначе берём САМЫЙ дальний
    реально достигнутый TP -- при 30-секундном опросе, если цена уже на TP2, значит
    прошла и TP1, публикуем лучший фактически достигнутый уровень."""
    if _sl_hit(direction, price, sl):
        return "SL_HIT", "sl"
    if _tp_hit(direction, price, tp3):
        return "TP3_HIT", "tp3"
    if _tp_hit(direction, price, tp2):
        return "TP2_HIT", "tp2"
    if _tp_hit(direction, price, tp1):
        return "TP1_HIT", "tp1"
    return None, None


def _compute_actual_r(rec, level):
    entered_price = rec.get("entered_price")
    sl = rec.get("sl")
    if entered_price is None or sl is None:
        return None
    risk = abs(entered_price - sl) or 1e-9
    if level == "sl":
        exit_price, sign = sl, -1
    else:
        exit_price = rec.get(level)
        if exit_price is None:
            return None
        sign = 1
    reward = abs(exit_price - entered_price)
    return round(sign * reward / risk, 2)


async def _notify_outcome(rec):
    if _bot is None or _owner_chat_id is None:
        return
    if rec.get("entered_ts") and rec.get("outcome_ts"):
        mins = (rec["outcome_ts"] - rec["entered_ts"]) / 60
        time_str = f"{mins:.0f}мин" if mins < 60 else f"{mins/60:.1f}ч"
    else:
        time_str = "?"
    r_str = f"{rec['actual_r']:+.2f}R" if rec.get("actual_r") is not None else "?"
    text = f"{rec['symbol']} | {rec['source']} | {rec['outcome']} | {r_str} | {time_str} от входа"
    try:
        await _bot.send_message(_owner_chat_id, text)
    except Exception as e:
        print(f"Signal Journal: не удалось отправить уведомление об исходе: {e}")


async def run_tracker():
    """Каждые 30с сверяет активные записи с live_prices, обновляет статус. Только
    наблюдение -- не влияет на генерацию сигналов. При каждом ЗАКРЫТИИ сигнала (переход
    в TERMINAL_STATUSES) -- пробует закоммитить журнал в GitHub (см. _commit_to_github,
    сам ограничен 1 коммитом в 5 минут)."""
    global _dirty
    while True:
        now = time.time()
        changed = False
        closed = False
        for rec in list(_journal.values()):
            if rec["status"] in TERMINAL_STATUSES:
                continue
            price, _age = live_prices.get_live_price(rec["symbol"])
            if price is None:
                continue

            if rec["status"] == "PENDING":
                if _touches_entry(price, rec["entry_lo"], rec["entry_hi"]):
                    rec["status"] = "ENTERED"
                    rec["entered_ts"] = now
                    rec["entered_price"] = price
                    rec["updated_ts"] = now
                    changed = True
                elif now - rec["ts"] > PENDING_EXPIRE_SEC:
                    rec["status"] = "EXPIRED"
                    rec["outcome"] = "EXPIRED"
                    rec["outcome_ts"] = now
                    rec["updated_ts"] = now
                    changed = True
                    closed = True

            elif rec["status"] == "ENTERED":
                status, level = _check_outcome(rec["direction"], price, rec["sl"],
                                                rec["tp1"], rec["tp2"], rec["tp3"])
                if status:
                    rec["status"] = status
                    rec["outcome"] = status
                    rec["outcome_level"] = level
                    rec["outcome_ts"] = now
                    rec["actual_r"] = _compute_actual_r(rec, level)
                    rec["updated_ts"] = now
                    changed = True
                    closed = True
                    await _notify_outcome(rec)

        if changed:
            _dirty = True
            _save()
        if closed:
            try:
                await _commit_to_github()
            except Exception as e:
                print(f"Signal Journal: _commit_to_github (on closure) failed: {e}")
        await asyncio.sleep(TRACK_INTERVAL_SEC)


def get_status_counts():
    """Для /radar_status: (активных, закрытых)."""
    active = sum(1 for r in _journal.values() if r["status"] not in TERMINAL_STATUSES)
    closed = sum(1 for r in _journal.values() if r["status"] in TERMINAL_STATUSES)
    return active, closed


def get_journal_summary(window_sec=None) -> dict:
    """Сводка для /journal. window_sec=None -- за всё время."""
    now = time.time()
    recs = list(_journal.values())
    if window_sec is not None:
        recs = [r for r in recs if now - r["ts"] <= window_sec]

    total = len(recs)
    entered = [r for r in recs if r.get("entered_ts") is not None]
    closed_with_outcome = [r for r in recs if r.get("outcome") in OUTCOME_STATUSES]
    wins = [r for r in closed_with_outcome if r["outcome"] != "SL_HIT"]
    losses = [r for r in closed_with_outcome if r["outcome"] == "SL_HIT"]

    entered_pct = round(len(entered) / total * 100, 1) if total else None
    win_rate = round(len(wins) / len(closed_with_outcome) * 100, 1) if closed_with_outcome else None
    r_values = [r["actual_r"] for r in closed_with_outcome if r.get("actual_r") is not None]
    avg_r = round(sum(r_values) / len(r_values), 2) if r_values else None

    by_source = {}
    for r in recs:
        s = r["source"]
        agg = by_source.setdefault(s, {"total": 0, "wins": 0, "losses": 0})
        agg["total"] += 1
        if r.get("outcome") == "SL_HIT":
            agg["losses"] += 1
        elif r.get("outcome") in ("TP1_HIT", "TP2_HIT", "TP3_HIT"):
            agg["wins"] += 1

    by_grade = {}
    for r in closed_with_outcome:
        g = r.get("grade") or "?"
        agg = by_grade.setdefault(g, {"total": 0, "wins": 0, "losses": 0})
        agg["total"] += 1
        if r["outcome"] == "SL_HIT":
            agg["losses"] += 1
        else:
            agg["wins"] += 1
    for agg in by_grade.values():
        agg["win_rate"] = round(agg["wins"] / agg["total"] * 100, 1) if agg["total"] else None

    return {
        "total": total, "entered_count": len(entered), "entered_pct": entered_pct,
        "win_rate": win_rate, "avg_r": avg_r,
        "wins": len(wins), "losses": len(losses),
        "by_source": by_source, "by_grade": by_grade,
    }


def get_stats_for_source(source: str) -> dict:
    """Для строки в карточке сигнала: '📒 Journal: N закрытых сигналов этого типа,
    win rate X%'. closed -- количество ЗАКРЫТЫХ С ИСХОДОМ (не EXPIRED) записей этого
    source, win_rate -- None если closed==0."""
    recs = [r for r in _journal.values() if r["source"] == source and r.get("outcome") in OUTCOME_STATUSES]
    closed = len(recs)
    wins = sum(1 for r in recs if r["outcome"] != "SL_HIT")
    win_rate = round(wins / closed * 100, 1) if closed else None
    return {"closed": closed, "wins": wins, "win_rate": win_rate}
