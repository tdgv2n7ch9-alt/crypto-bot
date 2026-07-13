"""
pytest для Пакета 16 (tools/summer_spot_plan.py) -- летний спот-ранжир. Покрывает
чистые/детерминированные функции: скоринг, назначение ярусов, DeFiLlama TVL/revenue
матчинг, лестницы владельца из journal/spot_plans.json, фильтр вселенной (стейблы/
wrapped), бонус зоны Королева. Сетевые вызовы (CoinGecko/Binance/DeFiLlama) везде
замоканы -- этот файл не делает реальных HTTP-запросов.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot
import summer_spot_plan as ssp


def _coin(symbol="TEST", price=10.0, market_cap=1_000_000_000, volume_24h=50_000_000,
          fdv=1_100_000_000, ath=100.0, ath_change_pct=-90.0,
          ch_1h=0.5, ch_24h=1.0, ch_7d=5.0, ch_30d=-10.0, name=None):
    return {
        "symbol": symbol, "slug": symbol.lower(), "name": name or symbol,
        "rank": 50, "price": price, "market_cap": market_cap, "fdv": fdv,
        "volume_24h": volume_24h, "ath": ath, "ath_change_pct": ath_change_pct,
        "ch_1h": ch_1h, "ch_24h": ch_24h, "ch_7d": ch_7d, "ch_30d": ch_30d,
    }


# ── score_coin() ─────────────────────────────────────────────────────────

def test_score_coin_base_is_50_when_all_data_missing():
    coin = _coin(fdv=None, ath_change_pct=None, ch_7d=None, ch_30d=None, market_cap=0)
    result = ssp.score_coin(coin, {"ok": False}, {}, {"score": 0}, korolev_bonus=False)
    assert result["score"] == 50.0


def test_score_coin_rewards_moderate_ath_drawdown_and_early_bounce():
    """Сценарий владельца: просадка 40-90% от ATH + ранний отскок 7д (0..12%) --
    ДОЛЖНО давать положительные дельты (contrarian-формула для дна, не momentum)."""
    coin = _coin(ath_change_pct=-70.0, ch_7d=5.0, ch_30d=-15.0)
    result = ssp.score_coin(coin, {"ok": False}, {}, {"score": 0}, korolev_bonus=False)
    assert result["score"] > 50.0
    labels = [f[0] for f in result["factors"]]
    assert any("зона интереса" in l for l in labels)
    assert any("раннего отскока" in l for l in labels)
    assert any("коррекционной фазе" in l for l in labels)


def test_score_coin_penalizes_near_ath_and_already_pumped():
    """Цена у хаёв (просадка <15%) + уже сильный импульс 7д (>12%) -- это НЕ дно,
    формула должна штрафовать, не поощрять."""
    coin = _coin(ath_change_pct=-5.0, ch_7d=25.0, ch_30d=20.0)
    result = ssp.score_coin(coin, {"ok": False}, {}, {"score": 0}, korolev_bonus=False)
    assert result["score"] < 50.0


def test_score_coin_vrvp_bonus_when_price_inside_high_volume_zone():
    coin = _coin(ath_change_pct=None, ch_7d=None, ch_30d=None)
    profile_inside = {"ok": True, "ch_90d": None, "vrvp": {"price_inside": True, "price_below_pct": 0}}
    profile_outside = {"ok": True, "ch_90d": None, "vrvp": {"price_inside": False, "price_below_pct": 0}}
    r_inside = ssp.score_coin(coin, profile_inside, {}, {"score": 0}, korolev_bonus=False)
    r_outside = ssp.score_coin(coin, profile_outside, {}, {"score": 0}, korolev_bonus=False)
    assert r_inside["score"] > r_outside["score"]


def test_score_coin_rug_score_penalty_proportional():
    coin = _coin(ath_change_pct=None, ch_7d=None, ch_30d=None)
    r_clean = ssp.score_coin(coin, {"ok": False}, {}, {"score": 0}, korolev_bonus=False)
    r_risky = ssp.score_coin(coin, {"ok": False}, {}, {"score": 35}, korolev_bonus=False)
    assert r_risky["score"] < r_clean["score"]
    assert round(r_clean["score"] - r_risky["score"], 2) == round(35 * 0.3, 2)


def test_score_coin_fdv_mcap_penalty_for_large_unlock_overhang():
    """SUI-подобный кейс: большой FDV/MCap (навес будущей эмиссии) -- штраф."""
    coin_low = _coin(market_cap=1_000_000_000, fdv=1_100_000_000, ath_change_pct=None, ch_7d=None, ch_30d=None)
    coin_high = _coin(market_cap=1_000_000_000, fdv=4_000_000_000, ath_change_pct=None, ch_7d=None, ch_30d=None)
    r_low = ssp.score_coin(coin_low, {"ok": False}, {}, {"score": 0}, korolev_bonus=False)
    r_high = ssp.score_coin(coin_high, {"ok": False}, {}, {"score": 0}, korolev_bonus=False)
    assert r_low["score"] > r_high["score"]


def test_score_coin_korolev_bonus_applied():
    coin = _coin(ath_change_pct=None, ch_7d=None, ch_30d=None)
    r_no = ssp.score_coin(coin, {"ok": False}, {}, {"score": 0}, korolev_bonus=False)
    r_yes = ssp.score_coin(coin, {"ok": False}, {}, {"score": 0}, korolev_bonus=True)
    assert r_yes["score"] - r_no["score"] == 6.0


def test_score_coin_missing_data_produces_na_factor_not_fabricated_number():
    """Честность: н/д-данные не подставляют выдуманное число, идут нулевой дельтой
    с явной пометкой "н/д" в факторе."""
    coin = _coin(ath_change_pct=None, ch_7d=None, ch_30d=None)
    result = ssp.score_coin(coin, {"ok": False}, {}, {"score": 0}, korolev_bonus=False)
    na_labels = [f[0] for f in result["factors"] if f[1] == 0]
    assert any("ATH% -- н/д" in l for l in na_labels)
    assert any("7д импульс -- н/д" in l for l in na_labels)
    assert any("90д импульс -- н/д" in l for l in na_labels)


# ── assign_tier() ────────────────────────────────────────────────────────

def test_assign_tier_majors_quality_beta_named_and_default():
    assert ssp.assign_tier("BTC") == "majors"
    assert ssp.assign_tier("ETH") == "majors"
    assert ssp.assign_tier("SOL") == "majors"
    assert ssp.assign_tier("AAVE") == "quality"
    assert ssp.assign_tier("UNI") == "quality"
    assert ssp.assign_tier("LINK") == "quality"
    assert ssp.assign_tier("MORPHO") == "quality"
    assert ssp.assign_tier("ENA") == "quality"
    assert ssp.assign_tier("SUI") == "beta"
    assert ssp.assign_tier("AVAX") == "beta"
    assert ssp.assign_tier("WLD") == "beta"
    assert ssp.assign_tier("JASMY") == "beta"
    assert ssp.assign_tier("RANDOMCOIN") == "прочие"


# ── build_tvl_revenue_map() ──────────────────────────────────────────────

def test_tvl_revenue_map_excludes_bridge_category():
    """Живой кейс из разведки: AVAX символ матчится с 'Avalanche Core Bridge' --
    ЭТО НЕ TVL монеты AVAX, категория Bridge обязана исключаться."""
    universe = [_coin(symbol="AVAX", name="Avalanche")]
    protocols = [
        {"symbol": "AVAX", "name": "Avalanche Core Bridge", "tvl": 133_000_000, "category": "Bridge"},
    ]
    result = ssp.build_tvl_revenue_map(universe, protocols, [])
    assert result["AVAX"]["tvl_usd"] is None
    assert result["AVAX"]["applicable"] is False


def test_tvl_revenue_map_sums_matching_protocol_versions():
    """AAVE V2+V3 -- обе версии суммируются в один TVL (реальный кейс из разведки)."""
    universe = [_coin(symbol="AAVE", name="Aave")]
    protocols = [
        {"symbol": "AAVE", "name": "Aave V3", "tvl": 13_000_000_000, "category": "Lending"},
        {"symbol": "AAVE", "name": "Aave V2", "tvl": 107_000_000, "category": "Lending"},
    ]
    result = ssp.build_tvl_revenue_map(universe, protocols, [])
    assert result["AAVE"]["tvl_usd"] == 13_000_000_000 + 107_000_000
    assert result["AAVE"]["applicable"] is True


def test_tvl_revenue_map_fees_matched_by_name_not_symbol():
    """DeFiLlama fees API не отдаёт symbol (см. разведку) -- матчинг по name."""
    universe = [_coin(symbol="AAVE", name="Aave")]
    fees = [{"name": "Aave V3", "total30d": 3_700_000, "category": "Lending"}]
    result = ssp.build_tvl_revenue_map(universe, [], fees)
    assert result["AAVE"]["revenue_30d_usd"] == 3_700_000


def test_tvl_revenue_map_not_applicable_for_l1_without_defi_protocol():
    universe = [_coin(symbol="XRPX", name="NotARealProtocolXyz")]
    result = ssp.build_tvl_revenue_map(universe, [], [])
    assert result["XRPX"]["applicable"] is False
    assert result["XRPX"]["tvl_usd"] is None
    assert result["XRPX"]["revenue_30d_usd"] is None


# ── has_korolev_long_zone() ──────────────────────────────────────────────

def test_korolev_zone_bonus_only_for_long_side():
    watch_zones = {
        "BTCUSDT": [{"side": "LONG", "lo": 1, "hi": 2}, {"side": "SHORT", "lo": 3, "hi": 4}],
        "ETHUSDT": [{"side": "SHORT", "lo": 1, "hi": 2}],
    }
    assert ssp.has_korolev_long_zone("BTC", watch_zones) is True
    assert ssp.has_korolev_long_zone("ETH", watch_zones) is False
    assert ssp.has_korolev_long_zone("NOPE", watch_zones) is False


# ── build_ladder_for_coin() -- owner-provided plans используются как есть ──

def test_ladder_avax_sui_aave_used_verbatim_from_spot_plans_json():
    """Живая проверка на реальном journal/spot_plans.json -- три плана владельца
    (AVAX/SUI/AAVE) должны прийти БЕЗ пересчёта, с правильным источником."""
    spot_plans = ssp.load_spot_plans()
    assert "AVAXUSDT" in spot_plans
    assert "SUIUSDT" in spot_plans
    assert "AAVEUSDT" in spot_plans

    avax = ssp.build_ladder_for_coin(_coin(symbol="AVAX", price=3.5), {"ok": False}, spot_plans)
    assert avax["source"] == "владелец (journal/spot_plans.json)"
    assert avax["sl"] == 2.70
    assert [l["price"] for l in avax["ladder"]] == [4.10, 3.45, 2.90]
    assert avax["invalidation"] == "закрытие W1 ниже 2.70"

    sui = ssp.build_ladder_for_coin(_coin(symbol="SUI", price=0.45), {"ok": False}, spot_plans)
    assert sui["sl"] == 0.354
    assert [l["price"] for l in sui["ladder"]] == [0.55, 0.46, 0.39]
    assert "разлоки" in sui["note"]

    aave = ssp.build_ladder_for_coin(_coin(symbol="AAVE", price=95.0), {"ok": False}, spot_plans)
    assert aave["sl"] == 58.90
    assert aave["tp"] == 109.7
    assert [l["price"] for l in aave["ladder"]] == [63.57, 62.00, 60.43]
    assert aave["invalidation"] == "закрытие W1 ниже 58.90"
    assert "не догонять рынок" in aave["note"]


def test_ladder_falls_back_to_na_when_no_binance_data_and_no_owner_plan():
    result = ssp.build_ladder_for_coin(_coin(symbol="NOPLAN", price=1.0), {"ok": False}, {})
    assert result["source"] == "н/д"
    assert result["ladder"] is None
    assert result["sl"] is None
    assert result["tp"] is None


# ── fetch_universe() -- фильтр стейблов/wrapped ─────────────────────────

def test_fetch_universe_excludes_stablecoins_and_wrapped(monkeypatch):
    fake_response = [
        {"id": "bitcoin", "symbol": "btc", "name": "Bitcoin", "market_cap_rank": 1,
         "current_price": 60000, "market_cap": 1_000_000_000_000,
         "fully_diluted_valuation": 1_000_000_000_000, "total_volume": 1_000_000,
         "ath": 70000, "ath_change_percentage": -10,
         "price_change_percentage_1h_in_currency": 0.1,
         "price_change_percentage_24h_in_currency": 1.0,
         "price_change_percentage_7d_in_currency": 2.0,
         "price_change_percentage_30d_in_currency": -5.0},
        {"id": "tether", "symbol": "usdt", "name": "Tether", "market_cap_rank": 3,
         "current_price": 1.0, "market_cap": 100_000_000_000,
         "fully_diluted_valuation": 100_000_000_000, "total_volume": 1_000_000,
         "ath": 1.1, "ath_change_percentage": -1,
         "price_change_percentage_1h_in_currency": 0,
         "price_change_percentage_24h_in_currency": 0,
         "price_change_percentage_7d_in_currency": 0,
         "price_change_percentage_30d_in_currency": 0},
        {"id": "wrapped-bitcoin", "symbol": "wbtc", "name": "Wrapped Bitcoin", "market_cap_rank": 20,
         "current_price": 60000, "market_cap": 10_000_000_000,
         "fully_diluted_valuation": 10_000_000_000, "total_volume": 1_000_000,
         "ath": 70000, "ath_change_percentage": -10,
         "price_change_percentage_1h_in_currency": 0,
         "price_change_percentage_24h_in_currency": 0,
         "price_change_percentage_7d_in_currency": 0,
         "price_change_percentage_30d_in_currency": 0},
    ]
    monkeypatch.setattr(bot, "_cg_get", lambda url, params=None, timeout=10: fake_response)
    result = ssp.fetch_universe(top_n=10)
    symbols = [c["symbol"] for c in result]
    assert symbols == ["BTC"]
    assert "USDT" not in symbols
    assert "WBTC" not in symbols


def test_fetch_universe_honest_empty_on_coingecko_failure(monkeypatch):
    def _raise(*a, **kw):
        raise Exception("429 Too Many Requests")
    monkeypatch.setattr(bot, "_cg_get", _raise)
    result = ssp.fetch_universe(top_n=10)
    assert result == []


# ── journal/spot_plans.json -- целостность файла ─────────────────────────

def test_spot_plans_json_is_valid_and_has_required_fields():
    path = ssp.SPOT_PLANS_PATH
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    for key in ("AVAXUSDT", "SUIUSDT", "AAVEUSDT"):
        assert key in data
        plan = data[key]
        for field in ("tier", "zone", "ladder", "sl", "invalidation"):
            assert field in plan, f"{key} missing {field}"
        assert plan["zone"]["lo"] < plan["zone"]["hi"]
        assert len(plan["ladder"]) == 3
        assert sum(l["pct"] for l in plan["ladder"]) == 100
