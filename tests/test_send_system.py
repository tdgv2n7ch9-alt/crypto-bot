"""
pytest для bot.send_system()/_system_channel_chat_id() -- П-Каналы (владелец,
2026-07-15): единая точка отправки СИСТЕМНЫХ/инфраструктурных сообщений,
отдельно от торговых сигналов (которые эту функцию не используют).
Этап подготовки -- без chat_id новой группы SYSTEM_CHANNEL_CHAT_ID не
задан, вся логика должна корректно вести себя и в этом состоянии, и после
того, как владелец пришлёт chat_id.
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot


def _run(coro):
    return asyncio.run(coro)


def setup_function(_):
    os.environ.pop("SYSTEM_CHANNEL_CHAT_ID", None)


def teardown_function(_):
    os.environ.pop("SYSTEM_CHANNEL_CHAT_ID", None)


# ── _system_channel_chat_id() ────────────────────────────────────────────

def test_system_channel_none_when_unset():
    assert bot._system_channel_chat_id() is None


def test_system_channel_none_when_empty_string():
    os.environ["SYSTEM_CHANNEL_CHAT_ID"] = ""
    assert bot._system_channel_chat_id() is None


def test_system_channel_parses_valid_int():
    os.environ["SYSTEM_CHANNEL_CHAT_ID"] = "-1001234567890"
    assert bot._system_channel_chat_id() == -1001234567890


def test_system_channel_invalid_value_is_honest_none_not_crash():
    os.environ["SYSTEM_CHANNEL_CHAT_ID"] = "not-a-number"
    assert bot._system_channel_chat_id() is None


# ── send_system() -- этап подготовки (system channel НЕ настроен) ────────

def test_prep_stage_non_critical_goes_to_owner_only():
    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()
    _run(bot.send_system(fake_bot, "test message"))
    fake_bot.send_message.assert_called_once()
    args = fake_bot.send_message.call_args[0]
    assert args[0] == int(os.getenv("OWNER_CHAT_ID", "7009350191"))


def test_prep_stage_message_has_sys_prefix():
    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()
    _run(bot.send_system(fake_bot, "деплой упал"))
    text = fake_bot.send_message.call_args[0][1]
    assert text.startswith(bot.SYSTEM_MESSAGE_PREFIX)
    assert "деплой упал" in text


def test_prep_stage_critical_still_goes_to_owner_only_once():
    """До настройки системного канала критическое сообщение всё равно идёт
    только в основной чат (второго чата пока не существует) -- не дублируется."""
    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()
    _run(bot.send_system(fake_bot, "критично", critical=True))
    assert fake_bot.send_message.call_count == 1


# ── send_system() -- системный канал настроен ─────────────────────────────

def test_configured_non_critical_goes_to_system_channel_only():
    os.environ["SYSTEM_CHANNEL_CHAT_ID"] = "-1009999"
    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()
    _run(bot.send_system(fake_bot, "рутинный алерт"))
    fake_bot.send_message.assert_called_once()
    assert fake_bot.send_message.call_args[0][0] == -1009999


def test_configured_critical_goes_to_both_chats():
    os.environ["SYSTEM_CHANNEL_CHAT_ID"] = "-1009999"
    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()
    _run(bot.send_system(fake_bot, "бот молчит", critical=True))
    assert fake_bot.send_message.call_count == 2
    sent_chat_ids = {c.args[0] for c in fake_bot.send_message.call_args_list}
    assert sent_chat_ids == {-1009999, int(os.getenv("OWNER_CHAT_ID", "7009350191"))}


def test_configured_critical_prefix_applied_to_both():
    os.environ["SYSTEM_CHANNEL_CHAT_ID"] = "-1009999"
    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()
    _run(bot.send_system(fake_bot, "потеря данных", critical=True))
    for call in fake_bot.send_message.call_args_list:
        assert call.args[1].startswith(bot.SYSTEM_MESSAGE_PREFIX)


# ── send_system() -- отказоустойчивость ──────────────────────────────────

def test_one_chat_failure_does_not_block_the_other(monkeypatch, caplog):
    os.environ["SYSTEM_CHANNEL_CHAT_ID"] = "-1009999"
    fake_bot = MagicMock()
    calls = []

    async def _send(chat_id, text, **kw):
        calls.append(chat_id)
        if chat_id == -1009999:
            raise RuntimeError("Telegram down for this chat")

    fake_bot.send_message = _send
    _run(bot.send_system(fake_bot, "критично", critical=True))
    # оба чата были ПОПЫТАНЫ, несмотря на сбой одного
    assert set(calls) == {-1009999, int(os.getenv("OWNER_CHAT_ID", "7009350191"))}


def test_kwargs_passed_through_eg_parse_mode():
    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()
    _run(bot.send_system(fake_bot, "*bold*", parse_mode="Markdown"))
    assert fake_bot.send_message.call_args.kwargs.get("parse_mode") == "Markdown"


def test_system_channel_equal_to_owner_id_no_duplicate_send():
    """Граничный случай -- если владелец по ошибке впишет в SYSTEM_CHANNEL_
    CHAT_ID тот же chat_id, что и основной OWNER_CHAT_ID, критическое
    сообщение не должно уйти ДВАЖДЫ в один и тот же чат."""
    os.environ["SYSTEM_CHANNEL_CHAT_ID"] = os.getenv("OWNER_CHAT_ID", "7009350191")
    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()
    _run(bot.send_system(fake_bot, "test", critical=True))
    assert fake_bot.send_message.call_count == 1


# ── _log_new_group_contact() -- диагностика chat_id новых групп ─────────

def _fake_update(chat_type, chat_id=-100123, title="Some Group"):
    upd = MagicMock()
    chat = MagicMock()
    chat.type = chat_type
    chat.id = chat_id
    chat.title = title
    upd.effective_chat = chat
    return upd


def test_log_new_group_contact_logs_group_chat(caplog):
    import logging
    with caplog.at_level(logging.INFO, logger="bot"):
        _run(bot._log_new_group_contact(_fake_update("supergroup", -1004495191296, "BEST TRADE — Система"), MagicMock()))
    assert any("-1004495191296" in r.message for r in caplog.records)
    assert any("BEST TRADE" in r.message for r in caplog.records)


def test_log_new_group_contact_ignores_private_chat(caplog):
    import logging
    with caplog.at_level(logging.INFO, logger="bot"):
        _run(bot._log_new_group_contact(_fake_update("private", 7009350191, None), MagicMock()))
    assert not any("[CHAT-DIAG]" in r.message for r in caplog.records)


def test_log_new_group_contact_no_crash_on_missing_chat():
    upd = MagicMock()
    upd.effective_chat = None
    _run(bot._log_new_group_contact(upd, MagicMock()))  # не должно бросить исключение


# ── retrofit: check_deploy_freshness() использует send_system ────────────

def test_deploy_freshness_alert_uses_send_system(monkeypatch):
    import asyncio as _asyncio
    import datetime as _dt

    calls = []

    async def _fake_send_system(bot_arg, text, critical=False, **kw):
        calls.append({"text": text, "critical": critical})

    monkeypatch.setattr(bot, "send_system", _fake_send_system)
    bot._deploy_check_boot_sha["sha"] = "aaa1111"
    bot._deploy_check_boot_sha["date"] = None
    bot._deploy_alerted_shas.clear()
    old_ts = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    monkeypatch.setattr(bot, "_fetch_main_head_sync", lambda: ("bbb2222", old_ts))
    monkeypatch.setattr(bot, "_compare_commits_sync", lambda a, b: ["bot.py"])
    _run(bot.check_deploy_freshness(MagicMock()))
    assert len(calls) == 1
    assert calls[0]["critical"] is False  # deploy-alert -- не критично (владелец не классифицировал так)


# ── retrofit: run_watchdog() использует send_system с правильным critical ──

def test_watchdog_job_stale_alert_is_critical(monkeypatch):
    calls = []

    async def _fake_send_system(bot_arg, text, critical=False, **kw):
        calls.append({"text": text, "critical": critical})

    monkeypatch.setattr(bot, "send_system", _fake_send_system)
    # Владелец, 2026-07-17 (ночной mute п.5): run_watchdog() теперь сам
    # проверяет _is_night_mute_window() ДО вызова send_system() -- без
    # явного мока этот тест был бы чувствителен к реальному часу запуска
    # pytest (флейки, если прогнать между 00:00 и 08:00 по TZ проекта).
    monkeypatch.setattr(bot, "_is_night_mute_window", lambda now=None: False)
    bot._job_alerted_stale.clear()
    monkeypatch.setattr(bot, "_job_expected_interval_sec", {"testjob": 60})
    monkeypatch.setattr(bot, "_job_heartbeats", {})
    monkeypatch.setattr(bot, "_PROCESS_START_TS", time_module().time() - 1000)
    monkeypatch.setattr(bot, "_DATA_SOURCE_STATUS", {})
    _run(bot.run_watchdog(MagicMock()))
    assert len(calls) == 1
    assert calls[0]["critical"] is True
    assert "testjob" in calls[0]["text"]


# ── ночной mute класса [SYS]-watchdog/reconnect (владелец, 2026-07-17, утро п.5) ──

def test_watchdog_stale_alert_muted_during_night_window(monkeypatch, tmp_path):
    """В окне [00:00,08:00) heartbeat-stale алерт НЕ отправляется, но
    _job_alerted_stale НЕ помечается -- следующий тик должен перепроверить
    заново (не потерять сигнал молча навсегда)."""
    calls = []

    async def _fake_send_system(bot_arg, text, critical=False, **kw):
        calls.append({"text": text, "critical": critical})

    monkeypatch.setattr(bot, "send_system", _fake_send_system)
    monkeypatch.setattr(bot, "_is_night_mute_window", lambda now=None: True)
    monkeypatch.setattr(bot, "NIGHT_MUTE_LOG_PATH", str(tmp_path / "night_mute_log.json"))
    bot._job_alerted_stale.clear()
    monkeypatch.setattr(bot, "_job_expected_interval_sec", {"testjob": 60})
    monkeypatch.setattr(bot, "_job_heartbeats", {})
    monkeypatch.setattr(bot, "_PROCESS_START_TS", time_module().time() - 1000)
    monkeypatch.setattr(bot, "_DATA_SOURCE_STATUS", {})
    _run(bot.run_watchdog(MagicMock()))
    assert calls == []  # ничего не отправлено
    assert "testjob" not in bot._job_alerted_stale  # не помечено -- перепроверится снова


def test_watchdog_stale_alert_fires_after_window_ends_if_still_stale(monkeypatch, tmp_path):
    """Симуляция: ночью замьючено (не помечено), следующий тик уже ПОСЛЕ
    08:00 -- та же самая непрошедшая проблема должна алертнуть немедленно,
    не потеряться."""
    calls = []

    async def _fake_send_system(bot_arg, text, critical=False, **kw):
        calls.append({"text": text, "critical": critical})

    monkeypatch.setattr(bot, "send_system", _fake_send_system)
    monkeypatch.setattr(bot, "NIGHT_MUTE_LOG_PATH", str(tmp_path / "night_mute_log.json"))
    bot._job_alerted_stale.clear()
    monkeypatch.setattr(bot, "_job_expected_interval_sec", {"testjob": 60})
    monkeypatch.setattr(bot, "_job_heartbeats", {})
    monkeypatch.setattr(bot, "_PROCESS_START_TS", time_module().time() - 1000)
    monkeypatch.setattr(bot, "_DATA_SOURCE_STATUS", {})

    # Тик 1 -- ночь, муто
    monkeypatch.setattr(bot, "_is_night_mute_window", lambda now=None: True)
    _run(bot.run_watchdog(MagicMock()))
    assert calls == []

    # Тик 2 -- уже утро, проблема всё ещё жива
    monkeypatch.setattr(bot, "_is_night_mute_window", lambda now=None: False)
    _run(bot.run_watchdog(MagicMock()))
    assert len(calls) == 1
    assert calls[0]["critical"] is True


def test_watchdog_stale_alert_not_muted_outside_night_window(monkeypatch, tmp_path):
    calls = []

    async def _fake_send_system(bot_arg, text, critical=False, **kw):
        calls.append({"text": text, "critical": critical})

    monkeypatch.setattr(bot, "send_system", _fake_send_system)
    monkeypatch.setattr(bot, "_is_night_mute_window", lambda now=None: False)
    monkeypatch.setattr(bot, "NIGHT_MUTE_LOG_PATH", str(tmp_path / "night_mute_log.json"))
    bot._job_alerted_stale.clear()
    monkeypatch.setattr(bot, "_job_expected_interval_sec", {"testjob": 60})
    monkeypatch.setattr(bot, "_job_heartbeats", {})
    monkeypatch.setattr(bot, "_PROCESS_START_TS", time_module().time() - 1000)
    monkeypatch.setattr(bot, "_DATA_SOURCE_STATUS", {})
    _run(bot.run_watchdog(MagicMock()))
    assert len(calls) == 1
    assert "testjob" in bot._job_alerted_stale


# ── _is_night_mute_window() ──────────────────────────────────────────────

def test_is_night_mute_window_true_at_midnight():
    import datetime as _dt
    now = _dt.datetime(2026, 7, 17, 0, 0, tzinfo=bot.TZ)
    assert bot._is_night_mute_window(now) is True


def test_is_night_mute_window_true_just_before_8am():
    import datetime as _dt
    now = _dt.datetime(2026, 7, 17, 7, 59, tzinfo=bot.TZ)
    assert bot._is_night_mute_window(now) is True


def test_is_night_mute_window_false_at_8am_exactly():
    import datetime as _dt
    now = _dt.datetime(2026, 7, 17, 8, 0, tzinfo=bot.TZ)
    assert bot._is_night_mute_window(now) is False


def test_is_night_mute_window_false_at_noon():
    import datetime as _dt
    now = _dt.datetime(2026, 7, 17, 12, 0, tzinfo=bot.TZ)
    assert bot._is_night_mute_window(now) is False


def test_is_night_mute_window_false_just_before_midnight():
    import datetime as _dt
    now = _dt.datetime(2026, 7, 17, 23, 59, tzinfo=bot.TZ)
    assert bot._is_night_mute_window(now) is False


# ── send_system() night_mutable ──────────────────────────────────────────

def test_send_system_night_mutable_suppressed_in_window(monkeypatch, tmp_path):
    monkeypatch.setattr(bot, "_is_night_mute_window", lambda now=None: True)
    monkeypatch.setattr(bot, "NIGHT_MUTE_LOG_PATH", str(tmp_path / "night_mute_log.json"))
    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()
    _run(bot.send_system(fake_bot, "coarse переподключён", night_mutable=True,
                          night_mute_kind="pump_radar_reconnect"))
    fake_bot.send_message.assert_not_called()


def test_send_system_night_mutable_sent_outside_window(monkeypatch, tmp_path):
    monkeypatch.setattr(bot, "_is_night_mute_window", lambda now=None: False)
    monkeypatch.setattr(bot, "NIGHT_MUTE_LOG_PATH", str(tmp_path / "night_mute_log.json"))
    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()
    _run(bot.send_system(fake_bot, "coarse переподключён", night_mutable=True,
                          night_mute_kind="pump_radar_reconnect"))
    fake_bot.send_message.assert_called_once()


def test_send_system_non_night_mutable_alert_ignores_night_window(monkeypatch, tmp_path):
    """Алерты БЕЗ night_mutable=True (например, shadow data-loss) не должны
    замьючиваться ночью, даже если сейчас ночное окно -- владелец explicit:
    "critical-алерты не трогать" (в смысле "прочие критические алерты класс
    не задет этим изменением")."""
    monkeypatch.setattr(bot, "_is_night_mute_window", lambda now=None: True)
    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()
    _run(bot.send_system(fake_bot, "shadow-поток не пишет 17ч", critical=True))
    fake_bot.send_message.assert_called_once()


def test_send_system_night_mute_records_event(monkeypatch, tmp_path):
    log_path = tmp_path / "night_mute_log.json"
    monkeypatch.setattr(bot, "_is_night_mute_window", lambda now=None: True)
    monkeypatch.setattr(bot, "NIGHT_MUTE_LOG_PATH", str(log_path))
    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()
    _run(bot.send_system(fake_bot, "coarse переподключён (реконнект #3)",
                          night_mutable=True, night_mute_kind="pump_radar_reconnect"))
    assert log_path.exists()
    import json
    data = json.loads(log_path.read_text())
    assert len(data["events"]) == 1
    assert data["events"][0]["kind"] == "pump_radar_reconnect"


def test_record_night_mute_resets_on_new_day(monkeypatch, tmp_path):
    log_path = tmp_path / "night_mute_log.json"
    monkeypatch.setattr(bot, "NIGHT_MUTE_LOG_PATH", str(log_path))
    log_path.write_text('{"date": "2020-01-01", "events": [{"ts": 1, "kind": "old", "detail": ""}]}')
    bot._record_night_mute("watchdog_heartbeat", "testjob: 15 мин")
    import json
    data = json.loads(log_path.read_text())
    assert len(data["events"]) == 1  # старый день отброшен, не накопился
    assert data["events"][0]["kind"] == "watchdog_heartbeat"


def test_notify_owner_reconnect_passes_night_mutable(monkeypatch):
    """pump_detector._notify_owner() пробрасывает night_mutable/night_mute_
    kind в bot.send_system() без искажений."""
    import pump_detector as _pd
    calls = []

    async def _fake_send_system(bot_arg, text, critical=False, night_mutable=False,
                                 night_mute_kind="", **kw):
        calls.append({"night_mutable": night_mutable, "night_mute_kind": night_mute_kind})

    monkeypatch.setattr(bot, "send_system", _fake_send_system)

    class _FakeCtx:
        def __init__(self):
            self.bot = MagicMock()

    _run(_pd._notify_owner(_FakeCtx(), "coarse переподключён", night_mutable=True,
                            night_mute_kind="pump_radar_reconnect"))
    assert calls[0]["night_mutable"] is True
    assert calls[0]["night_mute_kind"] == "pump_radar_reconnect"


def test_watchdog_source_failure_alert_is_not_critical(monkeypatch):
    calls = []

    async def _fake_send_system(bot_arg, text, critical=False, **kw):
        calls.append({"text": text, "critical": critical})

    monkeypatch.setattr(bot, "send_system", _fake_send_system)
    bot._source_alerted.clear()
    monkeypatch.setattr(bot, "_job_expected_interval_sec", {})
    monkeypatch.setattr(bot, "_DATA_SOURCE_STATUS",
                         {"cmc": {"consecutive_failures": bot._SOURCE_ALERT_THRESHOLD, "last_error": "429"}})
    monkeypatch.setattr(bot, "_OPTIONAL_SOURCES", set())
    _run(bot.run_watchdog(MagicMock()))
    assert len(calls) == 1
    assert calls[0]["critical"] is False


def time_module():
    import time
    return time


# ── retrofit: run_daily_backup() failure alert is critical ──────────────

def test_daily_backup_failure_alert_is_critical(monkeypatch):
    calls = []

    async def _fake_send_system(bot_arg, text, critical=False, **kw):
        calls.append({"text": text, "critical": critical})

    monkeypatch.setattr(bot, "send_system", _fake_send_system)

    async def _fail(*a, **kw):
        return False

    monkeypatch.setattr(bot.subscribers, "backup_snapshot", _fail)
    monkeypatch.setattr(bot.signal_journal, "backup_snapshot", _fail)
    _run(bot.run_daily_backup(MagicMock()))
    assert len(calls) == 1
    assert calls[0]["critical"] is True


def test_daily_backup_success_no_alert(monkeypatch):
    calls = []

    async def _fake_send_system(bot_arg, text, critical=False, **kw):
        calls.append({"text": text, "critical": critical})

    monkeypatch.setattr(bot, "send_system", _fake_send_system)

    async def _ok(*a, **kw):
        return True

    monkeypatch.setattr(bot.subscribers, "backup_snapshot", _ok)
    monkeypatch.setattr(bot.signal_journal, "backup_snapshot", _ok)
    _run(bot.run_daily_backup(MagicMock()))
    assert len(calls) == 0


# ── retrofit: _startup_integrity_check() uses send_system, non-critical ──

def test_startup_integrity_check_uses_send_system_non_critical(monkeypatch, tmp_path):
    # Владелец, приёмка v130 (2026-07-16): STARTUP_NOTIFY_STATE_FILE изолируется
    # от реального journal/ -- иначе повторный локальный прогон этого теста в
    # течение 30 мин throttle'ится собственным же предыдущим запуском.
    monkeypatch.setattr(bot, "STARTUP_NOTIFY_STATE_FILE", str(tmp_path / "last_startup_notify.json"))
    calls = []

    async def _fake_send_system(bot_arg, text, critical=False, **kw):
        calls.append({"text": text, "critical": critical, "kw": kw})

    monkeypatch.setattr(bot, "send_system", _fake_send_system)
    _run(bot._startup_integrity_check(MagicMock(), 7009350191))
    assert len(calls) == 1
    assert calls[0]["critical"] is False
    assert calls[0]["kw"].get("parse_mode") == "Markdown"
