#!/usr/bin/env python3
"""
📊 BEST TRADE Bot v6.0
+ Модуль рыночного обзора каждые 30 мин:
  BTC цена/зоны, ETH, BTC.D, TOTAL капа, OTHERS.D
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

def get_global_metrics() -> dict:
    """Глобальные метрики рынка: Total MarketCap, BTC.D, ETH.D, Others"""
    try:
        url = "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest"
        headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        d = r.json().get("data", {})
        q = d.get("quote", {}).get("USD", {})
        return {
            "total_mcap":       q.get("total_market_cap", 0),
            "total_vol_24h":    q.get("total_volume_24h", 0),
            "btc_dominance":    d.get("btc_dominance", 0),
            "eth_dominance":    d.get("eth_dominance", 0),
            "mcap_change_24h":  q.get("total_market_cap_yesterday_percentage_change", 0),
            "active_coins":     d.get("active_cryptocurrencies", 0),
        }
    except Exception as e:
        log.error(f"Global metrics error: {e}")
        return {}

def get_btc_price() -> dict:
    """Быстрый запрос цены BTC + ETH"""
    try:
        url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
        headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
        params  = {"symbol": "BTC,ETH", "convert": "USD"}
        r = requests.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        data = r.json().get("data", {})
        result = {}
        for sym in ["BTC", "ETH"]:
            if sym in data:
                q = data[sym][0]["quote"]["USD"] if isinstance(data[sym], list) else data[sym]["quote"]["USD"]
                result[sym] = {
                    "price":   q.get("price", 0),
                    "ch1h":    q.get("percent_change_1h", 0),
                    "ch24h":   q.get("percent_change_24h", 0),
                    "ch7d":    q.get("percent_change_7d", 0),
                    "vol24h":  q.get("volume_24h", 0),
                    "mcap":    q.get("market_cap", 0),
                }
        return result
    except Exception as e:
        log.error(f"BTC/ETH price error: {e}")
        return {}

def get_coingecko_ohlc(slug: str, days: int = 7) -> list:
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
    if m >= 1e12: return f"${m/1e12:.2f}T"
    if m >= 1e9:  return f"${m/1e9:.2f}B"
    if m >= 1e6:  return f"${m/1e6:.2f}M"
    return f"${m:.0f}"

def fv(v: float) -> str:
    if v >= 1e9: return f"${v/1e9:.2f}B"
    if v >= 1e6: return f"${v/1e6:.2f}M"
    return f"${v:.0f}"

def trend_arrow(ch: float) -> str:
    if ch >= 3:   return "🟢"
    if ch >= 0:   return "🔵"
    if ch >= -3:  return "🟠"
    return "🔴"

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

    ema20  = ch7d > 0
    ema50  = ch30d > -10
    ema200 = ch30d > 0

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

    score = 0
    if ema200 and ema50 and ema20: score += 3
    elif ema50 and ema20:          score += 2
    elif ema20:                    score += 1
    elif not ema50 and not ema200: score -= 2

    if rsi_4h < 30:   score += 3
    elif rsi_4h < 40: score += 2
    elif rsi_4h > 70: score -= 2
    elif rsi_4h > 80: score -= 3

    if ch24h >= 10:    score += 2
    elif ch24h >= 4:   score += 1
    elif ch24h <= -10: score -= 2
    elif ch24h <= -4:  score -= 1

    if ch1h >= 3:    score += 1
    elif ch1h <= -3: score -= 1

    if vol_ratio >= 20: score += 2
    elif vol_ratio >= 10: score += 1

    is_long = score >= 0

    if is_long:
        zone1_lo = round(price * 0.97, 8);  zone1_hi = round(price * 1.00, 8)
        zone2_lo = round(price * 0.93, 8);  zone2_hi = round(price * 0.97, 8)
        zone3_lo = round(price * 0.88, 8);  zone3_hi = round(price * 0.93, 8)
        stop     = round(price * 0.85, 8)
        tp1      = round(price * 1.04, 8)
        tp2      = round(price * 1.06, 8)
        tp3      = round(price * 1.10, 8)
        swing    = round(price * 0.92, 8)
    else:
        zone1_lo = round(price * 1.00, 8);  zone1_hi = round(price * 1.03, 8)
        zone2_lo = round(price * 1.03, 8);  zone2_hi = round(price * 1.07, 8)
        zone3_lo = round(price * 1.07, 8);  zone3_hi = round(price * 1.12, 8)
        stop     = round(price * 1.15, 8)
        tp1      = round(price * 0.96, 8)
        tp2      = round(price * 0.94, 8)
        tp3      = round(price * 0.90, 8)
        swing    = round(price * 1.08, 8)

    if score >= 5:    label = "🔥 СИЛЬНЫЙ ЛОНГ"; action = "ПОКУПАТЬ"
    elif score >= 3:  label = "✅ ЛОНГ";          action = "ИСКАТЬ ВХОД"
    elif score >= 1:  label = "📈 СЛАБЫЙ ЛОНГ";  action = "НАБЛЮДАТЬ"
    elif score >= -1: label = "⚪️ НЕЙТРАЛЬНО";   action = "В СТОРОНЕ"
    elif score >= -3: label = "📉 СЛАБЫЙ ШОРТ";  action = "ОСТОРОЖНО"
    elif score >= -5: label = "🔻 ШОРТ";          action = "ШОРТИТЬ"
    else:             label = "💥 СИЛЬНЫЙ ШОРТ"; action = "АКТИВНЫЙ ШОРТ"

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
        "swing": swing,
        "zone1": f"`{fp(zone1_lo)}` – `{fp(zone1_hi)}`",
        "zone2": f"`{fp(zone2_lo)}` – `{fp(zone2_hi)}`",
        "zone3": f"`{fp(zone3_lo)}` – `{fp(zone3_hi)}`",
        "stop": fp(stop), "tp1": fp(tp1), "tp2": fp(tp2), "tp3": fp(tp3),
    }

# ═══════════════════════════════════════════
# РЫНОЧНЫЙ ОБЗОР (НОВЫЙ МОДУЛЬ)
# ═══════════════════════════════════════════

# Зоны поддержки/сопротивления BTC (обновляй вручную при необходимости)
BTC_ZONES = {
    "support": [
        {"level": 62137, "label": "S1 — Зона Королева (верх)"},
        {"level": 61316, "label": "S2 — Зона Королева (низ)"},
        {"level": 59000, "label": "S3 — Психологический уровень"},
    ],
    "resistance": [
        {"level": 63800, "label": "R1 — Локальное сопротивление"},
        {"level": 65000, "label": "R2 — Ключевой уровень"},
        {"level": 67000, "label": "R3 — Верхняя граница канала"},
    ],
}

ETH_ZONES = {
    "support": [
        {"level": 1706, "label": "S1 — Голубая зона BIG TRADER"},
        {"level": 1665, "label": "S2 — Следующая поддержка"},
    ],
    "resistance": [
        {"level": 1740, "label": "R1 — Серая зона сопротивления"},
        {"level": 1760, "label": "R2 — Верх серой зоны"},
    ],
}

def analyze_market_structure(btc: dict, eth: dict, gm: dict, coins: list) -> dict:
    """Анализируем структуру рынка по 5 индикаторам"""
    btc_price  = btc.get("price", 0)
    btc_ch24h  = btc.get("ch24h", 0)
    eth_price  = eth.get("price", 0)
    eth_ch24h  = eth.get("ch24h", 0)
    btc_dom    = gm.get("btc_dominance", 0)
    eth_dom    = gm.get("eth_dominance", 0)
    others_dom = 100 - btc_dom - eth_dom
    total_mcap = gm.get("total_mcap", 0)
    mcap_ch    = gm.get("mcap_change_24h", 0)

    # BTC сигнал
    btc_nearest_support = None
    btc_nearest_resist  = None
    for z in BTC_ZONES["support"]:
        if btc_price > z["level"]:
            dist_pct = (btc_price - z["level"]) / btc_price * 100
            btc_nearest_support = {"level": z["level"], "label": z["label"], "dist_pct": dist_pct}
            break
    for z in BTC_ZONES["resistance"]:
        if btc_price < z["level"]:
            dist_pct = (z["level"] - btc_price) / btc_price * 100
            btc_nearest_resist = {"level": z["level"], "label": z["label"], "dist_pct": dist_pct}
            break

    # Настроение рынка
    pos_coins = sum(1 for c in coins if c["quote"]["USDT"].get("percent_change_24h", 0) > 0)
    sentiment_pct = pos_coins / len(coins) * 100 if coins else 50
    if sentiment_pct >= 65:   sentiment = "🟢 Бычий"
    elif sentiment_pct >= 50: sentiment = "🔵 Умеренно бычий"
    elif sentiment_pct >= 35: sentiment = "🟠 Умеренно медвежий"
    else:                     sentiment = "🔴 Медвежий"

    # BTC.D сигнал для альтов
    if btc_dom > 59:
        dom_signal = "🔴 BTC доминирует — деньги в BTC, альты под давлением"
    elif btc_dom > 56:
        dom_signal = "🟡 BTC.D нейтральна — ротация возможна"
    else:
        dom_signal = "🟢 BTC.D снижается — деньги перетекают в альты"

    # OTHERS.D сигнал
    if others_dom < 8.2:
        others_signal = "🔴 Альткоины слабеют — капитал уходит"
    elif others_dom > 8.8:
        others_signal = "🟢 Альткоины усиливаются — капитал притекает"
    else:
        others_signal = "🟡 Альткоины нейтральны"

    # TOTAL капа
    if mcap_ch >= 2:
        total_signal = "🟢 Рынок растёт"
    elif mcap_ch >= 0:
        total_signal = "🔵 Рынок стабилен"
    elif mcap_ch >= -2:
        total_signal = "🟠 Рынок корректируется"
    else:
        total_signal = "🔴 Рынок падает"

    # Итоговый вердикт
    bull_signals = 0
    if btc_ch24h > 1:   bull_signals += 1
    if eth_ch24h > 1:   bull_signals += 1
    if btc_dom < 57:    bull_signals += 1
    if mcap_ch > 0:     bull_signals += 1
    if others_dom > 8.3: bull_signals += 1

    if bull_signals >= 4:   verdict = "🟢 БЫЧИЙ РЫНОК — можно искать лонги"
    elif bull_signals >= 3: verdict = "🔵 УМЕРЕННО БЫЧИЙ — осторожные лонги"
    elif bull_signals >= 2: verdict = "🟡 НЕЙТРАЛЬНЫЙ — ждать сигналов"
    elif bull_signals >= 1: verdict = "🟠 ОСТОРОЖНО — рынок под давлением"
    else:                   verdict = "🔴 МЕДВЕЖИЙ — воздерживаться от лонгов"

    return {
        "btc_price": btc_price, "btc_ch24h": btc_ch24h,
        "eth_price": eth_price, "eth_ch24h": eth_ch24h,
        "btc_dom": btc_dom, "eth_dom": eth_dom, "others_dom": others_dom,
        "total_mcap": total_mcap, "mcap_ch": mcap_ch,
        "btc_nearest_support": btc_nearest_support,
        "btc_nearest_resist":  btc_nearest_resist,
        "sentiment": sentiment, "sentiment_pct": sentiment_pct,
        "dom_signal": dom_signal,
        "others_signal": others_signal,
        "total_signal": total_signal,
        "verdict": verdict,
        "bull_signals": bull_signals,
    }

def build_market_overview(ms: dict) -> str:
    """Строим текст рыночного обзора для Telegram"""
    now = datetime.now(TZ).strftime("%d.%m.%Y  %H:%M")

    btc_arrow = trend_arrow(ms["btc_ch24h"])
    eth_arrow = trend_arrow(ms["eth_ch24h"])
    mcap_arrow = trend_arrow(ms["mcap_ch"])

    # BTC зоны
    sup = ms["btc_nearest_support"]
    res = ms["btc_nearest_resist"]
    btc_support_line = f"  └ Поддержка: ${sup['level']:,} ({sup['label']}) — {sup['dist_pct']:.1f}% ниже" if sup else "  └ Нет данных"
    btc_resist_line  = f"  └ Сопротивление: ${res['level']:,} ({res['label']}) — {res['dist_pct']:.1f}% выше" if res else "  └ Нет данных"

    lines = [
        "🌍 *ОБЗОР РЫНКА — BEST TRADE*",
        f"🕐 {now} Istanbul",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"₿ *BTC* {btc_arrow}  ${ms['btc_price']:,.0f}  ({fc(ms['btc_ch24h'])})",
        btc_support_line,
        btc_resist_line,
        "",
        f"Ξ *ETH* {eth_arrow}  ${ms['eth_price']:,.0f}  ({fc(ms['eth_ch24h'])})",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "📊 *ДОМИНАЦИЯ*",
        f"  BTC.D: *{ms['btc_dom']:.2f}%*  |  ETH.D: {ms['eth_dom']:.2f}%  |  Others: {ms['others_dom']:.2f}%",
        f"  {ms['dom_signal']}",
        f"  {ms['others_signal']}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"💰 *TOTAL* {mcap_arrow}  {fm(ms['total_mcap'])}  ({fc(ms['mcap_ch'])} за 24ч)",
        f"  {ms['total_signal']}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🧭 *Настроение рынка:* {ms['sentiment']}",
        f"  Растут: {ms['sentiment_pct']:.0f}% монет из топ-300",
        "",
        f"🎯 *ВЕРДИКТ:* {ms['verdict']}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "⚠️ Риск: *2% депозита* | SL всегда перед входом",
    ]
    return "\n".join(lines)

def build_market_overview_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("₿ BTC Chart", url="https://www.tradingview.com/chart/?symbol=BINANCE:BTCUSDT"),
            InlineKeyboardButton("Ξ ETH Chart", url="https://www.tradingview.com/chart/?symbol=BINANCE:ETHUSDT"),
        ],
        [
            InlineKeyboardButton("📊 BTC.D",   url="https://www.tradingview.com/chart/?symbol=CRYPTOCAP:BTC.D"),
            InlineKeyboardButton("📈 TOTAL",   url="https://www.tradingview.com/chart/?symbol=CRYPTOCAP:TOTAL"),
            InlineKeyboardButton("📉 OTHERS.D", url="https://www.tradingview.com/chart/?symbol=CRYPTOCAP:OTHERS.D"),
        ],
        [
            InlineKeyboardButton("🔄 Обновить обзор", callback_data="market_overview"),
            InlineKeyboardButton("🤖 Сигналы",        callback_data="signals"),
        ],
    ])

# ═══════════════════════════════════════════
# ГЕНЕРАЦИЯ ГРАФИКА
# ═══════════════════════════════════════════
def generate_chart(symbol: str, slug: str, a: dict) -> io.BytesIO:
    candles = get_coingecko_ohlc(slug, days=7)

    fig, ax = plt.subplots(figsize=(12, 6), facecolor=BG)
    ax.set_facecolor(BG)

    if candles and len(candles) >= 5:
        closes = [c["close"] for c in candles]
        n = len(candles)

        ema4  = calc_ema(closes, min(4,  len(closes)))
        ema7  = calc_ema(closes, min(7,  len(closes)))
        ema14 = calc_ema(closes, min(14, len(closes)))
        ema28 = calc_ema(closes, min(28, len(closes)))

        w = 0.4
        for i, c in enumerate(candles):
            color = GREEN if c["close"] >= c["open"] else RED
            ax.plot([i, i], [c["low"], c["high"]], color=color, lw=0.8, zorder=2)
            ax.add_patch(plt.Rectangle(
                (i - w/2, min(c["open"], c["close"])),
                w, abs(c["close"] - c["open"]),
                color=color, zorder=3
            ))

        colors_ema = [YELLOW, ORANGE, "#FF6B6B", "#4ECDC4"]
        labels_ema = ["EMA4", "EMA7", "EMA14", "EMA28"]
        for ema_vals, col, lbl in zip([ema4, ema7, ema14, ema28], colors_ema, labels_ema):
            valid = [(i, v) for i, v in enumerate(ema_vals) if v is not None]
            if valid:
                ax.plot([x[0] for x in valid], [x[1] for x in valid],
                        color=col, lw=1.2, alpha=0.9, label=lbl, zorder=4)

        price = closes[-1]
        ext   = n * 0.18

        def draw_level(val_str, color, label, ls="-", lw=1.2):
            try:
                val = float(str(val_str).replace(",", "").replace("$", "").strip())
                ax.axhline(y=val, color=color, linestyle=ls, linewidth=lw, alpha=0.85, zorder=5)
                ax.text(n - 1 + ext * 0.2, val, f" {label}: {val_str}",
                        color=color, fontsize=7.5, va="center", fontweight="bold", zorder=6)
            except:
                pass

        draw_level(a["tp3"],  "#009999", "TP3", "--", 1.0)
        draw_level(a["tp2"],  "#00BBAA", "TP2", "--", 1.0)
        draw_level(a["tp1"],  "#00DDAA", "TP1", "--", 1.0)
        draw_level(fp(a["swing"]), BLUE, "Swing", ":", 1.0)
        draw_level(fp(a["price"]), WHITE, "Entry", "-", 1.8)
        draw_level(a["stop"],  RED,      "SL",   "--", 1.2)

        try:
            entry_val = a["price"]
            stop_val  = float(a["stop"].replace(",","").replace("$","").strip())
            tp1_val   = float(a["tp1"].replace(",","").replace("$","").strip())
            ax.axhspan(entry_val, stop_val, alpha=0.08, color=RED,   zorder=1)
            ax.axhspan(entry_val, tp1_val,  alpha=0.06, color=GREEN, zorder=1)
        except:
            pass

        ax.legend(loc="upper left", fontsize=7.5, facecolor="#1A2332",
                  edgecolor=GRAY, labelcolor=WHITE, framealpha=0.85)

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

    side = "LONG" if a["is_long"] else "SHORT"
    side_color = GREEN if a["is_long"] else RED
    ax.set_title(f"{symbol}USDT  •  1h  •  {side}",
                 color=side_color, fontsize=13, fontweight="bold", pad=10)
    ax.grid(color="#1A2332", lw=0.4, zorder=0)
    ax.tick_params(colors=GRAY, labelsize=7)
    ax.spines[:].set_color("#1E2A3A")
    fig.text(0.5, 0.5, "BEST TRADE", fontsize=55, color="white", alpha=0.04,
             ha="center", va="center", fontweight="bold", rotation=20, zorder=0)

    plt.tight_layout(pad=0.5)
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    buf.seek(0)
    return buf

# ═══════════════════════════════════════════
# ТЕКСТ СИГНАЛА
# ═══════════════════════════════════════════
def build_signal_text(symbol: str, coin: dict, a: dict) -> str:
    side_emoji = "🟢" if a["is_long"] else "🔴"
    side_text  = "LONG" if a["is_long"] else "SHORT"
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
        f"🎯 *Тейк-профит 1:* {a['tp1']} ({pct_from_entry(a['tp1'])})",
        f"🎯 *Тейк-профит 2:* {a['tp2']} ({pct_from_entry(a['tp2'])})",
        f"🎯 *Тейк-профит 3:* {a['tp3']} ({pct_from_entry(a['tp3'])})",
        f"🔴 *Стоп лосс:* {a['stop']} ({sl_pct(a['stop'])})",
        f"📌 *{swing_label}:* {fp(a['swing'])}",
    ]
    return "\n".join(lines)

# ═══════════════════════════════════════════
# ОСТАЛЬНЫЕ ОТЧЁТЫ (без изменений)
# ═══════════════════════════════════════════
def build_market_report(coins: list) -> list:
    now  = datetime.now(TZ)
    up   = sorted(coins, key=lambda x: x["quote"]["USDT"].get("percent_change_24h", 0), reverse=True)
    dn   = sorted(coins, key=lambda x: x["quote"]["USDT"].get("percent_change_24h", 0))
    pos  = sum(1 for c in coins if c["quote"]["USDT"].get("percent_change_24h", 0) > 0)
    pct  = pos / len(coins) * 100
    mood = "🟢 Бычий" if pct >= 60 else ("🔴 Медвежий" if pct < 40 else "🟡 Нейтральный")
    now_str  = now.strftime("%d.%m.%Y  %H:%M")
    sent_str = now.strftime("%d.%m %H:%M:%S")

    def arr(ch): return "🟢" if ch >= 5 else ("🔵" if ch >= 0 else ("🟠" if ch >= -5 else "🔴"))
    def ps(ch):  return ("+" if ch >= 0 else "") + f"{ch:.2f}%"

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
        q   = c["quote"]["USDT"]
        ch  = q.get("percent_change_24h", 0)
        sym = c["symbol"]
        lines1.append(arr(ch) + " " + str(i) + ". *" + sym + "*")
        lines1.append("    💰 $" + fp(q["price"]) + "  " + ps(ch))
    for c in up[:8]:
        b1.append(InlineKeyboardButton("📊 " + c["symbol"], url=cmc_link(c.get("slug", c["symbol"].lower()))))

    lines2 = ["📉 *ТОП-15 ПАДЕНИЕ за 24ч*"]
    b2 = []
    for i, c in enumerate(dn[:15], 1):
        q   = c["quote"]["USDT"]
        ch  = q.get("percent_change_24h", 0)
        sym = c["symbol"]
        lines2.append(arr(ch) + " " + str(i) + ". *" + sym + "*")
        lines2.append("    💰 $" + fp(q["price"]) + "  " + ps(ch))
    lines2.extend(["", "🕐 Отправлено: " + sent_str + " UTC+3", "📡 CoinMarketCap • Топ-300 монет"])
    for c in dn[:8]:
        b2.append(InlineKeyboardButton("📊 " + c["symbol"], url=cmc_link(c.get("slug", c["symbol"].lower()))))

    return [{"text": "\n".join(lines1), "btns": b1}, {"text": "\n".join(lines2), "btns": b2}]

def build_signals_report(coins: list) -> list:
    now      = datetime.now(TZ)
    analyzed = [(c, full_analysis(c)) for c in coins]
    longs    = sorted([(c,a) for c,a in analyzed if a["score"] >= 3],
                      key=lambda x: x[1]["score"], reverse=True)[:8]
    shorts   = sorted([(c,a) for c,a in analyzed if a["score"] <= -3],
                      key=lambda x: x[1]["score"])[:5]
    results  = []
    now_str  = now.strftime("%d.%m.%Y  %H:%M")
    header   = (f"🤖 *BEST TRADE — Сигналы*\n"
                f"🕐 {now_str} Istanbul\n\n"
                f"🟢 Лонг: {len(longs)} монет  |  🔴 Шорт: {len(shorts)} монет")
    results.append({"type": "text", "text": header, "btns": []})

    for c, a in longs + shorts:
        symbol = c["symbol"]
        slug   = c.get("slug", symbol.lower())
        text   = build_signal_text(symbol, c, a)
        kb = [InlineKeyboardButton("📈 Открыть график на TradingView", url=tv_link(symbol))]
        results.append({"type": "coin", "symbol": symbol, "slug": slug,
                        "text": text, "btns": kb, "analysis": a})

    results.append({"type": "text",
                    "text": "⚠️ Риск на сделку: *2-3%*\nСтоп *ВСЕГДА* выставляй до входа!",
                    "btns": []})
    return results

def build_period_report(period: str, coins: list) -> list:
    field_map = {"1h": "percent_change_1h", "24h": "percent_change_24h", "7d": "percent_change_7d"}
    label_map = {"1h": "1 ЧАС", "24h": "24 ЧАСА", "7d": "7 ДНЕЙ"}
    field = field_map.get(period, "percent_change_24h")
    label = label_map.get(period, "24 ЧАСА")
    now   = datetime.now(TZ)

    def ti(ch):  return "🟢" if ch >= 5 else ("🔵" if ch >= 0 else ("🟠" if ch >= -5 else "🔴"))
    def fc2(ch): return f"+{ch:.2f}%" if ch >= 0 else f"{ch:.2f}%"

    up = sorted(coins, key=lambda x: x["quote"]["USDT"].get(field, 0), reverse=True)
    dn = sorted(coins, key=lambda x: x["quote"]["USDT"].get(field, 0))

    t1 = f"📊 BEST TRADE — ТОП за {label}\n🕐 {now.strftime('%H:%M')} Istanbul\n\n🚀 ЛИДЕРЫ РОСТА\n{'─'*28}\n"
    b1 = []
    for i, c in enumerate(up[:15], 1):
        ch = c["quote"]["USDT"].get(field, 0)
        p  = c["quote"]["USDT"].get("price", 0)
        t1 += f"{i:>2}. {c['symbol']:<8} ${fp(p):<14} {ti(ch)} {fc2(ch)}\n"
    for c in up[:8]:
        b1.append(InlineKeyboardButton(f"📊 {c['symbol']}", url=cmc_link(c.get("slug", c["symbol"].lower()))))

    t2 = f"📉 ЛИДЕРЫ ПАДЕНИЯ\n{'─'*28}\n"
    b2 = []
    for i, c in enumerate(dn[:15], 1):
        ch = c["quote"]["USDT"].get(field, 0)
        p  = c["quote"]["USDT"].get("price", 0)
        t2 += f"{i:>2}. {c['symbol']:<8} ${fp(p):<14} {ti(ch)} {fc2(ch)}\n"
    for c in dn[:8]:
        b2.append(InlineKeyboardButton(f"📊 {c['symbol']}", url=cmc_link(c.get("slug", c["symbol"].lower()))))

    return [{"text": t1, "btns": b1}, {"text": t2, "btns": b2}]

# ═══════════════════════════════════════════
# КЛАВИАТУРЫ
# ═══════════════════════════════════════════
def main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌍 Обзор рынка",  callback_data="market_overview"),
         InlineKeyboardButton("🤖 Сигналы",      callback_data="signals")],
        [InlineKeyboardButton("📊 Рынок",        callback_data="report"),
         InlineKeyboardButton("⏱ 1ч",           callback_data="period_1h"),
         InlineKeyboardButton("📅 24ч",          callback_data="period_24h")],
    ])

async def send_parts(bot, chat_id, parts, query=None):
    for i, part in enumerate(parts):
        text    = part["text"]
        btns    = part.get("btns", [])
        is_last = (i == len(parts) - 1)
        rows    = [btns[j:j+4] for j in range(0, len(btns), 4)]
        if is_last:
            rows.append([
                InlineKeyboardButton("🌍 Обзор рынка",  callback_data="market_overview"),
                InlineKeyboardButton("🤖 Сигналы",      callback_data="signals"),
            ])
            rows.append([
                InlineKeyboardButton("⏱ 1ч",  callback_data="period_1h"),
                InlineKeyboardButton("📅 24ч", callback_data="period_24h"),
                InlineKeyboardButton("📆 7д",  callback_data="period_7d"),
            ])
        kb = InlineKeyboardMarkup(rows) if rows else None
        if query and i == 0:
            await query.edit_message_text(text, parse_mode="Markdown",
                                          reply_markup=kb, disable_web_page_preview=True)
        else:
            await bot.send_message(chat_id, text, parse_mode="Markdown",
                                   reply_markup=kb, disable_web_page_preview=True)

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
        "📊 *BEST TRADE v6.0*\n\n"
        "Топ-300 • CoinMarketCap\n"
        "🌍 Рыночный обзор каждые 30 мин\n"
        "BTC + ETH + BTC.D + TOTAL + OTHERS.D\n"
        "Графики • EMA • RSI • Зоны набора\n"
        "🕐 Стамбул UTC+3\n\n"
        "Команды:\n"
        "/market — рыночный обзор\n"
        "/coin BTC — анализ монеты\n"
        "/top — топ рынка\n"
        "/signals — торговые сигналы",
        parse_mode="Markdown",
        reply_markup=main_kb()
    )

async def cmd_market(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /market — рыночный обзор"""
    msg = await update.message.reply_text("⏳ Загружаю рыночные данные...")
    try:
        prices = get_btc_price()
        gm     = get_global_metrics()
        coins  = get_top300()
        btc = prices.get("BTC", {})
        eth = prices.get("ETH", {})
        ms  = analyze_market_structure(btc, eth, gm, coins)
        text = build_market_overview(ms)
        kb   = build_market_overview_kb()
        await msg.edit_text(text, parse_mode="Markdown",
                            reply_markup=kb, disable_web_page_preview=True)
    except Exception as e:
        log.error(f"Market overview error: {e}")
        await msg.edit_text("❌ Ошибка загрузки данных. Попробуй позже.")

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
    try:
        chart = generate_chart(symbol, slug, a)
        text  = build_signal_text(symbol, coin, a)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("📈 TradingView", url=tv_link(symbol)),
        ],[
            InlineKeyboardButton("🔄 Обновить", callback_data=f"coin_{symbol}"),
            InlineKeyboardButton("📈 CMC",      url=cmc_link(slug)),
        ]])
        await msg.delete()
        await update.message.reply_photo(photo=chart, caption=text,
                                         parse_mode="Markdown", reply_markup=kb)
    except Exception as e:
        log.error(f"Chart error: {e}")
        text = build_signal_text(symbol, coin, a)
        kb   = InlineKeyboardMarkup([[
            InlineKeyboardButton("📈 CMC",       url=cmc_link(slug)),
            InlineKeyboardButton("📊 TV",        url=tv_link(symbol)),
            InlineKeyboardButton("🔄 Обновить", callback_data=f"coin_{symbol}"),
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
    parts = build_signals_report(coins)
    for part in parts:
        try:
            if part["type"] == "text":
                rows = [part["btns"][i:i+2] for i in range(0, len(part["btns"]), 2)] if part["btns"] else []
                rows.append([
                    InlineKeyboardButton("🌍 Обзор рынка", callback_data="market_overview"),
                    InlineKeyboardButton("🤖 Сигналы",     callback_data="signals"),
                ])
                await bot.send_message(chat_id, part["text"], parse_mode="Markdown",
                                       reply_markup=InlineKeyboardMarkup(rows))
            elif part["type"] == "coin":
                symbol = part["symbol"]
                slug   = part["slug"]
                a      = part["analysis"]
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("📈 TradingView", url=tv_link(symbol)),
                ],[
                    InlineKeyboardButton("🔄 Обновить", callback_data=f"coin_{symbol}"),
                    InlineKeyboardButton("📈 CMC",      url=cmc_link(slug)),
                ]])
                try:
                    chart = generate_chart(symbol, slug, a)
                    await bot.send_photo(chat_id=chat_id, photo=chart,
                                         caption=part["text"], parse_mode="Markdown", reply_markup=kb)
                except Exception as e:
                    log.error(f"Chart error {symbol}: {e}")
                    await bot.send_message(chat_id, part["text"], parse_mode="Markdown",
                                           reply_markup=kb, disable_web_page_preview=True)
        except Exception as e:
            log.error(f"Send error: {e}")

async def cmd_signals(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Анализирую топ-300... ~30 секунд")
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

    if data == "market_overview":
        await q.edit_message_text("⏳ Загружаю рыночные данные...", parse_mode="Markdown")
        try:
            prices = get_btc_price()
            gm     = get_global_metrics()
            coins  = get_top300()
            btc = prices.get("BTC", {})
            eth = prices.get("ETH", {})
            ms  = analyze_market_structure(btc, eth, gm, coins)
            text = build_market_overview(ms)
            kb   = build_market_overview_kb()
            await q.edit_message_text(text, parse_mode="Markdown",
                                      reply_markup=kb, disable_web_page_preview=True)
        except Exception as e:
            log.error(f"Market overview callback error: {e}")
            await q.edit_message_text("❌ Ошибка загрузки данных")

    elif data in ("report", "signals") or data.startswith("period_"):
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
                InlineKeyboardButton("📈 CMC",       url=cmc_link(slug)),
                InlineKeyboardButton("📊 TV",        url=tv_link(symbol)),
            ],[
                InlineKeyboardButton("🔄 Обновить", callback_data=f"coin_{symbol}"),
                InlineKeyboardButton("◀️ Назад",     callback_data="market_overview"),
            ]])
            await q.message.delete()
            await ctx.bot.send_photo(chat_id=chat_id, photo=chart,
                                     caption=text, parse_mode="Markdown", reply_markup=kb)
        except Exception as e:
            log.error(f"Chart error: {e}")
            text = build_signal_text(symbol, coin, a)
            kb   = InlineKeyboardMarkup([[
                InlineKeyboardButton("📈 CMC",       url=cmc_link(slug)),
                InlineKeyboardButton("📊 TV",        url=tv_link(symbol)),
                InlineKeyboardButton("🔄 Обновить", callback_data=f"coin_{symbol}"),
            ]])
            await ctx.bot.send_message(chat_id, text, parse_mode="Markdown",
                                       reply_markup=kb, disable_web_page_preview=True)

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
    """Каждые 30 мин: сначала рыночный обзор, потом сигналы"""
    chat_ids = load_chat_ids() | user_chat_ids
    if not chat_ids:
        return
    now_str = datetime.now(TZ).strftime("%H:%M")
    log.info(f"Рассылка {now_str} Istanbul")

    # Загружаем всё одним запросом
    coins  = get_top300()
    prices = get_btc_price()
    gm     = get_global_metrics()

    if not coins:
        return

    btc = prices.get("BTC", {})
    eth = prices.get("ETH", {})
    ms  = analyze_market_structure(btc, eth, gm, coins)
    overview_text = build_market_overview(ms)
    overview_kb   = build_market_overview_kb()

    for cid in chat_ids:
        try:
            # 1. Рыночный обзор
            await bot.send_message(cid, overview_text, parse_mode="Markdown",
                                   reply_markup=overview_kb, disable_web_page_preview=True)
            await asyncio.sleep(1)
            # 2. Сигналы по монетам
            await send_signals(bot, cid, coins)
        except Exception as e:
            log.error(f"Ошибка рассылки {cid}: {e}")

# ═══════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("market",  cmd_market))
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
    log.info("✅ BEST TRADE v6.0 | Market Overview | Istanbul UTC+3")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
