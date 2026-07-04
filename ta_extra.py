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

ZONE_WIDTH_MIN_PCT = 0.3        # ширина зоны S/R -- нижняя граница диапазона из ТЗ
ZONE_WIDTH_MAX_PCT = 0.8        # верхняя граница
ZONE_WIDTH_ATR_MULT = 0.5       # ширина = clamp(ATR% * mult, MIN, MAX)
ZONE_MIN_TOUCHES = 2            # фрактальная зона валидна только с 2+ касаниями; EMA -- отдельная категория, без порога
SR_SL_BUFFER_PCT = 2.5          # буфер SL за зоной входа (середина диапазона 2-3% из ТЗ)
SR_MIN_RR_TP1 = 1.5             # R:R-гейт по TP1
SR_ATR_PERIOD = 14


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
    0 если свипа нет вовсе. Берём наиболее свежий из 1h/4h, если оба присутствуют.

    volume_confirmed is None (неизвестно -- обычный случай для CoinGecko free OHLC, где
    объёма по свече нет вообще) НЕ даёт веса в скор: свип без данных об объёме -- недостаточно
    обоснован, чтобы влиять на Rocket Score, только на текст карточки. volume_confirmed
    True/False (объём реально известен) по-прежнему учитывается в любом случае -- это
    не про "объём подтвердил", а про "у нас вообще ЕСТЬ данные, чтобы судить"."""
    candidates = [s for s in (sweep_1h, sweep_4h)
                  if s and s["bars_ago"] <= FRESH_SWEEP_BARS and s["volume_confirmed"] is not None]
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


# ── Зоны поддержки/сопротивления + построение сделки от структуры ────────────

def _calc_atr_simple(candles: list, period: int = SR_ATR_PERIOD) -> float:
    """Простой ATR (True Range, среднее за последние period баров) -- достаточно для
    подбора ширины зоны, не нужен полный Wilder-сглаженный вариант bot.py:calc_atr()."""
    if len(candles) < 2:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h, l, prev_c = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
    window = trs[-period:] if len(trs) >= period else trs
    return sum(window) / len(window) if window else 0.0


def _timeframe_hours(tf_label: str) -> float:
    return {"1h": 1, "4h": 4, "1d": 24}.get(tf_label, 1)


def _collect_touch_points(candles: list, tf_label: str) -> list:
    """Фрактальные swing high/low этой серии как точки касания, с давностью в часах
    (нормализовано по таймфрейму, чтобы точки с разных ТФ были сравнимы по свежести)."""
    if not candles:
        return []
    highs, lows = _find_fractals(candles)
    n = len(candles)
    tf_hours = _timeframe_hours(tf_label)
    points = []
    for idx, level in highs:
        points.append({"kind": "high", "price": level, "hours_ago": (n - 1 - idx) * tf_hours, "tf": tf_label})
    for idx, level in lows:
        points.append({"kind": "low", "price": level, "hours_ago": (n - 1 - idx) * tf_hours, "tf": tf_label})
    return points


def find_sr_zones(candles_1h: list, candles_4h: list, candles_1d: list, price: float,
                   ema_ctx: dict = None) -> dict:
    """Зоны поддержки/сопротивления: кластеры фрактальных swing high/low (1h/4h/1d,
    2+ касания) + EMA50/100/200 (4h) как отдельная категория динамических уровней (без
    порога касаний -- это не фрактал, а математическое среднее).

    Ширина зоны = clamp(ATR% * ZONE_WIDTH_ATR_MULT, MIN, MAX) -- волатильнее рынок,
    шире зона, в пределах 0.3-0.8% из ТЗ.

    Возвращает {"above": [zone,...], "below": [zone,...]}, каждый список отсортирован
    от ближайшей к цене зоны к дальней. zone = {"lo","hi","mid","touches","hours_ago",
    "sources": [tf,...]}."""
    if not price or price <= 0:
        return {"above": [], "below": []}

    atr_candles = candles_4h or candles_1h or candles_1d or []
    atr = _calc_atr_simple(atr_candles)
    atr_pct = (atr / price * 100) if price else 0
    width_pct = max(ZONE_WIDTH_MIN_PCT, min(ZONE_WIDTH_MAX_PCT, atr_pct * ZONE_WIDTH_ATR_MULT))
    width_abs = price * width_pct / 100

    points = []
    points += _collect_touch_points(candles_1h, "1h")
    points += _collect_touch_points(candles_4h, "4h")
    points += _collect_touch_points(candles_1d, "1d")

    zones = []
    fractal_points = [p for p in points if p["kind"] in ("high", "low")]
    if fractal_points:
        fractal_points.sort(key=lambda p: p["price"])
        clusters, current = [], [fractal_points[0]]
        for p in fractal_points[1:]:
            if p["price"] - current[-1]["price"] <= width_abs:
                current.append(p)
            else:
                clusters.append(current)
                current = [p]
        clusters.append(current)

        for cluster in clusters:
            if len(cluster) < ZONE_MIN_TOUCHES:
                continue  # только фракталы с 2+ касаниями -- иначе это шум, не уровень
            prices = [p["price"] for p in cluster]
            mid = sum(prices) / len(prices)
            lo = min(mid - width_abs / 2, min(prices))
            hi = max(mid + width_abs / 2, max(prices))
            zones.append({
                "lo": lo, "hi": hi, "mid": mid,
                "touches": len(cluster), "hours_ago": min(p["hours_ago"] for p in cluster),
                "sources": sorted(set(p["tf"] for p in cluster)),
            })

    if ema_ctx and ema_ctx.get("tf_4h"):
        ema4h = ema_ctx["tf_4h"].get("ema", {})
        for period in (50, 100, 200):
            val = ema4h.get(period)
            if val:
                zones.append({
                    "lo": val - width_abs / 2, "hi": val + width_abs / 2, "mid": val,
                    "touches": 1, "hours_ago": 0, "sources": ["ema"],
                })

    # Зона может физически накрывать текущую цену (mid выше цены, но lo -- ниже, если зона
    # достаточно широкая и близкая) -- зажимаем границу по цене, иначе "зона сопротивления"
    # включает точку входа снизу и entry для шорта строится НИЖЕ текущей цены (не то, что
    # должно быть -- вход в шорт ждём РОСТА цены В зону, а не факта, что зона уже накрывает нас).
    above, below = [], []
    for z in zones:
        if z["mid"] > price:
            above.append({**z, "lo": max(z["lo"], price)})
        elif z["mid"] < price:
            below.append({**z, "hi": min(z["hi"], price)})
    above.sort(key=lambda z: z["mid"])
    below.sort(key=lambda z: -z["mid"])
    return {"above": above, "below": below}


def smart_round(val: float) -> float:
    """Округление цены до значащих цифр, адекватное и для BTC ($60000), и для
    мелких альтов ($0.00000123). Вынесено сюда из bot.py:real_full_analysis(), чтобы
    fa_engine.py могло им пользоваться без импорта bot.py (см. build_trade_from_structure)."""
    import math
    if val == 0:
        return 0
    magnitude = math.floor(math.log10(abs(val))) if val > 0 else 0
    precision = max(8, -magnitude + 3)
    return round(val, precision)


def build_trade_from_structure(direction: str, price: float, zones: dict):
    """Строит вход/SL/TP от зон структуры (find_sr_zones()). direction: "long"/"short".
    Вход -- DCA 50/30/20 внутри ближайшей зоны (entry1 у границы зоны, ближней к цене --
    основной ориентир для R:R; entry3 у дальней границы, самый агрессивный транш). SL --
    за зоной с буфером SR_SL_BUFFER_PCT от её дальней (по направлению риска) границы;
    зона уже построена на wick-инклюзивных high/low фракталов, так что хвосты уже учтены
    в самой границе. TP1/2/3 -- следующие 3 зоны с противоположной стороны; при нехватке
    зон -- Fibonacci-подобное расширение от риска (2.0/3.2/5.0x) как фоллбэк.

    Возвращает None, если нет ни одной зоны для входа (нечего строить), иначе dict:
    {"entry1","entry2","entry3","entry_lo","entry_hi","sl","tp1","tp2","tp3",
     "rr_tp1","rr_tp2","rr_tp3","entry_zone","tp_zones","rr_gate_pass"}."""
    if not price or price <= 0:
        return None

    entry_zones = zones.get("below", []) if direction == "long" else zones.get("above", [])
    tp_zones = zones.get("above", []) if direction == "long" else zones.get("below", [])
    if not entry_zones:
        return None

    entry_zone = entry_zones[0]
    if direction == "long":
        entry1, entry3 = entry_zone["hi"], entry_zone["lo"]  # entry1 ближе к цене (50%), entry3 -- дно зоны (20%)
        sl = entry_zone["lo"] * (1 - SR_SL_BUFFER_PCT / 100)
    else:
        entry1, entry3 = entry_zone["lo"], entry_zone["hi"]
        sl = entry_zone["hi"] * (1 + SR_SL_BUFFER_PCT / 100)
    entry2 = (entry1 + entry3) / 2
    entry_lo, entry_hi = min(entry_zone["lo"], entry_zone["hi"]), max(entry_zone["lo"], entry_zone["hi"])

    risk = abs(entry1 - sl) or 1e-9
    tps = [z["mid"] for z in tp_zones[:3]]
    fib_mults = (2.0, 3.2, 5.0)
    while len(tps) < 3:
        mult = fib_mults[len(tps)]
        tps.append(entry1 + risk * mult if direction == "long" else entry1 - risk * mult)
    tp1, tp2, tp3 = tps[0], tps[1], tps[2]

    def _rr(tp):
        return round(abs(tp - entry1) / risk, 2)

    rr_tp1 = _rr(tp1)
    return {
        "entry1": entry1, "entry2": entry2, "entry3": entry3,
        "entry_lo": entry_lo, "entry_hi": entry_hi,
        "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "rr_tp1": rr_tp1, "rr_tp2": _rr(tp2), "rr_tp3": _rr(tp3),
        "entry_zone": entry_zone, "tp_zones": tp_zones[:3],
        "rr_gate_pass": rr_tp1 >= SR_MIN_RR_TP1,
    }


# ── Доп. блоки для fa_engine.py (Полный анализ, 13 блоков) ───────────────────
# Всё ниже — тоже чистые функции над уже полученными свечами, без фетчинга.

def rsi(closes: list, period: int = 14) -> float:
    """RSI (Wilder), возвращает 50.0 (нейтрально) при нехватке данных."""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def ema_last(closes: list, period: int) -> float:
    """Последнее значение EMA(period), "мягкий" сид — см. _calc_ema_series. None-безопасно:
    при пустом closes возвращает 0.0."""
    if not closes:
        return 0.0
    series = _calc_ema_series(closes)[period] if period in EMA_PERIODS else None
    if series is not None:
        return series[-1] if series[-1] is not None else closes[-1]
    # период вне стандартного набора EMA_PERIODS — считаем на лету
    k = 2 / (period + 1)
    e = closes[0]
    for p in closes[1:]:
        e = p * k + e * (1 - k)
    return e


def swing_points(candles: list):
    """Публичная обёртка над _find_fractals — фрактальные swing high/low, по возрастанию
    index. Возвращает (swing_highs, swing_lows), каждый — список (index, price)."""
    return _find_fractals(candles)


def multi_tf_bias(candles_1d: list, candles_4h: list, candles_1h: list) -> dict:
    """Блок 1 ТЗ: bias LONG/SHORT/NEUTRAL из структуры 1D (HH/HL vs LH/LL по фракталам)
    + EMA-стек 4h как согласованность, 1h — контекст входа (тоже EMA-стек).

    Возвращает {"bias", "structure_1d", "tf_agreement", "detail"}."""
    out = {"bias": "NEUTRAL", "structure_1d": "не определена", "tf_agreement": "н/д", "detail": []}
    highs, lows = swing_points(candles_1d)
    structure_dir = None
    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1][1] > highs[-2][1]
        hl = lows[-1][1] > lows[-2][1]
        lh = highs[-1][1] < highs[-2][1]
        ll = lows[-1][1] < lows[-2][1]
        if hh and hl:
            structure_dir = "long"
            out["structure_1d"] = "HH/HL (аптренд)"
        elif lh and ll:
            structure_dir = "short"
            out["structure_1d"] = "LH/LL (даунтренд)"
        else:
            out["structure_1d"] = "смешанная (микс HH/LL)"

    ema_ctx = ema_context(candles_1h, candles_4h)
    tf4h = ema_ctx.get("tf_4h")
    tf1h = ema_ctx.get("tf_1h")
    stack_4h = tf4h["stack"] if tf4h else "недостаточно данных"
    stack_1h = tf1h["stack"] if tf1h else "недостаточно данных"

    ema_dir = None
    if stack_4h == "бычий":
        ema_dir = "long"
    elif stack_4h == "медвежий":
        ema_dir = "short"

    out["detail"] = [
        f"1D структура: {out['structure_1d']}",
        f"4H EMA-стек: {stack_4h}",
        f"1H EMA-стек (контекст входа): {stack_1h}",
    ]

    if structure_dir and ema_dir and structure_dir == ema_dir:
        out["bias"] = "LONG" if structure_dir == "long" else "SHORT"
        out["tf_agreement"] = "1D и 4H согласованы"
    elif structure_dir and not ema_dir:
        out["bias"] = "LONG" if structure_dir == "long" else "SHORT"
        out["tf_agreement"] = "1D задаёт направление, 4H EMA не подтверждает явно"
    elif ema_dir and not structure_dir:
        out["bias"] = "LONG" if ema_dir == "long" else "SHORT"
        out["tf_agreement"] = "4H EMA задаёт направление, 1D структура не подтверждает явно"
    elif structure_dir and ema_dir and structure_dir != ema_dir:
        out["bias"] = "NEUTRAL"
        out["tf_agreement"] = "1D и 4H расходятся"
    else:
        out["bias"] = "NEUTRAL"
        out["tf_agreement"] = "недостаточно данных"

    return out


def elliott_wave_heuristic(closes_1d: list, rsi_1d: float) -> dict:
    """Блок 2 ТЗ: упрощённая позиция в волновой структуре по swing-точкам 1D.
    Честно возвращает wave=None ("волна не определена"), если структура неясная —
    это не баг, а отражение того, что упрощённая эвристика не всегда классифицируется.

    Объём по свечам 1D недоступен (CoinGecko free OHLC отдаёт vol=0.0 всегда), поэтому
    условие "волна 5 с растущим объёмом" проверяется без объёмной компоненты, с явной
    пометкой в note."""
    out = {"wave": None, "label": "волна не определена", "note": "", "score_delta": 0}
    # closes_1d — просто числа (не candle-словари), swing_points тут не подходит — строим
    # swing-пивоты по close-серии вручную.
    n = len(closes_1d)
    if n < 15:
        out["note"] = "мало данных 1D для волнового анализа"
        return out

    side = 2
    piv_hi, piv_lo = [], []
    for i in range(side, n - side):
        v = closes_1d[i]
        if all(v > closes_1d[i - k] for k in range(1, side + 1)) and all(v > closes_1d[i + k] for k in range(1, side + 1)):
            piv_hi.append((i, v))
        if all(v < closes_1d[i - k] for k in range(1, side + 1)) and all(v < closes_1d[i + k] for k in range(1, side + 1)):
            piv_lo.append((i, v))

    pivots = sorted(piv_hi + piv_lo, key=lambda p: p[0])
    if len(pivots) < 4:
        out["note"] = "недостаточно чётких swing-точек"
        return out

    last4 = pivots[-4:]
    legs = [last4[i + 1][1] - last4[i][1] for i in range(3)]  # 3 последних хода

    if legs[0] < 0 and legs[1] > 0 and legs[2] > abs(legs[0]) * 0.8:
        out.update(wave="3", label="похоже на волну 3 (импульс)",
                    note="приоритет лонга — волна 3 обычно самая сильная", score_delta=10)
    elif legs[0] > 0 and legs[1] > 0 and rsi_1d > 70:
        out.update(wave="5", label="похоже на волну 5 (RSI перегрет)",
                    note="возможен разворот/коррекция после волны 5", score_delta=-5)
    elif legs[0] > 0 and legs[1] < 0 and legs[2] < 0:
        out.update(wave="C", label="похоже на коррекцию ABC (волна C вниз)",
                    note="ждать завершения коррекции", score_delta=0)
    elif legs[0] < 0 and legs[1] < 0 and legs[2] > 0:
        out.update(wave="2", label="похоже на волну 2 (коррекция перед волной 3)",
                    note="потенциальный вход у завершения коррекции", score_delta=5)
    else:
        out["note"] = "структура неоднозначная"
    return out


def smc_setup_type(candles_4h: list) -> dict:
    """Блок 3 ТЗ: BOS (пробой по тренду) / CHoCH (смена характера) / range (равные хаи/лои),
    по фрактальным swing-точкам 4h. Отличие BOS от CHoCH: BOS — продолжение уже
    установленного тренда (пред. свинги тоже HH/HL или LH/LL), CHoCH — первый пробой
    ПРОТИВ предыдущей структуры (разворотный сигнал)."""
    out = {"type": None, "label": "структура не определена"}
    highs, lows = swing_points(candles_4h)
    if len(highs) < 3 or len(lows) < 3:
        return out

    h = [p for _, p in highs[-3:]]
    l = [p for _, p in lows[-3:]]

    avg_h = sum(h) / len(h)
    avg_l = sum(l) / len(l)
    equal_highs = (max(h) - min(h)) <= avg_h * (ZONE_WIDTH_MIN_PCT / 100)
    equal_lows = (max(l) - min(l)) <= avg_l * (ZONE_WIDTH_MIN_PCT / 100)
    if equal_highs and equal_lows:
        out.update(type="range", label="range (равные хаи/лои — накопление в диапазоне)")
        return out

    prior_up = h[0] < h[1] and l[0] < l[1]
    prior_down = h[0] > h[1] and l[0] > l[1]
    last_hh = h[2] > h[1]
    last_ll = l[2] < l[1]

    if prior_up and last_hh:
        out.update(type="BOS_bull", label="BOS — пробой структуры вверх по тренду")
    elif prior_down and last_hh:
        out.update(type="CHoCH_bull", label="CHoCH — смена характера, первый пробой вверх")
    elif prior_down and last_ll:
        out.update(type="BOS_bear", label="BOS — пробой структуры вниз по тренду")
    elif prior_up and last_ll:
        out.update(type="CHoCH_bear", label="CHoCH — смена характера, первый пробой вниз")
    return out


def find_fvg_zones(candles_4h: list, price: float) -> list:
    """Блок 4 ТЗ: незакрытые FVG на 4h — гэпы между свечами i-1 и i+1, ещё не
    "закрытые" (цена не возвращалась внутрь гэпа после его формирования).
    Возвращает список {"lo","hi","mid","type":"bull"/"bear","distance_pct"}, ближайшие
    к цене — первыми."""
    if len(candles_4h) < 3 or not price:
        return []
    zones = []
    n = len(candles_4h)
    for i in range(1, n - 1):
        prev_c, next_c = candles_4h[i - 1], candles_4h[i + 1]
        # bullish FVG
        if prev_c["high"] < next_c["low"]:
            gap_lo, gap_hi = prev_c["high"], next_c["low"]
            filled = any(c["low"] <= gap_lo for c in candles_4h[i + 2:])
            if not filled:
                mid = (gap_lo + gap_hi) / 2
                zones.append({"lo": gap_lo, "hi": gap_hi, "mid": mid, "type": "bull",
                              "distance_pct": round((mid - price) / price * 100, 2)})
        # bearish FVG
        if next_c["high"] < prev_c["low"]:
            gap_lo, gap_hi = next_c["high"], prev_c["low"]
            filled = any(c["high"] >= gap_hi for c in candles_4h[i + 2:])
            if not filled:
                mid = (gap_lo + gap_hi) / 2
                zones.append({"lo": gap_lo, "hi": gap_hi, "mid": mid, "type": "bear",
                              "distance_pct": round((mid - price) / price * 100, 2)})
    zones.sort(key=lambda z: abs(z["distance_pct"]))
    return zones


def equal_levels(candles: list, tolerance_pct: float = 0.3) -> list:
    """Блок 6 ТЗ: equal highs/lows — 2+ вершины/донья в пределах tolerance_pct друг от
    друга (магниты стопов). Возвращает список {"price","kind":"high"/"low","touches"}."""
    highs, lows = swing_points(candles)
    out = []
    for kind, pts in (("high", [p for _, p in highs]), ("low", [p for _, p in lows])):
        if len(pts) < 2:
            continue
        pts_sorted = sorted(pts)
        cluster = [pts_sorted[0]]
        for v in pts_sorted[1:]:
            if v - cluster[-1] <= cluster[-1] * tolerance_pct / 100:
                cluster.append(v)
            else:
                if len(cluster) >= 2:
                    out.append({"price": sum(cluster) / len(cluster), "kind": kind, "touches": len(cluster)})
                cluster = [v]
        if len(cluster) >= 2:
            out.append({"price": sum(cluster) / len(cluster), "kind": kind, "touches": len(cluster)})
    return out


def wyckoff_phase_heuristic(closes_1d: list, price: float) -> dict:
    """Блок 9 ТЗ: Wyckoff-эвристика по диапазону/положению цены (БЕЗ объёма — CoinGecko
    free OHLC не отдаёт объём по свече, честно помечаем это в note, а не подставляем
    фиктивный сигнал)."""
    out = {"phase": "не определена", "note": "объём: нет данных (CoinGecko free OHLC не отдаёт объём по свече)"}
    if len(closes_1d) < 20:
        out["note"] = "мало данных 1D для определения фазы. " + out["note"]
        return out
    window = closes_1d[-90:] if len(closes_1d) >= 90 else closes_1d
    lo, hi = min(window), max(window)
    pos = (price - lo) / (hi - lo) if hi > lo else 0.5
    look = min(30, len(closes_1d) - 1)
    trend_pct = (closes_1d[-1] - closes_1d[-1 - look]) / closes_1d[-1 - look] * 100 if closes_1d[-1 - look] else 0

    if trend_pct > 15:
        out["phase"] = "Маркап (Markup)"
    elif trend_pct < -15:
        out["phase"] = "Маркдаун (Markdown)"
    elif pos < 0.3:
        out["phase"] = "Накопление (Accumulation)"
    elif pos > 0.7:
        out["phase"] = "Распределение (Distribution)"
    else:
        out["phase"] = "переходная/не определена"
    return out
