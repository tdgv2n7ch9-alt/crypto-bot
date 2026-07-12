"""
BEST TRADE — Security Log (Пакет SECURITY-HARDENING М4, владелец "да")

Журнал security-событий: команды, отказы в доступе, гранты/отзывы ролей, ошибки
auth. Тот же паттерн, что shadow_engine.py/subscribers.py -- локальный JSON
(Railway ephemeral) ПЛЮС best-effort персистентность через GitHub Contents API
(journal/security_log.json), но с важным отличием: log_event() -- ЧИСТО
ЛОКАЛЬНАЯ, БЫСТРАЯ операция (вызывается на КАЖДЫЙ апдейт внутри
access_control.enforce(), не может позволить себе сетевой вызов на каждый
вызов) -- GitHub-синк вынесен в ОТДЕЛЬНУЮ периодическую задачу (sync_to_github(),
регистрируется в scheduler бота, не в самом log_event()).

Использует retry-catchup паттерн, найденный и исправленный в Пакете 11 М1 для
shadow_engine.py (см. SHADOW_ANALYSIS.md 23:42) -- ОТ РОЖДЕНИЯ, не задним числом:
_github_get_shadow_sync-эквивалент здесь честно различает "файла нет" (пусто,
можно создавать) от "запрос не удался" (ошибка, синк прерывается, не пытается
затереть существующие записи меньшим локальным списком).

Честный кап на размер: MAX_LOCAL_EVENTS -- держим последние N событий, не
бесконечный рост файла (в отличие от shadow_signals.json/signals.json, где
каждая запись -- ценные данные для анализа надолго, security-лог нужен для
недавней картины, не для истории на годы).
"""
import base64
import json
import os
import time

import requests

import signal_journal  # переиспользуем _github_configured/_validate_github_token/_github_headers/_github_api_base

SECURITY_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "journal", "security_log.json")
GITHUB_SECURITY_LOG_PATH = "journal/security_log.json"
MAX_LOCAL_EVENTS = 5000

_events: list = []
_dirty = False
_github_sha = None

EVENT_COMMAND = "command"
EVENT_DENIED = "denied"
EVENT_GRANT = "grant"
EVENT_REVOKE = "revoke"
EVENT_INVITE_GENERATED = "invite_generated"
EVENT_INVITE_REDEEMED = "invite_redeemed"
EVENT_AUTO_BAN = "auto_ban"
EVENT_RATE_LIMITED = "rate_limited"
EVENT_FLOOD_GUARD = "flood_guard"
EVENT_LOCKDOWN = "lockdown"
EVENT_UNLOCK = "unlock"
EVENT_AUTH_ERROR = "auth_error"


def _atomic_write_json(path: str, obj) -> bool:
    """Тот же паттерн, что shadow_engine._atomic_write_json."""
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
        print(f"security_log: atomic write failed ({e})")
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return False


def _load_local() -> list:
    if not os.path.exists(SECURITY_LOG_FILE):
        return []
    try:
        with open(SECURITY_LOG_FILE) as f:
            data = json.load(f)
        return data.get("events", []) if isinstance(data, dict) else []
    except Exception:
        return []


def log_event(event_type: str, chat_id: int, detail: str = "", ts: float = None) -> None:
    """Быстрая, чисто локальная запись -- НЕ делает сетевых вызовов, безопасна для
    вызова на каждый апдейт внутри access_control.enforce(). Не бросает исключений
    наружу -- сбой логирования не должен ронять обработку апдейта."""
    global _dirty
    try:
        _events.append({
            "ts": ts if ts is not None else time.time(),
            "type": event_type, "chat_id": chat_id, "detail": detail,
        })
        if len(_events) > MAX_LOCAL_EVENTS:
            del _events[: len(_events) - MAX_LOCAL_EVENTS]
        _dirty = True
        _atomic_write_json(SECURITY_LOG_FILE, {"schema_version": 1, "events": _events})
    except Exception as e:
        print(f"security_log.log_event failed: {e}")


def load_startup_events() -> None:
    """Подтягивает уже накопленные локальные события при старте процесса (если
    контейнер не пересобирался -- иначе честно пусто, это Railway ephemeral)."""
    global _events
    _events = _load_local()


def _github_get_sync():
    """Возвращает (events, sha) при успехе/пустом файле, (False, None) при ошибке
    запроса -- ТОТ ЖЕ различимый контракт, что shadow_engine._github_get_shadow_sync
    после фикса Пакета 11 М1 (не схлопывает "нет файла" и "сбой запроса")."""
    if not signal_journal._github_configured():
        return None, None
    token_issue = signal_journal._validate_github_token()
    if token_issue:
        print(f"security_log: {token_issue}")
        return None, None
    try:
        r = requests.get(f"{signal_journal._github_api_base()}/contents/{GITHUB_SECURITY_LOG_PATH}",
                          headers=signal_journal._github_headers(), timeout=15)
        if r.status_code == 404:
            return [], None
        r.raise_for_status()
        data = r.json()
        content = base64.b64decode(data["content"]).decode()
        payload = json.loads(content)
        events = payload.get("events", []) if isinstance(payload, dict) else []
        return events, data["sha"]
    except Exception as e:
        detail = getattr(getattr(e, "response", None), "text", "")
        print(f"security_log: GitHub GET failed ({e} {detail[:300]})")
        return False, None


def _github_put_sync(events: list, sha):
    if not signal_journal._github_configured():
        return None
    token_issue = signal_journal._validate_github_token()
    if token_issue:
        print(f"security_log: {token_issue}")
        return None
    try:
        payload = {"schema_version": 1, "events": events}
        body = {
            "message": f"security_log: {len(events)} событий",
            "content": base64.b64encode(json.dumps(payload, ensure_ascii=False).encode()).decode(),
        }
        if sha:
            body["sha"] = sha
        r = requests.put(f"{signal_journal._github_api_base()}/contents/{GITHUB_SECURITY_LOG_PATH}",
                          headers=signal_journal._github_headers(), json=body, timeout=20)
        if r.status_code == 409:
            return "conflict"
        r.raise_for_status()
        return r.json()["content"]["sha"]
    except Exception as e:
        detail = getattr(getattr(e, "response", None), "text", "")
        print(f"security_log: GitHub PUT failed ({e} {detail[:300]})")
        return None


async def sync_to_github() -> bool:
    """Периодическая best-effort синхронизация в GitHub -- регистрируется отдельной
    задачей в scheduler бота (интервал, не на каждое событие). Catchup-логика: если
    локальный список длиннее удалённого -- отправляет ВЕСЬ локальный список (не
    только новые события с момента последнего синка -- события здесь неупорядоченно
    дописываются, простой append надёжнее точечного дифа на этом объёме)."""
    global _github_sha, _dirty
    if not signal_journal._github_configured() or not _dirty:
        return False
    import asyncio
    loop = asyncio.get_event_loop()
    for attempt in range(2):
        remote, sha = await loop.run_in_executor(None, _github_get_sync)
        if remote is False:
            return False  # транзиентная ошибка GET -- не пытаемся PUT вслепую
        events_to_push = _events[-MAX_LOCAL_EVENTS:]
        result = await loop.run_in_executor(None, _github_put_sync, events_to_push, sha)
        if result == "conflict":
            continue
        if result:
            _github_sha = result
            _dirty = False
            return True
        return False
    return False


def get_daily_summary(window_sec: float = 24 * 3600, now_ts: float = None) -> dict:
    """Для утренней/дневной сводки -- счётчики событий за окно, по типам. Честно
    считает 0 на пустых данных, не выдумывает."""
    now = now_ts if now_ts is not None else time.time()
    cutoff = now - window_sec
    recent = [e for e in _events if e.get("ts", 0) >= cutoff]
    by_type: dict = {}
    for e in recent:
        t = e.get("type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1
    return {"total": len(recent), "by_type": by_type}
