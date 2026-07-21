"""
pytest для bot._defillama_price_fallback()/get_price_with_defillama_fallback()
-- владелец, 2026-07-21: DefiLlama /prices как резерв к CoinGecko при
открытом circuit breaker (снизить видимые 429-паузы). ТОЛЬКО фоллбек --
при живом CoinGecko DefiLlama не вызывается вообще.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, raise_exc=None):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self._raise_exc = raise_exc

    def raise_for_status(self):
        if self._raise_exc:
            raise self._raise_exc

    def json(self):
        return self._json_data


# ── _defillama_price_fallback() ──────────────────────────────────────────

def test_defillama_fallback_returns_price_on_success(monkeypatch):
    monkeypatch.setattr(bot, "ENABLE_DEFILLAMA_PRICE_FALLBACK", True)

    def fake_get(url, timeout=None):
        assert url == "https://coins.llama.fi/prices/current/coingecko:bitcoin"
        return _FakeResponse(json_data={"coins": {"coingecko:bitcoin": {"price": 63941.5, "symbol": "BTC"}}})

    monkeypatch.setattr(bot.requests, "get", fake_get)
    price = bot._defillama_price_fallback("bitcoin")
    assert price == 63941.5


def test_defillama_fallback_disabled_by_flag_returns_zero_no_network(monkeypatch):
    monkeypatch.setattr(bot, "ENABLE_DEFILLAMA_PRICE_FALLBACK", False)
    called = {"n": 0}

    def fake_get(*a, **kw):
        called["n"] += 1
        return _FakeResponse()

    monkeypatch.setattr(bot.requests, "get", fake_get)
    price = bot._defillama_price_fallback("bitcoin")
    assert price == 0.0
    assert called["n"] == 0  # флаг False -- сети не касается вообще


def test_defillama_fallback_network_error_logs_explicitly_not_silent(monkeypatch, caplog):
    monkeypatch.setattr(bot, "ENABLE_DEFILLAMA_PRICE_FALLBACK", True)

    def boom(url, timeout=None):
        raise ConnectionError("simulated network failure")

    monkeypatch.setattr(bot.requests, "get", boom)
    import logging
    with caplog.at_level(logging.ERROR, logger="bot"):
        price = bot._defillama_price_fallback("bitcoin")
    assert price == 0.0
    assert any("defillama fallback" in rec.message for rec in caplog.records)  # НЕ silent except:pass


def test_defillama_fallback_empty_response_logs_and_returns_zero(monkeypatch, caplog):
    monkeypatch.setattr(bot, "ENABLE_DEFILLAMA_PRICE_FALLBACK", True)
    monkeypatch.setattr(bot.requests, "get", lambda url, timeout=None: _FakeResponse(json_data={"coins": {}}))
    import logging
    with caplog.at_level(logging.ERROR, logger="bot"):
        price = bot._defillama_price_fallback("unknowncoin")
    assert price == 0.0
    assert any("defillama fallback" in rec.message for rec in caplog.records)


def test_defillama_fallback_http_error_logs_explicitly(monkeypatch, caplog):
    monkeypatch.setattr(bot, "ENABLE_DEFILLAMA_PRICE_FALLBACK", True)

    def fake_get(url, timeout=None):
        import requests as _requests
        resp = _FakeResponse(status_code=500, raise_exc=_requests.exceptions.HTTPError("500 error"))
        return resp

    monkeypatch.setattr(bot.requests, "get", fake_get)
    import logging
    with caplog.at_level(logging.ERROR, logger="bot"):
        price = bot._defillama_price_fallback("bitcoin")
    assert price == 0.0
    assert any("defillama fallback" in rec.message for rec in caplog.records)


# ── get_price_with_defillama_fallback() ──────────────────────────────────

def test_get_price_uses_coingecko_when_alive_does_not_call_defillama(monkeypatch):
    monkeypatch.setattr(bot, "cg_rate_limit_status", lambda: {"in_cooldown": False})
    defillama_called = {"n": 0}
    monkeypatch.setattr(bot, "_defillama_price_fallback", lambda cid: defillama_called.__setitem__("n", defillama_called["n"] + 1) or 999)
    monkeypatch.setattr(bot, "get_all_coins", lambda: [
        {"symbol": "BTC", "quote": {"USDT": {"price": 63941.5}}},
    ])

    price = bot.get_price_with_defillama_fallback("BTC")
    assert price == 63941.5
    assert defillama_called["n"] == 0  # CoinGecko жив -- DefiLlama не вызывается вообще


def test_get_price_uses_defillama_when_circuit_breaker_open(monkeypatch):
    monkeypatch.setattr(bot, "cg_rate_limit_status", lambda: {"in_cooldown": True})
    get_all_coins_called = {"n": 0}
    monkeypatch.setattr(bot, "get_all_coins", lambda: get_all_coins_called.__setitem__("n", get_all_coins_called["n"] + 1) or [])
    monkeypatch.setattr(bot, "_defillama_price_fallback", lambda cid: 63941.5 if cid == "bitcoin" else 0.0)
    monkeypatch.setattr(bot, "_cg_slug", lambda symbol: "bitcoin")

    price = bot.get_price_with_defillama_fallback("BTC")
    assert price == 63941.5
    assert get_all_coins_called["n"] == 0  # circuit breaker открыт -- обычный путь не трогаем


def test_get_price_symbol_not_found_returns_zero(monkeypatch):
    monkeypatch.setattr(bot, "cg_rate_limit_status", lambda: {"in_cooldown": False})
    monkeypatch.setattr(bot, "get_all_coins", lambda: [{"symbol": "ETH", "quote": {"USDT": {"price": 1839.0}}}])
    price = bot.get_price_with_defillama_fallback("NONEXISTENT")
    assert price == 0.0
