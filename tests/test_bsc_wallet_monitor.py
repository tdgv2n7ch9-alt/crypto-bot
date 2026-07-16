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
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bsc_wallet_monitor as bwm


@pytest.fixture(autouse=True)
def _fixed_rpc_providers(monkeypatch):
    """Владелец, 2026-07-16: RPC_PROVIDERS теперь строится из QUICKNODE_BSC_URL
    в окружении процесса (_build_rpc_providers()) -- тесты не должны зависеть
    от того, задана ли эта переменная на машине, где они запускаются. Фиксируем
    предсказуемый двухпровайдерный список (primary+fallback), тот же паттерн,
    что был раньше (1rpc.io+blastapi), но с примерными URL -- сами тесты
    проверяют fallback-логику по позиции в списке, не по конкретным доменам."""
    monkeypatch.setattr(bwm, "RPC_PROVIDERS", [
        {"url": "https://primary.test", "max_range": 50},
        {"url": "https://bsc-mainnet.public.blastapi.io", "max_range": 10},
    ])


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
    logs, incomplete, last_block = bwm.get_transfer_logs(100, 100)
    assert len(logs) == 1
    assert incomplete is False


def test_get_transfer_logs_skips_range_honestly_when_all_providers_fail(monkeypatch, caplog):
    def fake_rpc(url, method, params, timeout=15):
        if method != "eth_getLogs":
            return {"result": "0x0"}
        return {"error": {"code": -32005, "message": "limit exceeded"}}

    monkeypatch.setattr(bwm, "_rpc_call", fake_rpc)
    logs, incomplete, last_block = bwm.get_transfer_logs(100, 100)
    assert logs == []  # честный пустой результат, не выдуманные данные
    assert incomplete is True  # оба провайдера мертвы -- диапазон не покрыт


def test_get_transfer_logs_marks_provider_dead_after_rate_limit_not_retried_every_chunk(monkeypatch):
    """Регресс-замок на критический баг 2026-07-15: провайдер, отказавший с
    rate-limit-классом ошибки, НЕ должен пробоваться заново на каждом
    следующем чанке -- иначе 27+ идентичных отказов подряд блокируют event loop."""
    call_log = []

    def fake_rpc(url, method, params, timeout=15):
        if method != "eth_getLogs":
            return {"result": "0x0"}
        call_log.append(url)
        if url == bwm.RPC_PROVIDERS[0]["url"]:
            return {"error": {"code": -32001, "message": "usage limit"}}
        return {"result": []}

    monkeypatch.setattr(bwm, "_rpc_call", fake_rpc)
    # диапазон на несколько чанков провайдера 0 (max_range=50)
    bwm.get_transfer_logs(100, 249)
    primary_calls = [c for c in call_log if c == bwm.RPC_PROVIDERS[0]["url"]]
    assert len(primary_calls) == 1  # мёртвый провайдер пробуется РОВНО один раз за вызов


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
    monkeypatch.setattr(bwm, "QUICKNODE_CALL_LOG_FILE", str(tmp_path / "quicknode_call_log.json"))
    monkeypatch.setattr(bwm, "_budget_throttled", False)


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
                         lambda a, b: ([_transfer_log(bwm.AKE_WATCHED_WALLETS[0], "0xdead", 100, block=1000)], False, b))
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
    monkeypatch.setattr(bwm, "get_transfer_logs", lambda a, b: ([known_log], False, b))
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


# --- Критический регресс 2026-07-15: run_in_executor, bounded catch-up, honest notify ---

def test_check_ake_wallets_uses_run_in_executor_for_blocking_calls(monkeypatch, tmp_path):
    """Владелец: блокирующие сетевые вызовы обязаны идти через run_in_executor,
    не выполняться синхронно внутри корутины (это и было причиной регресса --
    шторм 1rpc.io ошибок блокировал весь event loop)."""
    _fresh_state_files(monkeypatch, tmp_path)
    bwm._save_state({"last_scanned_block": 999})
    executor_calls = []

    def _mk(name, real):
        real.__name__ = name
        return real

    async def fake_run_in_executor(fn, *args):
        executor_calls.append(fn.__name__)
        return fn(*args)

    monkeypatch.setattr(bwm, "get_latest_block",
                         _mk("get_latest_block", lambda: (1001, bwm.RPC_PROVIDERS[0])))
    monkeypatch.setattr(bwm, "get_transfer_logs",
                         _mk("get_transfer_logs", lambda a, b: ([], False, b)))
    monkeypatch.setattr(bwm, "get_ake_price_usd",
                         _mk("get_ake_price_usd", lambda: 0.001))

    _run(bwm.check_ake_wallets(bot=None, send_system_fn=lambda *a, **k: None,
                                run_in_executor_fn=fake_run_in_executor))
    assert "get_latest_block" in executor_calls
    assert "get_transfer_logs" in executor_calls
    assert "get_ake_price_usd" in executor_calls


def test_check_ake_wallets_bounds_catchup_to_max_blocks_per_tick(monkeypatch, tmp_path):
    """Владелец: "не пытаться нагнать весь бэклог за один тик" -- ловит диапазон,
    реально запрошенный у get_transfer_logs, и убеждается, что он <= MAX_BLOCKS_PER_TICK,
    даже если backlog (latest - last_scanned) намного больше."""
    _fresh_state_files(monkeypatch, tmp_path)
    huge_backlog_last_scanned = 1000
    bwm._save_state({"last_scanned_block": huge_backlog_last_scanned})
    latest = huge_backlog_last_scanned + bwm.MAX_BLOCKS_PER_TICK * 10  # backlog в 10x больше лимита
    monkeypatch.setattr(bwm, "get_latest_block", lambda: (latest, bwm.RPC_PROVIDERS[0]))

    captured_range = {}
    def fake_transfer_logs(a, b):
        captured_range["from"] = a
        captured_range["to"] = b
        return [], False, b
    monkeypatch.setattr(bwm, "get_transfer_logs", fake_transfer_logs)
    monkeypatch.setattr(bwm, "get_ake_price_usd", lambda: 0.001)

    _run(bwm.check_ake_wallets(bot=None, send_system_fn=lambda *a, **k: None))
    requested_range = captured_range["to"] - captured_range["from"] + 1
    assert requested_range <= bwm.MAX_BLOCKS_PER_TICK
    state = bwm._load_state()
    assert state["last_scanned_block"] < latest  # НЕ прыгнули сразу к latest


def test_check_ake_wallets_incomplete_advances_state_only_to_processed_boundary(monkeypatch, tmp_path):
    """partial success (несколько чанков прошли, потом все провайдеры легли) --
    state продвигается ровно до реально обработанной границы, не теряя и не
    задваивая диапазон на следующем тике."""
    _fresh_state_files(monkeypatch, tmp_path)
    bwm._save_state({"last_scanned_block": 999})
    monkeypatch.setattr(bwm, "get_latest_block", lambda: (1200, bwm.RPC_PROVIDERS[0]))
    monkeypatch.setattr(bwm, "get_transfer_logs", lambda a, b: ([], True, 1050))  # incomplete=True, дошли до 1050
    monkeypatch.setattr(bwm, "get_ake_price_usd", lambda: 0.001)

    sent = []
    async def fake_send(bot, text, critical=False):
        sent.append((text, critical))

    _run(bwm.check_ake_wallets(bot=None, send_system_fn=fake_send))
    state = bwm._load_state()
    assert state["last_scanned_block"] == 1050  # не 1200 (latest) и не 999 (без прогресса)
    assert any("недоступен" in t or "отказали" in t for t, c in sent)
    assert all(c is True for t, c in sent)  # честный down-notify тоже критический


def test_check_ake_wallets_down_notify_not_spammed_every_tick(monkeypatch, tmp_path):
    """Владелец: "уведомлением раз в 15 мин (не молчать и не копить)" -- НЕ на
    каждый минутный тик."""
    _fresh_state_files(monkeypatch, tmp_path)
    bwm._save_state({"last_scanned_block": 999, "last_source_down_notify_ts": time.time()})
    monkeypatch.setattr(bwm, "get_latest_block", lambda: (1200, bwm.RPC_PROVIDERS[0]))
    monkeypatch.setattr(bwm, "get_transfer_logs", lambda a, b: ([], True, 999))
    monkeypatch.setattr(bwm, "get_ake_price_usd", lambda: 0.001)

    sent = []
    async def fake_send(bot, text, critical=False):
        sent.append((text, critical))

    _run(bwm.check_ake_wallets(bot=None, send_system_fn=fake_send))
    assert sent == []  # только что уведомляли -- в пределах интервала молчим


# ── _build_rpc_providers() (владелец, 2026-07-16: QuickNode primary) ───────

def test_build_rpc_providers_uses_quicknode_as_primary_when_set(monkeypatch):
    monkeypatch.setenv("QUICKNODE_BSC_URL", "https://my-endpoint.quiknode.pro/abc123/")
    providers = bwm._build_rpc_providers()
    assert providers[0]["url"] == "https://my-endpoint.quiknode.pro/abc123/"
    # владелец, живая находка 2026-07-16: QuickNode discover-план -- код -32615
    # "limited to a 5 range" на КАЖДОМ чанке при max_range=50 -- 5 подтверждён живьём.
    assert providers[0]["max_range"] == 5
    assert providers[1]["url"] == "https://bsc-mainnet.public.blastapi.io"


def test_build_rpc_providers_falls_back_to_blastapi_only_when_unset(monkeypatch):
    monkeypatch.delenv("QUICKNODE_BSC_URL", raising=False)
    providers = bwm._build_rpc_providers()
    assert len(providers) == 1
    assert providers[0]["url"] == "https://bsc-mainnet.public.blastapi.io"


def test_max_blocks_per_tick_is_50():
    """Владелец, 2026-07-16: возвращено на 50 после перехода на QuickNode."""
    assert bwm.MAX_BLOCKS_PER_TICK == 50


# ── бюджет-контроль QuickNode credits (владелец, 2026-07-16) ───────────────

def test_record_quicknode_call_only_counts_quicknode_url(monkeypatch, tmp_path):
    _fresh_state_files(monkeypatch, tmp_path)
    monkeypatch.setenv("QUICKNODE_BSC_URL", "https://qn.test/abc")

    def fake_post(url, json, timeout):
        class _R:
            def json(_self):
                return {"result": "0x1"}
        return _R()

    monkeypatch.setattr(bwm.requests, "post", fake_post)
    bwm._rpc_call("https://qn.test/abc", "eth_blockNumber", [])
    bwm._rpc_call("https://bsc-mainnet.public.blastapi.io", "eth_blockNumber", [])  # не считается

    log_data = bwm._load_call_log()
    assert log_data["calls"] == {"eth_blockNumber": 1}


def test_quicknode_budget_report_not_ok_before_min_sample(monkeypatch, tmp_path):
    _fresh_state_files(monkeypatch, tmp_path)
    now = time.time()
    bwm._atomic_write_json(bwm.QUICKNODE_CALL_LOG_FILE, {"process_start_ts": now, "calls": {"eth_getLogs": 5}})
    report = bwm.quicknode_budget_report(now_ts=now + 60)  # 60с < _BUDGET_MIN_SAMPLE_SEC
    assert report["ok"] is False


def test_quicknode_budget_report_projects_monthly_credits(monkeypatch, tmp_path):
    _fresh_state_files(monkeypatch, tmp_path)
    now = time.time()
    start = now - 3600  # час назад
    bwm._atomic_write_json(bwm.QUICKNODE_CALL_LOG_FILE, {
        "process_start_ts": start,
        "calls": {"eth_blockNumber": 60, "eth_getLogs": 120},
    })
    report = bwm.quicknode_budget_report(now_ts=now)
    assert report["ok"] is True
    total_calls = 180
    expected_credits_used = total_calls * bwm.QUICKNODE_CREDITS_PER_CALL
    assert report["credits_used_since_restart"] == expected_credits_used
    expected_per_day = expected_credits_used / 3600 * 86400
    assert abs(report["credits_per_day_projected"] - expected_per_day) < 1
    assert abs(report["credits_per_month_projected"] - expected_per_day * 30) < 30


def test_quicknode_budget_report_flags_over_budget(monkeypatch, tmp_path):
    _fresh_state_files(monkeypatch, tmp_path)
    now = time.time()
    start = now - 3600
    # Огромный объём вызовов за час -> прогноз/мес заведомо выше 8M.
    bwm._atomic_write_json(bwm.QUICKNODE_CALL_LOG_FILE, {
        "process_start_ts": start,
        "calls": {"eth_getLogs": 100_000},
    })
    report = bwm.quicknode_budget_report(now_ts=now)
    assert report["ok"] is True
    assert report["over_budget"] is True


def test_quicknode_budget_report_under_budget_not_flagged(monkeypatch, tmp_path):
    _fresh_state_files(monkeypatch, tmp_path)
    now = time.time()
    start = now - 3600
    bwm._atomic_write_json(bwm.QUICKNODE_CALL_LOG_FILE, {
        "process_start_ts": start,
        "calls": {"eth_getLogs": 10},
    })
    report = bwm.quicknode_budget_report(now_ts=now)
    assert report["ok"] is True
    assert report["over_budget"] is False


def test_effective_max_blocks_per_tick_throttles_when_over_budget(monkeypatch, tmp_path):
    _fresh_state_files(monkeypatch, tmp_path)
    now = time.time()
    start = now - 3600
    bwm._atomic_write_json(bwm.QUICKNODE_CALL_LOG_FILE, {
        "process_start_ts": start,
        "calls": {"eth_getLogs": 100_000},
    })
    monkeypatch.setattr(bwm.time, "time", lambda: now)
    assert bwm._effective_max_blocks_per_tick() == bwm.REDUCED_MAX_BLOCKS_PER_TICK
    assert bwm._budget_throttled is True


def test_effective_max_blocks_per_tick_stays_throttled_once_triggered(monkeypatch, tmp_path):
    """Владелец: сработавшее сокращение держится до рестарта, не мигает
    туда-обратно на пограничных значениях прогноза каждый тик."""
    _fresh_state_files(monkeypatch, tmp_path)
    monkeypatch.setattr(bwm, "_budget_throttled", True)
    assert bwm._effective_max_blocks_per_tick() == bwm.REDUCED_MAX_BLOCKS_PER_TICK


def test_effective_max_blocks_per_tick_normal_when_under_budget(monkeypatch, tmp_path):
    _fresh_state_files(monkeypatch, tmp_path)
    now = time.time()
    start = now - 3600
    bwm._atomic_write_json(bwm.QUICKNODE_CALL_LOG_FILE, {
        "process_start_ts": start,
        "calls": {"eth_getLogs": 10},
    })
    monkeypatch.setattr(bwm.time, "time", lambda: now)
    assert bwm._effective_max_blocks_per_tick() == bwm.MAX_BLOCKS_PER_TICK
    assert bwm._budget_throttled is False
