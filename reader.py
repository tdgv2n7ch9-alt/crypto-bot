#!/usr/bin/env python3
"""
BEST TRADE — Telethon Reader v4
Мониторит каналы трейдеров и отправляет сигналы
напрямую в Telegram через бота (без файлов).
"""

import asyncio
import os
import re
import time
import logging
from datetime import datetime
import pytz
import requests

from telethon import TelegramClient, events
from telethon.tl.types import Message

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

TZ       = pytz.timezone("Europe/Istanbul")
API_ID   = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
# ID чата куда отправлять — твой личный chat_id
# Получи его написав /start боту и посмотрев chat_ids.txt
# Или используй переменную окружения
OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID", "0"))
SESSION  = "best_trade_reader"

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

def send_telegram(text: str):
    """Отправляет сообщение через бота"""
    if not BOT_TOKEN or not OWNER_CHAT_ID:
        log.error("BOT_TOKEN или OWNER_CHAT_ID не заданы")
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": OWNER_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }, timeout=10)
    except Exception as e:
        log.error(f"send_telegram: {e}")

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


async def main():
    if not API_ID or not API_HASH:
        log.error("❌ TG_API_ID или TG_API_HASH не заданы!")
        return

    if not BOT_TOKEN:
        log.error("❌ BOT_TOKEN не задан!")
        return

    if not OWNER_CHAT_ID:
        log.error("❌ OWNER_CHAT_ID не задан! Укажи свой Telegram chat_id")
        return

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

    log.info(f"📡 Мониторю {len(channel_ids)} каналов...")

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
