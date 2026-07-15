"""
pytest для shadow_engine._write_local() идемпотентности (владелец, задача #233,
2026-07-15: регресс 997 дублей/23 вне порядка -- расследование показало, что дубли
полностью confined к архиву, 0 в активном файле, корень -- исторический эпизод ДО
фикса дефекта watchdog #181, два процесса писали в один файл). Фикс здесь --
дешёвая защита от ЛЮБОГО повтора того же (symbol, ts), не только известной причины.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shadow_engine as se


def test_write_local_rejects_exact_duplicate(monkeypatch, tmp_path):
    monkeypatch.setattr(se, "SHADOW_FILE", str(tmp_path / "shadow_signals.json"))
    monkeypatch.setattr(se, "ROTATION_SIZE_BYTES", 10 ** 9)  # ротацию не триггерим в тесте

    record = {"symbol": "AKEUSDT", "ts": 12345.6789, "direction": "short"}
    assert se._write_local(record) is True
    assert se._write_local(dict(record)) is True  # тот же (symbol, ts), другой dict-объект

    with open(se.SHADOW_FILE) as f:
        import json
        data = json.load(f)
    assert len(data["records"]) == 1  # второй вызов НЕ добавил дубль


def test_write_local_allows_same_symbol_different_ts(monkeypatch, tmp_path):
    monkeypatch.setattr(se, "SHADOW_FILE", str(tmp_path / "shadow_signals.json"))
    monkeypatch.setattr(se, "ROTATION_SIZE_BYTES", 10 ** 9)

    se._write_local({"symbol": "AKEUSDT", "ts": 100.0})
    se._write_local({"symbol": "AKEUSDT", "ts": 200.0})

    with open(se.SHADOW_FILE) as f:
        import json
        data = json.load(f)
    assert len(data["records"]) == 2  # разные ts -- оба записаны


def test_write_local_allows_different_symbol_same_ts(monkeypatch, tmp_path):
    monkeypatch.setattr(se, "SHADOW_FILE", str(tmp_path / "shadow_signals.json"))
    monkeypatch.setattr(se, "ROTATION_SIZE_BYTES", 10 ** 9)

    se._write_local({"symbol": "AKEUSDT", "ts": 100.0})
    se._write_local({"symbol": "BTCUSDT", "ts": 100.0})

    with open(se.SHADOW_FILE) as f:
        import json
        data = json.load(f)
    assert len(data["records"]) == 2


def test_write_local_dedup_scoped_to_local_only_not_archive(monkeypatch, tmp_path):
    """_write_local() проверяет только _load_local() (активный файл) -- НЕ читает
    архив на каждую запись (было бы дорого при больших архивах). Это ожидаемо:
    архивная дедупликация -- отдельный инструмент (tools/dedup_shadow_archive.py),
    не горячий путь записи."""
    monkeypatch.setattr(se, "SHADOW_FILE", str(tmp_path / "shadow_signals.json"))
    monkeypatch.setattr(se, "ROTATION_SIZE_BYTES", 10 ** 9)
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    monkeypatch.setattr(se, "ARCHIVE_DIR", str(archive_dir))
    import json
    (archive_dir / "shadow_signals_old.json").write_text(
        json.dumps({"schema_version": 1, "records": [{"symbol": "AKEUSDT", "ts": 12345.6789}]}))

    # тот же ключ, что уже в архиве, но НЕ в активном файле -- должен записаться
    # (write-time guard не консультирует архив, это by-design для горячего пути)
    ok = se._write_local({"symbol": "AKEUSDT", "ts": 12345.6789})
    assert ok is True
    with open(se.SHADOW_FILE) as f:
        data = json.load(f)
    assert len(data["records"]) == 1
