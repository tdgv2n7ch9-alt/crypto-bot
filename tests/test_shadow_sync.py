"""
pytest для shadow_engine._github_get_shadow_sync()/_sync_to_github_sync() -- Пакет 11 М1
(находка ночного цикла 2026-07-12/13, см. SHADOW_ANALYSIS.md запись 23:42):

1) Раньше транзиентный сбой GET (сеть/парсинг) возвращал тот же (None, None), что и
   "файла ещё нет" (404) -- вызывающий код трактовал ошибку как пустой файл и пытался
   PUT без sha (422 на существующий файл; в худшем случае риск затирания). Тест
   проверяет, что теперь это два РАЗНЫХ, различимых исхода.
2) Раньше _sync_to_github_sync() пушила ТОЛЬКО запись текущего вызова -- если прошлый
   вызов не смог синкнуться, его запись терялась для GitHub-копии навсегда (пока не
   будет передана снова явно, что не происходит). Тест проверяет ретрай-catchup: синк
   теперь подтягивает весь локальный хвост, которого ещё нет в remote.
"""
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


def test_github_get_shadow_sync_404_returns_empty_list_not_none(monkeypatch):
    _github_ready(monkeypatch)
    monkeypatch.setattr(se.requests, "get", lambda *a, **kw: _FakeResponse(status_code=404))
    records, sha = se._github_get_shadow_sync()
    assert records == []
    assert sha is None


def test_github_get_shadow_sync_network_error_returns_false_not_empty_list(monkeypatch):
    _github_ready(monkeypatch)

    def boom(*a, **kw):
        raise ConnectionError("simulated network failure")

    monkeypatch.setattr(se.requests, "get", boom)
    records, sha = se._github_get_shadow_sync()
    assert records is False  # НЕ [] и НЕ None -- отдельный сигнал "ошибка, не пустой файл"
    assert sha is None


def test_github_get_shadow_sync_parse_error_returns_false(monkeypatch):
    _github_ready(monkeypatch)

    class _BadJsonResponse(_FakeResponse):
        def json(self):
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

    monkeypatch.setattr(se.requests, "get", lambda *a, **kw: _BadJsonResponse(status_code=200))
    records, sha = se._github_get_shadow_sync()
    assert records is False
    assert sha is None


def test_github_get_shadow_sync_not_configured_returns_none_none(monkeypatch):
    monkeypatch.setattr(se.signal_journal, "_github_configured", lambda: False)
    records, sha = se._github_get_shadow_sync()
    assert records is None
    assert sha is None


# --- НАХОДКА 2026-07-13 (владелец "да" -- Находка 1): Contents API отдаёт
# encoding="none"/content="" для файлов >1MB -- json.loads("") даёт ровно
# "Expecting value: line 1 column 1 (char 0)", подтверждено живьём прямым запросом
# (journal/shadow_signals.json = 1 049 083 байт на момент находки). Фикс -- Git Blobs
# API fallback (лимит 100MB) по тому же sha, вместо капа файла (shadow_signals.json
# сознательно НЕ капается, см. докстринг модуля). -----------------------------------

import base64
import json


def test_github_get_shadow_sync_large_file_falls_back_to_blob_api(monkeypatch):
    """encoding=none/content="" (файл >1MB) -> второй запрос к git/blobs/{sha},
    декодируется корректно."""
    _github_ready(monkeypatch)
    records_payload = {"schema_version": 1, "records": [{"symbol": "BTCUSDT", "ts": 111.0}]}
    blob_content_b64 = base64.b64encode(json.dumps(records_payload).encode()).decode()

    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        if "/git/blobs/" in url:
            assert url.endswith("/git/blobs/abc123sha")
            return _FakeResponse(status_code=200, json_data={"content": blob_content_b64})
        return _FakeResponse(status_code=200, json_data={
            "content": "", "encoding": "none", "sha": "abc123sha",
        })

    monkeypatch.setattr(se.requests, "get", fake_get)
    records, sha = se._github_get_shadow_sync()

    assert len(calls) == 2  # Contents API, затем Git Blobs API
    assert records == [{"symbol": "BTCUSDT", "ts": 111.0}]
    assert sha == "abc123sha"


def test_github_get_shadow_sync_small_file_does_not_call_blob_api(monkeypatch):
    """Файл <1MB -- encoding="base64" как обычно, НЕ должен делать второй запрос
    (регрессия -- fallback только для encoding=='none')."""
    _github_ready(monkeypatch)
    records_payload = {"schema_version": 1, "records": [{"symbol": "ETHUSDT", "ts": 222.0}]}
    content_b64 = base64.b64encode(json.dumps(records_payload).encode()).decode()

    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        return _FakeResponse(status_code=200, json_data={
            "content": content_b64, "encoding": "base64", "sha": "def456sha",
        })

    monkeypatch.setattr(se.requests, "get", fake_get)
    records, sha = se._github_get_shadow_sync()

    assert len(calls) == 1  # только Contents API, blob fallback не тронут
    assert records == [{"symbol": "ETHUSDT", "ts": 222.0}]
    assert sha == "def456sha"


def test_github_get_shadow_sync_blob_api_failure_returns_false_not_crash(monkeypatch):
    """Если и блоб-запрос падает -- честная ошибка (False, None), не крэш и не
    подмена на пустой файл."""
    _github_ready(monkeypatch)

    def fake_get(url, headers=None, timeout=None):
        if "/git/blobs/" in url:
            raise ConnectionError("blob fetch failed")
        return _FakeResponse(status_code=200, json_data={
            "content": "", "encoding": "none", "sha": "abc123sha",
        })

    monkeypatch.setattr(se.requests, "get", fake_get)
    records, sha = se._github_get_shadow_sync()
    assert records is False
    assert sha is None


def test_sync_aborts_on_get_error_does_not_attempt_put(monkeypatch):
    """Ключевой регрессионный тест: транзиентная ошибка GET не должна приводить к
    попытке PUT (риск создания файла без sha поверх существующего)."""
    put_called = {"count": 0}

    def fake_get_shadow_sync():
        return False, None  # симулирует ошибку GET

    def fake_put(*a, **kw):
        put_called["count"] += 1
        return True

    monkeypatch.setattr(se, "_github_get_shadow_sync", fake_get_shadow_sync)
    monkeypatch.setattr(se, "_github_put_shadow_via_data_api", fake_put)
    monkeypatch.setattr(se.signal_journal, "_github_configured", lambda: True)

    ok = se._sync_to_github_sync({"symbol": "BTCUSDT", "ts": 111})
    assert ok is False
    assert put_called["count"] == 0


def test_sync_not_configured_returns_false_without_put(monkeypatch):
    put_called = {"count": 0}

    monkeypatch.setattr(se, "_github_get_shadow_sync", lambda: (None, None))
    monkeypatch.setattr(se, "_github_put_shadow_via_data_api", lambda *a, **kw: put_called.__setitem__("count", put_called["count"] + 1))
    monkeypatch.setattr(se.signal_journal, "_github_configured", lambda: False)

    ok = se._sync_to_github_sync({"symbol": "BTCUSDT", "ts": 111})
    assert ok is False
    assert put_called["count"] == 0


def test_sync_catches_up_local_backlog_not_just_current_record(monkeypatch):
    """Регрессия на находку 2026-07-12/13: прошлая запись, не ушедшая в GitHub из-за
    сбоя предыдущего синка, должна попасть в PUT вместе с новой -- не потеряться."""
    local_backlog = [
        {"symbol": "OLDCOIN", "ts": 100, "source": "send_scheduled"},   # не ушла в прошлый раз
        {"symbol": "NEWCOIN", "ts": 200, "source": "send_scheduled"},   # текущий вызов
    ]
    monkeypatch.setattr(se, "_load_local", lambda: local_backlog)
    monkeypatch.setattr(se, "_github_get_shadow_sync", lambda: ([], "remote_sha"))

    put_payload = {}

    def fake_put(records):
        put_payload["records"] = records
        return True

    monkeypatch.setattr(se, "_github_put_shadow_via_data_api", fake_put)
    monkeypatch.setattr(se.signal_journal, "_github_configured", lambda: True)

    ok = se._sync_to_github_sync(local_backlog[-1])
    assert ok is True
    pushed_keys = {(r["symbol"], r["ts"]) for r in put_payload["records"]}
    assert ("OLDCOIN", 100) in pushed_keys
    assert ("NEWCOIN", 200) in pushed_keys


def test_sync_no_missing_records_returns_true_without_put(monkeypatch):
    """Если remote уже содержит всё, что есть локально (например, прошлый синк на самом
    деле удался, несмотря на что-то ещё) -- не делаем лишний PUT."""
    local_records = [{"symbol": "BTCUSDT", "ts": 111}]
    monkeypatch.setattr(se, "_load_local", lambda: local_records)
    monkeypatch.setattr(se, "_github_get_shadow_sync", lambda: (local_records, "remote_sha"))

    put_called = {"count": 0}
    monkeypatch.setattr(se, "_github_put_shadow_via_data_api",
                         lambda *a, **kw: put_called.__setitem__("count", put_called["count"] + 1))
    monkeypatch.setattr(se.signal_journal, "_github_configured", lambda: True)

    ok = se._sync_to_github_sync({"symbol": "BTCUSDT", "ts": 111})
    assert ok is True
    assert put_called["count"] == 0


# --- GitHub-422 вариант C: _github_put_shadow_via_data_api() (Git Data API) ---

def test_put_via_data_api_pushes_blob_tree_commit_ref(monkeypatch):
    """Полный happy-path: GET ref -> GET commit -> POST blob -> POST tree ->
    POST commit -> PATCH ref, все 6 запросов в правильном порядке."""
    _github_ready(monkeypatch)
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(("GET", url))
        if url.endswith("/git/refs/heads/main"):
            return _FakeResponse(json_data={"object": {"sha": "base_commit_sha"}})
        if url.endswith("/git/commits/base_commit_sha"):
            return _FakeResponse(json_data={"tree": {"sha": "base_tree_sha"}})
        raise AssertionError(f"unexpected GET {url}")

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(("POST", url))
        if url.endswith("/git/blobs"):
            return _FakeResponse(json_data={"sha": "blob_sha"})
        if url.endswith("/git/trees"):
            assert json["base_tree"] == "base_tree_sha"
            return _FakeResponse(json_data={"sha": "new_tree_sha"})
        if url.endswith("/git/commits"):
            assert json["tree"] == "new_tree_sha"
            assert json["parents"] == ["base_commit_sha"]
            return _FakeResponse(json_data={"sha": "new_commit_sha"})
        raise AssertionError(f"unexpected POST {url}")

    def fake_patch(url, headers=None, json=None, timeout=None):
        calls.append(("PATCH", url))
        assert json["sha"] == "new_commit_sha"
        return _FakeResponse(json_data={})

    monkeypatch.setattr(se.requests, "get", fake_get)
    monkeypatch.setattr(se.requests, "post", fake_post)
    monkeypatch.setattr(se.requests, "patch", fake_patch)

    ok = se._github_put_shadow_via_data_api([{"symbol": "BTCUSDT", "ts": 111}])
    assert ok is True
    assert [c[0] for c in calls] == ["GET", "GET", "POST", "POST", "POST", "PATCH"]


def test_put_via_data_api_retries_on_ref_race_then_succeeds(monkeypatch):
    """Ref сдвинулся между GET и PATCH (конкурентный коммит) -- PATCH падает
    на первой попытке, вторая попытка со свежим ref/base_tree проходит."""
    _github_ready(monkeypatch)
    attempt = {"n": 0}

    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/git/refs/heads/main"):
            return _FakeResponse(json_data={"object": {"sha": f"commit_{attempt['n']}"}})
        return _FakeResponse(json_data={"tree": {"sha": f"tree_{attempt['n']}"}})

    def fake_post(url, headers=None, json=None, timeout=None):
        if url.endswith("/git/blobs"):
            return _FakeResponse(json_data={"sha": "blob_sha"})
        if url.endswith("/git/trees"):
            return _FakeResponse(json_data={"sha": "new_tree_sha"})
        return _FakeResponse(json_data={"sha": "new_commit_sha"})

    def fake_patch(url, headers=None, json=None, timeout=None):
        attempt["n"] += 1
        if attempt["n"] == 1:
            import requests as _requests
            resp = _FakeResponse(status_code=422, raise_exc=_requests.exceptions.HTTPError("422 ref race"))
            resp.raise_for_status()
        return _FakeResponse(json_data={})

    monkeypatch.setattr(se.requests, "get", fake_get)
    monkeypatch.setattr(se.requests, "post", fake_post)
    monkeypatch.setattr(se.requests, "patch", fake_patch)

    ok = se._github_put_shadow_via_data_api([{"symbol": "BTCUSDT", "ts": 111}])
    assert ok is True
    assert attempt["n"] == 2  # первая попытка упала, вторая (свежий ref) прошла


def test_put_via_data_api_gives_up_after_max_attempts(monkeypatch):
    """Устойчивая ошибка (не транзиентная гонка) -- не зависает, честно False
    после исчерпания попыток."""
    _github_ready(monkeypatch)

    def boom(*a, **kw):
        raise ConnectionError("persistent network failure")

    monkeypatch.setattr(se.requests, "get", boom)

    ok = se._github_put_shadow_via_data_api([{"symbol": "BTCUSDT", "ts": 111}], max_attempts=3)
    assert ok is False


def test_put_via_data_api_not_configured_returns_false(monkeypatch):
    monkeypatch.setattr(se.signal_journal, "_github_configured", lambda: False)
    ok = se._github_put_shadow_via_data_api([{"symbol": "BTCUSDT", "ts": 111}])
    assert ok is False


# --- Пакет 11 (owner-запрос "целостность shadow-окон"): integrity_report() ---

def test_integrity_report_empty_records_is_honest_not_fabricated():
    report = se.integrity_report([])
    assert report["total"] == 0
    assert report["schema_ok"] is True
    assert report["duplicate_count"] == 0
    assert report["out_of_order_count"] == 0


def test_integrity_report_none_records_treated_as_empty():
    report = se.integrity_report(None)
    assert report["total"] == 0


def test_integrity_report_clean_records_no_false_positives():
    records = [
        {"symbol": "BTCUSDT", "ts": 100},
        {"symbol": "ETHUSDT", "ts": 101},
        {"symbol": "BNBUSDT", "ts": 102},
    ]
    report = se.integrity_report(records)
    assert report["total"] == 3
    assert report["schema_ok"] is True
    assert report["duplicate_count"] == 0
    assert report["out_of_order_count"] == 0


def test_integrity_report_detects_duplicate_symbol_ts_key():
    records = [
        {"symbol": "BTCUSDT", "ts": 100},
        {"symbol": "BTCUSDT", "ts": 100},  # дубль
        {"symbol": "ETHUSDT", "ts": 101},
    ]
    report = se.integrity_report(records)
    assert report["duplicate_count"] == 1
    assert report["duplicate_keys"][0]["key"] == ("BTCUSDT", 100)
    assert report["duplicate_keys"][0]["count"] == 2


def test_integrity_report_detects_out_of_order_ts_same_symbol():
    records = [
        {"symbol": "BTCUSDT", "ts": 100},
        {"symbol": "BTCUSDT", "ts": 50},   # раньше предыдущей записи ТОГО ЖЕ символа -- нарушение
        {"symbol": "BNBUSDT", "ts": 102},
    ]
    report = se.integrity_report(records)
    assert report["out_of_order_count"] == 1


def test_integrity_report_cross_symbol_interleave_not_out_of_order():
    """Владелец, задача #281 (2026-07-19, живая находка JASMY/SOL 51мс):
    разные символы пишутся независимо и не обязаны идти по возрастанию ts
    друг относительно друга -- межсимвольное чередование НЕ нарушение порядка."""
    records = [
        {"symbol": "JASMYUSDT", "ts": 100.42},
        {"symbol": "SOLUSDT", "ts": 100.37},  # раньше предыдущей строки, но ДРУГОЙ символ
        {"symbol": "BNBUSDT", "ts": 102},
    ]
    report = se.integrity_report(records)
    assert report["out_of_order_count"] == 0


def test_integrity_report_detects_missing_symbol_or_ts():
    records = [
        {"symbol": "BTCUSDT", "ts": 100},
        {"symbol": None, "ts": 101},
        {"symbol": "ETHUSDT", "ts": None},
    ]
    report = se.integrity_report(records)
    assert report["schema_ok"] is False
    assert set(report["schema_bad_indices"]) == {1, 2}
