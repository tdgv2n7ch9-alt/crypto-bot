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

Пакет SECURITY-HARDENING М1 (владелец "да"): та же запись/файл теперь несёт РОЛЬ
доступа ("role": "OWNER"/"VIP"/"TRIAL"/отсутствует) поверх уже существующего
"subscribed" (это разные оси -- subscribed решает про автосигналы, role решает про
доступ к командам вообще, см. access_control.py). Легаси-записи без "role" при
subscribed=True трактуются как VIP (грандфазеринг уже существующих подписчиков --
на момент внедрения это только сам владелец, но принцип общий). OWNER_CHAT_ID
получает роль OWNER ВСЕГДА, в обход хранилища -- см. access_control.get_role().
Инвайт-коды хранятся в том же файле, отдельным top-level ключом "invite_codes".
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

_subscribers = {}       # chat_id (int) -> {"subscribed": bool, "updated_ts": float, "role": str|None, "role_expires_ts": float|None}
_invite_codes = {}      # code (str) -> {"role": str, "expires_days": int|None, "created_ts": float, "used": bool, "used_by": int|None, "used_ts": float|None}
_github_sha = None
_dirty = False
_last_commit_ts = 0.0
_github_lock = None
_last_github_error = None
_load_source = "none"   # "github" | "fallback" | "none" (ещё не инициализировано)

ROLE_OWNER = "OWNER"
ROLE_VIP = "VIP"
ROLE_TRIAL = "TRIAL"
ROLE_NONE = "NONE"
_ROLE_LEVEL = {ROLE_NONE: 0, ROLE_TRIAL: 1, ROLE_VIP: 2, ROLE_OWNER: 3}


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
    """GET data/chat_ids.json. Возвращает (subs_dict, invite_codes_dict, sha) либо
    (None, None, None), если файла ещё нет / GitHub не настроен / недоступен. Синхронно
    -- вызывать через run_in_executor."""
    global _last_github_error
    if not _github_configured():
        return None, None, None
    token_issue = _validate_github_token()
    if token_issue:
        _last_github_error = token_issue
        print(f"Subscribers: {token_issue}")
        return None, None, None
    try:
        r = requests.get(f"{_github_api_base()}/contents/{GITHUB_CHAT_IDS_PATH}",
                          headers=_github_headers(), timeout=15)
        if r.status_code == 404:
            _last_github_error = None
            return None, None, None
        r.raise_for_status()
        data = r.json()
        content = base64.b64decode(data["content"]).decode()
        payload = json.loads(content)
        subs = {int(k): v for k, v in payload.get("subscribers", {}).items()}
        codes = payload.get("invite_codes", {})
        _last_github_error = None
        return subs, codes, data["sha"]
    except Exception as e:
        detail = getattr(getattr(e, "response", None), "text", "")
        _last_github_error = f"GET failed: {e} {detail[:300]}"
        print(f"Subscribers: GitHub load failed ({_last_github_error})")
        return None, None, None


def _github_put_file_sync(subs: dict, codes: dict, sha):
    """PUT data/chat_ids.json (подписчики + инвайт-коды). Возвращает новый sha при успехе,
    None при ошибке, "conflict" при 409 (sha устарел -- вызывающий должен перечитать и
    повторить)."""
    global _last_github_error
    if not _github_configured():
        return None
    token_issue = _validate_github_token()
    if token_issue:
        _last_github_error = token_issue
        print(f"Subscribers: {token_issue}")
        return None
    try:
        payload = {"subscribers": subs, "invite_codes": codes or {}}
        body = {
            "message": f"subscribers: sync {len(subs)} chat_ids, {len(codes or {})} invite codes",
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


def _merge_invite_codes(local: dict, remote: dict) -> dict:
    """Коды создаются один раз, могут только перейти used=False -> used=True (никогда
    наоборот) -- при слиянии used=True побеждает независимо от того, с какой стороны."""
    merged = dict(local)
    for code, rrec in remote.items():
        lrec = merged.get(code)
        if lrec is None:
            merged[code] = rrec
            continue
        if rrec.get("used") and not lrec.get("used"):
            merged[code] = rrec
    return merged


def _add_owner_fallback(reason: str):
    global _load_source
    owner_id = int(os.getenv("OWNER_CHAT_ID", "7009350191"))
    _subscribers[owner_id] = {"subscribed": True, "updated_ts": time.time(), "role": ROLE_OWNER}
    _load_source = "fallback"
    print(f"Subscribers: {reason} -- fallback: OWNER_CHAT_ID {owner_id} добавлен подписчиком")


async def startup_sync():
    """Вызывается один раз при старте бота. Подтягивает подписчиков+инвайт-коды из GitHub
    и мержит с локальным (пустым на свежем контейнере) состоянием. Если GitHub не настроен
    или недоступен -- OWNER_CHAT_ID добавляется как fallback-подписчик (см. докстринг
    модуля). Не бросает исключений наружу -- отсутствие/недоступность GitHub не должна
    мешать боту стартовать."""
    global _subscribers, _invite_codes, _github_sha, _load_source
    if not _github_configured():
        _add_owner_fallback("GITHUB_TOKEN/GITHUB_OWNER/GITHUB_REPO не заданы")
        return
    try:
        loop = asyncio.get_event_loop()
        remote, codes, sha = await loop.run_in_executor(None, _github_get_file_sync)
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
        _invite_codes = _merge_invite_codes(_invite_codes, codes or {})
        _load_source = "github"
        active = sum(1 for s in _subscribers.values() if s.get("subscribed"))
        print(f"Subscribers: загружено {len(remote)} записей из GitHub, активных подписчиков: {active}, "
              f"инвайт-кодов: {len(_invite_codes)}")
    except Exception as e:
        _add_owner_fallback(f"startup_sync failed ({e})")


def _get_github_lock():
    global _github_lock
    if _github_lock is None:
        _github_lock = asyncio.Lock()
    return _github_lock


async def _commit_to_github(force: bool = False):
    """Коммитит текущих подписчиков+инвайт-коды в GitHub, если есть несохранённые
    изменения (_dirty) и с последнего коммита прошло >= GITHUB_COMMIT_MIN_INTERVAL_SEC
    (либо force=True). При конфликте sha (409) перечитывает файл и повторяет один раз.
    Не бросает исключений наружу -- ошибка сети не должна ронять /start или /stop."""
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
        codes = dict(_invite_codes)
        for attempt in range(2):
            result = await loop.run_in_executor(None, _github_put_file_sync, subs, codes, _github_sha)
            if result == "conflict":
                remote, remote_codes, sha = await loop.run_in_executor(None, _github_get_file_sync)
                _github_sha = sha
                if remote is not None:
                    subs = _merge_subscribers(subs, remote)
                    codes = _merge_invite_codes(codes, remote_codes or {})
                continue
            if result:
                _github_sha = result
                _dirty = False
                _last_commit_ts = now
            break


async def subscribe(chat_id: int):
    """/start -- регистрирует chat_id как активного подписчика, коммитит в GitHub
    (мягкий rate-limit -- не чаще раза в минуту, см. GITHUB_COMMIT_MIN_INTERVAL_SEC).
    Сохраняет уже существующие role/role_expires_ts (найдена и исправлена в Пакете
    SECURITY-HARDENING М1 находка: раньше эта функция ПОЛНОСТЬЮ перезаписывала запись,
    что стирало бы роль при повторном /start -- теперь только merge полей subscribed/
    updated_ts поверх уже существующей записи)."""
    global _dirty
    rec = dict(_subscribers.get(chat_id) or {})
    rec["subscribed"] = True
    rec["updated_ts"] = time.time()
    _subscribers[chat_id] = rec
    _dirty = True
    await _commit_to_github()


async def unsubscribe(chat_id: int):
    """/stop -- помечает chat_id как отписанного. Запись НЕ удаляется (а помечается
    subscribed=False с новым updated_ts) -- иначе последующий мерж с GitHub мог бы
    воскресить отписку, если там ещё есть старая subscribed=True запись. Роль (доступ
    к командам) НЕ трогается -- /stop это отписка от автосигналов, не отзыв доступа
    (для отзыва доступа -- /revoke, отдельная владельческая команда)."""
    global _dirty
    rec = dict(_subscribers.get(chat_id) or {})
    rec["subscribed"] = False
    rec["updated_ts"] = time.time()
    _subscribers[chat_id] = rec
    _dirty = True
    await _commit_to_github()


def active_chat_ids() -> set:
    """Множество активных (subscribed=True) chat_id -- замена старому
    load_chat_ids() | user_chat_ids."""
    return {cid for cid, rec in _subscribers.items() if rec.get("subscribed")}


# --- Пакет SECURITY-HARDENING М1 (владелец "да") -- роли и инвайт-коды ------------

def get_role_raw(chat_id: int) -> str:
    """Роль chat_id БЕЗ учёта OWNER_CHAT_ID-обхода (см. access_control.get_role() --
    та функция добавляет hardcoded OWNER для owner_id поверх этой). Учитывает
    просрочку TRIAL (role_expires_ts) и грандфазеринг легаси-записей
    (subscribed=True без явного role -> VIP, см. докстринг модуля)."""
    rec = _subscribers.get(chat_id)
    if not rec:
        return ROLE_NONE
    role = rec.get("role")
    if role is None:
        return ROLE_VIP if rec.get("subscribed") else ROLE_NONE
    if role == ROLE_TRIAL:
        expires = rec.get("role_expires_ts")
        if expires is not None and time.time() >= expires:
            return ROLE_NONE
    return role


async def set_role(chat_id: int, role: str, expires_ts: float = None):
    """Владельческая операция (/grant, /revoke -> role=ROLE_NONE, инвайт-редемпшн).
    Сохраняет subscribed/updated_ts, если запись уже была -- роль это отдельная ось."""
    global _dirty
    rec = dict(_subscribers.get(chat_id) or {"subscribed": False})
    rec["role"] = role
    rec["role_expires_ts"] = expires_ts
    rec["updated_ts"] = time.time()
    _subscribers[chat_id] = rec
    _dirty = True
    await _commit_to_github(force=True)  # admin-действие -- коммитим сразу, не ждём rate-limit


def list_users() -> list:
    """Для /users -- список всех известных chat_id с ролью/подпиской, для владельца."""
    out = []
    for cid, rec in _subscribers.items():
        out.append({
            "chat_id": cid,
            "role": get_role_raw(cid),
            "subscribed": rec.get("subscribed", False),
            "updated_ts": rec.get("updated_ts"),
        })
    return sorted(out, key=lambda x: x["updated_ts"] or 0, reverse=True)


def _gen_invite_code() -> str:
    import secrets
    return secrets.token_urlsafe(9)  # ~12 символов, URL-safe (годится для /start-payload)


async def generate_invite_code(role: str, expires_days: int = None) -> str:
    """/invite generate -- одноразовый код на конкретную роль (обычно VIP/TRIAL).
    Не самозапись -- код создаёт ТОЛЬКО владелец (гейт на уровне access_control,
    не здесь -- эта функция сама по себе не проверяет роль вызывающего)."""
    global _dirty
    code = _gen_invite_code()
    _invite_codes[code] = {
        "role": role, "expires_days": expires_days,
        "created_ts": time.time(), "used": False, "used_by": None, "used_ts": None,
    }
    _dirty = True
    await _commit_to_github(force=True)
    return code


async def redeem_invite_code(code: str, chat_id: int) -> str:
    """Погашает одноразовый код для chat_id -- возвращает выданную роль либо None,
    если код невалиден/уже использован. Одноразовость: "used" ставится сразу же,
    до commit -- гонка между двумя одновременными редемпшенами того же кода
    теоретически возможна (нет распределённой блокировки), но при последовательной
    обработке апдейтов одним процессом (как здесь) не встречается на практике."""
    global _dirty
    rec = _invite_codes.get(code)
    if not rec or rec.get("used"):
        return None
    rec["used"] = True
    rec["used_by"] = chat_id
    rec["used_ts"] = time.time()
    expires_days = rec.get("expires_days")
    expires_ts = (time.time() + expires_days * 86400) if expires_days else None
    await set_role(chat_id, rec["role"], expires_ts)  # уже коммитит (force=True внутри)
    _dirty = True
    await _commit_to_github(force=True)
    return rec["role"]


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
