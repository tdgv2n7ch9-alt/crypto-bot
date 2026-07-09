"""
BEST TRADE — Subscribers (список чатов для автосигналов/алертов, /start и /stop)

Хранение: in-memory (Railway ephemeral -- при пересборке контейнера обнуляется, как это
и произошло при миграции Nixpacks->Dockerfile) ПЛЮС персистентность через GitHub Contents
API (data/chat_ids.json в том же приватном репо, что и код) -- та же схема, что и
signal_journal.py (GITHUB_TOKEN/GITHUB_OWNER/GITHUB_REPO, last-write-wins по updated_ts,
sha-конфликт -> перечитать и повторить). Раньше подписчики хранились в локальном
chat_ids.txt -- он в .gitignore, поэтому не входит в Docker-образ, и при пересборке
сервиса (см. Railway-инцидент 2026-07-07/08) список подписчиков просто исчезал, из-за
чего send_scheduled()/check_alerts() рассылали (или не рассылали) сигналы неизвестно кому.

Записи хранятся как {"subscribed": bool, "updated_ts": float}, а не просто "в списке/нет"
-- иначе последующий мерж с GitHub мог бы "воскресить" явно отписавшегося через /stop
пользователя, если удалённая копия ещё не знает об отписке.

Fallback: если GitHub недоступен/не настроен на старте -- OWNER_CHAT_ID добавляется как
подписчик автоматически, чтобы владелец никогда не выпадал из рассылки автосигналов.
"""

import asyncio
import base64
import json
import os
import time

import requests

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "").strip()
GITHUB_REPO = os.getenv("GITHUB_REPO", "").strip()
GITHUB_CHAT_IDS_PATH = "data/chat_ids.json"
GITHUB_COMMIT_MIN_INTERVAL_SEC = 60  # список подписчиков маленький и меняется редко -- мягче, чем у журнала

_subscribers = {}       # chat_id (int) -> {"subscribed": bool, "updated_ts": float}
_github_sha = None
_dirty = False
_last_commit_ts = 0.0
_github_lock = None
_last_github_error = None
_load_source = "none"   # "github" | "fallback" | "none" (ещё не инициализировано)


def _validate_github_token() -> str:
    """См. signal_journal._validate_github_token -- тот же класс паст-артефакта
    (smart quote/BOM/неразрывный пробел в Railway env vars) даёт малопонятную ошибку
    'latin-1 codec can't encode' вместо явного указания на проблему в токене."""
    try:
        GITHUB_TOKEN.encode("ascii")
        return ""
    except UnicodeEncodeError as e:
        bad_char = GITHUB_TOKEN[e.start]
        return (f"GITHUB_TOKEN содержит не-ASCII символ на позиции {e.start} "
                f"('{bad_char}', U+{ord(bad_char):04X})")


def _github_configured() -> bool:
    return bool(GITHUB_TOKEN and GITHUB_OWNER and GITHUB_REPO)


def _github_api_base() -> str:
    return f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"


def _github_headers() -> dict:
    return {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}


def _github_get_file_sync():
    """GET data/chat_ids.json. Возвращает (subs_dict, sha) либо (None, None), если файла
    ещё нет / GitHub не настроен / недоступен. Синхронно -- вызывать через run_in_executor."""
    global _last_github_error
    if not _github_configured():
        return None, None
    token_issue = _validate_github_token()
    if token_issue:
        _last_github_error = token_issue
        print(f"Subscribers: {token_issue}")
        return None, None
    try:
        r = requests.get(f"{_github_api_base()}/contents/{GITHUB_CHAT_IDS_PATH}",
                          headers=_github_headers(), timeout=15)
        if r.status_code == 404:
            _last_github_error = None
            return None, None
        r.raise_for_status()
        data = r.json()
        content = base64.b64decode(data["content"]).decode()
        payload = json.loads(content)
        subs = {int(k): v for k, v in payload.get("subscribers", {}).items()}
        _last_github_error = None
        return subs, data["sha"]
    except Exception as e:
        detail = getattr(getattr(e, "response", None), "text", "")
        _last_github_error = f"GET failed: {e} {detail[:300]}"
        print(f"Subscribers: GitHub load failed ({_last_github_error})")
        return None, None


def _github_put_file_sync(subs: dict, sha):
    """PUT data/chat_ids.json. Возвращает новый sha при успехе, None при ошибке,
    "conflict" при 409 (sha устарел -- вызывающий должен перечитать и повторить)."""
    global _last_github_error
    if not _github_configured():
        return None
    token_issue = _validate_github_token()
    if token_issue:
        _last_github_error = token_issue
        print(f"Subscribers: {token_issue}")
        return None
    try:
        payload = {"subscribers": subs}
        body = {
            "message": f"subscribers: sync {len(subs)} chat_ids",
            "content": base64.b64encode(json.dumps(payload).encode()).decode(),
        }
        if sha:
            body["sha"] = sha
        r = requests.put(f"{_github_api_base()}/contents/{GITHUB_CHAT_IDS_PATH}",
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
        print(f"Subscribers: GitHub save failed ({_last_github_error})")
        return None


def _merge_subscribers(local: dict, remote: dict) -> dict:
    """last-write-wins по updated_ts, тот же принцип, что signal_journal._merge_records."""
    merged = dict(local)
    for cid, rrec in remote.items():
        lrec = merged.get(cid)
        if lrec is None:
            merged[cid] = rrec
            continue
        if rrec.get("updated_ts", 0) > lrec.get("updated_ts", 0):
            merged[cid] = rrec
    return merged


def _add_owner_fallback(reason: str):
    global _load_source
    owner_id = int(os.getenv("OWNER_CHAT_ID", "7009350191"))
    _subscribers[owner_id] = {"subscribed": True, "updated_ts": time.time()}
    _load_source = "fallback"
    print(f"Subscribers: {reason} -- fallback: OWNER_CHAT_ID {owner_id} добавлен подписчиком")


async def startup_sync():
    """Вызывается один раз при старте бота. Подтягивает подписчиков из GitHub и мержит с
    локальным (пустым на свежем контейнере) состоянием. Если GitHub не настроен или
    недоступен -- OWNER_CHAT_ID добавляется как fallback-подписчик (см. докстринг модуля).
    Не бросает исключений наружу -- отсутствие/недоступность GitHub не должна мешать боту
    стартовать."""
    global _subscribers, _github_sha, _load_source
    if not _github_configured():
        _add_owner_fallback("GITHUB_TOKEN/GITHUB_OWNER/GITHUB_REPO не заданы")
        return
    try:
        loop = asyncio.get_event_loop()
        remote, sha = await loop.run_in_executor(None, _github_get_file_sync)
        _github_sha = sha
        if remote is None:
            if _last_github_error:
                _add_owner_fallback(f"GitHub недоступен ({_last_github_error})")
            else:
                # файла ещё нет -- это не ошибка (первый запуск), но подписчиков пока
                # тоже нет нигде -- владелец всё равно должен получать автосигналы.
                _add_owner_fallback("data/chat_ids.json ещё не создан в GitHub")
            return
        _subscribers = _merge_subscribers(_subscribers, remote)
        _load_source = "github"
        active = sum(1 for s in _subscribers.values() if s.get("subscribed"))
        print(f"Subscribers: загружено {len(remote)} записей из GitHub, активных подписчиков: {active}")
    except Exception as e:
        _add_owner_fallback(f"startup_sync failed ({e})")


def _get_github_lock():
    global _github_lock
    if _github_lock is None:
        _github_lock = asyncio.Lock()
    return _github_lock


async def _commit_to_github(force: bool = False):
    """Коммитит текущих подписчиков в GitHub, если есть несохранённые изменения (_dirty)
    и с последнего коммита прошло >= GITHUB_COMMIT_MIN_INTERVAL_SEC (либо force=True).
    При конфликте sha (409) перечитывает файл и повторяет один раз. Не бросает исключений
    наружу -- ошибка сети не должна ронять /start или /stop."""
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
        subs = dict(_subscribers)
        for attempt in range(2):
            result = await loop.run_in_executor(None, _github_put_file_sync, subs, _github_sha)
            if result == "conflict":
                remote, sha = await loop.run_in_executor(None, _github_get_file_sync)
                _github_sha = sha
                if remote is not None:
                    subs = _merge_subscribers(subs, remote)
                continue
            if result:
                _github_sha = result
                _dirty = False
                _last_commit_ts = now
            break


async def subscribe(chat_id: int):
    """/start -- регистрирует chat_id как активного подписчика, коммитит в GitHub
    (мягкий rate-limit -- не чаще раза в минуту, см. GITHUB_COMMIT_MIN_INTERVAL_SEC)."""
    global _dirty
    _subscribers[chat_id] = {"subscribed": True, "updated_ts": time.time()}
    _dirty = True
    await _commit_to_github()


async def unsubscribe(chat_id: int):
    """/stop -- помечает chat_id как отписанного. Запись НЕ удаляется (а помечается
    subscribed=False с новым updated_ts) -- иначе последующий мерж с GitHub мог бы
    воскресить отписку, если там ещё есть старая subscribed=True запись."""
    global _dirty
    _subscribers[chat_id] = {"subscribed": False, "updated_ts": time.time()}
    _dirty = True
    await _commit_to_github()


def active_chat_ids() -> set:
    """Множество активных (subscribed=True) chat_id -- замена старому
    load_chat_ids() | user_chat_ids."""
    return {cid for cid, rec in _subscribers.items() if rec.get("subscribed")}


GITHUB_BACKUP_DIR = "backups"


def _github_put_backup_sync(path: str, payload: dict) -> bool:
    """PUT датированного снапшота в GitHub (ROADMAP П1.4) -- НОВЫЙ файл на каждую дату,
    в отличие от data/chat_ids.json (тот перезаписывается). Не требует sha -- это не
    обновление существующего файла, а создание нового пути. 422 (файл уже существует --
    повторный бэкап в тот же день) не считается ошибкой."""
    if not _github_configured():
        return False
    if _validate_github_token():
        return False
    try:
        body = {
            "message": f"backup: {path}",
            "content": base64.b64encode(
                json.dumps(payload, ensure_ascii=False, indent=2).encode()
            ).decode(),
        }
        r = requests.put(f"{_github_api_base()}/contents/{path}",
                          headers=_github_headers(), json=body, timeout=20)
        if r.status_code == 422:
            return True
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"Subscribers: backup PUT failed ({e})")
        return False


async def backup_snapshot(date_str: str) -> bool:
    """Дневной версионированный бэкап подписчиков в backups/<date>/chat_ids.json.
    Отдельно от data/chat_ids.json (тот -- рабочая копия, эта -- архив на дату)."""
    if not _github_configured():
        return False
    loop = asyncio.get_event_loop()
    payload = {"subscribers": {str(k): v for k, v in _subscribers.items()}}
    path = f"{GITHUB_BACKUP_DIR}/{date_str}/chat_ids.json"
    return await loop.run_in_executor(None, _github_put_backup_sync, path, payload)


def status() -> dict:
    """Для /radar_status: количество активных подписчиков + источник загрузки при старте
    (github/fallback) + последняя ошибка GitHub, если была."""
    return {
        "count": len(active_chat_ids()),
        "source": _load_source,
        "github_error": _last_github_error,
        "github_configured": _github_configured(),
    }
