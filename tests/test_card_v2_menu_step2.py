"""
pytest -- владелец, ДА, 2026-07-23 (сборка нового интерфейса, Шаг 2/3):
inline-глоссарий термины канон-карточки (glossary.CARD_TERMS["card_v2_canon"]/
AMD/NY-midnight) + owner-only калькулятор позиции (/calc, cmd_calc) + кнопки
на превью-карточке из Шага 1/3. Разделы РЫНОК/РАДАРЫ/МОИ/СИСТЕМА и полный
/terminology уже существовали ДО этого шага (_mv2_render_*, cmd_terminology)
-- не тестируются здесь заново, только НОВЫЕ добавления этого шага.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot
import glossary


def _run(coro):
    return asyncio.run(coro)


# ── glossary.py -- термины канон-карточки ──

def test_card_v2_canon_terms_registered():
    assert "card_v2_canon" in glossary.CARD_TERMS
    assert "card_v2_canon" in glossary.CARD_TITLES
    assert "AMD" in glossary.CARD_TERMS["card_v2_canon"]
    assert "NY-midnight" in glossary.CARD_TERMS["card_v2_canon"]


def test_format_card_glossary_text_for_canon_card_includes_amd_and_midnight():
    text = glossary.format_card_glossary_text("card_v2_canon")
    assert "AMD" in text
    assert "Accumulation-Manipulation-Distribution" in text
    assert "NY-midnight" in text
    assert "🧪 Канон-карточка" in text


def test_amd_and_ny_midnight_are_real_terms_not_just_card_terms_list():
    """CARD_TERMS ссылается на ключи TERMS -- честная проверка, что термины
    реально определены, не только упомянуты в списке карточки."""
    assert "AMD" in glossary.TERMS
    assert "NY-midnight" in glossary.TERMS


# ── _render_capital_calc_text() -- чистая функция ──

def test_render_capital_calc_text_includes_price_and_sl():
    text = bot._render_capital_calc_text(100.0, 96.0)
    assert "Вход: 100.0" in text
    assert "SL: 96.0" in text
    assert "Калькулятор позиции" in text


def test_render_capital_calc_text_reuses_card_v2_capital_table():
    """Тот же расчёт, что уже встроен в карточки -- не новая формула."""
    text = bot._render_capital_calc_text(100.0, 95.0)
    table = bot.card_v2.compute_capital_table(100.0, 95.0)
    expected_lines = bot.card_v2.format_capital_block(table)
    for line in expected_lines:
        assert line in text


# ── cmd_calc() -- owner-only ──

class _FakeUser:
    def __init__(self, uid):
        self.id = uid


class _FakeReply:
    def __init__(self):
        self.calls = []

    async def reply_text(self, text, **kw):
        self.calls.append({"text": text, "kw": kw})


class _FakeCalcUpdate:
    def __init__(self, uid, args=None):
        self.effective_user = _FakeUser(uid)
        self.message = _FakeReply()
        self._args = args or []


class _FakeCtx:
    def __init__(self, args=None):
        self.args = args or []


def test_cmd_calc_ignored_for_non_owner(monkeypatch):
    monkeypatch.setenv("OWNER_CHAT_ID", "999")
    update = _FakeCalcUpdate(uid=111)
    _run(bot.cmd_calc(update, _FakeCtx(["100", "96"])))
    assert update.message.calls == []


def test_cmd_calc_shows_usage_without_args(monkeypatch):
    monkeypatch.setenv("OWNER_CHAT_ID", "999")
    update = _FakeCalcUpdate(uid=999)
    _run(bot.cmd_calc(update, _FakeCtx([])))
    assert len(update.message.calls) == 1
    assert "Использование" in update.message.calls[0]["text"]


def test_cmd_calc_valid_args_renders_table(monkeypatch):
    monkeypatch.setenv("OWNER_CHAT_ID", "999")
    update = _FakeCalcUpdate(uid=999)
    _run(bot.cmd_calc(update, _FakeCtx(["100", "96"])))
    assert len(update.message.calls) == 1
    text = update.message.calls[0]["text"]
    assert "Калькулятор позиции" in text
    assert "Вход: 100.0" in text


def test_cmd_calc_non_numeric_args_honest_error(monkeypatch):
    monkeypatch.setenv("OWNER_CHAT_ID", "999")
    update = _FakeCalcUpdate(uid=999)
    _run(bot.cmd_calc(update, _FakeCtx(["abc", "96"])))
    assert "числами" in update.message.calls[0]["text"]


def test_cmd_calc_equal_price_and_sl_honest_error(monkeypatch):
    monkeypatch.setenv("OWNER_CHAT_ID", "999")
    update = _FakeCalcUpdate(uid=999)
    _run(bot.cmd_calc(update, _FakeCtx(["100", "100"])))
    assert "разными" in update.message.calls[0]["text"]


# ── Кнопки на canon-preview карточке (Шаг 1/3) -- обновлены Шагом 2/3 ──

def test_owner_preview_attaches_glossary_and_calc_buttons(monkeypatch):
    monkeypatch.setattr(bot, "CARD_V2_OWNER_PREVIEW", True)
    monkeypatch.setattr(bot, "get_open_interest", lambda sym: {"ok": False})
    monkeypatch.setattr(bot, "get_funding_rate", lambda sym: {"ok": False})
    monkeypatch.setattr(bot, "_get_ls_ratio", lambda sym: 1.0)
    monkeypatch.setattr(bot.ta_extra, "classify_amd_phase",
                        lambda candles: {"phase": "manipulation_bull", "price_vs_nymidnight": "above"})
    monkeypatch.setenv("OWNER_CHAT_ID", "999")

    calls = []

    class _FakeBot:
        async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
            calls.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})

    a = {
        "price": 100.0, "entry1": 100.0, "entry2": 99.0, "entry3": 98.0,
        "sl": 96.0, "tp1": 104.0, "tp2": 108.0, "tp3": 112.0,
        "rr": 2.0, "rr_gate_pass": True, "rocket": 70,
        "rsi_4h": 45.0, "macd_bullish": True, "macd_bearish": False,
        "trend_4h": "bullish", "supertrend_bull": True,
        "above_ema200": True, "above_ema50": True,
        "candles_4h": [{"timestamp": 0, "open": 99, "high": 101, "low": 98, "close": 100}],
    }
    _run(bot._maybe_send_card_v2_owner_preview(_FakeBot(), "BTC", a, "long", "manipulation_bull"))

    assert len(calls) == 1
    kb = calls[0]["reply_markup"]
    buttons = [b for row in kb.inline_keyboard for b in row]
    callback_datas = [b.callback_data for b in buttons]
    assert "glossary_card_v2_canon" in callback_datas
    assert "calc_100.0_96.0" in callback_datas
