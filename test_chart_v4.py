"""
Смоук-тесты Chart v4 (chart_v4.py): zones-инвариант + рендер с/без зон.
Без pytest (в проекте его нет, см. requirements.txt) -- запуск напрямую:
    python3 test_chart_v4.py
Падает с AssertionError и ненулевым кодом возврата при любом провале.
"""

import random

import chart_v4
import ta_extra


def _make_candles(seed, n=200, start=100.0, vol=0.015):
    rng = random.Random(seed)
    candles = []
    price = start
    for i in range(n):
        o = price
        c = o * (1 + rng.uniform(-vol, vol))
        h = max(o, c) * (1 + rng.uniform(0, vol * 0.5))
        l = min(o, c) * (1 - rng.uniform(0, vol * 0.5))
        candles.append({"open": o, "high": h, "low": l, "close": c,
                        "vol": rng.uniform(1000, 5000), "timestamp": i})
        price = c
    return candles


def test_zone_invariants():
    """Зоны (chart_v4.prepare_zones_for_chart, поверх ta_extra.find_sr_zones): верх > низ,
    и зона не пересекает цену насквозь -- above-зоны лежат не ниже цены, below-зоны не
    выше (с точностью до clamp в find_sr_zones)."""
    total_checked = 0
    for seed, start in ((1, 100.0), (2, 60000.0), (3, 0.0000123), (4, 3.5)):
        candles = _make_candles(seed, start=start)
        price = candles[-1]["close"]
        zones = ta_extra.find_sr_zones(candles, candles, candles, price)
        prepared = chart_v4.prepare_zones_for_chart(zones, candles_4h=candles)
        for z in prepared:
            bounds = chart_v4._zone_lo_hi(z)
            assert bounds is not None, f"seed={seed}: zone without usable bounds: {z}"
            lo, hi = bounds
            assert hi > lo, f"seed={seed}: zone hi<=lo: {z}"
            if z["side"] == "above":
                assert lo >= price * 0.999, f"seed={seed}: above-zone crosses price fully: {z} price={price}"
            else:
                assert hi <= price * 1.001, f"seed={seed}: below-zone crosses price fully: {z} price={price}"
            total_checked += 1
        # не больше ZONE_MAX_PER_SIDE с каждой стороны
        above_count = sum(1 for z in prepared if z["side"] == "above")
        below_count = sum(1 for z in prepared if z["side"] == "below")
        assert above_count <= chart_v4.ZONE_MAX_PER_SIDE, f"seed={seed}: too many above zones ({above_count})"
        assert below_count <= chart_v4.ZONE_MAX_PER_SIDE, f"seed={seed}: too many below zones ({below_count})"
    assert total_checked > 0, "no zones were ever produced across all seeds -- test is not exercising anything"
    print(f"[OK] test_zone_invariants ({total_checked} zones checked across 4 seeds)")


def test_chart_v4_renders_with_and_without_zones():
    """build_trade_chart_v4 не падает и возвращает непустой PNG-буфер: с зонами (fa_engine-
    подобный путь, уже классифицированные zones), без зон (promo-радар путь, zones=None),
    и с сырыми (не классифицированными) zones + candles_4h (real_full_analysis()-путь)."""
    candles = _make_candles(42, start=50.0)
    price = candles[-1]["close"]
    zones_raw = ta_extra.find_sr_zones(candles, candles, candles, price)

    buf_with_zones = chart_v4.build_trade_chart_v4(
        "TEST", candles, "long", entry_levels=[price * 0.98, price * 0.97, price * 0.96],
        sl=price * 0.94, tp1=price * 1.05, tp2=price * 1.08, tp3=price * 1.12,
        rr=1.8, tf_label="2h", zones=zones_raw, candles_4h=candles)
    assert buf_with_zones is not None and buf_with_zones.getbuffer().nbytes > 0

    buf_no_zones = chart_v4.build_trade_chart_v4(
        "TEST", candles, "short", entry_levels=[price * 1.02], sl=price * 1.06, tp1=price * 0.95)
    assert buf_no_zones is not None and buf_no_zones.getbuffer().nbytes > 0

    classified = {
        "above": ta_extra.classify_klvl_zones(zones_raw["above"], candles),
        "below": ta_extra.classify_klvl_zones(zones_raw["below"], candles),
    }
    buf_classified = chart_v4.build_trade_chart_v4(
        "TEST", candles, "long", entry_levels=[price * 0.98], sl=price * 0.94, tp1=price * 1.05,
        zones=classified)
    assert buf_classified is not None and buf_classified.getbuffer().nbytes > 0
    print("[OK] test_chart_v4_renders_with_and_without_zones")


if __name__ == "__main__":
    test_zone_invariants()
    test_chart_v4_renders_with_and_without_zones()
    print("\nALL TESTS PASSED")
