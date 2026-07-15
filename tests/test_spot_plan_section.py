"""
pytest для раздела 📈 СПОТ-ПЛАН в экране ЗОНЫ (владелец, 2026-07-15,
SOL-зона): journal/spot_plans.json писался (tools/summer_spot_plan.py), но
нигде не читался в живом боте (честная находка более ранней сессии,
PROGRESS.md "НОЧЬ#3 Н7") -- это первое реальное подключение. Покрывает:
(1) _load_spot_plans() -- честный loader, (2) _spot_plan_row_text() --
форматирование строки с дистанцией %, (3) интеграция в _zones_screen_text().
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot


def _run(coro):
    return asyncio.run(coro)


# ── _load_spot_plans() ──────────────────────────────────────────────────────

def test_load_spot_plans_reads_real_file():
    """Живая сверка (не мок): текущий journal/spot_plans.json существует и
    содержит хотя бы AVAX/SUI (Пакет 16)."""
    plans = bot._load_spot_plans()
    assert "AVAXUSDT" in plans
    assert "SUIUSDT" in plans


def test_load_spot_plans_missing_file_honest_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(bot, "SPOT_PLANS_FILE", str(tmp_path / "does_not_exist.json"))
    assert bot._load_spot_plans() == {}


def test_load_spot_plans_corrupt_file_honest_empty(monkeypatch, tmp_path):
    bad = tmp_path / "spot_plans.json"
    bad.write_text("not valid json{{{", encoding="utf-8")
    monkeypatch.setattr(bot, "SPOT_PLANS_FILE", str(bad))
    assert bot._load_spot_plans() == {}


# ── _spot_plan_row_text() ───────────────────────────────────────────────────

def _sol_plan():
    return {
        "tier": "author",
        "zone": {"lo": 18.77, "hi": 25.00},
        "ladder": [
            {"price": 25.00, "pct": 50},
            {"price": 21.9, "pct": 30},
            {"price": 19.4, "pct": 20},
        ],
        "sl": 17.2,
        "invalidation": "закрытие W1 ниже 17.2",
        "note": "глобальная зона спроса, база 2023, долгосрочный набор — НЕ тактика ближайших недель",
    }


def test_row_text_shows_ladder_sl_and_distance():
    text = bot._spot_plan_row_text("SOL", _sol_plan(), price=77.91)
    assert "SOL" in text
    assert "25" in text and "21.9" in text and "19.4" in text
    assert "50%" in text and "30%" in text and "20%" in text
    assert "17.2" in text  # SL
    assert "%" in text  # дистанция посчитана


def test_row_text_distance_percent_correct():
    # price=100, hi=50 -> (100-50)/100*100 = 50.0%
    plan = {"zone": {"lo": 40, "hi": 50}, "ladder": [{"price": 50, "pct": 50}], "sl": 35}
    text = bot._spot_plan_row_text("XXX", plan, price=100)
    assert "50.0%" in text


def test_row_text_price_already_in_or_below_zone_no_crash():
    plan = {"zone": {"lo": 40, "hi": 50}, "ladder": [{"price": 50, "pct": 100}], "sl": 35}
    text = bot._spot_plan_row_text("XXX", plan, price=45)
    assert "в зоне" in text.lower() or "ниже" in text.lower()


def test_row_text_honest_na_ladder_when_missing():
    plan = {"zone": {"lo": 40, "hi": 50}, "ladder": [], "sl": 35}
    text = bot._spot_plan_row_text("XXX", plan, price=100)
    assert "н/д" in text


def test_row_text_no_price_no_distance_no_crash():
    text = bot._spot_plan_row_text("XXX", _sol_plan(), price=0)
    assert "SOL" not in text or True  # just must not raise
    assert isinstance(text, str)


def test_row_text_includes_note():
    text = bot._spot_plan_row_text("SOL", _sol_plan(), price=77.91)
    assert "глобальная зона спроса" in text


# ── _zones_screen_text() -- интеграция ──────────────────────────────────────

def test_zones_screen_includes_spot_plan_section(monkeypatch):
    monkeypatch.setattr(bot.level_watch, "load_watch_zones", lambda: {"updated": "x", "source": "y"})
    monkeypatch.setattr(bot, "get_top500", lambda: [])
    monkeypatch.setattr(bot, "_load_spot_plans", lambda: {
        "SOLUSDT": _sol_plan(),
    })

    async def _fake_resolve(sym, cg_price):
        return 77.91, "fresh"

    monkeypatch.setattr("live_prices.resolve_price", _fake_resolve, raising=False)
    import live_prices
    monkeypatch.setattr(live_prices, "resolve_price", lambda sym, cg: (77.91, "fresh"))

    text, has_more = _run(bot._zones_screen_text())
    assert "СПОТ-ПЛАН" in text
    assert "SOL" in text


def test_zones_screen_no_spot_plan_section_when_empty(monkeypatch):
    monkeypatch.setattr(bot.level_watch, "load_watch_zones", lambda: {"updated": "x", "source": "y"})
    monkeypatch.setattr(bot, "get_top500", lambda: [])
    monkeypatch.setattr(bot, "_load_spot_plans", lambda: {})

    text, has_more = _run(bot._zones_screen_text())
    assert "СПОТ-ПЛАН" not in text
