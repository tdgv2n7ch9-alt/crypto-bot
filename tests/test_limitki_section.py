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


# ── _limitki_zone_status() -- спот-DCA зоны (НАЙДЕНО ЖИВЬЁМ, владелец,       ──
# 2026-07-15, SOL-зона): AVAXUSDT 2.79-4.14 при живой цене $6.68 показывала
# "ОТРАБОТАНА" ("возможность мимо"), хотя цена никогда не была в зоне --
# просто ещё не упала туда. Фикс: маркер "спот-план"/"спот-набор" в note. ──

def test_status_long_spot_dca_far_above_is_waiting_not_worked_out():
    """Живой кейс: AVAXUSDT 2.79-4.14, note содержит 'спот-план', цена 6.681
    (реально наблюдалось живьём 2026-07-15) -- ДО фикса возвращало
    ОТРАБОТАНА, честный ожидаемый результат -- ЖДЁМ ЦЕНУ."""
    label, dist = bot._limitki_zone_status(
        "LONG", 2.79, 4.14, price=6.681,
        note="лестница входа 4.10/3.45/2.90, SL 2.70, спот-план")
    assert label == "ЖДЁМ ЦЕНУ"
    assert dist > 0


def test_status_long_spot_dca_matches_owner_wording_spot_nabor():
    """Точная формулировка владельца ('SPOT-набор', латиница) для новой
    SOL-зоны -- тоже должна триггерить исправленную ветку."""
    label, _ = bot._limitki_zone_status(
        "LONG", 18.77, 25.00, price=77.91,
        note="глобальная зона спроса, база 2023, W1, SPOT-набор")
    assert label == "ЖДЁМ ЦЕНУ"


def test_status_long_without_spot_marker_still_worked_out():
    """Регресс-замок: обычные (не спот-DCA) LONG-зоны БЕЗ маркера в note
    сохраняют старое поведение -- ОТРАБОТАНА, когда цена ушла выше зоны."""
    label, dist = bot._limitki_zone_status("LONG", 100, 110, price=130, note="обычная тактическая зона")
    assert label == "ОТРАБОТАНА"
    assert dist > 0


def test_status_long_spot_dca_price_in_zone_still_price_v_zone():
    """Маркер спот-DCA не должен ломать нормальный статус 'ЦЕНА В ЗОНЕ'."""
    label, dist = bot._limitki_zone_status("LONG", 100, 110, price=105, note="спот-план")
    assert label == "ЦЕНА В ЗОНЕ"
    assert dist == 0.0


def test_status_short_spot_dca_far_below_is_waiting_not_worked_out():
    """Симметричный случай для SHORT (синтетический -- живых SHORT-спот-DCA
    зон в данных пока нет, но логика должна быть последовательной)."""
    label, dist = bot._limitki_zone_status("SHORT", 100, 110, price=70, note="спот-план")
    assert label == "ЖДЁМ ЦЕНУ"
    assert dist > 0


def test_status_cancelled_zone_ignores_spot_marker():
    """ОТМЕНЕНА всегда приоритетнее -- маркер спот-DCA не должен эту ветку
    перебивать."""
    label, dist = bot._limitki_zone_status("LONG", 100, 110, price=130, cancelled=True, note="спот-план")
    assert label == "ОТМЕНЕНА"
    assert dist is None


def test_is_spot_dca_zone_detects_all_known_variants():
    for note in ("спот-план", "спот-набор", "спот план", "спот набор",
                 "SPOT-набор", "SPOT набор", "лестница входа, спот-план, SL 2.70"):
        assert bot._is_spot_dca_zone(note), note


def test_is_spot_dca_zone_false_for_regular_notes():
    for note in ("галочка автора", "новая локальная зона, ретест снизу", ""):
        assert not bot._is_spot_dca_zone(note), note


def test_is_spot_dca_zone_handles_none():
    assert bot._is_spot_dca_zone(None) is False


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


# ── author_zones_status_summary() -- НОЧЬ#3 Н4/Н8 ───────────────────────────

def test_author_zones_status_summary_counts_by_status(monkeypatch):
    config = {
        "updated": "x", "source": "test",
        "BTCUSDT": [_zone("LONG", 100, 110)],   # цена 105 -> В ЗОНЕ
        "ETHUSDT": [_zone("LONG", 200, 210)],   # цена 150 -> ЖДЁМ
        "SOLUSDT": [_zone("SHORT", 50, 60)],    # цена 40 -> ОТРАБОТАНА
    }
    monkeypatch.setattr(bot.level_watch, "load_watch_zones", lambda: config)
    monkeypatch.setattr(bot, "get_top500", lambda: [
        {"symbol": "BTC", "quote": {"USDT": {"price": 105}}},
        {"symbol": "ETH", "quote": {"USDT": {"price": 150}}},
        {"symbol": "SOL", "quote": {"USDT": {"price": 40}}},
    ])
    result = bot.author_zones_status_summary()
    assert result["total"] == 3
    assert result["counts"]["ЦЕНА В ЗОНЕ"] == 1
    assert result["counts"]["ЖДЁМ ЦЕНУ"] == 1
    assert result["counts"]["ОТРАБОТАНА"] == 1


def test_author_zones_status_summary_missing_price_is_honest_na(monkeypatch):
    config = {"updated": "x", "source": "test", "BTCUSDT": [_zone("LONG", 100, 110)]}
    monkeypatch.setattr(bot.level_watch, "load_watch_zones", lambda: config)
    monkeypatch.setattr(bot, "get_top500", lambda: [])  # символ не найден в снапшоте
    result = bot.author_zones_status_summary()
    assert result["total"] == 1
    assert result["counts"]["н/д (нет цены)"] == 1


def test_author_zones_status_summary_empty_when_no_zones(monkeypatch):
    monkeypatch.setattr(bot.level_watch, "load_watch_zones",
                         lambda: {"updated": "x", "source": "y"})
    monkeypatch.setattr(bot, "get_top500", lambda: [])
    result = bot.author_zones_status_summary()
    assert result["total"] == 0
    assert result["zones"] == []
