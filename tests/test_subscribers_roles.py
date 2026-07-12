"""
pytest для subscribers.py -- роли доступа (Пакет SECURITY-HARDENING М1, владелец "да").
Файловый I/O (GitHub) изолирован через monkeypatch на _commit_to_github (не-op) --
эти тесты проверяют чистую логику ролей/грандфазеринга/просрочки/инвайт-кодов.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import subscribers as sub


def _noop_commit(monkeypatch):
    async def _fake(*a, **kw):
        return None
    monkeypatch.setattr(sub, "_commit_to_github", _fake)


def _reset_state(monkeypatch):
    monkeypatch.setattr(sub, "_subscribers", {})
    monkeypatch.setattr(sub, "_invite_codes", {})
    monkeypatch.setattr(sub, "_dirty", False)


def test_get_role_raw_unknown_chat_is_none(monkeypatch):
    _reset_state(monkeypatch)
    assert sub.get_role_raw(999) == sub.ROLE_NONE


def test_get_role_raw_legacy_subscribed_without_role_is_vip_grandfather(monkeypatch):
    _reset_state(monkeypatch)
    sub._subscribers[111] = {"subscribed": True, "updated_ts": time.time()}
    assert sub.get_role_raw(111) == sub.ROLE_VIP


def test_get_role_raw_legacy_unsubscribed_without_role_is_none(monkeypatch):
    _reset_state(monkeypatch)
    sub._subscribers[111] = {"subscribed": False, "updated_ts": time.time()}
    assert sub.get_role_raw(111) == sub.ROLE_NONE


def test_get_role_raw_explicit_role_returned(monkeypatch):
    _reset_state(monkeypatch)
    sub._subscribers[222] = {"subscribed": True, "updated_ts": time.time(), "role": sub.ROLE_VIP}
    assert sub.get_role_raw(222) == sub.ROLE_VIP


def test_get_role_raw_trial_not_expired(monkeypatch):
    _reset_state(monkeypatch)
    sub._subscribers[333] = {
        "subscribed": True, "updated_ts": time.time(),
        "role": sub.ROLE_TRIAL, "role_expires_ts": time.time() + 3600,
    }
    assert sub.get_role_raw(333) == sub.ROLE_TRIAL


def test_get_role_raw_trial_expired_becomes_none(monkeypatch):
    _reset_state(monkeypatch)
    sub._subscribers[444] = {
        "subscribed": True, "updated_ts": time.time(),
        "role": sub.ROLE_TRIAL, "role_expires_ts": time.time() - 1,
    }
    assert sub.get_role_raw(444) == sub.ROLE_NONE


def test_subscribe_preserves_existing_role(monkeypatch):
    """Регрессионный тест на находку в этом же пакете: subscribe() раньше полностью
    перезаписывала запись, стирая role при повторном /start."""
    _reset_state(monkeypatch)
    _noop_commit(monkeypatch)
    sub._subscribers[555] = {"subscribed": False, "updated_ts": 1.0, "role": sub.ROLE_VIP}
    asyncio.run(sub.subscribe(555))
    assert sub._subscribers[555]["role"] == sub.ROLE_VIP
    assert sub._subscribers[555]["subscribed"] is True


def test_unsubscribe_preserves_existing_role(monkeypatch):
    _reset_state(monkeypatch)
    _noop_commit(monkeypatch)
    sub._subscribers[666] = {"subscribed": True, "updated_ts": 1.0, "role": sub.ROLE_VIP}
    asyncio.run(sub.unsubscribe(666))
    assert sub._subscribers[666]["role"] == sub.ROLE_VIP
    assert sub._subscribers[666]["subscribed"] is False


def test_set_role_new_chat_id(monkeypatch):
    _reset_state(monkeypatch)
    _noop_commit(monkeypatch)
    asyncio.run(sub.set_role(777, sub.ROLE_VIP))
    assert sub.get_role_raw(777) == sub.ROLE_VIP


def test_set_role_none_revokes_access(monkeypatch):
    _reset_state(monkeypatch)
    _noop_commit(monkeypatch)
    sub._subscribers[888] = {"subscribed": True, "updated_ts": 1.0, "role": sub.ROLE_VIP}
    asyncio.run(sub.set_role(888, sub.ROLE_NONE))
    assert sub.get_role_raw(888) == sub.ROLE_NONE


def test_generate_and_redeem_invite_code(monkeypatch):
    _reset_state(monkeypatch)
    _noop_commit(monkeypatch)
    code = asyncio.run(sub.generate_invite_code(sub.ROLE_VIP))
    assert code in sub._invite_codes
    assert sub._invite_codes[code]["used"] is False

    role = asyncio.run(sub.redeem_invite_code(code, 999))
    assert role == sub.ROLE_VIP
    assert sub.get_role_raw(999) == sub.ROLE_VIP
    assert sub._invite_codes[code]["used"] is True
    assert sub._invite_codes[code]["used_by"] == 999


def test_redeem_invite_code_cannot_be_used_twice(monkeypatch):
    _reset_state(monkeypatch)
    _noop_commit(monkeypatch)
    code = asyncio.run(sub.generate_invite_code(sub.ROLE_VIP))
    asyncio.run(sub.redeem_invite_code(code, 111))
    role_second = asyncio.run(sub.redeem_invite_code(code, 222))
    assert role_second is None
    assert sub.get_role_raw(222) == sub.ROLE_NONE  # второй редемпшн не сработал


def test_redeem_unknown_code_returns_none(monkeypatch):
    _reset_state(monkeypatch)
    _noop_commit(monkeypatch)
    role = asyncio.run(sub.redeem_invite_code("not-a-real-code", 333))
    assert role is None


def test_redeem_invite_code_with_expiry_sets_role_expires_ts(monkeypatch):
    _reset_state(monkeypatch)
    _noop_commit(monkeypatch)
    code = asyncio.run(sub.generate_invite_code(sub.ROLE_TRIAL, expires_days=7))
    asyncio.run(sub.redeem_invite_code(code, 444))
    rec = sub._subscribers[444]
    assert rec["role"] == sub.ROLE_TRIAL
    assert rec["role_expires_ts"] is not None
    assert rec["role_expires_ts"] > time.time()


def test_list_users_includes_role(monkeypatch):
    _reset_state(monkeypatch)
    sub._subscribers[1] = {"subscribed": True, "updated_ts": 100.0, "role": sub.ROLE_VIP}
    sub._subscribers[2] = {"subscribed": True, "updated_ts": 200.0, "role": sub.ROLE_OWNER}
    users = sub.list_users()
    assert len(users) == 2
    # sorted by updated_ts descending -- chat_id 2 (newer) first
    assert users[0]["chat_id"] == 2
    assert users[0]["role"] == sub.ROLE_OWNER
    assert users[1]["role"] == sub.ROLE_VIP


def test_merge_invite_codes_used_wins():
    local = {"abc": {"used": False}}
    remote = {"abc": {"used": True, "used_by": 5}}
    merged = sub._merge_invite_codes(local, remote)
    assert merged["abc"]["used"] is True
    assert merged["abc"]["used_by"] == 5


def test_merge_invite_codes_local_used_not_overwritten_by_remote_unused():
    local = {"abc": {"used": True, "used_by": 5}}
    remote = {"abc": {"used": False}}
    merged = sub._merge_invite_codes(local, remote)
    assert merged["abc"]["used"] is True
    assert merged["abc"]["used_by"] == 5
