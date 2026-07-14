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


def _stack_label(last: dict, price: float = None) -> str:
    """"Бычий"/"медвежий" требует ДВУХ условий: (1) порядок EMA20>EMA50>EMA100>EMA200
    (или зеркально) И (2) цена ПОДТВЕРЖДАЕТ порядок -- находится выше (ниже) ОБЕИХ
    ближних EMA20/EMA50. Раньше проверялся только порядок EMA -- честный баг, найден
    владельцем на живой карточке Pump-Reversal EVAA (2026-07-11): "стек бычий (4h)"
    при цене НИЖЕ EMA20 и EMA50 (4h). EMA лагают за ценой -- порядок EMA может ещё не
    "догнать" свежий разворот, поэтому один лишь порядок недостаточен для честного
    вердикта. Расхождение порядка и цены -- "смешанный" (переходное состояние), не
    ложный бычий/медвежий. `price=None` (вызов без цены) -- откат к чистому порядку
    EMA, как раньше, для обратной совместимости мест, где цены под рукой нет."""
    vals = [last.get(p) for p in EMA_PERIODS]
    if any(v is None for v in vals):
        return "недостаточно данных"
    e20, e50, e100, e200 = vals
    ema_order_bull = e20 > e50 > e100 > e200
    ema_order_bear = e20 < e50 < e100 < e200
    if price is not None:
        if ema_order_bull and not (price > e20 and price > e50):
            return "смешанный"
        if ema_order_bear and not (price < e20 and price < e50):
            return "смешанный"
    if ema_order_bull:
        return "бычий"
    if ema_order_bear:
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
    stack = _stack_label(last, price)

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


def old_style_ema_trend(closes: list, ema_fast: int = 20, ema_slow: int = 50) -> str:
    """П-EMA (владелец, ночное задание 14->15.07, Пакет 3 -- подготовка, БЕЗ
    активации в бою) -- КОНТРОЛИРУЕМЫЙ дубликат `tf_trend()`, вложенной
    закрытой функции внутри `bot.pro_analysis()` (bot.py:9222): "цена выше
    быстрой EMA выше медленной EMA" = bullish, зеркально bearish, иначе
    neutral. НАМЕРЕННО не рефакторинг pro_analysis() на переиспользование
    этой функции -- pro_analysis() боевая (памп-радар), железные границы
    CLAUDE.md запрещают трогать боевую сигнальную логику без явного
    одобрения владельца, даже поведение-сохраняющим рефакторингом. Эта
    функция -- read-only копия ФОРМУЛЫ для shadow-сравнения со СТАРОЙ
    методологией multi-TF confluence в AUTO-пути (`real_full_analysis()`,
    который использует НОВУЮ методологию `ema_context()`/
    `ema_stack_score_delta()` как боевую -- см. shadow_engine.py
    EMA_AUTO_SHADOW_ENABLED)."""
    if len(closes) < ema_slow:
        return "neutral"
    ef = ema_last(closes, ema_fast) or closes[-1]
    es = ema_last(closes, ema_slow) or closes[-1]
    p = closes[-1]
    if p > ef > es:
        return "bullish"
    if p < ef < es:
        return "bearish"
    return "neutral"


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


TP_LADDER_MIN_STEP_PCT = 0.5  # владелец, кейс MOODENG 2026-07-13: TP1 0.0400262 vs
# TP2 0.04002969 -- разница 0.009%, неразличимо. find_sr_zones() иногда даёт 2+ зоны
# (например EMA-зона и фрактальная зона) с почти идентичным mid. Минимальный шаг между
# СОСЕДНИМИ принятыми целями лестницы -- TP1 НЕ переоценивается (боевой R:R-гейт/
# rr_gate_pass зависит только от TP1, см. bot.real_full_analysis() -- эта функция
# отвечает за боевые entry/SL/TP1, трогать выбор TP1 нельзя), только TP2 (шаг от TP1)
# и TP3 (шаг от TP2).


def build_trade_from_structure(direction: str, price: float, zones: dict):
    """Строит вход/SL/TP от зон структуры (find_sr_zones()). direction: "long"/"short".
    Вход -- DCA 50/30/20 внутри ближайшей зоны (entry1 у границы зоны, ближней к цене --
    основной ориентир для R:R; entry3 у дальней границы, самый агрессивный транш). SL --
    за зоной с буфером SR_SL_BUFFER_PCT от её дальней (по направлению риска) границы;
    зона уже построена на wick-инклюзивных high/low фракталов, так что хвосты уже учтены
    в самой границе. TP1 -- ближайшая зона с противоположной стороны (или Fibonacci-
    расширение 2.0x риска, если зон нет вообще) -- НЕ переоценивается валидатором лестницы
    (боевой R:R-гейт зависит только от TP1). TP2/TP3 -- следующие зоны структуры СО
    ШАГОМ >= TP_LADDER_MIN_STEP_PCT от предыдущей принятой цели (сканирует ВСЕ оставшиеся
    зоны, не только позиционно вторую/третью, пропуская слишком близкие к предыдущей);
    при нехватке разнесённых зон -- Fibonacci-подобное расширение от риска (3.2x/5.0x)
    как фоллбэк для незаполненного слота.

    Возвращает None, если нет ни одной зоны для входа (нечего строить), иначе dict:
    {"entry1","entry2","entry3","entry_lo","entry_hi","sl","tp1","tp2","tp3",
     "rr_tp1","rr_tp2","rr_tp3","entry_zone","tp_zones","tp_sources","rr_gate_pass"}.
    `tp_sources` -- ["structure"|"fibonacci", ...] для TP1/TP2/TP3, для честной
    пометки в карточках ("TP2: структура" vs "TP2: Fib-расширение")."""
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
    fib_mults = (2.0, 3.2, 5.0)

    def _fib(mult):
        return entry1 + risk * mult if direction == "long" else entry1 - risk * mult

    remaining_zone_mids = [z["mid"] for z in tp_zones]

    # TP1 -- поведение БУКВАЛЬНО не изменено относительно кода до этого патча
    # (ближайшая зона либо fib(2.0x), если зон нет вовсе).
    if remaining_zone_mids:
        tp1 = remaining_zone_mids.pop(0)
        tp_sources = ["structure"]
    else:
        tp1 = _fib(fib_mults[0])
        tp_sources = ["fibonacci"]

    tps = [tp1]
    last = tp1
    for slot_idx in (1, 2):
        picked = None
        while remaining_zone_mids:
            z = remaining_zone_mids.pop(0)
            if abs(z - last) / entry1 * 100 >= TP_LADDER_MIN_STEP_PCT:
                picked = z
                tp_sources.append("structure")
                break
        if picked is None:
            picked = _fib(fib_mults[slot_idx])
            tp_sources.append("fibonacci")
        tps.append(picked)
        last = picked
    tp1, tp2, tp3 = tps

    def _rr(tp):
        return round(abs(tp - entry1) / risk, 2)

    rr_tp1 = _rr(tp1)
    return {
        "entry1": entry1, "entry2": entry2, "entry3": entry3,
        "entry_lo": entry_lo, "entry_hi": entry_hi,
        "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "rr_tp1": rr_tp1, "rr_tp2": _rr(tp2), "rr_tp3": _rr(tp3),
        "entry_zone": entry_zone, "tp_zones": tp_zones[:3], "tp_sources": tp_sources,
        "rr_gate_pass": rr_tp1 >= SR_MIN_RR_TP1,
    }


DCA_WEIGHTS = (0.5, 0.3, 0.2)  # entry1/entry2/entry3 -- те же доли, что в подписях "Вход 1 (50%)" и т.д.


def weighted_dca_entry(trade: dict) -> float:
    """Средневзвешенный DCA-вход (50/30/20 по entry1/entry2/entry3 из
    build_trade_from_structure()) -- ОДНА честная база для R:R и % вместо смеси
    "R:R от entry1, % от live-цены" (АПГРЕЙД 11.07 Этап 2.1, x100-сканер: карточка
    показывала 'TP1 +2.9% R:R 1:1.6' при 'SL -9.9%' -- разные базы для % и R:R
    создавали видимость нечестного R:R, хотя сам rr_tp1 (от entry1) был посчитан
    правильно). НЕ используется в build_trade_from_structure()/боевом rr_gate_pass
    для top_long/top_short/fa_engine -- те гейты владелец менять не просил, остаются
    на entry1. Только для x100 (явный запрос владельца, аддитивно)."""
    w1, w2, w3 = DCA_WEIGHTS
    return trade["entry1"] * w1 + trade["entry2"] * w2 + trade["entry3"] * w3


def rr_from_base(trade: dict, base: float, min_rr: float = SR_MIN_RR_TP1) -> dict:
    """R:R по TP1/TP2/TP3 и honest-гейт относительно произвольной `base` (не
    обязательно entry1) -- та же формула, что приватная _rr() внутри
    build_trade_from_structure(), только с явной базой. Используется x100-сканером
    поверх уже посчитанного trade (weighted_dca_entry(trade) как base) -- см. её
    докстринг."""
    risk = abs(base - trade["sl"]) or 1e-9

    def _rr(tp):
        return round(abs(tp - base) / risk, 2)

    rr_tp1 = _rr(trade["tp1"])
    return {
        "base": base, "risk": risk,
        "rr_tp1": rr_tp1, "rr_tp2": _rr(trade["tp2"]), "rr_tp3": _rr(trade["tp3"]),
        "rr_gate_pass": rr_tp1 >= min_rr,
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
    out = {"bias": "NEUTRAL", "structure_1d": "не определена", "tf_agreement": "н/д", "detail": [],
           "key_low": None, "key_high": None}
    highs, lows = swing_points(candles_1d)

    # Локальная структура (ТЗ Блок 1): последний ключевой минимум (его пробой -- первая
    # предпосылка разворота/продолжения вниз) и последний ключевой хай (его снятие --
    # цель движения вверх) -- явно, с ценами, независимо от того, определился ли bias.
    if lows:
        idx, lvl = lows[-1]
        out["key_low"] = {"price": lvl, "bars_ago": len(candles_1d) - 1 - idx}
    if highs:
        idx, lvl = highs[-1]
        out["key_high"] = {"price": lvl, "bars_ago": len(candles_1d) - 1 - idx}

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
    if out["key_low"]:
        out["detail"].append(f"Ключевой минимум: {smart_round(out['key_low']['price'])} "
                             f"(пробой = предпосылка разворота/продолжения вниз)")
    if out["key_high"]:
        out["detail"].append(f"Ключевой хай: {smart_round(out['key_high']['price'])} "
                             f"(снятие = цель движения вверх)")

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


def smc_setup_type(candles_4h: list, bias_direction: str = None) -> dict:
    """Блок 3 ТЗ: BOS (пробой по тренду) / CHoCH (смена характера) / range (равные хаи/лои),
    по фрактальным swing-точкам 4h.

    ВАЖНО (см. историю бага v110->v111): bias_direction (блок 1 — Multi-TF bias, тот же
    единый источник структуры, что использует чеклист) — ЕДИНСТВЕННЫЙ критерий "по
    тренду"/"против тренда" здесь. Раньше BOS/CHoCH определялись по собственной 4h-локальной
    истории свингов (prior_up/prior_down), независимо от bias — из-за этого блоки 1/3/5
    могли противоречить друг другу (bias SHORT + "BOS вверх ПО ТРЕНДУ" одновременно).
    Теперь: пробой вверх при bias_direction=="long" -> BOS (продолжение, +скор), пробой
    вверх при bias_direction=="short" -> CHoCH (разворот ПРОТИВ bias, предупреждение,
    -скор). Если bias_direction не передан (NEUTRAL/неизвестен) — сверять не с чем,
    возвращается только факт пробоя без BOS/CHoCH ярлыка (aligned=None).

    Возвращает {"type", "label", "aligned": True/False/None} — aligned=True означает
    "пробой согласован с bias" (BOS), False — "пробой против bias" (CHoCH,
    контр-сигнал), None — bias не определён либо пробоя/структуры нет."""
    out = {"type": None, "label": "структура не определена", "aligned": None}
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

    last_hh = h[2] > h[1]
    last_ll = l[2] < l[1]
    if not last_hh and not last_ll:
        return out  # ни пробоя вверх, ни вниз -- структура не определена

    break_dir = "long" if last_hh else "short"  # пробой вверх -- в пользу лонга, вниз -- шорта
    break_word = "вверх" if last_hh else "вниз"

    if bias_direction is None:
        out.update(type=("break_up" if last_hh else "break_down"),
                   label=f"Пробой структуры {break_word} (bias NEUTRAL — BOS/CHoCH не размечены)")
        return out

    if break_dir == bias_direction:
        out.update(type=("BOS_bull" if last_hh else "BOS_bear"),
                   label=f"BOS — пробой структуры {break_word} по тренду (согласовано с bias {bias_direction.upper()})",
                   aligned=True)
    else:
        out.update(type=("CHoCH_bull" if last_hh else "CHoCH_bear"),
                   label=f"CHoCH — пробой {break_word} ПРОТИВ bias {bias_direction.upper()} — возможный разворот",
                   aligned=False)
    return out


def smc_setup_type_body_close_variant(candles_4h: list, bias_direction: str = None) -> dict:
    """A/B-вариант smc_setup_type() -- Пакет 11 М1 (владелец "да" на A/B тело-vs-фитиль).

    Находка ночного цикла (knowledge/METHODOLOGY_CORE.md §1): два источника расходятся
    в критерии валидности слома структуры. Инструктор B (уже реализован в
    smc_setup_type() выше и во всём боевом движке -- _find_fractals/swing_points
    сравнивают только high/low, без проверки close) считает пробой ТЕНЬЮ достаточным.
    "Урок 2. Structure.pdf" (cryptomannn.com) заявляет обратное: пробой без ЗАКРЫТИЯ
    свечи за уровнем -- это SFP (снятие ликвидности), не валидный слом.

    Та же логика swing-точек/range/HH-LL, что и smc_setup_type(), плюс один
    дополнительный гейт: среди свечей МЕЖДУ предпоследним и последним swing-экстремумом
    (включительно по последнему) должна быть хотя бы одна, ЗАКРЫВШАЯСЯ за уровнем
    предпоследнего экстремума. Если нет -- пробой понижается до
    "invalid_break_wick_only" вместо BOS/CHoCH/break_up/break_down.

    Shadow-only: НЕ вызывается из живого пути, не участвует в rocket/скоринге/гейтах --
    только измерение расхождения для отчёта владельцу (см. shadow_engine.py)."""
    out = {"type": None, "label": "структура не определена", "aligned": None}
    highs, lows = swing_points(candles_4h)
    if len(highs) < 3 or len(lows) < 3:
        return out

    h_idx = [i for i, _ in highs[-3:]]
    h = [p for _, p in highs[-3:]]
    l_idx = [i for i, _ in lows[-3:]]
    l = [p for _, p in lows[-3:]]

    avg_h = sum(h) / len(h)
    avg_l = sum(l) / len(l)
    equal_highs = (max(h) - min(h)) <= avg_h * (ZONE_WIDTH_MIN_PCT / 100)
    equal_lows = (max(l) - min(l)) <= avg_l * (ZONE_WIDTH_MIN_PCT / 100)
    if equal_highs and equal_lows:
        out.update(type="range", label="range (равные хаи/лои — накопление в диапазоне)")
        return out

    last_hh = h[2] > h[1]
    last_ll = l[2] < l[1]
    if not last_hh and not last_ll:
        return out

    break_dir = "long" if last_hh else "short"
    break_word = "вверх" if last_hh else "вниз"

    if last_hh:
        old_level, old_i, new_i = h[1], h_idx[1], h_idx[2]
        closed_beyond = any(c["close"] > old_level for c in candles_4h[old_i + 1:new_i + 1])
    else:
        old_level, old_i, new_i = l[1], l_idx[1], l_idx[2]
        closed_beyond = any(c["close"] < old_level for c in candles_4h[old_i + 1:new_i + 1])

    if not closed_beyond:
        out.update(type="invalid_break_wick_only",
                    label=f"Пробой {break_word} только тенью, без закрытия за уровнем "
                          f"{old_level:.6g} -- по критерию \"Урок 2. Structure\" это SFP, "
                          f"не валидный слом структуры")
        return out

    if bias_direction is None:
        out.update(type=("break_up" if last_hh else "break_down"),
                    label=f"Пробой структуры {break_word}, закрытие подтверждено "
                          f"(bias NEUTRAL — BOS/CHoCH не размечены)")
        return out

    if break_dir == bias_direction:
        out.update(type=("BOS_bull" if last_hh else "BOS_bear"),
                    label=f"BOS — пробой структуры {break_word} по тренду, закрытие "
                          f"подтверждено (согласовано с bias {bias_direction.upper()})",
                    aligned=True)
    else:
        out.update(type=("CHoCH_bull" if last_hh else "CHoCH_bear"),
                    label=f"CHoCH — пробой {break_word} ПРОТИВ bias {bias_direction.upper()}, "
                          f"закрытие подтверждено — возможный разворот",
                    aligned=False)
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


def wyckoff_phase_heuristic(closes_1d: list, price: float, vols_1d: list = None) -> dict:
    """Блок 9 ТЗ: Wyckoff-эвристика по диапазону/положению цены. Классификация фазы —
    только по цене (без объёмной компоненты, см. докстринг модуля — упрощённая
    эвристика). vols_1d — опционально, только для честной пометки в note: если объём
    реально есть в данных (Bybit — основной источник свечей), это отмечается явно, если
    его нет вообще или он везде 0.0 (CoinGecko-фоллбек), note честно об этом говорит,
    вместо того чтобы подставлять фиктивный сигнал."""
    has_volume = bool(vols_1d) and any((v or 0) > 0 for v in vols_1d)
    vol_note = ("объём: доступен, но не используется в этой упрощённой эвристике" if has_volume
                else "объём: нет данных (источник свечей не отдаёт объём по этой монете)")
    out = {"phase": "не определена", "note": vol_note}
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


# ── K-LVL: усиленные уровни (методика K-LVL/ICT) ────────────────────────────

KLVL_MIN_CRITERIA = 2             # зона становится K-LVL при выполнении >= этого числа критериев
KLVL_MIN_TOUCHES = 3              # критерий (а): 3+ касания
KLVL_IMPULSE_LOOKBACK_BARS = 3    # критерий (б): импульс в течение N баров после касания
KLVL_IMPULSE_PCT = 2.0            # критерий (б): импульс >= этого % от цены касания
KLVL_RANGE_LOOKBACK_BARS = 20     # критерий (г): диапазон последних N баров 4h
KLVL_RANGE_EDGE_TOLERANCE_PCT = 1.0  # критерий (г): допуск близости к границе диапазона
KLVL_POLARITY_FLIP_CLOSES = 2     # (3): пробой K-LVL подтверждён N закрытиями 4h за уровнем


def _impulse_after_touch(candles: list, zone_lo: float, zone_hi: float) -> bool:
    """Критерий (б): было ли хотя бы одно касание зоны (свеча зашла внутрь [lo,hi]),
    после которого в течение KLVL_IMPULSE_LOOKBACK_BARS баров цена ушла от цены касания
    на >= KLVL_IMPULSE_PCT% (реакция от уровня, а не просто прохождение сквозь него)."""
    n = len(candles)
    for i in range(n - 1):
        c = candles[i]
        touched = (zone_lo <= c["low"] <= zone_hi) or (zone_lo <= c["high"] <= zone_hi) or \
                  (c["low"] <= zone_lo and c["high"] >= zone_hi)
        if not touched:
            continue
        base_price = c["close"]
        if base_price <= 0:
            continue
        future = candles[i + 1:i + 1 + KLVL_IMPULSE_LOOKBACK_BARS]
        if not future:
            continue
        max_move_pct = max(abs(f["close"] - base_price) / base_price * 100 for f in future)
        if max_move_pct >= KLVL_IMPULSE_PCT:
            return True
    return False


def classify_klvl_zones(zones_side: list, candles_4h: list) -> list:
    """Присваивает зонам (список из find_sr_zones()["above"|"below"]) метку K-LVL по
    методике: зона -- K-LVL при выполнении >= KLVL_MIN_CRITERIA из:
      (а) 3+ касания
      (б) от уровня был импульс >= KLVL_IMPULSE_PCT% за KLVL_IMPULSE_LOOKBACK_BARS баров
          после касания (реакция, не пробой навылет)
      (в) зона построена частично из 1D-свинга (более значимый ТФ, чем чисто 1h/4h) --
          "1d" в zone["sources"]
      (г) зона совпадает с границей текущего диапазона (хай/лоу последних
          KLVL_RANGE_LOOKBACK_BARS 4h-баров, в пределах KLVL_RANGE_EDGE_TOLERANCE_PCT%)
    Возвращает НОВЫЙ список (не мутирует вход), каждая зона дополнена "klvl": bool и
    "klvl_criteria": dict."""
    range_hi = range_lo = None
    if candles_4h:
        window = candles_4h[-KLVL_RANGE_LOOKBACK_BARS:]
        if window:
            range_hi = max(c["high"] for c in window)
            range_lo = min(c["low"] for c in window)

    out = []
    for z in zones_side:
        crit_a = z.get("touches", 0) >= KLVL_MIN_TOUCHES
        crit_b = _impulse_after_touch(candles_4h, z["lo"], z["hi"]) if candles_4h else False
        crit_c = "1d" in z.get("sources", [])
        crit_d = False
        if range_hi is not None and z["mid"] > 0:
            tol = z["mid"] * KLVL_RANGE_EDGE_TOLERANCE_PCT / 100
            crit_d = abs(z["mid"] - range_hi) <= tol or abs(z["mid"] - range_lo) <= tol
        criteria = {"touches3+": crit_a, "impulse": crit_b, "swing_1d": crit_c, "range_edge": crit_d}
        z2 = dict(z)
        z2["klvl"] = sum(criteria.values()) >= KLVL_MIN_CRITERIA
        z2["klvl_criteria"] = criteria
        out.append(z2)
    return out


def detect_polarity_flip(zone: dict, candles_4h: list, was_below: bool) -> dict:
    """Критерий (3) ТЗ: K-LVL пробит и цена закрепилась (KLVL_POLARITY_FLIP_CLOSES
    закрытий 4h за уровнем) -- "смена роли" (support->resistance или наоборот).
    was_below: была ли зона исторически ПОДДЕРЖКОЙ (ниже цены на момент построения зоны) --
    если пробита вверх устояла, это уже не имеет смысла (это не пробой); проверяем пробой
    в сторону, разрушающую её изначальную роль (support пробит ВНИЗ, resistance -- ВВЕРХ).
    Возвращает {"flipped": bool, "new_role": "support"|"resistance"|None}."""
    if not candles_4h or len(candles_4h) < KLVL_POLARITY_FLIP_CLOSES:
        return {"flipped": False, "new_role": None}
    last_closes = [c["close"] for c in candles_4h[-KLVL_POLARITY_FLIP_CLOSES:]]
    mid = zone["mid"]
    if was_below:
        # была поддержкой (below price) -- пробой ВНИЗ (закрытия ниже zone lo) рвёт её роль
        broken = all(c < zone["lo"] for c in last_closes)
        return {"flipped": broken, "new_role": "resistance" if broken else None}
    else:
        # была сопротивлением (above price) -- пробой ВВЕРХ (закрытия выше zone hi) рвёт роль
        broken = all(c > zone["hi"] for c in last_closes)
        return {"flipped": broken, "new_role": "support" if broken else None}


def classify_breaker_or_mitigation(candles: list, direction: str) -> dict:
    """ПАТЧ (ночная сессия #2, Блок 1 -- теневой контур, см. SHADOW_MODE.md /
    knowledge/MISMATCH_REPORT.md п.5 / patches/03-breaker-mitigation/README.md).
    Аддитивная функция, ни один live вызывающий код её не использует -- подключена
    только к shadow_engine.py.

    Различает Breaker vs Mitigation Block по методике (knowledge/METHODOLOGY_CORE.md §3,
    подтверждено 2 независимыми источниками курса -- методичка + видео):
      - Breaker: на экстремуме последнего свинга, ОТ которого сформирован новый
        структурный HH/LL, ликвидность СНИМАЕТСЯ (импульс пробивает уровень навылет
        перед разворотом структуры).
      - Mitigation Block: ликвидность НЕ снимается с этого экстремума -- вместо этого
        формируется более высокий лоу (для лонга)/более низкий хай (для шорта), и уже
        ПОСЛЕ этого ломается структура.

    direction: "long" -- ищем последний swing low перед структурным движением вверх
    (бычий breaker/MB); "short" -- зеркально, swing high перед движением вниз.

    Реализация: кандидат на breaker/MB -- ВТОРОЙ С КОНЦА фрактальный свинг (не самый
    последний). Причина: по определению фрактала последний свинг-лоу уже ниже всех
    баров в пределах FRACTAL_SIDE_BARS справа от него -- если бы что-то позже пробило его
    ещё ниже И ТОЖЕ было фракталом, оно само стало бы последним свингом. Поэтому "свип
    последнего перед-структурным-движением свинга" физически проверяется на
    ВТОРОМ С КОНЦА свинге, а роль "снявшей ликвидность" свечи играет либо более свежий
    фрактальный свинг, либо просто любая свеча в сегменте до структурного хая/лоу.

    Возвращает {"type": "breaker"|"mitigation"|None, "zone": {"lo","hi"}|None,
    "swing_idx": int|None} -- None при недостатке свинг-данных, не выдумывает результат."""
    highs, lows = swing_points(candles)
    none_result = {"type": None, "zone": None, "swing_idx": None}

    if direction == "long":
        if len(lows) < 2 or len(highs) < 1:
            return none_result
        cand_idx, cand_price = lows[-2]
        highs_after = [(i, p) for i, p in highs if i > cand_idx]
        if not highs_after:
            return none_result
        struct_high_idx, _ = highs_after[0]
        segment = candles[cand_idx + 1:struct_high_idx + 1]
        swept = any(c["low"] < cand_price for c in segment)
        candle = candles[cand_idx]
        zone = {"lo": candle["low"], "hi": max(candle["open"], candle["close"])}
        return {"type": "breaker" if swept else "mitigation", "zone": zone,
                "swing_idx": cand_idx}

    if direction == "short":
        if len(highs) < 2 or len(lows) < 1:
            return none_result
        cand_idx, cand_price = highs[-2]
        lows_after = [(i, p) for i, p in lows if i > cand_idx]
        if not lows_after:
            return none_result
        struct_low_idx, _ = lows_after[0]
        segment = candles[cand_idx + 1:struct_low_idx + 1]
        swept = any(c["high"] > cand_price for c in segment)
        candle = candles[cand_idx]
        zone = {"lo": min(candle["open"], candle["close"]), "hi": candle["high"]}
        return {"type": "breaker" if swept else "mitigation", "zone": zone,
                "swing_idx": cand_idx}

    return none_result


def detect_price_indicator_divergence(candles: list, period: int = 14) -> dict:
    """ПАТЧ (ночная сессия #2, Блок 1 -- теневой контур, см. SHADOW_MODE.md /
    knowledge/MISMATCH_REPORT.md п.10 / patches/04-rsi-divergence/README.md).
    Аддитивная функция, ни один live вызывающий код её не использует -- подключена
    только к shadow_engine.py.

    Классическая/скрытая дивергенция цена-vs-RSI (knowledge/METHODOLOGY_CORE.md §10,
    источник: Урок 4. Дивергенция, TDP, TTS.mp4 [136s-367s]) -- КОНТРАРИАНСКАЯ трактовка
    источника, не наивная "дивергенция = сигнал входа":
      - Классическая (цена HH + RSI LH сверху, или цена LL + RSI HL снизу) -- источник
        трактует как признак ВОЗМОЖНОЙ коррекции ПРОТИВ HTF-bias, не сигнал входа по
        тренду дивергенции (это то, что делают "массы", по словам источника).
      - Скрытая (цена LH + RSI HH сверху = медвежья; цена HL + RSI LL снизу = бычья) --
        источник трактует как подтверждение ПРОДОЛЖЕНИЯ HTF-тренда.
      - Бычья скрытая (цена HL + RSI LL) выведена по симметрии с явно описанной медвежьей
        скрытой в источнике -- НЕ процитирована дословно, честно помечаю как вывод по
        аналогии, не прямую цитату.

    RSI считается в момент каждого свинга через уже существующий rsi() (срез closes до
    индекса свинга) -- переиспользует чистую функцию, не дублирует формулу.

    Возвращает {"bearish_classical", "bearish_hidden", "bullish_classical",
    "bullish_hidden": bool, "detail": [...]} -- сравнивает 2 последних свинг-хая (медвежьи
    варианты) и 2 последних свинг-лоя (бычьи варианты) по swing_points()."""
    highs, lows = swing_points(candles)
    closes = [c["close"] for c in candles]
    result = {"bearish_classical": False, "bearish_hidden": False,
              "bullish_classical": False, "bullish_hidden": False, "detail": []}

    if len(highs) >= 2:
        (i1, p1), (i2, p2) = highs[-2], highs[-1]
        r1 = rsi(closes[:i1 + 1], period)
        r2 = rsi(closes[:i2 + 1], period)
        if p2 > p1 and r2 < r1:
            result["bearish_classical"] = True
            result["detail"].append({"type": "bearish_classical", "price": (p1, p2), "rsi": (r1, r2)})
        elif p2 < p1 and r2 > r1:
            result["bearish_hidden"] = True
            result["detail"].append({"type": "bearish_hidden", "price": (p1, p2), "rsi": (r1, r2)})

    if len(lows) >= 2:
        (i1, p1), (i2, p2) = lows[-2], lows[-1]
        r1 = rsi(closes[:i1 + 1], period)
        r2 = rsi(closes[:i2 + 1], period)
        if p2 < p1 and r2 > r1:
            result["bullish_classical"] = True
            result["detail"].append({"type": "bullish_classical", "price": (p1, p2), "rsi": (r1, r2)})
        elif p2 > p1 and r2 < r1:
            result["bullish_hidden"] = True
            result["detail"].append({"type": "bullish_hidden", "price": (p1, p2), "rsi": (r1, r2)})

    return result


RSI_DIVERGENCE_AGAINST_PENALTY = 5  # Пакет 9 (владелец, "ДА" -- патч 04 в бой как штраф
# скоринга, НЕ жёсткий гейт, порог гейта не менять). ЧЕСТНО: в теневом конфиге
# НЕ было заранее заданного числового штрафа для этого патча (в отличие от
# DEAD_ZONE_SHADOW_SCORE_PENALTY в shadow_engine.py, который существовал для патча
# 01 до перевода в бой) -- владелец предполагал обратное, это расхождение
# зафиксировано в PROGRESS.md. Величина 5 баллов подобрана здесь как консервативный
# первый шаг (для сравнения: ema_stack_score_delta/sweep_score_delta дают +-8/+-10) на
# основе изоляции 03/04/05 (`PATCH_IMPACT.md`): affected win rate 49.2% vs
# не-affected 54.3% (-5.1 п.п.), avg R +0.565 vs +0.874, PF 2.15 vs 2.99 -- реальный,
# но не экстремальный эффект. Легко перенастраивается одной константой, включая до 0
# (эффективный откат) или пересчёт от новых shadow-данных.


def divergence_score_delta(divergence: dict, direction: str) -> int:
    """-RSI_DIVERGENCE_AGAINST_PENALTY, если КЛАССИЧЕСКАЯ RSI-дивергенция (см.
    detect_price_indicator_divergence()) направлена ПРОТИВ направления сигнала
    (bearish_classical при long, bullish_classical при short), иначе 0. Только штраф,
    без бонуса за дивергенцию "за" направление и без учёта скрытой (hidden)
    дивергенции -- источник трактует классическую контрарианской (сигнал возможной
    коррекции), скрытую как подтверждение продолжения тренда, разные интерпретации
    смешивать не даём (см. detect_price_indicator_divergence() докстринг)."""
    if not divergence:
        return 0
    against = (direction == "long" and divergence.get("bearish_classical")) or \
              (direction == "short" and divergence.get("bullish_classical"))
    return -RSI_DIVERGENCE_AGAINST_PENALTY if against else 0


BPR_MAX_BAR_GAP = 5   # макс. расстояние между формированием двух противоположных FVG,
                       # чтобы считать их "друг за другом" (BPR-пара), не случайным совпадением


def detect_bpr_zones(candles: list) -> list:
    """ПАТЧ (ночная сессия #2, Блок 1 -- теневой контур, см. SHADOW_MODE.md /
    knowledge/MISMATCH_REPORT.md п.3 / patches/05-bpr/README.md). Аддитивная функция,
    ни один live вызывающий код её не использует -- подключена только к shadow_engine.py.

    BPR (Balanced Price Range, knowledge/METHODOLOGY_CORE.md §4, источник BPR.mp4
    [0s-170s]) -- область пересечения ДВУХ ПРОТИВОПОЛОЖНЫХ имбалансов (FVG),
    формирующихся друг за другом при смене направления. Уже отдельно есть
    find_fvg_zones() (используется в fa_engine block4 для отображения POI), но она не
    хранит индекс формирования каждого FVG -- здесь он нужен, чтобы находить ПАРЫ
    соседних по времени противоположных FVG, поэтому BPR делает свой независимый скан
    той же 3-свечной формации (не дублирует логику find_fvg_zones для другой цели,
    просто не может её переиспользовать без индекса).

    Возвращает список {"lo","hi","mid","bull_fvg_idx","bear_fvg_idx","formed_idx"} --
    только реальные пересечения (lo < hi), отсортированные по свежести (formed_idx по
    убыванию, самые свежие -- первыми)."""
    n = len(candles)
    bull_fvgs, bear_fvgs = [], []
    for i in range(1, n - 1):
        prev_c, next_c = candles[i - 1], candles[i + 1]
        if prev_c["high"] < next_c["low"]:
            bull_fvgs.append((i, prev_c["high"], next_c["low"]))
        if next_c["high"] < prev_c["low"]:
            bear_fvgs.append((i, next_c["high"], prev_c["low"]))

    zones = []
    for bi, blo, bhi in bull_fvgs:
        for si, slo, shi in bear_fvgs:
            if abs(si - bi) > BPR_MAX_BAR_GAP:
                continue
            lo, hi = max(blo, slo), min(bhi, shi)
            if lo < hi:
                zones.append({
                    "lo": lo, "hi": hi, "mid": (lo + hi) / 2,
                    "bull_fvg_idx": bi, "bear_fvg_idx": si,
                    "formed_idx": max(bi, si),
                })
    zones.sort(key=lambda z: z["formed_idx"], reverse=True)
    return zones


# ── Пакет 5 М3 (владелец, "ДА" -- ТОЛЬКО shadow-скоринг, не бой) ──
# amd_phase/smc_inducement по методологии, отдельно от живого bot.pro_analysis()
# (который использует грубую час-суточную эвристику без ценового якоря вообще
# -- METHODOLOGY_CORE.md §18.2 -- и не трогается этим пакетом). Пишутся ТОЛЬКО
# в shadow-записи через shadow_engine.compute_shadow(), НИКАКОГО влияния на
# bull_pts/bear_pts/pro_score/боевые гейты.

import datetime as _dt
import pytz as _pytz

_NY_TZ = _pytz.timezone("America/New_York")


def ny_midnight_price(candles: list, now_utc: "_dt.datetime" = None):
    """Цена на New York Midnight (00:00 America/New_York, с учётом перехода на
    летнее время) -- осевой уровень AMD/MMXM-модели (METHODOLOGY_CORE.md
    §18.2/§18.3), которого в движке раньше не было ВООБЩЕ (grep на
    daily_open/midnight -- 0 совпадений до этого пакета). Разрешение свечей
    (4h) не даёт точного попадания в полночь -- честно берёт CLOSE ближайшей
    4h-свечи, чья временная метка <= NY-полночи, не интерполирует точнее, чем
    позволяют данные. None, если подходящей свечи нет (мало истории)."""
    if not candles or now_utc is None:
        return None
    now_ny = now_utc.astimezone(_NY_TZ)
    midnight_ny = now_ny.replace(hour=0, minute=0, second=0, microsecond=0)
    midnight_utc_ms = int(midnight_ny.astimezone(_pytz.utc).timestamp() * 1000)
    candidates = [c for c in candles if c.get("timestamp", 0) <= midnight_utc_ms]
    if not candidates:
        return None
    return candidates[-1]["close"]


def classify_amd_phase(candles_4h: list, now_utc: "_dt.datetime" = None) -> dict:
    """AMD/PO3-фаза, честно привязанная к New York Midnight (см.
    ny_midnight_price()) -- в отличие от живой эвристики bot.pro_analysis()
    (только час суток, без ценового якоря, см. METHODOLOGY_CORE.md §18.2).
    Часовые окна -- те же, что в живом коде (UTC+3, Стамбул): Азия 01-09
    accumulation, Лондон 09-13 manipulation, NY 15-22 distribution, иначе
    dead_zone. НЕ полная MMXM-модель (нет проверки breaker+непокрытый FVG для
    валидации SMR, METHODOLOGY_CORE.md §18.3) -- честно упрощённая версия, но
    с добавленным ценовым якорем, которого не было вообще. ТОЛЬКО для
    shadow-записей -- не читается никаким боевым путём."""
    if now_utc is None:
        now_utc = _dt.datetime.now(_dt.timezone.utc)
    now_ist = now_utc.astimezone(_dt.timezone(_dt.timedelta(hours=3)))
    h = now_ist.hour
    nymid = ny_midnight_price(candles_4h, now_utc)
    last_close = candles_4h[-1]["close"] if candles_4h else None
    price_vs_nymidnight = None
    if nymid is not None and last_close is not None:
        price_vs_nymidnight = "above" if last_close > nymid else "below"

    if 1 <= h < 9:
        phase = "accumulation"
    elif 9 <= h < 13:
        if price_vs_nymidnight == "below":
            phase = "manipulation_bear"
        elif price_vs_nymidnight == "above":
            phase = "manipulation_bull"
        else:
            phase = "manipulation"
    elif 15 <= h < 22:
        if price_vs_nymidnight == "above":
            phase = "distribution_bull"
        elif price_vs_nymidnight == "below":
            phase = "distribution_bear"
        else:
            phase = "distribution"
    else:
        phase = "dead_zone"

    return {"phase": phase, "nymidnight_price": nymid,
            "price_vs_nymidnight": price_vs_nymidnight}


OI_MATRIX_NEAR_ZERO_PCT = 0.1  # владелец, кейсы AVAX 15:42/DOT 14:48, 2026-07-13:
# |ΔOI| < этого порога -- шум измерения (CoinGecko OI approximation, не точная
# биржевая история), не реальный сдвиг открытого интереса. Тот же порог уже
# использовался в bot._analyze_whale_signal() для скоринга (Whale Monitor) --
# здесь распространяется и на ТЕКСТ интерпретации в остальных местах, которые
# этот же порог для текста не применяли (bot._format_whale_alert() oi_line,
# pump_detector._oi_matrix_label(), fa_engine._oi_matrix() oi_text).


def classify_oi_matrix(oi_change_pct, price_change_pct) -> str:
    """Классифицирует комбинацию цена×OI в один из 5 тегов:
    "up_up"/"up_down"/"down_up"/"down_down" (обе величины ненулевые, за
    порогом OI_MATRIX_NEAR_ZERO_PCT) или "near_zero" (|ΔOI| < порога -- ЛЮБОЙ
    вердикт вида "сквиз"/"выход из позиций" на таком шуме честно вводит в
    заблуждение). "no_data", если один из параметров None.

    ТОЛЬКО классификация для ТЕКСТА интерпретации -- не гейт и не боевой скор
    (см. владелец, 2026-07-13: "боевой скоринг не трогать"). Вызывающая
    сторона сама решает, как оформить каждый тег текстом/эмодзи -- эта
    функция только отвечает на вопрос "заслуживает ли ΔOI решительной
    интерпретации, или это шум"."""
    if oi_change_pct is None or price_change_pct is None:
        return "no_data"
    if abs(oi_change_pct) < OI_MATRIX_NEAR_ZERO_PCT:
        return "near_zero"
    price_up = price_change_pct > 0
    oi_up = oi_change_pct > 0
    if price_up and oi_up:
        return "up_up"
    if price_up and not oi_up:
        return "up_down"
    if not price_up and oi_up:
        return "down_up"
    return "down_down"


def detect_inducement_sweep(candles: list, min_bars_ago: int = 3) -> dict:
    """Упрощённая детекция inducement -- снятие ликвидности ПЕРЕД POI (не
    самим входом), см. METHODOLOGY_CORE.md §18.5 (источники "36 Стрим Работа
    в POI"/"Воркшоп 2.0 5 день"). Настоящий inducement требует привязки к
    конкретной POI-зоне и "чистого билдинга" перед свипом -- этого здесь НЕТ,
    честно не выдаю за полную реализацию. Упрощение: переиспользует
    detect_sweep() -- если свежий свип найден, но случился НЕ на последних
    min_bars_ago барах (то есть до текущей точки, не в момент входа),
    считается кандидатом на inducement. Порог min_bars_ago эвристический, не
    из источника."""
    sweep = detect_sweep(candles)
    if not sweep:
        return {"inducement_swept": False, "detail": None}
    if sweep["bars_ago"] >= min_bars_ago:
        return {"inducement_swept": True, "detail": sweep}
    return {"inducement_swept": False, "detail": sweep}


def detect_order_block(candles: list, price: float) -> dict:
    """ПАТЧ 07 (Пакет 7 М1 -- владелец "ДА" на вариант B из Пакета 5 М2/6 М1:
    shadow-only фикс геометрии, живой pro_analysis() НЕ трогается).

    Строит ОБЕ геометрии Order Block на ОДНИХ И ТЕХ ЖЕ 4H-свечах для честного
    сравнения:
      - "live" -- точное зеркало инлайн-кода `pro_analysis()` (`bot.py`,
        функция `pro_analysis`, блок "ICT ORDER BLOCK"): bull zone
        (candle["open"], candle["high"]), bear zone (candle["low"],
        candle["open"]) -- микс тела и фитиля. Это КОПИЯ формулы для
        тестируемости, не рефакторинг живого пути -- живой инлайн-код в
        bot.py не тронут и не вызывает эту функцию.
      - "methodology" -- по METHODOLOGY_CORE.md §18.1 ("OB ВСЕГДА по телу,
        даже некрасивый OB с большим фитилём — по телу, не по хвостам",
        источник "18. Бектесты 3.mp4"): bull zone (candle["close"],
        candle["open"]) -- тело последней медвежьей свечи перед разворотом
        (close < open, поэтому close -- нижняя граница тела); bear zone
        (candle["open"], candle["close"]) -- тело последней бычьей свечи
        перед разворотом.

    Критерий сигнальной свечи (body/range > 0.5) и подтверждение пробоя
    следующими 3 свечами -- ОДИНАКОВЫ для обеих геометрий и скопированы из
    живого кода без изменений; различие изолировано ИМЕННО в границах зоны
    (тело vs тело+фитиль) и, как следствие, в проверке "цена сейчас внутри
    зоны" (та же формула контроля vs 1%/0.99 буфер, что и в живом коде) --
    это и есть узкий вопрос, который просил проверить владелец, не полная
    переработка детектора.

    НЕ проверяет "снятие ликвидности предыдущего свинга" -- второй core-
    критерий OB по методике, честно отсутствует в ОБЕИХ версиях здесь (тот
    же пробел, что уже зафиксирован в §18.1, не расширяю объём патча за
    пределы geometry-вопроса).

    price=None (симптом отсутствующих данных) -- обе геометрии возвращаются
    как "не активны" (False), а не выдумывают активность без цены.

    Возвращает {"live": {"bull": bool, "bull_zone": (lo,hi)|None, "bear": bool,
    "bear_zone": (lo,hi)|None}, "methodology": {...та же форма...}}."""
    empty = {"bull": False, "bull_zone": None, "bear": False, "bear_zone": None}
    live = dict(empty)
    meth = dict(empty)
    if not candles or len(candles) < 9 or price is None:
        return {"live": live, "methodology": meth}

    for i in range(5, len(candles) - 3):
        candle = candles[i]
        next3 = candles[i + 1:i + 4]
        body = abs(candle["close"] - candle["open"])
        rng = candle["high"] - candle["low"]
        if rng == 0:
            continue

        if candle["close"] < candle["open"] and body / rng > 0.5:
            if all(c["close"] > candle["high"] for c in next3):
                live_lo, live_hi = candle["open"], candle["high"]
                if live_lo <= price <= live_hi * 1.01:
                    live["bull"], live["bull_zone"] = True, (live_lo, live_hi)
                meth_lo, meth_hi = candle["close"], candle["open"]
                if meth_lo <= price <= meth_hi * 1.01:
                    meth["bull"], meth["bull_zone"] = True, (meth_lo, meth_hi)

        if candle["close"] > candle["open"] and body / rng > 0.5:
            if all(c["close"] < candle["low"] for c in next3):
                live_lo, live_hi = candle["low"], candle["open"]
                if live_lo * 0.99 <= price <= live_hi:
                    live["bear"], live["bear_zone"] = True, (live_lo, live_hi)
                meth_lo, meth_hi = candle["open"], candle["close"]
                if meth_lo * 0.99 <= price <= meth_hi:
                    meth["bear"], meth["bear_zone"] = True, (meth_lo, meth_hi)

    return {"live": live, "methodology": meth}


# ── Пакет 14 (владелец, 2026-07-13): "тип сетапа" + 13-блочный shadow-вердикт ─
# real_full_analysis_TZ.md НЕ найден в репозитории (проверено find + git log
# --all по имени файла) -- состав ниже синтезирован из:
#   - real_full_analysis_TZ_reconstructed.md (реконструкция 13 блоков по fa_engine.py)
#   - knowledge/_ocr/trading_guide_4_.txt, раздел "Торговые сетапы" (AMD/
#     SH-BOS-RTO/Sweep/Cypher -- строки ~195-270)
#   - knowledge/KNOWLEDGE_INDEX.md, "Kira ICT Trading Analysis.pdf" (6-пунктовый
#     чек-лист входа, OI-матрица)
#   - knowledge/METHODOLOGY_CORE.md (killzone/DCA/общие правила)
# Честно помечено в PROGRESS.md как синтез, не находка оригинального файла.

CYPHER_XAB_RETRACE_LO = 0.382    # коррекция B от XA (нижняя граница)
CYPHER_XAB_RETRACE_HI = 0.618    # коррекция B от XA (верхняя граница)
CYPHER_XAC_PROJECTION_LO = 1.272 # C -- проекция XA (нижняя граница), т.е. |XC|/|XA|
CYPHER_XAC_PROJECTION_HI = 1.414 # верхняя граница проекции
CYPHER_XCD_RETRACE = 0.786       # D -- коррекция XC
CYPHER_TOLERANCE = 0.08          # допуск на каждое отношение -- эвристика на фрактальных
# точках, не инструмент точного рисования TradingView (источник прямо говорит: "На
# TradingView есть специальный инструмент рисования для этого паттерна" -- эта функция
# его НЕ заменяет, только грубый скрининг по свежим фракталам).


def detect_cypher_pattern(candles: list) -> dict:
    """Тип сетапа "Cypher" -- гармонический паттерн (Даррен Оглсби, Fibonacci-
    based), см. knowledge/_ocr/trading_guide_4_.txt ("Паттерн Cypher"): коррекция
    точки B первичного отрезка XA лежит между 0.382 и 0.618; точка C -- проекция
    XA от 1.272 до 1.414; точка D -- 0.786 коррекции XC. TP1/TP2 -- 0.382/0.618
    коррекция CD (от D к C), SL -- за точкой X.

    Ищет ПОСЛЕДНИЕ 5 чередующихся фрактальных swing-точек (X-A-B-C-D, по
    _find_fractals) и проверяет геометрию с допуском CYPHER_TOLERANCE на каждое
    отношение. Честно возвращает bull=False/bear=False при отсутствии валидной
    геометрии -- не "почти похоже". direction по чередованию: X-low/A-high/...
    /D-low = bull (вход в лонг на D, цель -- назад к C), X-high/.../D-high = bear."""
    out = {"bull": False, "bear": False, "points": None, "tp1": None, "tp2": None, "sl": None}
    highs, lows = _find_fractals(candles)
    tagged = sorted([(i, p, "H") for i, p in highs] + [(i, p, "L") for i, p in lows],
                     key=lambda t: t[0])
    merged = []
    for i, p, kind in tagged:
        if merged and merged[-1][2] == kind:
            if (kind == "H" and p > merged[-1][1]) or (kind == "L" and p < merged[-1][1]):
                merged[-1] = (i, p, kind)
        else:
            merged.append((i, p, kind))
    if len(merged) < 5:
        return out
    X, A, B, C, D = merged[-5:]
    kinds = [pt[2] for pt in (X, A, B, C, D)]
    if kinds not in (["L", "H", "L", "H", "L"], ["H", "L", "H", "L", "H"]):
        return out

    xv, av, bv, cv, dv = X[1], A[1], B[1], C[1], D[1]
    xa = av - xv
    if xa == 0:
        return out
    ab_retrace = abs(av - bv) / abs(xa)
    if not (CYPHER_XAB_RETRACE_LO - CYPHER_TOLERANCE <= ab_retrace <= CYPHER_XAB_RETRACE_HI + CYPHER_TOLERANCE):
        return out
    xc_projection = abs(cv - xv) / abs(xa)
    if not (CYPHER_XAC_PROJECTION_LO - CYPHER_TOLERANCE <= xc_projection <= CYPHER_XAC_PROJECTION_HI + CYPHER_TOLERANCE):
        return out
    xc = cv - xv
    if xc == 0:
        return out
    cd_retrace_of_xc = abs(cv - dv) / abs(xc)
    if not (CYPHER_XCD_RETRACE - CYPHER_TOLERANCE <= cd_retrace_of_xc <= CYPHER_XCD_RETRACE + CYPHER_TOLERANCE):
        return out

    is_bull = kinds[0] == "L"
    cd = cv - dv
    tp1 = dv + cd * 0.382
    tp2 = dv + cd * 0.618
    sl = xv * (1 - SR_SL_BUFFER_PCT / 100) if is_bull else xv * (1 + SR_SL_BUFFER_PCT / 100)
    common = {"points": {"X": xv, "A": av, "B": bv, "C": cv, "D": dv},
              "tp1": tp1, "tp2": tp2, "sl": sl}
    if is_bull:
        out.update(bull=True, **common)
    else:
        out.update(bear=True, **common)
    return out


def classify_setup_type(candles_4h: list, direction: str = None, now_utc: "_dt.datetime" = None) -> dict:
    """"Тип сетапа" (владелец, Пакет 14, 2026-07-13) -- 4 паттерна из
    knowledge/_ocr/trading_guide_4_.txt ("Торговые сетапы"):
      - Cypher -- гармонический XABCD, см. detect_cypher_pattern() выше.
      - SH-BOS-RTO ("Stop Hunt - BOS - RTO. Часть 5") -- "первичный" разворот
        по тексту источника: свип стопов -> слом структуры (BOS) -> возврат в
        ориджин (RTO) для mitigation. Здесь: свежий sweep (в пределах
        FRESH_SWEEP_BARS) + BOS согласован с bias (aligned=True от
        smc_setup_type) -- прямое соответствие определению.
      - AMD ("AMD (Accumulation, Manipulation, Distribution)") -- фаза
        манипуляции/дистрибуции по уже существующей classify_amd_phase()
        (NY-midnight сессионная эвристика) -- САМА classify_amd_phase() честно
        помечена как упрощение (по часам сессии + цена vs NY-midnight, не
        полный range+двусторонний sweep+POI-тест из текста источника).
      - Sweep ("Liquidity Sweep") -- свежий sweep БЕЗ полного SH-BOS-RTO
        подтверждения (BOS не согласован/структура не определена) -- по
        тексту это более слабое/агрессивное условие ("преимущественное
        отсутствие возможности консервативного входа с подтверждением на LTF").
    Приоритет проверки: Cypher (самая специфичная геометрия) -> SH-BOS-RTO
    ("первичный" сетап по тексту, остальные -- "производные") -> AMD -> Sweep
    (самый общий случай) -> None. Возвращает {"setup_type": str|None, "label":
    str, "detail": {...}}."""
    cypher = detect_cypher_pattern(candles_4h)
    if cypher.get("bull") or cypher.get("bear"):
        side = "bull" if cypher["bull"] else "bear"
        return {"setup_type": "Cypher",
                "label": f"Cypher ({side}) — TP1 {smart_round(cypher['tp1'])} / "
                         f"TP2 {smart_round(cypher['tp2'])}, SL {smart_round(cypher['sl'])}",
                "detail": cypher}

    sweep = detect_sweep(candles_4h)
    bos = smc_setup_type(candles_4h, direction) if direction else {"aligned": None, "type": None,
                                                                     "label": "bias не определён"}
    if sweep and sweep["bars_ago"] <= FRESH_SWEEP_BARS and bos.get("aligned") is True:
        return {"setup_type": "SH-BOS-RTO",
                "label": f"SH-BOS-RTO — свип {sweep['type']} ({sweep['bars_ago']} баров назад) + {bos['label']}",
                "detail": {"sweep": sweep, "bos": bos}}

    amd = classify_amd_phase(candles_4h, now_utc)
    if amd["phase"] in ("manipulation_bull", "manipulation_bear", "distribution_bull", "distribution_bear"):
        return {"setup_type": "AMD",
                "label": f"AMD — фаза {amd['phase']} (цена {amd['price_vs_nymidnight']} NY-midnight)",
                "detail": amd}

    if sweep and sweep["bars_ago"] <= FRESH_SWEEP_BARS:
        return {"setup_type": "Sweep",
                "label": f"Liquidity Sweep — {sweep['type']} ({sweep['bars_ago']} баров назад), без подтверждённого BOS",
                "detail": sweep}

    return {"setup_type": None,
            "label": "чёткого сетапа из набора AMD/SH-BOS-RTO/Sweep/Cypher не найдено",
            "detail": {}}


TZ13_POI_PROXIMITY_PCT = 1.5        # "цена у POI" -- тот же порог, что fa_engine.POI_PROXIMITY_PCT
TZ13_CHECKLIST_MIN_FOR_TRADE = 4    # тот же порог, что fa_engine.CHECKLIST_MIN_FOR_TRADE


def _tz13_verdict_text(direction, has_setup: bool, checklist_score: int, setup_block: dict) -> str:
    if not direction:
        return "направление не определено (1D/4H не согласованы)"
    side_ru = "лонг" if direction == "long" else "шорт"
    setup_label = setup_block.get("label", "н/д") if setup_block else "н/д"
    status = "сетап готов" if has_setup else "сетапа нет"
    return f"{side_ru}: {status} (чеклист {checklist_score}/6), тип сетапа: {setup_label}"


def build_13block_verdict(candles_1h: list, candles_4h: list, candles_1d: list,
                           price: float, killzone_status: dict, funding: dict,
                           oi_change, oi_combo, ls_ratio: float,
                           now_utc: "_dt.datetime" = None) -> dict:
    """Пакет 14 (владелец, 2026-07-13): полный НЕЗАВИСИМЫЙ 13-блочный вердикт,
    параллельный bot.real_full_analysis() -- НЕ читает и не использует её
    is_long/rocket/tp/sl/entry, строит СВОЁ направление и план заново из
    ta_extra-примитивов (тот же принцип независимости, что и у остальных
    shadow-патчей shadow_engine.compute_shadow()). Не делает НИКАКИХ новых
    сетевых вызовов -- candles_1h/4h/1d, killzone_status, funding, oi_change/
    oi_combo, ls_ratio все уже посчитаны вызывающей стороной для других целей
    (см. bot.real_full_analysis() -- комментарий "уже посчитаны выше... без
    новых сетевых вызовов", тот же принцип).

    13 блоков: 1 bias, 2 Elliott 1D+4H, 3 тип сетапа, 4 POI/зоны, 5 чек-лист
    Kira|ICT, 6 ликвидность/sweep, 7 OI-матрица, 8 killzone/сессия
    (get_killzone_status() -- единый источник, см. ENGINE_UNIFICATION.md, НЕ
    четвёртое определение часов), 9 фаза рынка, 10 DCA 50/30/20, 11 TP1/2/3+R:R,
    12 SL за структурой (+2-3%, SR_SL_BUFFER_PCT), 13 итоговый вердикт.

    Каждый блок в try/except на уровне вызывающей стороны (bot.py) -- сама эта
    функция может упасть целиком, вызывающая сторона это уже ожидает (тот же
    паттерн, что oi_funding_ls_shadow/bos_body_close_shadow/order_block_shadow)."""
    out = {"ok": True}
    closes_1d = [c["close"] for c in candles_1d] if candles_1d else [c["close"] for c in candles_4h]
    closes_4h = [c["close"] for c in candles_4h]
    rsi_1d = rsi(closes_1d, 14)
    rsi_4h = rsi(closes_4h, 14)

    # Блок 1: Multi-TF bias
    b1 = multi_tf_bias(candles_1d, candles_4h, candles_1h)
    direction = "long" if b1["bias"] == "LONG" else ("short" if b1["bias"] == "SHORT" else None)
    out["block1_bias"] = b1

    # Блок 2: Elliott Wave 1D + 4H (владелец явно просил обе -- "Elliott Wave 4H/1D")
    out["block2_elliott"] = {
        "elliott_1d": elliott_wave_heuristic(closes_1d, rsi_1d),
        "elliott_4h": elliott_wave_heuristic(closes_4h, rsi_4h),
    }

    # Блок 3: тип сетапа AMD/SH-BOS-RTO/Sweep/Cypher
    out["block3_setup_type"] = classify_setup_type(candles_4h, direction, now_utc)

    # Блок 4: POI/K-LVL зоны (те же примитивы, что fa_engine.py/build_trade_from_structure)
    ema_ctx = ema_context(candles_1h, candles_4h)
    zones = find_sr_zones(candles_1h, candles_4h, candles_1d, price, ema_ctx=ema_ctx)
    out["block4_zones"] = zones

    # Блок 5: чек-лист Kira|ICT (6 пунктов) -- см. Kira ICT Trading Analysis.pdf
    # (KNOWLEDGE_INDEX.md: "6-пунктовый чек-лист входа"), те же пункты, что уже
    # реализованы в fa_engine.py Block 5 (независимая, но методологически
    # идентичная реализация здесь -- без импорта fa_engine, ta_extra остаётся
    # листовым модулем без зависимостей на bot.py/fa_engine.py).
    sweep_1h = detect_sweep(candles_1h)
    sweep_4h = detect_sweep(candles_4h)
    items = []
    struct_ok = bool(direction) and (
        (direction == "long" and "аптренд" in b1.get("structure_1d", "")) or
        (direction == "short" and "даунтренд" in b1.get("structure_1d", "")))
    items.append(("Тренд старшего ТФ (1D) совпадает с направлением", struct_ok))
    fresh_sweep = bool(direction) and sweep_score_delta(sweep_1h, sweep_4h, direction) > 0
    items.append(("Свежий свип ликвидности в пользу направления", fresh_sweep))
    entry_side = "below" if direction == "long" else ("above" if direction == "short" else None)
    poi_zones = zones.get(entry_side, []) if entry_side else []
    near_poi = bool(poi_zones and price and
                     abs(poi_zones[0]["mid"] - price) / price * 100 <= TZ13_POI_PROXIMITY_PCT)
    items.append(("Цена у зоны интереса (не в вакууме)", near_poi))
    kz_ok = bool(killzone_status) and bool(
        killzone_status.get("is_good") or
        (killzone_status.get("next") and killzone_status["next"].get("in_min", 999) <= 60))
    items.append(("Killzone активна или близко", kz_ok))
    funding_ok = True
    if funding and funding.get("ok") and direction:
        rate = funding["rate"]
        funding_ok = not (rate > 0.05 if direction == "long" else rate < -0.05)
    items.append(("Funding не против позиции", funding_ok))
    trade = build_trade_from_structure(direction, price, zones) if direction else None
    rr_ok = bool(trade and trade["rr_gate_pass"])
    items.append(("R:R по структуре ≥ 1:1.5", rr_ok))
    checklist_score = sum(1 for _, ok in items if ok)
    out["block5_checklist"] = {"items": items, "score": checklist_score}

    # Блок 6: ликвидность/ловушки
    out["block6_liquidity"] = {
        "sweep_1h": sweep_1h, "sweep_4h": sweep_4h,
        "equal_levels": equal_levels(candles_4h, tolerance_pct=0.3),
    }

    # Блок 7: OI-матрица + funding + L/S (уже посчитаны вызывающей стороной, см.
    # bot.py oi_funding_ls_shadow -- один _get_oi_change() вызов на весь
    # real_full_analysis(), эта функция его не дублирует)
    out["block7_oi"] = {"oi_change_pct": oi_change, "oi_combo": oi_combo,
                         "funding": funding, "ls_ratio": ls_ratio}

    # Блок 8: killzone/сессия -- killzone_status ПРИХОДИТ от вызывающей стороны
    # (bot.get_killzone_status(), Патч 01) -- единый источник часов, не заводим
    # четвёртое определение (владелец, Задача Пакета 14 п.2; см. ENGINE_UNIFICATION.md).
    out["block8_killzone"] = killzone_status

    # Блок 9: фаза рынка (Wyckoff-эвристика)
    vols_1d = [c.get("vol", 0) for c in candles_1d] if candles_1d else [c.get("vol", 0) for c in candles_4h]
    out["block9_phase"] = wyckoff_phase_heuristic(closes_1d, price, vols_1d=vols_1d)

    # Блок 10/11/12: DCA 50/30/20, TP1/TP2/TP3 c R:R, SL за структурой (+2-3%)
    if trade:
        out["block10_dca"] = {"entry1": trade["entry1"], "entry2": trade["entry2"],
                               "entry3": trade["entry3"], "weights": "50/30/20"}
        out["block11_tp_rr"] = {"tp1": trade["tp1"], "tp2": trade["tp2"], "tp3": trade["tp3"],
                                 "rr_tp1": trade["rr_tp1"], "rr_tp2": trade["rr_tp2"],
                                 "rr_tp3": trade["rr_tp3"], "rr_gate_pass": trade["rr_gate_pass"]}
        out["block12_sl"] = {"sl": trade["sl"], "buffer_pct": SR_SL_BUFFER_PCT}
    else:
        out["block10_dca"] = {"entry1": None, "entry2": None, "entry3": None, "weights": "50/30/20"}
        out["block11_tp_rr"] = {"tp1": None, "tp2": None, "tp3": None, "rr_tp1": None,
                                 "rr_tp2": None, "rr_tp3": None, "rr_gate_pass": False}
        out["block12_sl"] = {"sl": None, "buffer_pct": SR_SL_BUFFER_PCT}

    # Блок 13: итоговый вердикт
    has_setup = bool(trade and trade["rr_gate_pass"] and checklist_score >= TZ13_CHECKLIST_MIN_FOR_TRADE)
    out["block13_verdict"] = {
        "has_setup": has_setup, "direction": direction, "score": checklist_score,
        "text": _tz13_verdict_text(direction, has_setup, checklist_score, out["block3_setup_type"]),
    }

    # Агрегированные поля верхнего уровня -- владелец, п.3: "score, направление,
    # зона, SL/TP" -- для удобного логирования/сравнения без раскопки вложенности.
    out["direction"] = direction
    out["score"] = checklist_score
    out["entry_zone"] = {"lo": trade["entry_lo"], "hi": trade["entry_hi"]} if trade else None
    out["sl"] = trade["sl"] if trade else None
    out["tp1"] = trade["tp1"] if trade else None
    out["tp2"] = trade["tp2"] if trade else None
    out["tp3"] = trade["tp3"] if trade else None
    out["setup_type"] = out["block3_setup_type"]["setup_type"]
    return out
