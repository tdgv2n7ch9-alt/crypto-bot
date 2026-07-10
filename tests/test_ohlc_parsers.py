"""bot._get_ohlc_bybit / bot._get_ohlc_coingecko -- парсинг ответов бирж в единый
формат свечи {"open","high","low","close","vol","timestamp"}. Сеть замокана
(monkeypatch requests.get) -- проверяется только преобразование формата, не сама
сеть. Ночная сессия #3, Блок D."""
import types

import bot


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_get_ohlc_bybit_parses_and_reverses_chronological(monkeypatch):
    # Bybit отдаёт свечи НОВЫЕ ПЕРВЫМИ -- функция должна развернуть в хронологический порядок
    raw_rows = [
        ["2000", "102", "103", "101", "102.5", "50", "5000"],  # новее
        ["1000", "100", "101", "99", "100.5", "40", "4000"],   # старее
    ]
    payload = {"result": {"list": raw_rows}}

    def fake_get(url, params=None, timeout=None):
        return _FakeResponse(payload)

    monkeypatch.setattr(bot.requests, "get", fake_get)
    monkeypatch.setattr(bot, "_bybit_kline_last_call_ts", 0.0)

    candles = bot._get_ohlc_bybit("BTC", "1h", 10)
    assert len(candles) == 2
    assert candles[0]["timestamp"] == 1000  # хронологически первая -- старая свеча
    assert candles[1]["timestamp"] == 2000
    assert candles[0]["open"] == 100.0
    assert candles[0]["high"] == 101.0
    assert candles[0]["low"] == 99.0
    assert candles[0]["close"] == 100.5
    assert candles[0]["vol"] == 40.0


def test_get_ohlc_bybit_unknown_interval_returns_empty():
    assert bot._get_ohlc_bybit("BTC", "13m", 10) == []


def test_get_ohlc_bybit_empty_list_returns_empty(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return _FakeResponse({"result": {"list": []}})

    monkeypatch.setattr(bot.requests, "get", fake_get)
    assert bot._get_ohlc_bybit("BTC", "1h", 10) == []


def test_get_ohlc_bybit_exception_returns_empty(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        raise Exception("network down")

    monkeypatch.setattr(bot.requests, "get", fake_get)
    assert bot._get_ohlc_bybit("BTC", "1h", 10) == []


def test_get_ohlc_coingecko_parses_ohlc_array(monkeypatch):
    # CoinGecko /ohlc: [timestamp, open, high, low, close] -- без volume (всегда 0.0)
    raw = [
        [1000000, 100, 105, 99, 102],
        [2000000, 102, 106, 101, 104],
    ]

    def fake_cg_get(url, params=None, timeout=None):
        return raw

    monkeypatch.setattr(bot, "_cg_get", fake_cg_get)
    monkeypatch.setattr(bot, "_cg_slug", lambda sym: "bitcoin")

    candles = bot._get_ohlc_coingecko("BTC", "4h", 200)
    assert len(candles) == 2
    assert candles[0]["timestamp"] == 1000000
    assert candles[0]["open"] == 100.0
    assert candles[0]["close"] == 102.0
    assert candles[0]["vol"] == 0.0  # CoinGecko free /ohlc не даёт объём -- честный 0.0


def test_get_ohlc_coingecko_respects_limit(monkeypatch):
    raw = [[i * 1000, 100, 101, 99, 100] for i in range(10)]

    def fake_cg_get(url, params=None, timeout=None):
        return raw

    monkeypatch.setattr(bot, "_cg_get", fake_cg_get)
    monkeypatch.setattr(bot, "_cg_slug", lambda sym: "bitcoin")

    candles = bot._get_ohlc_coingecko("BTC", "1h", 3)
    assert len(candles) == 3
    # последние 3 по хронологии (не первые)
    assert candles[-1]["timestamp"] == 9000


def test_get_ohlc_coingecko_exception_returns_empty(monkeypatch):
    def fake_cg_get(url, params=None, timeout=None):
        raise Exception("rate limited")

    monkeypatch.setattr(bot, "_cg_get", fake_cg_get)
    monkeypatch.setattr(bot, "_cg_slug", lambda sym: "bitcoin")
    assert bot._get_ohlc_coingecko("BTC", "1h", 10) == []
