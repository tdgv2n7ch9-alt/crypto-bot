"""
BEST TRADE — Pump Detector (Этап 2 дорожной карты)
Binance Futures WebSocket (не заблокирован на Railway, в отличие от REST).
Z-Score объёма >3σ vs 14-дневная норма + объём >5x нормы -> алерт в Telegram.

Запускается внутри bot.py через asyncio.create_task() (тот же процесс/event loop
что и сам бот) — см. run_pump_detector() ниже, который принимает уже готовые
bot / get_coin_by_symbol / full_analysis из bot.py вместо собственных заглушек.
"""

import asyncio
import json
import statistics
import time
from collections import deque

import websockets

SYMBOLS = ["btcusdt", "ethusdt", "solusdt"]  # начать с малого, расширять постепенно
WINDOW_DAYS = 14
Z_SCORE_THRESHOLD = 3.0
VOLUME_MULT_THRESHOLD = 5.0
CANDLE_INTERVAL = "1m"  # агрегируем объём по минутным свечам

BINANCE_WS_URL = "wss://fstream.binance.com/stream?streams=" + "/".join(
    f"{s}@kline_{CANDLE_INTERVAL}" for s in SYMBOLS
)

# история объёмов по символу для расчёта Z-Score (rolling window последних минутных свечей)
_volume_history = {s: deque(maxlen=60 * 24 * WINDOW_DAYS) for s in SYMBOLS}
_last_alert_ts = {s: 0 for s in SYMBOLS}
ALERT_COOLDOWN_SEC = 900  # не спамить чаще раза в 15 мин на символ


def compute_zscore(symbol, current_volume):
    hist = _volume_history[symbol]
    if len(hist) < 100:  # недостаточно данных для нормы
        return None, None
    mean = statistics.mean(hist)
    stdev = statistics.pstdev(hist) or 1e-9
    z = (current_volume - mean) / stdev
    mult = current_volume / (mean or 1e-9)
    return round(z, 2), round(mult, 2)


async def trigger_pump_alert(symbol, price, pct_move, z_score, volume_mult,
                              bot, owner_chat_id, get_coin_by_symbol, full_analysis):
    direction = "ПАМП 🚀" if pct_move > 0 else "ДАМП 🔻"
    sym = symbol.upper().replace("USDT", "")

    verdict_line = ""
    try:
        coin = get_coin_by_symbol(sym)
        if coin:
            a = full_analysis(coin)
            verdict_line = f"\nВердикт: {a['label']} · {a['rocket_label']} (score {a['score']}, rocket {a['rocket']})"
            if a.get("suspicious"):
                verdict_line += "\n⚠️ Подозрительный объём (vol/mcap > 50%)"
    except Exception:
        pass

    text = (
        f"⚡️ {direction} {sym}\n"
        f"Цена: ${price}\n"
        f"Движение (1м свеча): {pct_move}%\n"
        f"Z-Score объёма: {z_score}σ (x{volume_mult} от нормы)"
        f"{verdict_line}"
    )
    try:
        await bot.send_message(owner_chat_id, text, parse_mode="Markdown", disable_web_page_preview=True)
    except Exception as e:
        print(f"Pump Detector: send_message failed: {e}")


async def handle_kline(symbol, kline, bot, owner_chat_id, get_coin_by_symbol, full_analysis):
    """Обрабатывает закрытую минутную свечу, считает Z-Score, триггерит алерт."""
    if not kline.get("x"):  # свеча ещё не закрыта
        return

    volume = float(kline["v"])
    close = float(kline["c"])
    open_price = float(kline["o"])
    pct_move = round((close - open_price) / open_price * 100, 2) if open_price else 0.0

    z, mult = compute_zscore(symbol, volume)
    _volume_history[symbol].append(volume)

    if z is None:
        return  # ещё копим историю

    now = time.time()
    if z > Z_SCORE_THRESHOLD and mult > VOLUME_MULT_THRESHOLD:
        if now - _last_alert_ts[symbol] < ALERT_COOLDOWN_SEC:
            return  # cooldown, не дублируем алерт
        _last_alert_ts[symbol] = now
        await trigger_pump_alert(symbol, close, pct_move, z, mult,
                                  bot, owner_chat_id, get_coin_by_symbol, full_analysis)


async def run_pump_detector(bot, owner_chat_id, get_coin_by_symbol, full_analysis):
    """Точка входа для asyncio.create_task() из bot.py.

    bot               — telegram.Bot (app.bot), для send_message
    owner_chat_id     — chat_id владельца, куда слать алерты
    get_coin_by_symbol— callable(symbol_no_usdt: str) -> dict | None, coin-словарь как в get_all_coins()
    full_analysis     — bot.full_analysis, синхронная функция coin -> dict
    """
    print(f"Pump Detector: подключение к {len(SYMBOLS)} символам через Binance Futures WS")
    while True:
        try:
            async with websockets.connect(BINANCE_WS_URL, ping_interval=20) as ws:
                print("Pump Detector: соединение установлено")
                async for message in ws:
                    payload = json.loads(message)
                    stream_data = payload.get("data", {})
                    symbol = stream_data.get("s", "").lower()
                    kline = stream_data.get("k")
                    if symbol and kline:
                        await handle_kline(symbol, kline, bot, owner_chat_id, get_coin_by_symbol, full_analysis)
        except Exception as e:
            print(f"Pump Detector: соединение разорвано ({e}), переподключение через 5 сек")
            await asyncio.sleep(5)
