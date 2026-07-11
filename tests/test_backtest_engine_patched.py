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


def test_isolation_flags_default_both_patches_on(monkeypatch):
    # default call (no flags) -- both patches active DURING the run
    seen = {}

    def _capture(*a, **kw):
        seen["kz"] = bot.get_killzone_status
        seen["rr"] = ta_extra.SR_MIN_RR_TP1
        return {"trades": []}

    monkeypatch.setattr("backtest.engine.run_backtest", _capture)
    ep.run_backtest_patched([])
    assert seen["kz"] is bot.get_killzone_status_shadow
    assert seen["rr"] == 2.0


def test_isolation_flag_killzone_only(monkeypatch):
    orig_rr = ta_extra.SR_MIN_RR_TP1
    seen = {}

    def _capture(*a, **kw):
        seen["kz"] = bot.get_killzone_status
        seen["rr"] = ta_extra.SR_MIN_RR_TP1
        return {"trades": []}

    monkeypatch.setattr("backtest.engine.run_backtest", _capture)
    ep.run_backtest_patched([], apply_killzone_patch=True, apply_rr_gate_patch=False)
    assert seen["kz"] is bot.get_killzone_status_shadow
    assert seen["rr"] == orig_rr  # untouched -- патч 02 выключен


def test_isolation_flag_rr_gate_only(monkeypatch):
    orig_kz = bot.get_killzone_status
    seen = {}

    def _capture(*a, **kw):
        seen["kz"] = bot.get_killzone_status
        seen["rr"] = ta_extra.SR_MIN_RR_TP1
        return {"trades": []}

    monkeypatch.setattr("backtest.engine.run_backtest", _capture)
    ep.run_backtest_patched([], apply_killzone_patch=False, apply_rr_gate_patch=True)
    assert seen["kz"] is orig_kz  # untouched -- патч 01 выключен
    assert seen["rr"] == 2.0


def test_isolation_flags_both_off_leaves_live_values_during_run(monkeypatch):
    orig_kz = bot.get_killzone_status
    orig_rr = ta_extra.SR_MIN_RR_TP1
    seen = {}

    def _capture(*a, **kw):
        seen["kz"] = bot.get_killzone_status
        seen["rr"] = ta_extra.SR_MIN_RR_TP1
        return {"trades": []}

    monkeypatch.setattr("backtest.engine.run_backtest", _capture)
    ep.run_backtest_patched([], apply_killzone_patch=False, apply_rr_gate_patch=False)
    assert seen["kz"] is orig_kz
    assert seen["rr"] == orig_rr


def test_isolation_flags_restored_after_run_regardless_of_combination():
    orig_kz = bot.get_killzone_status
    orig_rr = ta_extra.SR_MIN_RR_TP1
    ep.run_backtest_patched([], apply_killzone_patch=True, apply_rr_gate_patch=False)
    assert bot.get_killzone_status is orig_kz
    assert ta_extra.SR_MIN_RR_TP1 == orig_rr
    ep.run_backtest_patched([], apply_killzone_patch=False, apply_rr_gate_patch=True)
    assert bot.get_killzone_status is orig_kz
    assert ta_extra.SR_MIN_RR_TP1 == orig_rr
