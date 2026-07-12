"""
pytest для security_log.py -- аудит-журнал (Пакет SECURITY-HARDENING М4, владелец "да").
Файловый I/O изолирован через monkeypatch SECURITY_LOG_FILE на tmp-путь; GitHub-синк
не тестируется сетевыми вызовами -- только контракт _github_get_sync/_github_put_sync
через monkeypatch signal_journal._github_configured (unconfigured -- return None/False).
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import security_log as sl


def _reset_state(monkeypatch, tmp_path):
    monkeypatch.setattr(sl, "_events", [])
    monkeypatch.setattr(sl, "_dirty", False)
    monkeypatch.setattr(sl, "_github_sha", None)
    monkeypatch.setattr(sl, "SECURITY_LOG_FILE", str(tmp_path / "security_log.json"))


def test_log_event_appends_and_marks_dirty(monkeypatch, tmp_path):
    _reset_state(monkeypatch, tmp_path)
    sl.log_event(sl.EVENT_COMMAND, 111, "market")
    assert len(sl._events) == 1
    assert sl._events[0]["type"] == sl.EVENT_COMMAND
    assert sl._events[0]["chat_id"] == 111
    assert sl._events[0]["detail"] == "market"
    assert sl._dirty is True


def test_log_event_writes_to_disk(monkeypatch, tmp_path):
    _reset_state(monkeypatch, tmp_path)
    sl.log_event(sl.EVENT_DENIED, 222, "cmd=coin")
    with open(sl.SECURITY_LOG_FILE) as f:
        data = json.load(f)
    assert data["schema_version"] == 1
    assert len(data["events"]) == 1
    assert data["events"][0]["chat_id"] == 222


def test_log_event_never_raises_on_write_failure(monkeypatch, tmp_path):
    _reset_state(monkeypatch, tmp_path)
    monkeypatch.setattr(sl, "_atomic_write_json", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")))
    sl.log_event(sl.EVENT_COMMAND, 333, "x")  # не должно бросить исключение


def test_log_event_caps_at_max_local_events(monkeypatch, tmp_path):
    _reset_state(monkeypatch, tmp_path)
    monkeypatch.setattr(sl, "MAX_LOCAL_EVENTS", 5)
    for i in range(8):
        sl.log_event(sl.EVENT_COMMAND, i, str(i))
    assert len(sl._events) == 5
    # старые (0,1,2) обрезаны, остались последние 5 (3..7)
    assert [e["chat_id"] for e in sl._events] == [3, 4, 5, 6, 7]


def test_load_startup_events_reads_local_file(monkeypatch, tmp_path):
    _reset_state(monkeypatch, tmp_path)
    sl.log_event(sl.EVENT_COMMAND, 444, "a")
    monkeypatch.setattr(sl, "_events", [])
    sl.load_startup_events()
    assert len(sl._events) == 1
    assert sl._events[0]["chat_id"] == 444


def test_load_startup_events_missing_file_is_empty(monkeypatch, tmp_path):
    _reset_state(monkeypatch, tmp_path)
    sl.load_startup_events()
    assert sl._events == []


def test_get_daily_summary_empty(monkeypatch, tmp_path):
    _reset_state(monkeypatch, tmp_path)
    summary = sl.get_daily_summary(now_ts=1000.0)
    assert summary == {"total": 0, "by_type": {}}


def test_get_daily_summary_counts_within_window(monkeypatch, tmp_path):
    _reset_state(monkeypatch, tmp_path)
    now = 100000.0
    sl._events.extend([
        {"ts": now - 10, "type": sl.EVENT_COMMAND, "chat_id": 1, "detail": ""},
        {"ts": now - 20, "type": sl.EVENT_DENIED, "chat_id": 2, "detail": ""},
        {"ts": now - 30, "type": sl.EVENT_DENIED, "chat_id": 3, "detail": ""},
        {"ts": now - (25 * 3600), "type": sl.EVENT_COMMAND, "chat_id": 4, "detail": ""},  # за окном
    ])
    summary = sl.get_daily_summary(window_sec=24 * 3600, now_ts=now)
    assert summary["total"] == 3
    assert summary["by_type"] == {sl.EVENT_COMMAND: 1, sl.EVENT_DENIED: 2}


def test_get_daily_summary_window_boundary_inclusive(monkeypatch, tmp_path):
    _reset_state(monkeypatch, tmp_path)
    now = 100000.0
    window = 3600.0
    sl._events.append({"ts": now - window, "type": sl.EVENT_COMMAND, "chat_id": 1, "detail": ""})
    summary = sl.get_daily_summary(window_sec=window, now_ts=now)
    assert summary["total"] == 1


def test_github_get_sync_not_configured_returns_none(monkeypatch):
    monkeypatch.setattr(sl.signal_journal, "_github_configured", lambda: False)
    events, sha = sl._github_get_sync()
    assert events is None
    assert sha is None


def test_sync_to_github_skips_when_not_dirty(monkeypatch, tmp_path):
    _reset_state(monkeypatch, tmp_path)
    monkeypatch.setattr(sl.signal_journal, "_github_configured", lambda: True)
    monkeypatch.setattr(sl, "_dirty", False)
    result = asyncio.run(sl.sync_to_github())
    assert result is False


def test_sync_to_github_skips_when_not_configured(monkeypatch, tmp_path):
    _reset_state(monkeypatch, tmp_path)
    monkeypatch.setattr(sl.signal_journal, "_github_configured", lambda: False)
    monkeypatch.setattr(sl, "_dirty", True)
    result = asyncio.run(sl.sync_to_github())
    assert result is False


def test_sync_to_github_aborts_on_transient_get_error(monkeypatch, tmp_path):
    """Retry-catchup контракт (см. докстринг модуля) -- GET-ошибка (False) не должна
    приводить к PUT вслепую без sha."""
    _reset_state(monkeypatch, tmp_path)
    monkeypatch.setattr(sl.signal_journal, "_github_configured", lambda: True)
    monkeypatch.setattr(sl, "_dirty", True)
    monkeypatch.setattr(sl, "_github_get_sync", lambda: (False, None))
    put_called = []
    monkeypatch.setattr(sl, "_github_put_sync", lambda events, sha: put_called.append((events, sha)))
    result = asyncio.run(sl.sync_to_github())
    assert result is False
    assert put_called == []


def test_sync_to_github_success_path(monkeypatch, tmp_path):
    _reset_state(monkeypatch, tmp_path)
    sl._events.append({"ts": time.time(), "type": sl.EVENT_COMMAND, "chat_id": 1, "detail": ""})
    monkeypatch.setattr(sl, "_dirty", True)
    monkeypatch.setattr(sl.signal_journal, "_github_configured", lambda: True)
    monkeypatch.setattr(sl, "_github_get_sync", lambda: ([], "sha1"))
    monkeypatch.setattr(sl, "_github_put_sync", lambda events, sha: "sha2")
    result = asyncio.run(sl.sync_to_github())
    assert result is True
    assert sl._github_sha == "sha2"
    assert sl._dirty is False


def test_sync_to_github_retries_once_on_conflict(monkeypatch, tmp_path):
    _reset_state(monkeypatch, tmp_path)
    sl._events.append({"ts": time.time(), "type": sl.EVENT_COMMAND, "chat_id": 1, "detail": ""})
    monkeypatch.setattr(sl, "_dirty", True)
    monkeypatch.setattr(sl.signal_journal, "_github_configured", lambda: True)
    calls = {"n": 0}

    def _get():
        calls["n"] += 1
        return [], f"sha{calls['n']}"

    put_calls = []

    def _put(events, sha):
        put_calls.append(sha)
        if len(put_calls) == 1:
            return "conflict"
        return "sha_final"

    monkeypatch.setattr(sl, "_github_get_sync", _get)
    monkeypatch.setattr(sl, "_github_put_sync", _put)
    result = asyncio.run(sl.sync_to_github())
    assert result is True
    assert len(put_calls) == 2
