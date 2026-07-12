"""
pytest для derivatives_extra.py (Пакет 9 М3: Options Skew + Liquidation Heatmap).
Чистые функции над синтетическими данными, повторяющими форму реальных ответов
Deribit get_book_summary_by_currency / OKX liquidation-orders (проверено живьём
2026-07-12, см. PROGRESS.md).
"""
import derivatives_extra as de


def _opt(name, iv, oi=100, underlying=64000):
    return {"instrument_name": name, "mark_iv": iv, "open_interest": oi,
            "underlying_price": underlying}


# --- compute_options_skew ---

def test_skew_empty_input_na():
    r = de.compute_options_skew([])
    assert r["ok"] is False
    assert r["skew"] is None


def test_skew_no_underlying_price_na():
    r = de.compute_options_skew([{"instrument_name": "BTC-31JUL26-64000-C", "mark_iv": 50}])
    assert r["ok"] is False


def test_skew_picks_highest_oi_expiry():
    items = [
        _opt("BTC-31JUL26-56000-P", 40, oi=1000), _opt("BTC-31JUL26-72000-C", 30, oi=1000),
        _opt("BTC-28AUG26-56000-P", 60, oi=10), _opt("BTC-28AUG26-72000-C", 20, oi=10),
    ]
    r = de.compute_options_skew(items)
    assert r["expiry"] == "31JUL26"  # higher combined OI


def test_skew_positive_when_puts_more_expensive():
    items = [
        _opt("BTC-31JUL26-56000-P", 45, oi=100),   # moneyness 0.875 -- in put band
        _opt("BTC-31JUL26-58000-P", 43, oi=100),   # moneyness 0.906 -- in put band
        _opt("BTC-31JUL26-70000-C", 25, oi=100),   # moneyness 1.094 -- in call band
        _opt("BTC-31JUL26-72000-C", 23, oi=100),   # moneyness 1.125 -- in call band
    ]
    r = de.compute_options_skew(items)
    assert r["ok"] is True
    assert r["skew"] > 0
    assert "путы дороже" in r["note"]


def test_skew_negative_when_calls_more_expensive():
    items = [
        _opt("BTC-31JUL26-56000-P", 20, oi=100),
        _opt("BTC-31JUL26-70000-C", 45, oi=100),
    ]
    r = de.compute_options_skew(items)
    assert r["skew"] < 0
    assert "FOMO" in r["note"]


def test_skew_neutral_near_zero():
    items = [
        _opt("BTC-31JUL26-56000-P", 30, oi=100),
        _opt("BTC-31JUL26-70000-C", 29, oi=100),
    ]
    r = de.compute_options_skew(items)
    assert abs(r["skew"]) <= 2
    assert r["note"] == "нейтрально"


def test_skew_na_when_no_options_in_moneyness_band():
    # strikes way outside the OTM bands -- only near-ATM options present
    items = [_opt("BTC-31JUL26-64000-C", 30, oi=100), _opt("BTC-31JUL26-64000-P", 30, oi=100)]
    r = de.compute_options_skew(items)
    assert r["ok"] is False
    assert "недостаточно" in r["note"]


def test_skew_ignores_malformed_instrument_names():
    items = [
        {"instrument_name": "GARBAGE", "mark_iv": 50, "open_interest": 100, "underlying_price": 64000},
        _opt("BTC-31JUL26-56000-P", 40, oi=100), _opt("BTC-31JUL26-70000-C", 30, oi=100),
    ]
    r = de.compute_options_skew(items)
    assert r["ok"] is True  # malformed entry skipped, rest still computes


# --- compute_liquidation_heatmap ---

def _liq_row(details):
    return {"details": details}


def _liq_event(bk_px, sz, side="sell"):
    return {"bkPx": str(bk_px), "sz": str(sz), "side": side}


def test_heatmap_empty_input_na():
    r = de.compute_liquidation_heatmap([], price_now=64000)
    assert r["ok"] is False
    assert r["retrospective"] is True


def test_heatmap_no_price_na():
    r = de.compute_liquidation_heatmap([_liq_row([_liq_event(64000, 1)])], price_now=0)
    assert r["ok"] is False


def test_heatmap_clusters_by_price_bucket():
    rows = [_liq_row([
        _liq_event(63500, 10),   # ~-0.8% from 64000 -> bucket -1
        _liq_event(63480, 5),    # same bucket
        _liq_event(65200, 20),   # ~+1.9% -> bucket +2
    ])]
    r = de.compute_liquidation_heatmap(rows, price_now=64000, bucket_pct=1.0)
    assert r["ok"] is True
    assert r["retrospective"] is True
    assert len(r["buckets"]) == 2
    # largest bucket (by notional) first
    top = r["buckets"][0]
    assert top["notional_usd"] > 0


def test_heatmap_top_n_limits_output():
    events = [_liq_event(64000 * (1 + i * 0.02), 1) for i in range(10)]
    rows = [_liq_row(events)]
    r = de.compute_liquidation_heatmap(rows, price_now=64000, bucket_pct=1.0, top_n=3)
    assert len(r["buckets"]) <= 3


def test_heatmap_ignores_malformed_events():
    rows = [_liq_row([{"bkPx": "not_a_number", "sz": "1", "side": "sell"}])]
    r = de.compute_liquidation_heatmap(rows, price_now=64000)
    assert r["ok"] is False


def test_heatmap_no_long_short_breakdown_only_aggregate():
    # honest simplification -- see derivatives_extra.py docstring re: side-label
    # ambiguity vs bot.get_liq_data()
    rows = [_liq_row([_liq_event(64000, 1)])]
    r = de.compute_liquidation_heatmap(rows, price_now=64000)
    assert "long_usd" not in r["buckets"][0]
    assert "short_usd" not in r["buckets"][0]
    assert "notional_usd" in r["buckets"][0]


def test_heatmap_price_bounds_around_current():
    rows = [_liq_row([_liq_event(64000, 10)])]  # exactly at price_now -> bucket 0
    r = de.compute_liquidation_heatmap(rows, price_now=64000, bucket_pct=1.0)
    b = r["buckets"][0]
    assert b["price_lo"] < 64000 < b["price_hi"]
