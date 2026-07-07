"""
BEST TRADE — Chart v4: развитие Chart v3 (chart_v3.py, который специально НЕ трогаем —
он остаётся live-фоллбеком, если v4 по любой причине не смог отрендерить). v3 несёт всё
как есть (фибо, лимитки DCA, R:R-зоны, свинг-уровни, шапка); v4 добавляет поверх:

  (а) Мульти-ТФ зоны (POI/K-LVL) закрашенными прямоугольниками — те же зоны, что считает
      fa_engine.py через ta_extra.find_sr_zones()/classify_klvl_zones() для карточки
      /full, теперь видны и на графике. Зона = ценовой диапазон (lo/hi), не линия; если
      вызывающая сторона отдаёт только уровень (напр. FVG-mid без сохранённых границ) —
      строим синтетическую зону level±0.3%. Спрос (below price, "below" в find_sr_zones)
      — синий/фиолетовый, предложение (above) — жёлтый/оливковый; K-LVL зоны ярче
      обычных (выше alpha, толще граница, ⚡ в подписи). Максимум 3 зоны с каждой
      стороны (ближайшие к цене) — find_sr_zones уже отдаёт списки, отсортированные по
      расстоянию, так что просто берём первые N.
  (б) Стрелка ожидаемого сценария — тонкая серая, от последней свечи к ближайшей
      целевой зоне по направлению сделки (long — вверх к TP1/зоне предложения, short —
      вниз к TP1/зоне спроса). Направление и TP1 уже обязательные параметры функции
      (та же сигнатура, что и build_trade_chart в v3), отдельного аргумента не нужно.

Зоны — необязательный параметр (zones=None): вызывающая сторона, у которой их нет под
рукой (напр. promo-график Памп-радара, там только entry/SL/TP без структуры), просто не
передаёт zones — график рендерится как v3 + стрелка, без прямоугольников. K-LVL
классификация (кто из zones — K-LVL) тоже опциональна: если zones уже классифицированы
(есть ключ "klvl", как в fa_engine.result["zones"] после classify_klvl_zones) — используем
как есть; если нет (напр. bot.py real_full_analysis()'s "a"["zones"], сырые from
find_sr_zones) и передан candles_4h — классифицируем здесь же (чистая функция, без
доп. HTTP-вызовов).

Сигнатура build_trade_chart_v4() — надмножество build_trade_chart() из v3 (те же
обязательные параметры + zones/candles_4h опционально), поэтому вызывающая сторона может
использовать общий try/except: chart_v4.build_trade_chart_v4(...) с фоллбеком на
chart_v3.build_trade_chart(...) при исключении.
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
DEMAND_COLOR = "#7B61FF"   # спрос/поддержка — синий/фиолетовый
SUPPLY_COLOR = "#B5A642"   # предложение/сопротивление — жёлтый/оливковый

FIB_RATIOS = (0.5, 0.618, 0.786)
DISPLAY_BARS = 120
FUTURE_WIDTH_PCT = 0.20   # R:R-зоны и лимитки тянутся вправо на ~20% ширины графика
LOCAL_SWING_WINDOW = 30   # окно для вторичного (локального) фибо-грида

ZONE_ALPHA = 0.18            # обычная POI-зона
ZONE_ALPHA_KLVL = 0.32        # K-LVL — ярче
ZONE_SYNTH_WIDTH_PCT = 0.3    # ширина синтетической зоны, если отдан только уровень (level±%)
ZONE_MAX_PER_SIDE = 3         # максимум зон с каждой стороны — иначе каша на графике


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


def _tf_label_for_zone(sources) -> str:
    """Подпись таймфрейма зоны по её sources (find_sr_zones уже кладёт туда список
    ТФ-источников кластера, напр. ["1h","4h"] или ["1d"] или ["ema"]). Порядок 1D > 4H >
    1H — старший ТФ первым, если зона собрана из нескольких."""
    order = (("1d", "1D"), ("4h", "4H"), ("1h", "1H"))
    src = sources or []
    labels = [lbl for key, lbl in order if key in src]
    if labels:
        return "+".join(labels)
    if "ema" in src:
        return "EMA"
    return ""


def _zone_lo_hi(z: dict):
    """(lo, hi) зоны -- если границ нет (только "price"/"mid", напр. FVG-подобный вход
    без сохранённых lo/hi), строим синтетическую zone level±ZONE_SYNTH_WIDTH_PCT%."""
    if "lo" in z and "hi" in z and z["lo"] is not None and z["hi"] is not None:
        lo, hi = z["lo"], z["hi"]
        return (lo, hi) if hi >= lo else (hi, lo)
    level = z.get("mid", z.get("price"))
    if level is None:
        return None
    width = abs(level) * ZONE_SYNTH_WIDTH_PCT / 100
    return level - width, level + width


def prepare_zones_for_chart(zones: dict, candles_4h: list = None,
                            max_per_side: int = ZONE_MAX_PER_SIDE) -> list:
    """zones: {"above":[...], "below":[...]} -- сырой (или уже K-LVL-классифицированный)
    вывод ta_extra.find_sr_zones(), каждый список отсортирован от ближайшей к цене зоны к
    дальней. Если зоны ещё не классифицированы (нет ключа "klvl") и переданы candles_4h --
    классифицируем здесь же (ta_extra.classify_klvl_zones, чистая функция без HTTP).
    Возвращает плоский список максимум 2*max_per_side зон (ближайшие к цене с каждой
    стороны), каждая дополнена "side" ("above"/"below")."""
    if not zones:
        return []
    out = []
    for side in ("above", "below"):
        side_zones = zones.get(side) or []
        if side_zones and "klvl" not in side_zones[0] and candles_4h:
            side_zones = ta_extra.classify_klvl_zones(side_zones, candles_4h)
        for z in side_zones[:max_per_side]:
            out.append({**z, "side": side})
    return out


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


def build_trade_chart_v4(symbol: str, candles: list, direction: str,
                         entry_levels: list, sl: float,
                         tp1: float, tp2: float = None, tp3: float = None,
                         rr: float = None, key_high: float = None, key_low: float = None,
                         tf_label: str = "2h", zones: dict = None,
                         candles_4h: list = None) -> io.BytesIO:
    """Рендерит PNG график сделки (Chart v4 = v3 + мульти-ТФ зоны + стрелка сценария).
    Параметры entry_levels/sl/tp1/... -- как в chart_v3.build_trade_chart (та же семантика,
    те же значения по умолчанию). zones: {"above":[...],"below":[...]} из
    ta_extra.find_sr_zones() (сырой или уже K-LVL-классифицированный) -- опционально, при
    отсутствии зоны просто не рисуются. candles_4h: только для K-LVL-классификации, если
    zones ещё не классифицированы -- опционально.
    Возвращает None, если данных недостаточно для осмысленного графика."""
    if not candles or len(candles) < 20 or not entry_levels:
        return None

    candles = candles[-DISPLAY_BARS:]
    n = len(candles)
    price = candles[-1]["close"]
    is_long = direction == "long"

    if key_high is None or key_low is None:
        _highs, _lows = ta_extra.swing_points(candles)
        if key_high is None and _highs:
            key_high = _highs[-1][1]
        if key_low is None and _lows:
            key_low = _lows[-1][1]

    primary_swing = _last_significant_swing(candles)
    local_swing = _last_significant_swing(candles[-LOCAL_SWING_WINDOW:])
    if local_swing:
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

    # --- Мульти-ТФ зоны (POI/K-LVL) -- позади свечей (zorder=0) ---
    prepared_zones = prepare_zones_for_chart(zones, candles_4h)
    zone_label_x = n - 1 + max(1, extension * 0.06)
    # Порог "слишком близко": зоны часто близки по цене к DCA/TP-уровням (одна и та же
    # структура find_sr_zones лежит и в основе плана сделки) -- без стаггера их подписи
    # рендерятся друг на друге и превращаются в нечитаемую кашу (см. живую проверку v119).
    all_prices = [c["high"] for c in candles] + [c["low"] for c in candles]
    price_span = (max(all_prices) - min(all_prices)) if all_prices else price * 0.1
    min_label_gap = max(price_span, price * 0.01) * 0.025
    placed_label_prices = []
    for z in prepared_zones:
        bounds = _zone_lo_hi(z)
        if bounds is None:
            continue
        lo, hi = bounds
        is_klvl = bool(z.get("klvl"))
        color = SUPPLY_COLOR if z["side"] == "above" else DEMAND_COLOR
        alpha = ZONE_ALPHA_KLVL if is_klvl else ZONE_ALPHA
        ax.add_patch(patches.Rectangle((-1, lo), n - (-1) - 1, hi - lo,
                                       facecolor=color, edgecolor=color,
                                       linewidth=1.4 if is_klvl else 0.6,
                                       alpha=alpha, zorder=0))
        tf_lbl = _tf_label_for_zone(z.get("sources"))
        mark = "⚡" if is_klvl else ""
        label = f"{mark}{tf_lbl}".strip()
        if label:
            mid = (lo + hi) / 2
            collisions = sum(1 for py in placed_label_prices if abs(mid - py) < min_label_gap)
            placed_label_prices.append(mid)
            label_x = zone_label_x + collisions * extension * 0.10
            ax.text(label_x, mid, label, color=color, fontsize=8.5,
                   va="center", ha="left", alpha=0.95,
                   fontweight="bold" if is_klvl else "normal", zorder=6)

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

    # --- Стрелка ожидаемого сценария: от последней свечи к ближайшей целевой зоне ---
    # long -- вверх к TP1 (supply/цель по вердикту), short -- вниз к TP1 (demand/цель).
    # TP1 обязателен для построения графика (см. проверку entry_levels/sl выше в
    # chart_v3-совместимой сигнатуре), поэтому это всегда самая близкая содержательная
    # цель по направлению сделки -- отдельный аргумент сценария не нужен.
    arrow_target = tp1 if tp1 is not None else (key_high if is_long else key_low)
    if arrow_target is not None:
        arrow_x = n - 1 + extension * 0.55
        ax.annotate("", xy=(arrow_x, arrow_target), xytext=(n - 1, price),
                   arrowprops=dict(arrowstyle="-|>", color=GRAY, alpha=0.55,
                                    linewidth=1.3, shrinkA=0, shrinkB=0), zorder=6)

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
