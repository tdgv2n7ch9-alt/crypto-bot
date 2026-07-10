"""backtest/engine_patched.py -- проверяет, что патчи корректно откатываются после
прогона (не остаются висеть на живых bot.py/ta_extra.py объектах)."""
import bot
import ta_extra

import backtest.engine_patched as ep


def test_patches_restored_after_run_even_on_empty_symbols():
    orig_kz = bot.get_killzone_status
    orig_rr = ta_extra.SR_MIN_RR_TP1

    ep.run_backtest_patched([])  # пустой список символов -- быстрый прогон

    assert bot.get_killzone_status is orig_kz
    assert ta_extra.SR_MIN_RR_TP1 == orig_rr


def test_patches_restored_even_if_scan_raises(monkeypatch):
    orig_kz = bot.get_killzone_status
    orig_rr = ta_extra.SR_MIN_RR_TP1

    def _boom(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr("backtest.engine.run_backtest", _boom)
    try:
        ep.run_backtest_patched(["FAKE"])
    except RuntimeError:
        pass

    assert bot.get_killzone_status is orig_kz
    assert ta_extra.SR_MIN_RR_TP1 == orig_rr
