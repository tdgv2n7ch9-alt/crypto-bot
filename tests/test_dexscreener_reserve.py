import logging
from unittest.mock import patch, MagicMock

import dexscreener_reserve as dr


def _pair(address="0xabc", liq=100_000, vol_h24=50_000, mcap=1_000_000,
          buys_h1=10, sells_h1=5, buys_h24=100, sells_h24=90, price="1.23"):
    return {
        "baseToken": {"address": address},
        "priceUsd": price,
        "liquidity": {"usd": liq},
        "volume": {"h24": vol_h24},
        "marketCap": mcap,
        "txns": {"h1": {"buys": buys_h1, "sells": sells_h1},
                 "h24": {"buys": buys_h24, "sells": sells_h24}},
        "pairCreatedAt": 1700000000000,
        "priceChange": {"h24": 5.5},
    }


def test_fetch_batch_pairs_groups_by_address():
    raw = [_pair(address="0xAAA"), _pair(address="0xAAA"), _pair(address="0xBBB")]
    with patch.object(dr, "_dex_get", return_value=raw):
        result = dr.fetch_batch_pairs("bsc", ["0xaaa", "0xbbb"])
    assert len(result["0xaaa"]) == 2
    assert len(result["0xbbb"]) == 1


def test_fetch_batch_pairs_empty_addresses_no_network_call():
    with patch.object(dr, "_dex_get") as mock_get:
        result = dr.fetch_batch_pairs("bsc", [])
    assert result == {}
    mock_get.assert_not_called()


def test_fetch_batch_pairs_over_limit_truncates_and_logs_error(caplog):
    addresses = [f"0x{i}" for i in range(35)]
    with patch.object(dr, "_dex_get", return_value=[]):
        with caplog.at_level(logging.ERROR):
            dr.fetch_batch_pairs("bsc", addresses)
    assert any("35 адресов" in r.message for r in caplog.records)


def test_dex_get_network_error_logs_and_returns_none(caplog):
    with patch("dexscreener_reserve.requests.get", side_effect=Exception("timeout")):
        with caplog.at_level(logging.ERROR):
            result = dr._dex_get("https://api.dexscreener.com/x")
    assert result is None
    assert any("не удался" in r.message for r in caplog.records)


def test_aggregate_liquidity_usd_sums_all_pools():
    pairs = [_pair(liq=10_000), _pair(liq=25_000), _pair(liq=None)]
    assert dr.aggregate_liquidity_usd(pairs) == 35_000


def test_extract_metrics_honest_none_on_missing_fields():
    metrics = dr.extract_metrics({"baseToken": {"address": "0x1"}})
    assert metrics["price_usd"] is None
    assert metrics["liquidity_usd"] is None
    assert metrics["market_cap"] is None


def test_compute_vol_mcap_ratio():
    assert dr.compute_vol_mcap_ratio({"volume_h24": 50_000, "market_cap": 1_000_000}) == 0.05


def test_compute_vol_mcap_ratio_none_on_zero_mcap():
    assert dr.compute_vol_mcap_ratio({"volume_h24": 50_000, "market_cap": 0}) is None
    assert dr.compute_vol_mcap_ratio({"volume_h24": 50_000, "market_cap": None}) is None


def test_is_thin_liquidity_below_threshold():
    assert dr.is_thin_liquidity(10_000, threshold_usd=50_000) is True
    assert dr.is_thin_liquidity(100_000, threshold_usd=50_000) is False


def test_is_thin_liquidity_none_data_not_flagged():
    assert dr.is_thin_liquidity(None) is False


def test_compute_taker_imbalance_positive_and_negative():
    assert dr.compute_taker_imbalance({"buys": 10, "sells": 5}) == (10 - 5) / 15
    assert dr.compute_taker_imbalance({"buys": 5, "sells": 10}) == (5 - 10) / 15


def test_compute_taker_imbalance_none_on_missing_or_zero_total():
    assert dr.compute_taker_imbalance({}) is None
    assert dr.compute_taker_imbalance({"buys": None, "sells": 5}) is None
    assert dr.compute_taker_imbalance({"buys": 0, "sells": 0}) is None


def test_cross_check_token_disabled_by_default_no_network_call():
    assert dr.ENABLE_DEXSCREENER_RESERVE is False
    with patch.object(dr, "fetch_all_pairs_for_token") as mock_fetch:
        result = dr.cross_check_token("bsc", "0xabc", "TEST")
    assert result == {"enabled": False}
    mock_fetch.assert_not_called()


def test_cross_check_token_enabled_picks_most_liquid_pair_and_aggregates():
    pairs = [_pair(liq=10_000, vol_h24=1_000), _pair(liq=90_000, vol_h24=50_000, price="2.00")]
    with patch.object(dr, "ENABLE_DEXSCREENER_RESERVE", True), \
         patch.object(dr, "fetch_all_pairs_for_token", return_value=pairs):
        result = dr.cross_check_token("bsc", "0xabc", "TEST")
    assert result["enabled"] is True
    assert result["ok"] is True
    assert result["price_usd"] == 2.00
    assert result["liquidity_usd_aggregate"] == 100_000
    assert result["pools_count"] == 2


def test_cross_check_token_no_pairs_logs_error(caplog):
    with patch.object(dr, "ENABLE_DEXSCREENER_RESERVE", True), \
         patch.object(dr, "fetch_all_pairs_for_token", return_value=[]):
        with caplog.at_level(logging.ERROR):
            result = dr.cross_check_token("bsc", "0xabc", "TEST")
    assert result == {"enabled": True, "ok": False}
    assert any("нет пар" in r.message for r in caplog.records)


def test_cross_check_token_price_discrepancy_computed():
    pairs = [_pair(liq=90_000, price="1.10")]
    with patch.object(dr, "ENABLE_DEXSCREENER_RESERVE", True), \
         patch.object(dr, "fetch_all_pairs_for_token", return_value=pairs):
        result = dr.cross_check_token("bsc", "0xabc", "TEST", reference_price=1.00)
    assert round(result["price_discrepancy_pct"], 2) == 10.0


def test_log_taker_imbalance_shadow_writes_local_and_syncs():
    with patch("dexscreener_reserve.shadow_engine._write_local", return_value=True) as mock_write, \
         patch("dexscreener_reserve.shadow_engine._sync_to_github_sync", return_value=True) as mock_sync:
        ok = dr.log_taker_imbalance_shadow("TEST", 0.33, 0.10, chain_id="bsc")
    assert ok is True
    mock_write.assert_called_once()
    mock_sync.assert_called_once()
    record = mock_write.call_args[0][0]
    assert record["type"] == "dexscreener_taker_imbalance_shadow"
    assert record["symbol"] == "TEST"
    assert record["taker_imbalance_h1"] == 0.33


def test_log_taker_imbalance_shadow_sync_failure_still_returns_true_and_logs(caplog):
    with patch("dexscreener_reserve.shadow_engine._write_local", return_value=True), \
         patch("dexscreener_reserve.shadow_engine._sync_to_github_sync", side_effect=Exception("network")):
        with caplog.at_level(logging.ERROR):
            ok = dr.log_taker_imbalance_shadow("TEST", 0.33, 0.10)
    assert ok is True
    assert any("GitHub sync" in r.message for r in caplog.records)


def test_log_taker_imbalance_shadow_local_write_failure_returns_false():
    with patch("dexscreener_reserve.shadow_engine._write_local", return_value=False), \
         patch("dexscreener_reserve.shadow_engine._sync_to_github_sync") as mock_sync:
        ok = dr.log_taker_imbalance_shadow("TEST", 0.33, 0.10)
    assert ok is False
    mock_sync.assert_not_called()
