"""
BEST TRADE — EMA-контекст + детектор свипа ликвидности (SFP)

Чистые функции над уже полученными OHLC-сериями (списки dict с ключами
open/high/low/close/vol/timestamp, в хронологическом порядке — тот же формат, что
возвращает bot.py:get_binance_ohlc()). Модуль ничего сам не фетчит и не импортирует
bot.py — вызывающая сторона передаёт уже полученные свечи, чтобы не плодить лишние
API-вызовы (candles обычно уже получены для других целей, см. real_ta()/real_full_analysis()
в bot.py).

Ограничение источника данных (важно для интерпретации результатов):
CoinGecko free /ohlc отдаёт фиксированную гранулярность по диапазону days, а не по
запрошенному interval — "1h" на деле означает 30-минутные бары за последние сутки
(~48 баров), "4h" — честные 4-часовые бары за ~30 дней (~180 баров). Из этого:
  - EMA200 на 4h технически недоступна (180 < 200) — используем "мягкий" сид EMA
    (без строгого требования полного периода на SMA-затравку), но ниже порога
    MIN_PERIOD_COVERAGE считаем период недостоверным и возвращаем None.
  - На "1h" (30-мин, ~48 баров) обычно доступна только EMA20 (иногда EMA50 впритык),
    EMA100/200 почти всегда None — стек (bull/bear/mixed) на этом ТФ поэтому часто
    "недостаточно данных". Это честное отражение реальности free-тира, а не баг.
  - Объём в get_binance_ohlc всегда 0.0 (CoinGecko free OHLC не отдаёт объём по свече).
    Если во входных candles объём везде нулевой, volume_confirmed возвращается как
    None ("неизвестно"), а не False — иначе Signal Journal статистика была бы
    систематически искажена ложным "не подтверждено".
"""

EMA_PERIODS = (20, 50, 100, 200)
MIN_PERIOD_COVERAGE = 0.5      # период считается достоверным при >= 50% баров от номинала
SLOPE_LOOKBACK_BARS = 5        # наклон EMA20/50: сравнение текущего значения с N баров назад

FRACTAL_SIDE_BARS = 2          # фрактал: 2 бара с каждой стороны (5-баровый паттерн)
SWEEP_LOOKBACK_BARS = 60       # горизонт поиска swing-точек
SWEEP_RECENT_BARS = 20         # среди скольки последних баров ищем сам свип
WICK_MIN_RATIO = 0.5           # хвост >= 50% диапазона бара сигнального свипа
VOLUME_CONFIRM_MULT = 1.5      # объём свипа >= 1.5x среднего за VOLUME_AVG_WINDOW баров
VOLUME_AVG_WINDOW = 20

FRESH_SWEEP_BARS = 12          # свип считается "свежим" для карточек/скоринга в пределах N баров


def _calc_ema_series(closes: list) -> dict:
    """EMA для всех EMA_PERIODS сразу, с "мягким" сидом (без обязательной SMA-затравки
    из period баров) — иначе короткие серии (30-мин "1h", 4ч "4h") никогда не дали бы
    EMA100/200. Периоды с покрытием < MIN_PERIOD_COVERAGE считаются недостоверными и их
    финальное значение — None."""
    n = len(closes)
    out = {}
    for period in EMA_PERIODS:
        if n == 0:
            out[period] = [None] * n
            continue
        k = 2 / (period + 1)
        series = [None] * n
        series[0] = closes[0]
        for i in range(1, n):
            series[i] = closes[i] * k + series[i - 1] * (1 - k)
        if n < period * MIN_PERIOD_COVERAGE:
            series = [None] * n  # покрытие слишком слабое — весь ряд недостоверен
        out[period] = series
    return out


def _stack_label(last: dict) -> str:
    vals = [last.get(p) for p in EMA_PERIODS]
    if any(v is None for v in vals):
        return "недостаточно данных"
    e20, e50, e100, e200 = vals
    if e20 > e50 > e100 > e200:
        return "бычий"
    if e20 < e50 < e100 < e200:
        return "медвежий"
    return "смешанный"


def _tf_context(candles: list):
    """candles: список dict (open/high/low/close/vol/timestamp), хронологический порядок."""
    if not candles:
        return None
    closes = [c["close"] for c in candles]
    if len(closes) < 5:
        return None

    series = _calc_ema_series(closes)
    last = {p: series[p][-1] for p in EMA_PERIODS}
    price = closes[-1]
    stack = _stack_label(last)

    def _slope(period):
        s = series[period]
        if len(s) <= SLOPE_LOOKBACK_BARS:
            return "н/д"
        cur, prev = s[-1], s[-1 - SLOPE_LOOKBACK_BARS]
        if cur is None or prev is None:
            return "н/д"
        return "рост" if cur > prev else "падение"

    above = {p: (price > last[p] if last[p] is not None else None) for p in EMA_PERIODS}
    distances = {p: abs(price - last[p]) / price * 100 for p in EMA_PERIODS if last[p]}
    nearest = None
    if distances:
        nearest_period = min(distances, key=distances.get)
        nearest = {
            "period": nearest_period,
            "value": last[nearest_period],
            "distance_pct": round(distances[nearest_period], 2),
        }

    return {
        "stack": stack,
        "ema": last,
        "above": above,
        "slope20": _slope(20),
        "slope50": _slope(50),
        "nearest_ema": nearest,
    }


def ema_context(candles_1h: list, candles_4h: list) -> dict:
    """EMA 20/50/100/200 на 1h и 4h из уже полученных OHLC-серий.
    Возвращает {"tf_1h": {...}|None, "tf_4h": {...}|None}."""
    return {"tf_1h": _tf_context(candles_1h), "tf_4h": _tf_context(candles_4h)}


def _find_fractals(candles: list):
    """Фрактальные swing high/low: FRACTAL_SIDE_BARS баров с каждой стороны ниже/выше.
    Возвращает (swing_highs, swing_lows) — списки (index, price), по возрастанию index."""
    n = len(candles)
    side = FRACTAL_SIDE_BARS
    highs, lows = [], []
    for i in range(side, n - side):
        h = candles[i]["high"]
        l = candles[i]["low"]
        if all(h > candles[i - k]["high"] for k in range(1, side + 1)) and \
           all(h > candles[i + k]["high"] for k in range(1, side + 1)):
            highs.append((i, h))
        if all(l < candles[i - k]["low"] for k in range(1, side + 1)) and \
           all(l < candles[i + k]["low"] for k in range(1, side + 1)):
            lows.append((i, l))
    return highs, lows


def _wick_ratio_high(candle) -> float:
    rng = candle["high"] - candle["low"]
    if rng <= 0:
        return 0.0
    body_top = max(candle["open"], candle["close"])
    return (candle["high"] - body_top) / rng


def _wick_ratio_low(candle) -> float:
    rng = candle["high"] - candle["low"]
    if rng <= 0:
        return 0.0
    body_bottom = min(candle["open"], candle["close"])
    return (body_bottom - candle["low"]) / rng


def _volume_confirmed(candles: list, sweep_idx: int):
    """None если объёма нет вообще в данных (CoinGecko free OHLC), иначе True/False по
    порогу VOLUME_CONFIRM_MULT от среднего за VOLUME_AVG_WINDOW баров ДО свипа."""
    window_start = max(0, sweep_idx - VOLUME_AVG_WINDOW)
    window = candles[window_start:sweep_idx]
    if not window:
        return None
    vols = [c.get("vol", 0) or 0 for c in window]
    if all(v == 0 for v in vols) and (candles[sweep_idx].get("vol") or 0) == 0:
        return None  # объёма в данных нет вообще — нельзя ни подтвердить, ни опровергнуть
    avg_vol = sum(vols) / len(vols) if vols else 0
    if avg_vol <= 0:
        return None
    return candles[sweep_idx].get("vol", 0) >= avg_vol * VOLUME_CONFIRM_MULT


def detect_sweep(candles: list):
    """SFP-детектор: свежий свип последнего значимого swing high/low в пределах
    SWEEP_RECENT_BARS баров. Возвращает None, либо:
    {"type": "sweep_high"|"sweep_low", "level": float, "bars_ago": int,
     "volume_confirmed": True|False|None}
    Если и sweep_high, и sweep_low найдены — возвращает более свежий (меньший bars_ago)."""
    n = len(candles)
    if n < (FRACTAL_SIDE_BARS * 2 + 3):
        return None

    swing_highs, swing_lows = _find_fractals(candles)
    recent_start = max(0, n - SWEEP_RECENT_BARS)

    def _best_sweep_high():
        if not swing_highs:
            return None
        best = None
        for idx in range(recent_start, n):
            c = candles[idx]
            # уровень — последний swing high, зафиксированный СТРОГО до этого бара
            prior = [lvl for (li, lvl) in swing_highs if li < idx]
            if not prior:
                continue
            level = prior[-1]
            if c["high"] > level and c["close"] < level and _wick_ratio_high(c) >= WICK_MIN_RATIO:
                best = (idx, level)  # оставляем самый последний найденный (переписываем)
        if best is None:
            return None
        idx, level = best
        return {
            "type": "sweep_high", "level": level, "bars_ago": n - 1 - idx,
            "volume_confirmed": _volume_confirmed(candles, idx),
        }

    def _best_sweep_low():
        if not swing_lows:
            return None
        best = None
        for idx in range(recent_start, n):
            c = candles[idx]
            prior = [lvl for (li, lvl) in swing_lows if li < idx]
            if not prior:
                continue
            level = prior[-1]
            if c["low"] < level and c["close"] > level and _wick_ratio_low(c) >= WICK_MIN_RATIO:
                best = (idx, level)
        if best is None:
            return None
        idx, level = best
        return {
            "type": "sweep_low", "level": level, "bars_ago": n - 1 - idx,
            "volume_confirmed": _volume_confirmed(candles, idx),
        }

    sh = _best_sweep_high()
    sl = _best_sweep_low()
    if sh and sl:
        return sh if sh["bars_ago"] <= sl["bars_ago"] else sl
    return sh or sl


def ema_stack_score_delta(ema_ctx: dict, direction: str) -> int:
    """+8 если 4h-стек по направлению сигнала, -8 если против, 0 если смешанный/н\\д.
    4h выбран как основной ТФ для скоринга (старший ТФ = контекст в SMC/ICT методологии
    этого бота), 1h показывается в карточке текстом, но не участвует в скоринге отдельно."""
    if not ema_ctx:
        return 0
    tf4h = ema_ctx.get("tf_4h")
    if not tf4h:
        return 0
    stack = tf4h.get("stack")
    if stack == "бычий":
        return 8 if direction == "long" else -8
    if stack == "медвежий":
        return 8 if direction == "short" else -8
    return 0


def sweep_score_delta(sweep_1h, sweep_4h, direction: str) -> int:
    """+10 если свежий (<=FRESH_SWEEP_BARS) свип поддерживает направление сигнала
    (sweep_high -> шорт, sweep_low -> лонг), -10 если свежий свип против направления,
    0 если свипа нет вовсе. Берём наиболее свежий из 1h/4h, если оба присутствуют."""
    candidates = [s for s in (sweep_1h, sweep_4h) if s and s["bars_ago"] <= FRESH_SWEEP_BARS]
    if not candidates:
        return 0
    sweep = min(candidates, key=lambda s: s["bars_ago"])
    supports_short = sweep["type"] == "sweep_high"
    supports_long = sweep["type"] == "sweep_low"
    if direction == "long":
        return 10 if supports_long else -10
    else:
        return 10 if supports_short else -10


def format_ema_stack_line(ema_ctx: dict) -> str:
    """'EMA-стек: бычий (1h), медвежий (4h)' — по одному значению на таймфрейм."""
    if not ema_ctx:
        return "EMA-стек: нет данных"
    tf1 = ema_ctx.get("tf_1h")
    tf4 = ema_ctx.get("tf_4h")
    s1 = tf1["stack"] if tf1 else "нет данных"
    s4 = tf4["stack"] if tf4 else "нет данных"
    return f"EMA-стек: {s1} (1h), {s4} (4h)"


def format_sweep_line(sweep_1h, sweep_4h, price_fmt=None) -> str:
    """'⚠️ Манипуляция: свип хаёв $X (4h, N баров назад, объём подтверждён) — ликвидность
    в шорт' для наиболее свежего свипа за последние FRESH_SWEEP_BARS баров на любом из
    ТФ, иначе None. price_fmt: опциональный форматтер float->str (по умолчанию :.6g)."""
    candidates = []
    if sweep_1h and sweep_1h["bars_ago"] <= FRESH_SWEEP_BARS:
        candidates.append(("1h", sweep_1h))
    if sweep_4h and sweep_4h["bars_ago"] <= FRESH_SWEEP_BARS:
        candidates.append(("4h", sweep_4h))
    if not candidates:
        return None
    tf_label, sweep = min(candidates, key=lambda x: x[1]["bars_ago"])

    fmt = price_fmt or (lambda v: f"{v:.6g}")
    level_str = fmt(sweep["level"])
    vc = sweep["volume_confirmed"]
    vol_str = "объём подтверждён" if vc is True else ("объём не подтверждён" if vc is False else "объём неизвестен")

    if sweep["type"] == "sweep_high":
        kind_str, liq_str = f"свип хаёв ${level_str}", "ликвидность в шорт"
    else:
        kind_str, liq_str = f"свип лоёв ${level_str}", "ликвидность в лонг"

    return f"⚠️ Манипуляция: {kind_str} ({tf_label}, {sweep['bars_ago']} баров назад, {vol_str}) — {liq_str}"
