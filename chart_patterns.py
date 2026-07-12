"""Патч 08 (Пакет 8 М3, shadow-only, информационно).

Классические чарт-паттерны по методике Bulkowski, "Encyclopedia of Chart
Patterns", 2-е изд. (пер. SmartBook / John Wiley & Sons, 2009,
ISBN 978-5-9791-0157-6), файл Trading/Булковский_энциклопедия_паттернов.pdf.

Источники (прочитаны напрямую из PDF, номера страниц PDF / книги):
  - Флаги — Глава 21, Таблица 21.1 (PDF с.236-245 = книга с.249-258).
  - Голова и плечи, вершина — Глава 26, Таблица 26.1 (PDF с.282-291 =
    книга с.295-304). "Голова и плечи, дно" — отдельная глава книги,
    НЕ прочитана; здесь используется геометрическое зеркало вершины
    (общепринятая симметрия модели), это эвристика, а не отдельно
    сверенный источник -- помечено в METHODOLOGY_CORE.md.
  - Симметричные треугольники — Глава 49, Таблица 49.1 (PDF с.503-511 =
    книга с.525-533). Восходящие/нисходящие треугольники (Главы 47/48)
    отдельно не прочитаны; классификация ascending/descending здесь --
    эвристика по наклону линий (плоская линия = не сходящийся тренд),
    не отдельно сверенный источник.

Все функции читают только candles (список dict с open/high/low/close/vol,
возрастающий индекс = более поздний бар) и ничего не пишут. Результат --
только информационная строка в карточке ТА + запись в shadow-скоринг
(patch 08). НЕ участвует в боевом гейте/скоринге (владелец, Пакет 8 М3:
"Бой не трогать").
"""
import numpy as np

import ta_extra


def _pct_diff(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return abs(a - b) / abs(b) * 100.0


def detect_flag(candles: list, pole_lookback: int = 10, max_flag_bars: int = 40,
                 pole_min_move_pct: float = 8.0) -> dict:
    """Bulkowski Табл. 21.1: (1) формация ограничена двумя примерно
    параллельными линиями тренда; (2) длительность обычно не более 3 недель
    (здесь -- max_flag_bars баров, по умолчанию 40 -- консервативный масштаб
    под 4h-свечи, книга даёт масштаб под дневные бары недельного рынка, точной
    конверсии в источнике нет, это эвристика); (3) крутое/быстрое
    предшествующее движение ("флагшток"); (4) обычно снижающийся объём во
    флаге. Правило измерения (книга с.258): цель = цена прорыва +/- высота
    флагштока.

    Возвращает {"bull": bool, "bear": bool, "pole_height": float|None,
    "target": float|None, "flag_bars": int|None}."""
    empty = {"bull": False, "bear": False, "pole_height": None,
             "target": None, "flag_bars": None}
    n = len(candles)
    if n < pole_lookback + 5:
        return empty

    for pole_end in range(pole_lookback, n - 3):
        pole_start = pole_end - pole_lookback
        pole = candles[pole_start:pole_end]
        pole_open = pole[0]["open"]
        pole_close = pole[-1]["close"]
        if pole_open == 0:
            continue
        move_pct = (pole_close - pole_open) / pole_open * 100.0
        if abs(move_pct) < pole_min_move_pct:
            continue
        is_bull_pole = move_pct > 0

        flag_end_limit = min(n, pole_end + max_flag_bars)
        flag = candles[pole_end:flag_end_limit]
        if len(flag) < 4:
            continue

        pole_range = max(c["high"] for c in pole) - min(c["low"] for c in pole)
        flag_range = max(c["high"] for c in flag) - min(c["low"] for c in flag)
        if pole_range <= 0 or flag_range <= 0:
            continue
        # флаг должен быть заметно уже флагштока -- узкий консолидационный канал
        if flag_range > pole_range * 0.6:
            continue

        flag_open = flag[0]["open"]
        flag_close = flag[-1]["close"]
        flag_drift_pct = (flag_close - flag_open) / flag_open * 100.0 if flag_open else 0.0
        # флаг наклонён слегка против флагштока или вбок, но не разворачивается полностью
        if is_bull_pole and flag_drift_pct < -abs(move_pct) * 0.5:
            continue
        if not is_bull_pole and flag_drift_pct > abs(move_pct) * 0.5:
            continue

        vols = [c.get("vol", 0) or 0 for c in flag]
        vol_declining = None
        if any(vols):
            first_half = vols[:len(vols) // 2] or vols
            second_half = vols[len(vols) // 2:] or vols
            vol_declining = (sum(second_half) / len(second_half)) <= (sum(first_half) / len(first_half))

        pole_height = pole_range
        breakout_price = flag[-1]["close"]
        target = breakout_price + pole_height if is_bull_pole else breakout_price - pole_height

        result = dict(empty)
        result["pole_height"] = pole_height
        result["target"] = target
        result["flag_bars"] = len(flag)
        result["vol_declining"] = vol_declining
        if is_bull_pole:
            result["bull"] = True
        else:
            result["bear"] = True
        return result

    return empty


def detect_head_and_shoulders(candles: list, shoulder_tolerance_pct: float = 15.0,
                               time_tolerance_pct: float = 50.0) -> dict:
    """Bulkowski Табл. 26.1 (вершина, прочитано напрямую): (1) три пика, центральный
    (голова) выше боковых (плечи); (2) плечи примерно симметричны по цене и по
    горизонтальному расстоянию от головы; (3) объём обычно выше на левом плече,
    ниже на правом (не проверяется здесь надёжно -- часто нет объёма в данных);
    (4) линия шеи соединяет два минимума между пиками, наклон любой; (5) пробой
    линии шеи вниз, возможен краткий отскок. Правило измерения (книга с.304):
    цель = цена прорыва линии шеи − высота формации (голова − линия шеи).

    "Дно" -- геометрическое зеркало (эвристика, не отдельно сверенный источник,
    см. докстринг модуля).

    Возвращает {"top": bool, "bottom": bool, "neckline": float|None,
    "target": float|None}."""
    empty = {"top": False, "bottom": False, "neckline": None, "target": None}
    highs, lows = ta_extra.swing_points(candles)
    if len(highs) >= 3:
        top = _check_hs_top(candles, highs, shoulder_tolerance_pct, time_tolerance_pct)
        if top["top"]:
            return top
    if len(lows) >= 3:
        bottom = _check_hs_bottom(candles, lows, shoulder_tolerance_pct, time_tolerance_pct)
        if bottom["bottom"]:
            return bottom
    return empty


def _check_hs_top(candles, highs, shoulder_tol, time_tol) -> dict:
    """Линия шеи -- НЕ обязательно фрактальная точка (на плоском/неявном участке
    между плечом и головой фрактал по строгому неравенству может не сформироваться
    -- см. _find_fractals), поэтому берётся буквальный минимум low() по сырым
    свечам сегмента между плечом и головой (то самое "второстепенное дно" по
    Bulkowski, просто без требования, чтобы оно было СТРОГИМ фракталом)."""
    empty = {"top": False, "bottom": False, "neckline": None, "target": None}
    for i in range(len(highs) - 3, -1, -1):
        ls_idx, ls_price = highs[i]
        head_idx, head_price = highs[i + 1]
        rs_idx, rs_price = highs[i + 2]
        if not (head_price > ls_price and head_price > rs_price):
            continue
        if _pct_diff(ls_price, rs_price) > shoulder_tol:
            continue
        left_span = head_idx - ls_idx
        right_span = rs_idx - head_idx
        if left_span <= 0 or right_span <= 0:
            continue
        if _pct_diff(left_span, right_span) > time_tol:
            continue
        trough1 = [c["low"] for c in candles[ls_idx + 1:head_idx]]
        trough2 = [c["low"] for c in candles[head_idx + 1:rs_idx]]
        if not trough1 or not trough2:
            continue
        neckline = (min(trough1) + min(trough2)) / 2.0
        target = neckline - (head_price - neckline)
        return {"top": True, "bottom": False, "neckline": neckline, "target": target}
    return empty


def _check_hs_bottom(candles, lows, shoulder_tol, time_tol) -> dict:
    """Зеркало _check_hs_top -- см. её докстринг про буквальный сегментный экстремум
    вместо строгого фрактала на линии шеи."""
    empty = {"top": False, "bottom": False, "neckline": None, "target": None}
    for i in range(len(lows) - 3, -1, -1):
        ls_idx, ls_price = lows[i]
        head_idx, head_price = lows[i + 1]
        rs_idx, rs_price = lows[i + 2]
        if not (head_price < ls_price and head_price < rs_price):
            continue
        if _pct_diff(ls_price, rs_price) > shoulder_tol:
            continue
        left_span = head_idx - ls_idx
        right_span = rs_idx - head_idx
        if left_span <= 0 or right_span <= 0:
            continue
        if _pct_diff(left_span, right_span) > time_tol:
            continue
        peak1 = [c["high"] for c in candles[ls_idx + 1:head_idx]]
        peak2 = [c["high"] for c in candles[head_idx + 1:rs_idx]]
        if not peak1 or not peak2:
            continue
        neckline = (max(peak1) + max(peak2)) / 2.0
        target = neckline + (neckline - head_price)
        return {"top": False, "bottom": True, "neckline": neckline, "target": target}
    return empty


def detect_triangle(candles: list, min_touches: int = 2, lookback_bars: int = 90,
                     flat_slope_tolerance_pct: float = 0.05) -> dict:
    """Bulkowski Табл. 49.1 (симметричные, прочитано напрямую): (1) форма из двух
    сходящихся линий тренда, соединяющихся у вершины; (2) минимум по 2 чётких
    второстепенных максимума/минимума, касающихся каждой линии (>=4 касания);
    (3) объём обычно снижается по ходу формации; (4) направление прорыва
    заранее неизвестно; (5) длительность обычно больше 3 недель (здесь --
    масштабировано под lookback_bars 4h-баров, эвристика, книга не даёт точной
    конверсии таймфрейма). Правило измерения (Табл. 49.8): высота формации
    (макс. хай − мин. лоу) прибавляется/вычитается от цены прорыва.

    Ascending/descending -- классификация по наклону линий (плоская линия
    считается "flat", см. flat_slope_tolerance_pct); отдельные главы книги
    по ascending/descending треугольникам не прочитаны напрямую, это
    эвристика (см. докстринг модуля).

    Возвращает {"type": "symmetric"|"ascending"|"descending"|None,
    "upper_slope": float|None, "lower_slope": float|None,
    "height": float|None, "apex_price": float|None}."""
    empty = {"type": None, "upper_slope": None, "lower_slope": None,
             "height": None, "apex_price": None}
    n = len(candles)
    if n < 10:
        return empty
    window = candles[max(0, n - lookback_bars):]
    highs, lows = ta_extra.swing_points(window)
    if len(highs) < min_touches or len(lows) < min_touches:
        return empty

    hx = np.array([idx for idx, _ in highs], dtype=float)
    hy = np.array([price for _, price in highs], dtype=float)
    lx = np.array([idx for idx, _ in lows], dtype=float)
    ly = np.array([price for _, price in lows], dtype=float)

    upper_slope, upper_b = np.polyfit(hx, hy, 1)
    lower_slope, lower_b = np.polyfit(lx, ly, 1)

    ref_price = window[-1]["close"] or 1.0
    upper_flat = abs(upper_slope) / ref_price * 100.0 < flat_slope_tolerance_pct
    lower_flat = abs(lower_slope) / ref_price * 100.0 < flat_slope_tolerance_pct

    converging = upper_slope < 0 and lower_slope > 0
    if not converging and not upper_flat and not lower_flat:
        return empty

    if upper_flat and lower_slope > 0:
        ttype = "ascending"
    elif lower_flat and upper_slope < 0:
        ttype = "descending"
    elif converging:
        ttype = "symmetric"
    else:
        return empty

    height = max(p for _, p in highs) - min(p for _, p in lows)
    if height <= 0:
        return empty

    apex_price = None
    denom = (lower_slope - upper_slope)
    if denom != 0:
        apex_x = (upper_b - lower_b) / denom
        apex_price = upper_slope * apex_x + upper_b

    return {"type": ttype, "upper_slope": upper_slope, "lower_slope": lower_slope,
            "height": height, "apex_price": apex_price}


def format_line(candles: list) -> str:
    """Одна информационная строка для карточки ТА (владелец, Пакет 8 М3:
    "отдельная строка в карточке ТА (информационно). Бой не трогать") -- считает
    все три детектора поверх уже полученных свечей и форматирует найденные паттерны.
    Чистое форматирование, не участвует ни в каком скоринге/гейте -- вызывающая
    сторона решает сама, показывать строку или нет (см. bot.cmd_patterns())."""
    parts = []
    try:
        tri = detect_triangle(candles)
        if tri["type"]:
            parts.append(f"треугольник ({tri['type']})")
    except Exception:
        pass
    try:
        flag_r = detect_flag(candles)
        if flag_r["bull"] or flag_r["bear"]:
            direction = "бычий" if flag_r["bull"] else "медвежий"
            target = flag_r.get("target")
            parts.append(f"флаг ({direction}, цель {target:.6g})" if target else f"флаг ({direction})")
    except Exception:
        pass
    try:
        hs = detect_head_and_shoulders(candles)
        if hs["top"] or hs["bottom"]:
            kind = "вершина" if hs["top"] else "дно"
            target = hs.get("target")
            parts.append(f"голова-плечи ({kind}, цель {target:.6g})" if target else f"голова-плечи ({kind})")
    except Exception:
        pass
    if not parts:
        return "📐 Классические паттерны (Булковский): не найдено"
    return "📐 Классические паттерны (Булковский): " + "; ".join(parts)
