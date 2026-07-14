"""
pytest для Пакет 20, Этап 3 (владелец, "быстрая победа" #2, находка
docs/TEXT_AUDIT.md A.7): bot.check_watchlist() обычная (не-author)
zone-touch карточка рендерила "📝 {al['note']}\n" без guard -- если note
пустая строка (дефолт zone.get("note", ""), bot.py:2520), строка
рендерилась голой "📝 " без содержания, нарушая правило чек-листа "нет
пустых значений — только н/д". Тест на рендер -- регрессия, если кто-то
уберёт guard обратно.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot
import inbox
import level_watch


def _coin(symbol, price):
    return {"symbol": symbol, "quote": {"USDT": {"price": price}}}


def _iso_inbox(monkeypatch, tmp_path):
    monkeypatch.setattr(inbox, "INBOX_FILE", str(tmp_path / "inbox.json"))


class _RecordingBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append({"chat_id": chat_id, "text": text, **kw})


def _patch_watchlist_deps_with_note(monkeypatch, note):
    cfg = {"updated": "d", "source": "s",
           "BTCUSDT": [{"side": "LONG", "lo": 100.0, "hi": 110.0, "note": note, "tier": None}]}
    monkeypatch.setattr(level_watch, "load_watch_zones", lambda *a, **kw: cfg)
    monkeypatch.setattr(level_watch, "format_liquidation_cluster_line",
                         lambda *a, **kw: "🗺 Ликвидации рядом: н/д")
    monkeypatch.setattr(bot, "get_cached_rug_line", lambda sym, coin: "")
    monkeypatch.setattr(bot, "add_to_game", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "watchlist_alerted", {})


def test_empty_note_renders_as_nd_not_bare_marker(monkeypatch, tmp_path):
    _iso_inbox(monkeypatch, tmp_path)
    _patch_watchlist_deps_with_note(monkeypatch, "")
    monkeypatch.setattr(inbox, "INBOX_MODE_ENABLED", False)
    owner_id = int(os.getenv("OWNER_CHAT_ID", "7009350191"))
    fake_bot = _RecordingBot()
    coins = [_coin("BTC", 105.0)]
    asyncio.run(bot.check_watchlist(fake_bot, {owner_id}, coins))
    assert len(fake_bot.sent) == 1
    text = fake_bot.sent[0]["text"]
    assert "📝 н/д" in text
    assert "📝 \n" not in text


def test_non_empty_note_still_shown_verbatim(monkeypatch, tmp_path):
    _iso_inbox(monkeypatch, tmp_path)
    _patch_watchlist_deps_with_note(monkeypatch, "конфлюэнс с ТВ-зоной")
    monkeypatch.setattr(inbox, "INBOX_MODE_ENABLED", False)
    owner_id = int(os.getenv("OWNER_CHAT_ID", "7009350191"))
    fake_bot = _RecordingBot()
    coins = [_coin("BTC", 105.0)]
    asyncio.run(bot.check_watchlist(fake_bot, {owner_id}, coins))
    text = fake_bot.sent[0]["text"]
    assert "📝 конфлюэнс с ТВ-зоной" in text
