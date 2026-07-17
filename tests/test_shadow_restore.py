"""
pytest для shadow_engine restore-путей GitHub -> диск (владелец, утренний
пакет 2026-07-17 п.2): ни активный SHADOW_FILE, ни архив не имели пути
восстановления на старте при потере локального диска -- в отличие от
journal_persistence.py для остальных journal-файлов. Добавлены
restore_shadow_file_sync()/restore_archive_sync()/restore_all_from_github_
sync(), тот же принцип, что journal_persistence.restore_file_sync():
восстанавливает ТОЛЬКО если локального файла ещё нет, не перезаписывает
живущий на диске процесс.
"""
import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shadow_engine as se


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, raise_exc=None):
        self.status_code = status_code
        self._json_data = json_data
        self._raise_exc = raise_exc

    def raise_for_status(self):
        if self._raise_exc:
            raise self._raise_exc

    def json(self):
        return self._json_data


def _github_ready(monkeypatch):
    monkeypatch.setattr(se.signal_journal, "_github_configured", lambda: True)
    monkeypatch.setattr(se.signal_journal, "_validate_github_token", lambda: "")
    monkeypatch.setattr(se.signal_journal, "_github_api_base", lambda: "https://api.github.com/repos/x/y")
    monkeypatch.setattr(se.signal_journal, "_github_headers", lambda: {})


def _content_response(obj):
    return _FakeResponse(status_code=200, json_data={
        "encoding": "base64",
        "sha": "abc123",
        "content": base64.b64encode(json.dumps(obj, ensure_ascii=False).encode()).decode(),
    })


# --- restore_shadow_file_sync() ---

def test_restore_shadow_file_sync_noop_when_local_already_exists(monkeypatch, tmp_path):
    local = tmp_path / "shadow_signals.json"
    local.write_text('{"schema_version": 1, "records": []}')
    monkeypatch.setattr(se, "SHADOW_FILE", str(local))

    def boom(*a, **kw):
        raise AssertionError("не должно дёргать сеть, если локальный файл уже есть")

    monkeypatch.setattr(se, "_github_get_shadow_sync", boom)
    assert se.restore_shadow_file_sync() is False


def test_restore_shadow_file_sync_restores_when_missing(monkeypatch, tmp_path):
    local = tmp_path / "shadow_signals.json"
    monkeypatch.setattr(se, "SHADOW_FILE", str(local))
    fake_records = [{"symbol": "BTC", "ts": 1000, "contour": "live", "type": "signal"}]
    monkeypatch.setattr(se, "_github_get_shadow_sync", lambda: (fake_records, "sha1"))

    assert se.restore_shadow_file_sync() is True
    assert local.exists()
    data = json.loads(local.read_text())
    assert data["records"] == fake_records


def test_restore_shadow_file_sync_noop_when_github_empty(monkeypatch, tmp_path):
    local = tmp_path / "shadow_signals.json"
    monkeypatch.setattr(se, "SHADOW_FILE", str(local))
    monkeypatch.setattr(se, "_github_get_shadow_sync", lambda: ([], None))
    assert se.restore_shadow_file_sync() is False
    assert not local.exists()


# --- _github_list_archive_names_sync() ---

def test_github_list_archive_names_filters_to_shadow_signals_json(monkeypatch):
    _github_ready(monkeypatch)
    listing = _FakeResponse(status_code=200, json_data=[
        {"type": "file", "name": "shadow_signals_20260701_20260702.json"},
        {"type": "file", "name": "shadow_signals_20260703_20260704_2.json"},
        {"type": "file", "name": ".pushed.json"},
        {"type": "dir", "name": "subdir"},
    ])
    monkeypatch.setattr(se.requests, "get", lambda *a, **kw: listing)
    names = se._github_list_archive_names_sync()
    assert names == ["shadow_signals_20260701_20260702.json",
                      "shadow_signals_20260703_20260704_2.json"]


def test_github_list_archive_names_404_returns_empty(monkeypatch):
    _github_ready(monkeypatch)
    monkeypatch.setattr(se.requests, "get", lambda *a, **kw: _FakeResponse(status_code=404))
    assert se._github_list_archive_names_sync() == []


# --- restore_archive_sync() ---

def test_restore_archive_sync_skips_existing_restores_missing(monkeypatch, tmp_path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    existing = archive_dir / "shadow_signals_20260701_20260702.json"
    existing.write_text('{"schema_version": 1, "records": [{"already": "here"}]}')
    monkeypatch.setattr(se, "ARCHIVE_DIR", str(archive_dir))

    monkeypatch.setattr(se, "_github_list_archive_names_sync", lambda: [
        "shadow_signals_20260701_20260702.json",   # already local -- must NOT be touched
        "shadow_signals_20260703_20260704.json",   # missing -- must be restored
    ])

    fetched = {}

    def fake_get_json(repo_path):
        fetched[repo_path] = fetched.get(repo_path, 0) + 1
        return {"schema_version": 1, "records": [{"symbol": "ETH", "ts": 2000}]}

    monkeypatch.setattr(se, "_github_get_json_file_sync", fake_get_json)

    result = se.restore_archive_sync()
    assert result == {"attempted": 1, "restored": 1}

    # existing file untouched (idempotent, no fetch attempted for it)
    assert "journal/archive/shadow_signals_20260701_20260702.json" not in fetched
    assert json.loads(existing.read_text())["records"] == [{"already": "here"}]

    new_file = archive_dir / "shadow_signals_20260703_20260704.json"
    assert new_file.exists()
    assert json.loads(new_file.read_text())["records"] == [{"symbol": "ETH", "ts": 2000}]


def test_restore_archive_sync_survives_fetch_failure_for_one_file(monkeypatch, tmp_path):
    archive_dir = tmp_path / "archive"
    monkeypatch.setattr(se, "ARCHIVE_DIR", str(archive_dir))
    monkeypatch.setattr(se, "_github_list_archive_names_sync", lambda: [
        "shadow_signals_a.json", "shadow_signals_b.json",
    ])

    def fake_get_json(repo_path):
        if repo_path.endswith("shadow_signals_a.json"):
            return None  # simulated fetch failure
        return {"schema_version": 1, "records": []}

    monkeypatch.setattr(se, "_github_get_json_file_sync", fake_get_json)
    result = se.restore_archive_sync()
    assert result == {"attempted": 2, "restored": 1}
    assert not (archive_dir / "shadow_signals_a.json").exists()
    assert (archive_dir / "shadow_signals_b.json").exists()


def test_restore_archive_sync_noop_when_github_has_no_files(monkeypatch, tmp_path):
    archive_dir = tmp_path / "archive"
    monkeypatch.setattr(se, "ARCHIVE_DIR", str(archive_dir))
    monkeypatch.setattr(se, "_github_list_archive_names_sync", lambda: [])
    result = se.restore_archive_sync()
    assert result == {"attempted": 0, "restored": 0}
    assert not archive_dir.exists()  # не создаём директорию впустую


# --- restore_all_from_github_sync() ---

def test_restore_all_from_github_sync_combines_both_and_is_fail_soft(monkeypatch):
    monkeypatch.setattr(se, "restore_shadow_file_sync", lambda: True)
    monkeypatch.setattr(se, "restore_archive_sync", lambda: {"attempted": 3, "restored": 2})
    result = se.restore_all_from_github_sync()
    assert result == {"shadow_restored": True, "attempted": 3, "restored": 2}


def test_restore_all_from_github_sync_survives_shadow_restore_exception(monkeypatch):
    def boom():
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(se, "restore_shadow_file_sync", boom)
    monkeypatch.setattr(se, "restore_archive_sync", lambda: {"attempted": 0, "restored": 0})
    result = se.restore_all_from_github_sync()  # не должно бросить исключение
    assert result["shadow_restored"] is False


def test_restore_all_from_github_sync_survives_archive_restore_exception(monkeypatch):
    def boom():
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(se, "restore_shadow_file_sync", lambda: False)
    monkeypatch.setattr(se, "restore_archive_sync", boom)
    result = se.restore_all_from_github_sync()  # не должно бросить исключение
    assert result == {"shadow_restored": False, "attempted": 0, "restored": 0}
