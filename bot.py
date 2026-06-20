#!/usr/bin/env python3
"""
📊 BEST TRADE Bot v5.0
Стиль: картинка графиков (4ч+15м) + зоны набора + RSI по таймфреймам
Топ-300 CoinMarketCap | Стамбул UTC+3
"""

import asyncio
import io
import logging
import os
import requests
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from datetime import datetime, timedelta
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

# ═══════════════════════════════════════════
BOT_TOKEN   = os.getenv("BOT_TOKEN")
CMC_API_KEY = os.getenv("CMC_API_KEY", "7c581d74b60d4c40879edc0431b5e53a")
TZ          = pytz.timezone("Europe/Istanbul")

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ═══════════════════════════════════════════
# ЦВЕТА
# ═══════════════════════════════════════════
BG      = "#0D1421"
PANEL   = "#131C2E"
GREEN   = "#16C784"
RED     = "#EA3943"
GOLD    = "#FFD700"
BLUE    = "#3861FB"
WHITE   = "#FFFFFF"
GRAY    = "#7B8BB2"
YELLOW  = "#F0B90B"
ORANGE  = "#F3841E"

# ═══════════════════════════════════════════
# CMC API
# ═══════════════════════════════════════════
def get_top300():
    try:
        url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
        headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
        params  = {"limit": 300, "convert": "USDT", "sort": "market_cap"}
        r = requests.get(url, headers=headers, params=params, timeout=15)
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception as e:
        log.error(f"CMC error: {e}")
        return []

def get_coingecko_ohlc(slug: str, days: int = 7) -> list:
    """Получить OHLC данные с CoinGecko"""
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{slug}/ohlc"
        params = {"vs_currency": "usd", "days": str(days)}
        r = requests.get(url, params=params, timeout=12)
        r.raise_for_status()
        data = r.json()
        candles = []
        for d in data:
            candles.append({
                "time":  datetime.fromtimestamp(d[0]/1000, tz=TZ),
                "open":  d[1], "high": d[2],
                "low":   d[3], "close": d[4],
            })
        return candles
    except Exception as e:
        log.error(f"CoinGecko OHLC error {slug}: {e}")
        return []

def get_coingecko_prices(slug: str, days: int = 1) -> list:
    """Получить почасовые цены с CoinGecko"""
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{slug}/market_chart"
        params = {"vs_currency": "usd", "days": str(days), "interval": "hourly"}
        r = requests.get(url, params=params, timeout=12)
        r.raise_for_status()
        prices = r.json().get("prices", [])
        return [(datetime.fromtimestamp(p[0]/1000, tz=TZ), p[1]) for p in prices]
    except Exception as e:
        log.error(f"CoinGecko prices error {slug}: {e}")
        return []

def cmc_link(slug: str)  -> str: return f"https://coinmarketcap.com/currencies/{slug}/"
def tv_link(symbol: str) -> str: return f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}USDT"

# ═══════════════════════════════════════════
# ФОРМАТИРОВАНИЕ
# ═══════════════════════════════════════════
def fp(p: float) -> str:
    if p >= 1000:  return f"{p:,.2f}"
    if p >= 1:     return f"{p:.4f}"
    if p >= 0.01:  return f"{p:.5f}"
    return f"{p:.8f}"

def fc(ch: float) -> str:
    return f"+{ch:.2f}%" if ch >= 0 else f"{ch:.2f}%"

def fm(m: float) -> str:
    if m >= 1e9: return f"${m/1e9:.2f}B"
    if m >= 1e6: return f"${m/1e6:.2f}M"
    return f"${m:.0f}"

def fv(v: float) -> str:
    if v >= 1e9: return f"${v/1e9:.2f}B"
    if v >= 1e6: return f"${v/1e6:.2f}M"
    return f"${v:.0f}"

# ═══════════════════════════════════════════
# ТЕХНИЧЕСКИЙ АНАЛИЗ
# ═══════════════════════════════════════════
def calc_ema(prices: list, period: int) -> list:
    if len(prices) < period:
        return [None] * len(prices)
    emas = [None] * (period - 1)
    sma  = sum(prices[:period]) / period
    emas.append(sma)
    k = 2 / (period + 1)
    for p in prices[period:]:
        emas.append(p * k + emas[-1] * (1 - k))
    return emas

def calc_rsi(prices: list, period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains  = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)

def full_analysis(coin: dict) -> dict:
    q     = coin["quote"]["USDT"]
    ch1h  = q.get("percent_change_1h",  0) or 0
    ch24h = q.get("percent_change_24h", 0) or 0
    ch7d  = q.get("percent_change_7d",  0) or 0
    ch30d = q.get("percent_change_30d", 0) or 0
    vol   = q.get("volume_24h", 0) or 0
    mcap  = q.get("market_cap", 0) or 0
    price = q.get("price",      0) or 0
    vol_ratio = (vol / mcap * 100) if mcap > 0 else 0

    # EMA позиция (оценочная)
    ema20  = ch7d > 0
    ema50  = ch30d > -10
    ema200 = ch30d > 0

    # RSI оценочный по таймфреймам
    m15  = ch1h * 0.25
    m1h  = ch1h
    m4h  = ch1h * 0.5 + ch24h * 0.5
    m1d  = ch24h

    def rsi_est(m):
        if m > 15:  return 82.0
        if m > 8:   return 70.0
        if m > 3:   return 60.0
        if m > 0:   return 52.0
        if m > -3:  return 45.0
        if m > -8:  return 35.0
        if m > -15: return 25.0
        return 18.0

    rsi_15m = rsi_est(m15)
    rsi_1h  = rsi_est(m1h)
    rsi_4h  = rsi_est(m4h)
    rsi_1d  = rsi_est(m1d)

    # Скоринг
    score = 0
    if ema200 and ema50 and ema20: score += 3
    elif ema50 and ema20:          score += 2
    elif ema20:                    score += 1
    elif not ema50 and not ema200: score -= 2

    if rsi_4h < 30:  score += 3
    elif rsi_4h < 40: score += 2
    elif rsi_4h > 70: score -= 2
    elif rsi_4h > 80: score -= 3

    if ch24h >= 10:   score += 2
    elif ch24h >= 4:  score += 1
    elif ch24h <= -10: score -= 2
    elif ch24h <= -4:  score -= 1

    if ch1h >= 3:    score += 1
    elif ch1h <= -3: score -= 1

    if vol_ratio >= 20: score += 2
    elif vol_ratio >= 10: score += 1

    is_long = score >= 0

    # Зоны набора (3 уровня)
    atr = max(abs(ch24h) / 100, 0.03) * price
    if is_long:
        zone1_lo = round(price * 0.97, 8);  zone1_hi = round(price * 1.00, 8)
        zone2_lo = round(price * 0.93, 8);  zone2_hi = round(price * 0.97, 8)
        zone3_lo = round(price * 0.88, 8);  zone3_hi = round(price * 0.93, 8)
        stop     = round(price * 0.85, 8)
        tp1      = round(price * 1.04, 8)
        tp2      = round(price * 1.06, 8)
        tp3      = round(price * 1.10, 8)
    else:
        zone1_lo = round(price * 1.00, 8);  zone1_hi = round(price * 1.03, 8)
        zone2_lo = round(price * 1.03, 8);  zone2_hi = round(price * 1.07, 8)
        zone3_lo = round(price * 1.07, 8);  zone3_hi = round(price * 1.12, 8)
        stop     = round(price * 1.15, 8)
        tp1      = round(price * 0.96, 8)
        tp2      = round(price * 0.94, 8)
        tp3      = round(price * 0.90, 8)

    if score >= 5:    label = "🔥 СИЛЬНЫЙ ЛОНГ"; action = "ПОКУПАТЬ"
    elif score >= 3:  label = "✅ ЛОНГ";          action = "ИСКАТЬ ВХОД"
    elif score >= 1:  label = "📈 СЛАБЫЙ ЛОНГ";  action = "НАБЛЮДАТЬ"
    elif score >= -1: label = "⚪️ НЕЙТРАЛЬНО";   action = "В СТОРОНЕ"
    elif score >= -3: label = "📉 СЛАБЫЙ ШОРТ";  action = "ОСТОРОЖНО"
    elif score >= -5: label = "🔻 ШОРТ";          action = "ШОРТИТЬ"
    else:             label = "💥 СИЛЬНЫЙ ШОРТ"; action = "АКТИВНЫЙ ШОРТ"

    # Определяем зону монеты
    if rsi_4h < 30:
        zone_status = "в зоне перепроданности 🟢"
    elif rsi_4h > 70:
        zone_status = "в зоне перекупленности 🔴"
    elif score >= 3:
        zone_status = "в 1-й зоне набора 🟡"
    elif score >= 1:
        zone_status = "во 2-й зоне набора 🟡"
    else:
        zone_status = "вне зон набора ⚪️"

    return {
        "label": label, "action": action, "score": score, "is_long": is_long,
        "zone_status": zone_status,
        "ema20": ema20, "ema50": ema50, "ema200": ema200,
        "rsi_15m": rsi_15m, "rsi_1h": rsi_1h, "rsi_4h": rsi_4h, "rsi_1d": rsi_1d,
        "ch1h": ch1h, "ch24h": ch24h, "ch7d": ch7d, "ch30d": ch30d,
        "vol_ratio": vol_ratio, "vol": vol, "price": price, "mcap": mcap,
        "zone1": f"`{fp(zone1_lo)}` – `{fp(zone1_hi)}`",
        "zone2": f"`{fp(zone2_lo)}` – `{fp(zone2_hi)}`",
        "zone3": f"`{fp(zone3_lo)}` – `{fp(zone3_hi)}`",
        "stop": fp(stop), "tp1": fp(tp1), "tp2": fp(tp2), "tp3": fp(tp3),
    }

# ═══════════════════════════════════════════
# ГЕНЕРАЦИЯ ГРАФИКА (4ч + 15м рядом)
# ═══════════════════════════════════════════
def generate_chart(symbol: str, slug: str, a: dict) -> io.BytesIO:
    """Один большой свечной график в стиле Kriptano"""

    candles = get_coingecko_ohlc(slug, days=7)

    fig, ax = plt.subplots(figsize=(12, 6), facecolor=BG)
    ax.set_facecolor(BG)

    if candles and len(candles) >= 5:
        closes = [c["close"] for c in candles]
        n = len(candles)

        # EMA линии
        ema4  = calc_ema(closes, min(4,  len(closes)))
        ema7  = calc_ema(closes, min(7,  len(closes)))
        ema14 = calc_ema(closes, min(14, len(closes)))
        ema28 = calc_ema(closes, min(28, len(closes)))

        # Свечи
        w = 0.4
        for i, c in enumerate(candles):
            color = GREEN if c["close"] >= c["open"] else RED
            ax.plot([i, i], [c["low"], c["high"]], color=color, lw=0.8, zorder=2)
            ax.add_patch(plt.Rectangle(
                (i - w/2, min(c["open"], c["close"])),
                w, abs(c["close"] - c["open"]),
                color=color, zorder=3
            ))

        # EMA линии
        colors_ema = [YELLOW, ORANGE, "#FF6B6B", "#4ECDC4"]
        labels_ema = ["EMA4", "EMA7", "EMA14", "EMA28"]
        for ema_vals, col, lbl in zip([ema4, ema7, ema14, ema28], colors_ema, labels_ema):
            valid = [(i, v) for i, v in enumerate(ema_vals) if v is not None]
            if valid:
                ax.plot([x[0] for x in valid], [x[1] for x in valid],
                        color=col, lw=1.2, alpha=0.9, label=lbl, zorder=4)

        # Уровни входа/ТП/Стоп
        price = closes[-1]
        ext   = n * 0.18

        def draw_level(val_str, color, label, ls="-", lw=1.2):
            try:
                val = float(val_str.replace(",", "").replace("$", "").strip())
                ax.axhline(y=val, color=color, linestyle=ls,
                           linewidth=lw, alpha=0.85, zorder=5)
                ax.text(n - 1 + ext * 0.2, val,
                        f" {label}: {val_str}",
                        color=color, fontsize=7.5,
                        va="center", fontweight="bold", zorder=6)
            except:
                pass

        draw_level(a["tp3"],  "#009999", "TP3", "--", 1.0)
        draw_level(a["tp2"],  "#00BBAA", "TP2", "--", 1.0)
        draw_level(a["tp1"],  "#00DDAA", "TP1", "--", 1.0)
        draw_level(fp(a["swing"]), BLUE,  "Swing", ":", 1.0)
        draw_level(fp(a["price"]), WHITE, "Entry", "-", 1.8)
        draw_level(a["stop"],  RED,      "SL",    "--", 1.2)

        # Зона стопа
        try:
            entry_val = a["price"]
            stop_val  = float(a["stop"].replace(",","").replace("$","").strip())
            tp1_val   = float(a["tp1"].replace(",","").replace("$","").strip())
            ax.axhspan(entry_val, stop_val, alpha=0.08, color=RED,   zorder=1)
            ax.axhspan(entry_val, tp1_val,  alpha=0.06, color=GREEN, zorder=1)
        except:
            pass

        # Легенда EMA
        ax.legend(loc="upper left", fontsize=7.5,
                  facecolor="#1A2332", edgecolor=GRAY,
                  labelcolor=WHITE, framealpha=0.85)

        # X метки
        step = max(n // 7, 1)
        ticks = list(range(0, n, step))
        ax.set_xticks(ticks)
        ax.set_xticklabels(
            [candles[i]["time"].strftime("%d.%m\n%H:%M") for i in ticks],
            fontsize=7, color=GRAY
        )
        ax.set_xlim(-1, n + ext)

    else:
        ax.text(0.5, 0.5, f"Нет данных для {symbol}",
                ha="center", va="center", color=GRAY,
                fontsize=14, transform=ax.transAxes)

    # Заголовок
    side = "LONG" if a["is_long"] else "SHORT"
    side_color = GREEN if a["is_long"] else RED
    ax.set_title(
        f"{symbol}USDT  •  1h  •  {side}",
        color=side_color, fontsize=13, fontweight="bold", pad=10
    )

    # Стиль
    ax.grid(color="#1A2332", lw=0.4, zorder=0)
    ax.tick_params(colors=GRAY, labelsize=7)
    ax.spines[:].set_color("#1E2A3A")

    # Водяной знак
    fig.text(0.5, 0.5, "BEST TRADE",
             fontsize=55, color="white", alpha=0.04,
             ha="center", va="center",
             fontweight="bold", rotation=20, zorder=0)

    plt.tight_layout(pad=0.5)
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130,
                bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    buf.seek(0)
    return buf


# ═══════════════════════════════════════════
# ТЕКСТ СИГНАЛА (стиль примера)
# ═══════════════════════════════════════════
def build_signal_text(symbol: str, coin: dict, a: dict) -> str:
    """Формат 1в1 как Kriptano"""
    side_emoji = "🟢" if a["is_long"] else "🔴"
    side_text  = "LONG" if a["is_long"] else "SHORT"

    # Рассчитываем % от точки входа
    price = a["price"]
    def pct_from_entry(target_str):
        try:
            target = float(str(target_str).replace(",","").replace("$","").strip())
            if a["is_long"]:
                return f"+{(target - price) / price * 100:.2f}%"
            else:
                return f"+{(price - target) / price * 100:.2f}%"
        except:
            return ""

    def sl_pct(stop_str):
        try:
            stop = float(str(stop_str).replace(",","").replace("$","").strip())
            if a["is_long"]:
                return f"{(stop - price) / price * 100:.2f}%"
            else:
                return f"{(price - stop) / price * 100:.2f}%"
        except:
            return ""

    swing_label = "Swing High" if not a["is_long"] else "Swing Low"

    lines = [
        f"📊 *{symbol}USDT* {side_emoji} *{side_text}*",
        "",
        f"💰 *Точка входа:* {fp(price)}",
        f"🎯 *Тейк-профит 1:* {a['tp1']}",
        f"({pct_from_entry(a['tp1'])})",
        f"🎯 *Тейк-профит 2:* {a['tp2']}",
        f"({pct_from_entry(a['tp2'])})",
        f"🎯 *Тейк-профит 3:* {a['tp3']}",
        f"({pct_from_entry(a['tp3'])})",
        f"🔴 *Стоп лосс:* {a['stop']}",
        f"({sl_pct(a['stop'])})",
        f"📌 *{swing_label}:* {a['swing']}",
    ]
    return "\n".join(lines)


# ═══════════════════════════════════════════
# СВОДКА РЫНКА
# ═══════════════════════════════════════════
def build_market_report(coins: list) -> list:
    now = datetime.now(TZ)
    up  = sorted(coins, key=lambda x: x["quote"]["USDT"].get("percent_change_24h", 0), reverse=True)
    dn  = sorted(coins, key=lambda x: x["quote"]["USDT"].get("percent_change_24h", 0))
    pos = sum(1 for c in coins if c["quote"]["USDT"].get("percent_change_24h", 0) > 0)
    pct = pos / len(coins) * 100
    mood = "🟢 Бычий" if pct >= 60 else ("🔴 Медвежий" if pct < 40 else "🟡 Нейтральный")
    now_str = now.strftime("%d.%m.%Y  %H:%M")
    sent_str = now.strftime("%d.%m %H:%M:%S")

    def arr(ch): return "🟢" if ch >= 5 else ("🔵" if ch >= 0 else ("🟠" if ch >= -5 else "🔴"))
    def ps(ch): return ("+" if ch >= 0 else "") + f"{ch:.2f}%"

    lines1 = [
        "🔥 *Обзор рынка*",
        "📊 BEST TRADE  •  " + now_str + " Istanbul",
        "",
        "Сентимент: " + mood,
        "Растут: " + str(pos) + " из " + str(len(coins)) + " монет (" + f"{pct:.0f}" + "%)",
        "",
        "🚀 *ТОП-15 РОСТ за 24ч*",
    ]
    b1 = []
    for i, c in enumerate(up[:15], 1):
        q  = c["quote"]["USDT"]
        ch = q.get("percent_change_24h", 0)
        sym = c["symbol"]
        price = fp(q["price"])
        pct_s = ps(ch)
        icon = arr(ch)
        lines1.append(icon + " " + str(i) + ". *" + sym + "*")
        lines1.append("    💰 $" + price + "  " + pct_s)
    for c in up[:8]:
        b1.append(InlineKeyboardButton(
            "📊 " + c["symbol"],
            url=cmc_link(c.get("slug", c["symbol"].lower()))
        ))

    lines2 = ["📉 *ТОП-15 ПАДЕНИЕ за 24ч*"]
    b2 = []
    for i, c in enumerate(dn[:15], 1):
        q  = c["quote"]["USDT"]
        ch = q.get("percent_change_24h", 0)
        sym = c["symbol"]
        price = fp(q["price"])
        pct_s = ps(ch)
        icon = arr(ch)
        lines2.append(icon + " " + str(i) + ". *" + sym + "*")
        lines2.append("    💰 $" + price + "  " + pct_s)
    lines2.extend([
        "",
        "🕐 Отправлено: " + sent_str + " UTC+3",
        "📡 CoinMarketCap • Топ-300 монет",
    ])
    for c in dn[:8]:
        b2.append(InlineKeyboardButton(
            "📊 " + c["symbol"],
            url=cmc_link(c.get("slug", c["symbol"].lower()))
        ))

    return [
        {"text": "\n".join(lines1), "btns": b1},
        {"text": "\n".join(lines2), "btns": b2},
    ]


def build_signals_report(coins: list) -> list:
    """Возвращает список сообщений — каждая монета отдельно с графиком"""
    now      = datetime.now(TZ)
    analyzed = [(c, full_analysis(c)) for c in coins]
    longs    = sorted([(c,a) for c,a in analyzed if a["score"] >= 3],
                      key=lambda x: x[1]["score"], reverse=True)[:8]
    shorts   = sorted([(c,a) for c,a in analyzed if a["score"] <= -3],
                      key=lambda x: x[1]["score"])[:5]

    results = []

    # Заголовок
    now_str = now.strftime("%d.%m.%Y  %H:%M")
    header = (
        f"🤖 *BEST TRADE — Сигналы*\n"
        f"🕐 {now_str} Istanbul\n\n"
        f"🟢 Лонг: {len(longs)} монет  |  🔴 Шорт: {len(shorts)} монет"
    )
    results.append({"type": "text", "text": header, "btns": []})

    # Каждая монета отдельно
    for c, a in longs + shorts:
        symbol = c["symbol"]
        slug   = c.get("slug", symbol.lower())
        text   = build_signal_text(symbol, c, a)
        kb = [
            InlineKeyboardButton("📈 Открыть график на TradingView", url=tv_link(symbol)),
        ]
        results.append({
            "type": "coin",
            "symbol": symbol,
            "slug": slug,
            "text": text,
            "btns": kb,
            "analysis": a,
        })

    # Подвал
    footer = "⚠️ Риск на сделку: *2-3%*\nСтоп *ВСЕГДА* выставляй до входа!"
    results.append({"type": "text", "text": footer, "btns": []})

    return results


def build_period_report(period: str, coins: list) -> list:
    field_map = {"1h": "percent_change_1h", "24h": "percent_change_24h", "7d": "percent_change_7d"}
    label_map = {"1h": "1 ЧАС", "24h": "24 ЧАСА", "7d": "7 ДНЕЙ"}
    field = field_map.get(period, "percent_change_24h")
    label = label_map.get(period, "24 ЧАСА")
    now   = datetime.now(TZ)

    def trend_icon(ch):
        if ch >= 5:  return "🟢"
        if ch >= 0:  return "🔵"
        if ch >= -5: return "🟠"
        return "🔴"
    def fc2(ch): return f"+{ch:.2f}%" if ch >= 0 else f"{ch:.2f}%"

    up = sorted(coins, key=lambda x: x["quote"]["USDT"].get(field, 0), reverse=True)
    dn = sorted(coins, key=lambda x: x["quote"]["USDT"].get(field, 0))

    t1  = f"📊 BEST TRADE — ТОП за {label}\n"
    t1 += f"🕐 {now.strftime('%H:%M')} Istanbul\n\n"
    t1 += "🚀 ЛИДЕРЫ РОСТА\n" + "─"*28 + "\n"
    b1 = []
    for i, c in enumerate(up[:15], 1):
        ch = c["quote"]["USDT"].get(field, 0)
        p  = c["quote"]["USDT"].get("price", 0)
        t1 += f"{i:>2}. {c['symbol']:<8} ${fp(p):<14} {trend_icon(ch)} {fc2(ch)}\n"
    for c in up[:8]:
        b1.append(InlineKeyboardButton(
            f"📊 {c['symbol']}",
            url=cmc_link(c.get("slug", c["symbol"].lower()))
        ))

    t2  = "📉 ЛИДЕРЫ ПАДЕНИЯ\n" + "─"*28 + "\n"
    b2 = []
    for i, c in enumerate(dn[:15], 1):
        ch = c["quote"]["USDT"].get(field, 0)
        p  = c["quote"]["USDT"].get("price", 0)
        t2 += f"{i:>2}. {c['symbol']:<8} ${fp(p):<14} {trend_icon(ch)} {fc2(ch)}\n"
    for c in dn[:8]:
        b2.append(InlineKeyboardButton(
            f"📊 {c['symbol']}",
            url=cmc_link(c.get("slug", c["symbol"].lower()))
        ))

    return [
        {"text": f"{t1}", "btns": b1},
        {"text": f"{t2}", "btns": b2},
    ]

# ═══════════════════════════════════════════
# КЛАВИАТУРЫ
# ═══════════════════════════════════════════
def main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Рынок",    callback_data="report"),
         InlineKeyboardButton("🤖 Сигналы", callback_data="signals")],
        [InlineKeyboardButton("⏱ 1ч",  callback_data="period_1h"),
         InlineKeyboardButton("📅 24ч", callback_data="period_24h"),
         InlineKeyboardButton("📆 7д",  callback_data="period_7d")],
    ])

async def send_parts(bot, chat_id, parts, query=None):
    for i, part in enumerate(parts):
        text    = part["text"]
        btns    = part.get("btns", [])
        is_last = (i == len(parts) - 1)
        rows    = [btns[j:j+4] for j in range(0, len(btns), 4)]
        if is_last:
            rows.append([
                InlineKeyboardButton("⏱ 1ч",  callback_data="period_1h"),
                InlineKeyboardButton("📅 24ч", callback_data="period_24h"),
                InlineKeyboardButton("📆 7д",  callback_data="period_7d"),
            ])
            rows.append([
                InlineKeyboardButton("📊 Рынок",    callback_data="report"),
                InlineKeyboardButton("🤖 Сигналы", callback_data="signals"),
            ])
        kb = InlineKeyboardMarkup(rows) if rows else None
        if query and i == 0:
            await query.edit_message_text(
                text, parse_mode="Markdown",
                reply_markup=kb, disable_web_page_preview=True
            )
        else:
            await bot.send_message(
                chat_id, text, parse_mode="Markdown",
                reply_markup=kb, disable_web_page_preview=True
            )

# ═══════════════════════════════════════════
# HANDLERS
# ═══════════════════════════════════════════
user_chat_ids = set()

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_chat_ids.add(chat_id)
    with open("chat_ids.txt", "a") as f:
        f.write(f"{chat_id}\n")
    await update.message.reply_text(
        "📊 *BEST TRADE*\n\n"
        "Топ-300 • CoinMarketCap\n"
        "Графики 4ч + 15м • EMA • RSI\n"
        "Зоны набора • Рассылка каждые 30 мин\n"
        "🕐 Стамбул UTC+3\n\n"
        "Команды:\n"
        "/coin BTC — графики + анализ\n"
        "/top — топ рынка\n"
        "/signals — сигналы",
        parse_mode="Markdown",
        reply_markup=main_kb()
    )

async def cmd_coin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Напиши: `/coin BTC`", parse_mode="Markdown")
        return
    symbol = ctx.args[0].upper()
    msg    = await update.message.reply_text(f"⏳ Анализирую {symbol}...")
    coins  = get_top300()
    coin   = next((c for c in coins if c["symbol"] == symbol), None)
    if not coin:
        await msg.edit_text(f"❌ {symbol} не найден в топ-300")
        return

    a    = full_analysis(coin)
    slug = coin.get("slug", symbol.lower())

    # Генерируем график
    try:
        chart = generate_chart(symbol, slug, a)
        text  = build_signal_text(symbol, coin, a)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("📈 Открыть график на TradingView", url=tv_link(symbol)),
        ],[
            InlineKeyboardButton("🔄 Обновить", callback_data=f"coin_{symbol}"),
            InlineKeyboardButton("📈 CMC",      url=cmc_link(slug)),
        ]])
        await msg.delete()
        await update.message.reply_photo(
            photo=chart, caption=text,
            parse_mode="Markdown", reply_markup=kb
        )
    except Exception as e:
        log.error(f"Chart error: {e}")
        text = build_signal_text(symbol, coin, a)
        kb   = InlineKeyboardMarkup([[
            InlineKeyboardButton("📈 CoinMarketCap", url=cmc_link(slug)),
            InlineKeyboardButton("📊 TradingView",   url=tv_link(symbol)),
            InlineKeyboardButton("🔄 Обновить",      callback_data=f"coin_{symbol}"),
        ]])
        await msg.edit_text(text, parse_mode="Markdown",
                            reply_markup=kb, disable_web_page_preview=True)

async def cmd_top(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Загружаю данные...")
    coins = get_top300()
    if not coins:
        await msg.edit_text("❌ Нет данных")
        return
    await msg.delete()
    await send_parts(ctx.bot, update.effective_chat.id, build_market_report(coins))

async def send_signals(bot, chat_id: int, coins: list):
    """Отправляем каждый сигнал отдельно с графиком"""
    parts = build_signals_report(coins)
    nav_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📊 Рынок",    callback_data="report"),
        InlineKeyboardButton("🤖 Сигналы", callback_data="signals"),
    ]])

    for part in parts:
        try:
            if part["type"] == "text":
                rows = [part["btns"][i:i+2] for i in range(0, len(part["btns"]), 2)] if part["btns"] else []
                rows.append([
                    InlineKeyboardButton("📊 Рынок",    callback_data="report"),
                    InlineKeyboardButton("🤖 Сигналы", callback_data="signals"),
                ])
                await bot.send_message(
                    chat_id, part["text"],
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(rows)
                )
            elif part["type"] == "coin":
                symbol = part["symbol"]
                slug   = part["slug"]
                a      = part["analysis"]
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("📈 Открыть график на TradingView", url=tv_link(symbol)),
                ],[
                    InlineKeyboardButton("🔄 Обновить", callback_data=f"coin_{symbol}"),
                    InlineKeyboardButton("📈 CMC",      url=cmc_link(slug)),
                ]])
                try:
                    chart = generate_chart(symbol, slug, a)
                    await bot.send_photo(
                        chat_id=chat_id,
                        photo=chart,
                        caption=part["text"],
                        parse_mode="Markdown",
                        reply_markup=kb
                    )
                except Exception as e:
                    log.error(f"Chart error {symbol}: {e}")
                    await bot.send_message(
                        chat_id, part["text"],
                        parse_mode="Markdown",
                        reply_markup=kb,
                        disable_web_page_preview=True
                    )
        except Exception as e:
            log.error(f"Send error: {e}")

async def cmd_signals(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Анализирую топ-300... Это займёт ~30 секунд")
    coins = get_top300()
    if not coins:
        await msg.edit_text("❌ Нет данных")
        return
    await msg.delete()
    await send_signals(ctx.bot, update.effective_chat.id, coins)

async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    data = q.data
    await q.answer()

    if data in ("report", "signals") or data.startswith("period_"):
        await q.edit_message_text("⏳ Загружаю...", parse_mode="Markdown")
        coins = get_top300()
        if not coins:
            await q.edit_message_text("❌ Нет данных")
            return
        if data == "report":
            parts = build_market_report(coins)
        elif data == "signals":
            parts = build_signals_report(coins)
        else:
            parts = build_period_report(data.split("_")[1], coins)
        await send_parts(ctx.bot, q.message.chat_id, parts, query=q)

    elif data.startswith("coin_"):
        symbol  = data[5:]
        chat_id = q.message.chat_id
        await q.edit_message_text(f"⏳ Обновляю {symbol}...")
        coins = get_top300()
        coin  = next((c for c in coins if c["symbol"] == symbol), None)
        if not coin:
            await q.edit_message_text(f"❌ {symbol} не найден")
            return
        a    = full_analysis(coin)
        slug = coin.get("slug", symbol.lower())
        try:
            chart = generate_chart(symbol, slug, a)
            text  = build_signal_text(symbol, coin, a)
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("📈 CoinMarketCap", url=cmc_link(slug)),
                InlineKeyboardButton("📊 TradingView",   url=tv_link(symbol)),
            ],[
                InlineKeyboardButton("🔄 Обновить", callback_data=f"coin_{symbol}"),
                InlineKeyboardButton("◀️ Назад",     callback_data="report"),
            ]])
            await q.message.delete()
            await ctx.bot.send_photo(
                chat_id=chat_id, photo=chart,
                caption=text, parse_mode="Markdown", reply_markup=kb
            )
        except Exception as e:
            log.error(f"Chart error: {e}")
            text = build_signal_text(symbol, coin, a)
            kb   = InlineKeyboardMarkup([[
                InlineKeyboardButton("📈 CoinMarketCap", url=cmc_link(slug)),
                InlineKeyboardButton("📊 TradingView",   url=tv_link(symbol)),
                InlineKeyboardButton("🔄 Обновить",      callback_data=f"coin_{symbol}"),
            ]])
            await ctx.bot.send_message(
                chat_id, text, parse_mode="Markdown",
                reply_markup=kb, disable_web_page_preview=True
            )

# ═══════════════════════════════════════════
# РАССЫЛКА
# ═══════════════════════════════════════════
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
    log.info(f"Рассылка {datetime.now(TZ).strftime('%H:%M')} Istanbul")
    coins = get_top300()
    if not coins:
        return
    for cid in chat_ids:
        try:
            await send_parts(bot, cid, build_market_report(coins))
            await send_signals(bot, cid, coins)
        except Exception as e:
            log.error(f"Ошибка {cid}: {e}")

# ═══════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("coin",    cmd_coin))
    app.add_handler(CommandHandler("top",     cmd_top))
    app.add_handler(CommandHandler("signals", cmd_signals))
    app.add_handler(CallbackQueryHandler(callback_handler))

    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(
        lambda: asyncio.create_task(send_scheduled(app.bot)),
        "interval", minutes=30
    )
    scheduler.start()
    log.info("✅ BEST TRADE v5.0 | Istanbul UTC+3")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
