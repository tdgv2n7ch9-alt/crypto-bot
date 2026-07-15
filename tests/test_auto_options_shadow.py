"""
pytest для Фаза B Derivatives shadow-continuation, инкремент 2 (владелец,
приоритет 1 после инкремента 1): Put/Call Ratio + Max Pain (Deribit,
bot.get_options_data()) для КАЖДОГО AUTO-кандидата, за флагом
shadow_engine.OPTIONS_AUTO_SHADOW_ENABLED (по умолчанию False). Три вещи:
(1) _build_auto_options_shadow_record() -- сборка записи на готовых данных,
без сети; (2) log_auto_options_shadow_async() -- флаг-гейт гарантированно
no-op при выключенном флаге (включая ОТСУТСТВИЕ get_options_data()-вызова);
(3) при включённом -- запись формируется и пишется.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shadow_engine as se


def _run(coro):
    return asyncio.run(coro)


def _analysis(is_long=True, price=60000.0):
    return {"is_long": is_long, "price": price}


class _FakeBotModule:
    def __init__(self, ok=True, pcr=1.0, signal="neutral", max_pain=None):
        self._options_data = {"ok": ok, "put_call_ratio": pcr,
                               "options_signal": signal, "max_pain": max_pain}
        self.options_calls = 0

    def get_options_data(self):
        self.options_calls += 1
        return self._options_data


# ── shadow_engine._build_auto_options_shadow_record() ───────────────────────

def test_build_record_basic_fields():
    bot_mod = _FakeBotModule(pcr=0.65, signal="bullish", max_pain=58000)
    rec = se._build_auto_options_shadow_record(
        "BTC", _analysis(is_long=True, price=60000), promoted_live=True,
        bot_module=bot_mod, options_data=bot_mod._options_data)
    assert rec["type"] == "auto_options_shadow"
    assert rec["symbol"] == "BTC"
    assert rec["direction"] == "long"
    assert rec["promoted_live"] is True
    assert rec["put_call_ratio"] == 0.65
    assert rec["options_signal"] == "bullish"
    assert rec["max_pain"] == 58000
    assert rec["options_data_ok"] is True


def test_build_record_max_pain_distance_computed():
    bot_mod = _FakeBotModule(max_pain=57000)
    rec = se._build_auto_options_shadow_record(
        "ETH", _analysis(price=60000), promoted_live=False,
        bot_module=bot_mod, options_data=bot_mod._options_data)
    # (60000-57000)/60000*100 = 5.0
    assert rec["max_pain_distance_pct"] == 5.0


def test_build_record_max_pain_distance_none_when_missing():
    bot_mod = _FakeBotModule(max_pain=None)
    rec = se._build_auto_options_shadow_record(
        "ETH", _analysis(price=60000), promoted_live=False,
        bot_module=bot_mod, options_data=bot_mod._options_data)
    assert rec["max_pain_distance_pct"] is None


def test_build_record_aligned_bullish_long():
    bot_mod = _FakeBotModule(signal="bullish")
    rec = se._build_auto_options_shadow_record(
        "BTC", _analysis(is_long=True), promoted_live=True,
        bot_module=bot_mod, options_data=bot_mod._options_data)
    assert rec["aligned"] is True
    assert rec["opposed"] is False


def test_build_record_opposed_bullish_short():
    bot_mod = _FakeBotModule(signal="bullish")
    rec = se._build_auto_options_shadow_record(
        "BTC", _analysis(is_long=False), promoted_live=True,
        bot_module=bot_mod, options_data=bot_mod._options_data)
    assert rec["aligned"] is False
    assert rec["opposed"] is True


def test_build_record_neutral_signal_neither_aligned_nor_opposed():
    bot_mod = _FakeBotModule(signal="neutral")
    rec = se._build_auto_options_shadow_record(
        "BTC", _analysis(is_long=True), promoted_live=True,
        bot_module=bot_mod, options_data=bot_mod._options_data)
    assert rec["aligned"] is False
    assert rec["opposed"] is False


def test_build_record_bearish_short_aligned():
    bot_mod = _FakeBotModule(signal="bearish")
    rec = se._build_auto_options_shadow_record(
        "BTC", _analysis(is_long=False), promoted_live=True,
        bot_module=bot_mod, options_data=bot_mod._options_data)
    assert rec["aligned"] is True


def test_build_record_options_data_not_ok_reflected():
    bot_mod = _FakeBotModule(ok=False)
    rec = se._build_auto_options_shadow_record(
        "BTC", _analysis(), promoted_live=True,
        bot_module=bot_mod, options_data=bot_mod._options_data)
    assert rec["options_data_ok"] is False


def test_build_record_zero_price_no_crash_no_distance():
    bot_mod = _FakeBotModule(max_pain=58000)
    rec = se._build_auto_options_shadow_record(
        "BTC", _analysis(price=0), promoted_live=True,
        bot_module=bot_mod, options_data=bot_mod._options_data)
    assert rec["max_pain_distance_pct"] is None


# ── shadow_engine.log_auto_options_shadow_async() -- флаг-гейт ──────────────

def test_options_auto_shadow_disabled_by_default_is_true():
    assert se.OPTIONS_AUTO_SHADOW_ENABLED is False


def test_log_auto_options_shadow_noop_when_disabled(monkeypatch):
    write_calls = []
    bot_mod = _FakeBotModule()
    monkeypatch.setattr(se, "OPTIONS_AUTO_SHADOW_ENABLED", False)
    monkeypatch.setattr(se, "_write_local", lambda record: write_calls.append(record) or True)
    result = _run(se.log_auto_options_shadow_async(
        "BTC", _analysis(), promoted_live=True, bot_module=bot_mod))
    assert result is False
    assert write_calls == []
    assert bot_mod.options_calls == 0  # флаг ДО любого I/O, включая get_options_data()


def test_log_auto_options_shadow_writes_when_enabled(monkeypatch):
    write_calls = []
    bot_mod = _FakeBotModule(signal="bullish", max_pain=58000)
    monkeypatch.setattr(se, "OPTIONS_AUTO_SHADOW_ENABLED", True)
    monkeypatch.setattr(se, "_write_local", lambda record: write_calls.append(record) or True)
    monkeypatch.setattr(se, "_sync_to_github_sync", lambda record: True)
    result = _run(se.log_auto_options_shadow_async(
        "BTC", _analysis(), promoted_live=True, bot_module=bot_mod))
    assert result is True
    assert len(write_calls) == 1
    assert write_calls[0]["type"] == "auto_options_shadow"
    assert bot_mod.options_calls == 1


def test_log_auto_options_shadow_build_failure_returns_false_not_raise(monkeypatch):
    monkeypatch.setattr(se, "OPTIONS_AUTO_SHADOW_ENABLED", True)
    bot_mod = _FakeBotModule()

    def boom():
        raise KeyError("simulated")

    monkeypatch.setattr(bot_mod, "get_options_data", boom)
    result = _run(se.log_auto_options_shadow_async(
        "BTC", _analysis(), promoted_live=True, bot_module=bot_mod))
    assert result is False
