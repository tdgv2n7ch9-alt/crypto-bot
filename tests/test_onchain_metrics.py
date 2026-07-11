"""
pytest для onchain_metrics.py -- Фаза C каркас («Пакетный ритм» пакет 2, М5).
Никакого реального фетча (не реализован в этом пакете -- см. докстринг модуля,
Glassnode не имеет бесплатного тира) -- тестируется только честная деградация
"источник не настроен".
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import onchain_metrics as ocm


def test_not_configured_by_default(monkeypatch):
    monkeypatch.setattr(ocm, "ONCHAIN_DATA_SOURCE", "")
    monkeypatch.setattr(ocm, "ONCHAIN_API_KEY", "")
    assert ocm.is_configured() is False


def test_get_onchain_metrics_honest_when_not_configured(monkeypatch):
    monkeypatch.setattr(ocm, "ONCHAIN_DATA_SOURCE", "")
    result = ocm.get_onchain_metrics("BTC")
    assert result["ok"] is False
    assert "не настроен" in result["reason"]


def test_get_onchain_metrics_unknown_source(monkeypatch):
    monkeypatch.setattr(ocm, "ONCHAIN_DATA_SOURCE", "totally_made_up_source")
    monkeypatch.setattr(ocm, "ONCHAIN_API_KEY", "some-key")
    result = ocm.get_onchain_metrics("BTC")
    assert result["ok"] is False
    assert "не распознан" in result["reason"]


def test_get_onchain_metrics_known_source_missing_key(monkeypatch):
    monkeypatch.setattr(ocm, "ONCHAIN_DATA_SOURCE", "glassnode")
    monkeypatch.setattr(ocm, "ONCHAIN_API_KEY", "")
    result = ocm.get_onchain_metrics("BTC")
    assert result["ok"] is False
    assert "ONCHAIN_API_KEY" in result["reason"]


def test_get_onchain_metrics_known_source_with_key_still_not_implemented(monkeypatch):
    """Каркас: даже с источником+ключом фетчер ещё не реализован -- честно,
    не выдумывает данные, которых не фетчил."""
    monkeypatch.setattr(ocm, "ONCHAIN_DATA_SOURCE", "bgeometrics")
    monkeypatch.setattr(ocm, "ONCHAIN_API_KEY", "some-key")
    result = ocm.get_onchain_metrics("BTC")
    assert result["ok"] is False
    assert "фетчер" in result["reason"]


def test_is_configured_requires_both_source_and_key(monkeypatch):
    monkeypatch.setattr(ocm, "ONCHAIN_DATA_SOURCE", "glassnode")
    monkeypatch.setattr(ocm, "ONCHAIN_API_KEY", "")
    assert ocm.is_configured() is False
    monkeypatch.setattr(ocm, "ONCHAIN_API_KEY", "key")
    assert ocm.is_configured() is True


# ── shadow_score_adjustment() ──

def test_shadow_score_adjustment_no_data():
    adj = ocm.shadow_score_adjustment({"ok": False, "reason": "not configured"})
    assert adj["available"] is False
    assert adj["adjustment"] == 0


def test_shadow_score_adjustment_data_present_but_formula_not_designed():
    adj = ocm.shadow_score_adjustment({"ok": True, "sopr": 1.0})
    assert adj["available"] is False
    assert adj["adjustment"] == 0
    assert "формула" in adj["reason"]


# ── format_onchain_card_text() ──

def test_format_onchain_card_text_honest_not_configured(monkeypatch):
    monkeypatch.setattr(ocm, "ONCHAIN_DATA_SOURCE", "")
    text = ocm.format_onchain_card_text("BTC")
    assert "🚧" in text
    assert "не настроен" in text
