"""
pytest для morning_metrics.py -- «Утренняя сводка» («Пакетный ритм» пакет 2, М2).
Переиспользует daily_metrics.py helper-функции (уже покрыты
tests/test_daily_metrics.py) -- здесь тестируется сборка текста и деплой-статус,
файловый I/O изолирован через tmp_path/monkeypatch, без реальной сети/Telegram.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import daily_metrics
import morning_metrics
import signal_journal as sj


class _FakeBotModule:
    _deploy_check_boot_sha = {"sha": "aaa1111", "date": None}

    @staticmethod
    def get_data_source_status():
        return {"coingecko_markets": {"ok": True}, "yahoo_finance": {"ok": False}}

    @staticmethod
    def _fetch_main_head_sync():
        return "aaa1111", "2026-07-11T05:00:00Z"


# ── deploy_status_summary() ──

def test_deploy_status_up_to_date():
    text = morning_metrics.deploy_status_summary(_FakeBotModule())
    assert "актуален" in text
    assert "aaa1111" in text


def test_deploy_status_stale_main_ahead():
    class _StaleBotModule(_FakeBotModule):
        @staticmethod
        def _fetch_main_head_sync():
            return "bbb2222", "2026-07-11T05:00:00Z"

    text = morning_metrics.deploy_status_summary(_StaleBotModule())
    assert "ушёл вперёд" in text
    assert "aaa1111" in text and "bbb2222" in text


def test_deploy_status_boot_sha_missing():
    class _NoBootBotModule(_FakeBotModule):
        _deploy_check_boot_sha = {"sha": None, "date": None}

    text = morning_metrics.deploy_status_summary(_NoBootBotModule())
    assert "н/д" in text


def test_deploy_status_fetch_fails_gracefully():
    class _FailBotModule(_FakeBotModule):
        @staticmethod
        def _fetch_main_head_sync():
            return None, None

    text = morning_metrics.deploy_status_summary(_FailBotModule())
    assert "aaa1111" in text
    assert "не удалось проверить" in text


# ── build_morning_digest() ──

def test_build_morning_digest_no_crash_on_empty_data(monkeypatch, tmp_path):
    monkeypatch.setattr(sj, "_journal", {})
    monkeypatch.setattr(daily_metrics, "shadow_engine_file", lambda: str(tmp_path / "nope.json"))
    monkeypatch.setattr(daily_metrics.whale_radar, "EVENTS_DIR", str(tmp_path))
    monkeypatch.setattr(daily_metrics.level_watch, "EVENTS_DIR", str(tmp_path))
    text = morning_metrics.build_morning_digest(_FakeBotModule(), now_ts=1_000_000.0)
    assert "УТРЕННЯЯ СВОДКА" in text
    assert "Итог ночи" in text
    assert len(text) <= 4096


def test_build_morning_digest_uses_12h_window_not_24h(monkeypatch, tmp_path):
    """Сигнал закрылся 18ч назад -- должен НЕ попасть в 12ч-окно утренней сводки."""
    now = 1_000_000.0
    monkeypatch.setattr(sj, "_journal", {
        1: {"ts": now - 18 * 3600, "outcome_ts": now - 18 * 3600, "outcome": "TP1_HIT"},
    })
    monkeypatch.setattr(daily_metrics, "shadow_engine_file", lambda: str(tmp_path / "nope.json"))
    monkeypatch.setattr(daily_metrics.whale_radar, "EVENTS_DIR", str(tmp_path))
    monkeypatch.setattr(daily_metrics.level_watch, "EVENTS_DIR", str(tmp_path))
    text = morning_metrics.build_morning_digest(_FakeBotModule(), now_ts=now)
    assert "Закрыто: 0" in text


def test_build_morning_digest_includes_deploy_section(monkeypatch, tmp_path):
    monkeypatch.setattr(sj, "_journal", {})
    monkeypatch.setattr(daily_metrics, "shadow_engine_file", lambda: str(tmp_path / "nope.json"))
    monkeypatch.setattr(daily_metrics.whale_radar, "EVENTS_DIR", str(tmp_path))
    monkeypatch.setattr(daily_metrics.level_watch, "EVENTS_DIR", str(tmp_path))
    text = morning_metrics.build_morning_digest(_FakeBotModule(), now_ts=1_000_000.0)
    assert "Деплой" in text
    assert "актуален" in text
