"""
pytest для П-EMA re-logging (владелец, ночное задание 14->15.07, Пакет 3 --
ПОДГОТОВКА, БЕЗ активации в бою): shadow-сравнение старой (2-EMA-на-ТФ) и
новой (ta_extra.ema_context()) методологии для AUTO-скана
(bot.real_full_analysis()), за флагом shadow_engine.EMA_AUTO_SHADOW_ENABLED
(по умолчанию False). Три вещи проверяются отдельно: (1) ta_extra.old_style_
ema_trend() -- чистая формула-дубликат; (2) shadow_engine._build_auto_ema_
stack_shadow_record() -- сравнение на готовых данных, без сети; (3)
log_auto_ema_stack_shadow_async() -- флаг-гейт гарантированно no-op при
EMA_AUTO_SHADOW_ENABLED=False (ни локальной записи, ни сети), работает при
True (monkeypatch на True, тот же паттерн, что владелец включит одним словом).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shadow_engine as se
import ta_extra


def _run(coro):
    return asyncio.run(coro)


def _rising_closes(n=60, start=100.0, step=1.0):
    return [start + i * step for i in range(n)]


def _falling_closes(n=60, start=200.0, step=1.0):
    return [start - i * step for i in range(n)]


def _flat_closes(n=60, value=100.0):
    return [value for _ in range(n)]


# ── ta_extra.old_style_ema_trend() ──────────────────────────────────────────

def test_old_style_ema_trend_bullish_on_rising_closes():
    assert ta_extra.old_style_ema_trend(_rising_closes(), 20, 50) == "bullish"


def test_old_style_ema_trend_bearish_on_falling_closes():
    assert ta_extra.old_style_ema_trend(_falling_closes(), 20, 50) == "bearish"


def test_old_style_ema_trend_neutral_on_flat_closes():
    assert ta_extra.old_style_ema_trend(_flat_closes(), 20, 50) == "neutral"


def test_old_style_ema_trend_neutral_on_insufficient_data():
    assert ta_extra.old_style_ema_trend([100.0, 101.0], 20, 50) == "neutral"


# ── shadow_engine._build_auto_ema_stack_shadow_record() ─────────────────────

def _analysis(is_long=True, tf_1h_stack="бычий", tf_4h_stack="бычий",
              candles_1h=None, candles_4h=None):
    return {
        "is_long": is_long,
        "ema_ctx": {"tf_1h": {"stack": tf_1h_stack}, "tf_4h": {"stack": tf_4h_stack}},
        "candles_1h": candles_1h if candles_1h is not None else _rising_closes(),
        "candles_4h": candles_4h if candles_4h is not None else _rising_closes(),
    }


def test_build_record_agrees_when_both_bullish_long():
    a = _analysis(is_long=True, tf_4h_stack="бычий", candles_4h=_rising_closes())
    rec = se._build_auto_ema_stack_shadow_record("BTCUSDT", a, promoted_live=True)
    assert rec["type"] == "auto_ema_stack_shadow"
    assert rec["direction"] == "long"
    assert rec["tf_4h_new"] == "bullish"
    assert rec["tf_4h_old"] == "bullish"
    assert rec["score_delta_new"] == 8
    assert rec["score_delta_old"] == 8
    assert rec["diverges"] is False


def test_build_record_diverges_when_new_bullish_old_neutral():
    # candles_4h идут вниз-вверх так, чтобы old-style (EMA20/50 сырой порядок)
    # не совпал с новым стеком (ema_context() требует подтверждения ценой) --
    # здесь просто подаём flat candles_4h (old=neutral), а ema_ctx говорит "бычий".
    a = _analysis(is_long=True, tf_4h_stack="бычий", candles_4h=_flat_closes())
    rec = se._build_auto_ema_stack_shadow_record("ETHUSDT", a, promoted_live=True)
    assert rec["tf_4h_new"] == "bullish"
    assert rec["tf_4h_old"] == "neutral"
    assert rec["score_delta_new"] == 8
    assert rec["score_delta_old"] == 0
    assert rec["diverges"] is True


def test_build_record_short_direction_scoring():
    a = _analysis(is_long=False, tf_4h_stack="медвежий", candles_4h=_falling_closes())
    rec = se._build_auto_ema_stack_shadow_record("SOLUSDT", a, promoted_live=False)
    assert rec["direction"] == "short"
    assert rec["score_delta_new"] == 8  # медвежий стек ЗА short-направление
    assert rec["score_delta_old"] == 8
    assert rec["promoted_live"] is False


def test_build_record_missing_ema_ctx_is_neutral_not_crash():
    a = _analysis()
    a["ema_ctx"] = None
    rec = se._build_auto_ema_stack_shadow_record("BTCUSDT", a, promoted_live=True)
    assert rec["tf_4h_new"] == "neutral"
    assert rec["score_delta_new"] == 0


def test_build_record_missing_candles_is_neutral_not_crash():
    a = _analysis()
    a["candles_4h"] = []
    rec = se._build_auto_ema_stack_shadow_record("BTCUSDT", a, promoted_live=True)
    assert rec["tf_4h_old"] == "neutral"


# ── shadow_engine.log_auto_ema_stack_shadow_async() -- флаг-гейт ────────────

def test_log_auto_ema_stack_shadow_disabled_by_default_is_true():
    assert se.EMA_AUTO_SHADOW_ENABLED is False


def test_log_auto_ema_stack_shadow_noop_when_disabled(monkeypatch):
    write_calls = []
    monkeypatch.setattr(se, "EMA_AUTO_SHADOW_ENABLED", False)
    monkeypatch.setattr(se, "_write_local", lambda record: write_calls.append(record) or True)
    result = _run(se.log_auto_ema_stack_shadow_async("BTCUSDT", _analysis(), promoted_live=True))
    assert result is False
    assert write_calls == []  # никакого I/O при выключенном флаге


def test_log_auto_ema_stack_shadow_writes_when_enabled(monkeypatch):
    write_calls = []
    monkeypatch.setattr(se, "EMA_AUTO_SHADOW_ENABLED", True)
    monkeypatch.setattr(se, "_write_local", lambda record: write_calls.append(record) or True)
    monkeypatch.setattr(se, "_sync_to_github_sync", lambda record: True)
    result = _run(se.log_auto_ema_stack_shadow_async("BTCUSDT", _analysis(), promoted_live=True))
    assert result is True
    assert len(write_calls) == 1
    assert write_calls[0]["type"] == "auto_ema_stack_shadow"


def test_log_auto_ema_stack_shadow_build_failure_returns_false_not_raise(monkeypatch):
    monkeypatch.setattr(se, "EMA_AUTO_SHADOW_ENABLED", True)

    def boom(*a, **kw):
        raise KeyError("simulated")

    monkeypatch.setattr(se, "_build_auto_ema_stack_shadow_record", boom)
    result = _run(se.log_auto_ema_stack_shadow_async("BTCUSDT", _analysis(), promoted_live=True))
    assert result is False
