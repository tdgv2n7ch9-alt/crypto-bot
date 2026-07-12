"""
pytest для rug_radar.py (Пакет 9, модуль RUG-RADAR -- кейс LAB, METHODOLOGY_CORE.md
§21). Только чистые функции детекторов + compute_rug_risk() на синтетических
данных -- без сети (fetch_coingecko_detail не тестируется здесь, тривиальный
best-effort HTTP-враппер по образцу get_binance_alltime_low()).
"""
import rug_radar as rr


def _coin(market_cap=1e8, volume_24h=1e7, percent_change_30d=10.0):
    return {"quote": {"USDT": {
        "market_cap": market_cap, "volume_24h": volume_24h,
        "percent_change_30d": percent_change_30d,
    }}}


# --- detect_concentration ---

def test_concentration_na_without_holders_data():
    r = rr.detect_concentration(None)
    assert r["available"] is False
    assert r["points"] == 0


def test_concentration_triggers_above_50pct():
    r = rr.detect_concentration({"top10_pct": 62.0})
    assert r["available"] is True
    assert r["points"] == rr.CONCENTRATION_POINTS_MAX


def test_concentration_no_trigger_below_50pct():
    r = rr.detect_concentration({"top10_pct": 30.0})
    assert r["points"] == 0


# --- detect_fdv_mcap_ratio ---

def test_fdv_mcap_na_without_data():
    r = rr.detect_fdv_mcap_ratio(None, 1e8)
    assert r["available"] is False


def test_fdv_mcap_no_trigger_at_normal_ratio():
    r = rr.detect_fdv_mcap_ratio(2e8, 1e8)  # 2x
    assert r["points"] == 0


def test_fdv_mcap_triggers_above_threshold_lab_like():
    # LAB-like: $6B FDV pump vs much smaller real float -- extreme ratio caps at max
    r = rr.detect_fdv_mcap_ratio(6e9, 3.9e8)  # ~15.4x
    assert r["points"] == rr.FDV_MCAP_POINTS_MAX


def test_fdv_mcap_scales_between_3x_and_6x():
    r3 = rr.detect_fdv_mcap_ratio(3.3e8, 1e8)  # 3.3x -> small
    r6 = rr.detect_fdv_mcap_ratio(6e8, 1e8)    # 6x -> max
    assert 0 < r3["points"] < r6["points"] == rr.FDV_MCAP_POINTS_MAX


# --- detect_vertical_growth_thin_volume ---

def test_vertical_growth_na_without_data():
    r = rr.detect_vertical_growth_thin_volume(None, 1e7, 1e8)
    assert r["available"] is False


def test_vertical_growth_no_trigger_when_volume_healthy():
    # +350% but healthy volume (30% of mcap) -- not "thin"
    r = rr.detect_vertical_growth_thin_volume(350.0, 3e7, 1e8)
    assert r["points"] == 0


def test_vertical_growth_triggers_lab_like_pump():
    # LAB: +350% in days, thin volume relative to inflated mcap
    r = rr.detect_vertical_growth_thin_volume(350.0, 5e6, 1e8)  # vol/mcap = 5%
    assert r["points"] == rr.VERTICAL_GROWTH_POINTS


def test_vertical_growth_no_trigger_below_200pct():
    r = rr.detect_vertical_growth_thin_volume(50.0, 5e6, 1e8)
    assert r["points"] == 0


# --- detect_exchange_transfers ---

def test_exchange_transfers_na_without_provider():
    r = rr.detect_exchange_transfers(None)
    assert r["available"] is False
    assert r["points"] == 0


def test_exchange_transfers_triggers_when_positive():
    r = rr.detect_exchange_transfers({"large_transfer_usd_recent": 18_300_000})
    assert r["points"] == rr.EXCHANGE_TRANSFER_POINTS_MAX


def test_exchange_transfers_no_trigger_at_zero():
    r = rr.detect_exchange_transfers({"large_transfer_usd_recent": 0})
    assert r["points"] == 0


# --- detect_age_and_narrow_listing ---

def test_age_listing_na_without_any_data():
    r = rr.detect_age_and_narrow_listing(None, False, None)
    assert r["available"] is False


def test_age_listing_triggers_young_and_narrow():
    r = rr.detect_age_and_narrow_listing(30, False, 2)
    assert r["points"] == rr.AGE_LISTING_POINTS_MAX


def test_age_listing_no_trigger_old_token():
    r = rr.detect_age_and_narrow_listing(3000, False, 2)
    assert r["points"] == 0


def test_age_listing_no_trigger_wide_listing():
    r = rr.detect_age_and_narrow_listing(30, False, 20)
    assert r["points"] == 0


# --- compute_rug_risk / format_rug_risk_line (synthetic LAB-like scenario) ---

def test_compute_rug_risk_lab_like_scenario_partial_data():
    # Only what free CoinGecko data can give us: FDV/MCap + vertical growth --
    # concentration and exchange transfers stay "н/д" (honest, no fabricated data)
    coin = _coin(market_cap=3.9e8, volume_24h=1.5e7, percent_change_30d=350.0)
    cg_detail = {
        "market_data": {"fully_diluted_valuation": {"usd": 6e9},
                         "atl_date": {"usd": "2026-05-01T00:00:00.000Z"}},
        "genesis_date": None,
        "tickers": [{"market": {"name": "Bitget"}}, {"market": {"name": "Gate"}}],
    }
    result = rr.compute_rug_risk("LAB", coin, cg_detail=cg_detail)
    assert result["detectors"]["concentration"]["available"] is False
    assert result["detectors"]["exchange_transfers"]["available"] is False
    assert result["detectors"]["fdv_mcap"]["points"] == rr.FDV_MCAP_POINTS_MAX
    assert result["detectors"]["vertical_growth"]["points"] == rr.VERTICAL_GROWTH_POINTS
    assert result["detectors"]["age_listing"]["points"] == rr.AGE_LISTING_POINTS_MAX  # ~2mo old (approx via ATL), 2 exchanges
    assert result["max_possible_score"] < 100  # honestly capped, 2 detectors unavailable
    assert result["score"] == 55  # 20 (fdv/mcap) + 25 (vertical growth) + 10 (age/listing)
    assert result["warn"] is True  # >=40
    assert result["alert"] is False  # <70 -- honestly below alert even for this LAB-like case,
    # because 2 of 5 detectors (concentration, exchange transfers) are structurally н/д today


def test_compute_rug_risk_age_listing_unavailable_without_age_but_reports_exchanges():
    # genesis_date AND atl_date both missing -- age truly unknown, detector stays
    # honestly at 0 points (can't claim "young" without evidence) even though
    # exchange count is known
    coin = _coin(market_cap=3.9e8, volume_24h=1.5e7, percent_change_30d=350.0)
    cg_detail = {
        "market_data": {"fully_diluted_valuation": {"usd": 6e9}},
        "genesis_date": None,
        "tickers": [{"market": {"name": "Bitget"}}, {"market": {"name": "Gate"}}],
    }
    result = rr.compute_rug_risk("LAB", coin, cg_detail=cg_detail)
    assert result["detectors"]["age_listing"]["points"] == 0
    assert result["detectors"]["age_listing"]["available"] is True  # num_exchanges known


def test_compute_rug_risk_healthy_token_scores_low():
    coin = _coin(market_cap=1e10, volume_24h=5e8, percent_change_30d=5.0)
    cg_detail = {
        "market_data": {"fully_diluted_valuation": {"usd": 1.05e10}},
        "genesis_date": "2013-12-08",
        "tickers": [{"market": {"name": f"Exchange{i}"}} for i in range(20)],
    }
    result = rr.compute_rug_risk("DOGE", coin, cg_detail=cg_detail)
    assert result["score"] == 0
    assert result["warn"] is False


def test_format_rug_risk_line_empty_below_threshold():
    assert rr.format_rug_risk_line({"score": 10, "reasons": []}) == ""


def test_format_rug_risk_line_warn_between_40_and_70():
    line = rr.format_rug_risk_line({"score": 45, "reasons": ["FDV/MCap 5.0x"], "alert": False})
    assert line.startswith("⚠️ RUG-РИСК: 45/100")


def test_format_rug_risk_line_alert_at_70_plus():
    line = rr.format_rug_risk_line({"score": 75, "reasons": ["FDV/MCap 6.0x"], "alert": True})
    assert line.startswith("🔴 RUG-РИСК: 75/100")
    assert "инсайдерской схемы" in line


def test_compute_rug_risk_missing_cg_detail_only_uses_free_coin_fields():
    coin = _coin(market_cap=1e8, volume_24h=1e6, percent_change_30d=250.0)
    result = rr.compute_rug_risk("XYZ", coin, cg_detail=None)
    assert result["detectors"]["fdv_mcap"]["available"] is False
    assert result["detectors"]["age_listing"]["available"] is False
    assert result["detectors"]["vertical_growth"]["available"] is True
    assert result["detectors"]["vertical_growth"]["points"] == rr.VERTICAL_GROWTH_POINTS
