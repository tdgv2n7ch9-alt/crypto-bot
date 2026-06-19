#!/usr/bin/env python3
"""
🚀 Crypto Monitor Bot v2.1
Профессиональные трейдерские сигналы | Топ-300 CMC | Стамбул UTC+3
Ссылки на CoinMarketCap по каждой монете
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

# ═══════════════════════════════════════════════════════════════
BOT_TOKEN   = os.getenv("BOT_TOKEN")
CMC_API_KEY = os.getenv("CMC_API_KEY", "7c581d74b60d4c40879edc0431b5e53a")
TZ          = pytz.timezone("Europe/Istanbul")

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# CMC API
# ═══════════════════════════════════════════════════════════════
def get_top300():
    try:
        url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
        headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
        params = {"limit": 300, "convert": "USDT", "sort": "market_cap"}
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get("data", [])
    except Exception as e:
        log.error(f"CMC error: {e}")
        return []

def cmc_url(slug: str) -> str:
    return f"https://coinmarketcap.com/currencies/{slug}/"

def tv_url(symbol: str) -> str:
    return f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}USDT"

# ═══════════════════════════════════════════════════════════════
# ФОРМАТИРОВАНИЕ
# ═══════════════════════════════════════════════════════════════
def fmt_price(p: float) -> str:
    if p >= 1000:  return f"${p:,.2f}"
    if p >= 1:     return f"${p:.4f}"
    if p >= 0.01:  return f"${p:.6f}"
    return f"${p:.8f}"

def fmt_pct(ch: float) -> str:
    if ch >= 0: return f"▲ +{ch:.2f}%"
    return f"▼ {ch:.2f}%"

def fmt_mcap(m: float) -> str:
    if m >= 1e9: return f"${m/1e9:.2f}B"
    if m >= 1e6: return f"${m/1e6:.2f}M"
    return f"${m:.0f}"

def fmt_vol(v: float) -> str:
    if v >= 1e9: return f"${v/1e9:.2f}B"
    if v >= 1e6: return f"${v/1e6:.2f}M"
    return f"${v:.0f}"

def sparkline(ch1h, ch24h, ch7d) -> str:
    def arr(v):
        if v > 3:  return "🟢"
        if v > 0:  return "🔵"
        if v > -3: return "🟠"
        return "🔴"
    return f"{arr(ch7d)}7д {arr(ch24h)}24ч {arr(ch1h)}1ч"

# ═══════════════════════════════════════════════════════════════
# АНАЛИЗ
# ═══════════════════════════════════════════════════════════════
def analyze(coin: dict) -> dict:
    q     = coin["quote"]["USDT"]
    ch1h  = q.get("percent_change_1h",  0) or 0
    ch24h = q.get("percent_change_24h", 0) or 0
    ch7d  = q.get("percent_change_7d",  0) or 0
    ch30d = q.get("percent_change_30d", 0) or 0
    vol   = q.get("volume_24h",  0) or 0
    mcap  = q.get("market_cap",  0) or 0
    price = q.get("price",       0) or 0
    vol_ratio = (vol / mcap * 100) if mcap > 0 else 0

    score = 0
    signals  = []
    warnings = []

    if ch24h >= 15:   score += 3; signals.append("Сильный импульс +24ч")
    elif ch24h >= 7:  score += 2; signals.append("Рост +24ч")
    elif ch24h >= 3:  score += 1; signals.append("Слабый рост +24ч")
    elif ch24h <= -15: score -= 3; signals.append("Обвал -24ч")
    elif ch24h <= -7:  score -= 2; signals.append("Падение -24ч")
    elif ch24h <= -3:  score -= 1; signals.append("Слабое падение -24ч")

    if ch1h >= 5:    score += 2; signals.append("Пробой вверх 1ч")
    elif ch1h >= 2:  score += 1; signals.append("Рост 1ч")
    elif ch1h <= -5: score -= 2; signals.append("Пробой вниз 1ч")
    elif ch1h <= -2: score -= 1; signals.append("Падение 1ч")

    if ch7d >= 30:    score += 2; signals.append("Сильный недельный тренд")
    elif ch7d >= 10:  score += 1; signals.append("Позитивный недельный тренд")
    elif ch7d <= -30: score -= 2; warnings.append("Слабый недельный тренд")
    elif ch7d <= -10: score -= 1; warnings.append("Негативный недельный тренд")

    if vol_ratio >= 25:   score += 2; signals.append(f"Аномальный объём {vol_ratio:.0f}%")
    elif vol_ratio >= 12: score += 1; signals.append(f"Повышенный объём {vol_ratio:.0f}%")
    elif vol_ratio < 2:   warnings.append("Низкий объём — осторожно")

    if ch1h >= 3 and ch24h <= -5:  score += 1; signals.append("Возможный разворот вверх")
    if ch1h <= -3 and ch24h >= 5:  score -= 1; warnings.append("Возможный разворот вниз")

    if score >= 5:    label = "🔥 СИЛЬНЫЙ ЛОНГ"
    elif score >= 3:  label = "✅ ЛОНГ"
    elif score >= 1:  label = "📈 СЛАБЫЙ ЛОНГ"
    elif score == 0:  label = "⚪️ НЕЙТРАЛЬНО"
    elif score >= -2: label = "📉 СЛАБЫЙ ШОРТ"
    elif score >= -4: label = "🔻 ШОРТ"
    else:             label = "💥 СИЛЬНЫЙ ШОРТ"

    return {
        "label": label, "score": score,
        "signals": signals, "warnings": warnings,
        "ch1h": ch1h, "ch24h": ch24h, "ch7d": ch7d, "ch30d": ch30d,
        "vol_ratio": vol_ratio, "vol": vol, "price": price, "mcap": mcap,
    }

# ═══════════════════════════════════════════════════════════════
# ОТЧЁТЫ
# ═══════════════════════════════════════════════════════════════
def hdr(title: str, now: datetime) -> str:
    return (
        f"{'━'*28}\n"
        f"  {title}\n"
        f"  🕐 {now.strftime('%d.%m.%Y  %H:%M')} Istanbul\n"
        f"{'━'*28}\n"
    )

def build_market_report() -> list:
    now   = datetime.now(TZ)
    coins = get_top300()
    if not coins:
        return [{"text": "❌ Нет данных", "buttons": []}]

    up = sorted(coins, key=lambda x: x["quote"]["USDT"].get("percent_change_24h", 0), reverse=True)
    dn = sorted(coins, key=lambda x: x["quote"]["USDT"].get("percent_change_24h", 0))

    positive = sum(1 for c in coins if c["quote"]["USDT"].get("percent_change_24h", 0) > 0)
    pct_pos  = positive / len(coins) * 100
    if pct_pos >= 60:   mood = "🟢 Рынок бычий"
    elif pct_pos >= 45: mood = "🟡 Рынок нейтральный"
    else:               mood = "🔴 Рынок медвежий"

    # Сообщение 1 — гейнеры
    text1  = hdr("📊  ОБЗОР РЫНКА", now)
    text1 += f"  {mood}\n"
    text1 += f"  Растут: {positive}/{len(coins)} ({pct_pos:.0f}%)\n\n"
    text1 += "🚀  ТОП-15 РОСТ за 24ч\n"
    text1 += f"{'─'*28}\n"
    for i, c in enumerate(up[:15], 1):
        q  = c["quote"]["USDT"]
        ch = q.get("percent_change_24h", 0)
        text1 += f"{i:>2}. {c['symbol']:<8} {fmt_price(q['price']):<14} {fmt_pct(ch)}\n"

    # Кнопки для гейнеров
    btns1 = []
    for c in up[:8]:
        slug = c.get("slug", c["symbol"].lower())
        btns1.append(InlineKeyboardButton(
            f"🔗 {c['symbol']}",
            url=cmc_url(slug)
        ))

    # Сообщение 2 — лузеры
    text2  = f"{'─'*28}\n"
    text2 += "📉  ТОП-15 ПАДЕНИЕ за 24ч\n"
    text2 += f"{'─'*28}\n"
    for i, c in enumerate(dn[:15], 1):
        q  = c["quote"]["USDT"]
        ch = q.get("percent_change_24h", 0)
        text2 += f"{i:>2}. {c['symbol']:<8} {fmt_price(q['price']):<14} {fmt_pct(ch)}\n"
    text2 += f"{'━'*28}\n"
    text2 += f"  📡 CoinMarketCap • Топ-300\n"
    text2 += f"{'━'*28}"

    btns2 = []
    for c in dn[:8]:
        slug = c.get("slug", c["symbol"].lower())
        btns2.append(InlineKeyboardButton(
            f"🔗 {c['symbol']}",
            url=cmc_url(slug)
        ))

    return [
        {"text": f"```\n{text1}```", "buttons": btns1},
        {"text": f"```\n{text2}```", "buttons": btns2},
    ]


def build_signals_report() -> list:
    now   = datetime.now(TZ)
    coins = get_top300()
    if not coins:
        return [{"text": "❌ Нет данных", "buttons": []}]

    analyzed = [(c, analyze(c)) for c in coins]
    longs  = sorted([(c,a) for c,a in analyzed if a["score"] >= 3],  key=lambda x: x[1]["score"], reverse=True)[:10]
    shorts = sorted([(c,a) for c,a in analyzed if a["score"] <= -3], key=lambda x: x[1]["score"])[:10]

    text1  = hdr("🤖  АВТО-СИГНАЛЫ", now)
    text1 += "🟢  ЛОНГ-СИГНАЛЫ\n"
    text1 += f"{'─'*28}\n"
    btns1  = []
    if longs:
        for c, a in longs:
            sig_str = " / ".join(a["signals"][:2])
            text1 += (
                f"▶ {c['symbol']:<8} {fmt_price(a['price'])}\n"
                f"  {a['label']}\n"
                f"  {sparkline(a['ch1h'], a['ch24h'], a['ch7d'])}\n"
                f"  ↳ {sig_str}\n"
                f"{'─'*28}\n"
            )
            slug = c.get("slug", c["symbol"].lower())
            btns1.append(InlineKeyboardButton(f"📊 {c['symbol']}", url=cmc_url(slug)))
    else:
        text1 += "  Нет явных лонг-сигналов\n"
        text1 += f"{'─'*28}\n"

    text2  = "🔴  ШОРТ-СИГНАЛЫ\n"
    text2 += f"{'─'*28}\n"
    btns2  = []
    if shorts:
        for c, a in shorts:
            sig_str = " / ".join(a["signals"][:2])
            text2 += (
                f"▶ {c['symbol']:<8} {fmt_price(a['price'])}\n"
                f"  {a['label']}\n"
                f"  {sparkline(a['ch1h'], a['ch24h'], a['ch7d'])}\n"
                f"  ↳ {sig_str}\n"
                f"{'─'*28}\n"
            )
            slug = c.get("slug", c["symbol"].lower())
            btns2.append(InlineKeyboardButton(f"📊 {c['symbol']}", url=cmc_url(slug)))
    else:
        text2 += "  Нет явных шорт-сигналов\n"
        text2 += f"{'─'*28}\n"

    text2 += "⚠️  Сигналы на основе импульса и объёма.\n"
    text2 += "    Всегда проверяй на графике!"

    return [
        {"text": f"```\n{text1}```", "buttons": btns1},
        {"text": f"```\n{text2}```", "buttons": btns2},
    ]


def build_period_report(period: str) -> list:
    field_map = {"1h": "percent_change_1h", "24h": "percent_change_24h", "7d": "percent_change_7d"}
    label_map = {"1h": "1 ЧАС", "24h": "24 ЧАСА", "7d": "7 ДНЕЙ"}
    field = field_map.get(period, "percent_change_24h")
    label = label_map.get(period, "24 ЧАСА")

    now   = datetime.now(TZ)
    coins = get_top300()
    if not coins:
        return [{"text": "❌ Нет данных", "buttons": []}]

    up = sorted(coins, key=lambda x: x["quote"]["USDT"].get(field, 0), reverse=True)
    dn = sorted(coins, key=lambda x: x["quote"]["USDT"].get(field, 0))

    text1  = hdr(f"📊  ТОП за {label}", now)
    text1 += "🚀  ЛИДЕРЫ РОСТА\n"
    text1 += f"{'─'*28}\n"
    btns1  = []
    for i, c in enumerate(up[:15], 1):
        ch = c["quote"]["USDT"].get(field, 0)
        text1 += f"{i:>2}. {c['symbol']:<8} {fmt_price(c['quote']['USDT']['price']):<14} {fmt_pct(ch)}\n"
    for c in up[:8]:
        slug = c.get("slug", c["symbol"].lower())
        btns1.append(InlineKeyboardButton(f"📊 {c['symbol']}", url=cmc_url(slug)))

    text2  = f"{'─'*28}\n"
    text2 += "📉  ЛИДЕРЫ ПАДЕНИЯ\n"
    text2 += f"{'─'*28}\n"
    btns2  = []
    for i, c in enumerate(dn[:15], 1):
        ch = c["quote"]["USDT"].get(field, 0)
        text2 += f"{i:>2}. {c['symbol']:<8} {fmt_price(c['quote']['USDT']['price']):<14} {fmt_pct(ch)}\n"
    text2 += f"{'━'*28}"
    for c in dn[:8]:
        slug = c.get("slug", c["symbol"].lower())
        btns2.append(InlineKeyboardButton(f"📊 {c['symbol']}", url=cmc_url(slug)))

    return [
        {"text": f"```\n{text1}```", "buttons": btns1},
        {"text": f"```\n{text2}```", "buttons": btns2},
    ]


def build_coin_card(symbol: str) -> dict:
    coins  = get_top300()
    symbol = symbol.upper().strip()
    coin   = next((c for c in coins if c["symbol"] == symbol), None)

    if not coin:
        return {"text": f"❌ *{symbol}* не найден в топ-300", "buttons": []}

    q   = coin["quote"]["USDT"]
    a   = analyze(coin)
    now = datetime.now(TZ)
    slug = coin.get("slug", symbol.lower())

    vol_bar_n = min(int(a["vol_ratio"] / 5), 10)
    vol_bar   = "█" * vol_bar_n + "░" * (10 - vol_bar_n)
    sigs  = "\n".join([f"  ✅ {s}" for s in a["signals"]])  or "  —"
    warns = "\n".join([f"  ⚠️ {w}" for w in a["warnings"]]) or "  —"

    card = (
        f"{'━'*28}\n"
        f"  🪙  {coin['name']}  ({symbol})\n"
        f"  #{coin['cmc_rank']} по капитализации\n"
        f"{'━'*28}\n"
        f"  💰 Цена      {fmt_price(a['price'])}\n"
        f"  📊 Капитал   {fmt_mcap(a['mcap'])}\n"
        f"  📦 Объём 24ч {fmt_vol(a['vol'])}\n"
        f"{'─'*28}\n"
        f"  Динамика цены:\n"
        f"  1ч   {fmt_pct(a['ch1h'])}\n"
        f"  24ч  {fmt_pct(a['ch24h'])}\n"
        f"  7д   {fmt_pct(a['ch7d'])}\n"
        f"  30д  {fmt_pct(a['ch30d'])}\n"
        f"{'─'*28}\n"
        f"  Объём/Капитал: {a['vol_ratio']:.1f}%\n"
        f"  [{vol_bar}]\n"
        f"{'─'*28}\n"
        f"  Тренд:  {sparkline(a['ch1h'], a['ch24h'], a['ch7d'])}\n"
        f"  Сигнал: {a['label']}\n"
        f"{'─'*28}\n"
        f"  Факторы:\n{sigs}\n"
        f"  Предупреждения:\n{warns}\n"
        f"{'━'*28}\n"
        f"  🕐 {now.strftime('%H:%M')} Istanbul\n"
        f"{'━'*28}"
    )

    btns = [
        InlineKeyboardButton("📈 CoinMarketCap", url=cmc_url(slug)),
        InlineKeyboardButton("📊 TradingView",   url=tv_url(symbol)),
    ]
    return {"text": f"```\n{card}```", "buttons": btns}


# ═══════════════════════════════════════════════════════════════
# КЛАВИАТУРЫ
# ═══════════════════════════════════════════════════════════════
def make_kb(coin_btns: list, extra: list = None) -> InlineKeyboardMarkup:
    """Строим клавиатуру: монеты по 4 в ряд + навигация"""
    rows = []
    # Кнопки монет по 4 в строке
    for i in range(0, len(coin_btns), 4):
        rows.append(coin_btns[i:i+4])
    # Навигационный ряд
    nav = [
        InlineKeyboardButton("📊 Рынок",    callback_data="report"),
        InlineKeyboardButton("🤖 Сигналы", callback_data="signals"),
    ]
    if extra:
        rows.append(extra)
    rows.append(nav)
    return InlineKeyboardMarkup(rows)

def period_kb() -> list:
    return [
        InlineKeyboardButton("⏱ 1ч",  callback_data="period_1h"),
        InlineKeyboardButton("📅 24ч", callback_data="period_24h"),
        InlineKeyboardButton("📆 7д",  callback_data="period_7d"),
    ]

def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Рынок",    callback_data="report"),
         InlineKeyboardButton("🤖 Сигналы", callback_data="signals")],
        [InlineKeyboardButton("⏱ 1ч",  callback_data="period_1h"),
         InlineKeyboardButton("📅 24ч", callback_data="period_24h"),
         InlineKeyboardButton("📆 7д",  callback_data="period_7d")],
    ])

async def send_parts(bot_or_msg, chat_id, parts: list, is_edit=False):
    """Отправить список частей отчёта с кнопками"""
    for i, part in enumerate(parts):
        text    = part["text"]
        btns    = part.get("buttons", [])
        is_last = (i == len(parts) - 1)

        # Кнопки монет + навигация для последнего сообщения
        if is_last:
            kb = make_kb(btns, extra=period_kb())
        else:
            # Только кнопки монет для промежуточных
            rows = []
            for j in range(0, len(btns), 4):
                rows.append(btns[j:j+4])
            kb = InlineKeyboardMarkup(rows) if rows else None

        if is_edit and i == 0:
            await bot_or_msg.edit_message_text(
                text, parse_mode="Markdown",
                reply_markup=kb,
                disable_web_page_preview=True
            )
        else:
            await bot_or_msg.send_message(
                chat_id, text,
                parse_mode="Markdown",
                reply_markup=kb,
                disable_web_page_preview=True
            )

# ═══════════════════════════════════════════════════════════════
# HANDLERS
# ═══════════════════════════════════════════════════════════════
user_chat_ids = set()

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_chat_ids.add(chat_id)
    with open("chat_ids.txt", "a") as f:
        f.write(f"{chat_id}\n")
    await update.message.reply_text(
        "```\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  🚀  CRYPTO MONITOR  v2.1\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  Топ-300 • CoinMarketCap\n"
        "  Авто-сигналы: импульс + объём\n"
        "  Ссылки на CMC по каждой монете\n"
        "  Рассылка каждые 30 минут\n"
        "  🕐 Стамбул UTC+3\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  /coin BTC  — карточка монеты\n"
        "  /top       — топ прямо сейчас\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "```",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )

async def cmd_coin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Напиши: `/coin BTC`", parse_mode="Markdown")
        return
    symbol = ctx.args[0].upper()
    msg = await update.message.reply_text(f"⏳ Загружаю {symbol}...")
    result = build_coin_card(symbol)
    btns   = result["buttons"]
    btns.append(InlineKeyboardButton("🔄 Обновить", callback_data=f"coin_{symbol}"))
    kb = InlineKeyboardMarkup([btns])
    await msg.edit_text(result["text"], parse_mode="Markdown", reply_markup=kb)

async def cmd_top(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Загружаю данные...")
    parts = build_market_report()
    await send_parts(ctx.bot, update.effective_chat.id, parts, is_edit=False)
    await msg.delete()

async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    data = q.data
    await q.answer()

    if data == "report":
        await q.edit_message_text("⏳ Загружаю рынок...", parse_mode="Markdown")
        parts = build_market_report()
        await send_parts(q, q.message.chat_id, parts, is_edit=True)

    elif data == "signals":
        await q.edit_message_text("⏳ Анализирую топ-300...", parse_mode="Markdown")
        parts = build_signals_report()
        await send_parts(q, q.message.chat_id, parts, is_edit=True)

    elif data.startswith("period_"):
        period = data.split("_")[1]
        await q.edit_message_text("⏳ Загружаю топ...", parse_mode="Markdown")
        parts = build_period_report(period)
        await send_parts(q, q.message.chat_id, parts, is_edit=True)

    elif data.startswith("coin_"):
        symbol = data[5:]
        await q.edit_message_text(f"⏳ Обновляю {symbol}...", parse_mode="Markdown")
        result = build_coin_card(symbol)
        btns   = result["buttons"]
        btns.append(InlineKeyboardButton("🔄 Обновить", callback_data=f"coin_{symbol}"))
        kb = InlineKeyboardMarkup([btns])
        await q.edit_message_text(result["text"], parse_mode="Markdown", reply_markup=kb)

# ═══════════════════════════════════════════════════════════════
# РАССЫЛКА
# ═══════════════════════════════════════════════════════════════
def load_chat_ids() -> set:
    try:
        with open("chat_ids.txt") as f:
            return set(int(l.strip()) for l in f if l.strip())
    except:
        return set()

async def send_scheduled(bot: Bot):
    chat_ids = load_chat_ids() | user_chat_ids
    if not chat_ids:
        return
    now = datetime.now(TZ)
    log.info(f"Рассылка {now.strftime('%H:%M')} Istanbul")
    parts = build_market_report() + build_signals_report()
    for cid in chat_ids:
        try:
            await send_parts(bot, cid, parts)
        except Exception as e:
            log.error(f"Ошибка {cid}: {e}")

# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("coin",  cmd_coin))
    app.add_handler(CommandHandler("top",   cmd_top))
    app.add_handler(CallbackQueryHandler(callback_handler))

    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(
        lambda: asyncio.create_task(send_scheduled(app.bot)),
        "interval", minutes=30
    )
    scheduler.start()
    log.info("✅ Crypto Monitor v2.1 | Istanbul UTC+3")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
