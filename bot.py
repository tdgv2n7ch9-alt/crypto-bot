#!/usr/bin/env python3
"""
🚀 Crypto Monitor Bot — Telegram бот для мониторинга крипторынка
Данные: CoinMarketCap API (топ-300 монет)
Авто-сигналы по RSI, изменению цены, объёму
Часовой пояс: Стамбул (UTC+3)
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
BOT_TOKEN  = os.getenv("BOT_TOKEN")
CMC_API_KEY = os.getenv("CMC_API_KEY", "7c581d74b60d4c40879edc0431b5e53a")
TZ = pytz.timezone("Europe/Istanbul")  # UTC+3, Стамбул

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

# ==============================================================
# CMC API
# ==============================================================
def get_top300():
    """Получить топ-300 монет с CMC"""
    try:
        url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
        headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
        params = {
            "limit": 300,
            "convert": "USDT",
            "sort": "market_cap",
        }
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get("data", [])
    except Exception as e:
        log.error(f"CMC API error: {e}")
        return []

# ==============================================================
# АВТО-СИГНАЛЫ
# ==============================================================
def get_signal(coin: dict) -> dict:
    """Генерация авто-сигнала по монете"""
    q = coin["quote"]["USDT"]
    ch1h  = q.get("percent_change_1h", 0) or 0
    ch24h = q.get("percent_change_24h", 0) or 0
    ch7d  = q.get("percent_change_7d", 0) or 0
    vol24h = q.get("volume_24h", 0) or 0
    mcap   = q.get("market_cap", 0) or 0
    price  = q.get("price", 0) or 0

    # Объём / капитализация (активность)
    vol_ratio = (vol24h / mcap * 100) if mcap > 0 else 0

    # Простая логика сигнала
    score = 0
    reasons = []

    # 24ч движение
    if ch24h >= 10:
        score += 2
        reasons.append(f"🚀 +{ch24h:.1f}% за 24ч")
    elif ch24h >= 5:
        score += 1
        reasons.append(f"📈 +{ch24h:.1f}% за 24ч")
    elif ch24h <= -15:
        score -= 2
        reasons.append(f"💥 {ch24h:.1f}% за 24ч")
    elif ch24h <= -7:
        score -= 1
        reasons.append(f"📉 {ch24h:.1f}% за 24ч")

    # 1ч импульс
    if ch1h >= 3:
        score += 1
        reasons.append(f"⚡ +{ch1h:.1f}% за 1ч")
    elif ch1h <= -3:
        score -= 1
        reasons.append(f"⚡ {ch1h:.1f}% за 1ч")

    # 7д тренд
    if ch7d >= 20:
        score += 1
        reasons.append(f"📊 +{ch7d:.1f}% за 7д")
    elif ch7d <= -20:
        score -= 1
        reasons.append(f"📊 {ch7d:.1f}% за 7д")

    # Объём
    if vol_ratio >= 20:
        score += 1
        reasons.append(f"🔥 Объём {vol_ratio:.0f}% от капитализации")
    elif vol_ratio >= 10:
        reasons.append(f"📊 Объём {vol_ratio:.0f}% от капитализации")

    # Итоговый сигнал
    if score >= 3:
        signal = "🟢 СИЛЬНЫЙ ЛОНГ"
    elif score == 2:
        signal = "🟢 ЛОНГ"
    elif score == 1:
        signal = "🔵 СЛАБЫЙ ЛОНГ"
    elif score == 0:
        signal = "⚪ НЕЙТРАЛЬНО"
    elif score == -1:
        signal = "🟠 СЛАБЫЙ ШОРТ"
    elif score == -2:
        signal = "🔴 ШОРТ"
    else:
        signal = "🔴 СИЛЬНЫЙ ШОРТ"

    return {
        "signal": signal,
        "score": score,
        "reasons": reasons,
        "ch1h": ch1h,
        "ch24h": ch24h,
        "ch7d": ch7d,
        "vol_ratio": vol_ratio,
        "price": price,
        "mcap": mcap,
    }

# ==============================================================
# ФОРМАТИРОВАНИЕ
# ==============================================================
def fmt_price(p: float) -> str:
    if p >= 1000: return f"${p:,.2f}"
    if p >= 1:    return f"${p:.4f}"
    if p >= 0.01: return f"${p:.6f}"
    return f"${p:.8f}"

def fmt_change(ch: float) -> str:
    arrow = "🟢" if ch >= 0 else "🔴"
    return f"{arrow} {ch:+.2f}%"

def fmt_mcap(m: float) -> str:
    if m >= 1e9: return f"${m/1e9:.1f}B"
    if m >= 1e6: return f"${m/1e6:.1f}M"
    return f"${m:.0f}"

# ==============================================================
# ПОСТРОЕНИЕ ОТЧЁТОВ
# ==============================================================
def build_main_report() -> list[str]:
    """Главный отчёт: топ гейнеры и лузеры"""
    now = datetime.now(TZ)
    coins = get_top300()
    if not coins:
        return ["❌ Нет данных с CoinMarketCap"]

    sorted_up = sorted(coins, key=lambda x: x["quote"]["USDT"].get("percent_change_24h", 0), reverse=True)
    sorted_dn = sorted(coins, key=lambda x: x["quote"]["USDT"].get("percent_change_24h", 0))

    header = (
        f"📊 *Сводка рынка* | {now.strftime('%d.%m.%Y %H:%M')} Istanbul\n"
        f"Топ-300 по капитализации • CoinMarketCap\n"
        f"{'—'*28}\n"
    )

    gainers = "🚀 *ТОП-20 РОСТ за 24ч:*\n"
    for i, c in enumerate(sorted_up[:20], 1):
        q = c["quote"]["USDT"]
        gainers += f"{i}. *{c['symbol']}* {fmt_price(q['price'])} {fmt_change(q.get('percent_change_24h',0))} | {fmt_mcap(q.get('market_cap',0))}\n"

    losers = "\n📉 *ТОП-20 ПАДЕНИЕ за 24ч:*\n"
    for i, c in enumerate(sorted_dn[:20], 1):
        q = c["quote"]["USDT"]
        losers += f"{i}. *{c['symbol']}* {fmt_price(q['price'])} {fmt_change(q.get('percent_change_24h',0))} | {fmt_mcap(q.get('market_cap',0))}\n"

    footer = f"\n{'—'*28}\n🕐 Обновление каждые 30 минут"

    return [header + gainers, losers + footer]


def build_signals_report() -> list[str]:
    """Авто-сигналы: лучшие лонг и шорт возможности"""
    now = datetime.now(TZ)
    coins = get_top300()
    if not coins:
        return ["❌ Нет данных с CoinMarketCap"]

    signals = [(c, get_signal(c)) for c in coins]

    # Топ лонги (высокий score)
    longs = sorted(
        [(c, s) for c, s in signals if s["score"] >= 2],
        key=lambda x: x[1]["score"],
        reverse=True
    )[:15]

    # Топ шорты (низкий score)
    shorts = sorted(
        [(c, s) for c, s in signals if s["score"] <= -2],
        key=lambda x: x[1]["score"]
    )[:15]

    header = (
        f"🤖 *Авто-сигналы* | {now.strftime('%H:%M')} Istanbul\n"
        f"Анализ топ-300 монет по импульсу и объёму\n"
        f"{'—'*28}\n"
    )

    long_text = "🟢 *СИГНАЛЫ ЛОНГ:*\n"
    if longs:
        for c, s in longs:
            q = c["quote"]["USDT"]
            reasons_str = " | ".join(s["reasons"][:2])
            long_text += (
                f"*{c['symbol']}* {fmt_price(s['price'])} — {s['signal']}\n"
                f"  ↳ {reasons_str}\n"
            )
    else:
        long_text += "Нет явных сигналов лонг\n"

    short_text = "\n🔴 *СИГНАЛЫ ШОРТ:*\n"
    if shorts:
        for c, s in shorts:
            reasons_str = " | ".join(s["reasons"][:2])
            short_text += (
                f"*{c['symbol']}* {fmt_price(s['price'])} — {s['signal']}\n"
                f"  ↳ {reasons_str}\n"
            )
    else:
        short_text += "Нет явных сигналов шорт\n"

    footer = (
        f"\n{'—'*28}\n"
        f"⚠️ _Сигналы основаны на импульсе цены и объёме._\n"
        f"_Всегда проверяй на графике перед входом!_"
    )

    return [header + long_text, short_text + footer]


def build_coin_card(symbol: str) -> str:
    """Карточка конкретной монеты"""
    coins = get_top300()
    symbol = symbol.upper()
    coin = next((c for c in coins if c["symbol"] == symbol), None)

    if not coin:
        return f"❌ Монета *{symbol}* не найдена в топ-300"

    q = coin["quote"]["USDT"]
    s = get_signal(coin)
    now = datetime.now(TZ)

    reasons_str = "\n".join([f"  • {r}" for r in s["reasons"]]) or "  • Нет явных факторов"

    return (
        f"🪙 *{coin['name']} ({symbol})*\n"
        f"#{coin['cmc_rank']} по капитализации\n"
        f"{'—'*28}\n"
        f"💰 Цена: *{fmt_price(s['price'])}*\n"
        f"📊 Капитализация: {fmt_mcap(s['mcap'])}\n"
        f"{'—'*28}\n"
        f"⏱ За 1ч: {fmt_change(s['ch1h'])}\n"
        f"📅 За 24ч: {fmt_change(s['ch24h'])}\n"
        f"📆 За 7д: {fmt_change(s['ch7d'])}\n"
        f"🔥 Объём/Капитализация: {s['vol_ratio']:.1f}%\n"
        f"{'—'*28}\n"
        f"🤖 Сигнал: *{s['signal']}*\n"
        f"Факторы:\n{reasons_str}\n"
        f"{'—'*28}\n"
        f"🕐 {now.strftime('%H:%M')} Istanbul | CoinMarketCap"
    )


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
        [InlineKeyboardButton("📊 Сводка рынка", callback_data="report"),
         InlineKeyboardButton("🤖 Авто-сигналы", callback_data="signals")],
        [InlineKeyboardButton("⏱ Топ за 1ч", callback_data="period_1h"),
         InlineKeyboardButton("📅 Топ за 24ч", callback_data="period_24h"),
         InlineKeyboardButton("📆 Топ за 7д", callback_data="period_7d")],
    ]
    await update.message.reply_text(
        "🚀 *Crypto Monitor Bot*\n\n"
        "Анализирую *топ-300 монет* CoinMarketCap\n\n"
        "🤖 *Авто-сигналы* — лонг/шорт по импульсу цены и объёму\n"
        "📊 *Сводка* — топ гейнеры и лузеры\n"
        "🔍 *Поиск* — напиши `/coin BTC` для карточки монеты\n\n"
        "🕐 Рассылка каждые *30 минут* | Стамбул UTC+3",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def cmd_coin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /coin SYMBOL"""
    if not ctx.args:
        await update.message.reply_text("Напиши: `/coin BTC` или `/coin ETH`", parse_mode="Markdown")
        return
    symbol = ctx.args[0].upper()
    msg = await update.message.reply_text(f"⏳ Загружаю данные по *{symbol}*...", parse_mode="Markdown")
    card = build_coin_card(symbol)
    kb = [[InlineKeyboardButton("🔄 Обновить", callback_data=f"coin_{symbol}")]]
    await msg.edit_text(card, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "report":
        await q.edit_message_text("⏳ Загружаю сводку рынка...")
        msgs = build_main_report()
        kb = [[InlineKeyboardButton("🔄 Обновить", callback_data="report"),
               InlineKeyboardButton("🤖 Сигналы", callback_data="signals")]]
        await q.edit_message_text(msgs[0], parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        if len(msgs) > 1:
            await ctx.bot.send_message(q.message.chat_id, msgs[1], parse_mode="Markdown")

    elif data == "signals":
        await q.edit_message_text("⏳ Анализирую топ-300 монет...")
        msgs = build_signals_report()
        kb = [[InlineKeyboardButton("🔄 Обновить", callback_data="signals"),
               InlineKeyboardButton("📊 Сводка", callback_data="report")]]
        await q.edit_message_text(msgs[0], parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        if len(msgs) > 1:
            await ctx.bot.send_message(q.message.chat_id, msgs[1], parse_mode="Markdown")

    elif data.startswith("period_"):
        period = data.split("_")[1]
        field_map = {"1h": "percent_change_1h", "24h": "percent_change_24h", "7d": "percent_change_7d"}
        field = field_map[period]
        label_map = {"1h": "1ч", "24h": "24ч", "7d": "7д"}

        await q.edit_message_text(f"⏳ Загружаю топ за {label_map[period]}...")
        coins = get_top300()
        if not coins:
            await q.edit_message_text("❌ Нет данных")
            return

        now = datetime.now(TZ)
        sorted_up = sorted(coins, key=lambda x: x["quote"]["USDT"].get(field, 0), reverse=True)
        sorted_dn = sorted(coins, key=lambda x: x["quote"]["USDT"].get(field, 0))

        text = f"📊 *Топ за {label_map[period]}* | {now.strftime('%H:%M')} Istanbul\n{'—'*28}\n"
        text += "🚀 *Рост:*\n"
        for i, c in enumerate(sorted_up[:15], 1):
            ch = c["quote"]["USDT"].get(field, 0)
            text += f"{i}. *{c['symbol']}* {fmt_price(c['quote']['USDT']['price'])} {fmt_change(ch)}\n"

        text2 = f"📉 *Падение:*\n"
        for i, c in enumerate(sorted_dn[:15], 1):
            ch = c["quote"]["USDT"].get(field, 0)
            text2 += f"{i}. *{c['symbol']}* {fmt_price(c['quote']['USDT']['price'])} {fmt_change(ch)}\n"

        kb = [[
            InlineKeyboardButton("⏱ 1ч", callback_data="period_1h"),
            InlineKeyboardButton("📅 24ч", callback_data="period_24h"),
            InlineKeyboardButton("📆 7д", callback_data="period_7d"),
        ]]
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        await ctx.bot.send_message(q.message.chat_id, text2, parse_mode="Markdown")

    elif data.startswith("coin_"):
        symbol = data[5:]
        await q.edit_message_text(f"⏳ Обновляю {symbol}...")
        card = build_coin_card(symbol)
        kb = [[InlineKeyboardButton("🔄 Обновить", callback_data=f"coin_{symbol}")]]
        await q.edit_message_text(card, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))


# ==============================================================
# АВТОМАТИЧЕСКАЯ РАССЫЛКА
# ==============================================================
def load_chat_ids() -> set:
    try:
        with open("chat_ids.txt") as f:
            return set(int(line.strip()) for line in f if line.strip())
    except:
        return set()

async def send_scheduled(bot: Bot):
    chat_ids = load_chat_ids() | user_chat_ids
    if not chat_ids:
        log.warning("Нет chat_id для рассылки")
        return

    now = datetime.now(TZ)
    log.info(f"Рассылка в {now.strftime('%H:%M')} Istanbul")

    # Отправляем сводку + сигналы
    report_msgs = build_main_report()
    signal_msgs = build_signals_report()
    all_msgs = report_msgs + signal_msgs

    kb = [[
        InlineKeyboardButton("🔄 Обновить", callback_data="report"),
        InlineKeyboardButton("🤖 Сигналы", callback_data="signals"),
    ]]

    for cid in chat_ids:
        try:
            for i, msg in enumerate(all_msgs):
                markup = InlineKeyboardMarkup(kb) if i == len(all_msgs) - 1 else None
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
    app.add_handler(CommandHandler("coin", cmd_coin))
    app.add_handler(CallbackQueryHandler(callback_handler))

    scheduler = AsyncIOScheduler(timezone=TZ)
    bot = app.bot

    scheduler.add_job(
        lambda: asyncio.create_task(send_scheduled(bot)),
        "interval",
        minutes=30
    )
    scheduler.start()
    log.info("✅ Бот запущен! Топ-300 CMC. Авто-сигналы. Стамбул UTC+3.")

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
