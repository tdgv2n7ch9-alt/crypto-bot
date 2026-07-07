"""
BEST TRADE — Памп-радар v2 (полное покрытие рынка + детект дампов)
Транспорт: Bybit public WS (wss://stream.bybit.com/v5/public/linear). Binance fstream
недоступен из облачных ASN (Railway: 0 пакетов из US West/Singapore/EU Amsterdam при рабочем
Telegram/CoinGecko/GitHub — блокировка на стороне Binance, не сети Railway), поэтому транспорт
целиком перенесён на Bybit; сама логика детекта/стадий/алертов не менялась.
Данные для скоринга/алертов — только WS (tickers + klines) + CoinGecko/CMC через bot.py.

Двухступенчатая схема:
  1) Грубый детект — подписка tickers.SYMBOL по ВСЕМ USDT-linear парам разом (список инструментов
     через REST /v5/market/instruments-info один раз при старте — у Bybit нет all-market стрима
     как !miniTicker@arr у Binance), rolling-окна объёма/цены в памяти на символ. Триггер:
     Z-Score объёма >3σ + резкое движение цены за 1–5 мин.
  2) На срабатывании — динамическая подписка на kline.1.SYMBOL этого символа (live_prices.request_subscription,
     подхватывается run_pump_detector()'s механизмом _merge_dynamic_symbols) для точного ведения
     WATCHING/свечей/графика. После EXPIRED/PROMOTED/CONFIRMED_NO_ACTION — отписка.

Машина состояний, зеркальная для пампов и дампов:
  PUMP_DETECTED/DUMP_DETECTED -> WATCHING -> REVERSAL_CONFIRMED -> PROMOTED (памп, авто)
                                                                 -> добавлен вручную (дамп, кнопка)
                                                                 -> CONFIRMED_NO_ACTION (не прошёл гейт)
                                           \\-> EXPIRED (30 мин без разворота)

Памп: авто-промоушен в TOP_SHORT_SIGNALS при pro_score>=60 и R:R>=1:2 (как в v1).
Дамп: НЕ автодобавляется — алерт с кнопкой "✅ Добавить в ТОП ЛОНГ", R:R-гейт >=1:1.5 по TP1
      решает, показывать ли кнопку. Append-only в обоих случаях.

Запускается внутри bot.py через asyncio.create_task() (тот же процесс/event loop),
получает готовые функции из bot.py через PumpContext вместо собственных заглушек.
"""

import asyncio
import html
import io
import json
import statistics
import time
from collections import deque

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import requests
import websockets

import live_prices
import ta_extra

# ── Конфигурация: fine-grained (kline) слой, как в v1 ────────────
WINDOW_DAYS = 14
Z_SCORE_THRESHOLD = 3.0
VOLUME_MULT_THRESHOLD = 5.0
CANDLE_INTERVAL = "1m"
WATCH_TIMEOUT_SEC = 30 * 60        # WATCHING без разворота -> EXPIRED
CONFIRMED_GRACE_SEC = 15 * 60      # доп. окно после REVERSAL_CONFIRMED без промоушена/добавления
REVERSAL_DRAWDOWN_PCT = 4.0        # откат от пика для памп-REVERSAL_CONFIRMED
REVERSAL_RED_STREAK = 3            # мин. кол-во красных/зелёных 1м свечей подряд
REVERSAL_VOL_MULT = 2.0            # объём отката/отскока >= x от среднего
SL_BUFFER_PCT = 1.5                # памп: SL выше пика на +1.5%
DUMP_SL_BUFFER_PCT = 2.5           # дамп: SL под дном на -2.5% (буфер 2-3%)
PROMOTE_SCORE_THRESHOLD = 60       # порог pro_analysis().pro_score для авто-PROMOTED (памп)
PROMOTE_MIN_RR = 2.0               # памп: R:R >= 1:2 для авто-промоушена
DUMP_MIN_RR = 1.5                  # дамп: R:R-гейт >= 1:1.5 по TP1 для кнопки "Добавить в ТОП ЛОНГ"
MEMECOIN_MCAP_USD = 50_000_000     # ниже — помечаем ⚠️ МЕМКОИН
CHART_5M_BARS = 60                 # 5m-баров в чарт v2 (агрегируются из 1m WS-свечей)
CHART_CANDLES = CHART_5M_BARS * 5 + 20   # 1m-истории нужно с запасом на агрегацию+варм-ап индикаторов
CHART_BB_PERIOD = 20
CHART_BB_STD = 2
CHART_EMA_FAST = 20
CHART_EMA_SLOW = 50
CHART_VOL_MA_PERIOD = 20
CHART_SWING_LOOKBACK = 3            # баров с каждой стороны для локального экстремума
TOP_N_SYMBOLS = 20                 # всегда-live база — топ-N по объёму (для live_prices/фолбэка)
SYMBOL_REFRESH_SEC = 6 * 3600      # как часто пересобирать топ-N базу

# ── Конфигурация: грубый слой (Bybit tickers.*, полное покрытие) ─
# Binance fstream недоступен из облачных ASN (Railway: 0 пакетов из US West/Singapore/EU
# Amsterdam при рабочем Telegram/CoinGecko/GitHub — блокировка на уровне Binance, не сети
# Railway) -- транспорт перенесён на Bybit public WS. Детект/стадии/алерты не менялись.
BYBIT_WS_URL = "wss://stream.bybit.com/v5/public/linear"
BYBIT_INSTRUMENTS_URL = "https://api.bybit.com/v5/market/instruments-info"
BYBIT_SUB_BATCH_SIZE = 10           # Bybit: пачками по ~10 топиков на subscribe-запрос
BYBIT_PING_INTERVAL_SEC = 20        # app-level {"op":"ping"} каждые 20с — Bybit keepalive
STABLE_BASES = {"BUSD", "USDC", "TUSD", "FDUSD"}   # исключаем стейблы как базовый актив пары
COARSE_Z_THRESHOLD = 3.0
COARSE_MOVE_WINDOW_SEC = 180        # окно движения цены — 3 мин (в диапазоне 1–5 мин)
COARSE_MOVE_PCT_THRESHOLD = 3.0     # резкое движение цены, % за окно
COARSE_PRICE_HIST_MAXLEN = 400      # ~несколько минут истории цены на символ
COARSE_VOL_HIST_MAXLEN = 180        # история дельт объёма для Z-score базовой линии
COARSE_MIN_SAMPLES = 30             # мин. точек в истории дельт объёма, чтобы доверять Z-score
MIN_24H_VOLUME_USD = 5_000_000      # неликвид не сигналить
COARSE_ALERT_COOLDOWN_SEC = 60 * 60 # кулдаун 60 мин на повторный алерт по символу
MAX_CONCURRENT_WATCH = 15           # лимит одновременных WATCHING (памп+дамп суммарно)
COARSE_WATCHDOG_TIMEOUT_SEC = 60    # coarse молчит дольше — авто-реконнект + уведомление owner'у
COARSE_NO_DATA_ALERT_SEC = 5 * 60   # реконнект не восстанавливает данные 5 мин — "Радар без данных"
COARSE_RECONNECT_NOTIFY_COOLDOWN_SEC = 10 * 60  # антиспам: не чаще 1 раза в 10 мин на "переподключён"

# Chart v2 -- палитра в стиле биржевых терминалов (TradingView-подобная тёмная тема)
BG, GREEN, RED, WHITE, GRAY, YELLOW = "#131722", "#26A69A", "#EF5350", "#D1D4DC", "#787B86", "#F0B90B"
CHART_GRID_COLOR = "#2A2E39"
CHART_EMA_FAST_COLOR = "#2962FF"    # синий -- EMA20
CHART_EMA_SLOW_COLOR = "#FF6D00"    # оранжевый -- EMA50
CHART_BB_COLOR = "#787B86"          # серый -- полосы Боллинджера
CHART_BB_MID_COLOR = "#B2B5BE"
CHART_ENTRY_ZONE_COLOR = "#2962FF"
CHART_DETECT_LINE_COLOR = "#787B86"
CHART_PANEL_BG = "#1E222D"

# ── Состояние fine-grained слоя (в памяти процесса) ──────────────
_volume_history = {}              # symbol -> deque(volumes) — минутные объёмы с kline
_candle_history = {}              # symbol -> deque({"t","o","h","l","c","v"})
pump_watch = {}                    # symbol -> {...state..., "kind": "pump"}
dump_watch = {}                    # symbol -> {...state..., "kind": "dump"}
pump_history = deque(maxlen=1000)  # завершённые наблюдения (памп+дамп) за последние 24ч
_subscriptions = {}                # symbol -> set(chat_id) — кнопка "🔔 Следить"
_dump_offers = {}                  # symbol -> snapshot {entry,tp1,tp2,sl,rr} для кнопки "Добавить в ТОП ЛОНГ"
_current_symbols = []              # активный список kline-подписки (топ-N база + динамические)
_dynamically_added_symbols = set() # какие из _current_symbols добавлены динамически (можно отписать)
_symbols_ts = 0.0

# ── Состояние грубого слоя (Bybit tickers.*) ─────────────────────
_coarse_price_hist = {}      # symbol -> deque((ts, price))
_coarse_vol_cum = {}         # symbol -> последнее значение кумулятивного 24h quote volume (Bybit turnover24h)
_coarse_vol_delta_hist = {}  # symbol -> deque(дельта объёма между тиками) — база для Z-score
_coarse_vol24h = {}          # symbol -> текущий 24h quote volume (для гейта $5M)
_last_coarse_alert_ts = {}   # symbol -> ts последнего грубого алерта (кулдаун 60 мин)
_bybit_ticker_cache = {}     # symbol -> смёрженные поля последнего tickers.* сообщения (Bybit
                              # шлёт "snapshot" один раз, дальше "delta" только с изменившимися
                              # полями — держим последнее известное lastPrice/turnover24h)

# ── Самодиагностика (/radar_status, watchdog, стартовое уведомление) ─
_radar_start_ts = 0.0              # ts запуска радара (для аптайма)
_startup_notified = False          # одноразовое "Радар запущен: ..." после первого пакета coarse
_coarse_connected = False          # текущее состояние coarse WS-соединения
_coarse_last_packet_ts = 0.0       # ts последнего успешно обработанного пакета !miniTicker@arr
_coarse_reconnect_fail_start = None  # ts начала текущей серии проблем coarse (тишина/разрыв)
_coarse_watchdog_notified_no_data = False  # чтобы не спамить "Радар без данных" повторно
_kline_connected = False           # текущее состояние kline WS-соединения
_kline_last_packet_ts = 0.0        # ts последнего успешно обработанного kline-сообщения
_last_reconnect_notify_ts = 0.0    # ts последней отправки "coarse переподключён" (кулдаун 10 мин,
                                    # страховка от дублей: несколько параллельных инстансов радара
                                    # на Railway при выключенном Enable Teardown спамили этим алертом)
_coarse_reconnect_count = 0        # сколько раз watchdog ловил тишину coarse (для /radar_status и
                                    # текста алерта — видно масштаб проблемы даже когда алерт
                                    # подавлен кулдауном)
_coarse_msg_count = 0              # всего успешно обработанных пакетов !miniTicker@arr (для
                                    # диагностики Binance/сетевых инцидентов — сравнить с 0 после реконнектов)
_kline_msg_count = 0                # всего успешно обработанных kline-сообщений
_coarse_discovery_attempts = 0     # сколько раз пытались получить список инструментов Bybit REST
_coarse_discovery_last_error = None  # текст последней ошибки REST-запроса (None если последний
                                      # успешен) -- если coarse никогда не подключается, здесь видно
                                      # почему (таймаут/DNS/HTTP-код), не только "0 пакетов"


class PumpContext:
    """Набор функций из bot.py, внедряемых в детектор — чтобы не дублировать логику
    (killzone, OI-матрица, funding, скоринг) и не тащить сюда Binance REST."""
    def __init__(self, bot, owner_chat_id, get_coin_by_symbol, full_analysis, pro_analysis,
                 get_killzone_status, get_funding_pct, get_oi_usd, get_oi_change,
                 add_top_short_signal, get_ohlc=None):
        self.bot = bot
        self.owner_chat_id = owner_chat_id
        self.get_coin_by_symbol = get_coin_by_symbol
        self.full_analysis = full_analysis
        self.pro_analysis = pro_analysis
        self.get_killzone_status = get_killzone_status
        self.get_funding_pct = get_funding_pct
        self.get_oi_usd = get_oi_usd
        self.get_oi_change = get_oi_change
        self.add_top_short_signal = add_top_short_signal
        self.get_ohlc = get_ohlc   # bot.get_binance_ohlc(symbol, interval, limit) -- для
                                    # 1h/4h EMA-стека/свип-детектора в блоке "РАЗБОР" (см.
                                    # _build_analysis_block), только на REVERSAL_CONFIRMED


def get_pump_radar_state() -> dict:
    """Для раздела бота '⚡ Памп-радар': активные наблюдения + история за 24ч, памп и дамп раздельно."""
    now = time.time()

    def _active_list(watch_dict, is_pump):
        out = []
        for sym, st in watch_dict.items():
            level = st.get("peak_price") if is_pump else st.get("bottom_price")
            last = st.get("last_price", level)
            pct = (level - last) / level * 100 if level else 0
            out.append({
                "symbol": sym.upper().replace("USDT", ""), "stage": st["stage"],
                "elapsed_min": round((now - st["pump_time"]) / 60, 1),
                "pct_from_level": round(pct if is_pump else -pct, 2),
            })
        return out

    cutoff = now - 24 * 3600
    hist = [h for h in pump_history if h["ts"] >= cutoff]
    pump_hist = [h for h in hist if h.get("kind", "pump") == "pump"]
    dump_hist = [h for h in hist if h.get("kind") == "dump"]

    def _hist_stats(hlist):
        return {
            "detected": len(hlist),
            "reversed": sum(1 for h in hlist if h["final_stage"] in ("REVERSAL_CONFIRMED", "PROMOTED", "ADDED", "CONFIRMED_NO_ACTION")),
            "promoted": sum(1 for h in hlist if h["final_stage"] in ("PROMOTED", "ADDED")),
            "expired":  sum(1 for h in hlist if h["final_stage"] == "EXPIRED"),
        }

    return {
        "pumps_active": _active_list(pump_watch, True),
        "dumps_active": _active_list(dump_watch, False),
        "pumps_history_24h": _hist_stats(pump_hist),
        "dumps_history_24h": _hist_stats(dump_hist),
    }


def get_radar_status() -> dict:
    """Для /radar_status (owner-only): здоровье обоих WS-слоёв + текущая нагрузка + аптайм."""
    now = time.time()
    return {
        "coarse_connected": _coarse_connected,
        "coarse_last_packet_sec_ago": (round(now - _coarse_last_packet_ts, 1)
                                        if _coarse_last_packet_ts else None),
        "coarse_symbols": len(_coarse_price_hist),
        "coarse_msg_count": _coarse_msg_count,
        "coarse_reconnect_count": _coarse_reconnect_count,
        "coarse_discovery_attempts": _coarse_discovery_attempts,
        "coarse_discovery_last_error": _coarse_discovery_last_error,
        "kline_connected": _kline_connected,
        "kline_last_packet_sec_ago": (round(now - _kline_last_packet_ts, 1)
                                       if _kline_last_packet_ts else None),
        "kline_symbols": len(_current_symbols),
        "kline_msg_count": _kline_msg_count,
        "pump_watch_count": len(pump_watch),
        "dump_watch_count": len(dump_watch),
        "pump_watch_symbols": [s.upper().replace("USDT", "") for s in pump_watch],
        "dump_watch_symbols": [s.upper().replace("USDT", "") for s in dump_watch],
        "history_count": len(pump_history),
        "history_maxlen": pump_history.maxlen,
        "uptime_sec": round(now - _radar_start_ts, 0) if _radar_start_ts else 0,
    }


def subscribe_symbol(symbol: str, chat_id: int):
    _subscriptions.setdefault(symbol.upper().replace("USDT", ""), set()).add(chat_id)


def get_dump_offer(symbol: str):
    """Снимок уровней на момент REVERSAL_CONFIRMED дампа — для кнопки 'Добавить в ТОП ЛОНГ',
    не зависит от того, жив ли ещё сам dump_watch к моменту клика."""
    return _dump_offers.get(symbol.upper().replace("USDT", ""))


def _fmt_price(v: float) -> str:
    if v >= 1000: return f"{v:,.2f}"
    if v >= 1:    return f"{v:.4f}"
    if v >= 0.01: return f"{v:.5f}"
    return f"{v:.8f}"


def _oi_matrix_label(price_up: bool, oi_change_pct: float, funding: float) -> str:
    """Та же интерпретация OI-матрицы, что и в /market и Институционале bot.py."""
    oi_up = oi_change_pct > 0
    if price_up and oi_up:
        return "🟢 Цена↑ OI↑ — новые лонги, сильный тренд" if funding >= 0 else "🟡 Цена↑ OI↑ — шорт-сквиз возможен"
    if price_up and not oi_up:
        return "🟡 Цена↑ OI↓ — шорт-сквиз, может исчерпаться"
    if not price_up and oi_up:
        return "🔴 Цена↓ OI↑ — новые шорты, реальное давление"
    return "🟡 Цена↓ OI↓ — выход из позиций, движение слабеет"


async def _discover_top_symbols() -> list:
    """Топ-N Binance Futures перпетуалов по 24h объёму через CoinGecko /derivatives
    (Binance REST запрещён — используем ту же точку входа, что и OI/funding в bot.py).
    Это always-on база (для live_prices), НЕ единственный источник детекта — тот теперь
    полностью покрывает рынок через !miniTicker@arr, см. run_miniticker_stream()."""
    try:
        r = requests.get("https://api.coingecko.com/api/v3/derivatives", timeout=15)
        r.raise_for_status()
        rows = [x for x in r.json()
                if x.get("contract_type") == "perpetual" and "Binance" in (x.get("market") or "")
                and x.get("symbol", "").endswith("USDT")]
        rows.sort(key=lambda x: float(x.get("volume_24h") or 0), reverse=True)
        syms = [x["symbol"].lower() for x in rows[:TOP_N_SYMBOLS]]
        return syms or ["btcusdt", "ethusdt", "solusdt"]
    except Exception as e:
        print(f"Pump Radar: symbol discovery failed ({e}), falling back to BTC/ETH/SOL")
        return ["btcusdt", "ethusdt", "solusdt"]


def _discover_bybit_usdt_perp_symbols() -> list:
    """Полный список USDT-linear перпетуалов Bybit (REST, разово при старте coarse-слоя) --
    основа покрытия грубого детекта. У Bybit нет одного all-market стрима как !miniTicker@arr
    у Binance, поэтому вместо него подписываемся на tickers.SYMBOL по каждому инструменту.
    limit=1000 -- без него Bybit отдаёт только первые 500 из ~700+ инструментов постранично."""
    global _coarse_discovery_last_error, _coarse_discovery_attempts
    _coarse_discovery_attempts += 1
    try:
        r = requests.get(BYBIT_INSTRUMENTS_URL, params={"category": "linear", "limit": 1000}, timeout=15)
        r.raise_for_status()
        rows = r.json().get("result", {}).get("list", [])
        syms = []
        for row in rows:
            if row.get("status") != "Trading":
                continue
            if row.get("contractType") != "LinearPerpetual":
                continue
            if row.get("quoteCoin") != "USDT":
                continue
            symbol = row.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue
            if symbol[:-4].upper() in STABLE_BASES:
                continue
            syms.append(symbol.lower())
        _coarse_discovery_last_error = None
        return syms
    except Exception as e:
        _coarse_discovery_last_error = f"{type(e).__name__}: {e}"
        print(f"Pump Radar (coarse): Bybit instruments discovery failed ({_coarse_discovery_last_error})")
        return []


async def _bybit_subscribe(ws, topics: list):
    """Подписка пачками по BYBIT_SUB_BATCH_SIZE топиков на {"op":"subscribe","args":[...]}."""
    for i in range(0, len(topics), BYBIT_SUB_BATCH_SIZE):
        batch = topics[i:i + BYBIT_SUB_BATCH_SIZE]
        await ws.send(json.dumps({"op": "subscribe", "args": batch}))
        await asyncio.sleep(0.1)


async def _bybit_ping_loop(ws):
    """Bybit требует app-level {"op":"ping"} для keepalive (не просто WS-протокольный ping)."""
    while True:
        await asyncio.sleep(BYBIT_PING_INTERVAL_SEC)
        try:
            await ws.send(json.dumps({"op": "ping"}))
        except Exception:
            return


def _merge_bybit_ticker(symbol: str, data: dict) -> dict:
    """Bybit шлёт snapshot (все поля) один раз на топик, дальше delta (только изменившиеся
    поля) — держим последнее известное состояние на символ, чтобы delta-тики не теряли
    lastPrice/turnover24h, отсутствующие в конкретном сообщении."""
    cache = _bybit_ticker_cache.setdefault(symbol, {})
    cache.update({k: v for k, v in data.items() if v is not None})
    return cache


def _ensure_history(symbol: str):
    if symbol not in _volume_history:
        _volume_history[symbol] = deque(maxlen=60 * 24 * WINDOW_DAYS)
    if symbol not in _candle_history:
        _candle_history[symbol] = deque(maxlen=CHART_CANDLES + 10)


def _has_new_dynamic_symbols() -> bool:
    """Есть ли символы, запросившие live-цену (карточка в bot.py) или пойманные грубым
    детектом, но ещё не в kline-подписке."""
    for sym in live_prices.pending_subscriptions():
        if f"{sym.lower()}usdt" not in _current_symbols:
            return True
    return False


def _merge_dynamic_symbols() -> bool:
    """Добавляет в _current_symbols символы, запросившие live-цену/попавшие в грубый детект.
    Возвращает True, если список изменился (тогда WS нужно переподключить с новым набором)."""
    global _current_symbols
    added = False
    for sym in live_prices.pending_subscriptions():
        s_l = f"{sym.lower()}usdt"
        if s_l not in _current_symbols:
            _current_symbols.append(s_l)
            _dynamically_added_symbols.add(s_l)
            added = True
    return added


def _release_dynamic_symbol(symbol: str):
    """Отписка от kline-стрима символа после EXPIRED/PROMOTED/CONFIRMED_NO_ACTION — только если
    он был добавлен динамически (не входит в always-on топ-N базу) и больше нигде не отслеживается."""
    global _current_symbols
    if symbol not in _dynamically_added_symbols:
        return
    if symbol in pump_watch or symbol in dump_watch:
        return  # ещё активен где-то — защита от гонки
    _dynamically_added_symbols.discard(symbol)
    if symbol in _current_symbols:
        _current_symbols.remove(symbol)


def compute_zscore(symbol, current_volume):
    hist = _volume_history[symbol]
    if len(hist) < 100:
        return None, None
    mean = statistics.mean(hist)
    stdev = statistics.pstdev(hist) or 1e-9
    z = (current_volume - mean) / stdev
    mult = current_volume / (mean or 1e-9)
    return round(z, 2), round(mult, 2)


def _avg_volume(symbol) -> float:
    hist = _volume_history[symbol]
    return statistics.mean(hist) if hist else 1.0


def _find_weakest_watch():
    """Символ с наименьшим Z-score среди всех активных наблюдений (памп+дамп) — кандидат
    на вытеснение при превышении MAX_CONCURRENT_WATCH."""
    weakest = None
    for sym, w in pump_watch.items():
        z = w.get("z_score", 0)
        if weakest is None or z < weakest[1]:
            weakest = (sym, z, "pump")
    for sym, w in dump_watch.items():
        z = w.get("z_score", 0)
        if weakest is None or z < weakest[1]:
            weakest = (sym, z, "dump")
    return weakest if weakest else (None, None, None)


def _finalize_any(symbol: str, kind: str, final_stage: str):
    watch_dict = pump_watch if kind == "pump" else dump_watch
    w = watch_dict.pop(symbol, None)
    pump_history.append({"symbol": symbol.upper().replace("USDT", ""), "ts": time.time(),
                          "final_stage": final_stage, "kind": kind})
    _dump_offers.pop(symbol.upper().replace("USDT", ""), None)
    _release_dynamic_symbol(symbol)
    return w


# ── Грубый детект (!miniTicker@arr, полное покрытие рынка) ───────

def _process_coarse_tick(symbol: str, price: float, quote_vol_cum: float, ts: float):
    """Обрабатывает один тик !miniTicker@arr для одного символа. Синхронная и быстрая —
    вызывается до сотен раз на каждое сообщение стрима (там сразу массив по всем парам).
    Возвращает (kind, price, z, vol_mult) при триггере, иначе None."""
    if symbol not in _coarse_price_hist:
        _coarse_price_hist[symbol] = deque(maxlen=COARSE_PRICE_HIST_MAXLEN)
        _coarse_vol_delta_hist[symbol] = deque(maxlen=COARSE_VOL_HIST_MAXLEN)

    _coarse_vol24h[symbol] = quote_vol_cum
    _coarse_price_hist[symbol].append((ts, price))

    prev_cum = _coarse_vol_cum.get(symbol)
    _coarse_vol_cum[symbol] = quote_vol_cum
    if prev_cum is None:
        return None  # первый тик по символу — дельты ещё нет

    delta = quote_vol_cum - prev_cum
    if delta < 0:
        delta = 0.0  # защита от скачка при откате/ресинке 24ч-окна на бирже
    _coarse_vol_delta_hist[symbol].append(delta)

    hist = _coarse_vol_delta_hist[symbol]
    if len(hist) < COARSE_MIN_SAMPLES:
        return None  # недостаточно данных для базовой линии

    mean = statistics.mean(hist)
    stdev = statistics.pstdev(hist) or 1e-9
    z = (delta - mean) / stdev
    if z <= COARSE_Z_THRESHOLD:
        return None

    window_start = ts - COARSE_MOVE_WINDOW_SEC
    old_price = None
    for old_ts, old_p in _coarse_price_hist[symbol]:
        if old_ts >= window_start:
            old_price = old_p
            break
    if not old_price or old_price <= 0:
        return None
    move_pct = (price - old_price) / old_price * 100
    if abs(move_pct) < COARSE_MOVE_PCT_THRESHOLD:
        return None

    if _coarse_vol24h.get(symbol, 0) < MIN_24H_VOLUME_USD:
        return None  # неликвид
    if ts - _last_coarse_alert_ts.get(symbol, 0) < COARSE_ALERT_COOLDOWN_SEC:
        return None  # кулдаун 60 мин
    if symbol in pump_watch or symbol in dump_watch:
        return None  # уже отслеживается

    kind = "pump" if move_pct > 0 else "dump"
    vol_mult = delta / (mean or 1e-9)

    if len(pump_watch) + len(dump_watch) >= MAX_CONCURRENT_WATCH:
        weakest_sym, weakest_z, weakest_kind = _find_weakest_watch()
        if weakest_z is not None and z > weakest_z:
            _finalize_any(weakest_sym, weakest_kind, "EXPIRED")
        else:
            return None  # новый кандидат не сильнее слабейшего активного — пропускаем

    _last_coarse_alert_ts[symbol] = ts
    return (kind, price, round(z, 2), round(vol_mult, 2))


async def _start_watch(ctx: PumpContext, symbol: str, kind: str, price: float, z: float, vol_mult: float):
    """Создаёт наблюдение по грубому триггеру и запрашивает точную kline-подписку."""
    now = time.time()
    watch = {
        "kind": kind, "stage": "WATCHING",
        "peak_price": price, "bottom_price": price,
        "detect_price": price, "last_price": price,
        "pump_time": now, "z_score": z, "volume_mult": vol_mult,
        "red_streak": 0, "green_streak": 0,
        "entry_lo": None, "entry_hi": None, "sl": None, "tp1": None, "tp2": None,
    }
    (pump_watch if kind == "pump" else dump_watch)[symbol] = watch

    live_prices.request_subscription(symbol.upper().replace("USDT", ""))
    _ensure_history(symbol)

    sym = symbol.upper().replace("USDT", "")
    if kind == "pump":
        stage_title = "PUMP DETECTED 🚀"
        extra = ["🎯 Сценарий: возможен шорт после разворота",
                 "⏳ Наблюдаю за откатом до 30 минут..."]
    else:
        stage_title = "DUMP DETECTED 🔻"
        extra = ["🎯 Сценарий: возможен лонг после отскока от дна",
                 "⏳ Наблюдаю за разворотом до 30 минут..."]

    text = await _compose_alert(ctx, symbol, watch, stage_title, extra)
    await _send_alert(ctx, symbol, text, watch, f"pump_sub_{sym}")


async def _notify_owner(ctx: PumpContext, text: str):
    try:
        await ctx.bot.send_message(ctx.owner_chat_id, text)
    except Exception as e:
        print(f"Pump Radar: не удалось отправить owner-уведомление: {e}")


def _mark_start():
    global _radar_start_ts
    if not _radar_start_ts:
        _radar_start_ts = time.time()


async def run_miniticker_stream(ctx: PumpContext):
    """Bybit tickers.* — грубый детект по ВСЕМ USDT-linear парам разом (полное покрытие
    рынка), по одной подписке на инструмент (у Bybit нет all-market стрима как у Binance).
    Триггеры уходят в _start_watch().

    Самодиагностика: отслеживает состояние соединения (_coarse_connected,
    _coarse_last_packet_ts) для /radar_status, шлёт одноразовое стартовое
    уведомление owner'у и watchdog-алерты при тишине/разрыве (см. модульный docstring)."""
    global _coarse_connected, _coarse_last_packet_ts, _startup_notified
    global _coarse_reconnect_fail_start, _coarse_watchdog_notified_no_data
    global _last_reconnect_notify_ts, _coarse_reconnect_count, _coarse_msg_count
    first_message_logged = False
    _mark_start()

    symbols = []
    while not symbols:
        symbols = _discover_bybit_usdt_perp_symbols()
        if not symbols:
            print("Pump Radar (coarse): Bybit instruments list пуст, повтор через 5 сек")
            await asyncio.sleep(5)
    topics = [f"tickers.{s.upper()}" for s in symbols]
    print(f"Pump Radar (coarse): Bybit — {len(symbols)} USDT-perp инструментов, подключение")

    while True:
        ping_task = None
        try:
            async with websockets.connect(BYBIT_WS_URL, ping_interval=20) as ws:
                print("Pump Radar (coarse): соединение установлено (Bybit)")
                _coarse_connected = True
                await _bybit_subscribe(ws, topics)
                ping_task = asyncio.create_task(_bybit_ping_loop(ws))
                while True:
                    try:
                        message = await asyncio.wait_for(ws.recv(), timeout=COARSE_WATCHDOG_TIMEOUT_SEC)
                    except asyncio.TimeoutError:
                        _coarse_reconnect_count += 1
                        print(f"Pump Radar (coarse): тишина >{COARSE_WATCHDOG_TIMEOUT_SEC}с, "
                              f"реконнект #{_coarse_reconnect_count}")
                        _coarse_connected = False
                        if _coarse_reconnect_fail_start is None:
                            _coarse_reconnect_fail_start = time.time()
                        now_notify = time.time()
                        if now_notify - _last_reconnect_notify_ts >= COARSE_RECONNECT_NOTIFY_COOLDOWN_SEC:
                            _last_reconnect_notify_ts = now_notify
                            await _notify_owner(
                                ctx, f"coarse переподключён (реконнект #{_coarse_reconnect_count})")
                        break

                    try:
                        payload = json.loads(message)
                    except Exception:
                        continue
                    topic = payload.get("topic", "")
                    if not topic.startswith("tickers."):
                        continue  # subscribe-ack / pong / другой служебный ответ

                    data = payload.get("data") or {}
                    sym = (data.get("symbol") or topic.split(".", 1)[1]).lower()
                    merged = _merge_bybit_ticker(sym, data)
                    last_price_raw = merged.get("lastPrice")
                    turnover_raw = merged.get("turnover24h")
                    if last_price_raw is None or turnover_raw is None:
                        continue  # ещё не накопили snapshot-поля для этого символа
                    try:
                        price = float(last_price_raw)
                        vol_q = float(turnover_raw)
                    except (TypeError, ValueError):
                        continue
                    if price <= 0:
                        continue

                    now = time.time()
                    _coarse_last_packet_ts = now
                    _coarse_connected = True
                    _coarse_msg_count += 1
                    if _coarse_reconnect_fail_start is not None:
                        _coarse_reconnect_fail_start = None
                        _coarse_watchdog_notified_no_data = False

                    live_prices.update_price(sym, price)
                    result = _process_coarse_tick(sym, price, vol_q, now)

                    if not first_message_logged:
                        first_message_logged = True
                        print(f"Pump Radar (coarse): первый пакет обработан — "
                              f"подписка на {len(topics)} символов (Bybit)")
                        _log_memory_stats()
                        if not _startup_notified:
                            _startup_notified = True
                            kline_status = "OK" if _kline_connected else "..."
                            await _notify_owner(
                                ctx,
                                f"Радар запущен: {len(topics)} символов, "
                                f"coarse OK, kline {kline_status}")

                    if result:
                        await _start_watch(ctx, sym, *result)
        except Exception as e:
            print(f"Pump Radar (coarse): соединение разорвано ({e}), переподключение через 5 сек")
            _coarse_connected = False
            if _coarse_reconnect_fail_start is None:
                _coarse_reconnect_fail_start = time.time()
        finally:
            if ping_task:
                ping_task.cancel()

        if (_coarse_reconnect_fail_start is not None
                and not _coarse_watchdog_notified_no_data
                and time.time() - _coarse_reconnect_fail_start > COARSE_NO_DATA_ALERT_SEC):
            _coarse_watchdog_notified_no_data = True
            await _notify_owner(ctx, "Радар без данных")

        await asyncio.sleep(5)


def _log_memory_stats():
    print(f"Pump Radar: memory stats — coarse-tracked символов: {len(_coarse_price_hist)}, "
          f"pump_watch: {len(pump_watch)}, dump_watch: {len(dump_watch)}, "
          f"kline-подписка: {len(_current_symbols)} символов, "
          f"история (24ч буфер): {len(pump_history)}/{pump_history.maxlen}")


# ── Чарт ─────────────────────────────────────────────────────────

def _aggregate_5m(candles_1m: list, bars: int = CHART_5M_BARS) -> list:
    """Агрегирует 1m Bybit-свечи в 5m бары (группировка по времени -- устойчиво к
    пропускам данных из-за реконнектов WS, а не жёсткая нарезка по 5). Возвращает
    последние `bars` баров (хронологически, последний может быть ещё не закрыт)."""
    if not candles_1m:
        return []
    bucket_ms = 5 * 60 * 1000
    buckets = {}
    for c in candles_1m:
        b_ts = (c["t"] // bucket_ms) * bucket_ms
        b = buckets.get(b_ts)
        if b is None:
            buckets[b_ts] = {"t": b_ts, "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"], "v": c["v"]}
        else:
            b["h"] = max(b["h"], c["h"])
            b["l"] = min(b["l"], c["l"])
            b["c"] = c["c"]
            b["v"] += c["v"]
    out = [buckets[k] for k in sorted(buckets.keys())]
    return out[-bars:]


def _ema_series(values: list, period: int) -> list:
    """EMA, выровненный по длине values (None, пока не накоплено period точек)."""
    n = len(values)
    if n < period:
        return [None] * n
    k = 2 / (period + 1)
    out = [None] * (period - 1)
    prev = sum(values[:period]) / period
    out.append(prev)
    for v in values[period:]:
        prev = v * k + prev * (1 - k)
        out.append(prev)
    return out


def _sma_series(values: list, period: int) -> list:
    n = len(values)
    out = [None] * n
    for i in range(period - 1, n):
        out[i] = sum(values[i - period + 1:i + 1]) / period
    return out


def _bollinger_bands(values: list, period: int = CHART_BB_PERIOD, std_mult: float = CHART_BB_STD):
    """Возвращает (mid, up, dn) -- каждый список выровнен по длине values."""
    n = len(values)
    mid = _sma_series(values, period)
    up, dn = [None] * n, [None] * n
    for i in range(period - 1, n):
        window = values[i - period + 1:i + 1]
        std = statistics.pstdev(window)
        up[i] = mid[i] + std_mult * std
        dn[i] = mid[i] - std_mult * std
    return mid, up, dn


def _find_swings(bars: list, lookback: int = CHART_SWING_LOOKBACK):
    """Локальные экстремумы (фрактал: lookback баров строго ниже/выше с каждой стороны).
    Возвращает список (index, price, 'high'|'low')."""
    swings = []
    n = len(bars)
    for i in range(lookback, n - lookback):
        window = bars[i - lookback:i + lookback + 1]
        highs = [b["h"] for b in window]
        lows = [b["l"] for b in window]
        if bars[i]["h"] == max(highs) and highs.count(bars[i]["h"]) == 1:
            swings.append((i, bars[i]["h"], "high"))
        if bars[i]["l"] == min(lows) and lows.count(bars[i]["l"]) == 1:
            swings.append((i, bars[i]["l"], "low"))
    return swings


def _build_chart(symbol: str, watch: dict) -> io.BytesIO:
    """Chart v2 -- 5m свечи (агрегированы из 1m Bybit WS-данных), BB(20,2) + EMA20/50,
    свинг-аннотации, зоны сделки, инфо-панель, объём с MA(20). Стиль биржевого терминала."""
    candles_1m = list(_candle_history.get(symbol, []))
    bars = _aggregate_5m(candles_1m, CHART_5M_BARS)
    if len(bars) < 5:
        return None

    closes = [b["c"] for b in bars]
    vols = [b["v"] for b in bars]
    n = len(bars)
    kind = watch.get("kind", "pump")
    current_price = watch.get("last_price", closes[-1])

    ema_fast = _ema_series(closes, CHART_EMA_FAST)
    ema_slow = _ema_series(closes, CHART_EMA_SLOW)
    bb_mid, bb_up, bb_dn = _bollinger_bands(closes, CHART_BB_PERIOD, CHART_BB_STD)
    vol_ma = _sma_series(vols, CHART_VOL_MA_PERIOD)
    swings = _find_swings(bars, CHART_SWING_LOOKBACK)

    fig, (ax_p, ax_v) = plt.subplots(2, 1, figsize=(11, 8.5), facecolor=BG,
                                      gridspec_kw={"height_ratios": [3, 1]}, sharex=True)
    for ax in (ax_p, ax_v):
        ax.set_facecolor(BG)
        ax.tick_params(colors=WHITE, labelsize=10)
        ax.grid(color=CHART_GRID_COLOR, linewidth=0.5, alpha=0.6)
        for spine in ax.spines.values():
            spine.set_color(CHART_GRID_COLOR)

    # --- Свечи 5m ---
    for i, c in enumerate(bars):
        color = GREEN if c["c"] >= c["o"] else RED
        ax_p.plot([i, i], [c["l"], c["h"]], color=color, linewidth=1)
        ax_p.add_patch(patches.Rectangle((i - 0.32, min(c["o"], c["c"])), 0.64,
                                          max(abs(c["c"] - c["o"]), c["h"] * 0.0006),
                                          color=color))

    # --- Оверлеи: EMA 20/50 ---
    xs = list(range(n))
    ax_p.plot(xs, ema_fast, color=CHART_EMA_FAST_COLOR, linewidth=1.4, label=f"EMA{CHART_EMA_FAST}")
    ax_p.plot(xs, ema_slow, color=CHART_EMA_SLOW_COLOR, linewidth=1.4, label=f"EMA{CHART_EMA_SLOW}")

    # --- Оверлеи: Bollinger Bands(20,2) + подписи значений сверху (как на биржах) ---
    ax_p.plot(xs, bb_up, color=CHART_BB_COLOR, linewidth=0.9, linestyle="-", alpha=0.8)
    ax_p.plot(xs, bb_mid, color=CHART_BB_MID_COLOR, linewidth=0.9, linestyle="-", alpha=0.7)
    ax_p.plot(xs, bb_dn, color=CHART_BB_COLOR, linewidth=0.9, linestyle="-", alpha=0.8)
    if bb_up[-1] is not None:
        boll_line = (f"BOLL({CHART_BB_PERIOD},{CHART_BB_STD})  "
                     f"UP {_fmt_price(bb_up[-1])}   MB {_fmt_price(bb_mid[-1])}   DN {_fmt_price(bb_dn[-1])}")
        ax_p.text(0.5, 1.05, boll_line, transform=ax_p.transAxes, color=CHART_BB_COLOR,
                   fontsize=10, va="bottom", ha="center")

    # --- Аннотации: свинг-хай/лоу (самый значимый видимый экстремум каждого типа) ---
    highs = [s for s in swings if s[2] == "high"]
    lows = [s for s in swings if s[2] == "low"]
    if highs:
        i, price, _ = max(highs, key=lambda s: s[1])
        ax_p.annotate(_fmt_price(price), xy=(i, price), xytext=(i, price * 1.012),
                       color=GREEN, fontsize=10, ha="center",
                       arrowprops=dict(arrowstyle="->", color=GREEN, lw=1))
    if lows:
        i, price, _ = min(lows, key=lambda s: s[1])
        ax_p.annotate(_fmt_price(price), xy=(i, price), xytext=(i, price * 0.988),
                       color=RED, fontsize=10, ha="center", va="top",
                       arrowprops=dict(arrowstyle="->", color=RED, lw=1))

    # --- Текущая цена: горизонтальный пунктир + ценник у правой оси ---
    ax_p.axhline(current_price, color=YELLOW, linestyle="--", linewidth=1)
    ax_p.text(1.005, current_price, f" {_fmt_price(current_price)}", transform=ax_p.get_yaxis_transform(),
               color=BG, fontsize=10, va="center", ha="left",
               bbox=dict(boxstyle="round,pad=0.25", facecolor=YELLOW, edgecolor="none"))

    # --- Момент детекта: вертикальный пунктир + метка времени ---
    detect_ts_ms = watch.get("pump_time", time.time()) * 1000
    detect_idx = 0
    for i, b in enumerate(bars):
        if b["t"] <= detect_ts_ms:
            detect_idx = i
        else:
            break
    ax_p.axvline(detect_idx, color=CHART_DETECT_LINE_COLOR, linestyle="--", linewidth=1)
    ax_p.text(detect_idx, ax_p.get_ylim()[1], f" детект {time.strftime('%H:%M UTC', time.gmtime(watch.get('pump_time', time.time())))}",
               color=CHART_DETECT_LINE_COLOR, fontsize=10, va="top", ha="left", rotation=0)

    # --- Зоны сделки: вход (синий), TP (зелёный), SL (красный) с ценами и % ---
    # Подписи -- у ЛЕВОГО края (не у правого), чтобы не конфликтовать с ценником текущей
    # цены и инфо-панелью, которые всегда справа.
    def _pct(level):
        return (level - current_price) / current_price * 100 if current_price else 0

    label_x = max(1, n * 0.02)

    if watch.get("entry_lo") and watch.get("entry_hi"):
        lo, hi = sorted((watch["entry_lo"], watch["entry_hi"]))
        ax_p.axhspan(lo, hi, color=CHART_ENTRY_ZONE_COLOR, alpha=0.18)
        mid_e = (lo + hi) / 2
        ax_p.text(label_x, mid_e, f"Вход {_fmt_price(lo)}-{_fmt_price(hi)} ({_pct(mid_e):+.1f}%)",
                   color=CHART_ENTRY_ZONE_COLOR, fontsize=10, va="center", ha="left",
                   bbox=dict(boxstyle="round,pad=0.15", facecolor=BG, edgecolor="none", alpha=0.7))

    for key, color, lbl in [("tp1", GREEN, "TP1"), ("tp2", GREEN, "TP2"), ("sl", RED, "SL")]:
        level = watch.get(key)
        if not level:
            continue
        band = level * 0.0015
        ax_p.axhspan(level - band, level + band, color=color, alpha=0.22)
        ax_p.text(label_x, level, f"{lbl} {_fmt_price(level)} ({_pct(level):+.1f}%)",
                   color=color, fontsize=10, va="center", ha="left",
                   bbox=dict(boxstyle="round,pad=0.15", facecolor=BG, edgecolor="none", alpha=0.7))

    # --- Инфо-панель (полупрозрачный блок в углу, снизу справа -- верх справа занят
    # ценником текущей цены + BOLL-строкой) ---
    last_1m = candles_1m[-1] if candles_1m else None
    detect_price = watch.get("detect_price", current_price)
    pct_from_detect = (current_price - detect_price) / detect_price * 100 if detect_price else 0
    if last_1m:
        candle_range_pct = (last_1m["h"] - last_1m["l"]) / last_1m["l"] * 100 if last_1m["l"] else 0
        panel_lines = [
            f"Время: {time.strftime('%H:%M:%S UTC', time.gmtime(last_1m['t'] / 1000))}",
            f"O: {_fmt_price(last_1m['o'])}  H: {_fmt_price(last_1m['h'])}",
            f"L: {_fmt_price(last_1m['l'])}  C: {_fmt_price(last_1m['c'])}",
            f"%Изм от детекта: {pct_from_detect:+.2f}%",
            f"Диапазон: {candle_range_pct:.2f}%",
            f"Объём триггера: x{watch.get('volume_mult', 0):.1f} от нормы",
            f"Z-Score: {watch.get('z_score', 0):.1f}σ",
        ]
        panel_text = "\n".join(panel_lines)
        ax_p.text(0.98, 0.03, panel_text, transform=ax_p.transAxes, color=WHITE, fontsize=10.5,
                   va="bottom", ha="right", linespacing=1.6,
                   bbox=dict(boxstyle="round,pad=0.5", facecolor=CHART_PANEL_BG, edgecolor=CHART_GRID_COLOR, alpha=0.9))

    # --- Объём: бары зелёные/красные + MA(20) ---
    for i, c in enumerate(bars):
        color = GREEN if c["c"] >= c["o"] else RED
        ax_v.bar(i, c["v"], color=color, width=0.7, alpha=0.85)
    ax_v.plot(xs, vol_ma, color=YELLOW, linewidth=1.2, label=f"MA{CHART_VOL_MA_PERIOD}")
    ax_v.set_ylabel("Vol", color=GRAY, fontsize=10)
    ax_v.legend(loc="upper left", fontsize=10, facecolor=BG, labelcolor=WHITE, framealpha=0.4)

    detect_label = "детект"
    ax_p.set_title(f"{symbol.upper()} · 5m · {detect_label} {time.strftime('%H:%M UTC', time.gmtime(watch.get('pump_time', time.time())))}",
                    color=WHITE, fontsize=12, loc="left")
    ax_p.text(0.01, 0.02, "BEST TRADE", color=GRAY, fontsize=10, alpha=0.6,
               ha="left", va="bottom", transform=ax_p.transAxes)
    ax_p.legend(loc="upper left", fontsize=10, facecolor=BG, labelcolor=WHITE, framealpha=0.35,
                bbox_to_anchor=(0.0, 0.93))

    plt.tight_layout()
    plt.subplots_adjust(right=0.88, top=0.90)
    buf = io.BytesIO()
    plt.savefig(buf, format="png", facecolor=BG, dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf


# ── Композиция алертов ──────────────────────────────────────────

def _risk_block(entry: float, sl: float) -> str:
    risk_pct = abs(sl - entry) / entry * 100 if entry else 0
    lines = ["💰 <b>Риск на депозит:</b>"]
    for dep_risk in (1, 2, 3):
        if risk_pct > 0:
            size_pct = dep_risk / risk_pct * 100
            lines.append(f"  {dep_risk}% депозита → размер позиции ~{size_pct:.0f}% от депозита")
    return "\n".join(lines)


async def _compose_alert(ctx: PumpContext, symbol: str, watch: dict, stage_title: str,
                          extra_lines: list) -> str:
    sym = symbol.upper().replace("USDT", "")
    price = watch.get("last_price", watch.get("peak_price") or watch.get("bottom_price"))
    detect_price = watch["detect_price"]
    pct_move = (price - detect_price) / detect_price * 100 if detect_price else 0

    funding = 0.0; oi_now = 0.0; oi_chg = 0.0
    try:
        funding = ctx.get_funding_pct(sym)
        oi_now = ctx.get_oi_usd(sym)
        oi_chg = ctx.get_oi_change(sym)
    except Exception:
        pass

    kz_name = "?"
    try:
        kz = ctx.get_killzone_status()
        kz_name = kz["active"]["name"]
    except Exception:
        pass

    oi_line = _oi_matrix_label(price_up=pct_move > 0, oi_change_pct=oi_chg, funding=funding)

    memecoin_line = ""
    try:
        coin = ctx.get_coin_by_symbol(sym)
        mcap = (coin.get("quote", {}).get("USDT", {}).get("market_cap", 0) or 0) if coin else 0
        if 0 < mcap < MEMECOIN_MCAP_USD:
            memecoin_line = "\n⚠️ <b>МЕМКОИН</b> — низкая капитализация, повышенный риск манипуляции"
    except Exception:
        pass

    _, price_age = live_prices.get_live_price(sym)
    price_fresh = live_prices.freshness_label(price_age)

    sym_e = html.escape(sym)
    stage_e = html.escape(stage_title)
    kz_name_e = html.escape(str(kz_name))

    SEP = "━━━━━━━━━━━━━━━━━━━━"
    lines = [
        f"⚡ <b>ПАМП-РАДАР — {stage_e}</b>",
        f"<b>{sym_e}/USDT</b>{memecoin_line}",
        SEP, "",
        f"📍 Цена: <code>{_fmt_price(price)}</code>  <i>{html.escape(price_fresh)}</i>  ({pct_move:+.1f}% от детекта)",
        f"📊 Объём: x{watch.get('volume_mult', 0):.1f} от нормы · Z-Score: {watch.get('z_score', 0):.1f}σ",
        f"📈 Funding: {funding:+.4f}%",
        f"📊 OI: ${oi_now/1e6:.1f}M ({oi_chg:+.1f}% за 5 мин) — {html.escape(oi_line)}",
        f"⏰ Сессия: {kz_name_e}",
        "",
    ]
    lines.extend(extra_lines)
    lines.append(SEP)
    return "\n".join(lines)


async def _send_alert(ctx: PumpContext, symbol: str, text: str, watch: dict, subscribe_cb_data: str,
                       extra_button: tuple = None):
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        rows = [[InlineKeyboardButton("🔔 Следить", callback_data=subscribe_cb_data)]]
        if extra_button:
            rows.append([InlineKeyboardButton(extra_button[0], callback_data=extra_button[1])])
        kb = InlineKeyboardMarkup(rows)
    except Exception:
        kb = None

    chart = None
    try:
        chart = _build_chart(symbol, watch)
    except Exception as e:
        print(f"Pump Radar: chart build failed for {symbol}: {e}")

    try:
        if chart:
            await ctx.bot.send_photo(ctx.owner_chat_id, photo=chart, caption=text,
                                      parse_mode="HTML", reply_markup=kb)
        else:
            await ctx.bot.send_message(ctx.owner_chat_id, text, parse_mode="HTML",
                                        reply_markup=kb, disable_web_page_preview=True)
    except Exception as e:
        print(f"Pump Radar: send failed: {e}")

    sym = symbol.upper().replace("USDT", "")
    subs = _subscriptions.get(sym, set())
    for cid in subs:
        if cid == ctx.owner_chat_id:
            continue
        try:
            await ctx.bot.send_message(cid, text, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            pass


async def _notify_subscribers_zone(ctx: PumpContext, symbol: str, watch: dict, event: str):
    sym = symbol.upper().replace("USDT", "")
    subs = _subscriptions.get(sym, set())
    if not subs:
        return
    sym_e = html.escape(sym)
    text = {
        "entry": f"🔔 <b>{sym_e}</b> — цена вошла в зону входа <code>{_fmt_price(watch.get('entry_lo',0))}–{_fmt_price(watch.get('entry_hi',0))}</code>",
        "tp1":   f"🔔 <b>{sym_e}</b> — TP1 достигнут, двигай стоп в безубыток",
        "sl":    f"🔔 <b>{sym_e}</b> — цена у SL-зоны <code>{_fmt_price(watch.get('sl',0))}</code>, внимание",
    }.get(event)
    if not text:
        return
    for cid in subs:
        try:
            await ctx.bot.send_message(cid, text, parse_mode="HTML")
        except Exception:
            pass


# ── Машина состояний (fine-grained, по kline) ────────────────────

async def _try_promote_pump(ctx: PumpContext, symbol: str, watch: dict):
    sym = symbol.upper().replace("USDT", "")
    try:
        coin = ctx.get_coin_by_symbol(sym)
        if not coin:
            return
        pa = ctx.pro_analysis(sym, coin)
        entry = watch["entry_lo"] or watch["last_price"]
        sl = watch["sl"]
        tp1 = watch["tp1"]
        rr = abs(entry - tp1) / abs(sl - entry) if sl != entry else 0
        if pa.get("ok") and pa.get("direction") == "short" and pa.get("pro_score", 0) >= PROMOTE_SCORE_THRESHOLD and rr >= PROMOTE_MIN_RR:
            ctx.add_top_short_signal(sym, {
                "time": None, "entry": entry, "tp1": tp1, "tp2": watch["tp2"],
                "sl": sl, "rr": round(rr, 2), "status": "active",
                "note": "⚡ из Памп-радара",
            })
            watch["stage"] = "PROMOTED"
            text = await _compose_alert(ctx, symbol, watch, "PROMOTED ✅",
                                         [f"✅ Добавлено в ТОП ШОРТ (score {pa.get('pro_score',0)}, R:R 1:{rr:.1f})"])
            await _send_alert(ctx, symbol, text, watch, f"pump_sub_{sym}")
            _finalize_any(symbol, "pump", "PROMOTED")
            await _send_promotion_chart(ctx, sym, "short", entry, sl, tp1, watch.get("tp2"), rr)
    except Exception as e:
        print(f"Pump Radar: promote check {symbol}: {e}")


async def _send_promotion_chart(ctx: PumpContext, sym: str, direction: str,
                                entry: float, sl: float, tp1: float, tp2, rr: float):
    """Chart v4 (2h/~120 баров, свинг-стиль) для промоушена в ТОП ЛОНГ/ШОРТ -- отдельно
    от 5m Chart v2, который остаётся на исходном памп/дамп-алерте (см. chart_v3.py).
    Памп-радар не считает структурные POI-зоны (только entry/SL/TP из своего собственного
    watch-состояния) -- Chart v4 здесь рендерится без zones (валидный режим, см.
    chart_v4.py), даёт только стрелку сценария и всё остальное из v3. Фоллбек на Chart v3
    при исключении. Не критично для промоушена самого по себе -- ошибка здесь не должна
    ронять _try_promote_pump/pump_addlong_, поэтому обёрнуто в try/except на уровне
    вызова."""
    if not ctx.get_ohlc:
        return
    try:
        import functools
        import chart_v4
        import chart_v3
        loop = asyncio.get_event_loop()
        candles_2h = await loop.run_in_executor(None, ctx.get_ohlc, sym, "2h", 120)
        if not candles_2h:
            return
        chart = None
        try:
            chart = await loop.run_in_executor(None, functools.partial(
                chart_v4.build_trade_chart_v4, sym, candles_2h, direction,
                entry_levels=[entry], sl=sl, tp1=tp1, tp2=tp2))
        except Exception as e:
            print(f"Pump Radar: promotion chart_v4 {sym}: {e}, falling back to chart_v3")
        if chart is None:
            chart = await loop.run_in_executor(
                None, chart_v3.build_trade_chart, sym, candles_2h, direction,
                [entry], sl, tp1, tp2, None, round(rr, 2))
        if chart:
            side_ru = "ТОП ЛОНГ" if direction == "long" else "ТОП ШОРТ"
            await ctx.bot.send_photo(ctx.owner_chat_id, photo=chart,
                                     caption=f"📊 {sym} — {side_ru} (промоушен из Памп-радара)")
    except Exception as e:
        print(f"Pump Radar: promotion chart_v3 {sym}: {e}")


async def _build_analysis_block(ctx: PumpContext, symbol: str, watch: dict) -> str:
    """Блок 'РАЗБОР' под графиком в тексте алерта REVERSAL_CONFIRMED: EMA-контекст (1h/4h
    стек через ta_extra), BB-контекст (перерастяжение за полосы), свип-детектор если
    сработал, OI/funding, killzone. Требует свежий фетч 1h/4h OHLC (ctx.get_ohlc) --
    поэтому вызывается только на REVERSAL_CONFIRMED (точка принятия решения), а не на
    каждом алерте радара, чтобы не грузить CoinGecko на каждый грубый детект."""
    if not ctx.get_ohlc:
        return ""
    sym = symbol.upper().replace("USDT", "")
    try:
        loop = asyncio.get_event_loop()
        candles_1h = await loop.run_in_executor(None, ctx.get_ohlc, sym, "1h", 100)
        candles_4h = await loop.run_in_executor(None, ctx.get_ohlc, sym, "4h", 100)
    except Exception as e:
        print(f"Pump Radar: analysis block OHLC fetch {symbol}: {e}")
        return ""
    if not candles_1h and not candles_4h:
        return ""

    price = watch.get("last_price", 0)
    lines = []

    try:
        ema_ctx = ta_extra.ema_context(candles_1h, candles_4h)
        lines.append(ta_extra.format_ema_stack_line(ema_ctx))
        tf4h = ema_ctx.get("tf_4h")
        if tf4h and tf4h.get("ema", {}).get(20) and tf4h.get("ema", {}).get(50) and price:
            pos20 = "выше" if price > tf4h["ema"][20] else "ниже"
            pos50 = "выше" if price > tf4h["ema"][50] else "ниже"
            lines.append(f"Цена {pos20} EMA20 (4h), {pos50} EMA50 (4h)")
    except Exception as e:
        print(f"Pump Radar: analysis block EMA {symbol}: {e}")

    try:
        closes_4h = [c["close"] for c in candles_4h] if candles_4h else []
        if len(closes_4h) >= CHART_BB_PERIOD and price:
            _, bb_up, bb_dn = _bollinger_bands(closes_4h, CHART_BB_PERIOD, CHART_BB_STD)
            if bb_up[-1] is not None:
                if price > bb_up[-1]:
                    lines.append(f"BB(4h): цена ВЫШЕ верхней ленты ({_fmt_price(bb_up[-1])}) -- перерастяжение вверх")
                elif price < bb_dn[-1]:
                    lines.append(f"BB(4h): цена НИЖЕ нижней ленты ({_fmt_price(bb_dn[-1])}) -- перерастяжение вниз")
                else:
                    lines.append(f"BB(4h): цена внутри полос ({_fmt_price(bb_dn[-1])}-{_fmt_price(bb_up[-1])})")
    except Exception as e:
        print(f"Pump Radar: analysis block BB {symbol}: {e}")

    try:
        sweep_1h = ta_extra.detect_sweep(candles_1h) if candles_1h else None
        sweep_4h = ta_extra.detect_sweep(candles_4h) if candles_4h else None
        sweep_line = ta_extra.format_sweep_line(sweep_1h, sweep_4h, price_fmt=_fmt_price)
        if sweep_line:
            lines.append(sweep_line)
    except Exception as e:
        print(f"Pump Radar: analysis block sweep {symbol}: {e}")

    try:
        funding = ctx.get_funding_pct(sym)
        oi_now = ctx.get_oi_usd(sym)
        oi_chg = ctx.get_oi_change(sym)
        lines.append(f"Funding: {funding:+.4f}%  ·  OI: ${oi_now/1e6:.1f}M ({oi_chg:+.1f}% за 5 мин)")
    except Exception as e:
        print(f"Pump Radar: analysis block OI/funding {symbol}: {e}")

    try:
        kz = ctx.get_killzone_status()
        lines.append(f"Killzone: {kz['active']['name']}")
    except Exception as e:
        print(f"Pump Radar: analysis block killzone {symbol}: {e}")

    if not lines:
        return ""
    return "📋 <b>РАЗБОР:</b>\n" + "\n".join(f"  {html.escape(l)}" for l in lines)


async def _confirm_pump_reversal(ctx: PumpContext, symbol: str, watch: dict):
    watch["stage"] = "REVERSAL_CONFIRMED"
    peak = watch["peak_price"]
    close = watch["last_price"]
    watch["sl"] = round(peak * (1 + SL_BUFFER_PCT / 100), 8)
    watch["entry_hi"] = peak * 0.995
    watch["entry_lo"] = close
    # TP выводится из риска (entry-SL), а не фиксированным % от уже отскочившей цены —
    # иначе R:R почти никогда не дотягивает до PROMOTE_MIN_RR (см. коммит: фикс. % давал
    # R:R около 0.5-0.6 в реалистичных сценариях, гейт был практически недостижим).
    risk = abs(watch["sl"] - watch["entry_lo"])
    watch["tp1"] = watch["entry_lo"] - max(PROMOTE_MIN_RR, 2.0) * risk
    watch["tp2"] = watch["entry_lo"] - max(PROMOTE_MIN_RR, 2.0) * 1.6 * risk
    rr = abs(watch["tp1"] - close) / abs(close - watch["sl"]) if close != watch["sl"] else 0
    text = await _compose_alert(ctx, symbol, watch, "REVERSAL CONFIRMED 🔻",
                                 [f"🎯 Зона входа (шорт): <code>{_fmt_price(watch['entry_lo'])}–{_fmt_price(watch['entry_hi'])}</code>",
                                  f"🛑 SL: <code>{_fmt_price(watch['sl'])}</code> (пик +{SL_BUFFER_PCT}%)",
                                  f"🎯 TP1: <code>{_fmt_price(watch['tp1'])}</code>  TP2: <code>{_fmt_price(watch['tp2'])}</code>",
                                  f"R:R по TP1: 1:{rr:.1f}",
                                  _risk_block(watch["entry_lo"], watch["sl"]),
                                  "",
                                  "🛡 <b>Position Protection:</b> если уже в позиции — частичная фиксация на TP1, "
                                  "трейлинг-стоп в безубыток после TP1."])
    analysis = await _build_analysis_block(ctx, symbol, watch)
    if analysis:
        text = text + "\n\n" + analysis
    await _send_alert(ctx, symbol, text, watch, f"pump_sub_{symbol.upper().replace('USDT','')}")
    await _try_promote_pump(ctx, symbol, watch)


async def _confirm_dump_reversal(ctx: PumpContext, symbol: str, watch: dict):
    watch["stage"] = "REVERSAL_CONFIRMED"
    bottom = watch["bottom_price"]
    close = watch["last_price"]
    watch["sl"] = round(bottom * (1 - DUMP_SL_BUFFER_PCT / 100), 8)
    watch["entry_lo"] = bottom * 1.005
    watch["entry_hi"] = close
    # TP выводится из риска (SL-entry), а не фиксированным % от уже отскочившей цены — см.
    # аналогичный фикс в _confirm_pump_reversal: иначе R:R-гейт почти недостижим.
    risk = abs(watch["entry_hi"] - watch["sl"])
    watch["tp1"] = watch["entry_hi"] + max(DUMP_MIN_RR, 1.5) * risk
    watch["tp2"] = watch["entry_hi"] + max(DUMP_MIN_RR, 1.5) * 1.6 * risk
    rr = abs(watch["tp1"] - close) / abs(close - watch["sl"]) if close != watch["sl"] else 0
    sym = symbol.upper().replace("USDT", "")
    show_button = rr >= DUMP_MIN_RR

    rr_line = f"R:R по TP1: 1:{rr:.1f}"
    if not show_button:
        rr_line += "  ⚠️ ниже порога 1:1.5 — кнопка добавления недоступна"

    text = await _compose_alert(ctx, symbol, watch, "REVERSAL CONFIRMED 🟢",
                                 [f"🎯 Зона входа (лонг): <code>{_fmt_price(watch['entry_lo'])}–{_fmt_price(watch['entry_hi'])}</code>",
                                  f"🛑 SL: <code>{_fmt_price(watch['sl'])}</code> (дно −{DUMP_SL_BUFFER_PCT}%)",
                                  f"🎯 TP1: <code>{_fmt_price(watch['tp1'])}</code>  TP2: <code>{_fmt_price(watch['tp2'])}</code>",
                                  rr_line,
                                  _risk_block(watch["entry_lo"], watch["sl"]),
                                  "",
                                  "🛡 <b>Position Protection:</b> если уже в позиции — частичная фиксация на TP1, "
                                  "трейлинг-стоп в безубыток после TP1."])

    analysis = await _build_analysis_block(ctx, symbol, watch)
    if analysis:
        text = text + "\n\n" + analysis

    extra_button = None
    if show_button:
        _dump_offers[sym] = {"entry": watch["entry_lo"], "tp1": watch["tp1"], "tp2": watch["tp2"],
                              "sl": watch["sl"], "rr": round(rr, 2)}
        extra_button = ("✅ Добавить в ТОП ЛОНГ", f"pump_addlong_{sym}")

    await _send_alert(ctx, symbol, text, watch, f"pump_sub_{sym}", extra_button=extra_button)


def _finalize(symbol: str, watch: dict, final_stage: str):
    """Оставлено для обратной совместимости — используй _finalize_any (учитывает kind)."""
    kind = watch.get("kind", "pump")
    _finalize_any(symbol, kind, final_stage)


async def handle_kline(ctx: PumpContext, symbol: str, kline: dict):
    """Ведёт УЖЕ существующие наблюдения (памп/дамп) по 1m kline. Новые наблюдения теперь
    создаются только грубым детектом (_process_coarse_tick/_start_watch), не отсюда —
    это разделение ответственности between coarse (весь рынок) и fine (точное ведение)."""
    _ensure_history(symbol)
    is_closed = kline.get("x", False)
    close = float(kline["c"]); open_ = float(kline["o"])
    high = float(kline["h"]); low = float(kline["l"])
    volume = float(kline["v"])

    # Live-цена — на каждый тик, а не только на закрытии свечи (мост live_prices не трогаем).
    live_prices.update_price(symbol, close)

    watch = pump_watch.get(symbol) or dump_watch.get(symbol)
    if watch:
        watch["last_price"] = close

    if not is_closed:
        if watch:
            await _check_subscriber_zones(ctx, symbol, watch)
        return

    _candle_history[symbol].append({"t": kline.get("t", 0), "o": open_, "h": high, "l": low, "c": close, "v": volume})
    avg_vol_before = _avg_volume(symbol)
    _volume_history[symbol].append(volume)

    if not watch:
        return

    now = time.time()
    kind = watch.get("kind", "pump")

    if watch["stage"] in ("PUMP_DETECTED", "WATCHING"):
        if kind == "pump":
            if close > watch["peak_price"]:
                watch["peak_price"] = close
                watch["red_streak"] = 0
            watch["red_streak"] = watch["red_streak"] + 1 if close < open_ else 0
            drawdown = (watch["peak_price"] - close) / watch["peak_price"] * 100 if watch["peak_price"] else 0
            reversal = (drawdown >= REVERSAL_DRAWDOWN_PCT
                        and watch["red_streak"] >= REVERSAL_RED_STREAK
                        and volume >= REVERSAL_VOL_MULT * avg_vol_before)
            if reversal:
                await _confirm_pump_reversal(ctx, symbol, watch)
                return
        else:
            if close < watch["bottom_price"]:
                watch["bottom_price"] = close
                watch["green_streak"] = 0
            watch["green_streak"] = watch["green_streak"] + 1 if close > open_ else 0
            bounce = (close - watch["bottom_price"]) / watch["bottom_price"] * 100 if watch["bottom_price"] else 0
            reversal = (bounce >= REVERSAL_DRAWDOWN_PCT
                        and watch["green_streak"] >= REVERSAL_RED_STREAK
                        and volume >= REVERSAL_VOL_MULT * avg_vol_before)
            if reversal:
                await _confirm_dump_reversal(ctx, symbol, watch)
                return

        if now - watch["pump_time"] > WATCH_TIMEOUT_SEC:
            _finalize_any(symbol, kind, "EXPIRED")
            return


async def _check_subscriber_zones(ctx: PumpContext, symbol: str, watch: dict):
    if watch["stage"] != "REVERSAL_CONFIRMED":
        return
    price = watch["last_price"]
    kind = watch.get("kind", "pump")
    if watch.get("entry_lo") and watch.get("entry_hi") and watch["entry_lo"] <= price <= watch["entry_hi"] \
            and not watch.get("_notified_entry"):
        watch["_notified_entry"] = True
        await _notify_subscribers_zone(ctx, symbol, watch, "entry")
    if watch.get("tp1"):
        hit_tp1 = (price <= watch["tp1"]) if kind == "pump" else (price >= watch["tp1"])
        if hit_tp1 and not watch.get("_notified_tp1"):
            watch["_notified_tp1"] = True
            await _notify_subscribers_zone(ctx, symbol, watch, "tp1")
    if watch.get("sl"):
        near_sl = (price >= watch["sl"] * 0.998) if kind == "pump" else (price <= watch["sl"] * 1.002)
        if near_sl and not watch.get("_notified_sl"):
            watch["_notified_sl"] = True
            await _notify_subscribers_zone(ctx, symbol, watch, "sl")


# ── Точка входа ────────────────────────────────────────────────

async def run_pump_detector(ctx: PumpContext):
    """Kline-слой: ведёт точное состояние наблюдений (памп+дамп), созданных грубым детектом,
    плюс всегда-live топ-N база для live_prices."""
    global _current_symbols, _symbols_ts, _kline_connected, _kline_last_packet_ts, _kline_msg_count
    _mark_start()

    _current_symbols = await _discover_top_symbols()
    _merge_dynamic_symbols()
    _symbols_ts = time.time()
    for s in _current_symbols:
        _ensure_history(s)
    print(f"Pump Radar: kline-подписка на {len(_current_symbols)} символов (топ-{TOP_N_SYMBOLS} база)")

    while True:
        if time.time() - _symbols_ts > SYMBOL_REFRESH_SEC:
            try:
                new_syms = await _discover_top_symbols()
                if new_syms:
                    kept_dynamic = [s for s in _current_symbols if s in _dynamically_added_symbols]
                    _current_symbols = new_syms + [s for s in kept_dynamic if s not in new_syms]
            except Exception as e:
                print(f"Pump Radar: symbol refresh failed: {e}")
            _symbols_ts = time.time()

        if _merge_dynamic_symbols():
            for s in _current_symbols:
                _ensure_history(s)
            print(f"Pump Radar: kline-подписка обновлена ({len(_current_symbols)} символов)")

        topics = [f"kline.1.{s.upper()}" for s in _current_symbols]
        ping_task = None
        try:
            async with websockets.connect(BYBIT_WS_URL, ping_interval=20) as ws:
                print("Pump Radar: kline-соединение установлено (Bybit)")
                _kline_connected = True
                await _bybit_subscribe(ws, topics)
                ping_task = asyncio.create_task(_bybit_ping_loop(ws))
                while True:
                    try:
                        message = await asyncio.wait_for(ws.recv(), timeout=30)
                    except asyncio.TimeoutError:
                        if _has_new_dynamic_symbols():
                            break
                        continue
                    try:
                        payload = json.loads(message)
                    except Exception:
                        continue
                    topic = payload.get("topic", "")
                    if not topic.startswith("kline."):
                        continue  # subscribe-ack / pong / другой служебный ответ
                    _kline_last_packet_ts = time.time()
                    _kline_msg_count += 1
                    symbol = topic.rsplit(".", 1)[-1].lower()
                    for kd in (payload.get("data") or []):
                        kline = {
                            "x": bool(kd.get("confirm")),
                            "o": kd.get("open"), "h": kd.get("high"),
                            "l": kd.get("low"), "c": kd.get("close"),
                            "v": kd.get("volume"), "t": kd.get("start", 0),
                        }
                        await handle_kline(ctx, symbol, kline)
                    if _has_new_dynamic_symbols():
                        break
        except Exception as e:
            print(f"Pump Radar: kline-соединение разорвано ({e}), переподключение через 5 сек")
            _kline_connected = False
            await asyncio.sleep(5)
        finally:
            if ping_task:
                ping_task.cancel()

        now = time.time()
        for sym in list(pump_watch.keys()):
            w = pump_watch[sym]
            if w["stage"] in ("PUMP_DETECTED", "WATCHING") and now - w["pump_time"] > WATCH_TIMEOUT_SEC:
                _finalize_any(sym, "pump", "EXPIRED")
            elif w["stage"] == "REVERSAL_CONFIRMED" and now - w["pump_time"] > WATCH_TIMEOUT_SEC + CONFIRMED_GRACE_SEC:
                _finalize_any(sym, "pump", "CONFIRMED_NO_ACTION")
        for sym in list(dump_watch.keys()):
            w = dump_watch[sym]
            if w["stage"] in ("PUMP_DETECTED", "WATCHING") and now - w["pump_time"] > WATCH_TIMEOUT_SEC:
                _finalize_any(sym, "dump", "EXPIRED")
            elif w["stage"] == "REVERSAL_CONFIRMED" and now - w["pump_time"] > WATCH_TIMEOUT_SEC + CONFIRMED_GRACE_SEC:
                _finalize_any(sym, "dump", "CONFIRMED_NO_ACTION")
