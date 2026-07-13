"""
pytest для Пакет 18, п.13 (владелец, финальное название "⭐ ЛИМИТКИ"): отдельный
раздел под разметки автора watch_zones.json -- применяются 1в1 (side/зона/prio/
note как дал автор, без пересчёта движком), статус (ЖДЁМ ЦЕНУ/ЦЕНА В ЗОНЕ/
ОТРАБОТАНА) и дистанция считаются от честной живой цены. Карточка ИСПОЛНЕНИЯ по
тапу -- единственное, что бот добавляет от себя (лестница 50/30/20, SL за зоной,
размер позиции для $1000, ликвидации, rug-строка). Конфликтная строка "⚠️ против
зоны автора" в чужих сигналах в пределах 2% от author-зоны противоположной стороны.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot
import ta_extra
import live_prices


def _zone(side, lo, hi, prio=1, note=None, tier="author"):
    z = {"side": side, "lo": lo, "hi": hi, "prio": prio, "tier": tier}
    if note:
        z["note"] = note
    return z


# ── _limitki_collect_zones() ────────────────────────────────────────────────

def test_collect_zones_reads_real_watch_zones_file():
    """Живая сверка (не мок): текущий watch_zones.json после Пакета 18 п.13
    содержит tier=author зоны на всех активных символах."""
    items = bot._limitki_collect_zones()
    assert len(items) > 0
    assert all(it["zone"]["tier"] == "author" for it in items)
    symbols = {it["symbol"] for it in items}
    assert "BTC" in symbols and "ETH" in symbols


def test_collect_zones_skips_info_markers(monkeypatch):
    config = {
        "updated": "x", "source": "y",
        "BTCUSDT": [
            _zone("LONG", 100, 110),
            {"side": "INFO", "lo": 105, "hi": 105, "tier": "author"},
        ],
    }
    monkeypatch.setattr(bot.level_watch, "load_watch_zones", lambda: config)
    items = bot._limitki_collect_zones()
    assert len(items) == 1
    assert items[0]["zone"]["side"] == "LONG"


def test_collect_zones_skips_non_author_tier(monkeypatch):
    config = {
        "updated": "x", "source": "y",
        "BTCUSDT": [_zone("LONG", 100, 110, tier="structural")],
    }
    monkeypatch.setattr(bot.level_watch, "load_watch_zones", lambda: config)
    items = bot._limitki_collect_zones()
    assert items == []


def test_collect_zones_multi_zone_symbol_one_row_per_zone(monkeypatch):
    config = {
        "updated": "x", "source": "y",
        "ETHUSDT": [_zone("LONG", 1600, 1700, prio=1), _zone("SHORT", 1900, 1950, prio=2)],
    }
    monkeypatch.setattr(bot.level_watch, "load_watch_zones", lambda: config)
    items = bot._limitki_collect_zones()
    assert len(items) == 2
    assert {it["zone"]["side"] for it in items} == {"LONG", "SHORT"}


# ── _limitki_zone_status() ──────────────────────────────────────────────────

def test_status_long_wait_price_below_zone():
    label, dist = bot._limitki_zone_status("LONG", 100, 110, price=90)
    assert label == "ЖДЁМ ЦЕНУ"
    assert dist > 0


def test_status_long_price_in_zone():
    label, dist = bot._limitki_zone_status("LONG", 100, 110, price=105)
    assert label == "ЦЕНА В ЗОНЕ"
    assert dist == 0.0


def test_status_long_worked_out_price_above_zone():
    label, dist = bot._limitki_zone_status("LONG", 100, 110, price=130)
    assert label == "ОТРАБОТАНА"
    assert dist > 0


def test_status_short_wait_price_above_zone():
    label, dist = bot._limitki_zone_status("SHORT", 100, 110, price=130)
    assert label == "ЖДЁМ ЦЕНУ"


def test_status_short_price_in_zone():
    label, dist = bot._limitki_zone_status("SHORT", 100, 110, price=105)
    assert label == "ЦЕНА В ЗОНЕ"


def test_status_short_worked_out_price_below_zone():
    label, dist = bot._limitki_zone_status("SHORT", 100, 110, price=80)
    assert label == "ОТРАБОТАНА"


# ── _limitki_row_text() -- 1в1 формат ───────────────────────────────────────

def test_row_text_shows_zone_1to1_and_note(monkeypatch):
    monkeypatch.setattr(bot, "get_top500", lambda: [
        {"symbol": "BTC", "quote": {"USDT": {"price": 62000}}}])
    monkeypatch.setattr(live_prices, "get_live_price", lambda sym: (None, None))

    item = {"symbol": "BTC", "zone": _zone("LONG", 61840.9, 62285.0, prio=1,
                                             note="ключевая поддержка")}
    text = asyncio.run(bot._limitki_row_text(item))
    assert "BTC" in text and "LONG" in text
    assert "ключевая поддержка" in text
    assert "prio 1" in text
    assert "ЦЕНА В ЗОНЕ" in text


# ── _mv2_render_limitki() -- рендер с реальными символами ───────────────────

def test_render_limitki_with_real_watch_zones_symbols(monkeypatch):
    """Раздел рендерится со ВСЕМИ текущими author-символами (владелец, DoD:
    "раздел рендерится с текущими символами, статусы считаются")."""
    monkeypatch.setattr(bot, "get_top500", lambda: [])
    monkeypatch.setattr(live_prices, "get_live_price", lambda sym: (None, None))

    class _Q:
        def __init__(self):
            self.calls = []

        async def edit_message_text(self, chat_id, message_id, text, **kw):
            self.calls.append(text)

    q = _Q()
    asyncio.run(bot._mv2_render_limitki(q, 1, 1))
    assert q.calls
    text = q.calls[0]
    assert "ЛИМИТКИ" in text
    for sym in ("BTC", "ETH", "AVAX", "SOL", "ONDO"):
        assert sym in text


def test_menu_v2_has_limitki_button_first():
    kb = bot.main_kb_v2()
    first_row = kb.inline_keyboard[0]
    assert first_row[0].callback_data == "mv2_limitki"
    assert "ЛИМИТКИ" in first_row[0].text


# ── _limitki_execution_card_text() -- карточка исполнения ───────────────────

def test_execution_card_long_ladder_and_sl_beyond_zone(monkeypatch):
    monkeypatch.setattr(bot, "get_top500", lambda: [])
    monkeypatch.setattr(bot.level_watch, "format_liquidation_cluster_line",
                         lambda *a, **kw: "🗺 Ликвидации рядом: н/д")
    monkeypatch.setattr(bot, "get_cached_rug_line", lambda *a, **kw: "")

    text = asyncio.run(bot._limitki_execution_card_text("BTC", "LONG", 100.0, 110.0))
    assert "Вход 1 (50%)" in text and "Вход 2 (30%)" in text and "Вход 3 (20%)" in text
    # entry1 (50%, ближе к цене) -- верхняя граница зоны для LONG
    assert "110" in text
    # SL строго ЗА зоной (ниже lo), не внутри
    assert "SL:" in text
    sl_line = [l for l in text.splitlines() if l.strip().startswith("SL:")][0]
    sl_value = float(sl_line.split("SL:")[1].split("(")[0].strip().replace(",", ""))
    assert sl_value < 100.0
    assert "только лимитные ордера" in text.lower()


def test_execution_card_short_sl_above_zone(monkeypatch):
    monkeypatch.setattr(bot, "get_top500", lambda: [])
    monkeypatch.setattr(bot.level_watch, "format_liquidation_cluster_line",
                         lambda *a, **kw: "")
    monkeypatch.setattr(bot, "get_cached_rug_line", lambda *a, **kw: "")

    text = asyncio.run(bot._limitki_execution_card_text("ETH", "SHORT", 1900.0, 1950.0))
    sl_line = [l for l in text.splitlines() if l.strip().startswith("SL:")][0]
    sl_value = float(sl_line.split("SL:")[1].split("(")[0].strip().replace(",", ""))
    assert sl_value > 1950.0


def test_execution_card_position_size_for_1000_deposit(monkeypatch):
    monkeypatch.setattr(bot, "get_top500", lambda: [])
    monkeypatch.setattr(bot.level_watch, "format_liquidation_cluster_line", lambda *a, **kw: "")
    monkeypatch.setattr(bot, "get_cached_rug_line", lambda *a, **kw: "")

    text = asyncio.run(bot._limitki_execution_card_text("BTC", "LONG", 100.0, 110.0))
    assert "депозит $1000" in text
    for pct in (1, 2, 3):
        assert f"{pct}% риска" in text


def test_execution_card_shows_rug_line_when_warn(monkeypatch):
    monkeypatch.setattr(bot, "get_top500", lambda: [])
    monkeypatch.setattr(bot.level_watch, "format_liquidation_cluster_line", lambda *a, **kw: "")
    monkeypatch.setattr(bot, "get_cached_rug_line", lambda *a, **kw: "🛑 RUG-RADAR: 55 — навес")

    text = asyncio.run(bot._limitki_execution_card_text("LAB", "LONG", 0.20, 0.22))
    assert "RUG-RADAR" in text


def test_execution_card_no_rug_line_when_clean(monkeypatch):
    monkeypatch.setattr(bot, "get_top500", lambda: [])
    monkeypatch.setattr(bot.level_watch, "format_liquidation_cluster_line", lambda *a, **kw: "")
    monkeypatch.setattr(bot, "get_cached_rug_line", lambda *a, **kw: "")

    text = asyncio.run(bot._limitki_execution_card_text("BTC", "LONG", 100.0, 110.0))
    assert "RUG-RADAR" not in text


# ── _check_author_zone_conflict() ───────────────────────────────────────────

def test_conflict_flagged_when_opposite_side_within_2pct(monkeypatch):
    config = {
        "updated": "x", "source": "y",
        "BTCUSDT": [_zone("LONG", 100, 110)],
    }
    monkeypatch.setattr(bot.level_watch, "load_watch_zones", lambda: config)
    # цена внутри 2%-буфера вокруг зоны 100-110, чужой сигнал SHORT
    conflict = bot._check_author_zone_conflict("BTC", "SHORT", price=105)
    assert conflict == "⚠️ против зоны автора"


def test_no_conflict_when_same_side(monkeypatch):
    config = {
        "updated": "x", "source": "y",
        "BTCUSDT": [_zone("LONG", 100, 110)],
    }
    monkeypatch.setattr(bot.level_watch, "load_watch_zones", lambda: config)
    conflict = bot._check_author_zone_conflict("BTC", "LONG", price=105)
    assert conflict == ""


def test_no_conflict_when_far_from_zone(monkeypatch):
    config = {
        "updated": "x", "source": "y",
        "BTCUSDT": [_zone("LONG", 100, 110)],
    }
    monkeypatch.setattr(bot.level_watch, "load_watch_zones", lambda: config)
    conflict = bot._check_author_zone_conflict("BTC", "SHORT", price=200)
    assert conflict == ""


def test_no_conflict_for_unrelated_symbol(monkeypatch):
    config = {
        "updated": "x", "source": "y",
        "BTCUSDT": [_zone("LONG", 100, 110)],
    }
    monkeypatch.setattr(bot.level_watch, "load_watch_zones", lambda: config)
    conflict = bot._check_author_zone_conflict("ETH", "SHORT", price=105)
    assert conflict == ""


# ── zone-touch alert header (check_watchlist_alerts_from_level_watch) ──────

def test_zone_touch_alert_carries_author_tier(monkeypatch):
    config = {
        "updated": "x", "source": "test",
        "BTCUSDT": [_zone("LONG", 60000, 63000)],
    }
    monkeypatch.setattr(bot.level_watch, "load_watch_zones", lambda: config)
    coins = [{"symbol": "BTC", "quote": {"USDT": {"price": 61000}}}]
    alerts = bot.check_watchlist_alerts_from_level_watch(coins)
    assert len(alerts) == 1
    assert alerts[0]["tier"] == "author"
