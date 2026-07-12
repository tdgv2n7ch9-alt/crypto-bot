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


async def enforce(update, context):
    """PTB group=-1 хендлер -- вызывается ДО любого другого хендлера (см. install()).
    Молчаливо останавливает обработку (ApplicationHandlerStop) для недостаточной роли
    -- никакого ответа пользователю, чтобы не подтверждать существование бота (кроме
    инвайт-редемпшна через /start, который сам по себе честный ответ по дизайну)."""
    from telegram.ext import ApplicationHandlerStop

    chat = update.effective_chat
    if chat is None:
        return  # системные апдейты без chat -- пропускаем, не наш случай
    chat_id = chat.id
    role = get_role(chat_id)

    # callback_query (inline-кнопки) -- разрешено любой известной роли (TRIAL+), это
    # навигация внутри уже показанного меню, не новая точка входа в команду.
    if update.callback_query is not None:
        if role_allows(role, ROLE_TRIAL):
            return
        raise ApplicationHandlerStop

    cmd = _extract_command(update)
    if cmd is None:
        # свободный текст (не команда) -- тот же принцип, что callback: разрешено
        # уже известной роли (ввод символа для /coin и т.п.), молчаливый отказ NONE.
        if role_allows(role, ROLE_TRIAL):
            return
        raise ApplicationHandlerStop

    if cmd == "start":
        args = context.args or []
        if args:
            # попытка инвайт-редемпшна -- обрабатывается отдельно в cmd_start самим
            # ботом (см. bot.py) -- здесь просто пропускаем дальше, не блокируем.
            return
        if role_allows(role, ROLE_TRIAL):
            return  # уже есть роль -- обычный /start (повторный визит), пропускаем
        raise ApplicationHandlerStop  # NONE без инвайт-кода -- молчаливый отказ

    min_role = COMMAND_ROLE_MAP.get(cmd, DEFAULT_MIN_ROLE)
    if role_allows(role, min_role):
        return
    raise ApplicationHandlerStop


def install(app):
    """Регистрирует enforce() в группе -1 (обрабатывается ДО group=0, где живут все
    обычные хендлеры) -- ApplicationHandlerStop внутри enforce() останавливает
    обработку этого апдейта дальше по всем группам."""
    from telegram.ext import TypeHandler
    from telegram import Update
    app.add_handler(TypeHandler(Update, enforce), group=-1)
