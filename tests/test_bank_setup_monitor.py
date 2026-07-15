"""
pytest для bank_setup_monitor.py (владелец, СРОЧНЫЙ наряд вне очереди, 2026-07-15):
условный SHORT-сетап BANKUSDT -- CHoCH -> ретест -> инвалидация, критические алерты.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bank_setup_monitor as bsm


def _run(coro):
    return asyncio.run(coro)


def _candle(ts, o, h, l, c):
    return {"ts": ts, "o": o, "h": h, "l": l, "c": c}


def _fresh_state_file(monkeypatch, tmp_path):
    monkeypatch.setattr(bsm, "STATE_FILE", str(tmp_path / "bank_state.json"))


def test_first_run_only_bookmarks(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    candles = [_candle(1000, 0.05, 0.051, 0.049, 0.0505)]
    monkeypatch.setattr(bsm, "get_klines", lambda interval, limit=5, dead_sources=None: (candles, "bybit"))

    sent = []
    async def fake_send(bot, text, critical=False):
        sent.append((text, critical))

    result = _run(bsm.check_bank_setup(bot=None, send_system_fn=fake_send))
    assert result == []
    assert sent == []
    state = bsm._load_state()
    assert state["last_closed_15m_ts"] == 1000


def test_choch_fires_on_close_below_hl(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    bsm._save_state({**bsm._default_state(), "last_closed_15m_ts": 1000})

    def fake_klines(interval, limit=5, dead_sources=None):
        if interval == "15":
            return [_candle(2000, 0.0507, 0.0508, 0.0498, 0.0498)], "bybit"  # close < 0.0505
        return [], "bybit"

    monkeypatch.setattr(bsm, "get_klines", fake_klines)

    sent = []
    async def fake_send(bot, text, critical=False):
        sent.append((text, critical))

    result = _run(bsm.check_bank_setup(bot=None, send_system_fn=fake_send))
    assert result == ["choch"]
    assert len(sent) == 1
    assert sent[0][1] is True  # critical=True -- оба канала
    assert "0.0505" in sent[0][0]
    state = bsm._load_state()
    assert state["stage"] == bsm.STAGE_WATCHING_RETEST
    assert state["broken_level"] == 0.0505


def test_retest_fires_after_choch(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    bsm._save_state({**bsm._default_state(), "stage": bsm.STAGE_WATCHING_RETEST,
                      "broken_level": 0.0505, "last_closed_15m_ts": 2000})

    def fake_klines(interval, limit=5, dead_sources=None):
        if interval == "15":
            return [_candle(3000, 0.0498, 0.0507, 0.0497, 0.0503)], "bybit"  # в пределах ±0.5% от 0.0505
        return [], "bybit"

    monkeypatch.setattr(bsm, "get_klines", fake_klines)

    sent = []
    async def fake_send(bot, text, critical=False):
        sent.append((text, critical))

    result = _run(bsm.check_bank_setup(bot=None, send_system_fn=fake_send))
    assert result == ["retest"]
    assert "SL 0.0553" in sent[0][0]
    assert "0.044-0.046" in sent[0][0]
    state = bsm._load_state()
    assert state["stage"] == bsm.STAGE_DONE


def test_retest_does_not_fire_outside_tolerance(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    bsm._save_state({**bsm._default_state(), "stage": bsm.STAGE_WATCHING_RETEST,
                      "broken_level": 0.0505, "last_closed_15m_ts": 2000})

    def fake_klines(interval, limit=5, dead_sources=None):
        if interval == "15":
            return [_candle(3000, 0.048, 0.049, 0.047, 0.048)], "bybit"  # далеко от 0.0505
        return [], "bybit"

    monkeypatch.setattr(bsm, "get_klines", fake_klines)
    result = _run(bsm.check_bank_setup(bot=None, send_system_fn=lambda *a, **k: None))
    assert result == []
    state = bsm._load_state()
    assert state["stage"] == bsm.STAGE_WATCHING_RETEST  # осталось ждать


def test_invalidation_fires_on_1h_close_above(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    bsm._save_state({**bsm._default_state(), "last_closed_15m_ts": 1000})

    def fake_klines(interval, limit=5, dead_sources=None):
        if interval == "15":
            return [_candle(2000, 0.0508, 0.0509, 0.0507, 0.0508)], "bybit"  # выше HL, не триггерит CHoCH
        return [_candle(5000, 0.054, 0.0555, 0.053, 0.0554)], "bybit"  # 1H close > 0.0553

    monkeypatch.setattr(bsm, "get_klines", fake_klines)

    sent = []
    async def fake_send(bot, text, critical=False):
        sent.append((text, critical))

    result = _run(bsm.check_bank_setup(bot=None, send_system_fn=fake_send))
    assert "invalidation" in result
    assert any("отменён" in t for t, c in sent)
    state = bsm._load_state()
    assert state["stage"] == bsm.STAGE_INVALIDATED


def test_terminal_stage_stops_monitor(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    bsm._save_state({**bsm._default_state(), "stage": bsm.STAGE_INVALIDATED})

    called = {"n": 0}
    def fake_klines(interval, limit=5, dead_sources=None):
        called["n"] += 1
        return [], "bybit"
    monkeypatch.setattr(bsm, "get_klines", fake_klines)

    result = _run(bsm.check_bank_setup(bot=None, send_system_fn=lambda *a, **k: None))
    assert result == []
    assert called["n"] == 0  # даже klines не запрашивались -- монитор молча стоит


def test_hl_recompute_on_new_high(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    initial_state = bsm._default_state()
    initial_state["last_closed_15m_ts"] = 1000
    # откат перед новым хаем НЕ должен опускаться ниже текущего hl_level (0.0505),
    # иначе пересчитанный уровень окажется ниже прежнего и обновление корректно не сработает
    initial_state["candles_15m"] = [_candle(1000, 0.0508, 0.0512, 0.0507, 0.0508)]
    initial_state["highest_high_since_hl"] = 0.0505
    bsm._save_state(initial_state)

    def fake_klines(interval, limit=5, dead_sources=None):
        if interval == "15":
            # новый хай выше предыдущего диапазона -- HL должен пересчитаться
            return [_candle(2000, 0.0509, 0.0520, 0.0507, 0.0518)], "bybit"
        return [], "bybit"

    monkeypatch.setattr(bsm, "get_klines", fake_klines)

    sent = []
    async def fake_send(bot, text, critical=False):
        sent.append((text, critical))

    result = _run(bsm.check_bank_setup(bot=None, send_system_fn=fake_send))
    assert "hl_update" in result
    state = bsm._load_state()
    assert state["hl_level"] > 0.0505  # уровень поднялся


def test_recompute_hl_helper_finds_lowest_low_before_new_high():
    history = [
        {"ts": 100, "l": 0.05, "h": 0.051},
        {"ts": 200, "l": 0.048, "h": 0.0505},  # самый низкий откат
        {"ts": 300, "l": 0.049, "h": 0.052},   # новый хай здесь
    ]
    new_hl = bsm._recompute_hl_after_new_high(history, new_high_ts=300, old_hl_level=0.0505)
    assert new_hl == 0.048


# --- Распространение фикса #240: run_in_executor, dead-source tracking, honest notify ---

def test_get_klines_dead_source_not_retried_within_tick(monkeypatch):
    """Владелец: bybit, отказавший ОДИН раз в этом тике, не должен снова
    тратить сетевой таймаут-бюджет на следующем вызове get_klines В ТОМ ЖЕ
    ТИКЕ (тот же принцип, что dead_providers в bsc_wallet_monitor, #240)."""
    calls = {"bybit": 0, "bingx": 0}

    def fake_bybit(interval, limit=5):
        calls["bybit"] += 1
        raise RuntimeError("bybit down")

    def fake_bingx(interval, limit=5):
        calls["bingx"] += 1
        return [], "bingx"

    monkeypatch.setattr(bsm, "_fetch_klines_bybit", fake_bybit)
    monkeypatch.setattr(bsm, "_fetch_klines_bingx", fake_bingx)

    dead_sources = set()
    bsm.get_klines("15", 5, dead_sources)
    bsm.get_klines("60", 3, dead_sources)  # второй вызов -- ТОТ ЖЕ тик
    assert calls["bybit"] == 1  # НЕ 2 -- bybit помечен мёртвым после первого отказа
    assert calls["bingx"] == 2


def test_get_klines_raises_when_all_sources_dead(monkeypatch):
    monkeypatch.setattr(bsm, "_fetch_klines_bybit", lambda i, l=5: (_ for _ in ()).throw(RuntimeError("bybit down")))
    monkeypatch.setattr(bsm, "_fetch_klines_bingx", lambda i, l=5: (_ for _ in ()).throw(RuntimeError("bingx down")))
    try:
        bsm.get_klines("15", 5, set())
        assert False, "должен был поднять исключение"
    except RuntimeError as e:
        assert "все источники" in str(e)


def test_check_bank_setup_uses_run_in_executor(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    bsm._save_state({**bsm._default_state(), "last_closed_15m_ts": 1000})

    executor_calls = []

    async def fake_run_in_executor(fn, *args):
        executor_calls.append(fn.__name__)
        return fn(*args)

    def _mk(name, real):
        real.__name__ = name
        return real

    monkeypatch.setattr(bsm, "get_klines",
                         _mk("get_klines", lambda interval, limit=5, dead_sources=None: ([], "bybit")))

    _run(bsm.check_bank_setup(bot=None, send_system_fn=lambda *a, **k: None,
                               run_in_executor_fn=fake_run_in_executor))
    assert executor_calls.count("get_klines") >= 1


def test_check_bank_setup_honest_notify_on_total_source_failure(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    bsm._save_state({**bsm._default_state(), "last_closed_15m_ts": 1000})

    def fake_klines(interval, limit=5, dead_sources=None):
        raise RuntimeError("все источники недоступны")
    monkeypatch.setattr(bsm, "get_klines", fake_klines)

    sent = []
    async def fake_send(bot, text, critical=False):
        sent.append((text, critical))

    _run(bsm.check_bank_setup(bot=None, send_system_fn=fake_send))
    assert len(sent) == 1
    assert sent[0][1] is True
    assert "источники свечей" in sent[0][0]


def test_check_bank_setup_notify_not_spammed_every_tick(monkeypatch, tmp_path):
    _fresh_state_file(monkeypatch, tmp_path)
    bsm._save_state({**bsm._default_state(), "last_closed_15m_ts": 1000,
                      "last_source_down_notify_ts": time.time()})

    def fake_klines(interval, limit=5, dead_sources=None):
        raise RuntimeError("все источники недоступны")
    monkeypatch.setattr(bsm, "get_klines", fake_klines)

    sent = []
    async def fake_send(bot, text, critical=False):
        sent.append((text, critical))

    _run(bsm.check_bank_setup(bot=None, send_system_fn=fake_send))
    assert sent == []  # только что уведомляли -- в пределах интервала молчим
