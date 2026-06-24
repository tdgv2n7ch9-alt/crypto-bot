#!/usr/bin/env python3
"""
BEST TRADE — Telethon Reader v3
Мониторит 10 Telegram каналов трейдеров.
Сохраняет сигналы в /tmp/reader_signals.json для bot.py
"""

import asyncio
import json
import os
import re
import time
import logging
from datetime import datetime
import pytz

from telethon import TelegramClient, events
from telethon.tl.types import Message

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

TZ           = pytz.timezone("Europe/Istanbul")
API_ID       = int(os.getenv("TG_API_ID", "0"))
API_HASH     = os.getenv("TG_API_HASH", "")
SESSION      = "best_trade_reader"
SIGNALS_FILE = "/tmp/reader_signals.json"
MAX_SIGNALS  = 500

# ── 10 каналов трейдеров ──
SOURCE_CHANNELS = [
    "https://t.me/+nubqP8HBLLg5Yzhi",   # PIXEL ✅
    "https://t.me/+3pkjT8Jz4xZjMWRi",   # Канал 2 ✅
    "https://t.me/+qY_uk_VZOMs3YmJi",   # Канал 3 ✅
    "https://t.me/+nNG-ocI2mVpkMGFi",   # Канал 4 ✅
    "https://t.me/+aM6NhefyLNc4NjQy",   # Канал 5 ✅
    "https://t.me/zagovor_likvid",       # Заговор Ликвид ✅
    "https://t.me/+C318IK2q-jUwZDQy",   # Канал 7 ✅
    "https://t.me/+3Wy10C_fCzw4ODI6",   # Канал 8 ✅
    "https://t.me/+IwEnq8xPGtpiNTUy",   # Канал 9 ✅
    "https://t.me/+4IJ_K5gagNRjMzky",   # Канал 10 ✅
    "https://t.me/+8lwrSPGYY0VhOTMy",   # Канал 11 ✅
]

STABLECOINS = {"USDT","USDC","BUSD","DAI","FDUSD","TUSD","USDP"}

def load_signals() -> list:
    try:
        if os.path.exists(SIGNALS_FILE):
            with open(SIGNALS_FILE) as f:
                return json.load(f)
    except: pass
    return []

def save_signals(signals: list):
    try:
        with open(SIGNALS_FILE, "w") as f:
            json.dump(signals[-MAX_SIGNALS:], f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"save_signals: {e}")

def extract_symbol(text: str) -> str | None:
    patterns = [
        r'\$([A-Z]{2,10})',
        r'#([A-Z]{2,10}USDT)',
        r'\b([A-Z]{2,10})USDT\b',
        r'\b([A-Z]{2,10})/USDT\b',
        r'🟢\s*([A-Z]{2,10})',
        r'🔴\s*([A-Z]{2,10})',
        r'LONG\s+([A-Z]{2,10})',
        r'SHORT\s+([A-Z]{2,10})',
        r'([A-Z]{2,10})\s+LONG',
        r'([A-Z]{2,10})\s+SHORT',
    ]
    skip = {"BUY","SELL","LONG","SHORT","STOP","TAKE","PROFIT","LOSS",
            "USD","THE","FOR","AND","NOT","ARE","WAS","HAS","BUT","FROM",
            "USDT","BUSD","TP","SL","RR","ATH","ATL","EMA","RSI","DCA"}
    for p in patterns:
        m = re.search(p, text.upper())
        if m:
            sym = m.group(1).replace("USDT","").strip()
            if sym not in skip and len(sym) >= 2 and sym not in STABLECOINS:
                return sym
    return None

def extract_price(text: str, keywords: list) -> float | None:
    for kw in keywords:
        patterns = [
            rf'{kw}[:\s]*\$?\s*([0-9]+[.,]?[0-9]*)',
            rf'{kw}[:\s]+([0-9]+[.,]?[0-9]*)',
        ]
        for p in patterns:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                try:
                    return float(m.group(1).replace(",", "."))
                except: pass
    return None

def extract_signal(text: str, channel_name: str) -> dict:
    sig = {
        "channel": channel_name,
        "text":    text[:600],
        "summary": "",
        "ts":      time.time(),
        "time":    datetime.now(TZ).strftime("%d.%m %H:%M"),
        "symbol":  None,
        "side":    None,
        "entry":   None,
        "tp1":     None, "tp2": None, "tp3": None,
        "sl":      None,
        "leverage": None,
        "rr":      None,
        "quality": None,  # A+/A/B если канал указал
    }

    upper = text.upper()

    # Направление
    long_kws  = ["LONG","ЛОНГ","BUY","ПОКУПКА","⬆","🟢","📈","ЛОНГУЕМ"]
    short_kws = ["SHORT","ШОРТ","SELL","ПРОДАЖА","⬇","🔴","📉","ШОРТУЕМ"]
    if any(w in upper for w in long_kws):
        sig["side"] = "long"
    elif any(w in upper for w in short_kws):
        sig["side"] = "short"

    sig["symbol"] = extract_symbol(text)

    # Вход
    sig["entry"] = extract_price(text, ["вход","entry","enter","ep","цена","price","zone","зона"])

    # TP
    sig["tp1"] = extract_price(text, ["tp1","тп1","take profit 1","tp 1","цель 1","target 1","t1"])
    sig["tp2"] = extract_price(text, ["tp2","тп2","take profit 2","tp 2","цель 2","target 2","t2"])
    sig["tp3"] = extract_price(text, ["tp3","тп3","take profit 3","tp 3","цель 3","target 3","t3"])

    # Если нет TP1 — ищем просто "tp" или "цель"
    if not sig["tp1"]:
        sig["tp1"] = extract_price(text, ["tp","тп","цель","target","take"])

    # SL
    sig["sl"] = extract_price(text, ["sl","стоп","stop","stop loss","стоп лосс","сл"])

    # Плечо
    lev_m = re.search(r'(\d+)x\s*(?:плечо|leverage|lev)?', text, re.IGNORECASE)
    if lev_m:
        sig["leverage"] = int(lev_m.group(1))

    # R:R
    rr_m = re.search(r'r[:\s]*r[:\s]*1[:\s]*([0-9.]+)', text, re.IGNORECASE)
    if rr_m:
        try: sig["rr"] = float(rr_m.group(1))
        except: pass

    # Краткое резюме для отображения
    parts = []
    if sig["symbol"]: parts.append(sig["symbol"])
    if sig["side"]:   parts.append("🟢 ЛОНГ" if sig["side"]=="long" else "🔴 ШОРТ")
    if sig["entry"]:  parts.append(f"вход {sig['entry']}")
    if sig["tp1"]:    parts.append(f"TP1 {sig['tp1']}")
    if sig["tp2"]:    parts.append(f"TP2 {sig['tp2']}")
    if sig["sl"]:     parts.append(f"SL {sig['sl']}")
    if sig["leverage"]: parts.append(f"{sig['leverage']}x")
    sig["summary"] = "  ·  ".join(parts) if parts else text[:100]

    return sig


async def main():
    if not API_ID or not API_HASH:
        log.error("❌ TG_API_ID или TG_API_HASH не заданы!")
        return

    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.start()
    log.info("✅ Telethon авторизован")

    channel_entities = {}
    channel_names    = {}

    for link in SOURCE_CHANNELS:
        try:
            entity = await client.get_entity(link)
            name   = getattr(entity, "title", link.split("/")[-1])
            channel_entities[entity.id] = entity
            channel_names[entity.id]    = name
            log.info(f"✅ Подключён: {name}")
        except Exception as e:
            log.error(f"❌ {link}: {e}")

    if not channel_entities:
        log.error("❌ Нет доступных каналов")
        return

    log.info(f"📡 Мониторю {len(channel_entities)} каналов...")
    signals = load_signals()

    @client.on(events.NewMessage(chats=list(channel_entities.keys())))
    async def handler(event: events.NewMessage.Event):
        msg: Message = event.message
        text = msg.text or ""
        if len(text) < 10:
            return

        ch_name = channel_names.get(event.chat_id, f"ch_{event.chat_id}")
        sig     = extract_signal(text, ch_name)
        signals.append(sig)
        save_signals(signals)

        sym_info = f" [{sig['symbol']}]" if sig["symbol"] else ""
        log.info(f"📥 {ch_name}{sym_info}: {sig['summary'][:80]}")

    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
