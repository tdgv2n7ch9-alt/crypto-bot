import time

import whale_radar as wr


def test_apply_orderbook_snapshot_then_delta():
    book = wr.new_book()
    snapshot = {
        "type": "snapshot",
        "data": {
            "b": [["100.0", "1.0"], ["99.5", "2.0"]],
            "a": [["100.5", "1.5"], ["101.0", "3.0"]],
        },
    }
    wr.apply_orderbook_message(book, snapshot)
    assert book["bid"] == {100.0: 1.0, 99.5: 2.0}
    assert book["ask"] == {100.5: 1.5, 101.0: 3.0}

    delta = {
        "type": "delta",
        "data": {
            "b": [["99.5", "0"], ["99.0", "5.0"]],   # remove 99.5, add 99.0
            "a": [["100.5", "2.0"]],                  # resize 100.5
        },
    }
    wr.apply_orderbook_message(book, delta)
    assert book["bid"] == {100.0: 1.0, 99.0: 5.0}
    assert book["ask"] == {100.5: 2.0, 101.0: 3.0}


def test_apply_orderbook_snapshot_replaces_side():
    book = wr.new_book()
    book["bid"] = {50.0: 10.0}
    snapshot = {"type": "snapshot", "data": {"b": [["60.0", "1.0"]]}}
    wr.apply_orderbook_message(book, snapshot)
    assert book["bid"] == {60.0: 1.0}


def test_classify_whale_levels_absolute_threshold():
    # <5 levels -> only absolute threshold applies (median unreliable on sparse book)
    side = {100.0: 1.0, 99.0: 600.0}  # notional: 100, 59400
    whales = wr.classify_whale_levels(side, min_notional=50_000, median_mult=5.0)
    assert 99.0 in whales
    assert 100.0 not in whales


def test_classify_whale_levels_median_relative_threshold():
    # 6 levels -> median applies. Most levels small notional, one huge outlier.
    side = {
        10.0: 100.0,   # 1000
        10.1: 110.0,   # 1111
        10.2: 90.0,    # 918
        10.3: 105.0,   # 1081.5
        10.4: 95.0,    # 988
        10.5: 100000.0,  # 1,050,000 -- clearly a whale
    }
    whales = wr.classify_whale_levels(side, min_notional=50_000, median_mult=5.0)
    assert 10.5 in whales
    assert 10.0 not in whales
    assert len(whales) == 1


def test_classify_whale_levels_empty_side():
    assert wr.classify_whale_levels({}) == {}


def test_diff_whale_levels_appeared_and_disappeared():
    lifetimes = {}
    now = time.time()
    prev = {}
    curr = {100.0: 60_000.0}
    events = wr.diff_whale_levels(prev, curr, now, last_price=99.0, lifetimes=lifetimes)
    assert len(events) == 1
    assert events[0]["event"] == "appeared"
    assert 100.0 in lifetimes

    # now it disappears shortly after, price approached it closely -> spoof suspected
    later = now + 10
    events2 = wr.diff_whale_levels(curr, {}, later, last_price=100.05, lifetimes=lifetimes)
    assert len(events2) == 1
    ev = events2[0]
    assert ev["event"] == "disappeared"
    assert ev["lifetime_sec"] == 10.0
    assert ev["spoof_suspected"] is True
    assert 100.0 not in lifetimes


def test_diff_whale_levels_disappeared_not_spoof_when_slow_and_far():
    lifetimes = {100.0: time.time() - 120}  # lived 120s, well over SPOOF_MAX_LIFETIME_SEC
    now = time.time()
    events = wr.diff_whale_levels({100.0: 60_000.0}, {}, now, last_price=50.0, lifetimes=lifetimes)
    assert events[0]["spoof_suspected"] is False


def test_diff_whale_levels_resized():
    lifetimes = {100.0: time.time()}
    prev = {100.0: 60_000.0}
    curr = {100.0: 90_000.0}
    events = wr.diff_whale_levels(prev, curr, time.time(), last_price=99.0, lifetimes=lifetimes)
    assert len(events) == 1
    assert events[0]["event"] == "resized"
    assert events[0]["notional_usd"] == 90_000.0
    assert events[0]["prev_notional_usd"] == 60_000.0


def test_diff_whale_levels_small_resize_not_reported():
    lifetimes = {100.0: time.time()}
    prev = {100.0: 60_000.0}
    curr = {100.0: 60_100.0}  # <2% change
    events = wr.diff_whale_levels(prev, curr, time.time(), last_price=99.0, lifetimes=lifetimes)
    assert events == []


def test_notional_usd():
    assert wr.notional_usd(10.0, 5.0) == 50.0


def test_make_order_event_distance_pct():
    evt = {"event": "appeared", "notional_usd": 60_000.0}
    out = wr.make_order_event("BTCUSDT", "bid", 100.0, evt, last_price=110.0)
    assert out["type"] == "whale_order"
    assert out["symbol"] == "BTCUSDT"
    assert out["side"] == "bid"
    assert out["size_usd"] == 60_000.0
    # price 100 is below last_price 110 -> distance negative ~ -9.09%
    assert out["distance_pct"] < 0


def test_make_trade_event():
    out = wr.make_trade_event("ethusdt", "Buy", 1800.0, 55_000.0, ts_ms=1_700_000_000_000)
    assert out["type"] == "whale_trade"
    assert out["symbol"] == "ETHUSDT"
    assert out["side"] == "Buy"
    assert out["size_usd"] == 55_000.0
    assert out["ts"] == 1_700_000_000.0


def test_is_whale_trade_absolute_only_when_sparse_window():
    # <TRADE_MEDIAN_MIN_COUNT trades in window -> only absolute threshold applies
    window = [1000.0, 1200.0, 900.0]
    assert wr.is_whale_trade(window, 80_000.0, min_notional=75_000, median_mult=5.0, min_count=10) is True
    assert wr.is_whale_trade(window, 50_000.0, min_notional=75_000, median_mult=5.0, min_count=10) is False


def test_is_whale_trade_relative_threshold_with_full_window():
    # 10 trades, median ~1000 -> relative threshold (5x) = 5000, well below absolute 75K,
    # so absolute floor wins (max(75000, 5000)) -- notional must clear 75K regardless.
    window = [900.0, 950.0, 1000.0, 1050.0, 1100.0, 980.0, 1020.0, 990.0, 1010.0, 970.0]
    assert wr.is_whale_trade(window, 74_000.0, min_notional=75_000, median_mult=5.0, min_count=10) is False
    assert wr.is_whale_trade(window, 76_000.0, min_notional=75_000, median_mult=5.0, min_count=10) is True


def test_is_whale_trade_relative_threshold_dominates_on_large_median():
    # median large enough that 5x median > 75K absolute floor
    window = [20_000.0] * 10  # median 20,000 -> 5x = 100,000 > 75K absolute
    assert wr.is_whale_trade(window, 90_000.0, min_notional=75_000, median_mult=5.0, min_count=10) is False
    assert wr.is_whale_trade(window, 110_000.0, min_notional=75_000, median_mult=5.0, min_count=10) is True


def test_whale_radar_state_record_trade_uses_window_before_append():
    state = wr.WhaleRadarState()
    # fill window with small trades (won't itself count toward its own median)
    for _ in range(wr.TRADE_MEDIAN_MIN_COUNT):
        assert state.record_trade("btcusdt", 1000.0) is False  # below absolute floor
    # a genuinely large trade should be flagged whale (clears absolute + relative)
    assert state.record_trade("btcusdt", 200_000.0) is True
    # window now includes the 200K trade too, but it shouldn't have counted toward
    # its OWN classification (checked above) -- verify window length is bounded
    assert len(state.trade_windows["btcusdt"]) == wr.TRADE_MEDIAN_MIN_COUNT + 1


def test_whale_radar_state_scan_symbol_detects_and_tracks():
    state = wr.WhaleRadarState()
    state.ensure_symbol("btcusdt")
    state.last_price["btcusdt"] = 100.0
    events_log = []

    # sparse book with one whale bid (99.0 * 2000 = 198,000 -- clears the $100K floor)
    wr.apply_orderbook_message(state.books["btcusdt"], {
        "type": "snapshot",
        "data": {"b": [["99.0", "2000.0"]], "a": [["101.0", "1.0"]]},
    })
    events = state.scan_symbol("btcusdt", time.time())
    assert any(e["event"] == "appeared" and e["side"] == "bid" for e in events)

    # whale disappears
    wr.apply_orderbook_message(state.books["btcusdt"], {
        "type": "delta",
        "data": {"b": [["99.0", "0"]]},
    })
    events2 = state.scan_symbol("btcusdt", time.time())
    assert any(e["event"] == "disappeared" and e["side"] == "bid" for e in events2)


def test_fetch_top_symbols_filters_and_sorts(monkeypatch):
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"result": {"list": [
                {"symbol": "BTCUSDT", "turnover24h": "1000000"},
                {"symbol": "ETHUSDT", "turnover24h": "2000000"},
                {"symbol": "USDCUSDT", "turnover24h": "5000000"},  # stable base, excluded
                {"symbol": "BTCUSD", "turnover24h": "3000000"},     # not USDT-quoted, excluded
                {"symbol": "SOLUSDT", "turnover24h": "0"},          # zero turnover, excluded
            ]}}

    def fake_get(url, params=None, timeout=None):
        return FakeResp()

    monkeypatch.setattr(wr.requests, "get", fake_get)
    result = wr.fetch_top_symbols(n=10)
    assert result == ["ethusdt", "btcusdt"]
