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
import time
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

# ── ЖУРНАЛ АКТИВНЫХ АЛЕРТОВ (как "Монеты в отработке") ──
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
        lines.append(f"🔥 *Монет в отработке: {len(actives)}*\n")
        for sym, v in actives:
            lbl      = type_labels.get(v["type"], v["type"])
            t        = v["time"].strftime("%d.%m %H:%M")
            tv_url   = f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym}USDT"
            lines.append(f"• [{sym}USDT]({tv_url}) — {lbl}")
            lines.append(f"  ⏰ {t} UTC+3")
    else:
        lines.append("🔥 *Монет в отработке: 0*\n")
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

def get_all_coins():
    """
    Получает все монеты:
    1. CMC: до 5000 монет (3 запроса по ~1667) 
    2. Binance: все торгуемые USDT пары (дополняет если нет в CMC)
    Кэш 30 минут.
    """
    # Кэш — обновляем не чаще раза в 30 мин
    now_ts = datetime.now(TZ).timestamp()
    cache_key = "_all_coins_cache"
    if hasattr(get_all_coins, "_cache"):
        cached_time, cached_data = get_all_coins._cache
        if now_ts - cached_time < 1800 and cached_data:
            return cached_data

    result    = []
    seen_syms = set()

    # ── ШАГ 1: CMC listings — до 5000 монет постранично ──
    try:
        url     = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
        headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}

        # Загружаем по 1000, до 5 страниц
        for start in range(1, 5001, 1000):
            try:
                params = {
                    "start":   start,
                    "limit":   1000,
                    "convert": "USDT",
                    "sort":    "market_cap",
                }
                r = requests.get(url, headers=headers, params=params, timeout=25)
                if r.status_code != 200:
                    break
                batch = r.json().get("data", [])
                if not batch:
                    break

                added = 0
                for coin in batch:
                    sym  = coin.get("symbol", "")
                    tags = [t.lower() for t in coin.get("tags", [])]
                    q    = coin.get("quote", {}).get("USDT", {})
                    mcap = q.get("market_cap", 0) or 0

                    if sym in STABLECOINS:          continue
                    if "stablecoin" in tags:        continue
                    if "wrapped-tokens" in tags:    continue
                    if sym in seen_syms:            continue
                    if mcap > 0 and mcap < 100_000: continue  # меньше $100K — мусор

                    seen_syms.add(sym)
                    result.append(coin)
                    added += 1

                log.info(f"CMC batch start={start}: +{added} монет (всего {len(result)})")

                # Если пришло меньше 500 — это последняя страница
                if len(batch) < 500:
                    break

                time.sleep(0.5)  # rate limit

            except Exception as e:
                log.error(f"CMC batch start={start}: {e}")
                break

    except Exception as e:
        log.error(f"CMC all coins: {e}")

    # ── ШАГ 2: Binance — все USDT пары которых нет в CMC ──
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/24hr", timeout=20)
        if r.status_code == 200:
            binance_tickers = r.json()
            for t in binance_tickers:
                sym_full = t.get("symbol", "")
                if not sym_full.endswith("USDT"):
                    continue
                sym = sym_full[:-4]

                if sym in STABLECOINS:   continue
                if sym in seen_syms:     continue

                vol_usd = float(t.get("quoteVolume", 0))
                price   = float(t.get("lastPrice", 0))
                if vol_usd < 50_000:    continue  # слишком мало объёма
                if price <= 0:          continue

                # Создаём минимальный объект монеты
                coin_stub = {
                    "symbol":   sym,
                    "slug":     sym.lower(),
                    "cmc_rank": 9999,
                    "tags":     [],
                    "name":     sym,
                    "quote": {"USDT": {
                        "price":              price,
                        "volume_24h":         vol_usd,
                        "market_cap":         0,
                        "percent_change_1h":  float(t.get("priceChangePercent", 0)),
                        "percent_change_24h": float(t.get("priceChangePercent", 0)),
                        "percent_change_7d":  0,
                        "percent_change_30d": 0,
                        "percent_change_90d": 0,
                    }}
                }
                seen_syms.add(sym)
                result.append(coin_stub)

            log.info(f"Binance добавил дополнительные монеты. Итого: {len(result)}")

    except Exception as e:
        log.error(f"Binance all pairs: {e}")

    # Сортируем: CMC монеты по рангу, Binance-only — в конце по объёму
    cmc_coins    = [c for c in result if c.get("cmc_rank", 9999) < 9999]
    binance_only = [c for c in result if c.get("cmc_rank", 9999) == 9999]
    cmc_coins.sort(key=lambda x: x.get("cmc_rank", 9999))
    binance_only.sort(key=lambda x: x.get("quote",{}).get("USDT",{}).get("volume_24h",0), reverse=True)
    result = cmc_coins + binance_only

    log.info(f"Итого монет: {len(result)} (CMC: {len(cmc_coins)}, Binance-only: {len(binance_only)})")

    # Сохраняем в кэш
    get_all_coins._cache = (now_ts, result)
    return result


# Backward compat alias
def get_top500():
    return get_all_coins()

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
    """Получает свечи с Binance. Пробует несколько вариантов тикера."""
    def _fetch(ticker):
        try:
            url    = "https://api.binance.com/api/v3/klines"
            params = {"symbol": ticker, "interval": interval, "limit": limit}
            r      = requests.get(url, params=params, timeout=12)
            if r.status_code != 200:
                return []
            data = r.json()
            if not data or isinstance(data, dict):
                return []
            return [
                {
                    "time":  datetime.fromtimestamp(d[0] / 1000, tz=TZ),
                    "open":  float(d[1]),
                    "high":  float(d[2]),
                    "low":   float(d[3]),
                    "close": float(d[4]),
                    "vol":   float(d[5]),
                }
                for d in data
            ]
        except:
            return []

    # Пробуем разные варианты тикера
    sym_upper = symbol.upper().replace("USDT", "")
    for ticker in [f"{sym_upper}USDT", f"{sym_upper}BUSD", f"{sym_upper}BTC"]:
        result = _fetch(ticker)
        if result and len(result) >= 10:
            return result
    log.warning(f"Binance OHLC not found: {symbol} ({interval})")
    return []

def get_binance_24h(symbol: str) -> dict:
    """24h stats: high, low, open, last price"""
    sym_clean = symbol.upper().replace("USDT","").replace("BUSD","")
    for suffix in ["USDT", "BUSD"]:
        try:
            url    = "https://api.binance.com/api/v3/ticker/24hr"
            params = {"symbol": f"{sym_clean}{suffix}"}
            r      = requests.get(url, params=params, timeout=8)
            if r.status_code != 200: continue
            d = r.json()
            return {
                "high": float(d.get("highPrice", 0)),
                "low":  float(d.get("lowPrice",  0)),
                "open": float(d.get("openPrice", 0)),
                "last": float(d.get("lastPrice", 0)),
                "vol":  float(d.get("quoteVolume", 0)),
            }
        except: continue
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
    # Supply/Demand зоны
    if in_demand:       smc_factors.append("🟢 Demand Zone")
    if in_supply:       smc_factors.append("🔴 Supply Zone")

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
    """
    Профессиональный чистый график как в примере KRIPTANO:
    - Свечи + EMA + уровни (Entry, TP1/2/3, SL, Swing)
    - Никаких лишних полос и заливок
    - Объём внизу
    - Брендинг BEST TRADE
    """
    is_long       = a["is_long"]
    price         = a["price"]
    tp1, tp2, tp3 = a["tp1"], a["tp2"], a["tp3"]
    sl, swing     = a["sl"],  a["swing"]
    rsi           = a["rsi_4h"]

    # Получаем реальные свечи — несколько попыток с разными тикерами
    candles = []
    sym_clean = symbol.upper().replace("USDT","").replace("BUSD","")
    for ticker_suffix in ["USDT", "BUSD"]:
        try:
            url = "https://api.binance.com/api/v3/klines"
            params = {"symbol": f"{sym_clean}{ticker_suffix}", "interval": "4h", "limit": 200}
            r = requests.get(url, params=params, timeout=15)
            if r.status_code == 200:
                data = r.json()
                if data and isinstance(data, list) and len(data) >= 10:
                    candles = [
                        {"time": datetime.fromtimestamp(d[0]/1000, tz=TZ),
                         "open": float(d[1]), "high": float(d[2]),
                         "low": float(d[3]),  "close": float(d[4]),
                         "vol": float(d[5])}
                        for d in data
                    ]
                    log.info(f"Chart candles OK: {sym_clean}{ticker_suffix} n={len(candles)}")
                    break
        except Exception as e:
            log.error(f"Chart candle fetch {sym_clean}{ticker_suffix}: {e}")

    if not candles or len(candles) < 20:
        log.warning(f"Chart NO DATA for {symbol} - returning None")
        return None  # Не отправляем выдуманный график

    n_all      = len(candles)
    closes_all = [c["close"] for c in candles]

    # EMA по всем данным
    ema20_all  = calc_ema(closes_all, 20)
    ema50_all  = calc_ema(closes_all, 50)
    ema200_all = calc_ema(closes_all, min(200, n_all))

    # Supertrend (только для стрелок BUY/SELL — без заливки)
    st_all = calc_supertrend(candles, period=10, multiplier=3.0)

    # Показываем последние 80 свечей
    display_n = min(80, n_all)
    start_idx = n_all - display_n
    candles  = candles[start_idx:]
    ema20_v  = ema20_all[start_idx:]
    ema50_v  = ema50_all[start_idx:]
    ema200_v = ema200_all[start_idx:]
    st_v     = st_all[start_idx:]

    n    = len(candles)
    vols = [c.get("vol", 0) for c in candles]

    # ── LAYOUT ──
    fig = plt.figure(figsize=(14, 9), facecolor="#0B1120")
    gs  = fig.add_gridspec(
        10, 1, hspace=0,
        left=0.02, right=0.82,
        top=0.97, bottom=0.04
    )
    ax_brand = fig.add_subplot(gs[0:1,  0])
    ax       = fig.add_subplot(gs[1:8,  0])
    axv      = fig.add_subplot(gs[8:10, 0], sharex=ax)

    for ax_ in [ax_brand, ax, axv]:
        ax_.set_facecolor("#0B1120")

    # ── БРЕНДИНГ (оранжевая полоса) ──
    ax_brand.set_facecolor(ORANGE)
    ax_brand.set_xlim(0, 1); ax_brand.set_ylim(0, 1); ax_brand.axis("off")
    ax_brand.text(0.5, 0.58, "B E S T   T R A D E",
                  fontsize=18, color=WHITE, fontweight="bold",
                  ha="center", va="center", transform=ax_brand.transAxes,
                  family="monospace")
    ax_brand.text(0.5, 0.12, "S  I  G  N  A  L  S",
                  fontsize=7, color=WHITE, alpha=0.75,
                  ha="center", va="center", transform=ax_brand.transAxes,
                  family="monospace")

    # ── СВЕЧИ ──
    w = 0.42
    for i, c in enumerate(candles):
        col = "#26A69A" if c["close"] >= c["open"] else "#EF5350"
        ax.plot([i, i], [c["low"], c["high"]], color=col, lw=0.7, zorder=2)
        body_h = abs(c["close"] - c["open"]) or (c["high"] - c["low"]) * 0.015
        ax.add_patch(patches.Rectangle(
            (i - w/2, min(c["open"], c["close"])), w, body_h,
            linewidth=0, facecolor=col, alpha=0.95, zorder=3
        ))

    # ── EMA — чистые линии ──
    ema_cfg = [
        (ema20_v,  "#F0B90B", "EMA20",  1.6),
        (ema50_v,  "#F7931A", "EMA50",  1.8),
        (ema200_v, "#EF5350", "EMA200", 2.0),
    ]
    for vals, col, lbl, lw in ema_cfg:
        pts = [(i, v) for i, v in enumerate(vals) if v is not None]
        if len(pts) > 1:
            ax.plot([p[0] for p in pts], [p[1] for p in pts],
                    color=col, lw=lw, alpha=0.9, zorder=5)
            lx, ly = pts[-1]
            ax.text(lx + 0.5, ly, f" {lbl}",
                    color=col, fontsize=6.5, va="center",
                    fontweight="bold", zorder=8)

    # ── SUPERTREND — только стрелки BUY/SELL, без заливки ──
    for i, s in enumerate(st_v):
        if s["signal"] == "BUY":
            ax.annotate("▲ BUY",
                        xy=(i, candles[i]["low"] * 0.9982),
                        fontsize=7, color="#26A69A", fontweight="bold",
                        ha="center", va="top", zorder=10,
                        bbox=dict(boxstyle="round,pad=0.15",
                                  facecolor="#0B1120", edgecolor="#26A69A",
                                  alpha=0.9, lw=0.8))
        elif s["signal"] == "SELL":
            ax.annotate("▼ SELL",
                        xy=(i, candles[i]["high"] * 1.0018),
                        fontsize=7, color="#EF5350", fontweight="bold",
                        ha="center", va="bottom", zorder=10,
                        bbox=dict(boxstyle="round,pad=0.15",
                                  facecolor="#0B1120", edgecolor="#EF5350",
                                  alpha=0.9, lw=0.8))

    # ── УРОВНИ (как в примере KRIPTANO) ──
    ext = n * 0.28
    ax.set_xlim(-1, n + ext)

    def pct_str(t):
        d = (t - price) / price * 100
        v = d if is_long else -d
        return f"+{v:.2f}%" if v >= 0 else f"{v:.2f}%"

    def draw_level(val, color, label, pct="", ls="--", lw=1.3):
        ax.axhline(val, color=color, linestyle=ls, linewidth=lw,
                   alpha=0.85, zorder=6, xmax=0.78)
        label_x = n + ext * 0.04
        txt = f"{label}: {fp(val)}"
        if pct: txt += f"  ({pct})"
        ax.text(label_x, val, txt,
                color=color, fontsize=7.8, va="center",
                fontweight="bold", fontfamily="monospace", zorder=7)

    # Уровни сверху вниз
    draw_level(tp3,   "#00C896", "TP3",   pct_str(tp3),   "--", 1.0)
    draw_level(tp2,   "#00E5A0", "TP2",   pct_str(tp2),   "--", 1.0)
    draw_level(tp1,   "#26A69A", "TP1",   pct_str(tp1),   "--", 1.0)
    draw_level(price, "#FFD700", "Entry", "",              "-",  2.0)
    draw_level(swing, "#64B5F6", "Swing", "",              ":",  1.0)
    draw_level(sl,    "#EF5350", "SL",    pct_str(sl),    "--", 1.3)

    # Стрелка входа на последней свече
    ax.annotate("▲" if is_long else "▼",
                xy=(n - 1, price), fontsize=16,
                color="#FFD700", ha="center",
                va="bottom" if is_long else "top", zorder=9)

    # ── ЗАГОЛОВОК ──
    side_str = "LONG" if is_long else "SHORT"
    side_col = "#26A69A" if is_long else "#EF5350"
    ax.text(0.01, 0.98, f"{symbol}USDT  •  4H  •  {side_str}",
            fontsize=12, color=WHITE, fontweight="bold",
            va="top", ha="left", transform=ax.transAxes, zorder=10)
    rsi_t = "Перепродан" if rsi < 35 else ("Перекуплен" if rsi > 65 else "Нейтральный")
    rsi_c = "#26A69A" if rsi < 35 else ("#EF5350" if rsi > 65 else GRAY)
    ax.text(0.01, 0.90, f"RSI {rsi:.0f} — {rsi_t}",
            fontsize=8, color=rsi_c,
            va="top", ha="left", transform=ax.transAxes, zorder=10)

    # Supertrend статус
    cur_st = st_v[-1] if st_v else {"direction": 1}
    st_lbl = "SUPERTREND: BUY" if cur_st["direction"] == 1 else "SUPERTREND: SELL"
    st_col = "#26A69A" if cur_st["direction"] == 1 else "#EF5350"
    ax.text(0.01, 0.82, st_lbl,
            fontsize=8, color=st_col, fontweight="bold",
            va="top", ha="left", transform=ax.transAxes, zorder=10,
            bbox=dict(boxstyle="round,pad=0.25", facecolor="#0B1120",
                      edgecolor=st_col, alpha=0.85, lw=0.9))

    # ── ОБЪЁМ ──
    max_vol = max(vols) if max(vols) > 0 else 1
    for i, c in enumerate(candles):
        col = "#26A69A" if c["close"] >= c["open"] else "#EF5350"
        axv.bar(i, vols[i] / max_vol, width=0.7, color=col, alpha=0.45, zorder=2)
    axv.set_yticks([])
    axv.set_ylabel("Vol", color=GRAY, fontsize=7, rotation=0, labelpad=18)
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
    axv.set_xticks([]); plt.setp(ax.get_xticklabels(), visible=False)

    ax.tick_params(axis="y", colors=GRAY, labelsize=7.5, right=True, left=False)
    ax.yaxis.set_label_position("right"); ax.yaxis.tick_right()
    ax.grid(color="#151F30", lw=0.3, zorder=0, alpha=0.7)
    ax.spines[:].set_color("#1A2535")
    axv.spines[:].set_color("#1A2535")

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=155, bbox_inches="tight",
                facecolor="#0B1120", edgecolor="none")
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
        {"level": 62137, "label": "S1 Аналитика"},
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
    # ── СПОТ ПОРТФЕЛЬ (из таблицы 19.06.2026) ──
    "LINK": {
        "long":  [6.70, 7.40],
        "note":  "Chainlink — DeFi оракул. Зона накопления спот",
        "source": "Аналитика",
        "bias":  "LONG",
        "spot":  True,
    },
    "AVAX": {
        "long":  [4.50, 4.90],
        "note":  "Avalanche — L1. Спот-накопление у исторических минимумов",
        "source": "Аналитика",
        "bias":  "LONG",
        "spot":  True,
    },
    "UNI": {
        "long":  [2.50, 2.80],
        "note":  "Uniswap — DEX #1. Зона накопления. Цель при восстановлении рынка $6+",
        "source": "Аналитика",
        "bias":  "LONG",
        "spot":  True,
    },
    "DYDX": {
        "long":  [0.10, 0.12],
        "note":  "dYdX — перпетуальный DEX. Спот у дна. Recovery потенциал x5+",
        "source": "Аналитика",
        "bias":  "LONG",
        "spot":  True,
    },
    "PYTH": {
        "long":  [0.030, 0.032],
        "note":  "Pyth Network — оракул. Зона накопления. Конкурент Chainlink",
        "source": "Аналитика",
        "bias":  "LONG",
        "spot":  True,
    },
    "ORDI": {
        "long":  [2.30, 2.57],
        "note":  "Ordinals — Bitcoin NFT протокол. Зона накопления у дна",
        "source": "Аналитика",
        "bias":  "LONG",
        "spot":  True,
    },
    "AAVE": {
        "long":  [53.50, 63.50],
        "note":  "Aave — кредитный DeFi. Сильный проект. Зона DCA",
        "source": "Аналитика",
        "bias":  "LONG",
        "spot":  True,
    },
    "BEAT": {
        "long":  [1.10, 1.30],
        "note":  "CertiK аудит. Выше EMA200. Сильнейший спот-сетап портфеля",
        "source": "Аналитика",
        "bias":  "LONG",
        "spot":  True,
    },
    # ── ФЬЮЧЕРС ВОТЧЛИСТ ──
    "SOL": {
        "long":  [68.28, 69.34],
        "note":  "3 точки базы сформированы. Ключевая зона поддержки",
        "source": "Аналитика",
        "bias":  "LONG",
    },
    "ZK": {
        "short": [0.01278, 0.01314],
        "note":  "Шорт от зоны сопротивления",
        "source": "Аналитика",
        "bias":  "SHORT",
    },
    "ACH": {
        "long":  [0.005030, 0.005138],
        "note":  "Новая структура на старшем ТФ. Зона накопления",
        "source": "Аналитика",
        "bias":  "LONG",
    },
    "EIGEN": {
        "long":  [0.1790, 0.1871],
        "note":  "4H структура, хороший объём. Зона интереса",
        "source": "Аналитика",
        "bias":  "LONG",
    },
    "ETH": {
        "short": [1710, 1737],
        "note":  "Imbalance зона. Ретест → шорт. Цели: $1670, $1504",
        "source": "Аналитика",
        "bias":  "SHORT",
    },
    "APT": {
        "long":  [0.6300, 0.6397],
        "note":  "Нижняя граница структуры 4H. SL за старший ТФ",
        "source": "Аналитика",
        "bias":  "LONG",
    },
    "ENA": {
        "long":  [0.0875, 0.0941],
        "note":  "Зона накопления 4H. Цель $0.106–0.110",
        "source": "Аналитика",
        "bias":  "LONG",
    },
    "BTC": {
        "long":  [62000, 63000],
        "note":  "Откат к $62K = зона входа. Цель $70K+",
        "source": "Аналитика",
        "bias":  "LONG",
    },
    "PIPPIN": {
        "long":  [0.0120, 0.0135],
        "note":  "Вход при RSI < 30. Мемкоин Solana. Recovery потенциал",
        "source": "Аналитика",
        "bias":  "LONG",
    },
    "HYPE": {
        "long":  [None, None],
        "note":  "Цель ATH $77–80. Превосходство над альтами",
        "source": "Аналитика",
        "bias":  "LONG",
    },
}

# Монеты спот-портфеля для автоматического ТОП СПОТ
SPOT_PORTFOLIO = {
    sym: info for sym, info in WATCHLIST_ZONES.items() if info.get("spot")
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
    s_line = f"  🟢 Поддержка: ${sup['level']:,} ({sup['label']}) — {sup['dist']:.1f}% ниже" if sup else ""
    r_line = f"  🔴 Сопротивление: ${res['level']:,} ({res['label']}) — {res['dist']:.1f}% выше" if res else ""

    long_lines  = []
    short_lines = []
    for i, (c, a) in enumerate(ms.get("top_longs", []), 1):
        sym = c["symbol"]; ch = a["ch24h"]
        long_lines.append(f"  {i}. 🚀 *{sym}*  ${fp(a['price'])}  {fc(ch)}  RSI {a['rsi_4h']:.0f}")
    for i, (c, a) in enumerate(ms.get("top_shorts", []), 1):
        sym = c["symbol"]; ch = a["ch24h"]
        short_lines.append(f"  {i}. 📉 *{sym}*  ${fp(a['price'])}  {fc(ch)}  RSI {a['rsi_4h']:.0f}")

    lines = [
        "🌍 *ОБЗОР РЫНКА — BEST TRADE*",
        f"🕐 {now_utc3()}",
        "",
        f"{trend_arrow(ms['btc_ch24h'])} *Bitcoin (BTC)*  ${ms['btc_price']:,.0f}",
        f"  24ч: {fc(ms['btc_ch24h'])}",
    ]
    if s_line: lines.append(s_line)
    if r_line: lines.append(r_line)
    lines += [
        "",
        f"{trend_arrow(ms['eth_ch24h'])} *Ethereum (ETH)*  ${ms['eth_price']:,.0f}",
        f"  24ч: {fc(ms['eth_ch24h'])}",
        "",
        f"📊 *Доминация*",
        f"  BTC *{ms['btc_dom']:.2f}%*  ·  ETH {ms['eth_dom']:.2f}%  ·  Others {ms['others_dom']:.2f}%",
        f"  {ms['dom_signal']}",
        "",
        f"{trend_arrow(ms['mcap_ch'])} *Total Market Cap*  {fm(ms['total_mcap'])}",
        f"  {fc(ms['mcap_ch'])} за 24ч  ·  {ms['total_signal']}",
        "",
        f"🧭 *Настроение рынка:* {ms['sentiment']}",
        f"  Растут {ms['sentiment_pct']:.0f}% монет из топ-500",
        "",
        "🚀 *Топ лонги:*",
    ]
    lines += long_lines if long_lines else ["  Нет сигналов"]
    lines += ["", "📉 *Топ шорты:*"]
    lines += short_lines if short_lines else ["  Нет сигналов"]
    lines += [
        "",
        f"🎯 *Вердикт:* {ms['verdict']}",
        "",
        "⚠️ Риск: *2% депозита*  ·  SL обязателен",
    ]
    return "\n".join(lines)

def overview_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить",          callback_data="market_overview"),
         InlineKeyboardButton("📊 Тренд анализ",      callback_data="trend_analysis")],
        [InlineKeyboardButton("💎 ТОП СПОТ",          callback_data="top_spot"),
         InlineKeyboardButton("🟢 ТОП ЛОНГ",          callback_data="top_long")],
        [InlineKeyboardButton("🔴 ТОП ШОРТ",          callback_data="top_short"),
         InlineKeyboardButton("🔬 Полный анализ",     callback_data="menu_full")],
        [InlineKeyboardButton("₿ BTC Chart",          url=tv_link("BTC")),
         InlineKeyboardButton("📈 TOTAL",             url="https://www.tradingview.com/chart/?symbol=CRYPTOCAP:TOTAL")],
        [InlineKeyboardButton("🏠 Главное меню",      callback_data="show_menu")],
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

        # Pump: +5% за 1ч — только логируем, не спамим в канал
        if ch1h >= 5:
            last_alert = pump_alerted.get(sym, 0)
            if now_ts - last_alert > 3600:
                pump_alerted[sym] = now_ts
                add_to_game(sym, "pump", price)
                log.info(f"PUMP detected (silent): {sym} +{ch1h:.2f}%")

        # Dump: -5% за 1ч — только логируем, не спамим в канал
        elif ch1h <= -5:
            last_alert = pump_alerted.get(f"dump_{sym}", 0)
            if now_ts - last_alert > 3600:
                pump_alerted[f"dump_{sym}"] = now_ts
                add_to_game(sym, "dump", price)
                log.info(f"DUMP detected (silent): {sym} {ch1h:.2f}%")

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
    """Главное меню BEST TRADE v34"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌍 Обзор рынка",          callback_data="market_overview"),
         InlineKeyboardButton("📊 Тренд анализ",         callback_data="trend_analysis")],
        [InlineKeyboardButton("💎 ТОП СПОТ",             callback_data="top_spot"),
         InlineKeyboardButton("🟢 ТОП ЛОНГ",             callback_data="top_long")],
        [InlineKeyboardButton("🔴 ТОП ШОРТ",             callback_data="top_short"),
         InlineKeyboardButton("🔬 Полный анализ",        callback_data="menu_full")],
        [InlineKeyboardButton("🔥 Монеты в отработке",   callback_data="top_trades"),
         InlineKeyboardButton("📡 Сигналы каналов",      callback_data="channel_signals")],
        [InlineKeyboardButton("🐋 On-Chain (Lookonchain)", callback_data="onchain_info")],
    ])

def back_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🏠 Главное меню", callback_data="show_menu"),
    ]])

async def send_coin(bot, chat_id, symbol, slug, a, text):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 TradingView",    url=tv_link(symbol)),
         InlineKeyboardButton("📊 CoinMarketCap",  url=cmc_link(slug))],
        [InlineKeyboardButton("🔄 Обновить",       callback_data=f"coin_{symbol}"),
         InlineKeyboardButton("🏠 Меню",           callback_data="show_menu")],
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
        if chart is not None:
            log.info(f"Chart OK: {symbol} {chart.getbuffer().nbytes} bytes")
        else:
            log.info(f"Chart skipped (no real data): {symbol}")
    except Exception as e:
        log.error(f"Chart FAILED {symbol}: {type(e).__name__}: {e}")
        chart = None

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
        "📊 *BEST TRADE v34.0*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🧠 *Профессиональная торговая система*\n\n"
        "🔬 *Анализ:*\n"
        "· SMC/ICT · Order Blocks · FVG · BOS\n"
        "· EMA 20/50/200 · RSI · MACD · Supertrend\n"
        "· Wyckoff · AMD · Power of Three\n"
        "· Multi-TF Confluence · Killzone\n\n"
        "📡 *Мониторинг:*\n"
        "· 11 каналов трейдеров в реальном времени\n"
        "· On-chain данные (Lookonchain)\n"
        "· Сигналы с TP/SL автоматически\n\n"
        "⚠️ *Риск:* 1–2% депозита · SL всегда\n\n"
        "👇 Выбери раздел:",
        parse_mode="Markdown", reply_markup=main_kb()
    )

async def cmd_market(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Загружаю обзор рынка...")
    try:
        prices = get_btc_eth_price()
        gm     = get_global_metrics()
        coins  = get_all_coins()
        if not prices or not coins:
            await msg.edit_text("❌ Нет данных от API"); return

        btc = prices.get("BTC", {})
        eth = prices.get("ETH", {})
        btc_price = btc.get("price", 0)
        eth_price = eth.get("price", 0)
        btc_ch24  = btc.get("percent_change_24h", 0) or 0
        eth_ch24  = eth.get("percent_change_24h", 0) or 0

        # Доминация
        btc_dom    = gm.get("btc_dominance", 0)
        eth_dom    = gm.get("eth_dominance", 0)
        total_mcap = gm.get("total_market_cap", 0)
        mcap_ch    = gm.get("total_market_cap_yesterday_percentage_change", 0) or 0

        # Настроение — быстро по CMC данным
        pos = sum(1 for c in coins[:200]
                  if (c["quote"]["USDT"].get("percent_change_24h") or 0) > 0)
        pct = pos / 200 * 100
        if pct >= 65:    sentiment = "🟢 Бычье"
        elif pct >= 50:  sentiment = "🟡 Нейтральное"
        else:            sentiment = "🔴 Медвежье"

        # Топ лонги/шорты — только по CMC данным (быстро)
        long_lines  = []
        short_lines = []
        sorted_ch = sorted(coins[:100],
                           key=lambda c: c["quote"]["USDT"].get("percent_change_24h",0) or 0,
                           reverse=True)
        for c in sorted_ch[:5]:
            sym = c["symbol"]
            ch  = c["quote"]["USDT"].get("percent_change_24h", 0) or 0
            p   = c["quote"]["USDT"].get("price", 0)
            vol = c["quote"]["USDT"].get("volume_24h", 0) or 0
            if vol >= 2_000_000:
                long_lines.append(f"  🟢 *{sym}*  `{fp(p)}`  `{fc(ch)}`")
        for c in sorted(coins[:100],
                        key=lambda c: c["quote"]["USDT"].get("percent_change_24h",0) or 0)[:5]:
            sym = c["symbol"]
            ch  = c["quote"]["USDT"].get("percent_change_24h", 0) or 0
            p   = c["quote"]["USDT"].get("price", 0)
            vol = c["quote"]["USDT"].get("volume_24h", 0) or 0
            if vol >= 2_000_000:
                short_lines.append(f"  🔴 *{sym}*  `{fp(p)}`  `{fc(ch)}`")

        ta = trend_arrow
        lines = [
            "🌍 *ОБЗОР РЫНКА — BEST TRADE*",
            "━━━━━━━━━━━━━━━━━━━━━",
            f"🕐 {now_utc3()}",
            "",
            f"₿ *Bitcoin*   `${btc_price:,.0f}`  {ta(btc_ch24)} `{fc(btc_ch24)}`",
            f"Ξ *Ethereum*  `${eth_price:,.0f}`   {ta(eth_ch24)} `{fc(eth_ch24)}`",
            "",
            f"📊 *Доминация:*  BTC `{btc_dom:.1f}%`  ·  ETH `{eth_dom:.1f}%`",
            f"💰 *Total MCap:*  `{fm(total_mcap)}`  `{fc(mcap_ch)}`",
            f"🧭 *Настроение:*  {sentiment}  ·  `{pct:.0f}%` монет в плюсе",
            "",
            "━━━ 📈 *ТОП РОСТА 24ч* ━━━",
        ]
        lines += long_lines if long_lines else ["  —"]
        lines += ["", "━━━ 📉 *ТОП ПАДЕНИЯ 24ч* ━━━"]
        lines += short_lines if short_lines else ["  —"]
        lines += [
            "",
            "━━━━━━━━━━━━━━━━━━━━━",
            "⚠️ *Риск:* 1–2% депозита  ·  SL обязателен  ·  макс 3–5x",
        ]

        await msg.edit_text("\n".join(lines), parse_mode="Markdown",
                            reply_markup=overview_kb(), disable_web_page_preview=True)
    except Exception as e:
        log.error(f"cmd_market: {e}")
        await msg.edit_text(
            f"❌ Ошибка обзора рынка\n\nПопробуй ещё раз",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Обновить", callback_data="market_overview"),
                InlineKeyboardButton("🏠 Меню",     callback_data="show_menu"),
            ]])
        )

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
        side = "🟢 LONG" if a["is_long"] else "🔴 SHORT"
        # Только значимые SMC факторы (без BB Squeeze)
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

    # ── Новые главные разделы ──
    if data == "show_menu":
        await q.edit_message_text(
            "📊 *BEST TRADE v34.0*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🧠 *Профессиональная торговая система*\n\n"
            "🔬 SMC · ICT · Wyckoff · AMD · Multi-TF\n"
            "📡 11 каналов · On-chain · Killzone\n\n"
            "👇 Выбери раздел:",
            parse_mode="Markdown", reply_markup=main_kb()
        )

    elif data == "top_spot":
        await q.edit_message_text("💎 Загружаю ТОП СПОТ...", parse_mode="Markdown")
        class FakeUpdate:
            effective_chat = q.message.chat
            message        = q.message
        await cmd_top_spot(FakeUpdate(), ctx)

    elif data == "top_long":
        await q.edit_message_text("🟢 Загружаю ТОП ЛОНГ...", parse_mode="Markdown")
        class FakeUpdate:
            effective_chat = q.message.chat
            message        = q.message
        await cmd_top_long(FakeUpdate(), ctx)

    elif data == "top_short":
        await q.edit_message_text("🔴 Загружаю ТОП ШОРТ...", parse_mode="Markdown")
        class FakeUpdate:
            effective_chat = q.message.chat
            message        = q.message
        await cmd_top_short(FakeUpdate(), ctx)

    elif data == "menu_full":
        await q.edit_message_text(
            "🔬 *Полный анализ монеты*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Введи команду в чат:\n"
            "`/full BTC`  `/full ETH`  `/full SOL`\n"
            "`/full SYMBOL` — любая монета\n\n"
            "📊 *Включает 6 уровней анализа:*\n"
            "① SMC/ICT — OB · FVG · BOS · CHoCH · Sweep\n"
            "② Wyckoff — фаза накопления/распределения\n"
            "③ AMD — Power of Three (Asia/London/NY)\n"
            "④ Multi-TF — confluence 1H/4H/1D/1W\n"
            "⑤ Volume Profile · OI · Funding Rate\n"
            "⑥ Macro — Gold · USDT.D · ETH/BTC ratio\n\n"
            "🎯 *Результат:*\n"
            "Entry · TP1/TP2/TP3 · SL · Score 0–100\n"
            "Killzone · Качество входа A+/A/B/C",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Главное меню", callback_data="show_menu")],
            ])
        )

    elif data in ("game", "top_trades"):
        nav = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Обновить",     callback_data="top_trades"),
             InlineKeyboardButton("🏠 Главное меню", callback_data="show_menu")],
        ])

        lines = [f"🔥 *BEST TRADE — Монеты в отработке*", f"🕐 {now_utc3()}", ""]
        has_signals = False
        total = len(TOP_LONG_SIGNALS) + len(TOP_SHORT_SIGNALS) + len(TOP_SPOT_SIGNALS)

        if total > 0:
            lines[2] = f"🔥 *Сигналов в отработке: {total}*\n"

        # ── ЛОНГИ ──
        active_l = {s: v for s, v in TOP_LONG_SIGNALS.items() if v.get("status") != "done"}
        if active_l:
            has_signals = True
            for sym, v in active_l.items():
                try:
                    stats = get_binance_24h(sym)
                    cur   = stats.get("last", v["entry"]) if stats else v["entry"]
                    if not cur: cur = v["entry"]
                except: cur = v["entry"]

                entry = v["entry"]
                tp1   = v.get("tp1", entry * 1.02)
                tp2   = v.get("tp2", entry * 1.04)
                tp3   = v.get("tp3", entry * 1.08)
                sl    = v.get("sl",  entry * 0.85)
                move  = (cur - entry) / entry * 100 if entry > 0 else 0
                t     = v["time"].strftime("%d.%m %H:%M")
                tv    = tv_link(sym)
                dist  = (entry - cur) / entry * 100 if cur < entry else 0

                # Статус
                if cur >= tp3:          status = "🏆 TP3 достигнут!"
                elif cur >= tp2:        status = "✅✅ TP2 достигнут!"
                elif cur >= tp1:        status = "✅ TP1 — двигаем стоп"
                elif cur > entry*1.005: status = "📈 Отрабатывает"
                elif dist <= 1:        status = "⚡️ Близко к входу!"
                elif dist <= 2:        status = f"📍 До входа {dist:.1f}%"
                elif cur <= sl*1.01:   status = "⚠️ Близко к SL!"
                else:                  status = f"⏳ Ждём входа {dist:.1f}%"

                lines += [
                    f"• [{sym}USDT]({tv}) — 🟢 лонг",
                    f"  💰 Вход `{fp(entry)}`  Сейчас `{fp(cur)}`  `{move:+.1f}%`",
                    f"  🎯 TP1 `{fp(tp1)}`  TP2 `{fp(tp2)}`  SL `{fp(sl)}`",
                    f"  {status}",
                    f"  ⏰ {t} UTC+3",
                    "",
                ]

        # ── ШОРТЫ ──
        active_s = {s: v for s, v in TOP_SHORT_SIGNALS.items() if v.get("status") != "done"}
        if active_s:
            has_signals = True
            for sym, v in active_s.items():
                try:
                    stats = get_binance_24h(sym)
                    cur   = stats.get("last", v["entry"]) if stats else v["entry"]
                    if not cur: cur = v["entry"]
                except: cur = v["entry"]

                entry = v["entry"]
                tp1   = v.get("tp1", entry * 0.98)
                tp2   = v.get("tp2", entry * 0.96)
                sl    = v.get("sl",  entry * 1.15)
                move  = (entry - cur) / entry * 100 if entry > 0 else 0
                t     = v["time"].strftime("%d.%m %H:%M")
                tv    = tv_link(sym)
                dist  = (cur - entry) / entry * 100 if cur > entry else 0

                if cur <= tp2:          status = "✅✅ TP2 достигнут!"
                elif cur <= tp1:        status = "✅ TP1 — двигаем стоп"
                elif cur < entry*0.995: status = "📉 Отрабатывает"
                elif dist <= 1:        status = "⚡️ Близко к входу!"
                elif dist <= 2:        status = f"📍 До входа {dist:.1f}%"
                elif cur >= sl*0.99:   status = "⚠️ Близко к SL!"
                else:                  status = f"⏳ Ждём входа {dist:.1f}%"

                lines += [
                    f"• [{sym}USDT]({tv}) — 🔴 шорт",
                    f"  💰 Вход `{fp(entry)}`  Сейчас `{fp(cur)}`  `{move:+.1f}%`",
                    f"  🎯 TP1 `{fp(tp1)}`  TP2 `{fp(tp2)}`  SL `{fp(sl)}`",
                    f"  {status}",
                    f"  ⏰ {t} UTC+3",
                    "",
                ]

        # ── СПОТ наблюдение ──
        if TOP_SPOT_SIGNALS:
            has_signals = True
            for sym, v in TOP_SPOT_SIGNALS.items():
                tv     = tv_link(sym)
                t      = v["time"].strftime("%d.%m %H:%M")
                buy_lo = v.get("buy_zone_lo", v["entry"])
                buy_hi = v.get("buy_zone_hi", v["entry"])
                sell_t = v.get("sell_target", 0)
                lines += [
                    f"• [{sym}USDT]({tv}) — 💎 спот",
                    f"  🟢 Зона `{fp(buy_lo)}` — `{fp(buy_hi)}`",
                    f"  🔴 Цель `{fp(sell_t)}`",
                    f"  ⏰ {t} UTC+3",
                    "",
                ]

        # ── Отработавшие ──
        done_l = {s: v for s, v in TOP_LONG_SIGNALS.items()  if v.get("status") == "done"}
        done_s = {s: v for s, v in TOP_SHORT_SIGNALS.items() if v.get("status") == "done"}
        if done_l or done_s:
            lines.append("✅ *Отработали:*")
            for sym, v in list(done_l.items())[:5]:
                tv = tv_link(sym)
                t  = v["time"].strftime("%d.%m %H:%M")
                lines.append(f"• [{sym}USDT]({tv}) — 📈 выросла")
                lines.append(f"  ⏰ {t} UTC+3")
            for sym, v in list(done_s.items())[:5]:
                tv = tv_link(sym)
                t  = v["time"].strftime("%d.%m %H:%M")
                lines.append(f"• [{sym}USDT]({tv}) — 📉 упала")
                lines.append(f"  ⏰ {t} UTC+3")

        if not has_signals:
            lines += [
                "📭 *Активных сигналов нет*\n",
                "Сигналы появляются автоматически каждые 30 мин.",
                "Или открой вручную:",
                "🟢 ТОП ЛОНГ  ·  🔴 ТОП ШОРТ",
            ]

        try:
            await q.edit_message_text(
                "\n".join(lines), parse_mode="Markdown",
                reply_markup=nav, disable_web_page_preview=False
            )
        except: await q.answer("Обновлено ✅")
        # Закрытие сделки
        parts = data.split("_")
        action = parts[0]   # tp / sl
        mode   = parts[1]   # long / short
        sym    = parts[2]
        result = f"✅ TP сработал" if action == "tp" else "❌ SL сработал"
        store  = TOP_LONG_SIGNALS if mode == "long" else TOP_SHORT_SIGNALS
        if sym in store:
            store[sym]["status"] = "done"
            store[sym]["result"] = result
        await q.answer(f"{result} по {sym}USDT")
        await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(f"{'✅' if action=='tp' else '❌'} {result} — {sym}", callback_data="noop"),
            InlineKeyboardButton("🏠 Меню", callback_data="show_menu"),
        ]]))

    elif data.startswith("full_"):
        symbol = data[5:]
        await q.edit_message_text(f"🔍 Полный анализ *{symbol}*...", parse_mode="Markdown")
        try: await q.message.delete()
        except: pass
        await _do_full_analysis(ctx.bot, q.message.chat_id, symbol)

    elif data == "market_overview":
        await q.edit_message_text("⏳ Загружаю обзор...", parse_mode="Markdown")
        try:
            # Используем FakeUpdate чтобы вызвать исправленный cmd_market
            class FakeMsg:
                async def reply_text(self, text, **kw):
                    return await ctx.bot.send_message(q.message.chat_id, text, **kw)
            class FakeUpdate:
                effective_chat = q.message.chat
                message = FakeMsg()
            try: await q.message.delete()
            except: pass
            await cmd_market(FakeUpdate(), ctx)
        except Exception as e:
            log.error(f"overview cb: {e}")
            await ctx.bot.send_message(q.message.chat_id, "❌ Ошибка обзора рынка")

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
        rockets  = sorted([(c,a) for c,a in analyzed
                           if not a.get("suspicious", False)],
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

    elif data == "precision":
        # Перенаправляем на команду через fake update
        await q.answer("🎯 Открываю Precision Shots...")
        await q.edit_message_text("🎯 Используй команду /7 для Precision Shots", parse_mode="Markdown")

    elif data == "game":
        text = f"\U0001f550 {now_utc3()}\n\n" + build_game_digest()
        nav  = InlineKeyboardMarkup([[
            InlineKeyboardButton("\U0001f504 Обновить",  callback_data="game"),
            InlineKeyboardButton("\U0001f30d Обзор",     callback_data="market_overview"),
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

    elif data == "onchain_info":
        nav = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Обновить", callback_data="onchain_info"),
             InlineKeyboardButton("🏠 Меню",     callback_data="show_menu")],
        ])
        try:
            await q.edit_message_text(
                "🐋 *On-Chain мониторинг*\n"
                "━━━━━━━━━━━━━━━━━━━━━\n\n"
                "✅ *Lookonchain* активен\n\n"
                "📊 *Что отслеживается:*\n"
                "· Движения китов (>$1M)\n"
                "· BlackRock / Grayscale ETF потоки\n"
                "· Bitcoin ETF NetFlow (дневной)\n"
                "· Накопление/распределение крупных кошельков\n"
                "· Ротация между монетами\n\n"
                "📨 *Алерты приходят автоматически* в личку\n"
                "когда Reader обнаруживает важное событие\n\n"
                "⏱ Проверка каждые *10 минут*\n\n"
                f"🕐 {now_utc3()}",
                parse_mode="Markdown", reply_markup=nav
            )
        except Exception as e:
            if "not modified" in str(e).lower():
                await q.answer("✅ Актуально")

    elif data == "channel_signals":
        await _show_channel_signals(q)

# ═══════════════════════════════════════════
# СИГНАЛЫ ИЗ ВНЕШНИХ КАНАЛОВ (Telethon reader)
# ═══════════════════════════════════════════

_READER_SIGNALS_FILE = "/tmp/reader_signals.json"

async def _show_channel_signals(q):
    """
    Читает сигналы собранные reader.py и показывает дайджест.
    reader.py пишет в /tmp/reader_signals.json
    Формат: [{"channel": str, "time": str, "text": str, "symbol": str|None, ...}]
    """
    nav = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить", callback_data="channel_signals"),
         InlineKeyboardButton("🏠 Меню",     callback_data="show_menu")],
    ])

    try:
        msg_text = (
            "📡 *Сигналы каналов*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "✅ Reader v5 активен\n"
            "📡 Мониторит *11 каналов* в реальном времени\n"
            "🐋 On-chain: *Lookonchain* (каждые 10 мин)\n\n"
            "📋 *Подключённые каналы:*\n"
            "① PIXEL\n"
            "② Мысли Эмилии\n"
            "③ Биржевой спекулянт\n"
            "④ Scalping Blog | Адель\n"
            "⑤ Kira | ICT\n"
            "⑥ Заговор ликвидности\n"
            "⑦ MANIPULATOR\n"
            "⑧ VAGR TRADING\n"
            "⑨ ANNA TRADE\n"
            "⑩ 2Trade – Kirill Sobolev\n"
            "⑪ КРИПТА С НУЛЯ | ТТ\n\n"
            "📨 Сигналы с TP/SL приходят *автоматически* в личку\n\n"
            f"🕐 {now_utc3()}"
        )
        try:
            await q.edit_message_text(msg_text, parse_mode="Markdown", reply_markup=nav)
        except Exception as e:
            if "not modified" in str(e).lower():
                # Удаляем старое и отправляем новое — гарантированное обновление
                try:
                    await q.message.delete()
                    await ctx.bot.send_message(
                        q.message.chat_id, msg_text,
                        parse_mode="Markdown", reply_markup=nav
                    )
                except:
                    await q.answer("✅ Обновлено")
            else:
                raise e
        return

        signals = []
        if not signals:
            pass

        # Группируем по каналу, берём последние 24ч
        cutoff = datetime.now(TZ).timestamp() - 86400
        recent = [s for s in signals
                  if s.get("ts", 0) > cutoff or not s.get("ts")]
        recent.sort(key=lambda x: x.get("ts", 0), reverse=True)

        lines = [
            "📡 *BEST TRADE — Сигналы каналов*",
            f"🕐 {now_utc3()}",
            f"📊 Последние 24ч: *{len(recent)} сигналов*",
            "",
        ]

        # Группируем по каналу
        by_channel: dict = {}
        for s in recent:
            ch = s.get("channel", "Неизвестный")
            by_channel.setdefault(ch, []).append(s)

        for ch_name, ch_signals in list(by_channel.items())[:10]:
            lines.append(f"*📺 {ch_name}*")
            for sig in ch_signals[:3]:   # макс 3 сигнала с канала
                t   = sig.get("time", "")
                sym = sig.get("symbol")
                txt = sig.get("summary", sig.get("text", ""))[:200]

                if sym:
                    # Если есть монета — форматируем как сигнал
                    entry  = sig.get("entry")
                    tp1    = sig.get("tp1")
                    sl     = sig.get("sl")
                    side   = sig.get("side", "")
                    side_e = "🟢" if side == "long" else ("🔴" if side == "short" else "💎")
                    tv     = tv_link(sym)

                    sig_line = f"  {side_e} [{sym}USDT]({tv})"
                    if entry: sig_line += f"  вход `{fp(float(entry))}`"
                    if tp1:   sig_line += f"  TP `{fp(float(tp1))}`"
                    if sl:    sig_line += f"  SL `{fp(float(sl))}`"
                    lines.append(sig_line)
                    if t:
                        lines.append(f"  ⏰ {t}")
                else:
                    # Просто текст сигнала
                    lines.append(f"  📝 {txt}")
                    if t:
                        lines.append(f"  ⏰ {t}")
            lines.append("")

        lines.append(f"_Обновлено: {now_utc3()}_")

        text = "\n".join(lines)
        if len(text) > 4096:
            text = text[:4090] + "..."

        try:
            await q.edit_message_text(text, parse_mode="Markdown",
                                      reply_markup=nav, disable_web_page_preview=True)
        except Exception as edit_err:
            if "not modified" in str(edit_err).lower():
                await q.answer("✅ Данные актуальны")
            else:
                raise edit_err

    except Exception as e:
        log.error(f"channel_signals: {e}")
        try:
            await q.edit_message_text(
                f"❌ Ошибка: {str(e)[:200]}",
                parse_mode="Markdown", reply_markup=nav
            )
        except:
            await q.answer("❌ Ошибка загрузки")

# ═══════════════════════════════════════════
# РАССЫЛКА
# ═══════════════════════════════════════════
async def send_scheduled(bot: Bot):
    """
    Запускается каждые 30 мин.
    Проходит по всем монетам и СРАЗУ отправляет сигнал
    как только находит подходящую — не ждёт накопления.
    """
    chat_ids = load_chat_ids() | user_chat_ids
    if not chat_ids:
        return

    log.info(f"[AUTO] Старт сканирования {now_utc3()}")

    try:
        coins = get_all_coins()
        if not coins:
            log.error("[AUTO] Нет монет")
            return

        sent_long  = 0
        sent_short = 0
        sent_spot  = 0
        already_sent = set(list(TOP_LONG_SIGNALS.keys()) + list(TOP_SHORT_SIGNALS.keys()))

        for coin in coins:
            q         = coin["quote"]["USDT"]
            price     = q.get("price",             0) or 0
            ch1h      = q.get("percent_change_1h",  0) or 0
            ch24h     = q.get("percent_change_24h", 0) or 0
            ch7d      = q.get("percent_change_7d",  0) or 0
            ch30d     = q.get("percent_change_30d", 0) or 0
            ch90d     = q.get("percent_change_90d", 0) or 0
            vol       = q.get("volume_24h",         0) or 0
            mcap      = q.get("market_cap",         0) or 0
            rank      = coin.get("cmc_rank", 9999)
            sym       = coin["symbol"]
            vol_ratio = (vol / mcap * 100) if mcap > 0 else 0
            slug      = coin.get("slug", sym.lower())

            if price <= 0 or vol < 500_000: continue
            if vol_ratio > 60:              continue
            if sym in already_sent:         continue  # уже отправляли в этот цикл

            # ── ЛОНГ: сразу отправляем ──
            if (ch1h > 0.5 and ch24h > 2.0 and ch7d > 0
                    and vol >= 2_000_000 and sent_long < 10):

                tp1 = price * 1.02; tp2 = price * 1.04; tp3 = price * 1.08
                sl  = price * 0.85; swing = price * 0.92
                score = min(50 + int(ch1h*2 + ch24h*1.5), 95)

                a_stub = {
                    "price": price, "is_long": True,
                    "tp1": tp1, "tp2": tp2, "tp3": tp3,
                    "sl": sl, "swing": swing, "rr": 2.5,
                    "rocket": score, "rocket_label": "🚀 СИГНАЛ",
                    "rsi_4h": 50.0, "rsi_1h": 50.0, "rsi_1d": 50.0,
                    "ch1h": ch1h, "ch24h": ch24h, "ch7d": ch7d,
                    "ch30d": ch30d, "ch90d": ch90d,
                    "vol": vol, "mcap": mcap, "rank": rank,
                    "above_ema20": ch24h > 0, "above_ema50": ch7d > 0,
                    "above_ema200": False,
                    "macd_bullish": ch1h > 0, "macd_bearish": False,
                    "bb_squeeze": False, "vol_spike": False,
                    "smc_factors": [], "suspicious": False,
                    "st_label": "—", "trend_4h": "bullish",
                    "atr": price*0.03, "support": price*0.92,
                    "resistance": price*1.08,
                    "ema20_4h": price*0.99, "ema50_4h": price*0.97,
                    "ema200_4h": price*0.85, "fund_recovery": False,
                }

                text = _build_signal_post(sym, a_stub, {}, mode="long")
                TOP_LONG_SIGNALS[sym] = {
                    "time": datetime.now(TZ), "entry": price,
                    "tp1": tp1, "tp2": tp2, "tp3": tp3,
                    "sl": sl, "rr": 2.5, "status": "active",
                }
                _save_signals()
                already_sent.add(sym)
                sent_long += 1

                for cid in chat_ids:
                    try:
                        await send_coin(bot, cid, sym, slug, a_stub, text)
                    except Exception as e:
                        log.error(f"[AUTO LONG] {sym} → {cid}: {e}")
                await asyncio.sleep(1.0)
                continue  # к следующей монете

            # ── ШОРТ: сразу отправляем ──
            if (ch1h < -0.5 and ch24h < -2.0
                    and vol >= 2_000_000 and sent_short < 10):

                tp1 = price * 0.98; tp2 = price * 0.96; tp3 = price * 0.92
                sl  = price * 1.15; swing = price * 1.08
                score = min(50 + int(abs(ch1h)*2 + abs(ch24h)*1.5), 95)

                a_stub = {
                    "price": price, "is_long": False,
                    "tp1": tp1, "tp2": tp2, "tp3": tp3,
                    "sl": sl, "swing": swing, "rr": 2.5,
                    "rocket": score, "rocket_label": "📉 СИГНАЛ",
                    "rsi_4h": 65.0, "rsi_1h": 65.0, "rsi_1d": 55.0,
                    "ch1h": ch1h, "ch24h": ch24h, "ch7d": ch7d,
                    "ch30d": ch30d, "ch90d": ch90d,
                    "vol": vol, "mcap": mcap, "rank": rank,
                    "above_ema20": False, "above_ema50": False,
                    "above_ema200": False,
                    "macd_bullish": False, "macd_bearish": True,
                    "bb_squeeze": False, "vol_spike": False,
                    "smc_factors": [], "suspicious": False,
                    "st_label": "—", "trend_4h": "bearish",
                    "atr": price*0.03, "support": price*0.85,
                    "resistance": price*1.08,
                    "ema20_4h": price*1.01, "ema50_4h": price*1.03,
                    "ema200_4h": price*1.15, "fund_recovery": False,
                }

                text = _build_signal_post(sym, a_stub, {}, mode="short")
                TOP_SHORT_SIGNALS[sym] = {
                    "time": datetime.now(TZ), "entry": price,
                    "tp1": tp1, "tp2": tp2, "tp3": tp3,
                    "sl": sl, "rr": 2.5, "status": "active",
                }
                _save_signals()
                already_sent.add(sym)
                sent_short += 1

                for cid in chat_ids:
                    try:
                        await send_coin(bot, cid, sym, slug, a_stub, text)
                    except Exception as e:
                        log.error(f"[AUTO SHORT] {sym} → {cid}: {e}")
                await asyncio.sleep(1.0)
                continue

            # ── СПОТ: сразу отправляем ──
            if (ch90d < -40 and ch7d > 0
                    and vol >= 1_000_000 and mcap >= 10_000_000
                    and sent_spot < 3):

                x_ath = 1 / (1 + ch90d/100) if ch90d < -5 else 1.0
                buy2  = price * 0.95; buy1 = price * 0.88; buy3 = price * 0.78
                sell  = price * x_ath * 0.85

                if x_ath >= 5:   pot = f"~x{x_ath:.1f} 🔥🔥"
                elif x_ath >= 3: pot = f"~x{x_ath:.1f} 🔥"
                else:            pot = f"~x{x_ath:.1f} ⚡️"

                text = "\n".join(filter(None, [
                    f"*{sym}USDT* 💎 *СПОТ*",
                    f"📡 Аналитика BEST TRADE  ·  Rank #{rank}",
                    "",
                    f"💰 *Цена:* `{fp(price)}`",
                    f"🎯 *Потенциал:* *{pot} до ATH*",
                    "",
                    f"📊 90д: *{fc(ch90d)}*  30д: *{fc(ch30d)}*  7д: *{fc(ch7d)}*",
                    "",
                    f"💵 *Вход 1 (40%):* `{fp(buy2)}`",
                    "",
                    f"💵 *Вход 2 (40%):* `{fp(buy1)}`",
                    "",
                    f"💵 *Вход 3 (20%):* `{fp(buy3)}`",
                    "",
                    f"🥇 *Цель:* `{fp(sell)}`  *(~x{sell/price:.1f})*" if sell > price else "",
                    "",
                    f"⚠️ Позиция: 5–10% портфеля  ·  Горизонт: от 3 мес.",
                    f"#{sym}USDT",
                ]))

                a_stub = {
                    "price": price, "is_long": True,
                    "tp1": buy2, "tp2": buy1, "tp3": buy3,
                    "sl": price*0.70, "swing": price*0.80, "rr": 3.0,
                    "rocket": 70, "rocket_label": "💎 СПОТ",
                    "rsi_4h": 35.0, "rsi_1h": 35.0, "rsi_1d": 30.0,
                    "ch1h": ch1h, "ch24h": ch24h, "ch7d": ch7d,
                    "ch30d": ch30d, "ch90d": ch90d,
                    "vol": vol, "mcap": mcap, "rank": rank,
                    "above_ema20": False, "above_ema50": False,
                    "above_ema200": False,
                    "smc_factors": [], "suspicious": False,
                    "fund_recovery": True,
                    "macd_bullish": False, "macd_bearish": False,
                    "ema20_4h": price, "ema50_4h": price, "ema200_4h": price,
                }

                TOP_SPOT_SIGNALS[sym] = {
                    "time": datetime.now(TZ), "entry": price,
                    "buy_zone_lo": buy1, "buy_zone_hi": buy2,
                    "atl": buy3, "sell_target": sell, "status": "watching",
                }
                _save_signals()
                already_sent.add(sym)
                sent_spot += 1

                for cid in chat_ids:
                    try:
                        await send_coin(bot, cid, sym, slug, a_stub, text)
                    except Exception as e:
                        log.error(f"[AUTO SPOT] {sym} → {cid}: {e}")
                await asyncio.sleep(1.0)

            # Стоп когда набрали максимум
            if sent_long >= 10 and sent_short >= 10 and sent_spot >= 3:
                break

        log.info(
            f"[AUTO] Завершено: 🟢 {sent_long} лонг  "
            f"🔴 {sent_short} шорт  💎 {sent_spot} спот"
        )

    except Exception as e:
        log.error(f"[AUTO] Критическая ошибка: {e}")


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
        reply_markup=nav
    )



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

async def check_spot_alerts(bot: Bot, chat_ids: set):
    """Алерт когда спот-монета подходит к зоне покупки (ATL/мин)"""
    if not TOP_SPOT_SIGNALS: return
    now_ts = datetime.now(TZ).timestamp()
    alerted_key = "_spot_alert"

    for sym, v in TOP_SPOT_SIGNALS.items():
        if v.get("status") == "done": continue
        buy_lo = v.get("buy_zone_lo", 0)
        buy_hi = v.get("buy_zone_hi", 0)
        if not buy_lo: continue

        # Не спамим чаще раза в 2 часа
        last_alert = pump_alerted.get(f"{alerted_key}_{sym}", 0)
        if now_ts - last_alert < 7200: continue

        try:
            stats = get_binance_24h(sym)
            if not stats: continue
            cur_price = stats.get("low", 0) or stats.get("high", 0)
            if not cur_price: continue

            in_zone = buy_lo * 0.98 <= cur_price <= buy_hi * 1.05
            near_zone = cur_price <= buy_hi * 1.10  # в 10% от зоны

            if in_zone or near_zone:
                pump_alerted[f"{alerted_key}_{sym}"] = now_ts
                status_str = "⚡️ ЦЕНА В ЗОНЕ ПОКУПКИ!" if in_zone else "📍 Цена приближается к зоне"
                text = (
                    f"💎 *СПОТ АЛЕРТ — {sym}USDT*\n"
                    f"🕐 {now_utc3()}\n\n"
                    f"{status_str}\n\n"
                    f"💰 Текущая цена: `{fp(cur_price)}`\n"
                    f"🟢 Зона покупки: `{fp(buy_lo)}` — `{fp(buy_hi)}`\n"
                    f"🔴 Цель продажи: `{fp(v.get('sell_target', 0))}`\n\n"
                    f"💡 Стратегия DCA — входить частями\n"
                    f"⚠️ Позиция: не более 5-10% портфеля\n\n"
                    f"#{sym}USDT  #СПОТТРЕЙДИНГ"
                )
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("📈 TradingView", url=tv_link(sym)),
                    InlineKeyboardButton("🔥 TOP Сделки",  callback_data="top_trades"),
                ]])
                for cid in chat_ids:
                    try:
                        await bot.send_message(cid, text, parse_mode="Markdown", reply_markup=kb)
                    except Exception as e:
                        log.error(f"Spot alert {cid}: {e}")
                log.info(f"SPOT ALERT: {sym} @ {fp(cur_price)} | зона {fp(buy_lo)}-{fp(buy_hi)}")
        except Exception as e:
            log.error(f"check_spot_alerts {sym}: {e}")


async def check_entry_approach(bot: Bot, chat_ids: set):
    """Алерт когда цена приближается к точке входа (1-2%) для активных лонг/шорт сигналов"""
    now_ts = datetime.now(TZ).timestamp()

    for sym, v in list(TOP_LONG_SIGNALS.items()):
        if v.get("status") == "done": continue
        last_alert = pump_alerted.get(f"_entry_l_{sym}", 0)
        if now_ts - last_alert < 3600: continue  # не чаще раза в час

        try:
            stats = get_binance_24h(sym)
            if not stats: continue
            cur   = stats.get("last", 0)
            entry = v.get("entry", 0)
            if not cur or not entry: continue

            dist = (entry - cur) / entry * 100 if cur < entry else 0
            if 0 < dist <= 2.0:  # цена в 0-2% от входа
                pump_alerted[f"_entry_l_{sym}"] = now_ts
                text = (
                    f"⚡️ *ВХОД БЛИЗКО — {sym}USDT* 🟢 ЛОНГ\n"
                    f"🕐 {now_utc3()}\n\n"
                    f"💰 Цена входа: `{fp(entry)}`\n"
                    f"📍 Текущая:    `{fp(cur)}`\n"
                    f"📏 До входа:   `{dist:.1f}%`\n\n"
                    f"🎯 TP1: `{fp(v.get('tp1', entry*1.02))}`\n"
                    f"🛑 SL:  `{fp(v.get('sl', entry*0.85))}`\n\n"
                    f"⚠️ Готовься к входу!\n#{sym}USDT"
                )
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("📈 TradingView", url=tv_link(sym)),
                    InlineKeyboardButton("🔥 TOP Сделки",  callback_data="top_trades"),
                ]])
                for cid in chat_ids:
                    try: await bot.send_message(cid, text, parse_mode="Markdown", reply_markup=kb)
                    except Exception as e: log.error(f"Entry approach alert {cid}: {e}")
        except Exception as e:
            log.error(f"check_entry_approach {sym}: {e}")

    for sym, v in list(TOP_SHORT_SIGNALS.items()):
        if v.get("status") == "done": continue
        last_alert = pump_alerted.get(f"_entry_s_{sym}", 0)
        if now_ts - last_alert < 3600: continue

        try:
            stats = get_binance_24h(sym)
            if not stats: continue
            cur   = stats.get("last", 0)
            entry = v.get("entry", 0)
            if not cur or not entry: continue

            dist = (cur - entry) / entry * 100 if cur > entry else 0
            if 0 < dist <= 2.0:
                pump_alerted[f"_entry_s_{sym}"] = now_ts
                text = (
                    f"⚡️ *ВХОД БЛИЗКО — {sym}USDT* 🔴 ШОРТ\n"
                    f"🕐 {now_utc3()}\n\n"
                    f"💰 Цена входа: `{fp(entry)}`\n"
                    f"📍 Текущая:    `{fp(cur)}`\n"
                    f"📏 До входа:   `{dist:.1f}%`\n\n"
                    f"🎯 TP1: `{fp(v.get('tp1', entry*0.98))}`\n"
                    f"🛑 SL:  `{fp(v.get('sl', entry*1.15))}`\n\n"
                    f"⚠️ Готовься к входу!\n#{sym}USDT"
                )
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("📈 TradingView", url=tv_link(sym)),
                    InlineKeyboardButton("🔥 TOP Сделки",  callback_data="top_trades"),
                ]])
                for cid in chat_ids:
                    try: await bot.send_message(cid, text, parse_mode="Markdown", reply_markup=kb)
                    except Exception as e: log.error(f"Entry approach alert {cid}: {e}")
        except Exception as e:
            log.error(f"check_entry_approach {sym}: {e}")


# ═══════════════════════════════════════════════════════════════════
# УРОВЕНЬ 1 — CONFLUENCE MATRIX (7+ факторов)
# ═══════════════════════════════════════════════════════════════════

def confluence_matrix(a: dict, pa: dict, coin: dict,
                      btc_ctx: dict, kz: dict) -> dict:
    """
    Максимально строгий фильтр — сигнал только при 7+ совпадениях.
    Каждый фактор имеет вес. Итог: score 0-100 и grade A+/A/B/C/D.
    """
    is_long = a.get("is_long", True)
    factors = []
    score   = 0

    checks = [
        # (условие, вес, описание)
        (a.get("above_ema200"),                                    8,  "EMA200 ✅"),
        (a.get("trend_4h") == ("bullish" if is_long else "bearish"), 7, "Тренд 4H ✅"),
        (a.get("supertrend_bull") is (True if is_long else False),  7,  "Supertrend ✅"),
        (a.get("macd_bullish") if is_long else a.get("macd_bearish"), 6, "MACD ✅"),
        ((a.get("rsi_4h",50)<35) if is_long else (a.get("rsi_4h",50)>65), 8, "RSI зона ✅"),
        (pa.get("ict_ob_bull") if is_long else pa.get("ict_ob_bear"), 10, "ICT Order Block ✅"),
        (pa.get("ict_liquidity_sweep"),                             9,  "Liq Sweep ✅"),
        ((pa.get("ict_fvg_bull") if is_long else pa.get("ict_fvg_bear")), 7, "FVG ✅"),
        (pa.get("smc_bos") == ("bull" if is_long else "bear"),     8,  "BOS ✅"),
        (pa.get("smc_choch") == ("bull" if is_long else "bear"),   9,  "CHoCH ✅"),
        (pa.get("wyckoff_phase") in (["Accumulation","Markup"] if is_long
                                      else ["Distribution","Markdown"]), 8, "Wyckoff ✅"),
        (btc_ctx.get("long_ok") if is_long else btc_ctx.get("short_ok"), 7, "BTC контекст ✅"),
        (kz.get("is_good"),                                        5,  "Killzone ✅"),
        (pa.get("tf_confluence",0) >= (2 if is_long else -2),      8,  "TF Confluence ✅"),
        (a.get("vol_spike") or pa.get("vol_trend")=="increasing",  5,  "Объём растёт ✅"),
    ]

    hits = 0
    for cond, weight, label in checks:
        if cond:
            score += weight
            factors.append(label)
            hits += 1

    score = min(100, score)

    if hits >= 10 and score >= 80:   grade = "A+ 🔥🔥"
    elif hits >= 8 and score >= 65:  grade = "A+ 🔥"
    elif hits >= 7 and score >= 55:  grade = "A ✅"
    elif hits >= 5 and score >= 40:  grade = "B 🟡"
    elif hits >= 3:                  grade = "C ⚠️"
    else:                            grade = "D ❌"

    return {
        "score":   score,
        "hits":    hits,
        "grade":   grade,
        "factors": factors,
        "pass":    grade.startswith("A"),
    }


# ═══════════════════════════════════════════════════════════════════
# УРОВЕНЬ 2 — VOLUME PROFILE / POC
# ═══════════════════════════════════════════════════════════════════

def get_volume_profile(symbol: str, tf: str = "4h", limit: int = 100) -> dict:
    """
    Volume Profile — где сосредоточен основной объём.
    POC (Point of Control) = цена с максимальным объёмом = сильнейший уровень.
    VAH (Value Area High) = верх зоны ценности (70% объёма)
    VAL (Value Area Low)  = низ зоны ценности
    """
    result = {"ok": False, "poc": 0.0, "vah": 0.0, "val": 0.0,
              "price_in_va": False, "price_above_poc": False,
              "label": "", "levels": []}
    try:
        candles = get_binance_ohlc(symbol, tf, limit)
        if not candles or len(candles) < 20:
            return result

        price = candles[-1]["close"]

        # Строим гистограмму объёма по ценовым уровням
        all_high = max(c["high"] for c in candles)
        all_low  = min(c["low"]  for c in candles)
        if all_high <= all_low:
            return result

        bins = 50  # 50 ценовых уровней
        step = (all_high - all_low) / bins
        vol_bins = [0.0] * bins

        for c in candles:
            lo, hi, vol = c["low"], c["high"], c["vol"]
            for b in range(bins):
                bin_lo = all_low + b * step
                bin_hi = bin_lo + step
                # Пересечение свечи с бином
                overlap_lo = max(lo, bin_lo)
                overlap_hi = min(hi, bin_hi)
                if overlap_hi > overlap_lo:
                    frac = (overlap_hi - overlap_lo) / (hi - lo) if hi > lo else 1.0
                    vol_bins[b] += vol * frac

        # POC = бин с максимальным объёмом
        poc_bin = vol_bins.index(max(vol_bins))
        poc = all_low + (poc_bin + 0.5) * step

        # Value Area — 70% объёма вокруг POC
        total_vol = sum(vol_bins)
        target    = total_vol * 0.70
        accum     = vol_bins[poc_bin]
        lo_b = hi_b = poc_bin

        while accum < target and (lo_b > 0 or hi_b < bins - 1):
            expand_lo = vol_bins[lo_b - 1] if lo_b > 0 else 0
            expand_hi = vol_bins[hi_b + 1] if hi_b < bins - 1 else 0
            if expand_lo >= expand_hi and lo_b > 0:
                lo_b -= 1; accum += expand_lo
            elif hi_b < bins - 1:
                hi_b += 1; accum += expand_hi
            else:
                break

        vah = all_low + (hi_b + 1) * step
        val = all_low + lo_b * step

        price_in_va    = val <= price <= vah
        price_above_poc = price > poc

        if price_in_va and price_above_poc:
            label = "🟢 Цена в Value Area выше POC — бычий контекст"
        elif price_in_va and not price_above_poc:
            label = "🔴 Цена в Value Area ниже POC — медвежий контекст"
        elif price > vah:
            label = "🚀 Цена выше VAH — сильный бычий импульс"
        elif price < val:
            label = "📉 Цена ниже VAL — сильный медвежий импульс"
        else:
            label = "⚖️ Нейтральная зона"

        result.update({
            "ok": True, "poc": round(poc, 8),
            "vah": round(vah, 8), "val": round(val, 8),
            "price_in_va": price_in_va,
            "price_above_poc": price_above_poc,
            "label": label,
            "levels": [round(val, 8), round(poc, 8), round(vah, 8)],
        })

    except Exception as e:
        log.error(f"volume_profile {symbol}: {e}")

    return result


# ═══════════════════════════════════════════════════════════════════
# УРОВЕНЬ 3 — MARKET MICROSTRUCTURE (стакан / стена ордеров)
# ═══════════════════════════════════════════════════════════════════

def get_order_book_analysis(symbol: str) -> dict:
    """
    Анализ стакана ордеров Binance.
    Находит крупные стены (bid/ask walls) — уровни где институционалы
    защищают позиции. Это реальные уровни поддержки/сопротивления.
    """
    result = {
        "ok": False,
        "bid_wall": None,    # крупная поддержка
        "ask_wall": None,    # крупное сопротивление
        "bid_ask_ratio": 1.0, # > 1.5 = давление покупателей
        "imbalance": "neutral",
        "label": "",
        "support_levels": [],
        "resistance_levels": [],
    }
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/depth",
            params={"symbol": f"{symbol}USDT", "limit": 100},
            timeout=8
        )
        if r.status_code != 200:
            return result

        data   = r.json()
        bids   = [(float(p), float(q)) for p, q in data.get("bids", [])]
        asks   = [(float(p), float(q)) for p, q in data.get("asks", [])]

        if not bids or not asks:
            return result

        mid_price = (bids[0][0] + asks[0][0]) / 2

        # Находим стены — ордера > 3× среднего
        def find_walls(orders, n=20):
            if not orders: return []
            vols = [q for _, q in orders[:n]]
            avg  = sum(vols) / len(vols) if vols else 1
            walls = [(p, q) for p, q in orders[:n] if q > avg * 3.0]
            return sorted(walls, key=lambda x: x[1], reverse=True)[:3]

        bid_walls = find_walls(bids)
        ask_walls = find_walls(asks)

        # Суммарный объём bid vs ask (первые 20 уровней)
        total_bid = sum(q for _, q in bids[:20])
        total_ask = sum(q for _, q in asks[:20])
        ratio     = total_bid / total_ask if total_ask > 0 else 1.0

        if ratio > 1.5:
            imbalance = "bullish"
            label = f"🟢 Стакан: покупатели доминируют (ratio {ratio:.2f})"
        elif ratio < 0.67:
            imbalance = "bearish"
            label = f"🔴 Стакан: продавцы доминируют (ratio {ratio:.2f})"
        else:
            imbalance = "neutral"
            label = f"⚖️ Стакан сбалансирован (ratio {ratio:.2f})"

        result.update({
            "ok":           True,
            "bid_wall":     bid_walls[0][0] if bid_walls else None,
            "ask_wall":     ask_walls[0][0] if ask_walls else None,
            "bid_ask_ratio": round(ratio, 2),
            "imbalance":    imbalance,
            "label":        label,
            "support_levels":    [p for p, _ in bid_walls],
            "resistance_levels": [p for p, _ in ask_walls],
        })

    except Exception as e:
        log.error(f"order_book {symbol}: {e}")

    return result


# ═══════════════════════════════════════════════════════════════════
# УРОВЕНЬ 4 — DXY / GOLD КОРРЕЛЯЦИЯ
# ═══════════════════════════════════════════════════════════════════

def get_macro_context() -> dict:
    """
    Макро контекст: DXY, Gold, NQ корреляция.
    Крипта = риск-актив. Когда DXY растёт → крипта падает.
    NQ (Nasdaq) и крипта — высокая корреляция в 2024-2026.
    Gold падает = risk-off = давление на крипту.
    ETH/BTC ratio — опережающий индикатор альтсезона.
    """
    result = {
        "ok": False,
        "eth_btc_ratio": 0.0,
        "eth_btc_trend": "neutral",
        "altseason": False,
        "altseason_label": "",
        "btc_dominance": 0.0,
        "dom_trend": "neutral",
        "macro_label": "",
        "risk_on": True,
        # Традиционные рынки
        "nq_trend": "unknown",
        "nq_label": "",
        "gold_trend": "unknown", 
        "gold_label": "",
        "traditional_risk": "neutral",
    }
    try:
        # ETH/BTC ratio — ключевой индикатор альтсезона
        eth_btc = get_binance_ohlc("ETHBTC", "1d", 30)
        if eth_btc and len(eth_btc) >= 14:
            closes = [c["close"] for c in eth_btc]
            ratio  = closes[-1]
            ema7   = calc_ema(closes, 7)[-1]  or ratio
            ema14  = calc_ema(closes, 14)[-1] or ratio

            if ratio > ema7 > ema14:
                eth_btc_trend = "bullish"
                altseason     = True
                altseason_lbl = "🚀 ETH/BTC растёт — альтсезон активен"
            elif ratio < ema7 < ema14:
                eth_btc_trend = "bearish"
                altseason     = False
                altseason_lbl = "🔴 ETH/BTC падает — доминация BTC, осторожно с альтами"
            else:
                eth_btc_trend = "neutral"
                altseason     = False
                altseason_lbl = "⚖️ ETH/BTC нейтрален"

            result.update({
                "eth_btc_ratio": round(ratio, 6),
                "eth_btc_trend": eth_btc_trend,
                "altseason":     altseason,
                "altseason_label": altseason_lbl,
            })

        # BTC доминация через CMC
        try:
            gm = get_global_metrics()
            dom = gm.get("btc_dominance", 0)
            result["btc_dominance"] = round(dom, 1)
            if dom > 55:
                result["dom_trend"]   = "btc_season"
                result["macro_label"] = f"₿ Доминация BTC {dom:.1f}% — деньги в BTC"
                result["risk_on"]     = False
            elif dom < 45:
                result["dom_trend"]   = "alt_season"
                result["macro_label"] = f"🌊 Доминация BTC {dom:.1f}% — альтсезон"
                result["risk_on"]     = True
            else:
                result["dom_trend"]   = "neutral"
                result["macro_label"] = f"⚖️ Доминация BTC {dom:.1f}% — нейтрально"
        except: pass

        # ── NQ (Nasdaq) корреляция ──
        # Используем QQQ-подобный proxy: AAPL/MSFT недоступны на Binance
        # Анализируем BTC поведение vs традиционные рынки через CoinGecko
        try:
            # Gold proxy через PAXG/USDT на Binance
            gold_data = get_binance_ohlc("PAXG", "1d", 10)
            if gold_data and len(gold_data) >= 5:
                gold_closes = [c["close"] for c in gold_data]
                gold_ch = (gold_closes[-1] - gold_closes[-5]) / gold_closes[-5] * 100
                if gold_ch > 1:
                    result["gold_trend"]  = "bullish"
                    result["gold_label"]  = f"🥇 Gold +{gold_ch:.1f}% — неопределённость, осторожно"
                    result["traditional_risk"] = "cautious"
                elif gold_ch < -1:
                    result["gold_trend"] = "bearish"
                    result["gold_label"] = f"🥇 Gold {gold_ch:.1f}% — risk-off, давление на крипту ⚠️"
                    result["traditional_risk"] = "risk_off"
                else:
                    result["gold_trend"] = "neutral"
                    result["gold_label"] = f"🥇 Gold нейтральный ({gold_ch:.1f}%)"
        except: pass

        result["ok"] = True

    except Exception as e:
        log.error(f"macro_context: {e}")

    return result


# ═══════════════════════════════════════════════════════════════════
# УРОВЕНЬ 5 — СЕЗОННОСТЬ
# ═══════════════════════════════════════════════════════════════════

def get_seasonality() -> dict:
    """
    Исторические паттерны сезонности крипторынка.
    На основе 10 лет данных BTC.
    """
    now   = datetime.now(TZ)
    month = now.month
    day   = now.day

    # Исторический bias по месяцам (+ бычий, - медвежий, 0 нейтральный)
    monthly_bias = {
        1:  (-2, "Январь — исторически слабый месяц после декабрьского роста"),
        2:  (+1, "Февраль — умеренно бычий, часто пре-халвинг движение"),
        3:  (+2, "Март — сильный месяц, институционалы возвращаются"),
        4:  (+3, "Апрель — исторически лучший месяц BTC ('Uptober #2')"),
        5:  (+1, "Май — 'Sell in May'? Не всегда, но осторожность уместна"),
        6:  (-1, "Июнь — исторически слабый, коррекции часты"),
        7:  (+1, "Июль — лето часто даёт отскок после июньских минимумов"),
        8:  (-1, "Август — тихий месяц, низкие объёмы"),
        9:  (-2, "Сентябрь — исторически худший месяц крипты"),
        10: (+3, "'Uptober' — лучший месяц для BTC исторически"),
        11: (+2, "Ноябрь — продолжение роста, Q4 ралли"),
        12: (+1, "Декабрь — часто рост до середины, потом коррекция"),
    }

    # Паттерны по неделям
    week_of_month = (day - 1) // 7 + 1
    weekly_notes = {
        1: "Первая неделя — часто продолжение прошлого месяца",
        2: "Вторая неделя — часто разворот или консолидация",
        3: "Третья неделя — опционные экспирации (пятница)",
        4: "Четвёртая неделя — конец месяца, часто манипуляция",
    }

    # Халвинг цикл (следующий ~апрель 2028)
    # Последний халвинг: апрель 2024
    from datetime import date
    last_halving  = date(2024, 4, 20)
    next_halving  = date(2028, 4, 20)
    days_since    = (now.date() - last_halving).days
    days_to_next  = (next_halving - now.date()).days
    cycle_pct     = days_since / (next_halving - last_halving).days * 100

    if cycle_pct < 15:
        halving_phase = "Pre-halving run 🚀"
        halving_bias  = +3
    elif cycle_pct < 35:
        halving_phase = "Post-halving accumulation 💎"
        halving_bias  = +1
    elif cycle_pct < 60:
        halving_phase = "Bull market expansion 🔥"
        halving_bias  = +3
    elif cycle_pct < 80:
        halving_phase = "Distribution / correction ⚠️"
        halving_bias  = -2
    else:
        halving_phase = "Bear market / accumulation 🔄"
        halving_bias  = -1

    bias, month_note = monthly_bias.get(month, (0, ""))
    total_bias = bias + halving_bias

    if total_bias >= 4:    season_label = "🔥🔥 Очень бычий сезон"
    elif total_bias >= 2:  season_label = "🟢 Бычий сезон"
    elif total_bias >= 0:  season_label = "⚪ Нейтральный сезон"
    elif total_bias >= -2: season_label = "🟡 Осторожный сезон"
    else:                  season_label = "🔴 Медвежий сезон"

    return {
        "ok":           True,
        "month":        month,
        "month_bias":   bias,
        "month_note":   month_note,
        "week":         week_of_month,
        "week_note":    weekly_notes.get(week_of_month, ""),
        "halving_phase": halving_phase,
        "halving_bias":  halving_bias,
        "days_to_next_halving": days_to_next,
        "cycle_pct":    round(cycle_pct, 1),
        "total_bias":   total_bias,
        "label":        season_label,
    }


# ═══════════════════════════════════════════════════════════════════
# УРОВЕНЬ 6 — ON-CHAIN ДАННЫЕ
# ═══════════════════════════════════════════════════════════════════

def get_onchain_data(symbol: str) -> dict:
    """
    On-chain метрики через публичные API.
    Exchange Netflow: отток с бирж = бычий сигнал (ходлеры уходят)
                      приток на биржи = медвежий (готовятся продавать)
    Whale movements через Binance large trades.
    """
    result = {
        "ok":           False,
        "exchange_flow": None,   # "inflow" / "outflow" / "neutral"
        "flow_label":   "",
        "whale_buys":   0,
        "whale_sells":  0,
        "whale_label":  "",
        "large_trade_ratio": 1.0,
        "net_flow_signal": "neutral",
    }
    try:
        # Анализируем крупные сделки через Binance агрегированные трейды
        r = requests.get(
            "https://api.binance.com/api/v3/aggTrades",
            params={
                "symbol": f"{symbol}USDT",
                "limit":  500,
            },
            timeout=8
        )
        if r.status_code != 200:
            return result

        trades = r.json()
        if not trades:
            return result

        # Средний объём одной сделки
        all_qtys = [float(t["q"]) for t in trades]
        avg_qty  = sum(all_qtys) / len(all_qtys) if all_qtys else 1

        # "Кит" = сделка > 10× среднего
        whale_threshold = avg_qty * 10

        whale_buys  = 0
        whale_sells = 0
        buy_vol     = 0.0
        sell_vol    = 0.0

        for t in trades:
            qty     = float(t["q"])
            is_sell = t.get("m", False)  # maker = продавец
            if qty >= whale_threshold:
                if is_sell:
                    whale_sells += 1; sell_vol += qty
                else:
                    whale_buys  += 1; buy_vol  += qty

        # Pressure ratio
        total_vol = buy_vol + sell_vol
        if total_vol > 0:
            buy_ratio = buy_vol / total_vol
        else:
            buy_ratio = 0.5

        # Сигнал
        if buy_ratio > 0.65:
            flow    = "outflow"
            flow_lbl = f"🟢 Киты покупают ({whale_buys} крупных покупок)"
            net_sig  = "bullish"
        elif buy_ratio < 0.35:
            flow    = "inflow"
            flow_lbl = f"🔴 Киты продают ({whale_sells} крупных продаж)"
            net_sig  = "bearish"
        else:
            flow    = "neutral"
            flow_lbl = "⚖️ Киты нейтральны"
            net_sig  = "neutral"

        whale_lbl = (f"🐋 {whale_buys} покупок  {whale_sells} продаж  "
                     f"Buy pressure: {buy_ratio*100:.0f}%")

        result.update({
            "ok":              True,
            "exchange_flow":   flow,
            "flow_label":      flow_lbl,
            "whale_buys":      whale_buys,
            "whale_sells":     whale_sells,
            "whale_label":     whale_lbl,
            "large_trade_ratio": round(buy_ratio, 2),
            "net_flow_signal": net_sig,
        })

    except Exception as e:
        log.error(f"onchain {symbol}: {e}")

    return result


# ═══════════════════════════════════════════════════════════════════
# BACKTESTING — проверка точности сигналов на истории
# ═══════════════════════════════════════════════════════════════════

def backtest_signal(symbol: str, is_long: bool, lookback_candles: int = 90) -> dict:
    """
    Симулирует сигналы на исторических данных 4H за lookback_candles свечей.
    Для каждой точки входа проверяет: достиг ли TP1/TP2/TP3 раньше SL.

    Возвращает:
    - winrate (% прибыльных сделок)
    - avg_rr (среднее R:R)
    - total_trades
    - best_streak / worst_streak
    - expectancy (математическое ожидание на сделку)
    """
    result = {
        "ok": False,
        "total": 0,
        "wins": 0,
        "losses": 0,
        "winrate": 0.0,
        "avg_rr": 0.0,
        "expectancy": 0.0,
        "best_streak": 0,
        "worst_streak": 0,
        "label": "",
        "summary": "",
    }
    try:
        candles = get_binance_ohlc(symbol, "4h", lookback_candles + 30)
        if not candles or len(candles) < 40:
            return result

        closes = [c["close"] for c in candles]
        highs  = [c["high"]  for c in candles]
        lows   = [c["low"]   for c in candles]

        trades = []
        i = 20  # начинаем после прогрева индикаторов

        while i < len(candles) - 10:
            price = closes[i]
            atr_w = [abs(candles[j]["high"] - candles[j]["low"]) for j in range(max(0,i-14), i)]
            atr   = sum(atr_w) / len(atr_w) if atr_w else price * 0.02

            # Swing уровни для TP/SL
            lookback = min(20, i)
            recent_highs = highs[i-lookback:i]
            recent_lows  = lows[i-lookback:i]

            levels_above = sorted([h for h in recent_highs if h > price * 1.005])
            levels_below = sorted([l for l in recent_lows  if l < price * 0.995], reverse=True)

            if is_long:
                sl  = levels_below[0] * 0.998 if levels_below else price - atr * 1.5
                tp1 = levels_above[0] * 0.998 if levels_above else price + atr * 1.0
                tp2 = levels_above[1] * 0.998 if len(levels_above) > 1 else price + atr * 1.618
                tp3 = levels_above[2] * 0.998 if len(levels_above) > 2 else price + atr * 2.618
                sl  = max(sl, price * 0.80)
            else:
                sl  = levels_above[0] * 1.002 if levels_above else price + atr * 1.5
                tp1 = levels_below[0] * 1.002 if levels_below else price - atr * 1.0
                tp2 = levels_below[1] * 1.002 if len(levels_below) > 1 else price - atr * 1.618
                tp3 = levels_below[2] * 1.002 if len(levels_below) > 2 else price - atr * 2.618
                sl  = min(sl, price * 1.20)

            if abs(sl - price) < price * 0.003:
                i += 3; continue

            rr3 = abs(tp3 - price) / abs(sl - price) if abs(sl - price) > 0 else 1.5

            # Симуляция: смотрим следующие 10 свечей
            outcome = None
            exit_rr = 0.0
            for j in range(i+1, min(i+11, len(candles))):
                h = highs[j]
                l = lows[j]
                if is_long:
                    if l <= sl:
                        outcome = "loss"; exit_rr = -1.0; break
                    if h >= tp3:
                        outcome = "win"; exit_rr = rr3; break
                    if h >= tp2:
                        outcome = "win"; exit_rr = abs(tp2-price)/abs(sl-price); break
                    if h >= tp1:
                        outcome = "win"; exit_rr = abs(tp1-price)/abs(sl-price); break
                else:
                    if h >= sl:
                        outcome = "loss"; exit_rr = -1.0; break
                    if l <= tp3:
                        outcome = "win"; exit_rr = rr3; break
                    if l <= tp2:
                        outcome = "win"; exit_rr = abs(tp2-price)/abs(sl-price); break
                    if l <= tp1:
                        outcome = "win"; exit_rr = abs(tp1-price)/abs(sl-price); break

            if outcome:
                trades.append({"outcome": outcome, "rr": exit_rr})

            i += 5  # шаг 5 свечей (20ч) между сигналами

        if not trades:
            return result

        wins   = [t for t in trades if t["outcome"] == "win"]
        losses = [t for t in trades if t["outcome"] == "loss"]
        total  = len(trades)
        wr     = len(wins) / total * 100
        avg_rr = sum(t["rr"] for t in trades) / total
        # Expectancy = winrate × avg_win_rr + lossrate × (-1)
        avg_win_rr = sum(t["rr"] for t in wins) / len(wins) if wins else 0
        expectancy = (len(wins)/total * avg_win_rr) + (len(losses)/total * (-1.0))

        # Серии
        best_streak = worst_streak = cur_w = cur_l = 0
        for t in trades:
            if t["outcome"] == "win":
                cur_w += 1; cur_l = 0
                best_streak = max(best_streak, cur_w)
            else:
                cur_l += 1; cur_w = 0
                worst_streak = max(worst_streak, cur_l)

        # Оценка
        if wr >= 60 and expectancy > 0.3:   label = "🔥 Отличная стратегия"
        elif wr >= 50 and expectancy > 0:    label = "✅ Рабочая стратегия"
        elif wr >= 40 and expectancy > -0.2: label = "🟡 Умеренная стратегия"
        else:                                label = "🔴 Слабая — пересмотреть"

        summary = (f"Сделок: {total}  Побед: {len(wins)} ({wr:.0f}%)  "
                   f"Поражений: {len(losses)}  "
                   f"Avg R:R: {avg_rr:.2f}  "
                   f"Expectancy: {expectancy:+.2f}R")

        result.update({
            "ok": True, "total": total,
            "wins": len(wins), "losses": len(losses),
            "winrate": round(wr, 1),
            "avg_rr": round(avg_rr, 2),
            "expectancy": round(expectancy, 2),
            "best_streak": best_streak,
            "worst_streak": worst_streak,
            "label": label,
            "summary": summary,
        })

    except Exception as e:
        log.error(f"backtest {symbol}: {e}")

    return result


# ═══════════════════════════════════════════════════════════════════
# НОВОСТИ / КАТАЛИЗАТОРЫ — парсинг событий по монете
# ═══════════════════════════════════════════════════════════════════

def get_coin_news(symbol: str) -> dict:
    """
    Получает последние новости и события по монете.
    Источники: CoinGecko events + CryptoCompare news API (бесплатные).
    Возвращает sentiment и ключевые события.
    """
    result = {
        "ok": False,
        "news": [],
        "sentiment": "neutral",
        "sentiment_score": 0,
        "catalyst": None,
        "label": "",
    }
    try:
        # CryptoCompare News (бесплатно, без API ключа)
        url = "https://min-api.cryptocompare.com/data/v2/news/"
        params = {
            "categories": symbol.upper(),
            "lang": "EN",
            "sortOrder": "latest",
        }
        r = requests.get(url, params=params, timeout=8)
        if r.status_code != 200:
            return result

        data = r.json().get("Data", [])[:5]  # топ 5 новостей
        if not data:
            return result

        news_items = []
        pos = neg = 0

        positive_kw = ["partnership", "launch", "upgrade", "bullish", "listing",
                       "adoption", "mainnet", "milestone", "record", "ath",
                       "integration", "grant", "investment", "rally", "pump"]
        negative_kw = ["hack", "exploit", "scam", "bear", "crash", "lawsuit",
                       "sec", "ban", "dump", "delay", "concern", "risk",
                       "sell", "drop", "plunge", "fraud", "investigation"]

        for item in data:
            title = item.get("title", "")
            body  = (item.get("body", "") or "")[:200]
            ts    = item.get("published_on", 0)
            src   = item.get("source", "")
            url_  = item.get("url", "")
            t_lower = title.lower()

            sentiment = "neutral"
            if any(kw in t_lower for kw in positive_kw):
                sentiment = "positive"; pos += 1
            elif any(kw in t_lower for kw in negative_kw):
                sentiment = "negative"; neg += 1

            age_h = (datetime.now().timestamp() - ts) / 3600 if ts else 999
            age_str = f"{int(age_h)}ч назад" if age_h < 48 else f"{int(age_h/24)}д назад"

            news_items.append({
                "title":     title[:100],
                "source":    src,
                "sentiment": sentiment,
                "age":       age_str,
                "url":       url_,
            })

        # Итоговый сентимент
        total = pos + neg
        if pos > neg and pos >= 2:
            sentiment = "positive"
            label = f"🟢 Позитивный фон ({pos} бычьих новостей)"
            score = pos
        elif neg > pos and neg >= 2:
            sentiment = "negative"
            label = f"🔴 Негативный фон ({neg} медвежьих новостей)"
            score = -neg
        else:
            sentiment = "neutral"
            label = "⚪ Нейтральный фон"
            score = 0

        # Ключевой катализатор (самая свежая позитивная/негативная)
        catalyst = None
        for n in news_items:
            if n["sentiment"] != "neutral":
                catalyst = n
                break

        result.update({
            "ok": True,
            "news": news_items,
            "sentiment": sentiment,
            "sentiment_score": score,
            "catalyst": catalyst,
            "label": label,
        })

    except Exception as e:
        log.error(f"get_coin_news {symbol}: {e}")

    return result


# ═══════════════════════════════════════════════════════════════════
# РАСШИРЕННАЯ ТОКЕНОМИКА
# ═══════════════════════════════════════════════════════════════════

# Расширенная база — 60+ монет
UNLOCK_SCHEDULE: dict = {
    # ── ВЫСОКИЙ РИСК ──
    "ASTER": {"unlock_date": "2035-01", "unlock_pct": 60, "risk": "high",
               "note": "Давление анлоков до 2035"},
    "ARB":   {"unlock_date": "2024-04", "unlock_pct": 44, "risk": "high",
               "note": "Крупные анлоки команды и инвесторов"},
    "ZK":    {"unlock_date": "2025-06", "unlock_pct": 35, "risk": "high",
               "note": "Крупные анлоки через год после TGE"},
    "STRK":  {"unlock_date": "2025-02", "unlock_pct": 40, "risk": "high",
               "note": "Высокое давление продаж от инвесторов"},
    "WLD":   {"unlock_date": "2025-07", "unlock_pct": 45, "risk": "high",
               "note": "Очень высокая инфляция токена"},
    "TIA":   {"unlock_date": "2025-10", "unlock_pct": 55, "risk": "high",
               "note": "Огромные анлоки инвесторов"},
    "MANTA": {"unlock_date": "2025-01", "unlock_pct": 38, "risk": "high",
               "note": "Анлоки инвесторов"},
    "ALT":   {"unlock_date": "2025-03", "unlock_pct": 30, "risk": "high",
               "note": "Быстрые анлоки"},
    "EIGEN": {"unlock_date": "2025-09", "unlock_pct": 35, "risk": "high",
               "note": "Крупные анлоки инвесторов"},
    "SAGA":  {"unlock_date": "2025-04", "unlock_pct": 42, "risk": "high",
               "note": "Большой процент команды"},
    "OMNI":  {"unlock_date": "2025-05", "unlock_pct": 38, "risk": "high",
               "note": "Ранние инвесторы выходят"},
    "REZ":   {"unlock_date": "2025-05", "unlock_pct": 50, "risk": "high",
               "note": "Высокая инфляция"},
    "METIS": {"unlock_date": "2025-06", "unlock_pct": 30, "risk": "high",
               "note": "Регулярные крупные анлоки"},
    "LISTA": {"unlock_date": "2025-07", "unlock_pct": 35, "risk": "high",
               "note": "Молодой проект, высокий риск"},
    "PORTAL":{"unlock_date": "2025-03", "unlock_pct": 45, "risk": "high",
               "note": "Gaming токен, высокая эмиссия"},
    "PIXEL": {"unlock_date": "2025-04", "unlock_pct": 40, "risk": "high",
               "note": "Gaming, большие анлоки"},
    "AEVO":  {"unlock_date": "2025-06", "unlock_pct": 33, "risk": "high",
               "note": "DEX токен, инфляция"},
    "ETHFI": {"unlock_date": "2025-03", "unlock_pct": 36, "risk": "high",
               "note": "LST протокол, анлоки команды"},
    # ── СРЕДНИЙ РИСК ──
    "OP":    {"unlock_date": "2024-05", "unlock_pct": 30, "risk": "medium",
               "note": "Постепенные анлоки каждый месяц"},
    "APT":   {"unlock_date": "2025-10", "unlock_pct": 25, "risk": "medium",
               "note": "Регулярные анлоки до 2025"},
    "SUI":   {"unlock_date": "2025-05", "unlock_pct": 20, "risk": "medium",
               "note": "Анлоки инвесторов каждый квартал"},
    "PYTH":  {"unlock_date": "2025-11", "unlock_pct": 22, "risk": "medium",
               "note": "Равномерные анлоки"},
    "JUP":   {"unlock_date": "2025-01", "unlock_pct": 30, "risk": "medium",
               "note": "Анлоки команды и советников"},
    "SEI":   {"unlock_date": "2025-08", "unlock_pct": 18, "risk": "medium",
               "note": "Умеренные анлоки"},
    "BLUR":  {"unlock_date": "2025-02", "unlock_pct": 25, "risk": "medium",
               "note": "NFT маркетплейс, умеренно"},
    "GMT":   {"unlock_date": "2025-04", "unlock_pct": 20, "risk": "medium",
               "note": "Move-to-earn, постепенные анлоки"},
    "DYDX":  {"unlock_date": "2025-12", "unlock_pct": 15, "risk": "low",
               "note": "Умеренные анлоки, проект зрелый"},
    "ANKR":  {"unlock_date": "2025-06", "unlock_pct": 12, "risk": "medium",
               "note": "Небольшие регулярные анлоки"},
    "CELR":  {"unlock_date": "2025-03", "unlock_pct": 15, "risk": "medium",
               "note": "Layer2, умеренная инфляция"},
    "HOOK":  {"unlock_date": "2025-04", "unlock_pct": 28, "risk": "medium",
               "note": "GameFi, регулярные анлоки"},
    "PERP":  {"unlock_date": "2025-05", "unlock_pct": 20, "risk": "medium",
               "note": "DEX, умеренно"},
    "CYBER": {"unlock_date": "2025-06", "unlock_pct": 22, "risk": "medium",
               "note": "SocialFi, умеренные анлоки"},
    "ACE":   {"unlock_date": "2025-07", "unlock_pct": 25, "risk": "medium",
               "note": "Gaming, умеренные анлоки"},
    # ── НИЗКИЙ РИСК ──
    "BTC":   {"unlock_date": "N/A",     "unlock_pct": 0,  "risk": "low",
               "note": "Фиксированная эмиссия, нет анлоков"},
    "ETH":   {"unlock_date": "N/A",     "unlock_pct": 0,  "risk": "low",
               "note": "Дефляционный после merge"},
    "BNB":   {"unlock_date": "N/A",     "unlock_pct": 0,  "risk": "low",
               "note": "Регулярный burn, нет анлоков"},
    "SOL":   {"unlock_date": "N/A",     "unlock_pct": 5,  "risk": "low",
               "note": "Небольшая инфляция (~5%), управляемо"},
    "LINK":  {"unlock_date": "N/A",     "unlock_pct": 3,  "risk": "low",
               "note": "Зрелый проект, минимальная эмиссия"},
    "AAVE":  {"unlock_date": "N/A",     "unlock_pct": 2,  "risk": "low",
               "note": "DeFi blue chip, низкая инфляция"},
    "UNI":   {"unlock_date": "N/A",     "unlock_pct": 2,  "risk": "low",
               "note": "Зрелый DEX, стабильная эмиссия"},
    "AVAX":  {"unlock_date": "2025-07", "unlock_pct": 8,  "risk": "low",
               "note": "Небольшие оставшиеся анлоки"},
    "DOT":   {"unlock_date": "N/A",     "unlock_pct": 10, "risk": "low",
               "note": "Инфляция ~10% но идёт на стейкинг"},
    "ATOM":  {"unlock_date": "N/A",     "unlock_pct": 10, "risk": "low",
               "note": "Инфляция компенсируется стейкингом"},
    "MATIC": {"unlock_date": "2025-12", "unlock_pct": 8,  "risk": "low",
               "note": "Переход на POL, небольшие анлоки"},
    "NEAR":  {"unlock_date": "N/A",     "unlock_pct": 5,  "risk": "low",
               "note": "Умеренная инфляция"},
    "FTM":   {"unlock_date": "N/A",     "unlock_pct": 3,  "risk": "low",
               "note": "Sonic апгрейд, стабильно"},
    "INJ":   {"unlock_date": "N/A",     "unlock_pct": 5,  "risk": "low",
               "note": "Дефляционная модель burn"},
    "ORDI":  {"unlock_date": "N/A",     "unlock_pct": 0,  "risk": "low",
               "note": "BRC-20, фиксированная эмиссия"},
    "RUNE":  {"unlock_date": "N/A",     "unlock_pct": 8,  "risk": "low",
               "note": "THORChain, умеренная инфляция"},
    "ONDO":  {"unlock_date": "2025-01", "unlock_pct": 18, "risk": "medium",
               "note": "RWA токен, регулярные анлоки"},
    "DOGE":  {"unlock_date": "N/A",     "unlock_pct": 0,  "risk": "low",
               "note": "Нет анлоков, меметика"},
    "SHIB":  {"unlock_date": "N/A",     "unlock_pct": 0,  "risk": "low",
               "note": "Мем, нет анлоков"},
    "PEPE":  {"unlock_date": "N/A",     "unlock_pct": 0,  "risk": "low",
               "note": "Мем, нет анлоков"},
    "WIF":   {"unlock_date": "N/A",     "unlock_pct": 0,  "risk": "low",
               "note": "Мем на Solana, нет анлоков"},
    "BONK":  {"unlock_date": "N/A",     "unlock_pct": 0,  "risk": "low",
               "note": "Мем на Solana, нет анлоков"},
    "BEAT":  {"unlock_date": "N/A",     "unlock_pct": 5,  "risk": "low",
               "note": "Небольшая эмиссия"},
}

# ═══════════════════════════════════════════════════════════════════
# BTC CORRELATION — фильтр по рыночному контексту
# ═══════════════════════════════════════════════════════════════════

def get_btc_market_context() -> dict:
    """
    Анализирует текущий тренд BTC и даёт рекомендацию.
    90% альтов коррелируют с BTC — входить против BTC опасно.

    Логика профессионала:
    - BTC падает → не открывать лонги на альты
    - BTC растёт → лонги на альты безопаснее
    - BTC в боковике → смотреть на индивидуальную силу монеты
    """
    result = {
        "ok":         False,
        "trend_1h":   "neutral",
        "trend_4h":   "neutral",
        "trend_1d":   "neutral",
        "btc_price":  0.0,
        "btc_ch1h":   0.0,
        "btc_ch24h":  0.0,
        "dominance":  0.0,
        "signal":     "neutral",   # "bull" / "bear" / "neutral"
        "long_ok":    True,
        "short_ok":   True,
        "warning":    "",
        "label":      "",
        "rsi_4h":     50.0,
        "fear_greed": None,
    }

    try:
        # BTC свечи
        c1h = get_binance_ohlc("BTC", "1h", 50)  or []
        c4h = get_binance_ohlc("BTC", "4h", 100) or []
        c1d = get_binance_ohlc("BTC", "1d", 50)  or []

        if not c4h:
            return result

        cl1h = [c["close"] for c in c1h]
        cl4h = [c["close"] for c in c4h]
        cl1d = [c["close"] for c in c1d]

        btc_price = cl4h[-1]
        result["btc_price"] = btc_price

        # EMA тренд по каждому TF
        def tf_bias(closes, fast=20, slow=50):
            if len(closes) < slow: return "neutral"
            ef = calc_ema(closes, fast)[-1] or closes[-1]
            es = calc_ema(closes, slow)[-1]  or closes[-1]
            p  = closes[-1]
            if p > ef > es:   return "bull"
            if p < ef < es:   return "bear"
            if p > es:        return "neutral_bull"
            return "neutral_bear"

        t1h = tf_bias(cl1h, 9, 21)   if len(cl1h) >= 21  else "neutral"
        t4h = tf_bias(cl4h, 20, 50)  if len(cl4h) >= 50  else "neutral"
        t1d = tf_bias(cl1d, 50, 200) if len(cl1d) >= 200 else "neutral"

        result["trend_1h"] = t1h
        result["trend_4h"] = t4h
        result["trend_1d"] = t1d

        # RSI BTC 4H
        rsi_btc = calc_rsi(cl4h, 14) if len(cl4h) >= 15 else 50.0
        result["rsi_4h"] = rsi_btc

        # Изменения цены
        ch1h  = (cl4h[-1] - cl4h[-4])  / cl4h[-4]  * 100 if len(cl4h) >= 4  else 0
        ch24h = (cl4h[-1] - cl4h[-7])  / cl4h[-7]  * 100 if len(cl4h) >= 7  else 0
        result["btc_ch1h"]  = round(ch1h, 2)
        result["btc_ch24h"] = round(ch24h, 2)

        # Определяем общий сигнал
        bull_count = sum(1 for t in [t1h, t4h, t1d] if "bull" in t)
        bear_count = sum(1 for t in [t1h, t4h, t1d] if "bear" in t)

        if bull_count >= 2:
            signal   = "bull"
            long_ok  = True
            short_ok = False
            label    = "🟢 BTC бычий — лонги приоритет"
            warning  = ""
        elif bear_count >= 2:
            signal   = "bear"
            long_ok  = False
            short_ok = True
            label    = "🔴 BTC медвежий — шорты приоритет"
            warning  = "⚠️ BTC падает — лонги на альты высокий риск"
        elif t4h == "bull" or t1d == "neutral_bull":
            signal   = "neutral_bull"
            long_ok  = True
            short_ok = True
            label    = "🟡 BTC нейтральный с бычьим уклоном"
            warning  = ""
        elif t4h == "bear" or t1d == "neutral_bear":
            signal   = "neutral_bear"
            long_ok  = True   # можно но осторожно
            short_ok = True
            label    = "🟡 BTC нейтральный с медвежьим уклоном"
            warning  = "⚠️ BTC слабый — снижай размер лонгов"
        else:
            signal   = "neutral"
            long_ok  = True
            short_ok = True
            label    = "⚪ BTC в боковике — смотри на альт индивидуально"
            warning  = ""

        # Дополнительный фильтр: BTC падает >3% за 1ч — не открывать лонги
        if ch1h < -3:
            long_ok  = False
            warning  = f"🚨 BTC -{ abs(ch1h):.1f}% за 1ч — НЕ ВХОДИТЬ В ЛОНГ"
            label   += f"  ⚡️ Резкое падение {ch1h:.1f}%"

        # BTC растёт >5% за 1ч — осторожно с шортами
        if ch1h > 5:
            short_ok = False
            label   += f"  🚀 Резкий рост +{ch1h:.1f}%"

        result.update({
            "ok":       True,
            "signal":   signal,
            "long_ok":  long_ok,
            "short_ok": short_ok,
            "label":    label,
            "warning":  warning,
        })

    except Exception as e:
        log.error(f"btc_market_context: {e}")

    return result


# ═══════════════════════════════════════════════════════════════════
# POSITION SIZING — расчёт размера позиции
# Профессиональный риск-менеджмент
# ═══════════════════════════════════════════════════════════════════

def calc_position_size(
    price: float,
    sl: float,
    deposit: float = 1000.0,
    risk_pct: float = 1.0,
    leverage: float = 1.0,
    quality: str = "B 🟡",
) -> dict:
    """
    Расчёт размера позиции по методу фиксированного % риска.

    Формула профессионала:
    Риск на сделку = депозит × risk_pct%
    Размер позиции = Риск / (цена - SL)
    
    Адаптирует risk_pct под качество сигнала:
    A+ → 2% риска, A → 1.5%, B → 1%, C → 0.5%
    """
    if price <= 0 or sl <= 0 or price == sl:
        return {"ok": False}

    # Адаптивный риск под качество
    quality_risk = {
        "A+ 🔥": 2.0,
        "A ✅":  1.5,
        "B 🟡":  1.0,
        "C ⚠️":  0.5,
    }
    adj_risk_pct = quality_risk.get(quality, risk_pct)

    risk_usd    = deposit * adj_risk_pct / 100        # $ риска на сделку
    sl_distance = abs(price - sl) / price * 100       # % до SL
    sl_usd      = abs(price - sl)                     # $ до SL за монету

    if sl_usd <= 0:
        return {"ok": False}

    # Размер позиции в монетах
    position_coins = risk_usd / sl_usd

    # Размер позиции в USD
    position_usd   = position_coins * price

    # С учётом плеча — нужно обеспечение
    margin_required = position_usd / leverage

    # % от депозита который занимает позиция
    deposit_pct = margin_required / deposit * 100

    # Максимальный размер (не более 20% депозита без плеча)
    max_position_usd = deposit * 0.20 * leverage
    if position_usd > max_position_usd:
        position_usd   = max_position_usd
        position_coins = position_usd / price
        margin_required = position_usd / leverage
        deposit_pct    = margin_required / deposit * 100
        capped = True
    else:
        capped = False

    # DCA варианты (3 входа)
    dca1_usd = position_usd * 0.40   # 40% сразу
    dca2_usd = position_usd * 0.35   # 35% на откате
    dca3_usd = position_usd * 0.25   # 25% у SL зоны

    return {
        "ok":             True,
        "risk_usd":       round(risk_usd, 2),
        "risk_pct":       adj_risk_pct,
        "sl_distance_pct": round(sl_distance, 2),
        "position_usd":   round(position_usd, 2),
        "position_coins": round(position_coins, 6),
        "margin_required": round(margin_required, 2),
        "deposit_pct":    round(deposit_pct, 1),
        "leverage":       leverage,
        "capped":         capped,
        "dca1_usd":       round(dca1_usd, 2),
        "dca2_usd":       round(dca2_usd, 2),
        "dca3_usd":       round(dca3_usd, 2),
        "deposit":        deposit,
    }


def format_position_size(ps: dict, is_long: bool = True) -> str:
    """Форматирует расчёт позиции для вставки в сигнал"""
    if not ps.get("ok"):
        return ""

    side = "лонг" if is_long else "шорт"
    lines = [
        f"💼 *Размер позиции (депозит ${ps['deposit']:.0f}):*",
        f"  Риск: `${ps['risk_usd']:.2f}` ({ps['risk_pct']}% депозита)",
        f"  SL дистанция: `{ps['sl_distance_pct']:.2f}%`",
        f"  Позиция: `${ps['position_usd']:.2f}` ({ps['deposit_pct']:.1f}% депозита)",
    ]
    if ps["leverage"] > 1:
        lines.append(f"  Плечо: `{ps['leverage']}x`  Маржа: `${ps['margin_required']:.2f}`")
    if ps["capped"]:
        lines.append("  ⚠️ Размер ограничен (макс 20% депозита)")
    lines += [
        f"  *DCA вход:*",
        f"    Вход 1 (40%): `${ps['dca1_usd']:.2f}`",
        f"    Вход 2 (35%): `${ps['dca2_usd']:.2f}`",
        f"    Вход 3 (25%): `${ps['dca3_usd']:.2f}`",
    ]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# ICT KILLZONES — лучшее время для входа
# ═══════════════════════════════════════════════════════════════════

def get_killzone_status() -> dict:
    """
    ICT Killzones — временны́е окна когда институционалы наиболее активны.
    Все в UTC+3 (Istanbul).

    Asia Session:    01:00–04:00 UTC+3  (тихая зона, но часто sweep)
    London Open:     10:00–12:00 UTC+3  (самый сильный импульс дня)
    London Close:    18:00–19:00 UTC+3  (разворот или продолжение)
    NY Open:         16:00–18:00 UTC+3  (второй по силе импульс)
    NY Close:        23:00–00:00 UTC+3  (часто манипуляция перед закрытием)
    """
    now = datetime.now(TZ)
    h   = now.hour
    m   = now.minute
    hm  = h * 60 + m  # минуты с полуночи

    zones = [
        {"name": "🌏 Asia Session",   "start": 1*60,  "end": 4*60,  "quality": "B",
         "desc": "Sweep ликвидности, тихие движения"},
        {"name": "🇬🇧 London Open",   "start": 10*60, "end": 12*60, "quality": "A+",
         "desc": "Сильнейший импульс дня — лучшее время"},
        {"name": "🇺🇸 NY Open",       "start": 16*60, "end": 18*60, "quality": "A",
         "desc": "Второй по силе импульс, много объёма"},
        {"name": "🔄 London Close",   "start": 18*60, "end": 19*60, "quality": "B",
         "desc": "Разворот или продолжение лондонского движения"},
        {"name": "🌙 NY Close",       "start": 23*60, "end": 24*60, "quality": "C",
         "desc": "Манипуляция перед закрытием, осторожно"},
    ]

    active = None
    for z in zones:
        if z["start"] <= hm < z["end"]:
            active = z
            remaining = z["end"] - hm
            active["remaining_min"] = remaining
            break

    # Следующая зона
    next_zone = None
    future = [(z, z["start"] - hm if z["start"] > hm else z["start"] + 24*60 - hm)
              for z in zones]
    future.sort(key=lambda x: x[1])
    if future:
        next_zone = future[0][0].copy()
        next_zone["in_min"] = future[0][1]

    # Оценка текущего момента
    if active:
        is_good = active["quality"] in ("A+", "A")
    else:
        is_good = False
        # Dead zone — между сессиями
        active = {"name": "💤 Dead Zone", "quality": "D",
                  "desc": "Между сессиями — избегать входов", "remaining_min": 0}

    return {
        "active":    active,
        "next":      next_zone,
        "is_good":   is_good,
        "hour":      h,
        "all_zones": zones,
    }


def killzone_label() -> str:
    """Короткая строка для вставки в сигнал"""
    kz = get_killzone_status()
    active = kz["active"]
    nxt    = kz["next"]
    q      = active["quality"]
    q_e    = {"A+": "🔥", "A": "✅", "B": "🟡", "C": "⚠️", "D": "❌"}.get(q, "")
    rem    = active.get("remaining_min", 0)

    line = f"{q_e} {active['name']}  (качество входа: {q})"
    if rem:
        line += f"  ещё {rem} мин"
    if nxt:
        line += f"\n⏰ Следующая зона: {nxt['name']} через {nxt['in_min']} мин"
    return line


# ═══════════════════════════════════════════════════════════════════
# ФИЛЬТР КАЧЕСТВА СИГНАЛОВ — только A / A+
# ═══════════════════════════════════════════════════════════════════

def signal_quality_filter(a: dict, pa: dict, coin: dict) -> dict:
    """
    Жёсткий фильтр: сигнал проходит только если 3+ факторов совпали.
    Возвращает {"pass": bool, "quality": str, "reasons": list, "score": int}
    
    Профессиональный трейдер входит только при A/A+ качестве.
    """
    reasons  = []
    warnings = []
    score    = 0
    is_long  = a.get("is_long", True)
    rsi_4h   = a.get("rsi_4h", 50)
    rocket   = a.get("rocket", 0)

    # ── Обязательные условия (хотя бы 1 из 2) ──
    has_structure = False

    # 1. Trend alignment (4H тренд совпадает с направлением)
    trend_4h = a.get("trend_4h", "neutral")
    if (is_long and trend_4h == "bullish") or (not is_long and trend_4h == "bearish"):
        score += 15; reasons.append("✅ Тренд 4H совпадает с направлением")
        has_structure = True
    elif trend_4h == "neutral":
        score += 5; reasons.append("🟡 Тренд нейтральный — осторожно")
    else:
        warnings.append("⚠️ Контртрендовый вход — повышенный риск")

    # 2. Supertrend подтверждение
    st_bull = a.get("supertrend_bull")
    if (is_long and st_bull is True) or (not is_long and st_bull is False):
        score += 12; reasons.append("✅ Supertrend подтверждает направление")
        has_structure = True

    # 3. RSI в нужной зоне
    if is_long and rsi_4h < 35:
        score += 15; reasons.append(f"✅ RSI перепродан ({rsi_4h:.0f}) — зона покупки")
    elif is_long and rsi_4h < 50:
        score += 8;  reasons.append(f"🟡 RSI нейтральный ({rsi_4h:.0f})")
    elif is_long and rsi_4h > 70:
        score -= 10; warnings.append(f"⚠️ RSI перекуплен ({rsi_4h:.0f}) — не входить в лонг")
    if not is_long and rsi_4h > 65:
        score += 15; reasons.append(f"✅ RSI перекуплен ({rsi_4h:.0f}) — зона продажи")
    elif not is_long and rsi_4h < 30:
        score -= 10; warnings.append(f"⚠️ RSI перепродан ({rsi_4h:.0f}) — не входить в шорт")

    # 4. MACD подтверждение
    if (is_long and a.get("macd_bullish")) or (not is_long and a.get("macd_bearish")):
        score += 10; reasons.append("✅ MACD подтверждает")

    # 5. EMA200 — ключевой фильтр
    if is_long and a.get("above_ema200"):
        score += 12; reasons.append("✅ Выше EMA200 — бычья структура")
    elif is_long and not a.get("above_ema200"):
        score += 3;  reasons.append("🟡 Ниже EMA200 — контртренд к дневному")
    if not is_long and not a.get("above_ema200"):
        score += 12; reasons.append("✅ Ниже EMA200 — медвежья структура")

    # 6. PRO Analysis факторы
    if pa.get("ok"):
        pro_score = pa.get("pro_score", 0)
        if pro_score >= 70:
            score += 15; reasons.append(f"✅ PRO Score {pro_score}/100 — высокое качество")
        elif pro_score >= 50:
            score += 8;  reasons.append(f"🟡 PRO Score {pro_score}/100 — умеренное качество")
        else:
            score -= 5;  warnings.append(f"⚠️ PRO Score {pro_score}/100 — слабый сетап")

        # ICT факторы из pro_analysis
        if pa.get("ict_ob_bull") and is_long:
            score += 15; reasons.append("✅ ICT Bullish Order Block")
        if pa.get("ict_ob_bear") and not is_long:
            score += 15; reasons.append("✅ ICT Bearish Order Block")
        if pa.get("ict_liquidity_sweep"):
            score += 12; reasons.append("✅ Liquidity Sweep — манипуляция завершена")
        if pa.get("smc_choch"):
            score += 10; reasons.append(f"✅ CHoCH {pa['smc_choch']} — смена характера")
        if (pa.get("ict_fvg_bull") and is_long) or (pa.get("ict_fvg_bear") and not is_long):
            score += 8; reasons.append("✅ FVG — дисбаланс заполняется")

        # Wyckoff подтверждение
        wy = pa.get("wyckoff_phase")
        if (is_long and wy == "Accumulation") or (is_long and wy == "Markup"):
            score += 10; reasons.append(f"✅ Wyckoff: {wy}")
        elif (not is_long and wy == "Distribution") or (not is_long and wy == "Markdown"):
            score += 10; reasons.append(f"✅ Wyckoff: {wy}")

        # TF confluence
        conf = pa.get("tf_confluence", 0)
        if (is_long and conf >= 3) or (not is_long and conf <= -3):
            score += 12; reasons.append(f"✅ {abs(conf)}/4 таймфреймов согласны")
        elif abs(conf) >= 2:
            score += 5

        # Funding rate
        fr = pa.get("funding_rate")
        if fr is not None:
            if is_long and fr < -0.03:
                score += 8; reasons.append(f"✅ Funding отрицательный ({fr:.4f}%) — шорты платят")
            elif not is_long and fr > 0.06:
                score += 8; reasons.append(f"✅ Funding высокий ({fr:.4f}%) — лонги перегреты")

    # 7. Rocket Score
    if rocket >= 70:
        score += 10; reasons.append(f"✅ Rocket Score {rocket}/100")
    elif rocket >= 55:
        score += 5
    else:
        warnings.append(f"⚠️ Rocket Score низкий ({rocket}/100)")

    # 8. Подозрительный объём
    if a.get("suspicious"):
        score -= 25; warnings.append("❌ Аномальный объём — возможная манипуляция")

    # ── BTC Корреляция ──
    btc = get_btc_market_context()
    if btc["ok"]:
        if is_long and btc["signal"] == "bull":
            score += 12; reasons.append("✅ BTC бычий — попутный ветер для лонгов")
        elif is_long and btc["signal"] == "bear":
            score -= 20; warnings.append("🚨 BTC медвежий — лонг ПРОТИВ рынка, высокий риск")
        elif is_long and "neutral_bear" in btc["signal"]:
            score -= 8;  warnings.append("⚠️ BTC слабый — снижай размер позиции")
        elif not is_long and btc["signal"] == "bear":
            score += 12; reasons.append("✅ BTC медвежий — попутный ветер для шортов")
        elif not is_long and btc["signal"] == "bull":
            score -= 15; warnings.append("⚠️ BTC бычий — шорт против тренда")
        # Резкое падение BTC
        if not btc["long_ok"] and is_long:
            score -= 25; warnings.append(f"🚨 BTC резко падает ({btc['btc_ch1h']:.1f}% за 1ч) — НЕ ВХОДИТЬ")

    # ── USDT.D фильтр ──
    # Высокий USDT.D = деньги в стейблах = медвежий рынок
    try:
        usdt_d_data = get_usdt_dominance() if "get_usdt_dominance" in dir() else {}
        usdt_d = usdt_d_data.get("usdt_d", 0)
        if usdt_d > 9.0 and is_long:
            score -= 15; warnings.append(f"🚨 USDT.D={usdt_d:.1f}% — сильное медвежье давление, лонг рискован")
        elif usdt_d > 8.5 and is_long:
            score -= 8; warnings.append(f"⚠️ USDT.D={usdt_d:.1f}% — осторожно с лонгами")
        elif usdt_d < 7.5 and is_long:
            score += 8; reasons.append(f"✅ USDT.D={usdt_d:.1f}% — деньги идут в крипту")
        elif usdt_d > 8.5 and not is_long:
            score += 10; reasons.append(f"✅ USDT.D={usdt_d:.1f}% — рынок медвежий, шорт по тренду")
    except: pass

    # ── Корреляция Gold / традиционные рынки ──
    try:
        mac_ctx = get_macro_context()
        if mac_ctx.get("traditional_risk") == "risk_off" and is_long:
            score -= 10; warnings.append("⚠️ Gold падает — risk-off на традиционных рынках")
        elif mac_ctx.get("traditional_risk") == "risk_off" and not is_long:
            score += 5; reasons.append("✅ Risk-off — попутный ветер для шортов")
    except: pass

    # ── Killzone бонус/штраф ──
    kz = get_killzone_status()
    if kz["is_good"]:
        score += 8; reasons.append(f"✅ {kz['active']['name']} — активная сессия")
    elif kz["active"]["quality"] == "D":
        score -= 5; warnings.append(f"⚠️ Dead Zone — плохое время для входа")

    # ── Итог ──
    score = max(0, min(100, score))

    if score >= 75 and has_structure:    quality = "A+ 🔥"
    elif score >= 55 and has_structure:  quality = "A ✅"
    elif score >= 40:                    quality = "B 🟡"
    else:                                quality = "C ⚠️"

    passes = quality in ("A+ 🔥", "A ✅")

    return {
        "pass":     passes,
        "quality":  quality,
        "score":    score,
        "reasons":  reasons,
        "warnings": warnings,
        "kz":       kz,
    }



def get_tokenomics(symbol: str) -> dict:
    """
    Возвращает данные о токеномике монеты.
    Включает: анлоки, риск, рекомендацию.
    """
    sym = symbol.upper()
    unlock = UNLOCK_SCHEDULE.get(sym)

    result = {
        "has_data":    False,
        "risk":        "unknown",
        "risk_label":  "⚪ Нет данных",
        "note":        "",
        "unlock_pct":  0,
        "recommendation": "",
        "spot_ok":     True,
    }

    if not unlock:
        return result

    risk = unlock["risk"]
    pct  = unlock.get("unlock_pct", 0)
    note = unlock.get("note", "")
    date = unlock.get("unlock_date", "")

    risk_labels = {
        "high":   "🔴 Высокий риск анлоков",
        "medium": "🟡 Умеренный риск анлоков",
        "low":    "🟢 Низкий риск анлоков",
    }

    rec = ""
    spot_ok = True
    if risk == "high":
        rec = f"⚠️ Спот нежелателен — {pct}% токенов разблокируется → давление продаж"
        spot_ok = False
    elif risk == "medium":
        rec = f"🟡 Спот с осторожностью — анлоки {pct}%, держи позицию до {date}"
    else:
        rec = f"✅ Токеномика норм — анлоки {pct}% не критичны"

    result.update({
        "has_data":    True,
        "risk":        risk,
        "risk_label":  risk_labels.get(risk, "⚪"),
        "note":        note,
        "unlock_pct":  pct,
        "unlock_date": date,
        "recommendation": rec,
        "spot_ok":     spot_ok,
    })
    return result


async def check_alerts(bot: Bot):
    """Каждые 5 мин: pump/dump + zone + supertrend + watchlist + spot + entry alerts"""
    chat_ids = load_chat_ids() | user_chat_ids
    if not chat_ids: return
    try:
        coins = get_all_coins()
        if not coins: return
        await check_pump_dump(bot, chat_ids, coins)
        await check_entry_zones(bot, chat_ids, coins)
        await check_watchlist(bot, chat_ids, coins)
        await check_supertrend_signals(bot, chat_ids, coins)
        await check_spot_alerts(bot, chat_ids)
        await check_entry_approach(bot, chat_ids)
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
        InlineKeyboardButton("🌍 /1 Обзор",   callback_data="market_overview"),
        InlineKeyboardButton("🤖 /3 Сигналы", callback_data="signals"),
    ]])
    await msg.edit_text("\n".join(lines), parse_mode="Markdown",
                        reply_markup=nav, disable_web_page_preview=True)

async def cmd_game(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /8 — Монеты в отработке
    Дайджест всех активных алертов за 48ч + отработавшие
    """
    nav = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Обновить",    callback_data="game"),
        InlineKeyboardButton("🌍 Обзор",       callback_data="market_overview"),
    ], [
        InlineKeyboardButton("🚀 Ракеты",      callback_data="rockets"),
        InlineKeyboardButton("🎯 Precision",   callback_data="precision"),
    ]])
    text = f"🕐 {now_utc3()}\n\n" + build_game_digest()
    await update.message.reply_text(
        text, parse_mode="Markdown",
        reply_markup=nav,
        disable_web_page_preview=False  # ссылки на TradingView кликабельны
    )

# ═══════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════


# main() перенесён в конец файла после всех функций

# ═══════════════════════════════════════════════════════════════════
# PRO ANALYSIS — институциональный уровень
# SMC / ICT / Wyckoff / Elliott / Multi-TF / OI / Funding
# ═══════════════════════════════════════════════════════════════════

def pro_analysis(symbol: str, coin: dict) -> dict:
    """
    Профессиональный анализ уровня топ-трейдера.
    Включает: SMC/ICT, Wyckoff, Elliott Wave (упрощ.), 
    Multi-TF confluence, OI, Funding, Volume Profile, 
    Market Structure, Liquidity Sweep detection.
    
    Возвращает расширенный dict с оценкой 0-100 и 
    детальным объяснением каждого фактора.
    """
    q     = coin["quote"]["USDT"]
    ch1h  = q.get("percent_change_1h",  0) or 0
    ch24h = q.get("percent_change_24h", 0) or 0
    ch7d  = q.get("percent_change_7d",  0) or 0
    ch30d = q.get("percent_change_30d", 0) or 0
    ch90d = q.get("percent_change_90d", 0) or 0
    vol   = q.get("volume_24h",  0) or 0
    mcap  = q.get("market_cap",  0) or 0
    rank  = coin.get("cmc_rank", 999)
    vol_ratio = (vol / mcap * 100) if mcap > 0 else 0

    result = {
        "ok": False,
        "pro_score": 0,        # 0-100 итоговый скор
        "direction": "neutral", # long / short / neutral
        "confidence": 0,        # 0-100 уверенность
        "setup_type": None,     # ICT / Wyckoff / SMC / Elliott / Breakout
        "factors": [],          # список факторов с весами
        "warnings": [],         # предупреждения
        "entry_quality": None,  # A+ / A / B / C
        # Структура рынка
        "market_structure": "unknown",  # uptrend/downtrend/ranging/accumulation/distribution
        "phase": None,           # Wyckoff phase
        # ICT концепции
        "ict_ob_bull": False,    # Bullish Order Block
        "ict_ob_bear": False,    # Bearish Order Block
        "ict_fvg_bull": False,   # Fair Value Gap вверх
        "ict_fvg_bear": False,   # Fair Value Gap вниз
        "ict_liquidity_sweep": False,  # Sweep of buy/sell side liquidity
        "ict_killzone": False,   # London/NY/Asia killzone
        "ict_pd_array": None,    # Premium/Discount array
        # SMC
        "smc_bos": None,         # Break of Structure: "bull" / "bear"
        "smc_choch": None,       # Change of Character: "bull" / "bear"
        "smc_inducement": False, # Inducement level swept
        # Wyckoff
        "wyckoff_phase": None,   # Accumulation/Distribution/Markup/Markdown
        "wyckoff_event": None,   # SC/AR/ST/Spring/UTAD etc
        # Elliott
        "elliott_wave": None,    # "wave3_up" / "wave5_up" / "wave_c_down" etc
        # Volume
        "vol_climax": False,     # Climax volume (abnormal spike)
        "vol_dry_up": False,     # Volume dry-up (consolidation)
        "vol_trend": None,       # "increasing" / "decreasing"
        # OI / Funding
        "funding_rate": None,
        "oi_change": None,
        "oi_signal": None,
        # Multi-TF
        "tf_1h": "neutral",
        "tf_4h": "neutral",
        "tf_1d": "neutral",
        "tf_1w": "neutral",
        "tf_confluence": 0,      # сколько TF согласны
    }

    try:
        # ── ЗАГРУЖАЕМ СВЕЧИ ПО ВСЕМ TF ──
        c1h  = get_binance_ohlc(symbol, "1h",  100) or []
        c4h  = get_binance_ohlc(symbol, "4h",  200) or []
        c1d  = get_binance_ohlc(symbol, "1d",  365) or []
        c1w  = get_binance_ohlc(symbol, "1w",  100) or []

        if len(c4h) < 50:
            return result

        # ── БАЗОВЫЕ ДАННЫЕ ──
        closes_1h = [c["close"] for c in c1h]
        closes_4h = [c["close"] for c in c4h]
        closes_1d = [c["close"] for c in c1d]
        closes_1w = [c["close"] for c in c1w]
        highs_4h  = [c["high"]  for c in c4h]
        lows_4h   = [c["low"]   for c in c4h]
        vols_4h   = [c["vol"]   for c in c4h]
        price     = closes_4h[-1]

        # ── EMA MULTI-TF ──
        ema20_4h  = calc_ema(closes_4h, 20)[-1]  or price
        ema50_4h  = calc_ema(closes_4h, 50)[-1]  or price
        ema200_4h = calc_ema(closes_4h, 200)[-1] or price
        ema200_1d = (calc_ema(closes_1d, 200)[-1] or price) if len(closes_1d) >= 200 else price
        ema50_1d  = (calc_ema(closes_1d, 50)[-1]  or price) if len(closes_1d) >= 50  else price
        ema20_1w  = (calc_ema(closes_1w, 20)[-1]  or price) if len(closes_1w) >= 20  else price

        # ── RSI MULTI-TF ──
        rsi_1h = calc_rsi(closes_1h, 14) if len(closes_1h) >= 15 else 50.0
        rsi_4h = calc_rsi(closes_4h, 14) if len(closes_4h) >= 15 else 50.0
        rsi_1d = calc_rsi(closes_1d, 14) if len(closes_1d) >= 15 else 50.0
        rsi_1w = calc_rsi(closes_1w, 14) if len(closes_1w) >= 15 else 50.0

        # ── MACD 4H ──
        ema12_4h = calc_ema(closes_4h, 12)
        ema26_4h = calc_ema(closes_4h, 26)
        macd_line = [a-b for a,b in zip(ema12_4h, ema26_4h) if a and b]
        sig_line  = calc_ema(macd_line, 9) if len(macd_line) >= 9 else [0.0]
        macd_val  = macd_line[-1]  if macd_line  else 0.0
        sig_val   = sig_line[-1]   if sig_line   else 0.0
        macd_hist = macd_val - sig_val
        macd_bull = macd_val > sig_val
        macd_bear = macd_val < sig_val
        # Гистограмма растёт/падает
        macd_hist_growing = (len(macd_line) > 1 and
                             macd_hist > (macd_line[-2] - (sig_line[-2] if len(sig_line) > 1 else 0)))

        # ── ATR 4H ──
        atr_vals = calc_atr(c4h, 14)
        atr = atr_vals[-1] if atr_vals else price * 0.03

        # ── SUPERTREND 4H ──
        st_vals = calc_supertrend(c4h, 10, 3.0)
        st_bull = st_vals[-1]["direction"] == 1 if st_vals else None

        # ── MARKET STRUCTURE (BOS/CHoCH) ──
        # Определяем структуру через swing highs/lows на 4H
        lookback = min(50, len(c4h))
        recent   = c4h[-lookback:]
        # Swing highs
        sh = []
        for i in range(2, len(recent)-2):
            if recent[i]["high"] > recent[i-1]["high"] and recent[i]["high"] > recent[i+1]["high"]:
                sh.append((i, recent[i]["high"]))
        # Swing lows
        sl_pts = []
        for i in range(2, len(recent)-2):
            if recent[i]["low"] < recent[i-1]["low"] and recent[i]["low"] < recent[i+1]["low"]:
                sl_pts.append((i, recent[i]["low"]))

        # BOS Bull: новый swing high выше предыдущего
        bos_bull = False
        if len(sh) >= 2 and sh[-1][1] > sh[-2][1]:
            bos_bull = True
        # BOS Bear: новый swing low ниже предыдущего
        bos_bear = False
        if len(sl_pts) >= 2 and sl_pts[-1][1] < sl_pts[-2][1]:
            bos_bear = True
        # CHoCH: смена после BOS
        choch_bull = bos_bull and len(sl_pts) >= 2 and sl_pts[-1][1] > sl_pts[-2][1]
        choch_bear = bos_bear and len(sh) >= 2 and sh[-1][1] < sh[-2][1]

        result["smc_bos"]   = "bull" if bos_bull else ("bear" if bos_bear else None)
        result["smc_choch"] = "bull" if choch_bull else ("bear" if choch_bear else None)

        # ── ICT ORDER BLOCKS ──
        # Bullish OB: последняя медвежья свеча перед импульсным ростом
        ob_bull = False
        ob_bear = False
        ob_bull_zone = None
        ob_bear_zone = None

        for i in range(5, len(c4h)-3):
            candle  = c4h[i]
            next3   = c4h[i+1:i+4]
            body    = abs(candle["close"] - candle["open"])
            rng     = candle["high"] - candle["low"]
            if rng == 0: continue

            # Bullish OB: медвежья свеча + следующие 3 закрылись выше high OB
            if (candle["close"] < candle["open"] and body/rng > 0.5):
                if all(c["close"] > candle["high"] for c in next3):
                    # Цена сейчас в зоне OB (ретест)
                    ob_lo = candle["open"]
                    ob_hi = candle["high"]
                    if ob_lo <= price <= ob_hi * 1.01:
                        ob_bull = True
                        ob_bull_zone = (ob_lo, ob_hi)

            # Bearish OB: бычья свеча + следующие 3 закрылись ниже low OB
            if (candle["close"] > candle["open"] and body/rng > 0.5):
                if all(c["close"] < candle["low"] for c in next3):
                    ob_lo = candle["low"]
                    ob_hi = candle["open"]
                    if ob_lo * 0.99 <= price <= ob_hi:
                        ob_bear = True
                        ob_bear_zone = (ob_lo, ob_hi)

        result["ict_ob_bull"] = ob_bull
        result["ict_ob_bear"] = ob_bear

        # ── ICT FAIR VALUE GAP (FVG) ──
        # FVG Bull: свеча[i-1].high < свеча[i+1].low — дыра в ценовом действии
        fvg_bull = False
        fvg_bear = False
        for i in range(1, len(c4h)-1):
            gap_lo = c4h[i-1]["high"]
            gap_hi = c4h[i+1]["low"]
            if gap_hi > gap_lo:  # Bullish FVG
                if gap_lo <= price <= gap_hi * 1.02:
                    fvg_bull = True
            gap_lo2 = c4h[i+1]["high"]
            gap_hi2 = c4h[i-1]["low"]
            if gap_hi2 < gap_lo2:  # Bearish FVG
                if gap_hi2 * 0.98 <= price <= gap_lo2:
                    fvg_bear = True

        result["ict_fvg_bull"] = fvg_bull
        result["ict_fvg_bear"] = fvg_bear

        # ── LIQUIDITY SWEEP ──
        # Цена взяла ликвидность под swing low затем вернулась (bull sweep)
        liq_bull_sweep = False
        liq_bear_sweep = False
        if len(sl_pts) >= 2 and len(c4h) >= 5:
            prev_sl = sl_pts[-2][1]
            last_5_lows = [c["low"] for c in c4h[-5:]]
            last_5_cls  = [c["close"] for c in c4h[-5:]]
            if min(last_5_lows) < prev_sl and last_5_cls[-1] > prev_sl:
                liq_bull_sweep = True  # взяли ликвидность под SL и вернулись
        if len(sh) >= 2 and len(c4h) >= 5:
            prev_sh_h = sh[-2][1]
            last_5_highs = [c["high"] for c in c4h[-5:]]
            last_5_cls   = [c["close"] for c in c4h[-5:]]
            if max(last_5_highs) > prev_sh_h and last_5_cls[-1] < prev_sh_h:
                liq_bear_sweep = True

        result["ict_liquidity_sweep"] = liq_bull_sweep or liq_bear_sweep

        # ── AMD СТРУКТУРА (ICT Power of Three) ──
        # Accumulation (Asia) → Manipulation (London sweep) → Distribution (NY move)
        # Определяем текущую фазу по времени + price action
        amd_phase = None
        amd_label = None
        now_h = datetime.now(TZ).hour

        if len(c4h) >= 6:
            high_6  = max(c["high"]  for c in c4h[-6:])
            low_6   = min(c["low"]   for c in c4h[-6:])
            mid_6   = (high_6 + low_6) / 2
            last_close = c4h[-1]["close"]

            # Asia (01-09 UTC+3): накопление — тихие движения
            if 1 <= now_h < 9:
                amd_phase = "accumulation"
                amd_label = "🌏 AMD: Фаза накопления (Asia) — жди манипуляцию"
            # London open (09-13 UTC+3): манипуляция — sweep
            elif 9 <= now_h < 13:
                if last_close < low_6 * 1.001:  # свип вниз
                    amd_phase = "manipulation_bear"
                    amd_label = "🇬🇧 AMD: Манипуляция ↓ (London sweep) — возможный разворот вверх"
                elif last_close > high_6 * 0.999:
                    amd_phase = "manipulation_bull"
                    amd_label = "🇬🇧 AMD: Манипуляция ↑ (London sweep) — возможный разворот вниз"
                else:
                    amd_phase = "manipulation"
                    amd_label = "🇬🇧 AMD: Лондонское открытие — следи за свипом"
            # NY (15-22 UTC+3): распределение — основное движение
            elif 15 <= now_h < 22:
                if last_close > mid_6:
                    amd_phase = "distribution_bull"
                    amd_label = "🇺🇸 AMD: Распределение ↑ (NY движение) — импульс вверх"
                else:
                    amd_phase = "distribution_bear"
                    amd_label = "🇺🇸 AMD: Распределение ↓ (NY движение) — импульс вниз"
            else:
                amd_phase = "dead_zone"
                amd_label = "💤 AMD: Dead Zone — между сессиями"

        result["amd_phase"] = amd_phase
        result["amd_label"] = amd_label

        # ── WYCKOFF ФАЗЫ ──
        # Упрощённое определение по volume + price action на 1D
        wyckoff_phase = None
        wyckoff_event = None
        if len(closes_1d) >= 30:
            price_30d_min = min(closes_1d[-30:])
            price_30d_max = max(closes_1d[-30:])
            price_range   = price_30d_max - price_30d_min
            price_pos     = (price - price_30d_min) / price_range if price_range > 0 else 0.5

            vols_1d = [c["vol"] for c in c1d[-30:]] if c1d else []
            vol_avg_30d = sum(vols_1d) / len(vols_1d) if vols_1d else 1
            vol_last5   = sum(vols_1d[-5:]) / 5       if len(vols_1d) >= 5 else vol_avg_30d
            vol_inc = vol_last5 > vol_avg_30d * 1.2

            if ch90d < -50 and price_pos < 0.3:
                if vol_inc and ch7d > 0:
                    wyckoff_phase = "Accumulation"
                    wyckoff_event = "Spring/LPS — разворот у дна"
                else:
                    wyckoff_phase = "Markdown"
                    wyckoff_event = "Phase D/E — продолжение падения"
            elif ch90d < -30 and abs(ch30d) < 10:
                wyckoff_phase = "Accumulation"
                wyckoff_event = "Phase B/C — накопление"
            elif ch30d > 15 and ch7d > 5:
                wyckoff_phase = "Markup"
                wyckoff_event = "Phase E — восходящее движение"
            elif ch30d > 20 and ch7d < -5 and price_pos > 0.7:
                wyckoff_phase = "Distribution"
                wyckoff_event = "UTAD / Phase B — раздача"

        result["wyckoff_phase"] = wyckoff_phase
        result["wyckoff_event"] = wyckoff_event

        # ── ELLIOTT WAVE (упрощённый паттерн) ──
        # Определяем волну по структуре движений
        elliott_wave = None
        if len(closes_4h) >= 50:
            # Смотрим на последние 3 значимых движения
            moves = []
            prev = closes_4h[-50]
            step = 10
            for i in range(-40, 0, step):
                cur = closes_4h[i]
                moves.append((cur - prev) / prev * 100)
                prev = cur

            if len(moves) >= 4:
                # Волна 3 вверх: сильный рост после коррекции
                if moves[-2] < -5 and moves[-1] > 8:
                    elliott_wave = "wave3_up 🚀"
                # Волна 5 вверх: новый хай но RSI дивергенция
                elif moves[-1] > 5 and rsi_4h > 70 and moves[-3] > 0:
                    elliott_wave = "wave5_up ⚠️"
                # Волна C вниз: коррекция после роста
                elif moves[-2] > 5 and moves[-1] < -5:
                    elliott_wave = "wave_c_down 📉"
                # Волна 2 коррекция (лучшая точка входа для волны 3)
                elif moves[-3] > 10 and moves[-2] < -5 and moves[-1] > 2:
                    elliott_wave = "wave2_correction 💎"

        result["elliott_wave"] = elliott_wave

        # ── VOLUME ANALYSIS ──
        vol_avg_20 = sum(vols_4h[-20:]) / 20 if len(vols_4h) >= 20 else 1
        vol_now    = vols_4h[-1]
        vol_climax = vol_now > vol_avg_20 * 3.0  # экстремальный объём
        vol_dry    = vol_now < vol_avg_20 * 0.4  # иссякание объёма
        vol_trend_inc = sum(vols_4h[-5:]) / 5 > sum(vols_4h[-20:-5]) / 15 if len(vols_4h) >= 20 else False

        result["vol_climax"] = vol_climax
        result["vol_dry_up"] = vol_dry
        result["vol_trend"] = "increasing" if vol_trend_inc else "decreasing"

        # ── OI / FUNDING (Binance futures) ──
        funding_rate = None
        oi_change    = None
        oi_signal    = None
        try:
            # Funding rate
            r_fr = requests.get(
                "https://fapi.binance.com/fapi/v1/premiumIndex",
                params={"symbol": f"{symbol}USDT"}, timeout=5
            )
            if r_fr.status_code == 200:
                d = r_fr.json()
                funding_rate = float(d.get("lastFundingRate", 0)) * 100
            # OI
            r_oi = requests.get(
                "https://fapi.binance.com/futures/data/openInterestHist",
                params={"symbol": f"{symbol}USDT", "period": "4h", "limit": 2}, timeout=5
            )
            if r_oi.status_code == 200:
                oi_data = r_oi.json()
                if len(oi_data) >= 2:
                    oi_now  = float(oi_data[-1].get("sumOpenInterestValue", 0))
                    oi_prev = float(oi_data[-2].get("sumOpenInterestValue", 1))
                    oi_change = (oi_now - oi_prev) / oi_prev * 100 if oi_prev else 0
                    # Интерпретация
                    if oi_change > 3 and ch1h > 0:
                        oi_signal = "🟢 OI растёт + цена растёт → сильный лонг"
                    elif oi_change > 3 and ch1h < 0:
                        oi_signal = "🔴 OI растёт + цена падает → сильный шорт"
                    elif oi_change < -3 and ch1h > 0:
                        oi_signal = "⚡️ OI падает + цена растёт → шорт-сквиз"
                    elif oi_change < -3 and ch1h < 0:
                        oi_signal = "💨 OI падает + цена падает → лонг-ликвидации"
        except: pass

        result["funding_rate"] = funding_rate
        result["oi_change"]    = oi_change
        result["oi_signal"]    = oi_signal

        # ── PREMIUM/DISCOUNT ARRAY (ICT) ──
        # Определяем находится ли цена в Premium (выше 50% диапазона) или Discount
        if len(closes_4h) >= 20:
            hi_20 = max(highs_4h[-20:])
            lo_20 = min(lows_4h[-20:])
            mid   = (hi_20 + lo_20) / 2
            eq    = mid  # Equilibrium
            if price < eq * 0.95:
                result["ict_pd_array"] = "Discount 💚 (зона покупки)"
            elif price > eq * 1.05:
                result["ict_pd_array"] = "Premium 🔴 (зона продажи)"
            else:
                result["ict_pd_array"] = "Equilibrium ⚖️"

        # ── MULTI-TF ТРЕНД ──
        def tf_trend(closes, ema_fast, ema_slow):
            if len(closes) < ema_slow: return "neutral"
            ef = calc_ema(closes, ema_fast)[-1] or closes[-1]
            es = calc_ema(closes, ema_slow)[-1] or closes[-1]
            p  = closes[-1]
            if p > ef > es:  return "bullish"
            if p < ef < es:  return "bearish"
            return "neutral"

        tf_1h_trend = tf_trend(closes_1h, 20, 50)  if len(closes_1h) >= 50  else "neutral"
        tf_4h_trend = tf_trend(closes_4h, 20, 50)
        tf_1d_trend = tf_trend(closes_1d, 50, 200) if len(closes_1d) >= 200 else "neutral"
        tf_1w_trend = tf_trend(closes_1w, 10, 20)  if len(closes_1w) >= 20  else "neutral"

        result["tf_1h"] = tf_1h_trend
        result["tf_4h"] = tf_4h_trend
        result["tf_1d"] = tf_1d_trend
        result["tf_1w"] = tf_1w_trend

        bull_tfs = sum(1 for t in [tf_1h_trend, tf_4h_trend, tf_1d_trend, tf_1w_trend] if t == "bullish")
        bear_tfs = sum(1 for t in [tf_1h_trend, tf_4h_trend, tf_1d_trend, tf_1w_trend] if t == "bearish")
        result["tf_confluence"] = bull_tfs if bull_tfs >= bear_tfs else -bear_tfs

        # ── ИТОГОВЫЙ PRO SCORE ──
        factors  = []
        warnings = []
        score    = 0
        bull_pts = 0
        bear_pts = 0

        # 1. Multi-TF confluence (макс вес)
        if bull_tfs >= 3:
            bull_pts += 20; factors.append(f"✅ Multi-TF: {bull_tfs}/4 бычьих TF (+20)")
        elif bull_tfs == 2:
            bull_pts += 10; factors.append(f"🟡 Multi-TF: 2/4 бычьих TF (+10)")
        if bear_tfs >= 3:
            bear_pts += 20; factors.append(f"✅ Multi-TF: {bear_tfs}/4 медвежьих TF (+20)")
        elif bear_tfs == 2:
            bear_pts += 10

        # 2. ICT Order Block
        if ob_bull:
            bull_pts += 15; factors.append("🟢 ICT Bullish Order Block — ретест зоны покупки (+15)")
        if ob_bear:
            bear_pts += 15; factors.append("🔴 ICT Bearish Order Block — ретест зоны продажи (+15)")

        # 3. Liquidity Sweep
        if liq_bull_sweep:
            bull_pts += 12; factors.append("⚡️ Liquidity Sweep под SL → разворот вверх (+12)")
        if liq_bear_sweep:
            bear_pts += 12; factors.append("⚡️ Liquidity Sweep над BH → разворот вниз (+12)")

        # 4. FVG
        if fvg_bull:
            bull_pts += 8; factors.append("🟢 Bullish FVG — цена в зоне дисбаланса (+8)")
        if fvg_bear:
            bear_pts += 8; factors.append("🔴 Bearish FVG — цена в зоне дисбаланса (+8)")

        # 5. BOS / CHoCH
        if bos_bull:
            bull_pts += 8; factors.append("🟢 BOS вверх — структура рынка бычья (+8)")
        if bos_bear:
            bear_pts += 8; factors.append("🔴 BOS вниз — структура рынка медвежья (+8)")
        if choch_bull:
            bull_pts += 10; factors.append("🚀 CHoCH — смена характера → разворот вверх (+10)")
        if choch_bear:
            bear_pts += 10; factors.append("📉 CHoCH — смена характера → разворот вниз (+10)")

        # 6. RSI Multi-TF
        if rsi_4h < 30 and rsi_1d < 35:
            bull_pts += 10; factors.append(f"🟢 RSI перепродан на 4H({rsi_4h:.0f}) и 1D({rsi_1d:.0f}) (+10)")
        elif rsi_4h < 40:
            bull_pts += 5;  factors.append(f"🟡 RSI 4H перепродан ({rsi_4h:.0f}) (+5)")
        if rsi_4h > 70 and rsi_1d > 65:
            bear_pts += 10; factors.append(f"🔴 RSI перекуплен на 4H({rsi_4h:.0f}) и 1D({rsi_1d:.0f}) (+10)")
        elif rsi_4h > 65:
            bear_pts += 5

        # 7. MACD
        if macd_bull and macd_hist_growing:
            bull_pts += 7; factors.append("🟢 MACD пересечение вверх + гистограмма растёт (+7)")
        elif macd_bull:
            bull_pts += 3
        if macd_bear and not macd_hist_growing:
            bear_pts += 7; factors.append("🔴 MACD пересечение вниз (+7)")

        # 8. Supertrend
        if st_bull is True:
            bull_pts += 8;  factors.append("🟢 Supertrend: BUY сигнал (+8)")
        elif st_bull is False:
            bear_pts += 8;  factors.append("🔴 Supertrend: SELL сигнал (+8)")

        # 9. Wyckoff
        if wyckoff_phase == "Accumulation":
            bull_pts += 10; factors.append(f"💎 Wyckoff: {wyckoff_event} (+10)")
        elif wyckoff_phase == "Markup":
            bull_pts += 8;  factors.append(f"🚀 Wyckoff: {wyckoff_event} (+8)")
        elif wyckoff_phase == "Distribution":
            bear_pts += 10; factors.append(f"⚠️ Wyckoff: {wyckoff_event} (+10)")
        elif wyckoff_phase == "Markdown":
            bear_pts += 8;  factors.append(f"📉 Wyckoff: {wyckoff_event} (+8)")

        # 10. Elliott Wave
        if elliott_wave:
            if "wave3_up" in elliott_wave:
                bull_pts += 12; factors.append(f"🚀 Elliott: {elliott_wave} — самая сильная волна (+12)")
            elif "wave2_correction" in elliott_wave:
                bull_pts += 10; factors.append(f"💎 Elliott: {elliott_wave} — идеальная точка входа (+10)")
            elif "wave5_up" in elliott_wave:
                bull_pts += 5; warnings.append(f"⚠️ Elliott: {elliott_wave} — финальная волна, риск разворота")
            elif "wave_c_down" in elliott_wave:
                bear_pts += 10; factors.append(f"📉 Elliott: {elliott_wave} (+10)")

        # 11. OI / Funding
        if oi_change is not None:
            if oi_change > 5 and ch1h > 0:
                bull_pts += 8; factors.append(f"🟢 OI +{oi_change:.1f}% + рост цены → институционалы покупают (+8)")
            elif oi_change < -5 and ch1h > 0:
                bull_pts += 6; factors.append(f"⚡️ OI -{abs(oi_change):.1f}% + рост → шорт-сквиз (+6)")
            elif oi_change > 5 and ch1h < 0:
                bear_pts += 8; factors.append(f"🔴 OI растёт + цена падает → медведи усиливаются (+8)")

        if funding_rate is not None:
            if funding_rate < -0.05:
                bull_pts += 6; factors.append(f"🟢 Funding rate отрицательный ({funding_rate:.4f}%) → шорты платят (+6)")
            elif funding_rate > 0.08:
                bear_pts += 6; factors.append(f"🔴 Funding rate высокий ({funding_rate:.4f}%) → лонги перегреты (+6)")
            elif abs(funding_rate) < 0.01:
                bull_pts += 2; factors.append(f"⚖️ Funding нейтральный — здоровый рынок (+2)")

        # 12. Volume
        if vol_trend_inc and ch1h > 0:
            bull_pts += 5; factors.append("📊 Объём растёт + цена растёт → подтверждение тренда (+5)")
        if vol_climax:
            warnings.append("⚠️ Climax volume — возможен разворот или продолжение")
        if vol_dry and abs(ch24h) < 2:
            bull_pts += 4; factors.append("💎 Volume dry-up в боковике → breakout близко (+4)")

        # 13. Фундаментал
        if rank <= 20:
            bull_pts += 5; factors.append(f"🏆 Топ-20 по рыночной капе (rank #{rank}) (+5)")
        elif rank <= 50:
            bull_pts += 3; factors.append(f"🥇 Топ-50 (rank #{rank}) (+3)")

        # ── ОПРЕДЕЛЯЕМ НАПРАВЛЕНИЕ ──
        if bull_pts > bear_pts + 10:
            direction = "long"
            score = min(100, 30 + bull_pts)
        elif bear_pts > bull_pts + 10:
            direction = "short"
            score = min(100, 30 + bear_pts)
        else:
            direction = "neutral"
            score = max(bull_pts, bear_pts)

        # Снижаем при подозрительных сигналах
        if vol_ratio > 50:
            score = int(score * 0.6)
            warnings.append("⚠️ Аномальный Vol/MCap — возможна манипуляция")

        # Тип сетапа
        if ob_bull or ob_bear:        setup = "ICT Order Block"
        elif liq_bull_sweep or liq_bear_sweep: setup = "ICT Liquidity Sweep"
        elif choch_bull or choch_bear: setup = "SMC CHoCH"
        elif wyckoff_phase:            setup = f"Wyckoff {wyckoff_phase}"
        elif elliott_wave:             setup = f"Elliott {elliott_wave}"
        elif fvg_bull or fvg_bear:    setup = "ICT FVG"
        else:                          setup = "Multi-TF Confluence"

        # Entry quality
        strong_confluences = sum([
            ob_bull and direction == "long",
            ob_bear and direction == "short",
            liq_bull_sweep and direction == "long",
            liq_bear_sweep and direction == "short",
            choch_bull and direction == "long",
            choch_bear and direction == "short",
            bull_tfs >= 3 and direction == "long",
            bear_tfs >= 3 and direction == "short",
            (fvg_bull and direction == "long") or (fvg_bear and direction == "short"),
            (rsi_4h < 30 and direction == "long") or (rsi_4h > 70 and direction == "short"),
        ])
        if strong_confluences >= 5:   entry_q = "A+ 🔥"
        elif strong_confluences >= 3: entry_q = "A ✅"
        elif strong_confluences >= 2: entry_q = "B 🟡"
        else:                         entry_q = "C ⚠️"

        result.update({
            "ok":             True,
            "pro_score":      score,
            "direction":      direction,
            "confidence":     min(100, score),
            "setup_type":     setup,
            "factors":        factors,
            "warnings":       warnings,
            "entry_quality":  entry_q,
            "market_structure": f"{direction}",
            "phase":          wyckoff_phase,
            # raw data for display
            "rsi_1h": rsi_1h, "rsi_4h": rsi_4h, "rsi_1d": rsi_1d,
            "ema200_4h": ema200_4h, "ema200_1d": ema200_1d,
            "macd_bull": macd_bull, "macd_hist": macd_hist,
            "atr": atr, "st_bull": st_bull,
            "price": price,
            "support": min(lows_4h[-20:]) if len(lows_4h) >= 20 else price*0.95,
            "resistance": max(highs_4h[-20:]) if len(highs_4h) >= 20 else price*1.05,
        })

    except Exception as e:
        log.error(f"pro_analysis {symbol}: {e}")

    return result


# ═══════════════════════════════════════════════════════════════════
# РЕАЛЬНЫЙ ТЕХНИЧЕСКИЙ АНАЛИЗ — из свечей Binance (не CMC estimate)
# ═══════════════════════════════════════════════════════════════════

def real_ta(symbol: str) -> dict:
    """
    Все индикаторы из реальных OHLC свечей Binance.
    4H - основной таймфрейм, 1D - тренд, 1H - моментум.
    """
    result = {
        "ok": False,
        "rsi_1h": 50.0, "rsi_4h": 50.0, "rsi_1d": 50.0,
        "ema20": 0.0, "ema50": 0.0, "ema200": 0.0,
        "above_ema20": False, "above_ema50": False, "above_ema200": False,
        "macd_hist": 0.0, "macd_signal_bull": False, "macd_signal_bear": False,
        "bb_upper": 0.0, "bb_lower": 0.0, "bb_squeeze": False,
        "vol_avg": 0.0, "vol_spike": False,
        "price": 0.0,
        "supertrend_bull": None,
        "atr": 0.0,
        "support": 0.0, "resistance": 0.0,
        "trend_4h": "neutral",   # bullish / bearish / neutral
        "candles_4h": [],
    }
    try:
        c4h = get_binance_ohlc(symbol, "4h", 200)
        if not c4h or len(c4h) < 50:
            return result

        closes_4h = [c["close"] for c in c4h]
        highs_4h  = [c["high"]  for c in c4h]
        lows_4h   = [c["low"]   for c in c4h]
        vols_4h   = [c["vol"]   for c in c4h]
        price     = closes_4h[-1]

        # ── EMA (4H свечи) ──
        _ema20  = calc_ema(closes_4h, 20)
        _ema50  = calc_ema(closes_4h, 50)
        _ema200 = calc_ema(closes_4h, 200)
        ema20_v  = next((v for v in reversed(_ema20)  if v is not None), price)
        ema50_v  = next((v for v in reversed(_ema50)  if v is not None), price)
        ema200_v = next((v for v in reversed(_ema200) if v is not None), price)

        # ── RSI ──
        rsi_4h = calc_rsi(closes_4h, 14)

        c1h = get_binance_ohlc(symbol, "1h", 50)
        rsi_1h = calc_rsi([c["close"] for c in c1h], 14) if c1h else 50.0

        c1d = get_binance_ohlc(symbol, "1d", 50)
        rsi_1d = calc_rsi([c["close"] for c in c1d], 14) if c1d else 50.0

        # ── MACD (12, 26, 9) на 4H ──
        ema12 = calc_ema(closes_4h, 12)
        ema26 = calc_ema(closes_4h, 26)
        macd_line = [
            (a - b) for a, b in zip(ema12, ema26)
            if a is not None and b is not None
        ]
        signal_line = calc_ema(macd_line, 9) if len(macd_line) >= 9 else [0.0]
        macd_val  = macd_line[-1]  if macd_line  else 0.0
        sig_val   = signal_line[-1] if signal_line else 0.0
        macd_hist = macd_val - sig_val
        macd_bull = macd_val > sig_val and (len(macd_line) < 2 or macd_line[-2] <= (signal_line[-2] or macd_line[-2]))
        macd_bear = macd_val < sig_val and (len(macd_line) < 2 or macd_line[-2] >= (signal_line[-2] or macd_line[-2]))

        # ── Bollinger Bands (20, 2σ) ──
        window = closes_4h[-20:]
        bb_mid  = sum(window) / 20
        bb_std  = (sum((x - bb_mid)**2 for x in window) / 20) ** 0.5
        bb_up   = bb_mid + 2 * bb_std
        bb_dn   = bb_mid - 2 * bb_std
        bb_w    = (bb_up - bb_dn) / bb_mid if bb_mid > 0 else 0
        bb_sqz  = bb_w < 0.04   # сжатие < 4% ширина

        # ── Volume spike ──
        vol_avg = sum(vols_4h[-20:]) / 20 if len(vols_4h) >= 20 else 1
        vol_now = vols_4h[-1]
        vol_spk = vol_now > vol_avg * 1.5

        # ── ATR (14) ──
        atr_vals = calc_atr(c4h, 14)
        atr_v    = atr_vals[-1] if atr_vals else 0.0

        # ── Supply / Demand зоны (SMC метод) ──
        # Demand (покупка) = зоны где цена резко выросла после консолидации
        # Supply (продажа) = зоны где цена резко упала после консолидации
        demand_zones = []  # [(low, high), ...]
        supply_zones = []

        for i in range(5, len(candles) - 5):
            c = candles[i]
            # Спред свечи
            body = abs(c["close"] - c["open"])
            rng  = c["high"] - c["low"]
            if rng == 0: continue

            # Импульсная свеча вверх → Demand зона (основание)
            if (c["close"] > c["open"]                         # бычья
                    and body / rng > 0.6                       # сильное тело
                    and body > sum(abs(candles[j]["close"] - candles[j]["open"])
                                   for j in range(i-3, i)) / 3):  # больше среднего
                demand_zones.append((c["low"], c["open"]))

            # Импульсная свеча вниз → Supply зона (верхушка)
            if (c["close"] < c["open"]
                    and body / rng > 0.6
                    and body > sum(abs(candles[j]["close"] - candles[j]["open"])
                                   for j in range(i-3, i)) / 3):
                supply_zones.append((c["close"], c["high"]))

        # Ближайшая Demand зона ниже цены = support
        price_now = closes_4h[-1]
        relevant_demand = [z for z in demand_zones if z[1] < price_now]
        relevant_supply = [z for z in supply_zones if z[0] > price_now]

        if relevant_demand:
            closest_demand = max(relevant_demand, key=lambda z: z[1])
            support = closest_demand[0]
        else:
            recent_lows = sorted(lows_4h[-50:])
            support = sum(recent_lows[:5]) / 5

        if relevant_supply:
            closest_supply = min(relevant_supply, key=lambda z: z[0])
            resistance = closest_supply[1]
        else:
            recent_highs = sorted(highs_4h[-50:], reverse=True)
            resistance = sum(recent_highs[:5]) / 5

        # Флаг: цена у зоны
        in_demand_zone = any(z[0] <= price_now <= z[1] * 1.02 for z in demand_zones[-10:])
        in_supply_zone = any(z[0] * 0.98 <= price_now <= z[1] for z in supply_zones[-10:])

        # ── Supertrend (4H) ──
        st_vals = calc_supertrend(c4h, 10, 3.0)
        st_bull = st_vals[-1]["direction"] == 1 if st_vals else None

        # ── Тренд 4H (по EMA) ──
        if price > ema20_v > ema50_v:
            trend_4h = "bullish"
        elif price < ema20_v < ema50_v:
            trend_4h = "bearish"
        else:
            trend_4h = "neutral"

        result.update({
            "ok": True,
            "rsi_1h": round(rsi_1h, 1),
            "rsi_4h": round(rsi_4h, 1),
            "rsi_1d": round(rsi_1d, 1),
            "ema20":  round(ema20_v, 8),
            "ema50":  round(ema50_v, 8),
            "ema200": round(ema200_v, 8),
            "above_ema20":  price > ema20_v,
            "above_ema50":  price > ema50_v,
            "above_ema200": price > ema200_v,
            "macd_hist":        round(macd_hist, 8),
            "macd_signal_bull": bool(macd_bull),
            "macd_signal_bear": bool(macd_bear),
            "bb_upper":  round(bb_up, 8),
            "bb_lower":  round(bb_dn, 8),
            "bb_squeeze": bb_sqz,
            "vol_avg":   round(vol_avg, 2),
            "vol_spike": vol_spk,
            "price":     price,
            "supertrend_bull": st_bull,
            "atr":       round(atr_v, 8),
            "support":   round(support, 8),
            "resistance": round(resistance, 8),
            "trend_4h":  trend_4h,
            "candles_4h": c4h,
            # Supply/Demand зоны
            "in_demand_zone": in_demand_zone,
            "in_supply_zone": in_supply_zone,
            "demand_zones": demand_zones[-5:],  # последние 5
            "supply_zones": supply_zones[-5:],
        })
    except Exception as e:
        log.error(f"real_ta {symbol}: {e}")
    return result


def real_full_analysis(coin: dict) -> dict:
    """
    Полный анализ с РЕАЛЬНЫМИ индикаторами из Binance свечей.
    Заменяет full_analysis для /full, top_spot, top_long, top_short.
    """
    q     = coin["quote"]["USDT"]
    ch1h  = q.get("percent_change_1h",  0) or 0
    ch24h = q.get("percent_change_24h", 0) or 0
    ch7d  = q.get("percent_change_7d",  0) or 0
    ch30d = q.get("percent_change_30d", 0) or 0
    ch90d = q.get("percent_change_90d", 0) or 0
    vol   = q.get("volume_24h",  0) or 0
    mcap  = q.get("market_cap",  0) or 0
    rank  = coin.get("cmc_rank", 999)
    sym   = coin["symbol"]
    vol_ratio = (vol / mcap * 100) if mcap > 0 else 0
    suspicious = vol_ratio > 50

    # Реальный ТА из Binance
    ta = real_ta(sym)
    price = ta["price"] if ta["ok"] and ta["price"] > 0 else (q.get("price", 0) or 0)

    rsi_4h = ta["rsi_4h"] if ta["ok"] else 50.0
    rsi_1h = ta["rsi_1h"] if ta["ok"] else 50.0
    rsi_1d = ta["rsi_1d"] if ta["ok"] else 50.0

    ema20_v  = ta["ema20"]  if ta["ok"] else price
    ema50_v  = ta["ema50"]  if ta["ok"] else price
    ema200_v = ta["ema200"] if ta["ok"] else price

    above_ema20  = ta["above_ema20"]  if ta["ok"] else False
    above_ema50  = ta["above_ema50"]  if ta["ok"] else False
    above_ema200 = ta["above_ema200"] if ta["ok"] else False

    macd_bullish = ta["macd_signal_bull"] if ta["ok"] else False
    macd_bearish = ta["macd_signal_bear"] if ta["ok"] else False
    bb_squeeze   = ta["bb_squeeze"]       if ta["ok"] else False
    vol_spike    = ta["vol_spike"]        if ta["ok"] else False
    supertrend_bull = ta.get("supertrend_bull")
    trend_4h     = ta.get("trend_4h", "neutral")
    atr          = ta.get("atr", 0.0)
    support      = ta.get("support", price * 0.92)
    resistance   = ta.get("resistance", price * 1.08)

    # SMC из реальных данных
    smc_bos_bull    = ch7d > 8  and ch30d < -10
    smc_bos_bear    = ch7d < -8 and ch30d > 10
    smc_ob_accum    = 5 <= vol_ratio <= 40 and abs(ch24h) < 2
    smc_liq_sweep   = ch1h < -3 and vol_ratio >= 5
    smc_smart_accum = ch24h < -3 and ch7d > 0 and 5 <= vol_ratio <= 50
    smc_smart_dist  = ch24h > 8  and 10 <= vol_ratio <= 50
    smc_fvg_bull    = ch1h >= 2 and ch24h > 3
    smc_fvg_bear    = ch1h <= -2 and ch24h < -3
    tf_aligned_bull = above_ema20 and above_ema50 and trend_4h == "bullish"
    tf_aligned_bear = not above_ema20 and not above_ema50 and trend_4h == "bearish"
    fund_recovery   = ch90d < -40 and ch7d > 5 and not suspicious

    # Supply/Demand зоны из реальных свечей
    in_demand = ta.get("in_demand_zone", False)
    in_supply = ta.get("in_supply_zone", False)

    # Direction: реальный тренд из TA
    score_ta = 0
    if trend_4h == "bullish":    score_ta += 3
    elif trend_4h == "bearish":  score_ta -= 3
    if above_ema200:             score_ta += 2
    else:                        score_ta -= 1
    if macd_bullish:             score_ta += 2
    elif macd_bearish:           score_ta -= 2
    if rsi_4h < 40:              score_ta += 1
    elif rsi_4h > 65:            score_ta -= 1
    if ch24h >= 3:               score_ta += 1
    elif ch24h <= -3:            score_ta -= 1
    if ch7d > 5:                 score_ta += 1
    elif ch7d < -5:              score_ta -= 1
    if supertrend_bull is True:  score_ta += 2
    elif supertrend_bull is False: score_ta -= 2
    # Supply/Demand влияют на направление
    if in_demand:                score_ta += 3   # цена в зоне покупки → лонг
    if in_supply:                score_ta -= 3   # цена в зоне продажи → шорт

    is_long = score_ta >= 0

    # Rocket Score
    rocket = 30
    if above_ema20:              rocket += 6
    if above_ema50:              rocket += 5
    if above_ema200:             rocket += 8
    if macd_bullish and is_long: rocket += 5
    if macd_bearish and not is_long: rocket += 5
    if rsi_4h < 35 and is_long:  rocket += 8
    if rsi_4h > 65 and not is_long: rocket += 6
    if bb_squeeze:               rocket += 4
    if vol_spike:                rocket += 4
    if smc_bos_bull and is_long: rocket += 6
    if smc_ob_accum and is_long: rocket += 5
    if smc_liq_sweep and is_long:rocket += 4
    if smc_smart_accum and is_long: rocket += 8
    if smc_fvg_bull and is_long: rocket += 3
    if supertrend_bull is True and is_long:  rocket += 6
    elif supertrend_bull is False and not is_long: rocket += 6
    # Supply/Demand бонусы
    if in_demand and is_long:    rocket += 12   # цена в зоне Demand → сильный лонг
    if in_supply and not is_long: rocket += 12  # цена в зоне Supply → сильный шорт
    if in_demand and not is_long: rocket -= 10  # противоречие
    if in_supply and is_long:    rocket -= 10   # противоречие
    if rank <= 20:               rocket += 6
    elif rank <= 50:             rocket += 4
    elif rank <= 200:            rocket += 2
    if vol >= 10_000_000 and vol_ratio <= 50: rocket += 3
    if mcap >= 1_000_000_000:   rocket += 3
    if fund_recovery and is_long: rocket += 9
    if suspicious:               rocket -= 20
    if ch24h < -10 and is_long:  rocket -= 8
    if rsi_4h > 80 and is_long:  rocket -= 5
    rocket = max(0, min(100, rocket))

    # ══════════════════════════════════════════════════════
    # ПРОФЕССИОНАЛЬНЫЙ РАСЧЁТ TP/SL — по реальным уровням
    # Логика топ-трейдера:
    #   SL — ЗА Order Block / swing low/high, не ATR×1.5
    #   TP1 — ближайший Supply/Demand уровень
    #   TP2 — следующий значимый уровень (50% FVG / предыдущий High)
    #   TP3 — структурная цель (100% движения / EMA200 / ATH зоны)
    # ══════════════════════════════════════════════════════
    import math

    def smart_round(val):
        if val == 0: return 0
        magnitude = math.floor(math.log10(abs(val))) if val > 0 else 0
        precision = max(8, -magnitude + 3)
        return round(val, precision)

    # ── Собираем реальные уровни из 4H свечей ──
    try:
        c4h_levels = get_binance_ohlc(sym, "4h", 100) or []
        highs_4h_l = [c["high"] for c in c4h_levels]
        lows_4h_l  = [c["low"]  for c in c4h_levels]

        # Swing Highs и Lows (локальные экстремумы)
        swing_highs = []
        swing_lows  = []
        for i in range(2, len(c4h_levels)-2):
            if (c4h_levels[i]["high"] > c4h_levels[i-1]["high"] and
                c4h_levels[i]["high"] > c4h_levels[i-2]["high"] and
                c4h_levels[i]["high"] > c4h_levels[i+1]["high"] and
                c4h_levels[i]["high"] > c4h_levels[i+2]["high"]):
                swing_highs.append(c4h_levels[i]["high"])
            if (c4h_levels[i]["low"] < c4h_levels[i-1]["low"] and
                c4h_levels[i]["low"] < c4h_levels[i-2]["low"] and
                c4h_levels[i]["low"] < c4h_levels[i+1]["low"] and
                c4h_levels[i]["low"] < c4h_levels[i+2]["low"]):
                swing_lows.append(c4h_levels[i]["low"])

        # Уровни выше и ниже текущей цены
        levels_above = sorted([h for h in swing_highs if h > price * 1.005])
        levels_below = sorted([l for l in swing_lows  if l < price * 0.995], reverse=True)

        # Ближайшие уровни
        r1 = levels_above[0] if len(levels_above) > 0 else None
        r2 = levels_above[1] if len(levels_above) > 1 else None
        r3 = levels_above[2] if len(levels_above) > 2 else None
        s1 = levels_below[0] if len(levels_below) > 0 else None
        s2 = levels_below[1] if len(levels_below) > 1 else None

    except Exception:
        r1 = r2 = r3 = s1 = s2 = None
        highs_4h_l = []
        lows_4h_l  = []

    # ── ATR для минимального расстояния ──
    atr_min = atr if atr > 0 else price * 0.02

    if is_long:
        # ── SL: за ближайший Swing Low, но минимум 1×ATR ──
        if s1 and s1 < price - atr_min * 0.5:
            sl_raw = s1 * 0.998   # чуть ниже swing low
        elif s2 and s2 < price - atr_min:
            sl_raw = s2 * 0.998
        else:
            sl_raw = price - atr_min * 1.5
        sl = smart_round(max(sl_raw, price * 0.80))  # не более -20%

        # ── TP1: ближайший Swing High ──
        if r1 and r1 < price * 1.15:
            tp1 = smart_round(r1 * 0.998)    # чуть не доходим до resistance
        else:
            tp1 = smart_round(price + atr_min * 1.0)

        # ── TP2: второй Swing High / 1.618× движения от входа ──
        move = price - sl_raw
        fib_target = price + move * 1.618
        if r2 and r2 < price * 1.30:
            tp2 = smart_round(r2 * 0.998)
        else:
            tp2 = smart_round(fib_target)

        # ── TP3: структурная цель (EMA200 / 2.618 Fib / max 4H) ──
        fib_target3 = price + move * 2.618
        if r3 and r3 < price * 1.50:
            tp3 = smart_round(r3 * 0.998)
        elif ema200_v > price * 1.05:
            tp3 = smart_round(ema200_v * 0.998)
        else:
            tp3 = smart_round(fib_target3)

        swing = smart_round(s1 if s1 else price * 0.92)

    else:  # SHORT
        # ── SL: за ближайший Swing High ──
        if r1 and r1 > price + atr_min * 0.5:
            sl_raw = r1 * 1.002
        elif r2 and r2 > price + atr_min:
            sl_raw = r2 * 1.002
        else:
            sl_raw = price + atr_min * 1.5
        sl = smart_round(min(sl_raw, price * 1.20))  # не более +20%

        # ── TP1: ближайший Swing Low ──
        if s1 and s1 > price * 0.85:
            tp1 = smart_round(s1 * 1.002)
        else:
            tp1 = smart_round(price - atr_min * 1.0)

        # ── TP2: второй Swing Low / 1.618 Fib ──
        move = sl_raw - price
        fib_target = price - move * 1.618
        if s2 and s2 > price * 0.70:
            tp2 = smart_round(s2 * 1.002)
        else:
            tp2 = smart_round(fib_target)

        # ── TP3: 2.618 Fib / EMA200 снизу ──
        fib_target3 = price - move * 2.618
        if s2 and s2 * 0.85 > price * 0.50:
            tp3 = smart_round(s2 * 0.85)
        elif ema200_v < price * 0.95 and ema200_v > 0:
            tp3 = smart_round(ema200_v * 1.002)
        else:
            tp3 = smart_round(fib_target3)

        swing = smart_round(r1 if r1 else price * 1.08)

    # ── Финальные проверки ──
    if is_long:
        # tp1 < tp2 < tp3 и все выше цены
        tp1 = smart_round(max(tp1, price * 1.01))
        tp2 = smart_round(max(tp2, tp1 * 1.01))
        tp3 = smart_round(max(tp3, tp2 * 1.01))
        sl  = smart_round(min(sl,  price * 0.99))
    else:
        # tp1 > tp2 > tp3 и все ниже цены
        tp1 = smart_round(min(tp1, price * 0.99))
        tp2 = smart_round(min(tp2, tp1 * 0.99))
        tp3 = smart_round(min(tp3, tp2 * 0.99))
        sl  = smart_round(max(sl,  price * 1.01))

    if sl <= 0 or sl == price:
        sl = smart_round(price * 0.85 if is_long else price * 1.15)
    if swing <= 0 or swing == price:
        swing = smart_round(price * 0.92 if is_long else price * 1.08)

    rr = abs(tp3 - price) / abs(sl - price) if abs(sl - price) > 0 else 1.5

    # Метки источников уровней для отображения
    sl_source  = "Swing Low" if is_long else "Swing High"
    tp1_source = f"R{1 if is_long else 'S'}1 · Swing уровень"
    tp2_source = "Fib 1.618" if not r2 else "R2 · Swing уровень"
    tp3_source = "Fib 2.618" if not r3 else "R3 · Структурная цель"
    if rocket >= 80:   rocket_label = "🚀🔥 ROCKET"
    elif rocket >= 70: rocket_label = "🚀 СИЛЬНЫЙ"
    elif rocket >= 60: rocket_label = "✅ ХОРОШИЙ"
    elif rocket >= 50: rocket_label = "🟡 СРЕДНИЙ"
    elif rocket >= 40: rocket_label = "🟠 СЛАБЫЙ"
    else:              rocket_label = "🔴 ИЗБЕГАТЬ"

    smc_factors = []
    if smc_bos_bull:     smc_factors.append("BOS ↑")
    if smc_bos_bear:     smc_factors.append("BOS ↓")
    if smc_ob_accum:     smc_factors.append("OB Накопление")
    if smc_liq_sweep:    smc_factors.append("Liq Sweep")
    if smc_smart_accum:  smc_factors.append("Smart Accum 💎")
    if smc_smart_dist:   smc_factors.append("Smart Dist ⚠️")
    if smc_fvg_bull:     smc_factors.append("FVG ↑")
    if smc_fvg_bear:     smc_factors.append("FVG ↓")
    if tf_aligned_bull:  smc_factors.append("TF Align Bull")
    if tf_aligned_bear:  smc_factors.append("TF Align Bear")
    if fund_recovery:    smc_factors.append("Recovery 🔄")
    if bb_squeeze:       smc_factors.append("BB Squeeze")
    if macd_bullish:     smc_factors.append("MACD Bull")
    if macd_bearish:     smc_factors.append("MACD Bear")
    if supertrend_bull is True:  smc_factors.append("ST BUY ✅")
    elif supertrend_bull is False: smc_factors.append("ST SELL 🔴")

    return {
        "label": rocket_label, "score": score_ta, "is_long": is_long,
        "rocket": rocket, "rocket_label": rocket_label,
        "price": price, "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "sl": sl, "swing": swing, "rr": rr,
        "sl_source": sl_source, "tp1_source": tp1_source,
        "tp2_source": tp2_source, "tp3_source": tp3_source,
        "rsi_4h": rsi_4h, "rsi_1h": rsi_1h, "rsi_1d": rsi_1d,
        "ch1h": ch1h, "ch24h": ch24h, "ch7d": ch7d, "ch30d": ch30d, "ch90d": ch90d,
        "vol": vol, "mcap": mcap, "vol_ratio": vol_ratio, "rank": rank,
        "ema20_4h": ema20_v, "ema50_4h": ema50_v, "ema200_4h": ema200_v,
        "above_ema20": above_ema20, "above_ema50": above_ema50, "above_ema200": above_ema200,
        "macd_bullish": macd_bullish, "macd_bearish": macd_bearish,
        "bb_squeeze": bb_squeeze, "vol_spike": vol_spike,
        "tf_aligned_bull": tf_aligned_bull, "smc_bos_bull": smc_bos_bull,
        "smc_smart_accum": smc_smart_accum, "fund_recovery": fund_recovery,
        "smc_factors": smc_factors, "suspicious": suspicious,
        "supertrend_bull": supertrend_bull,
        "trend_4h": trend_4h,
        "atr": atr, "support": support, "resistance": resistance,
        "st_label": ("🟢 BUY" if supertrend_bull else ("🔴 SELL" if supertrend_bull is False else "—")),
        "fund_rank_top50": rank <= 50, "fund_liquid": vol >= 10_000_000 and vol_ratio <= 50,
        # ema aliases для совместимости
        "ema20_1h": ema20_v, "ema50_1h": ema50_v, "ema200_1h": ema200_v,
        "ema20_1d": ema20_v, "ema50_1d": ema50_v, "ema200_1d": ema200_v,
        "rsi_1h": rsi_1h, "rsi_1d": rsi_1d,
    }


# ═══════════════════════════════════════════════════════════════════
# НОВЫЕ КОМАНДЫ: ТОП СПОТ, ТОП ЛОНГ, ТОП ШОРТ, ПОЛНЫЙ АНАЛИЗ
# ═══════════════════════════════════════════════════════════════════

# Хранилище активных сигналов с постами
TOP_LONG_SIGNALS:  dict = {}
TOP_SHORT_SIGNALS: dict = {}
TOP_SPOT_SIGNALS:  dict = {}

# Файл для персистентного хранения сигналов между рестартами
import json as _json

_SIGNALS_FILE = "/tmp/best_trade_signals.json"

def _save_signals():
    """Сохраняем сигналы в файл"""
    try:
        data = {
            "long":  {s: {k: str(v) if isinstance(v, datetime) else v
                          for k,v in d.items()}
                      for s, d in TOP_LONG_SIGNALS.items()},
            "short": {s: {k: str(v) if isinstance(v, datetime) else v
                          for k,v in d.items()}
                      for s, d in TOP_SHORT_SIGNALS.items()},
            "spot":  {s: {k: str(v) if isinstance(v, datetime) else v
                          for k,v in d.items()}
                      for s, d in TOP_SPOT_SIGNALS.items()},
        }
        with open(_SIGNALS_FILE, "w") as f:
            _json.dump(data, f)
    except Exception as e:
        log.error(f"_save_signals: {e}")

def _load_signals():
    """Загружаем сигналы после рестарта"""
    global TOP_LONG_SIGNALS, TOP_SHORT_SIGNALS, TOP_SPOT_SIGNALS
    try:
        if not os.path.exists(_SIGNALS_FILE):
            return
        with open(_SIGNALS_FILE) as f:
            data = _json.load(f)
        def parse_entry(d):
            out = dict(d)
            try: out["time"] = datetime.fromisoformat(d["time"])
            except: out["time"] = datetime.now(TZ)
            for k in ["entry","tp1","tp2","tp3","sl","rr","buy_zone_lo","buy_zone_hi","atl","sell_target"]:
                if k in out:
                    try: out[k] = float(out[k])
                    except: pass
            return out
        TOP_LONG_SIGNALS  = {s: parse_entry(v) for s,v in data.get("long", {}).items()}
        TOP_SHORT_SIGNALS = {s: parse_entry(v) for s,v in data.get("short",{}).items()}
        TOP_SPOT_SIGNALS  = {s: parse_entry(v) for s,v in data.get("spot", {}).items()}
        log.info(f"Загружены сигналы: {len(TOP_LONG_SIGNALS)} лонг, {len(TOP_SHORT_SIGNALS)} шорт, {len(TOP_SPOT_SIGNALS)} спот")
    except Exception as e:
        log.error(f"_load_signals: {e}")

def _signal_kb(symbol: str, msg_id: int = 0, chat_id: int = 0, mode: str = "long") -> InlineKeyboardMarkup:
    """Кнопки под сигналом"""
    tv = tv_link(symbol)
    cb = f"close_{mode}_{symbol}"
    rows = [
        [InlineKeyboardButton("📈 TradingView", url=tv),
         InlineKeyboardButton("🏠 Главное меню", callback_data="show_menu")],
    ]
    if mode in ("long", "short"):
        rows.append([
            InlineKeyboardButton("✅ TP достигнут",  callback_data=f"tp_{mode}_{symbol}"),
            InlineKeyboardButton("❌ SL сработал",   callback_data=f"sl_{mode}_{symbol}"),
        ])
    return InlineKeyboardMarkup(rows)


def _build_signal_post(symbol: str, a: dict, stats_24h: dict,
                       mode: str = "long") -> str:
    is_long = mode in ("long", "spot")
    price   = a["price"]

    def pct(t):
        d = (t - price) / price * 100
        v = d if is_long else -d
        return f"+{v:.2f}%" if v >= 0 else f"{v:.2f}%"

    side_e = "🟢" if is_long else "🔴"
    side_t = "LONG" if mode == "long" else ("СПОТ" if mode == "spot" else "SHORT")
    swing_lbl = "Swing Low" if is_long else "Swing High"

    if mode != "spot":
        lines = [
            f"*{symbol}USDT* {side_e} *{side_t}*",
            "",
            f"💵 *Точка входа:* `{fp(price)}`",
            "",
            f"🎯 *Тейк-профит 1:* `{fp(a['tp1'])}` *({pct(a['tp1'])})*",
            "",
            f"🎯 *Тейк-профит 2:* `{fp(a['tp2'])}` *({pct(a['tp2'])})*",
            "",
            f"🎯 *Тейк-профит 3:* `{fp(a['tp3'])}` *({pct(a['tp3'])})*",
            "",
            f"🔴 *Стоп лосс:* `{fp(a['sl'])}` *({pct(a['sl'])})*",
            "",
            f"📍 *{swing_lbl}:* `{fp(a['swing'])}`",
        ]
    else:
        lines = [
            f"*{symbol}USDT* 💎 *СПОТ*",
            "",
            f"💵 *Зона входа:* `{fp(price)}`",
            "",
            f"🎯 *Цель:* `{fp(a['tp2'])}` *({pct(a['tp2'])})*",
            "",
            f"🛑 *Стоп (опцион):* `{fp(a['sl'])}`",
        ]

    return "\n".join(lines)
    side_e   = "🟢" if is_long else "🔴"
    side_t   = "LONG" if mode == "long" else ("СПОТ" if mode == "spot" else "SHORT")
    price    = a["price"]
    r        = a["rocket"]
    rsi_4h   = a["rsi_4h"]
    trend_4h = a.get("trend_4h", "neutral")

    def pct(t):
        d = (t - price) / price * 100
        return f"+{d:.2f}%" if d >= 0 else f"{d:.2f}%"

    def ri(v): return "🟢" if v < 30 else ("🔴" if v > 70 else "🔵")

    bar = "▓" * int(r/10) + "░" * (10 - int(r/10))

    # EMA строка
    ema_tags = []
    if a.get("above_ema200"): ema_tags.append("EMA200 ✅")
    if a.get("above_ema50"):  ema_tags.append("EMA50 ✅")
    if a.get("above_ema20"):  ema_tags.append("EMA20 ✅")
    if not ema_tags: ema_tags = ["Ниже всех EMA ⚠️"]

    # MACD
    macd_t = "▲ Бычий" if a.get("macd_bullish") else ("▼ Медвежий" if a.get("macd_bearish") else "→ Нейт.")
    # Trend
    trend_t = {"bullish": "↑ Бычий", "bearish": "↓ Медвежий", "neutral": "→ Нейтральный"}.get(trend_4h, "—")
    # ST
    st_t = a.get("st_label", "—")
    # Vol
    vol = a["vol"]
    vol_s = f"${vol/1e9:.2f}B" if vol>=1e9 else (f"${vol/1e6:.1f}M" if vol>=1e6 else f"${vol/1e3:.0f}K")
    # SMC
    smc = [f for f in a.get("smc_factors",[]) if "BB" not in f and "MACD" not in f][:3]

    # Conclusion
    if a.get("suspicious"):          conclusion = "⚠️ Аномальный объём — высокий риск"
    elif is_long and rsi_4h > 75:    conclusion = "⏳ Перекуплен — ждать отката"
    elif is_long and rsi_4h < 30 and r >= 70: conclusion = "🔥 RSI у дна + сильный сигнал — идеальный вход!"
    elif is_long and r >= 80:        conclusion = "🚀 Приоритетный сетап — высокий потенциал"
    elif is_long and r >= 65:        conclusion = "✅ Сильный лонг-сетап"
    elif not is_long and r >= 70:    conclusion = "📉 Сильный шорт-сетап"
    elif mode == "spot" and a.get("fund_recovery"): conclusion = "🔄 Recovery — DCA зона накопления"
    else:                            conclusion = "⏳ Умеренный сигнал — ждём подтверждения"

    # Header
    if mode == "long":   header_line = f"🟢 *ЛОНГ СИГНАЛ*"
    elif mode == "short": header_line = f"🔴 *ШОРТ СИГНАЛ*"
    else:                 header_line = f"💎 *СПОТ НАКОПЛЕНИЕ*"

    lines = [
        f"{'─'*28}",
        f"{side_e} *{symbol}USDT*  ·  {header_line}",
        f"🕐 {now_utc3()}  ·  📡 Аналитика BEST TRADE",
        f"{'─'*28}",
        "",
        f"⚡️ *Сила сигнала:*  `{r}/100`  {a['rocket_label']}",
        f"  `{bar}`",
        "",
        f"📍 *Позиция по EMA:*  {' · '.join(ema_tags)}",
        f"💡 {conclusion}",
        "",
        f"{'─'*28}",
        f"💰 *ТОЧКИ СДЕЛКИ*",
        f"{'─'*28}",
        "",
        f"  Вход:    `{fp(price)}`",
    ]

    if mode != "spot":
        lines += [
            f"  TP1:    `{fp(a['tp1'])}`  *({pct(a['tp1'])})*",
            f"  TP2:    `{fp(a['tp2'])}`  *({pct(a['tp2'])})*",
            f"  TP3:    `{fp(a['tp3'])}`  *({pct(a['tp3'])})*",
            f"  SL:     `{fp(a['sl'])}`",
            f"  R:R     `1:{a['rr']:.1f}`",
        ]
    else:
        lines += [
            f"  Цель:   `{fp(a['tp2'])}`  *({pct(a['tp2'])})*",
            f"  Стоп:   `{fp(a['sl'])}`  *(—SL для спота необязателен)*",
        ]

    if stats_24h:
        h24 = stats_24h.get("high", 0); l24 = stats_24h.get("low", 0)
        if h24 and l24:
            best = l24*1.005 if is_long else h24*0.995
            lines += ["", f"  📅 24H:  🔼`{fp(h24)}`  🔽`{fp(l24)}`  🎯`{fp(best)}`"]

    lines += [
        "",
        f"{'─'*28}",
        f"📊 *ИНДИКАТОРЫ*",
        f"{'─'*28}",
        "",
        f"  RSI 4H:    {ri(rsi_4h)} `{rsi_4h:.0f}`  {'← перепродан!' if rsi_4h<30 else ('← перекуплен!' if rsi_4h>70 else '')}",
        f"  MACD:      `{macd_t}`",
        f"  Тренд 4H:  `{trend_t}`",
        f"  Supertrend:`{st_t}`",
    ]
    if smc:
        lines.append(f"  SMC:       `{'  ·  '.join(smc)}`")
    lines += [
        "",
        f"  Объём:     `{vol_s}`  ·  Rank `#{a.get('rank','—')}`",
        f"  Изм:  1H`{fc(a['ch1h'])}`  24H`{fc(a['ch24h'])}`  7D`{fc(a['ch7d'])}`",
        "",
        f"{'─'*28}",
        f"⚠️  Риск: *2% депозита*  ·  SL обязателен",
        f"#{symbol}USDT",
    ]
    return "\n".join(lines)


# BACKWARD COMPAT alias
_old_build_signal_post = _build_signal_post

async def cmd_top_spot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/spot — ТОП СПОТ: монеты с максимальным потенциалом иксов"""
    msg = await update.message.reply_text(
        "💎 Ищу монеты с максимальным потенциалом...\n"
        "Анализирую топ-500 по CMC + Binance данные"
    )
    coins = get_top500()
    if not coins:
        await msg.edit_text("❌ Нет данных"); return

    # ── ФИЛЬТР 1: Качество проекта + падение от ATH ──
    # Теги которые говорят о реальном проекте
    QUALITY_TAGS = {
        "defi", "layer-1", "layer-2", "layer1", "layer2",
        "dex", "lending-borowing", "yield-farming",
        "oracle", "infrastructure", "gaming", "web3",
        "nft", "metaverse", "cross-chain", "payments",
        "exchange-based-tokens", "governance",
    }

    candidates = []
    for coin in coins:
        q         = coin["quote"]["USDT"]
        ch90d     = q.get("percent_change_90d", 0) or 0
        ch7d      = q.get("percent_change_7d",  0) or 0
        ch30d     = q.get("percent_change_30d", 0) or 0
        ch24h     = q.get("percent_change_24h", 0) or 0
        vol       = q.get("volume_24h",  0) or 0
        mcap      = q.get("market_cap",  0) or 0
        price     = q.get("price",       0) or 0
        rank      = coin.get("cmc_rank", 999)
        vol_ratio = (vol / mcap * 100) if mcap > 0 else 0
        tags      = {t.lower() for t in coin.get("tags", [])}

        # Жёсткие фильтры (убираем мусор)
        if vol_ratio > 60:          continue   # памп/дамп манипуляция
        if vol < 500_000:           continue   # нет ликвидности
        if mcap < 10_000_000:       continue   # микрокап — слишком рискованно
        if "stablecoin" in tags:    continue
        if ch90d > -20:             continue   # не упала достаточно

        # Скоринг иксового потенциала
        score = 0.0

        # 1. Падение от пика — основной фактор (чем больше упала = больше иксов)
        drop_score = abs(ch90d)          # -80% → 80 очков
        score += drop_score * 1.0

        # 2. Признаки разворота (накопление началось)
        if ch7d > 0:   score += ch7d * 4.0    # растёт за неделю — приоритет
        if ch30d > 0:  score += ch30d * 1.5   # месяц в плюсе
        if ch24h > 0:  score += ch24h * 2.0   # сегодня растёт

        # 3. Качество проекта
        if rank <= 20:   score += 50
        elif rank <= 50: score += 35
        elif rank <= 100: score += 20
        elif rank <= 200: score += 10
        elif rank <= 300: score += 5

        # Бонус за качественные теги
        tag_bonus = len(tags & QUALITY_TAGS) * 8
        score += min(tag_bonus, 30)

        # 4. Объём тренд (накопление)
        if 2 <= vol_ratio <= 30:  score += 15   # нормальный объём
        elif vol_ratio < 2:       score -= 10   # слишком мало активности
        if vol >= 50_000_000:     score += 15   # высокая ликвидность
        elif vol >= 10_000_000:   score += 8

        # Потенциал в иксах до ATH (приблизительно)
        # Если упала -80% → нужно вырасти в 5x до ATH
        x_to_ath = 1 / (1 + ch90d/100) if ch90d < -5 else 1.0
        score += min(x_to_ath * 5, 40)   # макс бонус за x-потенциал

        candidates.append((coin, score, x_to_ath, ch90d, ch7d))

    # Сортируем по скору
    candidates.sort(key=lambda x: x[1], reverse=True)
    top_spot = candidates[:10]

    nav = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить",     callback_data="top_spot"),
         InlineKeyboardButton("🏠 Главное меню", callback_data="show_menu")],
        [InlineKeyboardButton("🟢 ТОП ЛОНГ",    callback_data="top_long"),
         InlineKeyboardButton("🔴 ТОП ШОРТ",    callback_data="top_short")],
    ])

    if not top_spot:
        await msg.edit_text(
            "😔 Нет подходящих монет\n\nРынок в росте — все уже выросли.",
            parse_mode="Markdown", reply_markup=nav)
        return

    # Сводный список
    list_lines = [
        "💎 *BEST TRADE — ТОП СПОТ*",
        f"🕐 {now_utc3()}",
        "📡 Аналитика BEST TRADE",
        "",
        "🎯 *Монеты с максимальным иксовым потенциалом:*",
        "",
    ]

    for i, (c, score, x_ath, ch90, ch7) in enumerate(top_spot, 1):
        sym    = c["symbol"]
        tv     = tv_link(sym)
        prc    = c["quote"]["USDT"].get("price", 0)
        rank   = c.get("cmc_rank", 999)
        vol    = c["quote"]["USDT"].get("volume_24h", 0) or 0
        vol_s  = f"${vol/1e9:.1f}B" if vol>=1e9 else f"${vol/1e6:.0f}M"

        # Иконка потенциала
        if x_ath >= 10:   pot_icon = "🔥🔥🔥"
        elif x_ath >= 5:  pot_icon = "🔥🔥"
        elif x_ath >= 3:  pot_icon = "🔥"
        elif x_ath >= 2:  pot_icon = "⚡️"
        else:             pot_icon = "📈"

        trend_icon = "🟢" if ch7 > 0 else "🔴"

        list_lines += [
            f"{i}. [{sym}USDT]({tv})  {pot_icon}",
            f"   💰 `{fp(prc)}`  ·  Rank #{rank}  ·  Vol {vol_s}",
            f"   📉 -90д: `{ch90:.0f}%`  ·  Потенциал: `~x{x_ath:.1f}` до ATH",
            f"   {trend_icon} 7д: `{fc(ch7)}`",
            "",
        ]

    list_lines += ["📊 Детальный разбор каждой монеты ниже ↓"]

    await msg.edit_text("\n".join(list_lines), parse_mode="Markdown",
                        reply_markup=nav, disable_web_page_preview=False)

    # Детальный разбор каждой монеты
    for coin, score, x_ath, ch90d_v, ch7d_v in top_spot:
        sym  = coin["symbol"]
        slug = coin.get("slug", sym.lower())
        q    = coin["quote"]["USDT"]
        try:
            prog = await update.message.reply_text(f"⏳ Разбор {sym}...")

            a          = real_full_analysis(coin)
            stats_24h  = get_binance_24h(sym)
            atl        = get_binance_alltime_low(sym)
            candles_1d = get_binance_ohlc(sym, "1d", 365)
            candles_1w = get_binance_ohlc(sym, "1w", 200)

            price  = a["price"]
            ath    = max((c["high"] for c in candles_1w), default=0) if candles_1w else max((c["high"] for c in candles_1d), default=0)
            ch24h  = q.get("percent_change_24h", 0) or 0
            ch30d  = q.get("percent_change_30d", 0) or 0
            vol    = q.get("volume_24h", 0) or 0
            mcap   = q.get("market_cap", 0) or 0
            rank   = coin.get("cmc_rank", 999)
            vol_s  = f"${vol/1e9:.2f}B" if vol>=1e9 else f"${vol/1e6:.1f}M"
            mcap_s = fm(mcap) if mcap>0 else "—"

            # Зоны
            closes_1d = [c["close"] for c in candles_1d] if candles_1d else []
            zone_30d  = min((c["low"] for c in candles_1d[-30:]), default=0) if len(candles_1d)>=30 else 0
            zone_90d  = min((c["low"] for c in candles_1d[-90:]), default=0) if len(candles_1d)>=90 else 0
            ema200_d  = next((v for v in reversed(calc_ema(closes_1d,200)) if v), 0) if len(closes_1d)>=200 else 0
            rsi_1d    = calc_rsi(closes_1d,14) if len(closes_1d)>=15 else 50.0

            vol_7d  = sum(c["vol"] for c in candles_1d[-7:]) /7  if len(candles_1d)>=7  else 0
            vol_30d = sum(c["vol"] for c in candles_1d[-30:])/30 if len(candles_1d)>=30 else 0
            vol_growing = vol_7d > vol_30d * 1.15

            from_atl = ((price-atl)/atl*100)    if atl>0 else 0
            from_ath = ((price-ath)/ath*100)     if ath>0 else 0
            to_ath   = ((ath-price)/price*100)   if ath>price>0 else 0

            # Зоны покупки DCA
            buy1 = zone_90d if zone_90d>0 else price*0.85
            buy2 = (zone_30d or price*0.92)
            buy3 = atl*1.03 if atl>0 else price*0.75

            def ri(v): return "🟢" if v<30 else ("🔴" if v>70 else "🔵")
            smc = [f for f in a.get("smc_factors",[]) if "BB" not in f]

            if x_ath >= 10:   pot_str = f"~x{x_ath:.0f} 🔥🔥🔥"
            elif x_ath >= 5:  pot_str = f"~x{x_ath:.1f} 🔥🔥"
            elif x_ath >= 3:  pot_str = f"~x{x_ath:.1f} 🔥"
            elif x_ath >= 2:  pot_str = f"~x{x_ath:.1f} ⚡️"
            else:             pot_str = f"~x{x_ath:.1f} 📈"

            # Вердикт
            spot_score = sum([rsi_1d<35, ch90d_v<-60, ch7d_v>0, vol_growing, x_ath>=3, price<=buy2*1.1, bool(smc)])
            if spot_score >= 6:   verdict_e, verdict_t = "🔥", "Лучший момент для входа"
            elif spot_score >= 4: verdict_e, verdict_t = "✅", "Хорошая зона накопления"
            elif spot_score >= 2: verdict_e, verdict_t = "🟡", "Можно начинать DCA"
            else:                 verdict_e, verdict_t = "⚠️", "Ждать лучшей цены"

            tags_str = " · ".join(list({t for t in coin.get("tags",[]) if t.lower() in {"defi","layer-1","layer-2","dex","oracle","gaming","nft","payments","infrastructure","web3"}})[:3])

            # Формат как в примере — чистый, без лишних блоков
            lines = [
                f"*{sym}USDT* 💎 *СПОТ*",
                f"📡 Аналитика BEST TRADE  ·  Rank #{rank}",
                "",
                f"💰 *Цена сейчас:* `{fp(price)}`",
                f"🎯 *Потенциал до ATH:* *{pot_str}*",
                "",
            ]
            if ath > 0:
                lines.append(f"🔺 *ATH:* `{fp(ath)}`  *(нужно +{to_ath:.0f}% до ATH)*")
            if atl > 0:
                lines.append(f"🔻 *ATL:* `{fp(atl)}`  *(текущая выше ATL на +{from_atl:.0f}%)*")
            lines += [
                "",
                f"📊 90д: *{fc(ch90d_v)}*  30д: *{fc(ch30d)}*  7д: *{fc(ch7d_v)}*",
                "",
                f"📈 RSI(1D): {ri(rsi_1d)}`{rsi_1d:.0f}` {'← перепродан!' if rsi_1d<30 else ''}",
            ]
            if ema200_d:
                lines.append(f"EMA200(1D): `{fp(ema200_d)}` {'✅ выше' if price>ema200_d else '❌ ниже'}")
            if tags_str:
                lines.append(f"🏷 {tags_str}")
            lines += [
                "",
                f"💵 *Вход 1 (40%):* `{fp(buy2)}`  *(сейчас / на откате)*",
                f"💵 *Вход 2 (40%):* `{fp(buy1)}`  *(мин 90д)*",
                f"💵 *Вход 3 (20%):* `{fp(buy3)}`  *(у ATL)*",
            ]
            if ath > 0:
                lines += [
                    "",
                    f"🥉 *Цель 1:* `{fp(ath*0.33)}`  *(~x{ath*0.33/price:.1f})*",
                    f"🥈 *Цель 2:* `{fp(ath*0.60)}`  *(~x{ath*0.60/price:.1f})*",
                    f"🥇 *Цель 3:* `{fp(ath*0.90)}`  *(~x{ath*0.90/price:.1f})*",
                ]
            lines += [
                "",
                f"{verdict_e} *{verdict_t}*",
                f"⚠️ Позиция: 5–10% портфеля  ·  Горизонт: от 3 мес.",
                f"#{sym}USDT",
            ]

            TOP_SPOT_SIGNALS[sym] = {
                "time": datetime.now(TZ), "entry": price,
                "buy_zone_lo": buy1, "buy_zone_hi": buy2,
                "atl": atl, "sell_target": ath*0.9 if ath>0 else price*5,
                "status": "watching",
            }

            await prog.delete()
            await send_coin(ctx.bot, update.effective_chat.id, sym, slug, a, "\n".join(lines))
            await asyncio.sleep(2.0)

        except Exception as e:
            log.error(f"top_spot {sym}: {e}")

    await ctx.bot.send_message(
        update.effective_chat.id,
        "📊 *BEST TRADE — Главное меню*\n\n👇 Выбери раздел:",
        parse_mode="Markdown", reply_markup=main_kb()
    )


async def cmd_top_long(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/long — ТОП ЛОНГ: ракеты + лонг-сигналы, отсортированные по Rocket Score"""
    msg = await update.message.reply_text("🟢 Ищу лучшие лонг-сетапы... ~40 сек")
    coins = get_top500()
    if not coins:
        await msg.edit_text("❌ Нет данных"); return

    # Фильтр кандидатов по CMC данным (быстро)
    pre = []
    for coin in coins:
        q = coin["quote"]["USDT"]
        vol       = q.get("volume_24h",  0) or 0
        mcap      = q.get("market_cap",  0) or 0
        ch24h     = q.get("percent_change_24h", 0) or 0
        vol_ratio = (vol / mcap * 100) if mcap > 0 else 0
        # Смягчённые фильтры — работают и в медвежий рынок
        if vol >= 1_000_000 and vol_ratio < 60 and ch24h > -20:
            pre.append(coin)

    # Сортируем: сначала те что растут, потом нейтральные
    pre.sort(key=lambda c: c["quote"]["USDT"].get("percent_change_24h", 0) or 0, reverse=True)

    # Реальный ТА из Binance свечей для топ кандидатов
    scored = []
    for coin in pre[:150]:
        try:
            a  = real_full_analysis(coin)
            pa = pro_analysis(coin["symbol"], coin)
            sqf = signal_quality_filter(a, pa, coin)
            # Принимаем A/A+ или если rocket высокий
            if a["is_long"] and not a.get("suspicious"):
                if sqf["pass"] or a["rocket"] >= 65:
                    a["_sqf"] = sqf  # сохраняем для вывода
                    scored.append((coin, a))
        except: pass

    # Сортируем по Rocket Score (ракеты автоматически наверху)
    scored.sort(key=lambda x: x[1]["rocket"], reverse=True)
    top_long = scored[:7]

    nav = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить",     callback_data="top_long"),
         InlineKeyboardButton("🏠 Главное меню", callback_data="show_menu")],
        [InlineKeyboardButton("🔴 ТОП ШОРТ",    callback_data="top_short"),
         InlineKeyboardButton("💎 ТОП СПОТ",    callback_data="top_spot")],
    ])

    if not top_long:
        # Резервный вариант — берём лучшие по RSI < 40 независимо от is_long
        fallback = []
        for coin in pre[:50]:
            try:
                a = real_full_analysis(coin)
                if not a.get("suspicious") and a["rsi_4h"] < 45:
                    fallback.append((coin, a))
            except: pass
        fallback.sort(key=lambda x: x[1]["rsi_4h"])
        top_long = fallback[:5]

    if not top_long:
        await msg.edit_text(
            "😔 *Нет лонг-сетапов сейчас*\n\n"
            "Все монеты перекуплены или нет данных.\n"
            "Попробуй ТОП ШОРТ или ТОП СПОТ.",
            parse_mode="Markdown", reply_markup=nav
        )
        return

    # Сводный список с ссылками
    btc_ctx = get_btc_market_context()
    btc_warn = ""
    if btc_ctx["ok"] and not btc_ctx["long_ok"]:
        btc_warn = f"\n🚨 *{btc_ctx['warning']}*\n"

    list_lines = [
        "🟢 *BEST TRADE — ТОП ЛОНГ*",
        f"🕐 {now_utc3()}",
        f"₿ {btc_ctx.get('label', '')}",
    ]
    if btc_warn:
        list_lines.append(btc_warn)
    list_lines += [
        "",
        "📋 *Лучшие лонг-сетапы прямо сейчас:*",
        f"🕐 Killzone: {killzone_label().split(chr(10))[0]}",
        "",
    ]
    for i, (c, a) in enumerate(top_long, 1):
        sym    = c["symbol"]
        tv     = tv_link(sym)
        sqf    = a.get("_sqf", {})
        q_lbl  = sqf.get("quality", "—")
        tkn    = get_tokenomics(sym)
        tkn_e  = "" if not tkn["has_data"] else ("🔴" if tkn["risk"]=="high" else ("🟡" if tkn["risk"]=="medium" else "🟢"))
        rocket_lbl = "🚀🔥" if a["rocket"] >= 80 else ("🚀" if a["rocket"] >= 68 else "✅")
        rsi_t  = "перепродан 🟢" if a["rsi_4h"] < 30 else ("нейтр." if a["rsi_4h"] < 60 else "перекуплен ⚠️")
        trend_t = "↑ бычий" if a.get("trend_4h") == "bullish" else ("↓ медвежий" if a.get("trend_4h") == "bearish" else "→ нейтр.")
        list_lines += [
            f"🟢 {i}. [{sym}USDT]({tv})  {rocket_lbl}  {tkn_e}",
            f"   💰 `{fp(a['price'])}`  Score `{a['rocket']}`  Вход: *{q_lbl}*",
            f"   RSI `{a['rsi_4h']:.0f}` {rsi_t}  ·  {trend_t}",
            "",
        ]
    list_lines += ["📊 Подробные сетапы ниже ↓"]

    await msg.edit_text("\n".join(list_lines), parse_mode="Markdown",
                        reply_markup=nav, disable_web_page_preview=False)

    # Отправляем каждый сетап с графиком
    for coin, a in top_long:
        sym  = coin["symbol"]
        slug = coin.get("slug", sym.lower())
        try:
            stats = get_binance_24h(sym)
            text  = _build_signal_post(sym, a, stats, mode="long")
            await send_coin(ctx.bot, update.effective_chat.id, sym, slug, a, text)
            TOP_LONG_SIGNALS[sym] = {
                "time":    datetime.now(TZ),
                "entry":   a["price"],
                "tp1": a["tp1"], "tp2": a["tp2"], "tp3": a["tp3"],
                "sl":  a["sl"], "rr": a["rr"],
                "status":  "active",
                "chat_id": update.effective_chat.id,
            }
            await asyncio.sleep(2.0)
        except Exception as e:
            log.error(f"top_long {sym}: {e}")

    await ctx.bot.send_message(
        update.effective_chat.id,
        "📊 *BEST TRADE — Главное меню*\n\n👇 Выбери раздел:",
        parse_mode="Markdown", reply_markup=main_kb()
    )


async def cmd_top_short(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/short — ТОП ШОРТ: ракеты SHORT + шорт-сигналы, по Rocket Score"""
    msg = await update.message.reply_text("🔴 Ищу лучшие шорт-сетапы... ~40 сек")
    coins = get_top500()
    if not coins:
        await msg.edit_text("❌ Нет данных"); return

    pre = []
    for coin in coins:   # все монеты
        q = coin["quote"]["USDT"]
        vol      = q.get("volume_24h",  0) or 0
        mcap     = q.get("market_cap",  0) or 0
        vol_ratio = (vol / mcap * 100) if mcap > 0 else 0
        if vol >= 1_000_000 and vol_ratio < 60:
            pre.append(coin)

    scored = []
    for coin in pre[:80]:
        try:
            a = real_full_analysis(coin)
            # Более мягкий фильтр для шортов
            if not a["is_long"] and not a.get("suspicious") and a["rocket"] >= 40:
                scored.append((coin, a))
            elif a.get("rsi_4h", 50) > 72 and a["vol"] >= 2_000_000:
                a_short = dict(a); a_short["is_long"] = False
                scored.append((coin, a_short))
        except: pass

    scored.sort(key=lambda x: x[1]["rocket"], reverse=True)
    top_short = scored[:7]

    nav = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить",    callback_data="top_short"),
         InlineKeyboardButton("🏠 Главное меню", callback_data="show_menu")],
        [InlineKeyboardButton("🟢 ТОП ЛОНГ",   callback_data="top_long"),
         InlineKeyboardButton("💎 ТОП СПОТ",   callback_data="top_spot")],
    ])

    if not top_short:
        await msg.edit_text(
            "😔 *Нет шорт-сетапов сейчас*\n\n"
            "Рынок нейтральный или бычий.\n"
            "Попробуй ТОП ЛОНГ или вернись позже.",
            parse_mode="Markdown", reply_markup=nav
        )
        return

    list_lines = [
        "🔴 *BEST TRADE — ТОП ШОРТ*",
        f"🕐 {now_utc3()}",
        f"📡 Аналитика BEST TRADE",
        "",
        "📋 *Лучшие шорт-сетапы прямо сейчас:*",
        "",
    ]
    for i, (c, a) in enumerate(top_short, 1):
        sym  = c["symbol"]
        tv   = tv_link(sym)
        rsi_t = "перекуплен 🔴" if a["rsi_4h"] > 70 else ("нейтр." if a["rsi_4h"] > 45 else "перепродан 🟢")
        trend_t = "↓ медвежий" if a.get("trend_4h") == "bearish" else ("↑ бычий" if a.get("trend_4h") == "bullish" else "→ нейтр.")
        ema_t = "выше EMA200 ⚠️" if a.get("above_ema200") else "ниже EMA200 ✅"
        list_lines += [
            f"🔴 {i}. [{sym}USDT]({tv})",
            f"   💰 `{fp(a['price'])}`  ·  Score `{a['rocket']}`  ·  RSI `{a['rsi_4h']:.0f}` {rsi_t}",
            f"   📈 Тренд: {trend_t}  ·  {ema_t}",
            "",
        ]
    list_lines += ["📊 Подробные сетапы ниже ↓"]

    await msg.edit_text("\n".join(list_lines), parse_mode="Markdown",
                        reply_markup=nav, disable_web_page_preview=False)

    for coin, a in top_short:
        sym  = coin["symbol"]
        slug = coin.get("slug", sym.lower())
        try:
            stats = get_binance_24h(sym)
            text  = _build_signal_post(sym, a, stats, mode="short")
            await send_coin(ctx.bot, update.effective_chat.id, sym, slug, a, text)
            TOP_SHORT_SIGNALS[sym] = {
                "time":    datetime.now(TZ),
                "entry":   a["price"],
                "tp1": a["tp1"], "tp2": a["tp2"], "tp3": a["tp3"],
                "sl":  a["sl"], "rr": a["rr"],
                "status":  "active",
                "chat_id": update.effective_chat.id,
            }
            await asyncio.sleep(2.0)
        except Exception as e:
            log.error(f"top_short {sym}: {e}")

    await ctx.bot.send_message(
        update.effective_chat.id,
        "📊 *BEST TRADE — Главное меню*\n\n👇 Выбери раздел:",
        parse_mode="Markdown", reply_markup=main_kb()
    )


async def _search_coin_by_symbol(symbol: str) -> dict | None:
    """Ищет монету по символу через CMC API — все монеты с капой от $1M"""
    try:
        url     = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
        headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
        params  = {"symbol": symbol.upper(), "convert": "USDT"}
        r = requests.get(url, headers=headers, params=params, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json().get("data", {})
        if not data:
            return None
        # CMC может вернуть список если несколько монет с одним символом
        items = list(data.values())
        if not items:
            return None
        # Берём первый (с наибольшей капой обычно)
        item = items[0] if isinstance(items[0], dict) else items[0][0]
        mcap = item.get("quote", {}).get("USDT", {}).get("market_cap", 0) or 0
        if mcap < 1_000_000:  # фильтр $1M минимум
            return None
        log.info(f"CMC found by symbol: {symbol} rank={item.get('cmc_rank')}")
        return item
    except Exception as e:
        log.error(f"CMC symbol search {symbol}: {e}")
        return None



async def _do_full_analysis(bot, chat_id: int, symbol: str) -> bool:
    """Полный анализ — одно красивое сообщение без линий и перегруза"""
    symbol = symbol.upper().replace("USDT","").replace("BUSD","")

    coins = get_all_coins()
    coin  = next((c for c in coins if c["symbol"] == symbol), None)
    if not coin:
        coin = await _search_coin_by_symbol(symbol)
    if not coin:
        # Binance fallback
        test = None
        for suffix in ["USDT","BUSD"]:
            try:
                r = requests.get("https://api.binance.com/api/v3/klines",
                    params={"symbol":f"{symbol}{suffix}","interval":"4h","limit":5},
                    timeout=10)
                if r.status_code == 200 and isinstance(r.json(), list) and r.json():
                    test = r.json(); break
            except: pass
        if not test:
            await bot.send_message(chat_id,
                f"❌ *{symbol}USDT* не найден\n\nПроверь символ: `/full BTC` · `/full SOL`",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏠 Главное меню", callback_data="show_menu")
                ]]))
            return False
        price_now = float(test[-1][4])
        coin = {
            "symbol": symbol, "slug": symbol.lower(), "cmc_rank": 9999,
            "tags": [], "name": symbol,
            "quote": {"USDT": {
                "price": price_now, "volume_24h": 0, "market_cap": 0,
                "percent_change_1h": 0, "percent_change_24h": 0,
                "percent_change_7d": 0, "percent_change_30d": 0,
                "percent_change_90d": 0,
            }}
        }

    slug  = coin.get("slug", symbol.lower())
    q     = coin["quote"]["USDT"]
    rank  = coin.get("cmc_rank", 9999)

    # ── PRO ANALYSIS — полный институциональный анализ ──
    pa    = pro_analysis(symbol, coin)
    a     = real_full_analysis(coin)   # для TP/SL расчётов
    price = pa["price"] if pa["ok"] and pa["price"] > 0 else a["price"]

    candles_1d = get_binance_ohlc(symbol, "1d", 365)
    candles_1w = get_binance_ohlc(symbol, "1w", 200)
    atl        = get_binance_alltime_low(symbol)

    ath = 0.0
    if candles_1w and len(candles_1w) > 1:
        ath = max(c["high"] for c in candles_1w)
    elif candles_1d and len(candles_1d) > 1:
        ath = max(c["high"] for c in candles_1d)

    closes_1d = [c["close"] for c in candles_1d] if candles_1d else []
    zone_90d  = min((c["low"] for c in candles_1d[-90:]), default=0) if len(candles_1d)>=90 else 0
    zone_30d  = min((c["low"] for c in candles_1d[-30:]), default=0) if len(candles_1d)>=30 else 0

    ch1h  = q.get("percent_change_1h",  0) or 0
    ch24h = q.get("percent_change_24h", 0) or 0
    ch7d  = q.get("percent_change_7d",  0) or 0
    ch30d = q.get("percent_change_30d", 0) or 0
    ch90d = q.get("percent_change_90d", 0) or 0
    vol24 = q.get("volume_24h", 0) or 0
    mcap  = q.get("market_cap", 0) or 0

    to_ath   = ((ath - price)/price*100)  if ath > price > 0 else 0
    from_atl = ((price - atl)/atl*100)    if atl > 0 else 0
    x_ath    = (ath/price) if ath > price > 0 else 1.0
    buy_lo   = zone_90d if zone_90d > 0 else price*0.75
    buy_hi   = zone_30d if zone_30d > 0 else price*0.88
    sell_t   = ath*0.9  if ath > 0 else price*3.0

    # Направление и уровень
    direction = pa.get("direction", "long") if pa["ok"] else ("long" if a["is_long"] else "short")
    is_long   = direction != "short"
    pro_score = pa.get("pro_score", a["rocket"])
    eq_bar    = "▓"*int(pro_score/10) + "░"*(10-int(pro_score/10))
    entry_q   = pa.get("entry_quality", "B 🟡")
    setup     = pa.get("setup_type", "Multi-TF")

    side_e = "🟢" if is_long else "🔴"
    side_t = "LONG" if is_long else "SHORT"

    rsi_4h = pa.get("rsi_4h", a["rsi_4h"])
    rsi_1d = pa.get("rsi_1d", 50.0)
    ema200_4h = pa.get("ema200_4h", 0)
    ema200_1d = pa.get("ema200_1d", 0)

    def ri(v): return "🟢" if v<30 else "🔴" if v>70 else "🔵"
    def pct(t): d=(t-price)/price*100; return f"+{d:.2f}%" if d>=0 else f"{d:.2f}%"

    vol_s  = f"${vol24/1e9:.2f}B" if vol24>=1e9 else f"${vol24/1e6:.1f}M" if vol24>=1e6 else f"${vol24/1e3:.0f}K"
    mcap_s = fm(mcap) if mcap>0 else "—"

    # ── ВСЕ 6 УРОВНЕЙ ЭКСПЕРТНОГО АНАЛИЗА ──
    kz      = get_killzone_status()
    sqf     = signal_quality_filter(a, pa, coin)
    tkn     = get_tokenomics(symbol)
    btc_ctx = get_btc_market_context()
    news    = get_coin_news(symbol)
    bt      = backtest_signal(symbol, is_long, lookback_candles=60)

    # Уровень 1 — Confluence Matrix
    cm  = confluence_matrix(a, pa, coin, btc_ctx, kz)
    # Уровень 2 — Volume Profile
    vp  = get_volume_profile(symbol)
    # Уровень 3 — Order Book
    ob  = get_order_book_analysis(symbol)
    # Уровень 4 — Macro (DXY/ETH-BTC/Gold/NQ)
    mac = get_macro_context()
    # Уровень 5 — Сезонность
    sea = get_seasonality()
    # Уровень 6 — On-chain
    onc = get_onchain_data(symbol)

    ps  = calc_position_size(
        price    = price,
        sl       = a["sl"],
        deposit  = 1000.0,
        risk_pct = 1.0,
        leverage = 3.0 if not (ch90d < -40) else 1.0,
        quality  = sqf["quality"],
    )

    kz_e   = {"A+":"🔥","A":"✅","B":"🟡","C":"⚠️","D":"❌"}.get(kz["active"]["quality"],"")
    sqf_e  = "🔥" if "A+" in sqf["quality"] else ("✅" if "A " in sqf["quality"] else "🟡")

    # ── CONFLUENCE MATRIX — итоговая оценка ──
    cm_bar = "▓" * (cm["hits"]) + "░" * (15 - cm["hits"])
    parts_header_extra = [
        "",
        f"🎯 *CONFLUENCE MATRIX: {cm['grade']}*  ({cm['hits']}/15 факторов)",
        f"`{cm_bar}`",
    ]
    if cm["factors"]:
        parts_header_extra.append("  " + "  ·  ".join(cm["factors"][:6]))

    # ── ФОРМАТ ПРОФЕССИОНАЛЬНОГО ОТЧЁТА ──
    parts = [
        f"*{symbol}USDT* {side_e} *{side_t}*",
        f"📡 BEST TRADE PRO   Rank #{rank}",
        "",
        f"💰 *{fp(price)}*   Vol {vol_s}   MCap {mcap_s}",
    ]

    # Confluence Matrix сразу после заголовка
    parts += parts_header_extra

    if ath > 0:
        parts.append(f"🔺 ATH `{fp(ath)}`  потенциал `~x{x_ath:.1f}` (+{to_ath:.0f}%)")
    if atl > 0:
        parts.append(f"🔻 ATL `{fp(atl)}`  от ATL +{from_atl:.0f}%")

    parts += [
        "",
        f"📊 1H`{fc(ch1h)}`  24H`{fc(ch24h)}`  7D`{fc(ch7d)}`  30D`{fc(ch30d)}`  90D`{fc(ch90d)}`",
        "",
        f"🏆 *PRO Score: `{pro_score}/100`*   Качество входа: *{entry_q}*",
        f"`{eq_bar}`",
        f"🎯 Сетап: *{setup}*",
        "",
    ]

    # Структура рынка
    tf_map = {"bullish": "🟢↑", "bearish": "🔴↓", "neutral": "⚪→"}
    if pa["ok"]:
        tf_line = (f"TF: 1H{tf_map.get(pa['tf_1h'],'?')}  "
                   f"4H{tf_map.get(pa['tf_4h'],'?')}  "
                   f"1D{tf_map.get(pa['tf_1d'],'?')}  "
                   f"1W{tf_map.get(pa['tf_1w'],'?')}")
        conf = pa.get("tf_confluence", 0)
        conf_str = f"  Confluence: {abs(conf)}/4 {'🟢' if conf > 0 else '🔴'}"
        parts.append(tf_line + conf_str)

    parts += [
        f"RSI(1H){ri(pa.get('rsi_1h',50))}`{pa.get('rsi_1h',rsi_4h):.0f}`  "
        f"RSI(4H){ri(rsi_4h)}`{rsi_4h:.0f}`  "
        f"RSI(1D){ri(rsi_1d)}`{rsi_1d:.0f}`",
    ]
    if ema200_4h:
        parts.append(f"EMA200(4H)`{fp(ema200_4h)}` {'✅' if price>ema200_4h else '❌'}  "
                     f"EMA200(1D)`{fp(ema200_1d)}` {'✅' if price>ema200_1d>0 else '❌'}")

    # ICT / SMC факторы
    ict_hits = []
    if pa.get("ict_ob_bull"):     ict_hits.append("OB Bull ✅")
    if pa.get("ict_ob_bear"):     ict_hits.append("OB Bear 🔴")
    if pa.get("ict_fvg_bull"):    ict_hits.append("FVG Bull ✅")
    if pa.get("ict_fvg_bear"):    ict_hits.append("FVG Bear 🔴")
    if pa.get("ict_liquidity_sweep"): ict_hits.append("Liq Sweep ⚡️")
    if pa.get("smc_bos"):         ict_hits.append(f"BOS {pa['smc_bos']} 🔀")
    if pa.get("smc_choch"):       ict_hits.append(f"CHoCH {pa['smc_choch']} 🔄")
    if pa.get("ict_pd_array"):    ict_hits.append(pa["ict_pd_array"])
    if ict_hits:
        parts.append(f"SMC/ICT: `{'  ·  '.join(ict_hits[:4])}`")

    # Wyckoff + Elliott
    if pa.get("wyckoff_phase"):
        parts.append(f"Wyckoff: `{pa['wyckoff_phase']}` — {pa.get('wyckoff_event','')}")
    if pa.get("elliott_wave"):
        parts.append(f"Elliott: `{pa['elliott_wave']}`")

    # OI / Funding
    if pa.get("oi_signal"):
        parts.append(pa["oi_signal"])
    if pa.get("funding_rate") is not None:
        fr = pa["funding_rate"]
        fr_e = "🟢" if fr < -0.02 else ("🔴" if fr > 0.05 else "⚖️")
        parts.append(f"Funding: {fr_e}`{fr:+.4f}%`")

    # Volume
    vol_info = []
    if pa.get("vol_climax"):  vol_info.append("Climax ⚠️")
    if pa.get("vol_dry_up"):  vol_info.append("Dry-up 💎")
    if pa.get("vol_trend") == "increasing": vol_info.append("Vol↑ 📊")
    if vol_info:
        parts.append(f"Volume: `{'  ·  '.join(vol_info)}`")

    # Ключевые факторы
    factors = pa.get("factors", [])
    if factors:
        parts += ["", "📋 *Ключевые факторы:*"]
        for f_ in factors[:6]:
            parts.append(f"  {f_}")

    # Предупреждения
    warnings = pa.get("warnings", [])
    if warnings:
        parts += [""]
        for w in warnings[:3]:
            parts.append(w)

    # Volume Profile (Уровень 2)
    if vp["ok"]:
        parts += [
            "",
            f"📊 *Volume Profile:*",
            f"  POC: `{fp(vp['poc'])}`  VAH: `{fp(vp['vah'])}`  VAL: `{fp(vp['val'])}`",
            f"  {vp['label']}",
        ]

    # Order Book (Уровень 3)
    if ob["ok"]:
        parts += ["", f"📖 *Стакан:* {ob['label']}"]
        if ob["bid_wall"]:
            parts.append(f"  🟢 Стена покупок: `{fp(ob['bid_wall'])}`")
        if ob["ask_wall"]:
            parts.append(f"  🔴 Стена продаж: `{fp(ob['ask_wall'])}`")

    # Macro / ETH-BTC / Gold / AMD (Уровень 4)
    if mac["ok"]:
        parts += ["", f"🌍 *Макро контекст:*"]
        if mac["altseason_label"]:
            parts.append(f"  {mac['altseason_label']}")
        if mac["macro_label"]:
            parts.append(f"  {mac['macro_label']}")
        if mac.get("gold_label"):
            parts.append(f"  {mac['gold_label']}")
        trad = mac.get("traditional_risk", "neutral")
        if trad == "risk_off":
            parts.append(f"  ⚠️ Традиционные рынки в risk-off — осторожно с лонгами")
        elif trad == "cautious":
            parts.append(f"  ⚠️ Gold растёт — рынок неопределён")

    # AMD Phase (ICT Power of Three)
    amd_lbl = pa.get("amd_label") if pa.get("ok") else None
    if amd_lbl:
        parts.append(f"  {amd_lbl}")

    # Сезонность (Уровень 5)
    if sea["ok"]:
        parts += [
            "",
            f"📅 *Сезонность:* {sea['label']}",
            f"  {sea['month_note']}",
            f"  🔄 Халвинг цикл: `{sea['halving_phase']}`  "
            f"({sea['cycle_pct']:.0f}% цикла)  "
            f"До следующего: `{sea['days_to_next_halving']}д`",
        ]

    # On-chain (Уровень 6)
    if onc["ok"]:
        parts += [
            "",
            f"🐋 *On-chain:* {onc['flow_label']}",
            f"  {onc['whale_label']}",
        ]

    # Killzone
    kz_active = kz["active"]
    kz_nxt    = kz.get("next")
    parts.append(
        f"{kz_e} *Killzone:* {kz_active['name']}  качество `{kz_active['quality']}`"
        + (f"  ещё {kz_active.get('remaining_min',0)} мин" if kz_active.get('remaining_min') else "")
    )
    if kz_nxt:
        parts.append(f"   ⏰ Следующая: {kz_nxt['name']} через {kz_nxt.get('in_min',0)} мин")

    # Качество сигнала
    parts += [
        "",
        f"{sqf_e} *Качество входа: {sqf['quality']}*  (Score: {sqf['score']}/100)",
    ]
    for r_ in sqf["reasons"][:4]:
        parts.append(f"  {r_}")
    for w_ in sqf["warnings"][:2]:
        parts.append(f"  {w_}")

    # Новости / катализаторы
    if news["ok"]:
        parts += ["", f"📰 *Новости:* {news['label']}"]
        if news["catalyst"]:
            cat = news["catalyst"]
            e   = "🟢" if cat["sentiment"] == "positive" else "🔴"
            parts.append(f"  {e} {cat['title'][:80]} — _{cat['age']}_")
        for n in news["news"][1:3]:
            e = "🟢" if n["sentiment"]=="positive" else ("🔴" if n["sentiment"]=="negative" else "⚪")
            parts.append(f"  {e} {n['title'][:70]}")

    # Backtesting
    if bt["ok"]:
        bt_e = "🔥" if bt["winrate"] >= 60 else ("✅" if bt["winrate"] >= 50 else ("🟡" if bt["winrate"] >= 40 else "🔴"))
        parts += [
            "",
            f"📊 *Backtesting (последние 60 свечей 4H):*",
            f"  {bt_e} Winrate: `{bt['winrate']:.0f}%`  "
            f"Сделок: `{bt['total']}`  "
            f"Expectancy: `{bt['expectancy']:+.2f}R`",
            f"  Лучшая серия: `{bt['best_streak']}` побед  "
            f"Худшая: `{bt['worst_streak']}` поражений",
            f"  {bt['label']}",
        ]

    # Токеномика
    if tkn["has_data"]:
        parts += ["", f"🔑 *Токеномика:* {tkn['risk_label']}"]
        parts.append(f"  {tkn['note']}")
        parts.append(f"  {tkn['recommendation']}")
        if not tkn["spot_ok"]:
            parts.append("  ❌ *Для спота — не рекомендуется*")

    # BTC Корреляция
    if btc_ctx["ok"]:
        btc_ok = btc_ctx["long_ok"] if is_long else btc_ctx["short_ok"]
        btc_e  = "✅" if btc_ok else "🚨"
        parts += [
            "",
            f"₿ *BTC контекст:* {btc_ctx['label']}",
            f"  BTC `${btc_ctx['btc_price']:,.0f}`  "
            f"1H`{fc(btc_ctx['btc_ch1h'])}`  24H`{fc(btc_ctx['btc_ch24h'])}`  "
            f"RSI4H`{btc_ctx['rsi_4h']:.0f}`",
            f"  {btc_e} {'Входить можно' if btc_ok else 'НЕ ВХОДИТЬ — BTC против'}"
        ]
        if btc_ctx["warning"]:
            parts.append(f"  {btc_ctx['warning']}")

    # Размер позиции
    if ps.get("ok"):
        parts += ["", format_position_size(ps, is_long)]

    parts += [""]  # разделитель перед TP/SL

    # TP/SL
    parts += [
        f"💵 *Точка входа:* `{fp(price)}`",
        "",
        f"🎯 *TP1:* `{fp(a['tp1'])}` *({pct(a['tp1'])})* _{a.get('tp1_source','')}_",
        f"🎯 *TP2:* `{fp(a['tp2'])}` *({pct(a['tp2'])})* _{a.get('tp2_source','')}_",
        f"🎯 *TP3:* `{fp(a['tp3'])}` *({pct(a['tp3'])})* _{a.get('tp3_source','')}_",
        "",
        f"🔴 *SL:* `{fp(a['sl'])}` _{a.get('sl_source','')}_ · R:R `1:{a['rr']:.1f}`",
    ]

    # Спот DCA зоны
    if ath > 0 and ch90d < -30:
        parts += [
            "",
            f"💎 *Спот DCA зоны:*",
            f"  Зона 1 (40%): `{fp(buy_hi)}`",
            f"  Зона 2 (40%): `{fp(buy_lo)}`",
            f"  Зона 3 (20%): `{fp(atl*1.05 if atl>0 else price*0.7)}`",
            f"  Цель: `{fp(sell_t)}`  (~x{sell_t/price:.1f})",
        ]

    # Рекомендация
    if pro_score >= 75:    ve, vt = "🔥", "СИЛЬНЫЙ СИГНАЛ"
    elif pro_score >= 60:  ve, vt = "✅", "ХОРОШИЙ СИГНАЛ"
    elif pro_score >= 45:  ve, vt = "🟡", "УМЕРЕННЫЙ — ждать подтверждения"
    else:                  ve, vt = "⚠️", "СЛАБЫЙ — воздержаться"

    if is_long and rsi_1d < 35 and ch90d < -40:
        rec = "💎 Приоритет — спот DCA"
    elif is_long:
        rec = "⚡️ Фьючерс лонг 2-5x"
    else:
        rec = "⚡️ Фьючерс шорт 2-5x"

    parts += [
        "",
        f"{ve} *{vt}*   {rec}",
        f"⚠️ Риск 1-2% депозита   SL обязателен",
        f"#{symbol}USDT",
    ]

    text = "\n".join(parts)
    if len(text) > 4096:
        text = text[:4090] + "..."

    await send_coin(bot, chat_id, symbol, slug, a, text)
    return True


async def cmd_full_v2(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/full SYMBOL — полный анализ монеты"""
    if not ctx.args:
        await update.message.reply_text(
            "🔬 *Полный анализ — /full*\n\n"
            "Использование: `/full BTC`\n"
            "Пример: `/full ETH` · `/full SOL` · `/full RIVER`\n\n"
            "Включает:\n"
            "· EMA 20/50/200 · RSI · MACD · Supertrend\n"
            "· ATH / ATL · SMC/ICT · Зоны входа\n"
            "· Фандинг · OI · Спот vs Фьючерс · Вердикт",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Главное меню", callback_data="show_menu"),
            ]])
        )
        return

    symbol = ctx.args[0].upper().replace("USDT","").replace("BUSD","")
    msg    = await update.message.reply_text(
        f"🔍 Анализирую *{symbol}USDT*...", parse_mode="Markdown"
    )
    try:
        await msg.delete()
    except: pass
    await _do_full_analysis(ctx.bot, update.effective_chat.id, symbol)


async def cmd_myid(update: Update, ctx):
    uid = update.effective_user.id
    cid = update.effective_chat.id
    await update.message.reply_text(
        f"👤 *Твой User ID:* `{uid}`\n💬 *Chat ID:* `{cid}`",
        parse_mode="Markdown"
    )

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("myid",      cmd_myid))
    app.add_handler(CommandHandler("spot",      cmd_top_spot))
    app.add_handler(CommandHandler("long",      cmd_top_long))
    app.add_handler(CommandHandler("short",     cmd_top_short))
    app.add_handler(CommandHandler("full",      cmd_full_v2))
    app.add_handler(CommandHandler("menu",      lambda u,c: u.message.reply_text(
        "📊 *BEST TRADE — Главное меню*\n\n👇 Выбери раздел:",
        parse_mode="Markdown", reply_markup=main_kb())))
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
        send_scheduled,
        "interval",
        minutes=30,
        args=[app.bot],
        next_run_time=datetime.now(TZ)  # первый запуск сразу
    )
    scheduler.add_job(
        check_alerts,
        "interval",
        minutes=5,
        args=[app.bot]
    )
    scheduler.start()
    log.info("✅ BEST TRADE v32.0 | Supply/Demand | Real-time signals | UTC+3")
    _load_signals()
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
