"""
pytest для батчинга shadow-sync коммитов (владелец, ДА, 2026-07-15, окно
60-120с) -- shadow_engine._sync_to_github_sync() теперь пропускает реальный
GET+PUT, если с прошлой РЕАЛЬНОЙ попытки прошло меньше GITHUB_SYNC_MIN_
INTERVAL_SEC. Локальная дюрабельность (_write_local()) НЕ зависит от этого
гейта -- проверяется отдельно (запись на диск происходит ДО вызова этой
функции во всех log_*_shadow_async(), сам гейт этого пути не трогает).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shadow_engine as se


def test_sync_gate_constant_within_owner_window():
    assert 60 <= se.GITHUB_SYNC_MIN_INTERVAL_SEC <= 120


def test_first_call_after_reset_always_syncs(monkeypatch):
    """_last_github_sync_attempt_ts=0.0 (сброшено фикстурой conftest) --
    первый вызов в свежем процессе ВСЕГДА проходит гейт (now - 0.0 всегда
    больше интервала для любого разумного now)."""
    calls = []
    monkeypatch.setattr(se, "_github_get_shadow_sync", lambda: (calls.append(1) or [], None))
    monkeypatch.setattr(se, "_load_local", lambda: [])
    result = se._sync_to_github_sync({"symbol": "BTC"}, now=1_000_000.0)
    assert result is True
    assert calls == [1]  # реальный GET произошёл


def test_second_call_within_interval_is_skipped(monkeypatch):
    calls = []
    monkeypatch.setattr(se, "_github_get_shadow_sync", lambda: (calls.append(1) or [], None))
    monkeypatch.setattr(se, "_load_local", lambda: [])
    se._sync_to_github_sync({"symbol": "BTC"}, now=1_000_000.0)
    calls.clear()
    result = se._sync_to_github_sync({"symbol": "ETH"}, now=1_000_000.0 + 30)  # +30с < 90с гейта
    assert result is True  # честный True -- локальная запись уже сделана вызывающей стороной
    assert calls == []  # НО реального сетевого GET не было


def test_call_after_interval_elapsed_syncs_again(monkeypatch):
    calls = []
    monkeypatch.setattr(se, "_github_get_shadow_sync", lambda: (calls.append(1) or [], None))
    monkeypatch.setattr(se, "_load_local", lambda: [])
    se._sync_to_github_sync({"symbol": "BTC"}, now=1_000_000.0)
    calls.clear()
    result = se._sync_to_github_sync(
        {"symbol": "ETH"}, now=1_000_000.0 + se.GITHUB_SYNC_MIN_INTERVAL_SEC + 1)
    assert result is True
    assert calls == [1]  # интервал прошёл -- реальный GET снова случился


def test_skipped_call_does_not_advance_gate_timestamp(monkeypatch):
    """Гейт НЕ обновляется на пропущенных вызовах -- иначе непрерывный поток
    записей чаще интервала мог бы бесконечно откладывать реальный синк
    (каждый skip сдвигал бы дедлайн). Проверяем: skip в T+30 не сдвигает
    _last_github_sync_attempt_ts, следующий реальный синк случается ровно в
    T+90, не в T+30+90."""
    monkeypatch.setattr(se, "_github_get_shadow_sync", lambda: ([], None))
    monkeypatch.setattr(se, "_load_local", lambda: [])
    se._sync_to_github_sync({"symbol": "BTC"}, now=1_000_000.0)  # T0 -- реальный синк
    se._sync_to_github_sync({"symbol": "MID"}, now=1_000_030.0)  # T0+30 -- skip
    calls = []
    monkeypatch.setattr(se, "_github_get_shadow_sync", lambda: (calls.append(1) or [], None))
    # T0+91 (>90 от T0, но <90 от T0+30, если бы skip двигал гейт) -- должен
    # быть реальный синк, если гейт считается от T0, не от T0+30.
    se._sync_to_github_sync({"symbol": "LATE"}, now=1_000_000.0 + se.GITHUB_SYNC_MIN_INTERVAL_SEC + 1)
    assert calls == [1]


def test_batched_window_collects_multiple_local_records_in_one_put(monkeypatch, tmp_path):
    """Интеграционная проверка сути батчинга: несколько локальных записей,
    накопленных ЗА ВРЕМЯ гейта (пока реальные синки пропускались), уходят в
    GitHub ОДНИМ PUT на следующем реальном окне -- та же catchup-логика, что
    уже была (диф local vs remote), просто реже запускается."""
    monkeypatch.setattr(se, "SHADOW_FILE", str(tmp_path / "shadow.json"))
    monkeypatch.setattr(se, "ARCHIVE_DIR", str(tmp_path / "archive"))
    put_calls = []

    def _fake_put(records, sha):
        put_calls.append(len(records))
        return "newsha"

    monkeypatch.setattr(se, "_github_get_shadow_sync", lambda: ([], None))
    monkeypatch.setattr(se, "_github_put_shadow_sync", _fake_put)

    # Запись 1 -- тут же реальный синк (гейт свежий).
    se._write_local({"symbol": "SYM0", "ts": 1_000_000.0})
    se._sync_to_github_sync(None, now=1_000_000.0)
    assert put_calls == [1]

    # Ещё 4 записи легли локально ПОСЛЕ этого синка -- каждая своим
    # log_*_shadow_async()-вызовом дёргает sync, но гейт держит их в skip
    # до истечения интервала (то самое, что раньше давало 1 PUT на запись).
    for i in range(1, 5):
        se._write_local({"symbol": f"SYM{i}", "ts": 1_000_000.0 + i})
        se._sync_to_github_sync(None, now=1_000_000.0 + 10 + i)  # в пределах гейта -- skip
    assert put_calls == [1]  # второй PUT ещё не случился

    # Окно истекло -- следующий вызов реально синкает ВЕСЬ накопленный хвост
    # (все 5 локальных записей) ОДНИМ PUT -- та же catchup-логика, что уже
    # была, просто сработала на всей пачке разом.
    se._sync_to_github_sync(None, now=1_000_000.0 + se.GITHUB_SYNC_MIN_INTERVAL_SEC + 1)
    assert put_calls[-1] == 5


def test_write_local_unaffected_by_sync_gate(monkeypatch, tmp_path):
    """Дюрабельность: _write_local() пишет на диск НЕЗАВИСИМО от состояния
    гейта -- гейт касается только downstream GitHub-синка."""
    monkeypatch.setattr(se, "SHADOW_FILE", str(tmp_path / "shadow.json"))
    monkeypatch.setattr(se, "ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setattr(se, "_github_get_shadow_sync", lambda: ([], None))
    se._sync_to_github_sync(None, now=1_000_000.0)  # "тратит" гейт
    ok = se._write_local({"symbol": "AFTER_GATE_SPENT", "ts": 2_000_000.0})
    assert ok is True
    records = se._load_local()
    assert any(r["symbol"] == "AFTER_GATE_SPENT" for r in records)
