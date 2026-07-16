"""
pytest для П-Ротация (владелец, решение §3 утреннего брифа 2026-07-14): ротация
journal/shadow_signals.json (12.5МБ живьём за 3 суток -- _write_local() читает-
дописывает-перезаписывает ВЕСЬ файл на каждую новую запись, стоимость растёт
линейно без ограничения) в journal/archive/shadow_signals_<от>_<до>.json, БЕЗ
потери данных -- get_local_records() по умолчанию читает активный файл ПЛЮС
все архивы.

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


def test_rotate_noop_when_below_size_threshold(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(se, "ROTATION_SIZE_BYTES", 10 * 1024 * 1024)
    now = time.time()
    _write_active(se.SHADOW_FILE, [_rec(ts=now - 100 * 86400)])  # старая запись, но файл маленький
    assert se._rotate_if_needed(now_ts=now) == ""
    # активный файл не тронут
    with open(se.SHADOW_FILE) as f:
        assert len(json.load(f)["records"]) == 1


def test_rotate_noop_when_nothing_older_than_keep_window(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(se, "ROTATION_SIZE_BYTES", 1)  # тривиально маленький порог -- всегда "большой"
    monkeypatch.setattr(se, "ROTATION_KEEP_DAYS", 3)
    now = time.time()
    _write_active(se.SHADOW_FILE, [_rec(ts=now - 3600)])  # свежая запись, в пределах keep-окна
    assert se._rotate_if_needed(now_ts=now) == ""


def test_rotate_moves_old_records_to_archive_keeps_recent_in_active(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(se, "ROTATION_SIZE_BYTES", 1)
    monkeypatch.setattr(se, "ROTATION_KEEP_DAYS", 3)
    now = time.time()
    old1 = _rec("BTCUSDT", now - 10 * 86400)
    old2 = _rec("ETHUSDT", now - 5 * 86400)
    recent = _rec("SOLUSDT", now - 1 * 3600)
    _write_active(se.SHADOW_FILE, [old1, old2, recent])

    archive_path = se._rotate_if_needed(now_ts=now)
    assert archive_path != ""
    assert os.path.exists(archive_path)

    with open(se.SHADOW_FILE) as f:
        active_records = json.load(f)["records"]
    assert active_records == [recent]

    with open(archive_path) as f:
        archived_records = json.load(f)["records"]
    assert {r["symbol"] for r in archived_records} == {"BTCUSDT", "ETHUSDT"}


def test_rotate_archive_filename_reflects_date_range(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(se, "ROTATION_SIZE_BYTES", 1)
    monkeypatch.setattr(se, "ROTATION_KEEP_DAYS", 3)
    now = time.time()
    old = _rec("BTCUSDT", now - 10 * 86400)
    from datetime import datetime
    expected_date = datetime.utcfromtimestamp(old["ts"]).strftime("%Y%m%d")
    _write_active(se.SHADOW_FILE, [old])

    archive_path = se._rotate_if_needed(now_ts=now)
    assert expected_date in os.path.basename(archive_path)


def test_rotate_no_data_loss_total_records_preserved(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(se, "ROTATION_SIZE_BYTES", 1)
    monkeypatch.setattr(se, "ROTATION_KEEP_DAYS", 3)
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
    monkeypatch.setattr(se, "ROTATION_SIZE_BYTES", 1)
    monkeypatch.setattr(se, "ROTATION_KEEP_DAYS", 3)
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
    monkeypatch.setattr(se, "ROTATION_SIZE_BYTES", 1)
    monkeypatch.setattr(se, "ROTATION_KEEP_DAYS", 3)
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


# ── _write_local() опционально ротирует ──────────────────────────────────────

def test_write_local_triggers_rotation_when_size_crosses_threshold(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(se, "ROTATION_SIZE_BYTES", 200)  # маленький порог -- легко пересечь в тесте
    monkeypatch.setattr(se, "ROTATION_KEEP_DAYS", 3)
    now = time.time()
    old_records = [_rec(f"SYM{i}USDT", now - 10 * 86400, pad="x" * 50) for i in range(5)]
    _write_active(se.SHADOW_FILE, old_records)
    assert os.path.getsize(se.SHADOW_FILE) > 200

    ok = se._write_local(_rec("NEWUSDT", now))
    assert ok is True
    assert os.path.isdir(se.ARCHIVE_DIR)
    archived = os.listdir(se.ARCHIVE_DIR)
    assert any(name.startswith("shadow_signals_") for name in archived)
    # новая запись осталась в активном файле, старые -- в архиве
    with open(se.SHADOW_FILE) as f:
        active = json.load(f)["records"]
    assert any(r["symbol"] == "NEWUSDT" for r in active)
    assert not any(r["symbol"] == "SYM0USDT" for r in active)


def test_write_local_no_rotation_when_small(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(se, "ROTATION_SIZE_BYTES", 5 * 1024 * 1024)
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
