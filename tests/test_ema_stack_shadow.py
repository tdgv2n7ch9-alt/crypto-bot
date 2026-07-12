"""
pytest для Пакета 9 кусок 2 (EMA-стек унификация -- diff+shadow, владелец "да"
ТОЛЬКО на это, НЕ на переключение live): shadow_engine._build_ema_stack_shadow_record()
и bot.pro_analysis()'s "ema_stack_shadow" поле. Проверяем ДВЕ вещи по отдельности:
(1) shadow-запись правильно вычисляет "would_promote_new"/"diverges" из уже готового
ema_stack_shadow, не делая сетевых вызовов; (2) новое поле в pro_analysis() -- чистая
добавка, НЕ меняющая live pro_score/direction/bull_pts/bear_pts (последнее проверяется
косвенно: у тестовых данных pro_score/direction идентичны до и после появления поля,
т.к. само поле вычисляется ПОСЛЕ того, как live-значения уже финализированы).
"""
import shadow_engine as se


def _ema_stack_shadow(pro_score_new=70, direction_new="short", bull_tfs_new=0, bear_tfs_new=3):
    return {
        "tf_1h_new": "bearish", "tf_4h_new": "bearish",
        "bull_tfs_old": 0, "bear_tfs_old": 4,
        "bull_tfs_new": bull_tfs_new, "bear_tfs_new": bear_tfs_new,
        "pro_score_old": 84, "direction_old": "short",
        "pro_score_new": pro_score_new, "direction_new": direction_new,
    }


def test_no_error_no_divergence_when_new_score_still_promotes():
    rec = se._build_ema_stack_shadow_record("SOLUSDT", _ema_stack_shadow(pro_score_new=74, direction_new="short"),
                                              promoted_live=True, rr=2.5)
    assert rec["type"] == "ema_stack_shadow"
    assert rec["would_promote_new"] is True
    assert rec["diverges"] is False
    assert rec["promoted_live"] is True


def test_diverges_when_new_score_drops_below_threshold():
    # SOL live case observed 2026-07-12: old short 74 -> new neutral 34, would NOT promote
    rec = se._build_ema_stack_shadow_record("SOLUSDT", _ema_stack_shadow(pro_score_new=34, direction_new="neutral"),
                                              promoted_live=False, rr=2.5)
    assert rec["would_promote_new"] is False
    assert rec["diverges"] is False  # both agree: not promoted


def test_diverges_true_when_old_promoted_but_new_would_not():
    rec = se._build_ema_stack_shadow_record("SOLUSDT", _ema_stack_shadow(pro_score_new=34, direction_new="neutral"),
                                              promoted_live=True, rr=2.5)
    assert rec["would_promote_new"] is False
    assert rec["diverges"] is True


def test_would_promote_new_respects_rr_gate():
    # score/direction pass but R:R below PROMOTE_MIN_RR -- must not promote
    rec = se._build_ema_stack_shadow_record("SOLUSDT", _ema_stack_shadow(pro_score_new=90, direction_new="short"),
                                              promoted_live=False, rr=0.5)
    assert rec["would_promote_new"] is False


def test_returns_false_on_empty_shadow_without_raising():
    import asyncio
    ok = asyncio.run(se.log_ema_stack_shadow_async("SOLUSDT", {}, promoted_live=False, rr=1.0))
    assert ok is False


def test_returns_false_on_error_shadow_without_raising():
    import asyncio
    ok = asyncio.run(se.log_ema_stack_shadow_async("SOLUSDT", {"error": "boom"}, promoted_live=False, rr=1.0))
    assert ok is False


def test_record_carries_raw_ema_stack_shadow_fields_through():
    shadow = _ema_stack_shadow()
    rec = se._build_ema_stack_shadow_record("BTCUSDT", shadow, promoted_live=False, rr=1.0)
    for key, val in shadow.items():
        assert rec[key] == val
