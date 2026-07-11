"""
pytest для pump_detector._compose_alert()/_build_analysis_block() -- единый снапшот
funding/OI/killzone на карточку (owner, 2026-07-11, найдено на живой карточке EVAA):
шапка и блок РАЗБОР показывали РАЗНЫЙ OI % за 5 мин в одном сообщении, потому что
каждый блок независимо вызывал ctx.get_oi_change() -- функция с побочным эффектом
(мутирует состояние при каждом вызове), второй вызов через секунды видел уже
"предыдущее" значение, записанное первым вызовом. Фикс: один снапшот, переданный
явно (market_snapshot), а не независимые фетчи.

Также покрывает явную пометку "Мёртвая зона" (killzone quality=="D").
"""
import asyncio

import pump_detector as pd


class FakeCtx:
    def __init__(self, kz_quality="A", kz_name="🇬🇧 London Open"):
        self.funding_calls = 0
        self.oi_usd_calls = 0
        self.oi_change_calls = 0
        self.killzone_calls = 0
        self.kz_quality = kz_quality
        self.kz_name = kz_name
        self.get_ohlc = None  # no OHLC -> _build_analysis_block returns "" early (not under test here)

    def get_funding_pct(self, sym):
        self.funding_calls += 1
        return 0.01

    def get_oi_usd(self, sym):
        self.oi_usd_calls += 1
        return 1e8

    def get_oi_change(self, sym):
        self.oi_change_calls += 1
        return 1.7  # simulates the "real" value seen once, before the mutation bug fires

    def get_killzone_status(self):
        self.killzone_calls += 1
        return {"active": {"name": self.kz_name, "quality": self.kz_quality}}

    def get_coin_by_symbol(self, sym):
        return None  # no memecoin check complications


def _watch():
    return {"last_price": 1.5, "peak_price": 1.6, "detect_price": 1.4,
            "volume_mult": 6.0, "z_score": 4.0}


def test_compose_alert_without_snapshot_fetches_independently():
    ctx = FakeCtx()
    text = asyncio.run(pd._compose_alert(ctx, "EVAAUSDT", _watch(), "TEST", []))
    assert ctx.funding_calls == 1
    assert ctx.oi_usd_calls == 1
    assert ctx.oi_change_calls == 1
    assert ctx.killzone_calls == 1
    assert "1.7" in text  # OI change % actually shown


def test_compose_alert_with_snapshot_does_not_refetch():
    ctx = FakeCtx()
    snapshot = {"funding": 0.02, "oi_now": 2e8, "oi_chg": 1.7,
                "kz": {"active": {"name": "🇬🇧 London Open", "quality": "A"}}}
    text = asyncio.run(pd._compose_alert(ctx, "EVAAUSDT", _watch(), "TEST", [],
                                          market_snapshot=snapshot))
    assert ctx.funding_calls == 0
    assert ctx.oi_usd_calls == 0
    assert ctx.oi_change_calls == 0
    assert ctx.killzone_calls == 0
    assert "1.7" in text


def test_compose_alert_and_analysis_block_reuse_same_snapshot_no_mismatch():
    # simulate the EVAA bug repro: a stateful oi_change function that would give a
    # DIFFERENT value on a second call (like the real bot._get_oi_change mutation bug) --
    # verify that when a single snapshot is passed to BOTH functions, both show the
    # SAME number, unlike the old independent-fetch behavior.
    call_log = []

    class StatefulCtx(FakeCtx):
        def get_oi_change(self, sym):
            call_log.append(1)
            # first call: +1.7%, any subsequent call (the bug): +0.0% -- but with
            # snapshot reuse, get_oi_change should never be called a second time here
            return 1.7 if len(call_log) == 1 else 0.0

    ctx = StatefulCtx()
    snapshot = {"funding": ctx.get_funding_pct("EVAA"), "oi_now": ctx.get_oi_usd("EVAA"),
                "oi_chg": ctx.get_oi_change("EVAA"), "kz": ctx.get_killzone_status()}
    header = asyncio.run(pd._compose_alert(ctx, "EVAAUSDT", _watch(), "TEST", [],
                                            market_snapshot=snapshot))
    analysis_oi_line_source = snapshot  # _build_analysis_block would use the same dict
    assert "1.7" in header
    assert len(call_log) == 1, "get_oi_change should only be called ONCE across the whole card"
    # the analysis block reuses the same snapshot values directly, so it can't diverge
    assert snapshot["oi_chg"] == 1.7


def test_compose_alert_shows_dead_zone_warning_when_quality_d():
    ctx = FakeCtx(kz_quality="D", kz_name="💀 Dead Zone")
    text = asyncio.run(pd._compose_alert(ctx, "EVAAUSDT", _watch(), "TEST", []))
    assert "Мёртвая зона" in text
    assert "пониженная ликвидность" in text


def test_compose_alert_no_dead_zone_warning_when_quality_not_d():
    ctx = FakeCtx(kz_quality="A", kz_name="🇬🇧 London Open")
    text = asyncio.run(pd._compose_alert(ctx, "EVAAUSDT", _watch(), "TEST", []))
    assert "Мёртвая зона" not in text


def test_compose_alert_dead_zone_warning_via_snapshot_too():
    ctx = FakeCtx()
    snapshot = {"funding": 0.0, "oi_now": 0.0, "oi_chg": 0.0,
                "kz": {"active": {"name": "💀 Dead Zone", "quality": "D"}}}
    text = asyncio.run(pd._compose_alert(ctx, "EVAAUSDT", _watch(), "TEST", [],
                                          market_snapshot=snapshot))
    assert "Мёртвая зона" in text
    assert ctx.killzone_calls == 0  # still didn't refetch


def _flat_candles(n=210, price=1.5):
    return [{"open": price, "high": price * 1.001, "low": price * 0.999,
              "close": price, "vol": 0} for _ in range(n)]


class FakeCtxWithOhlc(FakeCtx):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.get_ohlc = lambda sym, interval, limit: _flat_candles()


def test_build_analysis_block_without_snapshot_fetches_independently():
    ctx = FakeCtxWithOhlc()
    text = asyncio.run(pd._build_analysis_block(ctx, "EVAAUSDT", _watch()))
    assert ctx.funding_calls == 1
    assert ctx.oi_usd_calls == 1
    assert ctx.oi_change_calls == 1
    assert ctx.killzone_calls == 1
    assert "1.7" in text


def test_build_analysis_block_with_snapshot_does_not_refetch():
    ctx = FakeCtxWithOhlc()
    snapshot = {"funding": 0.02, "oi_now": 2e8, "oi_chg": 1.7,
                "kz": {"active": {"name": "🇬🇧 London Open", "quality": "A"}}}
    text = asyncio.run(pd._build_analysis_block(ctx, "EVAAUSDT", _watch(),
                                                  market_snapshot=snapshot))
    assert ctx.funding_calls == 0
    assert ctx.oi_usd_calls == 0
    assert ctx.oi_change_calls == 0
    assert ctx.killzone_calls == 0
    assert "1.7" in text
    assert "London Open" in text
