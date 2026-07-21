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
    monkeypatch.setattr(se, "_push_shadow_via_git_cli", fake_put)
    monkeypatch.setattr(se.signal_journal, "_github_configured", lambda: True)

    ok = se._sync_to_github_sync({"symbol": "BTCUSDT", "ts": 111})
    assert ok is False
    assert put_called["count"] == 0


def test_sync_not_configured_returns_false_without_put(monkeypatch):
    put_called = {"count": 0}

    monkeypatch.setattr(se, "_github_get_shadow_sync", lambda: (None, None))
    monkeypatch.setattr(se, "_push_shadow_via_git_cli", lambda *a, **kw: put_called.__setitem__("count", put_called["count"] + 1))
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

    monkeypatch.setattr(se, "_push_shadow_via_git_cli", fake_put)
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
    monkeypatch.setattr(se, "_push_shadow_via_git_cli",
                         lambda *a, **kw: put_called.__setitem__("count", put_called["count"] + 1))
    monkeypatch.setattr(se.signal_journal, "_github_configured", lambda: True)

    ok = se._sync_to_github_sync({"symbol": "BTCUSDT", "ts": 111})
    assert ok is True
    assert put_called["count"] == 0


def test_sync_pushes_local_state_to_shrink_remote_after_rotation(monkeypatch):
    """Владелец, P0 2026-07-21 (живая находка при верификации): count-based
    ротация (введена этим же пакетом) регулярно усыхает активный файл --
    remote ДОЛЖЕН зеркалить это усыхание, а не только дополняться. Remote
    содержит запись, которую локальная ротация УЖЕ убрала из активного
    файла (перенесла в архив) -- синк обязан отправить УМЕНЬШЕННЫЙ локальный
    список, не remote+missing (это бы навсегда сохранило устаревшую запись
    и не дало remote усохнуть -- тот же класс проблемы, из-за которой
    затевался весь P0)."""
    remote_records = [{"symbol": "OLD_ROTATED_OUT", "ts": 1}, {"symbol": "STILL_ACTIVE", "ts": 2}]
    local_records = [{"symbol": "STILL_ACTIVE", "ts": 2}]  # OLD_ROTATED_OUT уже в архиве, не в active

    monkeypatch.setattr(se, "_load_local", lambda: local_records)
    monkeypatch.setattr(se, "_github_get_shadow_sync", lambda: (remote_records, "remote_sha"))
    monkeypatch.setattr(se.signal_journal, "_github_configured", lambda: True)

    pushed = {}

    def fake_push(records):
        pushed["records"] = records
        return True

    monkeypatch.setattr(se, "_push_shadow_via_git_cli", fake_push)
    ok = se._sync_to_github_sync()
    assert ok is True
    # push отправляет ТОЛЬКО текущий локальный (усохший) список -- не remote+local
    assert pushed["records"] == local_records
    assert {r["symbol"] for r in pushed["records"]} == {"STILL_ACTIVE"}


# --- P0 2026-07-21 (рецидив 40МБ/GitHub-422): _push_shadow_via_git_cli() ---
# (нативный git push, ЗАМЕНЯЕТ REST Git Data API -- см. её докстринг)

def _fake_run_git_dispatcher(script: dict):
    """Строит fake _run_git(args, cwd, ...) -- ключ script -- первый элемент
    args (git-подкоманда), значение -- (rc, out, err) либо callable(args) ->
    (rc, out, err) для команд, которым нужна логика по попытке (push-race)."""
    def _fake(args, cwd=None, timeout=None, env=None):
        key = args[0]
        entry = script.get(key, (0, "", ""))
        if callable(entry):
            return entry(args)
        return entry
    return _fake


def test_push_via_git_cli_happy_path(monkeypatch, tmp_path):
    """Полный happy-path: sparse-checkout init/set -> fetch -> checkout ->
    add -> commit -> push, все команды в правильном порядке, push успешен
    с первой попытки."""
    _github_ready(monkeypatch)
    monkeypatch.setattr(se, "_GIT_SYNC_DIR", str(tmp_path))
    monkeypatch.setattr(se, "_ensure_git_sync_dir", lambda: True)

    calls = []

    def fake(args, cwd=None, timeout=None, env=None):
        calls.append(args[0])
        return (0, "", "")

    monkeypatch.setattr(se, "_run_git", fake)
    ok = se._push_shadow_via_git_cli([{"symbol": "BTCUSDT", "ts": 111}])
    assert ok is True
    assert calls == ["fetch", "checkout", "add", "commit", "push"]
    # рабочий файл реально записан перед add/commit
    with open(os.path.join(str(tmp_path), "journal", "shadow_signals.json"), encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["records"] == [{"symbol": "BTCUSDT", "ts": 111}]


def test_push_via_git_cli_retries_on_push_rejection_then_succeeds(monkeypatch, tmp_path):
    """Push отклонён (гонка с другим пушем) на первой попытке -- вторая
    попытка (свежий fetch+rebuild) проходит."""
    _github_ready(monkeypatch)
    monkeypatch.setattr(se, "_GIT_SYNC_DIR", str(tmp_path))
    monkeypatch.setattr(se, "_ensure_git_sync_dir", lambda: True)

    attempt = {"n": 0}

    def fake(args, cwd=None, timeout=None, env=None):
        if args[0] == "push":
            attempt["n"] += 1
            if attempt["n"] == 1:
                return (1, "", "! [rejected] main -> main (fetch first)")
            return (0, "", "")
        return (0, "", "")

    monkeypatch.setattr(se, "_run_git", fake)
    ok = se._push_shadow_via_git_cli([{"symbol": "BTCUSDT", "ts": 111}])
    assert ok is True
    assert attempt["n"] == 2


def test_push_via_git_cli_gives_up_after_max_attempts(monkeypatch, tmp_path):
    """Устойчивая ошибка (не транзиентная гонка) -- не зависает, честно False
    после исчерпания попыток."""
    _github_ready(monkeypatch)
    monkeypatch.setattr(se, "_GIT_SYNC_DIR", str(tmp_path))
    monkeypatch.setattr(se, "_ensure_git_sync_dir", lambda: True)
    monkeypatch.setattr(se, "_run_git", lambda args, cwd=None, timeout=None, env=None: (1, "", "persistent failure"))

    ok = se._push_shadow_via_git_cli([{"symbol": "BTCUSDT", "ts": 111}], max_attempts=3)
    assert ok is False


def test_push_via_git_cli_not_configured_returns_false(monkeypatch):
    monkeypatch.setattr(se.signal_journal, "_github_configured", lambda: False)
    ok = se._push_shadow_via_git_cli([{"symbol": "BTCUSDT", "ts": 111}])
    assert ok is False


def test_push_via_git_cli_nothing_to_commit_returns_true(monkeypatch, tmp_path):
    """remote уже содержит идентичное содержимое -- commit говорит "nothing
    to commit", это не ошибка, а честный признак "уже синхронизировано"."""
    _github_ready(monkeypatch)
    monkeypatch.setattr(se, "_GIT_SYNC_DIR", str(tmp_path))
    monkeypatch.setattr(se, "_ensure_git_sync_dir", lambda: True)

    def fake(args, cwd=None, timeout=None, env=None):
        if args[0] == "commit":
            return (1, "nothing to commit, working tree clean", "")
        return (0, "", "")

    monkeypatch.setattr(se, "_run_git", fake)
    ok = se._push_shadow_via_git_cli([{"symbol": "BTCUSDT", "ts": 111}])
    assert ok is True


def test_push_via_git_cli_ensure_dir_failure_returns_false(monkeypatch):
    _github_ready(monkeypatch)
    monkeypatch.setattr(se, "_ensure_git_sync_dir", lambda: False)
    ok = se._push_shadow_via_git_cli([{"symbol": "BTCUSDT", "ts": 111}])
    assert ok is False


def test_run_git_masks_token_from_output(monkeypatch):
    """Токен НИКОГДА не должен просочиться в лог -- _run_git() маскирует его
    из stdout/stderr перед возвратом вызывающему коду."""
    monkeypatch.setattr(se.signal_journal, "GITHUB_TOKEN", "supersecrettoken123")

    class _FakeCompleted:
        returncode = 1
        stdout = "some output with supersecrettoken123 embedded"
        stderr = "error mentioning supersecrettoken123 too"

    monkeypatch.setattr(se.subprocess, "run", lambda *a, **kw: _FakeCompleted())
    rc, out, err = se._run_git(["status"], cwd="/tmp")
    assert "supersecrettoken123" not in out
    assert "supersecrettoken123" not in err
    assert "***" in out
    assert "***" in err


def test_ensure_git_sync_dir_sparse_checkout_targets_single_file_not_whole_dir(monkeypatch, tmp_path):
    """Живая находка при ревью (2026-07-21): cone-режим sparse-checkout с
    путём "journal" материализовал бы ВЕСЬ каталог, включая journal/archive/
    (сотни МБ архивных файлов) -- ровно то, чего мы избегаем этим переходом
    на git. Sparse-checkout должен быть настроен на ОДИН файл
    (GITHUB_SHADOW_PATH), НЕ на каталог `journal`, и БЕЗ --cone."""
    monkeypatch.setattr(se, "_GIT_SYNC_DIR", str(tmp_path))
    calls = []

    def fake(args, cwd=None, timeout=None, env=None):
        calls.append(list(args))
        return (0, "", "")

    monkeypatch.setattr(se, "_run_git", fake)
    ok = se._ensure_git_sync_dir()
    assert ok is True

    sparse_calls = [c for c in calls if c[0] == "sparse-checkout"]
    assert ["sparse-checkout", "init"] in sparse_calls  # без --cone
    assert ["sparse-checkout", "set", se.GITHUB_SHADOW_PATH] in sparse_calls
    assert not any("--cone" in c for c in sparse_calls)
    assert not any(c == ["sparse-checkout", "set", "journal"] for c in sparse_calls)


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
