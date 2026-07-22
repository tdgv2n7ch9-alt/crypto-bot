"""
pytest -- владелец, 2026-07-21 (safety pause): PAUSE_LIVE_SIGNAL_EMISSION
останавливает генерацию/рассылку НОВЫХ живых сигналов
(signal_loop.run_signal_loop, tz13/patch09-путь). Реверсивно, env-var,
default true на момент введения (см. bot.py докстринг рядом с константой).

Владелец, 2026-07-22 (OWNER GATE, узкое снятие паузы ТОЛЬКО для AUTO):
`bot.send_scheduled()` (AUTO-контур) больше НЕ проверяет
`PAUSE_LIVE_SIGNAL_EMISSION` -- у него теперь отдельный флаг
`AUTO_LIVE_EMISSION_ENABLED` (см. тесты ниже и `test_auto_live_emission_
gate.py` для AMD-фильтра/concurrent-limit/kill-switch). `signal_loop`-тесты
ниже не менялись -- тот путь по-прежнему проверяет старый флаг САМ,
без единого изменения.
"""
import asyncio
import logging
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot
import signal_loop


def _run(coro):
    return asyncio.run(coro)


def test_pause_live_signal_emission_default_env_is_true():
    assert os.getenv("PAUSE_LIVE_SIGNAL_EMISSION", "true").strip().lower() in ("1", "true", "yes", "on")


def test_send_scheduled_auto_disabled_skips_subscribers_lookup(caplog):
    with patch.object(bot, "AUTO_LIVE_EMISSION_ENABLED", False), \
         patch.object(bot.subscribers, "active_chat_ids") as mock_ids:
        with caplog.at_level(logging.WARNING):
            _run(bot.send_scheduled(MagicMock()))
    mock_ids.assert_not_called()
    assert bot._last_auto_scan["status"] == "пауза: AUTO_LIVE_EMISSION_ENABLED=false"
    assert any("AUTO_LIVE_EMISSION_ENABLED=false" in r.message for r in caplog.records)


def test_send_scheduled_auto_enabled_proceeds_to_subscribers_lookup():
    with patch.object(bot, "AUTO_LIVE_EMISSION_ENABLED", True), \
         patch.object(bot, "_auto_emission_kill_switch_triggered", return_value=False), \
         patch.object(bot.subscribers, "active_chat_ids", return_value=[]) as mock_ids:
        _run(bot.send_scheduled(MagicMock()))
    mock_ids.assert_called_once()
    assert bot._last_auto_scan["status"] == "пропуск: нет подписчиков"


def test_send_scheduled_kill_switch_triggered_skips_subscribers_lookup():
    with patch.object(bot, "AUTO_LIVE_EMISSION_ENABLED", True), \
         patch.object(bot, "_auto_emission_kill_switch_triggered", return_value=True), \
         patch.object(bot, "_maybe_alert_auto_kill_switch", new=AsyncMock()) as mock_alert, \
         patch.object(bot.subscribers, "active_chat_ids") as mock_ids:
        _run(bot.send_scheduled(MagicMock()))
    mock_ids.assert_not_called()
    mock_alert.assert_awaited_once()
    assert "kill-switch" in bot._last_auto_scan["status"]


def test_send_scheduled_no_longer_checks_pause_live_signal_emission():
    """Владелец, 2026-07-22: узкое снятие паузы -- AUTO больше не читает
    PAUSE_LIVE_SIGNAL_EMISSION вообще, даже если он True (signal_loop
    продолжает его проверять отдельно, не через этот код)."""
    with patch.object(bot, "PAUSE_LIVE_SIGNAL_EMISSION", True), \
         patch.object(bot, "AUTO_LIVE_EMISSION_ENABLED", True), \
         patch.object(bot, "_auto_emission_kill_switch_triggered", return_value=False), \
         patch.object(bot.subscribers, "active_chat_ids", return_value=[]) as mock_ids:
        _run(bot.send_scheduled(MagicMock()))
    mock_ids.assert_called_once()


def test_run_signal_loop_paused_skips_stage1_screen(caplog):
    fake_bot_module = MagicMock()
    fake_bot_module.PAUSE_LIVE_SIGNAL_EMISSION = True
    with patch.object(signal_loop, "_stage1_screen") as mock_screen:
        with caplog.at_level(logging.INFO):
            _run(signal_loop.run_signal_loop(fake_bot_module, MagicMock(), owner_chat_id=1))
    mock_screen.assert_not_called()


def test_run_signal_loop_missing_flag_defaults_to_not_paused():
    fake_bot_module = MagicMock(spec=[])  # нет атрибута PAUSE_LIVE_SIGNAL_EMISSION
    with patch.object(signal_loop, "_stage1_screen", return_value=None) as mock_screen:
        _run(signal_loop.run_signal_loop(fake_bot_module, MagicMock(), owner_chat_id=1))
    mock_screen.assert_called_once()


def test_run_exit_tracker_not_gated_by_pause_flag():
    """Открытые сделки/лимитки НЕ трогает -- run_exit_tracker не должен
    зависеть от PAUSE_LIVE_SIGNAL_EMISSION вообще."""
    import inspect
    src = inspect.getsource(signal_loop.run_exit_tracker)
    assert "PAUSE_LIVE_SIGNAL_EMISSION" not in src
