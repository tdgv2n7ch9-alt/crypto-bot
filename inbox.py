"""
inbox.py -- ПАКЕТ 19, П2 (владелец): антиспам-инбоксы. За флагом
INBOX_MODE_ENABLED (env, default false, тот же паттерн, что MENU_V2_ENABLED
в bot.py) -- владелец включает явно (Railway env var) после приёмки, деплой
этого файла сам по себе НИЧЕГО не меняет в живом поведении.

Идея (владелец, Пакет 19 П2): автосигналы вне жёсткого bypass-списка (⭐
author zone-touch / rug WARN на карточке / AUTO-сигнал с rocket score >=85)
не шлются в чат напрямую -- копятся по разделам (ТОЧКИ/РАДАРЫ/x100),
компактный дайджест раз в MIN_DIGEST_INTERVAL_SEC. Кнопки меню получают
счётчик непрочитанного, вход в раздел сбрасывает его.

Область применения (честно, важно): касается ТОЛЬКО чата владельца
(OWNER_CHAT_ID). На момент написания `data/chat_ids.json` содержит РОВНО
одного активного подписчика -- самого владельца (проверено живьём) -- но
код НЕ полагается на это временное состояние: вызывающая сторона (bot.py)
обязана маршрутизировать через инбокс ТОЛЬКО сообщения адресату
owner_id, любые другие получатели (будущие реальные подписчики) продолжают
получать прямую отправку без изменений -- инбокс/дайджест/счётчики меню
это личная лента ОДНОГО человека (владельца), не общий почтовый ящик на
всех.

journal/inbox.json -- ЛОКАЛЬНЫЙ файл, НЕ синхронизируется на GitHub
(сознательный выбор, не совпадает с паттерном journal/shadow_signals.json/
security_log.json/watch_zones.json, которые все синкаются -- инбокс хранит
эфемерное UI-состояние "непрочитанного", не исторический журнал; переживает
работу процесса, НЕ переживает редеплой -- честное ограничение: элементы,
скопившиеся с последнего дайджеста и ещё не отправленные, теряются при
рестарте контейнера, ровно как переживает любое другое runtime-состояние
в памяти этого проекта).

Формат файла:
{"sections": {"tochki": [{"ts":..., ...}, ...], "radary": [...], "x100": []},
 "unread": {"tochki": N, "radary": N, "x100": N},
 "pending": {"tochki": N, "radary": N, "x100": N},
 "last_digest_ts": float}
"unread" -- держится до захода в раздел (mark_read). "pending" -- новые
элементы с прошлого дайджеста, сбрасывается КАЖДЫМ вызовом дайджеста
независимо от unread (это два разных счётчика намеренно).
"""
import json
import logging
import os
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

log = logging.getLogger(__name__)

INBOX_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "journal", "inbox.json")
CAP_PER_SECTION = 50
MIN_DIGEST_INTERVAL_SEC = 30 * 60
BYPASS_ROCKET_SCORE = 85

INBOX_MODE_ENABLED = os.getenv("INBOX_MODE_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")

# "x100" -- секция заведена по спецификации владельца (пример дайджеста
# "ТОЧКИ +3 · РАДАРЫ +2 · x100 +1"), но честно: x100-сканер сейчас ТОЛЬКО
# ручная команда/кнопка (bot.py CommandHandler("x100", ...), нет
# scheduler.add_job для автоматического скана) -- эта секция структурно
# существует, но никогда не наполнится САМА, пока не появится отдельный
# автоматический x100-скан (вне рамок этого пакета, не выдумываю его здесь).
SECTION_LABELS = {
    "tochki": "🎯 ТОЧКИ",
    "radary": "📡 РАДАРЫ",
    "x100": "🚀 x100",
}
SECTION_CALLBACKS = {
    "tochki": "mv2_tochki",
    "radary": "mv2_radary",
    "x100": "x100_scan",
}


def _empty_state() -> dict:
    return {
        "sections": {s: [] for s in SECTION_LABELS},
        "unread": {s: 0 for s in SECTION_LABELS},
        "pending": {s: 0 for s in SECTION_LABELS},
        "last_digest_ts": 0.0,
    }


def _load() -> dict:
    base = _empty_state()
    if not os.path.exists(INBOX_FILE):
        return base
    try:
        with open(INBOX_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return base
    base["sections"].update({k: v for k, v in (data.get("sections") or {}).items() if k in SECTION_LABELS})
    base["unread"].update({k: v for k, v in (data.get("unread") or {}).items() if k in SECTION_LABELS})
    base["pending"].update({k: v for k, v in (data.get("pending") or {}).items() if k in SECTION_LABELS})
    base["last_digest_ts"] = data.get("last_digest_ts", 0.0)
    return base


def _save(data: dict) -> None:
    """Атомарная запись -- тот же паттерн, что shadow_engine._atomic_write_json()
    (tmp-файл в той же директории + os.replace, не подхватить недописанный
    файл при краше на середине записи)."""
    d = os.path.dirname(INBOX_FILE)
    os.makedirs(d, exist_ok=True)
    tmp_path = os.path.join(d, f".inbox.json.tmp{os.getpid()}")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, INBOX_FILE)


def add_item(section: str, item: dict, now_ts: float = None) -> None:
    """Добавляет элемент в раздел -- cap CAP_PER_SECTION, старые вытесняются
    (тот же идиом append+pop(0), что price_cache в bot.check_pump_dump(),
    единственный существующий прецедент в проекте для bounded recent-list)."""
    if section not in SECTION_LABELS:
        raise ValueError(f"unknown inbox section: {section!r}")
    now = now_ts if now_ts is not None else time.time()
    data = _load()
    entry = dict(item)
    entry["ts"] = now
    items = data["sections"][section]
    items.append(entry)
    while len(items) > CAP_PER_SECTION:
        items.pop(0)
    data["unread"][section] = data["unread"].get(section, 0) + 1
    data["pending"][section] = data["pending"].get(section, 0) + 1
    _save(data)


def get_unread_counts() -> dict:
    return _load()["unread"]


def get_section_items(section: str) -> list:
    return list(_load()["sections"].get(section, []))


def mark_read(section: str) -> None:
    """Вход в раздел = прочитано (владелец, спецификация). НЕ трогает
    pending -- та копится независимо для следующего дайджеста."""
    data = _load()
    if section in data["unread"] and data["unread"][section] != 0:
        data["unread"][section] = 0
        _save(data)


def should_bypass_inbox(rocket_score: int = None, is_author_zone_touch: bool = False,
                          rug_warn: bool = False) -> bool:
    """Три bypass-категории (владелец, дословно): ⭐ ЛИМИТКИ zone-touch
    (author-зоны), rug-алерты WARN по watch_zones, сигналы силой >=85.
    Всё остальное -- в разделы (когда INBOX_MODE_ENABLED)."""
    if is_author_zone_touch or rug_warn:
        return True
    if rocket_score is not None and rocket_score >= BYPASS_ROCKET_SCORE:
        return True
    return False


def pop_pending_digest(now_ts: float = None) -> dict:
    """Возвращает {section: N} -- НОВЫЕ элементы с прошлого дайджеста
    (только секции с N>0), сбрасывает pending и обновляет last_digest_ts.
    Пустой словарь -- копить было нечего, вызывающая сторона не шлёт
    дайджест вообще (см. should_send_digest())."""
    now = now_ts if now_ts is not None else time.time()
    data = _load()
    pending = {s: n for s, n in data["pending"].items() if n > 0}
    for s in data["pending"]:
        data["pending"][s] = 0
    data["last_digest_ts"] = now
    _save(data)
    return pending


def should_send_digest(now_ts: float = None) -> bool:
    """Не чаще раза в MIN_DIGEST_INTERVAL_SEC (владелец) И только если
    реально есть что показать -- дайджест на пустом инбоксе не шлётся."""
    now = now_ts if now_ts is not None else time.time()
    data = _load()
    if not any(n > 0 for n in data["pending"].values()):
        return False
    return (now - data["last_digest_ts"]) >= MIN_DIGEST_INTERVAL_SEC


def format_digest_text(pending_counts: dict) -> str:
    """"📬 Новое: ТОЧКИ +3 · РАДАРЫ +2 · x100 +1" (владелец, дословный
    формат) -- None, если pending_counts пуст (вызывающая сторона не шлёт)."""
    parts = [f"{SECTION_LABELS[s]} +{n}" for s, n in pending_counts.items()
              if n > 0 and s in SECTION_LABELS]
    if not parts:
        return None
    return "📬 Новое: " + " · ".join(parts)


def menu_badge(section: str, base_label: str, counts: dict = None) -> str:
    """"🎯 ТОЧКИ (3)" при unread>0, иначе базовая метка без изменений
    (владелец, спецификация счётчиков в Меню). `counts` -- опционально
    заранее загруженный get_unread_counts(), чтобы не читать файл на
    КАЖДУЮ кнопку меню отдельно (main_kb_v2() строит 6 кнопок разом)."""
    if counts is None:
        counts = get_unread_counts()
    n = counts.get(section, 0)
    return f"{base_label} ({n})" if n > 0 else base_label


def _digest_keyboard(pending_counts: dict) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(SECTION_LABELS[s], callback_data=SECTION_CALLBACKS[s])]
             for s in pending_counts if s in SECTION_LABELS]
    rows.append([InlineKeyboardButton("🏠 Меню", callback_data="show_menu")])
    return InlineKeyboardMarkup(rows)


async def send_inbox_digest(bot, owner_id: int) -> None:
    """Точка входа для scheduler.add_job(..., "interval", minutes=30) -- тот
    же паттерн, что daily_metrics.send_daily_digest/morning_metrics.
    send_morning_digest (регистрируется в bot.py post_init(), переживает
    рестарт нативно). No-op, если INBOX_MODE_ENABLED=false (деплой этого
    кода сам по себе ничего не меняет) или если копить было нечего/рано
    (should_send_digest())."""
    if not INBOX_MODE_ENABLED:
        return
    if not should_send_digest():
        return
    pending = pop_pending_digest()
    text = format_digest_text(pending)
    if not text:
        return
    try:
        await bot.send_message(owner_id, text, reply_markup=_digest_keyboard(pending))
    except Exception as e:
        log.error(f"inbox: send_inbox_digest failed: {e}")
