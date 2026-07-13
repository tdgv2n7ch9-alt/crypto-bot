"""
pytest для ПАКЕТ 19, П4 (владелец): L/S ratio shadow A/B -- "за тренд"
(живая трактовка Whale Radar, bot._analyze_whale_signal()) vs "контр"
(гипотеза владельца: экстремальный перекос толпы -- контр-сигнал).
Файловый I/O изолирован через monkeypatch на shadow_engine.SHADOW_FILE.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shadow_engine as se


def _iso(monkeypatch, tmp_path):
    monkeypatch.setattr(se, "SHADOW_FILE", str(tmp_path / "shadow_signals.json"))
    # GitHub-синк не должен пытаться реально стучаться в сеть в тестах.
    monkeypatch.setattr(se, "_sync_to_github_sync", lambda record: None)


# ── compute_ls_ratio_contrarian_verdict() ──

def test_high_ls_trend_long_contrarian_short():
    v = se.compute_ls_ratio_contrarian_verdict(2.67)
    assert v["trend_direction"] == "LONG"
    assert v["contrarian_direction"] == "SHORT"
    assert v["is_extreme"] is True
    assert v["diverges"] is True


def test_low_ls_trend_short_contrarian_long():
    v = se.compute_ls_ratio_contrarian_verdict(0.5)
    assert v["trend_direction"] == "SHORT"
    assert v["contrarian_direction"] == "LONG"
    assert v["is_extreme"] is True
    assert v["diverges"] is True


def test_neutral_zone_no_divergence():
    v = se.compute_ls_ratio_contrarian_verdict(1.0)
    assert v["trend_direction"] == "NEUTRAL"
    assert v["contrarian_direction"] == "NEUTRAL"
    assert v["is_extreme"] is False
    assert v["diverges"] is False


def test_thresholds_match_live_whale_radar_exactly():
    """Пороги 1.5/0.7 -- ТЕ ЖЕ, что bot._analyze_whale_signal() live
    (bot.py:5985,5988) -- не переизобретаем свои."""
    assert se.LS_EXTREME_HIGH == 1.5
    assert se.LS_EXTREME_LOW == 0.7
    boundary_high = se.compute_ls_ratio_contrarian_verdict(1.5)
    assert boundary_high["is_extreme"] is False  # строго > 1.5, не >=
    boundary_low = se.compute_ls_ratio_contrarian_verdict(0.7)
    assert boundary_low["is_extreme"] is False  # строго < 0.7, не <=


# ── log_ls_contrarian_shadow_async() ──

def test_extreme_ls_writes_record(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    ok = asyncio.run(se.log_ls_contrarian_shadow_async("BTC", 2.67, 0.02, "LONG", 78))
    assert ok is True
    records = se.get_local_records()
    assert len(records) == 1
    r = records[0]
    assert r["type"] == "ls_contrarian_shadow"
    assert r["symbol"] == "BTC"
    assert r["ls"] == 2.67
    assert r["trend_direction"] == "LONG"
    assert r["contrarian_direction"] == "SHORT"
    assert r["diverges"] is True
    assert r["live_direction"] == "LONG"
    assert r["live_score_100"] == 78


def test_neutral_ls_does_not_write_record(monkeypatch, tmp_path):
    """Владелец: копим только сигнал, не засоряем журнал нейтральной зоной
    без A/B-содержания."""
    _iso(monkeypatch, tmp_path)
    ok = asyncio.run(se.log_ls_contrarian_shadow_async("ETH", 1.0, 0.0, "NEUTRAL", 30))
    assert ok is False
    assert se.get_local_records() == []


def test_does_not_raise_on_build_failure(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)

    def _boom(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(se, "_build_ls_contrarian_shadow_record", _boom)
    ok = asyncio.run(se.log_ls_contrarian_shadow_async("BTC", 2.0, 0.0, "LONG", 50))
    assert ok is False


# ── ls_contrarian_readiness_summary() ──

def test_readiness_counts_only_diverging_records():
    records = [
        {"type": "ls_contrarian_shadow", "diverges": True},
        {"type": "ls_contrarian_shadow", "diverges": True},
        {"type": "ls_contrarian_shadow", "diverges": False},  # не должно попасть (не бывает живьём, но честно)
        {"type": "ema_stack_shadow", "diverges": True},  # другой тип -- не считается
    ]
    result = se.ls_contrarian_readiness_summary(records, threshold=100)
    assert result["n"] == 2
    assert result["ready"] is False
    assert result["remaining"] == 98


def test_readiness_ready_at_threshold():
    records = [{"type": "ls_contrarian_shadow", "diverges": True} for _ in range(100)]
    result = se.ls_contrarian_readiness_summary(records, threshold=100)
    assert result["ready"] is True
    assert result["remaining"] == 0


def test_readiness_empty_records():
    result = se.ls_contrarian_readiness_summary([], threshold=100)
    assert result == {"n": 0, "threshold": 100, "ready": False, "remaining": 100}
