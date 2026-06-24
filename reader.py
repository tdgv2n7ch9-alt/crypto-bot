#!/usr/bin/env python3
"""
BEST TRADE — Telethon Reader v2
Мониторит внешние Telegram каналы трейдеров.
Сохраняет сигналы в /tmp/reader_signals.json для bot.py
"""

import asyncio
import json
import os
import re
import time
import logging
from datetime import datetime, timezone
import pytz

from telethon import TelegramClient, events
from telethon.tl.types import Message

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

TZ = pytz.timezone("Europe/Istanbul")
API_ID   = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")
SESSION  = "best_trade_reader"
SIGNALS_FILE = "/tmp/reader_signals.json"
MAX_SIGNALS  = 200  # хранить последние N сигналов

# ── Каналы для мониторинга ──
# Добавляй ссылки по мере получения
SOURCE_CHANNELS = [
    "https://t.me/+nubqP8HBLLg5Yzhi",  # PIXEL
    # Остальные 9 — добавим когда получим ссылки:
    # "https://t.me/...",  # Теория Вероятностей
    # "https://t.me/...",  # Королев о крипте
    # "https://t.me/...",  # Скальпинг блог Адель
    # "https://t.me/...",  # Биржевой спекулянт
    # "https://t.me/...",  # Мысли Эмилии
    # "https://t.me/...",  # Kita ICT
    # "https://t.me/...",  # Лудомания
    # "https://t.me/...",  # Джо
    # "https://t.me/...",  # Андрей crypto
]

def load_signals() -> list:
    try:
        if os.path.exists(SIGNALS_FILE):
            with open(SIGNALS_FILE) as f:
                return json.load(f)
    except:
        pass
    return []

def save_signals(signals: list):
    try:
        # Оставляем только последние MAX_SIGNALS
        signals = signals[-MAX_SIGNALS:]
        with open(SIGNALS_FILE, "w") as f:
            json.dump(signals, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"save_signals: {e}")

def extract_symbol(text: str) -> str | None:
    """Ищет символ монеты в тексте"""
    # Паттерны: $BTC, #BTC, BTCUSDT, BTC/USDT, BTC USDT
    patterns = [
        r'\$([A-Z]{2,10})',
        r'#([A-Z]{2,10})',
        r'\b([A-Z]{2,10})USDT\b',
        r'\b([A-Z]{2,10})/USDT\b',
        r'\b([A-Z]{3,8})\b(?=\s*[\-:\/]\s*(?:USDT|USD|usdt))',
    ]
    for p in patterns:
        m = re.search(p, text.upper())
        if m:
            sym = m.group(1)
            # Исключаем не-монеты
            skip = {"BUY","SELL","LONG","SHORT","STOP","TAKE","PROFIT","LOSS",
                    "USD","THE","FOR","AND","NOT","ARE","WAS","HAS","BUT"}
            if sym not in skip and len(sym) >= 2:
                return sym
    return None

def extract_price(text: str, keyword: str) -> float | None:
    """Ищет цену после ключевого слова"""
    patterns = [
        rf'{keyword}[:\s]*\$?([0-9]+\.?[0-9]*)',
        rf'{keyword}[:\s]*([0-9]+\.?[0-9]*)',
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except:
                pass
    return None

def extract_signal_data(text: str, channel_name: str) -> dict:
    """Парсит текст сообщения — ищет сигнал"""
    data = {
        "channel": channel_name,
        "text":    text[:500],
        "summary": "",
        "ts":      time.time(),
        "time":    datetime.now(TZ).strftime("%d.%m %H:%M"),
        "symbol":  None,
        "side":    None,
        "entry":   None,
        "tp1":     None,
        "tp2":     None,
        "tp3":     None,
        "sl":      None,
    }

    upper = text.upper()

    # Определяем направление
    if any(w in upper for w in ["LONG", "ЛОНГ", "BUY", "ПОКУПКА", "↑", "🟢"]):
        data["side"] = "long"
    elif any(w in upper for w in ["SHORT", "ШОРТ", "SELL", "ПРОДАЖА", "↓", "🔴"]):
        data["side"] = "short"

    # Ищем символ
    data["symbol"] = extract_symbol(text)

    # Ищем цены
    for kw in ["вход", "entry", "enter", "цена", "price", "ep"]:
        v = extract_price(text, kw)
        if v:
            data["entry"] = v
            break

    for kw in ["tp1", "тп1", "take profit 1", "цель 1", "target 1"]:
        v = extract_price(text, kw)
        if v:
            data["tp1"] = v
            break

    for kw in ["tp2", "тп2", "take profit 2", "цель 2", "target 2"]:
        v = extract_price(text, kw)
        if v:
            data["tp2"] = v
            break

    for kw in ["tp3", "тп3", "take profit 3", "цель 3", "target 3"]:
        v = extract_price(text, kw)
        if v:
            data["tp3"] = v
            break

    for kw in ["sl", "стоп", "stop", "stop loss", "стоп лосс"]:
        v = extract_price(text, kw)
        if v:
            data["sl"] = v
            break

    # Краткое резюме
    sym_str  = data["symbol"] or "?"
    side_str = {"long": "🟢 ЛОНГ", "short": "🔴 ШОРТ"}.get(data["side"] or "", "💡")
    entry_str = f"вход {data['entry']}" if data["entry"] else ""
    tp_str    = f"TP {data['tp1']}" if data["tp1"] else ""
    sl_str    = f"SL {data['sl']}" if data["sl"] else ""
    parts = [p for p in [sym_str, side_str, entry_str, tp_str, sl_str] if p]
    data["summary"] = "  ·  ".join(parts) if parts else text[:100]

    return data


async def main():
    if not API_ID or not API_HASH:
        log.error("❌ TG_API_ID или TG_API_HASH не заданы!")
        return

    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.start()
    log.info("✅ Telethon авторизован")

    # Получаем entity для каждого канала
    channel_entities = {}
    channel_names = {}
    for link in SOURCE_CHANNELS:
        try:
            entity = await client.get_entity(link)
            name   = getattr(entity, "title", link.split("/")[-1])
            channel_entities[entity.id] = entity
            channel_names[entity.id]    = name
            log.info(f"✅ Подключён: {name} (id={entity.id})")
        except Exception as e:
            log.error(f"❌ Не могу подключиться к {link}: {e}")

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
            return  # игнорируем очень короткие

        chat_id   = event.chat_id
        ch_name   = channel_names.get(chat_id, f"channel_{chat_id}")

        sig = extract_signal_data(text, ch_name)
        signals.append(sig)
        save_signals(signals)

        sym_info = f" [{sig['symbol']}]" if sig["symbol"] else ""
        log.info(f"📥 {ch_name}{sym_info}: {sig['summary'][:80]}")

    log.info("🔄 Слушаю новые сообщения...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
