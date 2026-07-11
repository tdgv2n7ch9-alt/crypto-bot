"""
pytest для onchain_metrics.py -- Фаза C каркас («Пакетный ритм» пакет 2, М5).
Никакого реального фетча (не реализован в этом пакете -- см. докстринг модуля,
Glassnode не имеет бесплатного тира) -- тестируется только честная деградация
"источник не настроен".
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import onchain_metrics as ocm


def test_not_configured_by_default(monkeypatch):
    monkeypatch.setattr(ocm, "ONCHAIN_DATA_SOURCE", "")
    monkeypatch.setattr(ocm, "ONCHAIN_API_KEY", "")
    assert ocm.is_configured() is False


def test_get_onchain_metrics_honest_when_not_configured(monkeypatch):
    monkeypatch.setattr(ocm, "ONCHAIN_DATA_SOURCE", "")
    result = ocm.get_onchain_metrics("BTC")
    assert result["ok"] is False
    assert "не настроен" in result["reason"]


def test_get_onchain_metrics_unknown_source(monkeypatch):
    monkeypatch.setattr(ocm, "ONCHAIN_DATA_SOURCE", "totally_made_up_source")
    monkeypatch.setattr(ocm, "ONCHAIN_API_KEY", "some-key")
    result = ocm.get_onchain_metrics("BTC")
    assert result["ok"] is False
    assert "не распознан" in result["reason"]


def test_get_onchain_metrics_known_source_missing_key(monkeypatch):
    monkeypatch.setattr(ocm, "ONCHAIN_DATA_SOURCE", "glassnode")
    monkeypatch.setattr(ocm, "ONCHAIN_API_KEY", "")
    result = ocm.get_onchain_metrics("BTC")
    assert result["ok"] is False
    assert "ONCHAIN_API_KEY" in result["reason"]


def test_get_onchain_metrics_known_source_with_key_still_not_implemented(monkeypatch):
    """Каркас: даже с источником+ключом фетчер ещё не реализован -- честно,
    не выдумывает данные, которых не фетчил."""
    monkeypatch.setattr(ocm, "ONCHAIN_DATA_SOURCE", "bgeometrics")
    monkeypatch.setattr(ocm, "ONCHAIN_API_KEY", "some-key")
    result = ocm.get_onchain_metrics("BTC")
    assert result["ok"] is False
    assert "фетчер" in result["reason"]


def test_is_configured_requires_both_source_and_key(monkeypatch):
    monkeypatch.setattr(ocm, "ONCHAIN_DATA_SOURCE", "glassnode")
    monkeypatch.setattr(ocm, "ONCHAIN_API_KEY", "")
    assert ocm.is_configured() is False
    monkeypatch.setattr(ocm, "ONCHAIN_API_KEY", "key")
    assert ocm.is_configured() is True


# ── shadow_score_adjustment() ──

def test_shadow_score_adjustment_no_data():
    adj = ocm.shadow_score_adjustment({"ok": False, "reason": "not configured"})
    assert adj["available"] is False
    assert adj["adjustment"] == 0


def test_shadow_score_adjustment_data_present_but_formula_not_designed():
    adj = ocm.shadow_score_adjustment({"ok": True, "sopr": 1.0})
    assert adj["available"] is False
    assert adj["adjustment"] == 0
    assert "формула" in adj["reason"]


# ── Пакет 3 М2: реальные бесплатные источники ──
# Юнит-тесты фетчеров мокают requests.get -- никаких реальных сетевых
# запросов в pytest (детерминизм, скорость, не зависит от аптайма внешних
# API). Живая проверка живых ответов сделана вручную curl'ом 2026-07-11 --
# см. PROGRESS.md/KNOWLEDGE_GAPS.md, здесь тестируется только наш код.

class _FakeResponse:
    def __init__(self, payload, status_ok=True):
        self._payload = payload
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("HTTP error")

    def json(self):
        return self._payload


def test_safe_get_json_success(monkeypatch):
    monkeypatch.setattr(ocm.requests, "get", lambda url, timeout: _FakeResponse({"a": 1}))
    r = ocm._safe_get_json("https://example.test")
    assert r == {"ok": True, "data": {"a": 1}}


def test_safe_get_json_network_error(monkeypatch):
    def _raise(url, timeout):
        raise ConnectionError("boom")
    monkeypatch.setattr(ocm.requests, "get", _raise)
    r = ocm._safe_get_json("https://example.test")
    assert r["ok"] is False
    assert "boom" in r["reason"]


def test_fetch_mempool_fees_success(monkeypatch):
    monkeypatch.setattr(ocm.requests, "get", lambda url, timeout: _FakeResponse(
        {"fastestFee": 5, "halfHourFee": 4, "hourFee": 3, "economyFee": 1}))
    r = ocm.fetch_mempool_fees()
    assert r == {"ok": True, "fastest_sat_vb": 5, "half_hour_sat_vb": 4,
                 "hour_sat_vb": 3, "economy_sat_vb": 1}


def test_fetch_mempool_fees_failure_propagates(monkeypatch):
    def _raise(url, timeout):
        raise TimeoutError("slow")
    monkeypatch.setattr(ocm.requests, "get", _raise)
    r = ocm.fetch_mempool_fees()
    assert r["ok"] is False


def test_fetch_blockchain_hashrate_success(monkeypatch):
    monkeypatch.setattr(ocm.requests, "get", lambda url, timeout: _FakeResponse(
        {"unit": "Hash Rate TH/s", "values": [{"x": 1, "y": 1.0}, {"x": 2, "y": 8.9e8}]}))
    r = ocm.fetch_blockchain_hashrate()
    assert r == {"ok": True, "hashrate": 8.9e8, "unit": "Hash Rate TH/s"}


def test_fetch_blockchain_hashrate_empty_values_is_honest_failure(monkeypatch):
    monkeypatch.setattr(ocm.requests, "get", lambda url, timeout: _FakeResponse(
        {"unit": "Hash Rate TH/s", "values": []}))
    r = ocm.fetch_blockchain_hashrate()
    assert r["ok"] is False
    assert "пуст" in r["reason"]


def test_fetch_defillama_global_tvl_success(monkeypatch):
    monkeypatch.setattr(ocm.requests, "get", lambda url, timeout: _FakeResponse(
        [{"date": 1, "tvl": 100}, {"date": 2, "tvl": 200}]))
    r = ocm.fetch_defillama_global_tvl()
    assert r == {"ok": True, "tvl_usd": 200}


def test_fetch_defillama_stablecoins_success(monkeypatch):
    monkeypatch.setattr(ocm.requests, "get", lambda url, timeout: _FakeResponse(
        [{"date": "1", "totalCirculatingUSD": {"peggedUSD": 123.0}}]))
    r = ocm.fetch_defillama_stablecoins()
    assert r == {"ok": True, "stablecoin_supply_usd": 123.0}


def test_fetch_fear_greed_success(monkeypatch):
    monkeypatch.setattr(ocm.requests, "get", lambda url, timeout: _FakeResponse(
        {"data": [{"value": "26", "value_classification": "Fear"}]}))
    r = ocm.fetch_fear_greed()
    assert r == {"ok": True, "value": 26, "classification": "Fear"}


def test_get_free_onchain_snapshot_btc_includes_chain(monkeypatch):
    ok = {"ok": True}
    for name in ("fetch_fear_greed", "fetch_defillama_global_tvl", "fetch_defillama_stablecoins",
                 "fetch_blockchain_hashrate", "fetch_blockchain_difficulty",
                 "fetch_blockchain_miners_revenue", "fetch_mempool_fees",
                 "fetch_mempool_congestion", "fetch_mempool_difficulty_adjustment"):
        monkeypatch.setattr(ocm, name, lambda: dict(ok))
    snap = ocm.get_free_onchain_snapshot("BTC")
    assert snap["ok"] is True
    assert snap["symbol"] == "BTC"
    assert snap["btc_chain"] is not None
    assert set(snap["btc_chain"]) == {"hashrate", "difficulty", "miners_revenue",
                                       "mempool_fees", "mempool_congestion",
                                       "difficulty_adjustment"}


def test_get_free_onchain_snapshot_eth_has_no_btc_chain(monkeypatch):
    for name in ("fetch_fear_greed", "fetch_defillama_global_tvl", "fetch_defillama_stablecoins"):
        monkeypatch.setattr(ocm, name, lambda: {"ok": True})
    snap = ocm.get_free_onchain_snapshot("ETH")
    assert snap["symbol"] == "ETH"
    assert snap["btc_chain"] is None


def test_get_free_onchain_snapshot_all_failed_is_honestly_not_ok(monkeypatch):
    for name in ("fetch_fear_greed", "fetch_defillama_global_tvl", "fetch_defillama_stablecoins",
                 "fetch_blockchain_hashrate", "fetch_blockchain_difficulty",
                 "fetch_blockchain_miners_revenue", "fetch_mempool_fees",
                 "fetch_mempool_congestion", "fetch_mempool_difficulty_adjustment"):
        monkeypatch.setattr(ocm, name, lambda: {"ok": False, "reason": "down"})
    snap = ocm.get_free_onchain_snapshot("BTC")
    assert snap["ok"] is False


# ── shadow_score_adjustment_free() ──

def test_shadow_score_adjustment_free_snapshot_not_ok():
    adj = ocm.shadow_score_adjustment_free({"ok": False})
    assert adj["available"] is False
    assert adj["adjustment"] == 0


def test_shadow_score_adjustment_free_formula_not_designed():
    adj = ocm.shadow_score_adjustment_free({"ok": True})
    assert adj["available"] is False
    assert adj["adjustment"] == 0
    assert "формула" in adj["reason"]


# ── format_onchain_card_text() -- Пакет 3 (реальные данные) ──

def test_format_onchain_card_text_btc_full_success(monkeypatch):
    monkeypatch.setattr(ocm, "fetch_fear_greed",
                         lambda: {"ok": True, "value": 26, "classification": "Fear"})
    monkeypatch.setattr(ocm, "fetch_defillama_global_tvl", lambda: {"ok": True, "tvl_usd": 1e11})
    monkeypatch.setattr(ocm, "fetch_defillama_stablecoins",
                         lambda: {"ok": True, "stablecoin_supply_usd": 2e11})
    monkeypatch.setattr(ocm, "fetch_blockchain_hashrate",
                         lambda: {"ok": True, "hashrate": 8.9e8, "unit": "TH/s"})
    monkeypatch.setattr(ocm, "fetch_blockchain_difficulty", lambda: {"ok": True, "difficulty": 1.3e14})
    monkeypatch.setattr(ocm, "fetch_blockchain_miners_revenue",
                         lambda: {"ok": True, "usd_per_day": 2.6e7})
    monkeypatch.setattr(ocm, "fetch_mempool_fees",
                         lambda: {"ok": True, "fastest_sat_vb": 3, "economy_sat_vb": 1})
    monkeypatch.setattr(ocm, "fetch_mempool_congestion", lambda: {"ok": True, "tx_count": 97774})
    monkeypatch.setattr(ocm, "fetch_mempool_difficulty_adjustment",
                         lambda: {"ok": True, "progress_pct": 99.9, "estimated_change_pct": -5.05})
    text = ocm.format_onchain_card_text("BTC")
    assert "Fear & Greed: 26/100 (Fear)" in text
    assert "Хешрейт BTC" in text
    assert "Мемпул: 97774" in text
    assert "KNOWLEDGE_GAPS.md" in text
    assert "⚠️" not in text


def test_format_onchain_card_text_partial_failure_is_honest(monkeypatch):
    monkeypatch.setattr(ocm, "fetch_fear_greed", lambda: {"ok": True, "value": 50, "classification": "Neutral"})
    monkeypatch.setattr(ocm, "fetch_defillama_global_tvl", lambda: {"ok": False, "reason": "timeout"})
    monkeypatch.setattr(ocm, "fetch_defillama_stablecoins", lambda: {"ok": False, "reason": "timeout"})
    monkeypatch.setattr(ocm, "fetch_blockchain_hashrate", lambda: {"ok": False, "reason": "timeout"})
    monkeypatch.setattr(ocm, "fetch_blockchain_difficulty", lambda: {"ok": False, "reason": "timeout"})
    monkeypatch.setattr(ocm, "fetch_blockchain_miners_revenue", lambda: {"ok": False, "reason": "timeout"})
    monkeypatch.setattr(ocm, "fetch_mempool_fees", lambda: {"ok": False, "reason": "timeout"})
    monkeypatch.setattr(ocm, "fetch_mempool_congestion", lambda: {"ok": False, "reason": "timeout"})
    monkeypatch.setattr(ocm, "fetch_mempool_difficulty_adjustment", lambda: {"ok": False, "reason": "timeout"})
    text = ocm.format_onchain_card_text("BTC")
    assert "Fear & Greed: 50/100" in text
    assert "⚠️ Не удалось получить" in text


def test_format_onchain_card_text_eth_notes_btc_only_metrics(monkeypatch):
    monkeypatch.setattr(ocm, "fetch_fear_greed", lambda: {"ok": True, "value": 50, "classification": "Neutral"})
    monkeypatch.setattr(ocm, "fetch_defillama_global_tvl", lambda: {"ok": True, "tvl_usd": 1e11})
    monkeypatch.setattr(ocm, "fetch_defillama_stablecoins", lambda: {"ok": True, "stablecoin_supply_usd": 2e11})
    text = ocm.format_onchain_card_text("ETH")
    assert "только для BTC" in text
    assert "KNOWLEDGE_GAPS.md" in text
