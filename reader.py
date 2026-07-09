#!/usr/bin/env python3
"""
BEST TRADE — Telethon Reader v5
- Мониторит 11 каналов трейдеров через Telethon
- Парсит Lookonchain (on-chain данные) через Nitter RSS
- Отправляет сигналы напрямую в Telegram
"""

import asyncio
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

SOURCE_CHANNELS = [
    "https://t.me/+nubqP8HBLLg5Yzhi",   # PIXEL
    "https://t.me/+3pkjT8Jz4xZjMWRi",
    "https://t.me/+qY_uk_VZOMs3YmJi",
    "https://t.me/+nNG-ocI2mVpkMGFi",
    "https://t.me/+aM6NhefyLNc4NjQy",
    "https://t.me/zagovor_likvid",
    "https://t.me/+C318IK2q-jUwZDQy",
    "https://t.me/+3Wy10C_fCzw4ODI6",
    "https://t.me/+IwEnq8xPGtpiNTUy",
    "https://t.me/+4IJ_K5gagNRjMzky",
    "https://t.me/+8lwrSPGYY0VhOTMy",
]

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

def send_telegram(text: str):
    """Отправляет сообщение через бота"""
    if not BOT_TOKEN or not OWNER_CHAT_ID:
        log.error("BOT_TOKEN или OWNER_CHAT_ID не заданы")
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        r = requests.post(url, json={
            "chat_id": OWNER_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }, timeout=10)
        if r.status_code != 200:
            log.error(f"Telegram error: {r.text[:100]}")
    except Exception as e:
        log.error(f"send_telegram: {e}")

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

    return "\n".join(lines)

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
    channel_ids   = []

    for link in SOURCE_CHANNELS:
        try:
            entity = await client.get_entity(link)
            name   = getattr(entity, "title", link.split("/")[-1])
            channel_names[entity.id] = name
            channel_ids.append(entity.id)
            log.info(f"✅ Подключён: {name}")
        except Exception as e:
            log.error(f"❌ {link}: {e}")

    log.info(f"📡 Мониторю {len(channel_ids)} каналов + Lookonchain...")

    # Уведомление о старте
    send_telegram(
        f"✅ *BEST TRADE Reader v5 запущен*\n\n"
        f"📡 Telegram каналов: *{len(channel_ids)}*\n"
        f"🔍 On-chain: *Lookonchain* (каждые 10 мин)\n"
        f"🕐 {datetime.now(TZ).strftime('%d.%m.%Y %H:%M UTC+3')}"
    )

    @client.on(events.NewMessage(chats=channel_ids))
    async def handler(event: events.NewMessage.Event):
        msg: Message = event.message
        text = msg.text or ""
        if len(text) < 15:
            return

        ch_name = channel_names.get(event.chat_id, f"Канал {event.chat_id}")
        formatted = format_signal(text, ch_name)
        send_telegram(formatted)
        log.info(f"📥 {ch_name}: {text[:60]}")

    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
