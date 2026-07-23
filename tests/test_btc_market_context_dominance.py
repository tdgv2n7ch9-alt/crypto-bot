"""
pytest -- владелец, ДА, 2026-07-23 (Mini App /panel редизайн, живая находка):
"Доминация 0.0%" в /panel (и тот же битый маппинг в текстовом экране РЫНОК,
bot.py:5145) -- get_btc_market_context()["dominance"]/["fear_greed"] были
захардкожены в дефолтах result{} и НИКОГДА не пересчитывались внутри функции.
Реальный источник для dominance -- get_global_metrics()["btc_dominance"],
тот же, что уже честно работает в "Обзор рынка"/"Тренд" (bot.py:3981 и
аналоги). Fear&Greed -- тот же inline-фетч alternative.me, что уже
используется в 3 других местах проекта (bot.py:3993 и аналоги).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot


def _patch_common(monkeypatch, dominance=57.3, fg_value=42):
    monkeypatch.setattr(bot, "get_btc_eth_price", lambda: {"BTC": {"price": 60000.0, "ch24h": 1.5}})
    monkeypatch.setattr(bot, "get_binance_ohlc", lambda sym, tf, n: [])
    monkeypatch.setattr(bot, "get_global_metrics", lambda: {"btc_dominance": dominance})

    class _FakeResp:
        def json(self_inner):
            return {"data": [{"value": str(fg_value), "value_classification": "Neutral"}]}

    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **kw: _FakeResp())


def test_dominance_computed_from_global_metrics_not_hardcoded_zero(monkeypatch):
    _patch_common(monkeypatch, dominance=57.3)
    ctx = bot.get_btc_market_context()
    assert ctx["dominance"] == 57.3


def test_fear_greed_computed_not_none(monkeypatch):
    _patch_common(monkeypatch, fg_value=42)
    ctx = bot.get_btc_market_context()
    assert ctx["fear_greed"] == 42


def test_dominance_honest_zero_when_global_metrics_fails(monkeypatch):
    monkeypatch.setattr(bot, "get_btc_eth_price", lambda: {"BTC": {"price": 60000.0, "ch24h": 1.5}})
    monkeypatch.setattr(bot, "get_binance_ohlc", lambda sym, tf, n: [])

    def boom():
        raise RuntimeError("CoinGecko down")
    monkeypatch.setattr(bot, "get_global_metrics", boom)

    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("fng down")))

    ctx = bot.get_btc_market_context()
    assert ctx["dominance"] == 0.0
    assert ctx["fear_greed"] is None
    # остальной контекст всё равно строится (best-effort, не блокирует)
    assert ctx["ok"] is True
