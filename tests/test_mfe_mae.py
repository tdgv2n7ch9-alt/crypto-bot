"""
pytest для mfe_mae.py -- MFE/MAE (владелец, ДА 2026-07-18, очередь п.6):
(1) чистая математика R-мультиплов, (2) running-обновление по живым тикам,
(3) историчный расчёт по 1m-свечам (Bybit-фетч мокается, без сети),
(4) retro_mfe_mae_for_closed() -- сквозной сценарий на синтетике.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import mfe_mae as mm


# ── _mfe_mae_r / compute_mfe_mae_from_candles ───────────────────────────────

def test_mfe_mae_r_long_favorable_and_adverse():
    mfe, mae = mm._mfe_mae_r("long", entry=100.0, sl=90.0, best_price=115.0, worst_price=95.0)
    assert mfe == 1.5   # (115-100)/10
    assert mae == 0.5   # (100-95)/10


def test_mfe_mae_r_short_favorable_and_adverse():
    mfe, mae = mm._mfe_mae_r("short", entry=100.0, sl=110.0, best_price=85.0, worst_price=105.0)
    assert mfe == 1.5   # (100-85)/10
    assert mae == 0.5   # (105-100)/10


def test_mfe_mae_r_zero_risk_returns_none():
    mfe, mae = mm._mfe_mae_r("long", entry=100.0, sl=100.0, best_price=110.0, worst_price=90.0)
    assert mfe is None and mae is None


def test_mae_never_negative_even_if_worst_price_beyond_entry_favorably():
    """Если worst_price всё равно лучше входа (сделка не разу не ушла в минус) --
    mae честно 0, не отрицательное число."""
    mfe, mae = mm._mfe_mae_r("long", entry=100.0, sl=90.0, best_price=120.0, worst_price=101.0)
    assert mae == 0.0


def test_compute_mfe_mae_from_candles_long():
    candles = [
        {"high": 102.0, "low": 99.0},
        {"high": 108.0, "low": 96.0},   # худшая точка (96) и лучше пока не видели
        {"high": 112.0, "low": 105.0},  # лучшая точка (112)
    ]
    result = mm.compute_mfe_mae_from_candles("long", entry=100.0, sl=90.0, candles=candles)
    assert result["best_price"] == 112.0
    assert result["worst_price"] == 96.0
    assert result["mfe_r"] == 1.2   # (112-100)/10
    assert result["mae_r"] == 0.4   # (100-96)/10


def test_compute_mfe_mae_from_candles_short():
    candles = [
        {"high": 101.0, "low": 98.0},
        {"high": 104.0, "low": 90.0},   # лучшая точка (90)
        {"high": 106.0, "low": 92.0},   # худшая точка (106)
    ]
    result = mm.compute_mfe_mae_from_candles("short", entry=100.0, sl=110.0, candles=candles)
    assert result["best_price"] == 90.0
    assert result["worst_price"] == 106.0
    assert result["mfe_r"] == 1.0   # (100-90)/10
    assert result["mae_r"] == 0.6   # (106-100)/10


def test_compute_mfe_mae_from_candles_empty_returns_none():
    assert mm.compute_mfe_mae_from_candles("long", 100.0, 90.0, []) is None


def test_compute_mfe_mae_from_candles_missing_direction_returns_none():
    assert mm.compute_mfe_mae_from_candles("sideways", 100.0, 90.0, [{"high": 1, "low": 1}]) is None


# ── update_running_mfe_mae ───────────────────────────────────────────────────

def test_running_update_first_tick_seeds_both():
    mfe_price, mae_price = mm.update_running_mfe_mae("long", entered_price=100.0, current_price=105.0)
    assert mfe_price == 105.0 and mae_price == 105.0


def test_running_update_long_tracks_extremes_over_multiple_ticks():
    mfe_price, mae_price = None, None
    for price in [105.0, 98.0, 112.0, 101.0]:
        mfe_price, mae_price = mm.update_running_mfe_mae("long", 100.0, price, mfe_price, mae_price)
    assert mfe_price == 112.0  # лучшая цена за всю историю тиков
    assert mae_price == 98.0   # худшая цена за всю историю тиков


def test_running_update_short_tracks_extremes_inverted():
    mfe_price, mae_price = None, None
    for price in [95.0, 103.0, 90.0, 98.0]:
        mfe_price, mae_price = mm.update_running_mfe_mae("short", 100.0, price, mfe_price, mae_price)
    assert mfe_price == 90.0   # лучшая (самая низкая) цена для шорта
    assert mae_price == 103.0  # худшая (самая высокая) цена для шорта


def test_running_update_no_op_without_entered_price():
    mfe_price, mae_price = mm.update_running_mfe_mae("long", None, 105.0, prev_mfe_price=1.0, prev_mae_price=2.0)
    assert mfe_price == 1.0 and mae_price == 2.0  # не тронуто -- ещё не вошли


def test_running_mfe_mae_r_converts_prices_to_r_multiples():
    mfe_r, mae_r = mm.running_mfe_mae_r("long", entered_price=100.0, sl=90.0, mfe_price=115.0, mae_price=95.0)
    assert mfe_r == 1.5 and mae_r == 0.5


def test_running_mfe_mae_r_none_when_no_ticks_yet():
    mfe_r, mae_r = mm.running_mfe_mae_r("long", entered_price=100.0, sl=90.0, mfe_price=None, mae_price=None)
    assert mfe_r is None and mae_r is None


# ── fetch_bybit_1m_candles_sync -- сеть мокается ────────────────────────────

def test_fetch_bybit_candles_single_page(monkeypatch):
    calls = []

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            # Bybit отдаёт новые бары первыми (порядок ts убывающий)
            return {"result": {"list": [
                ["1000120000", "102", "103", "101", "102.5", "10"],
                ["1000060000", "101", "102", "100", "101.5", "10"],
                ["1000000000", "100", "101", "99", "100.5", "10"],
            ]}}

    def fake_get(url, params=None, timeout=None):
        calls.append(params)
        return _FakeResp()

    monkeypatch.setattr(mm.requests, "get", fake_get)
    # end_ts выровнен на последнюю свечу первой страницы -- следующий cur_start_ms
    # (last_ts+60s) перешагивает end_ms, вторая страница не запрашивается.
    candles = mm.fetch_bybit_1m_candles_sync("BTC", start_ts=1_000_000, end_ts=1_000_120)
    assert len(calls) == 1
    assert len(candles) == 3
    assert candles[0]["ts"] == 1_000_000.0  # хронологический порядок после сортировки
    assert candles[-1]["high"] == 103.0


def test_fetch_bybit_candles_paginates_beyond_one_page(monkeypatch):
    """Вторая страница запрашивается, если первая вернула ровно лимит (здесь --
    имитируем через возврат непустого списка на 1-м вызове и пустого на 2-м,
    сигнализируя конец данных без реального 1000-бара лимита в тесте)."""
    call_n = {"n": 0}

    class _FakeResp:
        def __init__(self, rows):
            self._rows = rows

        def raise_for_status(self):
            pass

        def json(self):
            return {"result": {"list": self._rows}}

    def fake_get(url, params=None, timeout=None):
        call_n["n"] += 1
        if call_n["n"] == 1:
            return _FakeResp([["1000060000", "1", "2", "0.5", "1.5", "1"],
                               ["1000000000", "1", "1.5", "0.9", "1.1", "1"]])
        return _FakeResp([])  # вторая страница пустая -- конец данных

    monkeypatch.setattr(mm.requests, "get", fake_get)
    candles = mm.fetch_bybit_1m_candles_sync("ETH", start_ts=1_000_000, end_ts=1_000_300)
    assert call_n["n"] == 2
    assert len(candles) == 2


def test_fetch_bybit_candles_network_error_returns_empty(monkeypatch):
    def boom(*a, **kw):
        raise ConnectionError("simulated")

    monkeypatch.setattr(mm.requests, "get", boom)
    candles = mm.fetch_bybit_1m_candles_sync("BTC", start_ts=1_000_000, end_ts=1_000_200)
    assert candles == []


# ── retro_mfe_mae_for_closed -- сквозной сценарий на синтетике ─────────────

def test_retro_mfe_mae_for_closed_end_to_end():
    shadow_records = [
        {"symbol": "BTC", "direction": "long", "ts": 1_000_000.0, "sl": 90.0,
         "promoted_live": True, "live_journal_id": 1},
    ]
    journal_records = {
        1: {"symbol": "BTC", "direction": "long", "outcome": "TP1_HIT",
            "entered_ts": 1_000_050.0, "entered_price": 100.0,
            "outcome_ts": 1_000_300.0},
    }

    def fake_fetch(symbol, start_ts, end_ts):
        assert symbol == "BTC"
        assert start_ts == 1_000_050.0 and end_ts == 1_000_300.0
        return [{"high": 112.0, "low": 96.0}]

    results = mm.retro_mfe_mae_for_closed(shadow_records, journal_records, fetch_candles_fn=fake_fetch)
    assert len(results) == 1
    r = results[0]
    assert r["symbol"] == "BTC" and r["outcome"] == "TP1_HIT"
    assert r["mfe_r"] == 1.2 and r["mae_r"] == 0.4


def test_retro_mfe_mae_skips_non_promoted():
    shadow_records = [{"symbol": "BTC", "direction": "long", "ts": 1.0, "sl": 90.0,
                        "promoted_live": False, "live_journal_id": 1}]
    journal_records = {1: {"outcome": "TP1_HIT", "entered_ts": 1.0, "entered_price": 100.0,
                            "outcome_ts": 2.0, "direction": "long", "symbol": "BTC"}}
    results = mm.retro_mfe_mae_for_closed(shadow_records, journal_records, fetch_candles_fn=lambda *a: [])
    assert results == []


def test_retro_mfe_mae_skips_unmatched():
    shadow_records = [{"symbol": "BTC", "direction": "long", "ts": 1.0, "sl": 90.0,
                        "promoted_live": True, "live_journal_id": 999}]
    results = mm.retro_mfe_mae_for_closed(shadow_records, {}, fetch_candles_fn=lambda *a: [])
    assert results == []


def test_retro_mfe_mae_skips_auxiliary_type_record():
    """DEDUP_MATCHING_AUDIT.md фикс (владелец, 2026-07-21) -- явный фильтр по
    type, даже если гипотетически auxiliary-запись содержала бы sl/direction
    (сейчас случайно защищено их отсутствием, но фильтр не должен полагаться
    только на побочный эффект)."""
    shadow_records = [
        {"symbol": "BTC", "direction": "long", "ts": 1_000_000.0, "sl": 90.0,
         "type": "auto_onchain_shadow", "promoted_live": True, "live_journal_id": 1},
    ]
    journal_records = {
        1: {"symbol": "BTC", "direction": "long", "outcome": "TP1_HIT",
            "entered_ts": 1_000_050.0, "entered_price": 100.0,
            "outcome_ts": 1_000_300.0},
    }
    results = mm.retro_mfe_mae_for_closed(shadow_records, journal_records,
                                           fetch_candles_fn=lambda *a: [{"high": 112.0, "low": 96.0}])
    assert results == []
