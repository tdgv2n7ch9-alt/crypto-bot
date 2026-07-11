"""
pytest для bot.trend_arrow() -- «Пакетный ритм» пакет 2, М1. Та же историческая
порча пустых ASCII-строк, что уже дважды находили и чинили в этой сессии
(get_killzone_status(), top_trades-хендлер).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot


def test_strong_up():
    assert bot.trend_arrow(5.0) == "🚀"


def test_strong_up_boundary():
    assert bot.trend_arrow(3.0) == "🚀"


def test_mild_up():
    assert bot.trend_arrow(1.0) == "📈"


def test_mild_up_boundary():
    assert bot.trend_arrow(0.0) == "📈"


def test_mild_down():
    assert bot.trend_arrow(-1.0) == "📉"


def test_mild_down_boundary():
    assert bot.trend_arrow(-3.0) == "📉"


def test_strong_down():
    assert bot.trend_arrow(-5.0) == "💥"


def test_never_returns_empty_string():
    for ch in (10.0, 3.0, 2.9, 0.0, -0.1, -3.0, -3.1, -100.0):
        assert bot.trend_arrow(ch) != ""
