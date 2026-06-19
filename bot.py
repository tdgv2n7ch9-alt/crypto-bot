#!/usr/bin/env python3
"""
🚀 BEST TRADE Bot v4.0
Свечные графики с брендингом | EMA20/50/200 | Уровни входа/ТП/Стоп
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
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch
from datetime import datetime, timedelta
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
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
        params  = {"limit": 300, "convert": "USDT", "sort": "market_cap"}
        r = requests.get(url, headers=headers, params=params, timeout=15)
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception as e:
        log.error(f"CMC error: {e}")
        return []

def get_price_history(slug: str, symbol: str) -> list:
    """Получить историю цены с CoinGecko (7 дней, 4ч псевдо-свечи)"""
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{slug}/market_chart"
        params = {"vs_currency": "usd", "days": "7", "interval": "hourly"}
        r = requests.get(url, params=params, timeout=12)
        r.raise_for_status()
        data = r.json()
        prices  = data.get("prices", [])
        volumes = data.get("total_volumes", [])
        candles = []
        step = 4
        for i in range(0, len(prices) - step, step):
            chunk_p = [p[1] for p in prices[i:i+step]]
            chunk_v = [v[1] for v in volumes[i:i+step]] if volumes else [0]*step
            ts = prices[i][0] / 1000
            candles.append({
                "time":  datetime.fromtimestamp(ts, tz=TZ),
                "open":  chunk_p[0],
                "high":  max(chunk_p),
                "low":   min(chunk_p),
                "close": chunk_p[-1],
                "vol":   sum(chunk_v),
            })
        return candles
    except Exception as e:
        log.error(f"CoinGecko error {slug}: {e}")
        return []

def cmc_link(slug: str)  -> str: return f"https://coinmarketcap.com/currencies/{slug}/"
def tv_link(symbol: str) -> str: return f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}USDT"

# ═══════════════════════════════════════════════════════════════
# ФОРМАТИРОВАНИЕ
# ═══════════════════════════════════════════════════════════════
def fp(p: float) -> str:
    if p >= 1000:  return f"${p:,.2f}"
    if p >= 1:     return f"${p:.4f}"
    if p >= 0.01:  return f"${p:.5f}"
    return f"${p:.8f}"

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

def trend_icon(ch: float) -> str:
    if ch >= 5:  return "🟢"
    if ch >= 0:  return "🔵"
    if ch >= -5: return "🟠"
    return "🔴"

# ═══════════════════════════════════════════════════════════════
# EMA РАСЧЁТ
# ═══════════════════════════════════════════════════════════════
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

# ═══════════════════════════════════════════════════════════════
# АНАЛИЗ
# ═══════════════════════════════════════════════════════════════
def estimate_rsi(ch1h, ch24h, ch7d) -> dict:
    m = ch1h * 0.5 + ch24h * 0.3 + ch7d * 0.2
    if m > 15:   return {"display": "~80 🔴 Перекуплен",       "sig": "overbought",        "val": 80}
    if m > 7:    return {"display": "~67 🟡 Высокий",          "sig": "high",              "val": 67}
    if m > 2:    return {"display": "~55 🔵 Нейтральный+",     "sig": "neutral_high",      "val": 55}
    if m > -2:   return {"display": "~45 ⚪️ Нейтральный",      "sig": "neutral",           "val": 45}
    if m > -7:   return {"display": "~35 🟡 Низкий",           "sig": "low",               "val": 35}
    if m > -15:  return {"display": "~25 🟢 Перепродан",       "sig": "oversold",          "val": 25}
    return        {"display": "~15 🟢 Сильно перепродан",      "sig": "strongly_oversold", "val": 15}

def estimate_macd(ch1h, ch24h, ch7d) -> dict:
    s = ch1h * 2 + ch24h; l = ch7d
    if s > l + 5:  return {"display": "🟢 Бычий",       "bull": True}
    if s > l:      return {"display": "🔵 Слабо бычий", "bull": True}
    if s < l - 5:  return {"display": "🔴 Медвежий",    "bull": False}
    return         {"display": "🟠 Слабо медвежий",      "bull": False}

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

    rsi  = estimate_rsi(ch1h, ch24h, ch7d)
    macd = estimate_macd(ch1h, ch24h, ch7d)

    # EMA позиция
    ema20_bull  = ch7d > 0
    ema50_bull  = ch30d > -10
    ema200_bull = ch30d > 0
    if ema200_bull and ema50_bull and ema20_bull:
        ema_trend = "🟢 Выше EMA20 / EMA50 / EMA200"; ema_score = 3
    elif ema50_bull and ema20_bull:
        ema_trend = "🔵 Выше EMA20/50, ниже EMA200";  ema_score = 2
    elif ema20_bull:
        ema_trend = "🟠 Выше EMA20, ниже EMA50/200";  ema_score = 1
    elif not ema50_bull and not ema200_bull:
        ema_trend = "🔴 Ниже EMA20 / EMA50 / EMA200"; ema_score = -2
    else:
        ema_trend = "⚪️ Смешанный тренд";             ema_score = 0

    score = 0; reasons = []; warnings = []
    score += ema_score
    if ema_score >= 2:    reasons.append("Цена выше ключевых EMA")
    elif ema_score <= -2: warnings.append("Цена ниже всех EMA")

    if rsi["sig"] in ("oversold","strongly_oversold"):
        score += 2; reasons.append("RSI перепродан → разворот вверх")
    elif rsi["sig"] == "overbought":
        score -= 2; warnings.append("RSI перекуплен → риск коррекции")
    elif rsi["sig"] == "high":
        score -= 1; warnings.append("RSI высокий — ждать откат")

    if macd["bull"]: score += 1; reasons.append("MACD бычий")
    else:            score -= 1; warnings.append("MACD медвежий")

    if ch24h >= 15:    score += 2; reasons.append(f"Сильный импульс +{ch24h:.1f}% за 24ч")
    elif ch24h >= 7:   score += 1; reasons.append(f"Рост +{ch24h:.1f}% за 24ч")
    elif ch24h <= -15: score -= 2; warnings.append(f"Обвал {ch24h:.1f}% за 24ч")
    elif ch24h <= -7:  score -= 1; warnings.append(f"Падение {ch24h:.1f}% за 24ч")

    if ch1h >= 4:    score += 1; reasons.append(f"Пробой вверх +{ch1h:.1f}% за 1ч")
    elif ch1h <= -4: score -= 1; warnings.append(f"Пробой вниз {ch1h:.1f}% за 1ч")

    if vol_ratio >= 25:   score += 2; reasons.append(f"Аномальный объём {vol_ratio:.0f}%")
    elif vol_ratio >= 12: score += 1; reasons.append(f"Повышенный объём {vol_ratio:.0f}%")
    elif vol_ratio < 2:   warnings.append("Низкий объём — осторожно")

    is_long = score >= 0
    atr_pct = max(abs(ch24h) / 100, 0.03)

    if is_long:
        entry = price * (1 - atr_pct * 0.5)
        stop  = entry  * (1 - atr_pct * 1.5)
        tp1   = price  * (1 + atr_pct * 0.7)
        tp2   = price  * (1 + atr_pct * 1.5)
        tp3   = price  * (1 + atr_pct * 3.0)
        swing = price  * (1 - atr_pct * 0.3)
        tp1_pct = (tp1 - entry) / entry * 100
        tp2_pct = (tp2 - entry) / entry * 100
        tp3_pct = (tp3 - entry) / entry * 100
        sl_pct  = (stop - entry) / entry * 100
    else:
        entry = price * (1 + atr_pct * 0.5)
        stop  = entry  * (1 + atr_pct * 1.5)
        tp1   = price  * (1 - atr_pct * 0.7)
        tp2   = price  * (1 - atr_pct * 1.5)
        tp3   = price  * (1 - atr_pct * 3.0)
        swing = price  * (1 + atr_pct * 0.3)
        tp1_pct = (entry - tp1) / entry * 100
        tp2_pct = (entry - tp2) / entry * 100
        tp3_pct = (entry - tp3) / entry * 100
        sl_pct  = (stop  - entry) / entry * 100

    rr = abs(tp1_pct / sl_pct) if sl_pct != 0 else 0

    if score >= 6:    label = "🔥 СИЛЬНЫЙ ЛОНГ"; side = "🟢 LONG";  action = "ПОКУПАТЬ"
    elif score >= 4:  label = "✅ ЛОНГ";          side = "🟢 LONG";  action = "ИСКАТЬ ВХОД"
    elif score >= 2:  label = "📈 СЛАБЫЙ ЛОНГ";  side = "🔵 LONG";  action = "НАБЛЮДАТЬ"
    elif score >= -1: label = "⚪️ НЕЙТРАЛЬНО";   side = "⚪️ WAIT";  action = "В СТОРОНЕ"
    elif score >= -3: label = "📉 СЛАБЫЙ ШОРТ";  side = "🟠 SHORT"; action = "ОСТОРОЖНО"
    elif score >= -5: label = "🔻 ШОРТ";          side = "🔴 SHORT"; action = "ШОРТИТЬ"
    else:             label = "💥 СИЛЬНЫЙ ШОРТ"; side = "🔴 SHORT"; action = "АКТИВНЫЙ ШОРТ"

    if score >= 4:    when = "Сейчас или при откате к зоне входа"
    elif score >= 2:  when = "Ждать RSI < 40 + подтверждение свечой"
    elif score >= -1: when = "Не входить — нет направления"
    elif score >= -3: when = "Шорт от сопротивления / EMA200"
    else:             when = "Шорт активен. Стоп выше последнего максимума"

    return {
        "label": label, "side": side, "action": action, "when": when,
        "score": score, "is_long": is_long,
        "ema_trend": ema_trend, "ema20": ema20_bull, "ema50": ema50_bull, "ema200": ema200_bull,
        "rsi": rsi, "macd": macd,
        "reasons": reasons, "warnings": warnings,
        "ch1h": ch1h, "ch24h": ch24h, "ch7d": ch7d, "ch30d": ch30d,
        "vol_ratio": vol_ratio, "vol": vol, "price": price, "mcap": mcap,
        "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2, "tp3": tp3, "swing": swing,
        "tp1_pct": tp1_pct, "tp2_pct": tp2_pct, "tp3_pct": tp3_pct,
        "sl_pct": sl_pct, "rr": f"{rr:.1f}:1",
    }

# ═══════════════════════════════════════════════════════════════
# ГЕНЕРАЦИЯ ГРАФИКА — BEST TRADE STYLE
# ═══════════════════════════════════════════════════════════════
def generate_chart(symbol: str, a: dict, candles: list) -> io.BytesIO:
    """Рисуем свечной график в стиле BEST TRADE"""

    # ── Параметры ──
    BG      = "#0D0D0D"
    GRID    = "#1A1A2E"
    GREEN   = "#00FF88"
    RED     = "#FF3355"
    BLUE    = "#4A9EFF"
    GOLD    = "#FFD700"
    WHITE   = "#FFFFFF"
    GRAY    = "#888888"
    EMA20C  = "#FFD700"   # золотой
    EMA50C  = "#4A9EFF"   # синий
    EMA200C = "#FF4444"   # красный

    fig = plt.figure(figsize=(14, 8), facecolor=BG)
    ax  = fig.add_axes([0.06, 0.18, 0.72, 0.72], facecolor=BG)
    ax_vol = fig.add_axes([0.06, 0.05, 0.72, 0.12], facecolor=BG)

    if candles and len(candles) >= 10:
        closes = [c["close"] for c in candles]
        highs  = [c["high"]  for c in candles]
        lows   = [c["low"]   for c in candles]
        opens  = [c["open"]  for c in candles]
        vols   = [c["vol"]   for c in candles]
        times  = list(range(len(candles)))

        # EMA линии
        ema20  = calc_ema(closes, 20)
        ema50  = calc_ema(closes, 50)
        ema200 = calc_ema(closes, min(200, len(closes)))

        # Свечи
        w = 0.4
        for i, c in enumerate(candles):
            color = GREEN if c["close"] >= c["open"] else RED
            ax.plot([i, i], [c["low"], c["high"]], color=color, linewidth=0.8, zorder=2)
            rect = plt.Rectangle((i - w/2, min(c["open"], c["close"])),
                                  w, abs(c["close"] - c["open"]),
                                  color=color, zorder=3)
            ax.add_patch(rect)

        # EMA линии
        valid20  = [(i, v) for i, v in enumerate(ema20)  if v is not None]
        valid50  = [(i, v) for i, v in enumerate(ema50)  if v is not None]
        valid200 = [(i, v) for i, v in enumerate(ema200) if v is not None]

        if valid20:
            ax.plot([x[0] for x in valid20],  [x[1] for x in valid20],
                    color=EMA20C,  linewidth=1.2, label="EMA20",  zorder=4, alpha=0.9)
        if valid50:
            ax.plot([x[0] for x in valid50],  [x[1] for x in valid50],
                    color=EMA50C,  linewidth=1.2, label="EMA50",  zorder=4, alpha=0.9, linestyle="--")
        if valid200:
            ax.plot([x[0] for x in valid200], [x[1] for x in valid200],
                    color=EMA200C, linewidth=1.5, label="EMA200", zorder=4, alpha=0.9, linestyle=":")

        # Уровни входа/ТП/Стоп
        n = len(candles)
        ext = n * 0.15  # продолжение уровней вправо

        def draw_level(price, color, label, ls="-", lw=1.2):
            ax.axhline(y=price, color=color, linestyle=ls, linewidth=lw,
                       alpha=0.85, zorder=5, xmax=0.88)
            ax.text(n - 1 + ext * 0.3, price, f" {label}: {fp(price)}",
                    color=color, fontsize=7.5, va="center",
                    fontweight="bold", zorder=6)

        draw_level(a["entry"], WHITE,  "Entry", "-",  1.5)
        draw_level(a["stop"],  RED,    "SL",    "--", 1.2)
        draw_level(a["swing"], BLUE,   "Swing", ":",  1.0)
        draw_level(a["tp1"],   "#00DDAA", "TP1",  "-",  1.0)
        draw_level(a["tp2"],   "#00BBAA", "TP2",  "-",  1.0)
        draw_level(a["tp3"],   "#009999", "TP3",  "-",  1.0)

        # Зона стопа
        stop_color = RED if not a["is_long"] else GREEN
        ax.axhspan(a["entry"], a["stop"],
                   alpha=0.08, color=RED, zorder=1)
        ax.axhspan(a["entry"], a["tp1"],
                   alpha=0.06, color=GREEN, zorder=1)

        # Объём
        for i, c in enumerate(candles):
            color = GREEN if c["close"] >= c["open"] else RED
            ax_vol.bar(i, c["vol"], color=color, alpha=0.6, width=0.8)

        # Легенда EMA
        ax.legend(loc="upper left", fontsize=8,
                  facecolor=GRID, edgecolor=GRAY,
                  labelcolor=WHITE, framealpha=0.8)

        # X ось — время
        tick_step = max(len(candles) // 6, 1)
        tick_idxs = list(range(0, len(candles), tick_step))
        tick_lbls = [candles[i]["time"].strftime("%d.%m\n%H:%M") for i in tick_idxs]
        ax.set_xticks(tick_idxs)
        ax.set_xticklabels(tick_lbls, fontsize=7, color=GRAY)
        ax_vol.set_xticks([])

        ax.set_xlim(-1, n + ext)
        ax_vol.set_xlim(-1, n + ext)

    else:
        # Нет данных — рисуем заглушку
        ax.text(0.5, 0.5, f"Нет данных Binance\nдля {symbol}/USDT",
                ha="center", va="center", color=GRAY,
                fontsize=14, transform=ax.transAxes)

    # ── Сетка ──
    ax.grid(color=GRID, linewidth=0.5, zorder=0)
    ax.tick_params(colors=GRAY, labelsize=8)
    ax.spines[:].set_color(GRID)
    ax_vol.tick_params(colors=GRAY, labelsize=7)
    ax_vol.spines[:].set_color(GRID)
    ax_vol.yaxis.set_visible(False)

    # ── Заголовок ──
    side_label = "LONG" if a["is_long"] else "SHORT"
    side_color = GREEN if a["is_long"] else RED
    ax.set_title(
        f"{symbol}/USDT  •  1h  •  {side_label}",
        color=side_color, fontsize=13, fontweight="bold", pad=10
    )

    # ── Инфо-панель справа ──
    panel_ax = fig.add_axes([0.79, 0.05, 0.20, 0.85], facecolor=GRID)
    panel_ax.set_xlim(0, 1)
    panel_ax.set_ylim(0, 1)
    panel_ax.axis("off")

    def ptext(x, y, txt, color=WHITE, size=8, bold=False, ha="left"):
        panel_ax.text(x, y, txt, color=color, fontsize=size,
                      fontweight="bold" if bold else "normal",
                      ha=ha, va="center", transform=panel_ax.transAxes)

    ptext(0.5, 0.97, "BEST TRADE", color=GOLD, size=11, bold=True, ha="center")
    ptext(0.5, 0.91, "─" * 18,    color=GRAY, size=7, ha="center")

    ptext(0.05, 0.86, "💰 ВХОД",   color=GRAY,  size=7, bold=True)
    ptext(0.05, 0.82, fp(a["entry"]), color=WHITE, size=9, bold=True)

    ptext(0.05, 0.76, "─" * 18, color=GRID, size=7)

    ptext(0.05, 0.73, "🎯 ТП1",  color="#00DDAA", size=7, bold=True)
    ptext(0.05, 0.69, f"{fp(a['tp1'])}  (+{a['tp1_pct']:.2f}%)", color="#00DDAA", size=7.5)

    ptext(0.05, 0.65, "🎯 ТП2",  color="#00BBAA", size=7, bold=True)
    ptext(0.05, 0.61, f"{fp(a['tp2'])}  (+{a['tp2_pct']:.2f}%)", color="#00BBAA", size=7.5)

    ptext(0.05, 0.57, "🎯 ТП3",  color="#009999", size=7, bold=True)
    ptext(0.05, 0.53, f"{fp(a['tp3'])}  (+{a['tp3_pct']:.2f}%)", color="#009999", size=7.5)

    ptext(0.05, 0.48, "─" * 18, color=GRID, size=7)

    ptext(0.05, 0.45, "🛑 СТОП", color=RED, size=7, bold=True)
    ptext(0.05, 0.41, f"{fp(a['stop'])}  ({a['sl_pct']:.2f}%)", color=RED, size=7.5)

    ptext(0.05, 0.37, "📌 SWING", color=BLUE, size=7, bold=True)
    ptext(0.05, 0.33, fp(a["swing"]), color=BLUE, size=7.5)

    ptext(0.05, 0.29, "⚖️  R/R", color=GOLD, size=7, bold=True)
    ptext(0.05, 0.25, a["rr"], color=GOLD, size=9, bold=True)

    ptext(0.05, 0.20, "─" * 18, color=GRID, size=7)

    e20  = "✅" if a["ema20"]  else "❌"
    e50  = "✅" if a["ema50"]  else "❌"
    e200 = "✅" if a["ema200"] else "❌"
    ptext(0.05, 0.17, f"EMA20 {e20}", color=EMA20C,  size=7)
    ptext(0.05, 0.13, f"EMA50 {e50}", color=EMA50C,  size=7)
    ptext(0.05, 0.09, f"EMA200{e200}",color=EMA200C, size=7)

    now = datetime.now(TZ)
    ptext(0.5, 0.03,
          f"{now.strftime('%d.%m.%Y %H:%M')} IST",
          color=GRAY, size=6.5, ha="center")

    # ── Водяной знак ──
    fig.text(0.42, 0.50, "BEST TRADE",
             fontsize=52, color="white", alpha=0.04,
             ha="center", va="center", fontweight="bold",
             rotation=25, zorder=0)

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                facecolor=BG, edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf

# ═══════════════════════════════════════════════════════════════
# ТЕКСТОВАЯ КАРТОЧКА (запасная / для сводки)
# ═══════════════════════════════════════════════════════════════
def coin_text(symbol: str, coin: dict, a: dict) -> str:
    slug = coin.get("slug", symbol.lower())
    e20  = "✅" if a["ema20"]  else "❌"
    e50  = "✅" if a["ema50"]  else "❌"
    e200 = "✅" if a["ema200"] else "❌"
    bar_n   = min(int(a["vol_ratio"] / 4), 10)
    vol_bar = "█" * bar_n + "░" * (10 - bar_n)
    now  = datetime.now(TZ)

    reasons_str  = "\n".join([f"   ✅ {r}" for r in a["reasons"]])  or "   —"
    warnings_str = "\n".join([f"   ⚠️ {w}" for w in a["warnings"]]) or "   —"

    return (
        f"{'━'*32}\n"
        f"  📊 BEST TRADE\n"
        f"  {coin['name']} / USDT  {a['side']}\n"
        f"  #{coin.get('cmc_rank','?')} CMC\n"
        f"{'━'*32}\n"
        f"  💰 Точка входа:  {fp(a['entry'])}\n"
        f"{'━'*32}\n"
        f"  🎯 ТП1:  {fp(a['tp1'])}  (+{a['tp1_pct']:.2f}%)\n"
        f"  🎯 ТП2:  {fp(a['tp2'])}  (+{a['tp2_pct']:.2f}%)\n"
        f"  🎯 ТП3:  {fp(a['tp3'])}  (+{a['tp3_pct']:.2f}%)\n"
        f"{'━'*32}\n"
        f"  🛑 Стоп-лосс:  {fp(a['stop'])}  ({a['sl_pct']:.2f}%)\n"
        f"  📌 Swing:  {fp(a['swing'])}\n"
        f"  ⚖️  R/R:    {a['rr']}\n"
        f"{'━'*32}\n"
        f"  EMA20 {e20}  EMA50 {e50}  EMA200 {e200}\n"
        f"  {a['ema_trend']}\n"
        f"  RSI:  {a['rsi']['display']}\n"
        f"  MACD: {a['macd']['display']}\n"
        f"  Объём: {a['vol_ratio']:.1f}%  [{vol_bar}]\n"
        f"{'━'*32}\n"
        f"  1ч  {trend_icon(a['ch1h'])} {fc(a['ch1h'])}\n"
        f"  24ч {trend_icon(a['ch24h'])} {fc(a['ch24h'])}\n"
        f"  7д  {trend_icon(a['ch7d'])} {fc(a['ch7d'])}\n"
        f"{'━'*32}\n"
        f"  СИГНАЛ:   {a['label']}\n"
        f"  ДЕЙСТВИЕ: {a['action']}\n"
        f"  КОГДА:    {a['when']}\n"
        f"{'─'*32}\n"
        f"  Факторы:\n{reasons_str}\n"
        f"  Предупреждения:\n{warnings_str}\n"
        f"{'━'*32}\n"
        f"  🕐 {now.strftime('%d.%m.%Y %H:%M')} Istanbul\n"
        f"{'━'*32}"
    )

# ═══════════════════════════════════════════════════════════════
# СВОДКА РЫНКА
# ═══════════════════════════════════════════════════════════════
def build_market_report(coins: list) -> list:
    now = datetime.now(TZ)
    up  = sorted(coins, key=lambda x: x["quote"]["USDT"].get("percent_change_24h", 0), reverse=True)
    dn  = sorted(coins, key=lambda x: x["quote"]["USDT"].get("percent_change_24h", 0))
    pos = sum(1 for c in coins if c["quote"]["USDT"].get("percent_change_24h", 0) > 0)
    pct = pos / len(coins) * 100
    mood = "🟢 БЫЧИЙ" if pct >= 60 else ("🔴 МЕДВЕЖИЙ" if pct < 40 else "🟡 НЕЙТРАЛЬНЫЙ")

    t1  = f"{'━'*32}\n"
    t1 += f"  📊 BEST TRADE — ОБЗОР РЫНКА\n"
    t1 += f"  🕐 {now.strftime('%d.%m.%Y %H:%M')} Istanbul\n"
    t1 += f"{'━'*32}\n"
    t1 += f"  Сентимент: {mood}\n"
    t1 += f"  Растут: {pos}/{len(coins)} ({pct:.0f}%)\n"
    t1 += f"{'─'*32}\n"
    t1 += f"  🚀 ТОП-15 РОСТ за 24ч\n"
    t1 += f"{'─'*32}\n"
    b1 = []
    for i, c in enumerate(up[:15], 1):
        q  = c["quote"]["USDT"]
        ch = q.get("percent_change_24h", 0)
        t1 += f"  {i:>2}. {c['symbol']:<8} {fp(q['price']):<14} {trend_icon(ch)} {fc(ch)}\n"
    for c in up[:8]:
        b1.append(InlineKeyboardButton(f"📊 {c['symbol']}", url=cmc_link(c.get("slug", c["symbol"].lower()))))

    t2  = f"{'─'*32}\n  📉 ТОП-15 ПАДЕНИЕ за 24ч\n{'─'*32}\n"
    b2 = []
    for i, c in enumerate(dn[:15], 1):
        q  = c["quote"]["USDT"]
        ch = q.get("percent_change_24h", 0)
        t2 += f"  {i:>2}. {c['symbol']:<8} {fp(q['price']):<14} {trend_icon(ch)} {fc(ch)}\n"
    t2 += f"{'━'*32}\n  📡 CoinMarketCap • Топ-300\n{'━'*32}"
    for c in dn[:8]:
        b2.append(InlineKeyboardButton(f"📊 {c['symbol']}", url=cmc_link(c.get("slug", c["symbol"].lower()))))

    return [
        {"text": f"```\n{t1}```", "btns": b1},
        {"text": f"```\n{t2}```", "btns": b2},
    ]

def build_signals_text(coins: list) -> list:
    now      = datetime.now(TZ)
    analyzed = [(c, full_analysis(c)) for c in coins]
    longs    = sorted([(c,a) for c,a in analyzed if a["score"] >= 4],  key=lambda x: x[1]["score"], reverse=True)[:10]
    shorts   = sorted([(c,a) for c,a in analyzed if a["score"] <= -4], key=lambda x: x[1]["score"])[:10]

    def blk(c, a):
        e20 = "✅" if a["ema20"] else "❌"
        e50 = "✅" if a["ema50"] else "❌"
        e200= "✅" if a["ema200"] else "❌"
        return (
            f"{'─'*32}\n"
            f"  📊 {c['symbol']}/USDT  {a['side']}\n"
            f"  💰 Вход: {fp(a['entry'])}\n"
            f"  🎯 ТП1: {fp(a['tp1'])}  (+{a['tp1_pct']:.2f}%)\n"
            f"  🎯 ТП2: {fp(a['tp2'])}  (+{a['tp2_pct']:.2f}%)\n"
            f"  🎯 ТП3: {fp(a['tp3'])}  (+{a['tp3_pct']:.2f}%)\n"
            f"  🛑 Стоп: {fp(a['stop'])}  ({a['sl_pct']:.2f}%)\n"
            f"  ⚖️  R/R: {a['rr']}\n"
            f"  EMA: {e20}20 {e50}50 {e200}200\n"
            f"  RSI: {a['rsi']['display'].split()[0]}  "
            f"MACD: {'🟢' if a['macd']['bull'] else '🔴'}\n"
            f"  ⚡ {a['action']}: {a['when']}\n"
        )

    t1  = f"{'━'*32}\n  🤖 BEST TRADE — СИГНАЛЫ\n"
    t1 += f"  🕐 {now.strftime('%d.%m.%Y %H:%M')} Istanbul\n{'━'*32}\n"
    t1 += f"  🟢 ЛОНГ ({len(longs)} монет)\n"
    b1 = []
    for c, a in (longs or []):
        t1 += blk(c, a)
        b1.append(InlineKeyboardButton(f"📊 {c['symbol']}", url=cmc_link(c.get("slug", c["symbol"].lower()))))
    if not longs:
        t1 += f"{'─'*32}\n  Нет явных лонг-сигналов\n"

    t2  = f"{'━'*32}\n  🔴 ШОРТ ({len(shorts)} монет)\n{'━'*32}\n"
    b2 = []
    for c, a in (shorts or []):
        t2 += blk(c, a)
        b2.append(InlineKeyboardButton(f"📊 {c['symbol']}", url=cmc_link(c.get("slug", c["symbol"].lower()))))
    if not shorts:
        t2 += f"{'─'*32}\n  Нет явных шорт-сигналов\n"
    t2 += f"{'━'*32}\n  ⚠️ Риск на сделку: 2-3%\n  Стоп ВСЕГДА до входа!\n{'━'*32}"

    return [
        {"text": f"```\n{t1}```", "btns": b1},
        {"text": f"```\n{t2}```", "btns": b2},
    ]

def build_period_report(period: str, coins: list) -> list:
    field_map = {"1h": "percent_change_1h", "24h": "percent_change_24h", "7d": "percent_change_7d"}
    label_map = {"1h": "1 ЧАС", "24h": "24 ЧАСА", "7d": "7 ДНЕЙ"}
    field = field_map.get(period, "percent_change_24h")
    label = label_map.get(period, "24 ЧАСА")
    now   = datetime.now(TZ)
    up = sorted(coins, key=lambda x: x["quote"]["USDT"].get(field, 0), reverse=True)
    dn = sorted(coins, key=lambda x: x["quote"]["USDT"].get(field, 0))

    t1  = f"{'━'*32}\n  📊 BEST TRADE — ТОП за {label}\n"
    t1 += f"  🕐 {now.strftime('%H:%M')} Istanbul\n{'━'*32}\n  🚀 ЛИДЕРЫ РОСТА\n{'─'*32}\n"
    b1 = []
    for i, c in enumerate(up[:15], 1):
        ch = c["quote"]["USDT"].get(field, 0)
        t1 += f"  {i:>2}. {c['symbol']:<8} {fp(c['quote']['USDT']['price']):<14} {trend_icon(ch)} {fc(ch)}\n"
    for c in up[:8]:
        b1.append(InlineKeyboardButton(f"📊 {c['symbol']}", url=cmc_link(c.get("slug", c["symbol"].lower()))))

    t2  = f"{'─'*32}\n  📉 ЛИДЕРЫ ПАДЕНИЯ\n{'─'*32}\n"
    b2 = []
    for i, c in enumerate(dn[:15], 1):
        ch = c["quote"]["USDT"].get(field, 0)
        t2 += f"  {i:>2}. {c['symbol']:<8} {fp(c['quote']['USDT']['price']):<14} {trend_icon(ch)} {fc(ch)}\n"
    t2 += f"{'━'*32}"
    for c in dn[:8]:
        b2.append(InlineKeyboardButton(f"📊 {c['symbol']}", url=cmc_link(c.get("slug", c["symbol"].lower()))))

    return [
        {"text": f"```\n{t1}```", "btns": b1},
        {"text": f"```\n{t2}```", "btns": b2},
    ]

# ═══════════════════════════════════════════════════════════════
# КЛАВИАТУРЫ + ОТПРАВКА
# ═══════════════════════════════════════════════════════════════
def main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Рынок",    callback_data="report"),
         InlineKeyboardButton("🤖 Сигналы", callback_data="signals")],
        [InlineKeyboardButton("⏱ 1ч",  callback_data="period_1h"),
         InlineKeyboardButton("📅 24ч", callback_data="period_24h"),
         InlineKeyboardButton("📆 7д",  callback_data="period_7d")],
    ])

async def send_parts(bot, chat_id, parts: list, query=None):
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
            await query.edit_message_text(text, parse_mode="Markdown",
                                          reply_markup=kb, disable_web_page_preview=True)
        else:
            await bot.send_message(chat_id, text, parse_mode="Markdown",
                                   reply_markup=kb, disable_web_page_preview=True)

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
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "   📊  B E S T  T R A D E\n"
        "        Expert Bot  v4.0\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  Топ-300 • CoinMarketCap\n"
        "  EMA20 / EMA50 / EMA200\n"
        "  RSI  •  MACD  •  Объём\n"
        "  Вход + ТП1/2/3 + Стоп + R/R\n"
        "  📸 Свечные графики с уровнями\n"
        "  🔗 CMC + TradingView ссылки\n"
        "  🕐 Рассылка каждые 30 минут\n"
        "     Стамбул  UTC+3\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  /coin BTC  — график + анализ\n"
        "  /top       — топ рынка\n"
        "  /signals   — сигналы\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "```",
        parse_mode="Markdown",
        reply_markup=main_kb()
    )

async def cmd_coin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Напиши: `/coin BTC`", parse_mode="Markdown")
        return
    symbol = ctx.args[0].upper()
    msg = await update.message.reply_text(f"⏳ Анализирую *{symbol}*...", parse_mode="Markdown")
    coins  = get_top300()
    coin   = next((c for c in coins if c["symbol"] == symbol), None)
    if not coin:
        await msg.edit_text(f"❌ *{symbol}* не найден в топ-300", parse_mode="Markdown")
        return

    a      = full_analysis(coin)
    slug   = coin.get("slug", symbol.lower())
    candles = get_price_history(coin.get('slug', symbol.lower()), symbol)

    # Генерируем график
    try:
        chart_buf = generate_chart(symbol, a, candles)
        caption = f"```\n{coin_text(symbol, coin, a)}```"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("📈 CoinMarketCap", url=cmc_link(slug)),
            InlineKeyboardButton("📊 TradingView",   url=tv_link(symbol)),
        ],[
            InlineKeyboardButton("🔄 Обновить",      callback_data=f"coin_{symbol}"),
            InlineKeyboardButton("◀️ Назад",          callback_data="report"),
        ]])
        await msg.delete()
        await update.message.reply_photo(
            photo=chart_buf,
            caption=caption,
            parse_mode="Markdown",
            reply_markup=kb
        )
    except Exception as e:
        log.error(f"Chart error: {e}")
        # Запасной вариант — только текст
        text = f"```\n{coin_text(symbol, coin, a)}```"
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
        await msg.edit_text("❌ Нет данных. Попробуй позже.")
        return
    await msg.delete()
    await send_parts(ctx.bot, update.effective_chat.id, build_market_report(coins))

async def cmd_signals(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Анализирую топ-300...")
    coins = get_top300()
    if not coins:
        await msg.edit_text("❌ Нет данных.")
        return
    await msg.delete()
    await send_parts(ctx.bot, update.effective_chat.id, build_signals_text(coins))

async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    data = q.data
    await q.answer()

    if data in ("report", "signals") or data.startswith("period_"):
        await q.edit_message_text("⏳ Загружаю данные...", parse_mode="Markdown")
        coins = get_top300()
        if not coins:
            await q.edit_message_text("❌ Нет данных.")
            return
        if data == "report":
            parts = build_market_report(coins)
        elif data == "signals":
            parts = build_signals_text(coins)
        else:
            parts = build_period_report(data.split("_")[1], coins)
        await send_parts(ctx.bot, q.message.chat_id, parts, query=q)

    elif data.startswith("coin_"):
        symbol  = data[5:]
        chat_id = q.message.chat_id
        await q.edit_message_text(f"⏳ Обновляю *{symbol}*...", parse_mode="Markdown")
        coins  = get_top300()
        coin   = next((c for c in coins if c["symbol"] == symbol), None)
        if not coin:
            await q.edit_message_text(f"❌ {symbol} не найден")
            return
        a       = full_analysis(coin)
        slug    = coin.get("slug", symbol.lower())
        candles = get_price_history(coin.get('slug', symbol.lower()), symbol)
        try:
            chart_buf = generate_chart(symbol, a, candles)
            caption   = f"```\n{coin_text(symbol, coin, a)}```"
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("📈 CoinMarketCap", url=cmc_link(slug)),
                InlineKeyboardButton("📊 TradingView",   url=tv_link(symbol)),
            ],[
                InlineKeyboardButton("🔄 Обновить", callback_data=f"coin_{symbol}"),
                InlineKeyboardButton("◀️ Назад",     callback_data="report"),
            ]])
            await q.message.delete()
            await ctx.bot.send_photo(chat_id=chat_id, photo=chart_buf,
                                     caption=caption, parse_mode="Markdown", reply_markup=kb)
        except Exception as e:
            log.error(f"Chart error: {e}")
            text = f"```\n{coin_text(symbol, coin, a)}```"
            kb   = InlineKeyboardMarkup([[
                InlineKeyboardButton("📈 CoinMarketCap", url=cmc_link(slug)),
                InlineKeyboardButton("📊 TradingView",   url=tv_link(symbol)),
                InlineKeyboardButton("🔄 Обновить",      callback_data=f"coin_{symbol}"),
            ]])
            await ctx.bot.send_message(chat_id, text, parse_mode="Markdown",
                                       reply_markup=kb, disable_web_page_preview=True)

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
    log.info(f"Рассылка {datetime.now(TZ).strftime('%H:%M')} Istanbul")
    coins = get_top300()
    if not coins:
        return
    parts = build_market_report(coins) + build_signals_text(coins)
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
    log.info("✅ BEST TRADE Bot v4.0 | Istanbul UTC+3")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
