#!/usr/bin/env python3
"""
📊 BEST TRADE Bot v9.0
- Топ-500 CMC
- /1 market  /2 BTC coin  /3 signals  /4 top
- Время UTC+3 везде
- График с EMA20/50/200 (200 свечей)
- Хештег #SYMBOLUSDT внизу каждого поста
- Pump/Dump детектор (каждые 5 мин)
- Алерт входа в зону (каждые 5 мин)
- Min/Max дня + лучшая точка входа
- Обзор рынка: 🚀 лонги / 📉 шорты с эмодзи
- Рассылка каждые 30 мин
"""

import asyncio
import io
import logging
import os
import random
import requests
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

# ── КЭШ для pump/dump и алертов ──
price_cache      = {}   # {symbol: [price1, price2, ...]} последние цены
alerted_zones    = {}   # {symbol: timestamp} чтобы не спамить
pump_alerted     = {}   # {symbol: timestamp}

# ═══════════════════════════════════════════
# DATA FUNCTIONS
# ═══════════════════════════════════════════
def get_top500():
    try:
        url     = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
        headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
        params  = {"limit": 500, "convert": "USDT", "sort": "market_cap"}
        r = requests.get(url, headers=headers, params=params, timeout=20)
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception as e:
        log.error(f"CMC error: {e}")
        return []

def get_global_metrics() -> dict:
    try:
        url     = "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest"
        headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        d = r.json().get("data", {})
        q = d.get("quote", {}).get("USD", {})
        return {
            "total_mcap":      q.get("total_market_cap", 0),
            "btc_dominance":   d.get("btc_dominance", 0),
            "eth_dominance":   d.get("eth_dominance", 0),
            "mcap_change_24h": q.get("total_market_cap_yesterday_percentage_change", 0),
        }
    except Exception as e:
        log.error(f"Global metrics error: {e}")
        return {}

def get_btc_eth_price() -> dict:
    try:
        url     = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
        headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
        params  = {"symbol": "BTC,ETH", "convert": "USD"}
        r = requests.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        data   = r.json().get("data", {})
        result = {}
        for sym in ["BTC", "ETH"]:
            if sym in data:
                item = data[sym]
                q    = (item[0] if isinstance(item, list) else item)["quote"]["USD"]
                result[sym] = {
                    "price": q.get("price", 0),
                    "ch1h":  q.get("percent_change_1h", 0),
                    "ch24h": q.get("percent_change_24h", 0),
                }
        return result
    except Exception as e:
        log.error(f"BTC/ETH price error: {e}")
        return {}

def get_binance_ohlc(symbol: str, interval: str = "4h", limit: int = 200) -> list:
    try:
        url    = "https://api.binance.com/api/v3/klines"
        params = {"symbol": f"{symbol}USDT", "interval": interval, "limit": limit}
        r      = requests.get(url, params=params, timeout=12)
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

def get_binance_24h(symbol: str) -> dict:
    """24h stats: high, low, open"""
    try:
        url    = "https://api.binance.com/api/v3/ticker/24hr"
        params = {"symbol": f"{symbol}USDT"}
        r      = requests.get(url, params=params, timeout=8)
        r.raise_for_status()
        d = r.json()
        return {
            "high":  float(d.get("highPrice", 0)),
            "low":   float(d.get("lowPrice", 0)),
            "open":  float(d.get("openPrice", 0)),
            "vol":   float(d.get("quoteVolume", 0)),
        }
    except:
        return {}

def get_binance_alltime_low(symbol: str) -> float:
    """Исторический минимум — берём monthly свечи"""
    try:
        url    = "https://api.binance.com/api/v3/klines"
        params = {"symbol": f"{symbol}USDT", "interval": "1M", "limit": 200}
        r      = requests.get(url, params=params, timeout=12)
        r.raise_for_status()
        lows = [float(d[3]) for d in r.json()]
        return min(lows) if lows else 0
    except:
        return 0

# ═══════════════════════════════════════════
# ФОРМАТИРОВАНИЕ
# ═══════════════════════════════════════════
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

def now_utc3() -> str:
    return datetime.now(TZ).strftime("%d.%m.%Y %H:%M UTC+3")

def trend_arrow(ch):
    if ch >= 3:  return "🚀"
    if ch >= 0:  return "🟢"
    if ch >= -3: return "🔴"
    return "💥"

def cmc_link(slug):  return f"https://coinmarketcap.com/currencies/{slug}/"
def tv_link(symbol): return f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}USDT"

# ═══════════════════════════════════════════
# ТЕХНИЧЕСКИЙ АНАЛИЗ
# ═══════════════════════════════════════════
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

def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains  = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    ag = sum(gains) / period
    al = sum(losses) / period
    if al == 0: return 100.0
    return round(100 - (100 / (1 + ag / al)), 1)

def calc_atr(candles: list, period: int = 14) -> list:
    """Average True Range"""
    if len(candles) < 2:
        return [0.0] * len(candles)
    trs = [0.0]
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i-1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    atrs = [0.0] * period
    if len(trs) >= period:
        atrs_val = sum(trs[:period]) / period
        atrs[period-1] = atrs_val
        for i in range(period, len(trs)):
            atrs_val = (atrs_val * (period - 1) + trs[i]) / period
            atrs.append(atrs_val)
    else:
        atrs = [0.0] * len(trs)
    # Pad to match candles length
    while len(atrs) < len(candles):
        atrs.append(atrs[-1] if atrs else 0.0)
    return atrs[:len(candles)]

def calc_supertrend(candles: list, period: int = 10, multiplier: float = 3.0) -> list:
    """
    Supertrend индикатор.
    Возвращает список dict: {"value": float, "direction": 1=bull/-1=bear, "signal": "BUY"/"SELL"/None}
    signal — только в момент смены направления
    """
    n    = len(candles)
    atrs = calc_atr(candles, period)

    results    = [{"value": 0.0, "direction": 1, "signal": None}] * n
    upper_band = [0.0] * n
    lower_band = [0.0] * n
    st         = [0.0] * n
    direction  = [1]  * n  # 1 = bull, -1 = bear

    for i in range(period, n):
        hl2  = (candles[i]["high"] + candles[i]["low"]) / 2
        atr  = atrs[i]
        ub   = hl2 + multiplier * atr
        lb   = hl2 - multiplier * atr

        # Adjust bands
        if i > period:
            prev_ub = upper_band[i-1]
            prev_lb = lower_band[i-1]
            ub = min(ub, prev_ub) if candles[i-1]["close"] < prev_ub else ub
            lb = max(lb, prev_lb) if candles[i-1]["close"] > prev_lb else lb

        upper_band[i] = ub
        lower_band[i] = lb

        prev_dir  = direction[i-1] if i > period else 1
        prev_st   = st[i-1] if i > period else lb
        close     = candles[i]["close"]

        if prev_dir == 1:
            # Was bullish — stay bullish if close > lower band
            if close < lower_band[i]:
                direction[i] = -1
                st[i]        = upper_band[i]
            else:
                direction[i] = 1
                st[i]        = lower_band[i]
        else:
            # Was bearish — stay bearish if close < upper band
            if close > upper_band[i]:
                direction[i] = 1
                st[i]        = lower_band[i]
            else:
                direction[i] = -1
                st[i]        = upper_band[i]

        # Signal — только при смене
        sig = None
        if i > period and direction[i] != direction[i-1]:
            sig = "BUY" if direction[i] == 1 else "SELL"

        results[i] = {"value": st[i], "direction": direction[i], "signal": sig}

    return results

def get_supertrend_signal(symbol: str) -> dict:
    """
    Получает текущий сигнал Supertrend для монеты.
    Возвращает: direction, last_signal, last_signal_price, last_signal_time, pct_since_signal
    """
    candles = get_binance_ohlc(symbol, interval="4h", limit=100)
    if not candles or len(candles) < 20:
        return {"direction": 1, "last_signal": None, "label": "Нет данных"}

    st = calc_supertrend(candles, period=10, multiplier=3.0)

    current_dir = st[-1]["direction"]
    current_val = st[-1]["value"]

    # Ищем последний сигнал
    last_signal = None
    last_signal_price = None
    last_signal_time  = None
    for i in range(len(st) - 1, -1, -1):
        if st[i]["signal"]:
            last_signal       = st[i]["signal"]
            last_signal_price = candles[i]["close"]
            last_signal_time  = candles[i].get("time")
            break

    current_price = candles[-1]["close"]
    pct = 0.0
    if last_signal_price and last_signal_price > 0:
        pct = (current_price - last_signal_price) / last_signal_price * 100
        if last_signal == "SELL":
            pct = -pct  # для шорта инвертируем

    label = "🟢 BUY" if current_dir == 1 else "🔴 SELL"

    return {
        "direction":          current_dir,
        "supertrend_value":   current_val,
        "label":              label,
        "last_signal":        last_signal,
        "last_signal_price":  last_signal_price,
        "last_signal_time":   last_signal_time,
        "pct_since_signal":   round(pct, 2),
        "current_price":      current_price,
        "st_values":          st,
        "candles":            candles,
    }

def full_analysis(coin: dict) -> dict:
    """
    Полный многофакторный анализ монеты:
    TA (EMA, RSI, MACD-proxy, Bollinger-proxy, Volume, Momentum)
    + SMC (BOS, OB, FVG, Liquidity, структура)
    + Fundamental (mcap rank, vol/mcap, momentum score)
    + Итоговый "Rocket Score" 0-100
    """
    q     = coin["quote"]["USDT"]
    ch1h  = q.get("percent_change_1h",  0) or 0
    ch24h = q.get("percent_change_24h", 0) or 0
    ch7d  = q.get("percent_change_7d",  0) or 0
    ch30d = q.get("percent_change_30d", 0) or 0
    ch90d = q.get("percent_change_90d", 0) or 0
    vol   = q.get("volume_24h",  0) or 0
    mcap  = q.get("market_cap",  0) or 0
    price = q.get("price",       0) or 0
    rank  = coin.get("cmc_rank", 999)
    vol_ratio = (vol / mcap * 100) if mcap > 0 else 0

    # ── RSI приближение по изменениям ──
    def rsi_est(m):
        if m > 15:  return 82.0
        if m > 8:   return 70.0
        if m > 3:   return 60.0
        if m > 0:   return 52.0
        if m > -3:  return 45.0
        if m > -8:  return 35.0
        if m > -15: return 25.0
        return 18.0

    m4h    = ch1h * 0.5 + ch24h * 0.5
    rsi_1h = rsi_est(ch1h)
    rsi_4h = rsi_est(m4h)
    rsi_1d = rsi_est(ch24h)

    # ── EMA СТРУКТУРА (приближение через % изменения) ──
    # Логика: если монета росла 30д — она выше EMA200; росла 7д — выше EMA20
    above_ema200 = ch30d > 0
    above_ema50  = ch30d > -10
    above_ema20  = ch7d  > 0

    # ── BOLLINGER BANDS proxy ──
    # Если 1ч изменение очень маленькое → цена у средней, хорошая точка
    bb_squeeze = abs(ch1h) < 0.5   # сжатие боллинджера
    bb_breakout = abs(ch1h) >= 3.0  # пробой боллинджера

    # ── MACD proxy ──
    # Быстрая EMA (7д) пересекает медленную (30д) снизу вверх
    macd_bullish = ch7d > 0 and ch30d < ch7d  # быстрая выше медленной и растёт
    macd_bearish = ch7d < 0 and ch30d > ch7d

    # ── VOLUME ANALYSIS ──
    vol_spike    = vol_ratio >= 20   # аномальный объём — умные деньги
    vol_high     = vol_ratio >= 10
    vol_low      = vol_ratio < 3
    # Объём растёт при росте цены → подтверждение
    vol_confirm_bull = ch24h > 3 and vol_ratio >= 8
    vol_confirm_bear = ch24h < -3 and vol_ratio >= 8

    # ── MOMENTUM (сила тренда) ──
    # Многотаймфреймный моментум: все TF согласованы?
    tf_aligned_bull = ch1h > 0 and ch24h > 0 and ch7d > 0 and ch30d > 0
    tf_aligned_bear = ch1h < 0 and ch24h < 0 and ch7d < 0 and ch30d < 0
    # Ускорение: 1ч > 4ч > 1д (набирает силу)
    momentum_accel = ch1h > (ch24h / 24) * 2  # 1ч бьёт средний часовой темп 24ч в 2 раза

    # ── SMC АНАЛИЗ ──
    # BOS (Break of Structure): новый хай после коррекции
    # Прокси: 7д рост после 30д коррекции → BOS пробой
    smc_bos_bull = ch7d > 5 and ch30d < -5   # пробой структуры вверх после коррекции
    smc_bos_bear = ch7d < -5 and ch30d > 5

    # OB (Order Block): зоны накопления умных денег
    # Прокси: большой объём + маленькое движение цены → накопление
    smc_ob_accumulation = vol_ratio >= 15 and abs(ch24h) < 3  # накопление OB
    smc_ob_distribution = vol_ratio >= 15 and abs(ch24h) < 3 and ch7d < 0

    # FVG (Fair Value Gap): имбаланс, который нужно закрыть
    # Прокси: резкий скачок 1ч создаёт FVG
    smc_fvg_bull = ch1h >= 3 and ch24h > 5   # FVG после импульса вверх
    smc_fvg_bear = ch1h <= -3 and ch24h < -5  # FVG после импульса вниз

    # LIQUIDITY SWEEP: свип ликвидности перед разворотом
    # Прокси: резкий всплеск вниз с объёмом → свип лоёв, потом рост
    smc_liq_sweep_bull = ch1h < -4 and vol_ratio >= 12  # свип низов с объёмом
    smc_liq_sweep_bear = ch1h > 4  and vol_ratio >= 12  # свип хаёв с объёмом

    # SMART MONEY DIVERGENCE: цена падает, объём растёт → накопление
    smc_smart_accum = ch24h < -5 and vol_ratio >= 15  # умные деньги покупают на падении
    smc_smart_dist  = ch24h > 10  and vol_ratio >= 15  # умные деньги продают на росте

    # ── ФУНДАМЕНТАЛЬНЫЙ АНАЛИЗ ──
    # Ранг монеты (топ монеты более надёжны)
    fund_rank_top50   = rank <= 50
    fund_rank_top200  = rank <= 200
    # Liquidity score: большой объём относительно капы = ликвидная монета
    fund_liquid = vol >= 50_000_000      # объём > $50M = ликвидная
    fund_mega   = mcap >= 1_000_000_000  # капа > $1B = голубые фишки
    fund_mid    = mcap >= 100_000_000    # капа > $100M = средний уровень
    # Recovery potential: сильно упала за 90д но начинает восстановление
    fund_recovery = ch90d < -50 and ch7d > 5  # упала -50% за 90д, но +5% за неделю

    # ── ROCKET SCORE (0-100) — главный показатель потенциала ──
    rocket = 50  # база

    # Технический анализ (+/-)
    if above_ema200 and above_ema50 and above_ema20: rocket += 8
    elif above_ema50 and above_ema20:                rocket += 5
    elif above_ema20:                                rocket += 2
    elif not above_ema50 and not above_ema200:       rocket -= 8

    if rsi_4h < 30:   rocket += 8   # перепродан — лучшая точка входа
    elif rsi_4h < 40: rocket += 5
    elif rsi_4h < 50: rocket += 2
    elif rsi_4h > 70: rocket -= 5
    elif rsi_4h > 80: rocket -= 8

    if macd_bullish:  rocket += 5
    if macd_bearish:  rocket -= 5
    if bb_squeeze:    rocket += 3   # сжатие перед взрывом
    if bb_breakout and ch1h > 0: rocket += 4

    if vol_spike:              rocket += 6
    elif vol_high:             rocket += 3
    elif vol_low:              rocket -= 4
    if vol_confirm_bull:       rocket += 5
    if vol_confirm_bear:       rocket -= 5

    if tf_aligned_bull:        rocket += 6
    if tf_aligned_bear:        rocket -= 6
    if momentum_accel:         rocket += 4

    # SMC факторы
    if smc_bos_bull:           rocket += 7   # BOS пробой вверх — сильный сигнал
    if smc_bos_bear:           rocket -= 7
    if smc_ob_accumulation:    rocket += 6   # накопление OB
    if smc_liq_sweep_bull:     rocket += 5   # свип лоёв → готов к росту
    if smc_liq_sweep_bear:     rocket -= 5
    if smc_smart_accum:        rocket += 8   # умные деньги покупают
    if smc_smart_dist:         rocket -= 6   # умные деньги продают
    if smc_fvg_bull:           rocket += 3   # FVG вверх
    if smc_fvg_bear:           rocket -= 3

    # Фундаментальные факторы
    if fund_rank_top50:        rocket += 5
    elif fund_rank_top200:     rocket += 3
    if fund_liquid:            rocket += 4
    if fund_mega:              rocket += 4
    elif fund_mid:             rocket += 2
    if fund_recovery:          rocket += 9   # восстановление после обвала

    # 90д momentum
    if ch90d > 50:             rocket -= 4  # перегрета (90д +50%)
    elif ch90d > 0:            rocket += 2  # устойчивый рост
    elif ch90d < -70:          rocket += 5  # глубокая коррекция = возможность

    rocket = max(0, min(100, rocket))

    # ── ОСНОВНОЙ SCORE для сортировки сигналов ──
    score = 0
    if above_ema200 and above_ema50 and above_ema20: score += 3
    elif above_ema50 and above_ema20:                score += 2
    elif above_ema20:                                score += 1
    elif not above_ema50 and not above_ema200:       score -= 2
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
    if smc_bos_bull:    score += 2
    if smc_smart_accum: score += 2
    if tf_aligned_bull: score += 1
    if fund_recovery:   score += 2

    is_long = score >= 0

    # ── TP/SL динамические (учитываем волатильность) ──
    # Чем выше RSI и объём, тем агрессивнее TP
    tp_mult = 1.0
    if rsi_4h < 35:  tp_mult = 1.3  # перепродан → больше потенциал
    if vol_spike:    tp_mult *= 1.2

    if is_long:
        tp1   = round(price * (1 + 0.04 * tp_mult), 8)
        tp2   = round(price * (1 + 0.08 * tp_mult), 8)
        tp3   = round(price * (1 + 0.15 * tp_mult), 8)
        sl    = round(price * 0.85, 8)
        swing = round(price * 0.92, 8)
    else:
        tp1   = round(price * (1 - 0.04 * tp_mult), 8)
        tp2   = round(price * (1 - 0.08 * tp_mult), 8)
        tp3   = round(price * (1 - 0.15 * tp_mult), 8)
        sl    = round(price * 1.15, 8)
        swing = round(price * 1.08, 8)

    rr = abs(tp3 - price) / abs(sl - price) if abs(sl - price) > 0 else 0

    # EMA значения (приближение)
    ema20_1h  = round(price / (1 + ch1h  / 100 * 0.15), 8)
    ema50_1h  = round(price / (1 + ch1h  / 100 * 0.40), 8)
    ema200_1h = round(price / (1 + ch1h  / 100 * 1.20), 8)
    ema20_4h  = round(price / (1 + ch24h / 100 * 0.10), 8)
    ema50_4h  = round(price / (1 + ch24h / 100 * 0.25), 8)
    ema200_4h = round(price / (1 + ch24h / 100 * 0.80), 8)
    ema20_1d  = round(price / (1 + ch7d  / 100 * 0.08), 8)
    ema50_1d  = round(price / (1 + ch7d  / 100 * 0.20), 8)
    ema200_1d = round(price / (1 + ch7d  / 100 * 0.60), 8)

    # ── МЕТКИ ──
    if rocket >= 80:   rocket_label = "🚀🔥 ROCKET"
    elif rocket >= 70: rocket_label = "🚀 СИЛЬНЫЙ"
    elif rocket >= 60: rocket_label = "✅ ХОРОШИЙ"
    elif rocket >= 50: rocket_label = "🟡 СРЕДНИЙ"
    elif rocket >= 40: rocket_label = "🟠 СЛАБЫЙ"
    else:              rocket_label = "🔴 ИЗБЕГАТЬ"

    if score >= 7:    label = "🚀🔥 СИЛЬНЫЙ ЛОНГ"
    elif score >= 5:  label = "🔥 ЛОНГ"
    elif score >= 3:  label = "✅ ЛОНГ"
    elif score >= 1:  label = "📈 СЛАБЫЙ ЛОНГ"
    elif score >= -1: label = "⚪️ НЕЙТРАЛЬНО"
    elif score >= -3: label = "📉 СЛАБЫЙ ШОРТ"
    elif score >= -5: label = "🔻 ШОРТ"
    else:             label = "💥 СИЛЬНЫЙ ШОРТ"

    # SMC факторы для отображения
    smc_factors = []
    if smc_bos_bull:        smc_factors.append("BOS ↑")
    if smc_bos_bear:        smc_factors.append("BOS ↓")
    if smc_ob_accumulation: smc_factors.append("OB Накопление")
    if smc_liq_sweep_bull:  smc_factors.append("Liq Sweep ↑")
    if smc_liq_sweep_bear:  smc_factors.append("Liq Sweep ↓")
    if smc_smart_accum:     smc_factors.append("Smart Accum 💎")
    if smc_smart_dist:      smc_factors.append("Smart Dist ⚠️")
    if smc_fvg_bull:        smc_factors.append("FVG ↑")
    if smc_fvg_bear:        smc_factors.append("FVG ↓")
    if tf_aligned_bull:     smc_factors.append("TF Align Bull")
    if fund_recovery:       smc_factors.append("Recovery 🔄")
    if bb_squeeze:          smc_factors.append("BB Squeeze")
    if macd_bullish:        smc_factors.append("MACD Bull")

    return {
        "label": label, "score": score, "is_long": is_long,
        "rocket": rocket, "rocket_label": rocket_label,
        "price": price, "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "sl": sl, "swing": swing, "rr": rr,
        "rsi_4h": rsi_4h, "rsi_1h": rsi_1h, "rsi_1d": rsi_1d,
        "ch1h": ch1h, "ch24h": ch24h, "ch7d": ch7d, "ch30d": ch30d, "ch90d": ch90d,
        "vol": vol, "mcap": mcap, "vol_ratio": vol_ratio, "rank": rank,
        "ema20_1h": ema20_1h, "ema50_1h": ema50_1h, "ema200_1h": ema200_1h,
        "ema20_4h": ema20_4h, "ema50_4h": ema50_4h, "ema200_4h": ema200_4h,
        "ema20_1d": ema20_1d, "ema50_1d": ema50_1d, "ema200_1d": ema200_1d,
        # SMC / TA флаги
        "above_ema200": above_ema200, "above_ema50": above_ema50, "above_ema20": above_ema20,
        "macd_bullish": macd_bullish, "macd_bearish": macd_bearish,
        "bb_squeeze": bb_squeeze, "vol_spike": vol_spike,
        "tf_aligned_bull": tf_aligned_bull, "smc_bos_bull": smc_bos_bull,
        "smc_smart_accum": smc_smart_accum, "fund_recovery": fund_recovery,
        "smc_factors": smc_factors,
        "fund_rank_top50": fund_rank_top50, "fund_liquid": fund_liquid,
        "st_label": "—",  # будет заполнено в send_coin если нужно
    }

# ═══════════════════════════════════════════
# ГЕНЕРАЦИЯ ГРАФИКА
# ═══════════════════════════════════════════
def detect_order_blocks(candles, is_long):
    obs = []
    if len(candles) < 5:
        return obs
    for i in range(2, len(candles) - 1):
        c    = candles[i]
        body = abs(c["close"] - c["open"])
        prev = candles[i - 1]
        if is_long:
            if (c["close"] < c["open"] and
                candles[i+1]["close"] > c["high"] and
                body > abs(prev["close"] - prev["open"]) * 1.2):
                obs.append({"lo": c["low"], "hi": c["high"], "idx": i, "type": "bull"})
        else:
            if (c["close"] > c["open"] and
                candles[i+1]["close"] < c["low"] and
                body > abs(prev["close"] - prev["open"]) * 1.2):
                obs.append({"lo": c["low"], "hi": c["high"], "idx": i, "type": "bear"})
    return obs[-3:]

def generate_signal_chart(symbol: str, a: dict, stats_24h: dict = None) -> io.BytesIO:
    is_long       = a["is_long"]
    price         = a["price"]
    tp1, tp2, tp3 = a["tp1"], a["tp2"], a["tp3"]
    sl, swing     = a["sl"],  a["swing"]
    rsi           = a["rsi_4h"]

    # Данные с Binance
    candles = get_binance_ohlc(symbol, interval="4h", limit=200)
    if not candles or len(candles) < 20:
        p = price * 0.96
        for _ in range(200):
            ch = random.gauss(0, 0.008)
            o = p; c = p * (1 + ch)
            h = max(o, c) * (1 + abs(random.gauss(0, 0.003)))
            l = min(o, c) * (1 - abs(random.gauss(0, 0.003)))
            candles.append({"open": o, "high": h, "low": l, "close": c, "vol": random.uniform(1e5,1e6), "time": None})
            p = c
        candles[-1]["close"] = price

    n_all      = len(candles)
    closes_all = [c["close"] for c in candles]

    # EMA по всем данным
    ema20_all  = calc_ema(closes_all, 20)
    ema50_all  = calc_ema(closes_all, 50)
    ema200_all = calc_ema(closes_all, min(200, n_all))

    # Supertrend по всем данным
    st_all = calc_supertrend(candles, period=10, multiplier=3.0)

    # Показываем последние 100
    display_n = min(100, n_all)
    start_idx = n_all - display_n
    candles   = candles[start_idx:]
    ema20_v   = ema20_all[start_idx:]
    ema50_v   = ema50_all[start_idx:]
    ema200_v  = ema200_all[start_idx:]
    st_v      = st_all[start_idx:]

    n    = len(candles)
    vols = [c.get("vol", 0) for c in candles]
    obs  = detect_order_blocks(candles, is_long)

    fig = plt.figure(figsize=(13, 8.5), facecolor=BG)
    gs  = fig.add_gridspec(9, 1, hspace=0,
                            left=0.01, right=0.80,
                            top=0.995, bottom=0.05)
    ax_brand = fig.add_subplot(gs[0:1, 0])
    ax       = fig.add_subplot(gs[1:7, 0])
    axv      = fig.add_subplot(gs[7:8, 0], sharex=ax)
    ax_info  = fig.add_subplot(gs[8:,  0])

    for a_ in [ax_brand, ax, axv, ax_info]:
        a_.set_facecolor(BG)

    # ── БРЕНДИНГ ──
    ax_brand.set_facecolor(ORANGE)
    ax_brand.set_xlim(0, 1); ax_brand.set_ylim(0, 1); ax_brand.axis("off")
    ax_brand.text(0.5, 0.55, "B E S T   T R A D E",
                  fontsize=20, color=WHITE, fontweight="bold",
                  ha="center", va="center", transform=ax_brand.transAxes)
    ax_brand.text(0.5, 0.12, "S  I  G  N  A  L  S",
                  fontsize=7, color=WHITE, alpha=0.70,
                  ha="center", va="center", transform=ax_brand.transAxes)

    # ── ORDER BLOCKS ──
    for ob in obs:
        ob_color = GREEN if ob["type"] == "bull" else RED
        ax.axhspan(ob["lo"], ob["hi"],
                   xmin=ob["idx"] / n, xmax=1.0,
                   alpha=0.10, color=ob_color, zorder=1)

    # ── SUPERTREND — зелёная/красная зона под/над ценой ──
    bull_xs, bull_ys = [], []
    bear_xs, bear_ys = [], []
    for i, s in enumerate(st_v):
        if s["value"] > 0:
            if s["direction"] == 1:
                bull_xs.append(i); bull_ys.append(s["value"])
            else:
                bear_xs.append(i); bear_ys.append(s["value"])

    if bull_xs:
        ax.fill_between(bull_xs,
                        [candles[i]["close"] for i in bull_xs],
                        bull_ys,
                        alpha=0.12, color=GREEN, zorder=1)
        ax.scatter(bull_xs, bull_ys, color=GREEN, s=2.5, zorder=4, linewidths=0)
    if bear_xs:
        ax.fill_between(bear_xs,
                        [candles[i]["close"] for i in bear_xs],
                        bear_ys,
                        alpha=0.12, color=RED, zorder=1)
        ax.scatter(bear_xs, bear_ys, color=RED, s=2.5, zorder=4, linewidths=0)

    # ── BUY/SELL стрелки на моментах переключения ──
    for i, s in enumerate(st_v):
        if s["signal"] == "BUY":
            ax.annotate("▲ BUY",
                        xy=(i, candles[i]["low"] * 0.9985),
                        fontsize=7.5, color=GREEN, fontweight="bold",
                        ha="center", va="top", zorder=10,
                        bbox=dict(boxstyle="round,pad=0.2", facecolor=BG,
                                  edgecolor=GREEN, alpha=0.85, lw=1.0))
        elif s["signal"] == "SELL":
            ax.annotate("▼ SELL",
                        xy=(i, candles[i]["high"] * 1.0015),
                        fontsize=7.5, color=RED, fontweight="bold",
                        ha="center", va="bottom", zorder=10,
                        bbox=dict(boxstyle="round,pad=0.2", facecolor=BG,
                                  edgecolor=RED, alpha=0.85, lw=1.0))

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

    # ── EMA — жирные линии с подписями ──
    ema_cfg = [
        (ema20_v,  "#F0B90B", "EMA20",  2.0),
        (ema50_v,  "#F7931A", "EMA50",  2.2),
        (ema200_v, "#FF4560", "EMA200", 2.5),
    ]
    for vals, col, lbl, lw in ema_cfg:
        pts = [(i, v) for i, v in enumerate(vals) if v is not None]
        if len(pts) > 1:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            ax.plot(xs, ys, color=col, lw=lw, alpha=0.95, label=lbl, zorder=5)
            lx, ly = pts[-1]
            ax.text(lx + 0.8, ly, f" {lbl}\n {fp(ly)}",
                    color=col, fontsize=6.2, va="center", fontweight="bold", zorder=8,
                    bbox=dict(boxstyle="round,pad=0.15", facecolor=BG,
                              alpha=0.7, edgecolor=col, lw=0.6))

    # ── SUPERTREND подпись текущего состояния ──
    current_st = st_v[-1] if st_v else {"direction": 1, "value": price}
    st_label   = "🟢 SUPERTREND: BUY" if current_st["direction"] == 1 else "🔴 SUPERTREND: SELL"
    st_color   = GREEN if current_st["direction"] == 1 else RED
    ax.text(0.012, 0.79, st_label,
            fontsize=8, color=st_color, fontweight="bold",
            va="top", ha="left", transform=ax.transAxes, zorder=10,
            bbox=dict(boxstyle="round,pad=0.3", facecolor=BG, edgecolor=st_color, alpha=0.85, lw=1.2))

    # ── ЗОНЫ SL/TP ──
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
        ax.axhline(val, color=color, linestyle=ls, linewidth=lw, alpha=0.92, zorder=6)
        ax.text(n + ext * 0.04, val,
                f"  {label}  {fp(val)}  {extra}",
                color=color, fontsize=7.5, va="center",
                fontweight="bold" if bold else "normal",
                fontfamily="monospace", zorder=7)

    draw_lvl(tp3,   TP3C,   "TP3",   pct_str(tp3),  "--", 1.0)
    draw_lvl(tp2,   TP2C,   "TP2",   pct_str(tp2),  "--", 1.0)
    draw_lvl(tp1,   TP1C,   "TP1",   pct_str(tp1),  "--", 1.0)
    draw_lvl(price, ENTRYC, "Entry", "",             "-",  2.2, True)
    draw_lvl(swing, SWINGC, "Swing", "",             ":",  1.0)
    draw_lvl(sl,    SLC,    "SL",    pct_str(sl),    "--", 1.3)

    # 24h min/max линии
    if stats_24h:
        h24 = stats_24h.get("high", 0)
        l24 = stats_24h.get("low",  0)
        if h24: ax.axhline(h24, color="#AAAAFF", linestyle=":", lw=0.8, alpha=0.6, zorder=4)
        if l24: ax.axhline(l24, color="#FFAAAA", linestyle=":", lw=0.8, alpha=0.6, zorder=4)

    ax.annotate("▲" if is_long else "▼",
                xy=(n - 1, price), fontsize=14,
                color=ENTRYC, ha="center", va="bottom", zorder=9)

    ax.legend(loc="upper left", fontsize=7.5,
              facecolor="#0D1B2A", edgecolor="#1E2A3A",
              labelcolor=WHITE, framealpha=0.92,
              borderpad=0.5, handlelength=1.4)

    # ── ОБЪЁМ ──
    max_vol = max(vols) if max(vols) > 0 else 1
    for i, c in enumerate(candles):
        col = GREEN if c["close"] >= c["open"] else RED
        axv.bar(i, vols[i] / max_vol, width=0.7, color=col, alpha=0.40, zorder=2)
    axv.set_yticks([])
    axv.spines[:].set_visible(False)

    # ── X МЕТКИ ──
    step  = max(n // 8, 1)
    ticks = list(range(0, n, step))
    if candles[0].get("time"):
        xlbls = [candles[i]["time"].strftime("%d.%m\n%H:%M") for i in ticks]
    else:
        now_t = datetime.now(timezone.utc)
        xlbls = [(now_t - timedelta(hours=(n - i) * 4)).strftime("%d.%m\n%H:%M") for i in ticks]
    ax.set_xticks(ticks); ax.set_xticklabels(xlbls, fontsize=6.5, color=GRAY)
    axv.tick_params(axis="x", colors=GRAY, labelsize=6)

    ax.tick_params(axis="y", colors=GRAY, labelsize=7.5, right=False, left=False)
    ax.yaxis.set_label_position("right"); ax.yaxis.tick_right()
    ax.grid(color="#1A2535", lw=0.3, zorder=0)
    ax.spines[:].set_color("#1E2A3A")

    # ── ЗАГОЛОВОК ──
    side_str  = "LONG" if is_long else "SHORT"
    rsi_color = GREEN if rsi < 35 else (RED if rsi > 65 else GRAY)
    rsi_tag   = "Перепродан 🟢" if rsi < 35 else ("Перекуплен 🔴" if rsi > 65 else "Нейтральный ⚪")
    ax.text(0.012, 0.97, f"{symbol}USDT  •  4H  •  {side_str}",
            fontsize=11, color=WHITE, fontweight="bold",
            va="top", ha="left", transform=ax.transAxes, zorder=10)
    ax.text(0.012, 0.89, f"RSI {rsi:.0f}  —  {rsi_tag}",
            fontsize=8, color=rsi_color,
            va="top", ha="left", transform=ax.transAxes, zorder=10)

    # ── НИЖНЯЯ ИНФО ПОЛОСА (24h stats) ──
    ax_info.axis("off")
    if stats_24h:
        h24 = stats_24h.get("high", 0)
        l24 = stats_24h.get("low",  0)
        best_entry = l24 * 1.005 if l24 else price
        info_txt = (f"  📅 24H:  🔼 {fp(h24)}   🔽 {fp(l24)}   "
                    f"🎯 Лучший вход: {fp(best_entry)}")
        ax_info.text(0.01, 0.5, info_txt,
                     color=GRAY, fontsize=7.5, va="center",
                     transform=ax_info.transAxes)

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    buf.seek(0)
    return buf

# ═══════════════════════════════════════════
# ТЕКСТ СИГНАЛА
# ═══════════════════════════════════════════
def build_signal_text(symbol: str, a: dict, stats_24h: dict = None, atl: float = 0) -> str:
    is_long = a["is_long"]
    price   = a["price"]
    tp1, tp2, tp3 = a["tp1"], a["tp2"], a["tp3"]
    sl, swing     = a["sl"],  a["swing"]
    rsi_1h = a["rsi_1h"]; rsi_4h = a["rsi_4h"]; rsi_1d = a["rsi_1d"]
    rr     = a["rr"]; vol = a["vol"]
    rocket = a.get("rocket", 50)
    rocket_label = a.get("rocket_label", "")

    side_emoji = "🟢" if is_long else "🔴"
    side_text  = "LONG" if is_long else "SHORT"
    swing_lbl  = "Swing Low" if is_long else "Swing High"

    def pct(t):
        d = (t - price) / price * 100
        v = d if is_long else -d
        return f"+{v:.2f}%" if v >= 0 else f"{v:.2f}%"

    def sl_pct(): return f"-{abs(sl-price)/price*100:.2f}%"

    def rsi_icon(r):
        if r < 30: return "🟢"
        if r > 70: return "🔴"
        return "🔵"

    vol_str = (f"${vol/1e9:.2f}B" if vol >= 1e9 else
               f"${vol/1e6:.1f}M" if vol >= 1e6 else f"${vol/1e3:.0f}K")

    def ep(v):
        if not v: return "—"
        d = (price - v) / v * 100
        return f"{'▲' if d>=0 else '▼'}{abs(d):.1f}%"

    filled = int(rocket / 10)
    bar = "█" * filled + "░" * (10 - filled)

    smc_factors = a.get("smc_factors", [])
    smc_line = "  ".join(smc_factors[:5]) if smc_factors else "—"

    ema_pos = []
    if a.get("above_ema200"): ema_pos.append("EMA200✅")
    if a.get("above_ema50"):  ema_pos.append("EMA50✅")
    if a.get("above_ema20"):  ema_pos.append("EMA20✅")
    if not ema_pos: ema_pos = ["Ниже всех EMA⚠️"]

    lines = [
        f"📊 *{symbol}USDT*  {side_emoji} *{side_text}*",
        f"🕐 {now_utc3()}",
        "",
        f"🚀 *Rocket Score:* `{rocket}/100`  {rocket_label}",
        f"`{bar}`",
        f"📍 {' | '.join(ema_pos)}",
        "",
        f"💰 Вход:  `{fp(price)}`",
        f"🎯 TP1:  `{fp(tp1)}`  ({pct(tp1)})",
        f"🎯 TP2:  `{fp(tp2)}`  ({pct(tp2)})",
        f"🎯 TP3:  `{fp(tp3)}`  ({pct(tp3)})",
        f"🛑 SL:   `{fp(sl)}`   ({sl_pct()})",
        f"📌 {swing_lbl}:  `{fp(swing)}`",
        "",
        "━━━━━━━━━━━━━━━━━━",
        f"📐 R:R: 1:{rr:.1f}  |  💹 Объём: {vol_str}",
        f"🏆 Rank #{a.get('rank','—')}  |  Vol/MCap: {a.get('vol_ratio',0):.1f}%",
        f"📊 1H `{fc(a['ch1h'])}`  24H `{fc(a['ch24h'])}`  7D `{fc(a['ch7d'])}`  30D `{fc(a['ch30d'])}`",
        "",
        f"🧠 *SMC:* `{smc_line}`",
        "",
        "📉 *EMA (% от цены):*",
        f"┌ 1H  EMA20`{ep(a['ema20_1h'])}` EMA50`{ep(a['ema50_1h'])}` EMA200`{ep(a['ema200_1h'])}`",
        f"├ 4H  EMA20`{ep(a['ema20_4h'])}` EMA50`{ep(a['ema50_4h'])}` EMA200`{ep(a['ema200_4h'])}`",
        f"└ 1D  EMA20`{ep(a['ema20_1d'])}` EMA50`{ep(a['ema50_1d'])}` EMA200`{ep(a['ema200_1d'])}`",
        "",
        f"📈 RSI: 1H{rsi_icon(rsi_1h)}`{rsi_1h:.0f}` 4H{rsi_icon(rsi_4h)}`{rsi_4h:.0f}` 1D{rsi_icon(rsi_1d)}`{rsi_1d:.0f}`",
        f"⚡️ *Supertrend (4H):* `{a.get('st_label', '—')}`",
    ]

    if stats_24h:
        h24 = stats_24h.get("high", 0); l24 = stats_24h.get("low", 0)
        if h24 and l24:
            lines += ["", "━━━━━━━━━━━━━━━━━━",
                      f"📅 *24H:*  🔼`{fp(h24)}`  🔽`{fp(l24)}`",
                      f"🎯 *Лучший вход дня:* `{fp(l24*1.005)}`"]

    if atl and atl > 0:
        lines.append(f"🏆 *Ист. минимум:* `{fp(atl)}`")

    lines += ["", f"#{symbol}USDT"]
    return "\n".join(lines)

# ═══════════════════════════════════════════
# РЫНОЧНЫЙ ОБЗОР
# ═══════════════════════════════════════════
BTC_ZONES = {
    "support":    [
        {"level": 62137, "label": "S1"},
        {"level": 61316, "label": "S2"},
        {"level": 59000, "label": "S3"},
    ],
    "resistance": [
        {"level": 63800, "label": "R1"},
        {"level": 65000, "label": "R2"},
        {"level": 67000, "label": "R3"},
    ],
}

def analyze_market(btc, eth, gm, coins):
    bp = btc.get("price", 0); ep = eth.get("price", 0)
    bd = gm.get("btc_dominance", 0); ed = gm.get("eth_dominance", 0)
    od = 100 - bd - ed
    tm = gm.get("total_mcap", 0); mc = gm.get("mcap_change_24h", 0)

    sup = next((z for z in BTC_ZONES["support"]    if bp > z["level"]), None)
    res = next((z for z in BTC_ZONES["resistance"] if bp < z["level"]), None)
    if sup: sup["dist"] = (bp - sup["level"]) / bp * 100
    if res: res["dist"] = (res["level"] - bp) / bp * 100

    pos = sum(1 for c in coins if c["quote"]["USDT"].get("percent_change_24h", 0) > 0)
    sp  = pos / len(coins) * 100 if coins else 50

    if sp >= 65:   sent = "🚀 Бычий"
    elif sp >= 50: sent = "🟢 Умеренно бычий"
    elif sp >= 35: sent = "🔴 Умеренно медвежий"
    else:          sent = "💥 Медвежий"

    dom_sig    = ("💥 BTC доминирует — альты под давлением" if bd > 59 else
                  ("🟡 BTC.D нейтральна" if bd > 56 else
                   "🚀 BTC.D снижается — деньги в альты"))
    others_sig = ("🔴 Альты слабеют" if od < 8.2 else
                  ("🚀 Альты усиливаются" if od > 8.8 else "🟡 Альты нейтральны"))
    total_sig  = ("🚀 Рынок растёт" if mc >= 2 else
                  ("🟢 Стабильно"   if mc >= 0 else
                   ("🔴 Коррекция"   if mc >= -2 else "💥 Рынок падает")))

    bulls = sum([btc.get("ch24h", 0) > 1, eth.get("ch24h", 0) > 1, bd < 57, mc > 0, od > 8.3])
    if bulls >= 4:   verdict = "🚀 БЫЧИЙ — ищем лонги"
    elif bulls >= 3: verdict = "🟢 УМЕРЕННО БЫЧИЙ — осторожные лонги"
    elif bulls >= 2: verdict = "🟡 НЕЙТРАЛЬНЫЙ — ждём сигналов"
    elif bulls >= 1: verdict = "🔴 ОСТОРОЖНО — рынок под давлением"
    else:            verdict = "💥 МЕДВЕЖИЙ — воздерживаемся от лонгов"

    analyzed   = [(c, full_analysis(c)) for c in coins]
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
    sup = ms["btc_sup"]; res = ms["btc_res"]
    s_line = f"  └ 🟢 Поддержка: ${sup['level']:,} ({sup['label']}) — {sup['dist']:.1f}% ниже" if sup else ""
    r_line = f"  └ 🔴 Сопротивление: ${res['level']:,} ({res['label']}) — {res['dist']:.1f}% выше" if res else ""

    long_lines  = []
    short_lines = []
    for i, (c, a) in enumerate(ms.get("top_longs", []), 1):
        sym = c["symbol"]; ch = a["ch24h"]
        long_lines.append(f"  {i}. 🚀 *{sym}*  ${fp(a['price'])}  {fc(ch)}  RSI {a['rsi_4h']:.0f}")
    for i, (c, a) in enumerate(ms.get("top_shorts", []), 1):
        sym = c["symbol"]; ch = a["ch24h"]
        short_lines.append(f"  {i}. 📉 *{sym}*  ${fp(a['price'])}  {fc(ch)}  RSI {a['rsi_4h']:.0f}")

    return "\n".join([
        "🌍 *ОБЗОР РЫНКА — BEST TRADE*",
        f"🕐 {now_utc3()}",
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
        f"  Растут {ms['sentiment_pct']:.0f}% монет из топ-500",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "🚀 *ТОП ЛОНГИ* (топ-500)",
    ] + (long_lines if long_lines else ["  Нет сигналов"]) + [
        "",
        "📉 *ТОП ШОРТЫ* (топ-500)",
    ] + (short_lines if short_lines else ["  Нет сигналов"]) + [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🎯 *ВЕРДИКТ:* {ms['verdict']}",
        "",
        "⚠️ Риск: *2% депозита*  |  SL обязателен",
    ])

def overview_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("₿ BTC",    url=tv_link("BTC")),
         InlineKeyboardButton("Ξ ETH",    url=tv_link("ETH"))],
        [InlineKeyboardButton("BTC.D",    url="https://www.tradingview.com/chart/?symbol=CRYPTOCAP:BTC.D"),
         InlineKeyboardButton("TOTAL",    url="https://www.tradingview.com/chart/?symbol=CRYPTOCAP:TOTAL"),
         InlineKeyboardButton("OTHERS.D", url="https://www.tradingview.com/chart/?symbol=CRYPTOCAP:OTHERS.D")],
        [InlineKeyboardButton("🔄 Обновить", callback_data="market_overview"),
         InlineKeyboardButton("🤖 Сигналы", callback_data="signals")],
    ])

# ═══════════════════════════════════════════
# PUMP / DUMP ДЕТЕКТОР
# ═══════════════════════════════════════════
async def check_pump_dump(bot, chat_ids, coins):
    now_ts = datetime.now(TZ).timestamp()
    for coin in coins:
        sym   = coin["symbol"]
        q     = coin["quote"]["USDT"]
        price = q.get("price", 0)
        ch1h  = q.get("percent_change_1h", 0) or 0

        # Обновляем кэш цен
        if sym not in price_cache:
            price_cache[sym] = []
        price_cache[sym].append(price)
        if len(price_cache[sym]) > 12:  # храним последний час
            price_cache[sym].pop(0)

        # Pump: +5% за 1ч
        if ch1h >= 5:
            last_alert = pump_alerted.get(sym, 0)
            if now_ts - last_alert > 3600:  # не чаще раза в час
                pump_alerted[sym] = now_ts
                slug = coin.get("slug", sym.lower())
                text = (f"🚀 *PUMP DETECTED!*\n"
                        f"🕐 {now_utc3()}\n\n"
                        f"*{sym}USDT*  +{ch1h:.2f}% за 1ч\n"
                        f"💰 Цена: `{fp(price)}`\n"
                        f"⚠️ Не гонись за памп-ом — жди отката!\n\n"
                        f"#{sym}USDT")
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("📈 TradingView", url=tv_link(sym)),
                    InlineKeyboardButton("CMC", url=cmc_link(slug)),
                ]])
                for cid in chat_ids:
                    try:
                        await bot.send_message(cid, text, parse_mode="Markdown", reply_markup=kb)
                    except Exception as e:
                        log.error(f"Pump alert {cid}: {e}")
                log.info(f"PUMP alert: {sym} +{ch1h:.2f}%")

        # Dump: -5% за 1ч
        elif ch1h <= -5:
            last_alert = pump_alerted.get(f"dump_{sym}", 0)
            if now_ts - last_alert > 3600:
                pump_alerted[f"dump_{sym}"] = now_ts
                slug = coin.get("slug", sym.lower())
                text = (f"💥 *DUMP DETECTED!*\n"
                        f"🕐 {now_utc3()}\n\n"
                        f"*{sym}USDT*  {ch1h:.2f}% за 1ч\n"
                        f"💰 Цена: `{fp(price)}`\n"
                        f"⚠️ Возможна зона набора лонга!\n\n"
                        f"#{sym}USDT")
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("📈 TradingView", url=tv_link(sym)),
                    InlineKeyboardButton("CMC", url=cmc_link(slug)),
                ]])
                for cid in chat_ids:
                    try:
                        await bot.send_message(cid, text, parse_mode="Markdown", reply_markup=kb)
                    except Exception as e:
                        log.error(f"Dump alert {cid}: {e}")
                log.info(f"DUMP alert: {sym} {ch1h:.2f}%")

# ═══════════════════════════════════════════
# АЛЕРТ ВХОДА В ЗОНУ
# ═══════════════════════════════════════════
async def check_entry_zones(bot, chat_ids, coins):
    """Когда цена входит в зону TP/SL — оповещаем"""
    now_ts = datetime.now(TZ).timestamp()
    analyzed = [(c, full_analysis(c)) for c in coins if c["quote"]["USDT"].get("price", 0) > 0]
    signals  = [(c, a) for c, a in analyzed if abs(a["score"]) >= 3]

    for coin, a in signals:
        sym   = coin["symbol"]
        price = a["price"]
        tp1   = a["tp1"]
        sl    = a["sl"]
        is_long = a["is_long"]

        # Цена подходит к entry zone (в пределах 1%)
        if is_long:
            near_entry = price <= a["swing"] * 1.01
        else:
            near_entry = price >= a["swing"] * 0.99

        if near_entry:
            last_alert = alerted_zones.get(sym, 0)
            if now_ts - last_alert > 1800:  # не чаще раза в 30 мин
                alerted_zones[sym] = now_ts
                side   = "LONG" if is_long else "SHORT"
                emoji  = "🟢" if is_long else "🔴"
                slug   = coin.get("slug", sym.lower())
                text   = (f"⚡️ *ЗОНА НАБОРА — ВХОДИМ!*\n"
                          f"🕐 {now_utc3()}\n\n"
                          f"{emoji} *{sym}USDT  {side}*\n"
                          f"💰 Цена: `{fp(price)}`\n"
                          f"🎯 TP1: `{fp(tp1)}`\n"
                          f"🛑 SL: `{fp(sl)}`\n"
                          f"📐 R:R: 1:{a['rr']:.1f}\n\n"
                          f"⚠️ Риск: 2% депозита | SL обязателен\n\n"
                          f"#{sym}USDT")
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("📈 TradingView", url=tv_link(sym)),
                    InlineKeyboardButton("CMC", url=cmc_link(slug)),
                ]])
                for cid in chat_ids:
                    try:
                        await bot.send_message(cid, text, parse_mode="Markdown", reply_markup=kb)
                    except Exception as e:
                        log.error(f"Zone alert {cid}: {e}")
                log.info(f"ZONE alert: {sym} {side} price={fp(price)}")

# ═══════════════════════════════════════════
# ОТПРАВКА
# ═══════════════════════════════════════════
def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌍 /1 Обзор",   callback_data="market_overview"),
         InlineKeyboardButton("🤖 /3 Сигналы", callback_data="signals"),
         InlineKeyboardButton("🚀 /5 Ракеты",  callback_data="rockets")],
        [InlineKeyboardButton("📊 /4 Топ",     callback_data="report"),
         InlineKeyboardButton("⏱ 1ч",         callback_data="period_1h"),
         InlineKeyboardButton("📅 24ч",        callback_data="period_24h")],
    ])

async def send_coin(bot, chat_id, symbol, slug, a, text):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 TradingView", url=tv_link(symbol))],
        [InlineKeyboardButton("🔄 Обновить", callback_data=f"coin_{symbol}"),
         InlineKeyboardButton("CMC", url=cmc_link(slug))],
        [InlineKeyboardButton("🌍 /1 Обзор", callback_data="market_overview"),
         InlineKeyboardButton("🤖 /3 Сигналы", callback_data="signals")],
    ])

    # Supertrend — получаем реальный сигнал
    try:
        st_data = get_supertrend_signal(symbol)
        a["st_label"] = st_data["label"]
        # Если текст ещё не содержит ST — пересобираем с ST
        if "Supertrend" not in text:
            stats_24h_for_text = get_binance_24h(symbol)
            text = build_signal_text(symbol, a, stats_24h_for_text)
    except Exception as e:
        log.error(f"ST fetch {symbol}: {e}")

    stats_24h = get_binance_24h(symbol)
    chart = None
    try:
        chart = generate_signal_chart(symbol, a, stats_24h)
        log.info(f"Chart OK: {symbol} {chart.getbuffer().nbytes} bytes")
    except Exception as e:
        log.error(f"Chart FAILED {symbol}: {type(e).__name__}: {e}")

    caption = text if len(text) <= 1024 else text[:1020] + "..."

    if chart is not None:
        try:
            chart.seek(0)
            await bot.send_photo(chat_id=chat_id, photo=chart,
                                 caption=caption, parse_mode="Markdown",
                                 reply_markup=kb)
            log.info(f"send_photo OK: {symbol}")
            return
        except Exception as e:
            log.error(f"send_photo FAILED {symbol}: {type(e).__name__}: {e}")
            try:
                chart.seek(0)
                await bot.send_photo(chat_id=chat_id, photo=chart)
                await bot.send_message(chat_id, text, parse_mode="Markdown",
                                       reply_markup=kb, disable_web_page_preview=True)
                return
            except Exception as e2:
                log.error(f"send_photo split FAILED {symbol}: {e2}")

    await bot.send_message(chat_id, text, parse_mode="Markdown",
                           reply_markup=kb, disable_web_page_preview=True)

async def send_signals_batch(bot, chat_id, coins):
    analyzed = [(c, full_analysis(c)) for c in coins]

    # Сортировка по Rocket Score — лучшие монеты первыми
    rockets  = sorted([(c,a) for c,a in analyzed if a["rocket"] >= 65 and a["is_long"]],
                      key=lambda x: x[1]["rocket"], reverse=True)[:3]
    longs    = sorted([(c,a) for c,a in analyzed if a["score"] >= 3],
                      key=lambda x: x[1]["score"], reverse=True)[:5]
    shorts   = sorted([(c,a) for c,a in analyzed if a["score"] <= -3],
                      key=lambda x: x[1]["score"])[:3]

    # Убираем дубликаты (rocket монеты уже могут быть в longs)
    rocket_syms = {c["symbol"] for c,a in rockets}
    longs = [(c,a) for c,a in longs if c["symbol"] not in rocket_syms]

    nav = InlineKeyboardMarkup([[
        InlineKeyboardButton("🌍 /1 Обзор",    callback_data="market_overview"),
        InlineKeyboardButton("🤖 /3 Сигналы",  callback_data="signals"),
        InlineKeyboardButton("🚀 /5 Ракеты",   callback_data="rockets"),
    ]])

    # ── ЗАГОЛОВОК СИГНАЛОВ ──
    header_lines = [
        "🤖 *BEST TRADE — Сигналы*",
        f"🕐 {now_utc3()}",
        "",
    ]
    if rockets:
        header_lines.append("🚀🔥 *ЛУЧШИЕ СЕТАПЫ (Rocket ≥65):*")
        for c, a in rockets:
            header_lines.append(f"  • *{c['symbol']}*  Score `{a['rocket']}/100`  {a['rocket_label']}")
        header_lines.append("")
    header_lines += [
        f"🟢 Лонг: {len(longs)+len(rockets)}  |  🔴 Шорт: {len(shorts)}",
        f"📊 Топ-500 CoinMarketCap",
    ]
    await bot.send_message(chat_id, "\n".join(header_lines),
                           parse_mode="Markdown", reply_markup=nav)

    # ── ROCKET МОНЕТЫ ПЕРВЫМИ ──
    for coin, a in rockets:
        sym   = coin["symbol"]
        slug  = coin.get("slug", sym.lower())
        stats = get_binance_24h(sym)
        text  = build_signal_text(sym, a, stats)
        await send_coin(bot, chat_id, sym, slug, a, text)
        await asyncio.sleep(1.5)

    # ── ОБЫЧНЫЕ ЛОНГИ ──
    for coin, a in longs:
        sym   = coin["symbol"]
        slug  = coin.get("slug", sym.lower())
        stats = get_binance_24h(sym)
        text  = build_signal_text(sym, a, stats)
        await send_coin(bot, chat_id, sym, slug, a, text)
        await asyncio.sleep(1.5)

    # ── ШОРТЫ ──
    for coin, a in shorts:
        sym   = coin["symbol"]
        slug  = coin.get("slug", sym.lower())
        stats = get_binance_24h(sym)
        text  = build_signal_text(sym, a, stats)
        await send_coin(bot, chat_id, sym, slug, a, text)
        await asyncio.sleep(1.5)

    await bot.send_message(chat_id,
        "⚠️ *Риск:* 2-3% депозита\nСтоп *ВСЕГДА* до входа в сделку!",
        parse_mode="Markdown")

# ═══════════════════════════════════════════
# HANDLERS
# ═══════════════════════════════════════════
user_chat_ids = set()

def load_chat_ids():
    try:
        with open("chat_ids.txt") as f:
            return set(int(l.strip()) for l in f if l.strip())
    except:
        return set()

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    user_chat_ids.add(cid)
    with open("chat_ids.txt", "a") as f:
        f.write(f"{cid}\n")
    await update.message.reply_text(
        "📊 *BEST TRADE v10.0*\n\n"
        "Топ-500 • CoinMarketCap\n"
        "🚀 Rocket Score — умный поиск ракет\n"
        "🧠 SMC + TA + Фундаментал (20+ факторов)\n"
        "📈 Графики EMA20/50/200 + уровни\n"
        "⚡️ Pump/Dump + Zone алерты\n"
        "📅 Min/Max дня + лучший вход\n"
        "🕐 Время UTC+3\n\n"
        "/1 — обзор рынка\n"
        "/2 BTC — анализ монеты\n"
        "/3 — торговые сигналы\n"
        "/4 — топ рынка\n"
        "/5 — 🚀 ракеты (лучшие сетапы)",
        parse_mode="Markdown", reply_markup=main_kb()
    )

async def cmd_market(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Загружаю...")
    try:
        prices = get_btc_eth_price(); gm = get_global_metrics(); coins = get_top500()
        ms = analyze_market(prices.get("BTC", {}), prices.get("ETH", {}), gm, coins)
        await msg.edit_text(build_overview_text(ms), parse_mode="Markdown",
                            reply_markup=overview_kb(), disable_web_page_preview=True)
    except Exception as e:
        log.error(f"cmd_market: {e}")
        await msg.edit_text("❌ Ошибка")

async def cmd_coin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Напиши: `/2 BTC`", parse_mode="Markdown")
        return
    symbol = ctx.args[0].upper()
    msg    = await update.message.reply_text(f"⏳ Анализирую {symbol}...")
    coins  = get_top500()
    coin   = next((c for c in coins if c["symbol"] == symbol), None)
    if not coin:
        await msg.edit_text(f"❌ {symbol} не найден в топ-500")
        return
    a     = full_analysis(coin)
    slug  = coin.get("slug", symbol.lower())
    stats = get_binance_24h(symbol)
    atl   = get_binance_alltime_low(symbol)
    text  = build_signal_text(symbol, a, stats, atl)
    await msg.delete()
    await send_coin(ctx.bot, update.effective_chat.id, symbol, slug, a, text)

async def cmd_signals(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Анализирую топ-500... ~60 сек")
    coins = get_top500()
    if not coins:
        await msg.edit_text("❌ Нет данных"); return
    await msg.delete()
    await send_signals_batch(ctx.bot, update.effective_chat.id, coins)

async def cmd_top(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg   = await update.message.reply_text("⏳ Загружаю...")
    coins = get_top500()
    if not coins:
        await msg.edit_text("❌ Нет данных"); return
    up  = sorted(coins, key=lambda x: x["quote"]["USDT"].get("percent_change_24h", 0), reverse=True)
    dn  = sorted(coins, key=lambda x: x["quote"]["USDT"].get("percent_change_24h", 0))
    pos = sum(1 for c in coins if c["quote"]["USDT"].get("percent_change_24h", 0) > 0)

    def row(i, c):
        q  = c["quote"]["USDT"]
        ch = q.get("percent_change_24h", 0)
        em = "🚀" if ch >= 5 else ("🟢" if ch >= 0 else ("🔴" if ch >= -5 else "💥"))
        return f"{em} {i}. *{c['symbol']}*  ${fp(q['price'])}  {fc(ch)}"

    nav = InlineKeyboardMarkup([[
        InlineKeyboardButton("🌍 /1 Обзор",   callback_data="market_overview"),
        InlineKeyboardButton("🤖 /3 Сигналы", callback_data="signals"),
        InlineKeyboardButton("🚀 /5 Ракеты",  callback_data="rockets"),
    ]])
    t1 = [f"🔥 *Топ-500 — BEST TRADE*", f"🕐 {now_utc3()}",
          f"Растут: {pos}/{len(coins)} ({pos/len(coins)*100:.0f}%)", "",
          "🚀 *ЛИДЕРЫ РОСТА 24ч*"]
    t1 += [row(i, c) for i, c in enumerate(up[:15], 1)]
    t2  = ["📉 *ЛИДЕРЫ ПАДЕНИЯ 24ч*"]
    t2 += [row(i, c) for i, c in enumerate(dn[:15], 1)]

    await msg.edit_text("\n".join(t1), parse_mode="Markdown", reply_markup=nav)
    await ctx.bot.send_message(update.effective_chat.id, "\n".join(t2),
                               parse_mode="Markdown", reply_markup=nav)

async def cmd_rockets(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Топ монет по Rocket Score — самые перспективные"""
    msg = await update.message.reply_text("🚀 Ищу ракеты в топ-500... ~30 сек")
    coins = get_top500()
    if not coins:
        await msg.edit_text("❌ Нет данных"); return

    analyzed = [(c, full_analysis(c)) for c in coins]
    rockets  = sorted([(c,a) for c,a in analyzed],
                      key=lambda x: x[1]["rocket"], reverse=True)[:10]

    nav = InlineKeyboardMarkup([[
        InlineKeyboardButton("🌍 /1 Обзор",   callback_data="market_overview"),
        InlineKeyboardButton("🤖 /3 Сигналы", callback_data="signals"),
    ]])

    lines = [
        "🚀🔥 *BEST TRADE — РАКЕТЫ*",
        f"🕐 {now_utc3()}",
        f"Топ-10 по Rocket Score из 500 монет",
        "",
    ]
    for i, (c, a) in enumerate(rockets, 1):
        sym  = c["symbol"]
        r    = a["rocket"]
        filled = int(r / 10)
        bar  = "█" * filled + "░" * (10 - filled)
        side = "🟢L" if a["is_long"] else "🔴S"
        smc  = " | ".join(a.get("smc_factors", [])[:3]) or "—"
        lines += [
            f"{i}. *{sym}*  `{r}/100` {a['rocket_label']}",
            f"   `{bar}` {side}",
            f"   💰`{fp(a['price'])}`  24H`{fc(a['ch24h'])}`  RSI`{a['rsi_4h']:.0f}`",
            f"   🧠 {smc}",
            "",
        ]
    lines.append("⚠️ Риск: 2% депозита | SL обязателен")

    await msg.delete()
    await ctx.bot.send_message(
        update.effective_chat.id, "\n".join(lines),
        parse_mode="Markdown", reply_markup=nav,
        disable_web_page_preview=True
    )

    # Отправляем топ-3 с графиком
    for coin, a in rockets[:3]:
        sym   = coin["symbol"]
        slug  = coin.get("slug", sym.lower())
        stats = get_binance_24h(sym)
        text  = build_signal_text(sym, a, stats)
        await send_coin(ctx.bot, update.effective_chat.id, sym, slug, a, text)
        await asyncio.sleep(1.5)
    msg   = await update.message.reply_text("⏳ Загружаю...")
    coins = get_top500()
    if not coins:
        await msg.edit_text("❌ Нет данных"); return
    now = datetime.now(TZ).strftime("%d.%m.%Y %H:%M UTC+3")
    up  = sorted(coins, key=lambda x: x["quote"]["USDT"].get("percent_change_24h", 0), reverse=True)
    dn  = sorted(coins, key=lambda x: x["quote"]["USDT"].get("percent_change_24h", 0))
    pos = sum(1 for c in coins if c["quote"]["USDT"].get("percent_change_24h", 0) > 0)

    def row(i, c):
        q  = c["quote"]["USDT"]
        ch = q.get("percent_change_24h", 0)
        em = "🚀" if ch >= 5 else ("🟢" if ch >= 0 else ("🔴" if ch >= -5 else "💥"))
        return f"{em} {i}. *{c['symbol']}*  ${fp(q['price'])}  {fc(ch)}"

    t1 = [f"🔥 *Топ-500 — BEST TRADE*", f"🕐 {now}",
          f"Растут: {pos}/{len(coins)} ({pos/len(coins)*100:.0f}%)", "",
          "🚀 *ЛИДЕРЫ РОСТА 24ч*"]
    t1 += [row(i, c) for i, c in enumerate(up[:15], 1)]
    t2  = ["📉 *ЛИДЕРЫ ПАДЕНИЯ 24ч*"]
    t2 += [row(i, c) for i, c in enumerate(dn[:15], 1)]

    nav = InlineKeyboardMarkup([[
        InlineKeyboardButton("🌍 /1 Обзор", callback_data="market_overview"),
        InlineKeyboardButton("🤖 /3 Сигналы", callback_data="signals"),
    ]])
    await msg.edit_text("\n".join(t1), parse_mode="Markdown", reply_markup=nav)
    await ctx.bot.send_message(update.effective_chat.id, "\n".join(t2),
                               parse_mode="Markdown", reply_markup=nav)

async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; data = q.data; await q.answer()

    if data == "market_overview":
        await q.edit_message_text("⏳ Загружаю...", parse_mode="Markdown")
        try:
            prices = get_btc_eth_price(); gm = get_global_metrics(); coins = get_top500()
            ms = analyze_market(prices.get("BTC", {}), prices.get("ETH", {}), gm, coins)
            await q.edit_message_text(build_overview_text(ms), parse_mode="Markdown",
                                      reply_markup=overview_kb(), disable_web_page_preview=True)
        except Exception as e:
            log.error(f"overview cb: {e}")
            await q.edit_message_text("❌ Ошибка")

    elif data == "signals":
        await q.edit_message_text("⏳ Загружаю сигналы...", parse_mode="Markdown")
        coins = get_top500()
        if not coins:
            await q.edit_message_text("❌ Нет данных"); return
        await send_signals_batch(ctx.bot, q.message.chat_id, coins)

    elif data == "rockets":
        await q.edit_message_text("🚀 Ищу ракеты...", parse_mode="Markdown")
        coins = get_top500()
        if not coins:
            await q.edit_message_text("❌ Нет данных"); return
        analyzed = [(c, full_analysis(c)) for c in coins]
        rockets  = sorted([(c,a) for c,a in analyzed],
                          key=lambda x: x[1]["rocket"], reverse=True)[:10]
        nav = InlineKeyboardMarkup([[
            InlineKeyboardButton("🌍 /1 Обзор",   callback_data="market_overview"),
            InlineKeyboardButton("🤖 /3 Сигналы", callback_data="signals"),
        ]])
        lines = ["🚀🔥 *РАКЕТЫ — Rocket Score*", f"🕐 {now_utc3()}", ""]
        for i, (c, a) in enumerate(rockets, 1):
            r = a["rocket"]; bar = "█"*int(r/10)+"░"*(10-int(r/10))
            side = "🟢L" if a["is_long"] else "🔴S"
            lines.append(f"{i}. *{c['symbol']}* `{r}/100` {side}  `{bar}`")
        await q.edit_message_text("\n".join(lines), parse_mode="Markdown",
                                  reply_markup=nav, disable_web_page_preview=True)

    elif data == "report":
        await q.edit_message_text("⏳ Загружаю...", parse_mode="Markdown")
        coins = get_top500()
        if coins:
            up  = sorted(coins, key=lambda x: x["quote"]["USDT"].get("percent_change_24h", 0), reverse=True)
            txt = "\n".join([f"🚀 *Топ рост 24ч*", f"🕐 {now_utc3()}"] +
                            [f"{i}. *{c['symbol']}*  ${fp(c['quote']['USDT']['price'])}  {fc(c['quote']['USDT'].get('percent_change_24h',0))}"
                             for i, c in enumerate(up[:20], 1)])
            await q.edit_message_text(txt, parse_mode="Markdown",
                                      reply_markup=overview_kb(), disable_web_page_preview=True)

    elif data.startswith("coin_"):
        symbol = data[5:]; cid = q.message.chat_id
        await q.edit_message_text(f"⏳ Обновляю {symbol}...")
        coins = get_top500()
        coin  = next((c for c in coins if c["symbol"] == symbol), None)
        if not coin:
            await q.edit_message_text(f"❌ {symbol} не найден"); return
        a    = full_analysis(coin)
        slug = coin.get("slug", symbol.lower())
        stats = get_binance_24h(symbol)
        text  = build_signal_text(symbol, a, stats)
        try: await q.message.delete()
        except: pass
        await send_coin(ctx.bot, cid, symbol, slug, a, text)

    elif data.startswith("period_"):
        period = data.split("_")[1]
        field  = {"1h": "percent_change_1h", "24h": "percent_change_24h",
                  "7d": "percent_change_7d"}.get(period, "percent_change_24h")
        coins  = get_top500()
        if not coins:
            await q.edit_message_text("❌ Нет данных"); return
        up  = sorted(coins, key=lambda x: x["quote"]["USDT"].get(field, 0), reverse=True)
        lbl = {"1h": "1 ЧАС", "24h": "24 ЧАСА", "7d": "7 ДНЕЙ"}.get(period, "24 ЧАСА")
        txt = "\n".join([f"📊 *Топ за {lbl}*", f"🕐 {now_utc3()}", ""] +
                        [f"{i}. *{c['symbol']}*  ${fp(c['quote']['USDT']['price'])}  {fc(c['quote']['USDT'].get(field,0))}"
                         for i, c in enumerate(up[:15], 1)])
        await q.edit_message_text(txt, parse_mode="Markdown",
                                  reply_markup=overview_kb(), disable_web_page_preview=True)

# ═══════════════════════════════════════════
# РАССЫЛКА
# ═══════════════════════════════════════════
async def send_scheduled(bot: Bot):
    chat_ids = load_chat_ids() | user_chat_ids
    if not chat_ids: return
    log.info(f"Рассылка {now_utc3()}")
    coins  = get_top500()
    prices = get_btc_eth_price()
    gm     = get_global_metrics()
    if not coins: return
    ms   = analyze_market(prices.get("BTC", {}), prices.get("ETH", {}), gm, coins)
    text = build_overview_text(ms)
    kb   = overview_kb()
    for cid in chat_ids:
        try:
            await bot.send_message(cid, text, parse_mode="Markdown",
                                   reply_markup=kb, disable_web_page_preview=True)
            await asyncio.sleep(1)
            await send_signals_batch(bot, cid, coins)
        except Exception as e:
            log.error(f"Рассылка {cid}: {e}")

supertrend_cache = {}  # {symbol: last_direction}

async def check_supertrend_signals(bot, chat_ids, coins):
    """
    Проверяет смену Supertrend для топ монет.
    Алерт когда BUY→SELL или SELL→BUY переключение.
    Проверяем топ-50 по объёму (остальные слишком медленно).
    """
    now_ts = datetime.now(TZ).timestamp()
    # Берём топ-50 по объёму 24h
    top_by_vol = sorted(coins,
                        key=lambda x: x["quote"]["USDT"].get("volume_24h", 0),
                        reverse=True)[:50]

    for coin in top_by_vol:
        sym = coin["symbol"]
        try:
            st_data = get_supertrend_signal(sym)
            new_dir = st_data["direction"]
            old_dir = supertrend_cache.get(sym)

            # Обновляем кэш
            supertrend_cache[sym] = new_dir

            # Сигнал только при смене
            if old_dir is None or old_dir == new_dir:
                continue

            slug       = coin.get("slug", sym.lower())
            signal_lbl = "🟢 BUY" if new_dir == 1 else "🔴 SELL"
            prev_lbl   = "🔴 SELL" if new_dir == 1 else "🟢 BUY"
            price      = st_data["current_price"]
            pct        = st_data["pct_since_signal"]
            last_sig   = st_data.get("last_signal", "")
            last_price = st_data.get("last_signal_price")
            last_time  = st_data.get("last_signal_time")

            time_str = last_time.strftime("%d.%m %H:%M UTC+3") if last_time else "—"
            pct_str  = f"+{pct:.2f}%" if pct >= 0 else f"{pct:.2f}%"

            text = (
                f"⚡️ *SUPERTREND — смена сигнала!*\n"
                f"🕐 {now_utc3()}\n\n"
                f"*{sym}USDT*  {prev_lbl} ➜ *{signal_lbl}*\n\n"
                f"💰 Цена сейчас: `{fp(price)}`\n"
            )
            if last_price:
                text += f"📍 Последний сигнал: `{fp(last_price)}` ({time_str})\n"
                text += f"📈 Движение с сигнала: `{pct_str}`\n"

            text += (
                f"\n"
                f"{'🚀 Преобладают покупатели — ищем лонг' if new_dir == 1 else '📉 Преобладают продавцы — осторожно'}\n\n"
                f"⚠️ Риск: 2% депозита | SL обязателен\n\n"
                f"#{sym}USDT"
            )
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("📈 TradingView", url=tv_link(sym)),
                InlineKeyboardButton("CMC", url=cmc_link(slug)),
            ]])
            for cid in chat_ids:
                try:
                    await bot.send_message(cid, text, parse_mode="Markdown", reply_markup=kb)
                except Exception as e:
                    log.error(f"ST alert {cid}: {e}")
            log.info(f"Supertrend {sym}: {prev_lbl}→{signal_lbl} @ {fp(price)}")
            await asyncio.sleep(0.5)

        except Exception as e:
            log.error(f"ST check {sym}: {e}")

async def check_alerts(bot: Bot):
    """Каждые 5 мин: pump/dump + zone + supertrend alerts"""
    chat_ids = load_chat_ids() | user_chat_ids
    if not chat_ids: return
    try:
        coins = get_top500()
        if not coins: return
        await check_pump_dump(bot, chat_ids, coins)
        await check_entry_zones(bot, chat_ids, coins)
        await check_supertrend_signals(bot, chat_ids, coins)
    except Exception as e:
        log.error(f"check_alerts: {e}")

# ═══════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("1",       cmd_market))
    app.add_handler(CommandHandler("2",       cmd_coin))
    app.add_handler(CommandHandler("3",       cmd_signals))
    app.add_handler(CommandHandler("4",       cmd_top))
    app.add_handler(CommandHandler("5",       cmd_rockets))
    # обратная совместимость
    app.add_handler(CommandHandler("market",  cmd_market))
    app.add_handler(CommandHandler("coin",    cmd_coin))
    app.add_handler(CommandHandler("signals", cmd_signals))
    app.add_handler(CommandHandler("top",     cmd_top))
    app.add_handler(CommandHandler("rockets", cmd_rockets))
    app.add_handler(CallbackQueryHandler(callback_handler))

    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(
        lambda: asyncio.create_task(send_scheduled(app.bot)),
        "interval", minutes=30
    )
    scheduler.add_job(
        lambda: asyncio.create_task(check_alerts(app.bot)),
        "interval", minutes=5
    )
    scheduler.start()
    log.info("✅ BEST TRADE v9.0 | TOP-500 | Pump/Dump | Zone Alerts | UTC+3")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
