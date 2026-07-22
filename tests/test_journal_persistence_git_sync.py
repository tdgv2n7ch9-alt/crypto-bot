"""
tests/test_journal_persistence_git_sync.py -- владелец, ДА, 2026-07-22:
sync_all_sync() пушил 7 файлов ПО ОДНОМУ через GitHub Contents API (7
отдельных PUT = 7 отдельных коммитов за ~8с) -- живая находка: это окно
регулярно ловило shadow-пуш (shadow_engine._push_shadow_via_git_cli()),
получая "[rejected] fetch first" на часть из его 5 попыток. Фикс --
_push_all_via_git_cli() в journal_persistence.py: те же файлы пишутся и
пушатся ОДНИМ git-коммитом, под ТЕМ ЖЕ shadow_engine._git_sync_lock, что и
shadow-пуш -- эти два пушера физически не могут оказаться в полёте
одновременно.

Тесты здесь проверяют ИМЕННО эти два свойства (не заново тестируют базовую
git-механику, уже покрытую test_shadow_git_sync_lock.py):
1. Один вызов _push_all_via_git_cli() с N файлами делает РОВНО один
   `git commit`, не по одному на файл.
2. _push_all_via_git_cli() и shadow_engine._sync_to_github_sync() никогда
   не оказываются внутри своих критических секций одновременно -- общий
   _git_sync_lock.
"""
import json
import os
import threading
import time

import pytest

import journal_persistence as jp
import shadow_engine as se


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(jp, "_JP_GIT_SYNC_DIR", str(tmp_path / "jp_git_sync"))
    monkeypatch.setattr(jp.signal_journal, "_github_configured", lambda: True)
    monkeypatch.setattr(jp.signal_journal, "_validate_github_token", lambda: "")
    monkeypatch.setattr(se, "_last_github_sync_attempt_ts", 0.0)
    yield


def _fake_run_git_factory(commit_calls, fail_first_n_pushes=0):
    """Симулирует успешный git-цикл (fetch/checkout/add/commit/push), считая
    сколько раз реально был вызван `commit` -- это и есть проверка батчинга
    (N файлов -> 1 add на файл, но ОДИН commit, не N)."""
    push_attempts = {"n": 0}

    def fake_run_git(args, cwd, timeout=60, env=None):
        if args[0] == "fetch":
            return 0, "", ""
        if args[0] == "checkout":
            return 0, "", ""
        if args[0] == "add":
            return 0, "", ""
        if args[0] == "commit":
            commit_calls.append(list(args))
            return 0, "", ""
        if args[0] == "push":
            push_attempts["n"] += 1
            if push_attempts["n"] <= fail_first_n_pushes:
                return 1, "", "! [rejected] HEAD -> main (fetch first)"
            return 0, "", ""
        raise AssertionError(f"unexpected git args: {args}")

    return fake_run_git


def test_push_all_via_git_cli_makes_exactly_one_commit_for_multiple_files(monkeypatch, tmp_path):
    commit_calls = []
    monkeypatch.setattr(se, "_run_git", _fake_run_git_factory(commit_calls))
    monkeypatch.setattr(se, "_git_remote_url", lambda: "https://x@github.com/o/r.git")
    monkeypatch.setattr(jp, "_jp_ensure_git_sync_dir", lambda: True)

    files_dir = tmp_path / "files"
    files_dir.mkdir()
    paths = []
    for i, name in enumerate(["ake_setup_state.json", "bsc_wallet_events.json", "pump_radar_state.json"]):
        p = files_dir / name
        p.write_text(json.dumps({"i": i}))
        paths.append((f"journal/{name}", str(p)))

    result = jp._push_all_via_git_cli(paths)

    assert len(commit_calls) == 1, f"ожидался РОВНО 1 commit на батч из {len(paths)} файлов, получили {len(commit_calls)}"
    assert set(result["synced"]) == {p for p, _ in paths}


def test_push_all_via_git_cli_empty_files_noop(monkeypatch):
    monkeypatch.setattr(se, "_run_git", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("не должно звать git вообще")))
    result = jp._push_all_via_git_cli([])
    assert result == {"synced": []}


def test_push_all_via_git_cli_and_shadow_sync_never_overlap(monkeypatch, tmp_path):
    """Тот же класс проверки, что test_shadow_git_sync_lock.py::
    test_archive_push_and_shadow_sync_never_overlap, но для ТРЕТЬЕГО независимого
    пушера (journal_persistence, а не shadow-архив) -- тот же _git_sync_lock должен
    сериализовать и его тоже."""
    monkeypatch.setattr(se, "GITHUB_SYNC_MIN_INTERVAL_SEC", 0)
    monkeypatch.setattr(jp, "_jp_ensure_git_sync_dir", lambda: True)
    monkeypatch.setattr(se, "_git_remote_url", lambda: "https://x@github.com/o/r.git")

    active = {"count": 0, "max_seen": 0}
    lock_for_counter = threading.Lock()

    def _enter():
        with lock_for_counter:
            active["count"] += 1
            active["max_seen"] = max(active["max_seen"], active["count"])
        time.sleep(0.03)

    def _exit():
        with lock_for_counter:
            active["count"] -= 1

    def fake_get_shadow_sync():
        _enter()
        try:
            return [], "sha123"
        finally:
            _exit()

    def fake_run_git_for_jp(args, cwd, timeout=60, env=None):
        if args[0] == "push":
            _enter()
            try:
                return 0, "", ""
            finally:
                _exit()
        return 0, "", ""

    monkeypatch.setattr(se, "_github_get_shadow_sync", fake_get_shadow_sync)
    monkeypatch.setattr(se, "_load_local", lambda: [])
    monkeypatch.setattr(se.signal_journal, "_github_configured", lambda: True)
    monkeypatch.setattr(se, "_run_git", fake_run_git_for_jp)

    files_dir = tmp_path / "files"
    files_dir.mkdir()
    p = files_dir / "ake_setup_state.json"
    p.write_text(json.dumps({"stage": "armed"}))
    files = [("journal/ake_setup_state.json", str(p))]

    def worker_shadow_sync():
        se._sync_to_github_sync(now=time.time())

    def worker_journal_push():
        jp._push_all_via_git_cli(files)

    threads = [threading.Thread(target=worker_shadow_sync) for _ in range(3)]
    threads += [threading.Thread(target=worker_journal_push) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert active["max_seen"] == 1, (
        "journal_persistence-пуш и shadow-пуш оказались одновременно в полёте -- "
        "не используют один и тот же _git_sync_lock"
    )
