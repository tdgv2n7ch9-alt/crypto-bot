"""
pytest для journal_persistence.py (владелец, 2026-07-16, КРИТИЧНО): journal/
на Railway эфемерный -- редеплой стирает state-файлы мониторов и AKE
wallet-поллер. Живая находка: собственный редеплой стёр 3-часовую историю
bsc_wallet_events.json посреди срочного AKE-расследования.

Покрывает: restore (только если локального файла ещё нет, не перезаписывает
живое состояние), sync (push с ретраем на 409-конфликт), обнаружение
zone_alert_state_*.json.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import journal_persistence as jp


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(jp, "_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(jp.signal_journal, "_github_configured", lambda: True)


# ── restore_file_sync() ──────────────────────────────────────────────────

def test_restore_file_sync_writes_when_local_missing(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    remote_obj = {"stage": "WATCHING_HL"}
    monkeypatch.setattr(jp, "_get_file_sync", lambda repo_path: (remote_obj, "sha123"))

    ok = jp.restore_file_sync("journal/bank_setup_state.json")
    assert ok is True
    with open(tmp_path / "journal" / "bank_setup_state.json") as f:
        assert json.load(f) == remote_obj


def test_restore_file_sync_does_not_overwrite_existing_local(monkeypatch, tmp_path):
    """Живое состояние (тот же процесс, не свежий редеплой) НЕ перетирается
    даже если в GitHub лежит другое содержимое."""
    _isolate(monkeypatch, tmp_path)
    local = tmp_path / "journal" / "bank_setup_state.json"
    local.parent.mkdir(parents=True)
    local.write_text(json.dumps({"stage": "INVALIDATED"}))
    monkeypatch.setattr(jp, "_get_file_sync", lambda repo_path: ({"stage": "WATCHING_HL"}, "sha"))

    ok = jp.restore_file_sync("journal/bank_setup_state.json")
    assert ok is False
    with open(local) as f:
        assert json.load(f) == {"stage": "INVALIDATED"}  # не тронуто


def test_restore_file_sync_honest_noop_when_not_on_github(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(jp, "_get_file_sync", lambda repo_path: (None, None))
    ok = jp.restore_file_sync("journal/bank_setup_state.json")
    assert ok is False
    assert not (tmp_path / "journal" / "bank_setup_state.json").exists()


# ── restore_all_sync() ───────────────────────────────────────────────────

def test_restore_all_sync_restores_known_files_and_zone_alert_states(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    remote_files = {
        "journal/bank_setup_state.json": ({"stage": "WATCHING_HL"}, "sha1"),
        "journal/ake_setup_state.json": ({"armed": {}}, "sha2"),
        "journal/bsc_wallet_events.json": ([], "sha3"),
        "journal/bsc_wallet_monitor_state.json": ({"last_scanned_block": 1}, "sha4"),
        "journal/pump_radar_state.json": ({}, "sha5"),
        "journal/zone_alert_state_gramusdt.json": ({"armed": {}}, "sha6"),
    }

    def fake_get(repo_path):
        return remote_files.get(repo_path, (None, None))

    monkeypatch.setattr(jp, "_get_file_sync", fake_get)
    monkeypatch.setattr(jp, "_list_journal_dir_sync", lambda: [
        "bank_setup_state.json", "zone_alert_state_gramusdt.json", "watch_zones.json",
    ])

    result = jp.restore_all_sync()
    restored = set(result["restored"])
    assert "journal/bank_setup_state.json" in restored
    assert "journal/zone_alert_state_gramusdt.json" in restored
    assert "journal/watch_zones.json" not in restored  # не в списке -- не наш файл


def test_restore_all_sync_skips_files_already_present_locally(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    local = tmp_path / "journal" / "bank_setup_state.json"
    local.parent.mkdir(parents=True)
    local.write_text(json.dumps({"stage": "LIVE"}))

    monkeypatch.setattr(jp, "_get_file_sync", lambda repo_path: ({"stage": "STALE"}, "sha"))
    monkeypatch.setattr(jp, "_list_journal_dir_sync", lambda: [])

    result = jp.restore_all_sync()
    assert "journal/bank_setup_state.json" not in result["restored"]
    with open(local) as f:
        assert json.load(f)["stage"] == "LIVE"


# ── sync_file_sync() / sync_all_sync() ───────────────────────────────────

def test_sync_file_sync_pushes_local_content(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    local = tmp_path / "journal" / "bank_setup_state.json"
    local.parent.mkdir(parents=True)
    local.write_text(json.dumps({"stage": "WATCHING_HL"}))

    put_calls = []

    monkeypatch.setattr(jp, "_get_file_sync", lambda repo_path: (None, "sha-current"))

    def fake_put(repo_path, obj, sha):
        put_calls.append((repo_path, obj, sha))
        return "sha-new"

    monkeypatch.setattr(jp, "_put_file_sync", fake_put)
    ok = jp.sync_file_sync("journal/bank_setup_state.json")
    assert ok is True
    assert put_calls == [("journal/bank_setup_state.json", {"stage": "WATCHING_HL"}, "sha-current")]


def test_sync_file_sync_retries_once_on_conflict(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    local = tmp_path / "journal" / "bank_setup_state.json"
    local.parent.mkdir(parents=True)
    local.write_text(json.dumps({"stage": "WATCHING_HL"}))

    monkeypatch.setattr(jp, "_get_file_sync", lambda repo_path: (None, "sha-x"))
    calls = {"n": 0}

    def fake_put(repo_path, obj, sha):
        calls["n"] += 1
        if calls["n"] == 1:
            return "conflict"
        return "sha-new"

    monkeypatch.setattr(jp, "_put_file_sync", fake_put)
    ok = jp.sync_file_sync("journal/bank_setup_state.json")
    assert ok is True
    assert calls["n"] == 2


def test_sync_file_sync_honest_false_when_local_missing(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    ok = jp.sync_file_sync("journal/bank_setup_state.json")
    assert ok is False


def test_sync_all_sync_includes_discovered_zone_alert_files(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    (journal_dir / "bank_setup_state.json").write_text(json.dumps({"stage": "WATCHING_HL"}))
    (journal_dir / "zone_alert_state_avaxusdt.json").write_text(json.dumps({"armed": {}}))

    monkeypatch.setattr(jp, "_get_file_sync", lambda repo_path: (None, None))
    monkeypatch.setattr(jp, "_put_file_sync", lambda repo_path, obj, sha: "sha-new")

    result = jp.sync_all_sync()
    assert "journal/bank_setup_state.json" in result["synced"]
    assert "journal/zone_alert_state_avaxusdt.json" in result["synced"]


# ── sync_all() (async wrapper) ───────────────────────────────────────────

def test_sync_all_async_uses_injected_executor(monkeypatch, tmp_path):
    import asyncio
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(jp, "sync_all_sync", lambda: {"synced": [], "attempted": 0})

    calls = []
    async def fake_executor(fn, *a):
        calls.append(fn)
        return fn(*a)

    result = asyncio.run(jp.sync_all(bot=None, run_in_executor_fn=fake_executor))
    assert result == {"synced": [], "attempted": 0}
    assert calls == [jp.sync_all_sync]
