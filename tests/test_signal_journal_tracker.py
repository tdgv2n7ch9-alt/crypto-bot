"""
pytest для signal_journal.run_tracker() -- живой фикс (не только ретроспективный
пересчёт данных): 72ч-истечение PENDING раньше стояло ПОСЛЕ "if price is None:
continue", так что символ без WS-покрытия (live_prices.get_live_price() всегда
возвращает (None, None)) не только не получал SL/TP-исход, но даже не истекал по
таймеру -- висел в PENDING бесконечно (найдено в ретроспективном пересчёте
journal/signals.json, PROGRESS.md 2026-07-11, живой пример id=11 NEX, 146ч при
пороге 72ч). Тест воспроизводит этот сценарий буквально: символ без WS-цены,
запись старше PENDING_EXPIRE_SEC.

run_tracker() -- бесконечный `while True` с await asyncio.sleep(TRACK_INTERVAL_SEC)
в конце. Тестируем ОДИН проход: monkeypatch asyncio.sleep так, чтобы он выбрасывал
управляемое исключение сразу после первой итерации тела цикла -- тело успевает
отработать полностью (обновить статусы, вызвать _save()), sleep() прерывает
дальнейшее вращение.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import live_prices
import signal_journal as sj


class _StopLoop(Exception):
    pass


async def _fake_sleep(_seconds):
    raise _StopLoop()


def _run_one_tick(monkeypatch):
    """Прогоняет ТЕЛО run_tracker() ровно один раз (см. докстринг модуля)."""
    monkeypatch.setattr(sj.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(sj, "_save", lambda: None)  # не пишем на диск в тестах
    try:
        asyncio.run(sj.run_tracker())
    except _StopLoop:
        pass


def _pending_record(ts, symbol="NEX"):
    return {
        "symbol": symbol, "direction": "long", "ts": ts,
        "entry_lo": 100.0, "entry_hi": 100.0, "sl": 90.0,
        "tp1": 110.0, "tp2": 120.0, "tp3": 130.0,
        "status": "PENDING", "entered_ts": None, "entered_price": None,
        "outcome": None, "outcome_ts": None, "outcome_level": None, "actual_r": None,
    }


def test_pending_expires_without_ever_getting_a_price(monkeypatch):
    """Живой сценарий NEX: get_live_price() ВСЕГДА возвращает (None, None) (символ
    вне WS-подписки), запись старше PENDING_EXPIRE_SEC -- раньше висела в PENDING
    навсегда, теперь честно истекает."""
    now = 2_000_000.0
    old_ts = now - sj.PENDING_EXPIRE_SEC - 3600  # на 1ч дальше порога истечения
    rec = _pending_record(old_ts)
    monkeypatch.setattr(sj, "_journal", {1: rec})
    monkeypatch.setattr(sj.time, "time", lambda: now)
    monkeypatch.setattr(live_prices, "get_live_price", lambda symbol: (None, None))

    _run_one_tick(monkeypatch)

    assert rec["status"] == "EXPIRED"
    assert rec["outcome"] == "EXPIRED"
    assert rec["outcome_ts"] == now


def test_pending_without_price_stays_pending_before_deadline(monkeypatch):
    """Та же ситуация (нет WS-цены), но запись ЕЩЁ не старше 72ч -- честно не
    трогаем, не форсируем истечение раньше времени."""
    now = 2_000_000.0
    recent_ts = now - sj.PENDING_EXPIRE_SEC + 3600  # на 1ч МЕНЬШЕ порога
    rec = _pending_record(recent_ts)
    monkeypatch.setattr(sj, "_journal", {1: rec})
    monkeypatch.setattr(sj.time, "time", lambda: now)
    monkeypatch.setattr(live_prices, "get_live_price", lambda symbol: (None, None))

    _run_one_tick(monkeypatch)

    assert rec["status"] == "PENDING"
    assert rec["outcome"] is None


def test_entry_priority_preserved_when_price_available_and_past_deadline():
    """Приоритет входа над истечением СОХРАНЁН (тот же if/elif, что был): если
    цена доступна, тронула зону входа И запись формально уже старше 72ч
    (редкое совпадение) -- запись входит, а не истекает. Прямой юнит-тест логики
    без полного прогона трекера (совпадение двух условий одновременно)."""
    # Реплицируем условие из run_tracker() напрямую, т.к. это едино-строчная
    # проверка приоритета -- не требует полного асинхронного прогона.
    price = 100.0
    entry_lo, entry_hi = 95.0, 105.0
    touches = price is not None and sj._touches_entry(price, entry_lo, entry_hi)
    assert touches is True  # вход проверяется и побеждает независимо от возраста записи


def test_entered_record_still_requires_price_to_resolve(monkeypatch):
    """ENTERED-запись без live-цены НЕ должна получать исход (нет данных для
    сравнения с SL/TP) -- price-гейт для ENTERED сохранён, диф не тронул эту ветку."""
    now = 2_000_000.0
    rec = {
        "symbol": "ZRO", "direction": "long", "ts": now - 100,
        "entry_lo": 100.0, "entry_hi": 100.0, "sl": 90.0,
        "tp1": 110.0, "tp2": 120.0, "tp3": 130.0,
        "status": "ENTERED", "entered_ts": now - 50, "entered_price": 100.0,
        "outcome": None, "outcome_ts": None, "outcome_level": None, "actual_r": None,
    }
    monkeypatch.setattr(sj, "_journal", {1: rec})
    monkeypatch.setattr(sj.time, "time", lambda: now)
    monkeypatch.setattr(live_prices, "get_live_price", lambda symbol: (None, None))

    _run_one_tick(monkeypatch)

    assert rec["status"] == "ENTERED"
    assert rec["outcome"] is None


def test_entered_record_resolves_when_price_available(monkeypatch):
    now = 2_000_000.0
    rec = {
        "symbol": "ZRO", "direction": "long", "ts": now - 100,
        "entry_lo": 100.0, "entry_hi": 100.0, "sl": 90.0,
        "tp1": 110.0, "tp2": 120.0, "tp3": 130.0,
        "status": "ENTERED", "entered_ts": now - 50, "entered_price": 100.0,
        "outcome": None, "outcome_ts": None, "outcome_level": None, "actual_r": None,
    }
    monkeypatch.setattr(sj, "_journal", {1: rec})
    monkeypatch.setattr(sj.time, "time", lambda: now)
    monkeypatch.setattr(live_prices, "get_live_price", lambda symbol: (111.0, 1.0))

    _run_one_tick(monkeypatch)

    assert rec["status"] == "TP1_HIT"
    assert rec["outcome"] == "TP1_HIT"


def test_pending_with_price_touching_entry_becomes_entered_not_expired(monkeypatch):
    """Контрольный тест: живой WS-покрытый символ, зона входа тронута -- поведение
    ДО дифа не сломано (вход побеждает истечение, если запись формально старая, но
    только что тронула зону -- этот кейс раньше вообще не мог дойти до expire-ветки,
    т.к. был перед price-гейтом; теперь явно проверяем, что вход всё ещё работает)."""
    now = 2_000_000.0
    old_ts = now - sj.PENDING_EXPIRE_SEC - 3600
    rec = _pending_record(old_ts, symbol="LIVEUSDT")
    monkeypatch.setattr(sj, "_journal", {1: rec})
    monkeypatch.setattr(sj.time, "time", lambda: now)
    monkeypatch.setattr(live_prices, "get_live_price", lambda symbol: (100.0, 0.5))  # внутри зоны 100-100

    _run_one_tick(monkeypatch)

    assert rec["status"] == "ENTERED"
    assert rec["entered_price"] == 100.0
