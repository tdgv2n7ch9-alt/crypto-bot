"""
pytest для Фаза B Derivatives shadow-continuation, инкремент 4 (владелец,
"Наряд на день" 2026-07-15): OKX Liquidation ratio + heatmap для КАЖДОГО
AUTO-кандидата, за флагом shadow_engine.LIQUIDATION_AUTO_SHADOW_ENABLED (по
умолчанию False). Три вещи: (1) _build_auto_liquidation_shadow_record() --
сборка записи на готовых данных, без сети; (2) log_auto_liquidation_shadow_
async() -- флаг-гейт гарантированно no-op при выключенном флаге (включая
ОТСУТСТВИЕ get_liq_data()-вызова); (3) при включённом -- запись формируется
и пишется, символ передаётся БЕЗ USDT-суффикса (get_liq_data сам достраивает).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shadow_engine as se


def _run(coro):
    return asyncio.run(coro)


def _analysis(is_long=True):
    return {"is_long": is_long}


class _FakeBotModule:
    def __init__(self, ok=True, liq_long=0, liq_short=0, liq_ratio=1.0,
                 liq_signal="neutral", heatmap=None):
        self._liq_data = {"ok": ok, "liq_long": liq_long, "liq_short": liq_short,
                           "liq_ratio": liq_ratio, "liq_signal": liq_signal,
                           "heatmap": heatmap}
        self.liq_calls = []

    def get_liq_data(self, symbol):
        self.liq_calls.append(symbol)
        return self._liq_data


# ── shadow_engine._build_auto_liquidation_shadow_record() ───────────────────

def test_build_record_basic_fields():
    bot_mod = _FakeBotModule(liq_long=500000, liq_short=200000, liq_ratio=2.5,
                              liq_signal="bearish")
    rec = se._build_auto_liquidation_shadow_record(
        "BTC", _analysis(is_long=True), promoted_live=True,
        bot_module=bot_mod, liq_data=bot_mod._liq_data)
    assert rec["type"] == "auto_liquidation_shadow"
    assert rec["symbol"] == "BTC"
    assert rec["direction"] == "long"
    assert rec["promoted_live"] is True
    assert rec["liq_long"] == 500000
    assert rec["liq_short"] == 200000
    assert rec["liq_ratio"] == 2.5
    assert rec["liq_signal"] == "bearish"
    assert rec["liq_data_ok"] is True


def test_build_record_heatmap_passthrough():
    heatmap = {"clusters": [{"price": 60000, "notional": 1200000}]}
    bot_mod = _FakeBotModule(heatmap=heatmap)
    rec = se._build_auto_liquidation_shadow_record(
        "BTC", _analysis(), promoted_live=True,
        bot_module=bot_mod, liq_data=bot_mod._liq_data)
    assert rec["heatmap"] == heatmap


def test_build_record_aligned_bearish_short():
    bot_mod = _FakeBotModule(liq_signal="bearish")
    rec = se._build_auto_liquidation_shadow_record(
        "BTC", _analysis(is_long=False), promoted_live=True,
        bot_module=bot_mod, liq_data=bot_mod._liq_data)
    assert rec["aligned"] is True
    assert rec["opposed"] is False


def test_build_record_opposed_bearish_long():
    bot_mod = _FakeBotModule(liq_signal="bearish")
    rec = se._build_auto_liquidation_shadow_record(
        "BTC", _analysis(is_long=True), promoted_live=True,
        bot_module=bot_mod, liq_data=bot_mod._liq_data)
    assert rec["aligned"] is False
    assert rec["opposed"] is True


def test_build_record_neutral_signal_neither_aligned_nor_opposed():
    bot_mod = _FakeBotModule(liq_signal="neutral")
    rec = se._build_auto_liquidation_shadow_record(
        "BTC", _analysis(is_long=True), promoted_live=True,
        bot_module=bot_mod, liq_data=bot_mod._liq_data)
    assert rec["aligned"] is False
    assert rec["opposed"] is False


def test_build_record_data_not_ok_reflected():
    bot_mod = _FakeBotModule(ok=False)
    rec = se._build_auto_liquidation_shadow_record(
        "XYZ", _analysis(), promoted_live=True,
        bot_module=bot_mod, liq_data=bot_mod._liq_data)
    assert rec["liq_data_ok"] is False


# ── shadow_engine.log_auto_liquidation_shadow_async() -- флаг-гейт ──────────

def test_liquidation_auto_shadow_disabled_by_default_is_true():
    assert se.LIQUIDATION_AUTO_SHADOW_ENABLED is False


def test_log_auto_liquidation_shadow_noop_when_disabled(monkeypatch):
    write_calls = []
    bot_mod = _FakeBotModule()
    monkeypatch.setattr(se, "LIQUIDATION_AUTO_SHADOW_ENABLED", False)
    monkeypatch.setattr(se, "_write_local", lambda record: write_calls.append(record) or True)
    result = _run(se.log_auto_liquidation_shadow_async(
        "BTC", _analysis(), promoted_live=True, bot_module=bot_mod))
    assert result is False
    assert write_calls == []
    assert bot_mod.liq_calls == []  # флаг ДО любого I/O, включая get_liq_data()


def test_log_auto_liquidation_shadow_writes_when_enabled(monkeypatch):
    write_calls = []
    bot_mod = _FakeBotModule(liq_ratio=3.0, liq_signal="bearish")
    monkeypatch.setattr(se, "LIQUIDATION_AUTO_SHADOW_ENABLED", True)
    monkeypatch.setattr(se, "_write_local", lambda record: write_calls.append(record) or True)
    monkeypatch.setattr(se, "_sync_to_github_sync", lambda record: True)
    result = _run(se.log_auto_liquidation_shadow_async(
        "BTC", _analysis(), promoted_live=True, bot_module=bot_mod))
    assert result is True
    assert len(write_calls) == 1
    assert write_calls[0]["type"] == "auto_liquidation_shadow"


def test_log_auto_liquidation_shadow_calls_with_bare_symbol_no_usdt_suffix(monkeypatch):
    """get_liq_data() сам достраивает '-USDT-SWAP' (bot.py:13482) -- символ
    должен передаваться БЕЗ суффикса, в отличие от CVD (инкремент 1), где
    суффикс добавляла shadow_engine сама."""
    bot_mod = _FakeBotModule()
    monkeypatch.setattr(se, "LIQUIDATION_AUTO_SHADOW_ENABLED", True)
    monkeypatch.setattr(se, "_write_local", lambda record: True)
    monkeypatch.setattr(se, "_sync_to_github_sync", lambda record: True)
    _run(se.log_auto_liquidation_shadow_async(
        "ETH", _analysis(), promoted_live=True, bot_module=bot_mod))
    assert bot_mod.liq_calls == ["ETH"]


def test_log_auto_liquidation_shadow_build_failure_returns_false_not_raise(monkeypatch):
    monkeypatch.setattr(se, "LIQUIDATION_AUTO_SHADOW_ENABLED", True)
    bot_mod = _FakeBotModule()

    def boom(symbol):
        raise KeyError("simulated")

    monkeypatch.setattr(bot_mod, "get_liq_data", boom)
    result = _run(se.log_auto_liquidation_shadow_async(
        "BTC", _analysis(), promoted_live=True, bot_module=bot_mod))
    assert result is False
