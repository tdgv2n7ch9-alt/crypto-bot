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

# ── ЖУРНАЛ АКТИВНЫХ АЛЕРТОВ (как "Монеты в игре") ──
# {symbol: {"type": str, "time": datetime, "price": float, "status": "active"/"done"}}
active_game: dict = {}   # {symbol: {"type", "time", "price", "status", "done_time"}}
done_game:   list = []   # последние отработавшие (макс 20)
MAX_GAME_HISTORY = 100

def add_to_game(symbol: str, alert_type: str, price: float):
    """Добавляет монету в журнал алертов"""
    # Если уже есть — не перезаписываем (оставляем первое время)
    if symbol not in active_game:
        active_game[symbol] = {
            "type":      alert_type,
            "time":      datetime.now(TZ),
            "price":     price,
            "status":    "active",
            "done_time": None,
        }
    # Чистим старые (>48ч активные)
    cutoff = datetime.now(TZ).timestamp() - 48 * 3600
    to_del = [s for s, v in active_game.items()
              if v["time"].timestamp() < cutoff]
    for s in to_del:
        del active_game[s]

def mark_done(symbol: str, result: str = "выросла"):
    """Отмечает монету как отработавшую"""
    if symbol in active_game:
        active_game[symbol]["status"]    = "done"
        active_game[symbol]["done_time"] = datetime.now(TZ)
        active_game[symbol]["result"]    = result
        # Добавляем в done_game список
        done_game.insert(0, {
            "symbol":    symbol,
            "result":    result,
            "done_time": datetime.now(TZ),
        })
        if len(done_game) > 20:
            done_game.pop()

def build_game_digest() -> str:
    """
    Строит дайджест в формате VANGA:
    • SYMBOLUSDT — 🚀 памп
      ⏰ 21.06 16:19 UTC+3
    """
    # Активные — сортируем по времени (новые сверху)
    actives = [(s, v) for s, v in active_game.items()
               if v["status"] == "active"]
    actives.sort(key=lambda x: x[1]["time"].timestamp(), reverse=True)

    type_labels = {
        "pump":        "🚀 памп",
        "dump":        "💥 дамп",
        "level":       "📍 коснулась уровня",
        "watchlist":   "📍 коснулась уровня",
        "supertrend":  "⚡️ смена тренда",
        "precision":   "🎯 precision сетап",
        "zone":        "📍 коснулась уровня",
    }

    lines = []
    if actives:
        lines.append(f"🔥 *Монет в игре: {len(actives)}*\n")
        for sym, v in actives:
            lbl      = type_labels.get(v["type"], v["type"])
            t        = v["time"].strftime("%d.%m %H:%M")
            tv_url   = f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym}USDT"
            lines.append(f"• [{sym}USDT]({tv_url}) — {lbl}")
            lines.append(f"  ⏰ {t} UTC+3")
    else:
        lines.append("🔥 *Монет в игре: 0*\n")
        lines.append("_Алертов пока нет_")

    # Отработавшие
    if done_game:
        lines.append("")
        lines.append("✅ *Отработали:*")
        for d in done_game[:10]:
            sym    = d["symbol"]
            result = d["result"]
            t      = d["done_time"].strftime("%d.%m %H:%M")
            tv_url = f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym}USDT"
            emoji  = "📈" if "вырос" in result else ("📉" if "упал" in result else "✅")
            lines.append(f"• [{sym}USDT]({tv_url}) — {emoji} {result}")
            lines.append(f"  ⏰ {t} UTC+3")

    return "\n".join(lines)

# ═══════════════════════════════════════════
# DATA FUNCTIONS
# ═══════════════════════════════════════════
STABLECOINS = {
    "USDT","USDC","BUSD","DAI","FDUSD","TUSD","USDP","USDD","FRAX","LUSD",
    "SUSD","ALUSD","GUSD","HUSD","EURS","XAUT","PAXG","WBTC","WETH","STETH",
    "WSTETH","RETH","CBETH","SFRXETH","ANKRETH","BETH","BETH","UST","USTC",
    "MIM","FEI","OUSD","DOLA","CUSD","CEUR","USDX","USDJ","USDN","BITCNY",
}

def get_top500():
    try:
        url     = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
        headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
        params  = {"limit": 600, "convert": "USDT", "sort": "market_cap"}
        r = requests.get(url, headers=headers, params=params, timeout=20)
        r.raise_for_status()
        raw = r.json().get("data", [])

        # Фильтруем стейблкоины и дубликаты символов
        seen_syms = set()
        result    = []
        for coin in raw:
            sym  = coin.get("symbol", "")
            tags = [t.lower() for t in coin.get("tags", [])]
            # Пропускаем стейблкоины
            if sym in STABLECOINS:             continue
            if "stablecoin" in tags:           continue
            if "wrapped-tokens" in tags:       continue
            # Пропускаем дубликаты символов (берём первый = выше по капе)
            if sym in seen_syms:               continue
            seen_syms.add(sym)
            result.append(coin)
            if len(result) >= 500:             break

        log.info(f"CMC: {len(result)} монет после фильтрации")
        return result
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

def get_funding_rate(symbol: str) -> dict:
    """Фандинг рейт с Binance Futures"""
    try:
        url    = "https://fapi.binance.com/fapi/v1/premiumIndex"
        params = {"symbol": f"{symbol}USDT"}
        r      = requests.get(url, params=params, timeout=8)
        r.raise_for_status()
        d    = r.json()
        rate = float(d.get("lastFundingRate", 0)) * 100  # в %
        mark = float(d.get("markPrice", 0))
        idx  = float(d.get("indexPrice", 0))
        # Basis = разница между фьючерсом и спотом
        basis = (mark - idx) / idx * 100 if idx > 0 else 0

        if rate > 0.1:    fr_signal = "🔴 Перегрет (лонги доминируют)"
        elif rate > 0.05: fr_signal = "🟡 Умеренный бычий"
        elif rate > 0:    fr_signal = "🟢 Нейтральный"
        elif rate > -0.05: fr_signal = "🟡 Умеренный медвежий"
        else:              fr_signal = "🟢 Шорт-сквиз возможен!"

        return {"rate": rate, "signal": fr_signal, "mark": mark, "basis": basis, "ok": True}
    except:
        return {"rate": 0, "signal": "—", "mark": 0, "basis": 0, "ok": False}

def get_open_interest(symbol: str) -> dict:
    """Open Interest с Binance Futures — изменение OI за 24ч"""
    try:
        # Текущий OI
        url = "https://fapi.binance.com/fapi/v1/openInterest"
        r   = requests.get(url, params={"symbol": f"{symbol}USDT"}, timeout=8)
        r.raise_for_status()
        oi_now = float(r.json().get("openInterest", 0))

        # OI Statistics (история)
        url2 = "https://fapi.binance.com/futures/data/openInterestHist"
        r2   = requests.get(url2, params={
            "symbol": f"{symbol}USDT", "period": "1h", "limit": 25
        }, timeout=8)
        r2.raise_for_status()
        hist = r2.json()
        if hist and len(hist) >= 2:
            oi_24h_ago = float(hist[0].get("sumOpenInterest", oi_now))
            oi_change  = (oi_now - oi_24h_ago) / oi_24h_ago * 100 if oi_24h_ago > 0 else 0
        else:
            oi_change = 0

        if oi_change > 5:    oi_signal = "📈 OI растёт — тренд усиливается"
        elif oi_change > 1:  oi_signal = "🟢 OI умеренно растёт"
        elif oi_change > -1: oi_signal = "🟡 OI стабилен"
        elif oi_change > -5: oi_signal = "🟠 OI снижается — позиции закрываются"
        else:                oi_signal = "🔴 OI резко падает — осторожно"

        return {"oi": oi_now, "change": oi_change, "signal": oi_signal, "ok": True}
    except:
        return {"oi": 0, "change": 0, "signal": "—", "ok": False}

def get_market_extras(symbol: str) -> dict:
    """Получаем фандинг + OI за один вызов"""
    fr = get_funding_rate(symbol)
    oi = get_open_interest(symbol)
    return {"funding": fr, "oi": oi}

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
    Пробует USDT, затем BUSD как fallback.
    """
    candles = get_binance_ohlc(symbol, interval="4h", limit=100)

    # Fallback: некоторые монеты торгуются только в паре с BTC или BNB
    if not candles or len(candles) < 20:
        try:
            url    = "https://api.binance.com/api/v3/klines"
            # Пробуем другие пары
            for quote in ["BUSD", "BTC", "ETH"]:
                r = requests.get(url,
                    params={"symbol": f"{symbol}{quote}", "interval": "4h", "limit": 100},
                    timeout=8)
                if r.status_code == 200:
                    data = r.json()
                    if data:
                        candles = [
                            {"time": datetime.fromtimestamp(d[0]/1000, tz=TZ),
                             "open": float(d[1]), "high": float(d[2]),
                             "low": float(d[3]), "close": float(d[4]),
                             "vol": float(d[5])}
                            for d in data
                        ]
                        break
        except:
            pass

    if not candles or len(candles) < 20:
        log.warning(f"Supertrend: нет данных для {symbol}")
        return {"direction": 1, "last_signal": None, "label": "—", "current_price": 0}

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
            pct = -pct

    label = "🟢 BUY" if current_dir == 1 else "🔴 SELL"

    return {
        "direction":         current_dir,
        "supertrend_value":  current_val,
        "label":             label,
        "last_signal":       last_signal,
        "last_signal_price": last_signal_price,
        "last_signal_time":  last_signal_time,
        "pct_since_signal":  round(pct, 2),
        "current_price":     current_price,
        "st_values":         st,
        "candles":           candles,
    }  # для шорта инвертируем


def full_analysis(coin: dict) -> dict:
    """
    Многофакторный анализ. Ключевое правило:
    - is_long определяется ТОЛЬКО реальными данными (ch24h, ch7d, ch30d)
    - Rocket Score учитывает качество сетапа, а не просто рост
    - Vol/MCap > 50% = подозрительно → штраф
    - Монета должна РЕАЛЬНО расти чтобы быть LONG
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

    suspicious = vol_ratio > 50  # ETF-прокси, памп-энд-дамп

    def rsi_est(m):
        if m > 15:  return 82.0
        if m > 8:   return 70.0
        if m > 3:   return 60.0
        if m > 0:   return 52.0
        if m > -3:  return 45.0
        if m > -8:  return 35.0
        if m > -15: return 25.0
        return 18.0

    m4h    = ch1h * 0.4 + ch24h * 0.6
    rsi_1h = rsi_est(ch1h)
    rsi_4h = rsi_est(m4h)
    rsi_1d = rsi_est(ch24h)

    # EMA - улучшенная логика (90д для EMA200)
    above_ema20  = ch7d  > -5
    above_ema50  = ch30d > -20
    above_ema200 = ch90d > -50

    momentum_pos    = ch1h > 0 and ch24h > 0
    tf_bull_strong  = ch1h > 0 and ch24h > 2  and ch7d > 3
    tf_bear_strong  = ch1h < 0 and ch24h < -2 and ch7d < -3
    tf_aligned_bull = ch1h > 0 and ch24h > 0 and ch7d > 0 and ch30d > 0
    tf_aligned_bear = ch1h < 0 and ch24h < 0 and ch7d < 0 and ch30d < 0

    bb_squeeze  = abs(ch1h) < 0.3
    bb_breakout = abs(ch1h) >= 3.0
    macd_bullish = ch7d > 0 and ch24h > ch7d / 7
    macd_bearish = ch7d < 0 and ch24h < ch7d / 7

    vol_spike        = 5 <= vol_ratio <= 50
    vol_high         = 2 <= vol_ratio < 5
    vol_low          = vol_ratio < 1
    vol_confirm_bull = ch24h > 3 and 3 <= vol_ratio <= 50
    vol_confirm_bear = ch24h < -3 and 3 <= vol_ratio <= 50

    smc_bos_bull    = ch7d  > 8  and ch30d < -10
    smc_bos_bear    = ch7d  < -8 and ch30d > 10
    smc_ob_accum    = 5 <= vol_ratio <= 40 and abs(ch24h) < 2
    smc_liq_sweep   = ch1h < -3 and vol_ratio >= 5
    smc_smart_accum = ch24h < -3 and ch7d > 0 and 5 <= vol_ratio <= 50
    smc_smart_dist  = ch24h > 8  and 10 <= vol_ratio <= 50
    smc_fvg_bull    = ch1h >= 2 and ch24h > 3
    smc_fvg_bear    = ch1h <= -2 and ch24h < -3

    fund_rank_top20  = rank <= 20
    fund_rank_top50  = rank <= 50
    fund_rank_top200 = rank <= 200
    fund_liquid      = vol >= 10_000_000 and vol_ratio <= 50
    fund_mega        = mcap >= 1_000_000_000
    fund_mid         = mcap >= 100_000_000
    fund_recovery    = ch90d < -40 and ch7d > 5 and not suspicious

    # DIRECTION SCORE
    dir_score = 0
    if ch24h >= 10:   dir_score += 4
    elif ch24h >= 5:  dir_score += 3
    elif ch24h >= 2:  dir_score += 2
    elif ch24h >= 0:  dir_score += 1
    elif ch24h >= -3:  dir_score -= 1
    elif ch24h >= -8:  dir_score -= 2
    elif ch24h >= -15: dir_score -= 3
    else:              dir_score -= 4

    if ch7d >= 10:  dir_score += 3
    elif ch7d >= 3: dir_score += 2
    elif ch7d >= 0: dir_score += 1
    elif ch7d >= -5:  dir_score -= 1
    elif ch7d >= -15: dir_score -= 2
    else:             dir_score -= 3

    if ch30d >= 20: dir_score += 2
    elif ch30d >= 5: dir_score += 1
    elif ch30d >= -10: pass
    elif ch30d >= -25: dir_score -= 1
    else:              dir_score -= 2

    if ch1h >= 2: dir_score += 1
    elif ch1h <= -2: dir_score -= 1

    if smc_bos_bull:    dir_score += 2
    if smc_smart_accum: dir_score += 2
    if tf_aligned_bull: dir_score += 2
    if smc_bos_bear:    dir_score -= 2
    if tf_aligned_bear: dir_score -= 2
    if fund_recovery:   dir_score += 3
    if fund_rank_top20 and dir_score >= -2:
        dir_score = max(dir_score, 0)

    is_long = dir_score >= 0

    # ROCKET SCORE
    rocket = 50
    if above_ema200 and above_ema50 and above_ema20: rocket += 6
    elif above_ema50 and above_ema20:                rocket += 3
    elif above_ema20:                                rocket += 1
    elif not above_ema50 and not above_ema200:       rocket -= 8

    if is_long:
        if rsi_4h < 30:   rocket += 10
        elif rsi_4h < 40: rocket += 7
        elif rsi_4h < 50: rocket += 3
        elif rsi_4h > 75: rocket -= 5
        elif rsi_4h > 65: rocket -= 2
    else:
        if rsi_4h > 75:   rocket += 10
        elif rsi_4h > 65: rocket += 7
        elif rsi_4h > 55: rocket += 3
        elif rsi_4h < 35: rocket -= 5

    if tf_bull_strong and is_long:      rocket += 7
    if tf_bear_strong and not is_long:  rocket += 7
    if macd_bullish and is_long:        rocket += 4
    if macd_bearish and not is_long:    rocket += 4
    if bb_squeeze:                      rocket += 4
    if bb_breakout and momentum_pos and is_long: rocket += 5
    if vol_spike:   rocket += 5
    elif vol_high:  rocket += 2
    elif vol_low:   rocket -= 5
    if vol_confirm_bull and is_long:     rocket += 5
    if vol_confirm_bear and not is_long: rocket += 5
    if smc_bos_bull and is_long:         rocket += 7
    if smc_ob_accum and is_long:         rocket += 5
    if smc_liq_sweep and is_long:        rocket += 4
    if smc_smart_accum and is_long:      rocket += 8
    if smc_fvg_bull and is_long:         rocket += 3
    if fund_rank_top20:                  rocket += 6
    elif fund_rank_top50:                rocket += 4
    elif fund_rank_top200:               rocket += 2
    if fund_liquid:                      rocket += 3
    if fund_mega:                        rocket += 3
    elif fund_mid:                       rocket += 1
    if fund_recovery and is_long:        rocket += 9
    if suspicious:                       rocket -= 20
    if ch90d > 100:                      rocket -= 5
    if ch24h < -10 and is_long:          rocket -= 8
    if ch7d  < -15 and is_long:          rocket -= 6

    rocket = max(0, min(100, rocket))
    score  = dir_score - (3 if suspicious else 0)

    tp_mult = 1.0
    if rsi_4h < 35 and is_long: tp_mult = 1.3
    if vol_spike:                tp_mult = min(tp_mult * 1.15, 1.5)

    # Динамическая точность для очень маленьких цен
    def smart_round(val):
        if val == 0: return 0
        import math
        # Достаточно значимых цифр чтобы TP1/TP2/TP3 различались
        magnitude = math.floor(math.log10(abs(val)))
        precision = max(8, -magnitude + 3)
        return round(val, precision)

    if is_long:
        tp1   = smart_round(price * (1 + 0.04 * tp_mult))
        tp2   = smart_round(price * (1 + 0.08 * tp_mult))
        tp3   = smart_round(price * (1 + 0.15 * tp_mult))
        sl    = smart_round(price * 0.85)
        swing = smart_round(price * 0.92)
    else:
        tp1   = smart_round(price * (1 - 0.04 * tp_mult))
        tp2   = smart_round(price * (1 - 0.08 * tp_mult))
        tp3   = smart_round(price * (1 - 0.15 * tp_mult))
        sl    = smart_round(price * 1.15)
        swing = smart_round(price * 1.08)

    rr = abs(tp3 - price) / abs(sl - price) if abs(sl - price) > 0 else 0

    ema20_1h  = round(price / (1 + ch1h  / 100 * 0.15), 8)
    ema50_1h  = round(price / (1 + ch1h  / 100 * 0.40), 8)
    ema200_1h = round(price / (1 + ch1h  / 100 * 1.20), 8)
    ema20_4h  = round(price / (1 + ch24h / 100 * 0.10), 8)
    ema50_4h  = round(price / (1 + ch24h / 100 * 0.25), 8)
    ema200_4h = round(price / (1 + ch24h / 100 * 0.80), 8)
    ema20_1d  = round(price / (1 + ch7d  / 100 * 0.08), 8)
    ema50_1d  = round(price / (1 + ch7d  / 100 * 0.20), 8)
    ema200_1d = round(price / (1 + ch7d  / 100 * 0.60), 8)

    if rocket >= 80:   rocket_label = "🚀🔥 ROCKET"
    elif rocket >= 70: rocket_label = "🚀 СИЛЬНЫЙ"
    elif rocket >= 60: rocket_label = "✅ ХОРОШИЙ"
    elif rocket >= 50: rocket_label = "🟡 СРЕДНИЙ"
    elif rocket >= 40: rocket_label = "🟠 СЛАБЫЙ"
    else:              rocket_label = "🔴 ИЗБЕГАТЬ"

    if score >= 8:    label = "🚀🔥 СИЛЬНЫЙ ЛОНГ" if is_long else "💥 СИЛЬНЫЙ ШОРТ"
    elif score >= 5:  label = "🔥 ЛОНГ" if is_long else "🔻 ШОРТ"
    elif score >= 3:  label = "✅ ЛОНГ" if is_long else "📉 ШОРТ"
    elif score >= 1:  label = "📈 СЛАБЫЙ ЛОНГ"
    elif score >= -1: label = "⚪️ НЕЙТРАЛЬНО"
    else:             label = "🔻 ШОРТ"

    smc_factors = []
    if smc_bos_bull:    smc_factors.append("BOS ↑")
    if smc_bos_bear:    smc_factors.append("BOS ↓")
    if smc_ob_accum:    smc_factors.append("OB Накопление")
    if smc_liq_sweep:   smc_factors.append("Liq Sweep")
    if smc_smart_accum: smc_factors.append("Smart Accum 💎")
    if smc_smart_dist:  smc_factors.append("Smart Dist ⚠️")
    if smc_fvg_bull:    smc_factors.append("FVG ↑")
    if smc_fvg_bear:    smc_factors.append("FVG ↓")
    if tf_aligned_bull: smc_factors.append("TF Align Bull")
    if tf_aligned_bear: smc_factors.append("TF Align Bear")
    if fund_recovery:   smc_factors.append("Recovery 🔄")
    if bb_squeeze:      smc_factors.append("BB Squeeze")
    if macd_bullish:    smc_factors.append("MACD Bull")
    if macd_bearish:    smc_factors.append("MACD Bear")
    if suspicious:      smc_factors.append("⚠️ Vol аномалия")

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
        "above_ema200": above_ema200, "above_ema50": above_ema50, "above_ema20": above_ema20,
        "macd_bullish": macd_bullish, "macd_bearish": macd_bearish,
        "bb_squeeze": bb_squeeze, "vol_spike": vol_spike,
        "tf_aligned_bull": tf_aligned_bull, "smc_bos_bull": smc_bos_bull,
        "smc_smart_accum": smc_smart_accum, "fund_recovery": fund_recovery,
        "smc_factors": smc_factors, "suspicious": suspicious,
        "fund_rank_top50": fund_rank_top50, "fund_liquid": fund_liquid,
        "st_label": "—",
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
def build_signal_text(symbol: str, a: dict,
                      stats_24h: dict = None,
                      atl: float = 0,
                      extras: dict = None) -> str:
    """
    Чистый формат сигнала.
    Убрано: EMA таблица (видна на графике), Vol/MCap%, строка 1H/24H/7D/30D
    Добавлено: Фандинг рейт, Open Interest, итоговый вывод
    """
    is_long = a["is_long"]
    price   = a["price"]
    tp1, tp2, tp3 = a["tp1"], a["tp2"], a["tp3"]
    sl, swing     = a["sl"],  a["swing"]
    rsi_4h = a["rsi_4h"]
    rr     = a["rr"]
    vol    = a["vol"]
    rocket = a.get("rocket", 50)
    rocket_label = a.get("rocket_label", "")

    side_emoji = "🟢" if is_long else "🔴"
    side_text  = "LONG" if is_long else "SHORT"
    swing_lbl  = "Swing Low" if is_long else "Swing High"

    def pct(t):
        d = (t - price) / price * 100
        v = d if is_long else -d
        return f"+{v:.2f}%" if v >= 0 else f"{v:.2f}%"

    def sl_pct(): return f"-{abs(sl - price) / price * 100:.2f}%"

    vol_str = (f"${vol/1e9:.2f}B" if vol >= 1e9 else
               f"${vol/1e6:.1f}M" if vol >= 1e6 else f"${vol/1e3:.0f}K")

    filled = int(rocket / 10)
    bar    = "█" * filled + "░" * (10 - filled)

    # EMA позиция (кратко)
    ema_pos = []
    if a.get("above_ema200"): ema_pos.append("EMA200✅")
    if a.get("above_ema50"):  ema_pos.append("EMA50✅")
    if a.get("above_ema20"):  ema_pos.append("EMA20✅")
    if not ema_pos:           ema_pos = ["Ниже всех EMA ⚠️"]
    ema_str = " | ".join(ema_pos)

    # RSI иконка
    def rsi_icon(r):
        if r < 30: return "🟢"
        if r > 70: return "🔴"
        return "🔵"

    # SMC факторы — только значимые (без BB Squeeze)
    raw_smc = [f for f in a.get("smc_factors", [])
               if "BB Squeeze" not in f and "MACD" not in f]
    smc_key = raw_smc[:3] if raw_smc else []

    # Дополнительные индикаторы
    macd_str = "▲ Бычий" if a.get("macd_bullish") else ("▼ Медвежий" if a.get("macd_bearish") else "Нейтральный")
    st_str   = a.get("st_label", "—")

    # Итоговый вывод с учётом рисков
    rsi_4h   = a["rsi_4h"]
    ch24h    = a["ch24h"]
    overbought = rsi_4h > 75
    oversold   = rsi_4h < 30
    suspicious = a.get("suspicious", False)

    if suspicious:
        conclusion = "⚠️ Аномальный объём — высокий риск, осторожно"
    elif is_long and overbought and not oversold:
        conclusion = "🟡 Перекуплен — ждать отката для входа"
    elif is_long and rocket >= 75 and oversold:
        conclusion = "🔥 Перепродан + сильный сигнал — лучшая точка входа!"
    elif is_long and rocket >= 75:
        conclusion = "🔥 Высокий потенциал — приоритетный сетап"
    elif is_long and rocket >= 60:
        conclusion = "✅ Хороший сетап — можно рассматривать"
    elif not is_long and rocket >= 70:
        conclusion = "📉 Сильный шорт-сетап"
    elif is_long and a.get("smc_smart_accum"):
        conclusion = "💎 Smart Money накапливают — следим за входом"
    elif is_long and a.get("fund_recovery"):
        conclusion = "🔄 Восстановление после коррекции — DCA зона"
    elif not is_long and ch24h < -10:
        conclusion = "📉 Сильное падение — шорт или ждём дна"
    else:
        conclusion = "⚠️ Слабый сигнал — ждём подтверждения"

    lines = [
        f"📊 *{symbol}USDT*  {side_emoji} *{side_text}*",
        f"🕐 {now_utc3()}",
        "",
        f"🚀 *{rocket}/100* {rocket_label}  `{bar}`",
        f"📍 {ema_str}",
        f"💡 {conclusion}",
        "",
        f"💰 Вход:    `{fp(price)}`",
        f"🎯 TP1:    `{fp(tp1)}`  ({pct(tp1)})",
        f"🎯 TP2:    `{fp(tp2)}`  ({pct(tp2)})",
        f"🎯 TP3:    `{fp(tp3)}`  ({pct(tp3)})",
        f"🛑 SL:      `{fp(sl)}`  ({sl_pct()})",
        f"📌 {swing_lbl}:  `{fp(swing)}`",
        "",
        "━━━━━━━━━━━━━━━━━━",
        f"📐 R:R `1:{rr:.1f}`  |  💹 Объём `{vol_str}`  |  Rank `#{a.get('rank','—')}`",
        f"📈 RSI 4H {rsi_icon(rsi_4h)}`{rsi_4h:.0f}`  |  MACD `{macd_str}`",
        f"⚡️ Supertrend: `{st_str}`",
    ]

    # SMC факторы
    if smc_key:
        lines.append(f"🧠 SMC: `{'  •  '.join(smc_key)}`")

    # 24H min/max + лучший вход
    if stats_24h:
        h24 = stats_24h.get("high", 0)
        l24 = stats_24h.get("low",  0)
        if h24 and l24:
            best = l24 * 1.005 if is_long else h24 * 0.995
            lines += [
                "",
                "━━━━━━━━━━━━━━━━━━",
                f"📅 24H:  🔼 `{fp(h24)}`   🔽 `{fp(l24)}`",
                f"🎯 Лучший вход дня: `{fp(best)}`",
            ]

    # Фандинг + OI (если есть данные с фьючерсов)
    if extras:
        fr = extras.get("funding", {})
        oi = extras.get("oi", {})
        if fr.get("ok") or oi.get("ok"):
            lines.append("")
            lines.append("━━━━━━━━━━━━━━━━━━")
        if fr.get("ok"):
            rate_str = f"{fr['rate']:+.4f}%"
            lines.append(f"💸 *Фандинг:* `{rate_str}`  {fr['signal']}")
        if oi.get("ok") and oi.get("oi", 0) > 0:
            oi_ch = oi.get("change", 0)
            oi_str = f"{oi_ch:+.1f}% за 24ч"
            lines.append(f"📊 *OI:* `{oi_str}`  {oi['signal']}")

    # Исторический минимум
    if atl and atl > 0:
        from_atl = (price - atl) / atl * 100
        lines.append(f"🏆 От ист. минимума: `+{from_atl:.0f}%`  (min `{fp(atl)}`)")

    lines += ["", f"#{symbol}USDT"]
    return "\n".join(lines)

    lines += ["", f"#{symbol}USDT"]
    return "\n".join(lines)

# ═══════════════════════════════════════════
# РЫНОЧНЫЙ ОБЗОР
# ═══════════════════════════════════════════
BTC_ZONES = {
    "support":    [
        {"level": 63000, "label": "Ключевой"},
        {"level": 62137, "label": "S1 Королёв"},
        {"level": 61316, "label": "S2"},
        {"level": 59000, "label": "S3"},
    ],
    "resistance": [
        {"level": 64300, "label": "Локальный хай"},
        {"level": 65000, "label": "R1"},
        {"level": 67000, "label": "R2"},
    ],
}

# ── ВОТЧЛИСТ — зоны входа из сигнальных каналов ──
# Формат: symbol → {"long": [lo, hi], "short": [lo, hi], "note": str, "source": str}
WATCHLIST_ZONES = {
    "SOL": {
        "long":  [68.28, 69.34],
        "note":  "3 точки базы сформированы. Глобально не лучшее место, смотреть по факту",
        "source": "Королёв",
        "bias":  "LONG",
    },
    "ZK": {
        "short": [0.01278, 0.01314],
        "note":  "Ближайшая шорт-зона. Работаем",
        "source": "Королёв",
        "bias":  "SHORT",
    },
    "ACH": {
        "long":  [0.005030, 0.005138],
        "note":  "Формируем новую структуру на старшем ТФ. Зона интереса снизу",
        "source": "Королёв",
        "bias":  "LONG",
    },
    "EIGEN": {
        "long":  [0.1790, 0.1871],
        "note":  "4H структура, хороший объём. Зона интереса",
        "source": "Королёв",
        "bias":  "LONG",
    },
    "HYPE": {
        "long":  [None, None],  # зона по рынку
        "note":  "Превосходство над альтами. TOTAL3 разворачивается → цель ATH $77, потенциал до $80",
        "source": "Аналитика",
        "bias":  "LONG",
    },
    "ETH": {
        "short": [1710, 1737],
        "note":  "Imbalance зона. Ретест сверху → шорт. Цели: $1670, при слабости $1504. Рассинхрон с BTC",
        "source": "Аналитика",
        "bias":  "SHORT",
    },
    "APT": {
        "long":  [0.6300, 0.6397],
        "note":  "Нижняя граница структуры 4H. Зона интереса. SL за границу старшего ТФ с запасом (проколы возможны)",
        "source": "Королёв",
        "bias":  "LONG",
    },
    "ENA": {
        "long":  [0.0875, 0.0941],
        "note":  "Зона накопления 4H. Цель $0.106–0.110. Сигнал Джо подтверждает",
        "source": "Джо + График",
        "bias":  "LONG",
    },
    "BTC": {
        "long":  [62000, 63000],
        "note":  "Откат к $62K = возможность. Сценарий: коррекция → рост к $70K+",
        "source": "BIG TRADER",
        "bias":  "LONG",
    },
    "BEAT": {
        "long":  [1.00, 1.10],
        "note":  "Лучший сетап вотчлиста. Выше EMA200, CertiK аудит",
        "source": "BEST TRADE",
        "bias":  "LONG",
    },
    "PIPPIN": {
        "long":  [0.0120, 0.0135],
        "note":  "Вход при RSI < 30. Шорт от EMA200 (~0.0208)",
        "source": "BEST TRADE",
        "bias":  "LONG",
    },
    "AVAX": {
        "short": [4.449, 4.90],
        "note":  "Сильный нисходящий тренд с 2021",
        "source": "BEST TRADE",
        "bias":  "SHORT",
    },
}

def check_watchlist_alerts(coins: list) -> list:
    """
    Проверяет попадание цены в зоны вотчлиста.
    Возвращает список алертов.
    """
    alerts = []
    coin_map = {c["symbol"]: c for c in coins}

    for sym, info in WATCHLIST_ZONES.items():
        coin = coin_map.get(sym)
        if not coin:
            continue
        price = coin["quote"]["USDT"].get("price", 0)
        if not price:
            continue

        bias  = info.get("bias", "LONG")
        note  = info.get("note", "")
        src   = info.get("source", "")

        zone_key = "long" if bias == "LONG" else "short"
        zone = info.get(zone_key, [None, None])
        if not zone or zone[0] is None:
            continue

        lo, hi = zone
        in_zone = lo <= price <= hi

        if in_zone:
            emoji = "🟢" if bias == "LONG" else "🔴"
            alerts.append({
                "symbol": sym, "price": price,
                "lo": lo, "hi": hi,
                "bias": bias, "emoji": emoji,
                "note": note, "source": src,
            })

    return alerts

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

    # Топ лонги: score>=3 И ch24h>0 И ch7d>0 (реально растут)
    top_longs  = sorted(
        [(c,a) for c,a in analyzed
         if a["score"] >= 3
         and a["ch24h"] > 0 and a["ch7d"] > 0
         and not a.get("suspicious", False)
         and a["rsi_4h"] <= 78
         and a["vol"] >= 1_000_000],
        key=lambda x: (x[1]["score"], x[1]["rocket"]), reverse=True
    )[:5]

    top_shorts = sorted(
        [(c,a) for c,a in analyzed
         if a["score"] <= -4
         and a["ch24h"] < 0
         and not a.get("suspicious", False)
         and a["vol"] >= 1_000_000],
        key=lambda x: x[1]["score"]
    )[:5]

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
         InlineKeyboardButton("🏠 Меню",     callback_data="show_menu")],
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
                add_to_game(sym, "pump", price)
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
                add_to_game(sym, "dump", price)
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
                add_to_game(sym, "zone", price)
                log.info(f"ZONE alert: {sym} {side} price={fp(price)}")

# ═══════════════════════════════════════════
# ОТПРАВКА
# ═══════════════════════════════════════════
def main_kb():
    """Главное меню — все разделы по названию, без цифр"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌍 Обзор рынка",     callback_data="market_overview"),
         InlineKeyboardButton("🤖 Сигналы",         callback_data="signals"),
         InlineKeyboardButton("🚀 Ракеты",          callback_data="rockets")],
        [InlineKeyboardButton("📊 Топ рынка",       callback_data="report"),
         InlineKeyboardButton("🎯 Precision Shots", callback_data="precision"),
         InlineKeyboardButton("🔥 В игре",          callback_data="game")],
        [InlineKeyboardButton("👁 Вотчлист",        callback_data="watchlist"),
         InlineKeyboardButton("📈 Анализ монеты",   callback_data="menu_coin")],
    ])

def back_to_menu_kb():
    """Кнопка возврата в меню — добавляется после каждой команды"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Главное меню", callback_data="show_menu")],
    ])

async def send_coin(bot, chat_id, symbol, slug, a, text):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 TradingView", url=tv_link(symbol))],
        [InlineKeyboardButton("🔄 Обновить", callback_data=f"coin_{symbol}"),
         InlineKeyboardButton("CMC", url=cmc_link(slug))],
        [InlineKeyboardButton("🌍 /1 Обзор", callback_data="market_overview"),
         InlineKeyboardButton("🤖 /3 Сигналы", callback_data="signals")],
    ])

    # Если текст уже содержит Supertrend — используем как есть
    # Если нет — пробуем получить и вставить
    if "Supertrend: —" in text or "Supertrend: `—`" in text:
        try:
            st_data = get_supertrend_signal(symbol)
            if st_data.get("label") and st_data["label"] != "—":
                a["st_label"] = st_data["label"]
                # Заменяем в тексте
                text = text.replace("Supertrend: `—`", f"Supertrend: `{st_data['label']}`")
                text = text.replace("Supertrend: —",   f"Supertrend: {st_data['label']}")
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
    def good_long(a):
        """Качественный лонг-сигнал"""
        return (a["is_long"]
                and not a.get("suspicious", False)
                and a["ch24h"] > -3        # не падает сегодня
                and a["ch7d"]  > -10       # не обвалился за неделю
                and a["rsi_4h"] <= 80      # не сильно перекуплен
                and a["vol"] >= 500_000)   # минимальная ликвидность $500K

    def good_short(a):
        """Качественный шорт-сигнал"""
        return (not a["is_long"]
                and not a.get("suspicious", False)
                and a["ch24h"] < 3         # реально падает или нейтрален
                and a["rsi_4h"] >= 20      # не сильно перепродан
                and a["vol"] >= 500_000)

    rockets = sorted(
        [(c,a) for c,a in analyzed
         if a["rocket"] >= 68 and good_long(a)
         and a["ch7d"] > 0],              # хотя бы за неделю растёт
        key=lambda x: x[1]["rocket"], reverse=True
    )[:3]

    longs  = sorted(
        [(c,a) for c,a in analyzed if a["score"] >= 3 and good_long(a)],
        key=lambda x: x[1]["score"], reverse=True
    )[:5]

    shorts = sorted(
        [(c,a) for c,a in analyzed if a["score"] <= -4 and good_short(a)],
        key=lambda x: x[1]["score"]
    )[:3]

    # Убираем дубликаты
    rocket_syms = {c["symbol"] for c,a in rockets}
    longs = [(c,a) for c,a in longs if c["symbol"] not in rocket_syms]

    nav = InlineKeyboardMarkup([[
        InlineKeyboardButton("🌍 /1 Обзор",    callback_data="market_overview"),
        InlineKeyboardButton("🤖 /3 Сигналы",  callback_data="signals"),
        InlineKeyboardButton("🚀 /5 Ракеты",   callback_data="rockets"),
    ]])

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

    async def _send(coin, a):
        sym  = coin["symbol"]
        slug = coin.get("slug", sym.lower())
        # Supertrend
        try:
            st_data = get_supertrend_signal(sym)
            a["st_label"] = st_data["label"]
        except:
            a["st_label"] = "—"
        stats  = get_binance_24h(sym)
        extras = get_market_extras(sym)  # фандинг + OI
        text   = build_signal_text(sym, a, stats, extras=extras)
        await send_coin(bot, chat_id, sym, slug, a, text)
        await asyncio.sleep(2.0)  # чуть больше паузы из-за доп запросов

    for coin, a in rockets:
        await _send(coin, a)
    for coin, a in longs:
        await _send(coin, a)
    for coin, a in shorts:
        await _send(coin, a)

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
        "📊 *BEST TRADE v15.0*\n\n"
        "Топ-500 • Фандинг • OI • Supertrend\n"
        "🚀 Rocket Score + SMC + Фундаментал\n"
        "📍 Вотчлист с зонами\n"
        "⚡️ Алерты: Pump/Dump + Зоны + ST\n"
        "🕐 Время UTC+3\n\n"
        "👇 Выбери раздел:",
        parse_mode="Markdown", reply_markup=main_kb()
    )

async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Повторно показывает главное меню"""
    await update.message.reply_text(
        "📊 *BEST TRADE — Меню*\n\n👇 Выбери раздел:",
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
    await update.message.reply_text("📊 *BEST TRADE — Меню*\n\n👇 Выбери раздел:",
                                    parse_mode="Markdown", reply_markup=main_kb())

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
    a      = full_analysis(coin)
    slug   = coin.get("slug", symbol.lower())
    try:
        st_data = get_supertrend_signal(symbol)
        a["st_label"] = st_data["label"]
    except:
        a["st_label"] = "—"
    stats  = get_binance_24h(symbol)
    atl    = get_binance_alltime_low(symbol)
    extras = get_market_extras(symbol)
    text   = build_signal_text(symbol, a, stats, atl, extras)
    await msg.delete()
    await send_coin(ctx.bot, update.effective_chat.id, symbol, slug, a, text)
    await ctx.bot.send_message(update.effective_chat.id,
                               "📊 *BEST TRADE — Меню*\n\n👇 Выбери раздел:",
                               parse_mode="Markdown", reply_markup=main_kb())

async def cmd_signals(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Анализирую топ-500... ~60 сек")
    coins = get_top500()
    if not coins:
        await msg.edit_text("❌ Нет данных"); return
    await msg.delete()
    await send_signals_batch(ctx.bot, update.effective_chat.id, coins)
    await ctx.bot.send_message(update.effective_chat.id,
                               "📊 *BEST TRADE — Меню*\n\n👇 Выбери раздел:",
                               parse_mode="Markdown", reply_markup=main_kb())

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
        InlineKeyboardButton("🔄 Обновить",  callback_data="report"),
        InlineKeyboardButton("🏠 Меню",      callback_data="show_menu"),
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
    await ctx.bot.send_message(update.effective_chat.id,
                               "📊 *BEST TRADE — Меню*\n\n👇 Выбери раздел:",
                               parse_mode="Markdown", reply_markup=main_kb())

async def cmd_rockets(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Топ монет по Rocket Score — самые перспективные"""
    msg = await update.message.reply_text("🚀 Ищу ракеты в топ-500... ~30 сек")
    coins = get_top500()
    if not coins:
        await msg.edit_text("❌ Нет данных"); return

    analyzed = [(c, full_analysis(c)) for c in coins]

    # Строгие фильтры: не подозрительные, ликвидные, RSI не перекуплен
    rockets = sorted(
        [(c,a) for c,a in analyzed
         if not a.get("suspicious", False)
         and a["vol"] >= 1_000_000
         and a["rsi_4h"] <= 82
         and (a["is_long"] and a["ch7d"] > -5 or not a["is_long"])],
        key=lambda x: x[1]["rocket"], reverse=True
    )[:10]

    nav = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Обновить", callback_data="rockets"),
        InlineKeyboardButton("🏠 Меню",     callback_data="show_menu"),
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
        side = "🟢 LONG" if a["is_long"] else "🔴 SHORT"
        smc_clean = [f for f in a.get("smc_factors", []) if "BB Squeeze" not in f]
        smc  = " | ".join(smc_clean[:3]) or "—"
        rsi_warn = " ⚠️Перекуплен" if a["rsi_4h"] > 70 else ""
        lines += [
            f"{i}. *{sym}*  `{r}/100` {a['rocket_label']}  {side}",
            f"   `{bar}`",
            f"   💰`{fp(a['price'])}`  24H`{fc(a['ch24h'])}`  7D`{fc(a['ch7d'])}`  RSI`{a['rsi_4h']:.0f}`{rsi_warn}",
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

    await ctx.bot.send_message(update.effective_chat.id,
                               "📊 *BEST TRADE — Меню*\n\n👇 Выбери раздел:",
                               parse_mode="Markdown", reply_markup=main_kb())

async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; data = q.data; await q.answer()

    # ── Показать главное меню ──
    if data == "show_menu":
        await q.edit_message_text(
            "📊 *BEST TRADE — Меню*\n\n👇 Выбери раздел:",
            parse_mode="Markdown", reply_markup=main_kb()
        )

    elif data == "market_overview":
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
        await ctx.bot.send_message(q.message.chat_id,
                                   "📊 *BEST TRADE — Меню*\n\n👇 Выбери раздел:",
                                   parse_mode="Markdown", reply_markup=main_kb())

    elif data == "rockets":
        await q.edit_message_text("🚀 Ищу ракеты...", parse_mode="Markdown")
        coins = get_top500()
        if not coins:
            await q.edit_message_text("❌ Нет данных"); return
        analyzed = [(c, full_analysis(c)) for c in coins]
        rockets  = sorted([(c,a) for c,a in analyzed
                           if not a.get("suspicious", False)],
                          key=lambda x: x[1]["rocket"], reverse=True)[:10]
        nav = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Обновить", callback_data="rockets"),
            InlineKeyboardButton("🏠 Меню",     callback_data="show_menu"),
        ]])
        lines = ["🚀🔥 *РАКЕТЫ — Rocket Score*", f"🕐 {now_utc3()}", ""]
        for i, (c, a) in enumerate(rockets, 1):
            r = a["rocket"]; bar = "█"*int(r/10)+"░"*(10-int(r/10))
            side = "🟢L" if a["is_long"] else "🔴S"
            lines.append(f"{i}. *{c['symbol']}* `{r}/100` {side}  `{bar}`")
        await q.edit_message_text("\n".join(lines), parse_mode="Markdown",
                                  reply_markup=nav, disable_web_page_preview=True)

    elif data == "precision":
        await q.edit_message_text(
            "🎯 *Precision Shots*\n\n"
            "Нажми /7 в чате или выбери другой раздел:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Меню", callback_data="show_menu"),
            ]])
        )

    elif data == "game":
        text = f"🕐 {now_utc3()}\n\n" + build_game_digest()
        nav  = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Обновить",  callback_data="game"),
            InlineKeyboardButton("🏠 Меню",      callback_data="show_menu"),
        ]])
        try:
            await q.edit_message_text(text, parse_mode="Markdown",
                                      reply_markup=nav, disable_web_page_preview=False)
        except:
            await q.answer("Обновлено ✅")

    elif data == "report":
        await q.edit_message_text("⏳ Загружаю...", parse_mode="Markdown")
        coins = get_top500()
        if coins:
            up  = sorted(coins, key=lambda x: x["quote"]["USDT"].get("percent_change_24h", 0), reverse=True)
            txt = "\n".join([f"🚀 *Топ рост 24ч*", f"🕐 {now_utc3()}"] +
                            [f"{i}. *{c['symbol']}*  ${fp(c['quote']['USDT']['price'])}  {fc(c['quote']['USDT'].get('percent_change_24h',0))}"
                             for i, c in enumerate(up[:20], 1)])
            nav = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Обновить", callback_data="report"),
                InlineKeyboardButton("🏠 Меню",     callback_data="show_menu"),
            ]])
            await q.edit_message_text(txt, parse_mode="Markdown",
                                      reply_markup=nav, disable_web_page_preview=True)

    elif data == "watchlist":
        coins    = get_top500()
        coin_map = {c["symbol"]: c for c in coins}
        lines = ["👁 *ВОТЧЛИСТ — BEST TRADE*", f"🕐 {now_utc3()}", ""]
        for sym, info in WATCHLIST_ZONES.items():
            coin     = coin_map.get(sym)
            bias     = info.get("bias", "LONG")
            note     = info.get("note", "")
            src      = info.get("source", "")
            zone_key = "long" if bias == "LONG" else "short"
            zone     = info.get(zone_key, [None, None])
            if coin:
                price     = coin["quote"]["USDT"].get("price", 0)
                ch24h     = coin["quote"]["USDT"].get("percent_change_24h", 0)
                price_str = f"`{fp(price)}`  {fc(ch24h)}"
            else:
                price = 0; price_str = "`—`"
            emoji      = "🟢" if bias == "LONG" else "🔴"
            in_zone    = zone and zone[0] is not None and price > 0 and zone[0] <= price <= zone[1]
            in_zone_str = " ⚡️ В ЗОНЕ!" if in_zone else ""
            lines.append(f"{emoji} *{sym}*  {price_str}{in_zone_str}")
            if zone and zone[0] is not None:
                lines.append(f"   📊 Зона: `{fp(zone[0])} — {fp(zone[1])}`")
            lines.append(f"   💡 {note[:60]}...")
            lines.append(f"   📡 {src}")
            lines.append("")
        nav = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Обновить", callback_data="watchlist"),
            InlineKeyboardButton("🏠 Меню",     callback_data="show_menu"),
        ]])
        try:
            await q.edit_message_text("\n".join(lines), parse_mode="Markdown",
                                      reply_markup=nav, disable_web_page_preview=True)
        except Exception as e:
            log.error(f"watchlist cb: {e}")

    elif data == "menu_coin":
        await q.edit_message_text(
            "📈 *Анализ монеты*\n\n"
            "Напиши в чат:\n"
            "`/2 BTC` — Bitcoin\n"
            "`/2 ETH` — Ethereum\n"
            "`/2 SOL` — Solana\n"
            "`/2 [любой символ]`\n\n"
            "Символ из топ-500 CMC",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Меню", callback_data="show_menu"),
            ]])
        )

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
        await ctx.bot.send_message(cid,
                                   "📊 *BEST TRADE — Меню*\n\n👇 Выбери раздел:",
                                   parse_mode="Markdown", reply_markup=main_kb())

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
        nav = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Обновить", callback_data=f"period_{period}"),
            InlineKeyboardButton("🏠 Меню",     callback_data="show_menu"),
        ]])
        await q.edit_message_text(txt, parse_mode="Markdown",
                                  reply_markup=nav, disable_web_page_preview=True)


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
            add_to_game(sym, "supertrend", price)
            log.info(f"Supertrend {sym}: {prev_lbl}→{signal_lbl} @ {fp(price)}")
            await asyncio.sleep(0.5)

        except Exception as e:
            log.error(f"ST check {sym}: {e}")

watchlist_alerted = {}  # {symbol: timestamp}

# ═══════════════════════════════════════════
# PRECISION SHOTS — глубокий анализ x10 сетапов
# ═══════════════════════════════════════════

def precision_shot_analysis(coin: dict, a: dict) -> dict:
    """
    Ищет монеты с потенциалом x5-x10.
    Три типа сетапа:
    1. RECOVERY  — упала -70%+ от ATH, начинает восстановление
    2. BREAKOUT  — пробой после долгого боковика с объёмом
    3. ACCUMULATION — Smart Money тихо накапливают
    Возвращает score 0-100 и тип сетапа.
    """
    ch1h  = a["ch1h"]
    ch24h = a["ch24h"]
    ch7d  = a["ch7d"]
    ch30d = a["ch30d"]
    ch90d = a["ch90d"]
    vol_ratio = a["vol_ratio"]
    rsi_4h    = a["rsi_4h"]
    rank      = a["rank"]
    vol       = a["vol"]
    mcap      = a["mcap"]
    price     = a["price"]
    suspicious = a.get("suspicious", False)

    # Базовые фильтры — если не проходит, сетап не рассматриваем
    if suspicious:            return {"type": None, "ps": 0, "factors": []}
    if vol < 2_000_000:       return {"type": None, "ps": 0, "factors": []}  # мин ликвидность $2M
    if rank > 400:            return {"type": None, "ps": 0, "factors": []}  # не полный шлак
    if price <= 0:            return {"type": None, "ps": 0, "factors": []}

    ps      = 0  # precision score
    factors = []
    setup   = None

    # ══════════════════════════════════
    # ТИП 1: RECOVERY (упала -70%+, разворот)
    # Логика: монета обвалилась, умные деньги накапливают на дне
    # Потенциал: x3-x10 при восстановлении к ATH
    # ══════════════════════════════════
    deep_dump  = ch90d < -60    # упала >60% за 3 месяца
    recovering = ch7d > 3       # но за неделю +3%
    accum_vol  = 5 <= vol_ratio <= 40  # объём есть но не аномальный

    if deep_dump and recovering and accum_vol:
        setup = "RECOVERY"
        ps += 30
        factors.append(f"🔄 Дно -{ abs(ch90d):.0f}% за 90д")

        # Дополнительные подтверждения
        if rsi_4h < 40:
            ps += 15; factors.append("🟢 RSI перепродан")
        if ch7d > 8:
            ps += 10; factors.append(f"📈 Сильное восстановление +{ch7d:.0f}% 7д")
        if ch24h > 3:
            ps += 8; factors.append(f"⚡️ Ускорение сегодня +{ch24h:.1f}%")
        if ch1h > 0 and ch24h > 0:
            ps += 5; factors.append("✅ Импульс по всем TF")
        if rank <= 100:
            ps += 10; factors.append(f"🏆 Топ-100 (rank #{rank})")
        elif rank <= 200:
            ps += 5
        if vol_ratio >= 15:
            ps += 8; factors.append("📊 Повышенный объём")
        if ch30d > -20:  # не продолжает падать на 30д
            ps += 7; factors.append("🛡 Стабилизация 30д")

        # Потенциал x роста (грубая оценка от текущего дна)
        potential_x = max(1.0, abs(ch90d) / 30)

    # ══════════════════════════════════
    # ТИП 2: BREAKOUT (пробой после боковика)
    # Логика: долгий боковик + резкий пробой с объёмом
    # Потенциал: x2-x5 за 2-4 недели
    # ══════════════════════════════════
    elif (abs(ch30d) < 15        # 30д боковик (цена почти не двигалась)
          and ch7d > 5            # но за неделю +5% (начало движения)
          and ch24h > 5           # сегодня +5%
          and vol_ratio >= 10):   # с объёмом

        setup = "BREAKOUT"
        ps += 25
        factors.append(f"💥 Пробой после боковика 30д")

        if ch24h > 15:
            ps += 15; factors.append(f"🚀 Сильный импульс +{ch24h:.0f}% 24ч")
        elif ch24h > 10:
            ps += 10; factors.append(f"📈 Импульс +{ch24h:.0f}% 24ч")
        if vol_ratio >= 20:
            ps += 12; factors.append(f"📊 Аномальный объём {vol_ratio:.0f}%")
        elif vol_ratio >= 15:
            ps += 7
        if rsi_4h < 65:  # не перекуплен при пробое
            ps += 8; factors.append("✅ RSI есть куда расти")
        if ch1h > 2:
            ps += 5; factors.append("⚡️ Ускорение прямо сейчас")
        if rank <= 50:
            ps += 10; factors.append(f"🏆 Голубая фишка #{rank}")
        elif rank <= 150:
            ps += 5

        potential_x = 2.0 + (ch24h / 20)

    # ══════════════════════════════════
    # ТИП 3: ACCUMULATION (тихое накопление)
    # Логика: цена стоит, но объём большой = умные деньги набирают
    # Потенциал: x3-x8 когда выстрелит
    # ══════════════════════════════════
    elif (abs(ch24h) < 3         # цена почти не движется
          and abs(ch7d) < 10     # неделю в боковике
          and vol_ratio >= 12    # но объём высокий
          and rsi_4h < 55):      # RSI нейтральный/низкий

        setup = "ACCUMULATION"
        ps += 20
        factors.append("💎 Тихое накопление (цена стоит, объём высокий)")

        if vol_ratio >= 20:
            ps += 15; factors.append(f"🔥 Объём {vol_ratio:.0f}% — Smart Money активны")
        elif vol_ratio >= 15:
            ps += 8
        if rsi_4h < 35:
            ps += 12; factors.append("🟢 RSI перепродан — идеальная зона")
        elif rsi_4h < 45:
            ps += 6
        if ch90d < -40:  # до этого сильно упала
            ps += 10; factors.append(f"📉 До этого упала -{abs(ch90d):.0f}% (дно?)")
        if rank <= 100:
            ps += 10; factors.append(f"🏆 Топ-100 (#{rank}) — надёжность")
        elif rank <= 200:
            ps += 5
        if abs(ch30d) < 5:  # идеальная консолидация
            ps += 8; factors.append("📊 Плотная консолидация 30д")

        potential_x = 3.0 + (abs(ch90d) / 25)

    else:
        return {"type": None, "ps": 0, "factors": []}

    ps = min(100, ps)

    # Итоговая оценка потенциала
    if ps >= 75:   quality = "🔥🔥 МАКСИМАЛЬНЫЙ ПОТЕНЦИАЛ"
    elif ps >= 60: quality = "🔥 ВЫСОКИЙ ПОТЕНЦИАЛ"
    elif ps >= 45: quality = "✅ ХОРОШИЙ СЕТАП"
    else:          quality = "📊 СЛЕДИМ"

    return {
        "type":       setup,
        "ps":         ps,
        "factors":    factors,
        "quality":    quality,
        "potential_x": round(potential_x, 1),
    }


async def cmd_precision(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /7 — PRECISION SHOTS
    1-3 монеты с максимальным потенциалом x5-x10
    Только когда сходятся 5+ факторов
    """
    msg = await update.message.reply_text(
        "🎯 *PRECISION SHOTS*\nГлубокий анализ топ-500...\n~45 сек",
        parse_mode="Markdown"
    )
    coins = get_top500()
    if not coins:
        await msg.edit_text("❌ Нет данных"); return

    results = []
    for coin in coins:
        a  = full_analysis(coin)
        ps = precision_shot_analysis(coin, a)
        if ps["type"] and ps["ps"] >= 45:
            results.append((coin, a, ps))

    # Сортируем по Precision Score
    results.sort(key=lambda x: x[2]["ps"], reverse=True)
    top = results[:5]  # показываем топ-5

    if not top:
        await msg.edit_text(
            "🎯 *PRECISION SHOTS*\n\n"
            "❌ Сейчас нет монет с достаточным набором факторов.\n"
            "Рынок не даёт чёткого сетапа — лучше подождать.\n\n"
            "_Повтори через 30 минут_",
            parse_mode="Markdown"
        )
        return

    nav = InlineKeyboardMarkup([[
        InlineKeyboardButton("🌍 /1 Обзор",   callback_data="market_overview"),
        InlineKeyboardButton("🚀 /5 Ракеты",  callback_data="rockets"),
        InlineKeyboardButton("🔄 Обновить",   callback_data="precision"),
    ]])

    # Заголовок
    header = [
        "🎯 *PRECISION SHOTS — BEST TRADE*",
        f"🕐 {now_utc3()}",
        f"Найдено сетапов: {len(results)} из 500 монет",
        "",
        "─── *Отфильтровано: 5+ факторов* ───",
        "",
    ]
    await msg.edit_text("\n".join(header), parse_mode="Markdown")

    # Отправляем каждый сетап отдельным постом с графиком
    for coin, a, ps_data in top:
        sym   = coin["symbol"]
        slug  = coin.get("slug", sym.lower())
        setup = ps_data["type"]
        score = ps_data["ps"]
        qual  = ps_data["quality"]
        px    = ps_data["potential_x"]
        facts = ps_data["factors"]

        # Иконка типа
        type_icon = {"RECOVERY": "🔄", "BREAKOUT": "💥", "ACCUMULATION": "💎"}.get(setup, "📊")
        type_name = {"RECOVERY": "RECOVERY", "BREAKOUT": "BREAKOUT", "ACCUMULATION": "НАКОПЛЕНИЕ"}.get(setup, setup)

        # Supertrend
        try:
            st_data = get_supertrend_signal(sym)
            a["st_label"] = st_data["label"]
        except:
            a["st_label"] = "—"

        stats  = get_binance_24h(sym)
        extras = get_market_extras(sym)

        # Строим пост
        is_long = a["is_long"]
        side_e  = "🟢" if is_long else "🔴"
        side_t  = "LONG" if is_long else "SHORT"

        def pct(t, p=a["price"]):
            d = (t - p) / p * 100
            v = d if is_long else -d
            return f"+{v:.2f}%" if v >= 0 else f"{v:.2f}%"

        vol_str = (f"${a['vol']/1e9:.2f}B" if a['vol'] >= 1e9 else
                   f"${a['vol']/1e6:.1f}M" if a['vol'] >= 1e6 else f"${a['vol']/1e3:.0f}K")

        filled = int(score / 10)
        bar = "█" * filled + "░" * (10 - filled)

        lines = [
            f"🎯 *{sym}USDT*  {side_e} *{side_t}*",
            f"🕐 {now_utc3()}",
            "",
            f"{type_icon} *{type_name}*  |  Precision: `{score}/100`",
            f"`{bar}`",
            f"{qual}",
            f"📈 Потенциал: *~x{px}*",
            "",
            "📋 *Почему этот сетап:*",
        ]
        for f_ in facts:
            lines.append(f"  {f_}")

        lines += [
            "",
            f"💰 Вход:  `{fp(a['price'])}`",
            f"🎯 TP1:  `{fp(a['tp1'])}`  ({pct(a['tp1'])})",
            f"🎯 TP2:  `{fp(a['tp2'])}`  ({pct(a['tp2'])})",
            f"🎯 TP3:  `{fp(a['tp3'])}`  ({pct(a['tp3'])})",
            f"🛑 SL:   `{fp(a['sl'])}`",
            "",
            "━━━━━━━━━━━━━━━━━━",
            f"📐 R:R `1:{a['rr']:.1f}`  |  💹 {vol_str}  |  Rank `#{a['rank']}`",
            f"📈 RSI 4H `{a['rsi_4h']:.0f}`  |  ST: `{a['st_label']}`",
            f"📊 1H`{fc(a['ch1h'])}`  24H`{fc(a['ch24h'])}`  7D`{fc(a['ch7d'])}`  90D`{fc(a['ch90d'])}`",
        ]

        if extras:
            fr = extras.get("funding", {})
            oi = extras.get("oi", {})
            if fr.get("ok"):
                lines.append(f"💸 Фандинг: `{fr['rate']:+.4f}%`  {fr['signal']}")
            if oi.get("ok") and oi.get("change", 0) != 0:
                lines.append(f"📊 OI: `{oi['change']:+.1f}%`  {oi['signal']}")

        if stats:
            h24 = stats.get("high", 0); l24 = stats.get("low", 0)
            if h24 and l24:
                best = l24 * 1.005 if is_long else h24 * 0.995
                lines.append(f"📅 24H: 🔼`{fp(h24)}` 🔽`{fp(l24)}`  🎯Вход: `{fp(best)}`")

        lines += ["", f"⚠️ Риск: *2% депозита* | SL обязателен", f"#{sym}USDT"]

        text = "\n".join(lines)
        await send_coin(ctx.bot, update.effective_chat.id, sym, slug, a, text)
        await asyncio.sleep(2.0)

    # Итог
    await ctx.bot.send_message(
        update.effective_chat.id,
        f"🎯 *Precision анализ завершён*\n"
        f"Показано: {len(top)} лучших сетапов\n\n"
        f"💡 *Как использовать:*\n"
        f"• RECOVERY — DCA вход, держать 2-8 недель\n"
        f"• BREAKOUT — вход сейчас, стоп за боковик\n"
        f"• НАКОПЛЕНИЕ — ждать пробоя, тогда входить\n\n"
        f"⚠️ Это анализ, не гарантия. Всегда SL!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Обновить", callback_data="precision"),
            InlineKeyboardButton("🏠 Меню",     callback_data="show_menu"),
        ]])
    )
    await ctx.bot.send_message(update.effective_chat.id,
                               "📊 *BEST TRADE — Меню*\n\n👇 Выбери раздел:",
                               parse_mode="Markdown", reply_markup=main_kb())



async def check_watchlist(bot, chat_ids, coins):
    """Проверяет попадание цены в зоны вотчлиста каждые 5 мин"""
    now_ts   = datetime.now(TZ).timestamp()
    alerts   = check_watchlist_alerts(coins)
    for al in alerts:
        sym = al["symbol"]
        last = watchlist_alerted.get(sym, 0)
        if now_ts - last < 1800:  # не чаще раза в 30 мин
            continue
        watchlist_alerted[sym] = now_ts
        text = (
            f"📍 *ВОТЧЛИСТ — ЗОНА ВХОДА!*\n"
            f"🕐 {now_utc3()}\n\n"
            f"{al['emoji']} *{sym}USDT  {al['bias']}*\n"
            f"💰 Цена: `{fp(al['price'])}`\n"
            f"📊 Зона: `{fp(al['lo'])} — {fp(al['hi'])}`\n\n"
            f"💡 {al['note']}\n"
            f"📡 Источник: {al['source']}\n\n"
            f"⚠️ Риск: 2% депозита | SL обязателен\n\n"
            f"#{sym}USDT"
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("📈 TradingView", url=tv_link(sym)),
        ]])
        for cid in chat_ids:
            try:
                await bot.send_message(cid, text, parse_mode="Markdown", reply_markup=kb)
            except Exception as e:
                log.error(f"Watchlist alert {cid}: {e}")
        add_to_game(sym, "watchlist", al["price"])
        log.info(f"Watchlist ALERT: {sym} @ {fp(al['price'])}")

async def check_alerts(bot: Bot):
    """Каждые 5 мин: pump/dump + zone + supertrend + watchlist alerts"""
    chat_ids = load_chat_ids() | user_chat_ids
    if not chat_ids: return
    try:
        coins = get_top500()
        if not coins: return
        await check_pump_dump(bot, chat_ids, coins)
        await check_entry_zones(bot, chat_ids, coins)
        await check_watchlist(bot, chat_ids, coins)
        await check_supertrend_signals(bot, chat_ids, coins)
    except Exception as e:
        log.error(f"check_alerts: {e}")

async def cmd_watchlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Показывает вотчлист с зонами и текущими ценами"""
    msg   = await update.message.reply_text("⏳ Загружаю вотчлист...")
    coins = get_top500()
    coin_map = {c["symbol"]: c for c in coins}

    lines = [
        "👁 *ВОТЧЛИСТ — BEST TRADE*",
        f"🕐 {now_utc3()}",
        "",
    ]

    for sym, info in WATCHLIST_ZONES.items():
        coin  = coin_map.get(sym)
        bias  = info.get("bias", "LONG")
        note  = info.get("note", "")
        src   = info.get("source", "")
        zone_key = "long" if bias == "LONG" else "short"
        zone  = info.get(zone_key, [None, None])

        if coin:
            price = coin["quote"]["USDT"].get("price", 0)
            ch24h = coin["quote"]["USDT"].get("percent_change_24h", 0)
            price_str = f"`{fp(price)}`  {fc(ch24h)}"
        else:
            price = 0
            price_str = "`—`"

        emoji = "🟢" if bias == "LONG" else "🔴"

        # Проверяем попадание в зону
        in_zone = False
        if zone and zone[0] is not None and price > 0:
            in_zone = zone[0] <= price <= zone[1]
        in_zone_str = " ⚡️ В ЗОНЕ!" if in_zone else ""

        lines.append(f"{emoji} *{sym}*  {price_str}{in_zone_str}")
        if zone and zone[0] is not None:
            lines.append(f"   📊 Зона: `{fp(zone[0])} — {fp(zone[1])}`")
        lines.append(f"   💡 {note[:60]}...")
        lines.append(f"   📡 {src}")
        lines.append("")

    nav = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Обновить", callback_data="watchlist"),
        InlineKeyboardButton("🏠 Меню",     callback_data="show_menu"),
    ]])
    await msg.edit_text("\n".join(lines), parse_mode="Markdown",
                        reply_markup=nav, disable_web_page_preview=True)
    await update.message.reply_text("📊 *BEST TRADE — Меню*\n\n👇 Выбери раздел:",
                                    parse_mode="Markdown", reply_markup=main_kb())

async def cmd_game(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /8 — Монеты в игре
    Дайджест всех активных алертов за 48ч + отработавшие
    """
    nav = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Обновить", callback_data="game"),
        InlineKeyboardButton("🏠 Меню",     callback_data="show_menu"),
    ]])
    text = f"🕐 {now_utc3()}\n\n" + build_game_digest()
    await update.message.reply_text(
        text, parse_mode="Markdown",
        reply_markup=nav,
        disable_web_page_preview=False
    )
    await update.message.reply_text("📊 *BEST TRADE — Меню*\n\n👇 Выбери раздел:",
                                    parse_mode="Markdown", reply_markup=main_kb())

# ═══════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("menu",      cmd_menu))
    app.add_handler(CommandHandler("1",         cmd_market))
    app.add_handler(CommandHandler("2",         cmd_coin))
    app.add_handler(CommandHandler("3",         cmd_signals))
    app.add_handler(CommandHandler("4",         cmd_top))
    app.add_handler(CommandHandler("5",         cmd_rockets))
    app.add_handler(CommandHandler("6",         cmd_watchlist))
    app.add_handler(CommandHandler("7",         cmd_precision))
    app.add_handler(CommandHandler("8",         cmd_game))
    app.add_handler(CommandHandler("game",      cmd_game))
    app.add_handler(CommandHandler("market",    cmd_market))
    app.add_handler(CommandHandler("coin",      cmd_coin))
    app.add_handler(CommandHandler("signals",   cmd_signals))
    app.add_handler(CommandHandler("top",       cmd_top))
    app.add_handler(CommandHandler("rockets",   cmd_rockets))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("precision", cmd_precision))
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
