"""
pytest для АПГРЕЙД 11.07 Этап 2 (честность витрины) -- чистые функции:
bot.whale_monitor_label() (2.3), bot.market_sentiment()/market_sentiment_emoji() (2.5).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot


# ── whale_monitor_label() ──

def test_whale_label_below_40_is_nablyudenie_no_star():
    label, stars = bot.whale_monitor_label("LONG", 39)
    assert label == "НАБЛЮДЕНИЕ"
    assert stars == ""


def test_whale_label_zero_score_is_nablyudenie():
    label, stars = bot.whale_monitor_label("SHORT", 0)
    assert label == "НАБЛЮДЕНИЕ"
    assert stars == ""


def test_whale_label_exactly_40_shows_direction_with_stars():
    label, stars = bot.whale_monitor_label("LONG", 40)
    assert label == "LONG"
    assert stars == "⭐⭐"  # round(40/20)=2


def test_whale_label_star_count_scales_with_score():
    _, stars_40 = bot.whale_monitor_label("LONG", 40)
    _, stars_100 = bot.whale_monitor_label("LONG", 100)
    assert len(stars_40) == 2   # round(40/20)=2
    assert len(stars_100) == 5  # round(100/20)=5, capped at 5


def test_whale_label_star_count_capped_at_5():
    _, stars = bot.whale_monitor_label("SHORT", 100)
    assert len(stars) <= 5


def test_whale_label_min_1_star_above_threshold():
    _, stars = bot.whale_monitor_label("LONG", 41)  # round(41/20)=2, still >=1
    assert len(stars) >= 1


def test_whale_label_preserves_direction_text_at_and_above_threshold():
    label, _ = bot.whale_monitor_label("SHORT", 65)
    assert label == "SHORT"


# ── market_sentiment() / market_sentiment_emoji() ──

def _coin(symbol, ch24h):
    return {"symbol": symbol, "quote": {"USDT": {"percent_change_24h": ch24h}}}


def test_market_sentiment_bull_at_60_pct():
    coins = [_coin(f"C{i}", 1.0) for i in range(60)] + [_coin(f"D{i}", -1.0) for i in range(40)]
    label, pct = bot.market_sentiment(coins, top_n=100)
    assert label == "БЫЧИЙ"
    assert pct == 60


def test_market_sentiment_bear_at_40_pct():
    coins = [_coin(f"C{i}", 1.0) for i in range(40)] + [_coin(f"D{i}", -1.0) for i in range(60)]
    label, pct = bot.market_sentiment(coins, top_n=100)
    assert label == "МЕДВЕЖИЙ"
    assert pct == 40


def test_market_sentiment_neutral_between_40_and_60():
    coins = [_coin(f"C{i}", 1.0) for i in range(50)] + [_coin(f"D{i}", -1.0) for i in range(50)]
    label, pct = bot.market_sentiment(coins, top_n=100)
    assert label == "НЕЙТРАЛЬНЫЙ"
    assert pct == 50


def test_market_sentiment_excludes_stablecoins():
    """Стейблкоины (0%-движение) не должны разбавлять пул -- раньше "Обзор" считал по
    coins[:200] БЕЗ исключения стейблов, что искусственно тянуло % вниз."""
    coins = [_coin("USDT", 0.0), _coin("USDC", 0.0)] + [_coin(f"C{i}", 1.0) for i in range(10)]
    label, pct = bot.market_sentiment(coins, top_n=100)
    assert pct == 100  # все 10 не-стейблов растут, стейблы исключены из пула
    assert label == "БЫЧИЙ"


def test_market_sentiment_respects_top_n():
    coins = [_coin(f"C{i}", 1.0) for i in range(5)] + [_coin(f"D{i}", -1.0) for i in range(95)]
    label, pct = bot.market_sentiment(coins, top_n=10)
    # первые 10 монет в пуле: 5 растущих + 5 падающих (из первых 95 падающих) -> 50%
    assert pct == 50


def test_market_sentiment_empty_pool_is_neutral():
    label, pct = bot.market_sentiment([], top_n=100)
    assert label == "НЕЙТРАЛЬНЫЙ"
    assert pct == 50


def test_market_sentiment_emoji_mapping():
    assert bot.market_sentiment_emoji("БЫЧИЙ") == "🟢"
    assert bot.market_sentiment_emoji("МЕДВЕЖИЙ") == "🔴"
    assert bot.market_sentiment_emoji("НЕЙТРАЛЬНЫЙ") == "🟡"


# ── rsi_4h_zone_label() ──

def test_rsi4_overbought_flag_at_75():
    assert bot.rsi_4h_zone_label(75.0) == "🔴 ПЕРЕКУПЛЕННОСТЬ"


def test_rsi4_overbought_flag_above_75():
    assert bot.rsi_4h_zone_label(79.4) == "🔴 ПЕРЕКУПЛЕННОСТЬ"


def test_rsi4_just_below_75_not_overbought():
    assert bot.rsi_4h_zone_label(74.9) != "🔴 ПЕРЕКУПЛЕННОСТЬ"


def test_rsi4_neutral_midrange():
    assert bot.rsi_4h_zone_label(50.0) == "⚪ НЕЙТРАЛЬНЫЙ"


def test_rsi4_oversold():
    assert bot.rsi_4h_zone_label(10.0) == "🔵 ПЕРЕПРОДАННОСТЬ"
