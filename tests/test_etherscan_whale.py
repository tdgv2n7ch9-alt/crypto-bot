"""
pytest для etherscan_whale.py (Пакет 9 М4) -- чистые функции only (get_token_contracts,
detect_large_exchange_transfers). fetch_token_transfers/fetch_transfer_data делают
сетевые вызовы -- не тестируются здесь напрямую (best-effort HTTP-враппер, тот же
принцип, что get_binance_alltime_low()/rug_radar.fetch_coingecko_detail).
"""
import etherscan_whale as ew


def test_get_token_contracts_extracts_known_chains():
    cg_detail = {"platforms": {"ethereum": "0xabc", "binance-smart-chain": "0xdef",
                                "solana": "SomeSolanaAddr"}}
    contracts = ew.get_token_contracts(cg_detail)
    assert contracts == {"ethereum": "0xabc", "binance-smart-chain": "0xdef"}


def test_get_token_contracts_empty_without_platforms():
    assert ew.get_token_contracts({}) == {}
    assert ew.get_token_contracts(None) == {}


def test_get_token_contracts_skips_empty_address():
    cg_detail = {"platforms": {"ethereum": "", "binance-smart-chain": "0xdef"}}
    assert ew.get_token_contracts(cg_detail) == {"binance-smart-chain": "0xdef"}


def _transfer(to_addr, value, decimals=18, tx_hash="0xhash1", ts="1700000000"):
    return {"to": to_addr, "value": str(value), "tokenDecimal": str(decimals),
            "hash": tx_hash, "timeStamp": ts}


def test_detect_large_transfers_empty_input_returns_na():
    r = ew.detect_large_exchange_transfers([], token_price_usd=1.0)
    assert r["available"] is False
    assert r["large_transfer_usd_recent"] is None
    assert r["matched_against_known_list_only"] is True


def test_detect_large_transfers_ignores_unknown_address():
    transfers = [_transfer("0xnotanexchange", 10_000 * 10**18)]
    r = ew.detect_large_exchange_transfers(transfers, token_price_usd=1.0)
    assert r["available"] is True
    assert r["large_transfer_usd_recent"] == 0
    assert r["transfers"] == []


def test_detect_large_transfers_matches_known_exchange_above_threshold():
    known_addr = "0xf977814e90da44bfa03b6295a0616a897441acec"  # Binance: Hot Wallet 20
    transfers = [_transfer(known_addr, 200_000 * 10**18)]  # 200k tokens
    r = ew.detect_large_exchange_transfers(transfers, token_price_usd=1.0)  # $1/token -> $200k
    assert r["available"] is True
    assert r["large_transfer_usd_recent"] == 200_000.0
    assert len(r["transfers"]) == 1
    assert r["transfers"][0]["exchange"] == "Binance: Hot Wallet 20"


def test_detect_large_transfers_below_min_usd_not_counted():
    known_addr = "0xf977814e90da44bfa03b6295a0616a897441acec"
    transfers = [_transfer(known_addr, 100 * 10**18)]  # 100 tokens @ $1 = $100, well below default min
    r = ew.detect_large_exchange_transfers(transfers, token_price_usd=1.0)
    assert r["large_transfer_usd_recent"] == 0
    assert r["transfers"] == []


def test_detect_large_transfers_case_insensitive_address_match():
    known_addr_upper = "0xF977814E90DA44BFA03B6295A0616A897441ACEC"
    transfers = [_transfer(known_addr_upper, 200_000 * 10**18)]
    r = ew.detect_large_exchange_transfers(transfers, token_price_usd=1.0)
    assert r["large_transfer_usd_recent"] == 200_000.0


def test_detect_large_transfers_uses_per_record_decimals():
    known_addr = "0xf977814e90da44bfa03b6295a0616a897441acec"
    # 6-decimal token (like USDC-style), 200_000_000_000 raw / 10**6 = 200_000 tokens
    transfers = [_transfer(known_addr, 200_000 * 10**6, decimals=6)]
    r = ew.detect_large_exchange_transfers(transfers, token_price_usd=1.0)
    assert r["large_transfer_usd_recent"] == 200_000.0


def test_detect_large_transfers_lab_like_april_reconstruction():
    # Reconstruction of the confirmed (web-verified) LAB April event: ~100M tokens
    # to a Bitget-style deposit address -- using a KNOWN address from our curated
    # list to prove the detector logic works end-to-end at LAB's actual scale.
    known_addr = "0xf977814e90da44bfa03b6295a0616a897441acec"
    price_at_time = 0.21  # LAB price ~01-04-2026 (see PROGRESS.md LAB backtest)
    transfers = [_transfer(known_addr, 100_000_000 * 10**18)]
    r = ew.detect_large_exchange_transfers(transfers, token_price_usd=price_at_time)
    assert r["available"] is True
    assert r["large_transfer_usd_recent"] == 100_000_000 * 0.21
    assert r["large_transfer_usd_recent"] > ew.LARGE_TRANSFER_USD_MIN


def test_detect_large_transfers_custom_known_addresses_override():
    custom = {"0xcustom": "MyExchange"}
    transfers = [_transfer("0xCUSTOM", 200_000 * 10**18)]
    r = ew.detect_large_exchange_transfers(transfers, token_price_usd=1.0, known_addresses=custom)
    assert r["transfers"][0]["exchange"] == "MyExchange"


def test_fetch_token_transfers_returns_empty_without_api_key():
    result = ew.fetch_token_transfers("0xsome_contract", "ethereum", api_key="")
    assert result == []


def test_fetch_transfer_data_returns_empty_dict_without_api_key():
    result = ew.fetch_transfer_data({"platforms": {"ethereum": "0xabc"}}, token_price_usd=1.0, api_key="")
    assert result == {}


def test_fetch_transfer_data_returns_empty_dict_without_contracts():
    result = ew.fetch_transfer_data({}, token_price_usd=1.0, api_key="fake_key_for_test")
    assert result == {}
