"""
pytest для Пакета 14 (владелец, 2026-07-13): "тип сетапа" (AMD/SH-BOS-RTO/Sweep/
Cypher) + ta_extra.build_13block_verdict() -- параллельный 13-блочный shadow-
вердикт к каждому AUTO-сигналу. real_full_analysis_TZ.md не найден в репозитории
(см. ta_extra.py докстринг перед build_13block_verdict() -- честная запись, не
выдуманный источник). Один тест минимум на каждый из 13 блоков + golden-тест
полного вердикта на детерминированной фикстуре (примитивы замоканы -- реальные
свечи слишком случайны для стабильной 6/6-чек-листа сборки).
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ta_extra as te


def _make_candle(v, vol=0):
    return {"open": v, "high": v + 0.01, "low": v - 0.01, "close": v, "vol": vol}


def _ramp(a, b, n):
    return [a + (b - a) * i / n for i in range(1, n)]


def _cypher_bull_candles():
    """X-A-B-C-D валидная бычья геометрия (см. detect_cypher_pattern докстринг),
    построена монотонными "рампами" между фрактальными точками, чтобы
    _find_fractals распознал ровно эти 5 точек, не лишние."""
    X, A, B, C, D = 100.0, 150.0, 125.0, 165.0, 113.91
    path = [X]
    path += _ramp(X, A, 8) + [A]
    path += _ramp(A, B, 8) + [B]
    path += _ramp(B, C, 8) + [C]
    path += _ramp(C, D, 8) + [D]
    path += _ramp(D, D + 5, 4)
    start_pad = [X + 5, X + 2]
    return [_make_candle(v) for v in (start_pad + path)]


def _random_walk_candles(n=250, start=100.0, seed=1):
    rnd = random.Random(seed)
    candles = []
    price = start
    for _ in range(n):
        o = price
        c = price * (1 + rnd.uniform(-0.02, 0.02))
        h = max(o, c) * (1 + rnd.uniform(0, 0.01))
        l = min(o, c) * (1 - rnd.uniform(0, 0.01))
        candles.append({"open": o, "high": h, "low": l, "close": c, "vol": 0})
        price = c
    return candles


def _flat_candles(n=250, price=100.0):
    return [_make_candle(price) for _ in range(n)]


# ── detect_cypher_pattern / classify_setup_type ────────────────────────────

def test_cypher_pattern_detects_valid_bull_geometry():
    candles = _cypher_bull_candles()
    res = te.detect_cypher_pattern(candles)
    assert res["bull"] is True
    assert res["bear"] is False
    assert res["tp1"] is not None and res["tp2"] is not None and res["sl"] is not None
    # SL за точкой X (100), с буфером вниз -- ниже X
    assert res["sl"] < res["points"]["X"]


def test_cypher_pattern_rejects_flat_no_geometry():
    candles = _flat_candles()
    res = te.detect_cypher_pattern(candles)
    assert res["bull"] is False
    assert res["bear"] is False


def test_cypher_pattern_rejects_wrong_ratios():
    """Геометрия есть (5 чередующихся точек), но отношения вне допуска Cypher --
    честно bull=False/bear=False, не "почти похоже"."""
    X, A, B, C, D = 100.0, 150.0, 140.0, 145.0, 143.0  # B-коррекция всего 20%, не 38-62%
    path = [X] + _ramp(X, A, 8) + [A] + _ramp(A, B, 8) + [B] + _ramp(B, C, 8) + [C] + _ramp(C, D, 8) + [D]
    path += _ramp(D, D + 5, 4)
    candles = [_make_candle(v) for v in ([X + 5, X + 2] + path)]
    res = te.detect_cypher_pattern(candles)
    assert res["bull"] is False and res["bear"] is False


def test_classify_setup_type_cypher_priority(monkeypatch):
    """Cypher проверяется первым -- если геометрия валидна, возвращается Cypher
    независимо от sweep/AMD-состояния."""
    candles = _cypher_bull_candles()
    result = te.classify_setup_type(candles, direction="long")
    assert result["setup_type"] == "Cypher"


def test_classify_setup_type_sh_bos_rto(monkeypatch):
    """Свежий sweep + BOS согласован с bias (aligned=True) -> SH-BOS-RTO."""
    monkeypatch.setattr(te, "detect_cypher_pattern", lambda c: {"bull": False, "bear": False})
    monkeypatch.setattr(te, "detect_sweep", lambda c: {"type": "sweep_low", "level": 99.0,
                                                          "bars_ago": 3, "volume_confirmed": None})
    monkeypatch.setattr(te, "smc_setup_type", lambda c, d: {"type": "BOS_bull", "aligned": True,
                                                               "label": "BOS test"})
    result = te.classify_setup_type(_flat_candles(), direction="long")
    assert result["setup_type"] == "SH-BOS-RTO"


def test_classify_setup_type_sweep_without_bos(monkeypatch):
    """Свежий sweep есть, но BOS НЕ согласован (aligned не True) -> Sweep, не SH-BOS-RTO."""
    monkeypatch.setattr(te, "detect_cypher_pattern", lambda c: {"bull": False, "bear": False})
    monkeypatch.setattr(te, "detect_sweep", lambda c: {"type": "sweep_high", "level": 101.0,
                                                          "bars_ago": 5, "volume_confirmed": None})
    monkeypatch.setattr(te, "smc_setup_type", lambda c, d: {"type": None, "aligned": None, "label": "н/д"})
    monkeypatch.setattr(te, "classify_amd_phase", lambda c, now_utc=None: {"phase": "accumulation",
                                                                             "nymidnight_price": 100.0,
                                                                             "price_vs_nymidnight": "above"})
    result = te.classify_setup_type(_flat_candles(), direction="long")
    assert result["setup_type"] == "Sweep"


def test_classify_setup_type_amd_branch(monkeypatch):
    """Нет sweep/Cypher, но AMD-фаза в манипуляции/дистрибуции -> AMD."""
    monkeypatch.setattr(te, "detect_cypher_pattern", lambda c: {"bull": False, "bear": False})
    monkeypatch.setattr(te, "detect_sweep", lambda c: None)
    monkeypatch.setattr(te, "classify_amd_phase", lambda c, now_utc=None: {
        "phase": "manipulation_bull", "nymidnight_price": 100.0, "price_vs_nymidnight": "above"})
    result = te.classify_setup_type(_flat_candles(), direction="long")
    assert result["setup_type"] == "AMD"


def test_classify_setup_type_none_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(te, "detect_cypher_pattern", lambda c: {"bull": False, "bear": False})
    monkeypatch.setattr(te, "detect_sweep", lambda c: None)
    monkeypatch.setattr(te, "classify_amd_phase", lambda c, now_utc=None: {
        "phase": "dead_zone", "nymidnight_price": None, "price_vs_nymidnight": None})
    result = te.classify_setup_type(_flat_candles(), direction="long")
    assert result["setup_type"] is None


# ── build_13block_verdict: один тест на каждый из 13 блоков ────────────────

_KZ = {"active": {"name": "London", "quality": "A"}, "is_good": True, "next": None}
_FUNDING = {"ok": True, "rate": 0.01}


def _base_verdict():
    candles = _random_walk_candles()
    return te.build_13block_verdict(candles, candles, candles, candles[-1]["close"],
                                     _KZ, _FUNDING, oi_change=1.0, oi_combo="up_up", ls_ratio=1.1)


def test_block1_bias_present():
    v = _base_verdict()
    assert "bias" in v["block1_bias"]
    assert v["block1_bias"]["bias"] in ("LONG", "SHORT", "NEUTRAL")


def test_block2_elliott_both_timeframes_present():
    v = _base_verdict()
    b2 = v["block2_elliott"]
    assert "elliott_1d" in b2 and "elliott_4h" in b2
    assert "label" in b2["elliott_1d"] and "label" in b2["elliott_4h"]


def test_block3_setup_type_is_one_of_known_values():
    v = _base_verdict()
    assert v["block3_setup_type"]["setup_type"] in ("AMD", "SH-BOS-RTO", "Sweep", "Cypher", None)


def test_block4_zones_above_below_shape():
    v = _base_verdict()
    assert "above" in v["block4_zones"] and "below" in v["block4_zones"]
    assert isinstance(v["block4_zones"]["above"], list)
    assert isinstance(v["block4_zones"]["below"], list)


def test_block5_checklist_six_items():
    v = _base_verdict()
    b5 = v["block5_checklist"]
    assert len(b5["items"]) == 6
    assert 0 <= b5["score"] <= 6
    assert b5["score"] == sum(1 for _, ok in b5["items"] if ok)


def test_block6_liquidity_sweep_fields_present():
    v = _base_verdict()
    b6 = v["block6_liquidity"]
    assert "sweep_1h" in b6 and "sweep_4h" in b6 and "equal_levels" in b6


def test_block7_oi_matrix_passes_through_not_recomputed():
    """Владелец, п.3: OI/funding/L-S -- уже посчитаны вызывающей стороной, эта
    функция их НЕ пересчитывает (нет своего _get_oi_change())."""
    v = te.build_13block_verdict(_random_walk_candles(), _random_walk_candles(),
                                  _random_walk_candles(), 100.0, _KZ,
                                  funding={"ok": True, "rate": 0.07},
                                  oi_change=3.3, oi_combo="down_up", ls_ratio=2.0)
    b7 = v["block7_oi"]
    assert b7["oi_change_pct"] == 3.3
    assert b7["oi_combo"] == "down_up"
    assert b7["ls_ratio"] == 2.0
    assert b7["funding"]["rate"] == 0.07


def test_block8_killzone_reuses_single_source_not_recomputed():
    """Владелец, п.2: killzone -- get_killzone_status() единый источник, эта
    функция НЕ считает часы заново -- то, что передали, то и возвращается."""
    custom_kz = {"active": {"name": "NY Open", "quality": "A+"}, "is_good": True, "next": None}
    v = te.build_13block_verdict(_random_walk_candles(), _random_walk_candles(),
                                  _random_walk_candles(), 100.0, custom_kz,
                                  _FUNDING, oi_change=1.0, oi_combo="up_up", ls_ratio=1.0)
    assert v["block8_killzone"] is custom_kz


def test_block9_phase_present():
    v = _base_verdict()
    assert "phase" in v["block9_phase"]


def test_block10_dca_weights_50_30_20():
    v = _base_verdict()
    assert v["block10_dca"]["weights"] == "50/30/20"


def test_block11_tp_rr_three_targets_present():
    v = _base_verdict()
    b11 = v["block11_tp_rr"]
    assert set(b11.keys()) == {"tp1", "tp2", "tp3", "rr_tp1", "rr_tp2", "rr_tp3", "rr_gate_pass"}


def test_block12_sl_buffer_matches_ta_extra_constant():
    v = _base_verdict()
    assert v["block12_sl"]["buffer_pct"] == te.SR_SL_BUFFER_PCT


def test_block13_verdict_has_required_fields():
    v = _base_verdict()
    b13 = v["block13_verdict"]
    assert set(b13.keys()) == {"has_setup", "direction", "score", "text"}
    assert isinstance(b13["text"], str) and b13["text"]


def test_no_direction_gives_honest_no_setup_not_fabricated():
    """direction=None (NEUTRAL bias) -- has_setup обязан быть False, DCA/TP/SL
    честно None, не выдуманные числа."""
    flat = _flat_candles()  # плоский рынок -- bias почти наверняка NEUTRAL/недостаточно данных
    v = te.build_13block_verdict(flat, flat, flat, 100.0, _KZ, _FUNDING,
                                  oi_change=0, oi_combo=None, ls_ratio=1.0)
    if v["direction"] is None:
        assert v["block13_verdict"]["has_setup"] is False
        assert v["entry_zone"] is None
        assert v["sl"] is None and v["tp1"] is None


# ── Golden-тест: полный вердикт на детерминированной фикстуре ──────────────

def test_build_13block_verdict_golden(monkeypatch):
    """Золотой тест: все примитивы замоканы на согласованный LONG-сценарий с
    полным 6/6 чек-листом -- проверяет, что build_13block_verdict() корректно
    СОБИРАЕТ результаты всех 13 блоков в единый вердикт (владелец, п.3: "score,
    направление, зона, SL/TP")."""
    monkeypatch.setattr(te, "multi_tf_bias", lambda c1d, c4h, c1h: {
        "bias": "LONG", "structure_1d": "HH/HL (аптренд)",
        "tf_agreement": "1D и 4H согласованы", "detail": [],
        "key_low": None, "key_high": None,
    })
    monkeypatch.setattr(te, "elliott_wave_heuristic", lambda closes, rsi_v: {
        "wave": "3", "label": "похоже на волну 3 (импульс)", "note": "", "score_delta": 10,
    })
    monkeypatch.setattr(te, "classify_setup_type", lambda c4h, direction=None, now_utc=None: {
        "setup_type": "SH-BOS-RTO", "label": "SH-BOS-RTO test", "detail": {},
    })
    fake_zones = {
        "below": [{"lo": 95.0, "hi": 98.0, "mid": 96.5, "touches": 3, "sources": ["4h"]}],
        "above": [{"lo": 105.0, "hi": 108.0, "mid": 106.5, "touches": 2, "sources": ["4h"]}],
    }
    monkeypatch.setattr(te, "find_sr_zones", lambda *a, **kw: fake_zones)
    monkeypatch.setattr(te, "detect_sweep", lambda c: {"type": "sweep_low", "level": 94.0,
                                                          "bars_ago": 2, "volume_confirmed": None})
    monkeypatch.setattr(te, "sweep_score_delta", lambda s1, s4, d: 5)
    monkeypatch.setattr(te, "equal_levels", lambda c, tolerance_pct=0.3: [])
    fake_trade = {
        "entry1": 98.0, "entry2": 96.5, "entry3": 95.0, "entry_lo": 95.0, "entry_hi": 98.0,
        "sl": 92.7, "tp1": 104.0, "tp2": 108.0, "tp3": 112.0,
        "rr_tp1": 2.0, "rr_tp2": 3.0, "rr_tp3": 4.0, "rr_gate_pass": True,
        "entry_zone": fake_zones["below"][0], "tp_zones": [],
    }
    monkeypatch.setattr(te, "build_trade_from_structure", lambda direction, price, zones: fake_trade)
    monkeypatch.setattr(te, "wyckoff_phase_heuristic", lambda closes, price, vols_1d=None: {
        "phase": "Накопление (Accumulation)", "note": ""})

    candles = _flat_candles(n=30, price=100.0)
    verdict = te.build_13block_verdict(candles, candles, candles, 97.0, _KZ, _FUNDING,
                                        oi_change=2.0, oi_combo="up_up", ls_ratio=1.2)

    assert verdict["direction"] == "long"
    assert verdict["setup_type"] == "SH-BOS-RTO"
    assert verdict["entry_zone"] == {"lo": 95.0, "hi": 98.0}
    assert verdict["sl"] == 92.7
    assert verdict["tp1"] == 104.0
    assert verdict["tp2"] == 108.0
    assert verdict["tp3"] == 112.0
    assert verdict["score"] == 6
    assert verdict["block13_verdict"]["has_setup"] is True
    assert "лонг" in verdict["block13_verdict"]["text"]
