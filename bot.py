
def get_usdt_dominance():
    try:
        r = __import__("requests").get("https://api.coingecko.com/api/v3/global", timeout=8)
        if r.status_code == 200:
            return {"usdt_d": round(r.json().get("data",{}).get("market_cap_percentage",{}).get("usdt",0),2)}
    except: pass
    return {"usdt_d": 0}
def get_market_trend_analysis():
    try:
        import requests as _r
        btc = _r.get("https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT", timeout=5).json()
        btc_price = float(btc.get("lastPrice", 0))
        btc_change = float(btc.get("priceChangePercent", 0))
        klines = _r.get("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=200", timeout=5).json()
        closes = [float(k[4]) for k in klines]
        ema200 = sum(closes[-200:]) / 200
        ema50 = sum(closes[-50:]) / 50
        gains, losses = [], []
        for i in range(1, 15):
            d = closes[-i] - closes[-i-1]
            (gains if d > 0 else losses).append(abs(d))
        avg_g = sum(gains)/14 if gains else 0.001
        avg_l = sum(losses)/14 if losses else 0.001
        rsi = 100 - (100 / (1 + avg_g/avg_l))
        if btc_price > ema200 and btc_price > ema50:
            bias = "БЫЧИЙ"
        elif btc_price < ema200:
            bias = "МЕДВЕЖИЙ"
        else:
            bias = "НЕЙТРАЛЬНЫЙ"
        s1 = round(ema200 * 0.98, 0)
        s2 = round(ema200 * 0.95, 0)
        r1 = round(ema50, 0)
        r2 = round(ema50 * 1.04, 0)
        drop = round((126021 - btc_price) / 126021 * 100, 1)
        return {"bias": bias, "btc_price": round(btc_price, 0), "rsi": round(rsi, 1), "ema200": round(ema200, 0), "ema50": round(ema50, 0), "usdt_d": round(btc_change, 2), "support1": s1, "support2": s2, "resist1": r1, "resist2": r2, "drop_from_ath": drop}
    except Exception as e:
        return {"bias": "ОШИБКА", "btc_price": 0, "rsi": 0, "ema200": 0, "ema50": 0, "usdt_d": 0, "support1": 0, "support2": 0, "resist1": 0, "resist2": 0, "drop_from_ath": 0}

def format_market_trend(ta):
    bias = ta.get("bias", "-")
    price = ta.get("btc_price", 0)
    rsi = ta.get("rsi", 0)
    ema200 = ta.get("ema200", 0)
    ema50 = ta.get("ema50", 0)
    change = ta.get("usdt_d", 0)
    s1 = ta.get("support1", 0)
    s2 = ta.get("support2", 0)
    r1 = ta.get("resist1", 0)
    r2 = ta.get("resist2", 0)
    drop = ta.get("drop_from_ath", 0)
    change_str = f"+{change}%" if change > 0 else f"{change}%"
    if rsi < 30: signal = "RSI перепродан - потенциал лонга"
    elif rsi > 70: signal = "RSI перекуплен - потенциал шорта"
    elif bias == "МЕДВЕЖИЙ": signal = "Закрытие выше EMA50 = сигнал лонга"
    else: signal = "Торговать по тренду"
    return (f"BTC АНАЛИЗ\n"
            f"Цена: ${price:,.0f} ({change_str} 24ч)\n"
            f"Тренд: {bias}\n"
            f"От ATH $126,021: -{drop}%\n\n"
            f"EMA200: ${ema200:,.0f}\n"
            f"EMA50:  ${ema50:,.0f}\n"
            f"RSI(14): {rsi}\n\n"
            f"Поддержки: ${s1:,.0f} / ${s2:,.0f}\n"
            f"Сопротивления: ${r1:,.0f} / ${r2:,.0f}\n\n"
            f"Сигнал: {signal}\n"
            f"Риск 1-2% депозита. SL обязателен")



"""
 BEST TRADE Bot v9.0
- -500 CMC
- /1 market  /2 BTC coin  /3 signals  /4 top
-  UTC+3 
-   EMA20/50/200 (200 )
-  #SYMBOLUSDT   
- Pump/Dump  ( 5 )
-     ( 5 )
- Min/Max  +   
-  :   /    
-   30 
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

#   
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

#    pump/dump   
price_cache      = {}   # {symbol: [price1, price2, ...]}  
alerted_zones    = {}   # {symbol: timestamp}   
pump_alerted     = {}   # {symbol: timestamp}

#     ( "  ") 
# {symbol: {"type": str, "time": datetime, "price": float, "status": "active"/"done"}}
active_game: dict = {}   # {symbol: {"type", "time", "price", "status", "done_time"}}
done_game:   list = []   #   ( 20)
MAX_GAME_HISTORY = 100

def add_to_game(symbol: str, alert_type: str, price: float):
    """    """
    #       (  )
    if symbol not in active_game:
        active_game[symbol] = {
            "type":      alert_type,
            "time":      datetime.now(TZ),
            "price":     price,
            "status":    "active",
            "done_time": None,
        }
    #   (>48 )
    cutoff = datetime.now(TZ).timestamp() - 48 * 3600
    to_del = [s for s, v in active_game.items()
              if v["time"].timestamp() < cutoff]
    for s in to_del:
        del active_game[s]

def mark_done(symbol: str, result: str = ""):
    """   """
    if symbol in active_game:
        active_game[symbol]["status"]    = "done"
        active_game[symbol]["done_time"] = datetime.now(TZ)
        active_game[symbol]["result"]    = result
        #   done_game 
        done_game.insert(0, {
            "symbol":    symbol,
            "result":    result,
            "done_time": datetime.now(TZ),
        })
        if len(done_game) > 20:
            done_game.pop()

def build_game_digest() -> str:
    """
        VANGA:
     SYMBOLUSDT   
       21.06 16:19 UTC+3
    """
    #      ( )
    actives = [(s, v) for s, v in active_game.items()
               if v["status"] == "active"]
    actives.sort(key=lambda x: x[1]["time"].timestamp(), reverse=True)

    type_labels = {
        "pump":        " ",
        "dump":        " ",
        "level":       "  ",
        "watchlist":   "  ",
        "supertrend":  "  ",
        "precision":   " precision ",
        "zone":        "  ",
    }

    lines = []
    if actives:
        lines.append(f" *  : {len(actives)}*\n")
        for sym, v in actives:
            lbl      = type_labels.get(v["type"], v["type"])
            t        = v["time"].strftime("%d.%m %H:%M")
            tv_url   = f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym}USDT"
            lines.append(f" [{sym}USDT]({tv_url})  {lbl}")
            lines.append(f"   {t} UTC+3")
    else:
        lines.append(" *  : 0*\n")
        lines.append("_  _")

    # 
    if done_game:
        lines.append("")
        lines.append(" *:*")
        for d in done_game[:10]:
            sym    = d["symbol"]
            result = d["result"]
            t      = d["done_time"].strftime("%d.%m %H:%M")
            tv_url = f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym}USDT"
            emoji  = "" if "" in result else ("" if "" in result else "")
            lines.append(f" [{sym}USDT]({tv_url})  {emoji} {result}")
            lines.append(f"   {t} UTC+3")

    return "\n".join(lines)

# 
# DATA FUNCTIONS
# 
STABLECOINS = {
    "USDT","USDC","BUSD","DAI","FDUSD","TUSD","USDP","USDD","FRAX","LUSD",
    "SUSD","ALUSD","GUSD","HUSD","EURS","XAUT","PAXG","WBTC","WETH","STETH",
    "WSTETH","RETH","CBETH","SFRXETH","ANKRETH","BETH","BETH","UST","USTC",
    "MIM","FEI","OUSD","DOLA","CUSD","CEUR","USDX","USDJ","USDN","BITCNY",
}

def get_all_coins():
    """
      :
    1. CMC:  5000  (3   ~1667) 
    2. Binance:   USDT  (    CMC)
     30 .
    """
    #        30 
    now_ts = datetime.now(TZ).timestamp()
    cache_key = "_all_coins_cache"
    if hasattr(get_all_coins, "_cache"):
        cached_time, cached_data = get_all_coins._cache
        if now_ts - cached_time < 1800 and cached_data:
            return cached_data

    result    = []
    seen_syms = set()

    #   1: CMC listings   5000   
    try:
        url     = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
        headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}

        #   1000,  5 
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
                    if mcap > 0 and mcap < 100_000: continue  #  $100K  

                    seen_syms.add(sym)
                    result.append(coin)
                    added += 1

                log.info(f"CMC batch start={start}: +{added}  ( {len(result)})")

                #    500    
                if len(batch) < 500:
                    break

                time.sleep(0.5)  # rate limit

            except Exception as e:
                log.error(f"CMC batch start={start}: {e}")
                break

    except Exception as e:
        log.error(f"CMC all coins: {e}")

    #   2: Binance   USDT     CMC 
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
                if vol_usd < 50_000:    continue  #   
                if price <= 0:          continue

                #    
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

            log.info(f"Binance   . : {len(result)}")

    except Exception as e:
        log.error(f"Binance all pairs: {e}")

    # : CMC   , Binance-only     
    cmc_coins    = [c for c in result if c.get("cmc_rank", 9999) < 9999]
    binance_only = [c for c in result if c.get("cmc_rank", 9999) == 9999]
    cmc_coins.sort(key=lambda x: x.get("cmc_rank", 9999))
    binance_only.sort(key=lambda x: x.get("quote",{}).get("USDT",{}).get("volume_24h",0), reverse=True)
    result = cmc_coins + binance_only

    log.info(f" : {len(result)} (CMC: {len(cmc_coins)}, Binance-only: {len(binance_only)})")

    #   
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
    """   Binance.    ."""
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

    #    
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
    """    monthly """
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
    """   Binance Futures"""
    try:
        url    = "https://fapi.binance.com/fapi/v1/premiumIndex"
        params = {"symbol": f"{symbol}USDT"}
        r      = requests.get(url, params=params, timeout=8)
        r.raise_for_status()
        d    = r.json()
        rate = float(d.get("lastFundingRate", 0)) * 100  #  %
        mark = float(d.get("markPrice", 0))
        idx  = float(d.get("indexPrice", 0))
        # Basis =     
        basis = (mark - idx) / idx * 100 if idx > 0 else 0

        if rate > 0.1:    fr_signal = "  ( )"
        elif rate > 0.05: fr_signal = "  "
        elif rate > 0:    fr_signal = " "
        elif rate > -0.05: fr_signal = "  "
        else:              fr_signal = " - !"

        return {"rate": rate, "signal": fr_signal, "mark": mark, "basis": basis, "ok": True}
    except:
        return {"rate": 0, "signal": "", "mark": 0, "basis": 0, "ok": False}

def get_open_interest(symbol: str) -> dict:
    """Open Interest  Binance Futures   OI  24"""
    try:
        #  OI
        url = "https://fapi.binance.com/fapi/v1/openInterest"
        r   = requests.get(url, params={"symbol": f"{symbol}USDT"}, timeout=8)
        r.raise_for_status()
        oi_now = float(r.json().get("openInterest", 0))

        # OI Statistics ()
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

        if oi_change > 5:    oi_signal = " OI    "
        elif oi_change > 1:  oi_signal = " OI  "
        elif oi_change > -1: oi_signal = " OI "
        elif oi_change > -5: oi_signal = " OI    "
        else:                oi_signal = " OI    "

        return {"oi": oi_now, "change": oi_change, "signal": oi_signal, "ok": True}
    except:
        return {"oi": 0, "change": 0, "signal": "", "ok": False}

def get_market_extras(symbol: str) -> dict:
    """  + OI   """
    fr = get_funding_rate(symbol)
    oi = get_open_interest(symbol)
    return {"funding": fr, "oi": oi}

# 
# 
# 
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
    if ch >= 3:  return ""
    if ch >= 0:  return ""
    if ch >= -3: return ""
    return ""

def cmc_link(slug):  return f"https://coinmarketcap.com/currencies/{slug}/"
def tv_link(symbol): return f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}USDT"

# 
#  
# 
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
    Supertrend .
      dict: {"value": float, "direction": 1=bull/-1=bear, "signal": "BUY"/"SELL"/None}
    signal      
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
            # Was bullish  stay bullish if close > lower band
            if close < lower_band[i]:
                direction[i] = -1
                st[i]        = upper_band[i]
            else:
                direction[i] = 1
                st[i]        = lower_band[i]
        else:
            # Was bearish  stay bearish if close < upper band
            if close > upper_band[i]:
                direction[i] = 1
                st[i]        = lower_band[i]
            else:
                direction[i] = -1
                st[i]        = upper_band[i]

        # Signal    
        sig = None
        if i > period and direction[i] != direction[i-1]:
            sig = "BUY" if direction[i] == 1 else "SELL"

        results[i] = {"value": st[i], "direction": direction[i], "signal": sig}

    return results

def get_supertrend_signal(symbol: str) -> dict:
    """
       Supertrend  .
     USDT,  BUSD  fallback.
    """
    candles = get_binance_ohlc(symbol, interval="4h", limit=100)

    # Fallback:        BTC  BNB
    if not candles or len(candles) < 20:
        try:
            url    = "https://api.binance.com/api/v3/klines"
            #   
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
        log.warning(f"Supertrend:    {symbol}")
        return {"direction": 1, "last_signal": None, "label": "", "current_price": 0}

    st = calc_supertrend(candles, period=10, multiplier=3.0)

    current_dir = st[-1]["direction"]
    current_val = st[-1]["value"]

    #   
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

    label = " BUY" if current_dir == 1 else " SELL"

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
    }  #   


def full_analysis(coin: dict) -> dict:
    """
     .  :
    - is_long     (ch24h, ch7d, ch30d)
    - Rocket Score   ,    
    - Vol/MCap > 50% =   
    -       LONG
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

    suspicious = vol_ratio > 50  # ETF-, --

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

    # EMA -   (90  EMA200)
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

    #      
    def smart_round(val):
        if val == 0: return 0
        import math
        #     TP1/TP2/TP3 
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

    if rocket >= 80:   rocket_label = " ROCKET"
    elif rocket >= 70: rocket_label = " "
    elif rocket >= 60: rocket_label = " "
    elif rocket >= 50: rocket_label = " "
    elif rocket >= 40: rocket_label = " "
    else:              rocket_label = " "

    if score >= 8:    label = "  " if is_long else "  "
    elif score >= 5:  label = " " if is_long else " "
    elif score >= 3:  label = " " if is_long else " "
    elif score >= 1:  label = "  "
    elif score >= -1: label = " "
    else:             label = " "

    smc_factors = []
    if smc_bos_bull:    smc_factors.append("BOS ")
    if smc_bos_bear:    smc_factors.append("BOS ")
    if smc_ob_accum:    smc_factors.append("OB ")
    if smc_liq_sweep:   smc_factors.append("Liq Sweep")
    if smc_smart_accum: smc_factors.append("Smart Accum ")
    if smc_smart_dist:  smc_factors.append("Smart Dist ")
    if smc_fvg_bull:    smc_factors.append("FVG ")
    if smc_fvg_bear:    smc_factors.append("FVG ")
    if tf_aligned_bull: smc_factors.append("TF Align Bull")
    if tf_aligned_bear: smc_factors.append("TF Align Bear")
    if fund_recovery:   smc_factors.append("Recovery ")
    if bb_squeeze:      smc_factors.append("BB Squeeze")
    if macd_bullish:    smc_factors.append("MACD Bull")
    if macd_bearish:    smc_factors.append("MACD Bear")
    if suspicious:      smc_factors.append(" Vol ")
    # Supply/Demand 
    if in_demand:       smc_factors.append(" Demand Zone")
    if in_supply:       smc_factors.append(" Supply Zone")

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
        "st_label": "",
    }

# 
#  
# 
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
          KRIPTANO:
    -  + EMA +  (Entry, TP1/2/3, SL, Swing)
    -     
    -  
    -  BEST TRADE
    """
    is_long       = a["is_long"]
    price         = a["price"]
    tp1, tp2, tp3 = a["tp1"], a["tp2"], a["tp3"]
    sl, swing     = a["sl"],  a["swing"]
    rsi           = a["rsi_4h"]

    #         
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
        return None  #    

    n_all      = len(candles)
    closes_all = [c["close"] for c in candles]

    # EMA   
    ema20_all  = calc_ema(closes_all, 20)
    ema50_all  = calc_ema(closes_all, 50)
    ema200_all = calc_ema(closes_all, min(200, n_all))

    # Supertrend (   BUY/SELL   )
    st_all = calc_supertrend(candles, period=10, multiplier=3.0)

    #   80 
    display_n = min(80, n_all)
    start_idx = n_all - display_n
    candles  = candles[start_idx:]
    ema20_v  = ema20_all[start_idx:]
    ema50_v  = ema50_all[start_idx:]
    ema200_v = ema200_all[start_idx:]
    st_v     = st_all[start_idx:]

    n    = len(candles)
    vols = [c.get("vol", 0) for c in candles]

    #  LAYOUT 
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

    #   ( ) 
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

    #   
    w = 0.42
    for i, c in enumerate(candles):
        col = "#26A69A" if c["close"] >= c["open"] else "#EF5350"
        ax.plot([i, i], [c["low"], c["high"]], color=col, lw=0.7, zorder=2)
        body_h = abs(c["close"] - c["open"]) or (c["high"] - c["low"]) * 0.015
        ax.add_patch(patches.Rectangle(
            (i - w/2, min(c["open"], c["close"])), w, body_h,
            linewidth=0, facecolor=col, alpha=0.95, zorder=3
        ))

    #  EMA    
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

    #  SUPERTREND    BUY/SELL,   
    for i, s in enumerate(st_v):
        if s["signal"] == "BUY":
            ax.annotate(" BUY",
                        xy=(i, candles[i]["low"] * 0.9982),
                        fontsize=7, color="#26A69A", fontweight="bold",
                        ha="center", va="top", zorder=10,
                        bbox=dict(boxstyle="round,pad=0.15",
                                  facecolor="#0B1120", edgecolor="#26A69A",
                                  alpha=0.9, lw=0.8))
        elif s["signal"] == "SELL":
            ax.annotate(" SELL",
                        xy=(i, candles[i]["high"] * 1.0018),
                        fontsize=7, color="#EF5350", fontweight="bold",
                        ha="center", va="bottom", zorder=10,
                        bbox=dict(boxstyle="round,pad=0.15",
                                  facecolor="#0B1120", edgecolor="#EF5350",
                                  alpha=0.9, lw=0.8))

    #   (   KRIPTANO) 
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

    #   
    draw_level(tp3,   "#00C896", "TP3",   pct_str(tp3),   "--", 1.0)
    draw_level(tp2,   "#00E5A0", "TP2",   pct_str(tp2),   "--", 1.0)
    draw_level(tp1,   "#26A69A", "TP1",   pct_str(tp1),   "--", 1.0)
    draw_level(price, "#FFD700", "Entry", "",              "-",  2.0)
    draw_level(swing, "#64B5F6", "Swing", "",              ":",  1.0)
    draw_level(sl,    "#EF5350", "SL",    pct_str(sl),    "--", 1.3)

    #     
    ax.annotate("" if is_long else "",
                xy=(n - 1, price), fontsize=16,
                color="#FFD700", ha="center",
                va="bottom" if is_long else "top", zorder=9)

    #   
    side_str = "LONG" if is_long else "SHORT"
    side_col = "#26A69A" if is_long else "#EF5350"
    ax.text(0.01, 0.98, f"{symbol}USDT    4H    {side_str}",
            fontsize=12, color=WHITE, fontweight="bold",
            va="top", ha="left", transform=ax.transAxes, zorder=10)
    rsi_t = "" if rsi < 35 else ("" if rsi > 65 else "")
    rsi_c = "#26A69A" if rsi < 35 else ("#EF5350" if rsi > 65 else GRAY)
    ax.text(0.01, 0.90, f"RSI {rsi:.0f}  {rsi_t}",
            fontsize=8, color=rsi_c,
            va="top", ha="left", transform=ax.transAxes, zorder=10)

    # Supertrend 
    cur_st = st_v[-1] if st_v else {"direction": 1}
    st_lbl = "SUPERTREND: BUY" if cur_st["direction"] == 1 else "SUPERTREND: SELL"
    st_col = "#26A69A" if cur_st["direction"] == 1 else "#EF5350"
    ax.text(0.01, 0.82, st_lbl,
            fontsize=8, color=st_col, fontweight="bold",
            va="top", ha="left", transform=ax.transAxes, zorder=10,
            bbox=dict(boxstyle="round,pad=0.25", facecolor="#0B1120",
                      edgecolor=st_col, alpha=0.85, lw=0.9))

    #   
    max_vol = max(vols) if max(vols) > 0 else 1
    for i, c in enumerate(candles):
        col = "#26A69A" if c["close"] >= c["open"] else "#EF5350"
        axv.bar(i, vols[i] / max_vol, width=0.7, color=col, alpha=0.45, zorder=2)
    axv.set_yticks([])
    axv.set_ylabel("Vol", color=GRAY, fontsize=7, rotation=0, labelpad=18)
    axv.spines[:].set_visible(False)

    #  X  
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

# 
#  
# 
def build_signal_text(symbol: str, a: dict,
                      stats_24h: dict = None,
                      atl: float = 0,
                      extras: dict = None) -> str:
    """
      .
    : EMA  (  ), Vol/MCap%,  1H/24H/7D/30D
    :  , Open Interest,  
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

    side_emoji = "" if is_long else ""
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
    bar    = "" * filled + "" * (10 - filled)

    # EMA  ()
    ema_pos = []
    if a.get("above_ema200"): ema_pos.append("EMA200")
    if a.get("above_ema50"):  ema_pos.append("EMA50")
    if a.get("above_ema20"):  ema_pos.append("EMA20")
    if not ema_pos:           ema_pos = ["  EMA "]
    ema_str = " | ".join(ema_pos)

    # RSI 
    def rsi_icon(r):
        if r < 30: return ""
        if r > 70: return ""
        return ""

    # SMC     ( BB Squeeze)
    raw_smc = [f for f in a.get("smc_factors", [])
               if "BB Squeeze" not in f and "MACD" not in f]
    smc_key = raw_smc[:3] if raw_smc else []

    #  
    macd_str = " " if a.get("macd_bullish") else (" " if a.get("macd_bearish") else "")
    st_str   = a.get("st_label", "")

    #     
    rsi_4h   = a["rsi_4h"]
    ch24h    = a["ch24h"]
    overbought = rsi_4h > 75
    oversold   = rsi_4h < 30
    suspicious = a.get("suspicious", False)

    if suspicious:
        conclusion = "     , "
    elif is_long and overbought and not oversold:
        conclusion = "      "
    elif is_long and rocket >= 75 and oversold:
        conclusion = "  +      !"
    elif is_long and rocket >= 75:
        conclusion = "     "
    elif is_long and rocket >= 60:
        conclusion = "     "
    elif not is_long and rocket >= 70:
        conclusion = "  -"
    elif is_long and a.get("smc_smart_accum"):
        conclusion = " Smart Money     "
    elif is_long and a.get("fund_recovery"):
        conclusion = "     DCA "
    elif not is_long and ch24h < -10:
        conclusion = "       "
    else:
        conclusion = "     "

    lines = [
        f" *{symbol}USDT*  {side_emoji} *{side_text}*",
        f" {now_utc3()}",
        "",
        f" *{rocket}/100* {rocket_label}  `{bar}`",
        f" {ema_str}",
        f" {conclusion}",
        "",
        f" :    `{fp(price)}`",
        f" TP1:    `{fp(tp1)}`  ({pct(tp1)})",
        f" TP2:    `{fp(tp2)}`  ({pct(tp2)})",
        f" TP3:    `{fp(tp3)}`  ({pct(tp3)})",
        f" SL:      `{fp(sl)}`  ({sl_pct()})",
        f" {swing_lbl}:  `{fp(swing)}`",
        "",
        "",
        f" R:R `1:{rr:.1f}`  |    `{vol_str}`  |  Rank `#{a.get('rank','')}`",
        f" RSI 4H {rsi_icon(rsi_4h)}`{rsi_4h:.0f}`  |  MACD `{macd_str}`",
        f" Supertrend: `{st_str}`",
    ]

    # SMC 
    if smc_key:
        lines.append(f" SMC: `{'    '.join(smc_key)}`")

    # 24H min/max +  
    if stats_24h:
        h24 = stats_24h.get("high", 0)
        l24 = stats_24h.get("low",  0)
        if h24 and l24:
            best = l24 * 1.005 if is_long else h24 * 0.995
            lines += [
                "",
                "",
                f" 24H:   `{fp(h24)}`    `{fp(l24)}`",
                f"   : `{fp(best)}`",
            ]

    #  + OI (    )
    if extras:
        fr = extras.get("funding", {})
        oi = extras.get("oi", {})
        if fr.get("ok") or oi.get("ok"):
            lines.append("")
            lines.append("")
        if fr.get("ok"):
            rate_str = f"{fr['rate']:+.4f}%"
            lines.append(f" *:* `{rate_str}`  {fr['signal']}")
        if oi.get("ok") and oi.get("oi", 0) > 0:
            oi_ch = oi.get("change", 0)
            oi_str = f"{oi_ch:+.1f}%  24"
            lines.append(f" *OI:* `{oi_str}`  {oi['signal']}")

    #  
    if atl and atl > 0:
        from_atl = (price - atl) / atl * 100
        lines.append(f"  . : `+{from_atl:.0f}%`  (min `{fp(atl)}`)")

    lines += ["", f"#{symbol}USDT"]
    return "\n".join(lines)

    lines += ["", f"#{symbol}USDT"]
    return "\n".join(lines)

# 
#  
# 
BTC_ZONES = {
    "support":    [
        {"level": 63000, "label": ""},
        {"level": 62137, "label": "S1 "},
        {"level": 61316, "label": "S2"},
        {"level": 59000, "label": "S3"},
    ],
    "resistance": [
        {"level": 64300, "label": " "},
        {"level": 65000, "label": "R1"},
        {"level": 67000, "label": "R2"},
    ],
}

#         
# : symbol  {"long": [lo, hi], "short": [lo, hi], "note": str, "source": str}
WATCHLIST_ZONES = {
    #    (  19.06.2026) 
    "LINK": {
        "long":  [6.70, 7.40],
        "note":  "Chainlink  DeFi .   ",
        "source": "",
        "bias":  "LONG",
        "spot":  True,
    },
    "AVAX": {
        "long":  [4.50, 4.90],
        "note":  "Avalanche  L1. -   ",
        "source": "",
        "bias":  "LONG",
        "spot":  True,
    },
    "UNI": {
        "long":  [2.50, 2.80],
        "note":  "Uniswap  DEX #1.  .     $6+",
        "source": "",
        "bias":  "LONG",
        "spot":  True,
    },
    "DYDX": {
        "long":  [0.10, 0.12],
        "note":  "dYdX   DEX.   . Recovery  x5+",
        "source": "",
        "bias":  "LONG",
        "spot":  True,
    },
    "PYTH": {
        "long":  [0.030, 0.032],
        "note":  "Pyth Network  .  .  Chainlink",
        "source": "",
        "bias":  "LONG",
        "spot":  True,
    },
    "ORDI": {
        "long":  [2.30, 2.57],
        "note":  "Ordinals  Bitcoin NFT .    ",
        "source": "",
        "bias":  "LONG",
        "spot":  True,
    },
    "AAVE": {
        "long":  [53.50, 63.50],
        "note":  "Aave   DeFi.  .  DCA",
        "source": "",
        "bias":  "LONG",
        "spot":  True,
    },
    "BEAT": {
        "long":  [1.10, 1.30],
        "note":  "CertiK .  EMA200.  - ",
        "source": "",
        "bias":  "LONG",
        "spot":  True,
    },
    #    
    "SOL": {
        "long":  [68.28, 69.34],
        "note":  "3   .   ",
        "source": "",
        "bias":  "LONG",
    },
    "ZK": {
        "short": [0.01278, 0.01314],
        "note":  "   ",
        "source": "",
        "bias":  "SHORT",
    },
    "ACH": {
        "long":  [0.005030, 0.005138],
        "note":  "    .  ",
        "source": "",
        "bias":  "LONG",
    },
    "EIGEN": {
        "long":  [0.1790, 0.1871],
        "note":  "4H ,  .  ",
        "source": "",
        "bias":  "LONG",
    },
    "ETH": {
        "short": [1710, 1737],
        "note":  "Imbalance .   . : $1670, $1504",
        "source": "",
        "bias":  "SHORT",
    },
    "APT": {
        "long":  [0.6300, 0.6397],
        "note":  "   4H. SL   ",
        "source": "",
        "bias":  "LONG",
    },
    "ENA": {
        "long":  [0.0875, 0.0941],
        "note":  "  4H.  $0.1060.110",
        "source": "",
        "bias":  "LONG",
    },
    "BTC": {
        "long":  [62000, 63000],
        "note":  "  $62K =  .  $70K+",
        "source": "",
        "bias":  "LONG",
    },
    "PIPPIN": {
        "long":  [0.0120, 0.0135],
        "note":  "  RSI < 30.  Solana. Recovery ",
        "source": "",
        "bias":  "LONG",
    },
    "HYPE": {
        "long":  [None, None],
        "note":  " ATH $7780.   ",
        "source": "",
        "bias":  "LONG",
    },
}

#  -    
SPOT_PORTFOLIO = {
    sym: info for sym, info in WATCHLIST_ZONES.items() if info.get("spot")
}

def check_watchlist_alerts(coins: list) -> list:
    """
         .
      .
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
            emoji = "" if bias == "LONG" else ""
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

    if sp >= 65:   sent = " "
    elif sp >= 50: sent = "  "
    elif sp >= 35: sent = "  "
    else:          sent = " "

    dom_sig    = (" BTC     " if bd > 59 else
                  (" BTC.D " if bd > 56 else
                   " BTC.D     "))
    others_sig = ("  " if od < 8.2 else
                  ("  " if od > 8.8 else "  "))
    total_sig  = ("  " if mc >= 2 else
                  (" "   if mc >= 0 else
                   (" "   if mc >= -2 else "  ")))

    bulls = sum([btc.get("ch24h", 0) > 1, eth.get("ch24h", 0) > 1, bd < 57, mc > 0, od > 8.3])
    if bulls >= 4:   verdict = "    "
    elif bulls >= 3: verdict = "     "
    elif bulls >= 2: verdict = "    "
    elif bulls >= 1: verdict = "     "
    else:            verdict = "     "

    analyzed   = [(c, full_analysis(c)) for c in coins]

    #  : score>=3  ch24h>0  ch7d>0 ( )
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
    s_line = f"   : ${sup['level']:,} ({sup['label']})  {sup['dist']:.1f}% " if sup else ""
    r_line = f"   : ${res['level']:,} ({res['label']})  {res['dist']:.1f}% " if res else ""

    long_lines  = []
    short_lines = []
    for i, (c, a) in enumerate(ms.get("top_longs", []), 1):
        sym = c["symbol"]; ch = a["ch24h"]
        long_lines.append(f"  {i}.  *{sym}*  ${fp(a['price'])}  {fc(ch)}  RSI {a['rsi_4h']:.0f}")
    for i, (c, a) in enumerate(ms.get("top_shorts", []), 1):
        sym = c["symbol"]; ch = a["ch24h"]
        short_lines.append(f"  {i}.  *{sym}*  ${fp(a['price'])}  {fc(ch)}  RSI {a['rsi_4h']:.0f}")

    lines = [
        " *   BEST TRADE*",
        f" {now_utc3()}",
        "",
        f"{trend_arrow(ms['btc_ch24h'])} *Bitcoin (BTC)*  ${ms['btc_price']:,.0f}",
        f"  24: {fc(ms['btc_ch24h'])}",
    ]
    if s_line: lines.append(s_line)
    if r_line: lines.append(r_line)
    lines += [
        "",
        f"{trend_arrow(ms['eth_ch24h'])} *Ethereum (ETH)*  ${ms['eth_price']:,.0f}",
        f"  24: {fc(ms['eth_ch24h'])}",
        "",
        f" **",
        f"  BTC *{ms['btc_dom']:.2f}%*    ETH {ms['eth_dom']:.2f}%    Others {ms['others_dom']:.2f}%",
        f"  {ms['dom_signal']}",
        "",
        f"{trend_arrow(ms['mcap_ch'])} *Total Market Cap*  {fm(ms['total_mcap'])}",
        f"  {fc(ms['mcap_ch'])}  24    {ms['total_signal']}",
        "",
        f" * :* {ms['sentiment']}",
        f"   {ms['sentiment_pct']:.0f}%   -500",
        "",
        " * :*",
    ]
    lines += long_lines if long_lines else ["   "]
    lines += ["", " * :*"]
    lines += short_lines if short_lines else ["   "]
    lines += [
        "",
        f" *:* {ms['verdict']}",
        "",
        " : *2% *    SL ",
    ]
    return "\n".join(lines)

def overview_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(" ",          callback_data="market_overview"),
         InlineKeyboardButton("  ",      callback_data="trend_analysis")],
        [InlineKeyboardButton("  ",          callback_data="top_spot"),
         InlineKeyboardButton("  ",          callback_data="top_long")],
        [InlineKeyboardButton("  ",          callback_data="top_short"),
         InlineKeyboardButton("  ",     callback_data="menu_full")],
        [InlineKeyboardButton(" BTC Chart",          url=tv_link("BTC")),
         InlineKeyboardButton(" TOTAL",             url="https://www.tradingview.com/chart/?symbol=CRYPTOCAP:TOTAL")],
        [InlineKeyboardButton("  ",      callback_data="show_menu")],
    ])

# 
# PUMP / DUMP 
# 
async def check_pump_dump(bot, chat_ids, coins):
    now_ts = datetime.now(TZ).timestamp()
    for coin in coins:
        sym   = coin["symbol"]
        q     = coin["quote"]["USDT"]
        price = q.get("price", 0)
        ch1h  = q.get("percent_change_1h", 0) or 0

        #   
        if sym not in price_cache:
            price_cache[sym] = []
        price_cache[sym].append(price)
        if len(price_cache[sym]) > 12:  #   
            price_cache[sym].pop(0)

        # Pump: +5%  1   ,    
        if ch1h >= 5:
            last_alert = pump_alerted.get(sym, 0)
            if now_ts - last_alert > 3600:
                pump_alerted[sym] = now_ts
                add_to_game(sym, "pump", price)
                log.info(f"PUMP detected (silent): {sym} +{ch1h:.2f}%")

        # Dump: -5%  1   ,    
        elif ch1h <= -5:
            last_alert = pump_alerted.get(f"dump_{sym}", 0)
            if now_ts - last_alert > 3600:
                pump_alerted[f"dump_{sym}"] = now_ts
                add_to_game(sym, "dump", price)
                log.info(f"DUMP detected (silent): {sym} {ch1h:.2f}%")

# 
#    
# 
async def check_entry_zones(bot, chat_ids, coins):
    """     TP/SL  """
    now_ts = datetime.now(TZ).timestamp()
    analyzed = [(c, full_analysis(c)) for c in coins if c["quote"]["USDT"].get("price", 0) > 0]
    signals  = [(c, a) for c, a in analyzed if abs(a["score"]) >= 3]

    for coin, a in signals:
        sym   = coin["symbol"]
        price = a["price"]
        tp1   = a["tp1"]
        sl    = a["sl"]
        is_long = a["is_long"]

        #    entry zone (  1%)
        if is_long:
            near_entry = price <= a["swing"] * 1.01
        else:
            near_entry = price >= a["swing"] * 0.99

        if near_entry:
            last_alert = alerted_zones.get(sym, 0)
            if now_ts - last_alert > 1800:  #     30 
                alerted_zones[sym] = now_ts
                side   = "LONG" if is_long else "SHORT"
                emoji  = "" if is_long else ""
                slug   = coin.get("slug", sym.lower())
                text   = (f" *   !*\n"
                          f" {now_utc3()}\n\n"
                          f"{emoji} *{sym}USDT  {side}*\n"
                          f" : `{fp(price)}`\n"
                          f" TP1: `{fp(tp1)}`\n"
                          f" SL: `{fp(sl)}`\n"
                          f" R:R: 1:{a['rr']:.1f}\n\n"
                          f" : 2%  | SL \n\n"
                          f"#{sym}USDT")
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton(" TradingView", url=tv_link(sym)),
                    InlineKeyboardButton("CMC", url=cmc_link(slug)),
                ]])
                for cid in chat_ids:
                    try:
                        await bot.send_message(cid, text, parse_mode="Markdown", reply_markup=kb)
                    except Exception as e:
                        log.error(f"Zone alert {cid}: {e}")
                add_to_game(sym, "zone", price)
                log.info(f"ZONE alert: {sym} {side} price={fp(price)}")

# 
# 
# 
def main_kb():
    """  BEST TRADE v34"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("  ",          callback_data="market_overview"),
         InlineKeyboardButton("  ",         callback_data="trend_analysis")],
        [InlineKeyboardButton("  ",             callback_data="top_spot"),
         InlineKeyboardButton("  ",             callback_data="top_long")],
        [InlineKeyboardButton("  ",             callback_data="top_short"),
         InlineKeyboardButton("  ",        callback_data="menu_full")],
        [InlineKeyboardButton("   ",   callback_data="top_trades"),
         InlineKeyboardButton("  ",      callback_data="channel_signals")],
        [InlineKeyboardButton(" On-Chain (Lookonchain)", callback_data="onchain_info")],
    ])

def back_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("  ", callback_data="show_menu"),
    ]])

async def send_coin(bot, chat_id, symbol, slug, a, text):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(" TradingView",    url=tv_link(symbol)),
         InlineKeyboardButton(" CoinMarketCap",  url=cmc_link(slug))],
        [InlineKeyboardButton(" ",       callback_data=f"coin_{symbol}"),
         InlineKeyboardButton(" ",           callback_data="show_menu")],
    ])

    #     Supertrend    
    #       
    if "Supertrend: " in text or "Supertrend: ``" in text:
        try:
            st_data = get_supertrend_signal(symbol)
            if st_data.get("label") and st_data["label"] != "":
                a["st_label"] = st_data["label"]
                #   
                text = text.replace("Supertrend: ``", f"Supertrend: `{st_data['label']}`")
                text = text.replace("Supertrend: ",   f"Supertrend: {st_data['label']}")
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

    #   Rocket Score    
    def good_long(a):
        """ -"""
        return (a["is_long"]
                and not a.get("suspicious", False)
                and a["ch24h"] > -3        #   
                and a["ch7d"]  > -10       #    
                and a["rsi_4h"] <= 80      #   
                and a["vol"] >= 500_000)   #   $500K

    def good_short(a):
        """ -"""
        return (not a["is_long"]
                and not a.get("suspicious", False)
                and a["ch24h"] < 3         #    
                and a["rsi_4h"] >= 20      #   
                and a["vol"] >= 500_000)

    rockets = sorted(
        [(c,a) for c,a in analyzed
         if a["rocket"] >= 68 and good_long(a)
         and a["ch7d"] > 0],              #     
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

    #  
    rocket_syms = {c["symbol"] for c,a in rockets}
    longs = [(c,a) for c,a in longs if c["symbol"] not in rocket_syms]

    nav = InlineKeyboardMarkup([[
        InlineKeyboardButton(" /1 ",    callback_data="market_overview"),
        InlineKeyboardButton(" /3 ",  callback_data="signals"),
        InlineKeyboardButton(" /5 ",   callback_data="rockets"),
    ]])

    header_lines = [
        " *BEST TRADE  *",
        f" {now_utc3()}",
        "",
    ]
    if rockets:
        header_lines.append(" *  (Rocket 65):*")
        for c, a in rockets:
            header_lines.append(f"   *{c['symbol']}*  Score `{a['rocket']}/100`  {a['rocket_label']}")
        header_lines.append("")
    header_lines += [
        f" : {len(longs)+len(rockets)}  |   : {len(shorts)}",
        f" -500 CoinMarketCap",
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
            a["st_label"] = ""
        stats  = get_binance_24h(sym)
        extras = get_market_extras(sym)  #  + OI
        text   = build_signal_text(sym, a, stats, extras=extras)
        await send_coin(bot, chat_id, sym, slug, a, text)
        await asyncio.sleep(2.0)  #    -  

    for coin, a in rockets:
        await _send(coin, a)
    for coin, a in longs:
        await _send(coin, a)
    for coin, a in shorts:
        await _send(coin, a)

    await bot.send_message(chat_id,
        " *:* 2-3% \n **    !",
        parse_mode="Markdown")

# 
# HANDLERS
# 
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
        " *BEST TRADE v34.0*\n"
        "\n\n"
        " *  *\n\n"
        " *:*\n"
        " SMC/ICT  Order Blocks  FVG  BOS\n"
        " EMA 20/50/200  RSI  MACD  Supertrend\n"
        " Wyckoff  AMD  Power of Three\n"
        " Multi-TF Confluence  Killzone\n\n"
        " *:*\n"
        " 11     \n"
        " On-chain  (Lookonchain)\n"
        "   TP/SL \n\n"
        " *:* 12%   SL \n\n"
        "  :",
        parse_mode="Markdown", reply_markup=main_kb()
    )

async def cmd_market(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("   ...")
    try:
        prices = get_btc_eth_price()
        gm     = get_global_metrics()
        coins  = get_all_coins()
        if not prices or not coins:
            await msg.edit_text("    API"); return

        btc = prices.get("BTC", {})
        eth = prices.get("ETH", {})
        btc_price = btc.get("price", 0)
        eth_price = eth.get("price", 0)
        btc_ch24  = btc.get("percent_change_24h", 0) or 0
        eth_ch24  = eth.get("percent_change_24h", 0) or 0

        # 
        btc_dom    = gm.get("btc_dominance", 0)
        eth_dom    = gm.get("eth_dominance", 0)
        total_mcap = gm.get("total_market_cap", 0)
        mcap_ch    = gm.get("total_market_cap_yesterday_percentage_change", 0) or 0

        #     CMC 
        pos = sum(1 for c in coins[:200]
                  if (c["quote"]["USDT"].get("percent_change_24h") or 0) > 0)
        pct = pos / 200 * 100
        if pct >= 65:    sentiment = " "
        elif pct >= 50:  sentiment = " "
        else:            sentiment = " "

        #  /    CMC  ()
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
                long_lines.append(f"   *{sym}*  `{fp(p)}`  `{fc(ch)}`")
        for c in sorted(coins[:100],
                        key=lambda c: c["quote"]["USDT"].get("percent_change_24h",0) or 0)[:5]:
            sym = c["symbol"]
            ch  = c["quote"]["USDT"].get("percent_change_24h", 0) or 0
            p   = c["quote"]["USDT"].get("price", 0)
            vol = c["quote"]["USDT"].get("volume_24h", 0) or 0
            if vol >= 2_000_000:
                short_lines.append(f"   *{sym}*  `{fp(p)}`  `{fc(ch)}`")

        ta = trend_arrow
        lines = [
            " *   BEST TRADE*",
            "",
            f" {now_utc3()}",
            "",
            f" *Bitcoin*   `${btc_price:,.0f}`  {ta(btc_ch24)} `{fc(btc_ch24)}`",
            f" *Ethereum*  `${eth_price:,.0f}`   {ta(eth_ch24)} `{fc(eth_ch24)}`",
            "",
            f" *:*  BTC `{btc_dom:.1f}%`    ETH `{eth_dom:.1f}%`",
            f" *Total MCap:*  `{fm(total_mcap)}`  `{fc(mcap_ch)}`",
            f" *:*  {sentiment}    `{pct:.0f}%`   ",
            "",
            "  *  24* ",
        ]
        lines += long_lines if long_lines else ["  "]
        lines += ["", "  *  24* "]
        lines += short_lines if short_lines else ["  "]
        lines += [
            "",
            "",
            " *:* 12%     SL      35x",
        ]

        await msg.edit_text("\n".join(lines), parse_mode="Markdown",
                            reply_markup=overview_kb(), disable_web_page_preview=True)
    except Exception as e:
        log.error(f"cmd_market: {e}")
        await msg.edit_text(
            f"   \n\n  ",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(" ", callback_data="market_overview"),
                InlineKeyboardButton(" ",     callback_data="show_menu"),
            ]])
        )

async def cmd_coin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text(": `/2 BTC`", parse_mode="Markdown")
        return
    symbol = ctx.args[0].upper()
    msg    = await update.message.reply_text(f"  {symbol}...")
    coins  = get_top500()
    coin   = next((c for c in coins if c["symbol"] == symbol), None)
    if not coin:
        await msg.edit_text(f" {symbol}    -500")
        return
    a      = full_analysis(coin)
    slug   = coin.get("slug", symbol.lower())
    try:
        st_data = get_supertrend_signal(symbol)
        a["st_label"] = st_data["label"]
    except:
        a["st_label"] = ""
    stats  = get_binance_24h(symbol)
    atl    = get_binance_alltime_low(symbol)
    extras = get_market_extras(symbol)
    text   = build_signal_text(symbol, a, stats, atl, extras)
    await msg.delete()
    await send_coin(ctx.bot, update.effective_chat.id, symbol, slug, a, text)

async def cmd_signals(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("  -500... ~60 ")
    coins = get_top500()
    if not coins:
        await msg.edit_text("  "); return
    await msg.delete()
    await send_signals_batch(ctx.bot, update.effective_chat.id, coins)

async def cmd_top(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg   = await update.message.reply_text(" ...")
    coins = get_top500()
    if not coins:
        await msg.edit_text("  "); return
    up  = sorted(coins, key=lambda x: x["quote"]["USDT"].get("percent_change_24h", 0), reverse=True)
    dn  = sorted(coins, key=lambda x: x["quote"]["USDT"].get("percent_change_24h", 0))
    pos = sum(1 for c in coins if c["quote"]["USDT"].get("percent_change_24h", 0) > 0)

    def row(i, c):
        q  = c["quote"]["USDT"]
        ch = q.get("percent_change_24h", 0)
        em = "" if ch >= 5 else ("" if ch >= 0 else ("" if ch >= -5 else ""))
        return f"{em} {i}. *{c['symbol']}*  ${fp(q['price'])}  {fc(ch)}"

    nav = InlineKeyboardMarkup([[
        InlineKeyboardButton(" /1 ",   callback_data="market_overview"),
        InlineKeyboardButton(" /3 ", callback_data="signals"),
        InlineKeyboardButton(" /5 ",  callback_data="rockets"),
    ]])
    t1 = [f" *-500  BEST TRADE*", f" {now_utc3()}",
          f": {pos}/{len(coins)} ({pos/len(coins)*100:.0f}%)", "",
          " *  24*"]
    t1 += [row(i, c) for i, c in enumerate(up[:15], 1)]
    t2  = [" *  24*"]
    t2 += [row(i, c) for i, c in enumerate(dn[:15], 1)]

    await msg.edit_text("\n".join(t1), parse_mode="Markdown", reply_markup=nav)
    await ctx.bot.send_message(update.effective_chat.id, "\n".join(t2),
                               parse_mode="Markdown", reply_markup=nav)

async def cmd_rockets(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """   Rocket Score   """
    msg = await update.message.reply_text("    -500... ~30 ")
    coins = get_top500()
    if not coins:
        await msg.edit_text("  "); return

    analyzed = [(c, full_analysis(c)) for c in coins]

    #  :  , , RSI  
    rockets = sorted(
        [(c,a) for c,a in analyzed
         if not a.get("suspicious", False)
         and a["vol"] >= 1_000_000
         and a["rsi_4h"] <= 82
         and (a["is_long"] and a["ch7d"] > -5 or not a["is_long"])],
        key=lambda x: x[1]["rocket"], reverse=True
    )[:10]

    nav = InlineKeyboardMarkup([[
        InlineKeyboardButton(" /1 ",   callback_data="market_overview"),
        InlineKeyboardButton(" /3 ", callback_data="signals"),
    ]])

    lines = [
        " *BEST TRADE  *",
        f" {now_utc3()}",
        f"-10  Rocket Score  500 ",
        "",
    ]
    for i, (c, a) in enumerate(rockets, 1):
        sym  = c["symbol"]
        r    = a["rocket"]
        filled = int(r / 10)
        bar  = "" * filled + "" * (10 - filled)
        side = " LONG" if a["is_long"] else " SHORT"
        #   SMC  ( BB Squeeze)
        smc_clean = [f for f in a.get("smc_factors", []) if "BB Squeeze" not in f]
        smc  = " | ".join(smc_clean[:3]) or ""
        rsi_warn = " " if a["rsi_4h"] > 70 else ""
        lines += [
            f"{i}. *{sym}*  `{r}/100` {a['rocket_label']}  {side}",
            f"   `{bar}`",
            f"   `{fp(a['price'])}`  24H`{fc(a['ch24h'])}`  7D`{fc(a['ch7d'])}`  RSI`{a['rsi_4h']:.0f}`{rsi_warn}",
            f"    {smc}",
            "",
        ]
    lines.append(" : 2%  | SL ")

    await msg.delete()
    await ctx.bot.send_message(
        update.effective_chat.id, "\n".join(lines),
        parse_mode="Markdown", reply_markup=nav,
        disable_web_page_preview=True
    )

    #  -3  
    for coin, a in rockets[:3]:
        sym   = coin["symbol"]
        slug  = coin.get("slug", sym.lower())
        stats = get_binance_24h(sym)
        text  = build_signal_text(sym, a, stats)
        await send_coin(ctx.bot, update.effective_chat.id, sym, slug, a, text)
        await asyncio.sleep(1.5)
    msg   = await update.message.reply_text(" ...")
    coins = get_top500()
    if not coins:
        await msg.edit_text("  "); return
    now = datetime.now(TZ).strftime("%d.%m.%Y %H:%M UTC+3")
    up  = sorted(coins, key=lambda x: x["quote"]["USDT"].get("percent_change_24h", 0), reverse=True)
    dn  = sorted(coins, key=lambda x: x["quote"]["USDT"].get("percent_change_24h", 0))
    pos = sum(1 for c in coins if c["quote"]["USDT"].get("percent_change_24h", 0) > 0)

    def row(i, c):
        q  = c["quote"]["USDT"]
        ch = q.get("percent_change_24h", 0)
        em = "" if ch >= 5 else ("" if ch >= 0 else ("" if ch >= -5 else ""))
        return f"{em} {i}. *{c['symbol']}*  ${fp(q['price'])}  {fc(ch)}"

    t1 = [f" *-500  BEST TRADE*", f" {now}",
          f": {pos}/{len(coins)} ({pos/len(coins)*100:.0f}%)", "",
          " *  24*"]
    t1 += [row(i, c) for i, c in enumerate(up[:15], 1)]
    t2  = [" *  24*"]
    t2 += [row(i, c) for i, c in enumerate(dn[:15], 1)]

    nav = InlineKeyboardMarkup([[
        InlineKeyboardButton(" /1 ", callback_data="market_overview"),
        InlineKeyboardButton(" /3 ", callback_data="signals"),
    ]])
    await msg.edit_text("\n".join(t1), parse_mode="Markdown", reply_markup=nav)
    await ctx.bot.send_message(update.effective_chat.id, "\n".join(t2),
                               parse_mode="Markdown", reply_markup=nav)

async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; data = q.data; await q.answer()

    #     
    if data == "show_menu":
        await q.edit_message_text(
            " *BEST TRADE v34.0*\n"
            "\n\n"
            " *  *\n\n"
            " SMC  ICT  Wyckoff  AMD  Multi-TF\n"
            " 11   On-chain  Killzone\n\n"
            "  :",
            parse_mode="Markdown", reply_markup=main_kb()
        )

    elif data == "top_spot":
        await q.edit_message_text("   ...", parse_mode="Markdown")
        class FakeUpdate:
            effective_chat = q.message.chat
            message        = q.message
        await cmd_top_spot(FakeUpdate(), ctx)

    elif data == "top_long":
        try: await q.message.delete()
        except: pass
        msg_sent = await ctx.bot.send_message(q.message.chat_id, "   -... ~40 ")
        class FakeMsgLong:
            chat_id = q.message.chat_id
            async def reply_text(self, text, **kw):
                return await msg_sent.edit_text(text, **kw)
            async def edit_text(self, text, **kw):
                return await msg_sent.edit_text(text, **kw)
        class FakeUpdateLong:
            effective_chat = q.message.chat
            message = FakeMsgLong()
        await cmd_top_long(FakeUpdateLong(), ctx)

    elif data == "top_short":
        await q.edit_message_text("   ...", parse_mode="Markdown")
        class FakeUpdate:
            effective_chat = q.message.chat
            message        = q.message
        await cmd_top_short(FakeUpdate(), ctx)

    elif data == "menu_full":
        await q.edit_message_text(
            " *  *\n"
            "\n\n"
            "   :\n"
            "`/full BTC`  `/full ETH`  `/full SOL`\n"
            "`/full SYMBOL`   \n\n"
            " * 6  :*\n"
            " SMC/ICT  OB  FVG  BOS  CHoCH  Sweep\n"
            " Wyckoff   /\n"
            " AMD  Power of Three (Asia/London/NY)\n"
            " Multi-TF  confluence 1H/4H/1D/1W\n"
            " Volume Profile  OI  Funding Rate\n"
            " Macro  Gold  USDT.D  ETH/BTC ratio\n\n"
            " *:*\n"
            "Entry  TP1/TP2/TP3  SL  Score 0100\n"
            "Killzone    A+/A/B/C",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("  ", callback_data="show_menu")],
            ])
        )

    elif data in ("game", "top_trades"):
        nav = InlineKeyboardMarkup([
            [InlineKeyboardButton(" ",     callback_data="top_trades"),
             InlineKeyboardButton("  ", callback_data="show_menu")],
        ])

        lines = [f" *BEST TRADE    *", f" {now_utc3()}", ""]
        has_signals = False
        total = len(TOP_LONG_SIGNALS) + len(TOP_SHORT_SIGNALS) + len(TOP_SPOT_SIGNALS)

        if total > 0:
            lines[2] = f" *  : {total}*\n"

        #   
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

                # 
                if cur >= tp3:          status = " TP3 !"
                elif cur >= tp2:        status = " TP2 !"
                elif cur >= tp1:        status = " TP1   "
                elif cur > entry*1.005: status = " "
                elif dist <= 1:        status = "   !"
                elif dist <= 2:        status = f"   {dist:.1f}%"
                elif cur <= sl*1.01:   status = "   SL!"
                else:                  status = f"   {dist:.1f}%"

                lines += [
                    f" [{sym}USDT]({tv})   ",
                    f"    `{fp(entry)}`   `{fp(cur)}`  `{move:+.1f}%`",
                    f"   TP1 `{fp(tp1)}`  TP2 `{fp(tp2)}`  SL `{fp(sl)}`",
                    f"  {status}",
                    f"   {t} UTC+3",
                    "",
                ]

        #   
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

                if cur <= tp2:          status = " TP2 !"
                elif cur <= tp1:        status = " TP1   "
                elif cur < entry*0.995: status = " "
                elif dist <= 1:        status = "   !"
                elif dist <= 2:        status = f"   {dist:.1f}%"
                elif cur >= sl*0.99:   status = "   SL!"
                else:                  status = f"   {dist:.1f}%"

                lines += [
                    f" [{sym}USDT]({tv})   ",
                    f"    `{fp(entry)}`   `{fp(cur)}`  `{move:+.1f}%`",
                    f"   TP1 `{fp(tp1)}`  TP2 `{fp(tp2)}`  SL `{fp(sl)}`",
                    f"  {status}",
                    f"   {t} UTC+3",
                    "",
                ]

        #    
        if TOP_SPOT_SIGNALS:
            has_signals = True
            for sym, v in TOP_SPOT_SIGNALS.items():
                tv     = tv_link(sym)
                t      = v["time"].strftime("%d.%m %H:%M")
                buy_lo = v.get("buy_zone_lo", v["entry"])
                buy_hi = v.get("buy_zone_hi", v["entry"])
                sell_t = v.get("sell_target", 0)
                lines += [
                    f" [{sym}USDT]({tv})   ",
                    f"    `{fp(buy_lo)}`  `{fp(buy_hi)}`",
                    f"    `{fp(sell_t)}`",
                    f"   {t} UTC+3",
                    "",
                ]

        #   
        done_l = {s: v for s, v in TOP_LONG_SIGNALS.items()  if v.get("status") == "done"}
        done_s = {s: v for s, v in TOP_SHORT_SIGNALS.items() if v.get("status") == "done"}
        if done_l or done_s:
            lines.append(" *:*")
            for sym, v in list(done_l.items())[:5]:
                tv = tv_link(sym)
                t  = v["time"].strftime("%d.%m %H:%M")
                lines.append(f" [{sym}USDT]({tv})   ")
                lines.append(f"   {t} UTC+3")
            for sym, v in list(done_s.items())[:5]:
                tv = tv_link(sym)
                t  = v["time"].strftime("%d.%m %H:%M")
                lines.append(f" [{sym}USDT]({tv})   ")
                lines.append(f"   {t} UTC+3")

        if not has_signals:
            lines += [
                " *  *\n",
                "    30 .",
                "  :",
                "        ",
            ]

        try:
            await q.edit_message_text(
                "\n".join(lines), parse_mode="Markdown",
                reply_markup=nav, disable_web_page_preview=False
            )
        except: await q.answer(" ")
        #  
        parts = data.split("_")
        action = parts[0]   # tp / sl
        mode   = parts[1]   # long / short
        sym    = parts[2]
        result = f" TP " if action == "tp" else " SL "
        store  = TOP_LONG_SIGNALS if mode == "long" else TOP_SHORT_SIGNALS
        if sym in store:
            store[sym]["status"] = "done"
            store[sym]["result"] = result
        await q.answer(f"{result}  {sym}USDT")
        await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(f"{'' if action=='tp' else ''} {result}  {sym}", callback_data="noop"),
            InlineKeyboardButton(" ", callback_data="show_menu"),
        ]]))

    elif data.startswith("full_"):
        symbol = data[5:]
        await q.edit_message_text(f"   *{symbol}*...", parse_mode="Markdown")
        try: await q.message.delete()
        except: pass
        await _do_full_analysis(ctx.bot, q.message.chat_id, symbol)

    elif data == "market_overview":
        await q.edit_message_text("  ...", parse_mode="Markdown")
        try:
            #  FakeUpdate    cmd_market
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
            await ctx.bot.send_message(q.message.chat_id, "   ")

    elif data == "signals":
        await q.edit_message_text("  ...", parse_mode="Markdown")
        coins = get_top500()
        if not coins:
            await q.edit_message_text("  "); return
        await send_signals_batch(ctx.bot, q.message.chat_id, coins)

    elif data == "rockets":
        await q.edit_message_text("  ...", parse_mode="Markdown")
        coins = get_top500()
        if not coins:
            await q.edit_message_text("  "); return
        analyzed = [(c, full_analysis(c)) for c in coins]
        rockets  = sorted([(c,a) for c,a in analyzed
                           if not a.get("suspicious", False)],
                          key=lambda x: x[1]["rocket"], reverse=True)[:10]
        nav = InlineKeyboardMarkup([[
            InlineKeyboardButton(" /1 ",   callback_data="market_overview"),
            InlineKeyboardButton(" /3 ", callback_data="signals"),
        ]])
        lines = [" *  Rocket Score*", f" {now_utc3()}", ""]
        for i, (c, a) in enumerate(rockets, 1):
            r = a["rocket"]; bar = ""*int(r/10)+""*(10-int(r/10))
            side = "L" if a["is_long"] else "S"
            lines.append(f"{i}. *{c['symbol']}* `{r}/100` {side}  `{bar}`")
        await q.edit_message_text("\n".join(lines), parse_mode="Markdown",
                                  reply_markup=nav, disable_web_page_preview=True)

    elif data == "precision":
        #     fake update
        await q.answer("  Precision Shots...")
        await q.edit_message_text("   /7  Precision Shots", parse_mode="Markdown")

    elif data == "game":
        text = f"\U0001f550 {now_utc3()}\n\n" + build_game_digest()
        nav  = InlineKeyboardMarkup([[
            InlineKeyboardButton("\U0001f504 ",  callback_data="game"),
            InlineKeyboardButton("\U0001f30d ",     callback_data="market_overview"),
        ]])
        try:
            await q.edit_message_text(text, parse_mode="Markdown",
                                      reply_markup=nav, disable_web_page_preview=False)
        except:
            await q.answer(" ")

    elif data == "report":
        await q.edit_message_text(" ...", parse_mode="Markdown")
        coins = get_top500()
        if coins:
            up  = sorted(coins, key=lambda x: x["quote"]["USDT"].get("percent_change_24h", 0), reverse=True)
            txt = "\n".join([f" *  24*", f" {now_utc3()}"] +
                            [f"{i}. *{c['symbol']}*  ${fp(c['quote']['USDT']['price'])}  {fc(c['quote']['USDT'].get('percent_change_24h',0))}"
                             for i, c in enumerate(up[:20], 1)])
            await q.edit_message_text(txt, parse_mode="Markdown",
                                      reply_markup=overview_kb(), disable_web_page_preview=True)

    elif data.startswith("coin_"):
        symbol = data[5:]; cid = q.message.chat_id
        await q.edit_message_text(f"  {symbol}...")
        coins = get_top500()
        coin  = next((c for c in coins if c["symbol"] == symbol), None)
        if not coin:
            await q.edit_message_text(f" {symbol}  "); return
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
            await q.edit_message_text("  "); return
        up  = sorted(coins, key=lambda x: x["quote"]["USDT"].get(field, 0), reverse=True)
        lbl = {"1h": "1 ", "24h": "24 ", "7d": "7 "}.get(period, "24 ")
        txt = "\n".join([f" *  {lbl}*", f" {now_utc3()}", ""] +
                        [f"{i}. *{c['symbol']}*  ${fp(c['quote']['USDT']['price'])}  {fc(c['quote']['USDT'].get(field,0))}"
                         for i, c in enumerate(up[:15], 1)])
        await q.edit_message_text(txt, parse_mode="Markdown",
                                  reply_markup=overview_kb(), disable_web_page_preview=True)

    elif data == "onchain_info":
        nav = InlineKeyboardMarkup([
            [InlineKeyboardButton(" ", callback_data="onchain_info"),
             InlineKeyboardButton(" ",     callback_data="show_menu")],
        ])
        try:
            await q.edit_message_text(
                " *On-Chain *\n"
                "\n\n"
                " *Lookonchain* \n\n"
                " * :*\n"
                "   (>$1M)\n"
                " BlackRock / Grayscale ETF \n"
                " Bitcoin ETF NetFlow ()\n"
                " /  \n"
                "   \n\n"
                " *  *  \n"
                " Reader   \n\n"
                "   *10 *\n\n"
                f" {now_utc3()}",
                parse_mode="Markdown", reply_markup=nav
            )
        except Exception as e:
            if "not modified" in str(e).lower():
                await q.answer(" ")

    elif data == "channel_signals":
        await _show_channel_signals(q)

# 
#     (Telethon reader)
# 

_READER_SIGNALS_FILE = "/tmp/reader_signals.json"

async def _show_channel_signals(q):
    """
       reader.py   .
    reader.py   /tmp/reader_signals.json
    : [{"channel": str, "time": str, "text": str, "symbol": str|None, ...}]
    """
    nav = InlineKeyboardMarkup([
        [InlineKeyboardButton(" ", callback_data="channel_signals"),
         InlineKeyboardButton(" ",     callback_data="show_menu")],
    ])

    try:
        msg_text = (
            " * *\n"
            "\n\n"
            " Reader v5 \n"
            "  *11 *   \n"
            " On-chain: *Lookonchain* ( 10 )\n\n"
            " * :*\n"
            " PIXEL\n"
            "  \n"
            "  \n"
            " Scalping Blog | \n"
            " Kira | ICT\n"
            "  \n"
            " MANIPULATOR\n"
            " VAGR TRADING\n"
            " ANNA TRADE\n"
            " 2Trade  Kirill Sobolev\n"
            "    | \n\n"
            "   TP/SL  **  \n\n"
            f" {now_utc3()}"
        )
        try:
            await q.edit_message_text(msg_text, parse_mode="Markdown", reply_markup=nav)
        except Exception as e:
            if "not modified" in str(e).lower():
                #        
                try:
                    await q.message.delete()
                    await ctx.bot.send_message(
                        q.message.chat_id, msg_text,
                        parse_mode="Markdown", reply_markup=nav
                    )
                except:
                    await q.answer(" ")
            else:
                raise e
        return

        signals = []
        if not signals:
            pass

        #   ,   24
        cutoff = datetime.now(TZ).timestamp() - 86400
        recent = [s for s in signals
                  if s.get("ts", 0) > cutoff or not s.get("ts")]
        recent.sort(key=lambda x: x.get("ts", 0), reverse=True)

        lines = [
            " *BEST TRADE   *",
            f" {now_utc3()}",
            f"  24: *{len(recent)} *",
            "",
        ]

        #   
        by_channel: dict = {}
        for s in recent:
            ch = s.get("channel", "")
            by_channel.setdefault(ch, []).append(s)

        for ch_name, ch_signals in list(by_channel.items())[:10]:
            lines.append(f"* {ch_name}*")
            for sig in ch_signals[:3]:   #  3   
                t   = sig.get("time", "")
                sym = sig.get("symbol")
                txt = sig.get("summary", sig.get("text", ""))[:200]

                if sym:
                    #       
                    entry  = sig.get("entry")
                    tp1    = sig.get("tp1")
                    sl     = sig.get("sl")
                    side   = sig.get("side", "")
                    side_e = "" if side == "long" else ("" if side == "short" else "")
                    tv     = tv_link(sym)

                    sig_line = f"  {side_e} [{sym}USDT]({tv})"
                    if entry: sig_line += f"   `{fp(float(entry))}`"
                    if tp1:   sig_line += f"  TP `{fp(float(tp1))}`"
                    if sl:    sig_line += f"  SL `{fp(float(sl))}`"
                    lines.append(sig_line)
                    if t:
                        lines.append(f"   {t}")
                else:
                    #   
                    lines.append(f"   {txt}")
                    if t:
                        lines.append(f"   {t}")
            lines.append("")

        lines.append(f"_: {now_utc3()}_")

        text = "\n".join(lines)
        if len(text) > 4096:
            text = text[:4090] + "..."

        try:
            await q.edit_message_text(text, parse_mode="Markdown",
                                      reply_markup=nav, disable_web_page_preview=True)
        except Exception as edit_err:
            if "not modified" in str(edit_err).lower():
                await q.answer("  ")
            else:
                raise edit_err

    except Exception as e:
        log.error(f"channel_signals: {e}")
        try:
            await q.edit_message_text(
                f" : {str(e)[:200]}",
                parse_mode="Markdown", reply_markup=nav
            )
        except:
            await q.answer("  ")

# 
# 
# 
async def send_scheduled(bot: Bot):
    """
      30 .
           
           .
    """
    chat_ids = load_chat_ids() | user_chat_ids
    if not chat_ids:
        return

    log.info(f"[AUTO]   {now_utc3()}")

    try:
        coins = get_all_coins()
        if not coins:
            log.error("[AUTO]  ")
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
            if sym in already_sent:         continue  #     

            #  :   
            if (ch1h > 0.5 and ch24h > 2.0 and ch7d > 0
                    and vol >= 2_000_000 and sent_long < 10):

                tp1 = price * 1.02; tp2 = price * 1.04; tp3 = price * 1.08
                sl  = price * 0.85; swing = price * 0.92
                score = min(50 + int(ch1h*2 + ch24h*1.5), 95)

                a_stub = {
                    "price": price, "is_long": True,
                    "tp1": tp1, "tp2": tp2, "tp3": tp3,
                    "sl": sl, "swing": swing, "rr": 2.5,
                    "rocket": score, "rocket_label": " ",
                    "rsi_4h": 50.0, "rsi_1h": 50.0, "rsi_1d": 50.0,
                    "ch1h": ch1h, "ch24h": ch24h, "ch7d": ch7d,
                    "ch30d": ch30d, "ch90d": ch90d,
                    "vol": vol, "mcap": mcap, "rank": rank,
                    "above_ema20": ch24h > 0, "above_ema50": ch7d > 0,
                    "above_ema200": False,
                    "macd_bullish": ch1h > 0, "macd_bearish": False,
                    "bb_squeeze": False, "vol_spike": False,
                    "smc_factors": [], "suspicious": False,
                    "st_label": "", "trend_4h": "bullish",
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
                        log.error(f"[AUTO LONG] {sym}  {cid}: {e}")
                await asyncio.sleep(1.0)
                continue  #   

            #  :   
            if (ch1h < -0.5 and ch24h < -2.0
                    and vol >= 2_000_000 and sent_short < 10):

                tp1 = price * 0.98; tp2 = price * 0.96; tp3 = price * 0.92
                sl  = price * 1.15; swing = price * 1.08
                score = min(50 + int(abs(ch1h)*2 + abs(ch24h)*1.5), 95)

                a_stub = {
                    "price": price, "is_long": False,
                    "tp1": tp1, "tp2": tp2, "tp3": tp3,
                    "sl": sl, "swing": swing, "rr": 2.5,
                    "rocket": score, "rocket_label": " ",
                    "rsi_4h": 65.0, "rsi_1h": 65.0, "rsi_1d": 55.0,
                    "ch1h": ch1h, "ch24h": ch24h, "ch7d": ch7d,
                    "ch30d": ch30d, "ch90d": ch90d,
                    "vol": vol, "mcap": mcap, "rank": rank,
                    "above_ema20": False, "above_ema50": False,
                    "above_ema200": False,
                    "macd_bullish": False, "macd_bearish": True,
                    "bb_squeeze": False, "vol_spike": False,
                    "smc_factors": [], "suspicious": False,
                    "st_label": "", "trend_4h": "bearish",
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
                        log.error(f"[AUTO SHORT] {sym}  {cid}: {e}")
                await asyncio.sleep(1.0)
                continue

            #  :   
            if (ch90d < -40 and ch7d > 0
                    and vol >= 1_000_000 and mcap >= 10_000_000
                    and sent_spot < 3):

                x_ath = 1 / (1 + ch90d/100) if ch90d < -5 else 1.0
                buy2  = price * 0.95; buy1 = price * 0.88; buy3 = price * 0.78
                sell  = price * x_ath * 0.85

                if x_ath >= 5:   pot = f"~x{x_ath:.1f} "
                elif x_ath >= 3: pot = f"~x{x_ath:.1f} "
                else:            pot = f"~x{x_ath:.1f} "

                text = "\n".join(filter(None, [
                    f"*{sym}USDT*  **",
                    f"  BEST TRADE    Rank #{rank}",
                    "",
                    f" *:* `{fp(price)}`",
                    f" *:* *{pot}  ATH*",
                    "",
                    f" 90: *{fc(ch90d)}*  30: *{fc(ch30d)}*  7: *{fc(ch7d)}*",
                    "",
                    f" * 1 (40%):* `{fp(buy2)}`",
                    "",
                    f" * 2 (40%):* `{fp(buy1)}`",
                    "",
                    f" * 3 (20%):* `{fp(buy3)}`",
                    "",
                    f" *:* `{fp(sell)}`  *(~x{sell/price:.1f})*" if sell > price else "",
                    "",
                    f" : 510%     :  3 .",
                    f"#{sym}USDT",
                ]))

                a_stub = {
                    "price": price, "is_long": True,
                    "tp1": buy2, "tp2": buy1, "tp3": buy3,
                    "sl": price*0.70, "swing": price*0.80, "rr": 3.0,
                    "rocket": 70, "rocket_label": " ",
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
                        log.error(f"[AUTO SPOT] {sym}  {cid}: {e}")
                await asyncio.sleep(1.0)

            #    
            if sent_long >= 10 and sent_short >= 10 and sent_spot >= 3:
                break

        log.info(
            f"[AUTO] :  {sent_long}   "
            f" {sent_short}    {sent_spot} "
        )

    except Exception as e:
        log.error(f"[AUTO]  : {e}")


supertrend_cache = {}  # {symbol: last_direction}

async def check_supertrend_signals(bot, chat_ids, coins):
    """
      Supertrend   .
      BUYSELL  SELLBUY .
     -50   (  ).
    """
    now_ts = datetime.now(TZ).timestamp()
    #  -50   24h
    top_by_vol = sorted(coins,
                        key=lambda x: x["quote"]["USDT"].get("volume_24h", 0),
                        reverse=True)[:50]

    for coin in top_by_vol:
        sym = coin["symbol"]
        try:
            st_data = get_supertrend_signal(sym)
            new_dir = st_data["direction"]
            old_dir = supertrend_cache.get(sym)

            #  
            supertrend_cache[sym] = new_dir

            #    
            if old_dir is None or old_dir == new_dir:
                continue

            slug       = coin.get("slug", sym.lower())
            signal_lbl = " BUY" if new_dir == 1 else " SELL"
            prev_lbl   = " SELL" if new_dir == 1 else " BUY"
            price      = st_data["current_price"]
            pct        = st_data["pct_since_signal"]
            last_sig   = st_data.get("last_signal", "")
            last_price = st_data.get("last_signal_price")
            last_time  = st_data.get("last_signal_time")

            time_str = last_time.strftime("%d.%m %H:%M UTC+3") if last_time else ""
            pct_str  = f"+{pct:.2f}%" if pct >= 0 else f"{pct:.2f}%"

            text = (
                f" *SUPERTREND   !*\n"
                f" {now_utc3()}\n\n"
                f"*{sym}USDT*  {prev_lbl}  *{signal_lbl}*\n\n"
                f"  : `{fp(price)}`\n"
            )
            if last_price:
                text += f"  : `{fp(last_price)}` ({time_str})\n"
                text += f"   : `{pct_str}`\n"

            text += (
                f"\n"
                f"{'     ' if new_dir == 1 else '    '}\n\n"
                f" : 2%  | SL \n\n"
                f"#{sym}USDT"
            )
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(" TradingView", url=tv_link(sym)),
                InlineKeyboardButton("CMC", url=cmc_link(slug)),
            ]])
            for cid in chat_ids:
                try:
                    await bot.send_message(cid, text, parse_mode="Markdown", reply_markup=kb)
                except Exception as e:
                    log.error(f"ST alert {cid}: {e}")
            add_to_game(sym, "supertrend", price)
            log.info(f"Supertrend {sym}: {prev_lbl}{signal_lbl} @ {fp(price)}")
            await asyncio.sleep(0.5)

        except Exception as e:
            log.error(f"ST check {sym}: {e}")

watchlist_alerted = {}  # {symbol: timestamp}

# 
# PRECISION SHOTS    x10 
# 

def precision_shot_analysis(coin: dict, a: dict) -> dict:
    """
        x5-x10.
      :
    1. RECOVERY    -70%+  ATH,  
    2. BREAKOUT        
    3. ACCUMULATION  Smart Money  
     score 0-100   .
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

    #      ,   
    if suspicious:            return {"type": None, "ps": 0, "factors": []}
    if vol < 2_000_000:       return {"type": None, "ps": 0, "factors": []}  #   $2M
    if rank > 400:            return {"type": None, "ps": 0, "factors": []}  #   
    if price <= 0:            return {"type": None, "ps": 0, "factors": []}

    ps      = 0  # precision score
    factors = []
    setup   = None

    # 
    #  1: RECOVERY ( -70%+, )
    # :  ,     
    # : x3-x10    ATH
    # 
    deep_dump  = ch90d < -60    #  >60%  3 
    recovering = ch7d > 3       #    +3%
    accum_vol  = 5 <= vol_ratio <= 40  #     

    if deep_dump and recovering and accum_vol:
        setup = "RECOVERY"
        ps += 30
        factors.append(f"  -{ abs(ch90d):.0f}%  90")

        #  
        if rsi_4h < 40:
            ps += 15; factors.append(" RSI ")
        if ch7d > 8:
            ps += 10; factors.append(f"   +{ch7d:.0f}% 7")
        if ch24h > 3:
            ps += 8; factors.append(f"   +{ch24h:.1f}%")
        if ch1h > 0 and ch24h > 0:
            ps += 5; factors.append("    TF")
        if rank <= 100:
            ps += 10; factors.append(f" -100 (rank #{rank})")
        elif rank <= 200:
            ps += 5
        if vol_ratio >= 15:
            ps += 8; factors.append("  ")
        if ch30d > -20:  #     30
            ps += 7; factors.append("  30")

        #  x  (    )
        potential_x = max(1.0, abs(ch90d) / 30)

    # 
    #  2: BREAKOUT (  )
    # :   +    
    # : x2-x5  2-4 
    # 
    elif (abs(ch30d) < 15        # 30  (   )
          and ch7d > 5            #    +5% ( )
          and ch24h > 5           #  +5%
          and vol_ratio >= 10):   #  

        setup = "BREAKOUT"
        ps += 25
        factors.append(f"    30")

        if ch24h > 15:
            ps += 15; factors.append(f"   +{ch24h:.0f}% 24")
        elif ch24h > 10:
            ps += 10; factors.append(f"  +{ch24h:.0f}% 24")
        if vol_ratio >= 20:
            ps += 12; factors.append(f"   {vol_ratio:.0f}%")
        elif vol_ratio >= 15:
            ps += 7
        if rsi_4h < 65:  #    
            ps += 8; factors.append(" RSI   ")
        if ch1h > 2:
            ps += 5; factors.append("   ")
        if rank <= 50:
            ps += 10; factors.append(f"   #{rank}")
        elif rank <= 150:
            ps += 5

        potential_x = 2.0 + (ch24h / 20)

    # 
    #  3: ACCUMULATION ( )
    # :  ,    =   
    # : x3-x8  
    # 
    elif (abs(ch24h) < 3         #    
          and abs(ch7d) < 10     #   
          and vol_ratio >= 12    #   
          and rsi_4h < 55):      # RSI /

        setup = "ACCUMULATION"
        ps += 20
        factors.append("   ( ,  )")

        if vol_ratio >= 20:
            ps += 15; factors.append(f"  {vol_ratio:.0f}%  Smart Money ")
        elif vol_ratio >= 15:
            ps += 8
        if rsi_4h < 35:
            ps += 12; factors.append(" RSI    ")
        elif rsi_4h < 45:
            ps += 6
        if ch90d < -40:  #    
            ps += 10; factors.append(f"    -{abs(ch90d):.0f}% (?)")
        if rank <= 100:
            ps += 10; factors.append(f" -100 (#{rank})  ")
        elif rank <= 200:
            ps += 5
        if abs(ch30d) < 5:  #  
            ps += 8; factors.append("   30")

        potential_x = 3.0 + (abs(ch90d) / 25)

    else:
        return {"type": None, "ps": 0, "factors": []}

    ps = min(100, ps)

    #   
    if ps >= 75:   quality = "  "
    elif ps >= 60: quality = "  "
    elif ps >= 45: quality = "  "
    else:          quality = " "

    return {
        "type":       setup,
        "ps":         ps,
        "factors":    factors,
        "quality":    quality,
        "potential_x": round(potential_x, 1),
    }


async def cmd_precision(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /7  PRECISION SHOTS
    1-3     x5-x10
       5+ 
    """
    msg = await update.message.reply_text(
        " *PRECISION SHOTS*\n  -500...\n~45 ",
        parse_mode="Markdown"
    )
    coins = get_top500()
    if not coins:
        await msg.edit_text("  "); return

    results = []
    for coin in coins:
        a  = full_analysis(coin)
        ps = precision_shot_analysis(coin, a)
        if ps["type"] and ps["ps"] >= 45:
            results.append((coin, a, ps))

    #   Precision Score
    results.sort(key=lambda x: x[2]["ps"], reverse=True)
    top = results[:5]  #  -5

    if not top:
        await msg.edit_text(
            " *PRECISION SHOTS*\n\n"
            "       .\n"
            "       .\n\n"
            "_  30 _",
            parse_mode="Markdown"
        )
        return

    nav = InlineKeyboardMarkup([[
        InlineKeyboardButton(" /1 ",   callback_data="market_overview"),
        InlineKeyboardButton(" /5 ",  callback_data="rockets"),
        InlineKeyboardButton(" ",   callback_data="precision"),
    ]])

    # 
    header = [
        " *PRECISION SHOTS  BEST TRADE*",
        f" {now_utc3()}",
        f" : {len(results)}  500 ",
        "",
        " *: 5+ * ",
        "",
    ]
    await msg.edit_text("\n".join(header), parse_mode="Markdown")

    #       
    for coin, a, ps_data in top:
        sym   = coin["symbol"]
        slug  = coin.get("slug", sym.lower())
        setup = ps_data["type"]
        score = ps_data["ps"]
        qual  = ps_data["quality"]
        px    = ps_data["potential_x"]
        facts = ps_data["factors"]

        #  
        type_icon = {"RECOVERY": "", "BREAKOUT": "", "ACCUMULATION": ""}.get(setup, "")
        type_name = {"RECOVERY": "RECOVERY", "BREAKOUT": "BREAKOUT", "ACCUMULATION": ""}.get(setup, setup)

        # Supertrend
        try:
            st_data = get_supertrend_signal(sym)
            a["st_label"] = st_data["label"]
        except:
            a["st_label"] = ""

        stats  = get_binance_24h(sym)
        extras = get_market_extras(sym)

        #  
        is_long = a["is_long"]
        side_e  = "" if is_long else ""
        side_t  = "LONG" if is_long else "SHORT"

        def pct(t, p=a["price"]):
            d = (t - p) / p * 100
            v = d if is_long else -d
            return f"+{v:.2f}%" if v >= 0 else f"{v:.2f}%"

        vol_str = (f"${a['vol']/1e9:.2f}B" if a['vol'] >= 1e9 else
                   f"${a['vol']/1e6:.1f}M" if a['vol'] >= 1e6 else f"${a['vol']/1e3:.0f}K")

        filled = int(score / 10)
        bar = "" * filled + "" * (10 - filled)

        lines = [
            f" *{sym}USDT*  {side_e} *{side_t}*",
            f" {now_utc3()}",
            "",
            f"{type_icon} *{type_name}*  |  Precision: `{score}/100`",
            f"`{bar}`",
            f"{qual}",
            f" : *~x{px}*",
            "",
            " *  :*",
        ]
        for f_ in facts:
            lines.append(f"  {f_}")

        lines += [
            "",
            f" :  `{fp(a['price'])}`",
            f" TP1:  `{fp(a['tp1'])}`  ({pct(a['tp1'])})",
            f" TP2:  `{fp(a['tp2'])}`  ({pct(a['tp2'])})",
            f" TP3:  `{fp(a['tp3'])}`  ({pct(a['tp3'])})",
            f" SL:   `{fp(a['sl'])}`",
            "",
            "",
            f" R:R `1:{a['rr']:.1f}`  |   {vol_str}  |  Rank `#{a['rank']}`",
            f" RSI 4H `{a['rsi_4h']:.0f}`  |  ST: `{a['st_label']}`",
            f" 1H`{fc(a['ch1h'])}`  24H`{fc(a['ch24h'])}`  7D`{fc(a['ch7d'])}`  90D`{fc(a['ch90d'])}`",
        ]

        if extras:
            fr = extras.get("funding", {})
            oi = extras.get("oi", {})
            if fr.get("ok"):
                lines.append(f" : `{fr['rate']:+.4f}%`  {fr['signal']}")
            if oi.get("ok") and oi.get("change", 0) != 0:
                lines.append(f" OI: `{oi['change']:+.1f}%`  {oi['signal']}")

        if stats:
            h24 = stats.get("high", 0); l24 = stats.get("low", 0)
            if h24 and l24:
                best = l24 * 1.005 if is_long else h24 * 0.995
                lines.append(f" 24H: `{fp(h24)}` `{fp(l24)}`  : `{fp(best)}`")

        lines += ["", f" : *2% * | SL ", f"#{sym}USDT"]

        text = "\n".join(lines)
        await send_coin(ctx.bot, update.effective_chat.id, sym, slug, a, text)
        await asyncio.sleep(2.0)

    # 
    await ctx.bot.send_message(
        update.effective_chat.id,
        f" *Precision  *\n"
        f": {len(top)}  \n\n"
        f" * :*\n"
        f" RECOVERY  DCA ,  2-8 \n"
        f" BREAKOUT   ,   \n"
        f"    ,  \n\n"
        f"  ,  .  SL!",
        parse_mode="Markdown",
        reply_markup=nav
    )



async def check_watchlist(bot, chat_ids, coins):
    """       5 """
    now_ts   = datetime.now(TZ).timestamp()
    alerts   = check_watchlist_alerts(coins)
    for al in alerts:
        sym = al["symbol"]
        last = watchlist_alerted.get(sym, 0)
        if now_ts - last < 1800:  #     30 
            continue
        watchlist_alerted[sym] = now_ts
        text = (
            f" *   !*\n"
            f" {now_utc3()}\n\n"
            f"{al['emoji']} *{sym}USDT  {al['bias']}*\n"
            f" : `{fp(al['price'])}`\n"
            f" : `{fp(al['lo'])}  {fp(al['hi'])}`\n\n"
            f" {al['note']}\n"
            f" : {al['source']}\n\n"
            f" : 2%  | SL \n\n"
            f"#{sym}USDT"
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(" TradingView", url=tv_link(sym)),
        ]])
        for cid in chat_ids:
            try:
                await bot.send_message(cid, text, parse_mode="Markdown", reply_markup=kb)
            except Exception as e:
                log.error(f"Watchlist alert {cid}: {e}")
        add_to_game(sym, "watchlist", al["price"])
        log.info(f"Watchlist ALERT: {sym} @ {fp(al['price'])}")

async def check_spot_alerts(bot: Bot, chat_ids: set):
    """  -     (ATL/)"""
    if not TOP_SPOT_SIGNALS: return
    now_ts = datetime.now(TZ).timestamp()
    alerted_key = "_spot_alert"

    for sym, v in TOP_SPOT_SIGNALS.items():
        if v.get("status") == "done": continue
        buy_lo = v.get("buy_zone_lo", 0)
        buy_hi = v.get("buy_zone_hi", 0)
        if not buy_lo: continue

        #      2 
        last_alert = pump_alerted.get(f"{alerted_key}_{sym}", 0)
        if now_ts - last_alert < 7200: continue

        try:
            stats = get_binance_24h(sym)
            if not stats: continue
            cur_price = stats.get("low", 0) or stats.get("high", 0)
            if not cur_price: continue

            in_zone = buy_lo * 0.98 <= cur_price <= buy_hi * 1.05
            near_zone = cur_price <= buy_hi * 1.10  #  10%  

            if in_zone or near_zone:
                pump_alerted[f"{alerted_key}_{sym}"] = now_ts
                status_str = "    !" if in_zone else "    "
                text = (
                    f" *   {sym}USDT*\n"
                    f" {now_utc3()}\n\n"
                    f"{status_str}\n\n"
                    f"  : `{fp(cur_price)}`\n"
                    f"  : `{fp(buy_lo)}`  `{fp(buy_hi)}`\n"
                    f"  : `{fp(v.get('sell_target', 0))}`\n\n"
                    f"  DCA   \n"
                    f" :   5-10% \n\n"
                    f"#{sym}USDT  #"
                )
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton(" TradingView", url=tv_link(sym)),
                    InlineKeyboardButton(" TOP ",  callback_data="top_trades"),
                ]])
                for cid in chat_ids:
                    try:
                        await bot.send_message(cid, text, parse_mode="Markdown", reply_markup=kb)
                    except Exception as e:
                        log.error(f"Spot alert {cid}: {e}")
                log.info(f"SPOT ALERT: {sym} @ {fp(cur_price)} |  {fp(buy_lo)}-{fp(buy_hi)}")
        except Exception as e:
            log.error(f"check_spot_alerts {sym}: {e}")


async def check_entry_approach(bot: Bot, chat_ids: set):
    """       (1-2%)   / """
    now_ts = datetime.now(TZ).timestamp()

    for sym, v in list(TOP_LONG_SIGNALS.items()):
        if v.get("status") == "done": continue
        last_alert = pump_alerted.get(f"_entry_l_{sym}", 0)
        if now_ts - last_alert < 3600: continue  #     

        try:
            stats = get_binance_24h(sym)
            if not stats: continue
            cur   = stats.get("last", 0)
            entry = v.get("entry", 0)
            if not cur or not entry: continue

            dist = (entry - cur) / entry * 100 if cur < entry else 0
            if 0 < dist <= 2.0:  #   0-2%  
                pump_alerted[f"_entry_l_{sym}"] = now_ts
                text = (
                    f" *   {sym}USDT*  \n"
                    f" {now_utc3()}\n\n"
                    f"  : `{fp(entry)}`\n"
                    f" :    `{fp(cur)}`\n"
                    f"  :   `{dist:.1f}%`\n\n"
                    f" TP1: `{fp(v.get('tp1', entry*1.02))}`\n"
                    f" SL:  `{fp(v.get('sl', entry*0.85))}`\n\n"
                    f"   !\n#{sym}USDT"
                )
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton(" TradingView", url=tv_link(sym)),
                    InlineKeyboardButton(" TOP ",  callback_data="top_trades"),
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
                    f" *   {sym}USDT*  \n"
                    f" {now_utc3()}\n\n"
                    f"  : `{fp(entry)}`\n"
                    f" :    `{fp(cur)}`\n"
                    f"  :   `{dist:.1f}%`\n\n"
                    f" TP1: `{fp(v.get('tp1', entry*0.98))}`\n"
                    f" SL:  `{fp(v.get('sl', entry*1.15))}`\n\n"
                    f"   !\n#{sym}USDT"
                )
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton(" TradingView", url=tv_link(sym)),
                    InlineKeyboardButton(" TOP ",  callback_data="top_trades"),
                ]])
                for cid in chat_ids:
                    try: await bot.send_message(cid, text, parse_mode="Markdown", reply_markup=kb)
                    except Exception as e: log.error(f"Entry approach alert {cid}: {e}")
        except Exception as e:
            log.error(f"check_entry_approach {sym}: {e}")


# 
#  1  CONFLUENCE MATRIX (7+ )
# 

def confluence_matrix(a: dict, pa: dict, coin: dict,
                      btc_ctx: dict, kz: dict) -> dict:
    """
           7+ .
       . : score 0-100  grade A+/A/B/C/D.
    """
    is_long = a.get("is_long", True)
    factors = []
    score   = 0

    checks = [
        # (, , )
        (a.get("above_ema200"),                                    8,  "EMA200 "),
        (a.get("trend_4h") == ("bullish" if is_long else "bearish"), 7, " 4H "),
        (a.get("supertrend_bull") is (True if is_long else False),  7,  "Supertrend "),
        (a.get("macd_bullish") if is_long else a.get("macd_bearish"), 6, "MACD "),
        ((a.get("rsi_4h",50)<35) if is_long else (a.get("rsi_4h",50)>65), 8, "RSI  "),
        (pa.get("ict_ob_bull") if is_long else pa.get("ict_ob_bear"), 10, "ICT Order Block "),
        (pa.get("ict_liquidity_sweep"),                             9,  "Liq Sweep "),
        ((pa.get("ict_fvg_bull") if is_long else pa.get("ict_fvg_bear")), 7, "FVG "),
        (pa.get("smc_bos") == ("bull" if is_long else "bear"),     8,  "BOS "),
        (pa.get("smc_choch") == ("bull" if is_long else "bear"),   9,  "CHoCH "),
        (pa.get("wyckoff_phase") in (["Accumulation","Markup"] if is_long
                                      else ["Distribution","Markdown"]), 8, "Wyckoff "),
        (btc_ctx.get("long_ok") if is_long else btc_ctx.get("short_ok"), 7, "BTC  "),
        (kz.get("is_good"),                                        5,  "Killzone "),
        (pa.get("tf_confluence",0) >= (2 if is_long else -2),      8,  "TF Confluence "),
        (a.get("vol_spike") or pa.get("vol_trend")=="increasing",  5,  "  "),
    ]

    hits = 0
    for cond, weight, label in checks:
        if cond:
            score += weight
            factors.append(label)
            hits += 1

    score = min(100, score)

    if hits >= 10 and score >= 80:   grade = "A+ "
    elif hits >= 8 and score >= 65:  grade = "A+ "
    elif hits >= 7 and score >= 55:  grade = "A "
    elif hits >= 5 and score >= 40:  grade = "B "
    elif hits >= 3:                  grade = "C "
    else:                            grade = "D "

    return {
        "score":   score,
        "hits":    hits,
        "grade":   grade,
        "factors": factors,
        "pass":    grade.startswith("A"),
    }


# 
#  2  VOLUME PROFILE / POC
# 

def get_volume_profile(symbol: str, tf: str = "4h", limit: int = 100) -> dict:
    """
    Volume Profile     .
    POC (Point of Control) =     =  .
    VAH (Value Area High) =    (70% )
    VAL (Value Area Low)  =   
    """
    result = {"ok": False, "poc": 0.0, "vah": 0.0, "val": 0.0,
              "price_in_va": False, "price_above_poc": False,
              "label": "", "levels": []}
    try:
        candles = get_binance_ohlc(symbol, tf, limit)
        if not candles or len(candles) < 20:
            return result

        price = candles[-1]["close"]

        #      
        all_high = max(c["high"] for c in candles)
        all_low  = min(c["low"]  for c in candles)
        if all_high <= all_low:
            return result

        bins = 50  # 50  
        step = (all_high - all_low) / bins
        vol_bins = [0.0] * bins

        for c in candles:
            lo, hi, vol = c["low"], c["high"], c["vol"]
            for b in range(bins):
                bin_lo = all_low + b * step
                bin_hi = bin_lo + step
                #    
                overlap_lo = max(lo, bin_lo)
                overlap_hi = min(hi, bin_hi)
                if overlap_hi > overlap_lo:
                    frac = (overlap_hi - overlap_lo) / (hi - lo) if hi > lo else 1.0
                    vol_bins[b] += vol * frac

        # POC =    
        poc_bin = vol_bins.index(max(vol_bins))
        poc = all_low + (poc_bin + 0.5) * step

        # Value Area  70%   POC
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
            label = "   Value Area  POC   "
        elif price_in_va and not price_above_poc:
            label = "   Value Area  POC   "
        elif price > vah:
            label = "   VAH    "
        elif price < val:
            label = "   VAL    "
        else:
            label = "  "

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


# 
#  3  MARKET MICROSTRUCTURE ( /  )
# 

def get_order_book_analysis(symbol: str) -> dict:
    """
       Binance.
       (bid/ask walls)    
     .    /.
    """
    result = {
        "ok": False,
        "bid_wall": None,    #  
        "ask_wall": None,    #  
        "bid_ask_ratio": 1.0, # > 1.5 =  
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

        #     > 3 
        def find_walls(orders, n=20):
            if not orders: return []
            vols = [q for _, q in orders[:n]]
            avg  = sum(vols) / len(vols) if vols else 1
            walls = [(p, q) for p, q in orders[:n] if q > avg * 3.0]
            return sorted(walls, key=lambda x: x[1], reverse=True)[:3]

        bid_walls = find_walls(bids)
        ask_walls = find_walls(asks)

        #   bid vs ask ( 20 )
        total_bid = sum(q for _, q in bids[:20])
        total_ask = sum(q for _, q in asks[:20])
        ratio     = total_bid / total_ask if total_ask > 0 else 1.0

        if ratio > 1.5:
            imbalance = "bullish"
            label = f" :   (ratio {ratio:.2f})"
        elif ratio < 0.67:
            imbalance = "bearish"
            label = f" :   (ratio {ratio:.2f})"
        else:
            imbalance = "neutral"
            label = f"   (ratio {ratio:.2f})"

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


# 
#  4  DXY / GOLD 
# 

def get_macro_context() -> dict:
    """
     : DXY, Gold, NQ .
     = -.  DXY    .
    NQ (Nasdaq)       2024-2026.
    Gold  = risk-off =   .
    ETH/BTC ratio    .
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
        #  
        "nq_trend": "unknown",
        "nq_label": "",
        "gold_trend": "unknown", 
        "gold_label": "",
        "traditional_risk": "neutral",
    }
    try:
        # ETH/BTC ratio    
        eth_btc = get_binance_ohlc("ETHBTC", "1d", 30)
        if eth_btc and len(eth_btc) >= 14:
            closes = [c["close"] for c in eth_btc]
            ratio  = closes[-1]
            ema7   = calc_ema(closes, 7)[-1]  or ratio
            ema14  = calc_ema(closes, 14)[-1] or ratio

            if ratio > ema7 > ema14:
                eth_btc_trend = "bullish"
                altseason     = True
                altseason_lbl = " ETH/BTC    "
            elif ratio < ema7 < ema14:
                eth_btc_trend = "bearish"
                altseason     = False
                altseason_lbl = " ETH/BTC    BTC,   "
            else:
                eth_btc_trend = "neutral"
                altseason     = False
                altseason_lbl = " ETH/BTC "

            result.update({
                "eth_btc_ratio": round(ratio, 6),
                "eth_btc_trend": eth_btc_trend,
                "altseason":     altseason,
                "altseason_label": altseason_lbl,
            })

        # BTC   CMC
        try:
            gm = get_global_metrics()
            dom = gm.get("btc_dominance", 0)
            result["btc_dominance"] = round(dom, 1)
            if dom > 55:
                result["dom_trend"]   = "btc_season"
                result["macro_label"] = f"  BTC {dom:.1f}%    BTC"
                result["risk_on"]     = False
            elif dom < 45:
                result["dom_trend"]   = "alt_season"
                result["macro_label"] = f"  BTC {dom:.1f}%  "
                result["risk_on"]     = True
            else:
                result["dom_trend"]   = "neutral"
                result["macro_label"] = f"  BTC {dom:.1f}%  "
        except: pass

        #  NQ (Nasdaq)  
        #  QQQ- proxy: AAPL/MSFT   Binance
        #  BTC  vs    CoinGecko
        try:
            # Gold proxy  PAXG/USDT  Binance
            gold_data = get_binance_ohlc("PAXG", "1d", 10)
            if gold_data and len(gold_data) >= 5:
                gold_closes = [c["close"] for c in gold_data]
                gold_ch = (gold_closes[-1] - gold_closes[-5]) / gold_closes[-5] * 100
                if gold_ch > 1:
                    result["gold_trend"]  = "bullish"
                    result["gold_label"]  = f" Gold +{gold_ch:.1f}%  , "
                    result["traditional_risk"] = "cautious"
                elif gold_ch < -1:
                    result["gold_trend"] = "bearish"
                    result["gold_label"] = f" Gold {gold_ch:.1f}%  risk-off,    "
                    result["traditional_risk"] = "risk_off"
                else:
                    result["gold_trend"] = "neutral"
                    result["gold_label"] = f" Gold  ({gold_ch:.1f}%)"
        except: pass

        result["ok"] = True

    except Exception as e:
        log.error(f"macro_context: {e}")

    return result


# 
#  5  
# 

def get_seasonality() -> dict:
    """
       .
      10   BTC.
    """
    now   = datetime.now(TZ)
    month = now.month
    day   = now.day

    #  bias   (+ , - , 0 )
    monthly_bias = {
        1:  (-2, "       "),
        2:  (+1, "   ,  - "),
        3:  (+2, "   ,  "),
        4:  (+3, "     BTC ('Uptober #2')"),
        5:  (+1, "  'Sell in May'?  ,   "),
        6:  (-1, "   ,  "),
        7:  (+1, "        "),
        8:  (-1, "   ,  "),
        9:  (-2, "     "),
        10: (+3, "'Uptober'     BTC "),
        11: (+2, "   , Q4 "),
        12: (+1, "     ,  "),
    }

    #   
    week_of_month = (day - 1) // 7 + 1
    weekly_notes = {
        1: "      ",
        2: "      ",
        3: "     ()",
        4: "    ,  ",
    }

    #   ( ~ 2028)
    #  :  2024
    from datetime import date
    last_halving  = date(2024, 4, 20)
    next_halving  = date(2028, 4, 20)
    days_since    = (now.date() - last_halving).days
    days_to_next  = (next_halving - now.date()).days
    cycle_pct     = days_since / (next_halving - last_halving).days * 100

    if cycle_pct < 15:
        halving_phase = "Pre-halving run "
        halving_bias  = +3
    elif cycle_pct < 35:
        halving_phase = "Post-halving accumulation "
        halving_bias  = +1
    elif cycle_pct < 60:
        halving_phase = "Bull market expansion "
        halving_bias  = +3
    elif cycle_pct < 80:
        halving_phase = "Distribution / correction "
        halving_bias  = -2
    else:
        halving_phase = "Bear market / accumulation "
        halving_bias  = -1

    bias, month_note = monthly_bias.get(month, (0, ""))
    total_bias = bias + halving_bias

    if total_bias >= 4:    season_label = "   "
    elif total_bias >= 2:  season_label = "  "
    elif total_bias >= 0:  season_label = "  "
    elif total_bias >= -2: season_label = "  "
    else:                  season_label = "  "

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


# 
#  6  ON-CHAIN 
# 

def get_onchain_data(symbol: str) -> dict:
    """
    On-chain    API.
    Exchange Netflow:    =   ( )
                         =  ( )
    Whale movements  Binance large trades.
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
        #     Binance  
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

        #    
        all_qtys = [float(t["q"]) for t in trades]
        avg_qty  = sum(all_qtys) / len(all_qtys) if all_qtys else 1

        # "" =  > 10 
        whale_threshold = avg_qty * 10

        whale_buys  = 0
        whale_sells = 0
        buy_vol     = 0.0
        sell_vol    = 0.0

        for t in trades:
            qty     = float(t["q"])
            is_sell = t.get("m", False)  # maker = 
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

        # 
        if buy_ratio > 0.65:
            flow    = "outflow"
            flow_lbl = f"   ({whale_buys}  )"
            net_sig  = "bullish"
        elif buy_ratio < 0.35:
            flow    = "inflow"
            flow_lbl = f"   ({whale_sells}  )"
            net_sig  = "bearish"
        else:
            flow    = "neutral"
            flow_lbl = "  "
            net_sig  = "neutral"

        whale_lbl = (f" {whale_buys}   {whale_sells}   "
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


# 
# BACKTESTING      
# 

def backtest_signal(symbol: str, is_long: bool, lookback_candles: int = 90) -> dict:
    """
         4H  lookback_candles .
        :   TP1/TP2/TP3  SL.

    :
    - winrate (%  )
    - avg_rr ( R:R)
    - total_trades
    - best_streak / worst_streak
    - expectancy (   )
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
        i = 20  #    

        while i < len(candles) - 10:
            price = closes[i]
            atr_w = [abs(candles[j]["high"] - candles[j]["low"]) for j in range(max(0,i-14), i)]
            atr   = sum(atr_w) / len(atr_w) if atr_w else price * 0.02

            # Swing   TP/SL
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

            # :   10 
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

            i += 5  #  5  (20)  

        if not trades:
            return result

        wins   = [t for t in trades if t["outcome"] == "win"]
        losses = [t for t in trades if t["outcome"] == "loss"]
        total  = len(trades)
        wr     = len(wins) / total * 100
        avg_rr = sum(t["rr"] for t in trades) / total
        # Expectancy = winrate  avg_win_rr + lossrate  (-1)
        avg_win_rr = sum(t["rr"] for t in wins) / len(wins) if wins else 0
        expectancy = (len(wins)/total * avg_win_rr) + (len(losses)/total * (-1.0))

        # 
        best_streak = worst_streak = cur_w = cur_l = 0
        for t in trades:
            if t["outcome"] == "win":
                cur_w += 1; cur_l = 0
                best_streak = max(best_streak, cur_w)
            else:
                cur_l += 1; cur_w = 0
                worst_streak = max(worst_streak, cur_l)

        # 
        if wr >= 60 and expectancy > 0.3:   label = "  "
        elif wr >= 50 and expectancy > 0:    label = "  "
        elif wr >= 40 and expectancy > -0.2: label = "  "
        else:                                label = "   "

        summary = (f": {total}  : {len(wins)} ({wr:.0f}%)  "
                   f": {len(losses)}  "
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


# 
#  /      
# 

def get_coin_news(symbol: str) -> dict:
    """
          .
    : CoinGecko events + CryptoCompare news API ().
     sentiment   .
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
        # CryptoCompare News (,  API )
        url = "https://min-api.cryptocompare.com/data/v2/news/"
        params = {
            "categories": symbol.upper(),
            "lang": "EN",
            "sortOrder": "latest",
        }
        r = requests.get(url, params=params, timeout=8)
        if r.status_code != 200:
            return result

        data = r.json().get("Data", [])[:5]  #  5 
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
            age_str = f"{int(age_h)} " if age_h < 48 else f"{int(age_h/24)} "

            news_items.append({
                "title":     title[:100],
                "source":    src,
                "sentiment": sentiment,
                "age":       age_str,
                "url":       url_,
            })

        #  
        total = pos + neg
        if pos > neg and pos >= 2:
            sentiment = "positive"
            label = f"   ({pos}  )"
            score = pos
        elif neg > pos and neg >= 2:
            sentiment = "negative"
            label = f"   ({neg}  )"
            score = -neg
        else:
            sentiment = "neutral"
            label = "  "
            score = 0

        #   (  /)
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


# 
#  
# 

#    60+ 
UNLOCK_SCHEDULE: dict = {
    #    
    "ASTER": {"unlock_date": "2035-01", "unlock_pct": 60, "risk": "high",
               "note": "   2035"},
    "ARB":   {"unlock_date": "2024-04", "unlock_pct": 44, "risk": "high",
               "note": "    "},
    "ZK":    {"unlock_date": "2025-06", "unlock_pct": 35, "risk": "high",
               "note": "     TGE"},
    "STRK":  {"unlock_date": "2025-02", "unlock_pct": 40, "risk": "high",
               "note": "    "},
    "WLD":   {"unlock_date": "2025-07", "unlock_pct": 45, "risk": "high",
               "note": "   "},
    "TIA":   {"unlock_date": "2025-10", "unlock_pct": 55, "risk": "high",
               "note": "  "},
    "MANTA": {"unlock_date": "2025-01", "unlock_pct": 38, "risk": "high",
               "note": " "},
    "ALT":   {"unlock_date": "2025-03", "unlock_pct": 30, "risk": "high",
               "note": " "},
    "EIGEN": {"unlock_date": "2025-09", "unlock_pct": 35, "risk": "high",
               "note": "  "},
    "SAGA":  {"unlock_date": "2025-04", "unlock_pct": 42, "risk": "high",
               "note": "  "},
    "OMNI":  {"unlock_date": "2025-05", "unlock_pct": 38, "risk": "high",
               "note": "  "},
    "REZ":   {"unlock_date": "2025-05", "unlock_pct": 50, "risk": "high",
               "note": " "},
    "METIS": {"unlock_date": "2025-06", "unlock_pct": 30, "risk": "high",
               "note": "  "},
    "LISTA": {"unlock_date": "2025-07", "unlock_pct": 35, "risk": "high",
               "note": " ,  "},
    "PORTAL":{"unlock_date": "2025-03", "unlock_pct": 45, "risk": "high",
               "note": "Gaming ,  "},
    "PIXEL": {"unlock_date": "2025-04", "unlock_pct": 40, "risk": "high",
               "note": "Gaming,  "},
    "AEVO":  {"unlock_date": "2025-06", "unlock_pct": 33, "risk": "high",
               "note": "DEX , "},
    "ETHFI": {"unlock_date": "2025-03", "unlock_pct": 36, "risk": "high",
               "note": "LST ,  "},
    #    
    "OP":    {"unlock_date": "2024-05", "unlock_pct": 30, "risk": "medium",
               "note": "   "},
    "APT":   {"unlock_date": "2025-10", "unlock_pct": 25, "risk": "medium",
               "note": "   2025"},
    "SUI":   {"unlock_date": "2025-05", "unlock_pct": 20, "risk": "medium",
               "note": "   "},
    "PYTH":  {"unlock_date": "2025-11", "unlock_pct": 22, "risk": "medium",
               "note": " "},
    "JUP":   {"unlock_date": "2025-01", "unlock_pct": 30, "risk": "medium",
               "note": "   "},
    "SEI":   {"unlock_date": "2025-08", "unlock_pct": 18, "risk": "medium",
               "note": " "},
    "BLUR":  {"unlock_date": "2025-02", "unlock_pct": 25, "risk": "medium",
               "note": "NFT , "},
    "GMT":   {"unlock_date": "2025-04", "unlock_pct": 20, "risk": "medium",
               "note": "Move-to-earn,  "},
    "DYDX":  {"unlock_date": "2025-12", "unlock_pct": 15, "risk": "low",
               "note": " ,  "},
    "ANKR":  {"unlock_date": "2025-06", "unlock_pct": 12, "risk": "medium",
               "note": "  "},
    "CELR":  {"unlock_date": "2025-03", "unlock_pct": 15, "risk": "medium",
               "note": "Layer2,  "},
    "HOOK":  {"unlock_date": "2025-04", "unlock_pct": 28, "risk": "medium",
               "note": "GameFi,  "},
    "PERP":  {"unlock_date": "2025-05", "unlock_pct": 20, "risk": "medium",
               "note": "DEX, "},
    "CYBER": {"unlock_date": "2025-06", "unlock_pct": 22, "risk": "medium",
               "note": "SocialFi,  "},
    "ACE":   {"unlock_date": "2025-07", "unlock_pct": 25, "risk": "medium",
               "note": "Gaming,  "},
    #    
    "BTC":   {"unlock_date": "N/A",     "unlock_pct": 0,  "risk": "low",
               "note": " ,  "},
    "ETH":   {"unlock_date": "N/A",     "unlock_pct": 0,  "risk": "low",
               "note": "  merge"},
    "BNB":   {"unlock_date": "N/A",     "unlock_pct": 0,  "risk": "low",
               "note": " burn,  "},
    "SOL":   {"unlock_date": "N/A",     "unlock_pct": 5,  "risk": "low",
               "note": "  (~5%), "},
    "LINK":  {"unlock_date": "N/A",     "unlock_pct": 3,  "risk": "low",
               "note": " ,  "},
    "AAVE":  {"unlock_date": "N/A",     "unlock_pct": 2,  "risk": "low",
               "note": "DeFi blue chip,  "},
    "UNI":   {"unlock_date": "N/A",     "unlock_pct": 2,  "risk": "low",
               "note": " DEX,  "},
    "AVAX":  {"unlock_date": "2025-07", "unlock_pct": 8,  "risk": "low",
               "note": "  "},
    "DOT":   {"unlock_date": "N/A",     "unlock_pct": 10, "risk": "low",
               "note": " ~10%    "},
    "ATOM":  {"unlock_date": "N/A",     "unlock_pct": 10, "risk": "low",
               "note": "  "},
    "MATIC": {"unlock_date": "2025-12", "unlock_pct": 8,  "risk": "low",
               "note": "  POL,  "},
    "NEAR":  {"unlock_date": "N/A",     "unlock_pct": 5,  "risk": "low",
               "note": " "},
    "FTM":   {"unlock_date": "N/A",     "unlock_pct": 3,  "risk": "low",
               "note": "Sonic , "},
    "INJ":   {"unlock_date": "N/A",     "unlock_pct": 5,  "risk": "low",
               "note": "  burn"},
    "ORDI":  {"unlock_date": "N/A",     "unlock_pct": 0,  "risk": "low",
               "note": "BRC-20,  "},
    "RUNE":  {"unlock_date": "N/A",     "unlock_pct": 8,  "risk": "low",
               "note": "THORChain,  "},
    "ONDO":  {"unlock_date": "2025-01", "unlock_pct": 18, "risk": "medium",
               "note": "RWA ,  "},
    "DOGE":  {"unlock_date": "N/A",     "unlock_pct": 0,  "risk": "low",
               "note": " , "},
    "SHIB":  {"unlock_date": "N/A",     "unlock_pct": 0,  "risk": "low",
               "note": ",  "},
    "PEPE":  {"unlock_date": "N/A",     "unlock_pct": 0,  "risk": "low",
               "note": ",  "},
    "WIF":   {"unlock_date": "N/A",     "unlock_pct": 0,  "risk": "low",
               "note": "  Solana,  "},
    "BONK":  {"unlock_date": "N/A",     "unlock_pct": 0,  "risk": "low",
               "note": "  Solana,  "},
    "BEAT":  {"unlock_date": "N/A",     "unlock_pct": 5,  "risk": "low",
               "note": " "},
}

# 
# BTC CORRELATION     
# 

def get_btc_market_context() -> dict:
    """
       BTC   .
    90%    BTC    BTC .

     :
    - BTC       
    - BTC      
    - BTC        
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
        # BTC 
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

        # EMA    TF
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

        #  
        ch1h  = (cl4h[-1] - cl4h[-4])  / cl4h[-4]  * 100 if len(cl4h) >= 4  else 0
        ch24h = (cl4h[-1] - cl4h[-7])  / cl4h[-7]  * 100 if len(cl4h) >= 7  else 0
        result["btc_ch1h"]  = round(ch1h, 2)
        result["btc_ch24h"] = round(ch24h, 2)

        #   
        bull_count = sum(1 for t in [t1h, t4h, t1d] if "bull" in t)
        bear_count = sum(1 for t in [t1h, t4h, t1d] if "bear" in t)

        if bull_count >= 2:
            signal   = "bull"
            long_ok  = True
            short_ok = False
            label    = " BTC    "
            warning  = ""
        elif bear_count >= 2:
            signal   = "bear"
            long_ok  = False
            short_ok = True
            label    = " BTC    "
            warning  = " BTC       "
        elif t4h == "bull" or t1d == "neutral_bull":
            signal   = "neutral_bull"
            long_ok  = True
            short_ok = True
            label    = " BTC    "
            warning  = ""
        elif t4h == "bear" or t1d == "neutral_bear":
            signal   = "neutral_bear"
            long_ok  = True   #   
            short_ok = True
            label    = " BTC    "
            warning  = " BTC     "
        else:
            signal   = "neutral"
            long_ok  = True
            short_ok = True
            label    = " BTC       "
            warning  = ""

        #  : BTC  >3%  1    
        if ch1h < -3:
            long_ok  = False
            warning  = f" BTC -{ abs(ch1h):.1f}%  1     "
            label   += f"     {ch1h:.1f}%"

        # BTC  >5%  1    
        if ch1h > 5:
            short_ok = False
            label   += f"     +{ch1h:.1f}%"

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


# 
# POSITION SIZING    
#  -
# 

def calc_position_size(
    price: float,
    sl: float,
    deposit: float = 1000.0,
    risk_pct: float = 1.0,
    leverage: float = 1.0,
    quality: str = "B ",
) -> dict:
    """
          % .

     :
       =   risk_pct%
      =  / ( - SL)
    
     risk_pct   :
    A+  2% , A  1.5%, B  1%, C  0.5%
    """
    if price <= 0 or sl <= 0 or price == sl:
        return {"ok": False}

    #    
    quality_risk = {
        "A+ ": 2.0,
        "A ":  1.5,
        "B ":  1.0,
        "C ":  0.5,
    }
    adj_risk_pct = quality_risk.get(quality, risk_pct)

    risk_usd    = deposit * adj_risk_pct / 100        # $   
    sl_distance = abs(price - sl) / price * 100       # %  SL
    sl_usd      = abs(price - sl)                     # $  SL  

    if sl_usd <= 0:
        return {"ok": False}

    #    
    position_coins = risk_usd / sl_usd

    #    USD
    position_usd   = position_coins * price

    #      
    margin_required = position_usd / leverage

    # %     
    deposit_pct = margin_required / deposit * 100

    #   (  20%   )
    max_position_usd = deposit * 0.20 * leverage
    if position_usd > max_position_usd:
        position_usd   = max_position_usd
        position_coins = position_usd / price
        margin_required = position_usd / leverage
        deposit_pct    = margin_required / deposit * 100
        capped = True
    else:
        capped = False

    # DCA  (3 )
    dca1_usd = position_usd * 0.40   # 40% 
    dca2_usd = position_usd * 0.35   # 35%  
    dca3_usd = position_usd * 0.25   # 25%  SL 

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
    """      """
    if not ps.get("ok"):
        return ""

    side = "" if is_long else ""
    lines = [
        f" *  ( ${ps['deposit']:.0f}):*",
        f"  : `${ps['risk_usd']:.2f}` ({ps['risk_pct']}% )",
        f"  SL : `{ps['sl_distance_pct']:.2f}%`",
        f"  : `${ps['position_usd']:.2f}` ({ps['deposit_pct']:.1f}% )",
    ]
    if ps["leverage"] > 1:
        lines.append(f"  : `{ps['leverage']}x`  : `${ps['margin_required']:.2f}`")
    if ps["capped"]:
        lines.append("     ( 20% )")
    lines += [
        f"  *DCA :*",
        f"     1 (40%): `${ps['dca1_usd']:.2f}`",
        f"     2 (35%): `${ps['dca2_usd']:.2f}`",
        f"     3 (25%): `${ps['dca3_usd']:.2f}`",
    ]
    return "\n".join(lines)


# 
# ICT KILLZONES     
# 

def get_killzone_status() -> dict:
    """
    ICT Killzones       .
      UTC+3 (Istanbul).

    Asia Session:    01:0004:00 UTC+3  ( ,   sweep)
    London Open:     10:0012:00 UTC+3  (   )
    London Close:    18:0019:00 UTC+3  (  )
    NY Open:         16:0018:00 UTC+3  (   )
    NY Close:        23:0000:00 UTC+3  (   )
    """
    now = datetime.now(TZ)
    h   = now.hour
    m   = now.minute
    hm  = h * 60 + m  #   

    zones = [
        {"name": " Asia Session",   "start": 1*60,  "end": 4*60,  "quality": "B",
         "desc": "Sweep ,  "},
        {"name": " London Open",   "start": 10*60, "end": 12*60, "quality": "A+",
         "desc": "     "},
        {"name": " NY Open",       "start": 16*60, "end": 18*60, "quality": "A",
         "desc": "   ,  "},
        {"name": " London Close",   "start": 18*60, "end": 19*60, "quality": "B",
         "desc": "    "},
        {"name": " NY Close",       "start": 23*60, "end": 24*60, "quality": "C",
         "desc": "  , "},
    ]

    active = None
    for z in zones:
        if z["start"] <= hm < z["end"]:
            active = z
            remaining = z["end"] - hm
            active["remaining_min"] = remaining
            break

    #  
    next_zone = None
    future = [(z, z["start"] - hm if z["start"] > hm else z["start"] + 24*60 - hm)
              for z in zones]
    future.sort(key=lambda x: x[1])
    if future:
        next_zone = future[0][0].copy()
        next_zone["in_min"] = future[0][1]

    #   
    if active:
        is_good = active["quality"] in ("A+", "A")
    else:
        is_good = False
        # Dead zone   
        active = {"name": " Dead Zone", "quality": "D",
                  "desc": "    ", "remaining_min": 0}

    return {
        "active":    active,
        "next":      next_zone,
        "is_good":   is_good,
        "hour":      h,
        "all_zones": zones,
    }


def killzone_label() -> str:
    """     """
    kz = get_killzone_status()
    active = kz["active"]
    nxt    = kz["next"]
    q      = active["quality"]
    q_e    = {"A+": "", "A": "", "B": "", "C": "", "D": ""}.get(q, "")
    rem    = active.get("remaining_min", 0)

    line = f"{q_e} {active['name']}  ( : {q})"
    if rem:
        line += f"   {rem} "
    if nxt:
        line += f"\n  : {nxt['name']}  {nxt['in_min']} "
    return line


# 
#      A / A+
# 

def signal_quality_filter(a: dict, pa: dict, coin: dict) -> dict:
    """
     :     3+  .
     {"pass": bool, "quality": str, "reasons": list, "score": int}
    
         A/A+ .
    """
    reasons  = []
    warnings = []
    score    = 0
    is_long  = a.get("is_long", True)
    rsi_4h   = a.get("rsi_4h", 50)
    rocket   = a.get("rocket", 0)

    #    (  1  2) 
    has_structure = False

    # 1. Trend alignment (4H    )
    trend_4h = a.get("trend_4h", "neutral")
    if (is_long and trend_4h == "bullish") or (not is_long and trend_4h == "bearish"):
        score += 15; reasons.append("  4H   ")
        has_structure = True
    elif trend_4h == "neutral":
        score += 5; reasons.append("    ")
    else:
        warnings.append("     ")

    # 2. Supertrend 
    st_bull = a.get("supertrend_bull")
    if (is_long and st_bull is True) or (not is_long and st_bull is False):
        score += 12; reasons.append(" Supertrend  ")
        has_structure = True

    # 3. RSI   
    if is_long and rsi_4h < 35:
        score += 15; reasons.append(f" RSI  ({rsi_4h:.0f})   ")
    elif is_long and rsi_4h < 50:
        score += 8;  reasons.append(f" RSI  ({rsi_4h:.0f})")
    elif is_long and rsi_4h > 70:
        score -= 10; warnings.append(f" RSI  ({rsi_4h:.0f})     ")
    if not is_long and rsi_4h > 65:
        score += 15; reasons.append(f" RSI  ({rsi_4h:.0f})   ")
    elif not is_long and rsi_4h < 30:
        score -= 10; warnings.append(f" RSI  ({rsi_4h:.0f})     ")

    # 4. MACD 
    if (is_long and a.get("macd_bullish")) or (not is_long and a.get("macd_bearish")):
        score += 10; reasons.append(" MACD ")

    # 5. EMA200   
    if is_long and a.get("above_ema200"):
        score += 12; reasons.append("  EMA200   ")
    elif is_long and not a.get("above_ema200"):
        score += 3;  reasons.append("  EMA200    ")
    if not is_long and not a.get("above_ema200"):
        score += 12; reasons.append("  EMA200   ")

    # 6. PRO Analysis 
    if pa.get("ok"):
        pro_score = pa.get("pro_score", 0)
        if pro_score >= 70:
            score += 15; reasons.append(f" PRO Score {pro_score}/100   ")
        elif pro_score >= 50:
            score += 8;  reasons.append(f" PRO Score {pro_score}/100   ")
        else:
            score -= 5;  warnings.append(f" PRO Score {pro_score}/100   ")

        # ICT   pro_analysis
        if pa.get("ict_ob_bull") and is_long:
            score += 15; reasons.append(" ICT Bullish Order Block")
        if pa.get("ict_ob_bear") and not is_long:
            score += 15; reasons.append(" ICT Bearish Order Block")
        if pa.get("ict_liquidity_sweep"):
            score += 12; reasons.append(" Liquidity Sweep   ")
        if pa.get("smc_choch"):
            score += 10; reasons.append(f" CHoCH {pa['smc_choch']}   ")
        if (pa.get("ict_fvg_bull") and is_long) or (pa.get("ict_fvg_bear") and not is_long):
            score += 8; reasons.append(" FVG   ")

        # Wyckoff 
        wy = pa.get("wyckoff_phase")
        if (is_long and wy == "Accumulation") or (is_long and wy == "Markup"):
            score += 10; reasons.append(f" Wyckoff: {wy}")
        elif (not is_long and wy == "Distribution") or (not is_long and wy == "Markdown"):
            score += 10; reasons.append(f" Wyckoff: {wy}")

        # TF confluence
        conf = pa.get("tf_confluence", 0)
        if (is_long and conf >= 3) or (not is_long and conf <= -3):
            score += 12; reasons.append(f" {abs(conf)}/4  ")
        elif abs(conf) >= 2:
            score += 5

        # Funding rate
        fr = pa.get("funding_rate")
        if fr is not None:
            if is_long and fr < -0.03:
                score += 8; reasons.append(f" Funding  ({fr:.4f}%)   ")
            elif not is_long and fr > 0.06:
                score += 8; reasons.append(f" Funding  ({fr:.4f}%)   ")

    # 7. Rocket Score
    if rocket >= 70:
        score += 10; reasons.append(f" Rocket Score {rocket}/100")
    elif rocket >= 55:
        score += 5
    else:
        warnings.append(f" Rocket Score  ({rocket}/100)")

    # 8.  
    if a.get("suspicious"):
        score -= 25; warnings.append("     ")

    #  BTC  
    btc = get_btc_market_context()
    if btc["ok"]:
        if is_long and btc["signal"] == "bull":
            score += 12; reasons.append(" BTC      ")
        elif is_long and btc["signal"] == "bear":
            score -= 20; warnings.append(" BTC     ,  ")
        elif is_long and "neutral_bear" in btc["signal"]:
            score -= 8;  warnings.append(" BTC     ")
        elif not is_long and btc["signal"] == "bear":
            score += 12; reasons.append(" BTC      ")
        elif not is_long and btc["signal"] == "bull":
            score -= 15; warnings.append(" BTC     ")
        #   BTC
        if not btc["long_ok"] and is_long:
            score -= 25; warnings.append(f" BTC   ({btc['btc_ch1h']:.1f}%  1)   ")

    #  USDT.D  
    #  USDT.D =    =  
    try:
        usdt_d_data = get_usdt_dominance() if "get_usdt_dominance" in dir() else {}
        usdt_d = usdt_d_data.get("usdt_d", 0)
        if usdt_d > 9.0 and is_long:
            score -= 15; warnings.append(f" USDT.D={usdt_d:.1f}%    ,  ")
        elif usdt_d > 8.5 and is_long:
            score -= 8; warnings.append(f" USDT.D={usdt_d:.1f}%    ")
        elif usdt_d < 7.5 and is_long:
            score += 8; reasons.append(f" USDT.D={usdt_d:.1f}%     ")
        elif usdt_d > 8.5 and not is_long:
            score += 10; reasons.append(f" USDT.D={usdt_d:.1f}%   ,   ")
    except: pass

    #   Gold /   
    try:
        mac_ctx = get_macro_context()
        if mac_ctx.get("traditional_risk") == "risk_off" and is_long:
            score -= 10; warnings.append(" Gold   risk-off   ")
        elif mac_ctx.get("traditional_risk") == "risk_off" and not is_long:
            score += 5; reasons.append(" Risk-off     ")
    except: pass

    #  Killzone / 
    kz = get_killzone_status()
    if kz["is_good"]:
        score += 8; reasons.append(f" {kz['active']['name']}   ")
    elif kz["active"]["quality"] == "D":
        score -= 5; warnings.append(f" Dead Zone     ")

    #   
    score = max(0, min(100, score))

    if score >= 75 and has_structure:    quality = "A+ "
    elif score >= 55 and has_structure:  quality = "A "
    elif score >= 40:                    quality = "B "
    else:                                quality = "C "

    passes = quality in ("A+ ", "A ")

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
        .
    : , , .
    """
    sym = symbol.upper()
    unlock = UNLOCK_SCHEDULE.get(sym)

    result = {
        "has_data":    False,
        "risk":        "unknown",
        "risk_label":  "  ",
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
        "high":   "   ",
        "medium": "   ",
        "low":    "   ",
    }

    rec = ""
    spot_ok = True
    if risk == "high":
        rec = f"    {pct}%     "
        spot_ok = False
    elif risk == "medium":
        rec = f"      {pct}%,    {date}"
    else:
        rec = f"     {pct}%  "

    result.update({
        "has_data":    True,
        "risk":        risk,
        "risk_label":  risk_labels.get(risk, ""),
        "note":        note,
        "unlock_pct":  pct,
        "unlock_date": date,
        "recommendation": rec,
        "spot_ok":     spot_ok,
    })
    return result


async def check_alerts(bot: Bot):
    """ 5 : pump/dump + zone + supertrend + watchlist + spot + entry alerts"""
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
    """      """
    msg   = await update.message.reply_text("  ...")
    coins = get_top500()
    coin_map = {c["symbol"]: c for c in coins}

    lines = [
        " *  BEST TRADE*",
        f" {now_utc3()}",
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
            price_str = "``"

        emoji = "" if bias == "LONG" else ""

        #    
        in_zone = False
        if zone and zone[0] is not None and price > 0:
            in_zone = zone[0] <= price <= zone[1]
        in_zone_str = "   !" if in_zone else ""

        lines.append(f"{emoji} *{sym}*  {price_str}{in_zone_str}")
        if zone and zone[0] is not None:
            lines.append(f"    : `{fp(zone[0])}  {fp(zone[1])}`")
        lines.append(f"    {note[:60]}...")
        lines.append(f"    {src}")
        lines.append("")

    nav = InlineKeyboardMarkup([[
        InlineKeyboardButton(" /1 ",   callback_data="market_overview"),
        InlineKeyboardButton(" /3 ", callback_data="signals"),
    ]])
    await msg.edit_text("\n".join(lines), parse_mode="Markdown",
                        reply_markup=nav, disable_web_page_preview=True)

async def cmd_game(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /8    
         48 + 
    """
    nav = InlineKeyboardMarkup([[
        InlineKeyboardButton(" ",    callback_data="game"),
        InlineKeyboardButton(" ",       callback_data="market_overview"),
    ], [
        InlineKeyboardButton(" ",      callback_data="rockets"),
        InlineKeyboardButton(" Precision",   callback_data="precision"),
    ]])
    text = f" {now_utc3()}\n\n" + build_game_digest()
    await update.message.reply_text(
        text, parse_mode="Markdown",
        reply_markup=nav,
        disable_web_page_preview=False  #   TradingView 
    )

# 
# MAIN
# 


# main()       

# 
# PRO ANALYSIS   
# SMC / ICT / Wyckoff / Elliott / Multi-TF / OI / Funding
# 

def pro_analysis(symbol: str, coin: dict) -> dict:
    """
       -.
    : SMC/ICT, Wyckoff, Elliott Wave (.), 
    Multi-TF confluence, OI, Funding, Volume Profile, 
    Market Structure, Liquidity Sweep detection.
    
      dict   0-100  
       .
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
        "pro_score": 0,        # 0-100  
        "direction": "neutral", # long / short / neutral
        "confidence": 0,        # 0-100 
        "setup_type": None,     # ICT / Wyckoff / SMC / Elliott / Breakout
        "factors": [],          #    
        "warnings": [],         # 
        "entry_quality": None,  # A+ / A / B / C
        #  
        "market_structure": "unknown",  # uptrend/downtrend/ranging/accumulation/distribution
        "phase": None,           # Wyckoff phase
        # ICT 
        "ict_ob_bull": False,    # Bullish Order Block
        "ict_ob_bear": False,    # Bearish Order Block
        "ict_fvg_bull": False,   # Fair Value Gap 
        "ict_fvg_bear": False,   # Fair Value Gap 
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
        "tf_confluence": 0,      #  TF 
    }

    try:
        #      TF 
        c1h  = get_binance_ohlc(symbol, "1h",  100) or []
        c4h  = get_binance_ohlc(symbol, "4h",  200) or []
        c1d  = get_binance_ohlc(symbol, "1d",  365) or []
        c1w  = get_binance_ohlc(symbol, "1w",  100) or []

        if len(c4h) < 50:
            return result

        #    
        closes_1h = [c["close"] for c in c1h]
        closes_4h = [c["close"] for c in c4h]
        closes_1d = [c["close"] for c in c1d]
        closes_1w = [c["close"] for c in c1w]
        highs_4h  = [c["high"]  for c in c4h]
        lows_4h   = [c["low"]   for c in c4h]
        vols_4h   = [c["vol"]   for c in c4h]
        price     = closes_4h[-1]

        #  EMA MULTI-TF 
        ema20_4h  = calc_ema(closes_4h, 20)[-1]  or price
        ema50_4h  = calc_ema(closes_4h, 50)[-1]  or price
        ema200_4h = calc_ema(closes_4h, 200)[-1] or price
        ema200_1d = (calc_ema(closes_1d, 200)[-1] or price) if len(closes_1d) >= 200 else price
        ema50_1d  = (calc_ema(closes_1d, 50)[-1]  or price) if len(closes_1d) >= 50  else price
        ema20_1w  = (calc_ema(closes_1w, 20)[-1]  or price) if len(closes_1w) >= 20  else price

        #  RSI MULTI-TF 
        rsi_1h = calc_rsi(closes_1h, 14) if len(closes_1h) >= 15 else 50.0
        rsi_4h = calc_rsi(closes_4h, 14) if len(closes_4h) >= 15 else 50.0
        rsi_1d = calc_rsi(closes_1d, 14) if len(closes_1d) >= 15 else 50.0
        rsi_1w = calc_rsi(closes_1w, 14) if len(closes_1w) >= 15 else 50.0

        #  MACD 4H 
        ema12_4h = calc_ema(closes_4h, 12)
        ema26_4h = calc_ema(closes_4h, 26)
        macd_line = [a-b for a,b in zip(ema12_4h, ema26_4h) if a and b]
        sig_line  = calc_ema(macd_line, 9) if len(macd_line) >= 9 else [0.0]
        macd_val  = macd_line[-1]  if macd_line  else 0.0
        sig_val   = sig_line[-1]   if sig_line   else 0.0
        macd_hist = macd_val - sig_val
        macd_bull = macd_val > sig_val
        macd_bear = macd_val < sig_val
        #  /
        macd_hist_growing = (len(macd_line) > 1 and
                             macd_hist > (macd_line[-2] - (sig_line[-2] if len(sig_line) > 1 else 0)))

        #  ATR 4H 
        atr_vals = calc_atr(c4h, 14)
        atr = atr_vals[-1] if atr_vals else price * 0.03

        #  SUPERTREND 4H 
        st_vals = calc_supertrend(c4h, 10, 3.0)
        st_bull = st_vals[-1]["direction"] == 1 if st_vals else None

        #  MARKET STRUCTURE (BOS/CHoCH) 
        #    swing highs/lows  4H
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

        # BOS Bull:  swing high  
        bos_bull = False
        if len(sh) >= 2 and sh[-1][1] > sh[-2][1]:
            bos_bull = True
        # BOS Bear:  swing low  
        bos_bear = False
        if len(sl_pts) >= 2 and sl_pts[-1][1] < sl_pts[-2][1]:
            bos_bear = True
        # CHoCH:   BOS
        choch_bull = bos_bull and len(sl_pts) >= 2 and sl_pts[-1][1] > sl_pts[-2][1]
        choch_bear = bos_bear and len(sh) >= 2 and sh[-1][1] < sh[-2][1]

        result["smc_bos"]   = "bull" if bos_bull else ("bear" if bos_bear else None)
        result["smc_choch"] = "bull" if choch_bull else ("bear" if choch_bear else None)

        #  ICT ORDER BLOCKS 
        # Bullish OB:      
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

            # Bullish OB:   +  3   high OB
            if (candle["close"] < candle["open"] and body/rng > 0.5):
                if all(c["close"] > candle["high"] for c in next3):
                    #     OB ()
                    ob_lo = candle["open"]
                    ob_hi = candle["high"]
                    if ob_lo <= price <= ob_hi * 1.01:
                        ob_bull = True
                        ob_bull_zone = (ob_lo, ob_hi)

            # Bearish OB:   +  3   low OB
            if (candle["close"] > candle["open"] and body/rng > 0.5):
                if all(c["close"] < candle["low"] for c in next3):
                    ob_lo = candle["low"]
                    ob_hi = candle["open"]
                    if ob_lo * 0.99 <= price <= ob_hi:
                        ob_bear = True
                        ob_bear_zone = (ob_lo, ob_hi)

        result["ict_ob_bull"] = ob_bull
        result["ict_ob_bear"] = ob_bear

        #  ICT FAIR VALUE GAP (FVG) 
        # FVG Bull: [i-1].high < [i+1].low     
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

        #  LIQUIDITY SWEEP 
        #     swing low   (bull sweep)
        liq_bull_sweep = False
        liq_bear_sweep = False
        if len(sl_pts) >= 2 and len(c4h) >= 5:
            prev_sl = sl_pts[-2][1]
            last_5_lows = [c["low"] for c in c4h[-5:]]
            last_5_cls  = [c["close"] for c in c4h[-5:]]
            if min(last_5_lows) < prev_sl and last_5_cls[-1] > prev_sl:
                liq_bull_sweep = True  #    SL  
        if len(sh) >= 2 and len(c4h) >= 5:
            prev_sh_h = sh[-2][1]
            last_5_highs = [c["high"] for c in c4h[-5:]]
            last_5_cls   = [c["close"] for c in c4h[-5:]]
            if max(last_5_highs) > prev_sh_h and last_5_cls[-1] < prev_sh_h:
                liq_bear_sweep = True

        result["ict_liquidity_sweep"] = liq_bull_sweep or liq_bear_sweep

        #  AMD  (ICT Power of Three) 
        # Accumulation (Asia)  Manipulation (London sweep)  Distribution (NY move)
        #      + price action
        amd_phase = None
        amd_label = None
        now_h = datetime.now(TZ).hour

        if len(c4h) >= 6:
            high_6  = max(c["high"]  for c in c4h[-6:])
            low_6   = min(c["low"]   for c in c4h[-6:])
            mid_6   = (high_6 + low_6) / 2
            last_close = c4h[-1]["close"]

            # Asia (01-09 UTC+3):    
            if 1 <= now_h < 9:
                amd_phase = "accumulation"
                amd_label = " AMD:   (Asia)   "
            # London open (09-13 UTC+3):   sweep
            elif 9 <= now_h < 13:
                if last_close < low_6 * 1.001:  #  
                    amd_phase = "manipulation_bear"
                    amd_label = " AMD:   (London sweep)    "
                elif last_close > high_6 * 0.999:
                    amd_phase = "manipulation_bull"
                    amd_label = " AMD:   (London sweep)    "
                else:
                    amd_phase = "manipulation"
                    amd_label = " AMD:      "
            # NY (15-22 UTC+3):    
            elif 15 <= now_h < 22:
                if last_close > mid_6:
                    amd_phase = "distribution_bull"
                    amd_label = " AMD:   (NY )   "
                else:
                    amd_phase = "distribution_bear"
                    amd_label = " AMD:   (NY )   "
            else:
                amd_phase = "dead_zone"
                amd_label = " AMD: Dead Zone   "

        result["amd_phase"] = amd_phase
        result["amd_label"] = amd_label

        #  WYCKOFF  
        #    volume + price action  1D
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
                    wyckoff_event = "Spring/LPS    "
                else:
                    wyckoff_phase = "Markdown"
                    wyckoff_event = "Phase D/E   "
            elif ch90d < -30 and abs(ch30d) < 10:
                wyckoff_phase = "Accumulation"
                wyckoff_event = "Phase B/C  "
            elif ch30d > 15 and ch7d > 5:
                wyckoff_phase = "Markup"
                wyckoff_event = "Phase E   "
            elif ch30d > 20 and ch7d < -5 and price_pos > 0.7:
                wyckoff_phase = "Distribution"
                wyckoff_event = "UTAD / Phase B  "

        result["wyckoff_phase"] = wyckoff_phase
        result["wyckoff_event"] = wyckoff_event

        #  ELLIOTT WAVE ( ) 
        #     
        elliott_wave = None
        if len(closes_4h) >= 50:
            #    3  
            moves = []
            prev = closes_4h[-50]
            step = 10
            for i in range(-40, 0, step):
                cur = closes_4h[i]
                moves.append((cur - prev) / prev * 100)
                prev = cur

            if len(moves) >= 4:
                #  3 :    
                if moves[-2] < -5 and moves[-1] > 8:
                    elliott_wave = "wave3_up "
                #  5 :    RSI 
                elif moves[-1] > 5 and rsi_4h > 70 and moves[-3] > 0:
                    elliott_wave = "wave5_up "
                #  C :   
                elif moves[-2] > 5 and moves[-1] < -5:
                    elliott_wave = "wave_c_down "
                #  2  (     3)
                elif moves[-3] > 10 and moves[-2] < -5 and moves[-1] > 2:
                    elliott_wave = "wave2_correction "

        result["elliott_wave"] = elliott_wave

        #  VOLUME ANALYSIS 
        vol_avg_20 = sum(vols_4h[-20:]) / 20 if len(vols_4h) >= 20 else 1
        vol_now    = vols_4h[-1]
        vol_climax = vol_now > vol_avg_20 * 3.0  #  
        vol_dry    = vol_now < vol_avg_20 * 0.4  #  
        vol_trend_inc = sum(vols_4h[-5:]) / 5 > sum(vols_4h[-20:-5]) / 15 if len(vols_4h) >= 20 else False

        result["vol_climax"] = vol_climax
        result["vol_dry_up"] = vol_dry
        result["vol_trend"] = "increasing" if vol_trend_inc else "decreasing"

        #  OI / FUNDING (Binance futures) 
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
                    # 
                    if oi_change > 3 and ch1h > 0:
                        oi_signal = " OI  +     "
                    elif oi_change > 3 and ch1h < 0:
                        oi_signal = " OI  +     "
                    elif oi_change < -3 and ch1h > 0:
                        oi_signal = " OI  +    -"
                    elif oi_change < -3 and ch1h < 0:
                        oi_signal = " OI  +    -"
        except: pass

        result["funding_rate"] = funding_rate
        result["oi_change"]    = oi_change
        result["oi_signal"]    = oi_signal

        #  PREMIUM/DISCOUNT ARRAY (ICT) 
        #      Premium ( 50% )  Discount
        if len(closes_4h) >= 20:
            hi_20 = max(highs_4h[-20:])
            lo_20 = min(lows_4h[-20:])
            mid   = (hi_20 + lo_20) / 2
            eq    = mid  # Equilibrium
            if price < eq * 0.95:
                result["ict_pd_array"] = "Discount  ( )"
            elif price > eq * 1.05:
                result["ict_pd_array"] = "Premium  ( )"
            else:
                result["ict_pd_array"] = "Equilibrium "

        #  MULTI-TF  
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

        #   PRO SCORE 
        factors  = []
        warnings = []
        score    = 0
        bull_pts = 0
        bear_pts = 0

        # 1. Multi-TF confluence ( )
        if bull_tfs >= 3:
            bull_pts += 20; factors.append(f" Multi-TF: {bull_tfs}/4  TF (+20)")
        elif bull_tfs == 2:
            bull_pts += 10; factors.append(f" Multi-TF: 2/4  TF (+10)")
        if bear_tfs >= 3:
            bear_pts += 20; factors.append(f" Multi-TF: {bear_tfs}/4  TF (+20)")
        elif bear_tfs == 2:
            bear_pts += 10

        # 2. ICT Order Block
        if ob_bull:
            bull_pts += 15; factors.append(" ICT Bullish Order Block     (+15)")
        if ob_bear:
            bear_pts += 15; factors.append(" ICT Bearish Order Block     (+15)")

        # 3. Liquidity Sweep
        if liq_bull_sweep:
            bull_pts += 12; factors.append(" Liquidity Sweep  SL    (+12)")
        if liq_bear_sweep:
            bear_pts += 12; factors.append(" Liquidity Sweep  BH    (+12)")

        # 4. FVG
        if fvg_bull:
            bull_pts += 8; factors.append(" Bullish FVG      (+8)")
        if fvg_bear:
            bear_pts += 8; factors.append(" Bearish FVG      (+8)")

        # 5. BOS / CHoCH
        if bos_bull:
            bull_pts += 8; factors.append(" BOS      (+8)")
        if bos_bear:
            bear_pts += 8; factors.append(" BOS      (+8)")
        if choch_bull:
            bull_pts += 10; factors.append(" CHoCH       (+10)")
        if choch_bear:
            bear_pts += 10; factors.append(" CHoCH       (+10)")

        # 6. RSI Multi-TF
        if rsi_4h < 30 and rsi_1d < 35:
            bull_pts += 10; factors.append(f" RSI   4H({rsi_4h:.0f})  1D({rsi_1d:.0f}) (+10)")
        elif rsi_4h < 40:
            bull_pts += 5;  factors.append(f" RSI 4H  ({rsi_4h:.0f}) (+5)")
        if rsi_4h > 70 and rsi_1d > 65:
            bear_pts += 10; factors.append(f" RSI   4H({rsi_4h:.0f})  1D({rsi_1d:.0f}) (+10)")
        elif rsi_4h > 65:
            bear_pts += 5

        # 7. MACD
        if macd_bull and macd_hist_growing:
            bull_pts += 7; factors.append(" MACD   +   (+7)")
        elif macd_bull:
            bull_pts += 3
        if macd_bear and not macd_hist_growing:
            bear_pts += 7; factors.append(" MACD   (+7)")

        # 8. Supertrend
        if st_bull is True:
            bull_pts += 8;  factors.append(" Supertrend: BUY  (+8)")
        elif st_bull is False:
            bear_pts += 8;  factors.append(" Supertrend: SELL  (+8)")

        # 9. Wyckoff
        if wyckoff_phase == "Accumulation":
            bull_pts += 10; factors.append(f" Wyckoff: {wyckoff_event} (+10)")
        elif wyckoff_phase == "Markup":
            bull_pts += 8;  factors.append(f" Wyckoff: {wyckoff_event} (+8)")
        elif wyckoff_phase == "Distribution":
            bear_pts += 10; factors.append(f" Wyckoff: {wyckoff_event} (+10)")
        elif wyckoff_phase == "Markdown":
            bear_pts += 8;  factors.append(f" Wyckoff: {wyckoff_event} (+8)")

        # 10. Elliott Wave
        if elliott_wave:
            if "wave3_up" in elliott_wave:
                bull_pts += 12; factors.append(f" Elliott: {elliott_wave}     (+12)")
            elif "wave2_correction" in elliott_wave:
                bull_pts += 10; factors.append(f" Elliott: {elliott_wave}     (+10)")
            elif "wave5_up" in elliott_wave:
                bull_pts += 5; warnings.append(f" Elliott: {elliott_wave}   ,  ")
            elif "wave_c_down" in elliott_wave:
                bear_pts += 10; factors.append(f" Elliott: {elliott_wave} (+10)")

        # 11. OI / Funding
        if oi_change is not None:
            if oi_change > 5 and ch1h > 0:
                bull_pts += 8; factors.append(f" OI +{oi_change:.1f}% +      (+8)")
            elif oi_change < -5 and ch1h > 0:
                bull_pts += 6; factors.append(f" OI -{abs(oi_change):.1f}% +   - (+6)")
            elif oi_change > 5 and ch1h < 0:
                bear_pts += 8; factors.append(f" OI  +      (+8)")

        if funding_rate is not None:
            if funding_rate < -0.05:
                bull_pts += 6; factors.append(f" Funding rate  ({funding_rate:.4f}%)    (+6)")
            elif funding_rate > 0.08:
                bear_pts += 6; factors.append(f" Funding rate  ({funding_rate:.4f}%)    (+6)")
            elif abs(funding_rate) < 0.01:
                bull_pts += 2; factors.append(f" Funding     (+2)")

        # 12. Volume
        if vol_trend_inc and ch1h > 0:
            bull_pts += 5; factors.append("   +      (+5)")
        if vol_climax:
            warnings.append(" Climax volume     ")
        if vol_dry and abs(ch24h) < 2:
            bull_pts += 4; factors.append(" Volume dry-up    breakout  (+4)")

        # 13. 
        if rank <= 20:
            bull_pts += 5; factors.append(f" -20    (rank #{rank}) (+5)")
        elif rank <= 50:
            bull_pts += 3; factors.append(f" -50 (rank #{rank}) (+3)")

        #    
        if bull_pts > bear_pts + 10:
            direction = "long"
            score = min(100, 30 + bull_pts)
        elif bear_pts > bull_pts + 10:
            direction = "short"
            score = min(100, 30 + bear_pts)
        else:
            direction = "neutral"
            score = max(bull_pts, bear_pts)

        #    
        if vol_ratio > 50:
            score = int(score * 0.6)
            warnings.append("  Vol/MCap   ")

        #  
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
        if strong_confluences >= 5:   entry_q = "A+ "
        elif strong_confluences >= 3: entry_q = "A "
        elif strong_confluences >= 2: entry_q = "B "
        else:                         entry_q = "C "

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


# 
#       Binance ( CMC estimate)
# 

def real_ta(symbol: str) -> dict:
    """
        OHLC  Binance.
    4H -  , 1D - , 1H - .
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

        #  EMA (4H ) 
        _ema20  = calc_ema(closes_4h, 20)
        _ema50  = calc_ema(closes_4h, 50)
        _ema200 = calc_ema(closes_4h, 200)
        ema20_v  = next((v for v in reversed(_ema20)  if v is not None), price)
        ema50_v  = next((v for v in reversed(_ema50)  if v is not None), price)
        ema200_v = next((v for v in reversed(_ema200) if v is not None), price)

        #  RSI 
        rsi_4h = calc_rsi(closes_4h, 14)

        c1h = get_binance_ohlc(symbol, "1h", 50)
        rsi_1h = calc_rsi([c["close"] for c in c1h], 14) if c1h else 50.0

        c1d = get_binance_ohlc(symbol, "1d", 50)
        rsi_1d = calc_rsi([c["close"] for c in c1d], 14) if c1d else 50.0

        #  MACD (12, 26, 9)  4H 
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

        #  Bollinger Bands (20, 2) 
        window = closes_4h[-20:]
        bb_mid  = sum(window) / 20
        bb_std  = (sum((x - bb_mid)**2 for x in window) / 20) ** 0.5
        bb_up   = bb_mid + 2 * bb_std
        bb_dn   = bb_mid - 2 * bb_std
        bb_w    = (bb_up - bb_dn) / bb_mid if bb_mid > 0 else 0
        bb_sqz  = bb_w < 0.04   #  < 4% 

        #  Volume spike 
        vol_avg = sum(vols_4h[-20:]) / 20 if len(vols_4h) >= 20 else 1
        vol_now = vols_4h[-1]
        vol_spk = vol_now > vol_avg * 1.5

        #  ATR (14) 
        atr_vals = calc_atr(c4h, 14)
        atr_v    = atr_vals[-1] if atr_vals else 0.0

        #  Supply / Demand  (SMC ) 
        # Demand () =       
        # Supply () =       
        demand_zones = []  # [(low, high), ...]
        supply_zones = []

        for i in range(5, len(candles) - 5):
            c = candles[i]
            #  
            body = abs(c["close"] - c["open"])
            rng  = c["high"] - c["low"]
            if rng == 0: continue

            #     Demand  ()
            if (c["close"] > c["open"]                         # 
                    and body / rng > 0.6                       #  
                    and body > sum(abs(candles[j]["close"] - candles[j]["open"])
                                   for j in range(i-3, i)) / 3):  #  
                demand_zones.append((c["low"], c["open"]))

            #     Supply  ()
            if (c["close"] < c["open"]
                    and body / rng > 0.6
                    and body > sum(abs(candles[j]["close"] - candles[j]["open"])
                                   for j in range(i-3, i)) / 3):
                supply_zones.append((c["close"], c["high"]))

        #  Demand    = support
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

        # :   
        in_demand_zone = any(z[0] <= price_now <= z[1] * 1.02 for z in demand_zones[-10:])
        in_supply_zone = any(z[0] * 0.98 <= price_now <= z[1] for z in supply_zones[-10:])

        #  Supertrend (4H) 
        st_vals = calc_supertrend(c4h, 10, 3.0)
        st_bull = st_vals[-1]["direction"] == 1 if st_vals else None

        #   4H ( EMA) 
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
            # Supply/Demand 
            "in_demand_zone": in_demand_zone,
            "in_supply_zone": in_supply_zone,
            "demand_zones": demand_zones[-5:],  #  5
            "supply_zones": supply_zones[-5:],
        })
    except Exception as e:
        log.error(f"real_ta {symbol}: {e}")
    return result


def real_full_analysis(coin: dict) -> dict:
    """
          Binance .
     full_analysis  /full, top_spot, top_long, top_short.
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

    #    Binance
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

    # SMC   
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

    # Supply/Demand    
    in_demand = ta.get("in_demand_zone", False)
    in_supply = ta.get("in_supply_zone", False)

    # Direction:    TA
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
    # Supply/Demand   
    if in_demand:                score_ta += 3   #      
    if in_supply:                score_ta -= 3   #      

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
    # Supply/Demand 
    if in_demand and is_long:    rocket += 12   #    Demand   
    if in_supply and not is_long: rocket += 12  #    Supply   
    if in_demand and not is_long: rocket -= 10  # 
    if in_supply and is_long:    rocket -= 10   # 
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

    # 
    #   TP/SL    
    #  -:
    #   SL   Order Block / swing low/high,  ATR1.5
    #   TP1   Supply/Demand 
    #   TP2     (50% FVG /  High)
    #   TP3    (100%  / EMA200 / ATH )
    # 
    import math

    def smart_round(val):
        if val == 0: return 0
        magnitude = math.floor(math.log10(abs(val))) if val > 0 else 0
        precision = max(8, -magnitude + 3)
        return round(val, precision)

    #      4H  
    try:
        c4h_levels = get_binance_ohlc(sym, "4h", 100) or []
        highs_4h_l = [c["high"] for c in c4h_levels]
        lows_4h_l  = [c["low"]  for c in c4h_levels]

        # Swing Highs  Lows ( )
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

        #      
        levels_above = sorted([h for h in swing_highs if h > price * 1.005])
        levels_below = sorted([l for l in swing_lows  if l < price * 0.995], reverse=True)

        #  
        r1 = levels_above[0] if len(levels_above) > 0 else None
        r2 = levels_above[1] if len(levels_above) > 1 else None
        r3 = levels_above[2] if len(levels_above) > 2 else None
        s1 = levels_below[0] if len(levels_below) > 0 else None
        s2 = levels_below[1] if len(levels_below) > 1 else None

    except Exception:
        r1 = r2 = r3 = s1 = s2 = None
        highs_4h_l = []
        lows_4h_l  = []

    #  ATR    
    atr_min = atr if atr > 0 else price * 0.02

    if is_long:
        #  SL:   Swing Low,   1ATR 
        if s1 and s1 < price - atr_min * 0.5:
            sl_raw = s1 * 0.998   #   swing low
        elif s2 and s2 < price - atr_min:
            sl_raw = s2 * 0.998
        else:
            sl_raw = price - atr_min * 1.5
        sl = smart_round(max(sl_raw, price * 0.80))  #   -20%

        #  TP1:  Swing High 
        if r1 and r1 < price * 1.15:
            tp1 = smart_round(r1 * 0.998)    #     resistance
        else:
            tp1 = smart_round(price + atr_min * 1.0)

        #  TP2:  Swing High / 1.618    
        move = price - sl_raw
        fib_target = price + move * 1.618
        if r2 and r2 < price * 1.30:
            tp2 = smart_round(r2 * 0.998)
        else:
            tp2 = smart_round(fib_target)

        #  TP3:   (EMA200 / 2.618 Fib / max 4H) 
        fib_target3 = price + move * 2.618
        if r3 and r3 < price * 1.50:
            tp3 = smart_round(r3 * 0.998)
        elif ema200_v > price * 1.05:
            tp3 = smart_round(ema200_v * 0.998)
        else:
            tp3 = smart_round(fib_target3)

        swing = smart_round(s1 if s1 else price * 0.92)

    else:  # SHORT
        #  SL:   Swing High 
        if r1 and r1 > price + atr_min * 0.5:
            sl_raw = r1 * 1.002
        elif r2 and r2 > price + atr_min:
            sl_raw = r2 * 1.002
        else:
            sl_raw = price + atr_min * 1.5
        sl = smart_round(min(sl_raw, price * 1.20))  #   +20%

        #  TP1:  Swing Low 
        if s1 and s1 > price * 0.85:
            tp1 = smart_round(s1 * 1.002)
        else:
            tp1 = smart_round(price - atr_min * 1.0)

        #  TP2:  Swing Low / 1.618 Fib 
        move = sl_raw - price
        fib_target = price - move * 1.618
        if s2 and s2 > price * 0.70:
            tp2 = smart_round(s2 * 1.002)
        else:
            tp2 = smart_round(fib_target)

        #  TP3: 2.618 Fib / EMA200  
        fib_target3 = price - move * 2.618
        if s2 and s2 * 0.85 > price * 0.50:
            tp3 = smart_round(s2 * 0.85)
        elif ema200_v < price * 0.95 and ema200_v > 0:
            tp3 = smart_round(ema200_v * 1.002)
        else:
            tp3 = smart_round(fib_target3)

        swing = smart_round(r1 if r1 else price * 1.08)

    #    
    if is_long:
        # tp1 < tp2 < tp3    
        tp1 = smart_round(max(tp1, price * 1.01))
        tp2 = smart_round(max(tp2, tp1 * 1.01))
        tp3 = smart_round(max(tp3, tp2 * 1.01))
        sl  = smart_round(min(sl,  price * 0.99))
    else:
        # tp1 > tp2 > tp3    
        tp1 = smart_round(min(tp1, price * 0.99))
        tp2 = smart_round(min(tp2, tp1 * 0.99))
        tp3 = smart_round(min(tp3, tp2 * 0.99))
        sl  = smart_round(max(sl,  price * 1.01))

    if sl <= 0 or sl == price:
        sl = smart_round(price * 0.85 if is_long else price * 1.15)
    if swing <= 0 or swing == price:
        swing = smart_round(price * 0.92 if is_long else price * 1.08)

    rr = abs(tp3 - price) / abs(sl - price) if abs(sl - price) > 0 else 1.5

    #     
    sl_source  = "Swing Low" if is_long else "Swing High"
    tp1_source = f"R{1 if is_long else 'S'}1  Swing "
    tp2_source = "Fib 1.618" if not r2 else "R2  Swing "
    tp3_source = "Fib 2.618" if not r3 else "R3   "
    if rocket >= 80:   rocket_label = " ROCKET"
    elif rocket >= 70: rocket_label = " "
    elif rocket >= 60: rocket_label = " "
    elif rocket >= 50: rocket_label = " "
    elif rocket >= 40: rocket_label = " "
    else:              rocket_label = " "

    smc_factors = []
    if smc_bos_bull:     smc_factors.append("BOS ")
    if smc_bos_bear:     smc_factors.append("BOS ")
    if smc_ob_accum:     smc_factors.append("OB ")
    if smc_liq_sweep:    smc_factors.append("Liq Sweep")
    if smc_smart_accum:  smc_factors.append("Smart Accum ")
    if smc_smart_dist:   smc_factors.append("Smart Dist ")
    if smc_fvg_bull:     smc_factors.append("FVG ")
    if smc_fvg_bear:     smc_factors.append("FVG ")
    if tf_aligned_bull:  smc_factors.append("TF Align Bull")
    if tf_aligned_bear:  smc_factors.append("TF Align Bear")
    if fund_recovery:    smc_factors.append("Recovery ")
    if bb_squeeze:       smc_factors.append("BB Squeeze")
    if macd_bullish:     smc_factors.append("MACD Bull")
    if macd_bearish:     smc_factors.append("MACD Bear")
    if supertrend_bull is True:  smc_factors.append("ST BUY ")
    elif supertrend_bull is False: smc_factors.append("ST SELL ")

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
        "st_label": (" BUY" if supertrend_bull else (" SELL" if supertrend_bull is False else "")),
        "fund_rank_top50": rank <= 50, "fund_liquid": vol >= 10_000_000 and vol_ratio <= 50,
        # ema aliases  
        "ema20_1h": ema20_v, "ema50_1h": ema50_v, "ema200_1h": ema200_v,
        "ema20_1d": ema20_v, "ema50_1d": ema50_v, "ema200_1d": ema200_v,
        "rsi_1h": rsi_1h, "rsi_1d": rsi_1d,
    }


# 
#  :  ,  ,  ,  
# 

#     
TOP_LONG_SIGNALS:  dict = {}
TOP_SHORT_SIGNALS: dict = {}
TOP_SPOT_SIGNALS:  dict = {}

#       
import json as _json

_SIGNALS_FILE = "/tmp/best_trade_signals.json"

def _save_signals():
    """   """
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
    """   """
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
        log.info(f" : {len(TOP_LONG_SIGNALS)} , {len(TOP_SHORT_SIGNALS)} , {len(TOP_SPOT_SIGNALS)} ")
    except Exception as e:
        log.error(f"_load_signals: {e}")

def _signal_kb(symbol: str, msg_id: int = 0, chat_id: int = 0, mode: str = "long") -> InlineKeyboardMarkup:
    """  """
    tv = tv_link(symbol)
    cb = f"close_{mode}_{symbol}"
    rows = [
        [InlineKeyboardButton(" TradingView", url=tv),
         InlineKeyboardButton("  ", callback_data="show_menu")],
    ]
    if mode in ("long", "short"):
        rows.append([
            InlineKeyboardButton(" TP ",  callback_data=f"tp_{mode}_{symbol}"),
            InlineKeyboardButton(" SL ",   callback_data=f"sl_{mode}_{symbol}"),
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

    side_e = "" if is_long else ""
    side_t = "LONG" if mode == "long" else ("" if mode == "spot" else "SHORT")
    swing_lbl = "Swing Low" if is_long else "Swing High"

    if mode != "spot":
        lines = [
            f"*{symbol}USDT* {side_e} *{side_t}*",
            "",
            f" * :* `{fp(price)}`",
            "",
            f" *- 1:* `{fp(a['tp1'])}` *({pct(a['tp1'])})*",
            "",
            f" *- 2:* `{fp(a['tp2'])}` *({pct(a['tp2'])})*",
            "",
            f" *- 3:* `{fp(a['tp3'])}` *({pct(a['tp3'])})*",
            "",
            f" * :* `{fp(a['sl'])}` *({pct(a['sl'])})*",
            "",
            f" *{swing_lbl}:* `{fp(a['swing'])}`",
        ]
    else:
        lines = [
            f"*{symbol}USDT*  **",
            "",
            f" * :* `{fp(price)}`",
            "",
            f" *:* `{fp(a['tp2'])}` *({pct(a['tp2'])})*",
            "",
            f" * ():* `{fp(a['sl'])}`",
        ]

    return "\n".join(lines)
    side_e   = "" if is_long else ""
    side_t   = "LONG" if mode == "long" else ("" if mode == "spot" else "SHORT")
    price    = a["price"]
    r        = a["rocket"]
    rsi_4h   = a["rsi_4h"]
    trend_4h = a.get("trend_4h", "neutral")

    def pct(t):
        d = (t - price) / price * 100
        return f"+{d:.2f}%" if d >= 0 else f"{d:.2f}%"

    def ri(v): return "" if v < 30 else ("" if v > 70 else "")

    bar = "" * int(r/10) + "" * (10 - int(r/10))

    # EMA 
    ema_tags = []
    if a.get("above_ema200"): ema_tags.append("EMA200 ")
    if a.get("above_ema50"):  ema_tags.append("EMA50 ")
    if a.get("above_ema20"):  ema_tags.append("EMA20 ")
    if not ema_tags: ema_tags = ["  EMA "]

    # MACD
    macd_t = " " if a.get("macd_bullish") else (" " if a.get("macd_bearish") else " .")
    # Trend
    trend_t = {"bullish": " ", "bearish": " ", "neutral": " "}.get(trend_4h, "")
    # ST
    st_t = a.get("st_label", "")
    # Vol
    vol = a["vol"]
    vol_s = f"${vol/1e9:.2f}B" if vol>=1e9 else (f"${vol/1e6:.1f}M" if vol>=1e6 else f"${vol/1e3:.0f}K")
    # SMC
    smc = [f for f in a.get("smc_factors",[]) if "BB" not in f and "MACD" not in f][:3]

    # Conclusion
    if a.get("suspicious"):          conclusion = "     "
    elif is_long and rsi_4h > 75:    conclusion = "    "
    elif is_long and rsi_4h < 30 and r >= 70: conclusion = " RSI   +     !"
    elif is_long and r >= 80:        conclusion = "     "
    elif is_long and r >= 65:        conclusion = "  -"
    elif not is_long and r >= 70:    conclusion = "  -"
    elif mode == "spot" and a.get("fund_recovery"): conclusion = " Recovery  DCA  "
    else:                            conclusion = "     "

    # Header
    if mode == "long":   header_line = f" * *"
    elif mode == "short": header_line = f" * *"
    else:                 header_line = f" * *"

    lines = [
        f"{''*28}",
        f"{side_e} *{symbol}USDT*    {header_line}",
        f" {now_utc3()}      BEST TRADE",
        f"{''*28}",
        "",
        f" * :*  `{r}/100`  {a['rocket_label']}",
        f"  `{bar}`",
        "",
        f" *  EMA:*  {'  '.join(ema_tags)}",
        f" {conclusion}",
        "",
        f"{''*28}",
        f" * *",
        f"{''*28}",
        "",
        f"  :    `{fp(price)}`",
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
            f"  :   `{fp(a['tp2'])}`  *({pct(a['tp2'])})*",
            f"  :   `{fp(a['sl'])}`  *(SL   )*",
        ]

    if stats_24h:
        h24 = stats_24h.get("high", 0); l24 = stats_24h.get("low", 0)
        if h24 and l24:
            best = l24*1.005 if is_long else h24*0.995
            lines += ["", f"   24H:  `{fp(h24)}`  `{fp(l24)}`  `{fp(best)}`"]

    lines += [
        "",
        f"{''*28}",
        f" **",
        f"{''*28}",
        "",
        f"  RSI 4H:    {ri(rsi_4h)} `{rsi_4h:.0f}`  {' !' if rsi_4h<30 else (' !' if rsi_4h>70 else '')}",
        f"  MACD:      `{macd_t}`",
        f"   4H:  `{trend_t}`",
        f"  Supertrend:`{st_t}`",
    ]
    if smc:
        lines.append(f"  SMC:       `{'    '.join(smc)}`")
    lines += [
        "",
        f"  :     `{vol_s}`    Rank `#{a.get('rank','')}`",
        f"  :  1H`{fc(a['ch1h'])}`  24H`{fc(a['ch24h'])}`  7D`{fc(a['ch7d'])}`",
        "",
        f"{''*28}",
        f"  : *2% *    SL ",
        f"#{symbol}USDT",
    ]
    return "\n".join(lines)


# BACKWARD COMPAT alias
_old_build_signal_post = _build_signal_post

async def cmd_top_spot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/spot   :     """
    msg = await update.message.reply_text(
        "     ...\n"
        " -500  CMC + Binance "
    )
    coins = get_top500()
    if not coins:
        await msg.edit_text("  "); return

    #   1:   +   ATH 
    #      
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

        #   ( )
        if vol_ratio > 60:          continue   # / 
        if vol < 500_000:           continue   #  
        if mcap < 10_000_000:       continue   #    
        if "stablecoin" in tags:    continue
        if ch90d > -20:             continue   #   

        #   
        score = 0.0

        # 1.       (   =  )
        drop_score = abs(ch90d)          # -80%  80 
        score += drop_score * 1.0

        # 2.   ( )
        if ch7d > 0:   score += ch7d * 4.0    #     
        if ch30d > 0:  score += ch30d * 1.5   #   
        if ch24h > 0:  score += ch24h * 2.0   #  

        # 3.  
        if rank <= 20:   score += 50
        elif rank <= 50: score += 35
        elif rank <= 100: score += 20
        elif rank <= 200: score += 10
        elif rank <= 300: score += 5

        #    
        tag_bonus = len(tags & QUALITY_TAGS) * 8
        score += min(tag_bonus, 30)

        # 4.   ()
        if 2 <= vol_ratio <= 30:  score += 15   #  
        elif vol_ratio < 2:       score -= 10   #   
        if vol >= 50_000_000:     score += 15   #  
        elif vol >= 10_000_000:   score += 8

        #     ATH ()
        #   -80%     5x  ATH
        x_to_ath = 1 / (1 + ch90d/100) if ch90d < -5 else 1.0
        score += min(x_to_ath * 5, 40)   #    x-

        candidates.append((coin, score, x_to_ath, ch90d, ch7d))

    #   
    candidates.sort(key=lambda x: x[1], reverse=True)
    top_spot = candidates[:10]

    nav = InlineKeyboardMarkup([
        [InlineKeyboardButton(" ",     callback_data="top_spot"),
         InlineKeyboardButton("  ", callback_data="show_menu")],
        [InlineKeyboardButton("  ",    callback_data="top_long"),
         InlineKeyboardButton("  ",    callback_data="top_short")],
    ])

    if not top_spot:
        await msg.edit_text(
            "   \n\n      .",
            parse_mode="Markdown", reply_markup=nav)
        return

    #  
    list_lines = [
        " *BEST TRADE   *",
        f" {now_utc3()}",
        "  BEST TRADE",
        "",
        " *    :*",
        "",
    ]

    for i, (c, score, x_ath, ch90, ch7) in enumerate(top_spot, 1):
        sym    = c["symbol"]
        tv     = tv_link(sym)
        prc    = c["quote"]["USDT"].get("price", 0)
        rank   = c.get("cmc_rank", 999)
        vol    = c["quote"]["USDT"].get("volume_24h", 0) or 0
        vol_s  = f"${vol/1e9:.1f}B" if vol>=1e9 else f"${vol/1e6:.0f}M"

        #  
        if x_ath >= 10:   pot_icon = ""
        elif x_ath >= 5:  pot_icon = ""
        elif x_ath >= 3:  pot_icon = ""
        elif x_ath >= 2:  pot_icon = ""
        else:             pot_icon = ""

        trend_icon = "" if ch7 > 0 else ""

        list_lines += [
            f"{i}. [{sym}USDT]({tv})  {pot_icon}",
            f"    `{fp(prc)}`    Rank #{rank}    Vol {vol_s}",
            f"    -90: `{ch90:.0f}%`    : `~x{x_ath:.1f}`  ATH",
            f"   {trend_icon} 7: `{fc(ch7)}`",
            "",
        ]

    list_lines += ["      "]

    await msg.edit_text("\n".join(list_lines), parse_mode="Markdown",
                        reply_markup=nav, disable_web_page_preview=False)

    #    
    for coin, score, x_ath, ch90d_v, ch7d_v in top_spot:
        sym  = coin["symbol"]
        slug = coin.get("slug", sym.lower())
        q    = coin["quote"]["USDT"]
        try:
            prog = await update.message.reply_text(f"  {sym}...")

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
            mcap_s = fm(mcap) if mcap>0 else ""

            # 
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

            #   DCA
            buy1 = zone_90d if zone_90d>0 else price*0.85
            buy2 = (zone_30d or price*0.92)
            buy3 = atl*1.03 if atl>0 else price*0.75

            def ri(v): return "" if v<30 else ("" if v>70 else "")
            smc = [f for f in a.get("smc_factors",[]) if "BB" not in f]

            if x_ath >= 10:   pot_str = f"~x{x_ath:.0f} "
            elif x_ath >= 5:  pot_str = f"~x{x_ath:.1f} "
            elif x_ath >= 3:  pot_str = f"~x{x_ath:.1f} "
            elif x_ath >= 2:  pot_str = f"~x{x_ath:.1f} "
            else:             pot_str = f"~x{x_ath:.1f} "

            # 
            spot_score = sum([rsi_1d<35, ch90d_v<-60, ch7d_v>0, vol_growing, x_ath>=3, price<=buy2*1.1, bool(smc)])
            if spot_score >= 6:   verdict_e, verdict_t = "", "   "
            elif spot_score >= 4: verdict_e, verdict_t = "", "  "
            elif spot_score >= 2: verdict_e, verdict_t = "", "  DCA"
            else:                 verdict_e, verdict_t = "", "  "

            tags_str = "  ".join(list({t for t in coin.get("tags",[]) if t.lower() in {"defi","layer-1","layer-2","dex","oracle","gaming","nft","payments","infrastructure","web3"}})[:3])

            #      ,   
            lines = [
                f"*{sym}USDT*  **",
                f"  BEST TRADE    Rank #{rank}",
                "",
                f" * :* `{fp(price)}`",
                f" *  ATH:* *{pot_str}*",
                "",
            ]
            if ath > 0:
                lines.append(f" *ATH:* `{fp(ath)}`  *( +{to_ath:.0f}%  ATH)*")
            if atl > 0:
                lines.append(f" *ATL:* `{fp(atl)}`  *(  ATL  +{from_atl:.0f}%)*")
            lines += [
                "",
                f" 90: *{fc(ch90d_v)}*  30: *{fc(ch30d)}*  7: *{fc(ch7d_v)}*",
                "",
                f" RSI(1D): {ri(rsi_1d)}`{rsi_1d:.0f}` {' !' if rsi_1d<30 else ''}",
            ]
            if ema200_d:
                lines.append(f"EMA200(1D): `{fp(ema200_d)}` {' ' if price>ema200_d else ' '}")
            if tags_str:
                lines.append(f" {tags_str}")
            lines += [
                "",
                f" * 1 (40%):* `{fp(buy2)}`  *( /  )*",
                f" * 2 (40%):* `{fp(buy1)}`  *( 90)*",
                f" * 3 (20%):* `{fp(buy3)}`  *( ATL)*",
            ]
            if ath > 0:
                lines += [
                    "",
                    f" * 1:* `{fp(ath*0.33)}`  *(~x{ath*0.33/price:.1f})*",
                    f" * 2:* `{fp(ath*0.60)}`  *(~x{ath*0.60/price:.1f})*",
                    f" * 3:* `{fp(ath*0.90)}`  *(~x{ath*0.90/price:.1f})*",
                ]
            lines += [
                "",
                f"{verdict_e} *{verdict_t}*",
                f" : 510%     :  3 .",
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
        " *BEST TRADE   *\n\n  :",
        parse_mode="Markdown", reply_markup=main_kb()
    )


async def cmd_top_long(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/long   :  + -,   Rocket Score"""
    msg = await update.message.reply_text("   -... ~40 ")
    coins = get_top500()
    if not coins:
        await msg.edit_text("  "); return

    #    CMC  ()
    pre = []
    for coin in coins:
        q = coin["quote"]["USDT"]
        vol       = q.get("volume_24h",  0) or 0
        mcap      = q.get("market_cap",  0) or 0
        ch24h     = q.get("percent_change_24h", 0) or 0
        vol_ratio = (vol / mcap * 100) if mcap > 0 else 0
        #        
        if vol >= 1_000_000 and vol_ratio < 60 and ch24h > -20:
            pre.append(coin)

    # :    ,  
    pre.sort(key=lambda c: c["quote"]["USDT"].get("percent_change_24h", 0) or 0, reverse=True)

    #    Binance    
    scored = []
    for coin in pre[:150]:
        try:
            a  = real_full_analysis(coin)
            pa = pro_analysis(coin["symbol"], coin)
            sqf = signal_quality_filter(a, pa, coin)
            #  A/A+   rocket 
            if a["is_long"] and not a.get("suspicious"):
                if sqf["pass"] or a["rocket"] >= 65:
                    a["_sqf"] = sqf  #   
                    scored.append((coin, a))
        except: pass

    #   Rocket Score (  )
    scored.sort(key=lambda x: x[1]["rocket"], reverse=True)
    top_long = scored[:7]

    nav = InlineKeyboardMarkup([
        [InlineKeyboardButton(" ",     callback_data="top_long"),
         InlineKeyboardButton("  ", callback_data="show_menu")],
        [InlineKeyboardButton("  ",    callback_data="top_short"),
         InlineKeyboardButton("  ",    callback_data="top_spot")],
    ])

    if not top_long:
        #       RSI < 40   is_long
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
            " * - *\n\n"
            "     .\n"
            "     .",
            parse_mode="Markdown", reply_markup=nav
        )
        return

    #    
    btc_ctx = get_btc_market_context()
    btc_warn = ""
    if btc_ctx["ok"] and not btc_ctx["long_ok"]:
        btc_warn = f"\n *{btc_ctx['warning']}*\n"

    list_lines = [
        " *BEST TRADE   *",
        f" {now_utc3()}",
        f" {btc_ctx.get('label', '')}",
    ]
    if btc_warn:
        list_lines.append(btc_warn)
    list_lines += [
        "",
        " * -  :*",
        f" Killzone: {killzone_label().split(chr(10))[0]}",
        "",
    ]
    for i, (c, a) in enumerate(top_long, 1):
        sym    = c["symbol"]
        tv     = tv_link(sym)
        sqf    = a.get("_sqf", {})
        q_lbl  = sqf.get("quality", "")
        tkn    = get_tokenomics(sym)
        tkn_e  = "" if not tkn["has_data"] else ("" if tkn["risk"]=="high" else ("" if tkn["risk"]=="medium" else ""))
        rocket_lbl = "" if a["rocket"] >= 80 else ("" if a["rocket"] >= 68 else "")
        rsi_t  = " " if a["rsi_4h"] < 30 else ("." if a["rsi_4h"] < 60 else " ")
        trend_t = " " if a.get("trend_4h") == "bullish" else (" " if a.get("trend_4h") == "bearish" else " .")
        list_lines += [
            f" {i}. [{sym}USDT]({tv})  {rocket_lbl}  {tkn_e}",
            f"    `{fp(a['price'])}`  Score `{a['rocket']}`  : *{q_lbl}*",
            f"   RSI `{a['rsi_4h']:.0f}` {rsi_t}    {trend_t}",
            "",
        ]
    list_lines += ["    "]

    await msg.edit_text("\n".join(list_lines), parse_mode="Markdown",
                        reply_markup=nav, disable_web_page_preview=False)

    #     
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
        " *BEST TRADE   *\n\n  :",
        parse_mode="Markdown", reply_markup=main_kb()
    )


async def cmd_top_short(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/short   :  SHORT + -,  Rocket Score"""
    msg = await update.message.reply_text("   -... ~40 ")
    coins = get_top500()
    if not coins:
        await msg.edit_text("  "); return

    pre = []
    for coin in coins:   #  
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
            #     
            if not a["is_long"] and not a.get("suspicious") and a["rocket"] >= 40:
                scored.append((coin, a))
            elif a.get("rsi_4h", 50) > 72 and a["vol"] >= 2_000_000:
                a_short = dict(a); a_short["is_long"] = False
                scored.append((coin, a_short))
        except: pass

    scored.sort(key=lambda x: x[1]["rocket"], reverse=True)
    top_short = scored[:7]

    nav = InlineKeyboardMarkup([
        [InlineKeyboardButton(" ",    callback_data="top_short"),
         InlineKeyboardButton("  ", callback_data="show_menu")],
        [InlineKeyboardButton("  ",   callback_data="top_long"),
         InlineKeyboardButton("  ",   callback_data="top_spot")],
    ])

    if not top_short:
        await msg.edit_text(
            " * - *\n\n"
            "   .\n"
            "     .",
            parse_mode="Markdown", reply_markup=nav
        )
        return

    list_lines = [
        " *BEST TRADE   *",
        f" {now_utc3()}",
        f"  BEST TRADE",
        "",
        " * -  :*",
        "",
    ]
    for i, (c, a) in enumerate(top_short, 1):
        sym  = c["symbol"]
        tv   = tv_link(sym)
        rsi_t = " " if a["rsi_4h"] > 70 else ("." if a["rsi_4h"] > 45 else " ")
        trend_t = " " if a.get("trend_4h") == "bearish" else (" " if a.get("trend_4h") == "bullish" else " .")
        ema_t = " EMA200 " if a.get("above_ema200") else " EMA200 "
        list_lines += [
            f" {i}. [{sym}USDT]({tv})",
            f"    `{fp(a['price'])}`    Score `{a['rocket']}`    RSI `{a['rsi_4h']:.0f}` {rsi_t}",
            f"    : {trend_t}    {ema_t}",
            "",
        ]
    list_lines += ["    "]

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
        " *BEST TRADE   *\n\n  :",
        parse_mode="Markdown", reply_markup=main_kb()
    )


async def _search_coin_by_symbol(symbol: str) -> dict | None:
    """     CMC API       $1M"""
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
        # CMC         
        items = list(data.values())
        if not items:
            return None
        #   (   )
        item = items[0] if isinstance(items[0], dict) else items[0][0]
        mcap = item.get("quote", {}).get("USDT", {}).get("market_cap", 0) or 0
        if mcap < 1_000_000:  #  $1M 
            return None
        log.info(f"CMC found by symbol: {symbol} rank={item.get('cmc_rank')}")
        return item
    except Exception as e:
        log.error(f"CMC symbol search {symbol}: {e}")
        return None



async def _do_full_analysis(bot, chat_id: int, symbol: str) -> bool:
    """         """
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
                f" *{symbol}USDT*  \n\n : `/full BTC`  `/full SOL`",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("  ", callback_data="show_menu")
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

    #  PRO ANALYSIS     
    pa    = pro_analysis(symbol, coin)
    a     = real_full_analysis(coin)   #  TP/SL 
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

    #   
    direction = pa.get("direction", "long") if pa["ok"] else ("long" if a["is_long"] else "short")
    is_long   = direction != "short"
    pro_score = pa.get("pro_score", a["rocket"])
    eq_bar    = ""*int(pro_score/10) + ""*(10-int(pro_score/10))
    entry_q   = pa.get("entry_quality", "B ")
    setup     = pa.get("setup_type", "Multi-TF")

    side_e = "" if is_long else ""
    side_t = "LONG" if is_long else "SHORT"

    rsi_4h = pa.get("rsi_4h", a["rsi_4h"])
    rsi_1d = pa.get("rsi_1d", 50.0)
    ema200_4h = pa.get("ema200_4h", 0)
    ema200_1d = pa.get("ema200_1d", 0)

    def ri(v): return "" if v<30 else "" if v>70 else ""
    def pct(t): d=(t-price)/price*100; return f"+{d:.2f}%" if d>=0 else f"{d:.2f}%"

    vol_s  = f"${vol24/1e9:.2f}B" if vol24>=1e9 else f"${vol24/1e6:.1f}M" if vol24>=1e6 else f"${vol24/1e3:.0f}K"
    mcap_s = fm(mcap) if mcap>0 else ""

    #   6    
    kz      = get_killzone_status()
    sqf     = signal_quality_filter(a, pa, coin)
    tkn     = get_tokenomics(symbol)
    btc_ctx = get_btc_market_context()
    news    = get_coin_news(symbol)
    bt      = backtest_signal(symbol, is_long, lookback_candles=60)

    #  1  Confluence Matrix
    cm  = confluence_matrix(a, pa, coin, btc_ctx, kz)
    #  2  Volume Profile
    vp  = get_volume_profile(symbol)
    #  3  Order Book
    ob  = get_order_book_analysis(symbol)
    #  4  Macro (DXY/ETH-BTC/Gold/NQ)
    mac = get_macro_context()
    #  5  
    sea = get_seasonality()
    #  6  On-chain
    onc = get_onchain_data(symbol)

    ps  = calc_position_size(
        price    = price,
        sl       = a["sl"],
        deposit  = 1000.0,
        risk_pct = 1.0,
        leverage = 3.0 if not (ch90d < -40) else 1.0,
        quality  = sqf["quality"],
    )

    kz_e   = {"A+":"","A":"","B":"","C":"","D":""}.get(kz["active"]["quality"],"")
    sqf_e  = "" if "A+" in sqf["quality"] else ("" if "A " in sqf["quality"] else "")

    #  CONFLUENCE MATRIX    
    cm_bar = "" * (cm["hits"]) + "" * (15 - cm["hits"])
    parts_header_extra = [
        "",
        f" *CONFLUENCE MATRIX: {cm['grade']}*  ({cm['hits']}/15 )",
        f"`{cm_bar}`",
    ]
    if cm["factors"]:
        parts_header_extra.append("  " + "    ".join(cm["factors"][:6]))

    #     
    parts = [
        f"*{symbol}USDT* {side_e} *{side_t}*",
        f" BEST TRADE PRO   Rank #{rank}",
        "",
        f" *{fp(price)}*   Vol {vol_s}   MCap {mcap_s}",
    ]

    # Confluence Matrix   
    parts += parts_header_extra

    if ath > 0:
        parts.append(f" ATH `{fp(ath)}`   `~x{x_ath:.1f}` (+{to_ath:.0f}%)")
    if atl > 0:
        parts.append(f" ATL `{fp(atl)}`   ATL +{from_atl:.0f}%")

    parts += [
        "",
        f" 1H`{fc(ch1h)}`  24H`{fc(ch24h)}`  7D`{fc(ch7d)}`  30D`{fc(ch30d)}`  90D`{fc(ch90d)}`",
        "",
        f" *PRO Score: `{pro_score}/100`*    : *{entry_q}*",
        f"`{eq_bar}`",
        f" : *{setup}*",
        "",
    ]

    #  
    tf_map = {"bullish": "", "bearish": "", "neutral": ""}
    if pa["ok"]:
        tf_line = (f"TF: 1H{tf_map.get(pa['tf_1h'],'?')}  "
                   f"4H{tf_map.get(pa['tf_4h'],'?')}  "
                   f"1D{tf_map.get(pa['tf_1d'],'?')}  "
                   f"1W{tf_map.get(pa['tf_1w'],'?')}")
        conf = pa.get("tf_confluence", 0)
        conf_str = f"  Confluence: {abs(conf)}/4 {'' if conf > 0 else ''}"
        parts.append(tf_line + conf_str)

    parts += [
        f"RSI(1H){ri(pa.get('rsi_1h',50))}`{pa.get('rsi_1h',rsi_4h):.0f}`  "
        f"RSI(4H){ri(rsi_4h)}`{rsi_4h:.0f}`  "
        f"RSI(1D){ri(rsi_1d)}`{rsi_1d:.0f}`",
    ]
    if ema200_4h:
        parts.append(f"EMA200(4H)`{fp(ema200_4h)}` {'' if price>ema200_4h else ''}  "
                     f"EMA200(1D)`{fp(ema200_1d)}` {'' if price>ema200_1d>0 else ''}")

    # ICT / SMC 
    ict_hits = []
    if pa.get("ict_ob_bull"):     ict_hits.append("OB Bull ")
    if pa.get("ict_ob_bear"):     ict_hits.append("OB Bear ")
    if pa.get("ict_fvg_bull"):    ict_hits.append("FVG Bull ")
    if pa.get("ict_fvg_bear"):    ict_hits.append("FVG Bear ")
    if pa.get("ict_liquidity_sweep"): ict_hits.append("Liq Sweep ")
    if pa.get("smc_bos"):         ict_hits.append(f"BOS {pa['smc_bos']} ")
    if pa.get("smc_choch"):       ict_hits.append(f"CHoCH {pa['smc_choch']} ")
    if pa.get("ict_pd_array"):    ict_hits.append(pa["ict_pd_array"])
    if ict_hits:
        parts.append(f"SMC/ICT: `{'    '.join(ict_hits[:4])}`")

    # Wyckoff + Elliott
    if pa.get("wyckoff_phase"):
        parts.append(f"Wyckoff: `{pa['wyckoff_phase']}`  {pa.get('wyckoff_event','')}")
    if pa.get("elliott_wave"):
        parts.append(f"Elliott: `{pa['elliott_wave']}`")

    # OI / Funding
    if pa.get("oi_signal"):
        parts.append(pa["oi_signal"])
    if pa.get("funding_rate") is not None:
        fr = pa["funding_rate"]
        fr_e = "" if fr < -0.02 else ("" if fr > 0.05 else "")
        parts.append(f"Funding: {fr_e}`{fr:+.4f}%`")

    # Volume
    vol_info = []
    if pa.get("vol_climax"):  vol_info.append("Climax ")
    if pa.get("vol_dry_up"):  vol_info.append("Dry-up ")
    if pa.get("vol_trend") == "increasing": vol_info.append("Vol ")
    if vol_info:
        parts.append(f"Volume: `{'    '.join(vol_info)}`")

    #  
    factors = pa.get("factors", [])
    if factors:
        parts += ["", " * :*"]
        for f_ in factors[:6]:
            parts.append(f"  {f_}")

    # 
    warnings = pa.get("warnings", [])
    if warnings:
        parts += [""]
        for w in warnings[:3]:
            parts.append(w)

    # Volume Profile ( 2)
    if vp["ok"]:
        parts += [
            "",
            f" *Volume Profile:*",
            f"  POC: `{fp(vp['poc'])}`  VAH: `{fp(vp['vah'])}`  VAL: `{fp(vp['val'])}`",
            f"  {vp['label']}",
        ]

    # Order Book ( 3)
    if ob["ok"]:
        parts += ["", f" *:* {ob['label']}"]
        if ob["bid_wall"]:
            parts.append(f"    : `{fp(ob['bid_wall'])}`")
        if ob["ask_wall"]:
            parts.append(f"    : `{fp(ob['ask_wall'])}`")

    # Macro / ETH-BTC / Gold / AMD ( 4)
    if mac["ok"]:
        parts += ["", f" * :*"]
        if mac["altseason_label"]:
            parts.append(f"  {mac['altseason_label']}")
        if mac["macro_label"]:
            parts.append(f"  {mac['macro_label']}")
        if mac.get("gold_label"):
            parts.append(f"  {mac['gold_label']}")
        trad = mac.get("traditional_risk", "neutral")
        if trad == "risk_off":
            parts.append(f"      risk-off    ")
        elif trad == "cautious":
            parts.append(f"   Gold    ")

    # AMD Phase (ICT Power of Three)
    amd_lbl = pa.get("amd_label") if pa.get("ok") else None
    if amd_lbl:
        parts.append(f"  {amd_lbl}")

    #  ( 5)
    if sea["ok"]:
        parts += [
            "",
            f" *:* {sea['label']}",
            f"  {sea['month_note']}",
            f"    : `{sea['halving_phase']}`  "
            f"({sea['cycle_pct']:.0f}% )  "
            f" : `{sea['days_to_next_halving']}`",
        ]

    # On-chain ( 6)
    if onc["ok"]:
        parts += [
            "",
            f" *On-chain:* {onc['flow_label']}",
            f"  {onc['whale_label']}",
        ]

    # Killzone
    kz_active = kz["active"]
    kz_nxt    = kz.get("next")
    parts.append(
        f"{kz_e} *Killzone:* {kz_active['name']}   `{kz_active['quality']}`"
        + (f"   {kz_active.get('remaining_min',0)} " if kz_active.get('remaining_min') else "")
    )
    if kz_nxt:
        parts.append(f"    : {kz_nxt['name']}  {kz_nxt.get('in_min',0)} ")

    #  
    parts += [
        "",
        f"{sqf_e} * : {sqf['quality']}*  (Score: {sqf['score']}/100)",
    ]
    for r_ in sqf["reasons"][:4]:
        parts.append(f"  {r_}")
    for w_ in sqf["warnings"][:2]:
        parts.append(f"  {w_}")

    #  / 
    if news["ok"]:
        parts += ["", f" *:* {news['label']}"]
        if news["catalyst"]:
            cat = news["catalyst"]
            e   = "" if cat["sentiment"] == "positive" else ""
            parts.append(f"  {e} {cat['title'][:80]}  _{cat['age']}_")
        for n in news["news"][1:3]:
            e = "" if n["sentiment"]=="positive" else ("" if n["sentiment"]=="negative" else "")
            parts.append(f"  {e} {n['title'][:70]}")

    # Backtesting
    if bt["ok"]:
        bt_e = "" if bt["winrate"] >= 60 else ("" if bt["winrate"] >= 50 else ("" if bt["winrate"] >= 40 else ""))
        parts += [
            "",
            f" *Backtesting ( 60  4H):*",
            f"  {bt_e} Winrate: `{bt['winrate']:.0f}%`  "
            f": `{bt['total']}`  "
            f"Expectancy: `{bt['expectancy']:+.2f}R`",
            f"   : `{bt['best_streak']}`   "
            f": `{bt['worst_streak']}` ",
            f"  {bt['label']}",
        ]

    # 
    if tkn["has_data"]:
        parts += ["", f" *:* {tkn['risk_label']}"]
        parts.append(f"  {tkn['note']}")
        parts.append(f"  {tkn['recommendation']}")
        if not tkn["spot_ok"]:
            parts.append("   *    *")

    # BTC 
    if btc_ctx["ok"]:
        btc_ok = btc_ctx["long_ok"] if is_long else btc_ctx["short_ok"]
        btc_e  = "" if btc_ok else ""
        parts += [
            "",
            f" *BTC :* {btc_ctx['label']}",
            f"  BTC `${btc_ctx['btc_price']:,.0f}`  "
            f"1H`{fc(btc_ctx['btc_ch1h'])}`  24H`{fc(btc_ctx['btc_ch24h'])}`  "
            f"RSI4H`{btc_ctx['rsi_4h']:.0f}`",
            f"  {btc_e} {' ' if btc_ok else '   BTC '}"
        ]
        if btc_ctx["warning"]:
            parts.append(f"  {btc_ctx['warning']}")

    #  
    if ps.get("ok"):
        parts += ["", format_position_size(ps, is_long)]

    parts += [""]  #   TP/SL

    # TP/SL
    parts += [
        f" * :* `{fp(price)}`",
        "",
        f" *TP1:* `{fp(a['tp1'])}` *({pct(a['tp1'])})* _{a.get('tp1_source','')}_",
        f" *TP2:* `{fp(a['tp2'])}` *({pct(a['tp2'])})* _{a.get('tp2_source','')}_",
        f" *TP3:* `{fp(a['tp3'])}` *({pct(a['tp3'])})* _{a.get('tp3_source','')}_",
        "",
        f" *SL:* `{fp(a['sl'])}` _{a.get('sl_source','')}_  R:R `1:{a['rr']:.1f}`",
    ]

    #  DCA 
    if ath > 0 and ch90d < -30:
        parts += [
            "",
            f" * DCA :*",
            f"   1 (40%): `{fp(buy_hi)}`",
            f"   2 (40%): `{fp(buy_lo)}`",
            f"   3 (20%): `{fp(atl*1.05 if atl>0 else price*0.7)}`",
            f"  : `{fp(sell_t)}`  (~x{sell_t/price:.1f})",
        ]

    # 
    if pro_score >= 75:    ve, vt = "", " "
    elif pro_score >= 60:  ve, vt = "", " "
    elif pro_score >= 45:  ve, vt = "", "   "
    else:                  ve, vt = "", "  "

    if is_long and rsi_1d < 35 and ch90d < -40:
        rec = "    DCA"
    elif is_long:
        rec = "   2-5x"
    else:
        rec = "   2-5x"

    parts += [
        "",
        f"{ve} *{vt}*   {rec}",
        f"  1-2%    SL ",
        f"#{symbol}USDT",
    ]

    text = "\n".join(parts)
    if len(text) > 4096:
        text = text[:4090] + "..."

    await send_coin(bot, chat_id, symbol, slug, a, text)
    return True


async def cmd_full_v2(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/full SYMBOL    """
    if not ctx.args:
        await update.message.reply_text(
            " *   /full*\n\n"
            ": `/full BTC`\n"
            ": `/full ETH`  `/full SOL`  `/full RIVER`\n\n"
            ":\n"
            " EMA 20/50/200  RSI  MACD  Supertrend\n"
            " ATH / ATL  SMC/ICT   \n"
            "   OI   vs   ",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("  ", callback_data="show_menu"),
            ]])
        )
        return

    symbol = ctx.args[0].upper().replace("USDT","").replace("BUSD","")
    msg    = await update.message.reply_text(
        f"  *{symbol}USDT*...", parse_mode="Markdown"
    )
    try:
        await msg.delete()
    except: pass
    await _do_full_analysis(ctx.bot, update.effective_chat.id, symbol)


async def cmd_myid(update: Update, ctx):
    uid = update.effective_user.id
    cid = update.effective_chat.id
    await update.message.reply_text(
        f" * User ID:* `{uid}`\n *Chat ID:* `{cid}`",
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
        " *BEST TRADE   *\n\n  :",
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
        next_run_time=datetime.now(TZ)  #   
    )
    scheduler.add_job(
        check_alerts,
        "interval",
        minutes=5,
        args=[app.bot]
    )
    scheduler.start()
    log.info(" BEST TRADE v32.0 | Supply/Demand | Real-time signals | UTC+3")
    _load_signals()
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
