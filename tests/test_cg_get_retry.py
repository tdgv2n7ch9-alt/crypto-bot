"""
pytest для bot._cg_get() -- ретрай на 429 + честная диагностика в
_startup_integrity_check() (владелец, находка 2026-07-15: health-регресс
"CoinGecko: пустой ответ" маскировал реальную 429-ошибку; живые логи
показали, что прежний _CG_MIN_INTERVAL=1.3с соблюдался честно, но КАЖДЫЙ
из 11 последовательных запросов всё равно получил 429 -- лимит бесплатного
тарифа CoinGecko сейчас строже прежнего допущения).
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot


class _FakeResponse:
    def __init__(self, status_code, json_data=None):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else []

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self):
        return self._json_data


@pytest.fixture(autouse=True)
def _reset_cg_state(monkeypatch):
    """Каждый тест -- с чистым кэшем/таймингом, чтобы прошлые тесты не
    влияли на текущий (общие модульные глобали)."""
    monkeypatch.setattr(bot, "_cg_cache", {})
    monkeypatch.setattr(bot, "_cg_last_call_ts", 0.0)
    monkeypatch.setattr(bot, "_cg_consecutive_429", 0)
    monkeypatch.setattr(bot, "_cg_cooldown_until", 0.0)
    # ускоряем тесты -- не ждём реальные секунды паузы/бэкоффа
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)


def test_cg_get_retries_once_on_429_and_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, params=None, timeout=10):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResponse(429)
        return _FakeResponse(200, [{"id": "bitcoin"}])

    monkeypatch.setattr(bot.requests, "get", fake_get)
    data = bot._cg_get("https://api.coingecko.com/api/v3/coins/markets", params={"page": 1})
    assert calls["n"] == 2  # один первичный + один ретрай
    assert data == [{"id": "bitcoin"}]


def test_cg_get_raises_if_retry_also_429(monkeypatch):
    def fake_get(url, params=None, timeout=10):
        return _FakeResponse(429)

    monkeypatch.setattr(bot.requests, "get", fake_get)
    with pytest.raises(Exception):
        bot._cg_get("https://api.coingecko.com/api/v3/coins/markets", params={"page": 1})


def test_cg_get_no_retry_needed_on_success(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, params=None, timeout=10):
        calls["n"] += 1
        return _FakeResponse(200, [{"id": "ethereum"}])

    monkeypatch.setattr(bot.requests, "get", fake_get)
    data = bot._cg_get("https://api.coingecko.com/api/v3/coins/markets", params={"page": 1})
    assert calls["n"] == 1  # без 429 -- без ретрая
    assert data == [{"id": "ethereum"}]


def test_cg_min_interval_increased_from_prior_value():
    """Регресс-замок: находка 2026-07-15 (11 запросов с честным интервалом
    1.3с -- все 429) -- пауза увеличена, не откатывается случайно назад."""
    assert bot._CG_MIN_INTERVAL > 1.3


# ── circuit breaker (владелец, приёмка v130, 2026-07-16) ───────────────────

def test_cg_get_opens_circuit_breaker_after_persistent_429(monkeypatch):
    """Живая находка: даже после ОДНОГО ретрая устойчивый 429 продолжается --
    circuit breaker должен взвестись (cooldown > 0) после исчерпания ретрая."""
    def fake_get(url, params=None, timeout=10):
        return _FakeResponse(429)

    monkeypatch.setattr(bot.requests, "get", fake_get)
    with pytest.raises(Exception):
        bot._cg_get("https://api.coingecko.com/api/v3/coins/markets", params={"page": 1})

    status = bot.cg_rate_limit_status()
    assert status["in_cooldown"] is True
    assert status["consecutive_429"] == 1


def test_cg_get_skips_network_call_during_cooldown(monkeypatch):
    """Пока breaker открыт -- НИ ОДНОГО сетевого вызова, честная быстрая ошибка."""
    monkeypatch.setattr(bot, "_cg_consecutive_429", 1)
    monkeypatch.setattr(bot, "_cg_cooldown_until", time.time() + 30)

    calls = {"n": 0}
    def fake_get(url, params=None, timeout=10):
        calls["n"] += 1
        return _FakeResponse(200, [])

    monkeypatch.setattr(bot.requests, "get", fake_get)
    with pytest.raises(RuntimeError, match="circuit breaker"):
        bot._cg_get("https://api.coingecko.com/api/v3/coins/markets", params={"page": 2})
    assert calls["n"] == 0


def test_cg_get_cooldown_escalates_exponentially_on_repeat_429(monkeypatch):
    """Второй подряд открытый breaker (после того, как первый истёк) должен
    дать окно БОЛЬШЕ первого -- экспоненциальный, не фиксированный бэкофф."""
    def fake_get(url, params=None, timeout=10):
        return _FakeResponse(429)

    monkeypatch.setattr(bot.requests, "get", fake_get)
    now = time.time()
    monkeypatch.setattr(bot.time, "time", lambda: now)

    with pytest.raises(Exception):
        bot._cg_get("https://api.coingecko.com/api/v3/coins/markets", params={"page": 1})
    first_cooldown = bot._cg_cooldown_until - now

    # breaker "истёк" -- время ушло вперёд за пределы cooldown, но счётчик не сброшен
    # руками (имитируем истечение окна, НЕ успех) -- следующая попытка тоже 429
    monkeypatch.setattr(bot, "_cg_cooldown_until", now)  # окно уже прошло
    with pytest.raises(Exception):
        bot._cg_get("https://api.coingecko.com/api/v3/coins/markets", params={"page": 3})
    second_cooldown = bot._cg_cooldown_until - now

    assert second_cooldown > first_cooldown


def test_cg_get_success_resets_consecutive_429_counter(monkeypatch):
    monkeypatch.setattr(bot, "_cg_consecutive_429", 3)
    monkeypatch.setattr(bot, "_cg_cooldown_until", 0.0)  # окно уже истекло

    def fake_get(url, params=None, timeout=10):
        return _FakeResponse(200, [{"id": "bitcoin"}])

    monkeypatch.setattr(bot.requests, "get", fake_get)
    bot._cg_get("https://api.coingecko.com/api/v3/coins/markets", params={"page": 5})
    assert bot._cg_consecutive_429 == 0
    assert bot.cg_rate_limit_status()["in_cooldown"] is False


# ── _startup_integrity_check() -- честная диагностика CoinGecko-строки ─────

@pytest.fixture(autouse=True)
def _reset_startup_notify_state(monkeypatch, tmp_path):
    """Изолирует STARTUP_NOTIFY_STATE_FILE от реального journal/ на диске --
    иначе тесты читали бы/писали бы настоящий файл между прогонами."""
    monkeypatch.setattr(bot, "STARTUP_NOTIFY_STATE_FILE", str(tmp_path / "last_startup_notify.json"))


def test_startup_check_shows_real_error_not_generic_empty(monkeypatch):
    monkeypatch.setattr(bot, "subscribers", type("S", (), {"status": staticmethod(lambda: {"count": 1, "source": "github"})})())
    monkeypatch.setattr(bot.signal_journal, "get_status_counts", lambda: (0, 0))
    monkeypatch.setattr(bot, "_fetch_coingecko_markets", lambda pages, per_page: [])
    monkeypatch.setitem(bot._DATA_SOURCE_STATUS, "coingecko_markets",
                         {"ok": False, "last_error": "HTTP 429: rate limited", "last_ts": time.time()})
    monkeypatch.setattr(bot.shadow_engine, "get_local_records", lambda: [])
    monkeypatch.setattr(bot.shadow_engine, "integrity_report", lambda recs: {"schema_ok": True, "duplicate_count": 0, "out_of_order_count": 0, "total": 0})

    sent = {}
    class _FakeBot:
        async def send_message(self, chat_id, text, **kw):
            sent["text"] = text

    import asyncio
    asyncio.run(bot._startup_integrity_check(_FakeBot(), owner_id=123))
    assert "429" in sent["text"] or "rate limited" in sent["text"]
    assert "пустой ответ" in sent["text"]  # контекст сохранён, не просто заменили текст


def test_startup_check_honest_when_no_recorded_error(monkeypatch):
    monkeypatch.setattr(bot, "subscribers", type("S", (), {"status": staticmethod(lambda: {"count": 1, "source": "github"})})())
    monkeypatch.setattr(bot.signal_journal, "get_status_counts", lambda: (0, 0))
    monkeypatch.setattr(bot, "_fetch_coingecko_markets", lambda pages, per_page: [])
    monkeypatch.setitem(bot._DATA_SOURCE_STATUS, "coingecko_markets",
                         {"ok": None, "last_error": None, "last_ts": 0})
    monkeypatch.setattr(bot.shadow_engine, "get_local_records", lambda: [])
    monkeypatch.setattr(bot.shadow_engine, "integrity_report", lambda recs: {"schema_ok": True, "duplicate_count": 0, "out_of_order_count": 0, "total": 0})

    sent = {}
    class _FakeBot:
        async def send_message(self, chat_id, text, **kw):
            sent["text"] = text

    import asyncio
    asyncio.run(bot._startup_integrity_check(_FakeBot(), owner_id=123))
    assert "без записанной ошибки" in sent["text"]  # честно, не выдумывает причину


# ── стартовое сообщение throttle (владелец, приёмка v130, 2026-07-16) ──────

def _patch_startup_check_deps(monkeypatch):
    monkeypatch.setattr(bot, "subscribers", type("S", (), {"status": staticmethod(lambda: {"count": 1, "source": "github"})})())
    monkeypatch.setattr(bot.signal_journal, "get_status_counts", lambda: (0, 0))
    monkeypatch.setattr(bot, "_fetch_coingecko_markets", lambda pages, per_page: [{"id": "bitcoin"}])
    monkeypatch.setattr(bot.shadow_engine, "get_local_records", lambda: [])
    monkeypatch.setattr(bot.shadow_engine, "integrity_report", lambda recs: {"schema_ok": True, "duplicate_count": 0, "out_of_order_count": 0, "total": 0})
    monkeypatch.setattr(bot.shadow_engine, "_push_pending_archives_sync", lambda: {"attempted": 0, "succeeded": 0})


def test_startup_notify_sent_on_first_ever_run(monkeypatch):
    """Нет файла состояния -- считаем, что throttle-окно не активно, отправляем."""
    _patch_startup_check_deps(monkeypatch)
    sent = {"n": 0}
    class _FakeBot:
        async def send_message(self, chat_id, text, **kw):
            sent["n"] += 1

    import asyncio
    asyncio.run(bot._startup_integrity_check(_FakeBot(), owner_id=123))
    assert sent["n"] == 1


def test_startup_notify_skipped_within_throttle_window(monkeypatch):
    """Владелец, приёмка v130: диагностика "рестарты 12:09/12:19/12:24" --
    несколько легитимных деплоев подряд не должны спамить стартовым
    сообщением на КАЖДЫЙ рестарт."""
    _patch_startup_check_deps(monkeypatch)
    bot._write_last_startup_notify_ts(time.time() - 60)  # 1 мин назад -- внутри окна 30 мин

    sent = {"n": 0}
    class _FakeBot:
        async def send_message(self, chat_id, text, **kw):
            sent["n"] += 1

    import asyncio
    asyncio.run(bot._startup_integrity_check(_FakeBot(), owner_id=123))
    assert sent["n"] == 0


def test_startup_notify_sent_again_after_window_passes(monkeypatch):
    _patch_startup_check_deps(monkeypatch)
    bot._write_last_startup_notify_ts(time.time() - bot.STARTUP_NOTIFY_MIN_GAP_SEC - 1)  # чуть за окном

    sent = {"n": 0}
    class _FakeBot:
        async def send_message(self, chat_id, text, **kw):
            sent["n"] += 1

    import asyncio
    asyncio.run(bot._startup_integrity_check(_FakeBot(), owner_id=123))
    assert sent["n"] == 1


def test_startup_notify_writes_state_after_sending(monkeypatch):
    _patch_startup_check_deps(monkeypatch)
    before = time.time()

    class _FakeBot:
        async def send_message(self, chat_id, text, **kw):
            pass

    import asyncio
    asyncio.run(bot._startup_integrity_check(_FakeBot(), owner_id=123))
    ts = bot._read_last_startup_notify_ts()
    assert ts is not None and ts >= before
