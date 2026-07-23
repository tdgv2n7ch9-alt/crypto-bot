"""
pytest -- владелец, ДА, 2026-07-23 (FIXLIST_INTERFACE.md п.4, живая находка):
(а) "Вход в позицию: — UTC+3" при отсутствии `v["time"]` (напр. вручную
добавленные "watching"-записи TOP_SHORT_SIGNALS с `time: null`, реально
встречены в /tmp/best_trade_signals.json) -> честное "не вошла".
(б) "Закрытые" показывал время ВХОДА под меткой без даты закрытия (момент
закрытия нигде не фиксировался) -> `closed_ts` пишется в момент перехода в
status="done", честное "н/д" для записей, закрытых ДО этого фикса.
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot


# ── _format_entry_time_line() ──

def test_entry_time_line_formats_real_time():
    v = {"time": bot.TZ.localize(datetime(2026, 7, 23, 12, 41))}
    assert bot._format_entry_time_line(v) == "23.07 12:41 UTC+3"


def test_entry_time_line_honest_not_entered_when_time_missing():
    v = {"time": None, "entry": 61700}
    assert bot._format_entry_time_line(v) == "не вошла"


def test_entry_time_line_honest_not_entered_when_time_key_absent():
    v = {"entry": 61700}
    assert bot._format_entry_time_line(v) == "не вошла"


# ── _format_closed_time_line() ──

def test_closed_time_line_formats_real_closed_ts():
    ts = bot.TZ.localize(datetime(2026, 7, 23, 15, 30)).timestamp()
    v = {"closed_ts": ts, "status": "done", "result": "TP1"}
    assert bot._format_closed_time_line(v) == "23.07 15:30 UTC+3"


def test_closed_time_line_honest_nd_when_missing():
    """Записи, закрытые ДО этого фикса, не имеют closed_ts -- честное "н/д",
    не выдумываем момент закрытия."""
    v = {"status": "done", "result": "SL", "time": bot.TZ.localize(datetime(2026, 7, 20, 10, 0))}
    assert bot._format_closed_time_line(v) == "н/д"


def test_closed_time_line_honest_nd_when_falsy_zero():
    v = {"status": "done", "result": "SL", "closed_ts": 0}
    assert bot._format_closed_time_line(v) == "н/д"
