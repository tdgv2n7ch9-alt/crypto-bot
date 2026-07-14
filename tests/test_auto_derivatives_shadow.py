"""
pytest для Фаза B Derivatives shadow-continuation, инкремент 1 (владелец,
задание после ночи 15->16.07: "текущий инкремент Фазы B (CVD + premium
shadow) НЕ прерывать") -- CVD + Perp/Spot премия для КАЖДОГО AUTO-кандидата,
за флагом shadow_engine.DERIV_AUTO_SHADOW_ENABLED (по умолчанию False).
Три вещи проверяются отдельно: (1) _build_auto_derivatives_shadow_record() --
сборка записи на готовых данных, без сети (bot_module/premium подаются
фейками); (2) log_auto_derivatives_shadow_async() -- флаг-гейт гарантированно
no-op при DERIV_AUTO_SHADOW_ENABLED=False (ни CVD/premium-вызова, ни записи,
ни sync); (3) при True -- premium запрашивается через run_in_executor (не
блокирует напрямую), запись формируется и пишется.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shadow_engine as se


def _run(coro):
    return asyncio.run(coro)


def _analysis(is_long=True):
    return {"is_long": is_long}


class _FakeBotModule:
    """Фейковый bot_module -- get_cvd_summary()/get_perp_spot_premium() без сети,
    тот же контракт, что настоящие bot.get_cvd_summary()/get_perp_spot_premium()."""

    def __init__(self, cvd_1h=0, cvd_4h=0, premium_ok=True, premium_pct=0.05):
        self._cvd = {"cvd_1h": cvd_1h, "cvd_4h": cvd_4h,
                     "direction_1h": "накопление лонгов" if cvd_1h > 0 else "нейтрально"}
        self._premium = {"ok": premium_ok, "perp": 100.0, "spot": 99.95,
                          "premium_pct": premium_pct, "signal": "норма"}
        self.cvd_calls = []
        self.premium_calls = []

    def get_cvd_summary(self, symbol):
        self.cvd_calls.append(symbol)
        return self._cvd

    def get_perp_spot_premium(self, symbol):
        self.premium_calls.append(symbol)
        return self._premium


# ── shadow_engine._build_auto_derivatives_shadow_record() ───────────────────

def test_build_record_basic_fields():
    bot_mod = _FakeBotModule(cvd_1h=15000, cvd_4h=42000, premium_pct=0.12)
    a = _analysis(is_long=True)
    rec = se._build_auto_derivatives_shadow_record(
        "BTC", a, promoted_live=True, bot_module=bot_mod, premium=bot_mod._premium)
    assert rec["type"] == "auto_derivatives_shadow"
    assert rec["symbol"] == "BTC"
    assert rec["direction"] == "long"
    assert rec["promoted_live"] is True
    assert rec["cvd_1h"] == 15000
    assert rec["cvd_4h"] == 42000
    assert rec["premium_ok"] is True
    assert rec["premium_pct"] == 0.12


def test_build_record_calls_cvd_with_usdt_suffix():
    bot_mod = _FakeBotModule()
    se._build_auto_derivatives_shadow_record(
        "ETH", _analysis(), promoted_live=False, bot_module=bot_mod, premium=bot_mod._premium)
    assert bot_mod.cvd_calls == ["ETHUSDT"]


def test_build_record_cvd_aligned_long_positive_cvd():
    bot_mod = _FakeBotModule(cvd_1h=5000)
    rec = se._build_auto_derivatives_shadow_record(
        "BTC", _analysis(is_long=True), promoted_live=True, bot_module=bot_mod, premium=bot_mod._premium)
    assert rec["cvd_aligned_1h"] is True
    assert rec["cvd_opposed_1h"] is False


def test_build_record_cvd_opposed_long_negative_cvd():
    bot_mod = _FakeBotModule(cvd_1h=-5000)
    rec = se._build_auto_derivatives_shadow_record(
        "BTC", _analysis(is_long=True), promoted_live=True, bot_module=bot_mod, premium=bot_mod._premium)
    assert rec["cvd_aligned_1h"] is False
    assert rec["cvd_opposed_1h"] is True


def test_build_record_cvd_neutral_neither_aligned_nor_opposed():
    bot_mod = _FakeBotModule(cvd_1h=0)
    rec = se._build_auto_derivatives_shadow_record(
        "BTC", _analysis(is_long=True), promoted_live=True, bot_module=bot_mod, premium=bot_mod._premium)
    assert rec["cvd_aligned_1h"] is False
    assert rec["cvd_opposed_1h"] is False


def test_build_record_short_direction_alignment():
    bot_mod = _FakeBotModule(cvd_1h=-3000)
    rec = se._build_auto_derivatives_shadow_record(
        "SOL", _analysis(is_long=False), promoted_live=False, bot_module=bot_mod, premium=bot_mod._premium)
    assert rec["direction"] == "short"
    assert rec["cvd_aligned_1h"] is True  # нетто-продажи ЗА short-направление


def test_build_record_premium_not_ok_is_none_not_zero():
    bot_mod = _FakeBotModule(premium_ok=False, premium_pct=999.0)
    rec = se._build_auto_derivatives_shadow_record(
        "XYZ", _analysis(), promoted_live=True, bot_module=bot_mod, premium=bot_mod._premium)
    assert rec["premium_ok"] is False
    assert rec["premium_pct"] is None  # честное "н/д", не число из недоступного источника


# ── shadow_engine.log_auto_derivatives_shadow_async() -- флаг-гейт ──────────

def test_deriv_auto_shadow_disabled_by_default_is_true():
    assert se.DERIV_AUTO_SHADOW_ENABLED is False


def test_log_auto_derivatives_shadow_noop_when_disabled(monkeypatch):
    write_calls = []
    bot_mod = _FakeBotModule()
    monkeypatch.setattr(se, "DERIV_AUTO_SHADOW_ENABLED", False)
    monkeypatch.setattr(se, "_write_local", lambda record: write_calls.append(record) or True)
    result = _run(se.log_auto_derivatives_shadow_async(
        "BTC", _analysis(), promoted_live=True, bot_module=bot_mod))
    assert result is False
    assert write_calls == []
    assert bot_mod.cvd_calls == []       # ни CVD-вызова
    assert bot_mod.premium_calls == []   # ни сетевого premium-вызова -- флаг ДО любого I/O


def test_log_auto_derivatives_shadow_writes_when_enabled(monkeypatch):
    write_calls = []
    bot_mod = _FakeBotModule(cvd_1h=1000, premium_pct=0.2)
    monkeypatch.setattr(se, "DERIV_AUTO_SHADOW_ENABLED", True)
    monkeypatch.setattr(se, "_write_local", lambda record: write_calls.append(record) or True)
    monkeypatch.setattr(se, "_sync_to_github_sync", lambda record: True)
    result = _run(se.log_auto_derivatives_shadow_async(
        "BTC", _analysis(), promoted_live=True, bot_module=bot_mod))
    assert result is True
    assert len(write_calls) == 1
    assert write_calls[0]["type"] == "auto_derivatives_shadow"
    assert bot_mod.premium_calls == ["BTC"]  # premium запрошен по базовому символу, без USDT


def test_log_auto_derivatives_shadow_build_failure_returns_false_not_raise(monkeypatch):
    monkeypatch.setattr(se, "DERIV_AUTO_SHADOW_ENABLED", True)
    bot_mod = _FakeBotModule()

    def boom(*a, **kw):
        raise KeyError("simulated")

    monkeypatch.setattr(bot_mod, "get_perp_spot_premium", boom)
    result = _run(se.log_auto_derivatives_shadow_async(
        "BTC", _analysis(), promoted_live=True, bot_module=bot_mod))
    assert result is False
