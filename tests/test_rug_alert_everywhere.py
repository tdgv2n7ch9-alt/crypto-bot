"""
pytest для мини-пакета (владелец, 2026-07-13): rug-строка "🛑 RUG-RADAR: {score} —
{детекторы}" во ВСЕХ пользовательских алертах (единый rug_radar.format_rug_alert_line())
+ Памп-радар DUMP-сценарий у WARN-монеты (score>=40) меняется с "возможен лонг" на
предупреждение "не торговать против навеса". Найдено живьём: LAB, DUMP-алерт 15:28,
score 45 -- rug_line считалась в pump_detector._compose_alert(), но НИКОГДА не
добавлялась в текст (dead variable), сценарий предлагал лонг несмотря на WARN.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pump_detector as pd
import rug_radar


# ── rug_radar.format_rug_alert_line() -- единый формат ─────────────────────

def test_format_rug_alert_line_at_or_above_warn():
    line = rug_radar.format_rug_alert_line({"score": 45, "reasons": ["навес инсайдеров", "тонкий объём"]})
    assert line == "🛑 RUG-RADAR: 45 — навес инсайдеров; тонкий объём"


def test_format_rug_alert_line_empty_below_warn():
    assert rug_radar.format_rug_alert_line({"score": 10, "reasons": []}) == ""


def test_format_rug_alert_line_empty_on_none():
    assert rug_radar.format_rug_alert_line(None) == ""


def test_format_rug_alert_line_reasons_fallback_when_empty():
    line = rug_radar.format_rug_alert_line({"score": 40, "reasons": []})
    assert line == "🛑 RUG-RADAR: 40 — детали н/д"


# ── pump_detector: DUMP-алерт по WARN-монете (кейс LAB) ────────────────────

class _FakeBot:
    def __init__(self):
        self.sent_texts = []

    async def send_message(self, chat_id, text, **kw):
        self.sent_texts.append(text)

    async def send_photo(self, chat_id, photo, caption, **kw):
        self.sent_texts.append(caption)


class _FakeCtx:
    def __init__(self, coin=None, cg_detail=None):
        self.bot = _FakeBot()
        self.owner_chat_id = 999
        self._coin = coin or {"quote": {"USDT": {
            "market_cap": 5_000_000, "volume_24h": 200_000,
            "percent_change_30d": 10.0, "price": 0.21}}}
        self._cg_detail = cg_detail if cg_detail is not None else {}

    def get_coin_by_symbol(self, sym):
        return self._coin

    def get_cg_detail(self, sym):
        return self._cg_detail

    def get_funding_pct(self, sym):
        return 0.0

    def get_oi_usd(self, sym):
        return 1e7

    def get_oi_change(self, sym):
        # near-zero (< ta_extra.OI_MATRIX_NEAR_ZERO_PCT) -- эти тесты проверяют
        # rug_warn override, не OI-матрицу (см. Пакет 18, п.4,
        # tests/test_pump_oi_matrix_scenario.py -- там OI варьируется предметно).
        return 0.0

    def get_killzone_status(self):
        return {"active": {"name": "NY Open", "quality": "A"}, "is_good": True, "next": None}


def _patch_common(monkeypatch):
    monkeypatch.setattr(pd, "_build_chart", lambda *a, **kw: None)
    monkeypatch.setattr(pd, "_ensure_history", lambda sym: None)
    monkeypatch.setattr(pd.etherscan_whale, "fetch_transfer_data", lambda *a, **kw: None)
    # Владелец, задача #3, 2026-07-16: _start_watch() теперь пишет состояние на
    # диск (save_state_to_disk) -- эти тесты проверяют только текст алерта,
    # изолируем от реального journal/pump_radar_state.json.
    monkeypatch.setattr(pd, "save_state_to_disk", lambda: None)


def test_dump_alert_lab_shows_rug_line_and_not_long_scenario(monkeypatch):
    """Кейс LAB 15:28: DUMP-алерт по WARN-монете (score 45) обязан содержать
    строку RUG-RADAR и НЕ содержать старый сценарий "возможен лонг"."""
    _patch_common(monkeypatch)
    monkeypatch.setattr(pd.rug_radar, "compute_rug_risk",
                         lambda *a, **kw: {"score": 45, "warn": True, "alert": False,
                                           "reasons": ["навес инсайдеров"]})

    ctx = _FakeCtx()
    asyncio.run(pd._start_watch(ctx, "LABUSDT", "dump", 0.21, 4.0, 3.0))

    assert ctx.bot.sent_texts, "алерт не был отправлен"
    text = ctx.bot.sent_texts[0]
    assert "RUG-RADAR" in text
    assert "45" in text
    assert "возможен лонг" not in text
    assert "НЕ торговать против навеса" in text


def test_dump_alert_non_warn_coin_keeps_original_scenario(monkeypatch):
    """score < 40 -- сценарий остаётся прежним ("возможен лонг"), rug-строки нет."""
    _patch_common(monkeypatch)
    monkeypatch.setattr(pd.rug_radar, "compute_rug_risk",
                         lambda *a, **kw: {"score": 5, "warn": False, "alert": False, "reasons": []})

    ctx = _FakeCtx()
    asyncio.run(pd._start_watch(ctx, "BTCUSDT", "dump", 60000.0, 4.0, 3.0))

    text = ctx.bot.sent_texts[0]
    assert "RUG-RADAR" not in text
    assert "возможен лонг после отскока" in text
    assert "НЕ торговать против навеса" not in text


def test_pump_scenario_text_unchanged_by_rug_warn(monkeypatch):
    """Владелец, п.1: override сценарного текста -- только для DUMP, PUMP не трогаем."""
    _patch_common(monkeypatch)
    monkeypatch.setattr(pd.rug_radar, "compute_rug_risk",
                         lambda *a, **kw: {"score": 45, "warn": True, "alert": False,
                                           "reasons": ["навес"]})

    ctx = _FakeCtx()
    asyncio.run(pd._start_watch(ctx, "LABUSDT", "pump", 0.30, 4.0, 3.0))

    text = ctx.bot.sent_texts[0]
    assert "возможен шорт после разворота" in text
    assert "RUG-RADAR" in text  # rug-строка в шапке всё равно показывается


def test_no_rug_data_available_no_line_no_crash(monkeypatch):
    """ctx.get_cg_detail отсутствует -- честная пустая rug-строка, алерт не падает."""
    _patch_common(monkeypatch)

    ctx = _FakeCtx()
    ctx.get_cg_detail = None  # ctx.get_cg_detail falsy -> _get_rug_risk возвращает None рано
    asyncio.run(pd._start_watch(ctx, "XUSDT", "dump", 1.0, 4.0, 3.0))
    text = ctx.bot.sent_texts[0]
    assert "RUG-RADAR" not in text
    assert "возможен лонг после отскока" in text
