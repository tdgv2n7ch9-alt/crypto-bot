"""
BEST TRADE — Access Control (Пакет SECURITY-HARDENING М1, владелец "да")

Deny-by-default: единый auth-слой ПЕРЕД каждым хендлером (PTB group=-1, см.
install() ниже) -- любой chat_id БЕЗ достаточной роли получает МОЛЧАЛИВЫЙ отказ
(нет ответа вообще, чтобы не подтверждать факт существования бота этому chat_id),
кроме одного явного исключения -- /start с валидным инвайт-кодом (единственный
способ попасть в систему, самозаписи нет).

Роли, по возрастанию прав: NONE(0) < TRIAL(1) < VIP(2) < OWNER(3) -- см.
subscribers.py, где реально живёт хранилище (та же запись/файл, что подписчики,
роль -- отдельное поле). OWNER_CHAT_ID получает роль OWNER ВСЕГДА, в обход
хранилища -- get_role() ниже -- это единственный "жёстко прошитый" случай,
чтобы владелец не мог случайно заблокировать сам себя этим же механизмом
(например, если GitHub недоступен и роль в сторе не подтягивается).

COMMAND_ROLE_MAP -- ЧЕСТНО, первый черновик распределения команд по ролям
(какие команды видит TRIAL/VIP/OWNER) -- это уже отчасти БИЗНЕС-решение
(что показывать бесплатному триалу, что премиум-подписчику), не чисто
техническое. Сделан РАЗУМНЫЙ дефолт (см. комментарии по группам ниже), но
это ПРЕДЛОЖЕНИЕ на пересмотр владельцем, не финальное решение -- см.
PROGRESS.md запись Пакета SECURITY-HARDENING М1.
"""
import base64
import json
import os
import time

import subscribers

ROLE_NONE = subscribers.ROLE_NONE
ROLE_TRIAL = subscribers.ROLE_TRIAL
ROLE_VIP = subscribers.ROLE_VIP
ROLE_OWNER = subscribers.ROLE_OWNER
_ROLE_LEVEL = subscribers._ROLE_LEVEL


def _owner_id() -> int:
    import os
    return int(os.getenv("OWNER_CHAT_ID", "7009350191"))


# --- Пакет SECURITY-HARDENING М3 (владелец "да") -- анти-абьюз ---------------------
# In-memory (не персистентное) состояние -- rate-limit/flood-guard окна короткие
# (десятки секунд-минуты), рестарт процесса естественно "прощает" временный абьюз.
# Единственное, что персистентно -- сам БАН (role=NONE через subscribers.set_role(),
# та же персистентная роль, что и везде), не счётчики к нему.

RATE_LIMIT_MAX_PER_MIN = 20        # команд/мин на пользователя, дальше -- кулдаун
RATE_LIMIT_WINDOW_SEC = 60.0
RATE_LIMIT_COOLDOWN_SEC = 60.0     # молчаливый отказ на это время после превышения

INVITE_FAIL_BAN_THRESHOLD = 5      # неудачных инвайт-кодов подряд -> автобан + алерт
GLOBAL_FLOOD_THRESHOLD_PER_MIN = 200   # суммарно по всем chat_id -- алерт владельцу
GLOBAL_FLOOD_ALERT_COOLDOWN_SEC = 300.0  # не спамить владельца при продолжающемся флуде

_command_history: dict = {}    # chat_id -> [timestamps] (только не-OWNER роли)
_cooldown_until: dict = {}     # chat_id -> ts, до которого молчаливый отказ (rate-limit)
_invite_fail_count: dict = {}  # chat_id -> число неудачных инвайт-попыток подряд
_global_command_history: list = []  # timestamps апдейтов всех non-OWNER chat_id
_last_flood_alert_ts = 0.0


def _prune_window(timestamps: list, now: float, window_sec: float) -> list:
    """Чистая функция -- убирает записи старше window_sec от now. Тестируется без
    состояния модуля."""
    cutoff = now - window_sec
    return [t for t in timestamps if t >= cutoff]


def check_rate_limit(chat_id: int, now: float = None) -> bool:
    """True, если chat_id разрешено выполнить команду прямо сейчас (в пределах
    лимита), False -- если превышен лимит (и chat_id уже поставлен на кулдаун).
    OWNER не подлежит рейт-лимиту вообще -- вызывающая сторона (enforce()) не
    вызывает эту функцию для OWNER-ролей, но и сама функция безопасна для любого
    chat_id, если вызвана."""
    now = now if now is not None else time.time()
    cooldown = _cooldown_until.get(chat_id)
    if cooldown is not None and now < cooldown:
        return False
    hist = _prune_window(_command_history.get(chat_id, []), now, RATE_LIMIT_WINDOW_SEC)
    hist.append(now)
    _command_history[chat_id] = hist
    if len(hist) > RATE_LIMIT_MAX_PER_MIN:
        _cooldown_until[chat_id] = now + RATE_LIMIT_COOLDOWN_SEC
        return False
    return True


def check_global_flood(now: float = None) -> bool:
    """True, если сработал глобальный flood-guard (суммарная нагрузка всех
    non-OWNER chat_id превысила порог за минуту) -- вызывающая сторона решает,
    алертить владельца или нет (см. _maybe_alert_flood)."""
    global _global_command_history
    now = now if now is not None else time.time()
    _global_command_history = _prune_window(_global_command_history, now, 60.0)
    _global_command_history.append(now)
    return len(_global_command_history) > GLOBAL_FLOOD_THRESHOLD_PER_MIN


def record_invite_failure(chat_id: int) -> bool:
    """Учитывает неудачную попытку инвайт-кода для chat_id. Возвращает True, если
    достигнут порог автобана (INVITE_FAIL_BAN_THRESHOLD) -- вызывающая сторона
    (bot.py cmd_start) сама вызывает subscribers.set_role(chat_id, ROLE_NONE) и
    алертит владельца, эта функция только считает."""
    count = _invite_fail_count.get(chat_id, 0) + 1
    _invite_fail_count[chat_id] = count
    return count >= INVITE_FAIL_BAN_THRESHOLD


def reset_invite_failures(chat_id: int):
    """Успешный редемпшн -- сбросить счётчик неудач (иначе легитимный пользователь,
    пару раз ошибившийся в коде, а потом введший верный, всё равно копил бы счётчик
    к следующему разу)."""
    _invite_fail_count.pop(chat_id, None)


async def _maybe_alert_owner_flood(context) -> None:
    """Best-effort алерт владельцу при глобальном флуде -- не чаще раза в
    GLOBAL_FLOOD_ALERT_COOLDOWN_SEC, чтобы не заспамить владельца тем же алертом
    раз в секунду, пока атака продолжается."""
    global _last_flood_alert_ts
    now = time.time()
    if now - _last_flood_alert_ts < GLOBAL_FLOOD_ALERT_COOLDOWN_SEC:
        return
    _last_flood_alert_ts = now
    try:
        await context.bot.send_message(
            _owner_id(),
            f"🚨 *Flood-guard*: >{GLOBAL_FLOOD_THRESHOLD_PER_MIN} команд/мин суммарно "
            f"по всем не-OWNER чатам -- возможна скоординированная атака.",
            parse_mode="Markdown")
    except Exception as e:
        print(f"access_control: flood alert failed: {e}")


# --- Lockdown (Пакет SECURITY-HARDENING М7, владелец "да") -----------------------
# /lockdown (OWNER) мгновенно замораживает ВСЕ хендлеры кроме владельца -- до
# /unlock. Проверка в enforce() -- чисто in-memory (никакого сетевого вызова на
# каждый апдейт, тот же принцип, что security_log.log_event()). Персистентность --
# best-effort через GitHub Contents API (тот же паттерн, что shadow_engine.py/
# subscribers.py/security_log.py), чтобы состояние lockdown пережило рестарт
# контейнера (Railway-редеплой посреди инцидента). ЧЕСТНО: если на старте GitHub
# недоступен/не настроен -- дефолт FALSE (разблокировано), не FALSE-safe в обратную
# сторону -- см. RUNBOOK_SECURITY.md, при активном инциденте после любого рестарта
# владелец должен проверить статус и при необходимости переиздать /lockdown.

LOCKDOWN_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "lockdown_state.json")
GITHUB_LOCKDOWN_PATH = "data/lockdown_state.json"

_lockdown_active = False


def is_locked_down() -> bool:
    return _lockdown_active


def _atomic_write_json(path: str, obj) -> bool:
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
        print(f"access_control: lockdown local write failed ({e})")
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return False


def _load_lockdown_local() -> bool:
    if not os.path.exists(LOCKDOWN_STATE_FILE):
        return False
    try:
        with open(LOCKDOWN_STATE_FILE) as f:
            data = json.load(f)
        return bool(data.get("active", False))
    except Exception:
        return False


def _github_get_lockdown_sync():
    """Возвращает (active, sha) при успехе (sha=None, если файла ещё нет -- тогда
    active=False по определению), либо (None, None) при недоступности/ошибке --
    вызывающий код в этом случае честно остаётся на локальном/дефолтном значении,
    не пытается угадать."""
    import signal_journal
    import requests
    if not signal_journal._github_configured():
        return None, None
    token_issue = signal_journal._validate_github_token()
    if token_issue:
        print(f"access_control: {token_issue}")
        return None, None
    try:
        r = requests.get(f"{signal_journal._github_api_base()}/contents/{GITHUB_LOCKDOWN_PATH}",
                          headers=signal_journal._github_headers(), timeout=15)
        if r.status_code == 404:
            return False, None
        r.raise_for_status()
        data = r.json()
        content = base64.b64decode(data["content"]).decode()
        payload = json.loads(content)
        return bool(payload.get("active", False)), data["sha"]
    except Exception as e:
        detail = getattr(getattr(e, "response", None), "text", "")
        print(f"access_control: lockdown GitHub GET failed ({e} {detail[:300]})")
        return None, None


def _github_put_lockdown_sync(active: bool, sha):
    import signal_journal
    import requests
    if not signal_journal._github_configured():
        return None
    token_issue = signal_journal._validate_github_token()
    if token_issue:
        print(f"access_control: {token_issue}")
        return None
    try:
        payload = {"active": active, "ts": time.time()}
        body = {
            "message": f"lockdown: {'ON' if active else 'OFF'}",
            "content": base64.b64encode(json.dumps(payload, ensure_ascii=False).encode()).decode(),
        }
        if sha:
            body["sha"] = sha
        r = requests.put(f"{signal_journal._github_api_base()}/contents/{GITHUB_LOCKDOWN_PATH}",
                          headers=signal_journal._github_headers(), json=body, timeout=20)
        if r.status_code == 409:
            return "conflict"
        r.raise_for_status()
        return r.json()["content"]["sha"]
    except Exception as e:
        detail = getattr(getattr(e, "response", None), "text", "")
        print(f"access_control: lockdown GitHub PUT failed ({e} {detail[:300]})")
        return None


async def load_lockdown_state() -> None:
    """Вызывается на старте бота (post_init) -- пытается восстановить lockdown-
    состояние из GitHub (переживает рестарт контейнера), иначе локальный файл,
    иначе честный дефолт FALSE."""
    global _lockdown_active
    import asyncio
    loop = asyncio.get_event_loop()
    active, _sha = await loop.run_in_executor(None, _github_get_lockdown_sync)
    if active is not None:
        _lockdown_active = active
        _atomic_write_json(LOCKDOWN_STATE_FILE, {"active": active, "ts": time.time()})
        return
    _lockdown_active = _load_lockdown_local()


async def set_lockdown(active: bool) -> None:
    """Мгновенный эффект -- флаг в памяти меняется ПЕРВЫМ, до любого сетевого
    вызова (чтобы /lockdown действовал мгновенно даже если GitHub недоступен).
    Локальная запись и GitHub-синк -- best-effort персистентность поверх этого."""
    global _lockdown_active
    _lockdown_active = active
    _atomic_write_json(LOCKDOWN_STATE_FILE, {"active": active, "ts": time.time()})
    import asyncio
    loop = asyncio.get_event_loop()
    _cur, sha = await loop.run_in_executor(None, _github_get_lockdown_sync)
    for _attempt in range(2):
        result = await loop.run_in_executor(None, _github_put_lockdown_sync, active, sha)
        if result == "conflict":
            _cur, sha = await loop.run_in_executor(None, _github_get_lockdown_sync)
            continue
        break


def get_role(chat_id: int) -> str:
    """Роль chat_id С учётом hardcoded OWNER_CHAT_ID-обхода -- владелец ВСЕГДА OWNER,
    независимо от состояния хранилища (см. докстринг модуля)."""
    if chat_id == _owner_id():
        return ROLE_OWNER
    return subscribers.get_role_raw(chat_id)


def role_allows(role: str, min_role: str) -> bool:
    return _ROLE_LEVEL.get(role, 0) >= _ROLE_LEVEL.get(min_role, 0)


# --- Карта команда -> минимальная роль -------------------------------------------
# TRIAL: только "обзор" (командное меню 1/market) -- явно по спецификации владельца
#   ("TRIAL (обзор, N дней)").
# VIP: все сигнальные/рыночные команды (спецификация "VIP (сигналы+рынок)").
# OWNER: административные/диагностические команды + все VIP-команды (по иерархии).
COMMAND_ROLE_MAP = {
    # --- базовые, доступны TRIAL+ ---
    "market": ROLE_TRIAL, "1": ROLE_TRIAL,
    "myid": ROLE_TRIAL, "menu": ROLE_TRIAL, "stop": ROLE_TRIAL,

    # --- сигналы/рынок, VIP+ ---
    "coin": ROLE_VIP, "2": ROLE_VIP,
    "signals": ROLE_VIP, "3": ROLE_VIP,
    "top": ROLE_VIP, "4": ROLE_VIP,
    "rockets": ROLE_VIP, "5": ROLE_VIP,
    "watchlist": ROLE_VIP, "6": ROLE_VIP,
    "precision": ROLE_VIP, "7": ROLE_VIP,
    "game": ROLE_VIP, "8": ROLE_VIP,
    "full": ROLE_VIP, "spot": ROLE_VIP, "long": ROLE_VIP, "short": ROLE_VIP,
    "x100": ROLE_VIP, "whales": ROLE_VIP, "patterns": ROLE_VIP, "zones": ROLE_VIP,
    "health": ROLE_VIP, "radar_status": ROLE_VIP,
    "stats": ROLE_VIP, "journal": ROLE_VIP,

    # --- административные, OWNER-only ---
    "zones_set": ROLE_OWNER, "journal_sync": ROLE_OWNER,
    "grant": ROLE_OWNER, "revoke": ROLE_OWNER, "users": ROLE_OWNER,
    "invite": ROLE_OWNER, "lockdown": ROLE_OWNER, "unlock": ROLE_OWNER, "trace": ROLE_OWNER,
}
# "start" НЕ в карте -- обрабатывается отдельно ниже (единственная команда,
# доступная NONE-роли, ради инвайт-редемпшна).
DEFAULT_MIN_ROLE = ROLE_OWNER  # неизвестная команда -- безопасный дефолт (deny by default)


def _extract_command(update) -> str:
    """Возвращает имя команды без "/" в нижнем регистре, либо None, если апдейт --
    не команда (текстовое сообщение/inline-кнопка обрабатываются отдельно)."""
    msg = update.effective_message
    if not msg or not msg.text or not msg.text.startswith("/"):
        return None
    cmd = msg.text.split()[0][1:].split("@")[0]  # срезать "/" и возможный "@botname"
    return cmd.lower()


def _role_check(role: str, update, context) -> bool:
    """Чистая (без побочных эффектов) проверка "разрешает ли роль этот апдейт" --
    вынесена отдельно от enforce(), чтобы анти-абьюз (rate-limit/flood-guard) в
    enforce() применялся ЕДИНООБРАЗНО ко всем веткам (callback/текст/команда),
    не дублируя код в каждой ветке."""
    if update.callback_query is not None:
        return role_allows(role, ROLE_TRIAL)

    cmd = _extract_command(update)
    if cmd is None:
        return role_allows(role, ROLE_TRIAL)

    if cmd == "start":
        args = context.args or []
        if args:
            return True  # инвайт-редемпшн -- пропускаем всегда, cmd_start сам решит
        return role_allows(role, ROLE_TRIAL)

    min_role = COMMAND_ROLE_MAP.get(cmd, DEFAULT_MIN_ROLE)
    return role_allows(role, min_role)


async def enforce(update, context):
    """PTB group=-1 хендлер -- вызывается ДО любого другого хендлера (см. install()).
    Молчаливо останавливает обработку (ApplicationHandlerStop) для недостаточной роли
    ИЛИ превышенного rate-limit (Пакет SECURITY-HARDENING М3) -- никакого ответа
    пользователю в обоих случаях, чтобы не подтверждать существование бота (кроме
    инвайт-редемпшна через /start, который сам по себе честный ответ по дизайну).
    Снаружи rate-limit и "недостаточная роль" выглядят одинаково (тишина) -- так и
    задумано, не раскрываем причину отказа."""
    from telegram.ext import ApplicationHandlerStop

    chat = update.effective_chat
    if chat is None:
        return  # системные апдейты без chat -- пропускаем, не наш случай
    chat_id = chat.id
    role = get_role(chat_id)

    import security_log

    if not _role_check(role, update, context):
        security_log.log_event(security_log.EVENT_DENIED, chat_id,
                                f"role={role} cmd={_extract_command(update) or '(text/callback)'}")
        raise ApplicationHandlerStop

    # Lockdown (М7) -- проверяется ПОСЛЕ обычной ролевой проверки (т.е. работает даже
    # для команд, на которые роли обычно хватило бы), но ДО анти-абьюза. OWNER
    # полностью исключён -- иначе /unlock самого владельца было бы некому вызвать.
    if _lockdown_active and role != ROLE_OWNER:
        security_log.log_event(security_log.EVENT_DENIED, chat_id, "lockdown")
        raise ApplicationHandlerStop

    # Анти-абьюз (М3) -- ТОЛЬКО для прошедших ролевую проверку, OWNER полностью
    # исключён (владелец не может сам себя случайно зарейтлимитить).
    if role != ROLE_OWNER:
        now = time.time()
        if check_global_flood(now):
            security_log.log_event(security_log.EVENT_FLOOD_GUARD, chat_id, "")
            await _maybe_alert_owner_flood(context)
        if not check_rate_limit(chat_id, now):
            security_log.log_event(security_log.EVENT_RATE_LIMITED, chat_id, "")
            raise ApplicationHandlerStop

    cmd = _extract_command(update)
    if cmd:
        security_log.log_event(security_log.EVENT_COMMAND, chat_id, cmd)


def install(app):
    """Регистрирует enforce() в группе -1 (обрабатывается ДО group=0, где живут все
    обычные хендлеры) -- ApplicationHandlerStop внутри enforce() останавливает
    обработку этого апдейта дальше по всем группам."""
    from telegram.ext import TypeHandler
    from telegram import Update
    app.add_handler(TypeHandler(Update, enforce), group=-1)
