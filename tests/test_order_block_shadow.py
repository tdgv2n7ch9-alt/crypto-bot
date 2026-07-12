"""
pytest для ta_extra.detect_order_block() -- Пакет 7 М1 (владелец, "ДА" на вариант B
из Пакета 5 М2/6 М1: shadow-only сравнение геометрии Order Block, живой
pro_analysis() НЕ трогается). Проверяет, что "live"-геометрия (тело+фитиль,
точное зеркало инлайн-кода bot.py) и "methodology"-геометрия (чистое тело,
METHODOLOGY_CORE.md §18.1) на ОДНИХ И ТЕХ ЖЕ свечах могут давать разные зоны и,
как следствие, разный ответ "цена сейчас внутри зоны" -- плюс что новые поля
попадают в shadow_engine.compute_shadow() как patch 07.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ta_extra
import shadow_engine as se


def _c(o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c}


def _filler(n=5):
    return [_c(100, 101, 99, 100) for _ in range(n)]


def _bull_ob_candles():
    """Сигнальная свеча (медвежья, тело/range>0.5) на индексе 5 + подтверждение
    пробоя тремя следующими свечами закрытием выше high. live zone = (open=110,
    high=112); methodology zone = (close=100, open=110)."""
    signal = _c(110, 112, 98, 100)  # close<open, body=10, range=14, ratio~0.71>0.5
    confirm = [_c(112, 116, 111, 113), _c(113, 117, 112, 114), _c(114, 118, 113, 115)]
    return _filler() + [signal] + confirm


def _bear_ob_candles():
    """Зеркально: сигнальная свеча бычья, live zone=(low=88,open=90),
    methodology zone=(open=90, close=100)."""
    signal = _c(90, 102, 88, 100)  # close>open, body=10, range=14, ratio~0.71>0.5
    confirm = [_c(88, 89, 84, 85), _c(85, 86, 81, 82), _c(82, 83, 78, 79)]
    return _filler() + [signal] + confirm


# ── detect_order_block() -- чистая функция ──

def test_detect_order_block_empty_candles():
    result = ta_extra.detect_order_block([], 100.0)
    assert result["live"]["bull"] is False
    assert result["methodology"]["bull"] is False


def test_detect_order_block_price_none():
    result = ta_extra.detect_order_block(_bull_ob_candles(), None)
    assert result["live"]["bull"] is False
    assert result["methodology"]["bull"] is False


def test_detect_order_block_too_few_candles():
    result = ta_extra.detect_order_block(_filler(3), 100.0)
    assert result["live"]["bull"] is False
    assert result["methodology"]["bull"] is False


def test_detect_order_block_bull_live_only_price_in_wick_not_body():
    # 112 внутри live-зоны (110..113.12), НО вне methodology-зоны (100..111.1)
    result = ta_extra.detect_order_block(_bull_ob_candles(), 112.0)
    assert result["live"]["bull"] is True
    assert result["live"]["bull_zone"] == (110, 112)
    assert result["methodology"]["bull"] is False


def test_detect_order_block_bull_both_agree_in_overlap():
    # 110.5 внутри ОБЕИХ зон (пересечение live [110,113.12] и meth [100,111.1])
    result = ta_extra.detect_order_block(_bull_ob_candles(), 110.5)
    assert result["live"]["bull"] is True
    assert result["methodology"]["bull"] is True
    assert result["methodology"]["bull_zone"] == (100, 110)


def test_detect_order_block_bull_price_far_away_neither_triggers():
    result = ta_extra.detect_order_block(_bull_ob_candles(), 500.0)
    assert result["live"]["bull"] is False
    assert result["methodology"]["bull"] is False


def test_detect_order_block_bear_live_only_price_in_wick_not_body():
    # 88.5 внутри live-зоны бир (88*0.99=87.12..90), НО вне meth-зоны (90*0.99=89.1..100)
    result = ta_extra.detect_order_block(_bear_ob_candles(), 88.5)
    assert result["live"]["bear"] is True
    assert result["methodology"]["bear"] is False


def test_detect_order_block_bear_methodology_only_price_in_body_not_wick():
    # 95 внутри meth-зоны (89.1..100), НО вне live-зоны (87.12..90)
    result = ta_extra.detect_order_block(_bear_ob_candles(), 95.0)
    assert result["live"]["bear"] is False
    assert result["methodology"]["bear"] is True


# ── compute_shadow() -- patch 07 подключение ──

class _FakeBotModule:
    def get_killzone_status(self):
        return {"active": {"quality": "A", "name": "London"}}

    def get_killzone_status_shadow(self):
        return {"active": {"quality": "A", "name": "London"}}


def _base_result(candles_4h, price):
    return {
        "block11_trade_plan": {
            "direction": "long", "entry1": 100, "entry3": 98, "sl": 96,
            "tp1": 104, "tp2": 108, "tp3": 112, "rr_tp1": 2.0,
        },
        "candles_4h": candles_4h,
        "price": price,
    }


def test_compute_shadow_includes_order_block_fields():
    record = se.compute_shadow("BTCUSDT", _base_result(_bull_ob_candles(), 110.5), _FakeBotModule())
    assert "order_block_live" in record
    assert "order_block_methodology" in record
    assert "bull" in record["order_block_live"]


def test_compute_shadow_order_block_divergence_flagged_as_patch_07():
    record = se.compute_shadow("BTCUSDT", _base_result(_bull_ob_candles(), 112.0), _FakeBotModule())
    assert "07-order-block-body" in record["patches_affected"]
    assert any("order_block" in d for d in record["discrepancy"])


def test_compute_shadow_order_block_no_divergence_when_both_agree():
    record = se.compute_shadow("BTCUSDT", _base_result(_bull_ob_candles(), 110.5), _FakeBotModule())
    assert "07-order-block-body" not in record["patches_affected"]


def test_compute_shadow_order_block_missing_price_no_crash():
    result = _base_result(_bull_ob_candles(), None)
    record = se.compute_shadow("BTCUSDT", result, _FakeBotModule())
    assert record["order_block_live"]["bull"] is False
    assert "07-order-block-body" not in record["patches_affected"]


def test_compute_shadow_order_block_failure_does_not_break_record(monkeypatch):
    monkeypatch.setattr(ta_extra, "detect_order_block",
                         lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    record = se.compute_shadow("BTCUSDT", _base_result(_bull_ob_candles(), 112.0), _FakeBotModule())
    assert record["order_block_live"]["bull"] is False
    assert any("order_block" in d for d in record["discrepancy"])


def test_compute_shadow_order_block_does_not_affect_live_pro_analysis():
    """Патч 07 -- чисто shadow-запись, ta_extra.detect_order_block() не вызывается
    и не импортируется живым pro_analysis() в bot.py (проверка через grep, не runtime --
    здесь только подтверждаем, что compute_shadow() не мутирует входной result)."""
    result = _base_result(_bull_ob_candles(), 112.0)
    snapshot = dict(result["block11_trade_plan"])
    se.compute_shadow("BTCUSDT", result, _FakeBotModule())
    assert result["block11_trade_plan"] == snapshot
