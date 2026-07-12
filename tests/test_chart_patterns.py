"""
pytest для chart_patterns.py -- Пакет 8 М3 (владелец: "Классические чарт-паттерны
(находка Булковского): детектор head&shoulders, флаги, треугольники -- НОВЫЙ модуль,
вывод в shadow-скоринг + отдельная строка в карточке ТА (информационно). Бой не
трогать"). Источник критериев -- Bulkowski, "Encyclopedia of Chart Patterns", 2-е
изд., Trading/Булковский_энциклопедия_паттернов.pdf (Табл. 21.1 флаги, Табл. 26.1
голова-плечи, Табл. 49.1 симметричные треугольники -- см. докстринг модуля).

Проверяет чистые геометрические функции и то, что patch 08 в shadow_engine
информационный (не участвует в affected/discrepancy) и не трогает боевой скоринг.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chart_patterns as cp
import shadow_engine as se


def _c(o, h, l, c, v=0):
    return {"open": o, "high": h, "low": l, "close": c, "vol": v}


def _flat(n, price=100.0, spread=0.5):
    return [_c(price, price + spread, price - spread, price) for _ in range(n)]


# ---------- detect_flag ----------

def _bull_flag_candles():
    pole = []
    for i in range(10):
        o = 100.0 + i * 1.5
        cl = o + 1.5
        pole.append(_c(o, cl + 0.3, o - 0.3, cl, v=1000 - i * 50))
    flag = []
    base = pole[-1]["close"]
    for i in range(8):
        drift = -0.3 * i / 8.0
        o = base + drift
        cl = o - 0.05
        flag.append(_c(o, o + 0.6, o - 0.6, cl, v=400 - i * 20))
    return pole + flag


def _bear_flag_candles():
    pole = []
    for i in range(10):
        o = 130.0 - i * 1.5
        cl = o - 1.5
        pole.append(_c(o, o + 0.3, cl - 0.3, cl, v=1000 - i * 50))
    flag = []
    base = pole[-1]["close"]
    for i in range(8):
        drift = 0.3 * i / 8.0
        o = base + drift
        cl = o + 0.05
        flag.append(_c(o, o + 0.6, o - 0.6, cl, v=400 - i * 20))
    return pole + flag


def test_detect_flag_bull():
    result = cp.detect_flag(_bull_flag_candles())
    assert result["bull"] is True
    assert result["bear"] is False
    assert result["pole_height"] is not None
    assert result["target"] > _bull_flag_candles()[-1]["close"]


def test_detect_flag_bear():
    result = cp.detect_flag(_bear_flag_candles())
    assert result["bear"] is True
    assert result["bull"] is False
    assert result["target"] < _bear_flag_candles()[-1]["close"]


def test_detect_flag_none_on_flat_data():
    result = cp.detect_flag(_flat(30))
    assert result["bull"] is False
    assert result["bear"] is False
    assert result["target"] is None


def test_detect_flag_none_on_too_short():
    result = cp.detect_flag(_flat(3))
    assert result["bull"] is False
    assert result["bear"] is False


# ---------- detect_head_and_shoulders ----------

def _hs_top_candles():
    """left shoulder ~110, head ~120, right shoulder ~110, neckline troughs ~100."""
    seq = []
    seq += _flat(3, price=95)
    seq += [_c(100, 111, 99, 105), _c(105, 110, 104, 106), _c(106, 107, 100, 101)]  # LS peak idx4
    seq += _flat(2, price=100)
    seq += [_c(101, 115, 100, 110), _c(110, 121, 109, 115), _c(115, 116, 99, 100)]  # head peak idx10
    seq += _flat(2, price=100)
    seq += [_c(100, 111, 99, 105), _c(105, 110, 104, 106), _c(106, 107, 95, 96)]  # RS peak idx16
    seq += _flat(3, price=95)
    return seq


def _hs_bottom_candles():
    seq = []
    seq += _flat(3, price=105)
    seq += [_c(100, 101, 89, 95), _c(95, 96, 90, 94), _c(94, 100, 93, 99)]  # LS trough idx4
    seq += _flat(2, price=100)
    seq += [_c(99, 100, 85, 90), _c(90, 91, 79, 85), _c(85, 100, 84, 90)]  # head trough idx10
    seq += _flat(2, price=100)
    seq += [_c(100, 101, 89, 95), _c(95, 96, 90, 94), _c(94, 105, 93, 104)]  # RS trough idx16
    seq += _flat(3, price=105)
    return seq


def test_detect_head_and_shoulders_top():
    result = cp.detect_head_and_shoulders(_hs_top_candles())
    assert result["top"] is True
    assert result["bottom"] is False
    assert result["neckline"] is not None
    assert result["target"] < result["neckline"]


def test_detect_head_and_shoulders_bottom():
    result = cp.detect_head_and_shoulders(_hs_bottom_candles())
    assert result["bottom"] is True
    assert result["top"] is False
    assert result["target"] > result["neckline"]


def test_detect_head_and_shoulders_none_on_flat_data():
    result = cp.detect_head_and_shoulders(_flat(30))
    assert result["top"] is False
    assert result["bottom"] is False


# ---------- detect_triangle ----------

def _symmetric_triangle_candles():
    """Сходящиеся хаи (снижаются) и лои (растут) -- 40 баров."""
    seq = []
    n = 40
    for i in range(n):
        high = 120.0 - i * 0.5
        low = 80.0 + i * 0.5
        mid = (high + low) / 2.0
        if i % 4 == 0:
            seq.append(_c(mid, high, mid - 1, mid))
        elif i % 4 == 2:
            seq.append(_c(mid, mid + 1, low, mid))
        else:
            seq.append(_c(mid, mid + 1, mid - 1, mid))
    return seq


def _ascending_triangle_candles():
    """Плоский верх (~120) и растущие лои."""
    seq = []
    n = 40
    for i in range(n):
        high = 120.0
        low = 80.0 + i * 0.7
        mid = (high + low) / 2.0
        if i % 4 == 0:
            seq.append(_c(mid, high, mid - 1, mid))
        elif i % 4 == 2:
            seq.append(_c(mid, mid + 1, low, mid))
        else:
            seq.append(_c(mid, mid + 1, mid - 1, mid))
    return seq


def _descending_triangle_candles():
    """Плоский низ (~80) и снижающиеся хаи."""
    seq = []
    n = 40
    for i in range(n):
        high = 120.0 - i * 0.7
        low = 80.0
        mid = (high + low) / 2.0
        if i % 4 == 0:
            seq.append(_c(mid, high, mid - 1, mid))
        elif i % 4 == 2:
            seq.append(_c(mid, mid + 1, low, mid))
        else:
            seq.append(_c(mid, mid + 1, mid - 1, mid))
    return seq


def test_detect_triangle_symmetric():
    result = cp.detect_triangle(_symmetric_triangle_candles())
    assert result["type"] == "symmetric"
    assert result["upper_slope"] < 0
    assert result["lower_slope"] > 0
    assert result["height"] > 0


def test_detect_triangle_ascending():
    result = cp.detect_triangle(_ascending_triangle_candles())
    assert result["type"] == "ascending"


def test_detect_triangle_descending():
    result = cp.detect_triangle(_descending_triangle_candles())
    assert result["type"] == "descending"


def test_detect_triangle_none_on_flat_data():
    result = cp.detect_triangle(_flat(30))
    assert result["type"] is None


def test_detect_triangle_none_on_too_short():
    result = cp.detect_triangle(_flat(5))
    assert result["type"] is None


# ---------- shadow_engine integration (patch 08, информационный) ----------

def test_shadow_engine_patch08_present_and_not_affecting_scoring():
    candles_4h = _symmetric_triangle_candles()
    result = {
        "price": candles_4h[-1]["close"],
        "candles_4h": candles_4h,
        "candles_1h": [],
        "candles_1d": [],
        "rocket": 5,
        "ema_ctx": {},
        "sr_zones": {},
        "sweep_1h": None,
        "sweep_4h": None,
    }

    class _FakeBot:
        pass

    shadow = se.compute_shadow("TESTUSDT", result, _FakeBot())
    assert "chart_patterns" in shadow
    assert "08-chart-patterns" not in shadow.get("patches_affected", [])
