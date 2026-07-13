"""
pytest для bot.get_supertrend_signal()/check_supertrend_signals() -- мини-пакет
(владелец, 2026-07-13, живой прогон: "Прошлый сигнал 0.32637 () -- 0.00%").

Два бага:
1. Поиск "прошлого" сигнала начинался с ПОСЛЕДНЕГО бара (len(st)-1) -- если
   направление сменилось именно на нём (а check_supertrend_signals() вызывает
   эту функцию именно ради проверки смены направления), цикл находил САМ
   СЕБЯ: last_signal_price совпадал с current_price (0.00% изменения).
2. candles[i].get("time") -- такого ключа в словаре свечи нет вообще (реальный
   ключ -- "timestamp", эпоха в мс), поэтому last_signal_time был ВСЕГДА None
   -- отсюда пустые скобки в тексте алерта.

Импорт bot.py требует BOT_TOKEN в окружении (модуль не подключается к Telegram
при импорте, только при main()).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot


def _candles(n=100, base_ts=1_700_000_000_000):
    return [{"open": 100 + i, "high": 101 + i, "low": 99 + i, "close": 100 + i,
             "vol": 0, "timestamp": base_ts + i * 14_400_000} for i in range(n)]


def _flat_st(n=100):
    return [{"value": 0.0, "direction": 1, "signal": None} for _ in range(n)]


def test_get_supertrend_signal_first_signal_after_restart_is_honest_none(monkeypatch):
    """ЕДИНСТВЕННЫЙ сигнал во всей истории -- на последнем баре (только что
    случившийся флип). Прошлого сигнала в истории НЕТ -- last_signal_price/
    last_signal_time обязаны быть честно None, не текущая цена/бар."""
    candles = _candles()
    st = _flat_st()
    st[-1] = {"value": 50.0, "direction": -1, "signal": "SELL"}

    monkeypatch.setattr(bot, "get_binance_ohlc", lambda sym, interval="4h", limit=100: candles)
    monkeypatch.setattr(bot, "calc_supertrend", lambda c, period=10, multiplier=3.0: st)

    result = bot.get_supertrend_signal("TESTSYM")
    assert result["last_signal_price"] is None
    assert result["last_signal_time"] is None
    assert result["pct_since_signal"] == 0.0


def test_get_supertrend_signal_finds_genuine_prior_signal_not_itself(monkeypatch):
    """Есть ДВА сигнала: настоящий прошлый (бар 50) и текущий флип (последний
    бар). Обязан найти бар 50, НЕ последний бар (баг: раньше искал с конца и
    находил сам последний флип)."""
    candles = _candles()
    st = _flat_st()
    st[50] = {"value": 120.0, "direction": -1, "signal": "SELL"}
    st[-1] = {"value": 199.0, "direction": 1, "signal": "BUY"}

    monkeypatch.setattr(bot, "get_binance_ohlc", lambda sym, interval="4h", limit=100: candles)
    monkeypatch.setattr(bot, "calc_supertrend", lambda c, period=10, multiplier=3.0: st)

    result = bot.get_supertrend_signal("TESTSYM")
    assert result["last_signal_price"] == candles[50]["close"]
    assert result["last_signal_price"] != result["current_price"]


def test_get_supertrend_signal_time_is_real_datetime_not_epoch_int(monkeypatch):
    """candles[i]["timestamp"] (эпоха, мс) -- ключ "time" не существует. Раньше
    .get("time") давал None всегда; теперь -- реальный datetime-объект (с
    .strftime(), как ожидает check_supertrend_signals())."""
    candles = _candles()
    st = _flat_st()
    st[50] = {"value": 120.0, "direction": -1, "signal": "SELL"}
    st[-1] = {"value": 199.0, "direction": 1, "signal": "BUY"}

    monkeypatch.setattr(bot, "get_binance_ohlc", lambda sym, interval="4h", limit=100: candles)
    monkeypatch.setattr(bot, "calc_supertrend", lambda c, period=10, multiplier=3.0: st)

    result = bot.get_supertrend_signal("TESTSYM")
    assert result["last_signal_time"] is not None
    assert hasattr(result["last_signal_time"], "strftime")
    result["last_signal_time"].strftime("%d.%m %H:%M UTC+3")  # не должно упасть


def test_get_supertrend_signal_no_prior_signal_at_all_honest_none(monkeypatch):
    """Ни одного сигнала во всей истории (direction всегда 1) -- честно None,
    не выдумываем."""
    candles = _candles()
    st = _flat_st()

    monkeypatch.setattr(bot, "get_binance_ohlc", lambda sym, interval="4h", limit=100: candles)
    monkeypatch.setattr(bot, "calc_supertrend", lambda c, period=10, multiplier=3.0: st)

    result = bot.get_supertrend_signal("TESTSYM")
    assert result["last_signal_price"] is None
    assert result["last_signal_time"] is None


class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(text)


def test_check_supertrend_signals_first_signal_shows_honest_na(monkeypatch):
    """Мини-пакет, п.2: при отсутствии прошлого сигнала алерт пишет 'н/д
    (первый сигнал)', не текущую цену/пустые скобки/0.00%."""
    monkeypatch.setattr(bot, "get_supertrend_signal", lambda sym: {
        "direction": 1, "last_signal_price": None, "last_signal_time": None,
        "pct_since_signal": 0.0, "current_price": 100.0,
    })
    monkeypatch.setattr(bot, "format_watchlist_rug_line", lambda sym, coin: "")
    bot.supertrend_cache.clear()
    bot.supertrend_cache["TEST"] = -1  # предыдущее направление SELL, новое BUY -> флип

    coin = {"symbol": "TEST", "slug": "test", "quote": {"USDT": {"volume_24h": 1_000_000}}}
    fake_bot = _FakeBot()
    asyncio.run(bot.check_supertrend_signals(fake_bot, {123}, [coin]))

    assert len(fake_bot.sent) == 1
    text = fake_bot.sent[0]
    assert "н/д (первый сигнал)" in text
    assert "()" not in text
    assert "0.00%" not in text


def test_check_supertrend_signals_shows_rug_line_when_warn(monkeypatch):
    """Мини-пакет, п.1: Supertrend-алерт тоже показывает rug-строку через тот
    же общий хелпер."""
    monkeypatch.setattr(bot, "get_supertrend_signal", lambda sym: {
        "direction": 1, "last_signal_price": 95.0,
        "last_signal_time": __import__("datetime").datetime(2026, 7, 10, tzinfo=bot.TZ),
        "pct_since_signal": 5.26, "current_price": 100.0,
    })
    monkeypatch.setattr(bot, "format_watchlist_rug_line",
                         lambda sym, coin: "🛑 RUG-RADAR: 45 — навес инсайдеров")
    bot.supertrend_cache.clear()
    bot.supertrend_cache["TEST"] = -1

    coin = {"symbol": "TEST", "slug": "test", "quote": {"USDT": {"volume_24h": 1_000_000}}}
    fake_bot = _FakeBot()
    asyncio.run(bot.check_supertrend_signals(fake_bot, {123}, [coin]))

    assert "RUG-RADAR: 45" in fake_bot.sent[0]
