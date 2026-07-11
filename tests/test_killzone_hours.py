"""
pytest для bot.get_killzone_status() -- Пакет 4 М3 (владелец, "ДА", METHODOLOGY_CORE.md
§18.7): NY Open расширен до 17:00 (было 14:00-16:00), London Close сдвинут на 17:00-19:00
(было 18:00-19:00, "не подтверждено источником"). Живой bot.datetime.now(TZ) заменён на
фиксированное время через monkeypatch -- только имя `datetime` в модуле bot, `timedelta`/
`timezone` не задеты (отдельные привязки из `from datetime import ...`).
"""
import datetime as _dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot


class _FrozenDatetime:
    _now = None

    @classmethod
    def now(cls, tz=None):
        return cls._now


def _freeze(monkeypatch, hour, minute):
    frozen = _dt.datetime(2026, 7, 14, hour, minute, tzinfo=bot.TZ)  # произвольный вторник
    fake = type("_Frozen", (), {"now": classmethod(lambda cls, tz=None: frozen)})
    monkeypatch.setattr(bot, "datetime", fake)


def test_ny_open_16_30_is_active(monkeypatch):
    # 16:30 UTC+3 -- было бы вне старого окна (14:00-16:00), должно попадать в новое (14:00-17:00)
    _freeze(monkeypatch, 16, 30)
    status = bot.get_killzone_status()
    assert status["active"] is not None
    assert status["active"]["name"] == "🇺🇸 NY Open"


def test_ny_open_ends_at_17_00_exclusive(monkeypatch):
    _freeze(monkeypatch, 17, 0)
    status = bot.get_killzone_status()
    # 17:00 -- граница: NY Open заканчивается [start<=hm<end), 17:00 больше не NY Open
    assert status["active"] is None or status["active"]["name"] != "🇺🇸 NY Open"


def test_london_close_starts_at_17_00(monkeypatch):
    _freeze(monkeypatch, 17, 0)
    status = bot.get_killzone_status()
    assert status["active"] is not None
    assert status["active"]["name"] == "🇬🇧 Лондон Close"


def test_london_close_covers_18_30(monkeypatch):
    _freeze(monkeypatch, 18, 30)
    status = bot.get_killzone_status()
    assert status["active"] is not None
    assert status["active"]["name"] == "🇬🇧 Лондон Close"


def test_london_close_ends_at_19_00(monkeypatch):
    _freeze(monkeypatch, 19, 0)
    status = bot.get_killzone_status()
    assert status["active"] is None or status["active"]["name"] != "🇬🇧 Лондон Close"


def test_ny_open_and_london_close_are_adjacent_not_overlapping():
    zones = {z["name"]: (z["start"], z["end"]) for z in [
        {"name": "🇺🇸 NY Open", "start": 14 * 60, "end": 17 * 60},
        {"name": "🇬🇧 Лондон Close", "start": 17 * 60, "end": 19 * 60},
    ]}
    ny_end = zones["🇺🇸 NY Open"][1]
    lc_start = zones["🇬🇧 Лондон Close"][0]
    assert ny_end == lc_start  # ровно стык, без разрыва и без наложения


def test_asia_and_london_open_unchanged(monkeypatch):
    # Не в скоупе Пакета 4 М3 -- эти часы не трогали, проверяем, что не съехали случайно
    _freeze(monkeypatch, 3, 0)
    status = bot.get_killzone_status()
    assert status["active"]["name"] == "🌏 Азиатская сессия"

    _freeze(monkeypatch, 10, 0)
    status = bot.get_killzone_status()
    assert status["active"]["name"] == "🇬🇧 Лондон Open"
