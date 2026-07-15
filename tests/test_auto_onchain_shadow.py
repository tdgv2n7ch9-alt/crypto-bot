"""
pytest для Фаза C on-chain shadow, инкремент 1 (владелец, "Наряд на день"
2026-07-15, приоритет "д" -- следующий модуль roadmap Фаз B-L после закрытия
Фазы B на сегодня): F&G/DeFiLlama TVL+стейблкоины/BTC хешрейт-сложность-
комиссии для КАЖДОГО AUTO-кандидата, за флагом shadow_engine.
ONCHAIN_AUTO_SHADOW_ENABLED (по умолчанию False). Три вещи: (1)
_build_auto_onchain_shadow_record() -- сборка записи на готовых данных, без
сети; (2) log_auto_onchain_shadow_async() -- флаг-гейт гарантированно no-op
при выключенном флаге (включая ОТСУТСТВИЕ get_onchain_snapshot_cached()-
вызова); (3) при включённом -- запись формируется и пишется.
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


def _snapshot(ok=True, fg_value=None, fg_class=None, market=None, btc_chain=None):
    market = market if market is not None else {
        "fear_greed": {"ok": fg_value is not None, "value": fg_value, "classification": fg_class},
        "defillama_tvl": {"ok": False, "reason": "not fetched in this fixture"},
        "defillama_stablecoins": {"ok": False, "reason": "not fetched in this fixture"},
    }
    return {"ok": ok, "symbol": "BTC", "market": market, "btc_chain": btc_chain}


class _FakeBotModule:
    def __init__(self, snapshot=None):
        self._snapshot = snapshot if snapshot is not None else _snapshot()
        self.onchain_calls = []

    def get_onchain_snapshot_cached(self, symbol):
        self.onchain_calls.append(symbol)
        return self._snapshot


# ── shadow_engine._build_auto_onchain_shadow_record() ───────────────────────

def test_build_record_basic_fields():
    snap = _snapshot(ok=True, fg_value=62, fg_class="Greed")
    bot_mod = _FakeBotModule(snap)
    rec = se._build_auto_onchain_shadow_record(
        "BTC", _analysis(is_long=True), promoted_live=True,
        bot_module=bot_mod, snapshot=snap)
    assert rec["type"] == "auto_onchain_shadow"
    assert rec["symbol"] == "BTC"
    assert rec["direction"] == "long"
    assert rec["promoted_live"] is True
    assert rec["onchain_ok"] is True
    assert rec["fear_greed_value"] == 62
    assert rec["fear_greed_classification"] == "Greed"


def test_build_record_direction_short():
    snap = _snapshot()
    bot_mod = _FakeBotModule(snap)
    rec = se._build_auto_onchain_shadow_record(
        "ETH", _analysis(is_long=False), promoted_live=False,
        bot_module=bot_mod, snapshot=snap)
    assert rec["direction"] == "short"
    assert rec["promoted_live"] is False


def test_build_record_market_passthrough_whole_dict():
    market = {
        "fear_greed": {"ok": True, "value": 40, "classification": "Fear"},
        "defillama_tvl": {"ok": True, "tvl_usd": 123456789.0},
        "defillama_stablecoins": {"ok": True, "stablecoin_supply_usd": 987654321.0},
    }
    snap = _snapshot(market=market)
    bot_mod = _FakeBotModule(snap)
    rec = se._build_auto_onchain_shadow_record(
        "BTC", _analysis(), promoted_live=True, bot_module=bot_mod, snapshot=snap)
    assert rec["market"] == market


def test_build_record_btc_chain_passthrough_when_present():
    btc_chain = {
        "hashrate": {"ok": True, "hashrate": 6.5e20, "unit": "hash/s"},
        "difficulty": {"ok": True, "difficulty": 9.0e13},
        "mempool_fees": {"ok": True, "fastest_sat_vb": 12},
    }
    snap = _snapshot(btc_chain=btc_chain)
    bot_mod = _FakeBotModule(snap)
    rec = se._build_auto_onchain_shadow_record(
        "BTC", _analysis(), promoted_live=True, bot_module=bot_mod, snapshot=snap)
    assert rec["btc_chain"] == btc_chain


def test_build_record_btc_chain_none_for_non_btc_symbol():
    """get_onchain_snapshot_cached() честно возвращает btc_chain=None для
    любого символа кроме BTC (см. onchain_metrics.get_free_onchain_snapshot()
    докстринг) -- запись это отражает как есть, не выдумывает данные."""
    snap = _snapshot(btc_chain=None)
    bot_mod = _FakeBotModule(snap)
    rec = se._build_auto_onchain_shadow_record(
        "SOL", _analysis(), promoted_live=True, bot_module=bot_mod, snapshot=snap)
    assert rec["btc_chain"] is None


def test_build_record_onchain_not_ok_reflected():
    snap = _snapshot(ok=False)
    bot_mod = _FakeBotModule(snap)
    rec = se._build_auto_onchain_shadow_record(
        "BTC", _analysis(), promoted_live=True, bot_module=bot_mod, snapshot=snap)
    assert rec["onchain_ok"] is False


def test_build_record_fear_greed_none_when_missing_no_crash():
    """Честный дефолт -- если market-словарь вообще без fear_greed ключа (не
    должно происходить в реальности, но защита от KeyError не помешает)."""
    snap = {"ok": False, "symbol": "BTC", "market": {}, "btc_chain": None}
    bot_mod = _FakeBotModule(snap)
    rec = se._build_auto_onchain_shadow_record(
        "BTC", _analysis(), promoted_live=True, bot_module=bot_mod, snapshot=snap)
    assert rec["fear_greed_value"] is None
    assert rec["fear_greed_classification"] is None


# ── shadow_engine.log_auto_onchain_shadow_async() -- флаг-гейт ──────────────

def test_onchain_auto_shadow_disabled_by_default_is_true():
    assert se.ONCHAIN_AUTO_SHADOW_ENABLED is False


def test_log_auto_onchain_shadow_noop_when_disabled(monkeypatch):
    write_calls = []
    bot_mod = _FakeBotModule()
    monkeypatch.setattr(se, "ONCHAIN_AUTO_SHADOW_ENABLED", False)
    monkeypatch.setattr(se, "_write_local", lambda record: write_calls.append(record) or True)
    result = _run(se.log_auto_onchain_shadow_async(
        "BTC", _analysis(), promoted_live=True, bot_module=bot_mod))
    assert result is False
    assert write_calls == []
    assert bot_mod.onchain_calls == []  # флаг ДО любого I/O, включая get_onchain_snapshot_cached()


def test_log_auto_onchain_shadow_writes_when_enabled(monkeypatch):
    write_calls = []
    bot_mod = _FakeBotModule(_snapshot(ok=True, fg_value=55, fg_class="Neutral"))
    monkeypatch.setattr(se, "ONCHAIN_AUTO_SHADOW_ENABLED", True)
    monkeypatch.setattr(se, "_write_local", lambda record: write_calls.append(record) or True)
    monkeypatch.setattr(se, "_sync_to_github_sync", lambda record: True)
    result = _run(se.log_auto_onchain_shadow_async(
        "BTC", _analysis(), promoted_live=True, bot_module=bot_mod))
    assert result is True
    assert len(write_calls) == 1
    assert write_calls[0]["type"] == "auto_onchain_shadow"
    assert bot_mod.onchain_calls == ["BTC"]


def test_log_auto_onchain_shadow_build_failure_returns_false_not_raise(monkeypatch):
    monkeypatch.setattr(se, "ONCHAIN_AUTO_SHADOW_ENABLED", True)
    bot_mod = _FakeBotModule()

    def boom(symbol):
        raise KeyError("simulated")

    monkeypatch.setattr(bot_mod, "get_onchain_snapshot_cached", boom)
    result = _run(se.log_auto_onchain_shadow_async(
        "BTC", _analysis(), promoted_live=True, bot_module=bot_mod))
    assert result is False


def test_log_auto_onchain_shadow_passes_symbol_through_unchanged(monkeypatch):
    bot_mod = _FakeBotModule()
    monkeypatch.setattr(se, "ONCHAIN_AUTO_SHADOW_ENABLED", True)
    monkeypatch.setattr(se, "_write_local", lambda record: True)
    monkeypatch.setattr(se, "_sync_to_github_sync", lambda record: True)
    _run(se.log_auto_onchain_shadow_async(
        "ETH", _analysis(), promoted_live=True, bot_module=bot_mod))
    assert bot_mod.onchain_calls == ["ETH"]
