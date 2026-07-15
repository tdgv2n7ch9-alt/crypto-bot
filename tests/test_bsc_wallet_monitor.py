"""
pytest для bsc_wallet_monitor.py (владелец, задача #226, 2026-07-15): мониторинг
оттока топ-кошельков AKE через публичный BSC RPC (без API-ключей). Покрывает:
(1) декодирование Transfer-логов, (2) чанкинг/fallback между RPC-провайдерами,
(3) честные "н/д"/skip-пути при отказе всех провайдеров, (4) порог алерта и
critical=True роутинг через send_system, (5) shadow-журнал пишет ВСЕ события,
не только те, что перешли порог, (6) синтетический прогон -- известный
Transfer >$200K действительно триггерит critical-алерт.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bsc_wallet_monitor as bwm


def _run(coro):
    return asyncio.run(coro)


def _transfer_log(frm, to, amount_tokens, block=1000, tx="0xabc"):
    topic_from = "0x" + frm[2:].rjust(64, "0").lower()
    topic_to = "0x" + to[2:].rjust(64, "0").lower()
    raw = int(amount_tokens * (10 ** bwm.AKE_DECIMALS))
    return {
        "blockNumber": hex(block),
        "transactionHash": tx,
        "topics": [bwm.TRANSFER_TOPIC, topic_from, topic_to],
        "data": hex(raw),
    }


# ── decode_transfer_log() ───────────────────────────────────────────────────

def test_decode_transfer_log_extracts_from_to_amount():
    frm = bwm.AKE_WATCHED_WALLETS[0]
    to = "0x000000000000000000000000000000000000dead"
    lg = _transfer_log(frm, to, 1_000_000.5, block=12345, tx="0xdeadbeef")
    ev = bwm.decode_transfer_log(lg)
    assert ev["from"].lower() == frm.lower()
    assert ev["to"].lower() == to.lower()
    assert abs(ev["amount"] - 1_000_000.5) < 1e-6
    assert ev["block"] == 12345
    assert ev["tx"] == "0xdeadbeef"


def test_watched_wallets_are_full_42_char_addresses():
    """Регресс-замок: усечённые адреса ("0x2733...d1f42c") никогда не должны
    попасть в конфиг -- иначе весь topics-фильтр молча ловит 0 совпадений."""
    for w in bwm.AKE_WATCHED_WALLETS:
        assert w.startswith("0x")
        assert len(w) == 42, f"{w} не похож на полный адрес"


def test_wallet_topics_padded_correctly():
    topics = bwm._wallet_topics()
    assert topics[0] == bwm.TRANSFER_TOPIC
    assert len(topics[1]) == len(bwm.AKE_WATCHED_WALLETS)
    for t in topics[1]:
        assert len(t) == 66  # "0x" + 64 hex chars
        assert t.startswith("0x")


# ── get_transfer_logs() -- fallback/chunking (мок _rpc_call) ────────────────

def test_get_transfer_logs_uses_primary_provider(monkeypatch):
    calls = []

    def fake_rpc(url, method, params, timeout=15):
        calls.append((url, method, params))
        if method == "eth_getLogs":
            return {"result": []}
        return {"result": "0x0"}

    monkeypatch.setattr(bwm, "_rpc_call", fake_rpc)
    bwm.get_transfer_logs(100, 100)
    assert calls[0][0] == bwm.RPC_PROVIDERS[0]["url"]


def test_get_transfer_logs_falls_back_on_primary_error(monkeypatch):
    def fake_rpc(url, method, params, timeout=15):
        if method != "eth_getLogs":
            return {"result": "0x0"}
        if url == bwm.RPC_PROVIDERS[0]["url"]:
            return {"error": {"code": -32001, "message": "usage limit"}}
        return {"result": [_transfer_log(bwm.AKE_WATCHED_WALLETS[0], "0xdead", 5, block=100)]}

    monkeypatch.setattr(bwm, "_rpc_call", fake_rpc)
    logs = bwm.get_transfer_logs(100, 100)
    assert len(logs) == 1


def test_get_transfer_logs_skips_range_honestly_when_all_providers_fail(monkeypatch, caplog):
    def fake_rpc(url, method, params, timeout=15):
        if method != "eth_getLogs":
            return {"result": "0x0"}
        return {"error": {"code": -32005, "message": "limit exceeded"}}

    monkeypatch.setattr(bwm, "_rpc_call", fake_rpc)
    logs = bwm.get_transfer_logs(100, 100)
    assert logs == []  # честный пустой результат, не выдуманные данные


def test_get_transfer_logs_chunks_wide_range(monkeypatch):
    """Диапазон шире max_range провайдера -- должен разбиться на несколько
    запросов, не упасть и не пропустить ни одного чанка."""
    seen_ranges = []

    def fake_rpc(url, method, params, timeout=15):
        if method != "eth_getLogs":
            return {"result": "0x0"}
        rng = params[0]
        seen_ranges.append((int(rng["fromBlock"], 16), int(rng["toBlock"], 16)))
        return {"result": []}

    monkeypatch.setattr(bwm, "_rpc_call", fake_rpc)
    max_range = bwm.RPC_PROVIDERS[0]["max_range"]
    bwm.get_transfer_logs(0, max_range * 3 - 1)
    assert len(seen_ranges) == 3
    assert seen_ranges[0] == (0, max_range - 1)
    assert seen_ranges[-1][1] == max_range * 3 - 1


# ── get_ake_price_usd() -- честный н/д при сбое ─────────────────────────────

def test_price_fetch_failure_returns_zero_not_exception(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("network down")

    monkeypatch.setattr(bwm.requests, "get", boom)
    assert bwm.get_ake_price_usd() == 0.0


# ── check_ake_wallets() -- порог, алерт, shadow-журнал ──────────────────────

def _fresh_state_files(monkeypatch, tmp_path):
    monkeypatch.setattr(bwm, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(bwm, "EVENTS_FILE", str(tmp_path / "events.json"))


def test_first_run_only_bookmarks_no_history_scan(monkeypatch, tmp_path):
    _fresh_state_files(monkeypatch, tmp_path)
    monkeypatch.setattr(bwm, "get_latest_block", lambda: (1000, bwm.RPC_PROVIDERS[0]))

    called = {"logs": False}

    def fake_get_logs(a, b):
        called["logs"] = True
        return []

    monkeypatch.setattr(bwm, "get_transfer_logs", fake_get_logs)

    sent = []
    async def fake_send_system(bot, text, critical=False):
        sent.append((text, critical))

    n = _run(bwm.check_ake_wallets(bot=None, send_system_fn=fake_send_system))
    assert n == 0
    assert called["logs"] is False  # первый запуск -- НЕ сканирует историю
    assert sent == []
    state = bwm._load_state()
    assert state["last_scanned_block"] == 1000


def test_below_threshold_logs_shadow_but_no_alert(monkeypatch, tmp_path):
    _fresh_state_files(monkeypatch, tmp_path)
    bwm._save_state({"last_scanned_block": 999})
    monkeypatch.setattr(bwm, "get_latest_block", lambda: (1001, bwm.RPC_PROVIDERS[0]))
    monkeypatch.setattr(bwm, "get_transfer_logs",
                         lambda a, b: [_transfer_log(bwm.AKE_WATCHED_WALLETS[0], "0xdead", 100, block=1000)])
    monkeypatch.setattr(bwm, "get_ake_price_usd", lambda: 0.001)  # 100 * 0.001 = $0.1, ниже порога

    sent = []
    async def fake_send_system(bot, text, critical=False):
        sent.append((text, critical))

    n = _run(bwm.check_ake_wallets(bot=None, send_system_fn=fake_send_system))
    assert n == 1
    assert sent == []  # ниже порога -- без алерта
    events = bwm._load_events()
    assert len(events) == 1
    assert events[0]["usd"] == 0.1


def test_synthetic_run_known_transfer_above_200k_triggers_critical_alert(monkeypatch, tmp_path):
    """DoD владельца дословно: "подставить в фильтр исторический блок с известным
    Transfer >$200K -> алерт срабатывает в ОБА канала". Здесь -- синтетический
    Transfer 300M AKE по цене $0.001 = $300K (> порога), проверяем, что
    send_system вызывается с critical=True (это и есть маршрут "оба канала",
    см. send_system() докстринг в bot.py)."""
    _fresh_state_files(monkeypatch, tmp_path)
    bwm._save_state({"last_scanned_block": 999})
    monkeypatch.setattr(bwm, "get_latest_block", lambda: (1001, bwm.RPC_PROVIDERS[0]))

    wallet = bwm.AKE_WATCHED_WALLETS[0]
    known_log = _transfer_log(wallet, "0x00000000000000000000000000000000000000ee", 300_000_000, block=1000, tx="0xknown200k")
    monkeypatch.setattr(bwm, "get_transfer_logs", lambda a, b: [known_log])
    monkeypatch.setattr(bwm, "get_ake_price_usd", lambda: 0.001)  # 300M * 0.001 = $300K

    sent = []
    async def fake_send_system(bot, text, critical=False):
        sent.append((text, critical))

    n = _run(bwm.check_ake_wallets(bot=None, send_system_fn=fake_send_system))
    assert n == 1
    assert len(sent) == 1
    text, critical = sent[0]
    assert critical is True  # ключевая проверка -- "оба канала" маршрутизируется через critical=True
    assert wallet.lower() in text.lower()
    assert "300,000,000" in text or "300000000" in text.replace(",", "")
    events = bwm._load_events()
    assert events[0]["usd"] == 300_000.0


def test_no_new_blocks_is_noop(monkeypatch, tmp_path):
    _fresh_state_files(monkeypatch, tmp_path)
    bwm._save_state({"last_scanned_block": 1000})
    monkeypatch.setattr(bwm, "get_latest_block", lambda: (1000, bwm.RPC_PROVIDERS[0]))

    called = {"logs": False}
    def fake_get_logs(a, b):
        called["logs"] = True
        return []
    monkeypatch.setattr(bwm, "get_transfer_logs", fake_get_logs)

    n = _run(bwm.check_ake_wallets(bot=None, send_system_fn=lambda *a, **k: None))
    assert n == 0
    assert called["logs"] is False


def test_all_rpc_providers_down_is_honest_noop(monkeypatch, tmp_path):
    _fresh_state_files(monkeypatch, tmp_path)
    monkeypatch.setattr(bwm, "get_latest_block", lambda: (None, None))

    n = _run(bwm.check_ake_wallets(bot=None, send_system_fn=lambda *a, **k: None))
    assert n == 0
    # состояние не тронуто -- честно ждём следующего цикла, не выдумываем блок
    assert bwm._load_state() == {}


def test_events_file_rotates_at_keep_max(monkeypatch, tmp_path):
    _fresh_state_files(monkeypatch, tmp_path)
    monkeypatch.setattr(bwm, "EVENTS_KEEP_MAX", 3)
    for i in range(5):
        bwm._append_event({"i": i})
    events = bwm._load_events()
    assert len(events) == 3
    assert [e["i"] for e in events] == [2, 3, 4]


def test_format_alert_text_includes_key_fields():
    ev = {"from": "0xAAA", "to": "0xBBB", "amount": 1234567.0, "usd": 250000.0,
          "block": 555, "tx": "0xtxhash"}
    text = bwm.format_alert_text(ev)
    assert "0xAAA" in text
    assert "0xBBB" in text
    assert "555" in text
    assert "0xtxhash" in text
    assert "250,000" in text
