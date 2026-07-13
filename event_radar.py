"""
event_radar.py -- EVENT-RADAR М2 (Пакет 13, 2026-07-13): листинги/делистинги через
официальные announcement-каналы Bybit и Binance. Владелец одобрил после отказа от
платного DropsTab: "Bybit announcements API и Binance CMS API проверены живьём
(200 OK) -- строй на них, без платных зависимостей".

Источники (оба проверены прямым curl 2026-07-13, точные поля -- НЕ пересказ, сырой
JSON смотрен глазами, см. PROGRESS.md):

  - Bybit REST `GET https://api.bybit.com/v5/announcements/index` -- официально
    документирован (bybit-exchange.github.io/docs/v5/announcement), публичный, без
    ключа. Параметры: locale (обязателен), type, limit. `type=new_crypto` --
    листинги (retCode=0, result.list[].title вида "New listing: SKHYUSDT Perpetual
    Contract..."). `type=delistings` (МНОЖЕСТВЕННОЕ число -- НЕ "delisting", первая
    попытка с "delisting" дала пустой список; правильный ключ найден живым поиском
    через articles с tags=["Delistings"], затем подтверждён напрямую: retCode=0,
    total=442, "Delisting of KORUUSDT Perpetual Contract"). Поле для dedup-id --
    url (стабильный, объявление своего ID в ответе не отдаёт).
  - Binance `https://www.binance.com/bapi/composite/v1/public/cms/article/list/query`
    -- ЧЕСТНО НЕ официально документированный REST-эндпоинт (официальная
    developers.binance.com/docs/cms/announcement описывает только WebSocket-топик
    com_announcement_en). Это internal API самого сайта binance.com, неофициально
    используемый сообществом (dev.binance.vision форум "Announcement related API").
    Параметры: type=1, catalogId, pageNo, pageSize. catalogId=48 = "New
    Cryptocurrency Listing" (data.catalogs[0].articles[].title вида "Binance
    Futures Will Launch USDⓈ-Margined SKHYUSDT..."), catalogId=161 = "Delisting"
    ("Notice of Removal of Spot Trading Pairs..."). Каждая статья отдаёт числовой
    id -- используется для dedup. РИСК, честно: нет SLA у internal API, может
    измениться/быть заблокирован без предупреждения -- если сломается, ошибка
    логируется (см. CLAUDE.md "Протокол правды" п.5), данные не выдумываются.

Извлечение символа из заголовка -- ЛУЧШЕЕ УСИЛИЕ (best-effort regex), НЕ 100%-ное:
заголовки вида "Delisting of ARTY,CTA,GTAI,..." или "...SKHYUSDT Perpetual
Contract..." разбираются, но нестандартные форматы (например, "Notice of Removal
of Spot Trading Pairs" без символов в заголовке) честно дают пустой список --
событие всё равно возвращается вызывающей стороне с полным заголовком, просто без
привязки к конкретному тикеру.

Правило алертов (решение владельца 2026-07-13): ЛЮБОЙ делистинг -- алерт владельцу
(объявления бирж редки и всегда достойны внимания, доп. фильтр не нужен). Листинг --
алерт ТОЛЬКО если извлечённый символ входит в watch_symbols (передаётся вызывающей
стороной -- WATCHLIST_ZONES.keys() из bot.py -- во избежание циклического импорта
event_radar<->bot.py, тот же паттерн, что rug_radar.fetch_coingecko_detail(bot_module, ...)).

Персистентность дедупликации: data/event_radar/seen_ids.json -- плоский список уже
виденных "exchange:id" строк. Локальный файл (та же честная оговорка про эфемерность
Railway-файловой системы, что у whale_radar.py -- при передеплое список обнулится,
возможен один повторный алерт по уже виденному объявлению сразу после передеплоя, не
критично для редких событий этого типа). НЕ GitHub-синк -- это не тренировочные
данные (в отличие от shadow_signals.json), а просто защита от повторного алерта на
одно и то же объявление каждый цикл поллинга.
"""

import json
import logging
import os
import re
import time

import requests

log = logging.getLogger(__name__)

BYBIT_ANNOUNCEMENT_URL = "https://api.bybit.com/v5/announcements/index"
BINANCE_ANNOUNCEMENT_URL = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"

BYBIT_TYPE_LISTING = "new_crypto"
BYBIT_TYPE_DELISTING = "delistings"
BINANCE_CATALOG_LISTING = 48
BINANCE_CATALOG_DELISTING = 161

SEEN_IDS_PATH = "data/event_radar/seen_ids.json"
MAX_SEEN_IDS = 2000  # плоский список -- старые записи за пределами разумного окна поллинга не нужны

_QUOTE_SUFFIXES = ("USDT", "USDC", "FDUSD", "TUSD", "BUSD", "BTC", "ETH", "EUR", "TRY", "BRL")


def extract_symbols_from_title(title: str) -> list:
    """Best-effort извлечение тикеров из заголовка объявления биржи. См. докстринг
    модуля -- честно не гарантирует 100% покрытие, на нестандартных заголовках
    возвращает []."""
    if not title:
        return []
    symbols = []
    # список через запятую без суффикса котировки: "Delisting of ARTY,CTA,GTAI,..."
    m = re.search(r'(?:[Oo]f|for)\s+([A-Z0-9]{2,15}(?:\s*,\s*[A-Z0-9]{2,15})+)', title)
    if m:
        symbols.extend(s.strip() for s in m.group(1).split(","))
    # тикер+котировка одним токеном: "SKHYUSDT", "(KORUUSDT)"
    for tok in re.findall(r'\b([A-Z0-9]{3,20})\b', title):
        for suf in _QUOTE_SUFFIXES:
            if tok.endswith(suf) and len(tok) > len(suf):
                symbols.append(tok[:-len(suf)])
                break
    seen = set()
    out = []
    for s in symbols:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def fetch_bybit_announcements(kind: str, limit: int = 20) -> list:
    """kind: "listing" | "delisting". Возвращает нормализованный список событий,
    [] при любой сетевой/API-ошибке (не бросает исключения наружу -- вызывающая
    сторона не должна падать из-за одного недоступного источника)."""
    type_key = BYBIT_TYPE_LISTING if kind == "listing" else BYBIT_TYPE_DELISTING
    try:
        r = requests.get(BYBIT_ANNOUNCEMENT_URL,
                          params={"locale": "en-US", "type": type_key, "limit": limit},
                          timeout=15)
        r.raise_for_status()
        data = r.json()
        if data.get("retCode") != 0:
            log.error(f"event_radar: bybit retCode={data.get('retCode')} retMsg={data.get('retMsg')}")
            return []
        items = data.get("result", {}).get("list", [])
    except Exception as e:
        log.error(f"event_radar: bybit fetch failed kind={kind} ({type(e).__name__}: {e})")
        return []

    out = []
    for it in items:
        title = it.get("title", "")
        url = it.get("url", "")
        ts_ms = it.get("publishTime") or it.get("dateTimestamp") or 0
        out.append({
            "exchange": "bybit",
            "kind": kind,
            "id": f"bybit:{url}",
            "title": title,
            "symbols": extract_symbols_from_title(title),
            "url": url,
            "ts": ts_ms / 1000 if ts_ms else time.time(),
        })
    return out


def fetch_binance_announcements(kind: str, page_size: int = 20) -> list:
    """kind: "listing" | "delisting". Та же fail-soft семантика, что и
    fetch_bybit_announcements -- [] на ошибке, ничего не выдумывает."""
    catalog_id = BINANCE_CATALOG_LISTING if kind == "listing" else BINANCE_CATALOG_DELISTING
    try:
        r = requests.get(BINANCE_ANNOUNCEMENT_URL,
                          params={"type": 1, "catalogId": catalog_id, "pageNo": 1, "pageSize": page_size},
                          timeout=15)
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            log.error(f"event_radar: binance code={data.get('code')} message={data.get('message')}")
            return []
        catalogs = (data.get("data") or {}).get("catalogs") or []
        articles = catalogs[0].get("articles", []) if catalogs else []
    except Exception as e:
        log.error(f"event_radar: binance fetch failed kind={kind} ({type(e).__name__}: {e})")
        return []

    out = []
    for it in articles:
        title = it.get("title", "")
        ts_ms = it.get("releaseDate") or 0
        out.append({
            "exchange": "binance",
            "kind": kind,
            "id": f"binance:{it.get('id')}",
            "title": title,
            "symbols": extract_symbols_from_title(title),
            "url": "",  # bapi не отдаёт публичный URL статьи напрямую -- честно пусто
            "ts": ts_ms / 1000 if ts_ms else time.time(),
        })
    return out


def fetch_all_events(limit: int = 20) -> list:
    """Опрашивает оба источника, оба вида (листинг/делистинг), возвращает
    объединённый список событий. Частичный отказ одного источника не блокирует
    остальные (каждый fetch_* уже fail-soft)."""
    events = []
    events.extend(fetch_bybit_announcements("listing", limit))
    events.extend(fetch_bybit_announcements("delisting", limit))
    events.extend(fetch_binance_announcements("listing", limit))
    events.extend(fetch_binance_announcements("delisting", limit))
    return events


# ── Дедупликация (локальный файл, best-effort) ──────────────────────────────

def _load_seen_ids(path: str = SEEN_IDS_PATH) -> set:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()
    except Exception as e:
        log.error(f"event_radar: _load_seen_ids failed ({type(e).__name__}: {e})")
        return set()


def _save_seen_ids(ids: set, path: str = SEEN_IDS_PATH) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        trimmed = list(ids)[-MAX_SEEN_IDS:]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(trimmed, f)
    except Exception as e:
        log.error(f"event_radar: _save_seen_ids failed ({type(e).__name__}: {e})")


def filter_new_events(events: list, seen_ids: set) -> list:
    """Чистая функция (тестируемая без файлового I/O): события, чьего id ещё нет
    в seen_ids."""
    return [e for e in events if e["id"] not in seen_ids]


# ── Решение "алертить ли" (чистая функция, владелец 2026-07-13) ─────────────

def should_alert(event: dict, watch_symbols: set) -> bool:
    """ЛЮБОЙ делистинг -- алерт. Листинг -- алерт только если пересечение
    extracted symbols с watch_symbols (регистронезависимо -- watch_symbols уже
    ожидается в верхнем регистре, как WATCHLIST_ZONES.keys() в bot.py, но на
    всякий случай сравнение через upper())."""
    if event.get("kind") == "delisting":
        return True
    watch_upper = {s.upper() for s in (watch_symbols or ())}
    return any(sym.upper() in watch_upper for sym in event.get("symbols", []))


def format_event_alert(event: dict) -> str:
    """Текст алерта владельцу для одного события."""
    icon = "🔴" if event["kind"] == "delisting" else "🟢"
    kind_label = "ДЕЛИСТИНГ" if event["kind"] == "delisting" else "ЛИСТИНГ"
    symbols_str = ", ".join(event.get("symbols", [])) or "н/д (не удалось извлечь тикер из заголовка)"
    lines = [
        f"{icon} EVENT-RADAR: {kind_label} -- {event['exchange'].upper()}",
        f"Тикер(ы): {symbols_str}",
        event["title"],
    ]
    if event.get("url"):
        lines.append(event["url"])
    return "\n".join(lines)


def poll_and_get_alerts(watch_symbols: set, limit: int = 20,
                         seen_ids_path: str = SEEN_IDS_PATH) -> list:
    """Главная точка входа для планировщика: опрашивает источники, отфильтровывает
    уже виденные объявления, среди новых отбирает те, что нужно алертить (по
    should_alert), помечает ВСЕ новые (не только заалерченные) как виденные --
    чтобы не алертить листинг не-watch монеты в будущем, если она внезапно
    попадёт в watch (объявление всё равно устареет к тому моменту). Возвращает
    список готовых текстов алертов (format_event_alert)."""
    events = fetch_all_events(limit=limit)
    seen = _load_seen_ids(seen_ids_path)
    new_events = filter_new_events(events, seen)
    if not new_events:
        return []

    alerts = []
    for e in new_events:
        seen.add(e["id"])
        if should_alert(e, watch_symbols):
            alerts.append(format_event_alert(e))
    _save_seen_ids(seen, seen_ids_path)
    return alerts
