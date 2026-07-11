"""
pytest для АПГРЕЙД 11.07 Этап 3 (первые деривативы, аддитивно): bot._parse_deribit_
option_name()/compute_max_pain() (3.3), bot.get_perp_spot_premium() (3.2, сетевой
вызов замокан). CVD -- см. tests/test_whale_radar.py (живёт в whale_radar.py).
"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot


# ── _parse_deribit_option_name() ──

def test_parse_call_option():
    assert bot._parse_deribit_option_name("BTC-27DEC24-70000-C") == (70000.0, "C")


def test_parse_put_option():
    assert bot._parse_deribit_option_name("BTC-27DEC24-65000-P") == (65000.0, "P")


def test_parse_unexpected_format_returns_none():
    assert bot._parse_deribit_option_name("BTC-PERPETUAL") is None
    assert bot._parse_deribit_option_name("") is None
    assert bot._parse_deribit_option_name("BTC-27DEC24-notanumber-C") is None
    assert bot._parse_deribit_option_name("BTC-27DEC24-70000-X") is None  # не C/P


# ── compute_max_pain() ──

def test_max_pain_no_items_returns_none():
    assert bot.compute_max_pain([]) is None


def test_max_pain_all_unparseable_returns_none():
    items = [{"instrument_name": "BTC-PERPETUAL", "open_interest": 100}]
    assert bot.compute_max_pain(items) is None


def test_max_pain_single_call_strike_dominates():
    """Один call-страйк с большим OI, put-страйки малы -- Max Pain должен быть БЛИЗКО
    к call-страйку (там, где выплата держателям call минимальна -- settlement <= strike)."""
    items = [
        {"instrument_name": "BTC-1JAN25-50000-C", "open_interest": 1000},
        {"instrument_name": "BTC-1JAN25-40000-P", "open_interest": 1},
        {"instrument_name": "BTC-1JAN25-60000-P", "open_interest": 1},
    ]
    mp = bot.compute_max_pain(items)
    assert mp is not None
    assert mp <= 50000  # ниже/на страйке call -- выплата держателям call = 0


def test_max_pain_symmetric_call_put_at_same_strike_is_that_strike():
    """При РАВНОМ OI call и put на ОДНОМ страйке и отсутствии других страйков --
    Max Pain обязан быть именно этот единственный страйк (единственный кандидат)."""
    items = [
        {"instrument_name": "BTC-1JAN25-50000-C", "open_interest": 500},
        {"instrument_name": "BTC-1JAN25-50000-P", "open_interest": 500},
    ]
    assert bot.compute_max_pain(items) == 50000.0


def test_max_pain_ignores_zero_oi_strikes_reasonably():
    items = [
        {"instrument_name": "BTC-1JAN25-50000-C", "open_interest": 0},
        {"instrument_name": "BTC-1JAN25-55000-P", "open_interest": 0},
    ]
    # ни одного реального OI -- функция всё равно должна вернуть какой-то страйк
    # (не падать), не обязательно осмысленный при полностью нулевом OI
    mp = bot.compute_max_pain(items)
    assert mp in (50000.0, 55000.0)


# ── get_perp_spot_premium() ──

def _fake_ticker_response(price):
    r = MagicMock()
    r.json.return_value = {"result": {"list": [{"lastPrice": str(price)}]}}
    return r


def test_perp_spot_premium_overheated_longs():
    with patch("bot.requests.get") as mock_get:
        mock_get.side_effect = [_fake_ticker_response(100.5), _fake_ticker_response(100.0)]
        res = bot.get_perp_spot_premium("BTC")
    assert res["ok"] is True
    assert res["premium_pct"] == 0.5
    assert "перегрев лонгов" in res["signal"]


def test_perp_spot_premium_backwardation_shorts_overheated():
    with patch("bot.requests.get") as mock_get:
        mock_get.side_effect = [_fake_ticker_response(99.0), _fake_ticker_response(100.0)]
        res = bot.get_perp_spot_premium("BTC")
    assert res["ok"] is True
    assert res["premium_pct"] == -1.0
    assert "перегрев шортов" in res["signal"]


def test_perp_spot_premium_normal_range():
    with patch("bot.requests.get") as mock_get:
        mock_get.side_effect = [_fake_ticker_response(100.1), _fake_ticker_response(100.0)]
        res = bot.get_perp_spot_premium("BTC")
    assert res["ok"] is True
    assert res["signal"] == "⚪ норма"


def test_perp_spot_premium_network_failure_is_honest_not_ok():
    with patch("bot.requests.get", side_effect=Exception("network down")):
        res = bot.get_perp_spot_premium("BTC")
    assert res["ok"] is False
    assert res["signal"] == "н/д"


def test_perp_spot_premium_empty_result_list_is_not_ok():
    r = MagicMock()
    r.json.return_value = {"result": {"list": []}}
    with patch("bot.requests.get", return_value=r):
        res = bot.get_perp_spot_premium("BTC")
    assert res["ok"] is False
