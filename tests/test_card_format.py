"""
pytest для card_format.py (владелец, П-Визуал v2, задача #207) -- единый
модуль форматирования цен по тику. Покрывает: decimals-from-tick, честный
fallback при отказе источника тика, кэш get_tick_size, приоритет
tick_size > symbol-lookup > магнитудная эвристика, регресс-замок на
"микрокап округлился до 0" (та же находка, что в card_v2.default_price_fmt).
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import card_format as cf


def test_decimals_from_tick_basic_cases():
    assert cf._decimals_from_tick(0.10) == 1
    assert cf._decimals_from_tick(0.01) == 2
    assert cf._decimals_from_tick(0.000001) == 6
    assert cf._decimals_from_tick(1.0) == 0
    assert cf._decimals_from_tick(25.0) == 0


def test_decimals_from_tick_honest_default_on_invalid_input():
    assert cf._decimals_from_tick(None) == 2
    assert cf._decimals_from_tick(0) == 2
    assert cf._decimals_from_tick(-1) == 2


def test_fetch_tick_size_returns_none_on_network_failure(monkeypatch):
    import requests
    def fake_get(*a, **k):
        raise requests.exceptions.ConnectionError("boom")
    monkeypatch.setattr(cf.requests, "get", fake_get)
    assert cf.fetch_tick_size("BTCUSDT") is None


def test_fetch_tick_size_returns_none_on_empty_list(monkeypatch):
    class FakeResp:
        def json(self):
            return {"result": {"list": []}}
    monkeypatch.setattr(cf.requests, "get", lambda *a, **k: FakeResp())
    assert cf.fetch_tick_size("NOTREAL") is None


def test_fetch_tick_size_parses_price_filter(monkeypatch):
    class FakeResp:
        def json(self):
            return {"result": {"list": [{"priceFilter": {"tickSize": "0.0001"}}]}}
    monkeypatch.setattr(cf.requests, "get", lambda *a, **k: FakeResp())
    assert cf.fetch_tick_size("AKEUSDT") == 0.0001


def test_get_tick_size_uses_cache_within_ttl(monkeypatch):
    cf._TICK_CACHE.clear()
    calls = {"n": 0}
    def fake_fetch(symbol):
        calls["n"] += 1
        return 0.01
    v1 = cf.get_tick_size("BTCUSDT", fetch_fn=fake_fetch)
    v2 = cf.get_tick_size("BTCUSDT", fetch_fn=fake_fetch)
    assert v1 == v2 == 0.01
    assert calls["n"] == 1  # второй вызов -- из кэша


def test_get_tick_size_refetches_after_ttl_expiry(monkeypatch):
    cf._TICK_CACHE.clear()
    calls = {"n": 0}
    def fake_fetch(symbol):
        calls["n"] += 1
        return 0.01
    cf.get_tick_size("ETHUSDT", fetch_fn=fake_fetch)
    cf._TICK_CACHE["ETHUSDT"] = (0.01, time.time() - cf.TICK_CACHE_TTL_SEC - 1)
    cf.get_tick_size("ETHUSDT", fetch_fn=fake_fetch)
    assert calls["n"] == 2


def test_get_tick_size_does_not_cache_honest_none(monkeypatch):
    cf._TICK_CACHE.clear()
    calls = {"n": 0}
    def fake_fetch(symbol):
        calls["n"] += 1
        return None
    cf.get_tick_size("DEADUSDT", fetch_fn=fake_fetch)
    cf.get_tick_size("DEADUSDT", fetch_fn=fake_fetch)
    assert calls["n"] == 2  # честный None не кэшируется -- следующий тик пробует снова


def test_format_price_explicit_tick_size_wins_over_symbol_lookup():
    text = cf.format_price(1.23456, symbol="BTCUSDT", tick_size=0.0001,
                            get_tick_size_fn=lambda s: 1.0)  # был бы 0 знаков
    assert text == "1.2346"


def test_format_price_uses_symbol_lookup_when_no_explicit_tick():
    text = cf.format_price(0.0007123, symbol="AKEUSDT",
                            get_tick_size_fn=lambda s: 0.0000001)
    assert text == "0.0007123"


def test_format_price_falls_back_to_magnitude_when_tick_unavailable():
    text = cf.format_price(0.0007123, symbol="UNKNOWNUSDT",
                            get_tick_size_fn=lambda s: None)
    assert text == "0.00071230"  # магнитудная эвристика: 0.01 > v -> 8 знаков


def test_format_price_no_symbol_no_tick_uses_magnitude_heuristic():
    assert cf.format_price(50000.5) == "50,000.50"
    assert cf.format_price(6.681) == "6.6810"


def test_format_price_regression_microcap_does_not_round_to_zero():
    """Владелец, находка при сборке card_v2 (см. card_v2.default_price_fmt
    докстринг) -- фиксированный .0f округлял микрокапы вида $0.0120 до "0".
    card_format.py -- единая точка правды, тот же класс регресса не должен
    повториться здесь."""
    text = cf.format_price(0.0120)
    assert text != "0"
    assert "12" in text


def test_format_price_thousands_separator_present_for_large_values():
    assert "," in cf.format_price(123456.78)
