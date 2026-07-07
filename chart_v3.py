"""
BEST TRADE — Chart v3: график сделки для свинг-сигналов (ТОП ЛОНГ/ШОРТ, signal_loop
алерты, промоушены радара). ТФ 2h (~120 баров) — в отличие от Chart v2
(pump_detector.py:_build_chart), который остаётся 5m для памп/дамп сценариев; это два
разных горизонта для разных типов сигналов, не замена друг друга.

Стиль — та же тёмная палитра, что и везде в проекте (BG/GREEN/RED/WHITE/GRAY/YELLOW,
см. bot.py). Поверх свечей: Фибоначчи-ретрейсмент от последнего значимого свинга (тот
же детектор, что и в fa_engine.py -- ta_extra.swing_points, чтобы уровни на графике
совпадали с уровнями в карточке анализа), горизонтальные линии лимиток DCA-входа,
R:R-зоны (красная к SL, зелёная к TP) вправо от текущего времени, ключевые swing-уровни
(хай/лоу из блока bias) с ценниками на правой оси. Info-панель OHLC (как в Chart v2)
здесь не нужна -- вместо неё компактная шапка: символ/ТФ/направление/R:R.

Модуль НЕ зависит от bot.py -- candles передаются вызывающей стороной уже
полученными (bot.get_binance_ohlc), это чистая функция рендеринга.
"""

import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches

import ta_extra

BG = "#0D1421"
GREEN = "#16C784"
RED = "#EA3943"
WHITE = "#FFFFFF"
GRAY = "#7B8BB2"
GOLD = "#F0B90B"
ENTRYC = "#FFD700"

FIB_RATIOS = (0.5, 0.618, 0.786)
DISPLAY_BARS = 120
FUTURE_WIDTH_PCT = 0.20   # R:R-зоны и лимитки тянутся вправо на ~20% ширины графика
LOCAL_SWING_WINDOW = 30   # окно для вторичного (локального) фибо-грида


def _fmt_price(v) -> str:
    if v is None:
        return "?"
    if abs(v) >= 1000:
        return f"{v:,.1f}"
    if abs(v) >= 1:
        return f"{v:,.4f}".rstrip("0").rstrip(".")
    return f"{v:.8g}"


def _last_significant_swing(candles):
    """Последний значимый ход (лоу->хай или хай->лоу) по фрактальным swing-точкам --
    тот же ta_extra.swing_points(), что использует fa_engine.py для блока 1/4, чтобы
    фибо-уровни на графике не расходились с уровнями в карточке анализа."""
    highs, lows = ta_extra.swing_points(candles)
    if not highs or not lows:
        return None
    last_high_idx, last_high_price = highs[-1]
    last_low_idx, last_low_price = lows[-1]
    if last_high_idx > last_low_idx:
        return {"start_idx": last_low_idx, "start_price": last_low_price,
               "end_idx": last_high_idx, "end_price": last_high_price, "dir": "up"}
    return {"start_idx": last_high_idx, "start_price": last_high_price,
           "end_idx": last_low_idx, "end_price": last_low_price, "dir": "down"}


def zone_bounds(a: float, b: float):
    """(lo, hi) для прямоугольника R:R-зоны между двумя уровнями -- порядок сам по себе
    кодирует направление (для шорта SL > entry -> красная зона выше входа; для лонга
    SL < entry -> ниже), не нужен отдельный if is_long/is_short. Вынесено в отдельную
    функцию ради тест-инварианта направления (см. смоук)."""
    return (a, b) if b > a else (b, a)


def _fib_levels(swing):
    start, end = swing["start_price"], swing["end_price"]
    diff = end - start
    return [(r, end - diff * r) for r in FIB_RATIOS]


def _draw_fib_grid(ax, swing, n, alpha, style, label_suffix=""):
    levels = _fib_levels(swing)
    label_x = max(1, n * 0.03)
    for ratio, level in levels:
        ax.axhline(level, color=GOLD, linewidth=1.1, linestyle=style, alpha=alpha, zorder=4)
        ax.text(label_x, level, f"{ratio} ({_fmt_price(level)}){label_suffix}",
               color=GOLD, fontsize=10, va="bottom", ha="left", alpha=min(1.0, alpha + 0.3),
               fontweight="bold", zorder=6)
    return levels


def build_trade_chart(symbol: str, candles: list, direction: str,
                      entry_levels: list, sl: float,
                      tp1: float, tp2: float = None, tp3: float = None,
                      rr: float = None, key_high: float = None, key_low: float = None,
                      tf_label: str = "2h") -> io.BytesIO:
    """Рендерит PNG график сделки. candles: список dict (open/high/low/close/vol/
    timestamp), хронологический порядок, любой ТФ (вызывающая сторона уже выбрала
    нужный — 2h для свинг-сигналов, см. докстринг модуля). direction: "long"/"short".
    entry_levels: [entry1, entry2, entry3] в порядке DCA (не обязательно по цене).
    Возвращает None, если данных недостаточно для осмысленного графика."""
    if not candles or len(candles) < 20 or not entry_levels:
        return None

    candles = candles[-DISPLAY_BARS:]
    n = len(candles)
    price = candles[-1]["close"]
    is_long = direction == "long"

    if key_high is None or key_low is None:
        # Не переданы вызывающей стороной (напр. send_coin() на старом real_full_analysis()
        # без блока bias) -- считаем сами из тех же свечей, что и фибо-свинг, чтобы график
        # был содержательным независимо от источника сигнала.
        _highs, _lows = ta_extra.swing_points(candles)
        if key_high is None and _highs:
            key_high = _highs[-1][1]
        if key_low is None and _lows:
            key_low = _lows[-1][1]

    primary_swing = _last_significant_swing(candles)
    local_swing = _last_significant_swing(candles[-LOCAL_SWING_WINDOW:])
    if local_swing:
        # индексы локального свинга посчитаны относительно урезанного окна -- сдвигаем
        # обратно в координаты полного отображаемого диапазона
        offset = n - min(LOCAL_SWING_WINDOW, n)
        local_swing["start_idx"] += offset
        local_swing["end_idx"] += offset

    is_cluster = bool(primary_swing and local_swing and
                      (local_swing["start_idx"], local_swing["end_idx"]) !=
                      (primary_swing["start_idx"], primary_swing["end_idx"]))

    extension = max(15, round(n * FUTURE_WIDTH_PCT))
    fig, ax = plt.subplots(figsize=(13, 7.5), facecolor=BG)
    ax.set_facecolor(BG)
    ax.tick_params(colors=WHITE, labelsize=10)
    ax.grid(color="#1E2A3F", linewidth=0.5, alpha=0.6)
    for spine in ax.spines.values():
        spine.set_color("#1E2A3F")

    # --- Свечи ---
    w = 0.62
    for i, c in enumerate(candles):
        col = GREEN if c["close"] >= c["open"] else RED
        ax.plot([i, i], [c["low"], c["high"]], color=col, linewidth=1, zorder=2)
        body_h = abs(c["close"] - c["open"]) or (c["high"] - c["low"]) * 0.01 or price * 0.0005
        ax.add_patch(patches.Rectangle((i - w / 2, min(c["open"], c["close"])), w, body_h,
                                       linewidth=0, facecolor=col, alpha=0.95, zorder=3))

    # --- Фибо-ретрейсмент от последнего значимого свинга ---
    if primary_swing:
        _draw_fib_grid(ax, primary_swing, n, alpha=0.55, style="--")
    if is_cluster:
        _draw_fib_grid(ax, local_swing, n, alpha=0.3, style=":", label_suffix=" (локал.)")

    # --- Лимитки входа (DCA) -- подписи справа, за последней свечой ---
    right_x = n - 1 + extension * 1.05
    dca_labels = ["1 лимитка", "2 лимитка", "3 лимитка"]
    for idx, level in enumerate(entry_levels[:3]):
        if level is None:
            continue
        ax.axhline(level, color=ENTRYC, linewidth=1.3, alpha=0.85, zorder=5)
        ax.text(right_x, level, f"{dca_labels[idx]} ({_fmt_price(level)})",
               color=ENTRYC, fontsize=10, va="center", ha="left", fontweight="bold", zorder=7)

    entry_ref = entry_levels[0]  # entry1 -- основной ориентир для R:R-зон, как в fa_engine

    # --- R:R-зоны: от входа вправо (будущее), красная к SL, зелёная к TP ---
    zone_x0, zone_x1 = n - 1, n - 1 + extension
    zone_w = zone_x1 - zone_x0
    if sl is not None:
        lo, hi = zone_bounds(entry_ref, sl)
        ax.add_patch(patches.Rectangle((zone_x0, lo), zone_w, hi - lo,
                                       facecolor=RED, alpha=0.22, linewidth=0, zorder=1))
    if tp1 is not None:
        lo, hi = zone_bounds(entry_ref, tp1)
        ax.add_patch(patches.Rectangle((zone_x0, lo), zone_w, hi - lo,
                                       facecolor=GREEN, alpha=0.22, linewidth=0, zorder=1))
    if tp1 is not None and tp2 is not None:
        lo, hi = zone_bounds(tp1, tp2)
        ax.add_patch(patches.Rectangle((zone_x0, lo), zone_w, hi - lo,
                                       facecolor=GREEN, alpha=0.10, linewidth=0, zorder=1))
    if tp3 is not None:
        ax.axhline(tp3, color=GREEN, linewidth=1, linestyle=":", alpha=0.6, zorder=4)
        ax.text(right_x, tp3, f"TP3 ({_fmt_price(tp3)})", color=GREEN, fontsize=9.5,
               va="center", ha="left", alpha=0.8, zorder=7)
    if sl is not None:
        ax.text(right_x, sl, f"SL ({_fmt_price(sl)})", color=RED, fontsize=10,
               va="center", ha="left", fontweight="bold", zorder=7)
    if tp1 is not None:
        ax.text(right_x, tp1, f"TP1 ({_fmt_price(tp1)})", color=GREEN, fontsize=10,
               va="center", ha="left", fontweight="bold", zorder=7)
    if tp2 is not None:
        ax.text(right_x, tp2, f"TP2 ({_fmt_price(tp2)})", color=GREEN, fontsize=9.5,
               va="center", ha="left", alpha=0.85, zorder=7)

    # --- Ключевые swing-уровни (хай/лоу из блока bias) -- белые линии, ценник справа ---
    for level, label in ((key_high, "хай"), (key_low, "лоу")):
        if level is None:
            continue
        ax.axhline(level, color=WHITE, linewidth=1, linestyle="-", alpha=0.5, zorder=4)
        ax.text(n - 1, level, f" {label} {_fmt_price(level)}", color=WHITE, fontsize=10,
               va="bottom", ha="left", alpha=0.85, zorder=7)

    # --- Текущая цена ---
    ax.axhline(price, color=GRAY, linestyle="--", linewidth=1, alpha=0.7, zorder=4)

    ax.set_xlim(-1, n - 1 + extension * 1.3)

    # --- Компактная шапка вместо OHLC-панели ---
    side_ru = "LONG" if is_long else "SHORT"
    side_col = GREEN if is_long else RED
    rr_str = f"R:R 1:{rr:.2f}" if rr is not None else "R:R —"
    header = f"{symbol}USDT · {tf_label} · {side_ru} · {rr_str}"
    ax.set_title(header, color=side_col, fontsize=13, loc="left", fontweight="bold", pad=10)
    ax.text(0.995, 1.01, "BEST TRADE", color=GRAY, fontsize=10, alpha=0.6,
           ha="right", va="bottom", transform=ax.transAxes)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", facecolor=BG, dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf
