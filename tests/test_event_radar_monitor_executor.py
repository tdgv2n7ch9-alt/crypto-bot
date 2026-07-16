"""
pytest для event_radar_monitor() event-loop-блокировки (владелец,
внеочередной аудит блокирующих вызовов, 2026-07-16, Tier 1 #2):
event_radar.poll_and_get_alerts() -- синхронная обёртка над
requests.get к Bybit+Binance announcement-эндпоинтам -- вызывалась
напрямую внутри async event_radar_monitor() (scheduled job, интервал
15 мин), без run_in_executor. Тот же класс регресса, что check_
supertrend_signals/whale_monitor (см. соседние тесты того же дня).

Полный прогон с реальными зависимостями (level_watch/get_all_coins/
inbox/send) дорог в моках (см. докстринг tests/test_inbox_wiring.py) --
здесь узкий тест на сам фикс: "тихий" путь (0 алертов), только чтобы
подтвердить, что poll_and_get_alerts() вызывается через executor с
правильными аргументами и функция не падает.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot


def test_event_radar_monitor_polls_via_executor_without_error(monkeypatch):
    monkeypatch.setattr(bot.level_watch, "load_watch_zones",
                         lambda: {"BTC": {}, "updated": "2026-07-16", "source": "x"})
    monkeypatch.setattr(bot, "get_all_coins", lambda: [{"symbol": "ETH"}])
    monkeypatch.setattr(bot, "WATCHLIST_ZONES", {"SOL": {}})

    captured = {}

    def fake_poll(watch_symbols, tracked_symbols=None, limit=20, known_symbols=None):
        captured["watch_symbols"] = watch_symbols
        captured["tracked_symbols"] = tracked_symbols
        return []  # тихий путь -- без алертов, не трогаем inbox/send

    monkeypatch.setattr(bot.event_radar, "poll_and_get_alerts", fake_poll)

    class _FakeBot:
        async def send_message(self, *a, **kw):
            raise AssertionError("не должно отправлять сообщения на тихом пути")

    asyncio.run(bot.event_radar_monitor(_FakeBot()))

    # BTC пришёл из watch_zones (без updated/source -- отфильтрованы),
    # SOL -- из WATCHLIST_ZONES -- run_in_executor передал аргументы
    # без искажений
    assert captured["watch_symbols"] == {"BTC", "SOL"}
    assert captured["tracked_symbols"] == {"ETH"}


def test_event_radar_monitor_survives_poll_exception(monkeypatch):
    """poll failed: -- честный лог + return, не падает наружу (существующее
    поведение, не менялось фиксом)."""
    monkeypatch.setattr(bot.level_watch, "load_watch_zones", lambda: {})
    monkeypatch.setattr(bot, "get_all_coins", lambda: [])
    monkeypatch.setattr(bot, "WATCHLIST_ZONES", {})

    def raising_poll(*a, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(bot.event_radar, "poll_and_get_alerts", raising_poll)

    class _FakeBot:
        async def send_message(self, *a, **kw):
            raise AssertionError("не должно отправлять сообщения")

    asyncio.run(bot.event_radar_monitor(_FakeBot()))  # не должно бросить исключение
