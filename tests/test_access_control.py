"""
pytest для access_control.py -- deny-by-default auth (Пакет SECURITY-HARDENING М1).
Update/Context -- лёгкие фейки (не тянем telegram.ext сюда, только
ApplicationHandlerStop нужен реально -- он импортируется внутри enforce()).
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OWNER_CHAT_ID", "7009350191")

import access_control as ac
import security_log as sl
import subscribers as sub


class _FakeChat:
    def __init__(self, cid):
        self.id = cid


class _FakeMessage:
    def __init__(self, text):
        self.text = text


class _FakeUpdate:
    def __init__(self, chat_id, text=None, is_callback=False):
        self.effective_chat = _FakeChat(chat_id) if chat_id is not None else None
        self.effective_message = _FakeMessage(text) if text is not None else None
        self.callback_query = object() if is_callback else None


class _FakeContext:
    def __init__(self, args=None):
        self.args = args or []


def _reset_state(monkeypatch):
    monkeypatch.setattr(sub, "_subscribers", {})
    monkeypatch.setattr(sub, "_invite_codes", {})
    # enforce() пишет в security_log.log_event() на каждый вызов -- эти тесты не должны
    # трогать реальный локальный journal/security_log.json (найдено живьём: pytest-прогон
    # молча писал туда тестовые chat_id вроде 123456/555/666/777, которые позже утекли на
    # GitHub через ручной sync_to_github() при живой проверке SEC М4 -- см. PROGRESS.md).
    monkeypatch.setattr(sl, "log_event", lambda *a, **kw: None)


def _set_role(chat_id, role, monkeypatch, expires_ts=None):
    sub._subscribers[chat_id] = {"subscribed": True, "updated_ts": 1.0, "role": role,
                                  "role_expires_ts": expires_ts}


async def _run_enforce(update, context=None):
    context = context or _FakeContext()
    try:
        await ac.enforce(update, context)
        return "ALLOWED"
    except Exception as e:
        if type(e).__name__ == "ApplicationHandlerStop":
            return "DENIED"
        raise


def test_owner_chat_id_always_owner_role_regardless_of_store(monkeypatch):
    _reset_state(monkeypatch)
    owner_id = ac._owner_id()
    assert ac.get_role(owner_id) == ac.ROLE_OWNER


def test_owner_role_even_if_store_has_conflicting_none(monkeypatch):
    """Владелец не может сам себя случайно понизить/заблокировать этим механизмом --
    даже если в сторе стоит role=NONE для его же chat_id (например, баг/опечатка)."""
    _reset_state(monkeypatch)
    owner_id = ac._owner_id()
    _set_role(owner_id, sub.ROLE_NONE, monkeypatch)
    assert ac.get_role(owner_id) == ac.ROLE_OWNER


def test_unknown_chat_command_denied_silently(monkeypatch):
    _reset_state(monkeypatch)
    update = _FakeUpdate(chat_id=123456, text="/coin BTC")
    result = asyncio.run(_run_enforce(update))
    assert result == "DENIED"


def test_vip_command_allowed_for_vip_role(monkeypatch):
    _reset_state(monkeypatch)
    _set_role(555, sub.ROLE_VIP, monkeypatch)
    update = _FakeUpdate(chat_id=555, text="/coin BTC")
    result = asyncio.run(_run_enforce(update))
    assert result == "ALLOWED"


def test_vip_command_denied_for_trial_role(monkeypatch):
    _reset_state(monkeypatch)
    _set_role(666, sub.ROLE_TRIAL, monkeypatch)
    update = _FakeUpdate(chat_id=666, text="/coin BTC")
    result = asyncio.run(_run_enforce(update))
    assert result == "DENIED"


def test_trial_command_allowed_for_trial_role(monkeypatch):
    _reset_state(monkeypatch)
    _set_role(666, sub.ROLE_TRIAL, monkeypatch)
    update = _FakeUpdate(chat_id=666, text="/market")
    result = asyncio.run(_run_enforce(update))
    assert result == "ALLOWED"


def test_owner_command_denied_for_vip_role(monkeypatch):
    _reset_state(monkeypatch)
    _set_role(777, sub.ROLE_VIP, monkeypatch)
    update = _FakeUpdate(chat_id=777, text="/grant 123 VIP")
    result = asyncio.run(_run_enforce(update))
    assert result == "DENIED"


def test_owner_command_allowed_for_owner(monkeypatch):
    _reset_state(monkeypatch)
    owner_id = ac._owner_id()
    update = _FakeUpdate(chat_id=owner_id, text="/grant 123 VIP")
    result = asyncio.run(_run_enforce(update))
    assert result == "ALLOWED"


def test_start_without_args_denied_for_unknown_chat(monkeypatch):
    _reset_state(monkeypatch)
    update = _FakeUpdate(chat_id=999, text="/start")
    result = asyncio.run(_run_enforce(update))
    assert result == "DENIED"


def test_start_with_invite_code_arg_allowed_through_for_unknown_chat(monkeypatch):
    """/start <code> -- пропускается дальше (не блокируется здесь), редемпшн кода
    обрабатывает сам cmd_start в bot.py."""
    _reset_state(monkeypatch)
    update = _FakeUpdate(chat_id=999, text="/start SOMECODE")
    context = _FakeContext(args=["SOMECODE"])
    result = asyncio.run(_run_enforce(update, context))
    assert result == "ALLOWED"


def test_start_without_args_allowed_for_already_known_role(monkeypatch):
    _reset_state(monkeypatch)
    _set_role(1010, sub.ROLE_VIP, monkeypatch)
    update = _FakeUpdate(chat_id=1010, text="/start")
    result = asyncio.run(_run_enforce(update))
    assert result == "ALLOWED"


def test_callback_query_allowed_for_known_role(monkeypatch):
    _reset_state(monkeypatch)
    _set_role(2020, sub.ROLE_VIP, monkeypatch)
    update = _FakeUpdate(chat_id=2020, is_callback=True)
    result = asyncio.run(_run_enforce(update))
    assert result == "ALLOWED"


def test_callback_query_denied_for_unknown_chat(monkeypatch):
    _reset_state(monkeypatch)
    update = _FakeUpdate(chat_id=3030, is_callback=True)
    result = asyncio.run(_run_enforce(update))
    assert result == "DENIED"


def test_free_text_allowed_for_known_role(monkeypatch):
    _reset_state(monkeypatch)
    _set_role(4040, sub.ROLE_VIP, monkeypatch)
    update = _FakeUpdate(chat_id=4040, text="BTC")  # не команда, просто текст
    result = asyncio.run(_run_enforce(update))
    assert result == "ALLOWED"


def test_free_text_denied_for_unknown_chat(monkeypatch):
    _reset_state(monkeypatch)
    update = _FakeUpdate(chat_id=5050, text="BTC")
    result = asyncio.run(_run_enforce(update))
    assert result == "DENIED"


def test_unmapped_command_defaults_to_owner_only_deny_by_default(monkeypatch):
    """Неизвестная команда, отсутствующая в COMMAND_ROLE_MAP -- безопасный дефолт
    (deny by default): требует OWNER, даже VIP не пройдёт."""
    _reset_state(monkeypatch)
    _set_role(6060, sub.ROLE_VIP, monkeypatch)
    update = _FakeUpdate(chat_id=6060, text="/some_future_unmapped_command")
    result = asyncio.run(_run_enforce(update))
    assert result == "DENIED"


def test_expired_trial_denied(monkeypatch):
    _reset_state(monkeypatch)
    _set_role(7070, sub.ROLE_TRIAL, monkeypatch, expires_ts=1.0)  # давно истёк
    update = _FakeUpdate(chat_id=7070, text="/market")
    result = asyncio.run(_run_enforce(update))
    assert result == "DENIED"


def test_role_allows_hierarchy():
    assert ac.role_allows(ac.ROLE_OWNER, ac.ROLE_VIP) is True
    assert ac.role_allows(ac.ROLE_VIP, ac.ROLE_OWNER) is False
    assert ac.role_allows(ac.ROLE_VIP, ac.ROLE_VIP) is True
    assert ac.role_allows(ac.ROLE_NONE, ac.ROLE_TRIAL) is False


# --- Пакет SECURITY-HARDENING М3: анти-абьюз (rate-limit/flood-guard/автобан) ---

def _reset_abuse_state(monkeypatch):
    monkeypatch.setattr(ac, "_command_history", {})
    monkeypatch.setattr(ac, "_cooldown_until", {})
    monkeypatch.setattr(ac, "_invite_fail_count", {})
    monkeypatch.setattr(ac, "_global_command_history", [])
    monkeypatch.setattr(ac, "_last_flood_alert_ts", 0.0)


def test_prune_window_removes_old_entries():
    ts = [9.0, 10.0, 20.0, 59.0, 60.0]
    result = ac._prune_window(ts, now=70.0, window_sec=60.0)
    # cutoff = now - window_sec = 10.0, граница ВКЛЮЧИТЕЛЬНО (t >= cutoff)
    assert result == [10.0, 20.0, 59.0, 60.0]  # 9.0 -- строго старше границы, вычищен


def test_check_rate_limit_allows_under_threshold(monkeypatch):
    _reset_abuse_state(monkeypatch)
    now = 1000.0
    for i in range(ac.RATE_LIMIT_MAX_PER_MIN):
        assert ac.check_rate_limit(111, now=now + i * 0.1) is True


def test_check_rate_limit_blocks_over_threshold(monkeypatch):
    _reset_abuse_state(monkeypatch)
    now = 1000.0
    for i in range(ac.RATE_LIMIT_MAX_PER_MIN):
        ac.check_rate_limit(222, now=now + i * 0.1)
    # следующий запрос в том же окне -- превышение
    assert ac.check_rate_limit(222, now=now + 1.0) is False


def test_check_rate_limit_cooldown_persists_until_expiry(monkeypatch):
    _reset_abuse_state(monkeypatch)
    now = 1000.0
    for i in range(ac.RATE_LIMIT_MAX_PER_MIN + 1):
        ac.check_rate_limit(333, now=now + i * 0.1)
    # ещё внутри кулдауна -- заблокирован, даже если формально окно истории пустое
    assert ac.check_rate_limit(333, now=now + 5.0) is False
    # после истечения кулдауна -- снова разрешено
    assert ac.check_rate_limit(333, now=now + ac.RATE_LIMIT_COOLDOWN_SEC + 10.0) is True


def test_check_rate_limit_independent_per_chat_id(monkeypatch):
    _reset_abuse_state(monkeypatch)
    now = 1000.0
    for i in range(ac.RATE_LIMIT_MAX_PER_MIN + 1):
        ac.check_rate_limit(444, now=now + i * 0.1)
    assert ac.check_rate_limit(444, now=now + 1.0) is False
    assert ac.check_rate_limit(555, now=now + 1.0) is True  # другой chat_id -- свежий лимит


def test_check_global_flood_triggers_over_threshold(monkeypatch):
    _reset_abuse_state(monkeypatch)
    now = 2000.0
    triggered = False
    for i in range(ac.GLOBAL_FLOOD_THRESHOLD_PER_MIN + 5):
        triggered = ac.check_global_flood(now=now + i * 0.01)
    assert triggered is True


def test_check_global_flood_not_triggered_under_threshold(monkeypatch):
    _reset_abuse_state(monkeypatch)
    now = 2000.0
    triggered = False
    for i in range(10):
        triggered = ac.check_global_flood(now=now + i * 0.01)
    assert triggered is False


def test_record_invite_failure_reaches_ban_threshold(monkeypatch):
    _reset_abuse_state(monkeypatch)
    banned = False
    for _ in range(ac.INVITE_FAIL_BAN_THRESHOLD):
        banned = ac.record_invite_failure(666)
    assert banned is True


def test_record_invite_failure_not_banned_below_threshold(monkeypatch):
    _reset_abuse_state(monkeypatch)
    banned = False
    for _ in range(ac.INVITE_FAIL_BAN_THRESHOLD - 1):
        banned = ac.record_invite_failure(777)
    assert banned is False


def test_reset_invite_failures_clears_counter(monkeypatch):
    _reset_abuse_state(monkeypatch)
    ac.record_invite_failure(888)
    ac.record_invite_failure(888)
    ac.reset_invite_failures(888)
    assert ac._invite_fail_count.get(888, 0) == 0


def test_owner_exempt_from_rate_limit_in_enforce(monkeypatch):
    """OWNER не подлежит rate-limit вообще -- enforce() не должен звать
    check_rate_limit для OWNER-роли (иначе даже владелец мог бы себя
    зарейтлимитить частыми командами)."""
    _reset_state(monkeypatch)
    _reset_abuse_state(monkeypatch)
    owner_id = ac._owner_id()
    calls = {"count": 0}
    orig = ac.check_rate_limit

    def _spy(*a, **kw):
        calls["count"] += 1
        return orig(*a, **kw)

    monkeypatch.setattr(ac, "check_rate_limit", _spy)
    for _ in range(ac.RATE_LIMIT_MAX_PER_MIN + 5):
        update = _FakeUpdate(chat_id=owner_id, text="/market")
        asyncio.run(_run_enforce(update))
    assert calls["count"] == 0


def test_non_owner_gets_rate_limited_in_enforce(monkeypatch):
    _reset_state(monkeypatch)
    _reset_abuse_state(monkeypatch)
    _set_role(999123, sub.ROLE_VIP, monkeypatch)
    results = []
    for _ in range(ac.RATE_LIMIT_MAX_PER_MIN + 3):
        update = _FakeUpdate(chat_id=999123, text="/market")
        results.append(asyncio.run(_run_enforce(update)))
    assert results[0] == "ALLOWED"
    assert "DENIED" in results  # где-то после превышения лимита должен появиться отказ


# --- Lockdown (Пакет SECURITY-HARDENING М7) -----------------------------------------

def _reset_lockdown_state(monkeypatch, tmp_path):
    monkeypatch.setattr(ac, "_lockdown_active", False)
    monkeypatch.setattr(ac, "LOCKDOWN_STATE_FILE", str(tmp_path / "lockdown_state.json"))


def test_is_locked_down_default_false(monkeypatch, tmp_path):
    _reset_lockdown_state(monkeypatch, tmp_path)
    assert ac.is_locked_down() is False


def test_set_lockdown_true_sets_in_memory_flag_immediately(monkeypatch, tmp_path):
    _reset_lockdown_state(monkeypatch, tmp_path)
    monkeypatch.setattr(ac, "_github_get_lockdown_sync", lambda: (None, None))
    monkeypatch.setattr(ac, "_github_put_lockdown_sync", lambda active, sha: None)
    asyncio.run(ac.set_lockdown(True))
    assert ac.is_locked_down() is True


def test_set_lockdown_false_clears_flag(monkeypatch, tmp_path):
    _reset_lockdown_state(monkeypatch, tmp_path)
    monkeypatch.setattr(ac, "_github_get_lockdown_sync", lambda: (None, None))
    monkeypatch.setattr(ac, "_github_put_lockdown_sync", lambda active, sha: None)
    monkeypatch.setattr(ac, "_lockdown_active", True)
    asyncio.run(ac.set_lockdown(False))
    assert ac.is_locked_down() is False


def test_set_lockdown_persists_to_local_file(monkeypatch, tmp_path):
    _reset_lockdown_state(monkeypatch, tmp_path)
    monkeypatch.setattr(ac, "_github_get_lockdown_sync", lambda: (None, None))
    monkeypatch.setattr(ac, "_github_put_lockdown_sync", lambda active, sha: None)
    asyncio.run(ac.set_lockdown(True))
    assert ac._load_lockdown_local() is True


def test_load_lockdown_state_prefers_github_when_available(monkeypatch, tmp_path):
    _reset_lockdown_state(monkeypatch, tmp_path)
    monkeypatch.setattr(ac, "_github_get_lockdown_sync", lambda: (True, "sha1"))
    asyncio.run(ac.load_lockdown_state())
    assert ac.is_locked_down() is True
    # локальный кэш тоже обновлён -- переживает следующий рестарт, если GitHub недоступен
    assert ac._load_lockdown_local() is True


def test_load_lockdown_state_falls_back_to_local_on_github_failure(monkeypatch, tmp_path):
    _reset_lockdown_state(monkeypatch, tmp_path)
    ac._atomic_write_json(ac.LOCKDOWN_STATE_FILE, {"active": True, "ts": 1.0})
    monkeypatch.setattr(ac, "_github_get_lockdown_sync", lambda: (None, None))
    asyncio.run(ac.load_lockdown_state())
    assert ac.is_locked_down() is True


def test_load_lockdown_state_defaults_false_when_nothing_available(monkeypatch, tmp_path):
    _reset_lockdown_state(monkeypatch, tmp_path)
    monkeypatch.setattr(ac, "_github_get_lockdown_sync", lambda: (None, None))
    asyncio.run(ac.load_lockdown_state())
    assert ac.is_locked_down() is False


def test_enforce_denies_vip_during_lockdown(monkeypatch, tmp_path):
    _reset_state(monkeypatch)
    _reset_lockdown_state(monkeypatch, tmp_path)
    _set_role(555444, sub.ROLE_VIP, monkeypatch)
    monkeypatch.setattr(ac, "_lockdown_active", True)
    update = _FakeUpdate(chat_id=555444, text="/market")
    assert asyncio.run(_run_enforce(update)) == "DENIED"


def test_enforce_allows_owner_during_lockdown(monkeypatch, tmp_path):
    """OWNER должен пройти даже во время lockdown -- иначе некому будет вызвать /unlock."""
    _reset_state(monkeypatch)
    _reset_lockdown_state(monkeypatch, tmp_path)
    monkeypatch.setattr(ac, "_lockdown_active", True)
    owner_id = ac._owner_id()
    update = _FakeUpdate(chat_id=owner_id, text="/unlock")
    assert asyncio.run(_run_enforce(update)) == "ALLOWED"


def test_enforce_allows_vip_when_not_locked_down(monkeypatch, tmp_path):
    _reset_state(monkeypatch)
    _reset_lockdown_state(monkeypatch, tmp_path)
    _set_role(555444, sub.ROLE_VIP, monkeypatch)
    update = _FakeUpdate(chat_id=555444, text="/market")
    assert asyncio.run(_run_enforce(update)) == "ALLOWED"


# --- П-Каналы (владелец, 2026-07-15): flood-guard алерт через bot.send_system ---

def test_maybe_alert_owner_flood_uses_send_system(monkeypatch):
    """_maybe_alert_owner_flood() лениво импортирует bot.py (bot.py импортирует
    access_control на уровне модуля -- обратный импорт на уровне модуля здесь
    создал бы циклическую зависимость) и зовёт bot.send_system(). Патчим
    bot.send_system напрямую -- лениво импортированный bot -- тот же кэшированный
    модуль из sys.modules, что уже загружен другими тестовыми файлами."""
    import bot as _bot_module

    calls = []

    async def _fake_send_system(bot_arg, text, critical=False, **kw):
        calls.append({"text": text, "critical": critical, "kw": kw})

    monkeypatch.setattr(_bot_module, "send_system", _fake_send_system)
    monkeypatch.setattr(ac, "_last_flood_alert_ts", 0.0)

    class _FakeContext:
        bot = object()

    asyncio.run(ac._maybe_alert_owner_flood(_FakeContext()))
    assert len(calls) == 1
    assert "Flood-guard" in calls[0]["text"]
    assert calls[0]["kw"].get("parse_mode") == "Markdown"


def test_maybe_alert_owner_flood_respects_cooldown(monkeypatch):
    import bot as _bot_module

    calls = []

    async def _fake_send_system(bot_arg, text, critical=False, **kw):
        calls.append(1)

    monkeypatch.setattr(_bot_module, "send_system", _fake_send_system)
    monkeypatch.setattr(ac, "_last_flood_alert_ts", time.time())  # только что алертили

    class _FakeContext:
        bot = object()

    asyncio.run(ac._maybe_alert_owner_flood(_FakeContext()))
    assert len(calls) == 0  # в пределах кулдауна -- не алертит повторно
