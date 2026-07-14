"""
pytest для Пакет 20, Этап 3 (владелец, "быстрая победа"): разделитель блоков
в bot._build_signal_post() (AUTO/точки карточка) унифицирован на
канонический "━" (card_v2.SEP) -- раньше был другой символ "➖", находка
docs/SIGNAL_VISUAL_STANDARD.md (П19 П3, находка #1). Тест на рендер --
регрессия, если кто-то вернёт старый символ обратно.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot
import card_v2


def _minimal_a(**overrides):
    a = {
        "price": 100.0, "price_fresh": "", "rocket": 78, "rsi_4h": 45, "trend_4h": "bullish",
        "ch24h": 2.5, "ch7d": 5.0, "ch30d": 10.0, "macd_bullish": True, "macd_bearish": False,
        "above_ema200": True, "above_ema50": True, "above_ema20": True, "smc_factors": [],
        "st_label": "", "entry1": 100.0, "tp1": 105.0, "tp2": 110.0, "tp3": 115.0, "sl": 95.0,
        "rr": 1.5, "rr_tp1": 1.0, "rr_tp2": 2.0, "rr_tp3": 3.0, "levels_source": None,
    }
    a.update(overrides)
    return a


def test_uses_canonical_separator_long():
    text = bot._build_signal_post("BTC", _minimal_a(), {}, mode="long")
    assert card_v2.SEP in text


def test_uses_canonical_separator_short():
    text = bot._build_signal_post("ETH", _minimal_a(), {}, mode="short")
    assert card_v2.SEP in text


def test_does_not_use_old_dash_separator():
    text = bot._build_signal_post("BTC", _minimal_a(), {}, mode="long")
    assert "➖" not in text


def test_spot_mode_also_uses_canonical_separator():
    text = bot._build_signal_post(
        "SOL", _minimal_a(tp1=1.1, tp2=1.2, tp3=1.3, sl=0.9), {}, mode="spot")
    assert card_v2.SEP in text
    assert "➖" not in text
