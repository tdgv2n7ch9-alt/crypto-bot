"""
pytest для П-Ротация (владелец, решение §3 утреннего брифа 2026-07-14, порог
ПЕРЕСМОТРЕН владельцем P0 2026-07-21 -- рецидив 40МБ/GitHub-422): ротация
journal/shadow_signals.json в journal/archive/shadow_signals_<от>_<до>.json,
БЕЗ потери данных -- get_local_records() по умолчанию читает активный файл
ПЛЮС все архивы. Порог -- ЧИСЛО ЗАПИСЕЙ (ROTATION_MAX_ACTIVE_RECORDS/
ROTATION_KEEP_RECORDS), не время/МБ -- см. докстринг констант в shadow_engine.py
про находку "3-суточное окно росло неограниченно с трафиком".

Все тесты monkeypatch'ат se.SHADOW_FILE/se.ARCHIVE_DIR/se.ARCHIVE_MANIFEST на
tmp_path -- ни один тест не трогает реальный journal/ этого репозитория.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shadow_engine as se


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(se, "SHADOW_FILE", str(tmp_path / "shadow_signals.json"))
    monkeypatch.setattr(se, "ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setattr(se, "ARCHIVE_MANIFEST", str(tmp_path / "archive" / ".pushed.json"))
    monkeypatch.setattr(se, "ROTATE_LOCK_FILE", str(tmp_path / ".shadow_rotate.lock"))


def _write_active(path: str, records: list):
    with open(path, "w") as f:
        json.dump({"schema_version": 1, "records": records}, f)


def _rec(symbol="BTCUSDT", ts=None, pad=""):
    return {"symbol": symbol, "ts": ts, "direction": "long", "pad": pad}


# ── _rotate_if_needed() ──────────────────────────────────────────────────────

def test_rotate_noop_when_file_missing(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert se._rotate_if_needed() == ""


def test_rotate_noop_when_below_record_threshold(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(se, "ROTATION_MAX_ACTIVE_RECORDS", 1000)
    now = time.time()
    _write_active(se.SHADOW_FILE, [_rec(ts=now - 100 * 86400)])  # старая запись, но записей мало
    assert se._rotate_if_needed(now_ts=now) == ""
    # активный файл не тронут
    with open(se.SHADOW_FILE) as f:
        assert len(json.load(f)["records"]) == 1


def test_rotate_noop_when_keep_covers_everything(monkeypatch, tmp_path):
    """Защитный случай (не должен встречаться при разумной конфигурации,
    MAX_ACTIVE > KEEP_RECORDS) -- если keep-порог >= числа записей, архивировать
    нечего даже после пересечения MAX_ACTIVE."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(se, "ROTATION_MAX_ACTIVE_RECORDS", 2)
    monkeypatch.setattr(se, "ROTATION_KEEP_RECORDS", 5)  # >= числа реальных записей
    now = time.time()
    _write_active(se.SHADOW_FILE, [_rec(ts=now - 3600) for _ in range(3)])
    assert se._rotate_if_needed(now_ts=now) == ""


def test_rotate_moves_old_records_to_archive_keeps_recent_in_active(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(se, "ROTATION_MAX_ACTIVE_RECORDS", 2)
    monkeypatch.setattr(se, "ROTATION_KEEP_RECORDS", 1)
    now = time.time()
    old1 = _rec("BTCUSDT", now - 10 * 86400)
    old2 = _rec("ETHUSDT", now - 5 * 86400)
    recent = _rec("SOLUSDT", now - 1 * 3600)
    _write_active(se.SHADOW_FILE, [old1, old2, recent])  # 3 записи > порога 2

    archive_path = se._rotate_if_needed(now_ts=now)
    assert archive_path != ""
    assert os.path.exists(archive_path)

    with open(se.SHADOW_FILE) as f:
        active_records = json.load(f)["records"]
    assert active_records == [recent]  # keep=1 -- только самая новая (последняя в файле)

    with open(archive_path) as f:
        archived_records = json.load(f)["records"]
    assert {r["symbol"] for r in archived_records} == {"BTCUSDT", "ETHUSDT"}


def test_rotate_keeps_by_file_order_not_ts_sort(monkeypatch, tmp_path):
    """Keep-набор -- последние N ЗАПИСЕЙ ФАЙЛА (append-order), не пересортировка
    по ts -- append-only структура означает, что порядок в файле УЖЕ отражает
    порядок записи, сортировка по ts не нужна и могла бы скрыть честные
    out_of_order-записи."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(se, "ROTATION_MAX_ACTIVE_RECORDS", 2)
    monkeypatch.setattr(se, "ROTATION_KEEP_RECORDS", 1)
    now = time.time()
    # третья запись в файле имеет МЕНЬШИЙ ts, чем вторая (out-of-order на практике),
    # но она последняя ПО ПОРЯДКУ В ФАЙЛЕ -- должна остаться в keep-наборе.
    r1 = _rec("A", now - 100)
    r2 = _rec("B", now - 50)
    r3 = _rec("C", now - 80)  # ts меньше r2, но записана позже
    _write_active(se.SHADOW_FILE, [r1, r2, r3])

    se._rotate_if_needed(now_ts=now)
    with open(se.SHADOW_FILE) as f:
        active_records = json.load(f)["records"]
    assert active_records == [r3]


def test_rotate_archive_filename_reflects_date_range(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(se, "ROTATION_MAX_ACTIVE_RECORDS", 1)
    monkeypatch.setattr(se, "ROTATION_KEEP_RECORDS", 0)
    now = time.time()
    old = _rec("BTCUSDT", now - 10 * 86400)
    recent = _rec("ETHUSDT", now)
    from datetime import datetime
    expected_date = datetime.utcfromtimestamp(old["ts"]).strftime("%Y%m%d")
    _write_active(se.SHADOW_FILE, [old, recent])

    archive_path = se._rotate_if_needed(now_ts=now)
    assert expected_date in os.path.basename(archive_path)


def test_rotate_no_data_loss_total_records_preserved(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(se, "ROTATION_MAX_ACTIVE_RECORDS", 10)
    monkeypatch.setattr(se, "ROTATION_KEEP_RECORDS", 8)
    now = time.time()
    records = [_rec(f"SYM{i}USDT", now - i * 86400) for i in range(20)]
    _write_active(se.SHADOW_FILE, records)

    se._rotate_if_needed(now_ts=now)
    all_after = se.get_local_records()
    assert len(all_after) == len(records)
    assert {r["symbol"] for r in all_after} == {r["symbol"] for r in records}


def test_rotate_skips_when_lock_already_held(monkeypatch, tmp_path):
    """Владелец, приёмка v130 (2026-07-16): живая находка -- Railway
    rolling-deploy оставляет старый и новый контейнер кратковременно живыми
    на общем диске; если оба почти одновременно решают ротировать один и
    тот же большой файл, каждый архивирует одни и те же старые записи в
    РАЗНЫЕ файлы -- дубли. Лок должен предотвращать конкурентную ротацию:
    если лок уже занят (эмулируем другим держателем), _rotate_if_needed()
    отступает, ничего не архивирует, файл не трогает."""
    import fcntl
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(se, "ROTATION_MAX_ACTIVE_RECORDS", 1)
    monkeypatch.setattr(se, "ROTATION_KEEP_RECORDS", 0)
    now = time.time()
    _write_active(se.SHADOW_FILE, [_rec("BTCUSDT", now - 10 * 86400)])

    os.makedirs(os.path.dirname(se.ROTATE_LOCK_FILE), exist_ok=True)
    holder_fd = os.open(se.ROTATE_LOCK_FILE, os.O_CREAT | os.O_RDWR)
    fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        result = se._rotate_if_needed(now_ts=now)
        assert result == ""
        assert not os.path.isdir(se.ARCHIVE_DIR)  # ничего не архивировано
        with open(se.SHADOW_FILE) as f:
            assert len(json.load(f)["records"]) == 1  # активный файл не тронут
    finally:
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        os.close(holder_fd)


def test_rotate_succeeds_after_lock_released(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(se, "ROTATION_MAX_ACTIVE_RECORDS", 0)
    monkeypatch.setattr(se, "ROTATION_KEEP_RECORDS", 0)
    now = time.time()
    _write_active(se.SHADOW_FILE, [_rec("BTCUSDT", now - 10 * 86400)])

    result = se._rotate_if_needed(now_ts=now)
    assert result != ""  # лок свободен -- ротация проходит нормально


def test_unique_archive_path_avoids_collision(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    os.makedirs(se.ARCHIVE_DIR, exist_ok=True)
    p1 = se._unique_archive_path("20260101", "20260102")
    with open(p1, "w") as f:
        json.dump({"schema_version": 1, "records": []}, f)
    p2 = se._unique_archive_path("20260101", "20260102")
    assert p1 != p2
    assert p2.endswith("_2.json")


# ── get_local_records() архив+актив ──────────────────────────────────────────

def test_get_local_records_merges_archive_and_active_by_default(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    os.makedirs(se.ARCHIVE_DIR, exist_ok=True)
    _write_active(se.SHADOW_FILE, [_rec("SOLUSDT", 300)])
    with open(os.path.join(se.ARCHIVE_DIR, "shadow_signals_20260101_20260102.json"), "w") as f:
        json.dump({"schema_version": 1, "records": [_rec("BTCUSDT", 100)]}, f)

    records = se.get_local_records()
    assert {r["symbol"] for r in records} == {"SOLUSDT", "BTCUSDT"}


def test_get_local_records_include_archive_false_returns_active_only(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    os.makedirs(se.ARCHIVE_DIR, exist_ok=True)
    _write_active(se.SHADOW_FILE, [_rec("SOLUSDT", 300)])
    with open(os.path.join(se.ARCHIVE_DIR, "shadow_signals_20260101_20260102.json"), "w") as f:
        json.dump({"schema_version": 1, "records": [_rec("BTCUSDT", 100)]}, f)

    records = se.get_local_records(include_archive=False)
    assert {r["symbol"] for r in records} == {"SOLUSDT"}


def test_get_local_records_no_archive_dir_behaves_like_before(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _write_active(se.SHADOW_FILE, [_rec("SOLUSDT", 300)])
    records = se.get_local_records()
    assert {r["symbol"] for r in records} == {"SOLUSDT"}


def test_load_archives_skips_corrupt_file_keeps_others(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    os.makedirs(se.ARCHIVE_DIR, exist_ok=True)
    with open(os.path.join(se.ARCHIVE_DIR, "shadow_signals_bad_bad.json"), "w") as f:
        f.write("{not valid json")
    with open(os.path.join(se.ARCHIVE_DIR, "shadow_signals_20260101_20260102.json"), "w") as f:
        json.dump({"schema_version": 1, "records": [_rec("BTCUSDT", 100)]}, f)

    records = se._load_archives()
    assert len(records) == 1
    assert records[0]["symbol"] == "BTCUSDT"


def test_load_archives_ignores_manifest_file(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    os.makedirs(se.ARCHIVE_DIR, exist_ok=True)
    with open(se.ARCHIVE_MANIFEST, "w") as f:
        json.dump(["shadow_signals_20260101_20260102.json"], f)
    records = se._load_archives()
    assert records == []


def test_load_archives_sorted_by_ts_not_filename_lexicographic_order(monkeypatch, tmp_path):
    """Владелец, P0 2026-07-21 (живая находка при верификации): имена файлов
    с числовым суффиксом (`_unique_archive_path()`, "_2".."_123"...) сортируются
    АЛФАВИТНО в os.listdir -- "_123.json" лексикографически МЕНЬШЕ "_13.json"
    ('2' < '3' на второй позиции), хотя 123 > 13. При частой ротации в
    пределах одних суток (обычное дело с сегодняшним count-based порогом)
    число файлов легко достигает двух-трёх цифр -- строковая сортировка
    даёт ложные "вне порядка" при склейке. Регресс-лок: файл с БОЛЬШИМ
    числовым суффиксом, но БОЛЕЕ РАННИМИ по времени записями, должен всё
    равно оказаться РАНЬШЕ в объединённом списке (_load_archives()
    сортирует по ts после сборки, не полагается на порядок файлов)."""
    _isolate(monkeypatch, tmp_path)
    os.makedirs(se.ARCHIVE_DIR, exist_ok=True)
    # "_123.json" < "_13.json" лексикографически, но записи в нём -- ПОЗЖЕ по времени
    with open(os.path.join(se.ARCHIVE_DIR, "shadow_signals_20260716_20260716_123.json"), "w") as f:
        json.dump({"schema_version": 1, "records": [_rec("ASSET", 200)]}, f)  # позже
    with open(os.path.join(se.ARCHIVE_DIR, "shadow_signals_20260716_20260716_13.json"), "w") as f:
        json.dump({"schema_version": 1, "records": [_rec("ASSET", 100)]}, f)  # раньше

    records = se._load_archives()
    assert [r["ts"] for r in records] == [100, 200]  # хронологический порядок, не по имени файла

    # сквозная проверка -- integrity_report() на этой связке не должен видеть "вне порядка"
    report = se.integrity_report(records)
    assert report["out_of_order_count"] == 0


# ── _write_local() опционально ротирует ──────────────────────────────────────

def test_write_local_triggers_rotation_when_count_crosses_threshold(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(se, "ROTATION_MAX_ACTIVE_RECORDS", 5)  # маленький порог -- легко пересечь в тесте
    monkeypatch.setattr(se, "ROTATION_KEEP_RECORDS", 3)
    now = time.time()
    old_records = [_rec(f"SYM{i}USDT", now - 10 * 86400, pad="x" * 50) for i in range(5)]
    _write_active(se.SHADOW_FILE, old_records)

    ok = se._write_local(_rec("NEWUSDT", now))  # 6-я запись -- пересекает порог 5
    assert ok is True
    assert os.path.isdir(se.ARCHIVE_DIR)
    archived = os.listdir(se.ARCHIVE_DIR)
    assert any(name.startswith("shadow_signals_") for name in archived)
    # новая запись осталась в активном файле, самые старые -- в архиве
    with open(se.SHADOW_FILE) as f:
        active = json.load(f)["records"]
    assert any(r["symbol"] == "NEWUSDT" for r in active)
    assert not any(r["symbol"] == "SYM0USDT" for r in active)


def test_write_local_no_rotation_when_below_threshold(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(se, "ROTATION_MAX_ACTIVE_RECORDS", 1000)
    ok = se._write_local(_rec("BTCUSDT", time.time()))
    assert ok is True
    assert not os.path.isdir(se.ARCHIVE_DIR)


# ── _push_pending_archives_sync() ────────────────────────────────────────────

def test_push_pending_archives_noop_no_archive_dir(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    result = se._push_pending_archives_sync()
    assert result == {"attempted": 0, "succeeded": 0}


def test_push_pending_archives_noop_github_not_configured(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    os.makedirs(se.ARCHIVE_DIR, exist_ok=True)
    with open(os.path.join(se.ARCHIVE_DIR, "shadow_signals_20260101_20260102.json"), "w") as f:
        json.dump({"schema_version": 1, "records": []}, f)
    monkeypatch.setattr(se.signal_journal, "_github_configured", lambda: False)
    result = se._push_pending_archives_sync()
    assert result == {"attempted": 0, "succeeded": 0}


def test_push_pending_archives_pushes_new_files_and_marks_manifest(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    os.makedirs(se.ARCHIVE_DIR, exist_ok=True)
    name = "shadow_signals_20260101_20260102.json"
    with open(os.path.join(se.ARCHIVE_DIR, name), "w") as f:
        json.dump({"schema_version": 1, "records": [_rec("BTCUSDT", 100)]}, f)

    monkeypatch.setattr(se.signal_journal, "_github_configured", lambda: True)
    pushed_calls = []

    def fake_put(path, payload):
        pushed_calls.append(path)
        return True

    monkeypatch.setattr(se.signal_journal, "_github_put_backup_sync", fake_put)
    result = se._push_pending_archives_sync()
    assert result == {"attempted": 1, "succeeded": 1}
    assert pushed_calls == [f"journal/archive/{name}"]

    # повторный вызов не пушит уже отправленный файл снова
    result2 = se._push_pending_archives_sync()
    assert result2 == {"attempted": 0, "succeeded": 0}
    assert pushed_calls == [f"journal/archive/{name}"]


def test_push_pending_archives_failed_put_not_marked_retried_next_time(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    os.makedirs(se.ARCHIVE_DIR, exist_ok=True)
    name = "shadow_signals_20260101_20260102.json"
    with open(os.path.join(se.ARCHIVE_DIR, name), "w") as f:
        json.dump({"schema_version": 1, "records": []}, f)

    monkeypatch.setattr(se.signal_journal, "_github_configured", lambda: True)
    monkeypatch.setattr(se.signal_journal, "_github_put_backup_sync", lambda path, payload: False)
    result = se._push_pending_archives_sync()
    assert result == {"attempted": 1, "succeeded": 0}
    # не помечен как отправленный -- следующий вызов повторит попытку
    assert se._load_pushed_archive_names() == set()


# ── push_pending_archives_async() -- владелец, P0 2026-07-21 (периодический,
# не только при старте, см. докстринг _push_pending_archives_sync) ──────────

def test_push_pending_archives_async_uses_injected_executor(monkeypatch, tmp_path):
    import asyncio
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(se, "_push_pending_archives_sync", lambda: {"attempted": 2, "succeeded": 2})

    calls = []

    async def fake_executor(fn, *a):
        calls.append(fn)
        return fn(*a)

    result = asyncio.run(se.push_pending_archives_async(bot=None, run_in_executor_fn=fake_executor))
    assert result == {"attempted": 2, "succeeded": 2}
    assert calls == [se._push_pending_archives_sync]


def test_push_pending_archives_async_real_executor_path(monkeypatch, tmp_path):
    """Без injected executor -- реальный loop.run_in_executor, не крэшит."""
    import asyncio
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(se, "_push_pending_archives_sync", lambda: {"attempted": 0, "succeeded": 0})
    result = asyncio.run(se.push_pending_archives_async())
    assert result == {"attempted": 0, "succeeded": 0}
