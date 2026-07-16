"""
pytest для whale_monitor() event-loop-блокировки (владелец, внеочередной
аудит блокирующих вызовов, 2026-07-16, Tier 1 #1): _get_ls_ratio()/
_get_oi_change() -- синхронные requests.get-обёртки -- вызывались напрямую
внутри async whale_monitor() для КАЖДОГО из 15 символов в _WHALE_WATCH
подряд, без run_in_executor -- тот же класс регресса, что
check_supertrend_signals (см. tests/test_supertrend_prev_signal.py).

Полный прогон whale_monitor() с реальными зависимостями (get_all_coins,
shadow_engine, inbox, rug_radar) дорог в моках (см. докстринг
tests/test_inbox_wiring.py -- этот путь сознательно не покрывался раньше
по той же причине). Здесь -- узкий тест на сам фикс: "тихий" путь без
алертов (_analyze_whale_signal возвращает None для всех символов), только
чтобы функция дошла до строк с _get_oi_change/_get_ls_ratio и корректно
их вызвала через executor -- без падения и с правильными значениями.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot


def test_whale_monitor_fetches_oi_and_ls_via_executor_without_error(monkeypatch):
    monkeypatch.setattr(bot, "_get_funding_rates", lambda: [
        {"symbol": "BTC", "funding": 0.0, "price": 60000.0, "price_fresh": "",
         "ch24h": 0.0, "ch7d": 0.0, "rank": 1, "vol": 1e9},
    ])
    monkeypatch.setattr(bot, "_WHALE_WATCH", ["BTC"])
    monkeypatch.setattr(bot, "_whale_last_alert", {})

    calls = {"oi": [], "ls": []}

    def fake_oi_change(sym):
        calls["oi"].append(sym)
        return 3.5

    def fake_ls_ratio(sym):
        calls["ls"].append(sym)
        return 1.2

    monkeypatch.setattr(bot, "_get_oi_change", fake_oi_change)
    monkeypatch.setattr(bot, "_get_ls_ratio", fake_ls_ratio)

    captured_args = {}

    def fake_analyze(symbol, funding, oi, ls, price, price_fresh, ch24h, ch7d, rank, vol):
        captured_args["oi"] = oi
        captured_args["ls"] = ls
        return None  # "тихий" путь -- без алерта, не трогаем shadow/inbox/rug/send

    monkeypatch.setattr(bot, "_analyze_whale_signal", fake_analyze)

    class _FakeBot:
        async def send_message(self, *a, **kw):
            raise AssertionError("не должно отправлять сообщения на тихом пути")

    asyncio.run(bot.whale_monitor(_FakeBot()))

    assert calls["oi"] == ["BTC"]
    assert calls["ls"] == ["BTC"]
    # значения дошли до _analyze_whale_signal БЕЗ искажения -- run_in_executor
    # передал (fn, sym) корректно, не потерял/не перепутал аргументы
    assert captured_args["oi"] == 3.5
    assert captured_args["ls"] == 1.2


def test_whale_monitor_survives_when_oi_or_ls_raises(monkeypatch):
    """_get_ls_ratio()/_get_oi_change() уже честно ловят свои сетевые
    исключения и возвращают дефолт (см. их докстринги) -- но сам цикл
    whale_monitor() тоже обёрнут в try/except на символ (существующее
    поведение, не менялось), проверяем, что фикс run_in_executor не
    сломал этот контракт."""
    monkeypatch.setattr(bot, "_get_funding_rates", lambda: [
        {"symbol": "BTC", "funding": 0.0, "price": 60000.0, "price_fresh": "",
         "ch24h": 0.0, "ch7d": 0.0, "rank": 1, "vol": 1e9},
        {"symbol": "ETH", "funding": 0.0, "price": 3000.0, "price_fresh": "",
         "ch24h": 0.0, "ch7d": 0.0, "rank": 2, "vol": 1e9},
    ])
    monkeypatch.setattr(bot, "_WHALE_WATCH", ["BTC", "ETH"])
    monkeypatch.setattr(bot, "_whale_last_alert", {})

    def fake_oi_change(sym):
        if sym == "BTC":
            raise RuntimeError("network down")
        return 1.0

    processed = []

    def fake_ls_ratio(sym):
        processed.append(sym)
        return 1.0

    monkeypatch.setattr(bot, "_get_oi_change", fake_oi_change)
    monkeypatch.setattr(bot, "_get_ls_ratio", fake_ls_ratio)
    monkeypatch.setattr(bot, "_analyze_whale_signal", lambda *a, **kw: None)

    class _FakeBot:
        async def send_message(self, *a, **kw):
            pass

    asyncio.run(bot.whale_monitor(_FakeBot()))
    # BTC упал на _get_oi_change -- цикл продолжил, ETH обработан
    assert processed == ["ETH"]
