"""
tests/test_shadow_git_sync_lock.py -- владелец, ДА, 2026-07-22, живая находка:
git-sync push шадоу-архива падал 5/5 систематически (`railway logs`:
`cannot lock ref 'refs/heads/main': is at X but expected Y`, меняющийся на
КАЖДОЙ из 5 попыток за ~9 секунд) -- реальная причина: 10 разных мест кода
зовут `_sync_to_github_sync()` через `run_in_executor(None, ...)` (реальные
ОС-потоки), а гейт `_last_github_sync_attempt_ts` читался/писался БЕЗ лока
(TOCTOU) -- несколько потоков проходили гейт одновременно и пускали
параллельный `git push` в один `_GIT_SYNC_DIR`, гоняясь друг с другом же,
а не только с внешним pusher'ом.

ВТОРОЙ раунд той же находки (тот же день, после первого деплоя фикса):
живой лог после деплоя ПЕРВОЙ версии лока показал, что падения
продолжились -- за 2.7с ДО каскада "cannot lock ref" шёл
"пуш архива ... не удался" (`_push_pending_archives_sync()`, GitHub
Contents API PUT, СОВСЕМ ДРУГОЙ транспорт, не native `git push`). Обе
функции мутируют один и тот же `main`-ref на GitHub -- `_git_sync_lock`
расширен, чтобы оборачивать ОБА пути, не только `_sync_to_github_sync()`.

Тесты ниже проверяют ТРИ части фикса:
1. `_git_sync_lock` реально сериализует конкурентные вызовы `_sync_to_github_sync`
   (никогда два потока не оказываются "внутри" критической секции одновременно).
2. `_note_git_sync_outcome`/owner-алерт: алерт шлётся РОВНО ОДИН раз после
   `GIT_SYNC_ALERT_THRESHOLD` подряд неудач, не раньше, не повторно при
   дальнейших неудачах того же эпизода, и сбрасывается при первом успехе.
3. `_push_pending_archives_sync()` (архивный PUT) и `_sync_to_github_sync()`
   (git-CLI push) никогда не оказываются внутри своих сетевых вызовов
   одновременно -- тот же `_git_sync_lock`, второй независимый путь.
"""
import json
import os
import threading
import time

import pytest

import shadow_engine as se


@pytest.fixture(autouse=True)
def _reset_git_sync_state(monkeypatch):
    monkeypatch.setattr(se, "_last_github_sync_attempt_ts", 0.0)
    monkeypatch.setattr(se, "_consecutive_git_sync_failures", 0)
    monkeypatch.setattr(se, "_git_sync_alert_sent", False)
    yield


def test_concurrent_calls_never_overlap_inside_critical_section(monkeypatch):
    """Живая находка: 10 разных мест кода зовут _sync_to_github_sync через
    run_in_executor -- реальные ОС-потоки, никакой asyncio-кооперативности между
    ними. GITHUB_SYNC_MIN_INTERVAL_SEC=0 здесь намеренно -- убирает гейт как
    переменную, чтобы тест бил ИМЕННО в инвариант "критическая секция (GET+push)
    никогда не выполняется параллельно в двух потоках", а не в везение с таймингом
    гонки за сам гейт (тот TOCTOU реален в проде при частом трафике, но
    недетерминирован в юнит-тесте на 5 потоках без принудительной задержки).

    Проверено вручную (temp-патч, без лока -- `with _git_sync_lock:` заменён на
    `if True:`): без лока max_seen уходит в 5 (все потоки внутри одновременно),
    с локом -- ровно 1. Это и есть регресс на находку."""
    monkeypatch.setattr(se, "GITHUB_SYNC_MIN_INTERVAL_SEC", 0)
    active = {"count": 0, "max_seen": 0}
    lock_for_counter = threading.Lock()

    def fake_get_shadow_sync():
        with lock_for_counter:
            active["count"] += 1
            active["max_seen"] = max(active["max_seen"], active["count"])
        time.sleep(0.05)  # эмулируем сетевой GET -- окно, где гонка проявилась бы без лока
        with lock_for_counter:
            active["count"] -= 1
        return [], "sha123"

    monkeypatch.setattr(se, "_github_get_shadow_sync", fake_get_shadow_sync)
    monkeypatch.setattr(se, "_load_local", lambda: [])
    monkeypatch.setattr(se.signal_journal, "_github_configured", lambda: True)

    results = []

    def worker():
        results.append(se._sync_to_github_sync(now=time.time()))

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert active["max_seen"] == 1, (
        "несколько потоков одновременно оказались внутри критической секции -- "
        "лок не сериализовал доступ"
    )
    assert all(results), "все вызовы должны отработать успешно (remote==local==[])"


def test_alert_fires_once_after_threshold_consecutive_failures(monkeypatch):
    monkeypatch.setattr(se, "_github_get_shadow_sync", lambda: ([], "sha"))
    monkeypatch.setattr(se, "_load_local", lambda: [{"symbol": "BTC", "ts": 1.0}])
    monkeypatch.setattr(se.signal_journal, "_github_configured", lambda: True)
    monkeypatch.setattr(se, "_push_shadow_via_git_cli", lambda records: False)

    alerts = []
    monkeypatch.setattr(se, "_alert_owner_git_sync_failure_sync", lambda n: alerts.append(n))

    for i in range(se.GIT_SYNC_ALERT_THRESHOLD - 1):
        se._sync_to_github_sync(now=1000.0 + i * se.GITHUB_SYNC_MIN_INTERVAL_SEC)
    assert alerts == [], "не должно алертить до достижения порога"

    se._sync_to_github_sync(now=1000.0 + (se.GIT_SYNC_ALERT_THRESHOLD - 1) * se.GITHUB_SYNC_MIN_INTERVAL_SEC)
    assert alerts == [se.GIT_SYNC_ALERT_THRESHOLD], "алерт должен сработать РОВНО на пороге"

    # Дальнейшие неудачи того же эпизода -- без повторного алерта (не спамить)
    se._sync_to_github_sync(now=1000.0 + se.GIT_SYNC_ALERT_THRESHOLD * se.GITHUB_SYNC_MIN_INTERVAL_SEC)
    assert alerts == [se.GIT_SYNC_ALERT_THRESHOLD], "повторный алерт в том же эпизоде -- спам, не должно быть"


def test_alert_resets_after_success(monkeypatch):
    monkeypatch.setattr(se, "_github_get_shadow_sync", lambda: ([], "sha"))
    monkeypatch.setattr(se, "_load_local", lambda: [{"symbol": "BTC", "ts": 1.0}])
    monkeypatch.setattr(se.signal_journal, "_github_configured", lambda: True)

    alerts = []
    monkeypatch.setattr(se, "_alert_owner_git_sync_failure_sync", lambda n: alerts.append(n))

    push_ok = {"value": False}
    monkeypatch.setattr(se, "_push_shadow_via_git_cli", lambda records: push_ok["value"])

    t = 1000.0
    for i in range(se.GIT_SYNC_ALERT_THRESHOLD):
        se._sync_to_github_sync(now=t)
        t += se.GITHUB_SYNC_MIN_INTERVAL_SEC
    assert alerts == [se.GIT_SYNC_ALERT_THRESHOLD]

    # Успех -- счётчик и флаг алерта сбрасываются
    push_ok["value"] = True
    se._sync_to_github_sync(now=t)
    assert se._consecutive_git_sync_failures == 0
    assert se._git_sync_alert_sent is False

    # Новый эпизод неудач после сброса -- алерт снова должен сработать на пороге
    push_ok["value"] = False
    t += se.GITHUB_SYNC_MIN_INTERVAL_SEC
    for i in range(se.GIT_SYNC_ALERT_THRESHOLD):
        se._sync_to_github_sync(now=t)
        t += se.GITHUB_SYNC_MIN_INTERVAL_SEC
    assert alerts == [se.GIT_SYNC_ALERT_THRESHOLD, se.GIT_SYNC_ALERT_THRESHOLD]


def test_alert_owner_sync_never_raises_without_token(monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    se._alert_owner_git_sync_failure_sync(5)  # не должно бросить исключение


def test_alert_owner_sync_never_raises_on_network_failure(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "fake-token-not-real")

    def boom(*a, **kw):
        raise ConnectionError("нет сети")

    monkeypatch.setattr(se.requests, "post", boom)
    se._alert_owner_git_sync_failure_sync(5)  # не должно бросить исключение наружу


def test_archive_push_and_shadow_sync_never_overlap(monkeypatch, tmp_path):
    """Второй раунд находки: _push_pending_archives_sync() (GitHub Contents API
    PUT архивных файлов) и _sync_to_github_sync() (native git push) -- РАЗНЫЕ
    транспорты, оба мутируют main-ref -- должны быть взаимоисключающими через
    тот же _git_sync_lock, не только каждый сам с собой."""
    monkeypatch.setattr(se, "GITHUB_SYNC_MIN_INTERVAL_SEC", 0)
    monkeypatch.setattr(se, "ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setattr(se, "ARCHIVE_MANIFEST", str(tmp_path / "archive" / ".pushed.json"))
    os.makedirs(se.ARCHIVE_DIR, exist_ok=True)
    for i in range(3):
        with open(os.path.join(se.ARCHIVE_DIR, f"shadow_signals_2026010{i}_20260102.json"), "w") as f:
            json.dump({"schema_version": 1, "records": []}, f)

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

    def fake_put_backup(path, payload):
        _enter()
        try:
            return True
        finally:
            _exit()

    monkeypatch.setattr(se, "_github_get_shadow_sync", fake_get_shadow_sync)
    monkeypatch.setattr(se, "_load_local", lambda: [])
    monkeypatch.setattr(se.signal_journal, "_github_configured", lambda: True)
    monkeypatch.setattr(se.signal_journal, "_github_put_backup_sync", fake_put_backup)

    def worker_shadow_sync():
        se._sync_to_github_sync(now=time.time())

    def worker_archive_push():
        se._push_pending_archives_sync()

    threads = [threading.Thread(target=worker_shadow_sync) for _ in range(3)]
    threads += [threading.Thread(target=worker_archive_push) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert active["max_seen"] == 1, (
        "архивный PUT и git-sync push оказались одновременно в полёте -- "
        "лок не покрывает оба транспорта"
    )
