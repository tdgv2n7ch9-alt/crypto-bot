"""
pytest для pump_detector state-персиста (владелец, задача #3, 2026-07-16):
journal/pump_radar_state.json был в journal_persistence.SYNCED_FILES с самого
начала, но pump_detector никогда не писал и не читал этот файл -- GitHub-синк
синкал несуществующий файл, редеплой стирал pump_watch/dump_watch/pump_history
без следа. Покрывает: атомарную запись, restore-только-если-пусто (не
перетирает живое состояние), честный noop при отсутствии файла, немедленную
запись на структурных переходах (_finalize_any).
"""
import json
import os

import pump_detector as pd


def _clear_state():
    pd.pump_watch.clear()
    pd.dump_watch.clear()
    pd.pump_history.clear()


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(pd, "STATE_FILE", str(tmp_path / "pump_radar_state.json"))
    _clear_state()


def test_save_state_to_disk_writes_watches_and_history(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    pd.pump_watch["BTCUSDT"] = {"kind": "pump", "stage": "WATCHING", "peak_price": 65000.0}
    pd.dump_watch["ETHUSDT"] = {"kind": "dump", "stage": "REVERSAL_CONFIRMED", "bottom_price": 1800.0}
    pd.pump_history.append({"symbol": "SOL", "ts": 123.0, "final_stage": "EXPIRED", "kind": "pump"})

    pd.save_state_to_disk()

    assert os.path.exists(pd.STATE_FILE)
    with open(pd.STATE_FILE) as f:
        data = json.load(f)
    assert data["pump_watch"]["BTCUSDT"]["peak_price"] == 65000.0
    assert data["dump_watch"]["ETHUSDT"]["stage"] == "REVERSAL_CONFIRMED"
    assert data["pump_history"] == [{"symbol": "SOL", "ts": 123.0, "final_stage": "EXPIRED", "kind": "pump"}]
    _clear_state()


def test_save_state_to_disk_is_atomic_no_tmp_leftover(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    pd.pump_watch["BTCUSDT"] = {"kind": "pump", "stage": "WATCHING"}
    pd.save_state_to_disk()
    leftovers = [p for p in os.listdir(tmp_path) if p.endswith(f"tmp{os.getpid()}")]
    assert leftovers == []
    _clear_state()


def test_load_state_from_disk_restores_when_memory_empty(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    with open(pd.STATE_FILE, "w") as f:
        json.dump({
            "pump_watch": {"BTCUSDT": {"kind": "pump", "stage": "WATCHING"}},
            "dump_watch": {},
            "pump_history": [{"symbol": "SOL", "ts": 1.0, "final_stage": "EXPIRED", "kind": "pump"}],
        }, f)

    ok = pd.load_state_from_disk()
    assert ok is True
    assert "BTCUSDT" in pd.pump_watch
    assert len(pd.pump_history) == 1
    _clear_state()


def test_load_state_from_disk_does_not_overwrite_live_state(monkeypatch, tmp_path):
    """Живое состояние (тот же процесс, не свежий редеплой) НЕ перетирается,
    даже если на диске лежит другое содержимое -- симметрично restore_file_
    sync() в journal_persistence.py."""
    _isolate(monkeypatch, tmp_path)
    with open(pd.STATE_FILE, "w") as f:
        json.dump({"pump_watch": {"STALE": {"stage": "WATCHING"}}, "dump_watch": {}, "pump_history": []}, f)

    pd.pump_watch["LIVE"] = {"kind": "pump", "stage": "WATCHING"}
    ok = pd.load_state_from_disk()
    assert ok is False
    assert "LIVE" in pd.pump_watch
    assert "STALE" not in pd.pump_watch
    _clear_state()


def test_load_state_from_disk_honest_false_when_file_missing(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    ok = pd.load_state_from_disk()
    assert ok is False
    assert pd.pump_watch == {}
    _clear_state()


def test_finalize_any_persists_immediately(monkeypatch, tmp_path):
    """Структурный переход (EXPIRED/PROMOTED/...) сохраняет на диск немедленно,
    не ждёт периодического run_state_persist_loop() -- иначе завершённое
    наблюдение теряется при редеплое в узком окне между финализацией и
    следующим тиком бэкстопа (STATE_SAVE_INTERVAL_SEC=60с)."""
    _isolate(monkeypatch, tmp_path)
    pd.pump_watch["BTCUSDT"] = {"kind": "pump", "stage": "WATCHING", "peak_price": 1.0,
                                 "bottom_price": 1.0, "last_price": 1.0, "pump_time": 0.0}

    pd._finalize_any("BTCUSDT", "pump", "EXPIRED")

    assert "BTCUSDT" not in pd.pump_watch
    assert os.path.exists(pd.STATE_FILE)
    with open(pd.STATE_FILE) as f:
        data = json.load(f)
    assert data["pump_watch"] == {}
    assert len(data["pump_history"]) == 1
    assert data["pump_history"][0]["final_stage"] == "EXPIRED"
    _clear_state()


def test_save_state_to_disk_survives_write_error(monkeypatch, tmp_path, caplog):
    """Best-effort -- ошибка записи (например, недоступная директория) не
    поднимает исключение наружу, только логируется."""
    monkeypatch.setattr(pd, "STATE_FILE", "/nonexistent-root-dir-xyz/pump_radar_state.json")
    _clear_state()
    pd.pump_watch["BTCUSDT"] = {"kind": "pump", "stage": "WATCHING"}
    pd.save_state_to_disk()  # не должно бросить исключение
    _clear_state()
