"""
pytest для chart_v4.whale_zone_linewidth()/draw_whale_zones() (Whale Radar Блок 3) --
линейный масштаб толщины проверяется чистой функцией; сам рендер — смоук-тестом
(график строится без исключений и с/без whale_zones даёт валидный PNG).
"""
import chart_v4


def test_whale_zone_linewidth_zero_usd_gives_min():
    assert chart_v4.whale_zone_linewidth(0) == chart_v4.WHALE_MIN_LINEWIDTH


def test_whale_zone_linewidth_saturates_at_ref_usd():
    lw = chart_v4.whale_zone_linewidth(chart_v4.WHALE_LINEWIDTH_REF_USD)
    assert lw == chart_v4.WHALE_MAX_LINEWIDTH


def test_whale_zone_linewidth_beyond_ref_usd_still_capped():
    lw = chart_v4.whale_zone_linewidth(chart_v4.WHALE_LINEWIDTH_REF_USD * 10)
    assert lw == chart_v4.WHALE_MAX_LINEWIDTH


def test_whale_zone_linewidth_midpoint_is_linear():
    lw = chart_v4.whale_zone_linewidth(chart_v4.WHALE_LINEWIDTH_REF_USD / 2)
    expected = (chart_v4.WHALE_MIN_LINEWIDTH + chart_v4.WHALE_MAX_LINEWIDTH) / 2
    assert abs(lw - expected) < 1e-9


def test_whale_zone_linewidth_monotonic():
    lw_small = chart_v4.whale_zone_linewidth(100_000)
    lw_big = chart_v4.whale_zone_linewidth(800_000)
    assert lw_small < lw_big


def _sample_candles(n=40, base=100.0):
    candles = []
    price = base
    for i in range(n):
        o = price
        c = price + (0.5 if i % 2 == 0 else -0.3)
        h = max(o, c) + 0.2
        l = min(o, c) - 0.2
        candles.append({"open": o, "high": h, "low": l, "close": c, "volume": 1000})
        price = c
    return candles


def test_build_trade_chart_v4_renders_with_whale_zones():
    candles = _sample_candles()
    whale_zones = {
        "bid": [{"price_lo": 95.0, "price_hi": 95.5, "mid": 95.25, "total_usd": 500_000.0,
                 "level_count": 3, "age_sec": 120.0}],
        "ask": [{"price_lo": 105.0, "price_hi": 105.5, "mid": 105.25, "total_usd": 1_200_000.0,
                 "level_count": 2, "age_sec": 30.0}],
    }
    png = chart_v4.build_trade_chart_v4(
        "BTCUSDT", candles, "long", entry_levels=[99.0, 98.0, 97.0], sl=95.0,
        tp1=103.0, tp2=106.0, tp3=110.0, rr=2.0, whale_zones=whale_zones,
    )
    assert png is not None
    data = png.getvalue()
    assert len(data) > 0
    assert data[:8] == b"\x89PNG\r\n\x1a\n"  # valid PNG signature


def test_build_trade_chart_v4_renders_without_whale_zones():
    # backward compatibility -- whale_zones omitted entirely, same as before this block
    candles = _sample_candles()
    png = chart_v4.build_trade_chart_v4(
        "BTCUSDT", candles, "long", entry_levels=[99.0, 98.0, 97.0], sl=95.0,
        tp1=103.0, tp2=106.0, tp3=110.0, rr=2.0,
    )
    assert png is not None


def test_draw_whale_zones_noop_on_empty_or_none(monkeypatch):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    # neither call should raise
    chart_v4.draw_whale_zones(ax, None, 40)
    chart_v4.draw_whale_zones(ax, {}, 40)
    chart_v4.draw_whale_zones(ax, {"bid": [], "ask": []}, 40)
    plt.close(fig)
