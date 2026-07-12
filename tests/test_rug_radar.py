"""
pytest для rug_radar.py (Пакет 9, модуль RUG-RADAR -- кейс LAB, METHODOLOGY_CORE.md
§21). Только чистые функции детекторов + compute_rug_risk() на синтетических
данных -- без сети (fetch_coingecko_detail не тестируется здесь, тривиальный
best-effort HTTP-враппер по образцу get_binance_alltime_low()).
"""
import pytest

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


# --- detect_exchange_transfers (Пакет 10: масштабирование от transfer/MCap, владелец 2026-07-12) ---

def test_exchange_transfers_na_without_provider():
    r = rr.detect_exchange_transfers(None, market_cap=1e8)
    assert r["available"] is False
    assert r["points"] == 0


def test_exchange_transfers_na_without_market_cap():
    # transfer data present but no MCap to compute ratio -- honestly н/д, not a guess
    r = rr.detect_exchange_transfers({"large_transfer_usd_recent": 18_300_000}, market_cap=None)
    assert r["available"] is False
    assert r["points"] == 0


def test_exchange_transfers_no_trigger_at_zero():
    r = rr.detect_exchange_transfers({"large_transfer_usd_recent": 0}, market_cap=1e8)
    assert r["points"] == 0


def test_exchange_transfers_no_trigger_below_warn_ratio():
    # $1M transfer on $1B MCap = 0.1% -- well below TRANSFER_MCAP_RATIO_WARN_PCT
    r = rr.detect_exchange_transfers({"large_transfer_usd_recent": 1_000_000}, market_cap=1e9)
    assert r["points"] == 0
    assert r["ratio_pct"] < rr.TRANSFER_MCAP_RATIO_WARN_PCT


def test_exchange_transfers_scales_between_warn_and_max_ratio():
    lo = rr.detect_exchange_transfers({"large_transfer_usd_recent": 3_000_000}, market_cap=1e8)   # 3%
    hi = rr.detect_exchange_transfers({"large_transfer_usd_recent": 20_000_000}, market_cap=1e8)  # 20%
    assert 0 < lo["points"] < hi["points"] < rr.EXCHANGE_TRANSFER_POINTS_MAX


def test_exchange_transfers_caps_at_max_ratio_and_beyond():
    at_cap = rr.detect_exchange_transfers({"large_transfer_usd_recent": 25_000_000}, market_cap=1e8)   # exactly 25%
    beyond = rr.detect_exchange_transfers({"large_transfer_usd_recent": 500_000_000}, market_cap=1e8)  # 500%
    assert at_cap["points"] == rr.EXCHANGE_TRANSFER_POINTS_MAX
    assert beyond["points"] == rr.EXCHANGE_TRANSFER_POINTS_MAX  # clipped, not runaway


def test_exchange_transfers_lab_reference_case_08_04_2026_hits_max():
    # Эталонный тест калибровки (владелец, 2026-07-12): подтверждённый перевод
    # 100M токенов LAB на Bitget 08.04.2026 -- $39.25M на MCap $29.95M = 131%.
    # Обязан давать МАКСИМУМ баллов детектора.
    r = rr.detect_exchange_transfers({"large_transfer_usd_recent": 39_246_315.32}, market_cap=29_954_773.55)
    assert r["ratio_pct"] > 100
    assert r["points"] == rr.EXCHANGE_TRANSFER_POINTS_MAX


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


# --- Пакет 10: калибровка exchange_transfers, эталонный тест владельца ---
# "кейс LAB ($39.25M = 131% MCap = максимум баллов) -- после калибровки скор LAB
# на 08.04 обязан пересечь WARN-порог. Показать до/после на LAB и на 3 здоровых
# токенах (ложных срабатываний быть не должно)."

def _lab_08_04_2026():
    # Реальные рыночные данные на дату подтверждённого перевода (см. PROGRESS.md
    # "Пакет 9 М4"): цена $0.3925, MCap $29.95M, объём $25.51M/24ч, FDV≈$392.5M.
    coin = {"quote": {"USDT": {
        "market_cap": 29_954_773.55, "volume_24h": 25_512_042.38,
        "percent_change_30d": 159.1,
    }}}
    cg_detail = {
        "market_data": {"fully_diluted_valuation": {"usd": 392_463_153.17},
                         "atl_date": {"usd": "2025-12-02T03:30:39.644Z"}},
        "genesis_date": None,
        "tickers": [{"market": {"name": f"Ex{i}"}} for i in range(21)],
    }
    transfer_data = {"large_transfer_usd_recent": 39_246_315.32}  # 100M токенов x $0.3925
    return coin, cg_detail, transfer_data


def test_lab_08_04_2026_before_recalibration_transfer_data_missing_stays_below_warn():
    # "До" -- без данных о переводе (как было бы без Etherscan-ключа) -- 20/55,
    # ниже WARN. Подтверждает, что рост скора "после" целиком из transfer-детектора.
    coin, cg_detail, _ = _lab_08_04_2026()
    before = rr.compute_rug_risk("LAB", coin, cg_detail=cg_detail, transfer_data=None)
    assert before["score"] == 20
    assert before["warn"] is False


def test_lab_08_04_2026_after_recalibration_crosses_warn_threshold():
    # "После" -- эталонный тест владельца: с реальным подтверждённым переводом
    # скор ОБЯЗАН пересечь WARN(40).
    coin, cg_detail, transfer_data = _lab_08_04_2026()
    after = rr.compute_rug_risk("LAB", coin, cg_detail=cg_detail, transfer_data=transfer_data)
    assert after["detectors"]["exchange_transfers"]["points"] == rr.EXCHANGE_TRANSFER_POINTS_MAX
    assert after["score"] == 45  # 20 (fdv/mcap) + 0 + 0 + 25 (transfer, максимум)
    assert after["warn"] is True
    assert after["score"] >= rr.RUG_RISK_WARN_THRESHOLD


@pytest.mark.parametrize("symbol,mcap,transfer_usd", [
    ("BTC", 1_283_741_231_638, 50_000_000),   # $50M transfer -- routine exchange flow for BTC
    ("ETH", 400_000_000_000, 20_000_000),
    ("SOL", 44_903_632_193, 10_000_000),
])
def test_healthy_large_caps_no_false_positive_after_recalibration(symbol, mcap, transfer_usd):
    # Ключевое доказательство калибровки: тот же ($ >100K) старый бинарный порог
    # раньше давал ЭТИМ ЖЕ токенам максимум баллов (ложное срабатывание) --
    # теперь, отнормировав на MCap, ни один не пересекает WARN.
    coin = {"quote": {"USDT": {"market_cap": mcap, "volume_24h": mcap * 0.05, "percent_change_30d": 5.0}}}
    cg_detail = {
        "market_data": {"fully_diluted_valuation": {"usd": mcap * 1.05},
                         "atl_date": {"usd": "2015-01-01T00:00:00.000Z"}},
        "genesis_date": "2015-01-01",
        "tickers": [{"market": {"name": f"Ex{i}"}} for i in range(20)],
    }
    transfer_data = {"large_transfer_usd_recent": transfer_usd}
    result = rr.compute_rug_risk(symbol, coin, cg_detail=cg_detail, transfer_data=transfer_data)
    assert result["detectors"]["exchange_transfers"]["points"] == 0
    assert result["score"] == 0
    assert result["warn"] is False
