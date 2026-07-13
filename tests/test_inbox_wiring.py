"""
pytest для ПАКЕТ 19, П2 (владелец): wiring антиспам-инбокса в bot.py --
меню-счётчики (main_kb_v2), mark_read при входе в раздел
(_mv2_render_tochki/_mv2_render_radary), и маршрутизация check_watchlist()
(⭐ author zone-touch всегда напрямую, rug WARN bypass, иначе -- в
"radary" при INBOX_MODE_ENABLED). Остальные 4 сайта (send_scheduled/
whale_monitor/event_radar_monitor/check_supertrend_signals) используют ТОТ
ЖЕ трёхстрочный паттерн (if INBOX_MODE_ENABLED and cid==owner_id and not
should_bypass_inbox(...): inbox.add_item(...) else: прежняя отправка) --
код-ревью подтверждает идентичную структуру, отдельные тесты на них не
писались в этом пакете (высокая цена мокания market-scan/whale/event-radar
зависимостей) -- см. честную оговорку в PROGRESS.md. Риск ограничен:
inbox.INBOX_MODE_ENABLED=false по умолчанию (владелец включает явно),
до этого момента новый код нигде не исполняется.
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


# ── main_kb_v2() badges ──

def test_main_kb_v2_shows_unread_badges(monkeypatch, tmp_path):
    _iso_inbox(monkeypatch, tmp_path)
    inbox.add_item("tochki", {"a": 1})
    inbox.add_item("tochki", {"a": 2})
    inbox.add_item("radary", {"a": 3})
    kb = bot.main_kb_v2()
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "🎯 ТОЧКИ (2)" in labels
    assert "📡 РАДАРЫ (1)" in labels


def test_main_kb_v2_plain_when_no_unread(monkeypatch, tmp_path):
    _iso_inbox(monkeypatch, tmp_path)
    kb = bot.main_kb_v2()
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "🎯 ТОЧКИ" in labels
    assert "📡 РАДАРЫ" in labels


# ── mark_read on section entry ──

def test_mv2_render_tochki_marks_read(monkeypatch, tmp_path):
    _iso_inbox(monkeypatch, tmp_path)
    inbox.add_item("tochki", {"a": 1})
    monkeypatch.setattr(bot, "_mv2_tochki_collect_items", lambda mode: [])
    fake_bot = _RecordingBot()

    class _FakeBotEditor:
        async def edit_message_text(self, **kw):
            pass

    asyncio.run(bot._mv2_render_tochki(_FakeBotEditor(), chat_id=1, message_id=1))
    assert inbox.get_unread_counts()["tochki"] == 0


def test_mv2_render_radary_marks_read(monkeypatch, tmp_path):
    _iso_inbox(monkeypatch, tmp_path)
    inbox.add_item("radary", {"a": 1})

    class _FakeQuery:
        async def edit_message_text(self, *a, **kw):
            pass

    asyncio.run(bot._mv2_render_radary(_FakeQuery()))
    assert inbox.get_unread_counts()["radary"] == 0


# ── check_watchlist() routing ──

def _patch_watchlist_deps(monkeypatch, tier=None, rug_line=""):
    cfg = {"updated": "d", "source": "s",
           "BTCUSDT": [{"side": "LONG", "lo": 100.0, "hi": 110.0, "note": "n", "tier": tier}]}
    monkeypatch.setattr(level_watch, "load_watch_zones", lambda *a, **kw: cfg)
    monkeypatch.setattr(level_watch, "format_liquidation_cluster_line",
                         lambda *a, **kw: "🗺 Ликвидации рядом: н/д")
    monkeypatch.setattr(bot, "get_cached_rug_line", lambda sym, coin: rug_line)
    monkeypatch.setattr(bot, "add_to_game", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "watchlist_alerted", {})


def test_author_zone_touch_always_direct_send_never_inbox(monkeypatch, tmp_path):
    """Bypass-правило #1 (владелец, дословно): author zone-touch ВСЕГДА
    напрямую, даже при INBOX_MODE_ENABLED=true и нулевом rug-риске."""
    _iso_inbox(monkeypatch, tmp_path)
    _patch_watchlist_deps(monkeypatch, tier="author", rug_line="")
    monkeypatch.setattr(inbox, "INBOX_MODE_ENABLED", True)
    owner_id = int(os.getenv("OWNER_CHAT_ID", "7009350191"))
    fake_bot = _RecordingBot()
    coins = [_coin("BTC", 105.0)]
    asyncio.run(bot.check_watchlist(fake_bot, {owner_id}, coins))
    assert len(fake_bot.sent) == 1
    assert "⭐" in fake_bot.sent[0]["text"] or "ЛИМИТКИ" in fake_bot.sent[0]["text"]
    assert inbox.get_unread_counts()["radary"] == 0


def test_non_author_rug_warn_bypasses_to_direct_send(monkeypatch, tmp_path):
    """Bypass-правило #2: rug WARN (непустая rug_line) -- напрямую, не в инбокс."""
    _iso_inbox(monkeypatch, tmp_path)
    _patch_watchlist_deps(monkeypatch, tier=None, rug_line="🛑 RUG-RADAR: 45/100")
    monkeypatch.setattr(inbox, "INBOX_MODE_ENABLED", True)
    owner_id = int(os.getenv("OWNER_CHAT_ID", "7009350191"))
    fake_bot = _RecordingBot()
    coins = [_coin("BTC", 105.0)]
    asyncio.run(bot.check_watchlist(fake_bot, {owner_id}, coins))
    assert len(fake_bot.sent) == 1
    assert inbox.get_unread_counts()["radary"] == 0


def test_non_author_no_rug_warn_routes_to_inbox_when_enabled(monkeypatch, tmp_path):
    """Ни author, ни rug WARN -- при включённом флаге уходит в "radary",
    НЕ прямой отправкой."""
    _iso_inbox(monkeypatch, tmp_path)
    _patch_watchlist_deps(monkeypatch, tier=None, rug_line="")
    monkeypatch.setattr(inbox, "INBOX_MODE_ENABLED", True)
    owner_id = int(os.getenv("OWNER_CHAT_ID", "7009350191"))
    fake_bot = _RecordingBot()
    coins = [_coin("BTC", 105.0)]
    asyncio.run(bot.check_watchlist(fake_bot, {owner_id}, coins))
    assert len(fake_bot.sent) == 0
    assert inbox.get_unread_counts()["radary"] == 1


def test_flag_disabled_behaves_exactly_like_before(monkeypatch, tmp_path):
    """РЕГРЕССИЯ: INBOX_MODE_ENABLED=false (дефолт) -- поведение НЕ
    изменилось ни для author, ни для обычной зоны, ни для rug WARN."""
    _iso_inbox(monkeypatch, tmp_path)
    _patch_watchlist_deps(monkeypatch, tier=None, rug_line="")
    monkeypatch.setattr(inbox, "INBOX_MODE_ENABLED", False)
    owner_id = int(os.getenv("OWNER_CHAT_ID", "7009350191"))
    fake_bot = _RecordingBot()
    coins = [_coin("BTC", 105.0)]
    asyncio.run(bot.check_watchlist(fake_bot, {owner_id}, coins))
    assert len(fake_bot.sent) == 1
    assert inbox.get_unread_counts() == {"tochki": 0, "radary": 0, "x100": 0}


def test_non_owner_recipient_always_direct_send_even_with_flag_on(monkeypatch, tmp_path):
    """Инбокс -- личная лента ТОЛЬКО владельца (см. inbox.py докстринг):
    любой другой chat_id продолжает получать прямую отправку независимо
    от флага (сейчас таких получателей нет живьём, но код не должен на
    это полагаться)."""
    _iso_inbox(monkeypatch, tmp_path)
    _patch_watchlist_deps(monkeypatch, tier=None, rug_line="")
    monkeypatch.setattr(inbox, "INBOX_MODE_ENABLED", True)
    other_chat_id = 999999
    fake_bot = _RecordingBot()
    coins = [_coin("BTC", 105.0)]
    asyncio.run(bot.check_watchlist(fake_bot, {other_chat_id}, coins))
    assert len(fake_bot.sent) == 1
    assert inbox.get_unread_counts()["radary"] == 0
