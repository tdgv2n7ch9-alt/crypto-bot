"""
pytest для shadow_engine._build_pump_reversal_record() -- Dead Zone shadow-штраф
(owner, 2026-07-11, карточка EVAA): METHODOLOGY_CORE.md §8 говорит, что killzone
влияет на качество сетапа, но killzone quality=="D" (Dead Zone) не давал НИКАКОГО
штрафа pro_score. Штраф -- ТОЛЬКО в shadow-записи (DEAD_ZONE_SHADOW_SCORE_PENALTY),
боевой pro_analysis()/_try_promote_pump() не трогается вообще.
"""
import shadow_engine as se


def _watch(peak=100.0, last=95.0):
    return {
        "peak_price": peak, "last_price": last, "volume_mult": 5.0, "z_score": 3.5,
        "entry_lo": 95.0, "sl": 102.0, "tp1": 85.0, "tp2": 80.0,
    }


def test_no_penalty_when_not_dead_zone():
    rec = se._build_pump_reversal_record("BTCUSDT", _watch(), funding=0.01, oi_usd=1e8,
                                          oi_change_pct=1.0, promoted_live=False,
                                          kz_quality="A", pro_score=70.0)
    assert rec["dead_zone"] is False
    assert rec["dead_zone_score_penalty"] == 0
    assert rec["pro_score_shadow_adjusted"] == 70.0
    assert rec["kz_quality"] == "A"


def test_penalty_applied_when_dead_zone():
    rec = se._build_pump_reversal_record("BTCUSDT", _watch(), funding=0.01, oi_usd=1e8,
                                          oi_change_pct=1.0, promoted_live=True,
                                          kz_quality="D", pro_score=65.0)
    assert rec["dead_zone"] is True
    assert rec["dead_zone_score_penalty"] == se.DEAD_ZONE_SHADOW_SCORE_PENALTY
    assert rec["pro_score_shadow_adjusted"] == 65.0 - se.DEAD_ZONE_SHADOW_SCORE_PENALTY


def test_dead_zone_flag_true_even_without_pro_score():
    # pro_score unavailable (pa was None/failed) -- dead_zone flag still recorded honestly,
    # just no adjusted score to compute (None, not a fabricated number)
    rec = se._build_pump_reversal_record("BTCUSDT", _watch(), funding=0.01, oi_usd=1e8,
                                          oi_change_pct=1.0, promoted_live=False,
                                          kz_quality="D", pro_score=None)
    assert rec["dead_zone"] is True
    assert rec["pro_score_live"] is None
    assert rec["pro_score_shadow_adjusted"] is None
    assert rec["dead_zone_score_penalty"] == se.DEAD_ZONE_SHADOW_SCORE_PENALTY


def test_kz_quality_none_defaults_to_not_dead_zone():
    rec = se._build_pump_reversal_record("BTCUSDT", _watch(), funding=0.01, oi_usd=1e8,
                                          oi_change_pct=1.0, promoted_live=False,
                                          kz_quality=None, pro_score=50.0)
    assert rec["dead_zone"] is False
    assert rec["kz_quality"] is None
    assert rec["pro_score_shadow_adjusted"] == 50.0


def test_penalty_could_push_below_promote_threshold_in_shadow_only():
    # illustrative: a candidate that WOULD promote live (pro_score=62 >= 60) but
    # wouldn't under the shadow-adjusted score -- purely informational, boevой gate
    # untouched (promoted_live passed through as-is, reflects actual live outcome)
    rec = se._build_pump_reversal_record("ETHUSDT", _watch(), funding=0.02, oi_usd=5e7,
                                          oi_change_pct=-0.5, promoted_live=True,
                                          kz_quality="D", pro_score=62.0)
    assert rec["promoted_live"] is True  # live decision unaffected
    assert rec["pro_score_shadow_adjusted"] == 52.0  # honestly below the usual 60 gate, shadow-only info


def test_record_still_builds_without_new_kwargs_backward_compatible():
    # old call signature (positional only, no kz_quality/pro_score) should still work
    rec = se._build_pump_reversal_record("BTCUSDT", _watch(), 0.01, 1e8, 1.0, False)
    assert rec["kz_quality"] is None
    assert rec["dead_zone"] is False
    assert rec["pro_score_live"] is None
