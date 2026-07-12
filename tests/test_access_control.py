"""
pytest для access_control.py -- deny-by-default auth (Пакет SECURITY-HARDENING М1).
Update/Context -- лёгкие фейки (не тянем telegram.ext сюда, только
ApplicationHandlerStop нужен реально -- он импортируется внутри enforce()).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OWNER_CHAT_ID", "7009350191")

import access_control as ac
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
