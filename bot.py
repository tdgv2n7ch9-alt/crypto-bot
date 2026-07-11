_WHALE_WATCH = ["BTC","ETH","SOL","BNB","XRP","ADA","DOGE","AVAX","LINK","DOT","MATIC","UNI","ATOM","LTC","BCH"]
_whale_last_alert = {}


# v42g
def get_usdt_dominance():
    try:
        data = _cg_get("https://api.coingecko.com/api/v3/global", timeout=8)
        return {"usdt_d": round(data.get("data",{}).get("market_cap_percentage",{}).get("usdt",0),2)}
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
import sys
import time
import io
import logging
import os
import random
import html
import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from datetime import datetime, timedelta, timezone
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz
import signal_journal
import subscribers
import ta_extra
import fa_engine
import signal_loop
import chart_v3
import chart_v4
import narrative
import whale_radar
import level_watch
import daily_metrics

BOT_TOKEN   = os.getenv("BOT_TOKEN")
CMC_API_KEY = os.getenv("CMC_API_KEY")
TWELVE_API_KEY = os.environ.get("twelve_api_key", "")
TZ          = pytz.timezone("Europe/Istanbul")
BOT_VERSION = "v130"         # обновлять при каждом коммите с изменением bot.py

# === Concurrency guard для тяжёлых сканов (ТОП ЛОНГ/ШОРТ/СПОТ, x100) ===
# Блокирующие HTTP-вызовы внутри сканов уводятся в run_in_executor, чтобы не морозить
# event loop (иначе /start и клики по кнопкам зависают на минуты за сканом). Guard не
# даёт второму запросу того же скана стартовать параллельно, и 30-мин автосигнальный
# джоб пропускает итерацию, если активен любой ручной скан.
_SCAN_TYPES = ("top_long", "top_short", "top_spot", "x100")
_scan_busy = {k: False for k in _SCAN_TYPES}

def _any_manual_scan_active() -> bool:
    return any(_scan_busy.values())

# Кэш результата тяжёлых сканов (60с TTL) -- повторное нажатие "Обновить" сразу после
# скана не должно снова гонять 80 блокирующих real_full_analysis()-вызовов. НЕ решает
# саму медлительность скана (см. _scan_top_short_sync и TIMING-логи), только защищает
# от бессмысленного повторного запуска в узком окне.
_SCAN_RESULT_CACHE_TTL = 60
_scan_result_cache = {k: {"ts": 0, "data": None} for k in _SCAN_TYPES}

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# === Общий rate-limiter + кэш для CoinGecko (free tier легко ловит 429 при частых вызовах) ===
import threading
_cg_lock = threading.Lock()
_cg_last_call_ts = 0.0
_CG_MIN_INTERVAL = 1.3          # мин. пауза между запросами к CoinGecko (~45/мин, с запасом от лимита)
_cg_cache = {}                  # (url, params_tuple) -> (ts, data)
_CG_CACHE_TTL = 60              # сек — одинаковые запросы в этом окне не бьют сеть повторно

def _cg_get(url: str, params: dict = None, timeout: int = 10):
    """Единая точка входа для GET-запросов к api.coingecko.com: делит один rate-limit
    и один кэш на все функции бота, чтобы параллельные экраны (OI/funding/OHLC/USDT mcap/
    dominance) не выбивали друг друга 429-й ошибкой при рендере."""
    global _cg_last_call_ts
    params = params or {}
    cache_key = (url, tuple(sorted(params.items())))
    now = time.time()
    cached = _cg_cache.get(cache_key)
    if cached and now - cached[0] < _CG_CACHE_TTL:
        return cached[1]
    with _cg_lock:
        wait = _CG_MIN_INTERVAL - (time.time() - _cg_last_call_ts)
        if wait > 0:
            time.sleep(wait)
        r = requests.get(url, params=params, timeout=timeout)
        _cg_last_call_ts = time.time()
    r.raise_for_status()
    data = r.json()
    _cg_cache[cache_key] = (time.time(), data)
    return data

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


def market_sentiment(coins: list, top_n: int = 100) -> tuple:
    """Единая формула рыночного сентимента (АПГРЕЙД 11.07 Этап 2.5) -- раньше карточки
    "Обзор" и "Тренд" считали сентимент по-разному: разный пул монет (топ-200 со
    стейблами vs топ-100 без стейблов) и разные пороги (>=65/>=50 vs >=60/<=40) --
    в одном скрине владельца "Обзор" писал "МЕДВЕЖИЙ (48% растут)", "Тренд" --
    "НЕЙТРАЛЬНЫЙ (49% бычьих)" при почти одинаковом %. Теперь один пул (топ-`top_n`
    по капе БЕЗ стейблкоинов -- их ~0%-движение искусственно тянуло долю "растущих"
    вниз) и один порог. Возвращает (label, pct_целое)."""
    pool = [c for c in coins if c.get("symbol") not in STABLECOINS][:top_n]
    if not pool:
        return "НЕЙТРАЛЬНЫЙ", 50
    bull = sum(1 for c in pool
               if (c.get("quote", {}).get("USDT", {}).get("percent_change_24h") or 0) > 0)
    pct = round(bull / len(pool) * 100)
    if pct >= 60:   label = "БЫЧИЙ"
    elif pct <= 40: label = "МЕДВЕЖИЙ"
    else:           label = "НЕЙТРАЛЬНЫЙ"
    return label, pct


def market_sentiment_emoji(label: str) -> str:
    return {"БЫЧИЙ": "🟢", "МЕДВЕЖИЙ": "🔴"}.get(label, "🟡")


_ALL_COINS_CACHE_TTL = 600   # 10 мин (см. ТЗ) -- было 1800с, но теперь первичный источник
                             # (CoinGecko markets) не расходует дефицитную месячную квоту

# Статус источников рангов/mcap -- для /radar_status. CMC теперь ФОЛЛБЕК (вызывается,
# только когда CoinGecko markets вернул пусто), поэтому его статус может быть "не
# проверялся в этом запуске", если CoinGecko всё это время исправно работал -- это
# нормально, не баг.
_DATA_SOURCE_STATUS = {
    "coingecko_markets": {"ok": None, "last_error": None, "last_ts": 0, "consecutive_failures": 0},
    "coingecko_global": {"ok": None, "last_error": None, "last_ts": 0, "consecutive_failures": 0},
    "cmc": {"ok": None, "last_error": None, "last_ts": 0, "consecutive_failures": 0},
    "cmc_global_metrics": {"ok": None, "last_error": None, "last_ts": 0, "consecutive_failures": 0},
    "yahoo_finance": {"ok": None, "last_error": None, "last_ts": 0, "consecutive_failures": 0},
}
_SOURCE_ALERT_THRESHOLD = 3   # подряд неудач источника -> алерт владельцу (run_watchdog)
_source_alerted = set()       # источники, по которым уже отправлен алерт (дедуп, как _job_alerted_stale)
# ROADMAP: CMC перестал быть критическим источником (решение владельца, 2026-07-10) --
# отказы этих источников НЕ считаются деградацией сервиса (CoinGecko первичный везде,
# CMC опциональный фоллбек), run_watchdog их не алертит, /health красит жёлтым, не красным.
_OPTIONAL_SOURCES = {"cmc", "cmc_global_metrics"}


def _record_source_result(name: str, ok: bool, error: str = None):
    """ROADMAP П3 -- единая точка учёта состояния источника данных: считает подряд идущие
    неудачи (для алерта в run_watchdog), а не просто последний факт ok/not ok. Замена
    разрозненных ручных `_DATA_SOURCE_STATUS[...] = {...}` присваиваний по всему файлу."""
    prev = _DATA_SOURCE_STATUS.get(name, {})
    consecutive = 0 if ok else prev.get("consecutive_failures", 0) + 1
    _DATA_SOURCE_STATUS[name] = {
        "ok": ok, "last_error": None if ok else error, "last_ts": time.time(),
        "consecutive_failures": consecutive,
    }
    if ok:
        _source_alerted.discard(name)


def _cmc_get(url: str, headers: dict, params: dict = None, timeout: int = 15):
    """ROADMAP П3 -- один короткий retry (1с backoff) ТОЛЬКО для транзиентных сетевых
    сбоев (таймаут/обрыв соединения) на пути к CMC. Намеренно НЕ ретраит успешно
    дошедшие HTTP-ответы с кодом ошибки (429 квота / 401 ключ) -- вызывающий код сам
    решает, что с ними делать (см. _fetch_cmc_markets docstring, зачем это разделение
    важно: ретрай 429/401 не может дать другой результат, только тратит кредит)."""
    last_exc = None
    for attempt in range(2):
        try:
            return requests.get(url, headers=headers, params=params, timeout=timeout)
        except requests.exceptions.RequestException as e:
            last_exc = e
            if attempt == 0:
                time.sleep(1)
                continue
    raise last_exc


def _validate_cmc_key() -> str:
    """См. subscribers._validate_github_token/signal_journal._validate_github_token --
    тот же класс паст-артефакта (smart quote/BOM/неразрывный пробел в Railway env vars)
    даёт малопонятную ошибку вместо явного указания на проблему в ключе. CMC вернул бы
    401 "invalid API key" и в этом случае -- неотличимо на вид от реально плохого ключа,
    но чинится по-разному (перевставить значение vs ротация ключа), стоит проверить
    первым (ROADMAP П3 -- см. PROGRESS.md, интермиттентные 401 из памяти проекта)."""
    if not CMC_API_KEY:
        return "CMC_API_KEY не задан (переменная окружения пуста/отсутствует)"
    try:
        CMC_API_KEY.encode("ascii")
        return ""
    except UnicodeEncodeError as e:
        bad_char = CMC_API_KEY[e.start]
        return (f"CMC_API_KEY содержит не-ASCII символ на позиции {e.start} "
                f"('{bad_char}', U+{ord(bad_char):04X}) -- похоже на артефакт копирования, "
                f"не обязательно сам ключ невалиден")


DATA_QUALITY_MAX_AGE_SEC = 15 * 60   # ROADMAP П3: источник считается устаревшим через N мин


def _data_quality_flags() -> list:
    """Валидатор входных данных перед сигналом (ROADMAP П3, доп. пункт очереди): смотрит на
    уже собранный _DATA_SOURCE_STATUS (CoinGecko/CMC/Yahoo=DXY) -- источник помечается
    "деградировавшим", если последняя проверка была неуспешной (ok=False) ИЛИ данных не
    было дольше DATA_QUALITY_MAX_AGE_SEC. НЕ блокирует сигнал -- вызывающий код передаёт
    результат в signal_journal.log_signal(degraded_data=...) как метку для последующего
    анализа "влияет ли деградация качества данных на win rate", решение о самом сигнале
    этой функцией не принимается и не меняется."""
    now = time.time()
    flags = []
    for name, status in _DATA_SOURCE_STATUS.items():
        if name in _OPTIONAL_SOURCES:
            # ROADMAP 2026-07-10 (решение владельца): CMC опционален, его отказ не
            # означает деградацию сигнальных данных (CoinGecko первичный везде).
            continue
        last_ts = status.get("last_ts") or 0
        age = now - last_ts if last_ts else None
        if status.get("ok") is False:
            flags.append(name)
        elif age is None or age > DATA_QUALITY_MAX_AGE_SEC:
            flags.append(f"{name}_stale")
    return flags


def _fetch_yahoo_chart(ticker: str, range_str: str = "5d", timeout: int = 6):
    """ROADMAP П3 -- общий фетчер для query2.finance.yahoo.com/.../chart/<ticker>
    (DXY/S&P500/Gold/VIX в /market и get_macro_data ходили за одним и тем же паттерном
    в 3 разных местах файла, каждое своим bare `except: pass`, тихо оседающим в 0 --
    именно в этом парсинге были все хотфиксы v56-v70, AUDIT.md §5.2).

    Исследование (2026-07-10, см. PROGRESS.md за источники): документированной бесплатной
    real-time альтернативы конкретно для ICE US Dollar Index (DXY) не нашлось --
    Twelve Data символ DXY не поддерживает (проверено вызовом symbol_search), Stooq сейчас
    закрыт JS proof-of-work анti-bot челленджем (не скриптуется голым GET), FRED DTWEXBGS
    -- другой по составу индекс (26 валют против 6 у DXY) и суточный с лагом, не замена.
    Оставляем тот же источник, но с реальным retry/valdiation/логированием вместо тихого
    проглатывания -- это и была фактическая причина многолетних багов, не сам источник.

    Возвращает (closes: list[float], regular_market_price: float|None) -- (None, None)
    при ошибке после ретрая. НЕ меняет то, как результат интерпретируется вызывающим кодом
    (пороги/веса macro_score и т.п. не тронуты -- только надёжность фетча)."""
    import time as _time
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range={range_str}"
    last_err = None
    for attempt in range(2):
        try:
            r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            data = r.json()
            result = data.get("chart", {}).get("result") or []
            if not result:
                raise ValueError(f"empty chart.result (chart.error={data.get('chart', {}).get('error')})")
            meta = result[0].get("meta", {})
            price = meta.get("regularMarketPrice")
            price = float(price) if price is not None else None
            closes_raw = result[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
            closes = [c for c in closes_raw if c is not None]
            _record_source_result("yahoo_finance", True)
            return closes, price
        except Exception as e:
            last_err = str(e)[:200]
            if attempt == 0:
                _time.sleep(1)  # один короткий retry -- транзиентные сетевые сбои/таймауты
                continue
    _record_source_result("yahoo_finance", False, f"{ticker}: {last_err}")
    return None, None


def get_data_source_status() -> dict:
    """Для /radar_status: последнее известное состояние источников рангов/mcap."""
    return _DATA_SOURCE_STATUS


# --- Health / heartbeat (ROADMAP П1) -------------------------------------------------
# /radar_status покрывает только памп-радар. Ничего не следило за тем, что остальные
# фоновые задачи (send_scheduled/check_alerts/whale_monitor/signal_loop/exit_tracker)
# реально ТИКАЮТ, а не просто "не упали" -- зависший (не упавший) event loop тут был бы
# не виден никак, кроме тишины в чате. Heartbeat не меняет поведение самих задач --
# только оборачивает их для APScheduler, отмечая факт успешного/неуспешного завершения.
_PROCESS_START_TS = time.time()
_job_heartbeats = {}          # name -> {"ts": float, "ok": bool, "detail": str}
_job_expected_interval_sec = {}   # name -> ожидаемый интервал тика, сек (для /health и watchdog)
_job_alerted_stale = set()    # чтобы watchdog не спамил на каждый тик, пока джоба не восстановится

# Whale Radar (Блок 1/2) -- НЕ путать с существующим whale_monitor()/"🐋 Whale Monitor"
# ниже (OI/funding/L-S-ratio институциональный скоринг, другая фича, тот же "кит" в
# названии случайно совпал). _whale_radar_state -- живое состояние стакана/сделок,
# создаётся здесь (пустое) ДО запуска фоновой задачи, чтобы get_whale_zones() ниже
# был безопасен вызывать даже до первого тика (просто вернёт пустые зоны).
_whale_radar_state = whale_radar.WhaleRadarState()


def get_whale_zones(symbol: str) -> dict:
    """Читает ТЕКУЩИЕ (уже кластеризованные) whale-зоны символа из живого состояния
    Whale Radar -- используется shadow_engine.compute_shadow() (Патч 06, только
    чтение, ничего не решает про боевой сигнал). Безопасно вызывать всегда: если
    Whale Radar ещё не успел просканировать символ (или задача не запущена по
    какой-то причине), вернёт {"bid": [], "ask": []}, не исключение."""
    try:
        return _whale_radar_state.get_zones(symbol.lower())
    except Exception:
        return {"bid": [], "ask": []}


def get_cvd_summary(symbol: str) -> dict:
    """Этап 3.1 (АПГРЕЙД 11.07) -- CVD (Cumulative Volume Delta) 1ч/4ч из живого
    состояния Whale Radar (тот же WS-поток publicTrade, что уже подписан для
    whale-детекции -- см. record_cvd() в whale_radar.py). Используется в карточке
    Институционал. Безопасно вызывать всегда: до первых данных вернёт нули/
    'нейтрально', не исключение (тот же принцип, что get_whale_zones())."""
    try:
        return _whale_radar_state.cvd_summary(symbol.lower())
    except Exception:
        return {"cvd_1h": 0, "cvd_4h": 0, "direction_1h": "нейтрально"}


# Блок 3, п.2: алерт owner-чату на появление крупной лимитки рядом с активным сигналом.
# Именованные константы -- калибровка владельца (задача: "≥$200K в пределах 3% от цены
# на активных сигналах"), не выдуманы отдельно от спеки.
WHALE_ALERT_MIN_USD = 200_000
WHALE_ALERT_MAX_DISTANCE_PCT = 3.0
WHALE_ALERT_COOLDOWN_SEC = 30 * 60  # анти-спам: не чаще раза в 30 мин на (символ, сторона)
_whale_alert_cooldown = {}


async def _send_whale_alert(bot: Bot, owner_id: int, event: dict):
    try:
        side_label = "БИД (ниже цены)" if event["side"] == "bid" else "АСК (выше цены)"
        base_symbol = event["symbol"].replace("USDT", "")
        text = (
            f"🐋 *Whale Radar — крупная лимитка рядом с активным сигналом*\n\n"
            f"*{event['symbol']}*: ${event['size_usd']:,.0f} ({side_label}) появилась "
            f"по цене `{event['price']}` ({event['distance_pct']:+.2f}% от текущей).\n\n"
            f"_Информационно, не гейт — `/whales {base_symbol}` для деталей._"
        )
        await bot.send_message(owner_id, text, parse_mode="Markdown")
    except Exception as e:
        print(f"Whale Radar: alert send failed: {e}")


def _make_whale_log_fn(bot: Bot, owner_id: int):
    """Оборачивает whale_radar.append_event() (персистентность всегда, как в Блоке 1)
    дополнительной проверкой алерт-критериев (Блок 3, п.2). Падение проверки алерта
    не должно ронять персистентность -- отдельный try/except вокруг неё."""
    def _log_fn(event):
        whale_radar.append_event(event)
        try:
            if event.get("type") != "whale_order" or event.get("event") != "appeared":
                return
            usd = event.get("size_usd", 0)
            dist = event.get("distance_pct")
            if usd < WHALE_ALERT_MIN_USD or dist is None or abs(dist) > WHALE_ALERT_MAX_DISTANCE_PCT:
                return
            base_symbol = event["symbol"].replace("USDT", "")
            is_active = (
                (base_symbol in TOP_LONG_SIGNALS and TOP_LONG_SIGNALS[base_symbol].get("status") == "active") or
                (base_symbol in TOP_SHORT_SIGNALS and TOP_SHORT_SIGNALS[base_symbol].get("status") == "active")
            )
            if not is_active:
                return
            key = (event["symbol"], event["side"])
            now_ts = time.time()
            if now_ts - _whale_alert_cooldown.get(key, 0) < WHALE_ALERT_COOLDOWN_SEC:
                return
            _whale_alert_cooldown[key] = now_ts
            asyncio.create_task(_send_whale_alert(bot, owner_id, event))
        except Exception as e:
            print(f"Whale Radar: alert-check failed: {e}")
    return _log_fn


async def _whale_radar_task(bot: Bot, owner_id: int):
    """Фоновая задача Whale Radar (Блок 2/3) -- тот же паттерн, что pump_detector: один
    asyncio.create_task на весь процесс, бесконечный цикл со своим внутренним
    реконнектом (см. whale_radar.run_whale_radar). Мутирует _whale_radar_state,
    созданный выше ДО запуска этой задачи -- get_whale_zones() уже может его читать.

    Символы явно объединены с топ-N по обороту (не заменяют его) -- CVD (Этап 3.1,
    АПГРЕЙД 11.07) обещан владельцу для BTC/ETH/SOL конкретно; на практике они и так
    всегда топ-3 по обороту (проверено живым вызовом fetch_top_symbols()), но список
    топ-N в этом процессе больше не пересобирается после старта (SYMBOL_REFRESH_SEC
    не используется -- существующее поведение, не трогаю в рамках Этапа 3), так что
    явная гарантия дешевле, чем полагаться на совпадение."""
    try:
        symbols = whale_radar.fetch_top_symbols()
        for must_have in ("btcusdt", "ethusdt", "solusdt"):
            if must_have not in symbols:
                symbols.append(must_have)
        await whale_radar.run_whale_radar(symbols=symbols, state=_whale_radar_state, verbose=False,
                                           log_fn=_make_whale_log_fn(bot, owner_id))
    except Exception as e:
        print(f"Whale Radar: фоновая задача упала ({type(e).__name__}: {e})")


async def _level_watch_task(bot: Bot, owner_id: int):
    """Level Watch (дневная разметка владельца, journal/watch_zones.json) -- тот же
    паттерн: один asyncio.create_task на весь процесс. startup_sync() сначала --
    подтягивает GitHub-версию поверх той, что была закоммичена этим самым деплоем
    (владелец мог обновить зоны через /zones_set ПОСЛЕ последнего git push)."""
    async def _send(oid, text):
        await bot.send_message(oid, text)

    try:
        await level_watch.startup_sync()
    except Exception as e:
        print(f"Level Watch: startup_sync упал ({type(e).__name__}: {e})")
    try:
        await level_watch.run_level_watch(_send, owner_id)
    except Exception as e:
        print(f"Level Watch: фоновая задача упала ({type(e).__name__}: {e})")


def _mark_heartbeat(name: str, ok: bool = True, detail: str = ""):
    _job_heartbeats[name] = {"ts": time.time(), "ok": ok, "detail": detail}
    if ok:
        _job_alerted_stale.discard(name)


def _heartbeat_wrapper(name: str, fn):
    """Оборачивает планируемую фоновую задачу heartbeat-отметкой после каждого вызова
    (успех либо исключение) -- саму задачу и её решения не трогает, просто наблюдает."""
    async def _wrapped(*args, **kwargs):
        try:
            result = await fn(*args, **kwargs)
            _mark_heartbeat(name, ok=True)
            return result
        except Exception as e:
            _mark_heartbeat(name, ok=False, detail=str(e)[:200])
            raise
    _wrapped.__name__ = f"heartbeat_{name}"
    return _wrapped


async def run_watchdog(bot: Bot):
    """Каждые WATCHDOG_INTERVAL_MIN проверяет heartbeat всех зарегистрированных фоновых
    задач. Если задача не отмечалась дольше 2x своего ожидаемого интервала -- шлёт
    владельцу ОДИН алерт на инцидент (не спамит), сбрасывается автоматически при
    следующем успешном heartbeat (см. _mark_heartbeat)."""
    import os
    owner_id = int(os.getenv("OWNER_CHAT_ID", "7009350191"))
    now = time.time()
    for name, expected in _job_expected_interval_sec.items():
        if not expected:
            continue
        hb = _job_heartbeats.get(name)
        age = (now - hb["ts"]) if hb else (now - _PROCESS_START_TS)
        if age > expected * 2 and name not in _job_alerted_stale:
            _job_alerted_stale.add(name)
            try:
                await bot.send_message(
                    owner_id,
                    f"⚠️ Watchdog: фоновая задача «{name}» не отмечалась {age/60:.0f} мин "
                    f"(ожидается раз в {expected/60:.0f} мин). Похоже, зависла или падает "
                    f"молча -- проверь /health.",
                )
            except Exception:
                pass

    # Источники данных (ROADMAP П3) -- N отказов подряд -> алерт (см. _record_source_result).
    # Ретраи внутри самих фетчеров не спасают от "ключ невалиден"/"квота исчерпана" --
    # тут нужен человек, не код. CMC-источники исключены (_OPTIONAL_SOURCES) -- решение
    # владельца 2026-07-10: CMC больше не критический источник, его отказ не должен
    # будить владельца, пока CoinGecko жив.
    for name, status in _DATA_SOURCE_STATUS.items():
        if name in _OPTIONAL_SOURCES:
            continue
        if status.get("consecutive_failures", 0) >= _SOURCE_ALERT_THRESHOLD and name not in _source_alerted:
            _source_alerted.add(name)
            try:
                await bot.send_message(
                    owner_id,
                    f"⚠️ Watchdog: источник данных «{name}» — {status['consecutive_failures']} отказов "
                    f"подряд. Последняя ошибка: {status.get('last_error') or '—'}. Проверь ключ/квоту "
                    f"(/health).",
                )
            except Exception:
                pass


async def run_daily_backup(bot: Bot):
    """Раз в сутки -- версионированный снапшот подписчиков и журнала в GitHub
    (backups/<date>/...), отдельно от рабочих data/chat_ids.json и journal/signals.json,
    которые last-write-wins перезаписываются (ROADMAP П1.4 -- у рабочих файлов нет
    истории снапшотов, эта задача её создаёт). Не бросает исключений наружу и не трогает
    рабочие файлы -- только читает текущее состояние и пишет по новому датированному пути."""
    import os
    owner_id = int(os.getenv("OWNER_CHAT_ID", "7009350191"))
    date_str = datetime.now(TZ).strftime("%Y-%m-%d")
    sub_ok = False
    journal_ok = False
    try:
        sub_ok = await subscribers.backup_snapshot(date_str)
    except Exception as e:
        print(f"run_daily_backup: subscribers snapshot failed: {e}")
    try:
        journal_ok = await signal_journal.backup_snapshot(date_str)
    except Exception as e:
        print(f"run_daily_backup: journal snapshot failed: {e}")
    if not (sub_ok and journal_ok):
        try:
            await bot.send_message(
                owner_id,
                f"⚠️ Дневной бэкап {date_str}: подписчики {'ок' if sub_ok else 'ОШИБКА'}, "
                f"журнал {'ок' if journal_ok else 'ОШИБКА'} -- проверь GITHUB_TOKEN/доступность.",
            )
        except Exception:
            pass


def _fetch_coingecko_markets(pages: int = 3, per_page: int = 250) -> list:
    """Первичный источник get_all_coins(): CoinGecko /coins/markets -- ранг/mcap/объём/
    цена/% изменения без месячной квоты CMC (freemium, наш общий rate-limit из _cg_get).
    До pages*per_page монет (по умолчанию верхние ~750 по капе).

    Ограничение: CoinGecko /coins/markets не отдаёт 90-дневное % изменение (только 1h/
    24h/7d/30d/200d/1y) -- percent_change_90d честно 0.0 для монет из этого источника
    (не фабрикуем; см. fa_engine.py-конвенцию "нет данных != придуманное значение)."""
    result = []
    for page in range(1, pages + 1):
        try:
            data = _cg_get("https://api.coingecko.com/api/v3/coins/markets", params={
                "vs_currency": "usd", "order": "market_cap_desc",
                "per_page": per_page, "page": page,
                "price_change_percentage": "1h,24h,7d,30d",
            }, timeout=20)
        except Exception as e:
            detail = str(e)
            if getattr(e, "response", None) is not None:
                detail = f"HTTP {e.response.status_code}: {e.response.text[:100]}"
            _DATA_SOURCE_STATUS["coingecko_markets"] = {"ok": False, "last_error": detail, "last_ts": time.time()}
            log.error(f"CoinGecko markets page {page}: {type(e).__name__}: {e}", exc_info=True)
            break
        if not data:
            break
        for d in data:
            sym = (d.get("symbol") or "").upper()
            if not sym or sym in STABLECOINS:
                continue
            mcap = d.get("market_cap") or 0
            if mcap and mcap < 100_000:
                continue
            result.append({
                "symbol": sym, "slug": d.get("id", sym.lower()),
                "cmc_rank": d.get("market_cap_rank") or 9999,
                "tags": [], "name": d.get("name", sym),
                "quote": {"USDT": {
                    "price": d.get("current_price", 0) or 0,
                    "volume_24h": d.get("total_volume", 0) or 0,
                    "market_cap": mcap,
                    "percent_change_1h": d.get("price_change_percentage_1h_in_currency", 0) or 0,
                    "percent_change_24h": d.get("price_change_percentage_24h_in_currency",
                                                d.get("price_change_percentage_24h", 0)) or 0,
                    "percent_change_7d": d.get("price_change_percentage_7d_in_currency", 0) or 0,
                    "percent_change_30d": d.get("price_change_percentage_30d_in_currency", 0) or 0,
                    "percent_change_90d": 0.0,
                }},
            })
        if len(data) < per_page:
            break
    if result:
        _DATA_SOURCE_STATUS["coingecko_markets"] = {"ok": True, "last_error": None, "last_ts": time.time()}
    return result


def _fetch_cmc_markets() -> list:
    """Фоллбек get_all_coins(): CMC listings (полный список до 5000, платная месячная
    квота) -- используется, только когда CoinGecko markets вернул пусто (недоступен/
    лимит). При исчерпании квоты CMC отвечает 429 с error_code 1010 -- фиксируем это в
    _DATA_SOURCE_STATUS для /radar_status, НЕ РЕТРАИМ HTTP-ошибки (429/401/etc) --
    осознанно (см. ниже), не тратим оставшийся платный кредит впустую на заведомо
    неуспешные повторы. run_watchdog шлёт владельцу алерт при N подряд неудач подряд
    (ROADMAP П3) -- retry тут не помог бы: 429 -- квота исчерпана до сброса, ретрай
    только тратит ещё один запрос впустую; 401 -- ключ невалиден, ретрай той же
    строкой ключа не может дать другой результат, нужна замена ключа человеком."""
    result = []
    seen_syms = set()
    key_issue = _validate_cmc_key()
    if key_issue:
        _record_source_result("cmc", False, key_issue)
        log.error(f"CMC all coins: {key_issue}")
        return result
    try:
        url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
        headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
        for start in range(1, 5001, 1000):
            try:
                params = {"start": start, "limit": 1000, "convert": "USDT", "sort": "market_cap"}
                r = requests.get(url, headers=headers, params=params, timeout=25)
                if r.status_code != 200:
                    err = r.json().get("status", {}).get("error_message", f"HTTP {r.status_code}")
                    _record_source_result("cmc", False, err)
                    log.error(f"CMC batch start={start}: {err}")
                    break
                batch = r.json().get("data", [])
                if not batch:
                    break
                added = 0
                for coin in batch:
                    sym = coin.get("symbol", "")
                    tags = [t.lower() for t in coin.get("tags", [])]
                    q = coin.get("quote", {}).get("USDT", {})
                    mcap = q.get("market_cap", 0) or 0
                    if sym in STABLECOINS: continue
                    if "stablecoin" in tags: continue
                    if "wrapped-tokens" in tags: continue
                    if sym in seen_syms: continue
                    if mcap > 0 and mcap < 100_000: continue
                    seen_syms.add(sym)
                    result.append(coin)
                    added += 1
                log.info(f"CMC batch start={start}: +{added} (всего {len(result)})")
                if len(batch) < 500:
                    break
                time.sleep(0.5)
            except Exception as e:
                log.error(f"CMC batch start={start}: {e}")
                break
        if result:
            _record_source_result("cmc", True)
    except Exception as e:
        _record_source_result("cmc", False, str(e))
        log.error(f"CMC all coins: {e}")
    return result


def get_all_coins():
    """Список монет с рангом/mcap/объёмом/% изменениями. Первичный источник --
    CoinGecko /coins/markets (без месячной квоты). CMC -- фоллбек, только если
    CoinGecko недоступен. НЕТ более "Binance-заглушки" с фиктивным rank=9999/mcap=0
    для монет вне обоих источников -- такая заглушка раньше молчаливо ложно
    срабатывала на мемкоин-фильтре (rank=9999 воспринимался как "супер-мемкоин", а не
    "неизвестно"). Если и CoinGecko, и CMC недоступны -- возвращаем последний кэш
    (даже протухший) вместо пустого списка, честнее, чем полное отсутствие данных."""
    now_ts = datetime.now(TZ).timestamp()
    if hasattr(get_all_coins, "_cache"):
        cached_time, cached_data = get_all_coins._cache
        if now_ts - cached_time < _ALL_COINS_CACHE_TTL and cached_data:
            return cached_data

    result = _fetch_coingecko_markets(pages=3, per_page=250)
    source_used = "coingecko"
    if not result:
        result = _fetch_cmc_markets()
        source_used = "cmc" if result else "none"

    if not result and hasattr(get_all_coins, "_cache"):
        log.error("get_all_coins: оба источника недоступны, отдаём протухший кэш")
        return get_all_coins._cache[1]

    result.sort(key=lambda x: x.get("cmc_rank", 9999))
    log.info(f"Список монет: {len(result)} (источник: {source_used})")
    get_all_coins._cache = (now_ts, result)
    return result


# Backward compat alias
def get_top500():
    return get_all_coins()

_global_metrics_cache = {"ts": 0, "data": {}}

def get_global_metrics() -> dict:
    """BTC.D/ETH.D/total_mcap — CoinGecko /global первичный источник, общий кэш на 60с
    (чтобы /market, Тренд и Институционал не расходились между собой). CMC — опциональный
    фоллбек, только если CoinGecko недоступен; отказ CMC сам по себе НЕ деградация
    (run_watchdog его не алертит, /health показывает жёлтым "отключён (опционально)").
    ROADMAP: CMC перестал быть критическим источником, 2026-07-10 (решение владельца)."""
    import time as _t
    if _t.time() - _global_metrics_cache["ts"] < 60 and _global_metrics_cache["data"]:
        return _global_metrics_cache["data"]
    try:
        data = _cg_get("https://api.coingecko.com/api/v3/global", timeout=15)
        d = data.get("data", {})
        result = {
            "total_mcap":      d.get("total_market_cap", {}).get("usd", 0) or 0,
            "btc_dominance":   d.get("market_cap_percentage", {}).get("btc", 0) or 0,
            "eth_dominance":   d.get("market_cap_percentage", {}).get("eth", 0) or 0,
            "mcap_change_24h": d.get("market_cap_change_percentage_24h_usd", 0) or 0,
        }
        _global_metrics_cache["ts"] = _t.time()
        _global_metrics_cache["data"] = result
        _record_source_result("coingecko_global", True)
        return result
    except Exception as e:
        _record_source_result("coingecko_global", False, f"{type(e).__name__}: {e}")
        log.error(f"Global metrics CoinGecko error: {type(e).__name__}: {e}")

    # --- CMC фоллбек, вызывается только если CoinGecko выше упал ---
    key_issue = _validate_cmc_key()
    if key_issue:
        _record_source_result("cmc_global_metrics", False, key_issue)
        return _global_metrics_cache["data"]
    try:
        url     = "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest"
        headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
        r = _cmc_get(url, headers, timeout=15)
        if r.status_code != 200:
            err = f"HTTP {r.status_code}: {r.text[:100]}"
            _record_source_result("cmc_global_metrics", False, err)
            return _global_metrics_cache["data"]
        d = r.json().get("data", {})
        q = d.get("quote", {}).get("USD", {})
        result = {
            "total_mcap":      q.get("total_market_cap", 0),
            "btc_dominance":   d.get("btc_dominance", 0),
            "eth_dominance":   d.get("eth_dominance", 0),
            "mcap_change_24h": q.get("total_market_cap_yesterday_percentage_change", 0),
        }
        _global_metrics_cache["ts"] = _t.time()
        _global_metrics_cache["data"] = result
        _record_source_result("cmc_global_metrics", True)
        return result
    except Exception as e:
        _record_source_result("cmc_global_metrics", False, f"{type(e).__name__}: {e}")
        return _global_metrics_cache["data"]

def get_btc_eth_price() -> dict:
    """BTC/ETH цена+%1ч/24ч — срез из get_all_coins() (CoinGecko первичный источник,
    CMC — опциональный фоллбек уже внутри неё, см. её докстринг). Раньше был отдельным
    прямым CMC-запросом; теперь ноль дополнительных вызовов -- данные уже в общем кэше.
    ROADMAP: CMC перестал быть критическим источником, 2026-07-10 (решение владельца)."""
    coins = get_all_coins()
    result = {}
    for sym in ("BTC", "ETH"):
        c = next((x for x in coins if x["symbol"] == sym), None)
        if not c:
            continue
        q = c.get("quote", {}).get("USDT", {})
        result[sym] = {
            "price": q.get("price", 0) or 0,
            "ch1h":  q.get("percent_change_1h", 0) or 0,
            "ch24h": q.get("percent_change_24h", 0) or 0,
        }
    return result

def _snap_cg_days(days: int) -> str:
    """CoinGecko /ohlc (free tier) принимает только days из {1,7,14,30,90,180,365} — иначе HTTP 400"""
    for allowed in (1, 7, 14, 30, 90, 180, 365):
        if days <= allowed:
            return str(allowed)
    return "365"

_CG_SLUG_MAP = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "BNB": "binancecoin", "XRP": "ripple", "ADA": "cardano",
    "AVAX": "avalanche-2", "DOT": "polkadot", "MATIC": "matic-network",
    "LINK": "chainlink", "UNI": "uniswap", "ATOM": "cosmos",
    "LTC": "litecoin", "BCH": "bitcoin-cash", "DOGE": "dogecoin",
    "SHIB": "shiba-inu", "TRX": "tron", "TON": "the-open-network",
    "NEAR": "near", "APT": "aptos", "ARB": "arbitrum",
    "OP": "optimism", "SUI": "sui", "INJ": "injective-protocol",
    "FTM": "fantom", "ALGO": "algorand", "ICP": "internet-computer",
    "AAVE": "aave", "CAKE": "pancakeswap-token", "MANA": "decentraland",
    "SAND": "the-sandbox", "AXS": "axie-infinity", "PEPE": "pepe",
    "WIF": "dogwifcoin", "BONK": "bonk", "JUP": "jupiter-exchange-solana",
}

def _cg_slug(symbol: str) -> str:
    sym = symbol.upper().replace("USDT","").replace("BUSD","").replace("USD","")
    return _CG_SLUG_MAP.get(sym, sym.lower())

BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline"
_BYBIT_INTERVAL_MAP = {"1h": "60", "1H": "60", "2h": "120", "2H": "120", "4h": "240", "4H": "240",
                        "1d": "D", "1D": "D", "1w": "W", "1W": "W"}
_bybit_kline_lock = threading.Lock()
_bybit_kline_last_call_ts = 0.0
_BYBIT_KLINE_MIN_INTERVAL = 0.15   # щедрый лимит Bybit REST (не сравнить с CoinGecko free)


def _get_ohlc_bybit(symbol: str, interval: str, limit: int) -> list:
    """OHLC через Bybit /v5/market/kline (category=linear) — первичный источник свечей:
    доступен из EU/Railway (в отличие от Binance), реальный volume по свече (в отличие от
    CoinGecko free /ohlc, который всегда отдаёт vol=0.0), до 1000 баров за один запрос,
    честные интервалы 1h/4h/1d/1w без гранулярной путаницы CoinGecko. Пустой список, если
    символ не является Bybit USDT-linear перпетуалом или запрос не удался — вызывающая
    сторона (get_binance_ohlc) в этом случае идёт в CoinGecko-фоллбек."""
    biv = _BYBIT_INTERVAL_MAP.get(interval)
    if not biv:
        return []
    global _bybit_kline_last_call_ts
    try:
        with _bybit_kline_lock:
            wait = _BYBIT_KLINE_MIN_INTERVAL - (time.time() - _bybit_kline_last_call_ts)
            if wait > 0:
                time.sleep(wait)
            r = requests.get(BYBIT_KLINE_URL, params={
                "category": "linear", "symbol": f"{symbol.upper()}USDT",
                "interval": biv, "limit": min(1000, max(1, limit)),
            }, timeout=10)
            _bybit_kline_last_call_ts = time.time()
        r.raise_for_status()
        rows = r.json().get("result", {}).get("list", [])
        if not rows:
            return []
        rows = list(reversed(rows))  # Bybit отдаёт новые бары первыми — разворачиваем в хронологический порядок
        return [{
            "open": float(row[1]), "high": float(row[2]),
            "low": float(row[3]), "close": float(row[4]),
            "vol": float(row[5]), "timestamp": int(row[0]),
        } for row in rows]
    except Exception:
        return []


def _get_ohlc_coingecko(symbol: str, interval: str = "4h", limit: int = 200) -> list:
    """OHLC через CoinGecko — фоллбек get_binance_ohlc() для монет вне Bybit USDT-linear
    перпетуалов (делистнутые/неперпетуальные и т.п.), Binance сам по себе заблокирован
    на Railway.

    CoinGecko free /ohlc отдаёт фиксированную гранулярность по диапазону days,
    а не по нашему interval: 1д -> 30-мин бары, 7-30д -> 4ч бары, 90-365д -> 4-дневные бары.
    days подобран так, чтобы попасть в нужную гранулярность и получить максимум точек,
    а не просто округлён вверх до ближайшего валидного значения (иначе 4ч-интервал
    случайно попадает в 4-дневную гранулярность и почти не даёт свечей)."""
    slug = _cg_slug(symbol)
    # days подобран под гранулярность CoinGecko, см. докстринг
    if interval in ("1h","1H"):
        days = "1"                 # 30-мин бары — ближайшая гранулярность к часовой
    elif interval in ("4h",):
        days = "30"                # 4ч бары, макс. точек в этой гранулярности (~180)
    elif interval in ("1d","1D","1w","1W"):
        days = "365"               # макс. история, ~92 точки по 4 дня
    else:
        raw = max(2, limit // 24) if (interval and interval[-1] in ("h","H")) else min(365, limit)
        days = _snap_cg_days(raw)  # неизвестный interval — старый расчёт, но без HTTP 400
    try:
        data = _cg_get(f"https://api.coingecko.com/api/v3/coins/{slug}/ohlc",
                        params={"vs_currency": "usd", "days": days}, timeout=10)
        if isinstance(data, list) and data:
            result = []
            for d in data:
                result.append({
                    "open": float(d[1]), "high": float(d[2]),
                    "low": float(d[3]), "close": float(d[4]),
                    "vol": 0.0, "timestamp": d[0]
                })
            return result[-limit:] if len(result) > limit else result
    except Exception:
        pass
    return []


def get_binance_ohlc(symbol: str, interval: str = "4h", limit: int = 200) -> list:
    """OHLC: Bybit REST (первичный источник — см. _get_ohlc_bybit) с фоллбеком на
    CoinGecko (_get_ohlc_coingecko) для монет вне Bybit USDT-linear перпетуалов. Имя
    оставлено прежним (get_binance_ohlc) — множество вызывающих мест по всему bot.py/
    ta_extra.py/fa_engine.py, переименование не даёт функциональной пользы и рискует
    разойтись местами; сам Binance по-прежнему недоступен с Railway."""
    data = _get_ohlc_bybit(symbol, interval, limit)
    if data:
        return data
    return _get_ohlc_coingecko(symbol, interval, limit)

def get_binance_24h(symbol: str) -> dict:
    """24h stats: high, low, open, last price — через CoinGecko (Binance заблокирован на Railway)"""
    try:
        slug = _cg_slug(symbol)
        data = _cg_get("https://api.coingecko.com/api/v3/coins/markets",
                        params={"vs_currency": "usd", "ids": slug, "price_change_percentage": "24h"},
                        timeout=8)
        if not data:
            return {}
        d = data[0]
        last = float(d.get("current_price", 0) or 0)
        ch24 = float(d.get("price_change_percentage_24h", 0) or 0)
        open_ = last / (1 + ch24/100) if (1 + ch24/100) != 0 else last
        return {
            "high": float(d.get("high_24h", 0) or 0),
            "low":  float(d.get("low_24h",  0) or 0),
            "open": open_,
            "last": last,
            "vol":  float(d.get("total_volume", 0) or 0),
        }
    except:
        return {}

def get_binance_alltime_low(symbol: str) -> float:
    """Исторический минимум цены — через CoinGecko /coins/{id} (market_data.atl), Binance заблокирован на Railway"""
    try:
        slug = _cg_slug(symbol)
        data = _cg_get(f"https://api.coingecko.com/api/v3/coins/{slug}",
                        params={"localization": "false", "tickers": "false", "community_data": "false",
                                "developer_data": "false"}, timeout=12)
        atl = float(data.get("market_data", {}).get("atl", {}).get("usd", 0) or 0)
        return atl
    except:
        return 0

def get_funding_rate(symbol: str) -> dict:
    """Funding rate через CoinGecko derivatives (замена fapi.binance.com, заблокированного на Railway)"""
    try:
        d = _fetch_coingecko_oi_map().get(symbol)
        if not d:
            return {"rate": 0, "signal": "", "mark": 0, "basis": 0, "ok": False}
        rate = d["funding"]  #  %  ( CoinGecko    lastFundingRate*100  Binance)
        mark = d["price"]
        idx  = d["index"]
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

_OI_CG_CACHE = {"ts": 0, "data": {}}
_OI_CG_TTL = 90  # seconds — one shared fetch of the full derivatives list serves every symbol
_OI_HISTORY = {}  # symbol -> (timestamp, oi_usd) snapshot from the previous poll

def _fetch_coingecko_oi_map() -> dict:
    """OI + funding + price по символам через CoinGecko /derivatives (замена fapi.binance.com,
    заблокированного на Railway). Берёт тикер Binance (Futures), либо крупнейший по OI на других
    биржах, если Binance для монеты нет. Кэш на _OI_CG_TTL секунд."""
    now_ts = time.time()
    if now_ts - _OI_CG_CACHE["ts"] < _OI_CG_TTL and _OI_CG_CACHE["data"]:
        return _OI_CG_CACHE["data"]
    result = {}
    try:
        data = _cg_get("https://api.coingecko.com/api/v3/derivatives", timeout=10)
        for item in data:
            sym = item.get("index_id")
            if not sym or item.get("contract_type") != "perpetual":
                continue
            oi = float(item.get("open_interest") or 0)
            if oi <= 0:
                continue
            is_binance = "Binance" in (item.get("market") or "")
            prev = result.get(sym)
            if prev is None or (is_binance and not prev["is_binance"]) or (is_binance == prev["is_binance"] and oi > prev["oi"]):
                result[sym] = {"oi": oi, "funding": float(item.get("funding_rate") or 0),
                               "price": float(item.get("price") or 0), "index": float(item.get("index") or 0),
                               "is_binance": is_binance}
        _OI_CG_CACHE["ts"] = now_ts
        _OI_CG_CACHE["data"] = result
    except Exception as e:
        log.error(f"[OI] coingecko derivatives fetch: {e}")
        return _OI_CG_CACHE["data"]
    return result

def _get_oi_usd(symbol: str) -> float:
    """Текущий Open Interest в USD для symbol (BTC, ETH, ...) через CoinGecko."""
    return _fetch_coingecko_oi_map().get(symbol, {}).get("oi", 0.0)

def _get_funding_pct(symbol: str) -> float:
    """Funding rate в % (как lastFundingRate*100 у Binance) через CoinGecko derivatives."""
    return _fetch_coingecko_oi_map().get(symbol, {}).get("funding", 0.0)

def get_open_interest(symbol: str) -> dict:
    """Open Interest через CoinGecko derivatives (замена fapi.binance.com, заблокированного на Railway)"""
    try:
        oi_now = _get_oi_usd(symbol)
        if oi_now <= 0:
            return {"oi": 0, "change": 0, "signal": "", "ok": False}
        oi_change = _get_oi_change(symbol)

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


def top_trades_long_status(entry: float, cur: float, tp1: float, tp2: float, tp3: float,
                            sl: float) -> tuple:
    """Статус карточки "Монеты в работе" (LONG) -- вынесено из cmd top_trades ради
    тестируемости, формула НЕ менялась (те же пороги, что были в исходном коде,
    только текст восстановлен + добавлен terminal_result). Возвращает (status_text,
    terminal_result|None) -- terminal_result != None означает, что позиция достигла
    финального уровня (TP3 целиком или SL) и должна уйти в архив."""
    dist = (entry - cur) / entry * 100 if cur < entry else 0
    if cur >= tp3:          return "✅ TP3 ДОСТИГНУТ!", "TP3"
    if cur >= tp2:          return "✅ TP2 достигнут", None
    if cur >= tp1:          return "✅ TP1 достигнут", None
    if cur > entry * 1.005: return "🟢 В плюсе", None
    if dist <= 1:           return "⚠️ Близко ко входу!", None
    if dist <= 2:           return f"🟡 Ждём вход, до входа {dist:.1f}%", None
    if cur <= sl * 1.01:    return "🔴 ПОД SL!", "SL"
    return f"🟡 Ждём вход, до входа {dist:.1f}%", None


def top_trades_short_status(entry: float, cur: float, tp1: float, tp2: float,
                             sl: float) -> tuple:
    """Зеркало top_trades_long_status() для SHORT (только tp1/tp2 -- у SHORT-записей
    в TOP_SHORT_SIGNALS нет tp3, тот же формат, что был в исходном коде)."""
    dist = (cur - entry) / entry * 100 if cur > entry else 0
    if cur <= tp2:          return "✅ TP2 ДОСТИГНУТ!", "TP2"
    if cur <= tp1:          return "✅ TP1 достигнут", None
    if cur < entry * 0.995: return "🟢 В плюсе", None
    if dist <= 1:           return "⚠️ Близко ко входу!", None
    if dist <= 2:           return f"🟡 Ждём вход, до входа {dist:.1f}%", None
    if cur >= sl * 0.99:    return "🔴 ПОД SL!", "SL"
    return f"🟡 Ждём вход, до входа {dist:.1f}%", None


WHALE_MONITOR_MIN_SCORE_FOR_DIRECTION = 40


def whale_monitor_label(direction: str, score_100: float) -> tuple:
    """Ярлык карточки Whale Monitor (АПГРЕЙД 11.07 Этап 2.3) -- при скоре <40 карточка
    раньше всё равно ставила "LONG ⭐"/"SHORT ⭐" (одна звезда минимум, см. round(x/20)
    и max(1,...) в исходном коде) -- то есть даже самый слабый сигнал выглядел как
    утверждённое направление со звездой. Теперь <40 -- честное 'НАБЛЮДЕНИЕ' без
    звёзд, LONG/SHORT со звёздами только от 40+. Возвращает (label, stars_str)."""
    if score_100 < WHALE_MONITOR_MIN_SCORE_FOR_DIRECTION:
        return "НАБЛЮДЕНИЕ", ""
    stars = "⭐" * max(1, min(5, round(score_100 / 20)))
    return direction, stars


def rsi_4h_zone_label(rsi_4h: float) -> str:
    """Зона RSI 4H с явным флагом (АПГРЕЙД 11.07 Этап 2.6) -- раньше карточка "Обзор"
    показывала голое число ('79.4') без статуса; >=75 -- тот же порог перекупленности,
    что уже используется в rocket score (bot.py rsi_4h>75)."""
    if rsi_4h >= 75:   return "🔴 ПЕРЕКУПЛЕННОСТЬ"
    if rsi_4h >= 55:   return "🟡 БЫЧИЙ"
    if rsi_4h >= 45:   return "⚪ НЕЙТРАЛЬНЫЙ"
    if rsi_4h >= 25:   return "🟠 МЕДВЕЖИЙ"
    return "🔵 ПЕРЕПРОДАННОСТЬ"

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
    DEPRECATED: базовая (не Binance/TA) версия скоринга, оставлена только ради
    существующих вызовов ниже по файлу — не используется /full (см. fa_engine.py) и
    не расширяется. Новая логика идёт в fa_engine.py / real_full_analysis().

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

    # R:R считаем от TP1 (не TP3) -- см. историю бага 3: карточка показывала "R:R 1:1.0"
    # рядом с TP1 всего +4%/SL -15%, т.к. rr тут был посчитан от TP3 (симметричного SL по
    # модулю 15%==15%), давая обманчивое впечатление о качестве СИМ ближайшего тейка.
    # TP1-базис — тот же конвеншен, что и в fa_engine/real_full_analysis (единый смысл
    # "R:R" по всему боту).
    rr = abs(tp1 - price) / abs(sl - price) if abs(sl - price) > 0 else 0

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

    # Ярлыки ниже -- НЕ структурная SMC-детекция (не BOS/OB/FVG/Sweep по свечам), а
    # пороги % изменения цены/объёма (см. вычисление smc_* выше в этой функции).
    # Названия отражают это честно (SMC_COVERAGE.md §3) -- реальная структурная
    # детекция живёт в ta_extra.py/pro_analysis(), сюда не переносилась (изменение
    # отображения, не формул/порогов).
    smc_factors = []
    if smc_bos_bull:    smc_factors.append("Имп. 7д↑/30д↓")
    if smc_bos_bear:    smc_factors.append("Имп. 7д↓/30д↑")
    if smc_ob_accum:    smc_factors.append("Штиль объёма")
    if smc_liq_sweep:   smc_factors.append("Резкий имп. 1ч")
    if smc_smart_accum: smc_factors.append("Откат в аптренде")
    if smc_smart_dist:  smc_factors.append("Сильный имп. 24ч")
    if smc_fvg_bull:    smc_factors.append("Имп. 1ч+24ч ↑")
    if smc_fvg_bear:    smc_factors.append("Имп. 1ч+24ч ↓")
    if tf_aligned_bull: smc_factors.append("TF Align Bull")
    if tf_aligned_bear: smc_factors.append("TF Align Bear")
    if fund_recovery:   smc_factors.append("Recovery ")
    if bb_squeeze:      smc_factors.append("BB Squeeze")
    if macd_bullish:    smc_factors.append("MACD Bull")
    if macd_bearish:    smc_factors.append("MACD Bear")
    if suspicious:      smc_factors.append(" Vol ")
    # Supply/Demand-зоны здесь недоступны (эта функция не использует real_ta()/TA-данные,
    # см. докстринг) -- в отличие от real_full_analysis(), где in_demand/in_supply реально
    # считаются. Раньше здесь были те же строки без определения переменных (NameError на
    # каждый вызов full_analysis() -- т.е. /coin, /signals, /top, /watchlist были сломаны).

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

    #  через CoinGecko (Binance заблокирован на Railway)
    candles = []
    try:
        raw = get_binance_ohlc(symbol, interval="4h", limit=200)
        if raw and len(raw) >= 10:
            candles = [
                {"time": datetime.fromtimestamp(c["timestamp"]/1000, tz=TZ),
                 "open": c["open"], "high": c["high"],
                 "low": c["low"], "close": c["close"], "vol": c["vol"]}
                for c in raw
            ]
    except Exception as e:
        log.error(f"Chart candle fetch {symbol}: {e}")

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
ATL_PCT_SANITY_LIMIT = 300  # см. ТЗ бага 2: |изм. от ATL| выше этого -- не показываем число,
                             # честное "н/д" вместо мусорного/нечитаемого множителя

def build_signal_text(symbol: str, a: dict,
                      stats_24h: dict = None,
                      atl: float = 0,
                      extras: dict = None) -> str:
    """
    Карточка сигнала (HTML, не Markdown -- см. историю бага "лейблы исчезают": это была
    НЕ проблема экранирования/парсинга markdown, а фактически утраченный кириллический
    текст в самих строковых литералах этого файла (широко распространено по bot.py,
    видимо старая порча кодировки задолго до этой сессии) -- лейблы здесь восстановлены
    заново. HTML choosen поверх этого как более устойчивый формат вперёд: экранируем
    html.escape() каждое динамическое значение, теги <b>/<i>/<code> вместо */_/`.
    """
    is_long = a["is_long"]
    price   = a["price"]
    tp1, tp2, tp3 = a["tp1"], a["tp2"], a["tp3"]
    sl, swing     = a["sl"],  a["swing"]
    rsi_4h = a["rsi_4h"]
    rr     = a["rr"]
    vol    = a["vol"]
    rocket = a.get("rocket", 50)
    rocket_label = html.escape(str(a.get("rocket_label", "")))
    sym_e = html.escape(symbol)

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

    # EMA-позиция (выше каких лежит цена)
    ema_pos = []
    if a.get("above_ema200"): ema_pos.append("EMA200")
    if a.get("above_ema50"):  ema_pos.append("EMA50")
    if a.get("above_ema20"):  ema_pos.append("EMA20")
    if not ema_pos:           ema_pos = ["ниже всех EMA"]
    ema_str = " | ".join(ema_pos)

    # RSI-иконка
    def rsi_icon(r):
        if r < 30: return "🟢"
        if r > 70: return "🔴"
        return "⚪"

    # SMC-факторы (без служебных BB Squeeze/MACD -- те уже показаны отдельно)
    raw_smc = [f for f in a.get("smc_factors", [])
               if "BB Squeeze" not in f and "MACD" not in f]
    smc_key = raw_smc[:3] if raw_smc else []

    macd_str = "бычий" if a.get("macd_bullish") else ("медвежий" if a.get("macd_bearish") else "нейтральный")
    st_str   = html.escape(str(a.get("st_label", "")))

    rsi_4h   = a["rsi_4h"]
    ch24h    = a["ch24h"]
    overbought = rsi_4h > 75
    oversold   = rsi_4h < 30
    suspicious = a.get("suspicious", False)

    if suspicious:
        conclusion = "⚠️ Подозрительный объём/капа — повышенный риск манипуляции"
    elif is_long and overbought and not oversold:
        conclusion = "⚠️ Перекуплено — вход рискован, дождись коррекции"
    elif is_long and rocket >= 75 and oversold:
        conclusion = "🚀 Сильный сигнал + перепроданность — хорошая точка входа"
    elif is_long and rocket >= 75:
        conclusion = "🚀 Сильный сигнал на покупку"
    elif is_long and rocket >= 60:
        conclusion = "📈 Умеренно бычий сигнал"
    elif not is_long and rocket >= 70:
        conclusion = "📉 Сильный сигнал на продажу"
    elif is_long and a.get("smc_smart_accum"):
        conclusion = "🧠 Похоже на накопление Smart Money"
    elif is_long and a.get("fund_recovery"):
        conclusion = "♻️ Признаки разворота после падения — рассмотри DCA"
    elif not is_long and ch24h < -10:
        conclusion = "📉 Сильное падение — жди стабилизации"
    else:
        conclusion = "⚪ Нейтральная картина, ждать более чёткого сигнала"

    lines = [
        f"<b>{sym_e}USDT</b>  {side_emoji} <b>{side_text}</b>",
        f"🕐 {now_utc3()}",
        "",
        f"🚀 <b>{rocket}/100</b> {rocket_label}  <code>{bar}</code>",
        f"📊 {ema_str}",
        f"{conclusion}",
        "",
        f"💰 Цена: <code>{fp(price)}</code>",
        f"🎯 TP1: <code>{fp(tp1)}</code>  ({pct(tp1)})",
        f"🎯 TP2: <code>{fp(tp2)}</code>  ({pct(tp2)})",
        f"🎯 TP3: <code>{fp(tp3)}</code>  ({pct(tp3)})",
        f"🛑 SL: <code>{fp(sl)}</code>  ({sl_pct()})",
        f"📐 {swing_lbl}: <code>{fp(swing)}</code>",
        "",
        f"⚖️ R:R <code>1:{rr:.1f}</code>  |  Объём <code>{vol_str}</code>  |  Rank <code>#{a.get('rank','')}</code>",
        f"📈 RSI 4H {rsi_icon(rsi_4h)}<code>{rsi_4h:.0f}</code>  |  MACD <code>{macd_str}</code>",
        f"📡 Supertrend: <code>{st_str}</code>",
    ]

    if smc_key:
        smc_str = html.escape(" · ".join(smc_key))
        lines.append(f"🧩 SMC: <code>{smc_str}</code>")

    if stats_24h:
        h24 = stats_24h.get("high", 0)
        l24 = stats_24h.get("low",  0)
        if h24 and l24:
            best = l24 * 1.005 if is_long else h24 * 0.995
            lines += [
                "",
                f"📊 24H: хай <code>{fp(h24)}</code>  лоу <code>{fp(l24)}</code>",
                f"   лучшая цена входа: <code>{fp(best)}</code>",
            ]

    if extras:
        fr = extras.get("funding", {})
        oi = extras.get("oi", {})
        if fr.get("ok") or oi.get("ok"):
            lines.append("")
        if fr.get("ok"):
            rate_str = f"{fr['rate']:+.4f}%"
            fr_signal = html.escape(str(fr.get("signal", "")))
            lines.append(f"💸 Funding: <code>{rate_str}</code>  {fr_signal}")
        if oi.get("ok") and oi.get("oi", 0) > 0:
            oi_ch = oi.get("change", 0)
            oi_str = f"{oi_ch:+.1f}% за 24ч"
            oi_signal = html.escape(str(oi.get("signal", "")))
            lines.append(f"📊 OI: <code>{oi_str}</code>  {oi_signal}")

    # Изменение от ATL -- см. историю бага 2: множитель от исторического минимума
    # (часто десятилетней давности) даёт огромные, бесполезные для трейдинга проценты;
    # ATL_PCT_SANITY_LIMIT честно скрывает такие значения вместо мусорного вывода.
    if atl and atl > 0:
        from_atl = (price - atl) / atl * 100
        if abs(from_atl) > ATL_PCT_SANITY_LIMIT:
            log.warning(f"build_signal_text {symbol}: изм. от ATL {from_atl:.0f}% "
                       f"вне разумных пределов (price={price}, atl={atl}) — скрыто, показано н/д")
            lines.append("📉 Изм. от ATL: н/д (величина вне разумных пределов)")
        else:
            lines.append(f"📉 Изм. от ATL: <code>+{from_atl:.0f}%</code>  (мин <code>{fp(atl)}</code>)")

    lines += ["", f"#{sym_e}USDT"]
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
        [InlineKeyboardButton("📊 Обзор рынка", callback_data="market_overview")],
        [InlineKeyboardButton("📈 Тренд анализ", callback_data="trend_analysis")],
        [InlineKeyboardButton("⭐️ ТОП СПОТ", callback_data="top_spot")],
        [InlineKeyboardButton("🟢 ТОП ЛОНГ", callback_data="top_long")],
        [InlineKeyboardButton("🔴 ТОП ШОРТ", callback_data="top_short")],
        [InlineKeyboardButton("🚀 x100 Сканер", callback_data="x100_scan")],
        [InlineKeyboardButton("🏦 Институционал", callback_data="institutional")],
        [InlineKeyboardButton("🐋 Whale Monitor", callback_data="whale_status")],
        [InlineKeyboardButton("⚡ Памп-радар", callback_data="pump_radar")],
        [InlineKeyboardButton("💼 Монеты в работе", callback_data="top_trades")],
        [InlineKeyboardButton("🔗 On-Chain", callback_data="onchain_info")],
        [InlineKeyboardButton("🔍 Полный анализ", callback_data="menu_full")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="show_menu")],
    ])
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
                emoji  = "🟢" if is_long else "🔴"
                slug   = coin.get("slug", sym.lower())
                sym_e  = html.escape(sym)
                text   = (f"🎯 <b>Цена вошла в зону входа!</b>\n"
                          f"🕐 {now_utc3()}\n\n"
                          f"{emoji} <b>{sym_e}USDT — {side}</b>\n"
                          f"💰 Цена: <code>{fp(price)}</code>\n"
                          f"🎯 TP1: <code>{fp(tp1)}</code>\n"
                          f"🛑 SL: <code>{fp(sl)}</code>\n"
                          f"⚖️ R:R: 1:{a['rr']:.1f}\n\n"
                          f"⚠️ Риск: не больше 2% депозита | ставь SL\n\n"
                          f"#{sym_e}USDT")
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("📊 TradingView", url=tv_link(sym)),
                    InlineKeyboardButton("CMC", url=cmc_link(slug)),
                ]])
                for cid in chat_ids:
                    try:
                        await bot.send_message(cid, text, parse_mode="HTML", reply_markup=kb)
                    except Exception as e:
                        log.error(f"Zone alert {cid}: {e}")
                add_to_game(sym, "zone", price)
                log.info(f"ZONE alert: {sym} {side} price={fp(price)}")

# 
# 
# 
def main_kb():
    """Главное меню BEST TRADE"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001f4ca Обзор рынка",    callback_data="market_overview"),
         InlineKeyboardButton("\U0001f4c8 Тренд анализ",   callback_data="trend_analysis")],
        [InlineKeyboardButton("\u2b50 ТОП СПОТ",           callback_data="top_spot"),
         InlineKeyboardButton("\U0001f7e2 ТОП ЛОНГ",       callback_data="top_long")],
        [InlineKeyboardButton("\U0001f534 ТОП ШОРТ",       callback_data="top_short"),
         InlineKeyboardButton("\U0001f680 x100 Сканер",    callback_data="x100_scan")],
        [InlineKeyboardButton("\U0001f4bc Монеты в работе",callback_data="top_trades")],
        [InlineKeyboardButton("\U0001f433 Whale Monitor",  callback_data="whale_status"),
         InlineKeyboardButton("\U0001f517 On-Chain",       callback_data="onchain_info")],
        [InlineKeyboardButton("\U0001f3e6 Институционал",  callback_data="institutional"),
         InlineKeyboardButton("⚡ Памп-радар",         callback_data="pump_radar")],
        [InlineKeyboardButton("\U0001f4cb Полный анализ",  callback_data="menu_full")],
    ])

def back_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🏠 Главное меню", callback_data="show_menu"),
    ]])

def nav_kb(refresh_data=None):
    """Нижняя навигация для всех разделов"""
    row = []
    if refresh_data:
        row.append(InlineKeyboardButton("🔄 Обновить", callback_data=refresh_data))
    row.append(InlineKeyboardButton("🏠 Меню", callback_data="show_menu"))
    return InlineKeyboardMarkup([row])

def _build_chart_v3_for_signal(symbol: str, a: dict):
    """Chart v4 (chart_v4.py) для ТОП ЛОНГ/ШОРТ/СПОТ и обычных карточек монеты: 2h/~120
    баров (свинг-сигнал, не памп/дамп), уровни из уже посчитанного a (real_full_analysis()-
    формат — entry1/2/3, sl, tp1/2/3, rr, is_long, zones). Только если в a реально есть
    уровни сделки (entry/SL/TP) — см. ТЗ "прикреплять... где есть entry/SL/TP". zones
    (найдены в a["zones"] -- find_sr_zones уже вызван внутри real_full_analysis(), без
    доп. API) даёт Chart v4 мульти-ТФ POI-прямоугольники; K-LVL-классификация зон
    делается тут же, на уже кэшированных 4h-свечах (get_binance_ohlc с тем же ключом,
    что и fa_engine — обычно cache hit). Chart v4 при исключении фоллбечится на Chart v3
    (та же сигнатура + уровни, без зон); None при любой проблеме на обоих — вызывающая
    сторона (send_coin) фоллбечится дальше на generate_signal_chart."""
    sl = a.get("sl")
    tp1 = a.get("tp1")
    entry1 = a.get("entry1", a.get("swing"))
    if not sl or not tp1 or not entry1:
        return None
    try:
        candles = get_binance_ohlc(symbol, "2h", 120)
        if not candles or len(candles) < 20:
            return None
        direction = "long" if a.get("is_long") else "short"
        # entry2/3 могут отсутствовать (full_analysis() даёт только один "swing"-уровень,
        # не настоящую DCA-зону) -- дублировать entry1 на все 3 уровня рисует 3 наложенные
        # друг на друга подписи "N лимитка" в одной точке; честнее показать один уровень.
        entry_levels = [lvl for lvl in (a.get("entry1", entry1), a.get("entry2"), a.get("entry3")) if lvl]
        rr = a.get("rr", a.get("rr_tp1"))
        try:
            zones = a.get("zones")
            candles_4h = get_binance_ohlc(symbol, "4h", 200) if zones else None
            chart = chart_v4.build_trade_chart_v4(
                symbol, candles, direction, entry_levels=entry_levels, sl=sl,
                tp1=tp1, tp2=a.get("tp2"), tp3=a.get("tp3"), rr=rr, tf_label="2h",
                zones=zones, candles_4h=candles_4h)
            if chart is not None:
                return chart
        except Exception as e:
            log.error(f"Chart v4 FAILED {symbol}: {type(e).__name__}: {e}, falling back to Chart v3")
        return chart_v3.build_trade_chart(
            symbol, candles, direction, entry_levels=entry_levels, sl=sl,
            tp1=tp1, tp2=a.get("tp2"), tp3=a.get("tp3"), rr=rr, tf_label="2h")
    except Exception as e:
        log.error(f"Chart v3 FAILED {symbol}: {type(e).__name__}: {e}")
        return None


async def send_coin(bot, chat_id, symbol, slug, a, text):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 TradingView",    url=tv_link(symbol)),
         InlineKeyboardButton("💹 CoinMarketCap",  url=cmc_link(slug))],
        [InlineKeyboardButton("🔄 Обновить анализ", callback_data=f"coin_{symbol}"),
         InlineKeyboardButton("🏠 Меню",            callback_data="show_menu")],
    ])

    #     Supertrend
    #
    if "Supertrend: <code></code>" in text:
        try:
            st_data = get_supertrend_signal(symbol)
            if st_data.get("label") and st_data["label"] != "":
                a["st_label"] = st_data["label"]
                text = text.replace("Supertrend: <code></code>",
                                    f"Supertrend: <code>{html.escape(st_data['label'])}</code>")
        except Exception as e:
            log.error(f"ST fetch {symbol}: {e}")

    stats_24h = get_binance_24h(symbol)
    chart = _build_chart_v3_for_signal(symbol, a)
    if chart is not None:
        log.info(f"Chart v3 OK: {symbol} {chart.getbuffer().nbytes} bytes")
    try:
        if chart is None:
            chart = generate_signal_chart(symbol, a, stats_24h)
        if chart is not None:
            log.info(f"Chart OK: {symbol} {chart.getbuffer().nbytes} bytes")
        else:
            log.info(f"Chart skipped (no real data): {symbol}")
    except Exception as e:
        log.error(f"Chart FAILED {symbol}: {type(e).__name__}: {e}")
        chart = None

    caption = text if len(text) <= 1024 else text[:1020].rsplit("\n", 1)[0] + "\n..."

    if chart is not None:
        try:
            chart.seek(0)
            await bot.send_photo(chat_id=chat_id, photo=chart,
                                 caption=caption, parse_mode="HTML",
                                 reply_markup=kb)
            log.info(f"send_photo OK: {symbol}")
            return
        except Exception as e:
            log.error(f"send_photo FAILED {symbol}: {type(e).__name__}: {e}")
            try:
                chart.seek(0)
                await bot.send_photo(chat_id=chat_id, photo=chart)
                await bot.send_message(chat_id, text, parse_mode="HTML",
                                       reply_markup=kb, disable_web_page_preview=True)
                return
            except Exception as e2:
                log.error(f"send_photo split FAILED {symbol}: {e2}")

    await bot.send_message(chat_id, text, parse_mode="HTML",
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
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    await subscribers.subscribe(cid)
    SEP = "━━━━━━━━━━━━━━━━━━━━"
    name = update.effective_user.first_name or "трейдер"
    await update.message.reply_text(
        f"👋 *Привет, {name}!*\n"
        f"🚀 *BEST TRADE {BOT_VERSION}* — твой крипто-аналитик\n"
        f"{SEP}\n\n"
        f"🧠 *Методология:*\n"
        f"  • SMC/ICT · Order Blocks · FVG · BOS\n"
        f"  • EMA 20/50/200 · RSI · MACD · Supertrend\n"
        f"  • Wyckoff · AMD · Power of Three\n"
        f"  • Multi-TF Confluence · Killzone\n\n"
        f"{SEP}\n\n"
        f"📡 *Источники данных:*\n"
        f"  • On-chain: Lookonchain\n"
        f"  • 🐋 Whale Monitor: Funding Rate + OI\n"
        f"  • CMC Топ-500 монет\n\n"
        f"{SEP}\n\n"
        f"⚡️ *Автосигналы каждые 30 минут*\n"
        f"⚠️ Риск: 1–2% депозита · SL обязателен\n\n"
        f"👇 *Выбери раздел:*",
        parse_mode="Markdown", reply_markup=main_kb()
    )

async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Отписка от автосигналов (send_scheduled) и алертов (check_alerts) -- см. subscribers.py."""
    cid = update.effective_chat.id
    await subscribers.unsubscribe(cid)
    await update.message.reply_text(
        "🔕 Отписка оформлена. Автосигналы и алерты больше не будут приходить в этот чат.\n"
        "Чтобы снова подписаться -- пришли /start.",
        parse_mode="Markdown"
    )

async def cmd_market(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Загружаю обзор рынка...")
    try:
        import datetime, math
        import requests as _r
        coins = get_all_coins()
        prices = get_btc_eth_price()
        gm = get_global_metrics()
        # ROADMAP: CoinGecko первичный источник для coins/prices (CMC — опциональный
        # фоллбек внутри get_all_coins()), поэтому реальный failure теперь означает, что
        # ОБА источника недоступны, а не просто "CMC-ключ мёртв" — 2026-07-10.
        if not coins:
            st_cg = _DATA_SOURCE_STATUS.get("coingecko_markets", {})
            st_cmc = _DATA_SOURCE_STATUS.get("cmc", {})
            failures = [
                f"CoinGecko /coins/markets: {st_cg.get('last_error') or 'нет данных'}",
                f"CMC /listings/latest (фоллбек): {st_cmc.get('last_error') or 'нет данных'}",
            ]
            text = "❌ Обзор рынка недоступен — упавшие источники:\n" + "\n".join(f"• {f}" for f in failures)
            await msg.edit_text(text)
            return

        btc=prices.get("BTC",{}); eth=prices.get("ETH",{})
        btc_price=btc.get("price",0) or 0
        btc_ch24=btc.get("ch24h",0) or 0
        eth_price=eth.get("price",0) or 0
        eth_ch24=eth.get("ch24h",0) or 0

        sq=next((c for c in coins if c["symbol"]=="SOL"),{})
        sol_price=sq.get("quote",{}).get("USDT",{}).get("price",0) or 0
        sol_ch24=sq.get("quote",{}).get("USDT",{}).get("percent_change_24h",0) or 0
        sol_ch7d=sq.get("quote",{}).get("USDT",{}).get("percent_change_7d",0) or 0

        bq=next((c for c in coins if c["symbol"]=="BTC"),{})
        bq_u=bq.get("quote",{}).get("USDT",{})
        btc_ch7d=bq_u.get("percent_change_7d",0) or 0
        btc_ch30=bq_u.get("percent_change_30d",0) or 0
        btc_vol24=bq_u.get("volume_24h",0) or 0

        eq=next((c for c in coins if c["symbol"]=="ETH"),{})
        eth_ch7d=eq.get("quote",{}).get("USDT",{}).get("percent_change_7d",0) or 0

        btc_dom=gm.get("btc_dominance",0) or 0
        eth_dom=gm.get("eth_dominance",0) or 0
        total_mcap=gm.get("total_mcap",0) or 0
        mcap_ch=gm.get("mcap_change_24h",0) or 0

        # === SENTIMENT === (единая формула с карточкой "Тренд" -- market_sentiment(), Этап 2.5)
        sentiment_label, pct = market_sentiment(coins)
        sentiment = market_sentiment_emoji(sentiment_label) + " " + sentiment_label

        # === FEAR & GREED ===
        fv=50; fl="Neutral"
        try:
            fg=_r.get("https://api.alternative.me/fng/?limit=1",timeout=5).json()
            fv=int(fg["data"][0]["value"]); fl=fg["data"][0]["value_classification"]
        except: pass
        if fv>=75: fg_em="🟢"; fg_z="ЖАДНОСТЬ"
        elif fv>=55: fg_em="🟡"; fg_z="УМ. ЖАДНОСТЬ"
        elif fv>=45: fg_em="⚪"; fg_z="НЕЙТРАЛЬНО"
        elif fv>=25: fg_em="🟠"; fg_z="СТРАХ"
        else: fg_em="🔴"; fg_z="КРАЙНИЙ СТРАХ"
        fg_bar="█"*(fv//10)+"░"*(10-fv//10)

        # === RSI BTC 1D через CoinGecko (Binance заблокирован на Railway) ===
        br=50.0
        try:
            kl=get_binance_ohlc("BTC","1d",16)
            br=calc_rsi([c["close"] for c in kl],14)
        except: pass
        if br>=70: rsi_z="🔴 ПЕРЕКУПЛЕН"
        elif br>=55: rsi_z="🟡 БЫЧИЙ"
        elif br>=45: rsi_z="⚪ НЕЙТРАЛЬНЫЙ"
        elif br>=30: rsi_z="🟠 МЕДВЕЖИЙ"
        else: rsi_z="🔴 ПЕРЕПРОДАН"

        # === RSI 4H ===
        br4=50.0
        try:
            kl4=get_binance_ohlc("BTC","4h",16)
            br4=calc_rsi([c["close"] for c in kl4],14)
        except: pass
        # Этап 2.6 (АПГРЕЙД 11.07): раньше показывалось голое число (напр. "79.4")
        # без статуса -- честный красный флаг перекупленности, см. rsi_4h_zone_label().
        rsi4_z = rsi_4h_zone_label(br4)

        # === EMA 50/200 trend ===
        ema_trend="N/A"
        try:
            kl_d=get_binance_ohlc("BTC","1d",210)
            closes=[c["close"] for c in kl_d]
            def ema(data,n):
                k2=2/(n+1); e=data[0]
                for p in data[1:]: e=p*k2+e*(1-k2)
                return e
            e50=ema(closes[-50:],50); e200=ema(closes,200)
            if e50>e200: ema_trend="🟢 EMA50 > EMA200 (ГОЛДЕН КРОСС)"
            else: ema_trend="🔴 EMA50 < EMA200 (ДЕАТКРОСС)"
        except: pass

        # === OI + Funding BTC ===
        oi_btc=0; fund_btc=0
        try:
            oi_btc=_get_oi_usd("BTC")/1e9
        except Exception as _e: log.error(f"OI BTC: {_e}")
        try:
            fund_btc=_get_funding_pct("BTC")
        except: pass
        if fund_btc>0.05: fund_z="🔴 ЛОНГИ ПЕРЕГРЕТЫ"
        elif fund_btc<-0.05: fund_z="🟢 ШОРТЫ ПЕРЕГРЕТЫ"
        else: fund_z="⚪ НОРМА"

        # === Liquidations 24h ===
        liq_long=0; liq_short=0
        try:
            ls_ratio=_get_ls_ratio("BTC")
        except: ls_ratio=1
        if ls_ratio>1.5: ls_z="🔴 Лонгов слишком много"
        elif ls_ratio<0.7: ls_z="🟢 Шортов слишком много"
        else: ls_z="⚪ Баланс"

        # === S&P500 context ===
        sp_ch=0
        _yq,_ = _fetch_yahoo_chart("%5EGSPC")
        if _yq and len(_yq)>=2: sp_ch=(_yq[-1]-_yq[-2])/_yq[-2]*100

        # === DXY ===
        dxy_ch=0
        _dc,_ = _fetch_yahoo_chart("DX-Y.NYB")
        if _dc and len(_dc)>=2: dxy_ch=(_dc[-1]-_dc[-2])/_dc[-2]*100

        # === Put/Call ratio Deribit ===
        pcr=0
        try:
            pcr_r=_r.get("https://www.deribit.com/api/v2/public/get_book_summary_by_currency",
                params={"currency":"BTC","kind":"option"},timeout=6).json()
            calls=sum(1 for x in pcr_r.get("result",[]) if x.get("instrument_name","").endswith("C"))
            puts=sum(1 for x in pcr_r.get("result",[]) if x.get("instrument_name","").endswith("P"))
            pcr=round(puts/calls,2) if calls>0 else 0
        except: pass
        if pcr>1.2: pcr_z="🔴 МЕДВЕЖИЙ (PCR>1.2)"
        elif pcr<0.7: pcr_z="🟢 БЫЧИЙ (PCR<0.7)"
        else: pcr_z="⚪ НЕЙТРАЛЬНЫЙ"

        # === BTC.D liquidity ===
        if btc_dom>55 and btc_ch24>1.5: liq="🔴 Капитал в BTC, альты под давлением"
        elif btc_dom>50 and btc_ch24<0: liq="🔴 BTC.D+BTC паника"
        elif btc_dom<50 and btc_ch24>0: liq="🟢 Альт-сезон формируется"
        else: liq="🟡 Консолидация"

        # === Market phase ===
        if btc_ch7d>5 and btc_ch30>10: phase="📈 АПТРЕНД"
        elif btc_ch7d<-5 and btc_ch30<-10: phase="📉 ДАУНТРЕНД"
        elif abs(btc_ch7d)<3: phase="↔ БОКОВИК / АККУМУЛЯЦИЯ"
        else: phase="🔄 КОРРЕКЦИЯ"

        # === ICT Killzone ===
        now_h=(datetime.datetime.utcnow().hour+3)%24
        if 2<=now_h<10: kz="🌙 Азия (02-10) — низкая волатильность"
        elif 10<=now_h<18: kz="🇬🇧 Лондон (10-18) — задаёт направление"
        elif 15<=now_h<23: kz="🇺🇸 Нью-Йорк (15-23) — макс. волатильность"
        else: kz="🌃 Ночная сессия"

        # === Top 24h / 7d ===
        s24=sorted(coins[:100],key=lambda c:c["quote"]["USDT"].get("percent_change_24h",0) or 0,reverse=True)
        def fc(v): return ("+"+str(round(v,1)) if v>=0 else str(round(v,1)))+"%"
        def fp(v): return ("+"+str(round(v,2)) if v>=0 else str(round(v,2)))+"%"
        def fe(v): return "🟢" if v>=2 else ("🔴" if v<=-2 else "🟡")
        ll=[]; sl=[]
        for c in s24[:5]:
            sym=c["symbol"]; ch=c["quote"]["USDT"].get("percent_change_24h",0) or 0
            v=c["quote"]["USDT"].get("volume_24h",0) or 0
            if v>=2000000: ll.append("  🟢 "+sym+" "+fc(ch))
        for c in s24[-5:]:
            sym=c["symbol"]; ch=c["quote"]["USDT"].get("percent_change_24h",0) or 0
            v=c["quote"]["USDT"].get("volume_24h",0) or 0
            if v>=2000000: sl.append("  🔴 "+sym+" "+fc(ch))
        s7d=sorted(coins[:100],key=lambda c:c["quote"]["USDT"].get("percent_change_7d",0) or 0,reverse=True)
        ll7=[]; sl7=[]
        for c in s7d[:3]:
            sym=c["symbol"]; ch=c["quote"]["USDT"].get("percent_change_7d",0) or 0
            v=c["quote"]["USDT"].get("volume_24h",0) or 0
            if v>=2000000: ll7.append("  🟢 "+sym+" "+fc(ch))
        for c in s7d[-3:]:
            sym=c["symbol"]; ch=c["quote"]["USDT"].get("percent_change_7d",0) or 0
            v=c["quote"]["USDT"].get("volume_24h",0) or 0
            if v>=2000000: sl7.append("  🔴 "+sym+" "+fc(ch))

        # === VERDICT SCORE — блоки 2/6/7: факторы с ✅/❌/🟡 + скор/100 + грейд ===
        score=0
        factors=[]  # (mark, text)
        if btc_ch24>2: score+=2; factors.append(("✅","BTC растёт сильно (24ч)"))
        elif btc_ch24>0: score+=1; factors.append(("🟡","BTC растёт слабо (24ч)"))
        elif btc_ch24<-2: score-=2; factors.append(("❌","BTC падает сильно (24ч)"))
        else: factors.append(("🟡","BTC около нуля (24ч)"))
        if fv>=55: score+=1; factors.append(("✅","Fear&Greed в зоне жадности"))
        elif fv<35: score-=1; factors.append(("❌","Fear&Greed в зоне страха"))
        else: factors.append(("🟡","Fear&Greed нейтрален"))
        if 55<=br<70: score+=1; factors.append(("✅","RSI 1D в здоровой бычьей зоне"))
        elif br>=70 or br<35: score-=1; factors.append(("❌","RSI 1D перекуплен/перепродан"))
        else: factors.append(("🟡","RSI 1D нейтрален"))
        if btc_dom<50 and btc_ch24>0: score+=2; factors.append(("✅","BTC.D<50% + рост — капитал идёт в альты"))
        else: factors.append(("🟡","BTC.D не даёт альт-сезон сигнала"))
        if fund_btc>0.05: score-=1; factors.append(("❌","Funding перегрет — лонги переполнены"))
        else: factors.append(("✅","Funding в норме"))
        if sp_ch>0: score+=1; factors.append(("✅","S&P500 растёт — риск-аппетит жив"))
        elif sp_ch<-1: score-=1; factors.append(("❌","S&P500 падает — риск-офф"))
        else: factors.append(("🟡","S&P500 нейтрален"))
        if pcr>1.2: score-=1; factors.append(("❌","Put/Call>1.2 — опционный рынок медвежий"))
        elif pcr<0.7: score+=1; factors.append(("✅","Put/Call<0.7 — опционный рынок бычий"))
        else: factors.append(("🟡","Put/Call нейтрален"))

        SCORE_MIN, SCORE_MAX = -7, 8
        score_100 = max(0, min(100, round((score - SCORE_MIN) / (SCORE_MAX - SCORE_MIN) * 100)))
        if score_100>=85: grade="A+"; verdict_e="🚀"; verdict_word="СИЛЬНЫЙ БЫЧИЙ"
        elif score_100>=65: grade="A"; verdict_e="📈"; verdict_word="БЫЧИЙ"
        elif score_100>=40: grade="B"; verdict_e="🟡"; verdict_word="НЕЙТРАЛЬНО-БЫЧИЙ"
        elif score_100>=20: grade="C"; verdict_e="⚪"; verdict_word="НЕЙТРАЛЬНЫЙ"
        else: grade="C"; verdict_e="📉"; verdict_word="МЕДВЕЖИЙ"
        verdict=f"{grade} {verdict_e} {verdict_word.title()}"

        SEP="➖"*18
        mcap_str="$"+str(round(total_mcap/1e12,2))+"T" if total_mcap>0 else "N/A"
        oi_str=str(round(oi_btc,1))+"B" if oi_btc>0 else "N/A"
        out=[
            # === Блок 1: шапка + время UTC+3 ===
            "⭐ BEST TRADE — ОБЗОР РЫНКА",
            f"🕐 _{now_utc3()}_",
            SEP,"",
            # === Блок 2: вердикт ===
            f"_{verdict_e} {verdict_word.title()}  |  Скор: {score_100}/100  |  Качество: {grade}_",
            "",
            SEP,"",
            # === Блок 3: цена и контекст ===
            "💰 КАПИТАЛИЗАЦИЯ",
            "  Общая: "+mcap_str+"  "+fp(mcap_ch),
            "  BTC.D: "+str(round(btc_dom,1))+"%   ETH.D: "+str(round(eth_dom,1))+"%","",
            "📊 ЦЕНЫ",
            "  BTC  $"+f"{round(btc_price,0):,.0f}"+"  "+fe(btc_ch24)+" "+fp(btc_ch24)+"  7d: "+fp(btc_ch7d),
            "  ETH  $"+f"{round(eth_price,2):,.2f}"+"  "+fe(eth_ch24)+" "+fp(eth_ch24)+"  7d: "+fp(eth_ch7d),
            "  SOL  $"+f"{round(sol_price,2):,.2f}"+"  "+fe(sol_ch24)+" "+fp(sol_ch24)+"  7d: "+fp(sol_ch7d),"",
            "🧠 ИНДИКАТОРЫ",
            "  RSI 1D: "+str(round(br,1))+"  "+rsi_z,
            "  RSI 4H: "+str(round(br4,1))+"  "+rsi4_z,
            "  "+ema_trend,
            "  Fear&Greed: "+str(fv)+"/100  "+fg_em+" "+fg_z,
            "  ["+fg_bar+"]",
            "  Сентимент: "+sentiment+" ("+str(int(round(pct,0)))+"% растут)","",
            "🏛 ИНСТИТУЦИОНАЛЫ",
            "  OI BTC: $"+oi_str,
            "  Funding: "+str(round(fund_btc,4))+"% — "+fund_z,
            "  L/S Ratio: "+str(round(ls_ratio,2))+" — "+ls_z,
            "  Put/Call: "+str(pcr)+" — "+pcr_z,
            "  S&P500: "+fp(sp_ch)+"  DXY: "+fp(dxy_ch),"",
            "🌊 ЛИКВИДНОСТЬ",
            "  BTC.D "+str(round(btc_dom,1))+"% — "+liq,"",
            "📈 ФАЗА РЫНКА","  "+phase,"",
            "⏰ ICT KILLZONE","  "+kz,"",
            "🔥 ТОП 24H","  Рост:"
        ]+ll+["  Падение:"]+sl+["",
            "📅 ТОП 7D","  Рост:"
        ]+ll7+["  Падение:"]+sl7+["",
            SEP,"",
            # === Блок 6: факторы ✅/❌/🟡 ===
            "📋 *Факторы:*",""
        ]+[f"  {mark}  {text}" for mark,text in factors]+["",
            SEP,"",
            # === Блок 7: расшифровка скора ===
            f"📋 *РАСШИФРОВКА СКОРА  {score_100}/100*","",
            "  Шкала силы:",
            "  0–19   📉  МЕДВЕЖИЙ",
            "  20–39  ⚪  НЕЙТРАЛЬНЫЙ",
            "  40–64  🟡  НЕЙТРАЛЬНО-БЫЧИЙ",
            "  65–84  📈  БЫЧИЙ",
            "  85–100 🚀  СИЛЬНЫЙ БЫЧИЙ","",
            "  Грейды: A+ ≥85 · A ≥65 · B ≥40 · C <40","",
            SEP,"🏆 ИТОГОВЫЙ ВЕРДИКТ: "+verdict,SEP
        ]
        await msg.edit_text("\n".join(out),parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Обновить",callback_data="market_overview"),
                InlineKeyboardButton("🏠 Меню",callback_data="show_menu")
            ],[
                InlineKeyboardButton("📈 Тренд",callback_data="trend_analysis"),
                InlineKeyboardButton("🏛 Институционал",callback_data="institutional")
            ]]),disable_web_page_preview=True)
    except Exception as e:
        import traceback
        log.error(f"cmd_market: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        await msg.edit_text(
            f"❌ Обзор рынка: {type(e).__name__}: {str(e)[:200]}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Меню",callback_data="show_menu")]]))

_FA_ENGINE_COIN_CACHE: dict = {}          # {symbol: (ts, result)} -- см. _get_fa_engine_result_cached
_FA_ENGINE_COIN_CACHE_TTL = 480            # 8 минут -- повторный /coin того же символа не жжёт лимит снова

async def _get_fa_engine_result_cached(symbol: str, coin: dict = None, timeout: float = 12.0):
    """Best-effort fa_engine.build_full_analysis() для /coin -- НЕ обязательное условие
    ответа команды: /coin и без него работает на legacy full_analysis()/build_signal_text
    (как до этого изменения). Даёт Chart v4 реальные зоны/структуру и данные для блока
    "Разбор" (narrative.py), если fa_engine успел уложиться в timeout.

    Жёсткий таймаут через asyncio.wait_for: при рейт-лимите CoinGecko fa_engine может
    подвиснуть/сильно затормозить -- ручная команда должна отвечать быстро всегда, при
    таймауте просто возвращаем None (карточка идёт как раньше, без зон/Разбора), не ждём
    и не роняем /coin.

    Кэш на _FA_ENGINE_COIN_CACHE_TTL секунд по символу (в памяти процесса, без TTL-обхода
    файлов/Redis -- то же, что и остальные in-memory кэши бота) -- иначе повторные /coin
    по одному и тому же символу подряд бьют по и так уже ограниченному CoinGecko-бюджету
    без всякой пользы (данные за 8 минут не успевают значимо измениться)."""
    now = time.time()
    cached = _FA_ENGINE_COIN_CACHE.get(symbol)
    if cached and now - cached[0] < _FA_ENGINE_COIN_CACHE_TTL:
        return cached[1]
    try:
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, fa_engine.build_full_analysis, symbol, coin),
            timeout=timeout)
    except asyncio.TimeoutError:
        log.info(f"fa_engine (/coin best-effort) timeout for {symbol}, using legacy card only")
        return None
    except Exception as e:
        log.error(f"fa_engine (/coin best-effort) failed for {symbol}: {type(e).__name__}: {e}")
        return None
    if result and result.get("ok"):
        _FA_ENGINE_COIN_CACHE[symbol] = (now, result)
        return result
    return None


async def cmd_coin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/coin (и /2) -- по-тикерная карточка. ЕДИНЫЙ ИСТОЧНИК с /full: fa_engine (см.
    _render_fa_result) -- раньше эта команда строила карточку из старого full_analysis()
    (фиксированные TP +4/+8/+15%, SL -15%, R:R всегда ~1:0.27, БЕЗ гейта), из-за чего
    карточка могла показать LONG с R:R 1:0.3, пока Chart v4/Разбор (уже на fa_engine)
    показывали SHORT R:R 1:1.6 для той же монеты -- см. историю бага. fa_engine вызывается
    best-effort с жёстким таймаутом + 8-мин кэшем (_get_fa_engine_result_cached) --
    ручная команда должна отвечать быстро всегда; при таймауте/недоступности -- честное
    сообщение, без отката на старые фиксированные проценты (см. bug 1: "нет данных !=
    придуманное значение")."""
    if not ctx.args:
        await update.message.reply_text(": `/2 BTC`", parse_mode="Markdown")
        return
    symbol = ctx.args[0].upper().replace("USDT", "").replace("BUSD", "")
    msg    = await update.message.reply_text(f"🔍 Анализирую *{symbol}USDT*...", parse_mode="Markdown")
    coins  = get_top500()
    coin   = next((c for c in coins if c["symbol"] == symbol), None)
    if not coin:
        await msg.edit_text(f"❌ {symbol} не найдена в топ-500")
        return
    try:
        await msg.delete()
    except Exception:
        pass

    fa_result = await _get_fa_engine_result_cached(symbol, coin)
    if not fa_result or not fa_result.get("ok"):
        await ctx.bot.send_message(
            update.effective_chat.id,
            f"⚠️ *{symbol}USDT*: не удалось построить структурный анализ "
            f"(таймаут или лимит API). Попробуй `/full {symbol}` или повтори позже.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Меню", callback_data="show_menu")
            ]]))
        return

    await _render_fa_result(ctx.bot, update.effective_chat.id, symbol, fa_result)

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
        ctx.user_data.pop("awaiting_full_symbol", None)
        SEP = "━━━━━━━━━━━━━━━━━━━━"
        await q.edit_message_text(
            f"🚀 *BEST TRADE {BOT_VERSION}*\n"
            f"_{now_utc3()}_\n"
            f"{SEP}\n\n"
            f"🧠 SMC · ICT · Wyckoff · AMD · Multi-TF\n"
            f"📡 On-chain · 🐋 Whale Monitor\n\n"
            f"👇 *Выбери раздел:*",
            parse_mode="Markdown", reply_markup=main_kb()
        )

    elif data == "top_spot":
        await q.edit_message_text("\U0001f504 Загружаю ТОП СПОТ...", parse_mode="Markdown")
        class FakeUpdate:
            class effective_chat:
                id = q.message.chat_id
            message = q.message
        await cmd_top_spot(FakeUpdate(), ctx)

    elif data == "top_long":
        try: await q.message.delete()
        except: pass
        msg_sent = await ctx.bot.send_message(q.message.chat_id, "\U0001f504 Загружаю ТОП ЛОНГ... ~40 сек")
        class FakeMsgLong:
            chat_id = q.message.chat_id
            async def reply_text(self, text, **kw):
                return await msg_sent.edit_text(text, **kw)
            async def edit_text(self, text, **kw):
                return await msg_sent.edit_text(text, **kw)
        class FakeUpdateLong:
            class effective_chat:
                id = q.message.chat_id
            message = FakeMsgLong()
        await cmd_top_long(FakeUpdateLong(), ctx)

    elif data == "top_short":
        await q.edit_message_text("\U0001f504 Загружаю ТОП ШОРТ...", parse_mode="Markdown")
        class FakeUpdate:
            class effective_chat:
                id = q.message.chat_id
            message = q.message
        await cmd_top_short(FakeUpdate(), ctx)

    elif data == "x100_scan":
        try: await q.message.delete()
        except: pass
        msg_x = await ctx.bot.send_message(q.message.chat_id, "🚀 Запускаю x100 сканер... ~15 сек")
        class FakeX100:
            class effective_chat:
                id = q.message.chat_id
            class message:
                chat_id = q.message.chat_id
                @staticmethod
                async def reply_text(text, **kw):
                    try: return await msg_x.edit_text(text, **kw)
                    except: return await ctx.bot.send_message(q.message.chat_id, text, **kw)
        await cmd_x100_scanner(FakeX100(), ctx)

    elif data == "menu_full":
        ctx.user_data["awaiting_full_symbol"] = True
        await q.edit_message_text(
            "🔍 *Полный анализ — 13 блоков*\n\n"
            "Напиши тикер монеты (например `BTC`, `ETH`, `SOL`) следующим сообщением,"
            " или используй команду `/full SYMBOL`.\n\n"
            "📋 *Блоки анализа:*\n"
            "Multi-TF bias · Elliott Wave · SMC-сетап (BOS/CHoCH/range) · POI · "
            "Чеклист K-LVL/ICT · Ликвидность/ловушки · OI/Funding/L-S · Killzone · "
            "Фаза рынка · Мемкоин-фильтр · План сделки · Rocket Score · Вердикт",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Меню", callback_data="show_menu")],
            ])
        )

    elif data in ("game", "top_trades"):
        nav = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Обновить",     callback_data="top_trades"),
             InlineKeyboardButton("🏠 Меню", callback_data="show_menu")],
        ])

        lines = [f"💼 *BEST TRADE — МОНЕТЫ В РАБОТЕ*", f"🕐 {now_utc3()}", ""]
        has_signals = False
        total = len(TOP_LONG_SIGNALS) + len(TOP_SHORT_SIGNALS) + len(TOP_SPOT_SIGNALS)

        if total > 0:
            lines[2] = f"📊 *Всего сигналов: {total}*\n"

        # ── LONG в работе ──
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
                t     = v["time"].strftime("%d.%m %H:%M") if v.get("time") else "—"
                tv    = tv_link(sym)

                # Найдено ночной сессией: раньше при пробое SL/TP3 запись НИКОГДА не
                # переходила в архив сама -- status="done" ставился только вручную
                # кнопкой (tp_/sl_ callback ниже по файлу). Теперь терминальный
                # уровень (TP3 полностью или SL) сразу переводит запись в архив --
                # формула статуса та же, что была, см. top_trades_long_status().
                status, terminal_result = top_trades_long_status(entry, cur, tp1, tp2, tp3, sl)

                if terminal_result:
                    v["status"] = "done"
                    v["result"] = terminal_result
                    continue  # ушла в архив -- покажет блок "Закрытые" ниже

                source = v.get("note") or signal_journal.get_latest_source(sym, "long") or "источник неизвестен"

                lines += [
                    f"🟢 [{sym}USDT]({tv}) — LONG",
                    f"  Вход: `{fp(entry)}`   Текущая: `{fp(cur)}`   {move:+.1f}%",
                    f"  TP1 `{fp(tp1)}`  TP2 `{fp(tp2)}`  TP3 `{fp(tp3)}`  SL `{fp(sl)}`",
                    f"  Статус: {status}",
                    f"  Источник: {source}",
                    f"  Вход в позицию: {t} UTC+3",
                    "",
                ]

        # ── SHORT в работе ──
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
                t     = v["time"].strftime("%d.%m %H:%M") if v.get("time") else "—"
                tv    = tv_link(sym)

                # Тот же принцип авто-архивации, что и у LONG выше — см. top_trades_short_status().
                status, terminal_result = top_trades_short_status(entry, cur, tp1, tp2, sl)

                if terminal_result:
                    v["status"] = "done"
                    v["result"] = terminal_result
                    continue

                source = v.get("note") or signal_journal.get_latest_source(sym, "short") or "источник неизвестен"

                lines += [
                    f"🔴 [{sym}USDT]({tv}) — SHORT",
                    f"  Вход: `{fp(entry)}`   Текущая: `{fp(cur)}`   {move:+.1f}%",
                    f"  TP1 `{fp(tp1)}`  TP2 `{fp(tp2)}`  SL `{fp(sl)}`",
                    f"  Статус: {status}",
                    f"  Источник: {source}",
                    f"  Вход в позицию: {t} UTC+3",
                    "",
                ]

        # ── SPOT (DCA) в работе ──
        if TOP_SPOT_SIGNALS:
            has_signals = True
            for sym, v in TOP_SPOT_SIGNALS.items():
                tv     = tv_link(sym)
                t      = v["time"].strftime("%d.%m %H:%M") if v.get("time") else "—"
                buy_lo = v.get("buy_zone_lo", v["entry"])
                buy_hi = v.get("buy_zone_hi", v["entry"])
                sell_t = v.get("sell_target", 0)
                source = v.get("note") or signal_journal.get_latest_source(sym, "long") or "источник неизвестен"
                lines += [
                    f"🟡 [{sym}USDT]({tv}) — SPOT DCA",
                    f"  Зона покупки: `{fp(buy_lo)}`–`{fp(buy_hi)}`",
                    f"  Цель продажи: `{fp(sell_t)}`",
                    f"  Источник: {source}",
                    f"  Вход в позицию: {t} UTC+3",
                    "",
                ]

        # ── Закрытые (архив) -- честно: раньше показывал ВСЕ записи (баг "if True"
        # вместо фильтра по статусу), сейчас только реально закрытые (status=="done"),
        # последние 5 по каждому направлению.
        done_l = {s: v for s, v in TOP_LONG_SIGNALS.items()  if v.get("status") == "done"}
        done_s = {s: v for s, v in TOP_SHORT_SIGNALS.items() if v.get("status") == "done"}
        if done_l or done_s:
            lines.append("📁 *Закрытые (последние):*")
            for sym, v in list(done_l.items())[-5:]:
                tv = tv_link(sym)
                t  = v["time"].strftime("%d.%m %H:%M") if v.get("time") else "—"
                result = v.get("result", "?")
                lines.append(f"  [{sym}USDT]({tv}) — {result}, вход {t} UTC+3")
            for sym, v in list(done_s.items())[-5:]:
                tv = tv_link(sym)
                t  = v["time"].strftime("%d.%m %H:%M") if v.get("time") else "—"
                result = v.get("result", "?")
                lines.append(f"  [{sym}USDT]({tv}) — {result}, вход {t} UTC+3")

        if not has_signals:
            lines += [
                "📭 *Сейчас нет активных сигналов*\n",
                "Сканирование рынка идёт каждые 30 минут.",
                "Загляни позже:",
                "как только появится качественный сетап — увидишь его здесь.",
            ]

        try:
            await q.edit_message_text(
                "\n".join(lines), parse_mode="Markdown",
                reply_markup=nav, disable_web_page_preview=False
            )
        except Exception as e:
            log.error(f"top_trades: {e}")
            await q.answer("Ошибка загрузки монет в работе")

    elif len(data.split("_")) == 3 and data.split("_")[0] in ("tp", "sl"):
        parts = data.split("_")
        action = parts[0]   # tp / sl
        mode   = parts[1]   # long / short
        sym    = parts[2]
        result = "TP" if action == "tp" else "SL"
        store  = TOP_LONG_SIGNALS if mode == "long" else TOP_SHORT_SIGNALS
        if sym in store:
            store[sym]["status"] = "done"
            store[sym]["result"] = result
        await q.answer(f"Отмечено: {result} по {sym}USDT")
        await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(f"{'✅' if action=='tp' else '❌'} {result} — {sym}", callback_data="noop"),
            InlineKeyboardButton("🏠 Меню", callback_data="show_menu"),
        ]]))
    elif data.startswith("full_"):
        symbol = data[5:]
        await q.edit_message_text(f"   *{symbol}*...", parse_mode="Markdown")
        try: await q.message.delete()
        except: pass
        await _do_full_analysis(ctx.bot, q.message.chat_id, symbol)

    elif data.startswith("sigloop_watch_"):
        # Слежение уже идёт автоматически с момента отправки алерта (см.
        # signal_loop.run_exit_tracker) -- кнопка просто подтверждает это владельцу,
        # отдельного watch-списка не заводим (не дублируем состояние).
        symbol = data[len("sigloop_watch_"):]
        await q.answer(f"Слежу за {symbol}USDT — оповещу о входе/TP/SL/развороте", show_alert=True)

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
        # ЕДИНЫЙ ИСТОЧНИК с /coin и /full (см. _render_fa_result) -- раньше эта кнопка
        # ("🔄 Обновить анализ") строила карточку из старого full_analysis() (фиксированные
        # TP +4/+8/+15%, SL -15%, без R:R-гейта), что могло разойтись с fa_engine-графиком/
        # Разбором для той же монеты. Теперь оба входа (команда и кнопка) идут через
        # fa_engine best-effort с таймаутом+кэшем.
        symbol = data[5:]; cid = q.message.chat_id
        await q.edit_message_text(f"🔍 Анализирую {symbol}USDT...")
        coins = get_top500()
        coin  = next((c for c in coins if c["symbol"] == symbol), None)
        if not coin:
            await q.edit_message_text(f"❌ {symbol} не найдена"); return
        try: await q.message.delete()
        except: pass
        fa_result = await _get_fa_engine_result_cached(symbol, coin)
        if not fa_result or not fa_result.get("ok"):
            await ctx.bot.send_message(cid,
                f"⚠️ *{symbol}USDT*: не удалось построить структурный анализ "
                f"(таймаут или лимит API). Попробуй `/full {symbol}` или повтори позже.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏠 Меню", callback_data="show_menu")]]))
            return
        await _render_fa_result(ctx.bot, cid, symbol, fa_result)

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
            [InlineKeyboardButton("🔄 Обновить", callback_data="onchain_info"),
             InlineKeyboardButton("🏠 Меню",     callback_data="show_menu")],
        ])
        try:
            await q.edit_message_text(
                "🔗 *On-Chain*\n\n"
                "🚧 Раздел в разработке — реального источника данных "
                "(ETF netflow, whale-трекинг) пока нет, показывать заглушку "
                "вместо цифр не будем.\n\n"
                f"🕐 {now_utc3()}",
                parse_mode="Markdown", reply_markup=nav
            )
        except Exception as e:
            if "not modified" in str(e).lower():
                await q.answer("Без изменений")

    elif data == "trend_analysis":
        await q.edit_message_text("\U0001f4ca Загружаю рыночные данные...", parse_mode="Markdown")
        try:
            import requests as _r

            # ROADMAP (решение владельца, 2026-07-10): CMC перестал быть критическим
            # источником -- эта карточка теперь читает BTC/ETH/SOL/топ-альты из
            # get_all_coins() (CoinGecko первичный, CMC опциональный фоллбек внутри неё),
            # вместо отдельных прямых CMC-запросов. Раньше карточка падала в "Данные
            # временно недоступны" при мёртвом CMC-ключе, даже если CoinGecko был жив --
            # реальной деградации не было, просто ненужная зависимость от CMC.
            coins = get_all_coins()

            if not coins:
                nav_degraded = InlineKeyboardMarkup([
                    [InlineKeyboardButton("\U0001f504 Повторить", callback_data="trend_analysis"),
                     InlineKeyboardButton("\U0001f3e0 Меню",      callback_data="show_menu")],
                ])
                st_cg = _DATA_SOURCE_STATUS.get("coingecko_markets", {})
                await q.edit_message_text(
                    "⚠️ *Данные временно недоступны*\n\n"
                    f"CoinGecko: {st_cg.get('last_error') or 'нет данных'}\n"
                    "Карточка рыночного тренда не строится без списка монет -- "
                    "показывать вместо неё нули было бы неверно.",
                    parse_mode="Markdown", reply_markup=nav_degraded
                )
                return

            def _q(sym):
                c = next((x for x in coins if x["symbol"] == sym), None)
                return c.get("quote", {}).get("USDT", {}) if c else {}

            btc_q, eth_q, sol_q = _q("BTC"), _q("ETH"), _q("SOL")

            btc_p   = btc_q.get("price", 0) or 0
            btc_ch  = btc_q.get("percent_change_24h", 0) or 0
            btc_7d  = btc_q.get("percent_change_7d", 0) or 0
            btc_30d = btc_q.get("percent_change_30d", 0) or 0
            btc_dom = 0

            eth_p   = eth_q.get("price", 0) or 0
            eth_ch  = eth_q.get("percent_change_24h", 0) or 0
            eth_7d  = eth_q.get("percent_change_7d", 0) or 0

            sol_p   = sol_q.get("price", 0) or 0
            sol_ch  = sol_q.get("percent_change_24h", 0) or 0

            # --- Доминация BTC/ETH — единый источник get_global_metrics() (как /market и Институционал) ---
            try:
                gm = get_global_metrics()
                btc_dom = round(gm.get("btc_dominance", 0) or 0, 1)
                eth_dom = round(gm.get("eth_dominance", 0) or 0, 1)
                total_mcap = gm.get("total_mcap", 0) or 0
            except:
                eth_dom = 0; total_mcap = 0

            # --- Fear & Greed ---
            fg_val, fg_label = 50, "Нейтральный"
            try:
                fg = _r.get("https://api.alternative.me/fng/?limit=1", timeout=5).json()
                fg_val = int(fg["data"][0]["value"])
                fg_label = fg["data"][0]["value_classification"]
            except:
                pass

            # --- Топ альты — из уже загруженного coins (CoinGecko), топ-100 по капе ---
            gainers = []
            losers  = []
            alt_coins_full = [c for c in coins[:100] if c["symbol"] not in STABLECOINS]
            for c in alt_coins_full:
                q_c = c.get("quote", {}).get("USDT", {})
                ch  = q_c.get("percent_change_24h", 0) or 0
                ch7 = q_c.get("percent_change_7d", 0) or 0
                p   = q_c.get("price", 0) or 0
                s   = c["symbol"]
                gainers.append((s, p, ch, ch7))
                losers.append((s, p, ch, ch7))
            # Единая формула с карточкой "Обзор" -- market_sentiment(), Этап 2.5
            # (раньше здесь был свой bull_pct, случайно совпадавший порогами с
            # Обзором, но так и не гарантированно единый источник).
            sentiment_t, bull_pct = market_sentiment(coins)

            gainers.sort(key=lambda x: x[2], reverse=True)
            gainers = gainers[:10]
            losers.sort(key=lambda x: x[2])
            losers = losers[:10]

            # --- Определяем тренды ---
            if btc_ch > 2 and btc_7d > 0:
                btc_trend = "\U0001f7e2 БЫЧИЙ"
                btc_bias  = "\u2705 Лонги в приоритете"
            elif btc_ch < -2 and btc_7d < 0:
                btc_trend = "\U0001f534 МЕДВЕЖИЙ"
                btc_bias  = "\u274c Шорты или кэш"
            else:
                btc_trend = "\U0001f7e1 НЕЙТРАЛЬНЫЙ"
                btc_bias  = "\u26a0\ufe0f Избирательные сетапы"

            if eth_ch > 1.5:
                eth_trend = "\U0001f7e2 БЫЧИЙ"
            elif eth_ch < -1.5:
                eth_trend = "\U0001f534 МЕДВЕЖИЙ"
            else:
                eth_trend = "\U0001f7e1 НЕЙТРАЛЬНЫЙ"

            sentiment_e = {"БЫЧИЙ": "\U0001f7e2", "МЕДВЕЖИЙ": "\U0001f534"}.get(sentiment_t, "\U0001f7e1")

            if fg_val >= 75:   fg_e = "\U0001f680"
            elif fg_val >= 55: fg_e = "\U0001f7e2"
            elif fg_val >= 45: fg_e = "\U0001f7e1"
            elif fg_val >= 25: fg_e = "\U0001f534"
            else:              fg_e = "\U0001f4a5"

            # === Блоки 2/6/7: вердикт + факторы ✅/❌/🟡 + скор/100 — по уже посчитанным данным выше ===
            t_score = 0
            t_factors = []
            if "БЫЧИЙ" in btc_trend and "\U0001f7e2" in btc_trend:
                t_score += 2; t_factors.append(("✅", "BTC тренд бычий (24ч+7д)"))
            elif "МЕДВЕЖИЙ" in btc_trend:
                t_score -= 2; t_factors.append(("❌", "BTC тренд медвежий (24ч+7д)"))
            else:
                t_factors.append(("🟡", "BTC тренд нейтральный"))
            if "\U0001f7e2" in eth_trend:
                t_score += 1; t_factors.append(("✅", "ETH тренд бычий"))
            elif "\U0001f534" in eth_trend:
                t_score -= 1; t_factors.append(("❌", "ETH тренд медвежий"))
            else:
                t_factors.append(("🟡", "ETH тренд нейтральный"))
            if btc_dom and btc_dom < 50 and btc_ch > 0:
                t_score += 2; t_factors.append(("✅", "BTC.D<50% + рост — альт-сезон формируется"))
            elif btc_dom and btc_dom > 55:
                t_score -= 1; t_factors.append(("❌", "BTC.D>55% — капитал в BTC, альты под давлением"))
            else:
                t_factors.append(("🟡", "Доминация не даёт чёткого сигнала"))
            if fg_val >= 55:
                t_score += 1; t_factors.append(("✅", "Fear&Greed в зоне жадности"))
            elif fg_val < 35:
                t_score -= 1; t_factors.append(("❌", "Fear&Greed в зоне страха"))
            else:
                t_factors.append(("🟡", "Fear&Greed нейтрален"))
            if bull_pct >= 60:
                t_score += 1; t_factors.append(("✅", f"{bull_pct}% альтов растут — широкий рост"))
            elif bull_pct <= 40:
                t_score -= 1; t_factors.append(("❌", f"только {bull_pct}% альтов растут — широкое падение"))
            else:
                t_factors.append(("🟡", "Рынок альтов смешанный"))

            T_MIN, T_MAX = -5, 7
            t_score_100 = max(0, min(100, round((t_score - T_MIN) / (T_MAX - T_MIN) * 100)))
            if t_score_100 >= 85:   t_grade="A+"; t_verdict_e="\U0001f680"; t_verdict_word="СИЛЬНЫЙ БЫЧИЙ"
            elif t_score_100 >= 65: t_grade="A";  t_verdict_e="\U0001f4c8"; t_verdict_word="БЫЧИЙ"
            elif t_score_100 >= 40: t_grade="B";  t_verdict_e="\U0001f7e1"; t_verdict_word="НЕЙТРАЛЬНО-БЫЧИЙ"
            elif t_score_100 >= 20: t_grade="C";  t_verdict_e="⚪";     t_verdict_word="НЕЙТРАЛЬНЫЙ"
            else:                   t_grade="C";  t_verdict_e="\U0001f4c9"; t_verdict_word="МЕДВЕЖИЙ"

            def fmt_ch(v): return f"+{v:.1f}%" if v >= 0 else f"{v:.1f}%"
            def fmt_p(v):
                if v >= 1000: return f"${v:,.0f}"
                if v >= 1:    return f"${v:,.2f}"
                return f"${v:.4f}"

            bdrop = round((126021 - btc_p) / 126021 * 100, 1) if btc_p > 0 else 0
            edrop = round((4878 - eth_p) / 4878 * 100, 1) if eth_p > 0 else 0

            mcap_str = ""
            if total_mcap > 0:
                if total_mcap >= 1e12: mcap_str = f"${total_mcap/1e12:.2f}T"
                else: mcap_str = f"${total_mcap/1e9:.0f}B"

            # --- Строим сообщение (премиум формат) ---
            SEP = "\u2796\u2796\u2796\u2796\u2796\u2796\u2796\u2796\u2796\u2796"

            # BTC bias вывод
            if btc_ch > 2 and btc_7d > 0:
                bias_line = "\u2705 *Лонги в приоритете*"
            elif btc_ch < -2 and btc_7d < 0:
                bias_line = "\u274c *Шорты или кэш*"
            else:
                bias_line = "\u26a0\ufe0f *Избирательные сетапы*"

            lines_out = [
                # === Блок 1: шапка + время ===
                "\U0001f4ca *BEST TRADE — РЫНОЧНЫЙ ТРЕНД*",
                f"\U0001f550 _{now_utc3()}_",
                SEP,
                "",
                # === Блок 2: вердикт ===
                f"_{t_verdict_e} {t_verdict_word.title()}  |  Скор: {t_score_100}/100  |  Качество: {t_grade}_",
                "",
                SEP,
                "",
                "\U0001fab2 *БИТКОИН / BTC*",
                "",
                f"\U0001f4cd  Цена:        *{fmt_p(btc_p)}*",
                f"\U0001f4c8  24ч:          *{fmt_ch(btc_ch)}*",
                f"\U0001f5d3  7д / 30д:   *{fmt_ch(btc_7d)}* / *{fmt_ch(btc_30d)}*",
                f"\U0001f3af  Тренд:      {btc_trend}",
                f"\U0001f4af  От ATH:     *\u2212{bdrop}%* ($126,021)",
                f"\U0001f4aa  Доминация: *{btc_dom}%*",
                f"\u27a1\ufe0f  Сигнал:    {bias_line}",
                "",
                SEP,
                "",
                "\U0001f48e *ЭФИРИУМ / ETH*",
                "",
                f"\U0001f4cd  Цена:      *{fmt_p(eth_p)}*",
                f"\U0001f4c8  24ч:        *{fmt_ch(eth_ch)}*",
                f"\U0001f5d3  7д:          *{fmt_ch(eth_7d)}*",
                f"\U0001f3af  Тренд:    {eth_trend}",
                f"\U0001f4af  От ATH:   *\u2212{edrop}%* ($4,878)",
            ]

            if sol_p > 0:
                sol_ch_str = fmt_ch(sol_ch)
                sol_trend = "\U0001f7e2 БЫЧИЙ" if sol_ch > 1.5 else ("\U0001f534 МЕДВЕЖИЙ" if sol_ch < -1.5 else "\U0001f7e1 НЕЙТРАЛЬНЫЙ")
                lines_out += [
                    "",
                    SEP,
                    "",
                    "\U0001f7e3 *SOLANA / SOL*",
                    "",
                    f"\U0001f4cd  Цена:    *{fmt_p(sol_p)}*",
                    f"\U0001f4c8  24ч:      *{sol_ch_str}*",
                    f"\U0001f3af  Тренд:  {sol_trend}",
                ]

            lines_out += [
                "",
                SEP,
                "",
                "\U0001f30d *РЫНОЧНЫЙ КОНТЕКСТ*",
                "",
            ]
            if mcap_str:
                lines_out.append(f"\U0001f4b0  Total MCap:    *{mcap_str}*")
            if btc_dom:
                lines_out.append(f"\U0001f4ca  BTC Dominance: *{btc_dom}%*")
                lines_out.append(f"\U0001f4ca  ETH Dominance: *{eth_dom}%*")

            lines_out += [
                "",
                f"{fg_e}  Fear & Greed:  *{fg_val}/100* — _{fg_label}_",
                f"{sentiment_e}  Сентимент:     *{sentiment_t}* ({bull_pct}% бычьих)",
            ]

            if gainers:
                lines_out += [
                    "",
                    SEP,
                    "",
                    "\U0001f7e2 *ТОП РОСТА 24ч (топ-100):*",
                    "",
                ]
                for i, (s, p, ch, ch7) in enumerate(gainers, 1):
                    ch7s = f" | 7д: +{ch7:.1f}%" if ch7 > 0 else (f" | 7д: {ch7:.1f}%" if ch7 < 0 else "")
                    lines_out.append(f"  {i}. \u2b06\ufe0f *{s}*  {fmt_p(p)}   *+{ch:.1f}%*{ch7s}")

            if losers:
                lines_out += [
                    "",
                    SEP,
                    "",
                    "\U0001f534 *ТОП ПАДЕНИЯ 24ч (топ-100):*",
                    "",
                ]
                for i, (s, p, ch, ch7) in enumerate(losers, 1):
                    ch7s = f" | 7д: {ch7:.1f}%" if ch7 != 0 else ""
                    lines_out.append(f"  {i}. \u2b07\ufe0f *{s}*  {fmt_p(p)}   *{ch:.1f}%*{ch7s}")

            lines_out += [
                "",
                SEP,
                f"\u26a0\ufe0f  Риск: 1\u20132% депозита \u2022 SL обязателен",
                "",
                SEP,
                "",
                # === Блок 6: факторы ✅/❌/🟡 ===
                "\U0001f4cb *Факторы:*",
                "",
            ] + [f"  {mark}  {text}" for mark, text in t_factors] + [
                "",
                SEP,
                "",
                # === Блок 7: расшифровка скора ===
                f"\U0001f4cb *РАСШИФРОВКА СКОРА  {t_score_100}/100*",
                "",
                "  Шкала силы:",
                "  0–19   \U0001f4c9  МЕДВЕЖИЙ",
                "  20–39  ⚪  НЕЙТРАЛЬНЫЙ",
                "  40–64  \U0001f7e1  НЕЙТРАЛЬНО-БЫЧИЙ",
                "  65–84  \U0001f4c8  БЫЧИЙ",
                "  85–100 \U0001f680  СИЛЬНЫЙ БЫЧИЙ",
                "",
                "  Грейды: A+ ≥85 · A ≥65 · B ≥40 · C <40",
            ]

            # === ФАЗА РЫНКА + ЛИКВИДНОСТЬ ===
            phase_lines = []
            if btc_7d > 5:
                market_phase = '📈 АПТРЕНД — highs и lows обновляются вверх'
            elif btc_7d < -5:
                market_phase = '📉 ДАУНТРЕНД — highs и lows обновляются вниз'
            else:
                market_phase = '⇔ ДИАПАЗОН / БАЗА — рынок в консолидации'
            phase_lines.append(f'🔄 *ФАЗА РЫНКА:* {market_phase}')
            try:
                btcd_val = float(str(btc_dom).replace('%','').strip())
            except Exception:
                btcd_val = None
            if btcd_val and btc_ch > 0 and btcd_val > 50:
                liq_line = '🔵 BTC.D↑ + BTC↑ → капитал в BTC, алты под давлением'
            elif btcd_val and btc_ch < 0 and btcd_val > 50:
                liq_line = '🔴 BTC.D↑ + BTC↓ → паника, алты сыплются'
            elif btcd_val and btc_ch > 0 and btcd_val < 50:
                liq_line = '🟢 BTC.D↓ + BTC↑ → альт-сезон, ротация в алты'
            elif btcd_val and btc_ch < 0 and btcd_val < 50:
                liq_line = '🟡 BTC.D↓ + BTC↓ → алты консолидируют'
            else:
                liq_line = '⚪ Недостаточно данных'
            phase_lines.append(f'🔀 *ЛИКВИДНОСТЬ:* {liq_line}')
            if btc_7d > 15:
                wave_line = 'Возможная волна 3 или 5 (импульс вверх) — осторожно на хаях'
            elif btc_7d > 5:
                wave_line = 'Возможная волна 1 или 3 (начало движения вверх)'
            elif btc_7d < -15:
                wave_line = 'Возможная волна C или 3 вниз — избегать лонгов'
            elif btc_7d < -5:
                wave_line = 'Возможная коррекционная волна A/B/C'
            else:
                wave_line = 'Волна не определена — рынок в базе'
            phase_lines.append(f'🌊 *ЭЛЛИОТТ (1D):* {wave_line}')
            lines_out += ['', SEP, ''] + phase_lines

            nav_trend = InlineKeyboardMarkup([
                [InlineKeyboardButton("\U0001f504 Обновить", callback_data="trend_analysis"),
                 InlineKeyboardButton("\U0001f3e0 Меню",    callback_data="show_menu")],
            ])
            await q.edit_message_text("\n".join(lines_out), parse_mode="Markdown", reply_markup=nav_trend)
        except Exception as e:
            await q.edit_message_text(f"\u274c Ошибка: {e}", reply_markup=back_kb())

    elif data == "x100_scan":
        await q.edit_message_text("🚀 x100 сканер...",parse_mode="Markdown")
        try:
            coins2=get_top500()
            if coins2:
                res=_scan_x100_candidates(coins2)
                from datetime import datetime as _d2
                SEP2="➖"*10
                pumps=res["pumps"]; dumps=res["dumps"]
                lns=["🚀 *BEST TRADE — x100 СКАНЕР*",f"🕐 _{now_utc3()}_",SEP2,""]
                if pumps:
                    lns+=["💎 *ПАМП КАНДИДАТЫ:*",""]
                    for i,p in enumerate(pumps[:5],1):
                        sym=p["coin"]["symbol"]; tv=f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym}USDT"
                        lns.append(f"*{i}. [{sym}/USDT]({tv})*  💎 `{p['score']}pts`")
                        lns.append(f"  90д: *{p['ch90d']:.0f}%*  MCap: ${p['mcap']/1e6:.0f}M")
                        lns.append("")
                if dumps:
                    lns+=[SEP2,"","⚠️ *ДАМП КАНДИДАТЫ:*",""]
                    for i,d in enumerate(dumps[:5],1):
                        sym=d["coin"]["symbol"]; tv=f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym}USDT"
                        lns.append(f"*{i}. [{sym}/USDT]({tv})*  🚨 `{d['score']}pts`")
                        lns.append(f"  30д: *+{d['ch30d']:.0f}%*  MCap: ${d['mcap']/1e6:.0f}M")
                        lns.append("")
                lns+=[SEP2,"⚠️ _Риск: 1-2% депозита · SL обязателен_"]
                nav_x=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Обновить",callback_data="x100_scan"),InlineKeyboardButton("🏠 Меню",callback_data="show_menu")],[InlineKeyboardButton("🟢 ТОП ЛОНГ",callback_data="top_long"),InlineKeyboardButton("🔴 ТОП ШОРТ",callback_data="top_short")]])
                await q.edit_message_text("\n".join(lns),parse_mode="Markdown",reply_markup=nav_x,disable_web_page_preview=True)
            else:
                await q.edit_message_text("❌ Нет данных",reply_markup=back_kb())
        except Exception as e:
            await q.edit_message_text(f"❌ {e}",reply_markup=back_kb())

    elif data == "institutional":
        await q.edit_message_text("⏳ Загружаю институциональный анализ...", parse_mode="Markdown")
        try:
            import datetime
            import requests as _r
            coins=get_all_coins()
            gm=get_global_metrics()
            prices=get_btc_eth_price()
            btc_dom=gm.get("btc_dominance",0) or 0
            eth_dom=gm.get("eth_dominance",0) or 0
            total_mcap=gm.get("total_mcap",0) or 0
            btc=prices.get("BTC",{}); btc_price=btc.get("price",0) or 0
            btc_ch24=btc.get("ch24h",0) or 0
            bq=next((c for c in coins if c["symbol"]=="BTC"),{})
            btc_ch7d=bq.get("quote",{}).get("USDT",{}).get("percent_change_7d",0) or 0
            btc_ch30=bq.get("quote",{}).get("USDT",{}).get("percent_change_30d",0) or 0
            btc_vol=bq.get("quote",{}).get("USDT",{}).get("volume_24h",0) or 0

            # Fear & Greed
            fv=50; fl="Neutral"
            try:
                fg=_r.get("https://api.alternative.me/fng/?limit=1",timeout=5).json()
                fv=int(fg["data"][0]["value"]); fl=fg["data"][0]["value_classification"]
            except: pass

            # OI BTC + ETH
            oi_btc=0; oi_eth=0
            try:
                oi_btc=_get_oi_usd("BTC")/1e9
            except: pass
            try:
                oi_eth=_get_oi_usd("ETH")/1e9
            except: pass

            # Funding rates BTC + ETH
            fund_btc=0; fund_eth=0
            try:
                fund_btc=_get_funding_pct("BTC")
            except: pass
            try:
                fund_eth=_get_funding_pct("ETH")
            except: pass

            # Long/Short ratio
            ls_ratio=1.0
            try:
                ls_ratio=_get_ls_ratio("BTC")
            except: pass

            # Put/Call ratio + Max Pain Deribit -- Этап 3.3 (АПГРЕЙД 11.07): раньше здесь
            # считался СВОЙ отдельный pcr = кол-во puts / кол-во calls instrument-СЧЁТОМ
            # (сколько СТРАЙКОВ есть, не сколько ими торгуют) -- слабая метрика, легко
            # оказывается около 1.0 просто потому что у Deribit похожее число
            # call/put-инструментов в листинге. Теперь честный OI-взвешенный put/call
            # (get_options_data(), тот же расчёт, что уже используется в карточке
            # "Обзор" -- один источник) + Max Pain (новое, compute_max_pain()).
            opts_i = get_options_data()
            pcr = opts_i.get("put_call_ratio", 0) if opts_i.get("ok") else 0
            max_pain_btc = opts_i.get("max_pain")

            # Perp/Spot премия BTC -- Этап 3.2 (АПГРЕЙД 11.07)
            premium_i = get_perp_spot_premium("BTC")

            # CVD BTC/ETH/SOL 1ч/4ч -- Этап 3.1 (АПГРЕЙД 11.07), живой WS-агрегатор Whale Radar
            cvd_btc = get_cvd_summary("BTCUSDT")
            cvd_eth = get_cvd_summary("ETHUSDT")
            cvd_sol = get_cvd_summary("SOLUSDT")

            # S&P500
            sp_price=0; sp_ch=0
            _yq,_ysp_price=_fetch_yahoo_chart("%5EGSPC")
            if _ysp_price is not None: sp_price=_ysp_price
            if _yq and len(_yq)>=2: sp_ch=(_yq[-1]-_yq[-2])/_yq[-2]*100

            # DXY
            dxy=0; dxy_ch=0
            dc,_dxy_price=_fetch_yahoo_chart("DX-Y.NYB")
            if dc:
                dxy=dc[-1]; dxy_ch=(dc[-1]-dc[-2])/dc[-2]*100 if len(dc)>=2 else 0

            # Gold
            gold=0; gold_ch=0
            _gc,_gold_price=_fetch_yahoo_chart("GC%3DF")
            if _gold_price is not None: gold=_gold_price
            if _gc and len(_gc)>=2: gold_ch=(_gc[-1]-_gc[-2])/_gc[-2]*100

            # VIX
            vix=0
            _,_vix_price=_fetch_yahoo_chart("%5EVIX", range_str="2d")
            if _vix_price is not None: vix=_vix_price

            # EMA 50/200 через CoinGecko (Binance заблокирован на Railway)
            ema_cross="N/A"
            try:
                kl=get_binance_ohlc("BTC","1d",210)
                cl=[c["close"] for c in kl]
                def ema_f(d,n):
                    k2=2/(n+1); e=d[0]
                    for p in d[1:]: e=p*k2+e*(1-k2)
                    return e
                e50=ema_f(cl[-50:],50); e200=ema_f(cl,200)
                if e50>e200: ema_cross="🟢 EMA50 > EMA200 (ГОЛДЕН КРОСС)"
                else: ema_cross="🔴 EMA50 < EMA200 (ДЕАД КРОСС)"
            except: pass

            # USDT market cap (stablecoin flow) — через CoinGecko, старый источник отдавал $0.0B.
            # Этап 2.4 (АПГРЕЙД 11.07): $0.0B оставался и после того фикса на пути ОШИБКИ фетча
            # (usdt_mcap оставался 0, но отображался как "$0.0B" -- неотличимо от "реально ноль").
            # Честно: при недоступности источника -- "н/д" текстом, не выдуманный ноль.
            usdt_mcap_res = {"ok": False}
            try:
                usdt_mcap_res = get_usdt_mcap()
            except Exception:
                pass
            usdt_mcap = usdt_mcap_res.get("usdt_mcap", 0) * 1e9

            def fpct(v): return ("+"+str(round(v,2)) if v>=0 else str(round(v,2)))+"%"
            def fe(v): return "🟢" if v>=0.5 else ("🔴" if v<=-0.5 else "⚪")

            # OI matrix
            if btc_ch24>0 and oi_btc>0:
                if fund_btc>0: oi_signal="🟢 Цена↑ OI↑ — новые лонги, сильный тренд"
                else: oi_signal="🟡 Цена↑ OI↓ — шорт-сквиз, может исчерпаться"
            else:
                if fund_btc<0: oi_signal="🔴 Цена↓ OI↑ — новые шорты, реальное давление"
                else: oi_signal="🟡 Цена↓ OI↓ — выход из позиций, движение слабеет"

            # BTC.D liquidity direction
            if btc_dom>55 and btc_ch24>1.5: liq_dir="🔴 Капитал в BTC — альты под давлением"
            elif btc_dom<50 and btc_ch24>0: liq_dir="🟢 Капитал в альты — альт-сезон"
            elif btc_dom>50 and btc_ch24<0: liq_dir="🔴 BTC.D+BTC паника"
            else: liq_dir="🟡 Консолидация"

            # S&P correlation signal
            if sp_ch<-1 and btc_ch24>0: sp_sig="⚠️ BTC игнорирует падение S&P — риск разворота"
            elif sp_ch<-1 and btc_ch24<0: sp_sig="🔴 BTC следует S&P — корреляция подтверждена"
            elif sp_ch>0.5 and btc_ch24>0: sp_sig="🟢 Синхронный рост"
            else: sp_sig="⚪ Нейтральная корреляция"

            if fund_btc>0.1: fund_warn="🚨 КРИТИЧЕСКИЙ ПЕРЕГРЕВ ЛОНГОВ"
            elif fund_btc>0.05: fund_warn="⚠️ Лонги перегреты"
            elif fund_btc<-0.05: fund_warn="🟢 Шорты перегреты — возможен разворот вверх"
            else: fund_warn="⚪ Норма"

            if vix>30: vix_z="🔴 ВЫСОКИЙ (паника на рынке)"
            elif vix>20: vix_z="🟠 ПОВЫШЕННЫЙ"
            else: vix_z="🟢 НИЗКИЙ (рынок спокоен)"

            # === Блоки 2/6/7: вердикт + факторы ✅/❌/🟡 + скор/100 — по уже посчитанным данным выше ===
            i_score=0; i_factors=[]
            if fund_btc>0.05: i_score-=1; i_factors.append(("❌","Funding BTC перегрет — лонги переполнены"))
            elif fund_btc<-0.05: i_score+=1; i_factors.append(("✅","Funding BTC отрицательный — возможен шорт-сквиз"))
            else: i_factors.append(("🟡","Funding BTC в норме"))
            if oi_btc>0 and btc_ch24>0: i_score+=1; i_factors.append(("✅","OI растёт вместе с ценой — сильный тренд"))
            elif oi_btc>0 and btc_ch24<0: i_score-=1; i_factors.append(("❌","OI растёт при падении цены — давление шортов"))
            else: i_factors.append(("🟡","OI не даёт чёткого сигнала"))
            if btc_dom<50 and btc_ch24>0: i_score+=1; i_factors.append(("✅","BTC.D<50% + рост — альт-сезон"))
            elif btc_dom>55 and btc_ch24>1.5: i_score-=1; i_factors.append(("❌","BTC.D>55% — капитал в BTC, альты под давлением"))
            else: i_factors.append(("🟡","Доминация нейтральна"))
            if sp_ch>0.5: i_score+=1; i_factors.append(("✅","S&P500 растёт — риск-аппетит жив"))
            elif sp_ch<-1.5: i_score-=1; i_factors.append(("❌","S&P500 падает — риск-офф давит на BTC"))
            else: i_factors.append(("🟡","S&P500 нейтрален"))
            if vix>25: i_score-=1; i_factors.append(("❌","VIX повышен — рынок нервничает"))
            else: i_factors.append(("✅","VIX в норме — рынок спокоен"))
            if pcr>1.2: i_score-=1; i_factors.append(("❌","Put/Call>1.2 — опционный рынок ждёт падения"))
            elif pcr<0.7: i_score+=1; i_factors.append(("✅","Put/Call<0.7 — опционный рынок ждёт роста"))
            else: i_factors.append(("🟡","Put/Call нейтрален"))

            I_MIN, I_MAX = -6, 6
            i_score_100 = max(0, min(100, round((i_score-I_MIN)/(I_MAX-I_MIN)*100)))
            if i_score_100>=85: i_grade="A+"; i_verdict_e="🚀"; i_verdict_word="СИЛЬНЫЙ БЫЧИЙ"
            elif i_score_100>=65: i_grade="A"; i_verdict_e="📈"; i_verdict_word="БЫЧИЙ"
            elif i_score_100>=40: i_grade="B"; i_verdict_e="🟡"; i_verdict_word="НЕЙТРАЛЬНО-БЫЧИЙ"
            elif i_score_100>=20: i_grade="C"; i_verdict_e="⚪"; i_verdict_word="НЕЙТРАЛЬНЫЙ"
            else: i_grade="C"; i_verdict_e="📉"; i_verdict_word="МЕДВЕЖИЙ"

            SEP="➖"*18
            out=[
                # === Блок 1: шапка + время ===
                "🏛 BEST TRADE — ИНСТИТУЦИОНАЛЬНЫЙ АНАЛИЗ",
                f"🕐 {now_utc3()}",
                SEP,"",
                # === Блок 2: вердикт ===
                f"_{i_verdict_e} {i_verdict_word.title()}  |  Скор: {i_score_100}/100  |  Качество: {i_grade}_",
                "",
                SEP,"",
                "💰 МАКРО РЫНОК",
                "  Общая кап: $"+str(round(total_mcap/1e12,2))+"T",
                "  BTC.D: "+str(round(btc_dom,1))+"% | ETH.D: "+str(round(eth_dom,1))+"%",
                "  USDT мкап: "+(f"${round(usdt_mcap/1e9,1)}B" if usdt_mcap_res.get("ok") else "н/д (источник недоступен)"),
                "  "+liq_dir,"",
                "📈 ФОНДОВЫЕ РЫНКИ",
                "  S&P500: $"+str(round(sp_price,0))[:-2]+"  "+fe(sp_ch)+" "+fpct(sp_ch),
                "  DXY:    "+str(round(dxy,2))+"  "+fe(-dxy_ch)+" "+fpct(dxy_ch),
                "  Gold:   $"+str(round(gold,0))[:-2]+"  "+fe(gold_ch)+" "+fpct(gold_ch),
                "  VIX:    "+str(round(vix,1))+"  "+vix_z,
                "  "+sp_sig,"",
                "🏛 ОПЦИОНЫ И ОИ",
                "  OI BTC: $"+str(round(oi_btc,1))+"B",
                "  OI ETH: $"+str(round(oi_eth,1))+"B",
                "  "+oi_signal,
                "  Funding BTC: "+str(round(fund_btc,4))+"% — "+fund_warn,
                "  Funding ETH: "+str(round(fund_eth,4))+"%",
                "  Put/Call (BTC): "+(str(pcr) if opts_i.get("ok") else "н/д"),
                "  Max Pain (BTC): "+(f"${max_pain_btc:,.0f}" if max_pain_btc else "н/д"),
                "  L/S Ratio: "+str(round(ls_ratio,2)),"",
                "🌐 ДЕРИВАТИВЫ (Фаза B)",
                "  Perp/Spot премия BTC: "+(fpct(premium_i["premium_pct"])+" — "+premium_i["signal"]
                                             if premium_i.get("ok") else "н/д (источник недоступен)"),
                "  CVD BTC 1ч: "+f"${cvd_btc['cvd_1h']:+,.0f}"+"  4ч: "+f"${cvd_btc['cvd_4h']:+,.0f}"+"  — "+cvd_btc["direction_1h"],
                "  CVD ETH 1ч: "+f"${cvd_eth['cvd_1h']:+,.0f}"+"  4ч: "+f"${cvd_eth['cvd_4h']:+,.0f}"+"  — "+cvd_eth["direction_1h"],
                "  CVD SOL 1ч: "+f"${cvd_sol['cvd_1h']:+,.0f}"+"  4ч: "+f"${cvd_sol['cvd_4h']:+,.0f}"+"  — "+cvd_sol["direction_1h"],
                "  _CVD ещё копится с момента последнего рестарта бота -- значения растут со временем_","",
                "🧠 ТЕХНИЧЕСКИЙ АНАЛИЗ",
                "  "+ema_cross,
                "  Fear&Greed: "+str(fv)+"/100 — "+fl,
                "  BTC 7d: "+fpct(btc_ch7d)+" | 30d: "+fpct(btc_ch30),"",
                SEP,"",
                # === Блок 6: факторы ✅/❌/🟡 ===
                "📋 *Факторы:*","",
            ]+["  "+mark+"  "+text for mark,text in i_factors]+["",
                SEP,"",
                # === Блок 7: расшифровка скора ===
                "📋 *РАСШИФРОВКА СКОРА  "+str(i_score_100)+"/100*","",
                "  Шкала силы:",
                "  0–19   📉  МЕДВЕЖИЙ",
                "  20–39  ⚪  НЕЙТРАЛЬНЫЙ",
                "  40–64  🟡  НЕЙТРАЛЬНО-БЫЧИЙ",
                "  65–84  📈  БЫЧИЙ",
                "  85–100 🚀  СИЛЬНЫЙ БЫЧИЙ","",
                "  Грейды: A+ ≥85 · A ≥65 · B ≥40 · C <40","",
                SEP,
                "💡 Итоговый вывод:",
            ]
            # Conclusion
            signals=[]
            if fund_btc>0.05: signals.append("⚠️ Лонги перегреты — жди флаша вниз")
            if fund_btc<-0.05: signals.append("🟢 Шорты перегреты — возможен шорт-сквиз")
            if sp_ch<-1.5: signals.append("🔴 S&P падает — риск для BTC")
            if vix>25: signals.append("⚠️ VIX повышен — рынок нервничает")
            if pcr>1.2: signals.append("🔴 Put/Call > 1.2 — ожидают падение")
            if pcr<0.7: signals.append("🟢 Put/Call < 0.7 — ожидают рост")
            if not signals: signals.append("⚪ Сигналов нет, рынок в равновесии")
            out+=["  "+s for s in signals]
            out+=[SEP]

            nav=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Обновить",callback_data="institutional"),
                InlineKeyboardButton("🏠 Меню",callback_data="show_menu")
            ],[
                InlineKeyboardButton("📊 Обзор",callback_data="market_overview"),
                InlineKeyboardButton("📈 Тренд",callback_data="trend_analysis")
            ]])
            await q.edit_message_text("\n".join(out),parse_mode="Markdown",reply_markup=nav,disable_web_page_preview=True)
        except Exception as e:
            log.error(f"institutional: {e}")
            await q.edit_message_text(f"❌ {e}",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Меню",callback_data="show_menu")]]))


    elif data == "whale_status":
        await q.edit_message_text("\U0001f433 Сканирую кит-активность...", parse_mode="Markdown")
        try:
            all_funding = _get_funding_rates()
            funding_map = {f["symbol"]: f for f in all_funding}

            found = []
            SEP = "\u2796\u2796\u2796\u2796\u2796\u2796\u2796\u2796\u2796\u2796"

            for sym in _WHALE_WATCH:
                fd = funding_map.get(sym, {})
                funding = fd.get("funding", 0)
                price   = fd.get("price", 0)
                price_fresh = fd.get("price_fresh", "")
                if price <= 0: continue
                oi = _get_oi_change(sym)
                ls = _get_ls_ratio(sym)
                w  = _analyze_whale_signal(sym, funding, oi, ls, price, price_fresh,
                                            fd.get("ch24h", 0), fd.get("ch7d", 0), fd.get("rank"), fd.get("vol", 0))
                if w:
                    found.append(w)
                else:
                    # Показываем базовые данные даже без сигнала
                    fr_e = "\U0001f534" if funding < -0.03 else ("\U0001f7e2" if funding > 0.05 else "\U0001f7e1")
                    oi_chg = oi.get("change_pct", 0) if isinstance(oi, dict) else (oi or 0)
                    ls_r = ls.get("ratio", 1.0) if isinstance(ls, dict) else (ls or 1.0)
                    found_info = {"sym": sym, "funding": funding, "oi": oi_chg, "ls": ls_r, "price": price, "signal": None, "fr_e": fr_e}

            if found:
                lines_w = [
                    "\U0001f433 *WHALE MONITOR — активные сигналы*",
                    f"\U0001f550 _{now_utc3()}_",
                    SEP, "",
                ]
                for w in found[:5]:
                    label, stars = whale_monitor_label(w["direction"], w.get("score_100", 0))
                    sig_e = "\U0001f534" if w["direction"] == "SHORT" else "\U0001f7e2"
                    if label == "НАБЛЮДЕНИЕ":
                        sig_e = "\U0001f7e1"  # скор <40 -- направление не утверждаем, жёлтый нейтральный маркер
                    lines_w += [
                        f"{sig_e} *{w['symbol']}/USDT* — {label}  {stars}  _(Скор {w.get('score_100',0)}/100, {w.get('grade','C')})_",
                        f"  Funding: `{w['funding']:+.4f}%`  |  OI: `{w['oi']:+.1f}%`  |  L/S: `{w['ls']:.2f}`",
                        f"  Цена: `{fp(w['price'])}`",
                        "",
                    ]
            else:
                # Показываем текущее состояние без сигналов
                lines_w = [
                    "\U0001f433 *WHALE MONITOR*",
                    f"\U0001f550 _{now_utc3()}_",
                    SEP, "",
                    "\u2705 *Активных кит-сигналов нет*",
                    "_Рынок в норме — аномалий не обнаружено_",
                    "", SEP, "",
                    "\U0001f4ca *Текущий Funding Rate:*", "",
                ]
                for sym in _WHALE_WATCH[:8]:
                    fd = funding_map.get(sym, {})
                    fr = fd.get("funding", 0)
                    pr = fd.get("price", 0)
                    if pr <= 0: continue
                    fr_e = "\U0001f534" if fr < -0.03 else ("\U0001f7e2" if fr > 0.05 else "\U0001f7e1")
                    lines_w.append(f"  {fr_e} *{sym}*: `{fr:+.4f}%`  |  `{fp(pr)}`")

            lines_w += [
                "", SEP,
                "_Авто-мониторинг каждые 15 мин_",
            ]

            nav_w = InlineKeyboardMarkup([
                [InlineKeyboardButton("\U0001f504 Обновить", callback_data="whale_status"),
                 InlineKeyboardButton("\U0001f3e0 Меню",    callback_data="show_menu")],
            ])
            await q.edit_message_text("\n".join(lines_w), parse_mode="Markdown", reply_markup=nav_w)
        except Exception as e:
            await q.edit_message_text(f"\u274c Ошибка Whale Monitor: {e}", reply_markup=back_kb())

    elif data == "pump_radar":
        try:
            from pump_detector import get_pump_radar_state
            st = get_pump_radar_state()
            SEP = "➖"*10
            stage_e = {"WATCHING": "\U0001f440", "PUMP_DETECTED": "\U0001f680", "DUMP_DETECTED": "\U0001f4a5",
                       "REVERSAL_CONFIRMED": "\U0001f53b", "PROMOTED": "✅"}
            lines_pr = ["⚡ *ПАМП-РАДАР*", f"\U0001f550 _{now_utc3()}_", SEP, ""]

            lines_pr.append("🔴 *Пампы (сценарий шорт):*\n")
            if st["pumps_active"]:
                for a in st["pumps_active"]:
                    e = stage_e.get(a["stage"], "•")
                    lines_pr.append(f"{e} *{a['symbol']}* — {a['stage']}  "
                                     f"({a['elapsed_min']:.0f} мин, {a['pct_from_level']:+.1f}% от пика)")
            else:
                lines_pr.append("Активных наблюдений нет")
            lines_pr.append("")

            lines_pr.append("🟢 *Дампы (сценарий лонг):*\n")
            if st["dumps_active"]:
                for a in st["dumps_active"]:
                    e = stage_e.get(a["stage"], "•")
                    lines_pr.append(f"{e} *{a['symbol']}* — {a['stage']}  "
                                     f"({a['elapsed_min']:.0f} мин, {a['pct_from_level']:+.1f}% от дна)")
            else:
                lines_pr.append("Активных наблюдений нет")
            lines_pr.append("")

            hp = st["pumps_history_24h"]; hd = st["dumps_history_24h"]
            lines_pr += [
                SEP, "",
                "\U0001f4ca *История за 24ч — пампы:*",
                f"  Детектов: {hp['detected']}  ·  Разворотов: {hp['reversed']}  ·  "
                f"Promoted: {hp['promoted']}  ·  Истекло: {hp['expired']}",
                "",
                "\U0001f4ca *История за 24ч — дампы:*",
                f"  Детектов: {hd['detected']}  ·  Разворотов: {hd['reversed']}  ·  "
                f"Добавлено: {hd['promoted']}  ·  Истекло: {hd['expired']}",
                "", SEP,
            ]
            nav_pr = InlineKeyboardMarkup([
                [InlineKeyboardButton("\U0001f504 Обновить", callback_data="pump_radar"),
                 InlineKeyboardButton("\U0001f3e0 Меню", callback_data="show_menu")],
            ])
            await q.edit_message_text("\n".join(lines_pr), parse_mode="Markdown", reply_markup=nav_pr)
        except Exception as e:
            await q.edit_message_text("Ошибка Памп-радара: "+str(e), reply_markup=back_kb())

    elif data.startswith("pump_sub_"):
        sym = data[len("pump_sub_"):]
        try:
            from pump_detector import subscribe_symbol
            subscribe_symbol(sym, q.message.chat_id)
            await q.answer(f"\U0001f514 Подписка на {sym} оформлена")
        except Exception as e:
            await q.answer("Ошибка подписки: "+str(e))

    elif data.startswith("pump_addlong_"):
        sym = data[len("pump_addlong_"):]
        try:
            from pump_detector import get_dump_offer
            offer = get_dump_offer(sym)
            if not offer:
                await q.answer("Предложение больше не активно")
            else:
                added = add_top_long_signal(sym, {
                    "entry": offer["entry"], "tp1": offer["tp1"], "tp2": offer["tp2"],
                    "sl": offer["sl"], "rr": offer["rr"], "status": "active",
                    "note": "⚡ из Памп-радара (дамп)",
                })
                await q.answer("✅ Добавлено в ТОП ЛОНГ" if added else "Уже есть в ТОП ЛОНГ")
                if added:
                    try:
                        import functools
                        loop = asyncio.get_event_loop()
                        candles_2h = await loop.run_in_executor(None, get_binance_ohlc, sym, "2h", 120)
                        if candles_2h:
                            chart = None
                            try:
                                chart = await loop.run_in_executor(None, functools.partial(
                                    chart_v4.build_trade_chart_v4, sym, candles_2h, "long",
                                    entry_levels=[offer["entry"]], sl=offer["sl"],
                                    tp1=offer["tp1"], tp2=offer["tp2"], rr=offer["rr"]))
                            except Exception as e:
                                log.error(f"promotion chart_v4 {sym}: {e}, falling back to chart_v3")
                            if chart is None:
                                chart = await loop.run_in_executor(
                                    None, chart_v3.build_trade_chart, sym, candles_2h, "long",
                                    [offer["entry"]], offer["sl"], offer["tp1"], offer["tp2"], None, offer["rr"])
                            if chart:
                                await ctx.bot.send_photo(q.message.chat_id, photo=chart,
                                    caption=f"📊 {sym} — ТОП ЛОНГ (промоушен из Памп-радара)")
                    except Exception as e:
                        log.error(f"promotion chart_v3 {sym}: {e}")
        except Exception as e:
            await q.answer("Ошибка: "+str(e))

def _get_funding_rates():
    """Приближение funding через 24ч % изменение цены (не настоящий фьючерсный funding --
    так было и раньше, формула не менялась). Источник цены/изменения -- get_all_coins()
    (CoinGecko первичный, CMC фоллбек внутри неё же). Раньше делал 10 отдельных прямых
    CMC-запросов по одному на символ -- теперь ноль дополнительных вызовов, данные уже
    в общем кэше get_all_coins(). ROADMAP: CMC перестал быть критическим, 2026-07-10."""
    try:
        from live_prices import resolve_price
        symbols = ["BTC","ETH","SOL","BNB","XRP","ADA","DOGE","AVAX","LINK","DOT"]
        coins = get_all_coins()
        by_sym = {c["symbol"]: c for c in coins}
        result = []
        for sym in symbols:
            c = by_sym.get(sym)
            if not c:
                continue
            try:
                q = c.get("quote", {}).get("USDT", {})
                cg_price = q.get("price", 0) or 0
                ch24 = q.get("percent_change_24h", 0) or 0
                if cg_price > 0:
                    price, price_fresh = resolve_price(sym, cg_price)
                    result.append({
                        "symbol": sym,
                        "funding": round(ch24 / 100 * 0.01, 6),
                        "price": round(price, 4),
                        "price_fresh": price_fresh,
                        "ch24h": ch24,
                        "ch7d": q.get("percent_change_7d", 0) or 0,
                        "rank": c.get("cmc_rank"),
                        "vol": q.get("volume_24h", 0) or 0,
                    })
            except:
                pass
        return result
    except:
        return []






def _analyze_whale_signal(symbol: str, funding: float, oi: float, ls: float, price: float, price_fresh: str = "",
                           ch24h: float = 0.0, ch7d: float = 0.0, rank=None, vol: float = 0.0):
    """Анализ whale сигнала — блоки 6/7 (факторы ✅/❌/🟡 + скор/грейд) собираются здесь,
    рендер текста — в _format_whale_alert()."""
    signals = []
    score = 0
    factors = []  # (mark, text) — ✅/❌/🟡 по каждому фактору, для блока 6

    if funding < -0.05:
        signals.append("🔴 Экстремальный шорт-финансинг"); score += 3
        factors.append(("✅", "Funding экстремально отрицательный — шорт-сквиз возможен"))
    elif funding < -0.01:
        signals.append("🟡 Негативный финансинг"); score += 1
        factors.append(("🟡", "Funding слегка отрицательный"))
    elif funding > 0.05:
        signals.append("🟢 Экстремальный лонг-финансинг"); score += 2
        factors.append(("✅", "Funding экстремально положительный — лонги перегреты"))
    elif funding > 0.01:
        signals.append("🟡 Позитивный финансинг"); score += 1
        factors.append(("🟡", "Funding слегка положительный"))
    else:
        factors.append(("❌", "Funding в норме — не подтверждает перекос"))

    if oi > 0.1:
        signals.append("📈 OI растёт"); score += 2
        factors.append(("✅", "OI растёт вместе с движением"))
    elif oi < -0.1:
        signals.append("📉 OI падает"); score += 1
        factors.append(("🟡", "OI падает — возможен выход из позиций"))
    else:
        factors.append(("❌", "OI без изменений — нет притока новых позиций"))

    if ls > 1.5:
        signals.append("🐋 Лонги доминируют"); score += 2
        factors.append(("✅", "L/S ratio — лонги явно доминируют"))
    elif ls < 0.7:
        signals.append("🐻 Шорты доминируют"); score += 2
        factors.append(("✅", "L/S ratio — шорты явно доминируют"))
    else:
        factors.append(("❌", "L/S ratio сбалансирован"))

    if score < 2 or not signals: return None
    direction = "LONG" if funding < -0.03 or ls > 1.3 else ("SHORT" if funding > 0.03 or ls < 0.8 else "NEUTRAL")

    score_100 = min(round(score / 7 * 100), 100)  # макс. очков в схеме выше — 7 (3+2+2)
    if score_100 >= 85:   grade = "A+"
    elif score_100 >= 65: grade = "A"
    elif score_100 >= 40: grade = "B"
    else:                 grade = "C"

    return {"symbol": symbol, "direction": direction, "score": score, "score_100": score_100, "grade": grade,
            "signals": signals, "factors": factors,
            "funding": funding, "oi": oi, "ls": ls, "price": price, "price_fresh": price_fresh,
            "ch24h": ch24h, "ch7d": ch7d, "rank": rank, "vol": vol}

def _get_ls_ratio(symbol: str) -> float:
    """Long/Short ratio через Bybit (CoinGecko/CMC такого не отдают бесплатно;
    Bybit не под тем гео-блоком, что fapi.binance.com на Railway)"""
    try:
        r = requests.get("https://api.bybit.com/v5/market/account-ratio",
                          params={"category": "linear", "symbol": f"{symbol}USDT",
                                   "period": "1h", "limit": 1}, timeout=6)
        r.raise_for_status()
        rows = r.json().get("result", {}).get("list", [])
        if not rows:
            return 1.0
        buy = float(rows[0].get("buyRatio", 0.5))
        sell = float(rows[0].get("sellRatio", 0.5))
        return buy / sell if sell > 0 else 1.0
    except:
        return 1.0

def _get_oi_change(symbol: str) -> float:
    """Изменение OI в % с момента предыдущего опроса через CoinGecko
    (approximation — CoinGecko не отдаёт бесплатную историю OI, в отличие от Binance)"""
    oi_now = _get_oi_usd(symbol)
    if oi_now <= 0:
        return 0.0
    prev = _OI_HISTORY.get(symbol)
    _OI_HISTORY[symbol] = (time.time(), oi_now)
    if not prev or prev[1] <= 0:
        return 0.0
    return (oi_now - prev[1]) / prev[1] * 100


def _get_liquidations(symbol: str) -> dict:
    """Ликвидации (заглушка — Binance недоступен на Railway)"""
    return {"long": 0, "short": 0}


def _get_large_trades(symbol: str) -> list:
    """Крупные сделки (заглушка — Binance недоступен на Railway)"""
    return []

def _format_whale_alert(w: dict) -> str:
    """Форматирует алерт кита для отправки — блоки 1,2,3,6,7 единого шаблона
    + словесная интерпретация OI-матрицы."""
    fp = lambda v, d=2: (f"${v:,.{d}f}" if v >= 1 else f"${v:.{d}f}")
    sym = w.get("symbol", "?")
    direction = w.get("direction", "NEUTRAL")
    score_100 = w.get("score_100", 0)
    grade = w.get("grade", "C")
    signals = w.get("signals", [])
    factors = w.get("factors", [])
    funding = w.get("funding", 0)
    oi = w.get("oi", 0)
    ls = w.get("ls", 1.0)
    price = w.get("price", 0)
    price_fresh = w.get("price_fresh", "")
    ch24h = w.get("ch24h", 0) or 0
    ch7d = w.get("ch7d", 0) or 0
    rank = w.get("rank")
    vol = w.get("vol", 0) or 0

    if score_100 >= 85:   verdict_e, verdict_word = "🚀", "ОЧЕНЬ СИЛЬНЫЙ"
    elif score_100 >= 65: verdict_e, verdict_word = "✅", "СИЛЬНЫЙ"
    elif score_100 >= 40: verdict_e, verdict_word = "⚠️", "УМЕРЕННЫЙ"
    else:                 verdict_e, verdict_word = "❌", "СЛАБЫЙ"

    # Словесная интерпретация OI-матрицы (цена × OI × funding), как в /market и Институционале
    price_up = ch24h > 0
    oi_up = oi > 0
    if price_up and oi_up:
        oi_line = "🟢 Цена↑ OI↑ — новые лонги, сильный тренд" if funding >= 0 else "🟡 Цена↑ OI↑ — шорт-сквиз возможен"
    elif price_up and not oi_up:
        oi_line = "🟡 Цена↑ OI↓ — шорт-сквиз, может исчерпаться"
    elif not price_up and oi_up:
        oi_line = "🔴 Цена↓ OI↑ — новые шорты, реальное давление"
    else:
        oi_line = "🟡 Цена↓ OI↓ — выход из позиций, движение слабеет"

    dir_emoji = "🟢 ЛОНГ" if direction == "LONG" else ("🔴 ШОРТ" if direction == "SHORT" else "⚪ НЕЙТРАЛЬНО")
    ch24_str = f"+{ch24h:.1f}%" if ch24h >= 0 else f"{ch24h:.1f}%"
    ch7_str  = f"+{ch7d:.1f}%" if ch7d >= 0 else f"{ch7d:.1f}%"
    rank_s = f"#{rank}" if rank else "— (нет данных)"
    vol_s  = f"${vol/1e9:.2f}B" if vol >= 1e9 else (f"${vol/1e6:.1f}M" if vol >= 1e6 else "— (нет данных)")

    SEP = "━━━━━━━━━━━━━━━━━━━━"
    lines_out = [
        # === Блок 1: шапка — раздел + пара + направление + время UTC+3 ===
        f"🐋 *WHALE MONITOR — {sym}/USDT*  {dir_emoji}",
        f"🕐 _{now_utc3()}_",
        SEP,
        "",
        # === Блок 2: вердикт ===
        f"_{verdict_e} {verdict_word}  |  Скор: {score_100}/100  |  Качество: {grade}_",
        "",
        SEP,
        "",
        # === Блок 3: цена и контекст ===
        f"📍 Цена: {fp(price)}  _{price_fresh}_" if price_fresh else f"📍 Цена: {fp(price)}",
        f"📊 24ч: *{ch24_str}*   7д: *{ch7_str}*",
        f"📈 Rank `{rank_s}`   Объём 24ч: `{vol_s}`",
        "",
        SEP,
        "",
        # === Блок 6: факторы ✅/❌/🟡 ===
        "📋 *Факторы:*",
        "",
    ]
    for mark, text in factors:
        lines_out.append(f"  {mark}  {text}")
    lines_out += [
        "",
        f"📈 Funding: {funding:.4f}%",
        f"📊 OI изменение: {oi:.2f}% — {oi_line}",
        f"⚖️ L/S Ratio: {ls:.2f}",
        "",
        SEP,
        "",
        # === Блок 7: расшифровка скора ===
        f"📋 *РАСШИФРОВКА СКОРА  {score_100}/100*",
        "",
        "  Шкала силы:",
        "  0–39   ❌  СЛАБЫЙ",
        "  40–64  ⚠️  УМЕРЕННЫЙ",
        "  65–84  ✅  СИЛЬНЫЙ",
        "  85–100 🚀  ОЧЕНЬ СИЛЬНЫЙ",
        "",
        "  Грейды: A+ ≥85 · A ≥65 · B ≥40 · C <40",
        "",
        SEP,
        f"#{sym}USDT",
    ]
    return "\n".join(lines_out)

async def whale_monitor(bot: Bot):
    fp = lambda v, d=2: (f'${v:,.{d}f}' if v >= 1 else f'${v:.{d}f}')
    """
    Запускается каждые 15 минут.
    Проверяет funding rate + OI + L/S ratio.
    Если обнаружена активность китов — шлёт алерт.
    """
    import os
    from datetime import datetime, timedelta

    owner_id = int(os.getenv("OWNER_CHAT_ID", "7009350191"))
    channel_id_str = os.getenv("CHANNEL_ID", "")

    now = datetime.now(TZ)
    cooldown = timedelta(hours=2)  # не чаще раза в 2 часа на монету

    try:
        # Получаем funding для всех монет разом
        all_funding = _get_funding_rates()
        funding_map = {f["symbol"]: f for f in all_funding}
    except:
        return

    alerts_sent = 0
    for sym in _WHALE_WATCH:
        try:
            # Cooldown
            last = _whale_last_alert.get(sym)
            if last and (now - last) < cooldown:
                continue

            fd = funding_map.get(sym, {})
            funding = fd.get("funding", 0)
            price   = fd.get("price", 0)
            price_fresh = fd.get("price_fresh", "")
            if price <= 0: continue

            oi = _get_oi_change(sym)
            ls = _get_ls_ratio(sym)

            w = _analyze_whale_signal(sym, funding, oi, ls, price, price_fresh,
                                       fd.get("ch24h", 0), fd.get("ch7d", 0), fd.get("rank"), fd.get("vol", 0))
            if not w: continue

            text = _format_whale_alert(w)
            _whale_last_alert[sym] = now

            # Шлём владельцу
            try:
                await bot.send_message(owner_id, text,
                                       parse_mode="Markdown",
                                       disable_web_page_preview=True)
                alerts_sent += 1
            except Exception as e:
                log.error(f"[WHALE] send owner: {e}")

            # Шлём в канал если есть
            if channel_id_str:
                try:
                    cid = int(channel_id_str)
                    await bot.send_message(cid, text,
                                           parse_mode="Markdown",
                                           disable_web_page_preview=True)
                except:
                    pass

            await asyncio.sleep(1)

        except Exception as e:
            log.error(f"[WHALE] {sym}: {e}")

    if alerts_sent:
        log.info(f"[WHALE]  {alerts_sent} алертов отправлено")


AUTO_SCAN_CAP = 30  # макс. кандидатов на направление в прескрине -- см. докстринг send_scheduled

# Диагностика последнего тика send_scheduled для /radar_status -- без этого "0 сигналов"
# неотличимо снаружи от "джоб вообще не запускался" (гейты качества строгие, честный
# 0/0 -- ожидаемый исход, не баг, но это нужно уметь подтвердить, а не гадать).
_last_auto_scan = {"ts": 0.0, "status": "ещё не запускался", "sent_long": 0, "sent_short": 0,
                    "candidates_long": 0, "candidates_short": 0}

async def send_scheduled(bot: Bot):
    """Автосигналы каждые 30 минут (см. scheduler.add_job(send_scheduled, interval, minutes=30)).

    РАНЬШЕ: строил сигналы из сырых %-эвристик (ch1h/ch24h/ch7d пороги) с ФИКСИРОВАННЫМИ
    TP +2/+4/+8% (long) или -2/-4/-8% (short), SL всегда -15%/+15%, R:R всегда захардкожен
    2.5 -- БЕЗ какой-либо проверки реальной структуры или R:R-гейта, и рассылал это ВСЕМ
    подписчикам автоматически. Ровно тот же класс бага, что и в /coin до фикса (см. историю
    бага 4) -- только тут он бил не по одному ручному запросу, а по расписанию на всех
    подписчиков сразу.

    ТЕПЕРЬ: дешёвый прескрин по %-моментуму (как раньше) только СУЖАЕТ список кандидатов
    -- не решает, слать сигнал или нет. Решение принимает real_full_analysis() (тот же
    движок и ТЕ ЖЕ гейты, что и ручные /long и /short: rocket>=60 + грейд A+/A/B, не
    подозрительный объём, не контртренд без свипа, R:R по структуре >= 1:1.5) -- сигнал
    уходит, только если реально прошёл все проверки, entry/SL/TP/R:R честные, от структуры,
    не выдуманные проценты. fa_engine здесь не используется (в отличие от /coin) --
    прогон fa_engine на сотнях монет каждые 30 минут по стоимости неприемлем (см. историю
    бага 3: даже real_full_analysis, который дешевле fa_engine, идёт ~1.5с/монету);
    real_full_analysis -- тот же движок, что уже проверен и используется в ручных
    /long и /short, с идентичными гейтами -- согласованно по всему боту.

    Спот-автосигнал ПОЛНОСТЬЮ УБРАН: его условие (ch90d < -40) физически недостижимо с
    v114 (get_all_coins() перешёл на CoinGecko, которая не отдаёт 90-дневное изменение --
    percent_change_90d всегда 0.0, см. историю бага 2), и для "спот-восстановления" нет
    структурной модели entry/SL/TP (в отличие от лонга/шорта) -- нечего чинить по тому же
    принципу, там никогда не было реального сетапа за фиксированными процентами."""
    _last_auto_scan["ts"] = time.time()
    chat_ids = subscribers.active_chat_ids()
    if not chat_ids:
        _last_auto_scan["status"] = "пропуск: нет подписчиков"
        return

    if _any_manual_scan_active():
        log.info("[AUTO] пропуск итерации -- активен ручной скан (top_long/top_short/top_spot/x100)")
        _last_auto_scan["status"] = "пропуск: активен ручной скан"
        return

    log.info(f"[AUTO] автосигналы {now_utc3()}")

    try:
        coins = get_all_coins()
        if not coins:
            log.error("[AUTO] нет данных по монетам")
            _last_auto_scan["status"] = "ошибка: нет данных по монетам"
            return

        already_sent = set(list(TOP_LONG_SIGNALS.keys()) + list(TOP_SHORT_SIGNALS.keys()))

        # Дешёвый прескрин по моментуму -- НЕ решение о сигнале, только сужает список для
        # дорогих real_full_analysis()-вызовов (см. AUTO_SCAN_CAP).
        long_candidates, short_candidates = [], []
        for coin in coins:
            q = coin["quote"]["USDT"]
            sym = coin["symbol"]
            price = q.get("price", 0) or 0
            ch1h = q.get("percent_change_1h", 0) or 0
            ch24h = q.get("percent_change_24h", 0) or 0
            ch7d = q.get("percent_change_7d", 0) or 0
            vol = q.get("volume_24h", 0) or 0
            mcap = q.get("market_cap", 0) or 0
            vol_ratio = (vol / mcap * 100) if mcap > 0 else 0

            if price <= 0 or vol < 2_000_000 or vol_ratio > 60 or sym in already_sent:
                continue
            if ch1h > 0.5 and ch24h > 2.0 and ch7d > 0:
                long_candidates.append(coin)
            elif ch1h < -0.5 and ch24h < -2.0:
                short_candidates.append(coin)

        sent_long = sent_short = 0
        work = ([(c, True) for c in long_candidates[:AUTO_SCAN_CAP]] +
                [(c, False) for c in short_candidates[:AUTO_SCAN_CAP]])

        for coin, want_long in work:
            if sent_long >= 10 and sent_short >= 10:
                break
            sym = coin["symbol"]
            if sym in already_sent:
                continue
            if want_long and sent_long >= 10:
                continue
            if not want_long and sent_short >= 10:
                continue

            try:
                a = real_full_analysis(coin)
            except Exception as e:
                log.error(f"[AUTO] real_full_analysis {sym}: {e}")
                continue

            is_long = a["is_long"]
            if is_long != want_long:
                continue  # прескрин предполагал одно направление, структура даёт другое -- не наш кандидат
            if a.get("suspicious"):
                continue
            if is_long and a["rsi_4h"] > RSI_EXTREME_LONG:
                continue
            if not is_long and a["rsi_4h"] < RSI_EXTREME_SHORT:
                continue
            grade = _signal_grade(a, is_long)
            if not (a["rocket"] >= 60 and grade in ("A+", "A", "B")):
                continue
            if _counter_trend_blocked(a, "long" if is_long else "short"):
                continue
            if not a.get("rr_gate_pass"):
                continue

            slug = coin.get("slug", sym.lower())
            mode = "long" if is_long else "short"
            text = _build_signal_post(sym, a, {}, mode=mode)
            target_dict = TOP_LONG_SIGNALS if is_long else TOP_SHORT_SIGNALS
            target_dict[sym] = {
                "time": datetime.now(TZ), "entry": a["price"],
                "tp1": a["tp1"], "tp2": a["tp2"], "tp3": a["tp3"],
                "sl": a["sl"], "rr": a["rr"], "status": "active",
            }
            _save_signals()
            # entry_lo/entry_hi -- фактический ценовой порядок (lo<hi), не порядок DCA-входа:
            # для LONG entry1 (первый транш) выше entry3, для SHORT -- наоборот (см. тот же
            # конвеншен в fa_engine.py и в ручных /long, /short).
            e_lo, e_hi = ((a["entry3"], a["entry1"]) if is_long else (a["entry1"], a["entry3"]))
            try:
                signal_journal.log_signal(
                    "TOP_LONG_AUTO" if is_long else "TOP_SHORT_AUTO", sym, mode, a["price"],
                    entry_lo=e_lo, entry_hi=e_hi, sl=a["sl"],
                    tp1=a["tp1"], tp2=a["tp2"], tp3=a["tp3"], rr=a["rr"],
                    rocket_score=a.get("rocket"), ema_stack=a.get("ema_ctx"),
                    sweep=a.get("sweep_4h") or a.get("sweep_1h"),
                    levels_source=a.get("levels_source"), grade=grade,
                    degraded_data=_data_quality_flags())
            except Exception as e:
                log.error(f"[JOURNAL] {'TOP_LONG_AUTO' if is_long else 'TOP_SHORT_AUTO'} {sym}: {e}")
            already_sent.add(sym)
            if is_long:
                sent_long += 1
            else:
                sent_short += 1

            for cid in chat_ids:
                try:
                    await send_coin(bot, cid, sym, slug, a, text)
                except Exception as e:
                    log.error(f"[AUTO {'LONG' if is_long else 'SHORT'}] {sym} -> {cid}: {e}")
            await asyncio.sleep(1.0)

        log.info(f"[AUTO] итог: {sent_long} лонг, {sent_short} шорт "
                 f"(из {len(long_candidates)}/{len(short_candidates)} кандидатов прескрина)")
        _last_auto_scan.update({
            "status": "ok", "sent_long": sent_long, "sent_short": sent_short,
            "candidates_long": len(long_candidates), "candidates_short": len(short_candidates),
        })

    except Exception as e:
        log.error(f"[AUTO] ошибка: {e}")
        _last_auto_scan["status"] = f"ошибка: {e}"


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
            sym_e      = html.escape(sym)
            signal_lbl = "🟢 BUY" if new_dir == 1 else "🔴 SELL"
            prev_lbl   = "SELL" if new_dir == 1 else "BUY"
            price      = st_data["current_price"]
            pct        = st_data["pct_since_signal"]
            last_price = st_data.get("last_signal_price")
            last_time  = st_data.get("last_signal_time")

            time_str = last_time.strftime("%d.%m %H:%M UTC+3") if last_time else ""
            pct_str  = f"+{pct:.2f}%" if pct >= 0 else f"{pct:.2f}%"

            text = (
                f"📡 <b>SUPERTREND — смена направления!</b>\n"
                f"🕐 {now_utc3()}\n\n"
                f"<b>{sym_e}USDT</b>  {prev_lbl} → <b>{signal_lbl}</b>\n\n"
                f"💰 Цена: <code>{fp(price)}</code>\n"
            )
            if last_price:
                text += f"📍 Прошлый сигнал: <code>{fp(last_price)}</code> ({time_str})\n"
                text += f"📊 Изменение с прошлого сигнала: <code>{pct_str}</code>\n"

            text += (
                f"\n"
                f"{'Разворот вверх — рассмотри лонг' if new_dir == 1 else 'Разворот вниз — рассмотри шорт'}\n\n"
                f"⚠️ Риск: не больше 2% депозита | ставь SL\n\n"
                f"#{sym_e}USDT"
            )
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("📊 TradingView", url=tv_link(sym)),
                InlineKeyboardButton("CMC", url=cmc_link(slug)),
            ]])
            for cid in chat_ids:
                try:
                    await bot.send_message(cid, text, parse_mode="HTML", reply_markup=kb)
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
        if True: continue
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
        if True: continue
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
        if True: continue
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
        # BTC price + change via CMC (Binance blocked на Railway)
        btc_q = get_btc_eth_price().get("BTC", {})
        btc_price = btc_q.get("price", 0) or 0
        ch24h = btc_q.get("ch24h", 0) or 0
        result["btc_price"] = btc_price
        result["btc_ch24h"] = round(ch24h, 2)

        # BTC OHLC for trend/EMA (Binance, may be empty on Railway)
        c1h = get_binance_ohlc("BTC", "1h", 50)  or []
        c4h = get_binance_ohlc("BTC", "4h", 100) or []
        c1d = get_binance_ohlc("BTC", "1d", 50)  or []

        cl1h = [c["close"] for c in c1h]
        cl4h = [c["close"] for c in c4h] or [btc_price]
        cl1d = [c["close"] for c in c1d] or [btc_price]

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
            label    = "🟢 BTC сильный бычий тренд (все ТФ)"
            warning  = ""
        elif bear_count >= 2:
            signal   = "bear"
            long_ok  = False
            short_ok = True
            label    = "🔴 BTC сильный медвежий тренд (все ТФ)"
            warning  = "⚠️ BTC в нисходящем тренде — лонги рискованны"
        elif t4h == "bull" or t1d == "neutral_bull":
            signal   = "neutral_bull"
            long_ok  = True
            short_ok = True
            label    = "🟡 BTC умеренно бычий"
            warning  = ""
        elif t4h == "bear" or t1d == "neutral_bear":
            signal   = "neutral_bear"
            long_ok  = True   # не блокируем лонги полностью
            short_ok = True
            label    = "🟡 BTC умеренно медвежий"
            warning  = "⚠️ BTC в слабом нисходящем тренде"
        else:
            signal   = "neutral"
            long_ok  = True
            short_ok = True
            label    = "⚪ BTC нейтрален, чёткого тренда нет"
            warning  = ""

        # Резкое падение BTC >3% за 1ч — блокируем лонги
        if ch1h < -3:
            long_ok  = False
            warning  = f"🔴 BTC упал на {abs(ch1h):.1f}% за 1ч — риск для лонгов"
            label   += f" | резкое падение {ch1h:.1f}%"

        # Резкий рост BTC >5% за 1ч — блокируем шорты
        if ch1h > 5:
            short_ok = False
            label   += f" | резкий рост +{ch1h:.1f}%"

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
    """ICT Killzones -- расписание по UTC+3 (Стамбул).

    ОБНОВЛЕНО 2026-07-11 (владелец): часы Asia/London Open/NY Open заменены на
    METHODOLOGY_CORE.md §8 (источник: "Урок 5. Время и киллзоны.mp4" [1662s-1902s]).
    Раньше это был теневой Патч 01 (см. get_killzone_status_shadow() ниже, ночная
    сессия #2 Блок 1) -- изолированный исторический бэктест (100 символов, ~12 мес,
    см. PATCH_IMPACT.md "Изоляция 01/02") показал улучшение по всем метрикам: win
    rate 53.6%->56.2%, avg R +0.832->+1.001, expectancy +0.815->+0.986,
    profit factor 2.86->3.37, сделок 2864->4796 (больше, не меньше -- более широкие
    часы дают checklist-пункту 4 (fa_engine Блок 5) чаще проходить). Владелец
    одобрил перенос в бой. London Close/NY Close не подтверждены источником,
    оставлены как были (то же ограничение, что и у теневой версии).

    Азиатская сессия: 00:00-08:00 UTC+3 (низкая волатильность, часто рендж/хай-лоу дня)
    Лондон Open:      09:00-12:00 UTC+3 (наибольшие объёмы дня, ищем хай/лоу дня)
    NY Open:          14:00-16:00 UTC+3 (продолжение диапазона, коррекция после Лондона)
    Лондон Close:     18:00-19:00 UTC+3 (не подтверждено источником)
    NY Close:         23:00-00:00 UTC+3 (не подтверждено источником)
    """
    now = datetime.now(TZ)
    h   = now.hour
    m   = now.minute
    hm  = h * 60 + m  # минут с полуночи

    zones = [
        {"name": "🌏 Азиатская сессия", "start": 0*60,  "end": 8*60,  "quality": "B",
         "desc": "Низкая волатильность, часто рендж или хай/лоу дня"},
        {"name": "🇬🇧 Лондон Open",     "start": 9*60,  "end": 12*60, "quality": "A+",
         "desc": "Наибольшие объёмы дня, ищем хай/лоу дня"},
        {"name": "🇺🇸 NY Open",         "start": 14*60, "end": 16*60, "quality": "A",
         "desc": "Продолжение дневного диапазона, часто коррекция после Лондона"},
        {"name": "🇬🇧 Лондон Close",    "start": 18*60, "end": 19*60, "quality": "B",
         "desc": "Закрытие Лондона, не подтверждено источником"},
        {"name": "🇺🇸 NY Close",        "start": 23*60, "end": 24*60, "quality": "C",
         "desc": "Закрытие Нью-Йорка, не подтверждено источником"},
    ]

    active = None
    for z in zones:
        if z["start"] <= hm < z["end"]:
            active = z
            remaining = z["end"] - hm
            active["remaining_min"] = remaining
            break

    # следующая killzone
    next_zone = None
    future = [(z, z["start"] - hm if z["start"] > hm else z["start"] + 24*60 - hm)
              for z in zones]
    future.sort(key=lambda x: x[1])
    if future:
        next_zone = future[0][0].copy()
        next_zone["in_min"] = future[0][1]

    # вне активных killzone
    if active:
        is_good = active["quality"] in ("A+", "A")
    else:
        is_good = False
        # Dead zone -- вне активных killzone
        active = {"name": "⚪ Dead Zone", "quality": "D",
                  "desc": "Вне активных killzone", "remaining_min": 0}

    return {
        "active":    active,
        "next":      next_zone,
        "is_good":   is_good,
        "hour":      h,
        "all_zones": zones,
    }


def get_killzone_status_shadow() -> dict:
    """ПАТЧ 01 (ночная сессия #2, Блок 1 -- теневой контур, см. SHADOW_MODE.md /
    patches/01-killzone-hours/README.md).

    ЧЕСТНО, 2026-07-11: владелец одобрил перенос этого патча в бой (см.
    PATCH_IMPACT.md "Изоляция 01/02") -- get_killzone_status() (live, выше) теперь
    считает ТЕМИ ЖЕ часами, что и эта функция. Сравнение в shadow_engine.compute_
    shadow() (патч "01-killzone-hours": kz_live vs kz_shadow) с этого момента ВСЕГДА
    будет live_good == shadow_good -- сравнивать уже нечего, эта функция стала
    неактивным дубликатом. НЕ удалена намеренно (минимальный дифф промоушена патча,
    см. коммит) -- удаление самой функции и её вызова в shadow_engine.py оставлено
    отдельной задачей уборки, не входит в этот дифф."""
    now = datetime.now(TZ)
    h   = now.hour
    m   = now.minute
    hm  = h * 60 + m

    zones = [
        {"name": "🌏 Азиатская сессия", "start": 0*60,  "end": 8*60,  "quality": "B",
         "desc": "Низкая волатильность, часто рендж или хай/лоу дня"},
        {"name": "🇬🇧 Лондон Open",     "start": 9*60,  "end": 12*60, "quality": "A+",
         "desc": "Наибольшие объёмы дня, ищем хай/лоу дня"},
        {"name": "🇺🇸 NY Open",         "start": 14*60, "end": 16*60, "quality": "A",
         "desc": "Продолжение дневного диапазона, часто коррекция после Лондона"},
        {"name": "🇬🇧 Лондон Close",    "start": 18*60, "end": 19*60, "quality": "B",
         "desc": "Не подтверждено источником, оставлено как в live-версии"},
        {"name": "🇺🇸 NY Close",        "start": 23*60, "end": 24*60, "quality": "C",
         "desc": "Не подтверждено источником, оставлено как в live-версии"},
    ]

    active = None
    for z in zones:
        if z["start"] <= hm < z["end"]:
            active = z
            active["remaining_min"] = z["end"] - hm
            break

    next_zone = None
    future = [(z, z["start"] - hm if z["start"] > hm else z["start"] + 24*60 - hm)
              for z in zones]
    future.sort(key=lambda x: x[1])
    if future:
        next_zone = future[0][0].copy()
        next_zone["in_min"] = future[0][1]

    if active:
        is_good = active["quality"] in ("A+", "A")
    else:
        is_good = False
        active = {"name": "⚪ Dead Zone", "quality": "D",
                  "desc": "Вне активных killzone", "remaining_min": 0}

    return {
        "active":    active,
        "next":      next_zone,
        "is_good":   is_good,
        "hour":      h,
        "all_zones": zones,
    }


def killzone_label() -> str:
    """Текущая ICT-сессия одной строкой, для строк вида '⏰ {label}'"""
    kz = get_killzone_status()
    active = kz["active"]
    nxt    = kz["next"]
    q      = active["quality"]
    q_e    = {"A+": "🟢", "A": "🟢", "B": "🟡", "C": "🟠", "D": "🔴"}.get(q, "⚪")
    rem    = active.get("remaining_min", 0)

    line = f"{q_e} {active['name']}  (качество: {q})"
    if rem:
        line += f"  осталось {rem} мин"
    if nxt:
        line += f"\nСледующая: {nxt['name']} через {nxt['in_min']} мин"
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
    chat_ids = subscribers.active_chat_ids()
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
        "price": 0.0, "price_fresh": "",
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
        from live_prices import resolve_price
        price, price_fresh = resolve_price(symbol, closes_4h[-1])  # live WS-цена, CoinGecko-фоллбек с пометкой

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
            # Funding rate (CoinGecko — fapi.binance.com заблокирован на Railway)
            if symbol in _fetch_coingecko_oi_map():
                funding_rate = _get_funding_pct(symbol)
            # OI (CoinGecko — fapi.binance.com заблокирован на Railway)
            if _get_oi_usd(symbol) > 0:
                oi_change = _get_oi_change(symbol)
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
            "price": price, "price_fresh": price_fresh,
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
        "price": 0.0, "price_fresh": "",
        "supertrend_bull": None,
        "atr": 0.0,
        "support": 0.0, "resistance": 0.0,
        "trend_4h": "neutral",   # bullish / bearish / neutral
        "candles_4h": [], "candles_1h": [], "candles_1d": [],
    }
    try:
        c4h = get_binance_ohlc(symbol, "4h", 200)
        if not c4h or len(c4h) < 50:
            return result

        closes_4h = [c["close"] for c in c4h]
        highs_4h  = [c["high"]  for c in c4h]
        lows_4h   = [c["low"]   for c in c4h]
        vols_4h   = [c["vol"]   for c in c4h]
        from live_prices import resolve_price
        price, price_fresh = resolve_price(symbol, closes_4h[-1])  # live WS-цена, CoinGecko-фоллбек с пометкой

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

        for i in range(5, len(c4h) - 5):
            c = c4h[i]
            #
            body = abs(c["close"] - c["open"])
            rng  = c["high"] - c["low"]
            if rng == 0: continue

            #     Demand  ()
            if (c["close"] > c["open"]                         #
                    and body / rng > 0.6                       #
                    and body > sum(abs(c4h[j]["close"] - c4h[j]["open"])
                                   for j in range(i-3, i)) / 3):  #
                demand_zones.append((c["low"], c["open"]))

            #     Supply  ()
            if (c["close"] < c["open"]
                    and body / rng > 0.6
                    and body > sum(abs(c4h[j]["close"] - c4h[j]["open"])
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
            "price":     price, "price_fresh": price_fresh,
            "supertrend_bull": st_bull,
            "atr":       round(atr_v, 8),
            "support":   round(support, 8),
            "resistance": round(resistance, 8),
            "trend_4h":  trend_4h,
            "candles_4h": c4h,
            "candles_1h": c1h,
            "candles_1d": c1d,
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

    # EMA-контекст (1h/4h) + детектор свипа ликвидности (SFP) -- из уже полученных в
    # real_ta() OHLC-серий, без новых API-вызовов.
    if ta["ok"]:
        ema_ctx = ta_extra.ema_context(ta.get("candles_1h", []), ta.get("candles_4h", []))
        sweep_1h = ta_extra.detect_sweep(ta.get("candles_1h", []))
        sweep_4h = ta_extra.detect_sweep(ta.get("candles_4h", []))
    else:
        ema_ctx, sweep_1h, sweep_4h = None, None, None

    if ta["ok"] and ta["price"] > 0:
        price, price_fresh = ta["price"], ta.get("price_fresh", "")
    else:
        from live_prices import resolve_price
        price, price_fresh = resolve_price(sym, q.get("price", 0) or 0)

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

    # EMA-стек + свежий свип ликвидности -- новые факторы, чисто additive (существующие
    # веса выше не трогали), см. ta_extra.py.
    _direction = "long" if is_long else "short"
    rocket = max(0, min(100, rocket
                         + ta_extra.ema_stack_score_delta(ema_ctx, _direction)
                         + ta_extra.sweep_score_delta(sweep_1h, sweep_4h, _direction)))

    # Вход/SL/TP от реальной структуры (find_sr_zones + build_trade_from_structure,
    # ta_extra.py) вместо фиксированных процентов от цены -- см. модульный докстринг
    # ta_extra.py. Зоны строятся из уже полученных в real_ta() свечей (1h/4h/1d),
    # новых API-вызовов не добавляет (заменяет собой прежний отдельный
    # get_binance_ohlc(sym, "4h", 100) для swing-уровней).
    import math

    def smart_round(val):
        if val == 0: return 0
        magnitude = math.floor(math.log10(abs(val))) if val > 0 else 0
        precision = max(8, -magnitude + 3)
        return round(val, precision)

    direction = "long" if is_long else "short"
    atr_min = atr if atr > 0 else price * 0.02

    zones = ta_extra.find_sr_zones(ta.get("candles_1h", []), ta.get("candles_4h", []),
                                    ta.get("candles_1d", []), price, ema_ctx=ema_ctx) if ta["ok"] else {"above": [], "below": []}
    trade = ta_extra.build_trade_from_structure(direction, price, zones)

    if trade:
        levels_source = "structure"
        entry1, entry2, entry3 = smart_round(trade["entry1"]), smart_round(trade["entry2"]), smart_round(trade["entry3"])
        sl  = smart_round(trade["sl"])
        tp1 = smart_round(trade["tp1"])
        tp2 = smart_round(trade["tp2"])
        tp3 = smart_round(trade["tp3"])
        rr_tp1, rr_tp2, rr_tp3 = trade["rr_tp1"], trade["rr_tp2"], trade["rr_tp3"]
        rr_gate_pass = trade["rr_gate_pass"]
        swing = smart_round(trade["entry_zone"]["mid"])
        touches = trade["entry_zone"]["touches"]
        sources = ", ".join(trade["entry_zone"]["sources"])
        sl_source  = f"S/R зона ({sources}, {touches} касан.)"
        tp1_source = "S/R зона" if trade["tp_zones"] else "Fib расширение"
        tp2_source = tp1_source
        tp3_source = tp1_source
    else:
        # Нет ни одной зоны для входа (совсем неликвидная монета без чёткой структуры) --
        # минимальный ATR-фоллбэк, помечен как fallback, не проходит R:R-гейт (недостаточно
        # обосновано структурой, чтобы показывать как готовый сигнал).
        levels_source = "fallback_atr"
        rr_gate_pass = False
        if is_long:
            sl  = smart_round(price - atr_min * 1.5)
            tp1 = smart_round(price + atr_min * 1.0)
            tp2 = smart_round(price + atr_min * 1.618)
            tp3 = smart_round(price + atr_min * 2.618)
        else:
            sl  = smart_round(price + atr_min * 1.5)
            tp1 = smart_round(price - atr_min * 1.0)
            tp2 = smart_round(price - atr_min * 1.618)
            tp3 = smart_round(price - atr_min * 2.618)
        entry1 = entry2 = entry3 = smart_round(price)
        risk = abs(price - sl) or 1e-9
        rr_tp1 = round(abs(tp1 - price) / risk, 2)
        rr_tp2 = round(abs(tp2 - price) / risk, 2)
        rr_tp3 = round(abs(tp3 - price) / risk, 2)
        swing = entry1
        sl_source = tp1_source = tp2_source = tp3_source = "ATR-фоллбэк (нет структуры)"

    rr = rr_tp1  # R:R-гейт и общий "заголовочный" R:R теперь оба по TP1 (см. ТЗ)

    if rocket >= 80:   rocket_label = " ROCKET"
    elif rocket >= 70: rocket_label = " "
    elif rocket >= 60: rocket_label = " "
    elif rocket >= 50: rocket_label = " "
    elif rocket >= 40: rocket_label = " "
    else:              rocket_label = " "

    # Ярлыки ниже -- НЕ структурная SMC-детекция (не BOS/OB/FVG/Sweep по свечам), а
    # пороги % изменения цены/объёма (см. вычисление smc_* выше в этой функции).
    # Названия отражают это честно (SMC_COVERAGE.md §3) -- реальная структурная
    # детекция живёт в ta_extra.py/pro_analysis(), сюда не переносилась (изменение
    # отображения, не формул/порогов).
    smc_factors = []
    if smc_bos_bull:     smc_factors.append("Имп. 7д↑/30д↓")
    if smc_bos_bear:     smc_factors.append("Имп. 7д↓/30д↑")
    if smc_ob_accum:     smc_factors.append("Штиль объёма")
    if smc_liq_sweep:    smc_factors.append("Резкий имп. 1ч")
    if smc_smart_accum:  smc_factors.append("Откат в аптренде")
    if smc_smart_dist:   smc_factors.append("Сильный имп. 24ч")
    if smc_fvg_bull:     smc_factors.append("Имп. 1ч+24ч ↑")
    if smc_fvg_bear:     smc_factors.append("Имп. 1ч+24ч ↓")
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
        "price": price, "price_fresh": price_fresh, "tp1": tp1, "tp2": tp2, "tp3": tp3,
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
        "ema_ctx": ema_ctx, "sweep_1h": sweep_1h, "sweep_4h": sweep_4h,
        "entry1": entry1, "entry2": entry2, "entry3": entry3,
        "rr_tp1": rr_tp1, "rr_tp2": rr_tp2, "rr_tp3": rr_tp3,
        "rr_gate_pass": rr_gate_pass, "levels_source": levels_source, "zones": zones,
    }


# 
#  :  ,  ,  ,  
# 

#     
TOP_LONG_SIGNALS:  dict = {}
TOP_SHORT_SIGNALS: dict = {}
TOP_SHORT_SIGNALS["BTC"] = {"time": None, "entry": 61700, "buy_zone_lo": 61500, "buy_zone_hi": 61930, "sl": 62200, "sell_target": 58073, "status": "watching", "tp1": 59200, "tp2": 58073, "note": "Шорт $61500-61930. SL $62200. Условие: цена ниже $61930"}
TOP_SHORT_SIGNALS["SOL"] = {"time": None, "entry": 74.50, "buy_zone_lo": 73.50, "buy_zone_hi": 74.92, "sl": 75.50, "sell_target": 62.00, "status": "watching", "tp1": 68.00, "tp2": 65.00, "tp3": 62.00, "note": "Шорт $73.5-74.92. SL $75.50. Хай $74.92 не пробивать"}
# 2026-07-11: "status" ниже вытеснена дневной разметкой владельца (level_watch.py,
# journal/watch_zones.json) -- запись НЕ удалена (append-only/история, владелец,
# задача "ETH level-watch"). "watching" нигде не проверяется как условие в коде
# (grep подтвердил), безопасно заменить значение. BTC/SOL/CAKE/AAVE не тронуты.
TOP_SHORT_SIGNALS["ETH"] = {"time": None, "entry": 1610, "buy_zone_lo": 1600, "buy_zone_hi": 1620, "sl": 1645, "sell_target": 1504, "status": "superseded_2026-07-11", "tp1": 1537, "tp2": 1504, "note": "4H имбаланс $1569-1620. Условие: пробой $1565. SL $1645"}

TOP_SPOT_SIGNALS:  dict = {}
TOP_SPOT_SIGNALS["CAKE"] = {"time": None, "entry": 1.1829, "buy_zone_lo": 1.1533, "buy_zone_hi": 1.1829, "sl": 1.12, "sell_target": 1.45, "status": "watching", "tp1": 1.30, "tp2": 1.40, "tp3": 1.45, "note": "DCA: 50%@1.1829 / 30%@1.168 / 20%@1.1533. SL $1.12"}

TOP_SPOT_SIGNALS["AAVE"] = {"time": None, "entry": 63.57, "buy_zone_lo": 60.43, "buy_zone_hi": 63.57, "sl": 59.00, "sell_target": 109.70, "status": "watching", "tp1": 73.27, "tp2": 76.55, "tp3": 82.06, "note": "DCA: 50%@63.57 / 30%@62.00 / 20%@60.43. SL $59. TP4 $109.70"}


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

def _signal_grade(a: dict, is_long: bool) -> str:
    """5-факторный грейд (A+/A/B/C) -- та же логика, что показывается в карточке
    (_build_signal_post), вынесена сюда, чтобы сканы могли скрывать C-грейд ДО рендера
    карточки, а не просто помечать его предупреждением."""
    rsi_4h = a.get("rsi_4h", 50)
    macd_bull = a.get("macd_bullish", False)
    macd_bear = a.get("macd_bearish", False)
    trend_4h = a.get("trend_4h", "neutral")
    st_label = (a.get("st_label") or "").upper()
    above_ema200 = a.get("above_ema200", False)
    above_ema50 = a.get("above_ema50", False)

    n_ok = 0
    if is_long:
        if rsi_4h < 50: n_ok += 1
        if macd_bull: n_ok += 1
        if trend_4h == "bullish": n_ok += 1
        if "UP" in st_label or "BULL" in st_label: n_ok += 1
        if above_ema200 or above_ema50: n_ok += 1
    else:
        if rsi_4h > 55: n_ok += 1
        if macd_bear: n_ok += 1
        if trend_4h == "bearish": n_ok += 1
        if "DOWN" in st_label or "BEAR" in st_label: n_ok += 1
        if not above_ema200: n_ok += 1

    if n_ok >= 5:   return "A+"
    if n_ok >= 4:   return "A"
    if n_ok >= 3:   return "B"
    return "C"


def _counter_trend_blocked(a: dict, direction: str) -> bool:
    """Блокирует контртрендовый сигнал: шорт против сильного бычьего технического фона
    (Тренд 4H восходящий + Supertrend BUY + бычий EMA-стек), лонг -- зеркально против
    медвежьего. Исключение: свежий ПОДТВЕРЖДЁННЫЙ объёмом свип в направлении сигнала --
    манипуляция достаточное обоснование для контртренда, обычный сетап без неё -- нет."""
    ema_ctx = a.get("ema_ctx") or {}
    tf_4h = ema_ctx.get("tf_4h") or {}
    stack = tf_4h.get("stack")
    trend_4h = a.get("trend_4h")
    st_label = (a.get("st_label") or "").upper()
    st_bull = "UP" in st_label or "BULL" in st_label
    st_bear = "DOWN" in st_label or "BEAR" in st_label

    def _fresh_confirmed_sweep(kind: str) -> bool:
        for sweep in (a.get("sweep_1h"), a.get("sweep_4h")):
            if (sweep and sweep.get("type") == kind
                    and sweep.get("bars_ago", 999) <= ta_extra.FRESH_SWEEP_BARS
                    and sweep.get("volume_confirmed") is True):
                return True
        return False

    if direction == "short":
        strong_bull = (trend_4h == "bullish" and st_bull and stack == "бычий")
        if not strong_bull:
            return False
        return not _fresh_confirmed_sweep("sweep_high")
    else:
        strong_bear = (trend_4h == "bearish" and st_bear and stack == "медвежий")
        if not strong_bear:
            return False
        return not _fresh_confirmed_sweep("sweep_low")


_JOURNAL_FOOTER_SOURCES = {
    "long":  ["TOP_LONG", "TOP_LONG_AUTO"],
    "short": ["TOP_SHORT", "TOP_SHORT_AUTO"],
    "spot":  ["TOP_SPOT", "TOP_SPOT_AUTO"],
    "x100":  ["X100"],
}


def _journal_footer_line(mode: str) -> str:
    """'📒 Journal: N закрытых сигналов этого типа, win rate X%' -- статистика по уже
    закрытым (с исходом) сигналам того же типа (ручные + авто-сканы вместе). Если данных
    меньше 10 -- честное предупреждение вместо возможно случайного числа."""
    sources = _JOURNAL_FOOTER_SOURCES.get(mode, [])
    closed = 0
    wins = 0
    for src in sources:
        st = signal_journal.get_stats_for_source(src)
        closed += st["closed"]
        wins += st["wins"]
    if closed < 10:
        return "📒 Journal: статистика копится, торговать с осторожностью"
    win_rate = round(wins / closed * 100, 1)
    return f"📒 Journal: {closed} закрытых сигналов этого типа, win rate {win_rate}%"


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
                       mode: str = "long", section: str = "") -> str:
    is_long = mode in ("long", "spot")
    price   = a["price"]
    price_fresh = a.get("price_fresh", "")
    r       = a["rocket"]
    rsi_4h  = a["rsi_4h"]
    trend_4h = a.get("trend_4h", "neutral")
    if not section:
        section = {"long": "ТОП ЛОНГ", "short": "ТОП ШОРТ", "spot": "ТОП СПОТ"}.get(mode, "СИГНАЛ")

    def pct(t):
        d = (t - price) / price * 100
        v = d if is_long else -d
        return f"+{v:.1f}%" if v >= 0 else f"{v:.1f}%"

    def sl_pct(sl):
        d = abs(sl - price) / price * 100
        return f"-{d:.1f}%"

    def ri(v):
        if v < 30: return "\U0001f7e2"   # green circle = oversold
        if v > 70: return "\U0001f534"   # red circle = overbought
        return "\U0001f7e1"              # yellow

    side_e = "\U0001f7e2" if is_long else "\U0001f534"
    side_t = "ЛОНГ" if mode == "long" else ("\U00002b50 СПОТ" if mode == "spot" else "ШОРТ")

    # --- Score label & grade ---
    if r >= 90:
        score_label = "ОТЛИЧНЫЙ сигнал \U0001f680"
        score_rec   = "\u2705 Максимальный вход 2-3% депозита"
        grade_name  = "A+"
        grade_factors_needed = 5
    elif r >= 75:
        score_label = "СИЛЬНЫЙ сигнал \U0001f4aa"
        score_rec   = "\u2705 Приоритетный вход 1-2% депозита"
        grade_name  = "A" if r >= 80 else "B"
        grade_factors_needed = 4 if r >= 80 else 3
    elif r >= 60:
        score_label = "ХОРОШИЙ сигнал \u2705"
        score_rec   = "\u2705 Можно входить с риском 1-2%"
        grade_name  = "B"
        grade_factors_needed = 3
    elif r >= 41:
        score_label = "УМЕРЕННЫЙ сигнал \u26a0\ufe0f"
        score_rec   = "\u26a0\ufe0f Малый риск, осторожно"
        grade_name  = "C"
        grade_factors_needed = 2
    else:
        score_label = "СЛАБЫЙ сигнал \u274c"
        score_rec   = "\u274c Пропустить"
        grade_name  = "C"
        grade_factors_needed = 1

    # --- Count quality factors ---
    ch24h = a.get("ch24h", 0)
    ch30d = a.get("ch30d", 0) if hasattr(a, "get") else 0
    macd_bull = a.get("macd_bullish", False)
    macd_bear = a.get("macd_bearish", False)
    above_ema200 = a.get("above_ema200", False)
    above_ema50  = a.get("above_ema50", False)
    above_ema20  = a.get("above_ema20", False)
    smc_factors  = [str(f) for f in a.get("smc_factors", []) if "BB" not in str(f) and "MACD" not in str(f)]
    st_label     = a.get("st_label", "")

    factors_ok  = []
    factors_bad = []

    if is_long:
        if rsi_4h < 50:
            factors_ok.append("RSI в зоне покупок")
        else:
            factors_bad.append("RSI перегрет")
        if macd_bull:
            factors_ok.append("MACD бычий")
        else:
            factors_bad.append("MACD нейтральный")
        if trend_4h == "bullish":
            factors_ok.append("Тренд 4H восходящий")
        else:
            factors_bad.append("Тренд не подтверждён")
        if "UP" in st_label.upper() or "BULL" in st_label.upper():
            factors_ok.append("Supertrend бычий")
        else:
            factors_bad.append("Supertrend нейтральный")
        if above_ema200 or above_ema50:
            factors_ok.append("Выше EMA")
        else:
            factors_bad.append("Ниже EMA")
    else:
        if rsi_4h > 55:
            factors_ok.append("RSI перегрет — шорт")
        else:
            factors_bad.append("RSI не подтверждён")
        if macd_bear:
            factors_ok.append("MACD медвежий")
        else:
            factors_bad.append("MACD нейтральный")
        if trend_4h == "bearish":
            factors_ok.append("Тренд 4H нисходящий")
        else:
            factors_bad.append("Тренд не подтверждён")
        if "DOWN" in st_label.upper() or "BEAR" in st_label.upper():
            factors_ok.append("Supertrend медвежий")
        else:
            factors_bad.append("Supertrend нейтральный")
        if not above_ema200:
            factors_ok.append("Ниже EMA200 — давление")
        else:
            factors_bad.append("Выше EMA200")

    # Grade by factor count -- via shared helper so scan-time gating (_signal_grade) and
    # card render never drift apart.
    n_ok = len(factors_ok)
    grade_name = _signal_grade(a, is_long)

    # --- Support / Resistance from swing ---
    swing = a.get("swing", price)
    sl    = a["sl"]
    tp1, tp2, tp3 = a["tp1"], a["tp2"], a["tp3"]
    rr    = a.get("rr", 0)

    if is_long:
        sup1 = round(sl * 0.99, 6)
        sup2 = round(sl * 0.96, 6)
        res1 = tp1
        res2 = tp3
    else:
        sup1 = tp1
        sup2 = tp3
        res1 = round(sl * 1.01, 6)
        res2 = round(sl * 1.04, 6)

    # --- EMA string ---
    ema_parts = []
    if above_ema200: ema_parts.append("EMA200 \u2705")
    if above_ema50:  ema_parts.append("EMA50 \u2705")
    if above_ema20:  ema_parts.append("EMA20 \u2705")
    if not ema_parts: ema_parts = ["Ниже EMA \u274c"]
    ema_str = " | ".join(ema_parts)

    # --- MACD / Trend / ST ---
    macd_str = "\U0001f7e2 Бычий" if macd_bull else ("\U0001f534 Медвежий" if macd_bear else "\U0001f7e1 Нейтральный")
    trend_str = {"bullish": "\U0001f7e2 Восходящий", "bearish": "\U0001f534 Нисходящий", "neutral": "\U0001f7e1 Боковой"}.get(trend_4h, "\U0001f7e1 Нейтральный")
    st_str = st_label if st_label else "—"

    # SMC
    smc_str = " | ".join(smc_factors[:3]) if smc_factors else "—"

    # Vol
    vol = a.get("vol", 0) or 0
    vol_s = f"${vol/1e9:.2f}B" if vol >= 1e9 else (f"${vol/1e6:.1f}M" if vol >= 1e6 else (f"${vol/1e3:.0f}K" if vol > 0 else "— (нет данных)"))
    rank_v = a.get("rank")
    rank_s = f"#{rank_v}" if rank_v else "— (нет данных)"

    # Нейтральный (🟡) фактор — объём, чисто информационный, не входит в грейд
    vol_ratio_a = a.get("vol_ratio")
    if vol_ratio_a is None and a.get("mcap"):
        vol_ratio_a = (vol / a["mcap"] * 100) if a["mcap"] else None
    if vol_ratio_a is None:
        vol_note = ("\U0001f7e1", "Объём: нет данных")
    elif 2 <= vol_ratio_a <= 30:
        vol_note = ("✅", f"Объём здоровый ({vol_ratio_a:.0f}% mcap)")
    elif vol_ratio_a > 60:
        vol_note = ("\U0001f7e1", f"Объём аномально высокий ({vol_ratio_a:.0f}% mcap) — осторожно")
    else:
        vol_note = ("\U0001f7e1", f"Объём слабый ({vol_ratio_a:.0f}% mcap)")

    # ch info
    ch24_str = f"+{ch24h:.1f}%" if ch24h >= 0 else f"{ch24h:.1f}%"
    ch7d_v  = a.get("ch7d", 0) or 0
    ch7_str = f"+{ch7d_v:.1f}%" if ch7d_v >= 0 else f"{ch7d_v:.1f}%"

    # --- Score emoji ---
    if r >= 90:   score_e = "\U0001f680"; score_word = "ОТЛИЧНЫЙ"
    elif r >= 75: score_e = "\U0001f4aa"; score_word = "СИЛЬНЫЙ"
    elif r >= 60: score_e = "\u2705";     score_word = "ХОРОШИЙ"
    elif r >= 41: score_e = "\u26a0\ufe0f"; score_word = "УМЕРЕННЫЙ"
    else:         score_e = "\u274c";     score_word = "СЛАБЫЙ"

    SEP = "\u2796\u2796\u2796\u2796\u2796\u2796\u2796\u2796\u2796\u2796"

    # ch30d
    ch30d_v = a.get("ch30d", 0) or 0
    ch30_str = f"+{ch30d_v:.1f}%" if ch30d_v >= 0 else f"{ch30d_v:.1f}%"

    # --- Риск на 1/2/3% депозита ---
    risk_lines = []
    entry_ref = price if mode != "spot" else (tp1 + tp2 + tp3) / 3  # DCA-средняя для спота
    sl_dist_pct = abs(entry_ref - sl) / entry_ref * 100 if entry_ref else 0
    if sl_dist_pct > 0:
        for dep_risk in (1, 2, 3):
            size_pct = dep_risk / sl_dist_pct * 100
            risk_lines.append(f"  {dep_risk}% депозита → размер позиции ~{size_pct:.0f}% от депозита")
    else:
        risk_lines.append("  — (нет данных для расчёта риска)")

    # === Блок 1: шапка — раздел + пара + направление + время UTC+3 ===
    lines = [
        f"*{section}  —  {symbol}/USDT*  {side_e}  *{side_t}*",
        f"\U0001f550 _{now_utc3()}_",
        # === Блок 2: вердикт — скор + качество ===
        f"_{score_e} {score_word}  |  Скор: {r}/100  |  Качество: {grade_name}_",
        SEP,
        "",
        # === Блок 3: цена и контекст ===
        f"\U0001f4cd  *Цена сейчас:*  `{fp(price)}`  _{price_fresh}_" if price_fresh else f"\U0001f4cd  *Цена сейчас:*  `{fp(price)}`",
        f"\U0001f4ca  24ч: *{ch24_str}*   7д: *{ch7_str}*   30д: *{ch30_str}*",
        f"\U0001f4c8  Rank `{rank_s}`   Объём 24ч: `{vol_s}`",
        "",
        SEP,
        "",
        # === Блок 4: зоны поддержки/сопротивления ===
        "\U0001f7e2  *ЗОНЫ*",
        "",
        f"\U0001f7e2  Поддержка:      `{fp(sup1)}`  /  `{fp(sup2)}`",
        f"\U0001f534  Сопротивление: `{fp(res1)}`  /  `{fp(res2)}`",
        "",
        SEP,
        "",
        # === Блок 5: сделка ===
        "\U0001f4bc  *СДЕЛКА*",
        "",
    ]

    if mode == "spot":
        lines += [
            f"`Вход 1 (40%):  {fp(tp1)}`",
            f"`Вход 2 (40%):  {fp(tp2)}`",
            f"`Вход 3 (20%):  {fp(tp3)}`",
            f"`SL:            {fp(sl)}`   *({sl_pct(sl)})*",
            f"`R:R    1:{rr:.1f}`",
        ]
    else:
        rr_tp1 = a.get("rr_tp1", rr)
        rr_tp2 = a.get("rr_tp2", rr)
        rr_tp3 = a.get("rr_tp3", rr)
        entry1 = a.get("entry1", price)
        lines += [
            f"`Вход:  {fp(entry1)}`",
            f"`TP1:   {fp(tp1)}`   *({pct(tp1)})*  R:R 1:{rr_tp1:.1f}",
            f"`TP2:   {fp(tp2)}`   *({pct(tp2)})*  R:R 1:{rr_tp2:.1f}",
            f"`TP3:   {fp(tp3)}`   *({pct(tp3)})*  R:R 1:{rr_tp3:.1f}",
            f"`SL:    {fp(sl)}`   *({sl_pct(sl)})*",
        ]
        if a.get("levels_source"):
            lines.append(f"`Источник уровней: {a['levels_source']}`")
        # POI-зона входа с TF-метками -- раньше эта информация (какие ТФ и сколько
        # касаний подтвердили зону, есть ли K-LVL) была только в /coin через narrative.py,
        # хотя sl_source уже содержит готовую строку (например "S/R зона (1h, 4h, 3
        # касан.)") -- просто не выводилась в обычную сигнальную карточку (баг №5, п.6).
        if a.get("sl_source") and "S/R зона" in a["sl_source"]:
            lines.append(f"`POI входа: {a['sl_source']}`")

    lines += [
        "",
        "⚠️  *Риск на депозит:*",
    ] + risk_lines + [
        "",
        SEP,
        "",
        # === Блок 6: факторы ===
        "\U0001f4c8  *ТЕХНИЧЕСКИЙ АНАЛИЗ*",
        "",
        f"  RSI 4H:        {ri(rsi_4h)}  `{rsi_4h:.0f}`",
        f"  MACD:          {macd_str}",
        f"  Тренд 4H:     {trend_str}",
        f"  Supertrend:   `{st_str}`",
        f"  EMA:           {ema_str}",
        f"  {ta_extra.format_ema_stack_line(a.get('ema_ctx'))}",
    ]

    if smc_factors:
        lines.append(f"  SMC:           `{smc_str}`")

    _sweep_line = ta_extra.format_sweep_line(a.get("sweep_1h"), a.get("sweep_4h"), price_fmt=fp)
    if _sweep_line:
        lines.append(f"  {_sweep_line}")

    lines += [
        f"  {vol_note[0]}  {vol_note[1]}",
        "",
        SEP,
        "",
        # === Блок 7: расшифровка скора ===
        f"\U0001f4cb  *РАСШИФРОВКА СКОРА  {r}/100*",
        "",
        f"  {score_e}  {score_word} сигнал",
        "",
        "  Шкала силы:",
        "  0–40    ❌  СЛАБЫЙ — пропустить",
        "  41–59  ⚠️  УМЕРЕННЫЙ — малый риск",
        "  60–74  ✅  ХОРОШИЙ — входить",
        "  75–89  \U0001f4aa  СИЛЬНЫЙ — приоритет",
        "  90–100 \U0001f680  ОТЛИЧНЫЙ — максимум",
        "",
        f"  *Качество {grade_name}* — {n_ok} из 5 факторов:",
        "",
    ]

    for f_ok in factors_ok[:5]:
        lines.append(f"  ✅  {f_ok}")
    for f_bad in factors_bad[:max(0, 5 - len(factors_ok))]:
        lines.append(f"  ❌  {f_bad}")

    lines += [
        "",
        "  Грейды:",
        "  A+ = все 5 факторов",
        "  A  = 4–5 факторов",
        "  B  = 3 фактора",
        "  C  = 1–2 фактора — осторожно",
        "",
        _journal_footer_line(mode),
        "",
        # === Блок 8: разделитель + хэштег (кнопки — отдельно, через send_coin/_signal_kb) ===
        SEP,
        f"#{symbol}USDT",
    ]

    return "\n".join(lines)


# BACKWARD COMPAT alias
_old_build_signal_post = _build_signal_post

# ─────────────────────────────────────────────
# 🚀 x100 SCANNER
# ─────────────────────────────────────────────
async def cmd_x100_scanner(update, ctx):
    if _scan_busy["x100"]:
        try:
            await update.message.reply_text("⏳ Скан уже выполняется, подожди")
        except Exception:
            pass
        return
    _scan_busy["x100"] = True
    try:
        await _cmd_x100_scanner_body(update, ctx)
    finally:
        _scan_busy["x100"] = False


async def _cmd_x100_scanner_body(update, ctx):
    msg = update.message
    try:
        await msg.reply_text("🚀 Сканирую топ-500 монет... ~15 сек", parse_mode="Markdown")
    except:
        pass
    try:
        def _scan_x100_sync():
            """Вся тяжёлая синхронная работа (CoinGecko-фетч + до 15 * 2 OHLC-запросов) --
            выполняется в run_in_executor, чтобы не морозить event loop бота (см.
            _scan_busy). Внутри нет ни одного await -- безопасно для потока.
            ROADMAP: раньше делал собственный прямой CMC-запрос в обход get_all_coins() --
            если ключ был мёртв, x100 тихо возвращал пустой список без объяснения.
            Теперь единый источник (CoinGecko первичный, CMC фоллбек только внутри
            get_all_coins(), см. её докстринг) -- 2026-07-10."""
            all_coins = get_all_coins()
            stables = {"USDT","USDC","BUSD","DAI","TUSD","FDUSD","USDP","FRAX","LUSD","GUSD","USDD","PYUSD","WBTC","WETH","CBBTC"}
            candidates = []
            for c in all_coins:
                sym = c.get("symbol", "")
                if sym in stables: continue
                q = c.get("quote", {}).get("USDT", {})
                price = q.get("price", 0) or 0
                mcap  = q.get("market_cap", 0) or 0
                vol24 = q.get("volume_24h", 0) or 0
                ch24  = q.get("percent_change_24h", 0) or 0
                ch7d  = q.get("percent_change_7d", 0) or 0
                ch30d = q.get("percent_change_30d", 0) or 0
                slug  = c.get("slug", sym.lower())
                name  = c.get("name", sym)
                if price <= 0 or mcap <= 0: continue
                score = 0
                reasons = []
                if mcap < 10_000_000:    score += 4; reasons.append("🔥 Микрокап <$10M")
                elif mcap < 50_000_000:  score += 3; reasons.append("💎 Кап <$50M")
                elif mcap < 200_000_000: score += 2; reasons.append("📊 Кап <$200M")
                elif mcap < 500_000_000: score += 1; reasons.append("Кап <$500M")
                vol_ratio = vol24 / mcap if mcap > 0 else 0
                if vol_ratio > 1.0:   score += 3; reasons.append("⚡ Объём >MCap")
                elif vol_ratio > 0.5: score += 2; reasons.append("📈 Объём >50% MCap")
                elif vol_ratio > 0.2: score += 1; reasons.append("Объём >20% MCap")
                if ch24 > 20:   score += 3; reasons.append(f"🚀 +{ch24:.0f}% 24ч")
                elif ch24 > 10: score += 2; reasons.append(f"📈 +{ch24:.0f}% 24ч")
                elif ch24 > 5:  score += 1; reasons.append(f"+{ch24:.0f}% 24ч")
                elif ch24 < -15: score -= 1
                if ch7d > 30:   score += 2; reasons.append(f"📊 +{ch7d:.0f}% 7д")
                elif ch7d > 10: score += 1; reasons.append(f"+{ch7d:.0f}% 7д")
                if ch30d > 50 and ch7d < -10: score += 2; reasons.append("♻️ Откат после роста")
                if 0 < price < 0.01: score += 1; reasons.append("💰 <$0.01")
                elif price < 0.1:    score += 1; reasons.append("💰 <$0.10")
                # Ужесточение качества: x100 не считает RSI/MACD/тренд/Supertrend (нет OHLC
                # до этой точки), поэтому здесь используется его СОБСТВЕННАЯ шкала (0-12) --
                # порог поднят с >=5 (показывал и 📈-тир) до >=7, оставляя только 💎/🔥-тир,
                # эквивалент "скор>=60 и грейд A/B" для этого типа сигнала.
                if score >= 7 and mcap < 500_000_000:
                    def fmt_mcap(m):
                        if m >= 1e9: return f"${m/1e9:.2f}B"
                        if m >= 1e6: return f"${m/1e6:.1f}M"
                        return f"${m/1e3:.0f}K"
                    def fmt_p2(v):
                        if v >= 1000: return f"${v:,.0f}"
                        if v >= 1:    return f"${v:,.3f}"
                        if v >= 0.01: return f"${v:.4f}"
                        return f"${v:.6f}"
                    candidates.append({
                        "sym": sym, "name": name, "slug": slug,
                        "price": fmt_p2(price), "mcap": fmt_mcap(mcap),
                        "vol_ratio": round(vol_ratio * 100, 1),
                        "ch24": ch24, "ch7d": ch7d, "ch30d": ch30d,
                        "score": score, "reasons": reasons[:3],
                    })
            candidates.sort(key=lambda x: (-x["score"]))
            top = candidates[:15]
            SEP = "━━━━━━━━━━━━━━━━━━━━"
            def sign(v): return f"+{v:.1f}%" if v >= 0 else f"{v:.1f}%"
            lines = [
                "🚀 *BEST TRADE — x100 СКАНЕР*",
                f"🕐 _{now_utc3()}_",
                f"📊 _Проанализировано: {len(all_coins)} монет_",
                SEP,
                "",
                "⚠️ *ДИСКЛЕЙМЕР:* x100 — высокий риск! 0.5–1% депозита",
                "",
                SEP,
            ]
            card_blocks = []
            shown_x100 = 0
            rejected_x100 = 0
            skipped_x100 = 0
            if top:
                for c in top:
                    grade = "🔥" if c["score"] >= 9 else ("💎" if c["score"] >= 7 else "📈")
                    try:
                        from live_prices import resolve_price
                        p_str = str(c["price"]).replace("$","").replace(",","")
                        p_cg = float(p_str) if p_str else 0.0
                        p, p_fresh = resolve_price(c["sym"], p_cg)  # live WS-цена для реальных торговых уровней
                        mc_str = str(c["mcap"]).replace("$","").replace(",","")
                        if "B" in mc_str: mc_val = float(mc_str.replace("B","")) * 1e9
                        elif "M" in mc_str: mc_val = float(mc_str.replace("M","")) * 1e6
                        elif "K" in mc_str: mc_val = float(mc_str.replace("K","")) * 1e3
                        else: mc_val = float(mc_str) if mc_str else 0.0
                        if mc_val < 50_000_000:    pot_min, pot_max = 3, 10
                        elif mc_val < 200_000_000: pot_min, pot_max = 2, 5
                        else:                      pot_min, pot_max = 1, 3

                        # Вход/SL/TP от реальной структуры вместо фиксированных % --
                        # x100 не считает OHLC нигде до этого места, так что здесь новые
                        # запросы (1h/4h/1d), но только для финальных ~15 кандидатов, не
                        # для всех отсканированных монет.
                        candles_1h_x100 = get_binance_ohlc(c["sym"], "1h", 250)
                        candles_4h_x100 = get_binance_ohlc(c["sym"], "4h", 200)
                        candles_1d_x100 = get_binance_ohlc(c["sym"], "1d", 365)
                        ema_ctx_x100 = ta_extra.ema_context(candles_1h_x100, candles_4h_x100)
                        sweep_1h_x100 = ta_extra.detect_sweep(candles_1h_x100)
                        sweep_4h_x100 = ta_extra.detect_sweep(candles_4h_x100)

                        # Этап 2.2 (АПГРЕЙД 11.07): раньше кандидат без live-цены ("нет WS")
                        # или без EMA-данных ("нет данных 1h/4h") всё равно показывался с
                        # этими честными, но бесполезными для трейдера пометками (см. живой
                        # пример SKYAI). Теперь такой кандидат SKIP -- причина в лог, не в
                        # выдачу.
                        if p_fresh == "(отложенная — нет WS)":
                            log.info(f"[SKIP] x100: {c['sym']} -- нет live-цены (нет WS)")
                            skipped_x100 += 1
                            continue
                        if ema_ctx_x100["tf_1h"] is None and ema_ctx_x100["tf_4h"] is None:
                            log.info(f"[SKIP] x100: {c['sym']} -- нет EMA-данных (1h и 4h)")
                            skipped_x100 += 1
                            continue

                        zones_x100 = ta_extra.find_sr_zones(candles_1h_x100, candles_4h_x100,
                                                             candles_1d_x100, p, ema_ctx=ema_ctx_x100)
                        trade_x100 = ta_extra.build_trade_from_structure("long", p, zones_x100)

                        # Этап 2.1 (АПГРЕЙД 11.07): раньше R:R считался от entry1, а
                        # показанный % -- от live-цены p (разные базы, см. живой пример
                        # MMT: "TP1 +2.9% R:R 1:1.6" при "SL -9.9%" -- по факту, если
                        # делить эти же проценты, R:R <1). Теперь ОДНА база для обоих --
                        # средневзвешенный DCA-вход 50/30/20 (weighted_dca_entry), гейт
                        # тоже пересчитан от неё же -- заявленный отсев R:R<1.5 честный.
                        # Это НЕ трогает боевой rr_gate_pass (entry1-базу) в
                        # build_trade_from_structure() самой -- та используется
                        # top_long/top_short/fa_engine и её владелец менять не просил.
                        rr_w = ta_extra.rr_from_base(trade_x100, ta_extra.weighted_dca_entry(trade_x100)) if trade_x100 else None

                        if not trade_x100 or not rr_w["rr_gate_pass"]:
                            rr_dbg = rr_w["rr_tp1"] if rr_w else "n/a"
                            log.info(f"[SR-GATE] x100: {c['sym']} отброшен -- "
                                     f"R:R по TP1 (DCA-база) {rr_dbg} < {ta_extra.SR_MIN_RR_TP1}")
                            rejected_x100 += 1
                            continue  # скрыт полностью, не показан с предупреждением
                        else:
                            low_52w  = min((cc["low"] for cc in candles_1d_x100), default=p * 0.35) if candles_1d_x100 else p * 0.35
                            high_52w = max((cc["high"] for cc in candles_1d_x100), default=p * 3.2) if candles_1d_x100 else p * 3.2

                            try:
                                signal_journal.log_signal("X100", c["sym"], "long", p,
                                                           entry_lo=trade_x100["entry_lo"], entry_hi=trade_x100["entry_hi"],
                                                           sl=trade_x100["sl"], tp1=trade_x100["tp1"],
                                                           tp2=trade_x100["tp2"], tp3=trade_x100["tp3"],
                                                           rocket_score=c["score"],
                                                           ema_stack=ema_ctx_x100,
                                                           sweep=sweep_4h_x100 or sweep_1h_x100,
                                                           levels_source="structure",
                                                           degraded_data=_data_quality_flags())
                            except Exception as e:
                                log.error(f"[JOURNAL] X100 {c['sym']}: {e}")
                            entry_avg = rr_w["base"]
                            pct = lambda a, b: (lambda v: f"+{v:.1f}%" if v >= 0 else f"{v:.1f}%")((b-a)/a*100) if a > 0 else "—"
                            spot = [
                                f"",
                                f"📍 СПОТ (R:R и % от средневзвешенного DCA-входа {fp(entry_avg)}):",
                                f"  Вход 1 (50%): {fp(trade_x100['entry1'])}",
                                f"  Вход 2 (30%): {fp(trade_x100['entry2'])}",
                                f"  Вход 3 (20%): {fp(trade_x100['entry3'])}",
                                f"  TP1: {fp(trade_x100['tp1'])} ({pct(entry_avg, trade_x100['tp1'])}) R:R 1:{rr_w['rr_tp1']}",
                                f"  TP2: {fp(trade_x100['tp2'])} ({pct(entry_avg, trade_x100['tp2'])}) R:R 1:{rr_w['rr_tp2']}",
                                f"  TP3: {fp(trade_x100['tp3'])} ({pct(entry_avg, trade_x100['tp3'])}) R:R 1:{rr_w['rr_tp3']}",
                                f"  SL: {fp(trade_x100['sl'])} ({pct(entry_avg, trade_x100['sl'])})",
                                f"  Потенциал: {pot_min}–{pot_max}x",
                                f"  {ta_extra.format_ema_stack_line(ema_ctx_x100)}",
                            ]
                            _x100_sweep_line = ta_extra.format_sweep_line(sweep_1h_x100, sweep_4h_x100, price_fmt=fp)
                            if _x100_sweep_line:
                                spot.append(f"  {_x100_sweep_line}")
                            spot += [
                                f"",
                                f"📍 ФЬЮЧЕРС (LONG x3–5):",
                                f"  Вход: {fp(trade_x100['entry1'])}",
                                f"  TP1: {fp(trade_x100['tp1'])} | TP2: {fp(trade_x100['tp2'])} | TP3: {fp(trade_x100['tp3'])}",
                                f"  SL: {fp(trade_x100['sl'])}",
                                f"  Потенциал: {pot_min*3}–{pot_max*5}x с плечом",
                                f"  📉 Мин/Макс: ${fp(low_52w)} / ${fp(high_52w)}",
                            ]
                            price_line = f"💰 Цена: {fp(p)}  _{p_fresh}_ | МКап: {c['mcap']}"
                    except Exception:
                        spot = []
                        price_line = f"💰 Цена: {c['price']} | МКап: {c['mcap']}"
                    shown_x100 += 1
                    card_blocks += [
                        SEP,
                        f"{grade} #{shown_x100} {c['sym']} — {c['name']}",
                        price_line,
                        f"📊 24ч: {sign(c['ch24'])} | 7д: {sign(c['ch7d'])} | 30д: {sign(c['ch30d'])}",
                        *spot,
                        f"⚡ {' · '.join(c['reasons'])}",
                        f"🎯 Скор: {c['score']}/12",
                        _journal_footer_line("x100"),
                    ]
            _x100_skip_note = f", {skipped_x100} skip без live-цены/EMA" if skipped_x100 else ""
            if card_blocks:
                lines.append(f"\n💎 *Найдено: {shown_x100} кандидатов*"
                              + (f" _(ещё {rejected_x100} отброшено по R:R < 1:1.5{_x100_skip_note})_" if (rejected_x100 or skipped_x100) else "") + "\n")
                lines += card_blocks
            elif rejected_x100 or skipped_x100:
                lines.append(f"\n❌ Кандидатов не найдено -- {rejected_x100} отброшено по R:R < 1:1.5{_x100_skip_note}")
            else:
                lines.append("\n❌ Кандидатов не найдено")
            lines += ["", SEP, "⚠️ SL обязателен • Проверяй фундаментал!"]
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Обновить", callback_data="x100_scan"),
                 InlineKeyboardButton("🏠 Меню",    callback_data="show_menu")],
            ])
            text = "\n".join(lines)
            if len(text) > 4090: text = text[:4087] + "..."
            return text, kb

        loop = asyncio.get_event_loop()
        text, kb = await loop.run_in_executor(None, _scan_x100_sync)

        try:
            await msg.reply_text(text, parse_mode="Markdown", reply_markup=kb)
        except:
            await msg.reply_text(text.replace("*","").replace("_",""), reply_markup=kb)
    except Exception as e:
        log.error(f"x100 error: {e}")
        try: await msg.reply_text(f"❌ Ошибка: {e}")
        except: pass

async def cmd_top_spot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/spot   :     """
    if _scan_busy["top_spot"]:
        await update.message.reply_text("⏳ Скан уже выполняется, подожди")
        return
    _scan_busy["top_spot"] = True
    try:
        await _cmd_top_spot_body(update, ctx)
    finally:
        _scan_busy["top_spot"] = False


async def _cmd_top_spot_body(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text(
        "⏳ Ищу спот-кандидатов для восстановления...\n"
        "📊 Топ-500 монет через CMC + CoinGecko"
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
    rejected = []  # (symbol, reason, ch90d) -- для честной сводки, если кандидатов не найдётся (см. ТОП ШОРТ)
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
        sym       = coin["symbol"]

        # Фильтры качества (памп/дамп, ликвидность, капа, стейблы, глубина просадки)
        if vol_ratio > 60:
            rejected.append((sym, f"подозрительный объём/капа (vol/mcap {vol_ratio:.0f}% > 60%)", ch90d))
            continue
        if vol < 500_000:
            rejected.append((sym, f"низкий объём (${vol/1e6:.1f}M < $0.5M)", ch90d))
            continue
        if mcap < 10_000_000:
            rejected.append((sym, f"низкая капитализация (${mcap/1e6:.1f}M < $10M)", ch90d))
            continue
        if "stablecoin" in tags:
            rejected.append((sym, "стейблкоин", ch90d))
            continue
        if ch90d > -20:
            rejected.append((sym, f"недостаточная просадка за 90д ({ch90d:.0f}% > -20%)", ch90d))
            continue

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
        [InlineKeyboardButton("🔄 Обновить",  callback_data="top_spot"),
         InlineKeyboardButton("🏠 Меню",      callback_data="show_menu")],
        [InlineKeyboardButton("🟢 ТОП ЛОНГ",  callback_data="top_long"),
         InlineKeyboardButton("🔴 ТОП ШОРТ",  callback_data="top_short")],
    ])

    if not top_spot:
        lines = [
            "🟡 *Нет спот-кандидатов, прошедших фильтры качества*",
            "Рынок сейчас не даёт монет с достаточной просадкой и ликвидностью.",
        ]
        rejected.sort(key=lambda r: r[2])  # по глубине просадки (ch90d) -- ближе к порогу первыми
        rejected_top3 = rejected[:3]
        if rejected_top3:
            lines += ["", "*Ближе всех к проходу (для прозрачности):*", ""]
            for sym, reason, _ch90d in rejected_top3:
                lines.append(f"  • {sym}: {reason}")
        await msg.edit_text(
            "\n".join(lines), parse_mode="Markdown", reply_markup=nav)
        return

    #  
    list_lines = [
        "⭐ *BEST TRADE — ТОП СПОТ*",
        f"🕐 {now_utc3()}",
        "📊 Кандидаты на восстановление после просадки",
        "",
        "💎 *Список кандидатов:*",
        "",
    ]

    for i, (c, score, x_ath, ch90, ch7) in enumerate(top_spot, 1):
        sym    = c["symbol"]
        tv     = tv_link(sym)
        prc    = c["quote"]["USDT"].get("price", 0)
        rank   = c.get("cmc_rank", 999)
        vol    = c["quote"]["USDT"].get("volume_24h", 0) or 0
        vol_s  = f"${vol/1e9:.1f}B" if vol>=1e9 else f"${vol/1e6:.0f}M"

        # Иконка потенциала восстановления до ATH
        if x_ath >= 10:   pot_icon = "🚀"
        elif x_ath >= 5:  pot_icon = "💎"
        elif x_ath >= 3:  pot_icon = "📈"
        elif x_ath >= 2:  pot_icon = "⭐"
        else:             pot_icon = "🔹"

        trend_icon = "🟢" if ch7 > 0 else "🔴"

        list_lines += [
            f"{i}. [{sym}USDT]({tv})  {pot_icon}",
            f"    Цена: `{fp(prc)}`    Rank #{rank}    Vol {vol_s}",
            f"    Просадка 90д: `{ch90:.0f}%`    Потенциал: `~x{x_ath:.1f}` до ATH",
            f"   {trend_icon} 7д: `{fc(ch7)}`",
            "",
        ]

    list_lines += ["⬇️ Детальный анализ каждой монеты — ниже"]

    await msg.edit_text("\n".join(list_lines), parse_mode="Markdown",
                        reply_markup=nav, disable_web_page_preview=False)

    #    
    for coin, score, x_ath, ch90d_v, ch7d_v in top_spot:
        sym  = coin["symbol"]
        slug = coin.get("slug", sym.lower())
        q    = coin["quote"]["USDT"]
        try:
            prog = await update.message.reply_text(f"  {sym}...")

            def _analyze_spot_candidate():
                a          = real_full_analysis(coin)
                stats_24h  = get_binance_24h(sym)
                atl        = get_binance_alltime_low(sym)
                candles_1d = get_binance_ohlc(sym, "1d", 365)
                candles_1w = get_binance_ohlc(sym, "1w", 200)
                return a, stats_24h, atl, candles_1d, candles_1w

            # Блокирующие HTTP-вызовы -- в executor, чтобы не морозить event loop между
            # кандидатами (их тут до 10, каждый по 5 синхронных запросов).
            _loop = asyncio.get_event_loop()
            a, stats_24h, atl, candles_1d, candles_1w = await _loop.run_in_executor(
                None, _analyze_spot_candidate)

            if not a.get("rr_gate_pass"):
                log.info(f"[SR-GATE] top_spot: {sym} отброшен -- "
                         f"R:R по TP1 {a.get('rr_tp1')} < {ta_extra.SR_MIN_RR_TP1}")
                await prog.delete()
                continue

            price  = a["price"]
            price_fresh = a.get("price_fresh", "")
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

            # Score label
            if spot_score >= 6:   sc_label, sc_rec = "ОТЛИЧНЫЙ \U0001f680", "\u2705 Максимальный вход 2-3%"
            elif spot_score >= 4: sc_label, sc_rec = "ХОРОШИЙ \u2705", "\u2705 Можно входить 1-2%"
            elif spot_score >= 2: sc_label, sc_rec = "УМЕРЕННЫЙ \u26a0\ufe0f", "\u26a0\ufe0f DCA осторожно"
            else:                 sc_label, sc_rec = "СЛАБЫЙ \u274c", "\u274c Пропустить"

            sc_grade = "A+" if spot_score >= 6 else ("A" if spot_score >= 5 else ("B" if spot_score >= 3 else "C"))
            spot_rocket = min(100, max(0, spot_score * 14))
            # EMA-стек + свежий свип ликвидности (спот всегда "long") -- аддитивно к уже
            # посчитанному spot_rocket, существующую формулу выше не трогаем.
            spot_rocket = max(0, min(100, spot_rocket
                                      + ta_extra.ema_stack_score_delta(a.get("ema_ctx"), "long")
                                      + ta_extra.sweep_score_delta(a.get("sweep_1h"), a.get("sweep_4h"), "long")))

            def ri_spot(v): return "\U0001f7e2" if v < 30 else ("\U0001f534" if v > 70 else "\U0001f7e1")

            ch24_s = f"+{ch24h:.1f}%" if ch24h >= 0 else f"{ch24h:.1f}%"
            ch30_s = f"+{ch30d:.1f}%" if ch30d >= 0 else f"{ch30d:.1f}%"
            ch90_s = f"+{ch90d_v:.1f}%" if ch90d_v >= 0 else f"{ch90d_v:.1f}%"
            ch7_s  = f"+{ch7d_v:.1f}%" if ch7d_v >= 0 else f"{ch7d_v:.1f}%"

            # Support / Resistance from buy zones
            sup1 = buy2
            sup2 = buy1
            res1 = ath * 0.33 if ath > 0 else price * 1.25
            res2 = ath * 0.60 if ath > 0 else price * 1.50

            ema200_str = f"`{fp(ema200_d)}`" if ema200_d else "—"
            ema200_pos = "\u2705 Выше" if (ema200_d and price > ema200_d) else "\u274c Ниже"

            # Quality factors
            fok = []
            fbad = []
            if rsi_1d < 35:       fok.append("RSI перепродан (1D)")
            else:                  fbad.append("RSI не перепродан")
            if ch90d_v < -60:     fok.append(f"Глубокая коррекция {ch90_s}")
            else:                  fbad.append("Нет глубокой коррекции")
            if ch7d_v > 0:        fok.append(f"Разворот 7д подтверждён")
            else:                  fbad.append("Нет разворота 7д")
            if vol_growing:       fok.append("Объём растёт")
            else:                  fbad.append("Объём не растёт")
            if x_ath >= 3:        fok.append(f"Потенциал ~x{x_ath:.1f} к ATH")
            else:                  fbad.append("Потенциал к ATH < x3")

            n_ok_spot = len(fok)
            grade_spot = "A+" if n_ok_spot >= 5 else ("A" if n_ok_spot >= 4 else ("B" if n_ok_spot >= 3 else "C"))

            if not (spot_rocket >= 60 and grade_spot in ("A+", "A", "B")):
                log.info(f"[QUALITY-GATE] top_spot: {sym} скрыт -- "
                         f"скор {spot_rocket}/100, грейд {grade_spot}")
                await prog.delete()
                continue

            lines = [
                f"*{sym}/USDT* \u2b50 *СПОТ*",
                f"_Скор: {spot_rocket}/100 | Качество: {grade_spot}_",
                "",
                f"\U0001f4cd Цена сейчас: `{fp(price)}`  _{price_fresh}_" if price_fresh else f"\U0001f4cd Цена сейчас: `{fp(price)}`",
                f"\U0001f4ca 24ч: {ch24_s} | 30д: {ch30_s} | 90д: {ch90_s}",
                "",
                f"\U0001f7e2 *Зоны:*",
                "",
                f"\U0001f7e2 Поддержка: `{fp(sup1)}` / `{fp(sup2)}`",
                "",
                f"\U0001f534 Сопротивление: `{fp(res1)}` / `{fp(res2)}`",
                "",
                f"\U0001f4bc *Зоны покупки (DCA):*",
                "",
                f"`Покупка 1 (40%): {fp(buy2)}`  _(зона 30д)_",
                f"`Покупка 2 (40%): {fp(buy1)}`  _(зона 90д)_",
                f"`Покупка 3 (20%): {fp(buy3)}`  _(у ATL)_",
            ]
            if ath > 0:
                lines += [
                    "",
                    f"\U0001f3af *Цели (к ATH):*",
                    "",
                    f"`Цель 1: {fp(ath*0.33)}`  _(~x{ath*0.33/price:.1f})_",
                    f"`Цель 2: {fp(ath*0.60)}`  _(~x{ath*0.60/price:.1f})_",
                    f"`Цель 3: {fp(ath*0.90)}`  _(~x{ath*0.90/price:.1f} от ATH)_",
                ]
            lines += [
                "",
                f"\U0001f4c8 *Технический анализ:*",
                "",
                f"RSI (1D):    {ri_spot(rsi_1d)} `{rsi_1d:.0f}`",
                f"EMA200 (1D): {ema200_pos} {ema200_str}",
                f"Потенциал:   *{pot_str}*",
                f"Rank:        #{rank}",
                ta_extra.format_ema_stack_line(a.get("ema_ctx")),
            ]
            _spot_sweep_line = ta_extra.format_sweep_line(a.get("sweep_1h"), a.get("sweep_4h"), price_fmt=fp)
            if _spot_sweep_line:
                lines.append(_spot_sweep_line)
            lines += [
                "",
                f"\U0001f4cb *Расшифровка скора ({spot_rocket}/100):*",
                "",
                "Шкала силы:",
                "0-40   \u274c СЛАБЫЙ — пропустить",
                "41-59  \u26a0\ufe0f УМЕРЕННЫЙ — малый риск",
                "60-74  \u2705 ХОРОШИЙ — входить",
                "75-89  \U0001f4aa СИЛЬНЫЙ — приоритет",
                "90-100 \U0001f680 ОТЛИЧНЫЙ — максимум",
                "",
                f"Качество {grade_spot} — {n_ok_spot} из 5 факторов:",
            ]
            for f_ok in fok:
                lines.append(f"\u2705 {f_ok}")
            for f_bad in fbad[:max(0, 5 - n_ok_spot)]:
                lines.append(f"\u274c {f_bad}")
            lines += [
                "",
                "Грейды:",
                "A+ = все 5 факторов",
                "A  = 4-5 факторов",
                "B  = 3 фактора",
                "C  = 1-2 фактора — осторожно",
                "",
                f"\u26a0\ufe0f Риск: 5-10% депозита, горизонт 3-6 мес.",
                f"#{sym}USDT",
            ]

            _spot_sell_target = ath*0.9 if ath>0 else price*5
            TOP_SPOT_SIGNALS[sym] = {
                "time": datetime.now(TZ), "entry": price,
                "buy_zone_lo": buy1, "buy_zone_hi": buy2,
                "atl": atl, "sell_target": _spot_sell_target,
                "status": "watching",
            }
            try:
                # /spot использует свою собственную схему уровней (zone_30d/zone_90d/ATL --
                # уже данные, не фиксированные %), а не find_sr_zones -- R:R-гейт выше уже
                # применён через a["rr_gate_pass"], но сами уровни здесь не "structure" в
                # смысле find_sr_zones, поэтому отдельная метка для честной статистики.
                signal_journal.log_signal("TOP_SPOT", sym, "long", price,
                                           entry_lo=buy1, entry_hi=buy2, sl=atl,
                                           tp1=_spot_sell_target, rocket_score=spot_rocket,
                                           ema_stack=a.get("ema_ctx"),
                                           sweep=a.get("sweep_4h") or a.get("sweep_1h"),
                                           levels_source="ath_recovery", grade=grade_spot,
                                           degraded_data=_data_quality_flags())
            except Exception as e:
                log.error(f"[JOURNAL] TOP_SPOT {sym}: {e}")

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


RSI_EXTREME_LONG = 75   # RSI(4H) выше этого -- перекуп, жёсткий отказ для лонга вне зависимости
                        # от остального скора (signal_quality_filter даёт за это лишь -10,
                        # недостаточно чтобы одно это остановило слабый но "проходной" сигнал)
RSI_EXTREME_SHORT = 25  # зеркально для шорта (RSI ниже -- перепроданность)


def _scan_top_long_sync():
    """Тяжёлая, полностью синхронная часть /long: CMC-фетч + до 50 блокирующих
    real_full_analysis()-вызовов + BTC-контекст. Выполняется в run_in_executor, чтобы не
    морозить event loop бота на минуты (см. _scan_busy).

    Без fallback-логики: плохой сигнал хуже отсутствия сигнала. Если ни один кандидат не
    прошёл все гейты (качество, RSI, R:R) -- top_long пуст, и cmd_top_long честно
    сообщает об этом + показывает топ-3 отклонённых с причиной (см. rejected)."""
    coins = get_top500()
    if not coins:
        return None, None, None, None

    pre = []
    for coin in coins:
        q = coin["quote"]["USDT"]
        vol   = q.get("volume_24h", 0) or 0
        mcap  = q.get("market_cap", 0) or 0
        ch24h = q.get("percent_change_24h", 0) or 0
        vol_ratio = (vol / mcap * 100) if mcap > 0 else 0
        if vol >= 500_000 and ch24h > -30:
            pre.append(coin)

    pre.sort(key=lambda c: c["quote"]["USDT"].get("percent_change_24h", 0) or 0, reverse=True)

    scored = []
    rejected = []  # (symbol, reason, rocket) -- для честной сводки, когда никто не прошёл
    for coin in pre[:50]:  # сокращено с 150 до 50
        try:
            a   = real_full_analysis(coin)
            sym = coin["symbol"]
            if not a["is_long"]:
                continue  # не наше направление -- не считаем "отклонённым лонг-кандидатом"

            if a.get("suspicious"):
                rejected.append((sym, "подозрительный объём (возможен памп)", a.get("rocket", 0)))
                continue
            if a["rsi_4h"] > RSI_EXTREME_LONG:
                rejected.append((sym, f"RSI перегрет ({a['rsi_4h']:.0f}) для лонга", a.get("rocket", 0)))
                continue

            pa  = pro_analysis(sym, coin)
            sqf = signal_quality_filter(a, pa, coin)

            grade = _signal_grade(a, True)
            if not (a["rocket"] >= 60 and grade in ("A+", "A", "B")):
                rejected.append((sym, f"качество недостаточно (Rocket {a.get('rocket', 0)}/100, "
                                       f"грейд {grade})", a.get("rocket", 0)))
                continue
            if _counter_trend_blocked(a, "long"):
                rejected.append((sym, "контртренд без подтверждённого свипа (медвежий фон)",
                                  a.get("rocket", 0)))
                continue
            if not a.get("rr_gate_pass"):
                rejected.append((sym, f"R:R по TP1 {a.get('rr_tp1')} < {ta_extra.SR_MIN_RR_TP1}",
                                  a.get("rocket", 0)))
                log.info(f"[SR-GATE] top_long: {sym} отброшен -- "
                         f"R:R по TP1 {a.get('rr_tp1')} < {ta_extra.SR_MIN_RR_TP1}")
                continue

            a["_sqf"] = sqf
            a["_grade"] = grade
            scored.append((coin, a))
        except: pass

    scored.sort(key=lambda x: x[1]["rocket"], reverse=True)
    top_long = scored[:5]
    rejected.sort(key=lambda r: r[2], reverse=True)  # ближе всех к проходу -- наверх сводки

    btc_ctx = get_btc_market_context()
    return coins, top_long, rejected[:3], btc_ctx


async def cmd_top_long(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/long — топ лонг кандидаты"""
    if _scan_busy["top_long"]:
        await update.message.reply_text("⏳ Скан уже выполняется, подожди")
        return
    _scan_busy["top_long"] = True
    try:
        msg = await update.message.reply_text("🟢 Анализирую рынок... ~20 сек")

        loop = asyncio.get_event_loop()
        coins, top_long, rejected_top3, btc_ctx = await loop.run_in_executor(None, _scan_top_long_sync)

        if coins is None:
            await msg.edit_text("❌ Нет данных CMC", reply_markup=nav_kb("top_long")); return

        nav = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Обновить", callback_data="top_long"),
             InlineKeyboardButton("🏠 Меню",    callback_data="show_menu")],
            [InlineKeyboardButton("🔴 ТОП ШОРТ", callback_data="top_short"),
             InlineKeyboardButton("⭐️ ТОП СПОТ", callback_data="top_spot")],
        ])

        if not top_long:
            # Плохой сигнал хуже отсутствия сигнала -- никакого fallback, честный ответ +
            # топ-3 отклонённых кандидатов с причиной для прозрачности.
            lines = [
                "🟡 *Сейчас нет сетапов с R:R ≥ 1:1.5*",
                "Рынок не даёт качественных входов.",
            ]
            if rejected_top3:
                lines += ["", "*Ближе всех к проходу (для прозрачности):*", ""]
                for sym, reason, _rocket in rejected_top3:
                    lines.append(f"  • {sym}: {reason}")
            await msg.edit_text(
                "\n".join(lines), parse_mode="Markdown", reply_markup=nav
            ); return

        btc_warn = ""
        if btc_ctx["ok"] and not btc_ctx["long_ok"]:
            btc_warn = f"\n⚠️ *{btc_ctx['warning']}*\n"

        SEP = "━━━━━━━━━━━━━━━━━━━━"
        kz_line = (killzone_label().split(chr(10)) or [""])[0]

        list_lines = [
            "🟢 *BEST TRADE — ТОП ЛОНГ*",
            f"🕐 _{now_utc3()}_",
            f"📊 {btc_ctx.get('label', '')}",
        ]
        if btc_warn:
            list_lines.append(btc_warn)
        list_lines += [SEP, "", "📈 *Лучшие лонг-кандидаты:*", f"⏰ {kz_line}", ""]

        for i, (c, a) in enumerate(top_long, 1):
            sym     = c["symbol"]
            tv      = tv_link(sym)
            r       = a["rocket"]
            grade   = "A+" if r >= 90 else ("A" if r >= 75 else ("B" if r >= 60 else "C"))
            rsi_e   = "🟢" if a["rsi_4h"] < 30 else ("🟡" if a["rsi_4h"] < 60 else "🔴")
            trend_e = "📈" if a.get("trend_4h") == "bullish" else ("📉" if a.get("trend_4h") == "bearish" else "➡️")
            score_e = "🔥" if r >= 80 else ("🦅" if r >= 65 else "✅")
            p   = a.get("price", 0) or 0
            # Реальные уровни от структуры (a["tp1"/"tp2"/"tp3"/"sl"]), не пересчёт по
            # фиксированным % -- иначе быстрый превью-список расходится с детальной
            # карточкой ниже, которая всегда использовала настоящие значения a[...].
            tp1, tp2, tp3, sl = a["tp1"], a["tp2"], a["tp3"], a["sl"]
            _pct = lambda v: f"{(v - p) / p * 100:+.1f}%" if p else "—"
            list_lines += [
                f"━━━━━━━━━━━━━━━━━━━━",
                f"{score_e} #{i}  {sym}/USDT",
                f"Скор: {r}/100  |  Качество: {grade}",
                f"",
                f"💰 Цена:      {fp(a['price'])}",
                f"📊 RSI (4H):  {rsi_e} {a['rsi_4h']:.0f}   |   Тренд: {trend_e}",
                f"",
                f"🎯 Цели (LONG) -- R:R по TP1 1:{a.get('rr_tp1', 0):.1f}:",
                f"  TP1:  ${tp1}   ({_pct(tp1)})",
                f"  TP2:  ${tp2}   ({_pct(tp2)})",
                f"  TP3:  ${tp3}   ({_pct(tp3)})",
                f"  SL:   ${sl}    ({_pct(sl)})",
                f"",
            ]
        list_lines += [SEP, "📋 _Детальный анализ каждой монеты ниже_ ⬇️"]

        await msg.edit_text("\n".join(list_lines), parse_mode="Markdown",
                            reply_markup=nav, disable_web_page_preview=True)

        for coin, a in top_long:
            sym  = coin["symbol"]
            slug = coin.get("slug", sym.lower())
            try:
                stats = get_binance_24h(sym)
                text  = _build_signal_post(sym, a, stats, mode="long")
                await send_coin(ctx.bot, update.effective_chat.id, sym, slug, a, text)
                TOP_LONG_SIGNALS[sym] = {
                    "time":  datetime.now(TZ), "entry": a["price"],
                    "tp1": a["tp1"], "tp2": a["tp2"], "tp3": a["tp3"],
                    "sl": a["sl"], "rr": a["rr"],
                    "status": "active", "chat_id": update.effective_chat.id,
                }
                try:
                    signal_journal.log_signal("TOP_LONG", sym, "long", a["price"],
                                               entry_lo=a.get("entry3", a["price"]),
                                               entry_hi=a.get("entry1", a["price"]), sl=a["sl"],
                                               tp1=a["tp1"], tp2=a["tp2"], tp3=a["tp3"],
                                               rr=a["rr"], rocket_score=a.get("rocket"),
                                               ema_stack=a.get("ema_ctx"),
                                               sweep=a.get("sweep_4h") or a.get("sweep_1h"),
                                               levels_source=a.get("levels_source"), grade=a.get("_grade"),
                                               degraded_data=_data_quality_flags())
                except Exception as e:
                    log.error(f"[JOURNAL] TOP_LONG {sym}: {e}")
                await asyncio.sleep(1.5)
            except Exception as e:
                log.error(f"top_long {sym}: {e}")

        await ctx.bot.send_message(
            update.effective_chat.id,
            "✅ *BEST TRADE — ТОП ЛОНГ готов*\n\nВыбери следующее действие:",
            parse_mode="Markdown", reply_markup=main_kb()
        )
    finally:
        _scan_busy["top_long"] = False


def _scan_top_short_sync(progress: dict = None):
    """Тяжёлая, полностью синхронная часть /short: CMC-фетч + до 80 блокирующих
    real_full_analysis()-вызовов. Выполняется в run_in_executor (см. _scan_busy).

    Без fallback-логики: плохой сигнал хуже отсутствия сигнала (см. _scan_top_long_sync).
    Отклонённые кандидаты собираются в rejected для честной сводки в cmd_top_short.

    progress: опциональный shared dict {"i","total"} -- обновляется по ходу цикла,
    читается из async-стороны (cmd_top_short) каждые 15с для апдейта сообщения
    пользователю. Простое присваивание int в CPython атомарно под GIL, отдельная
    блокировка не нужна для этого паттерна "один пишет, один читает".

    Тайминги по фазам логируются отдельно (список символов / OHLC+TA+fa-проверки
    совмещены внутри real_full_analysis() на каждую монету -- это ОДНА блокирующая
    real_full_analysis()-транзакция на монету, а не отдельно разложимые под-фазы, см.
    докстринг real_ta()) -- честно логируем как один блок, не выдумываем ложную
    гранулярность, которой в архитектуре сейчас нет."""
    t0 = time.time()
    coins = get_top500()
    t_list = time.time()
    log.info(f"[TIMING top_short] список символов: {len(coins) if coins else 0} монет за {t_list - t0:.1f}s")
    if not coins:
        return None, None, None

    pre = []
    for coin in coins:   #
        q = coin["quote"]["USDT"]
        vol      = q.get("volume_24h",  0) or 0
        mcap     = q.get("market_cap",  0) or 0
        vol_ratio = (vol / mcap * 100) if mcap > 0 else 0
        if vol >= 1_000_000 and vol_ratio < 60:
            pre.append(coin)
    t_prefilter = time.time()
    candidates_n = len(pre[:80])
    log.info(f"[TIMING top_short] прескрин: {len(pre)} прошли объём/ликвидность "
             f"(берём первые {candidates_n}) за {t_prefilter - t_list:.1f}s")
    if progress is not None:
        progress["total"] = candidates_n
        progress["i"] = 0

    scored = []
    rejected = []  # (symbol, reason, rocket)
    for idx, coin in enumerate(pre[:80]):
        if progress is not None:
            progress["i"] = idx + 1
        try:
            a = real_full_analysis(coin)
            sym = coin["symbol"]
            #
            if not a["is_long"]:
                if a.get("suspicious"):
                    rejected.append((sym, "подозрительный объём (возможен памп)", a.get("rocket", 0)))
                    continue
                if a["rsi_4h"] < RSI_EXTREME_SHORT:
                    rejected.append((sym, f"RSI перепродан ({a['rsi_4h']:.0f}) для шорта", a.get("rocket", 0)))
                    continue
                grade = _signal_grade(a, False)
                if not (a["rocket"] >= 60 and grade in ("A+", "A", "B")):
                    rejected.append((sym, f"качество недостаточно (Rocket {a['rocket']}/100, грейд {grade})",
                                      a.get("rocket", 0)))
                    continue
                if _counter_trend_blocked(a, "short"):
                    rejected.append((sym, "контртренд без подтверждённого свипа (бычий фон)",
                                      a.get("rocket", 0)))
                    continue
                if not a.get("rr_gate_pass"):
                    rejected.append((sym, f"R:R по TP1 {a.get('rr_tp1')} < {ta_extra.SR_MIN_RR_TP1}", a.get("rocket", 0)))
                    log.info(f"[SR-GATE] top_short: {sym} отброшен -- "
                             f"R:R по TP1 {a.get('rr_tp1')} < {ta_extra.SR_MIN_RR_TP1}")
                    continue
                a["_grade"] = grade
                scored.append((coin, a))
            elif a.get("rsi_4h", 50) > 72 and a["vol"] >= 2_000_000:
                # Контрарианский шорт против объективно бычьего фона -- разрешён ТОЛЬКО от
                # подтверждённой манипуляции (свежий sweep_high с volume_confirmed=True),
                # RSI-перекуп сам по себе больше не считается достаточным обоснованием.
                if _counter_trend_blocked(a, "short"):
                    rejected.append((sym, "контртренд (RSI-перекуп) без подтверждённого свипа -- "
                                           "недостаточное обоснование", a.get("rocket", 0)))
                    continue
                # a была построена под is_long (изначальное направление real_full_analysis)
                # -- entry/SL/TP нужно пересобрать под short заново от тех же зон, иначе
                # уровни останутся зеркально неверными (лонговая структура на шорт-сигнале).
                trade = ta_extra.build_trade_from_structure("short", a["price"], a.get("zones", {"above": [], "below": []}))
                if not trade:
                    rejected.append((sym, "нет зоны сопротивления для контрарианского входа", a.get("rocket", 0)))
                    continue
                if not trade["rr_gate_pass"]:
                    rejected.append((sym, f"R:R по TP1 {trade['rr_tp1']} < {ta_extra.SR_MIN_RR_TP1} (RSI-override)", a.get("rocket", 0)))
                    log.info(f"[SR-GATE] top_short (RSI-override): {sym} отброшен -- "
                             f"R:R по TP1 {trade['rr_tp1']} < {ta_extra.SR_MIN_RR_TP1}")
                    continue
                a_short = dict(a)
                a_short["is_long"] = False
                a_short["entry1"], a_short["entry2"], a_short["entry3"] = trade["entry1"], trade["entry2"], trade["entry3"]
                a_short["sl"], a_short["tp1"], a_short["tp2"], a_short["tp3"] = trade["sl"], trade["tp1"], trade["tp2"], trade["tp3"]
                a_short["rr"] = a_short["rr_tp1"] = trade["rr_tp1"]
                a_short["rr_tp2"], a_short["rr_tp3"] = trade["rr_tp2"], trade["rr_tp3"]
                a_short["rr_gate_pass"] = True
                # Это контрарианский шорт против объективно бычьего фона (перекуп по RSI) --
                # бычьи SMC-ярлыки, посчитанные под ИСХОДНОЕ (лонговое) направление, не
                # должны выглядеть в карточке как факторы, подтверждающие шорт (иначе панели
                # читаются как противоречащие друг другу).
                a_short["smc_factors"] = [f for f in a.get("smc_factors", []) if "Bull" not in f]
                a_short["tf_aligned_bull"] = False
                grade = _signal_grade(a_short, False)
                if not (a_short["rocket"] >= 60 and grade in ("A+", "A", "B")):
                    rejected.append((sym, f"качество недостаточно (Rocket {a_short['rocket']}/100, грейд {grade}, "
                                           f"RSI-override)", a_short.get("rocket", 0)))
                    continue
                a_short["_grade"] = grade
                scored.append((coin, a_short))
        except: pass

    scored.sort(key=lambda x: x[1]["rocket"], reverse=True)
    top_short = scored[:5]
    rejected.sort(key=lambda r: r[2], reverse=True)
    t_end = time.time()
    avg = (t_end - t_prefilter) / candidates_n if candidates_n else 0
    log.info(f"[TIMING top_short] анализ {candidates_n} монет (OHLC-загрузка+TA+fa-проверки "
             f"через real_full_analysis, совмещено на монету): {t_end - t_prefilter:.1f}s "
             f"(среднее {avg:.2f}s/монету) -- прошли {len(scored)}, отброшено {len(rejected)}. "
             f"Итого весь скан: {t_end - t0:.1f}s")
    return coins, top_short, rejected[:3]


async def cmd_top_short(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/short   :  SHORT + -,  Rocket Score"""
    if _scan_busy["top_short"]:
        await update.message.reply_text("⏳ Скан уже выполняется, подожди")
        return
    _scan_busy["top_short"] = True
    try:
        msg = await update.message.reply_text("🔴 Сканирую рынок на шорт-сетапы... ~40 сек")

        cached = _scan_result_cache["top_short"]
        if time.time() - cached["ts"] < _SCAN_RESULT_CACHE_TTL and cached["data"] is not None:
            coins, top_short, rejected_top3 = cached["data"]
            log.info("[TIMING top_short] отдан кэш скана (< 60с с прошлого запуска)")
        else:
            # Прогресс-репортер: конкурентная корутина поверх run_in_executor -- каждые
            # 15с читает progress (простой dict, пишется из sync-воркера в другом потоке,
            # int-присваивание атомарно под GIL) и правит сообщение "Сканирую... i/N".
            # ВАЖНО про concurrency guard: _scan_busy -- per-scan-type (top_short/top_spot/
            # top_long/x100 -- независимые флаги), так что top_spot НЕ блокирует старт
            # top_short через этот guard. Но оба (и любой другой скан на real_full_analysis())
            # разделяют ГЛОБАЛЬНЫЕ rate-limit локи _bybit_kline_lock/_cg_lock -- если два
            # скана реально идут одновременно, их HTTP-вызовы физически сериализуются через
            # эти локи, а не через _scan_busy. top_spot сейчас OHLC вообще не грузит (см. bug 2),
            # так что конкретно spot->short коллизии нет, но long/x100 -> short -- есть.
            progress = {"i": 0, "total": 0}
            loop = asyncio.get_event_loop()
            scan_future = loop.run_in_executor(None, _scan_top_short_sync, progress)

            async def _progress_reporter():
                last_i = -1
                while not scan_future.done():
                    await asyncio.sleep(15)
                    if scan_future.done():
                        break
                    i, total = progress["i"], progress["total"]
                    if total and i != last_i:
                        try:
                            await msg.edit_text(f"🔴 Сканирую рынок на шорт-сетапы... {i}/{total}")
                        except Exception:
                            pass
                        last_i = i

            reporter_task = asyncio.create_task(_progress_reporter())
            try:
                coins, top_short, rejected_top3 = await scan_future
            finally:
                reporter_task.cancel()

            if coins is not None:
                _scan_result_cache["top_short"] = {"ts": time.time(), "data": (coins, top_short, rejected_top3)}

        if coins is None:
            await msg.edit_text("❌ Нет данных"); return

        nav = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Обновить", callback_data="top_short"),
             InlineKeyboardButton("🏠 Меню",    callback_data="show_menu")],
            [InlineKeyboardButton("🟢 ТОП ЛОНГ", callback_data="top_long"),
             InlineKeyboardButton("⭐️ ТОП СПОТ", callback_data="top_spot")],
        ])

        if not top_short:
            lines = [
                "🟡 *Сейчас нет сетапов с R:R ≥ 1:1.5*",
                "Рынок не даёт качественных входов.",
            ]
            if rejected_top3:
                lines += ["", "*Ближе всех к проходу (для прозрачности):*", ""]
                for sym, reason, _rocket in rejected_top3:
                    lines.append(f"  • {sym}: {reason}")
            await msg.edit_text(
                "\n".join(lines), parse_mode="Markdown", reply_markup=nav
            ); return

        SEP = "━━━━━━━━━━━━━━━━━━━━"
        list_lines = [
        "🔴 *BEST TRADE — ТОП ШОРТ*",
        f"🕐 _{now_utc3()}_",
        SEP, "",
        "📉 *Лучшие шорт-кандидаты:*", "",
    ]
        for i, (c, a) in enumerate(top_short, 1):
            sym     = c["symbol"]
            tv      = tv_link(sym)
            r       = a["rocket"]
            grade   = "A+" if r >= 90 else ("A" if r >= 75 else ("B" if r >= 60 else "C"))
            rsi_e   = "🔴" if a["rsi_4h"] > 70 else ("🟡" if a["rsi_4h"] > 50 else "🟢")
            trend_e = "📉" if a.get("trend_4h") == "bearish" else ("📈" if a.get("trend_4h") == "bullish" else "➡️")
            score_e = "🔥" if r >= 80 else ("🦅" if r >= 65 else "✅")
            p   = a.get("price", 0) or 0
            # Реальные уровни от структуры (a["tp1"/"tp2"/"tp3"/"sl"]), не пересчёт по
            # фиксированным % -- иначе быстрый превью-список расходится с детальной
            # карточкой ниже, которая всегда использовала настоящие значения a[...].
            tp1, tp2, tp3, sl = a["tp1"], a["tp2"], a["tp3"], a["sl"]
            _pct = lambda v: f"{(v - p) / p * 100:+.1f}%" if p else "—"
            list_lines += [
                f"━━━━━━━━━━━━━━━━━━━━",
                f"{score_e} #{i}  {sym}/USDT",
                f"Скор: {r}/100  |  Качество: {grade}",
                f"",
                f"💰 Цена:      {fp(a['price'])}",
                f"📊 RSI (4H):  {rsi_e} {a['rsi_4h']:.0f}   |   Тренд: {trend_e}",
                f"",
                f"🎯 Цели (SHORT) -- R:R по TP1 1:{a.get('rr_tp1', 0):.1f}:",
                f"  TP1:  ${tp1}   ({_pct(tp1)})",
                f"  TP2:  ${tp2}   ({_pct(tp2)})",
                f"  TP3:  ${tp3}   ({_pct(tp3)})",
                f"  SL:   ${sl}    ({_pct(sl)})",
                f"",
            ]
        list_lines += [SEP, "📋 _Детальный анализ каждой монеты ниже_ ⬇️"]

        await msg.edit_text("\n".join(list_lines), parse_mode="Markdown",
                            reply_markup=nav, disable_web_page_preview=True)

        for coin, a in top_short:
            sym  = coin["symbol"]
            slug = coin.get("slug", sym.lower())
            try:
                stats = get_binance_24h(sym)
                text  = _build_signal_post(sym, a, stats, mode="short")
                await send_coin(ctx.bot, update.effective_chat.id, sym, slug, a, text)
                TOP_SHORT_SIGNALS[sym] = {
                    "time":  datetime.now(TZ), "entry": a["price"],
                    "tp1": a["tp1"], "tp2": a["tp2"], "tp3": a["tp3"],
                    "sl": a["sl"], "rr": a["rr"],
                    "status": "active", "chat_id": update.effective_chat.id,
                }
                try:
                    signal_journal.log_signal("TOP_SHORT", sym, "short", a["price"],
                                               entry_lo=a.get("entry1", a["price"]),
                                               entry_hi=a.get("entry3", a["price"]), sl=a["sl"],
                                               tp1=a["tp1"], tp2=a["tp2"], tp3=a["tp3"],
                                               rr=a["rr"], rocket_score=a.get("rocket"),
                                               ema_stack=a.get("ema_ctx"),
                                               sweep=a.get("sweep_4h") or a.get("sweep_1h"),
                                               levels_source=a.get("levels_source"), grade=a.get("_grade"),
                                               degraded_data=_data_quality_flags())
                except Exception as e:
                    log.error(f"[JOURNAL] TOP_SHORT {sym}: {e}")
                await asyncio.sleep(1.5)
            except Exception as e:
                log.error(f"top_short {sym}: {e}")

        await ctx.bot.send_message(
            update.effective_chat.id,
            "✅ *BEST TRADE — ТОП ШОРТ готов*\n\nВыбери следующее действие:",
            parse_mode="Markdown", reply_markup=main_kb()
        )
    finally:
        _scan_busy["top_short"] = False


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



async def _build_chart_v4_for_full(symbol: str, result: dict):
    """Chart v4 (chart_v4.py) для /full -- строится только если fa_engine нашёл реальный
    план сделки (block11_trade_plan.has_setup), т.к. build_trade_chart_v4/v3 требуют
    entry/SL/TP1 (см. их сигнатуру, они же используются в /coin и signal_loop). zones/
    candles_4h уже посчитаны build_full_analysis() (result["zones"]/result["candles_4h"])
    -- без доп. API-вызовов, кроме отдельных 2h-свечей под сам график (та же
    гранулярность, что и везде в проекте для свинг-сигналов, см. chart_v3.py). Фоллбек
    Chart v4 -> Chart v3 -> None (текстовая карточка отправляется в любом случае,
    картинка — бонус, а не обязательное условие)."""
    b11 = result.get("block11_trade_plan", {})
    if not b11.get("has_setup"):
        return None
    b1 = result.get("block1_bias", {})
    direction = b11["direction"]
    key_high = (b1.get("key_high") or {}).get("price")
    key_low = (b1.get("key_low") or {}).get("price")
    try:
        loop = asyncio.get_event_loop()
        candles = await loop.run_in_executor(None, get_binance_ohlc, symbol, "2h", 120)
        if not candles or len(candles) < 20:
            return None
        entry_levels = [b11["entry1"], b11["entry2"], b11["entry3"]]
        try:
            chart = chart_v4.build_trade_chart_v4(
                symbol, candles, direction, entry_levels=entry_levels,
                sl=b11["sl"], tp1=b11["tp1"], tp2=b11["tp2"], tp3=b11["tp3"],
                rr=b11["rr_tp1"], key_high=key_high, key_low=key_low, tf_label="2h",
                zones=result.get("zones"), candles_4h=result.get("candles_4h"))
            if chart is not None:
                return chart
        except Exception as e:
            log.error(f"Chart v4 FAILED (/full) {symbol}: {type(e).__name__}: {e}, falling back to Chart v3")
        return chart_v3.build_trade_chart(
            symbol, candles, direction, entry_levels=entry_levels,
            sl=b11["sl"], tp1=b11["tp1"], tp2=b11["tp2"], tp3=b11["tp3"],
            rr=b11["rr_tp1"], key_high=key_high, key_low=key_low, tf_label="2h")
    except Exception as e:
        log.error(f"Chart (/full) FAILED {symbol}: {type(e).__name__}: {e}")
        return None


async def _render_fa_result(bot, chat_id: int, symbol: str, result: dict) -> None:
    """Единый рендер результата fa_engine.build_full_analysis() -- карточка (Markdown) +
    Chart v4 (при наличии реального плана сделки) + "Разбор" (HTML, отдельным сообщением).

    ЕДИНЫЙ ИСТОЧНИК ДЛЯ /full И /coin: раньше /coin строил карточку из старого
    full_analysis()/build_signal_text() (фиксированные +4/+8/+15% TP, -15% SL, без R:R-
    гейта), а Chart v4/Разбор -- уже из fa_engine (best-effort) -- при расхождении
    направлений между движками карточка показывала LONG, а график и Разбор SHORT (или
    наоборот). Теперь и /full, и /coin вызывают ЭТУ функцию с ОДНИМ и тем же result --
    направление/entry/SL/TP/R:R везде из одного структурного расчёта, R:R-гейт ≥1:1.5 уже
    встроен в fa_engine.block11_trade_plan (has_setup=False, если гейт не пройден -- см.
    fa_engine._trade_plan()), поэтому карточка никогда не покажет R:R хуже 1.5 как готовую
    сделку."""
    card = fa_engine.render_full_analysis_card(result)
    chunks = fa_engine.split_card(card, limit=4096)

    chart = await _build_chart_v4_for_full(symbol, result)
    if chart is not None:
        try:
            chart.seek(0)
            await bot.send_photo(chat_id, photo=chart, caption=f"📊 {symbol}USDT · график сделки")
        except Exception as e:
            log.error(f"send_photo FAILED {symbol}: {type(e).__name__}: {e}")

    for i, chunk in enumerate(chunks):
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="show_menu")]]) if i == len(chunks) - 1 else None
        try:
            await bot.send_message(chat_id, chunk, parse_mode="Markdown", reply_markup=kb)
        except Exception:
            # markdown  -    ( * _ )    -
            await bot.send_message(chat_id, chunk, reply_markup=kb)

    # "Разбор" (narrative.py) -- отдельным сообщением с parse_mode="HTML" (карточка выше
    # рендерится Markdown'ом, смешивать с HTML-тегами <b> нельзя, см. docstring narrative.py).
    try:
        narrative_block = narrative.render_narrative_block(result)
        if narrative_block:
            await bot.send_message(chat_id, narrative_block, parse_mode="HTML")
    except Exception as e:
        log.error(f"narrative send failed {symbol}: {type(e).__name__}: {e}")


async def _do_full_analysis(bot, chat_id: int, symbol: str) -> bool:
    """         """
    symbol = symbol.upper().replace("USDT","").replace("BUSD","")

    coins = get_all_coins()
    coin  = next((c for c in coins if c["symbol"] == symbol), None)
    if not coin:
        coin = await _search_coin_by_symbol(symbol)
    if not coin:
        # Fallback через CoinGecko (Binance заблокирован на Railway)
        test = get_binance_ohlc(symbol, "4h", 5)
        if not test:
            await bot.send_message(chat_id,
                f" *{symbol}USDT*  \n\n : `/full BTC`  `/full SOL`",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("  ", callback_data="show_menu")
                ]]))
            return False
        price_now = test[-1]["close"]
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

    #    fa_engine ( run_in_executor,       )
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, fa_engine.build_full_analysis, symbol, coin)

    if not result.get("ok"):
        await bot.send_message(chat_id,
            f" *{symbol}USDT*:  \n{result.get('error','   ')}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("  ", callback_data="show_menu")
            ]]))
        return False

    await _render_fa_result(bot, chat_id, symbol, result)
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


async def handle_text_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает свободный текст только для кнопки «Полный анализ» (menu_full),
    которая просит прислать тикер следующим сообщением. Никаких других свободных
    текстовых сценариев в боте нет — если флаг не установлен, сообщение игнорируется."""
    if not ctx.user_data.pop("awaiting_full_symbol", False):
        return
    symbol = (update.message.text or "").strip().upper().replace("USDT", "").replace("BUSD", "")
    if not symbol or len(symbol) > 15 or not symbol.replace("/", "").isalnum():
        await update.message.reply_text("Не похоже на тикер. Пример: `BTC`", parse_mode="Markdown")
        return
    msg = await update.message.reply_text(f"🔍 Анализирую *{symbol}USDT*...", parse_mode="Markdown")
    try:
        await msg.delete()
    except Exception:
        pass
    await _do_full_analysis(ctx.bot, update.effective_chat.id, symbol)


async def cmd_myid(update: Update, ctx):
    uid = update.effective_user.id
    cid = update.effective_chat.id
    await update.message.reply_text(
        f" * User ID:* `{uid}`\n *Chat ID:* `{cid}`",
        parse_mode="Markdown"
    )

def add_top_short_signal(sym: str, entry: dict):
    """Добавляет сигнал в TOP_SHORT_SIGNALS только если символа там ещё нет — Памп-радар
    не перезаписывает руками ведённые/уже активные сигналы, только дописывает новые."""
    if sym in TOP_SHORT_SIGNALS:
        return False
    entry = dict(entry)
    entry["time"] = datetime.now(TZ)
    TOP_SHORT_SIGNALS[sym] = entry
    _save_signals()
    try:
        price = entry.get("entry")
        signal_journal.log_signal("PUMP_RADAR", sym, "short", price,
                                   entry_lo=price, entry_hi=price, sl=entry.get("sl"),
                                   tp1=entry.get("tp1"), tp2=entry.get("tp2"),
                                   rr=entry.get("rr"), degraded_data=_data_quality_flags())
    except Exception as e:
        log.error(f"[JOURNAL] PUMP_RADAR short {sym}: {e}")
    return True

def add_top_long_signal(sym: str, entry: dict):
    """Зеркало add_top_short_signal — для кнопки 'Добавить в ТОП ЛОНГ' на дамп-алертах
    Памп-радара. Append-only, тот же guard: не трогаем уже существующий символ."""
    if sym in TOP_LONG_SIGNALS:
        return False
    entry = dict(entry)
    entry["time"] = datetime.now(TZ)
    TOP_LONG_SIGNALS[sym] = entry
    _save_signals()
    try:
        price = entry.get("entry")
        signal_journal.log_signal("PUMP_RADAR", sym, "long", price,
                                   entry_lo=price, entry_hi=price, sl=entry.get("sl"),
                                   tp1=entry.get("tp1"), tp2=entry.get("tp2"),
                                   rr=entry.get("rr"), degraded_data=_data_quality_flags())
    except Exception as e:
        log.error(f"[JOURNAL] PUMP_RADAR long {sym}: {e}")
    return True

async def cmd_radar_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Owner-only самодиагностика Памп-радара: статус WS-слоёв, покрытие, watch, аптайм."""
    import os
    owner_id = int(os.getenv("OWNER_CHAT_ID", "7009350191"))
    if update.effective_user.id != owner_id:
        return

    from pump_detector import get_radar_status
    st = get_radar_status()

    def _conn_line(label, connected, ago):
        emoji = "\U0001f7e2" if connected else "\U0001f534"
        if ago is None:
            ago_str = "нет данных"
        else:
            ago_str = f"{ago:.0f} сек назад"
        return f"{emoji} {label}: {'подключён' if connected else 'не подключён'}, последний пакет {ago_str}"

    uptime = int(st["uptime_sec"])
    h, rem = divmod(uptime, 3600)
    m, s = divmod(rem, 60)

    lines = [
        "*Памп-радар — статус*",
        "",
        _conn_line("coarse (Bybit tickers)", st["coarse_connected"], st["coarse_last_packet_sec_ago"]),
        _conn_line("kline", st["kline_connected"], st["kline_last_packet_sec_ago"]),
        "",
        f"Покрытие coarse: {st['coarse_symbols']} символов, принято пакетов: {st['coarse_msg_count']}, "
        f"реконнектов: {st['coarse_reconnect_count']}",
        f"Kline-подписка: {st['kline_symbols']} символов, принято сообщений: {st['kline_msg_count']}",
    ]
    if st["coarse_symbols"] == 0:
        lines.append(
            f"⚠️ Bybit instruments-info: попыток {st['coarse_discovery_attempts']}, "
            f"последняя ошибка: {st['coarse_discovery_last_error'] or '—'}")
    lines += [
        "",
        f"Активных WATCHING (памп): {st['pump_watch_count']}"
        + (f" — {', '.join(st['pump_watch_symbols'])}" if st['pump_watch_symbols'] else ""),
        f"Активных WATCHING (дамп): {st['dump_watch_count']}"
        + (f" — {', '.join(st['dump_watch_symbols'])}" if st['dump_watch_symbols'] else ""),
        "",
        f"История (24ч буфер): {st['history_count']}/{st['history_maxlen']}",
        f"Аптайм: {h}ч {m}м {s}с",
    ]
    journal_active, journal_closed = signal_journal.get_status_counts()
    lines.append(f"Journal: {journal_active} активных, {journal_closed} закрытых")

    sub_status = subscribers.status()
    sub_src_emoji = {"github": "🟢", "fallback": "🟡", "none": "🔴"}.get(sub_status["source"], "⚪")
    lines.append(f"Подписчики: {sub_status['count']} ({sub_src_emoji} источник: {sub_status['source']})")
    if sub_status.get("github_error"):
        lines.append(f"  ⚠️ GitHub: {sub_status['github_error']}")

    if _last_auto_scan["ts"]:
        ago_min = (time.time() - _last_auto_scan["ts"]) / 60
        lines.append(
            f"Автосигналы: последний тик {ago_min:.0f} мин назад -- {_last_auto_scan['status']}"
            + (f" ({_last_auto_scan['sent_long']} лонг/{_last_auto_scan['sent_short']} шорт "
               f"из {_last_auto_scan['candidates_long']}/{_last_auto_scan['candidates_short']} кандидатов)"
               if _last_auto_scan["status"] == "ok" else "")
        )
    else:
        lines.append("Автосигналы: ещё не запускались в этом процессе")

    ds = get_data_source_status()
    def _src_line(name, label):
        s = ds.get(name, {})
        if s.get("ok") is None:
            return f"⚪ {label}: не проверялся в этом запуске"
        if s.get("ok"):
            return f"🟢 {label}: ok"
        return f"🔴 {label}: ошибка — {s.get('last_error') or '—'}"
    lines += [
        "",
        "*Источники рангов/mcap:*",
        _src_line("coingecko_markets", "CoinGecko markets"),
        _src_line("cmc", "CMC (фоллбек)"),
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_whales(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Owner-only: /whales <symbol> -- топ whale-зоны символа (Whale Radar Блок 3,
    см. WHALE_RADAR_NOTES.md). НЕ путать с существующим "🐋 Whale Monitor"
    (callback_data=whale_status) -- та фича про OI/funding/L-S-ratio институциональный
    скоринг, эта -- про крупные лимитки/сделки в стакане, независимые источники данных.
    Читает ТЕКУЩЕЕ состояние Whale Radar (что накоплено с момента старта процесса), не
    делает новых сетевых запросов -- если контур только что стартовал или символ вне
    топ-N по обороту, зон может не быть, честно об этом сказано, не выдумано.
    Только показ данных -- не влияет ни на какой боевой сигнал/гейт."""
    import os
    owner_id = int(os.getenv("OWNER_CHAT_ID", "7009350191"))
    if update.effective_user.id != owner_id:
        return
    if not ctx.args:
        await update.message.reply_text("Использование: `/whales BTC`", parse_mode="Markdown")
        return
    symbol = ctx.args[0].upper().replace("USDT", "") + "USDT"
    zones = get_whale_zones(symbol)
    all_zones = [dict(z, side=side) for side in ("bid", "ask") for z in zones.get(side, [])]
    if not all_zones:
        await update.message.reply_text(
            f"🐋 *Whale Radar — {symbol}*\n\n"
            f"Whale-зон пока нет — либо символ вне топ-{whale_radar.TOP_N_SYMBOLS} по "
            f"обороту (Whale Radar отслеживает только их), либо контур ещё не накопил "
            f"данные с момента старта процесса.",
            parse_mode="Markdown")
        return
    all_zones.sort(key=lambda z: z["total_usd"], reverse=True)

    def _age_str(age_sec):
        if age_sec is None:
            return "возраст неизвестен"
        h, rem = divmod(int(age_sec), 3600)
        m, _ = divmod(rem, 60)
        return f"{h}ч{m}м" if h else f"{m}м"

    lines = [f"🐋 *Whale Radar — {symbol}*", ""]
    for z in all_zones[:10]:
        side_label = "🟢 БИД" if z["side"] == "bid" else "🔴 АСК"
        price_str = (f"{ta_extra.smart_round(z['price_lo'])}"
                     if z["price_lo"] == z["price_hi"]
                     else f"{ta_extra.smart_round(z['price_lo'])}–{ta_extra.smart_round(z['price_hi'])}")
        lines.append(
            f"{side_label} `{price_str}` — ${z['total_usd']:,.0f} "
            f"({z['level_count']} уровн., держится {_age_str(z.get('age_sec'))})"
        )
    lines += [
        "",
        "_Только чтение, не влияет на боевые сигналы/гейты — источник для shadow-"
        "confluence (Патч 06, см. WHALE_RADAR_NOTES.md/SHADOW_MODE.md)._"
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_zones(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Owner-only: /zones -- активные зоны дневной разметки (level_watch.py,
    journal/watch_zones.json) + дата/источник разметки. Только показ данных -- не
    влияет ни на какой боевой сигнал/гейт."""
    import os
    owner_id = int(os.getenv("OWNER_CHAT_ID", "7009350191"))
    if update.effective_user.id != owner_id:
        return
    config = level_watch.load_watch_zones()
    updated = config.get("updated") or "нет данных"
    source = config.get("source") or "нет данных"
    lines = [f"📋 *Активные зоны* — {source}, разметка от {updated}", ""]
    any_zones = False
    for symbol, zones in config.items():
        if symbol in ("updated", "source") or not isinstance(zones, list):
            continue
        if not zones:
            continue
        any_zones = True
        lines.append(f"*{symbol}*")
        for z in sorted(zones, key=lambda z: (z["side"] != "LONG", z.get("prio", 99))):
            note = f" — {z['note']}" if z.get("note") else ""
            lines.append(f"  {z['side']} `{z['lo']}–{z['hi']}` (prio {z.get('prio', '?')}){note}")
        lines.append("")
    if not any_zones:
        lines.append("Зон нет.")
    lines.append("_Только чтение, не сигнал/гейт — информационный вотчер._")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_zones_set(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Owner-only: /zones_set {...json...} -- ПОЛНОСТЬЮ заменяет активную разметку
    (journal/watch_zones.json), чтобы владелец обновлял с телефона без Claude Code.
    Старая версия архивируется в journal/watch_zones_history/ автоматически (см.
    level_watch.replace_watch_zones), затем best-effort пуш в GitHub (переживает
    редеплой -- иначе следующий git push с этой сессии стёр бы правку владельца)."""
    import json
    import os
    import time
    owner_id = int(os.getenv("OWNER_CHAT_ID", "7009350191"))
    if update.effective_user.id != owner_id:
        return
    raw_parts = update.message.text.split(None, 1)
    if len(raw_parts) < 2 or not raw_parts[1].strip():
        await update.message.reply_text(
            "Использование: `/zones_set {\"updated\":\"YYYY-MM-DD\",\"source\":\"...\","
            "\"ETHUSDT\":[{\"side\":\"LONG\",\"lo\":1.0,\"hi\":2.0,\"prio\":1}]}`\n\n"
            "Полностью заменяет активные зоны (не дописывает) — старая версия "
            "архивируется автоматически.",
            parse_mode="Markdown")
        return
    try:
        new_config = json.loads(raw_parts[1])
    except Exception as e:
        await update.message.reply_text(f"❌ Не удалось разобрать JSON: {e}")
        return
    if not isinstance(new_config, dict):
        await update.message.reply_text("❌ Ожидался JSON-объект (dict) верхнего уровня.")
        return
    if "updated" not in new_config:
        new_config["updated"] = time.strftime("%Y-%m-%d")
    ok = level_watch.replace_watch_zones(new_config)
    if not ok:
        await update.message.reply_text("❌ Не удалось сохранить новый конфиг локально (см. логи процесса).")
        return
    github_ok = await level_watch.sync_watch_zones_to_github(new_config)
    github_note = "" if github_ok else "\n⚠️ GitHub-синк не удался (правка переживёт этот процесс, но НЕ переживёт редеплой — см. логи)."
    await update.message.reply_text(
        f"✅ Зоны заменены, разметка от {new_config.get('updated')}. Старая версия в архиве."
        f"{github_note}")


async def cmd_health(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Owner-only общий health-check процесса (в отличие от /radar_status -- тот покрывает
    только памп-радар). Аптайм + heartbeat всех фоновых задач + источники данных +
    подписчики/журнал. Честно показывает "ещё не тикала" вместо придуманного статуса."""
    import os
    owner_id = int(os.getenv("OWNER_CHAT_ID", "7009350191"))
    if update.effective_user.id != owner_id:
        return

    uptime = int(time.time() - _PROCESS_START_TS)
    h, rem = divmod(uptime, 3600)
    m, s = divmod(rem, 60)
    lines = [
        f"*BEST TRADE {BOT_VERSION} — /health*",
        "",
        f"Аптайм процесса: {h}ч {m}м {s}с",
        "",
        "*Фоновые задачи (heartbeat):*",
    ]
    now = time.time()
    for name, expected in _job_expected_interval_sec.items():
        hb = _job_heartbeats.get(name)
        if hb is None:
            lines.append(f"⚪ {name}: ещё не тикала в этом процессе")
            continue
        age = now - hb["ts"]
        stale = bool(expected) and age > expected * 2
        emoji = "🔴" if not hb["ok"] else ("🟡" if stale else "🟢")
        detail = f" — {hb['detail']}" if hb.get("detail") else ""
        lines.append(f"{emoji} {name}: последний тик {age/60:.0f} мин назад{detail}")

    lines += ["", "*Источники данных:*"]
    ds = get_data_source_status()
    for name, label in [("coingecko_markets", "CoinGecko markets"), ("coingecko_global", "CoinGecko global"),
                         ("cmc", "CMC listings (опц. фоллбек)"), ("cmc_global_metrics", "CMC global (опц. фоллбек)"),
                         ("yahoo_finance", "Yahoo (DXY/S&P/Gold/VIX)")]:
        s = ds.get(name, {})
        # ROADMAP 2026-07-10 (решение владельца): CMC-источники опциональны -- их отказ
        # НЕ деградация, если CoinGecko жив, поэтому жёлтый, не красный.
        if s.get("ok") is None:
            lines.append(f"⚪ {label}: не проверялся в этом запуске")
        elif s.get("ok"):
            lines.append(f"🟢 {label}: ok")
        elif name in _OPTIONAL_SOURCES:
            lines.append(f"🟡 {label}: отключён (опционально) — {s.get('last_error') or '—'}")
        else:
            fails = s.get("consecutive_failures", 0)
            fails_str = f" ({fails} подряд)" if fails > 1 else ""
            lines.append(f"🔴 {label}: {s.get('last_error') or '—'}{fails_str}")

    sub_status = subscribers.status()
    lines += ["", f"Подписчики: {sub_status['count']} (источник: {sub_status['source']})"]
    journal_active, journal_closed = signal_journal.get_status_counts()
    lines.append(f"Journal: {journal_active} активных, {journal_closed} закрытых")
    lines.append("\nПодробности памп-радара: /radar_status")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_journal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Owner-only сводка Signal Journal: 24ч/7д/всё время — entered %, win rate, средний R,
    разбивка по источникам."""
    import os
    owner_id = int(os.getenv("OWNER_CHAT_ID", "7009350191"))
    if update.effective_user.id != owner_id:
        return

    def _fmt_window(window_sec, label):
        s = signal_journal.get_journal_summary(window_sec)
        if s["total"] == 0:
            return f"*{label}:* нет сигналов"
        lines = [f"*{label}:* всего {s['total']}"]
        entered_str = f"{s['entered_pct']}%" if s["entered_pct"] is not None else "—"
        lines.append(f"  Entered: {entered_str}")
        if s["win_rate"] is not None:
            lines.append(f"  Win rate: {s['win_rate']}% ({s['wins']}W/{s['losses']}L)")
        else:
            lines.append("  Win rate: — (нет закрытых)")
        avg_r_str = f"{s['avg_r']:+.2f}" if s["avg_r"] is not None else "—"
        lines.append(f"  Средний факт. R: {avg_r_str}")
        if s.get("expectancy_r") is not None:
            awr = f"{s['avg_win_r']:+.2f}" if s.get("avg_win_r") is not None else "—"
            alr = f"{s['avg_loss_r']:+.2f}" if s.get("avg_loss_r") is not None else "—"
            lines.append(f"  Expectancy: {s['expectancy_r']:+.2f}R (avg win {awr} / avg loss {alr})")
        if s.get("degraded_count"):
            lines.append(f"  ⚠️ С деградировавшими данными: {s['degraded_count']} ({s['degraded_pct']}%)")
        if s["by_source"]:
            src_str = ", ".join(f"{k}: {v['total']}" for k, v in sorted(s["by_source"].items()))
            lines.append(f"  По источникам: {src_str}")
        by_grade = s.get("by_grade", {})
        grade_parts = [f"{g}: {by_grade[g]['win_rate']}% ({by_grade[g]['total']})"
                       for g in ("A+", "A", "B") if g in by_grade]
        if grade_parts:
            lines.append(f"  По грейдам: {', '.join(grade_parts)}")
        return "\n".join(lines)

    sl_stats = signal_journal.get_stats_for_source("signal_loop")
    sl_active = signal_loop.get_active_count()
    if sl_stats["closed"]:
        sl_line = (f"*Сигнальный контур (signal_loop):* {sl_active} активных, "
                   f"{sl_stats['closed']} закрытых, win rate {sl_stats['win_rate']}% "
                   f"({sl_stats['wins']}W/{sl_stats['closed']-sl_stats['wins']}L)")
    else:
        sl_line = f"*Сигнальный контур (signal_loop):* {sl_active} активных, закрытых пока нет"

    # Расширенная аналитика (ROADMAP П2, доп. пункт очереди) -- по монетам/времени суток/
    # losing streak, за всё время (короткие окна дают слишком мало данных на монету/час).
    ext = signal_journal.get_extended_analytics(None)
    ext_lines = ["*Доп. аналитика (всё время):*"]
    if ext["max_losing_streak"]:
        ext_lines.append(f"  Max losing streak: {ext['max_losing_streak']} подряд")
    top_symbols = sorted(ext["by_symbol"].items(), key=lambda kv: -kv[1]["total"])[:5]
    if top_symbols:
        sym_str = ", ".join(f"{sym}: {v['win_rate']}% ({v['total']})" for sym, v in top_symbols)
        ext_lines.append(f"  По монетам (топ-5 по числу сделок): {sym_str}")
    if ext["by_hour"]:
        best_hour = max(ext["by_hour"].items(), key=lambda kv: (kv[1]["win_rate"] or 0, kv[1]["total"]))
        worst_hour = min(ext["by_hour"].items(), key=lambda kv: (kv[1]["win_rate"] if kv[1]["win_rate"] is not None else 101, -kv[1]["total"]))
        ext_lines.append(f"  Час (TZ бота), лучший: {best_hour[0]}:00 — {best_hour[1]['win_rate']}% "
                          f"({best_hour[1]['total']}), худший: {worst_hour[0]}:00 — "
                          f"{worst_hour[1]['win_rate']}% ({worst_hour[1]['total']})")
    ext_block = "\n".join(ext_lines) if len(ext_lines) > 1 else None

    text_parts = [
        "*Signal Journal — статистика*",
        _fmt_window(24 * 3600, "24ч"),
        _fmt_window(7 * 24 * 3600, "7д"),
        _fmt_window(None, "Всё время"),
        sl_line,
    ]
    if ext_block:
        text_parts.append(ext_block)
    text = "\n\n".join(text_parts)
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Owner-only (ROADMAP П2): сигналов за неделю, win-rate по источникам и режимам,
    сравнение с предыдущей неделей ("деградация vs прошлый период" -- показываем ЧИСЛА
    и дельту, не подставляем оценочное слово "деградация"/"улучшение" без явного порога,
    см. Протокол правды п.6 -- решение, что считать деградацией, за владельцем)."""
    import os
    owner_id = int(os.getenv("OWNER_CHAT_ID", "7009350191"))
    if update.effective_user.id != owner_id:
        return

    WEEK = 7 * 24 * 3600
    now = time.time()
    this_week = signal_journal.get_journal_summary(WEEK, end_ts=now)
    prev_week = signal_journal.get_journal_summary(WEEK, end_ts=now - WEEK)

    lines = ["*/stats — неделя*", ""]
    lines.append(f"Сигналов за 7д: {this_week['total']} (прошлые 7д: {prev_week['total']})")

    def _wr(s):
        return f"{s['win_rate']}%" if s["win_rate"] is not None else "—"

    wr_this, wr_prev = this_week["win_rate"], prev_week["win_rate"]
    if wr_this is not None and wr_prev is not None:
        delta = round(wr_this - wr_prev, 1)
        arrow = "📈" if delta > 0 else ("📉" if delta < 0 else "➡️")
        lines.append(f"Win rate: {_wr(this_week)} (прошлая неделя: {_wr(prev_week)}, {arrow} {delta:+.1f}п.п.)")
    else:
        lines.append(f"Win rate: {_wr(this_week)} (прошлая неделя: {_wr(prev_week)})")

    avg_r_this = f"{this_week['avg_r']:+.2f}" if this_week["avg_r"] is not None else "—"
    avg_r_prev = f"{prev_week['avg_r']:+.2f}" if prev_week["avg_r"] is not None else "—"
    lines.append(f"Средний факт. R: {avg_r_this} (прошлая неделя: {avg_r_prev})")

    if this_week.get("expectancy_r") is not None:
        awr = f"{this_week['avg_win_r']:+.2f}" if this_week.get("avg_win_r") is not None else "—"
        alr = f"{this_week['avg_loss_r']:+.2f}" if this_week.get("avg_loss_r") is not None else "—"
        lines.append(f"Expectancy: {this_week['expectancy_r']:+.2f}R (avg win {awr} / avg loss {alr})")

    if this_week["by_source"]:
        lines.append("\n*По источникам (7д):*")
        for src, agg in sorted(this_week["by_source"].items()):
            wr = f"{agg['win_rate']}%" if agg["win_rate"] is not None else "—"
            lines.append(f"  {src}: {agg['total']} сигналов, win rate {wr}")
    else:
        lines.append("\nПо источникам: нет сигналов за 7д")

    if this_week["by_regime"]:
        lines.append("\n*По рыночному режиму (7д, закрытые с исходом):*")
        for reg, agg in sorted(this_week["by_regime"].items()):
            wr = f"{agg['win_rate']}%" if agg["win_rate"] is not None else "—"
            lines.append(f"  {reg}: {agg['total']} сигналов, win rate {wr}")
    else:
        lines.append("\nПо режиму: нет закрытых сигналов за 7д")

    rejected = signal_journal.get_rejected_summary(WEEK)
    if rejected["total"]:
        rej_str = ", ".join(f"{k}: {v}" for k, v in sorted(rejected["by_source"].items()))
        lines.append(f"\nОтклонено гейтами за 7д: {rejected['total']} ({rej_str})")

    lines.append("\nПодробнее по всем окнам (24ч/7д/всё время): /journal")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_journal_sync(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Owner-only: форсирует немедленный коммит Signal Journal в GitHub (в обход
    5-минутного рейт-лимита) -- для проверки персистентности или перед плановым
    редеплоем."""
    import os
    owner_id = int(os.getenv("OWNER_CHAT_ID", "7009350191"))
    if update.effective_user.id != owner_id:
        return
    status = await signal_journal.force_sync()
    if not status["configured"]:
        await update.message.reply_text(
            "⚠️ GitHub-персистентность не настроена -- нет GITHUB_TOKEN/GITHUB_OWNER/GITHUB_REPO "
            "в переменных окружения. Журнал работает только локально (ephemeral).")
        return
    if not status["success"]:
        text = (
            f"❌ Коммит НЕ выполнен (ошибка GitHub API)\n"
            f"Записей в журнале: {status['records']}\n"
            f"Ошибка: `{status.get('error') or 'неизвестна'}`"
        )
    elif not status["was_dirty"]:
        text = f"ℹ️ Пропущено — нечего сохранять\nЗаписей в журнале: {status['records']}"
    else:
        text = (
            f"✅ Коммит выполнен\n"
            f"Записей в журнале: {status['records']}\n"
            f"GitHub sha: `{status.get('sha') or '—'}`"
        )
    await update.message.reply_text(text, parse_mode="Markdown")


async def _startup_integrity_check(bot: Bot, owner_id: int):
    """ROADMAP П1.3 -- одно сообщение владельцу при старте вместо разрозненных: что реально
    проверено (подписчики/журнал загружены, CoinGecko отвечает), а не выдуманное "всё ок".
    Не бросает исключений наружу -- сбой самой проверки не должен мешать боту стартовать."""
    lines = [f"🚀 *BEST TRADE {BOT_VERSION} запущен*", ""]
    try:
        sub_status = subscribers.status()
        src_emoji = {"github": "🟢", "fallback": "🟡", "none": "🔴"}.get(sub_status["source"], "⚪")
        lines.append(f"{src_emoji} Подписчики: {sub_status['count']} (источник: {sub_status['source']})")
        if sub_status.get("github_error"):
            lines.append(f"  ⚠️ {sub_status['github_error']}")
    except Exception as e:
        lines.append(f"🔴 Подписчики: проверка упала ({str(e)[:150]})")

    try:
        j_active, j_closed = signal_journal.get_status_counts()
        lines.append(f"🟢 Journal: {j_active} активных, {j_closed} закрытых")
    except Exception as e:
        lines.append(f"🔴 Journal: проверка упала ({str(e)[:150]})")

    try:
        loop = asyncio.get_event_loop()
        test = await loop.run_in_executor(None, _fetch_coingecko_markets, 1, 5)
        lines.append(f"🟢 CoinGecko: ok ({len(test)} монет получено)" if test
                      else "🔴 CoinGecko: пустой ответ")
    except Exception as e:
        lines.append(f"🔴 CoinGecko: {str(e)[:150]}")

    try:
        await bot.send_message(owner_id, "\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        print(f"_startup_integrity_check: не удалось отправить сообщение владельцу: {e}")


async def _start_pump_detector(app):
    """post_init hook — запускает pump_detector (kline-слой) и грубый Bybit tickers-детект
    (полное покрытие рынка) в том же event loop, что и бот."""
    import os
    from pump_detector import run_pump_detector, run_miniticker_stream, PumpContext
    owner_id = int(os.getenv("OWNER_CHAT_ID", "7009350191"))

    def _get_coin(sym):
        return next((c for c in get_all_coins() if c.get("symbol") == sym), None)

    ctx = PumpContext(
        app.bot, owner_id, _get_coin, full_analysis,
        pro_analysis=pro_analysis,
        get_killzone_status=get_killzone_status,
        get_funding_pct=_get_funding_pct,
        get_oi_usd=_get_oi_usd,
        get_oi_change=_get_oi_change,
        add_top_short_signal=add_top_short_signal,
        get_ohlc=get_binance_ohlc,
    )
    asyncio.create_task(run_pump_detector(ctx))
    asyncio.create_task(run_miniticker_stream(ctx))
    asyncio.create_task(_whale_radar_task(app.bot, owner_id))
    asyncio.create_task(_level_watch_task(app.bot, owner_id))

    signal_journal.init(app.bot, owner_id)
    await signal_journal.startup_sync()
    asyncio.create_task(signal_journal.run_tracker())
    asyncio.create_task(signal_journal.run_github_sync_loop())

    await subscribers.startup_sync()

    # Планировщик (в т.ч. send_scheduled/whale_monitor с next_run_time=now(), т.е. первый
    # тик почти сразу) регистрируется ЗДЕСЬ, а не в main() -- раньше scheduler.start()
    # вызывался в main() ДО run_polling(), а post_init (эта функция, с await
    # subscribers.startup_sync()/signal_journal.startup_sync()) выполняется УЖЕ ВНУТРИ
    # run_polling(). Из-за этого немедленный первый тик send_scheduled/whale_monitor мог
    # реально выполниться раньше, чем подписчики/журнал успевали загрузиться -- поймано
    # живьём: send_scheduled увидел 0 подписчиков на процессе возрастом 38 секунд, хотя
    # подписчик (fallback-владелец) появился мгновением позже. _load_signals() -- по той
    # же причине сюда же, до первого тика.
    _load_signals()
    scheduler = AsyncIOScheduler(timezone=TZ)

    # Heartbeat-обёртки (ROADMAP П1) -- регистрируем в планировщике heartbeat_* вместо
    # голых функций, сами задачи и их решения не меняются (см. _heartbeat_wrapper).
    _job_expected_interval_sec["send_scheduled"] = 30 * 60
    _job_expected_interval_sec["check_alerts"] = 5 * 60
    _job_expected_interval_sec["whale_monitor"] = 15 * 60
    _job_expected_interval_sec["signal_loop"] = signal_loop.STAGE1_INTERVAL_MIN * 60
    _job_expected_interval_sec["exit_tracker"] = signal_loop.EXIT_TRACKER_INTERVAL_MIN * 60

    scheduler.add_job(
        _heartbeat_wrapper("send_scheduled", send_scheduled),
        "interval",
        minutes=30,
        args=[app.bot],
        next_run_time=datetime.now(TZ)
    )
    scheduler.add_job(
        _heartbeat_wrapper("check_alerts", check_alerts),
        "interval",
        minutes=5,
        args=[app.bot]
    )
    scheduler.add_job(
        _heartbeat_wrapper("whale_monitor", whale_monitor),
        "interval",
        minutes=15,
        args=[app.bot],
        next_run_time=datetime.now(TZ)
    )
    # BUY/SELL сигнальный контур (signal_loop.py). Передаём sys.modules[__name__]
    # (текущий реально исполняющийся модуль, __name__=="__main__" при запуске
    # `python bot.py") -- а не `import bot" изнутри signal_loop.py, иначе Python
    # создал бы ВТОРОЙ независимый экземпляр модуля bot (т.к. "bot" ещё не
    # зарегистрирован в sys.modules при запуске как __main__) со своими копиями
    # кэшей/rate-limiter'ов, отдельными от того, что используют все остальные
    # хендлеры -- см. докстринг signal_loop.py.
    scheduler.add_job(
        _heartbeat_wrapper("signal_loop", signal_loop.run_signal_loop),
        "interval",
        minutes=signal_loop.STAGE1_INTERVAL_MIN,
        args=[sys.modules[__name__], app.bot, owner_id],
    )
    scheduler.add_job(
        _heartbeat_wrapper("exit_tracker", signal_loop.run_exit_tracker),
        "interval",
        minutes=signal_loop.EXIT_TRACKER_INTERVAL_MIN,
        args=[sys.modules[__name__], app.bot],
    )
    # Watchdog (ROADMAP П1.2) -- следит за heartbeat выше, шлёт владельцу алерт, если
    # какая-то из задач замолчала дольше 2x своего интервала.
    scheduler.add_job(
        run_watchdog,
        "interval",
        minutes=10,
        args=[app.bot],
    )
    # Дневной версионированный бэкап БД (ROADMAP П1.4) -- в 03:00 по TZ бота, вне часов
    # активности рынка/владельца, не конфликтует по времени с часовым GitHub sync журнала.
    scheduler.add_job(
        run_daily_backup,
        "cron",
        hour=3, minute=0,
        args=[app.bot],
    )
    # «Метрики дня» (АПГРЕЙД 11.07, Этап 4) -- ежедневная сводка owner-чату 21:00 по
    # TZ бота (Europe/Istanbul == UTC+3). Тот же cron-паттерн, что run_daily_backup
    # выше -- переживает рестарт процесса нативно: job регистрируется заново на
    # каждом старте post_init(), а не хранится где-то в persisted-состоянии, которое
    # можно потерять. НЕ завязан на сессию Claude Code никаким образом -- это APScheduler
    # внутри самого боевого процесса на Railway.
    scheduler.add_job(
        daily_metrics.send_daily_digest,
        "cron",
        hour=daily_metrics.DIGEST_HOUR_UTC3, minute=0,
        args=[app.bot, owner_id],
    )
    scheduler.start()

    # Стартовый integrity-check (ROADMAP П1.3) -- ОДНО консолидированное сообщение
    # владельцу вместо разрозненных, только то, что реально проверено в этом запуске
    # (не подставляет "ок" туда, где проверка не делалась -- см. Протокол правды).
    await _startup_integrity_check(app.bot, owner_id)

def main():
    # concurrent_updates=True -- иначе PTB диспетчит апдейты СТРОГО последовательно и не
    # начнёт обрабатывать новый (например /start), пока не завершится ТЕКУЩИЙ хендлер
    # целиком, даже если тот сам не блокирует event loop (run_in_executor помогает не
    # морозить сам event loop для фоновых задач, но без этого флага не спасает от
    # последовательной очереди самого PTB).
    app = (Application.builder().token(BOT_TOKEN)
           .post_init(_start_pump_detector)
           .concurrent_updates(True)
           .build())
    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("stop",      cmd_stop))
    app.add_handler(CommandHandler("myid",      cmd_myid))
    app.add_handler(CommandHandler("radar_status", cmd_radar_status))
    app.add_handler(CommandHandler("whales",       cmd_whales))
    app.add_handler(CommandHandler("zones",        cmd_zones))
    app.add_handler(CommandHandler("zones_set",    cmd_zones_set))
    app.add_handler(CommandHandler("health",       cmd_health))
    app.add_handler(CommandHandler("journal",   cmd_journal))
    app.add_handler(CommandHandler("journal_sync", cmd_journal_sync))
    app.add_handler(CommandHandler("stats",     cmd_stats))
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
    app.add_handler(CommandHandler("x100",      cmd_x100_scanner))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input))

    # Планировщик регистрируется в post_init (_start_pump_detector), ПОСЛЕ
    # subscribers.startup_sync()/signal_journal.startup_sync() -- см. комментарий там же
    # про гонку немедленного первого тика (next_run_time=now()) с загрузкой подписчиков.
    log.info(" BEST TRADE v32.0 | Supply/Demand | Real-time signals | UTC+3")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

_macro_cache={}
_macro_ts=0.0
_opts_cache={}
_opts_ts=0.0
_liq_cache={}
_liq_ts=0.0

def get_macro_data():
    import time as _t
    global _macro_cache, _macro_ts
    if _t.time()-_macro_ts<600 and _macro_cache: return _macro_cache
    res={'ok':False,'dxy':0,'gold':0,'sp500':0,'vix':0,'dxy_ch':0,'gold_ch':0,'sp500_ch':0,'vix_ch':0,'macro_signal':'neutral','macro_score':0}
    try:
        sc=0
        # Гейт/веса ниже (sc+=...) -- НЕ ТРОГАТЬ без отдельного одобрения (сигнальная
        # логика/скоринг). Изменён только сам фетч (см. _fetch_yahoo_chart, ROADMAP П3) --
        # retry + логирование вместо тихого bare except.
        for tk,k in [('DX-Y.NYB','dxy'),('GC=F','gold'),('^GSPC','sp500'),('^VIX','vix')]:
            cl,_ = _fetch_yahoo_chart(tk, range_str="2d")
            if cl and len(cl)>=2:
                ch=(cl[-1]-cl[-2])/cl[-2]*100
                res[k]=round(cl[-1],2); res[f'{k}_ch']=round(ch,2)
                if k=='dxy': sc+=(-2 if ch>0.3 else (2 if ch<-0.3 else 0))
                elif k=='sp500': sc+=(2 if ch>0.5 else (-2 if ch<-1 else 0))
                elif k=='vix': sc+=(-3 if cl[-1]>30 else (2 if cl[-1]<15 else 0))
        res['ok']=True; res['macro_score']=sc
        res['macro_signal']='bullish' if sc>=3 else ('bearish' if sc<=-3 else 'neutral')
    except Exception as e:
        print(f"get_macro_data: {e}")
    _macro_cache=res; _macro_ts=_t.time(); return res

def _parse_deribit_option_name(name: str):
    """'BTC-27DEC24-70000-C' -> (70000.0, 'C'). None, если формат неожиданный
    (не 4 сегмента через '-' или страйк не число) -- honest skip, не догадка."""
    parts = name.split("-")
    if len(parts) != 4:
        return None
    try:
        strike = float(parts[2])
    except ValueError:
        return None
    opt_type = parts[3]
    if opt_type not in ("C", "P"):
        return None
    return strike, opt_type


def compute_max_pain(items: list) -> float:
    """Max Pain -- страйк, при котором суммарная выплата держателям опционов (по
    открытому интересу) минимальна, т.е. точка, где маркет-мейкеры/продавцы теряют
    меньше всего. Этап 3.3 (АПГРЕЙД 11.07). УПРОЩЕНИЕ, честно: считает по ВСЕМ
    экспирациям сразу (объединённый OI по страйку), не по ближайшей экспирации
    отдельно -- отдельный расчёт по экспирациям (что ближе к тому, как считают
    специализированные Max Pain калькуляторы) не сделан в рамках Этапа 3, это
    известное упрощение, не скрыто. Возвращает None, если нет опционных данных."""
    strikes_oi = {}
    for it in items:
        parsed = _parse_deribit_option_name(it.get("instrument_name", ""))
        if not parsed:
            continue
        strike, opt_type = parsed
        oi = it.get("open_interest", 0) or 0
        d = strikes_oi.setdefault(strike, {"C": 0.0, "P": 0.0})
        d[opt_type] += oi
    if not strikes_oi:
        return None
    best_strike, best_pain = None, None
    for candidate in sorted(strikes_oi):
        pain = 0.0
        for strike, oi in strikes_oi.items():
            if strike < candidate:
                pain += oi["C"] * (candidate - strike)   # call ITM at settlement=candidate
            elif strike > candidate:
                pain += oi["P"] * (strike - candidate)   # put ITM at settlement=candidate
        if best_pain is None or pain < best_pain:
            best_pain, best_strike = pain, candidate
    return best_strike


def get_options_data():
    import time as _t, requests as _r
    global _opts_cache, _opts_ts
    if _t.time()-_opts_ts<600 and _opts_cache: return _opts_cache
    res={'ok':False,'put_call_ratio':1.0,'iv_1m':0,'options_signal':'neutral','total_oi_calls':0,'total_oi_puts':0,'max_pain':None}
    try:
        r=_r.get('https://www.deribit.com/api/v2/public/get_book_summary_by_currency',params={'currency':'BTC','kind':'option'},timeout=8)
        if r.status_code==200:
            items=r.json().get('result',[])
            co=sum(i.get('open_interest',0) for i in items if 'C' in i.get('instrument_name',''))
            po=sum(i.get('open_interest',0) for i in items if 'P' in i.get('instrument_name',''))
            if co>0:
                pc=po/co; res.update({'put_call_ratio':round(pc,2),'total_oi_calls':round(co),'total_oi_puts':round(po),'ok':True})
                res['options_signal']='bearish' if pc>1.3 else ('bullish' if pc<0.7 else 'neutral')
            if items:
                res['iv_1m']=round(items[0].get('mark_iv',0),1)
                res['max_pain']=compute_max_pain(items)  # Этап 3.3 -- тот же fetch, без доп. запроса
    except: pass
    _opts_cache=res; _opts_ts=_t.time(); return res


def get_perp_spot_premium(symbol: str = "BTC") -> dict:
    """Perp/Spot премия через Bybit V5 tickers (linear vs spot), Этап 3.2 (АПГРЕЙД
    11.07). premium_pct>0.3% -- перп торгуется дороже спота -- перегрев лонгов
    (порог владельца из спеки задачи); <-0.3% -- зеркально, перегрев шортов
    (бэквордация). ok=False при недоступности источника -- вызывающий код обязан
    честно показать 'н/д', не 0.0%."""
    res = {"ok": False, "perp": 0.0, "spot": 0.0, "premium_pct": 0.0, "signal": "н/д"}
    try:
        r_perp = requests.get("https://api.bybit.com/v5/market/tickers",
                               params={"category": "linear", "symbol": f"{symbol}USDT"}, timeout=6)
        r_spot = requests.get("https://api.bybit.com/v5/market/tickers",
                               params={"category": "spot", "symbol": f"{symbol}USDT"}, timeout=6)
        perp_list = r_perp.json().get("result", {}).get("list", [])
        spot_list = r_spot.json().get("result", {}).get("list", [])
        if not perp_list or not spot_list:
            return res
        perp = float(perp_list[0].get("lastPrice", 0) or 0)
        spot = float(spot_list[0].get("lastPrice", 0) or 0)
        if perp <= 0 or spot <= 0:
            return res
        premium_pct = (perp - spot) / spot * 100
        if premium_pct > 0.3:
            signal = "🔴 перегрев лонгов (перп дороже спота)"
        elif premium_pct < -0.3:
            signal = "🟢 перегрев шортов (перп дешевле спота, бэквордация)"
        else:
            signal = "⚪ норма"
        res = {"ok": True, "perp": perp, "spot": spot,
               "premium_pct": round(premium_pct, 3), "signal": signal}
    except Exception:
        pass
    return res

def get_liq_data():
    import time as _t, requests as _r
    global _liq_cache, _liq_ts
    if _t.time()-_liq_ts<300 and _liq_cache: return _liq_cache
    res={'ok':False,'liq_long':0,'liq_short':0,'liq_ratio':1.0,'liq_signal':'neutral'}
    try:
        # OKX liquidation-orders (замена fapi.binance.com/allForceOrders, заблокированного на Railway)
        r=_r.get('https://www.okx.com/api/v5/public/liquidation-orders',
                 params={'instType':'SWAP','uly':'BTC-USDT','state':'filled','limit':'100'},timeout=8)
        if r.status_code==200:
            od=r.json().get('data',[])
            ct_val=0.01  # BTC-USDT-SWAP contract size = 0.01 BTC
            ll=ls=0.0
            for row in od:
                for d in row.get('details',[]):
                    notional=float(d.get('sz',0))*ct_val*float(d.get('bkPx',0))
                    if d.get('side')=='buy': ll+=notional
                    elif d.get('side')=='sell': ls+=notional
            rt=ll/ls if ls>0 else 1.0
            res.update({'liq_long':round(ll),'liq_short':round(ls),'liq_ratio':round(rt,2),'ok':True})
            res['liq_signal']='bearish' if rt>2 else ('bullish' if rt<0.5 else 'neutral')
    except: pass
    _liq_cache=res; _liq_ts=_t.time(); return res

_usdt_mcap_cache={"ts":0,"data":None}

def get_usdt_mcap():
    """USDT market cap через CoinGecko /coins/markets (замена источника, отдававшего $0.0B). Кэш 5 мин."""
    import time as _t
    global _usdt_mcap_cache
    if _t.time()-_usdt_mcap_cache["ts"]<300 and _usdt_mcap_cache["data"]:
        return _usdt_mcap_cache["data"]
    res={"ok":False,"usdt_mcap":0.0,"usdt_mcap_change_24h":0.0}
    try:
        data=_cg_get("https://api.coingecko.com/api/v3/coins/markets",
                     params={"vs_currency":"usd","ids":"tether","price_change_percentage":"24h"},timeout=8)
        if data:
            coin=data[0]
            mcap=float(coin.get("market_cap") or 0)
            mcap_change=float(coin.get("market_cap_change_percentage_24h") or 0)
            res.update({"ok":True,"usdt_mcap":round(mcap/1e9,2),"usdt_mcap_change_24h":round(mcap_change,2)})
    except: pass
    if res["ok"]:
        _usdt_mcap_cache={"ts":_t.time(),"data":res}
    return res

def get_stablecoin_dominance():
    """Доля USDT/USDC/DAI в общем рынке через CoinGecko (Framework v3, блок Stablecoin Market Share)."""
    res={"ok":False}
    try:
        data=_cg_get("https://api.coingecko.com/api/v3/coins/markets",
                     params={"vs_currency":"usd","ids":"tether,usd-coin,dai"},timeout=8)
        coins={c["id"]:float(c.get("market_cap") or 0) for c in data}
        total=sum(coins.values()) or 1.0
        res={"ok":True,
             "usdt_share":round(coins.get("tether",0)/total*100,1),
             "usdc_share":round(coins.get("usd-coin",0)/total*100,1),
             "dai_share":round(coins.get("dai",0)/total*100,1)}
    except: pass
    return res

def get_institutional_summary():
    macro=get_macro_data(); opts=get_options_data(); liqs=get_liq_data()
    sc=50; sig=[]; wrn=[]
    ms=macro.get('macro_signal','neutral')
    if ms=='bullish': sc+=15; sig.append(f"Macro bullish DXY {macro.get('dxy_ch',0):+.1f}%")
    elif ms=='bearish': sc-=15; wrn.append(f"Macro bearish DXY {macro.get('dxy_ch',0):+.1f}%")
    os2=opts.get('options_signal','neutral'); pc=opts.get('put_call_ratio',1.0)
    if os2=='bullish': sc+=10; sig.append(f'Options P/C {pc:.2f} bullish')
    elif os2=='bearish': sc-=10; wrn.append(f'Options P/C {pc:.2f} bearish')
    ls2=liqs.get('liq_signal','neutral'); lr=liqs.get('liq_ratio',1.0)
    if ls2=='bullish': sc+=8; sig.append(f'Short liqs R={lr:.1f}')
    elif ls2=='bearish': sc-=8; wrn.append(f'Long liqs R={lr:.1f}')
    vix=macro.get('vix',0)
    if vix>30: sc-=10; wrn.append(f'VIX {vix:.0f} extreme fear')
    elif 0<vix<15: sc+=5; sig.append(f'VIX {vix:.0f} low fear')
    sc=max(0,min(100,sc))
    if sc>=70: ov='BULLISH'
    elif sc>=55: ov='MODERATE BULLISH'
    elif sc<=30: ov='BEARISH'
    elif sc<=45: ov='MODERATE BEARISH'
    else: ov='NEUTRAL'
    return {'ok':True,'score':sc,'overall':ov,'signals':sig,'warnings':wrn,'macro':macro,'options':opts,'liquidations':liqs}