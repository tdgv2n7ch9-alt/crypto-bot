"""
pytest для pump_detector._notify_owner() -- П-Каналы (владелец, 2026-07-15):
системные уведомления Pump Radar (реконнект/старт/потеря данных) теперь
идут через bot.send_system(), не напрямую ctx.bot.send_message(). Ленивый
импорт bot.py внутри _notify_owner() -- патчим bot.send_system напрямую,
тот же кэшированный модуль из sys.modules, что уже загружен другими
тестовыми файлами.
"""
import asyncio
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot as _bot_module
import pump_detector


def _run(coro):
    return asyncio.run(coro)


class _FakeCtx:
    def __init__(self):
        self.bot = MagicMock()
        self.owner_chat_id = 7009350191


def test_notify_owner_uses_send_system_non_critical_by_default(monkeypatch):
    calls = []

    async def _fake_send_system(bot_arg, text, critical=False, **kw):
        calls.append({"text": text, "critical": critical})

    monkeypatch.setattr(_bot_module, "send_system", _fake_send_system)
    ctx = _FakeCtx()
    _run(pump_detector._notify_owner(ctx, "Радар запущен: 100 символов"))
    assert len(calls) == 1
    assert calls[0]["critical"] is False
    assert "Радар запущен" in calls[0]["text"]


def test_notify_owner_critical_flag_passed_through(monkeypatch):
    calls = []

    async def _fake_send_system(bot_arg, text, critical=False, **kw):
        calls.append({"text": text, "critical": critical})

    monkeypatch.setattr(_bot_module, "send_system", _fake_send_system)
    ctx = _FakeCtx()
    _run(pump_detector._notify_owner(ctx, "Радар без данных", critical=True))
    assert calls[0]["critical"] is True


def test_notify_owner_send_failure_does_not_raise(monkeypatch):
    async def _boom(bot_arg, text, critical=False, **kw):
        raise RuntimeError("Telegram down")

    monkeypatch.setattr(_bot_module, "send_system", _boom)
    ctx = _FakeCtx()
    _run(pump_detector._notify_owner(ctx, "test"))  # не должно бросить исключение
