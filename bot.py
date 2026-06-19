#!/usr/bin/env python3
"""
🤖 Crypto Monitor Bot — Telegram бот для мониторинга крипторынка
Рассылка 2 раза в день: 11:00 и 23:00 МСК
"""

import asyncio
import logging
import os
import requests
from datetime import datetime
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

# ============================================================
# НАСТРОЙКИ — ВСТАВЬ СВОИ ДАННЫЕ
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8840157382:AAG5QtS381RwZ5LpxkIoxPepSIfI9zwANMA")
CHAT_ID   = None                  # заполнится автоматически при /start

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

# ============================================================
# НАШИ МОНЕТЫ — ЗОНЫ ВХОДА ИЗ ЖУРНАЛА
# ============================================================
MY_COINS = {
    "BEATUSDT":   {"name":"BEAT",   "long":[1.00,1.10], "stop":0.90,  "tp":[2.00,2.40,3.50], "signal":"🟢 ЛОНГ"},
    "RIVERUSDT":  {"name":"RIVER",  "long":[4.00,4.20], "stop":3.80,  "tp":[5.00,5.50,6.00], "signal":"⏳ ЖДАТЬ"},
    "PLAYUSDT":   {"name":"PLAY",   "long":[0.030,0.035],"stop":0.027,"tp":[0.054,0.068,0.080],"signal":"⏳ ЖДАТЬ"},
    "LABUSDT":    {"name":"LAB",    "long":[15.0,15.8],  "stop":14.2,  "tp":[19,22,24],        "signal":"⚠️ ПЕРЕКУПЛЕН"},
    "ATOMUSDT":   {"name":"ATOM",   "long":[1.720,1.750],"stop":1.65, "tp":[1.912,2.10,2.50], "signal":"⏳ ЖДАТЬ"},
    "SKYAIUSDT":  {"name":"SKYAI",  "short":[0.353,0.375],"stop":0.395,"tp":[0.322],           "signal":"🔴 ШОРТ"},
    "BSBUSDT":    {"name":"BSB",    "long":[0.487,0.510],"stop":0.400,"tp":[0.620,0.677,0.750],"signal":"⏳ ЖДАТЬ"},
    "AVAXUSDT":   {"name":"AVAX",   "short":[6.30,6.45], "stop":5.50,  "tp":[6.80,7.50,8.50],  "signal":"🔴 ШОРТ"},
    "ASTERUSDT":  {"name":"ASTER",  "short":[0.69,0.70], "stop":0.730, "tp":[0.61,0.58],       "signal":"🔴 ШОРТ"},
    "POWERUSDT":  {"name":"POWER",  "long":[0.065,0.072],"stop":0.060,"tp":[0.085,0.095,0.105],"signal":"⚠️ ОСТОРОЖНО"},
}

# ============================================================
# BINANCE API
# ============================================================
def get_prices(symbols: list) -> dict:
    """Получить текущие цены и изменение 24ч с Binance"""
    try:
        url = "https://api.binance.com/api/v3/ticker/24hr"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        tickers = resp.json()
        result = {}
        for t in tickers:
            if t["symbol"] in symbols:
                result[t["symbol"]] = {
                    "price":  float(t["lastPrice"]),
                    "change": float(t["priceChangePercent"]),
                    "volume": float(t["quoteVolume"]),
                    "high":   float(t["highPrice"]),
                    "low":    float(t["lowPrice"]),
                }
        return result
    except Exception as e:
        log.error(f"Binance API error: {e}")
        return {}

def get_btc_dominance() -> str:
    """BTC цена для контекста"""
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=5)
        btc = float(r.json()["price"])
        return f"${btc:,.0f}"
    except:
        return "—"

# ============================================================
# ФОРМАТИРОВАНИЕ
# ============================================================
def fmt_price(p: float) -> str:
    if p >= 1000: return f"${p:,.2f}"
    if p >= 1:    return f"${p:.4f}"
    if p >= 0.01: return f"${p:.5f}"
    return f"${p:.8f}"

def fmt_change(ch: float) -> str:
    arrow = "🟢▲" if ch >= 0 else "🔴▼"
    return f"{arrow} {abs(ch):.2f}%"

def check_zone(price: float, coin_cfg: dict) -> str:
    """Проверить попадание цены в зону входа"""
    if "long" in coin_cfg:
        lo, hi = coin_cfg["long"]
        if lo * 0.98 <= price <= hi * 1.02:
            return "🔥 ЦЕНА В ЗОНЕ ЛОНГА!"
        if price < lo:
            dist = ((lo - price) / price) * 100
            return f"📉 До зоны лонга: -{dist:.1f}%"
        if price > hi:
            dist = ((price - hi) / hi) * 100
            return f"📈 Выше зоны лонга на +{dist:.1f}%"
    if "short" in coin_cfg:
        lo, hi = coin_cfg["short"]
        if lo * 0.98 <= price <= hi * 1.02:
            return "🔥 ЦЕНА В ЗОНЕ ШОРТА!"
        if price > hi:
            dist = ((price - hi) / hi) * 100
            return f"📈 До зоны шорта: +{dist:.1f}%"
        if price < lo:
            dist = ((lo - price) / price) * 100
            return f"📉 Ниже зоны шорта на -{dist:.1f}%"
    return ""

# ============================================================
# ФОРМИРОВАНИЕ СООБЩЕНИЙ
# ============================================================
def build_report(session: str) -> str:
    """Построить отчёт по всем монетам"""
    now = datetime.now(MOSCOW_TZ)
    symbols = list(MY_COINS.keys())
    prices = get_prices(symbols)
    btc = get_btc_dominance()

    lines = []
    lines.append(f"{'🌅' if 'Утро' in session else '🌙'} *{session}* | {now.strftime('%d.%m.%Y %H:%M')} МСК")
    lines.append(f"₿ BTC: *{btc}*")
    lines.append("━" * 28)

    # Сначала горячие — в зоне входа
    hot = []
    normal = []

    for sym, cfg in MY_COINS.items():
        t = prices.get(sym)
        if not t:
            continue
        price = t["price"]
        zone_status = check_zone(price, cfg)
        if "В ЗОНЕ" in zone_status:
            hot.append((sym, cfg, t, zone_status))
        else:
            normal.append((sym, cfg, t, zone_status))

    # 🔥 ГОРЯЧИЕ
    if hot:
        lines.append("🔥 *В ЗОНЕ ВХОДА — ДЕЙСТВОВАТЬ!*")
        for sym, cfg, t, zone in hot:
            ch = t["change"]
            lines.append(
                f"\n⚡ *{cfg['name']}*\n"
                f"   Цена: *{fmt_price(t['price'])}* {fmt_change(ch)}\n"
                f"   {zone}\n"
                f"   Сигнал: {cfg['signal']}\n"
                f"   🛑 Стоп: {fmt_price(cfg['stop'])}"
            )
        lines.append("━" * 28)

    # 📊 ОСТАЛЬНЫЕ
    lines.append("📊 *Все монеты:*\n")
    for sym, cfg, t, zone in normal:
        ch = t["change"]
        tp_str = " / ".join(fmt_price(x) for x in cfg.get("tp", []))
        entry_key = "long" if "long" in cfg else "short"
        entry = cfg[entry_key]
        entry_str = f"{fmt_price(entry[0])}–{fmt_price(entry[1])}"

        lines.append(
            f"*{cfg['name']}* — {fmt_price(t['price'])} {fmt_change(ch)}\n"
            f"  {cfg['signal']} | Вход: {entry_str}\n"
            f"  {zone}\n"
        )

    lines.append("━" * 28)
    lines.append("_Данные: Binance • Обновление через 12ч_")
    return "\n".join(lines)

def build_coin_detail(symbol: str) -> str:
    """Детальная карточка монеты"""
    cfg = MY_COINS.get(symbol)
    if not cfg:
        return "❌ Монета не найдена"

    prices = get_prices([symbol])
    t = prices.get(symbol)
    if not t:
        return f"⚠️ Нет данных для {symbol}"

    price = t["price"]
    ch = t["change"]
    zone = check_zone(price, cfg)
    entry_key = "long" if "long" in cfg else "short"
    entry = cfg[entry_key]
    tp_str = "\n".join(f"  ТП{i+1}: {fmt_price(x)}" for i, x in enumerate(cfg.get("tp", [])))

    bar_pct = int(((price - t["low"]) / max(t["high"] - t["low"], 0.000001)) * 10)
    bar = "█" * bar_pct + "░" * (10 - bar_pct)

    return (
        f"📈 *{cfg['name']} ({symbol})*\n\n"
        f"💰 Цена: *{fmt_price(price)}*\n"
        f"   {fmt_change(ch)} за 24ч\n\n"
        f"📊 24ч диапазон:\n"
        f"   [{bar}]\n"
        f"   Мин: {fmt_price(t['low'])} | Макс: {fmt_price(t['high'])}\n\n"
        f"📍 Зона {'лонга' if entry_key=='long' else 'шорта'}: "
        f"*{fmt_price(entry[0])} — {fmt_price(entry[1])}*\n"
        f"🛑 Стоп-лосс: *{fmt_price(cfg['stop'])}*\n"
        f"🎯 Тейк-профит:\n{tp_str}\n\n"
        f"📡 Статус: {zone if zone else cfg['signal']}\n"
        f"💹 Объём 24ч: ${t['volume']:,.0f}\n\n"
        f"🔗 [TradingView](https://www.tradingview.com/chart/?symbol=BINANCE:{symbol})"
    )

# ============================================================
# TELEGRAM HANDLERS
# ============================================================
user_chat_ids = set()

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_chat_ids.add(chat_id)
    # Сохранить chat_id в файл для автоматической рассылки
    with open("chat_ids.txt", "a") as f:
        f.write(f"{chat_id}\n")

    kb = [
        [InlineKeyboardButton("📊 Сводка сейчас", callback_data="report"),
         InlineKeyboardButton("⭐ Все монеты", callback_data="all")],
        [InlineKeyboardButton("🔥 Горячие сигналы", callback_data="hot")],
    ]
    await update.message.reply_text(
        "🤖 *Crypto Monitor Bot* запущен!\n\n"
        "Ты будешь получать сводку:\n"
        "🌅 *11:00 МСК* — утренняя сессия\n"
        "🌙 *23:00 МСК* — ночная сессия\n\n"
        "Или запроси сводку прямо сейчас 👇",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Загружаю данные с Binance...")
    report = build_report("Сводка по запросу")
    await msg.edit_text(report, parse_mode="Markdown", disable_web_page_preview=True)

async def cmd_coins(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Список монет с кнопками"""
    kb = []
    row = []
    for sym, cfg in MY_COINS.items():
        row.append(InlineKeyboardButton(cfg["name"], callback_data=f"coin_{sym}"))
        if len(row) == 3:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    await update.message.reply_text(
        "📋 Выбери монету для детального анализа:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def cmd_hot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Только горячие сигналы"""
    msg = await update.message.reply_text("🔍 Ищу горячие сигналы...")
    symbols = list(MY_COINS.keys())
    prices = get_prices(symbols)
    hot_lines = []
    for sym, cfg in MY_COINS.items():
        t = prices.get(sym)
        if not t:
            continue
        zone = check_zone(t["price"], cfg)
        if "В ЗОНЕ" in zone:
            ch = t["change"]
            hot_lines.append(
                f"🔥 *{cfg['name']}* — {fmt_price(t['price'])} {fmt_change(ch)}\n"
                f"   {zone}\n   Сигнал: {cfg['signal']}"
            )
    if hot_lines:
        text = "🔥 *ГОРЯЧИЕ СИГНАЛЫ — ЦЕНА В ЗОНЕ ВХОДА:*\n\n" + "\n\n".join(hot_lines)
    else:
        text = "😴 Сейчас нет монет в зоне входа.\nРынок ждёт движения."
    await msg.edit_text(text, parse_mode="Markdown")

async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "report":
        await q.edit_message_text("⏳ Загружаю...")
        report = build_report("Сводка по запросу")
        kb = [[InlineKeyboardButton("🔄 Обновить", callback_data="report")]]
        await q.edit_message_text(report, parse_mode="Markdown",
                                   disable_web_page_preview=True,
                                   reply_markup=InlineKeyboardMarkup(kb))

    elif data == "hot":
        await q.edit_message_text("🔍 Ищу горячие сигналы...")
        symbols = list(MY_COINS.keys())
        prices = get_prices(symbols)
        hot_lines = []
        for sym, cfg in MY_COINS.items():
            t = prices.get(sym)
            if not t:
                continue
            zone = check_zone(t["price"], cfg)
            if "В ЗОНЕ" in zone:
                ch = t["change"]
                hot_lines.append(f"🔥 *{cfg['name']}* — {fmt_price(t['price'])} {fmt_change(ch)}\n   {zone}")
        text = ("🔥 *ЦЕНА В ЗОНЕ ВХОДА:*\n\n" + "\n\n".join(hot_lines)) if hot_lines else "😴 Нет горячих сигналов"
        kb = [[InlineKeyboardButton("🔄 Обновить", callback_data="hot")]]
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "all":
        kb = []
        row = []
        for sym, cfg in MY_COINS.items():
            row.append(InlineKeyboardButton(cfg["name"], callback_data=f"coin_{sym}"))
            if len(row) == 3:
                kb.append(row)
                row = []
        if row:
            kb.append(row)
        await q.edit_message_text("📋 Выбери монету:", reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("coin_"):
        sym = data[5:]
        await q.edit_message_text("⏳ Загружаю данные...")
        detail = build_coin_detail(sym)
        kb = [
            [InlineKeyboardButton("🔄 Обновить", callback_data=f"coin_{sym}"),
             InlineKeyboardButton("◀️ Назад", callback_data="all")]
        ]
        await q.edit_message_text(detail, parse_mode="Markdown",
                                   disable_web_page_preview=False,
                                   reply_markup=InlineKeyboardMarkup(kb))

# ============================================================
# АВТОМАТИЧЕСКАЯ РАССЫЛКА
# ============================================================
def load_chat_ids() -> set:
    try:
        with open("chat_ids.txt") as f:
            return set(int(line.strip()) for line in f if line.strip())
    except:
        return set()

async def send_scheduled(bot: Bot, session_name: str):
    """Отправить сводку всем пользователям"""
    chat_ids = load_chat_ids() | user_chat_ids
    if not chat_ids:
        log.warning("Нет chat_id для рассылки")
        return

    report = build_report(session_name)
    kb = [[
        InlineKeyboardButton("🔥 Горячие", callback_data="hot"),
        InlineKeyboardButton("📊 Все монеты", callback_data="all"),
    ]]
    for cid in chat_ids:
        try:
            await bot.send_message(
                chat_id=cid,
                text=report,
                parse_mode="Markdown",
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup(kb)
            )
            log.info(f"Отправлено в {cid}")
        except Exception as e:
            log.error(f"Ошибка отправки в {cid}: {e}")

# ============================================================
# MAIN
# ============================================================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("coins",  cmd_coins))
    app.add_handler(CommandHandler("hot",    cmd_hot))
    app.add_handler(CallbackQueryHandler(callback_handler))

    # Планировщик рассылки
    scheduler = AsyncIOScheduler(timezone=MOSCOW_TZ)
    bot = app.bot

    # Каждые 30 минут
scheduler.add_job(
    lambda: asyncio.create_task(send_scheduled(bot, "🔄 Обновление каждые 30 минут")),
    "interval",
    minutes=30
)    scheduler.start()
    log.info("✅ Бот запущен! Рассылка: 11:00 и 23:00 МСК")

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
