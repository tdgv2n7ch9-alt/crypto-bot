#!/usr/bin/env python3
"""
BEST TRADE — Telethon Reader v5
- Мониторит 11 каналов трейдеров через Telethon
- Парсит Lookonchain (on-chain данные) через Nitter RSS
- Отправляет сигналы напрямую в Telegram
"""

import asyncio
import json
import os
import re
import time
import threading
import logging
from datetime import datetime, timezone
from xml.etree import ElementTree as ET
import pytz
import requests

from telethon import TelegramClient, events
from telethon.tl.types import Message

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

TZ            = pytz.timezone("Europe/Istanbul")
API_ID        = int(os.getenv("TG_API_ID", "0"))
API_HASH      = os.getenv("TG_API_HASH", "")
BOT_TOKEN     = os.getenv("BOT_TOKEN", "")
OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID", "0"))
# Абсолютный путь, привязанный к расположению скрипта — под launchd cwd не гарантирован,
# относительный SESSION приводил к sqlite3.OperationalError: unable to open database file.
SESSION       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "best_trade_reader")

# mode="signal" -- боевое поведение без изменений (format_signal -> send_telegram).
# mode="monitor" -- ТОЛЬКО архив (knowledge/channel_archive/), в сигнальный пайплайн и
# владельцу НЕ уходит. Оба режима архивируются одинаково -- единый формат записи, чтобы
# потом можно было посчитать win-rate по источникам/сделать NLP-анализ по всем каналам
# сразу, не только по боевым.
SOURCE_CHANNELS = [
    {"link": "https://t.me/+nubqP8HBLLg5Yzhi", "mode": "signal"},   # PIXEL
    {"link": "https://t.me/+3pkjT8Jz4xZjMWRi", "mode": "signal"},
    {"link": "https://t.me/+qY_uk_VZOMs3YmJi", "mode": "signal"},
    {"link": "https://t.me/+nNG-ocI2mVpkMGFi", "mode": "signal"},
    {"link": "https://t.me/+aM6NhefyLNc4NjQy", "mode": "signal"},
    {"link": "https://t.me/zagovor_likvid", "mode": "signal"},
    {"link": "https://t.me/+C318IK2q-jUwZDQy", "mode": "signal"},
    {"link": "https://t.me/+3Wy10C_fCzw4ODI6", "mode": "signal"},
    {"link": "https://t.me/+IwEnq8xPGtpiNTUy", "mode": "signal"},
    {"link": "https://t.me/+4IJ_K5gagNRjMzky", "mode": "signal"},
    {"link": "https://t.me/+8lwrSPGYY0VhOTMy", "mode": "signal"},
    {"link": -1001462186786, "mode": "monitor"},   # Королев о Крипте | ТТ
    {"link": -1001700967192, "mode": "monitor"},   # Теория Вероятностей | ТТ
]

ARCHIVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge", "channel_archive")

STABLECOINS = {"USDT","USDC","BUSD","DAI","FDUSD"}

# ═══════════════════════════════════════════
# LOOKONCHAIN — On-chain мониторинг
# ═══════════════════════════════════════════

# Nitter инстансы (fallback список)
NITTER_INSTANCES = [
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.1d4.us",
    "https://nitter.kavin.rocks",
]

LOOKONCHAIN_ACCOUNTS = [
    "lookonchain",   # главный on-chain аналитик
]

# Храним уже отправленные посты чтобы не дублировать
_seen_posts: set = set()

# Ключевые слова для фильтрации важных on-chain событий
ONCHAIN_KEYWORDS = [
    "whale", "кит", "blackrock", "grayscale", "etf",
    "btc", "eth", "hype", "sol", "накаплива", "продал",
    "вывел", "купил", "transfer", "deposit", "withdraw",
    "million", "млн", "$", "billion", "млрд",
    "netflow", "inflow", "outflow",
]

def is_important_onchain(text: str) -> bool:
    """Фильтр — только важные on-chain события"""
    text_lower = text.lower()
    # Должно содержать хотя бы 2 ключевых слова
    hits = sum(1 for kw in ONCHAIN_KEYWORDS if kw in text_lower)
    return hits >= 2

def fetch_nitter_rss(account: str) -> list:
    """Парсит RSS через nitter. Возвращает список постов."""
    posts = []
    for instance in NITTER_INSTANCES:
        try:
            url = f"{instance}/{account}/rss"
            r = requests.get(url, timeout=10, headers={
                "User-Agent": "Mozilla/5.0 (compatible; BEST-TRADE-BOT/1.0)"
            })
            if r.status_code != 200:
                continue

            root = ET.fromstring(r.text)
            channel = root.find("channel")
            if channel is None:
                continue

            items = channel.findall("item")
            for item in items[:10]:  # последние 10 постов
                title = item.findtext("title", "")
                desc  = item.findtext("description", "")
                link  = item.findtext("link", "")
                pub   = item.findtext("pubDate", "")

                # Очищаем HTML теги
                text = re.sub(r'<[^>]+>', ' ', desc or title)
                text = re.sub(r'\s+', ' ', text).strip()

                if text and link:
                    posts.append({
                        "text": text,
                        "link": link,
                        "pub":  pub,
                        "id":   link,
                    })

            if posts:
                log.info(f"✅ Nitter {instance}: {len(posts)} постов от @{account}")
                return posts

        except Exception as e:
            log.debug(f"Nitter {instance} ошибка: {e}")
            continue

    return posts

def format_onchain_alert(post: dict, account: str) -> str:
    """Форматирует on-chain алерт для Telegram"""
    text = post["text"]
    link = post["link"]

    # Извлекаем монеты из текста
    coins = re.findall(r'\$([A-Z]{2,10})', text)
    coins_str = " ".join(f"`${c}`" for c in set(coins)) if coins else ""

    # Извлекаем суммы в долларах
    amounts = re.findall(r'\$[\d,.]+[KMB]?', text)
    amounts_str = " · ".join(amounts[:3]) if amounts else ""

    lines = [
        f"🔍 *Lookonchain (@{account})*",
        "",
    ]

    # Определяем тип события
    text_lower = text.lower()
    if "blackrock" in text_lower:
        lines.append("🏦 *BlackRock движение*")
    elif "whale" in text_lower or "кит" in text_lower:
        lines.append("🐋 *Движение кита*")
    elif "etf" in text_lower:
        lines.append("📊 *ETF активность*")
    elif "накапливают" in text_lower or "accumulate" in text_lower:
        lines.append("💎 *Накопление*")

    if coins_str:
        lines.append(f"💰 Монеты: {coins_str}")
    if amounts_str:
        lines.append(f"💵 Суммы: {amounts_str}")

    lines.append("")
    lines.append(f"_{text[:400]}_")
    lines.append("")
    lines.append(f"🔗 [Источник]({link})")

    return "\n".join(lines)

def check_lookonchain():
    """Проверяет новые посты от Lookonchain. Запускается в отдельном потоке."""
    global _seen_posts
    log.info("🔍 Запуск мониторинга Lookonchain...")

    while True:
        try:
            for account in LOOKONCHAIN_ACCOUNTS:
                posts = fetch_nitter_rss(account)

                for post in posts:
                    post_id = post["id"]
                    if post_id in _seen_posts:
                        continue

                    _seen_posts.add(post_id)
                    text = post["text"]

                    # Фильтруем только важные
                    if not is_important_onchain(text):
                        log.debug(f"Пропуск нерелевантного поста: {text[:60]}")
                        continue

                    # Отправляем алерт
                    alert = format_onchain_alert(post, account)
                    send_telegram(alert)
                    log.info(f"📊 OnChain алерт: {text[:80]}")

                    time.sleep(1)  # небольшая пауза между сообщениями

        except Exception as e:
            log.error(f"check_lookonchain: {e}")

        # Проверяем каждые 10 минут
        time.sleep(600)

# ═══════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════
# Ночная сессия #2, Блок 3 ("Фаза А"): ретраи + дедуп. Честно: в текущей архитектуре
# reader.py (проверено -- AUDIT.md §"Между процессами", DOCUMENTATION.md §5) НЕТ
# файлового моста -- send_telegram() уже отправляет напрямую через Bot API, отдельный
# от bot.py процесс на Mac mini (не Railway). "Файловый мост /tmp" из формулировки
# задачи не найден в коде -- не выдумываю то, чего нет, здесь усиливается
# УЖЕ существующий прямой Bot API путь: ретраи с уважением Telegram 429/retry_after,
# персистентный дедуп по (channel_id, message_id), тестовый режим с явной пометкой.
# reader.py архитектурно НЕ имеет доступа к списку подписчиков (нет импорта
# subscribers.py, нет цикла по chat_id кроме OWNER_CHAT_ID) -- живая рассылка
# подписчикам физически невозможна из этого файла, не только "выключена флагом".

READER_TEST_MODE = os.getenv("READER_TEST_MODE", "1") not in ("0", "false", "False", "")
SEND_MAX_RETRIES = int(os.getenv("READER_SEND_MAX_RETRIES", "3"))
SEND_RETRY_BACKOFF_SEC = float(os.getenv("READER_SEND_RETRY_BACKOFF_SEC", "2"))


def send_telegram(text: str) -> bool:
    """Отправляет сообщение владельцу через Bot API с ретраями. Уважает Telegram 429
    (retry_after из ответа), ретраит 5xx и сетевые исключения с линейным backoff,
    НЕ ретраит прочие 4xx (клиентская ошибка -- повтор не поможет). Возвращает
    True/False (раньше ничего не возвращала -- вызывающий код это поле не использовал,
    аддитивное изменение сигнатуры, все текущие вызовы `send_telegram(x)` совместимы)."""
    if not BOT_TOKEN or not OWNER_CHAT_ID:
        log.error("BOT_TOKEN или OWNER_CHAT_ID не заданы")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for attempt in range(1, SEND_MAX_RETRIES + 1):
        try:
            r = requests.post(url, json={
                "chat_id": OWNER_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }, timeout=10)
            if r.status_code == 200:
                return True
            if r.status_code == 429:
                try:
                    retry_after = r.json().get("parameters", {}).get("retry_after", SEND_RETRY_BACKOFF_SEC)
                except Exception:
                    retry_after = SEND_RETRY_BACKOFF_SEC
                log.warning(f"Telegram 429, жду {retry_after}s (попытка {attempt}/{SEND_MAX_RETRIES})")
                time.sleep(retry_after)
                continue
            if 500 <= r.status_code < 600:
                log.warning(f"Telegram {r.status_code}, ретрай (попытка {attempt}/{SEND_MAX_RETRIES})")
                time.sleep(SEND_RETRY_BACKOFF_SEC * attempt)
                continue
            log.error(f"Telegram error {r.status_code}: {r.text[:200]}")
            return False
        except Exception as e:
            log.error(f"send_telegram попытка {attempt}/{SEND_MAX_RETRIES}: {e}")
            time.sleep(SEND_RETRY_BACKOFF_SEC * attempt)
    log.error(f"send_telegram: все {SEND_MAX_RETRIES} попыток исчерпаны, сообщение потеряно")
    return False


# --- Дедупликация сигналов канала (переживает рестарт launchd, в отличие от
# _seen_posts у Lookonchain, который только in-memory) ---------------------------
from collections import deque

DEDUP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reader_dedup_seen.json")
DEDUP_MAX_KEEP = 2000

_seen_order = deque(maxlen=DEDUP_MAX_KEEP)
_seen_set: set = set()


def _load_dedup():
    global _seen_order, _seen_set
    try:
        if os.path.exists(DEDUP_FILE):
            with open(DEDUP_FILE) as f:
                items = [tuple(x) for x in json.load(f)]
            _seen_order = deque(items, maxlen=DEDUP_MAX_KEEP)
            _seen_set = set(items)
            log.info(f"Дедуп: загружено {len(_seen_set)} ранее виденных сообщений")
    except Exception as e:
        log.error(f"_load_dedup: {e}")


def _save_dedup():
    try:
        with open(DEDUP_FILE, "w") as f:
            json.dump(list(_seen_order), f)
    except Exception as e:
        log.error(f"_save_dedup: {e}")


def _mark_seen(key) -> bool:
    """True, если сообщение УЖЕ было обработано (дубликат, пропустить). Иначе
    регистрирует ключ и возвращает False. Ограниченный размер (deque maxlen) --
    вытесняет самые старые ключи, не растёт бесконечно."""
    if key in _seen_set:
        return True
    if len(_seen_order) == _seen_order.maxlen:
        old = _seen_order.popleft()
        _seen_set.discard(old)
    _seen_order.append(key)
    _seen_set.add(key)
    _save_dedup()
    return False

# ═══════════════════════════════════════════
# ПАРСИНГ СИГНАЛОВ
# ═══════════════════════════════════════════

def extract_symbol(text: str):
    patterns = [
        r'\$([A-Z]{2,10})',
        r'\b([A-Z]{2,10})USDT\b',
        r'\b([A-Z]{2,10})/USDT\b',
        r'🟢\s*([A-Z]{2,10})',
        r'🔴\s*([A-Z]{2,10})',
        r'LONG\s+([A-Z]{2,10})',
        r'SHORT\s+([A-Z]{2,10})',
    ]
    skip = {"BUY","SELL","LONG","SHORT","STOP","TAKE","PROFIT","LOSS","USD","TP","SL"}
    for p in patterns:
        m = re.search(p, text.upper())
        if m:
            sym = m.group(1).replace("USDT","").strip()
            if sym not in skip and len(sym) >= 2 and sym not in STABLECOINS:
                return sym
    return None

def extract_price(text: str, keywords: list):
    for kw in keywords:
        m = re.search(rf'{kw}[:\s]*\$?\s*([0-9]+[.,]?[0-9]*)', text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1).replace(",", "."))
            except: pass
    return None

def format_signal(text: str, channel: str) -> str:
    """Форматирует сигнал для отправки"""
    upper = text.upper()
    sym = extract_symbol(text)

    side = None
    if any(w in upper for w in ["LONG","ЛОНГ","BUY","🟢"]):
        side = "🟢 ЛОНГ"
    elif any(w in upper for w in ["SHORT","ШОРТ","SELL","🔴"]):
        side = "🔴 ШОРТ"

    entry = extract_price(text, ["вход","entry","ep","цена"])
    tp1   = extract_price(text, ["tp1","тп1","t1"])
    tp2   = extract_price(text, ["tp2","тп2","t2"])
    sl    = extract_price(text, ["sl","стоп","stop"])

    lines = [f"📡 *{channel}*", ""]

    if sym and side:
        lines.append(f"*{sym}USDT* {side}")
        if entry: lines.append(f"💵 Вход: `{entry}`")
        if tp1:   lines.append(f"🎯 TP1: `{tp1}`")
        if tp2:   lines.append(f"🎯 TP2: `{tp2}`")
        if sl:    lines.append(f"🔴 SL: `{sl}`")
        lines.append("")

    # Оригинальный текст (первые 300 символов)
    lines.append(f"_{text[:300]}_")

    formatted = "\n".join(lines)
    if READER_TEST_MODE:
        formatted = "🧪 *[READER TEST]*\n" + formatted
    return formatted

# ═══════════════════════════════════════════
# АРХИВ КАНАЛОВ (все режимы, signal и monitor)
# ═══════════════════════════════════════════

def _channel_slug(name: str) -> str:
    slug = re.sub(r"[^\w\- а-яА-ЯёЁ]", "_", name).strip().replace(" ", "_")
    return slug[:60] or "unknown"

def archive_message(channel_id: int, channel_name: str, mode: str, text: str, has_media: bool):
    """Единый формат записи для signal и monitor каналов -- один .jsonl на канал в месяц
    (ротация по месяцу в имени файла, не растёт бесконечно один файл). Не бросает
    исключений наружу -- сбой архивации не должен ронять сигнальный пайплайн."""
    try:
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        now_utc = datetime.now(timezone.utc)
        fname = f"{_channel_slug(channel_name)}_{now_utc.strftime('%Y-%m')}.jsonl"
        path = os.path.join(ARCHIVE_DIR, fname)
        record = {
            "ts_utc": now_utc.isoformat(),
            "channel_id": channel_id,
            "channel_name": channel_name,
            "mode": mode,
            "text": text,
            "has_media": has_media,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        log.error(f"archive_message: {channel_name}: {e}")

# ═══════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════

async def main():
    if not API_ID or not API_HASH:
        log.error("❌ TG_API_ID или TG_API_HASH не заданы!")
        return
    if not BOT_TOKEN:
        log.error("❌ BOT_TOKEN не задан!")
        return
    if not OWNER_CHAT_ID:
        log.error("❌ OWNER_CHAT_ID не задан!")
        return

    _load_dedup()

    # Запускаем Lookonchain мониторинг в фоновом потоке
    onchain_thread = threading.Thread(
        target=check_lookonchain,
        daemon=True,
        name="LookonchainMonitor"
    )
    onchain_thread.start()
    log.info("🔍 Lookonchain монитор запущен (каждые 10 мин)")

    # Запускаем Telethon
    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.start()
    log.info("✅ Telethon авторизован")

    channel_names = {}
    channel_modes = {}
    channel_ids   = []

    for item in SOURCE_CHANNELS:
        link, mode = item["link"], item["mode"]
        try:
            entity = await client.get_entity(link)
            name   = getattr(entity, "title", None) or (str(link).split("/")[-1] if isinstance(link, str) else str(link))
            channel_names[entity.id] = name
            channel_modes[entity.id] = mode
            channel_ids.append(entity.id)
            log.info(f"✅ Подключён ({mode}): {name}")
        except Exception as e:
            log.error(f"❌ {link}: {e}")

    n_signal = sum(1 for m in channel_modes.values() if m == "signal")
    n_monitor = sum(1 for m in channel_modes.values() if m == "monitor")
    log.info(f"📡 Мониторю {len(channel_ids)} каналов ({n_signal} signal, {n_monitor} monitor) + Lookonchain...")

    # Уведомление о старте
    send_telegram(
        f"✅ *BEST TRADE Reader v5 запущен*\n\n"
        f"📡 Telegram каналов: *{len(channel_ids)}* ({n_signal} боевых, {n_monitor} monitor-only)\n"
        f"🔍 On-chain: *Lookonchain* (каждые 10 мин)\n"
        f"🕐 {datetime.now(TZ).strftime('%d.%m.%Y %H:%M UTC+3')}"
    )

    @client.on(events.NewMessage(chats=channel_ids))
    async def handler(event: events.NewMessage.Event):
        msg: Message = event.message
        text = msg.text or ""
        ch_name = channel_names.get(event.chat_id, f"Канал {event.chat_id}")
        mode = channel_modes.get(event.chat_id, "signal")

        # Архив -- ОБА режима, каждое сообщение (даже медиа без текста), единый формат.
        archive_message(event.chat_id, ch_name, mode, text, bool(msg.media))

        if mode == "monitor":
            log.info(f"🗄 [monitor] {ch_name}: {text[:60]}")
            return  # в сигнальный пайплайн и владельцу НЕ уходит

        if len(text) < 15:
            return

        # Дедуп по (канал, message_id) -- переживает рестарт launchd, см. _mark_seen().
        # Помечаем ДО отправки: повторная доставка того же апдейта Telethon-ом при
        # реконнекте не должна создавать повторную попытку отправки; надёжность самой
        # отправки обеспечивают ретраи внутри send_telegram(), не повторный вызов извне.
        dedup_key = (event.chat_id, msg.id)
        if _mark_seen(dedup_key):
            log.info(f"🔁 [dedup] {ch_name}: message_id={msg.id} уже обработан, пропуск")
            return

        formatted = format_signal(text, ch_name)
        ok = send_telegram(formatted)
        log.info(f"📥 {ch_name}: {text[:60]} (sent={ok})")

    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
