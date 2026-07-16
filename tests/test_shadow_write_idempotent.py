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


def test_write_local_dedup_now_consults_archive_too(monkeypatch, tmp_path):
    """Владелец, приёмка v130 (2026-07-16, регресс к #245): живая находка --
    прежняя версия warm-индекса (только активный файл) пропускала повторную
    запись уже АРХИВИРОВАННОГО uid (нашлось 1042 таких active<->archive
    дублей на живом контейнере). Теперь _warm_uid_index() читает архив тоже
    -- та же запись, что уже есть в архиве, должна быть отклонена."""
    monkeypatch.setattr(se, "SHADOW_FILE", str(tmp_path / "shadow_signals.json"))
    monkeypatch.setattr(se, "ROTATION_SIZE_BYTES", 10 ** 9)
    monkeypatch.setattr(se, "_UID_INDEX", None)
    monkeypatch.setattr(se, "_UID_INDEX_FILE", None)
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    monkeypatch.setattr(se, "ARCHIVE_DIR", str(archive_dir))
    import json
    archived_record = {"symbol": "AKEUSDT", "ts": 12345.6789, "type": "pump_reversal_shadow"}
    (archive_dir / "shadow_signals_old.json").write_text(
        json.dumps({"schema_version": 1, "records": [archived_record]}))

    ok = se._write_local(dict(archived_record))
    assert ok is True  # honest "ok" -- считаем дубль успешно обработанным, не ошибкой
    # активный файл не создан вовсе -- запись отклонена ДО первого append
    assert not os.path.exists(se.SHADOW_FILE)


def test_write_local_allows_new_record_not_in_archive(monkeypatch, tmp_path):
    """Расширение warm-индекса на архив не должно ложно блокировать НОВЫЕ
    записи, которых в архиве нет."""
    monkeypatch.setattr(se, "SHADOW_FILE", str(tmp_path / "shadow_signals.json"))
    monkeypatch.setattr(se, "ROTATION_SIZE_BYTES", 10 ** 9)
    monkeypatch.setattr(se, "_UID_INDEX", None)
    monkeypatch.setattr(se, "_UID_INDEX_FILE", None)
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    monkeypatch.setattr(se, "ARCHIVE_DIR", str(archive_dir))
    import json
    (archive_dir / "shadow_signals_old.json").write_text(
        json.dumps({"schema_version": 1, "records": [
            {"symbol": "AKEUSDT", "ts": 12345.6789, "type": "pump_reversal_shadow"}]}))

    ok = se._write_local({"symbol": "BTCUSDT", "ts": 99999.0, "type": "pump_reversal_shadow"})
    assert ok is True
    with open(se.SHADOW_FILE) as f:
        data = json.load(f)
    assert len(data["records"]) == 1


# --- Владелец, задача #245 (2026-07-16): uid-based writer, in-memory индекс ---

def test_record_uid_differs_by_type_same_symbol_and_ts():
    """uid включает `type` (тип события) -- владелец: hash(symbol+contour+ts+тип).
    Один и тот же symbol+ts, но РАЗНЫЙ type -- разные uid, не коллизия."""
    a = se._record_uid({"symbol": "AKEUSDT", "ts": 100.0, "type": "pump_reversal_shadow"})
    b = se._record_uid({"symbol": "AKEUSDT", "ts": 100.0, "type": "ema_stack_shadow"})
    assert a != b


def test_record_uid_stable_for_identical_record():
    r = {"symbol": "AKEUSDT", "ts": 100.0, "type": "pump_reversal_shadow"}
    assert se._record_uid(r) == se._record_uid(dict(r))


def test_record_uid_differs_by_contour_same_symbol_ts_type():
    """Владелец, приёмка v130: uid=hash(symbol+contour+ts+type) -- формула
    расширена на 4-е поле (contour) по прямому наряду. Без него два РАЗНЫХ
    контура, случайно совпавшие по symbol+type+ts (напр. tz13 и
    external_trader1_btc на одном тике), тихо схлопывались бы в один uid."""
    a = se._record_uid({"symbol": "BTCUSDT", "ts": 100.0, "type": "shadow", "contour": "tz13"})
    b = se._record_uid({"symbol": "BTCUSDT", "ts": 100.0, "type": "shadow", "contour": "external_trader1_btc"})
    assert a != b


def test_write_local_rejects_duplicate_via_uid_index_without_rereading_disk(monkeypatch, tmp_path):
    """Регресс-замок: после прогрева _UID_INDEX повторная запись того же uid
    отклоняется БЕЗ повторного полного чтения файла на дедуп-проверку (сама
    проверка -- O(1) по множеству, не скан списка)."""
    monkeypatch.setattr(se, "SHADOW_FILE", str(tmp_path / "shadow_signals.json"))
    monkeypatch.setattr(se, "ROTATION_SIZE_BYTES", 10 ** 9)
    monkeypatch.setattr(se, "_UID_INDEX", None)
    monkeypatch.setattr(se, "_UID_INDEX_FILE", None)

    record = {"symbol": "AKEUSDT", "ts": 12345.6789, "type": "pump_reversal_shadow"}
    assert se._write_local(record) is True
    assert se._UID_INDEX is not None
    assert se._record_uid(record) in se._UID_INDEX

    load_calls = {"n": 0}
    real_load = se._load_local
    def counting_load():
        load_calls["n"] += 1
        return real_load()
    monkeypatch.setattr(se, "_load_local", counting_load)

    assert se._write_local(dict(record)) is True  # дубль -- должен отклониться по индексу
    with open(se.SHADOW_FILE) as f:
        import json
        data = json.load(f)
    assert len(data["records"]) == 1
    assert load_calls["n"] == 0  # дедуп по uid -- НЕ читал диск повторно


def test_write_local_index_invalidated_when_shadow_file_changes(monkeypatch, tmp_path):
    """Регресс-замок (найден живьём при написании этих тестов): _UID_INDEX,
    прогретый для одного SHADOW_FILE, не должен молча использоваться после
    подмены пути -- иначе запись в НОВЫЙ файл ложно отклоняется как "дубль"
    из СТАРОГО файла."""
    monkeypatch.setattr(se, "ROTATION_SIZE_BYTES", 10 ** 9)
    monkeypatch.setattr(se, "_UID_INDEX", None)
    monkeypatch.setattr(se, "_UID_INDEX_FILE", None)

    monkeypatch.setattr(se, "SHADOW_FILE", str(tmp_path / "file_a.json"))
    se._write_local({"symbol": "AKEUSDT", "ts": 100.0, "type": "pump_reversal_shadow"})

    monkeypatch.setattr(se, "SHADOW_FILE", str(tmp_path / "file_b.json"))
    ok = se._write_local({"symbol": "AKEUSDT", "ts": 100.0, "type": "pump_reversal_shadow"})
    assert ok is True
    with open(se.SHADOW_FILE) as f:
        import json
        data = json.load(f)
    assert len(data["records"]) == 1  # записалось в НОВЫЙ файл, не отклонено по чужому индексу


def test_write_local_flags_out_of_order_without_dropping_record(monkeypatch, tmp_path):
    """Владелец: "если ts новой записи < последней -- писать, но помечать
    out_of_order=true (не терять данные)"."""
    monkeypatch.setattr(se, "SHADOW_FILE", str(tmp_path / "shadow_signals.json"))
    monkeypatch.setattr(se, "ROTATION_SIZE_BYTES", 10 ** 9)
    monkeypatch.setattr(se, "_UID_INDEX", None)
    monkeypatch.setattr(se, "_UID_INDEX_FILE", None)

    se._write_local({"symbol": "AKEUSDT", "ts": 200.0, "type": "pump_reversal_shadow"})
    se._write_local({"symbol": "BTCUSDT", "ts": 100.0, "type": "pump_reversal_shadow"})  # ts < предыдущей

    with open(se.SHADOW_FILE) as f:
        import json
        data = json.load(f)
    assert len(data["records"]) == 2  # оба записаны, ничего не потеряно
    assert data["records"][1].get("out_of_order") is True
    assert data["records"][0].get("out_of_order") is not True
