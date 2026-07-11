"""
pytest для shadow_engine.compute_whale_confluence() (Патч 06, Whale Radar Блок 2) --
чистая функция, читает уже посчитанные POI/whale-зоны, не делает сети/I-O.
"""
import shadow_engine as se


def _klvl_zone(lo, hi, touches=3):
    return {"lo": lo, "hi": hi, "mid": (lo + hi) / 2, "touches": touches, "klvl": True}


def _plain_zone(lo, hi, touches=1):
    return {"lo": lo, "hi": hi, "mid": (lo + hi) / 2, "touches": touches, "klvl": False}


def _whale_zone(lo, hi, usd):
    return {"price_lo": lo, "price_hi": hi, "mid": (lo + hi) / 2, "total_usd": usd, "level_count": 1}


def test_no_confluence_when_no_whale_zones():
    classified = {"below": [_klvl_zone(99.0, 100.0)], "above": []}
    out = se.compute_whale_confluence(classified, {"bid": [], "ask": []})
    assert out["whale_klvl_confluence"] is False
    assert out["whale_klvl_matches"] == []


def test_no_confluence_when_no_klvl_zones_only_plain():
    # a whale zone overlaps a POI zone, but that POI zone isn't K-LVL -> no match
    classified = {"below": [_plain_zone(99.0, 100.0)], "above": []}
    whale_zones = {"bid": [_whale_zone(99.2, 99.8, 500_000.0)], "ask": []}
    out = se.compute_whale_confluence(classified, whale_zones)
    assert out["whale_klvl_confluence"] is False


def test_confluence_detected_below_side_bid():
    classified = {"below": [_klvl_zone(99.0, 100.0)], "above": []}
    whale_zones = {"bid": [_whale_zone(99.2, 99.8, 500_000.0)], "ask": []}
    out = se.compute_whale_confluence(classified, whale_zones)
    assert out["whale_klvl_confluence"] is True
    assert len(out["whale_klvl_matches"]) == 1
    m = out["whale_klvl_matches"][0]
    assert m["poi_side"] == "below"
    assert m["whale_side"] == "bid"
    assert m["whale_usd"] == 500_000.0


def test_confluence_detected_above_side_ask():
    classified = {"below": [], "above": [_klvl_zone(110.0, 111.0)]}
    whale_zones = {"bid": [], "ask": [_whale_zone(110.5, 112.0, 300_000.0)]}
    out = se.compute_whale_confluence(classified, whale_zones)
    assert out["whale_klvl_confluence"] is True
    assert out["whale_klvl_matches"][0]["poi_side"] == "above"


def test_no_confluence_when_zones_dont_overlap():
    classified = {"below": [_klvl_zone(90.0, 91.0)], "above": []}
    whale_zones = {"bid": [_whale_zone(99.0, 99.5, 500_000.0)], "ask": []}
    out = se.compute_whale_confluence(classified, whale_zones)
    assert out["whale_klvl_confluence"] is False


def test_bid_zones_never_checked_against_above_poi():
    # a bid whale zone should not match an 'above' POI zone even if prices coincidentally overlap
    classified = {"below": [], "above": [_klvl_zone(99.0, 100.0)]}
    whale_zones = {"bid": [_whale_zone(99.2, 99.8, 500_000.0)], "ask": []}
    out = se.compute_whale_confluence(classified, whale_zones)
    assert out["whale_klvl_confluence"] is False


def test_multiple_matches_collected():
    classified = {"below": [_klvl_zone(99.0, 100.0), _klvl_zone(95.0, 96.0)], "above": []}
    whale_zones = {"bid": [_whale_zone(99.2, 99.8, 500_000.0), _whale_zone(95.1, 95.9, 700_000.0)], "ask": []}
    out = se.compute_whale_confluence(classified, whale_zones)
    assert out["whale_klvl_confluence"] is True
    assert len(out["whale_klvl_matches"]) == 2


def test_compute_shadow_skips_whale_patch_when_zones_none():
    result = {
        "block11_trade_plan": {"direction": "long", "rr_tp1": 2.0, "entry1": 100.0,
                                "entry3": 98.0, "sl": 95.0, "tp1": 106.0, "tp2": 110.0, "tp3": 115.0},
        "candles_4h": [],
        "block4_poi": {"classified_by_side": {"below": [_klvl_zone(97.0, 99.0)], "above": []}},
    }

    class FakeBotModule:
        def get_killzone_status(self):
            return {"active": {"quality": "A", "name": "London"}}

        def get_killzone_status_shadow(self):
            return {"active": {"quality": "A", "name": "London"}}

    record = se.compute_shadow("BTCUSDT", result, FakeBotModule(), whale_zones=None)
    assert record["whale_klvl_confluence"] is False
    assert record["whale_klvl_matches"] == []
    assert "06-whale-confluence" not in record["patches_affected"]


def test_compute_shadow_flags_whale_patch_when_confluence_found():
    result = {
        "block11_trade_plan": {"direction": "long", "rr_tp1": 2.0, "entry1": 100.0,
                                "entry3": 98.0, "sl": 95.0, "tp1": 106.0, "tp2": 110.0, "tp3": 115.0},
        "candles_4h": [],
        "block4_poi": {"classified_by_side": {"below": [_klvl_zone(97.0, 99.0)], "above": []}},
    }

    class FakeBotModule:
        def get_killzone_status(self):
            return {"active": {"quality": "A", "name": "London"}}

        def get_killzone_status_shadow(self):
            return {"active": {"quality": "A", "name": "London"}}

    whale_zones = {"bid": [_whale_zone(97.5, 98.5, 250_000.0)], "ask": []}
    record = se.compute_shadow("BTCUSDT", result, FakeBotModule(), whale_zones=whale_zones)
    assert record["whale_klvl_confluence"] is True
    assert "06-whale-confluence" in record["patches_affected"]
    assert any("whale" in d for d in record["discrepancy"])


def test_compute_shadow_whale_patch_failure_does_not_break_record(monkeypatch):
    # broken whale_zones input (missing expected keys) -> caught, doesn't crash compute_shadow
    result = {
        "block11_trade_plan": {"direction": "long", "rr_tp1": 2.0, "entry1": 100.0,
                                "entry3": 98.0, "sl": 95.0, "tp1": 106.0, "tp2": 110.0, "tp3": 115.0},
        "candles_4h": [],
        "block4_poi": {"classified_by_side": {"below": [_klvl_zone(97.0, 99.0)], "above": []}},
    }

    class FakeBotModule:
        def get_killzone_status(self):
            return {"active": {"quality": "A", "name": "London"}}

        def get_killzone_status_shadow(self):
            return {"active": {"quality": "A", "name": "London"}}

    broken_whale_zones = {"bid": [{"not_a_valid_key": 1}], "ask": []}
    record = se.compute_shadow("BTCUSDT", result, FakeBotModule(), whale_zones=broken_whale_zones)
    assert record["whale_klvl_confluence"] is False
    assert any("whale confluence calc failed" in d for d in record["discrepancy"])
