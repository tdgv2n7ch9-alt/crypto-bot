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


def test_build_morning_digest_shows_shadow_stats_breakdown(monkeypatch, tmp_path):
    """Пакет 6 М3 -- та же обогащённая shadow-секция, что «Метрики дня», через
    переиспользованный daily_metrics.shadow_vs_live_today()."""
    import json
    now = 1_000_000.0
    shadow_file = tmp_path / "shadow_signals.json"
    shadow_file.write_text(json.dumps({
        "schema_version": 1,
        "records": [
            {"ts": now - 10, "symbol": "BEAT", "direction": "short", "promoted_live": True,
             "dead_zone": False, "gate_reasons": [], "patches_affected": ["02-rr-gate"],
             "discrepancy": ["R:R 1.53 прошёл live-гейт (1.5), но НЕ прошёл бы shadow-гейт (2.0)"]},
        ],
    }))
    monkeypatch.setattr(sj, "_journal", {})
    monkeypatch.setattr(daily_metrics, "shadow_engine_file", lambda: str(shadow_file))
    monkeypatch.setattr(daily_metrics.whale_radar, "EVENTS_DIR", str(tmp_path))
    monkeypatch.setattr(daily_metrics.level_watch, "EVENTS_DIR", str(tmp_path))
    text = morning_metrics.build_morning_digest(_FakeBotModule(), now_ts=now)
    assert "Топ-1 расхождение" in text
    assert "BEAT" in text
    assert "Патчи 02-05" in text


# --- Пакет 11 М7: night_package_status_summary() + секция в дайджесте ---

def test_night_package_status_summary_extracts_status_lines(tmp_path):
    progress = tmp_path / "PROGRESS.md"
    progress.write_text(
        "## М1\n**Статус М1: ГОТОВ.**\n\n## М2\n**Статус М2: НЕ ЗАКРЫТ, причина X.**\n"
    )
    result = morning_metrics.night_package_status_summary(progress_md_path=str(progress))
    assert result == ["Статус М1: ГОТОВ.", "Статус М2: НЕ ЗАКРЫТ, причина X."]


def test_night_package_status_summary_missing_file_returns_empty(tmp_path):
    result = morning_metrics.night_package_status_summary(
        progress_md_path=str(tmp_path / "does_not_exist.md"))
    assert result == []


def test_night_package_status_summary_caps_at_max_lines(tmp_path):
    progress = tmp_path / "PROGRESS.md"
    lines = "\n".join(f"**Статус М{i}: ГОТОВ.**" for i in range(20))
    progress.write_text(lines)
    result = morning_metrics.night_package_status_summary(progress_md_path=str(progress))
    assert len(result) == morning_metrics.NIGHT_STATUS_MAX_LINES
    assert result[-1] == "Статус М19: ГОТОВ."


def test_night_package_status_summary_only_reads_tail(tmp_path):
    """Старые статусы за пределами tail_chars не должны попадать в выжимку --
    это НАМЕРЕННО (только последняя ночная сессия, не вся история)."""
    progress = tmp_path / "PROGRESS.md"
    old = "x" * 100 + "\n**Статус СТАРЫЙ: ГОТОВ.**\n" + "y" * 200
    progress.write_text(old)
    result = morning_metrics.night_package_status_summary(
        progress_md_path=str(progress), tail_chars=50)
    assert result == []


def test_build_morning_digest_includes_night_package_section(monkeypatch, tmp_path):
    progress = tmp_path / "PROGRESS.md"
    progress.write_text("**Статус М1: ГОТОВ.**\n")
    monkeypatch.setattr(morning_metrics, "PROGRESS_MD_PATH", str(progress))
    monkeypatch.setattr(sj, "_journal", {})
    monkeypatch.setattr(daily_metrics, "shadow_engine_file", lambda: str(tmp_path / "nope.json"))
    monkeypatch.setattr(daily_metrics.whale_radar, "EVENTS_DIR", str(tmp_path))
    monkeypatch.setattr(daily_metrics.level_watch, "EVENTS_DIR", str(tmp_path))
    text = morning_metrics.build_morning_digest(_FakeBotModule(), now_ts=1_000_000.0)
    assert "Ночной пакет" in text
    assert "Статус М1: ГОТОВ." in text


def test_build_morning_digest_night_package_na_when_no_progress_file(monkeypatch, tmp_path):
    monkeypatch.setattr(morning_metrics, "PROGRESS_MD_PATH", str(tmp_path / "missing.md"))
    monkeypatch.setattr(sj, "_journal", {})
    monkeypatch.setattr(daily_metrics, "shadow_engine_file", lambda: str(tmp_path / "nope.json"))
    monkeypatch.setattr(daily_metrics.whale_radar, "EVENTS_DIR", str(tmp_path))
    monkeypatch.setattr(daily_metrics.level_watch, "EVENTS_DIR", str(tmp_path))
    text = morning_metrics.build_morning_digest(_FakeBotModule(), now_ts=1_000_000.0)
    assert "н/д" in text
