#!/usr/bin/env python3
"""
📊 BEST TRADE Bot v8.0
- Обзор рынка: блок ТОП-5 ЛОНГИ / ТОП-5 ШОРТЫ из топ-300 CMC
- Сигналы: фикс отправки графика (caption limit guard + split fallback)
- Рыночный обзор каждые 30 мин
"""

import asyncio
import io
import logging
import os
import random
import requests
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from datetime import datetime, timedelta, timezone
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

BOT_TOKEN   = os.getenv("BOT_TOKEN")
CMC_API_KEY = os.getenv("CMC_API_KEY", "7c581d74b60d4c40879edc0431b5e53a")
TZ          = pytz.timezone("Europe/Istanbul")

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ── ПАЛИТРА ──
BG     = "#0D1421"
GREEN  = "#16C784"
RED    = "#EA3943"
WHITE  = "#FFFFFF"
GRAY   = "#7B8BB2"
YELLOW = "#F0B90B"
ORANGE = "#F7931A"
BLUE   = "#4A90D9"
TP1C   = "#00E5A0"
TP2C   = "#00C896"
TP3C   = "#00A87A"
SLC    = "#EA3943"
ENTRYC = "#FFD700"
SWINGC = "#4A90D9"

# ═══════════════════════════════════════════
# CMC / DATA
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
    try:
        url = "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest"
        headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        d = r.json().get("data", {})
        q = d.get("quote", {}).get("USD", {})
        return {
            "total_mcap":      q.get("total_market_cap", 0),
            "total_vol_24h":   q.get("total_volume_24h", 0),
            "btc_dominance":   d.get("btc_dominance", 0),
            "eth_dominance":   d.get("eth_dominance", 0),
            "mcap_change_24h": q.get("total_market_cap_yesterday_percentage_change", 0),
        }
    except Exception as e:
        log.error(f"Global metrics error: {e}")
        return {}

def get_btc_eth_price() -> dict:
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
                item = data[sym]
                q = (item[0] if isinstance(item, list) else item)["quote"]["USD"]
                result[sym] = {
                    "price": q.get("price", 0),
                    "ch1h":  q.get("percent_change_1h", 0),
                    "ch24h": q.get("percent_change_24h", 0),
                    "ch7d":  q.get("percent_change_7d", 0),
                }
        return result
    except Exception as e:
        log.error(f"BTC/ETH price error: {e}")
        return {}

def get_binance_ohlc(symbol: str, interval: str = "4h", limit: int = 80) -> list:
    """Получаем OHLC с Binance — без ограничений, без API ключа"""
    try:
        url = "https://api.binance.com/api/v3/klines"
        params = {"symbol": f"{symbol}USDT", "interval": interval, "limit": limit}
        r = requests.get(url, params=params, timeout=12)
        r.raise_for_status()
        return [
            {
                "time":  datetime.fromtimestamp(d[0] / 1000, tz=TZ),
                "open":  float(d[1]),
                "high":  float(d[2]),
                "low":   float(d[3]),
                "close": float(d[4]),
                "vol":   float(d[5]),
            }
            for d in r.json()
        ]
    except Exception as e:
        log.error(f"Binance OHLC error {symbol}: {e}")
        return []

def get_coingecko_ohlc(slug: str, days: int = 7) -> list:
    """Fallback — CoinGecko (может блокироваться на Railway)"""
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{slug}/ohlc"
        params = {"vs_currency": "usd", "days": str(days)}
        r = requests.get(url, params=params, timeout=12)
        r.raise_for_status()
        return [{"time": datetime.fromtimestamp(d[0]/1000, tz=TZ),
                 "open": d[1], "high": d[2], "low": d[3], "close": d[4], "vol": 0}
                for d in r.json()]
    except Exception as e:
        log.error(f"CoinGecko OHLC error {slug}: {e}")
        return []

def cmc_link(slug):   return f"https://coinmarketcap.com/currencies/{slug}/"
def tv_link(symbol):  return f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}USDT"

# ── ФОРМАТИРОВАНИЕ ──
def fp(p):
    if p >= 1000: return f"{p:,.2f}"
    if p >= 1:    return f"{p:.4f}"
    if p >= 0.01: return f"{p:.5f}"
    return f"{p:.8f}"

def fc(ch): return f"+{ch:.2f}%" if ch >= 0 else f"{ch:.2f}%"

def fm(m):
    if m >= 1e12: return f"${m/1e12:.2f}T"
    if m >= 1e9:  return f"${m/1e9:.2f}B"
    if m >= 1e6:  return f"${m/1e6:.2f}M"
    return f"${m:.0f}"

def trend_arrow(ch):
    if ch >= 3:  return "🟢"
    if ch >= 0:  return "🔵"
    if ch >= -3: return "🟠"
    return "🔴"

# ── ТЕХНИЧЕСКИЙ АНАЛИЗ ──
def calc_ema(prices, period):
    if len(prices) < period:
        return [None] * len(prices)
    emas = [None] * (period - 1)
    sma  = sum(prices[:period]) / period
    emas.append(sma)
    k = 2 / (period + 1)
    for p in prices[period:]:
        emas.append(p * k + emas[-1] * (1 - k))
    return emas

def calc_rsi_val(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains  = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    ag = sum(gains) / period
    al = sum(losses) / period
    if al == 0: return 100.0
    return round(100 - (100 / (1 + ag/al)), 1)

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

    def rsi_est(m):
        if m > 15: return 82.0
        if m > 8:  return 70.0
        if m > 3:  return 60.0
        if m > 0:  return 52.0
        if m > -3: return 45.0
        if m > -8: return 35.0
        if m > -15:return 25.0
        return 18.0

    m4h     = ch1h * 0.5 + ch24h * 0.5
    rsi_4h  = rsi_est(m4h)
    rsi_1h  = rsi_est(ch1h)
    rsi_1d  = rsi_est(ch24h)

    score = 0
    if ema200 and ema50 and ema20: score += 3
    elif ema50 and ema20:          score += 2
    elif ema20:                    score += 1
    elif not ema50 and not ema200: score -= 2
    if rsi_4h < 30:    score += 3
    elif rsi_4h < 40:  score += 2
    elif rsi_4h > 70:  score -= 2
    elif rsi_4h > 80:  score -= 3
    if ch24h >= 10:    score += 2
    elif ch24h >= 4:   score += 1
    elif ch24h <= -10: score -= 2
    elif ch24h <= -4:  score -= 1
    if ch1h >= 3:      score += 1
    elif ch1h <= -3:   score -= 1
    if vol_ratio >= 20: score += 2
    elif vol_ratio >= 10: score += 1

    is_long = score >= 0

    if is_long:
        tp1   = round(price * 1.04, 8)
        tp2   = round(price * 1.06, 8)
        tp3   = round(price * 1.10, 8)
        sl    = round(price * 0.85, 8)
        swing = round(price * 0.92, 8)
    else:
        tp1   = round(price * 0.96, 8)
        tp2   = round(price * 0.94, 8)
        tp3   = round(price * 0.90, 8)
        sl    = round(price * 1.15, 8)
        swing = round(price * 1.08, 8)

    rr = abs(tp3 - price) / abs(sl - price) if abs(sl - price) > 0 else 0

    ema_pos = "выше EMA200 ✅" if ema200 else "ниже EMA200 ⚠️"

    # EMA приближённые значения по таймфреймам
    # 1H: EMA20 ≈ цена * (1 + ch1h/100 * коэф), EMA50, EMA200
    ema20_1h  = round(price / (1 + ch1h  / 100 * 0.15), 6)
    ema50_1h  = round(price / (1 + ch1h  / 100 * 0.40), 6)
    ema200_1h = round(price / (1 + ch1h  / 100 * 1.20), 6)
    # 4H: используем ch24h как база
    ema20_4h  = round(price / (1 + ch24h / 100 * 0.10), 6)
    ema50_4h  = round(price / (1 + ch24h / 100 * 0.25), 6)
    ema200_4h = round(price / (1 + ch24h / 100 * 0.80), 6)
    # 1D: используем ch7d
    ema20_1d  = round(price / (1 + ch7d  / 100 * 0.08), 6)
    ema50_1d  = round(price / (1 + ch7d  / 100 * 0.20), 6)
    ema200_1d = round(price / (1 + ch7d  / 100 * 0.60), 6)

    if score >= 5:    label = "🔥 СИЛЬНЫЙ ЛОНГ"
    elif score >= 3:  label = "✅ ЛОНГ"
    elif score >= 1:  label = "📈 СЛАБЫЙ ЛОНГ"
    elif score >= -1: label = "⚪️ НЕЙТРАЛЬНО"
    elif score >= -3: label = "📉 СЛАБЫЙ ШОРТ"
    elif score >= -5: label = "🔻 ШОРТ"
    else:             label = "💥 СИЛЬНЫЙ ШОРТ"

    return {
        "label": label, "score": score, "is_long": is_long,
        "price": price, "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "sl": sl, "swing": swing, "rr": rr,
        "rsi_4h": rsi_4h, "rsi_1h": rsi_1h, "rsi_1d": rsi_1d,
        "ch1h": ch1h, "ch24h": ch24h, "ch7d": ch7d, "ch30d": ch30d,
        "ema_pos": ema_pos, "ema200": ema200,
        "vol": vol, "mcap": mcap, "vol_ratio": vol_ratio,
        # EMA по таймфреймам
        "ema20_1h":  ema20_1h,  "ema50_1h":  ema50_1h,  "ema200_1h": ema200_1h,
        "ema20_4h":  ema20_4h,  "ema50_4h":  ema50_4h,  "ema200_4h": ema200_4h,
        "ema20_1d":  ema20_1d,  "ema50_1d":  ema50_1d,  "ema200_1d": ema200_1d,
    }

# ═══════════════════════════════════════════
# ГЕНЕРАЦИЯ ГРАФИКА — BEST TRADE STYLE
# ═══════════════════════════════════════════
def detect_order_blocks(candles: list, is_long: bool) -> list:
    """Определяем Order Block зоны из реальных свечей"""
    obs = []
    if len(candles) < 5:
        return obs
    for i in range(2, len(candles) - 1):
        c = candles[i]
        body = abs(c["close"] - c["open"])
        prev = candles[i - 1]
        # Бычий OB: медвежья свеча перед сильным движением вверх
        if is_long:
            if (c["close"] < c["open"] and
                candles[i+1]["close"] > c["high"] and
                body > abs(prev["close"] - prev["open"]) * 1.2):
                obs.append({"lo": c["low"], "hi": c["high"], "idx": i, "type": "bull"})
        # Медвежий OB: бычья свеча перед сильным движением вниз
        else:
            if (c["close"] > c["open"] and
                candles[i+1]["close"] < c["low"] and
                body > abs(prev["close"] - prev["open"]) * 1.2):
                obs.append({"lo": c["low"], "hi": c["high"], "idx": i, "type": "bear"})
    return obs[-3:]  # последние 3 OB

def generate_signal_chart(symbol: str, slug: str, a: dict) -> io.BytesIO:
    is_long = a["is_long"]
    price   = a["price"]
    tp1, tp2, tp3 = a["tp1"], a["tp2"], a["tp3"]
    sl, swing     = a["sl"],  a["swing"]
    rsi           = a["rsi_4h"]

    # ── ДАННЫЕ: Binance первый, CoinGecko запасной ──
    candles = get_binance_ohlc(symbol, interval="4h", limit=80)
    if not candles or len(candles) < 10:
        candles = get_coingecko_ohlc(slug, days=7)
    if not candles or len(candles) < 10:
        # Заглушка если оба источника недоступны
        p = price * 0.96
        for _ in range(60):
            ch = random.gauss(0, 0.008)
            o = p; c = p * (1 + ch)
            h = max(o, c) * (1 + abs(random.gauss(0, 0.003)))
            l = min(o, c) * (1 - abs(random.gauss(0, 0.003)))
            candles.append({"open": o, "high": h, "low": l, "close": c, "vol": random.uniform(1e5, 1e6)})
            p = c
        candles[-1]["close"] = price

    n      = len(candles)
    closes = [c["close"] for c in candles]
    vols   = [c.get("vol", 0) for c in candles]

    # EMA
    ema20  = calc_ema(closes, min(20, n))
    ema50  = calc_ema(closes, min(50, n))
    ema200 = calc_ema(closes, min(200, n))

    # Order Blocks
    obs = detect_order_blocks(candles, is_long)

    # ── FIGURE ──
    fig = plt.figure(figsize=(13, 8.2), facecolor=BG)
    gs  = fig.add_gridspec(8, 1, hspace=0,
                            left=0.01, right=0.80,
                            top=0.995, bottom=0.06)
    ax_brand = fig.add_subplot(gs[0:1, 0])
    ax       = fig.add_subplot(gs[1:7, 0])
    axv      = fig.add_subplot(gs[7:,  0], sharex=ax)

    ax_brand.set_facecolor(ORANGE)
    ax.set_facecolor(BG)
    axv.set_facecolor(BG)

    # ══════════════════════════════════════
    # БРЕНДИНГ-ПОЛОСА — BEST TRADE
    # ══════════════════════════════════════
    ax_brand.set_xlim(0, 1)
    ax_brand.set_ylim(0, 1)
    ax_brand.axis("off")
    ax_brand.text(0.5, 0.55, "B E S T   T R A D E",
                  fontsize=20, color=WHITE, fontweight="bold",
                  ha="center", va="center",
                  transform=ax_brand.transAxes)
    ax_brand.text(0.5, 0.12, "S  I  G  N  A  L  S",
                  fontsize=7, color=WHITE, alpha=0.70,
                  ha="center", va="center",
                  transform=ax_brand.transAxes)
    for xv in [0.06, 0.94]:
        ax_brand.axvline(xv, color=WHITE, alpha=0.20, lw=0.8)

    # ── ORDER BLOCK ЗОНЫ (за свечами) ──
    for ob in obs:
        ob_color = "#16C784" if ob["type"] == "bull" else "#EA3943"
        ax.axhspan(ob["lo"], ob["hi"],
                   xmin=ob["idx"] / n, xmax=1.0,
                   alpha=0.10, color=ob_color, zorder=1)
        ax.axhline(ob["hi"], color=ob_color, lw=0.5,
                   alpha=0.30, linestyle="-", zorder=1)
        ax.axhline(ob["lo"], color=ob_color, lw=0.5,
                   alpha=0.30, linestyle="-", zorder=1)

    # ── СВЕЧИ ──
    w = 0.4
    for i, c in enumerate(candles):
        col = GREEN if c["close"] >= c["open"] else RED
        ax.plot([i, i], [c["low"], c["high"]], color=col, lw=0.65, zorder=2)
        h = abs(c["close"] - c["open"]) or (c["high"] - c["low"]) * 0.01
        ax.add_patch(patches.Rectangle(
            (i - w/2, min(c["open"], c["close"])), w, h,
            linewidth=0, facecolor=col, zorder=3
        ))

    # ── EMA ──
    for vals, col, lbl, lw in [
        (ema20,  YELLOW,    "EMA 20",  1.1),
        (ema50,  ORANGE,    "EMA 50",  1.2),
        (ema200, "#FF6B6B", "EMA 200", 1.4),
    ]:
        pts = [(i, v) for i, v in enumerate(vals) if v is not None]
        if len(pts) > 1:
            ax.plot([p[0] for p in pts], [p[1] for p in pts],
                    color=col, lw=lw, alpha=0.88, label=lbl, zorder=4)

    # ── ЗОНЫ SL/TP (полупрозрачные) ──
    if is_long:
        ax.axhspan(sl,    price, alpha=0.07, color=RED,   zorder=1)
        ax.axhspan(price, tp1,   alpha=0.05, color=GREEN, zorder=1)
    else:
        ax.axhspan(price, sl,    alpha=0.07, color=RED,   zorder=1)
        ax.axhspan(tp1,   price, alpha=0.05, color=GREEN, zorder=1)

    # ── УРОВНИ ──
    ext = n * 0.26
    ax.set_xlim(-1, n + ext)

    def pct_str(target):
        d = (target - price) / price * 100
        v = d if is_long else -d
        return f"(+{v:.2f}%)" if v >= 0 else f"({v:.2f}%)"

    def draw_lvl(val, color, label, extra="", ls="--", lw=1.2, bold=False):
        ax.axhline(val, color=color, linestyle=ls, linewidth=lw, alpha=0.92, zorder=5)
        ax.text(n + ext * 0.04, val,
                f"  {label}  {fp(val)}  {extra}",
                color=color, fontsize=7.8, va="center",
                fontweight="bold" if bold else "normal",
                fontfamily="monospace", zorder=6)

    draw_lvl(tp3,   TP3C,   "TP3",   pct_str(tp3),  "--", 1.0)
    draw_lvl(tp2,   TP2C,   "TP2",   pct_str(tp2),  "--", 1.0)
    draw_lvl(tp1,   TP1C,   "TP1",   pct_str(tp1),  "--", 1.0)
    draw_lvl(price, ENTRYC, "Entry", "",             "-",  2.2, True)
    draw_lvl(swing, SWINGC, "Swing", "",             ":",  1.0)
    draw_lvl(sl,    SLC,    "SL",    pct_str(sl),    "--", 1.3)

    ax.annotate("▲" if is_long else "▼",
                xy=(n - 1, price), fontsize=14,
                color=ENTRYC, ha="center", va="bottom", zorder=7)

    ax.legend(loc="upper left", fontsize=7.5,
              facecolor="#0D1B2A", edgecolor="#1E2A3A",
              labelcolor=WHITE, framealpha=0.92,
              borderpad=0.5, handlelength=1.4)

    # ── ОБЪЁМ (реальный с Binance) ──
    max_vol = max(vols) if max(vols) > 0 else 1
    for i, c in enumerate(candles):
        col = GREEN if c["close"] >= c["open"] else RED
        axv.bar(i, vols[i] / max_vol, width=0.7,
                color=col, alpha=0.40, zorder=2)
    axv.set_yticks([])
    axv.spines[:].set_visible(False)

    # ── X МЕТКИ (реальное время из Binance) ──
    step  = max(n // 8, 1)
    ticks = list(range(0, n, step))
    if candles[0].get("time"):
        xlbls = [candles[i]["time"].strftime("%d.%m\n%H:%M") for i in ticks]
    else:
        now_t = datetime.now(timezone.utc)
        xlbls = [(now_t - timedelta(hours=(n - i) * 4)).strftime("%d.%m\n%H:%M")
                 for i in ticks]
    ax.set_xticks(ticks)
    ax.set_xticklabels(xlbls, fontsize=6.5, color=GRAY)
    axv.tick_params(axis="x", colors=GRAY, labelsize=6)

    # ── ОСИ ──
    ax.tick_params(axis="y", colors=GRAY, labelsize=7.5, right=False, left=False)
    ax.yaxis.set_label_position("right")
    ax.yaxis.tick_right()
    ax.grid(color="#1A2535", lw=0.3, zorder=0)
    ax.spines[:].set_color("#1E2A3A")

    # ── ЗАГОЛОВОК НА ГРАФИКЕ ──
    side_str  = "LONG" if is_long else "SHORT"
    side_col  = GREEN if is_long else RED
    rsi_color = GREEN if rsi < 35 else (RED if rsi > 65 else GRAY)
    rsi_tag   = "Перепродан" if rsi < 35 else ("Перекуплен" if rsi > 65 else "Нейтральный")

    ax.text(0.012, 0.97, f"{symbol}USDT  •  4H  •  {side_str}",
            fontsize=11, color=WHITE, fontweight="bold",
            va="top", ha="left", transform=ax.transAxes, zorder=10)
    ax.text(0.012, 0.89, f"RSI {rsi:.0f}  —  {rsi_tag}",
            fontsize=8, color=rsi_color,
            va="top", ha="left", transform=ax.transAxes, zorder=10)

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150,
                bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    buf.seek(0)
    return buf

# ═══════════════════════════════════════════
# ТЕКСТ СИГНАЛА — ЧИСТЫЙ ФОРМАТ
# ═══════════════════════════════════════════
def build_signal_text(symbol: str, a: dict) -> str:
    is_long = a["is_long"]
    price   = a["price"]
    tp1, tp2, tp3 = a["tp1"], a["tp2"], a["tp3"]
    sl, swing     = a["sl"],  a["swing"]
    rsi_1h = a["rsi_1h"]
    rsi_4h = a["rsi_4h"]
    rsi_1d = a["rsi_1d"]
    rr     = a["rr"]
    vol    = a["vol"]

    side_emoji = "🟢" if is_long else "🔴"
    side_text  = "LONG" if is_long else "SHORT"
    swing_lbl  = "Swing Low" if is_long else "Swing High"

    def pct(target):
        d = (target - price) / price * 100
        v = d if is_long else -d
        return f"+{v:.2f}%" if v >= 0 else f"{v:.2f}%"

    def sl_pct():
        d = abs(sl - price) / price * 100
        return f"-{d:.2f}%"

    def rsi_icon(r):
        if r < 30:  return "🟢"
        if r > 70:  return "🔴"
        return "🔵"

    # Объём в читаемом формате $
    if vol >= 1e9:   vol_str = f"${vol/1e9:.2f}B"
    elif vol >= 1e6: vol_str = f"${vol/1e6:.1f}M"
    else:            vol_str = f"${vol/1e3:.0f}K"

    # % отклонение цены от EMA (+ = цена выше EMA, - = ниже)
    def ema_pct(ema_val):
        if ema_val <= 0: return "—"
        d = (price - ema_val) / ema_val * 100
        arrow = "▲" if d >= 0 else "▼"
        return f"{arrow}{abs(d):.1f}%"

    e20_1h  = ema_pct(a["ema20_1h"])
    e50_1h  = ema_pct(a["ema50_1h"])
    e200_1h = ema_pct(a["ema200_1h"])
    e20_4h  = ema_pct(a["ema20_4h"])
    e50_4h  = ema_pct(a["ema50_4h"])
    e200_4h = ema_pct(a["ema200_4h"])
    e20_1d  = ema_pct(a["ema20_1d"])
    e50_1d  = ema_pct(a["ema50_1d"])
    e200_1d = ema_pct(a["ema200_1d"])

    lines = [
        f"📊 *{symbol}USDT*  {side_emoji} *{side_text}*",
        "",
        f"💰 *Вход:*  `{fp(price)}`",
        f"🎯 *TP 1:*  `{fp(tp1)}`  ({pct(tp1)})",
        f"🎯 *TP 2:*  `{fp(tp2)}`  ({pct(tp2)})",
        f"🎯 *TP 3:*  `{fp(tp3)}`  ({pct(tp3)})",
        f"🛑 *SL:*    `{fp(sl)}`   ({sl_pct()})",
        f"📌 *{swing_lbl}:*  `{fp(swing)}`",
        "",
        f"━━━━━━━━━━━━━━━━━━",
        f"📐 *R:R:* 1:{rr:.1f}  |  💹 *Объём 24H:* {vol_str}",
        "",
        f"📉 *Скользящие (% от цены):*",
        f"┌ *1H*   EMA20 `{e20_1h}`  EMA50 `{e50_1h}`  EMA200 `{e200_1h}`",
        f"├ *4H*   EMA20 `{e20_4h}`  EMA50 `{e50_4h}`  EMA200 `{e200_4h}`",
        f"└ *1D*   EMA20 `{e20_1d}`  EMA50 `{e50_1d}`  EMA200 `{e200_1d}`",
        "",
        f"📈 *RSI:*  1H {rsi_icon(rsi_1h)} `{rsi_1h:.0f}`  |  4H {rsi_icon(rsi_4h)} `{rsi_4h:.0f}`  |  1D {rsi_icon(rsi_1d)} `{rsi_1d:.0f}`",
    ]
    return "\n".join(lines)

# ═══════════════════════════════════════════
# РЫНОЧНЫЙ ОБЗОР
# ═══════════════════════════════════════════
BTC_ZONES = {
    "support":    [
        {"level": 62137, "label": "S1 Королев (верх)"},
        {"level": 61316, "label": "S2 Королев (низ)"},
        {"level": 59000, "label": "S3 Психологический"},
    ],
    "resistance": [
        {"level": 63800, "label": "R1 Локальное"},
        {"level": 65000, "label": "R2 Ключевой"},
        {"level": 67000, "label": "R3 Верх канала"},
    ],
}

def analyze_market(btc: dict, eth: dict, gm: dict, coins: list) -> dict:
    bp = btc.get("price", 0)
    ep = eth.get("price", 0)
    bd = gm.get("btc_dominance", 0)
    ed = gm.get("eth_dominance", 0)
    od = 100 - bd - ed
    tm = gm.get("total_mcap", 0)
    mc = gm.get("mcap_change_24h", 0)

    sup = next((z for z in BTC_ZONES["support"]    if bp > z["level"]), None)
    res = next((z for z in BTC_ZONES["resistance"] if bp < z["level"]), None)
    if sup: sup["dist"] = (bp - sup["level"]) / bp * 100
    if res: res["dist"] = (res["level"] - bp) / bp * 100

    pos = sum(1 for c in coins if c["quote"]["USDT"].get("percent_change_24h", 0) > 0)
    sp  = pos / len(coins) * 100 if coins else 50

    if sp >= 65:   sent = "🟢 Бычий"
    elif sp >= 50: sent = "🔵 Умеренно бычий"
    elif sp >= 35: sent = "🟠 Умеренно медвежий"
    else:          sent = "🔴 Медвежий"

    dom_sig    = ("🔴 BTC доминирует — альты под давлением" if bd > 59 else
                  ("🟡 BTC.D нейтральна" if bd > 56 else
                   "🟢 BTC.D снижается — деньги в альты"))
    others_sig = ("🔴 Альты слабеют" if od < 8.2 else
                  ("🟢 Альты усиливаются" if od > 8.8 else "🟡 Альты нейтральны"))
    total_sig  = ("🟢 Рынок растёт" if mc >= 2 else
                  ("🔵 Стабильно" if mc >= 0 else
                   ("🟠 Коррекция" if mc >= -2 else "🔴 Рынок падает")))

    bulls = sum([btc.get("ch24h",0)>1, eth.get("ch24h",0)>1,
                 bd<57, mc>0, od>8.3])
    if bulls >= 4:   verdict = "🟢 БЫЧИЙ — ищем лонги"
    elif bulls >= 3: verdict = "🔵 УМЕРЕННО БЫЧИЙ — осторожные лонги"
    elif bulls >= 2: verdict = "🟡 НЕЙТРАЛЬНЫЙ — ждём сигналов"
    elif bulls >= 1: verdict = "🟠 ОСТОРОЖНО — рынок под давлением"
    else:            verdict = "🔴 МЕДВЕЖИЙ — воздерживаемся от лонгов"

    # ── ТОП ЛОНГИ / ШОРТЫ из топ-300 ──
    analyzed = [(c, full_analysis(c)) for c in coins]
    top_longs  = sorted([(c,a) for c,a in analyzed if a["score"] >= 3],
                        key=lambda x: x[1]["score"], reverse=True)[:5]
    top_shorts = sorted([(c,a) for c,a in analyzed if a["score"] <= -3],
                        key=lambda x: x[1]["score"])[:5]

    return {
        "btc_price": bp, "btc_ch24h": btc.get("ch24h", 0),
        "eth_price": ep, "eth_ch24h": eth.get("ch24h", 0),
        "btc_dom": bd, "eth_dom": ed, "others_dom": od,
        "total_mcap": tm, "mcap_ch": mc,
        "btc_sup": sup, "btc_res": res,
        "sentiment": sent, "sentiment_pct": sp,
        "dom_signal": dom_sig, "others_signal": others_sig,
        "total_signal": total_sig, "verdict": verdict,
        "top_longs": top_longs, "top_shorts": top_shorts,
    }

def build_overview_text(ms: dict) -> str:
    now = datetime.now(TZ).strftime("%d.%m.%Y  %H:%M")
    sup = ms["btc_sup"]
    res = ms["btc_res"]
    s_line = f"  └ 🟢 Поддержка: ${sup['level']:,}  ({sup['label']})  —  {sup['dist']:.1f}% ниже" if sup else ""
    r_line = f"  └ 🔴 Сопротивление: ${res['level']:,}  ({res['label']})  —  {res['dist']:.1f}% выше" if res else ""

    # ── ТОП ЛОНГИ ──
    long_lines = []
    for i, (c, a) in enumerate(ms.get("top_longs", []), 1):
        sym = c["symbol"]
        p   = a["price"]
        ch  = a["ch24h"]
        rsi = a["rsi_4h"]
        sign = "+" if ch >= 0 else ""
        long_lines.append(f"  {i}. *{sym}*  ${fp(p)}  {sign}{ch:.1f}%  RSI {rsi:.0f}")

    # ── ТОП ШОРТЫ ──
    short_lines = []
    for i, (c, a) in enumerate(ms.get("top_shorts", []), 1):
        sym = c["symbol"]
        p   = a["price"]
        ch  = a["ch24h"]
        rsi = a["rsi_4h"]
        short_lines.append(f"  {i}. *{sym}*  ${fp(p)}  {ch:.1f}%  RSI {rsi:.0f}")

    long_block  = "\n".join(long_lines)  if long_lines  else "  Нет сигналов"
    short_block = "\n".join(short_lines) if short_lines else "  Нет сигналов"

    return "\n".join([
        "🌍 *ОБЗОР РЫНКА  —  BEST TRADE*",
        f"🕐 {now}  Istanbul",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"{trend_arrow(ms['btc_ch24h'])} *BTC*  ${ms['btc_price']:,.0f}  ({fc(ms['btc_ch24h'])})",
        s_line, r_line,
        "",
        f"{trend_arrow(ms['eth_ch24h'])} *ETH*  ${ms['eth_price']:,.0f}  ({fc(ms['eth_ch24h'])})",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "📊 *ДОМИНАЦИЯ*",
        f"  BTC.D *{ms['btc_dom']:.2f}%*  |  ETH.D {ms['eth_dom']:.2f}%  |  Others {ms['others_dom']:.2f}%",
        f"  {ms['dom_signal']}",
        f"  {ms['others_signal']}",
        "",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"{trend_arrow(ms['mcap_ch'])} *TOTAL*  {fm(ms['total_mcap'])}  ({fc(ms['mcap_ch'])} 24ч)",
        f"  {ms['total_signal']}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🧭 *Настроение:* {ms['sentiment']}",
        f"  Растут {ms['sentiment_pct']:.0f}% монет из топ-300",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "🟢 *ТОП ЛОНГИ* (топ-300)",
        long_block,
        "",
        "🔴 *ТОП ШОРТЫ* (топ-300)",
        short_block,
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🎯 *ВЕРДИКТ:* {ms['verdict']}",
        "",
        "⚠️ Риск: *2% депозита*  |  SL обязателен",
    ])

def overview_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("₿ BTC",    url="https://www.tradingview.com/chart/?symbol=BINANCE:BTCUSDT"),
         InlineKeyboardButton("Ξ ETH",    url="https://www.tradingview.com/chart/?symbol=BINANCE:ETHUSDT")],
        [InlineKeyboardButton("BTC.D",    url="https://www.tradingview.com/chart/?symbol=CRYPTOCAP:BTC.D"),
         InlineKeyboardButton("TOTAL",    url="https://www.tradingview.com/chart/?symbol=CRYPTOCAP:TOTAL"),
         InlineKeyboardButton("OTHERS.D", url="https://www.tradingview.com/chart/?symbol=CRYPTOCAP:OTHERS.D")],
        [InlineKeyboardButton("🔄 Обновить", callback_data="market_overview"),
         InlineKeyboardButton("🤖 Сигналы", callback_data="signals")],
    ])

# ═══════════════════════════════════════════
# ОТЧЁТЫ
# ═══════════════════════════════════════════
def build_market_report(coins: list) -> list:
    now  = datetime.now(TZ)
    up   = sorted(coins, key=lambda x: x["quote"]["USDT"].get("percent_change_24h",0), reverse=True)
    dn   = sorted(coins, key=lambda x: x["quote"]["USDT"].get("percent_change_24h",0))
    pos  = sum(1 for c in coins if c["quote"]["USDT"].get("percent_change_24h",0)>0)
    pct  = pos/len(coins)*100
    mood = "🟢 Бычий" if pct>=60 else ("🔴 Медвежий" if pct<40 else "🟡 Нейтральный")

    def ps(ch): return ("+" if ch>=0 else "")+f"{ch:.2f}%"
    def ar(ch): return "🟢" if ch>=5 else ("🔵" if ch>=0 else ("🟠" if ch>=-5 else "🔴"))

    t1 = [f"🔥 *Обзор рынка — BEST TRADE*",
          f"🕐 {now.strftime('%d.%m.%Y  %H:%M')} Istanbul",
          f"Сентимент: {mood}  |  Растут: {pos}/{len(coins)} ({pct:.0f}%)",
          "", "🚀 *ТОП-15 РОСТ за 24ч*"]
    b1 = []
    for i, c in enumerate(up[:15], 1):
        q  = c["quote"]["USDT"]
        ch = q.get("percent_change_24h", 0)
        t1.append(f"{ar(ch)} {i}. *{c['symbol']}*  ${fp(q['price'])}  {ps(ch)}")
    for c in up[:8]:
        b1.append(InlineKeyboardButton(f"📊 {c['symbol']}", url=cmc_link(c.get("slug",c["symbol"].lower()))))

    t2 = ["📉 *ТОП-15 ПАДЕНИЕ за 24ч*"]
    b2 = []
    for i, c in enumerate(dn[:15], 1):
        q  = c["quote"]["USDT"]
        ch = q.get("percent_change_24h", 0)
        t2.append(f"{ar(ch)} {i}. *{c['symbol']}*  ${fp(q['price'])}  {ps(ch)}")
    t2.extend(["", f"📡 CoinMarketCap • Топ-300 • {now.strftime('%H:%M:%S')} UTC+3"])
    for c in dn[:8]:
        b2.append(InlineKeyboardButton(f"📊 {c['symbol']}", url=cmc_link(c.get("slug",c["symbol"].lower()))))

    return [{"text":"\n".join(t1),"btns":b1}, {"text":"\n".join(t2),"btns":b2}]

def build_signals_report(coins: list) -> list:
    now      = datetime.now(TZ)
    analyzed = [(c, full_analysis(c)) for c in coins]
    longs    = sorted([(c,a) for c,a in analyzed if a["score"]>=3],
                      key=lambda x: x[1]["score"], reverse=True)[:8]
    shorts   = sorted([(c,a) for c,a in analyzed if a["score"]<=-3],
                      key=lambda x: x[1]["score"])[:5]
    results  = []

    header = (f"🤖 *BEST TRADE — Сигналы*\n"
              f"🕐 {now.strftime('%d.%m.%Y  %H:%M')} Istanbul\n\n"
              f"🟢 Лонг: {len(longs)}  |  🔴 Шорт: {len(shorts)}")
    results.append({"type":"text","text":header,"btns":[]})

    for c, a in longs + shorts:
        sym  = c["symbol"]
        slug = c.get("slug", sym.lower())
        results.append({
            "type": "coin", "symbol": sym, "slug": slug,
            "text": build_signal_text(sym, a), "analysis": a,
            "btns": [InlineKeyboardButton("📈 TradingView", url=tv_link(sym))],
        })

    results.append({"type":"text",
                    "text":"⚠️ *Риск:* 2-3% депозита\nСтоп *ВСЕГДА* до входа в сделку!",
                    "btns":[]})
    return results

def build_period_report(period: str, coins: list) -> list:
    fm2 = {"1h":"percent_change_1h","24h":"percent_change_24h","7d":"percent_change_7d"}
    lm  = {"1h":"1 ЧАС","24h":"24 ЧАСА","7d":"7 ДНЕЙ"}
    f   = fm2.get(period,"percent_change_24h")
    l   = lm.get(period,"24 ЧАСА")
    now = datetime.now(TZ)
    def ti(ch): return "🟢" if ch>=5 else ("🔵" if ch>=0 else ("🟠" if ch>=-5 else "🔴"))
    def fc2(ch): return f"+{ch:.2f}%" if ch>=0 else f"{ch:.2f}%"
    up  = sorted(coins, key=lambda x: x["quote"]["USDT"].get(f,0), reverse=True)
    dn  = sorted(coins, key=lambda x: x["quote"]["USDT"].get(f,0))
    t1  = f"📊 BEST TRADE — ТОП за {l}\n🕐 {now.strftime('%H:%M')} Istanbul\n\n🚀 ЛИДЕРЫ РОСТА\n{'─'*26}\n"
    b1  = []
    for i,c in enumerate(up[:15],1):
        ch = c["quote"]["USDT"].get(f,0); p = c["quote"]["USDT"].get("price",0)
        t1 += f"{i:>2}. {c['symbol']:<8} ${fp(p):<14} {ti(ch)} {fc2(ch)}\n"
    for c in up[:8]:
        b1.append(InlineKeyboardButton(f"📊 {c['symbol']}", url=cmc_link(c.get("slug",c["symbol"].lower()))))
    t2 = f"📉 ЛИДЕРЫ ПАДЕНИЯ\n{'─'*26}\n"; b2 = []
    for i,c in enumerate(dn[:15],1):
        ch = c["quote"]["USDT"].get(f,0); p = c["quote"]["USDT"].get("price",0)
        t2 += f"{i:>2}. {c['symbol']:<8} ${fp(p):<14} {ti(ch)} {fc2(ch)}\n"
    for c in dn[:8]:
        b2.append(InlineKeyboardButton(f"📊 {c['symbol']}", url=cmc_link(c.get("slug",c["symbol"].lower()))))
    return [{"text":t1,"btns":b1},{"text":t2,"btns":b2}]

# ═══════════════════════════════════════════
# ОТПРАВКА
# ═══════════════════════════════════════════
def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌍 Обзор рынка",  callback_data="market_overview"),
         InlineKeyboardButton("🤖 Сигналы",      callback_data="signals")],
        [InlineKeyboardButton("📊 Рынок",        callback_data="report"),
         InlineKeyboardButton("⏱ 1ч",           callback_data="period_1h"),
         InlineKeyboardButton("📅 24ч",          callback_data="period_24h")],
    ])

async def send_coin(bot, chat_id, symbol, slug, a, text, extra_btns=[]):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 TradingView", url=tv_link(symbol))],
        [InlineKeyboardButton("🔄 Обновить", callback_data=f"coin_{symbol}"),
         InlineKeyboardButton("CMC", url=cmc_link(slug))],
        [InlineKeyboardButton("🌍 Обзор рынка", callback_data="market_overview"),
         InlineKeyboardButton("🤖 Сигналы",     callback_data="signals")],
    ])
    chart = None
    try:
        chart = generate_signal_chart(symbol, slug, a)
        log.info(f"Chart generated for {symbol}, size={chart.getbuffer().nbytes} bytes")
    except Exception as e:
        log.error(f"Chart generation FAILED {symbol}: {type(e).__name__}: {e}")

    # Telegram caption limit = 1024 chars
    caption = text if len(text) <= 1024 else text[:1020] + "..."

    if chart is not None:
        try:
            chart.seek(0)
            await bot.send_photo(
                chat_id=chat_id,
                photo=chart,
                caption=caption,
                parse_mode="Markdown",
                reply_markup=kb
            )
            log.info(f"send_photo OK: {symbol}")
            return
        except Exception as e:
            log.error(f"send_photo FAILED {symbol}: {type(e).__name__}: {e}")
            # Retry: send photo without caption, then text separately
            try:
                chart.seek(0)
                await bot.send_photo(chat_id=chat_id, photo=chart)
                await bot.send_message(
                    chat_id, text,
                    parse_mode="Markdown",
                    reply_markup=kb,
                    disable_web_page_preview=True
                )
                log.info(f"send_photo (split) OK: {symbol}")
                return
            except Exception as e2:
                log.error(f"send_photo split FAILED {symbol}: {type(e2).__name__}: {e2}")

    # Fallback — только текст
    await bot.send_message(
        chat_id, text,
        parse_mode="Markdown",
        reply_markup=kb,
        disable_web_page_preview=True
    )

async def send_signals(bot, chat_id, coins):
    parts = build_signals_report(coins)
    nav = InlineKeyboardMarkup([[
        InlineKeyboardButton("🌍 Обзор рынка", callback_data="market_overview"),
        InlineKeyboardButton("🤖 Сигналы",     callback_data="signals"),
    ]])
    for part in parts:
        try:
            if part["type"] == "text":
                await bot.send_message(chat_id, part["text"],
                                       parse_mode="Markdown", reply_markup=nav)
            elif part["type"] == "coin":
                await send_coin(bot, chat_id,
                                part["symbol"], part["slug"],
                                part["analysis"], part["text"])
        except Exception as e:
            log.error(f"send_signals error: {e}")

async def send_parts(bot, chat_id, parts, query=None):
    nav_rows = [
        [InlineKeyboardButton("🌍 Обзор рынка",  callback_data="market_overview"),
         InlineKeyboardButton("🤖 Сигналы",      callback_data="signals")],
        [InlineKeyboardButton("⏱ 1ч",  callback_data="period_1h"),
         InlineKeyboardButton("📅 24ч", callback_data="period_24h"),
         InlineKeyboardButton("📆 7д",  callback_data="period_7d")],
    ]
    for i, part in enumerate(parts):
        rows = [part["btns"][j:j+4] for j in range(0, len(part["btns"]), 4)]
        if i == len(parts)-1:
            rows += nav_rows
        kb = InlineKeyboardMarkup(rows) if rows else None
        if query and i == 0:
            await query.edit_message_text(part["text"], parse_mode="Markdown",
                                          reply_markup=kb, disable_web_page_preview=True)
        else:
            await bot.send_message(chat_id, part["text"], parse_mode="Markdown",
                                   reply_markup=kb, disable_web_page_preview=True)

# ═══════════════════════════════════════════
# HANDLERS
# ═══════════════════════════════════════════
user_chat_ids = set()

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    user_chat_ids.add(cid)
    with open("chat_ids.txt","a") as f: f.write(f"{cid}\n")
    await update.message.reply_text(
        "📊 *BEST TRADE v8.0*\n\n"
        "Топ-300 • CoinMarketCap\n"
        "🌍 Рыночный обзор каждые 30 мин\n"
        "📈 Графики с EMA + уровнями + водяным знаком\n"
        "RSI • R:R • Объём • EMA позиция\n"
        "🕐 Стамбул UTC+3\n\n"
        "/market — обзор рынка\n"
        "/coin BTC — анализ монеты\n"
        "/signals — торговые сигналы\n"
        "/top — топ рынка",
        parse_mode="Markdown", reply_markup=main_kb()
    )

async def cmd_market(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Загружаю рыночные данные...")
    try:
        prices = get_btc_eth_price()
        gm     = get_global_metrics()
        coins  = get_top300()
        ms     = analyze_market(prices.get("BTC",{}), prices.get("ETH",{}), gm, coins)
        await msg.edit_text(build_overview_text(ms), parse_mode="Markdown",
                            reply_markup=overview_kb(), disable_web_page_preview=True)
    except Exception as e:
        log.error(f"cmd_market: {e}")
        await msg.edit_text("❌ Ошибка загрузки данных")

async def cmd_coin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Напиши: `/coin BTC`", parse_mode="Markdown")
        return
    symbol = ctx.args[0].upper()
    msg    = await update.message.reply_text(f"⏳ Анализирую {symbol}...")
    coins  = get_top300()
    coin   = next((c for c in coins if c["symbol"]==symbol), None)
    if not coin:
        await msg.edit_text(f"❌ {symbol} не найден в топ-300")
        return
    a    = full_analysis(coin)
    slug = coin.get("slug", symbol.lower())
    text = build_signal_text(symbol, a)
    await msg.delete()
    await send_coin(ctx.bot, update.effective_chat.id, symbol, slug, a, text)

async def cmd_top(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Загружаю...")
    coins = get_top300()
    if not coins:
        await msg.edit_text("❌ Нет данных"); return
    await msg.delete()
    await send_parts(ctx.bot, update.effective_chat.id, build_market_report(coins))

async def cmd_signals(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Анализирую топ-300... ~30 сек")
    coins = get_top300()
    if not coins:
        await msg.edit_text("❌ Нет данных"); return
    await msg.delete()
    await send_signals(ctx.bot, update.effective_chat.id, coins)

async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; data = q.data; await q.answer()

    if data == "market_overview":
        await q.edit_message_text("⏳ Загружаю...", parse_mode="Markdown")
        try:
            prices = get_btc_eth_price(); gm = get_global_metrics(); coins = get_top300()
            ms = analyze_market(prices.get("BTC",{}), prices.get("ETH",{}), gm, coins)
            await q.edit_message_text(build_overview_text(ms), parse_mode="Markdown",
                                      reply_markup=overview_kb(), disable_web_page_preview=True)
        except Exception as e:
            log.error(f"overview cb: {e}")
            await q.edit_message_text("❌ Ошибка")

    elif data in ("report","signals") or data.startswith("period_"):
        await q.edit_message_text("⏳ Загружаю...", parse_mode="Markdown")
        coins = get_top300()
        if not coins:
            await q.edit_message_text("❌ Нет данных"); return
        if data == "report":       parts = build_market_report(coins)
        elif data == "signals":    parts = build_signals_report(coins)
        else:                      parts = build_period_report(data.split("_")[1], coins)
        await send_parts(ctx.bot, q.message.chat_id, parts, query=q)

    elif data.startswith("coin_"):
        symbol  = data[5:]; cid = q.message.chat_id
        await q.edit_message_text(f"⏳ Обновляю {symbol}...")
        coins = get_top300()
        coin  = next((c for c in coins if c["symbol"]==symbol), None)
        if not coin:
            await q.edit_message_text(f"❌ {symbol} не найден"); return
        a    = full_analysis(coin)
        slug = coin.get("slug", symbol.lower())
        text = build_signal_text(symbol, a)
        try: await q.message.delete()
        except: pass
        await send_coin(ctx.bot, cid, symbol, slug, a, text)

# ═══════════════════════════════════════════
# РАССЫЛКА
# ═══════════════════════════════════════════
def load_chat_ids():
    try:
        with open("chat_ids.txt") as f:
            return set(int(l.strip()) for l in f if l.strip())
    except: return set()

async def send_scheduled(bot: Bot):
    chat_ids = load_chat_ids() | user_chat_ids
    if not chat_ids: return
    log.info(f"Рассылка {datetime.now(TZ).strftime('%H:%M')} Istanbul")
    coins  = get_top300()
    prices = get_btc_eth_price()
    gm     = get_global_metrics()
    if not coins: return
    ms   = analyze_market(prices.get("BTC",{}), prices.get("ETH",{}), gm, coins)
    text = build_overview_text(ms)
    kb   = overview_kb()
    for cid in chat_ids:
        try:
            await bot.send_message(cid, text, parse_mode="Markdown",
                                   reply_markup=kb, disable_web_page_preview=True)
            await asyncio.sleep(1)
            await send_signals(bot, cid, coins)
        except Exception as e:
            log.error(f"Рассылка {cid}: {e}")

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
    log.info("✅ BEST TRADE v8.0 | Istanbul UTC+3 | Топ лонги/шорты в обзоре")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
