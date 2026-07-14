"""
pytest для daily_metrics.py -- «Метрики дня» (АПГРЕЙД 11.07 Этап 4). Файловый I/O
изолирован через tmp_path/monkeypatch, никакой реальной сети/Telegram.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import daily_metrics
import signal_journal as sj


# ── signal_journal.get_daily_digest_stats() ──

def test_digest_stats_separates_created_from_closed(monkeypatch):
    now = 1_000_000.0
    day_ago = now - 3600  # внутри 24ч окна
    two_days_ago = now - 3 * 86400  # СОЗДАН давно, но...
    monkeypatch.setattr(sj, "_journal", {
        1: {"ts": day_ago, "outcome_ts": None, "outcome": None},          # создан сегодня, ещё открыт
        2: {"ts": two_days_ago, "outcome_ts": day_ago, "outcome": "TP1_HIT"},  # старый, закрылся СЕГОДНЯ
        3: {"ts": two_days_ago, "outcome_ts": two_days_ago, "outcome": "SL_HIT"},  # старый и закрылся давно
    })
    stats = sj.get_daily_digest_stats(window_sec=86400, now_ts=now)
    assert stats["created_count"] == 1   # только запись 1
    assert stats["closed_count"] == 1    # только запись 2 (outcome_ts внутри окна)
    assert stats["wins"] == 1
    assert stats["losses"] == 0
    assert stats["win_rate_today"] == 100.0


def test_digest_stats_no_closed_today_is_honest_none(monkeypatch):
    now = 1_000_000.0
    monkeypatch.setattr(sj, "_journal", {1: {"ts": now, "outcome_ts": None, "outcome": None}})
    stats = sj.get_daily_digest_stats(window_sec=86400, now_ts=now)
    assert stats["closed_count"] == 0
    assert stats["win_rate_today"] is None


def test_digest_stats_by_outcome_breakdown(monkeypatch):
    now = 1_000_000.0
    monkeypatch.setattr(sj, "_journal", {
        1: {"ts": now, "outcome_ts": now, "outcome": "TP1_HIT"},
        2: {"ts": now, "outcome_ts": now, "outcome": "TP1_HIT"},
        3: {"ts": now, "outcome_ts": now, "outcome": "SL_HIT"},
        4: {"ts": now, "outcome_ts": now, "outcome": "EXPIRED"},
    })
    stats = sj.get_daily_digest_stats(window_sec=86400, now_ts=now)
    assert stats["by_outcome"] == {"TP1_HIT": 2, "SL_HIT": 1, "EXPIRED": 1}
    assert stats["wins"] == 2
    assert stats["losses"] == 1


# ── daily_metrics._read_jsonl_events() / top_whale_events_today() ──

def test_read_jsonl_events_from_todays_file(tmp_path, monkeypatch):
    now = time.time()
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(now, tz=timezone.utc)
    path = tmp_path / f"whale_events-{dt.strftime('%Y-%m-%d')}.jsonl"
    path.write_text(
        json.dumps({"type": "whale_trade", "symbol": "BTC", "size_usd": 100}) + "\n"
        + json.dumps({"type": "whale_trade", "symbol": "ETH", "size_usd": 500}) + "\n"
    )
    events = daily_metrics._read_jsonl_events(str(tmp_path), "whale_events", now_ts=now)
    assert len(events) == 2


def test_read_jsonl_events_missing_file_returns_empty(tmp_path):
    events = daily_metrics._read_jsonl_events(str(tmp_path), "whale_events", now_ts=time.time())
    assert events == []


def test_read_jsonl_events_skips_malformed_lines(tmp_path):
    now = time.time()
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(now, tz=timezone.utc)
    path = tmp_path / f"whale_events-{dt.strftime('%Y-%m-%d')}.jsonl"
    path.write_text('{"ok": true}\nNOT JSON\n{"ok": true}\n')
    events = daily_metrics._read_jsonl_events(str(tmp_path), "whale_events", now_ts=now)
    assert len(events) == 2  # 2 валидных, 1 битая строка честно пропущена


def test_read_jsonl_events_spans_midnight_utc_boundary(tmp_path):
    """Найдено при подготовке М2 -- окно, пересекающее полночь UTC, должно читать
    ОБА файла (вчера+сегодня), не только "сегодняшний"."""
    from datetime import datetime, timedelta, timezone
    # now -- чуть после полуночи UTC, окно 12ч уходит в предыдущие сутки
    now_dt = datetime(2026, 7, 11, 0, 30, tzinfo=timezone.utc)
    now = now_dt.timestamp()
    yesterday_path = tmp_path / "whale_events-2026-07-10.jsonl"
    today_path = tmp_path / "whale_events-2026-07-11.jsonl"
    yesterday_path.write_text(json.dumps({"symbol": "OLD", "size_usd": 1}) + "\n")
    today_path.write_text(json.dumps({"symbol": "NEW", "size_usd": 2}) + "\n")

    events = daily_metrics._read_jsonl_events(str(tmp_path), "whale_events", now_ts=now, window_sec=12 * 3600)
    symbols = {e["symbol"] for e in events}
    assert symbols == {"OLD", "NEW"}


def test_top_whale_events_sorted_by_size_desc(tmp_path, monkeypatch):
    now = time.time()
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(now, tz=timezone.utc)
    path = tmp_path / f"whale_events-{dt.strftime('%Y-%m-%d')}.jsonl"
    rows = [
        {"symbol": "A", "size_usd": 100}, {"symbol": "B", "size_usd": 500},
        {"symbol": "C", "size_usd": 300}, {"symbol": "D", "size_usd": 50},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    monkeypatch.setattr(daily_metrics.whale_radar, "EVENTS_DIR", str(tmp_path))
    top = daily_metrics.top_whale_events_today(n=3, now_ts=now)
    assert [e["symbol"] for e in top] == ["B", "C", "A"]


# ── shadow_vs_live_today() ──

def test_shadow_vs_live_counts_promoted_and_not(tmp_path, monkeypatch):
    now = 1_000_000.0
    shadow_file = tmp_path / "shadow_signals.json"
    shadow_file.write_text(json.dumps({
        "schema_version": 1,
        "records": [
            {"ts": now - 100, "promoted_live": True, "dead_zone": False},
            {"ts": now - 200, "promoted_live": False, "dead_zone": True},
            {"ts": now - 3 * 86400, "promoted_live": False, "dead_zone": False},  # вне окна
        ],
    }))
    monkeypatch.setattr(daily_metrics, "shadow_engine_file", lambda: str(shadow_file))
    result = daily_metrics.shadow_vs_live_today(now_ts=now)
    assert result["total"] == 2
    assert result["promoted"] == 1
    assert result["not_promoted"] == 1
    assert result["dead_zone_penalized"] == 1


def test_shadow_vs_live_missing_file_is_honest_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_metrics, "shadow_engine_file", lambda: str(tmp_path / "nope.json"))
    result = daily_metrics.shadow_vs_live_today(now_ts=time.time())
    assert result == {"total": 0, "promoted": 0, "not_promoted": 0, "dead_zone_penalized": 0,
                       "gate_reasons": {}, "patches_affected": {}, "top_discrepancy": None}


# ── shadow_vs_live_today() -- Пакет 6 М3: gate_reasons/patches_affected/top_discrepancy ──

def test_shadow_vs_live_gate_reasons_and_patches_counted(tmp_path, monkeypatch):
    now = 1_000_000.0
    shadow_file = tmp_path / "shadow_signals.json"
    shadow_file.write_text(json.dumps({
        "schema_version": 1,
        "records": [
            {"ts": now - 10, "promoted_live": False, "dead_zone": False,
             "gate_reasons": ["rr_gate", "rocket_or_grade"], "patches_affected": ["02-rr-gate"]},
            {"ts": now - 20, "promoted_live": False, "dead_zone": False,
             "gate_reasons": ["rocket_or_grade"], "patches_affected": ["02-rr-gate", "03-breaker-mitigation"]},
        ],
    }))
    monkeypatch.setattr(daily_metrics, "shadow_engine_file", lambda: str(shadow_file))
    result = daily_metrics.shadow_vs_live_today(now_ts=now)
    assert result["gate_reasons"] == {"rr_gate": 1, "rocket_or_grade": 2}
    assert result["patches_affected"] == {"02-rr-gate": 2, "03-breaker-mitigation": 1}


def test_shadow_vs_live_top_discrepancy_prefers_promoted(tmp_path, monkeypatch):
    now = 1_000_000.0
    shadow_file = tmp_path / "shadow_signals.json"
    shadow_file.write_text(json.dumps({
        "schema_version": 1,
        "records": [
            {"ts": now - 10, "symbol": "AAA", "direction": "long", "promoted_live": False,
             "dead_zone": False, "patches_affected": ["02-rr-gate", "03-breaker-mitigation",
                                                        "04-rsi-divergence"], "discrepancy": ["много патчей"]},
            {"ts": now - 20, "symbol": "BEAT", "direction": "short", "promoted_live": True,
             "dead_zone": False, "patches_affected": ["02-rr-gate"],
             "discrepancy": ["R:R 1.53 прошёл live-гейт (1.5), но НЕ прошёл бы shadow-гейт (2.0)"]},
        ],
    }))
    monkeypatch.setattr(daily_metrics, "shadow_engine_file", lambda: str(shadow_file))
    result = daily_metrics.shadow_vs_live_today(now_ts=now)
    td = result["top_discrepancy"]
    assert td["symbol"] == "BEAT"  # promoted побеждает даже с меньшим числом patches_affected
    assert td["promoted_live"] is True
    assert "shadow-гейт" in td["detail"]


def test_shadow_vs_live_top_discrepancy_falls_back_to_most_patches(tmp_path, monkeypatch):
    now = 1_000_000.0
    shadow_file = tmp_path / "shadow_signals.json"
    shadow_file.write_text(json.dumps({
        "schema_version": 1,
        "records": [
            {"ts": now - 10, "symbol": "AAA", "direction": "long", "promoted_live": False,
             "dead_zone": False, "patches_affected": ["02-rr-gate"], "discrepancy": []},
            {"ts": now - 20, "symbol": "ZZZ", "direction": "short", "promoted_live": False,
             "dead_zone": False, "patches_affected": ["02-rr-gate", "03-breaker-mitigation",
                                                        "05-bpr"], "discrepancy": []},
        ],
    }))
    monkeypatch.setattr(daily_metrics, "shadow_engine_file", lambda: str(shadow_file))
    result = daily_metrics.shadow_vs_live_today(now_ts=now)
    td = result["top_discrepancy"]
    assert td["symbol"] == "ZZZ"  # без promoted -- побеждает больше patches_affected
    assert td["promoted_live"] is False


def test_shadow_vs_live_top_discrepancy_none_when_no_records(tmp_path, monkeypatch):
    shadow_file = tmp_path / "shadow_signals.json"
    shadow_file.write_text(json.dumps({"schema_version": 1, "records": []}))
    monkeypatch.setattr(daily_metrics, "shadow_engine_file", lambda: str(shadow_file))
    result = daily_metrics.shadow_vs_live_today(now_ts=time.time())
    assert result["top_discrepancy"] is None


def test_shadow_vs_live_top_discrepancy_honest_detail_when_no_discrepancy_text(tmp_path, monkeypatch):
    now = 1_000_000.0
    shadow_file = tmp_path / "shadow_signals.json"
    shadow_file.write_text(json.dumps({
        "schema_version": 1,
        "records": [
            {"ts": now - 10, "symbol": "AAA", "direction": "long", "promoted_live": False,
             "dead_zone": False, "patches_affected": [], "discrepancy": []},
        ],
    }))
    monkeypatch.setattr(daily_metrics, "shadow_engine_file", lambda: str(shadow_file))
    result = daily_metrics.shadow_vs_live_today(now_ts=now)
    td = result["top_discrepancy"]
    assert "нет" in td["detail"]  # честно, не выдумывает текст расхождения, которого нет


# ── build_daily_digest() (интеграционно, всё замокано) ──

class _FakeBotModule:
    SOURCE_DISPLAY_LABELS = {"coingecko_markets": "CoinGecko markets",
                              "yahoo_finance": "Yahoo (DXY/S&P/Gold/VIX)"}

    @staticmethod
    def get_data_source_status():
        return {"coingecko_markets": {"ok": True}, "yahoo_finance": {"ok": False}}


def test_build_daily_digest_no_crash_on_empty_data(monkeypatch, tmp_path):
    monkeypatch.setattr(sj, "_journal", {})
    monkeypatch.setattr(daily_metrics, "shadow_engine_file", lambda: str(tmp_path / "nope.json"))
    monkeypatch.setattr(daily_metrics.whale_radar, "EVENTS_DIR", str(tmp_path))
    monkeypatch.setattr(daily_metrics.level_watch, "EVENTS_DIR", str(tmp_path))
    monkeypatch.setattr(daily_metrics.event_radar, "EVENTS_DIR", str(tmp_path / "event_radar_empty"))
    # security_log._events -- глобальное состояние процесса, могло быть засеяно другими
    # тестами (access_control.enforce() пишет в него по-настоящему) -- изолируем явно.
    monkeypatch.setattr(daily_metrics.security_log, "_events", [])
    text = daily_metrics.build_daily_digest(_FakeBotModule(), now_ts=1_000_000.0)
    assert "МЕТРИКИ ДНЯ" in text
    assert "н/д" in text  # честный win-rate при 0 закрытых
    assert "Security-лог" in text
    assert "Событий нет" in text  # честно, security_log._events пуст в тестовом процессе
    assert len(text) <= 4096


def test_build_daily_digest_shows_security_events(monkeypatch, tmp_path):
    monkeypatch.setattr(sj, "_journal", {})
    monkeypatch.setattr(daily_metrics, "shadow_engine_file", lambda: str(tmp_path / "nope.json"))
    monkeypatch.setattr(daily_metrics.whale_radar, "EVENTS_DIR", str(tmp_path))
    monkeypatch.setattr(daily_metrics.level_watch, "EVENTS_DIR", str(tmp_path))
    monkeypatch.setattr(daily_metrics.event_radar, "EVENTS_DIR", str(tmp_path / "event_radar_empty"))
    now = 1_000_000.0
    monkeypatch.setattr(daily_metrics.security_log, "_events", [
        {"ts": now - 10, "type": "denied", "chat_id": 1, "detail": ""},
        {"ts": now - 20, "type": "denied", "chat_id": 2, "detail": ""},
        {"ts": now - 30, "type": "grant", "chat_id": 3, "detail": ""},
    ])
    text = daily_metrics.build_daily_digest(_FakeBotModule(), now_ts=now)
    assert "Security-лог" in text
    assert "Всего: 3" in text
    assert "denied: 2" in text
    assert "grant: 1" in text


def test_build_daily_digest_shows_down_sources(monkeypatch, tmp_path):
    monkeypatch.setattr(sj, "_journal", {})
    monkeypatch.setattr(daily_metrics, "shadow_engine_file", lambda: str(tmp_path / "nope.json"))
    monkeypatch.setattr(daily_metrics.whale_radar, "EVENTS_DIR", str(tmp_path))
    monkeypatch.setattr(daily_metrics.level_watch, "EVENTS_DIR", str(tmp_path))
    monkeypatch.setattr(daily_metrics.event_radar, "EVENTS_DIR", str(tmp_path / "event_radar_empty"))
    text = daily_metrics.build_daily_digest(_FakeBotModule(), now_ts=1_000_000.0)
    # Находка 2026-07-14: раньше был сырой ключ "yahoo_finance: down" -- "_"
    # ломает parse_mode="Markdown" (см. ПАКЕТ 19 П0). Теперь -- человекочитаемая
    # метка без "_", как в welcome_text_v2().
    assert "Yahoo (DXY/S&P/Gold/VIX): down" in text
    assert "yahoo_finance" not in text
    assert text.count("_") % 2 == 0, "нечётное число '_' -- сломает parse_mode=\"Markdown\" в Telegram"


def test_build_daily_digest_shows_shadow_stats_breakdown(monkeypatch, tmp_path):
    now = 1_000_000.0
    shadow_file = tmp_path / "shadow_signals.json"
    shadow_file.write_text(json.dumps({
        "schema_version": 1,
        "records": [
            {"ts": now - 10, "symbol": "BEAT", "direction": "short", "promoted_live": True,
             "dead_zone": False, "gate_reasons": [], "patches_affected": ["02-rr-gate"],
             "discrepancy": ["R:R 1.53 прошёл live-гейт (1.5), но НЕ прошёл бы shadow-гейт (2.0)"]},
            {"ts": now - 20, "symbol": "AAA", "direction": "long", "promoted_live": False,
             "dead_zone": False, "gate_reasons": ["rocket_or_grade"],
             "patches_affected": ["03-breaker-mitigation"], "discrepancy": []},
        ],
    }))
    monkeypatch.setattr(sj, "_journal", {})
    monkeypatch.setattr(daily_metrics, "shadow_engine_file", lambda: str(shadow_file))
    monkeypatch.setattr(daily_metrics.whale_radar, "EVENTS_DIR", str(tmp_path))
    monkeypatch.setattr(daily_metrics.level_watch, "EVENTS_DIR", str(tmp_path))
    monkeypatch.setattr(daily_metrics.event_radar, "EVENTS_DIR", str(tmp_path / "event_radar_empty"))
    text = daily_metrics.build_daily_digest(_FakeBotModule(), now_ts=now)
    assert "Топ причин отказа" in text
    assert "rocket_or_grade" in text
    assert "Патчи 02-05" in text
    assert "02-rr-gate" in text
    assert "Топ-1 расхождение" in text
    assert "BEAT" in text
