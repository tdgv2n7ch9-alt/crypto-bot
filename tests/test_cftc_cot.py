"""
pytest для cftc_cot.py (владелец, ночная очередь 2026-07-22/23, CFTC_COT_
INTEGRATION_PLAN.md -- собран пакет, флаг OFF по умолчанию). Покрывает:
(1) флаг OFF -- гарантированный no-op БЕЗ сетевых вызовов; (2) флаг ON --
fetch/парсинг снимка; (3) refresh-гейт (раз в REFRESH_INTERVAL_SEC, не чаще);
(4) get_shadow_snapshot() -- возраст снимка в днях; (5) персистентность
состояния (tmp+os.replace); (6) сетевые ошибки не поднимаются наружу.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cftc_cot as cc


def _run(coro):
    return asyncio.run(coro)


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(cc, "STATE_FILE", str(tmp_path / "cftc_cot_btc.json"))


# ── Флаг OFF (дефолт) -- гарантированный no-op ──────────────────────────

def test_flag_off_fetch_never_calls_network(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(cc, "CFTC_COT_ENABLED", False)

    def boom(*a, **kw):
        raise AssertionError("сеть не должна вызываться при флаге OFF")

    monkeypatch.setattr(cc.requests, "get", boom)
    assert cc.fetch_latest_report_sync() == {}


def test_flag_off_refresh_is_noop_does_not_touch_state_file(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(cc, "CFTC_COT_ENABLED", False)
    result = cc.refresh_if_stale_sync(now=1000.0)
    assert result == {}
    assert not os.path.exists(cc.STATE_FILE)


def test_flag_off_get_shadow_snapshot_returns_disabled(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(cc, "CFTC_COT_ENABLED", False)
    assert cc.get_shadow_snapshot() == {"enabled": False}


# ── Флаг ON -- fetch/парсинг ─────────────────────────────────────────────

def test_fetch_latest_report_parses_first_row(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(cc, "CFTC_COT_ENABLED", True)

    captured = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"report_date_as_yyyy_mm_dd": "2026-07-14",
                      "lev_money_positions_long": "4015",
                      "lev_money_positions_short": "11506",
                      "open_interest_all": "19385"}]

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _Resp()

    monkeypatch.setattr(cc.requests, "get", fake_get)
    report = cc.fetch_latest_report_sync()
    assert report["report_date_as_yyyy_mm_dd"] == "2026-07-14"
    assert captured["url"] == cc.CFTC_DATASET_URL
    assert "133741" in captured["params"]["$where"]


def test_fetch_latest_report_empty_rows_returns_empty_dict(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(cc, "CFTC_COT_ENABLED", True)

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return []

    monkeypatch.setattr(cc.requests, "get", lambda *a, **kw: _Resp())
    assert cc.fetch_latest_report_sync() == {}


def test_fetch_latest_report_network_error_returns_empty_dict_not_raise(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(cc, "CFTC_COT_ENABLED", True)

    def boom(*a, **kw):
        raise ConnectionError("нет сети")

    monkeypatch.setattr(cc.requests, "get", boom)
    assert cc.fetch_latest_report_sync() == {}  # не должно поднять исключение


def test_compute_snapshot_computes_net_leveraged_money():
    report = {"report_date_as_yyyy_mm_dd": "2026-07-14",
               "lev_money_positions_long": "4015",
               "lev_money_positions_short": "11506",
               "open_interest_all": "19385"}
    snap = cc._compute_snapshot(report)
    assert snap["lev_money_long"] == 4015.0
    assert snap["lev_money_short"] == 11506.0
    assert snap["lev_money_net"] == 4015.0 - 11506.0
    assert snap["report_date"] == "2026-07-14"


def test_compute_snapshot_honest_none_on_missing_fields():
    snap = cc._compute_snapshot({"report_date_as_yyyy_mm_dd": "2026-07-14"})
    assert snap["lev_money_long"] is None
    assert snap["lev_money_net"] is None


def test_compute_snapshot_empty_report_returns_empty_dict():
    assert cc._compute_snapshot({}) == {}


# ── refresh-гейт (раз в REFRESH_INTERVAL_SEC) ────────────────────────────

def test_refresh_respects_interval_gate_no_fetch_within_window(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(cc, "CFTC_COT_ENABLED", True)
    cc._save_state({"last_check_ts": 1000.0})

    def boom(*a, **kw):
        raise AssertionError("не должно дёргать сеть внутри REFRESH_INTERVAL_SEC")

    monkeypatch.setattr(cc.requests, "get", boom)
    result = cc.refresh_if_stale_sync(now=1000.0 + cc.REFRESH_INTERVAL_SEC - 1)
    assert result == {"last_check_ts": 1000.0}


def test_refresh_fetches_after_interval_elapsed_and_saves_new_snapshot(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(cc, "CFTC_COT_ENABLED", True)
    monkeypatch.setattr(cc, "fetch_latest_report_sync", lambda: {
        "report_date_as_yyyy_mm_dd": "2026-07-14",
        "lev_money_positions_long": "4015",
        "lev_money_positions_short": "11506",
    })

    now = cc.REFRESH_INTERVAL_SEC + 1  # last_check_ts по умолчанию 0 -- гейт должен пропустить
    result = cc.refresh_if_stale_sync(now=now)
    assert result["snapshot"]["report_date"] == "2026-07-14"
    assert result["snapshot_saved_ts"] == now
    saved = cc._load_state()
    assert saved["snapshot"]["report_date"] == "2026-07-14"


def test_refresh_does_not_overwrite_snapshot_if_report_date_unchanged(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(cc, "CFTC_COT_ENABLED", True)
    cc._save_state({"last_check_ts": 1000.0,
                     "snapshot": {"report_date": "2026-07-14"},
                     "snapshot_saved_ts": 1000.0})
    monkeypatch.setattr(cc, "fetch_latest_report_sync", lambda: {
        "report_date_as_yyyy_mm_dd": "2026-07-14",  # тот же отчёт, ничего нового
        "lev_money_positions_long": "1", "lev_money_positions_short": "1",
    })

    result = cc.refresh_if_stale_sync(now=1000.0 + cc.REFRESH_INTERVAL_SEC + 1)
    assert result["snapshot_saved_ts"] == 1000.0  # НЕ обновилось


# ── get_shadow_snapshot() -- возраст снимка ──────────────────────────────

def test_get_shadow_snapshot_no_data_yet(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(cc, "CFTC_COT_ENABLED", True)
    assert cc.get_shadow_snapshot() == {"enabled": True, "available": False}


def test_get_shadow_snapshot_computes_age_days(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(cc, "CFTC_COT_ENABLED", True)
    cc._save_state({"snapshot": {"report_date": "2026-07-14", "lev_money_net": -7491.0},
                     "snapshot_saved_ts": 1000.0})
    snap = cc.get_shadow_snapshot(now=1000.0 + 3 * 86400)
    assert snap["enabled"] is True
    assert snap["available"] is True
    assert snap["age_days"] == 3.0
    assert snap["report_date"] == "2026-07-14"
    assert snap["lev_money_net"] == -7491.0


# ── async джоб-обёртка ────────────────────────────────────────────────────

def test_refresh_cftc_cot_async_uses_injected_executor(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(cc, "refresh_if_stale_sync", lambda: {"ok": True})

    calls = []

    async def fake_executor(fn, *a):
        calls.append(fn)
        return fn(*a)

    result = _run(cc.refresh_cftc_cot_async(run_in_executor_fn=fake_executor))
    assert result == {"ok": True}
    assert calls == [cc.refresh_if_stale_sync]
