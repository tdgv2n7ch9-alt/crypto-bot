#!/usr/bin/env python3
"""
🚀 Crypto Monitor Bot — Telegram бот для мониторинга крипторынка
Данные: CoinMarketCap API (топ-500 монет)
Рассылка каждые 30 минут
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

# ==============================================================
# НАСТРОЙКИ
# ==============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CMC_API_KEY = os.getenv("CMC_API_KEY", "7c581d74b60d4c40879edc0431b5e53a")
MOSCOW_TZ = pytz.timezone("Europe/Moscow")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

# ==============================================================
# CMC API
# ==============================================================
def get_top500():
    """Получить топ-500 монет с CMC"""
    try:
        url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
        headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
        params = {
            "limit": 500,
            "convert": "USDT",
            "sort": "market_cap",
        }
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", [])
    except Exception as e:
        log.error(f"CMC API error: {e}")
        return []

def fmt_price(p: float) -> str:
    if p >= 1000: return f"${p:,.2f}"
    if p >= 1: return f"${p:.4f}"
    if p >= 0.01: return f"${p:.6f}"
    return f"${p:.8f}"

def fmt_change(ch: float) -> str:
    arrow = "🟢" if ch >= 0 else "🔴"
    return f"{arrow} {ch:+.2f}%"

def fmt_mcap(m: float) -> str:
    if m >= 1e9: return f"${m/1e9:.2f}B"
    if m >= 1e6: return f"${m/1e6:.2f}M"
    return f"${m:.0f}"

# ==============================================================
# ФОРМИРОВАНИЕ СООБЩЕНИЙ
# ==============================================================
def build_report(session: str) -> list[str]:
    """Построить отчёт — возвращает список сообщений (разбитых по лимиту)"""
    now = datetime.now(MOSCOW_TZ)
    coins = get_top500()

    if not coins:
        return ["❌ Не удалось получить данные с CoinMarketCap. Попробуйте позже."]

    # Сортируем по изменению за 24ч
    sorted_gainers = sorted(
        coins,
        key=lambda x: x["quote"]["USDT"].get("percent_change_24h", 0),
        reverse=True
    )
    sorted_losers = sorted(
        coins,
        key=lambda x: x["quote"]["USDT"].get("percent_change_24h", 0)
    )

    top_gainers = sorted_gainers[:20]
    top_losers = sorted_losers[:20]

    header = (
        f"{'🌅' if 'Утро' in session else '🌙'} *{session}*\n"
        f"📅 {now.strftime('%d.%m.%Y %H:%M')} МСК\n"
        f"{'—' * 28}\n"
    )

    # Топ гейнеры
    gainers_text = "🚀 *ТОП-20 РОСТ за 24ч:*\n"
    for i, coin in enumerate(top_gainers, 1):
        q = coin["quote"]["USDT"]
        ch = q.get("percent_change_24h", 0)
        price = q.get("price", 0)
        gainers_text += (
            f"{i}. *{coin['symbol']}* — {fmt_price(price)} "
            f"{fmt_change(ch)} | MC: {fmt_mcap(q.get('market_cap', 0))}\n"
        )

    # Топ лузеры
    losers_text = "\n📉 *ТОП-20 ПАДЕНИЕ за 24ч:*\n"
    for i, coin in enumerate(top_losers, 1):
        q = coin["quote"]["USDT"]
        ch = q.get("percent_change_24h", 0)
        price = q.get("price", 0)
        losers_text += (
            f"{i}. *{coin['symbol']}* — {fmt_price(price)} "
            f"{fmt_change(ch)} | MC: {fmt_mcap(q.get('market_cap', 0))}\n"
        )

    footer = f"\n{'—' * 28}\n📊 Данные: CoinMarketCap • Топ-500 по капитализации"

    # Разбиваем на части (лимит Telegram 4096 символов)
    messages = []
    part1 = header + gainers_text
    part2 = losers_text + footer

    messages.append(part1)
    messages.append(part2)

    return messages

def build_top_by_change(period: str = "24h") -> list[str]:
    """Топ по изменению за период"""
    coins = get_top500()
    if not coins:
        return ["❌ Нет данных"]

    field_map = {
        "1h": "percent_change_1h",
        "24h": "percent_change_24h",
        "7d": "percent_change_7d",
    }
    field = field_map.get(period, "percent_change_24h")

    sorted_coins = sorted(
        coins,
        key=lambda x: x["quote"]["USDT"].get(field, 0),
        reverse=True
    )

    now = datetime.now(MOSCOW_TZ)
    text = f"📊 *Топ-20 рост за {period}* | {now.strftime('%H:%M')} МСК\n{'—'*28}\n"
    for i, coin in enumerate(sorted_coins[:20], 1):
        q = coin["quote"]["USDT"]
        ch = q.get(field, 0)
        price = q.get("price", 0)
        text += f"{i}. *{coin['symbol']}* — {fmt_price(price)} {fmt_change(ch)}\n"

    text2 = f"\n📉 *Топ-20 падение за {period}* | {now.strftime('%H:%M')} МСК\n{'—'*28}\n"
    for i, coin in enumerate(reversed(sorted_coins[-20:]), 1):
        q = coin["quote"]["USDT"]
        ch = q.get(field, 0)
        price = q.get("price", 0)
        text2 += f"{i}. *{coin['symbol']}* — {fmt_price(price)} {fmt_change(ch)}\n"

    return [text, text2]

# ==============================================================
# TELEGRAM HANDLERS
# ==============================================================
user_chat_ids = set()

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_chat_ids.add(chat_id)
    with open("chat_ids.txt", "a") as f:
        f.write(f"{chat_id}\n")

    kb = [
        [InlineKeyboardButton("📊 Сводка сейчас", callback_data="report_24h")],
        [
            InlineKeyboardButton("⏱ За 1ч", callback_data="period_1h"),
            InlineKeyboardButton("📅 За 24ч", callback_data="period_24h"),
            InlineKeyboardButton("📆 За 7д", callback_data="period_7d"),
        ],
    ]
    await update.message.reply_text(
        "🚀 *Crypto Monitor Bot запущен!*\n\n"
        "Я отслеживаю *топ-500 монет* по капитализации с CoinMarketCap.\n\n"
        "📊 Рассылка каждые *30 минут*\n"
        "Показываю топ-20 гейнеров и лузеров за период.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    period_map = {
        "report_24h": "24h",
        "period_1h": "1h",
        "period_24h": "24h",
        "period_7d": "7d",
    }

    if data in period_map:
        period = period_map[data]
        await q.edit_message_text("⏳ Загружаю данные с CoinMarketCap...")
        msgs = build_top_by_change(period)
        kb = [[
            InlineKeyboardButton("⏱ 1ч", callback_data="period_1h"),
            InlineKeyboardButton("📅 24ч", callback_data="period_24h"),
            InlineKeyboardButton("📆 7д", callback_data="period_7d"),
        ]]
        await q.edit_message_text(
            msgs[0],
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        if len(msgs) > 1:
            await ctx.bot.send_message(
                chat_id=q.message.chat_id,
                text=msgs[1],
                parse_mode="Markdown"
            )

# ==============================================================
# АВТОМАТИЧЕСКАЯ РАССЫЛКА
# ==============================================================
def load_chat_ids() -> set:
    try:
        with open("chat_ids.txt") as f:
            return set(int(line.strip()) for line in f if line.strip())
    except:
        return set()

async def send_scheduled(bot: Bot, session_name: str):
    chat_ids = load_chat_ids() | user_chat_ids
    if not chat_ids:
        log.warning("Нет chat_id для рассылки")
        return

    msgs = build_report(session_name)
    kb = [[
        InlineKeyboardButton("⏱ За 1ч", callback_data="period_1h"),
        InlineKeyboardButton("📅 За 24ч", callback_data="period_24h"),
        InlineKeyboardButton("📆 За 7д", callback_data="period_7d"),
    ]]

    for cid in chat_ids:
        try:
            for i, msg in enumerate(msgs):
                markup = InlineKeyboardMarkup(kb) if i == len(msgs) - 1 else None
                await bot.send_message(
                    chat_id=cid,
                    text=msg,
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                    reply_markup=markup
                )
            log.info(f"Отправлено в {cid}")
        except Exception as e:
            log.error(f"Ошибка отправки в {cid}: {e}")

# ==============================================================
# MAIN
# ==============================================================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(callback_handler))

    scheduler = AsyncIOScheduler(timezone=MOSCOW_TZ)
    bot = app.bot

    scheduler.add_job(
        lambda: asyncio.create_task(send_scheduled(bot, "🔄 Обновление каждые 30 минут")),
        "interval",
        minutes=30
    )
    scheduler.start()
    log.info("✅ Бот запущен! Топ-500 CMC. Рассылка каждые 30 минут.")

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
