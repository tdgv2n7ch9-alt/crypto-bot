"""
pytest для Пакет 18, п.11 (владелец, хвост Пакета 15): "/coin btc и /precision
я так и не проверил живьём -- добавь log.info([FA-PATH] cmd=... engine=...)
при следующем вызове, чтобы верификация была возможна и по логам". Тесты
подтверждают, что строка РЕАЛЬНО пишется при успешном прохождении команды
(не просто существует в коде), используя caplog -- та же гарантия, что
владелец получит на живом контейнере при отправке /coin BTC и /precision.
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot
import fa_engine


def _coin(symbol, rank=50, price=10.0, mcap=1_000_000_000, vol=50_000_000):
    return {
        "symbol": symbol, "slug": symbol.lower(), "cmc_rank": rank,
        "quote": {"USDT": {
            "price": price, "volume_24h": vol, "market_cap": mcap,
            "percent_change_1h": 1.0, "percent_change_24h": 2.0,
            "percent_change_7d": 3.0, "percent_change_30d": 4.0, "percent_change_90d": 5.0,
        }}
    }


class _FakeMsg:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text, **kw):
        self.edits.append(text)

    async def delete(self):
        pass


class _FakeReply:
    async def __call__(self, text, **kw):
        return _FakeMsg()


class _FakeMessage:
    def __init__(self):
        self.reply_text = _FakeReply()


class _FakeChat:
    id = 555


class _FakeBotObj:
    async def send_message(self, chat_id, text, **kw):
        pass

    async def send_photo(self, *a, **kw):
        pass


class _FakeUpdate:
    def __init__(self, args):
        self.message = _FakeMessage()
        self.effective_chat = _FakeChat()
        self.effective_user = type("U", (), {"id": 1})()


class _FakeCtx:
    def __init__(self, args):
        self.args = args
        self.bot = _FakeBotObj()


def test_cmd_coin_logs_fa_path_on_success(monkeypatch, caplog):
    coin = _coin("BTC")
    monkeypatch.setattr(bot, "get_top500", lambda: [coin])

    fa_result_ok = {"ok": True, "symbol": "BTC", "price": 10.0,
                     "block1_bias": {"bias": "LONG"},
                     "block11_trade_plan": {"has_setup": False, "reason": "н/д"},
                     "block13_verdict": {"text": "н/д"}}

    async def _fake_cached(symbol, coin_arg):
        return fa_result_ok

    monkeypatch.setattr(bot, "_get_fa_engine_result_cached", _fake_cached)

    async def _fake_render(bot_obj, chat_id, symbol, result):
        pass

    monkeypatch.setattr(bot, "_render_fa_result", _fake_render)

    update = _FakeUpdate(["BTC"])
    ctx = _FakeCtx(["BTC"])
    with caplog.at_level(logging.INFO):
        asyncio.run(bot.cmd_coin(update, ctx))

    matches = [r.message for r in caplog.records if "[FA-PATH]" in r.message]
    assert matches, "ни одной строки [FA-PATH] не залогировано"
    assert any("cmd=coin" in m and "symbol=BTC" in m and "engine=fa_engine" in m for m in matches)


def test_cmd_coin_no_fa_path_log_on_timeout(monkeypatch, caplog):
    """Честность: строка НЕ пишется, если fa_engine не отдал результат
    (таймаут/недоступность) -- лог подтверждает именно успешный fa_engine
    путь, не любой вызов команды."""
    coin = _coin("BTC")
    monkeypatch.setattr(bot, "get_top500", lambda: [coin])

    async def _fake_cached_fail(symbol, coin_arg):
        return None

    monkeypatch.setattr(bot, "_get_fa_engine_result_cached", _fake_cached_fail)

    update = _FakeUpdate(["BTC"])
    ctx = _FakeCtx(["BTC"])
    with caplog.at_level(logging.INFO):
        asyncio.run(bot.cmd_coin(update, ctx))

    matches = [r.message for r in caplog.records if "[FA-PATH]" in r.message]
    assert matches == []


def _precision_coin(symbol="ETH"):
    return _coin(symbol)


def test_cmd_precision_logs_fa_path_per_finalist(monkeypatch, caplog):
    monkeypatch.setattr(bot, "PRECISION_FA_MIGRATED", True)
    coin = _precision_coin("ETH")
    monkeypatch.setattr(bot, "get_top500", lambda: [coin])
    monkeypatch.setattr(bot, "precision_shot_analysis",
                         lambda c, a: {"type": "BREAKOUT", "ps": 80, "factors": ["f"],
                                        "quality": "🟢", "potential_x": 3.0})
    monkeypatch.setattr(bot, "get_supertrend_signal", lambda sym: {"label": ""})
    monkeypatch.setattr(bot, "get_binance_24h", lambda sym: None)
    monkeypatch.setattr(bot, "get_market_extras", lambda sym: None)

    async def _fake_send_coin(bot_obj, chat_id, sym, slug, a, text):
        pass

    monkeypatch.setattr(bot, "send_coin", _fake_send_coin)

    async def _fast_sleep(*a, **kw):
        return None

    monkeypatch.setattr(bot.asyncio, "sleep", _fast_sleep)

    fa_result_ok = {
        "ok": True, "price": 10.5,
        "block1_bias": {"bias": "LONG"},
        "block11_trade_plan": {"has_setup": True, "direction": "long",
                                "entry1": 10.0, "entry2": 9.8, "entry3": 9.6,
                                "sl": 9.4, "tp1": 11.0, "tp2": 11.5, "tp3": 12.0,
                                "rr_tp1": 2.5, "rr_tp2": 3.0, "rr_tp3": 4.0},
        "candles_4h": [], "zones": {},
    }
    monkeypatch.setattr(fa_engine, "build_full_analysis", lambda sym, c: fa_result_ok)

    update = _FakeUpdate([])
    ctx = _FakeCtx([])
    with caplog.at_level(logging.INFO):
        asyncio.run(bot.cmd_precision(update, ctx))

    matches = [r.message for r in caplog.records if "[FA-PATH]" in r.message]
    assert any("cmd=precision" in m and "symbol=ETH" in m and "engine=fa_engine" in m for m in matches)


def test_cmd_precision_logs_rollback_engine_when_flag_off(monkeypatch, caplog):
    monkeypatch.setattr(bot, "PRECISION_FA_MIGRATED", False)
    coin = _precision_coin("ETH")
    monkeypatch.setattr(bot, "get_top500", lambda: [coin])
    monkeypatch.setattr(bot, "precision_shot_analysis",
                         lambda c, a: {"type": "BREAKOUT", "ps": 80, "factors": ["f"],
                                        "quality": "🟢", "potential_x": 3.0})
    monkeypatch.setattr(bot, "get_supertrend_signal", lambda sym: {"label": ""})
    monkeypatch.setattr(bot, "get_binance_24h", lambda sym: None)
    monkeypatch.setattr(bot, "get_market_extras", lambda sym: None)

    async def _fake_send_coin(bot_obj, chat_id, sym, slug, a, text):
        pass

    monkeypatch.setattr(bot, "send_coin", _fake_send_coin)

    async def _fast_sleep(*a, **kw):
        return None

    monkeypatch.setattr(bot.asyncio, "sleep", _fast_sleep)

    def _fake_real_full_analysis(c):
        a = bot.full_analysis(c)
        a["tp1"], a["tp2"], a["tp3"], a["sl"], a["rr"] = 20.0, 21.0, 22.0, 18.0, 1.8
        return a

    monkeypatch.setattr(bot, "real_full_analysis", _fake_real_full_analysis)

    update = _FakeUpdate([])
    ctx = _FakeCtx([])
    with caplog.at_level(logging.INFO):
        asyncio.run(bot.cmd_precision(update, ctx))

    matches = [r.message for r in caplog.records if "[FA-PATH]" in r.message]
    assert any("engine=real_full_analysis" in m for m in matches)
