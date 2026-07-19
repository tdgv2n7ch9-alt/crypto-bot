"""
pytest для onchain_watch.py (владелец, срочный наряд 2026-07-17): мониторинг
разлока BANK (Lorenzo Protocol, BSC) -- получатели крупных переводов -> биржа.
Покрывает: (1) декодирование Transfer-логов, (2) tracked-recipient set (первый
хоп -> тихий лог, без алерта), (3) tracked->CEX -> critical алерт, (4) прямой
перевод->CEX выше порога (не tracked) -> отдельный, менее приоритетный алерт,
(5) первый запуск -- честный no-op без бэктеста истории, (6) честный incomplete-
skip при отказе всех RPC-провайдеров.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bsc_wallet_monitor as bwm
import onchain_watch as ow


@pytest.fixture(autouse=True)
def _fixed_rpc_providers(monkeypatch):
    monkeypatch.setattr(bwm, "RPC_PROVIDERS", [
        {"url": "https://primary.test", "max_range": 50},
        {"url": "https://bsc-mainnet.public.blastapi.io", "max_range": 10},
    ])


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(ow, "STATE_FILE", str(tmp_path / "onchain_watch_state.json"))
    monkeypatch.setattr(ow, "EVENTS_FILE", str(tmp_path / "onchain_watch_events.json"))


def _run(coro):
    return asyncio.run(coro)


def _transfer_log(frm, to, amount_tokens, block=1000, tx="0xabc"):
    topic_from = "0x" + frm[2:].rjust(64, "0").lower()
    topic_to = "0x" + to[2:].rjust(64, "0").lower()
    raw = int(amount_tokens * (10 ** ow.BANK_DECIMALS))
    return {
        "blockNumber": hex(block),
        "transactionHash": tx,
        "topics": [ow.TRANSFER_TOPIC, topic_from, topic_to],
        "data": hex(raw),
    }


ANY_ADDR = "0x111111111111111111111111111111111111111a"
ANY_ADDR2 = "0x222222222222222222222222222222222222222b"
CEX_ADDR = next(iter(ow.CEX_DEPOSIT_ADDRESSES))


# ── decode_transfer_log() ───────────────────────────────────────────────────

def test_decode_transfer_log_extracts_from_to_amount():
    lg = _transfer_log(ANY_ADDR, ANY_ADDR2, 12345.5, block=999, tx="0xdead")
    ev = ow.decode_transfer_log(lg)
    assert ev["from"].lower() == ANY_ADDR.lower()
    assert ev["to"].lower() == ANY_ADDR2.lower()
    assert abs(ev["amount"] - 12345.5) < 1e-6
    assert ev["block"] == 999
    assert ev["tx"] == "0xdead"


def test_cex_deposit_addresses_are_full_lowercase_42_char():
    for addr, name in ow.CEX_DEPOSIT_ADDRESSES.items():
        assert addr.startswith("0x")
        assert len(addr) == 42
        assert addr == addr.lower()
        assert name


def test_bank_contract_matches_owner_and_is_lowercase():
    assert ow.BANK_CONTRACT == "0x3aee7602b612de36088f3ffed8c8f10e86ebf2bf"
    assert ow.BANK_CONTRACT == ow.BANK_CONTRACT.lower()


# ── get_transfer_logs() reuse of bsc_wallet_monitor with custom contract ──

def test_get_transfer_logs_uses_bank_contract_not_ake(monkeypatch):
    seen_addresses = []

    def fake_rpc(url, method, params, timeout=15):
        seen_addresses.append(params[0]["address"])
        return {"result": []}

    monkeypatch.setattr(bwm, "_rpc_call", fake_rpc)
    bwm.get_transfer_logs(100, 100, [ow.TRANSFER_TOPIC], ow.BANK_CONTRACT)
    assert seen_addresses == [ow.BANK_CONTRACT]


def test_get_transfer_logs_default_contract_unaffected(monkeypatch):
    """Регресс-замок: не передавая `contract`, AKE-вызовы (существующие
    call sites в check_ake_wallets) должны продолжать использовать
    AKE_CONTRACT -- новый параметр не должен сломать старое поведение."""
    seen_addresses = []

    def fake_rpc(url, method, params, timeout=15):
        seen_addresses.append(params[0]["address"])
        return {"result": []}

    monkeypatch.setattr(bwm, "_rpc_call", fake_rpc)
    bwm.get_transfer_logs(100, 100)
    assert seen_addresses == [bwm.AKE_CONTRACT]


# ── check_bank_unlock() -- сквозные сценарии ───────────────────────────────

class _FakeBot:
    pass


def _stub_run_in_executor(latest_block, logs, price, incomplete=False, last_processed=None):
    async def run(fn, *args):
        if fn is bwm.get_latest_block:
            return latest_block, {"url": "https://primary.test"}
        if fn is bwm.get_transfer_logs:
            lp = last_processed if last_processed is not None else args[1]
            return logs, incomplete, lp
        if fn is ow.get_bank_price_usd:
            return price
        raise AssertionError(f"unexpected fn {fn}")
    return run


def test_first_run_sets_state_no_alert_no_backfill():
    sent = []

    async def send_system_fn(bot, text, critical=False, **kw):
        sent.append(text)

    n = _run(ow.check_bank_unlock(
        _FakeBot(), send_system_fn=send_system_fn,
        run_in_executor_fn=_stub_run_in_executor(latest_block=5000, logs=[], price=0.07),
    ))
    assert n == 0
    assert sent == []
    state = ow._load_state()
    assert state["last_scanned_block"] == 5000


def _seed_state(last_scanned_block=5000, tracked=None):
    ow._save_state({"last_scanned_block": last_scanned_block,
                     "tracked_recipients": tracked or []})


def test_large_transfer_to_unknown_address_tracks_silently_no_alert():
    _seed_state()
    sent = []

    async def send_system_fn(bot, text, critical=False, **kw):
        sent.append(text)

    log = _transfer_log(ANY_ADDR, ANY_ADDR2, ow.LARGE_TRANSFER_THRESHOLD_BANK + 1)
    n = _run(ow.check_bank_unlock(
        _FakeBot(), send_system_fn=send_system_fn,
        run_in_executor_fn=_stub_run_in_executor(latest_block=5050, logs=[log], price=0.07),
    ))
    assert n == 1
    assert sent == []  # первый хоп -- тихо, без алерта
    state = ow._load_state()
    assert ANY_ADDR2.lower() in state["tracked_recipients"]


def test_small_transfer_to_unknown_address_ignored():
    _seed_state()
    sent = []

    async def send_system_fn(bot, text, critical=False, **kw):
        sent.append(text)

    log = _transfer_log(ANY_ADDR, ANY_ADDR2, ow.LARGE_TRANSFER_THRESHOLD_BANK - 1)
    n = _run(ow.check_bank_unlock(
        _FakeBot(), send_system_fn=send_system_fn,
        run_in_executor_fn=_stub_run_in_executor(latest_block=5050, logs=[log], price=0.07),
    ))
    assert n == 0
    assert sent == []
    state = ow._load_state()
    assert state["tracked_recipients"] == []


def test_tracked_recipient_to_cex_triggers_critical_priority_alert():
    _seed_state(tracked=[ANY_ADDR2.lower()])
    sent = []

    async def send_system_fn(bot, text, critical=False, **kw):
        sent.append((text, critical))

    log = _transfer_log(ANY_ADDR2, CEX_ADDR, 1000)  # сумма ниже DIRECT-порога, но это не важно
    n = _run(ow.check_bank_unlock(
        _FakeBot(), send_system_fn=send_system_fn,
        run_in_executor_fn=_stub_run_in_executor(latest_block=5050, logs=[log], price=0.07),
    ))
    assert n == 1
    assert len(sent) == 1
    text, critical = sent[0]
    assert critical is True
    assert "РАЗЛОК" in text
    assert ow.CEX_DEPOSIT_ADDRESSES[CEX_ADDR] in text


def test_direct_untracked_transfer_to_cex_above_threshold_alerts_lower_priority():
    _seed_state()
    sent = []

    async def send_system_fn(bot, text, critical=False, **kw):
        sent.append((text, critical))

    amount = (ow.DIRECT_TO_CEX_ALERT_THRESHOLD_USD / 0.07) + 100
    log = _transfer_log(ANY_ADDR, CEX_ADDR, amount)
    n = _run(ow.check_bank_unlock(
        _FakeBot(), send_system_fn=send_system_fn,
        run_in_executor_fn=_stub_run_in_executor(latest_block=5050, logs=[log], price=0.07),
    ))
    assert n == 1
    assert len(sent) == 1
    text, critical = sent[0]
    assert "не подтверждено" in text


def test_direct_untracked_transfer_to_cex_below_threshold_no_alert():
    _seed_state()
    sent = []

    async def send_system_fn(bot, text, critical=False, **kw):
        sent.append(text)

    amount = (ow.DIRECT_TO_CEX_ALERT_THRESHOLD_USD / 0.07) - 100
    log = _transfer_log(ANY_ADDR, CEX_ADDR, amount)
    n = _run(ow.check_bank_unlock(
        _FakeBot(), send_system_fn=send_system_fn,
        run_in_executor_fn=_stub_run_in_executor(latest_block=5050, logs=[log], price=0.07),
    ))
    assert n == 0
    assert sent == []


def test_incomplete_tick_advances_only_to_last_processed_and_notifies_once():
    _seed_state()
    sent = []

    async def send_system_fn(bot, text, critical=False, **kw):
        sent.append(text)

    n = _run(ow.check_bank_unlock(
        _FakeBot(), send_system_fn=send_system_fn,
        run_in_executor_fn=_stub_run_in_executor(
            latest_block=5050, logs=[], price=0.07, incomplete=True, last_processed=5020),
    ))
    assert n == 0
    state = ow._load_state()
    assert state["last_scanned_block"] == 5020
    assert any("RPC-провайдеры" in t for t in sent)


def test_rescanning_same_tx_hash_does_not_duplicate_alert_or_event():
    """#287 (владелец, 2026-07-18): рескан перекрывающегося диапазона блоков
    (напр. тик прервался между алертом и _save_state()) не должен породить
    второй алерт/вторую запись на ТОТ ЖЕ tx_hash."""
    _seed_state(tracked=[ANY_ADDR2.lower()])
    sent = []

    async def send_system_fn(bot, text, critical=False, **kw):
        sent.append(text)

    log = _transfer_log(ANY_ADDR2, CEX_ADDR, 1000, tx="0xsametx")

    n1 = _run(ow.check_bank_unlock(
        _FakeBot(), send_system_fn=send_system_fn,
        run_in_executor_fn=_stub_run_in_executor(latest_block=5050, logs=[log], price=0.07),
    ))
    assert n1 == 1
    assert len(sent) == 1

    # Симулируем рескан того же диапазона (state["last_scanned_block"] НЕ
    # продвинут за пределы этого tx -- тот же лог возвращается снова).
    n2 = _run(ow.check_bank_unlock(
        _FakeBot(), send_system_fn=send_system_fn,
        run_in_executor_fn=_stub_run_in_executor(latest_block=5050, logs=[log], price=0.07),
    ))
    assert n2 == 0  # #287: дубль по tx_hash пропущен, не считается новым событием
    assert len(sent) == 1  # ни одного повторного алерта

    events = ow._load_events()
    assert len(events) == 1  # ни одной повторной записи в EVENTS_FILE


def test_no_new_blocks_since_last_scan_returns_zero_without_calling_price():
    _seed_state(last_scanned_block=9999)

    async def run_in_executor_fn(fn, *args):
        if fn is bwm.get_latest_block:
            return 9999, {"url": "https://primary.test"}
        raise AssertionError("не должно вызываться при from_block > latest")

    async def send_system_fn(bot, text, critical=False, **kw):
        raise AssertionError("не должно алертить без новых блоков")

    n = _run(ow.check_bank_unlock(_FakeBot(), send_system_fn=send_system_fn, run_in_executor_fn=run_in_executor_fn))
    assert n == 0
