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


def test_sync_aborts_on_get_error_does_not_attempt_put(monkeypatch):
    """Ключевой регрессионный тест: транзиентная ошибка GET не должна приводить к
    попытке PUT (риск создания файла без sha поверх существующего)."""
    put_called = {"count": 0}

    def fake_get_shadow_sync():
        return False, None  # симулирует ошибку GET

    def fake_put(*a, **kw):
        put_called["count"] += 1
        return "sha123"

    monkeypatch.setattr(se, "_github_get_shadow_sync", fake_get_shadow_sync)
    monkeypatch.setattr(se, "_github_put_shadow_sync", fake_put)
    monkeypatch.setattr(se.signal_journal, "_github_configured", lambda: True)

    ok = se._sync_to_github_sync({"symbol": "BTCUSDT", "ts": 111})
    assert ok is False
    assert put_called["count"] == 0


def test_sync_not_configured_returns_false_without_put(monkeypatch):
    put_called = {"count": 0}

    monkeypatch.setattr(se, "_github_get_shadow_sync", lambda: (None, None))
    monkeypatch.setattr(se, "_github_put_shadow_sync", lambda *a, **kw: put_called.__setitem__("count", put_called["count"] + 1))
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

    def fake_put(records, sha):
        put_payload["records"] = records
        put_payload["sha"] = sha
        return "new_sha"

    monkeypatch.setattr(se, "_github_put_shadow_sync", fake_put)
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
    monkeypatch.setattr(se, "_github_put_shadow_sync",
                         lambda *a, **kw: put_called.__setitem__("count", put_called["count"] + 1))
    monkeypatch.setattr(se.signal_journal, "_github_configured", lambda: True)

    ok = se._sync_to_github_sync({"symbol": "BTCUSDT", "ts": 111})
    assert ok is True
    assert put_called["count"] == 0


def test_sync_retries_once_on_conflict_then_succeeds(monkeypatch):
    calls = {"get": 0, "put": 0}

    def fake_get():
        calls["get"] += 1
        return [], f"sha_{calls['get']}"

    def fake_put(records, sha):
        calls["put"] += 1
        if calls["put"] == 1:
            return "conflict"
        return "new_sha"

    monkeypatch.setattr(se, "_load_local", lambda: [{"symbol": "BTCUSDT", "ts": 111}])
    monkeypatch.setattr(se, "_github_get_shadow_sync", fake_get)
    monkeypatch.setattr(se, "_github_put_shadow_sync", fake_put)
    monkeypatch.setattr(se.signal_journal, "_github_configured", lambda: True)

    ok = se._sync_to_github_sync({"symbol": "BTCUSDT", "ts": 111})
    assert ok is True
    assert calls["get"] == 2
    assert calls["put"] == 2
