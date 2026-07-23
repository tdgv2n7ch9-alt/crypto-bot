"""
pytest -- владелец, ДА, 2026-07-23 (сборка нового интерфейса, Шаг 1/3):
card_v2 подключён к send_scheduled() ЗА ФЛАГОМ CARD_V2_OWNER_PREVIEW,
OWNER-ONLY (параллельно старому формату, подписчицкие каналы не тронуты).

Покрывает: (1) _build_canon_preview_card() -- чистый адаптер real_full_
analysis() -> card_v2.assemble_card_v2_canon(), без сети; (2)
_maybe_send_card_v2_owner_preview() -- flag-гейт (OFF -- честный no-op,
0 сетевых вызовов), сборка+отправка ТОЛЬКО в owner_id при ON, best-effort
(исключение не проваливается наружу, не влияет на боевой сигнал).
"""
import asyncio
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot


def _run(coro):
    return asyncio.run(coro)


def _analysis(is_long=True, rocket=70, rr_gate_pass=True):
    price = 100.0
    return {
        "price": price, "entry1": 100.0, "entry2": 99.0, "entry3": 98.0,
        "sl": 96.0 if is_long else 104.0,
        "tp1": 104.0 if is_long else 96.0, "tp2": 108.0 if is_long else 92.0,
        "tp3": 112.0 if is_long else 88.0,
        "rr": 2.0, "rr_gate_pass": rr_gate_pass, "rocket": rocket,
        "rsi_4h": 45.0 if is_long else 60.0,
        "macd_bullish": is_long, "macd_bearish": not is_long,
        "trend_4h": "bullish" if is_long else "bearish",
        "supertrend_bull": is_long,
        "above_ema200": is_long, "above_ema50": is_long,
        "candles_4h": [{"timestamp": 0, "open": 99, "high": 101, "low": 98, "close": 100}],
    }


# ── _build_canon_preview_card() -- чистый адаптер, без сети ──

def test_build_canon_preview_card_long_includes_symbol_and_direction():
    with patch.object(bot.ta_extra, "classify_amd_phase",
                       return_value={"phase": "manipulation_bull", "price_vs_nymidnight": "above"}):
        text = bot._build_canon_preview_card("BTC", _analysis(is_long=True), "long", "manipulation_bull")
    assert "BTCUSDT LONG" in text
    assert "manipulation_bull" in text
    assert "above NY-midnight" in text


def test_build_canon_preview_card_short_direction():
    with patch.object(bot.ta_extra, "classify_amd_phase",
                       return_value={"phase": "dead_zone", "price_vs_nymidnight": "below"}):
        text = bot._build_canon_preview_card("ETH", _analysis(is_long=False), "short", "dead_zone")
    assert "ETHUSDT SHORT" in text
    assert "below NY-midnight" in text


def test_build_canon_preview_card_checklist_reflects_real_analysis_booleans():
    """Честный чеклист 6/6 когда ВСЕ 5 real_full_analysis-факторов + R:R-гейт
    в пользу направления -- не выдуманные данные, реальный подсчёт."""
    with patch.object(bot.ta_extra, "classify_amd_phase",
                       return_value={"phase": "manipulation_bull", "price_vs_nymidnight": "above"}):
        text = bot._build_canon_preview_card("BTC", _analysis(is_long=True, rr_gate_pass=True),
                                              "long", "manipulation_bull")
    assert text.count("  ✅ ") == 6


def test_build_canon_preview_card_checklist_reflects_failing_rr_gate():
    with patch.object(bot.ta_extra, "classify_amd_phase",
                       return_value={"phase": "manipulation_bull", "price_vs_nymidnight": "above"}):
        text = bot._build_canon_preview_card("BTC", _analysis(is_long=True, rr_gate_pass=False),
                                              "long", "manipulation_bull")
    # чеклист-строки идут с двойным отступом ("  ✅ "/"  ❌ ", format_strength_
    # block()) -- "❌ НЕ входить если" в блоке "ЧТО ДЕЛАТЬ" использует тот же
    # символ без отступа, считаем ИМЕННО чеклист, не весь текст
    assert text.count("  ✅ ") == 5
    assert text.count("  ❌ ") == 1
    assert "  ❌ R:R по структуре ≥ 1:1.5" in text


def test_build_canon_preview_card_uses_operator_preview_fields_when_present():
    a = _analysis(is_long=True)
    a["_owner_preview_oi"] = {"ok": True, "signal": " OI растёт"}
    a["_owner_preview_funding"] = {"ok": True, "signal": " нейтральный"}
    a["_owner_preview_ls_ratio"] = 2.5
    with patch.object(bot.ta_extra, "classify_amd_phase",
                       return_value={"phase": "manipulation_bull", "price_vs_nymidnight": "above"}):
        text = bot._build_canon_preview_card("BTC", a, "long", "manipulation_bull")
    assert "2.50" in text
    assert "лонги преобладают" in text


def test_build_canon_preview_card_honest_na_without_operator_preview_fields():
    with patch.object(bot.ta_extra, "classify_amd_phase",
                       return_value={"phase": "manipulation_bull", "price_vs_nymidnight": "above"}):
        text = bot._build_canon_preview_card("BTC", _analysis(is_long=True), "long", "manipulation_bull")
    assert "OI н/д" in text


# ── _maybe_send_card_v2_owner_preview() -- flag-гейт + best-effort отправка ──

def test_owner_preview_noop_when_flag_off(monkeypatch):
    monkeypatch.setattr(bot, "CARD_V2_OWNER_PREVIEW", False)

    def boom(*a, **kw):
        raise AssertionError("не должно вызываться при флаге OFF")

    monkeypatch.setattr(bot, "get_open_interest", boom)
    monkeypatch.setattr(bot, "get_funding_rate", boom)
    monkeypatch.setattr(bot, "_get_ls_ratio", boom)

    class _BoomBot:
        async def send_message(self, *a, **kw):
            raise AssertionError("не должно отправляться при флаге OFF")

    _run(bot._maybe_send_card_v2_owner_preview(_BoomBot(), "BTC", _analysis(), "long", "manipulation_bull"))


def test_owner_preview_sends_to_owner_id_only_when_flag_on(monkeypatch):
    monkeypatch.setattr(bot, "CARD_V2_OWNER_PREVIEW", True)
    monkeypatch.setattr(bot, "get_open_interest", lambda sym: {"ok": True, "signal": " OI растёт"})
    monkeypatch.setattr(bot, "get_funding_rate", lambda sym: {"ok": True, "signal": " нейтральный"})
    monkeypatch.setattr(bot, "_get_ls_ratio", lambda sym: 1.5)
    monkeypatch.setattr(bot.ta_extra, "classify_amd_phase",
                        lambda candles: {"phase": "manipulation_bull", "price_vs_nymidnight": "above"})
    monkeypatch.setenv("OWNER_CHAT_ID", "999")

    calls = []

    class _FakeBot:
        async def send_message(self, chat_id, text, parse_mode=None):
            calls.append({"chat_id": chat_id, "text": text, "parse_mode": parse_mode})

    _run(bot._maybe_send_card_v2_owner_preview(_FakeBot(), "BTC", _analysis(), "long", "manipulation_bull"))

    assert len(calls) == 1
    assert calls[0]["chat_id"] == 999
    assert calls[0]["parse_mode"] == "Markdown"
    assert "CARD_V2 PREVIEW" in calls[0]["text"]
    assert "BTCUSDT LONG" in calls[0]["text"]


def test_owner_preview_never_raises_on_internal_error(monkeypatch):
    """Best-effort -- сбой ЛЮБОГО шага (сеть/сборка/отправка) НЕ должен
    проваливаться наружу и мешать боевому сигналу в send_scheduled()."""
    monkeypatch.setattr(bot, "CARD_V2_OWNER_PREVIEW", True)

    def boom(sym):
        raise ConnectionError("нет сети")

    monkeypatch.setattr(bot, "get_open_interest", boom)
    monkeypatch.setattr(bot, "get_funding_rate", lambda sym: {"ok": False})
    monkeypatch.setattr(bot, "_get_ls_ratio", lambda sym: 1.0)

    class _FakeBot:
        async def send_message(self, *a, **kw):
            raise AssertionError("не должно дойти до отправки после сбоя выше")

    _run(bot._maybe_send_card_v2_owner_preview(_FakeBot(), "BTC", _analysis(), "long", "manipulation_bull"))
    # если дошли сюда без исключения -- тест прошёл (best-effort сработал)
