"""
pytest для tools/dedup_shadow_archive.py (владелец, задача #233): дедупликация
УЖЕ накопленных дублей в journal/archive/*.json -- держит первое вхождение
(symbol, ts), делает бэкап оригинала перед перезаписью, не трогает файлы без
дублей вообще (ни бэкапа, ни перезаписи).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.dedup_shadow_archive import dedup_records, process_file


def test_dedup_records_keeps_first_occurrence():
    records = [
        {"symbol": "AKEUSDT", "ts": 1.0, "peak_price": 100},
        {"symbol": "AKEUSDT", "ts": 1.0, "peak_price": 999},  # дубль-копия (другое значение внутри, тот же ключ)
        {"symbol": "BTCUSDT", "ts": 1.0},
        {"symbol": "AKEUSDT", "ts": 2.0},
    ]
    out, removed = dedup_records(records)
    assert removed == 1
    assert len(out) == 3
    assert out[0]["peak_price"] == 100  # первое вхождение сохранено


def test_dedup_records_no_dupes_untouched():
    records = [{"symbol": "AKEUSDT", "ts": 1.0}, {"symbol": "BTCUSDT", "ts": 1.0}]
    out, removed = dedup_records(records)
    assert removed == 0
    assert out == records


def test_dedup_records_skips_bad_schema_safely():
    """Записи без symbol/ts -- честно не дедупятся (не пытаемся ключевать
    неполные данные), но и не теряются."""
    records = [{"foo": "bar"}, {"symbol": "AKEUSDT", "ts": 1.0}, {"symbol": None, "ts": 1.0}]
    out, removed = dedup_records(records)
    assert len(out) == 3
    assert removed == 0


def test_process_file_dry_run_does_not_modify(tmp_path):
    f = tmp_path / "shadow_signals_test.json"
    f.write_text(json.dumps({"schema_version": 1, "records": [
        {"symbol": "AKEUSDT", "ts": 1.0}, {"symbol": "AKEUSDT", "ts": 1.0},
    ]}))
    original_content = f.read_text()

    result = process_file(str(f), apply=False, backup_dir=str(tmp_path / "backup"))
    assert result["before"] == 2
    assert result["after"] == 1
    assert result["removed"] == 1
    assert result["changed"] is True
    assert f.read_text() == original_content  # dry-run -- файл не тронут
    assert not (tmp_path / "backup").exists()  # и бэкап не создан


def test_process_file_apply_writes_backup_and_dedupes(tmp_path):
    f = tmp_path / "shadow_signals_test.json"
    original = {"schema_version": 1, "records": [
        {"symbol": "AKEUSDT", "ts": 1.0, "v": "orig"}, {"symbol": "AKEUSDT", "ts": 1.0, "v": "dup"},
        {"symbol": "BTCUSDT", "ts": 2.0},
    ]}
    f.write_text(json.dumps(original))
    backup_dir = tmp_path / "backup"

    result = process_file(str(f), apply=True, backup_dir=str(backup_dir))
    assert result["removed"] == 1
    assert result["after"] == 2

    with open(f) as fh:
        after = json.load(fh)
    assert len(after["records"]) == 2
    assert after["records"][0]["v"] == "orig"

    backup_file = backup_dir / "shadow_signals_test.json"
    assert backup_file.exists()
    with open(backup_file) as fh:
        backed_up = json.load(fh)
    assert len(backed_up["records"]) == 3  # бэкап -- ОРИГИНАЛ до дедупа


def test_process_file_no_dupes_no_backup(tmp_path):
    f = tmp_path / "shadow_signals_clean.json"
    f.write_text(json.dumps({"schema_version": 1, "records": [
        {"symbol": "AKEUSDT", "ts": 1.0}, {"symbol": "BTCUSDT", "ts": 2.0},
    ]}))
    backup_dir = tmp_path / "backup"

    result = process_file(str(f), apply=True, backup_dir=str(backup_dir))
    assert result["changed"] is False
    assert not backup_dir.exists()  # чистый файл -- никакого бэкапа/перезаписи
