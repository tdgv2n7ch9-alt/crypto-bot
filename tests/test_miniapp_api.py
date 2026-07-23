"""
pytest для П-MiniApp Этап 1 (владелец, docs/TZ_P-MiniApp_v1.md): read-only JSON
API-слой. Три уровня: (1) verify_telegram_init_data() -- чистая HMAC-функция,
без сети/aiohttp; (2) check_api_rate_limit()/_TTLCache -- чистые примитивы;
(3) полные HTTP-round-trip тесты через aiohttp.test_utils (реальный
TestClient, без реального TCP-порта) -- снапшот схемы /api/v1/track-record и
/api/v1/zones + сверка track-record с текстовым источником
(shadow_outcome_analysis.closed_outcomes_report()) на одних и тех же данных
(ТЗ раздел 7, DoD Этапа 1: "JSON, идентичный данным карточек бота").
"""
import asyncio
import hashlib
import hmac
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import miniapp_api as ma
import signal_journal

BOT_TOKEN = "123456:FAKE-TOKEN-FOR-TESTS-ONLY"


@pytest.fixture(autouse=True)
def _reset_global_rate_limit_state():
    """Мidleware по умолчанию пишет в модульный ma._api_call_history (не в тестовый
    `history=`) -- без сброса HTTP-тесты одного chat_id заражали бы друг друга
    (особенно test_rate_limit_enforced_over_http, который сознательно выжигает лимит)."""
    ma._api_call_history.clear()
    yield
    ma._api_call_history.clear()


def _run(coro):
    return asyncio.run(coro)


def _sign(data: dict, bot_token: str) -> str:
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()


def _build_init_data(user_id=7009350191, auth_date=None, bot_token=BOT_TOKEN,
                      bad_hash=False, omit_hash=False, omit_user=False,
                      omit_auth_date=False, malformed_user=False):
    auth_date = auth_date if auth_date is not None else int(time.time())
    data = {"query_id": "AAH_fake_query_id", "auth_date": str(auth_date)}
    if not omit_user:
        data["user"] = "not-json" if malformed_user else json.dumps({"id": user_id, "first_name": "Owner"})
    if omit_auth_date:
        del data["auth_date"]
    h = _sign(data, bot_token)
    if bad_hash:
        h = "0" * 64
    parts = [f"{k}={v}" for k, v in data.items()]
    if not omit_hash:
        parts.append(f"hash={h}")
    from urllib.parse import quote
    return "&".join(f"{k}={quote(v, safe='')}" for k, v in
                     [p.split("=", 1) for p in parts])


# ── verify_telegram_init_data() ──────────────────────────────────────────

def test_verify_valid_init_data_ok():
    init_data = _build_init_data()
    v = ma.verify_telegram_init_data(init_data, BOT_TOKEN)
    assert v["ok"] is True
    assert v["user_id"] == 7009350191


def test_verify_rejects_empty():
    v = ma.verify_telegram_init_data("", BOT_TOKEN)
    assert v["ok"] is False
    assert v["reason"] == "empty initData"


def test_verify_rejects_missing_bot_token():
    v = ma.verify_telegram_init_data(_build_init_data(), None)
    assert v["ok"] is False
    assert v["reason"] == "bot token not configured"


def test_verify_rejects_bad_signature():
    init_data = _build_init_data(bad_hash=True)
    v = ma.verify_telegram_init_data(init_data, BOT_TOKEN)
    assert v["ok"] is False
    assert v["reason"] == "signature mismatch"


def test_verify_rejects_wrong_bot_token():
    init_data = _build_init_data(bot_token="999:OTHER-TOKEN")
    v = ma.verify_telegram_init_data(init_data, BOT_TOKEN)
    assert v["ok"] is False
    assert v["reason"] == "signature mismatch"


def test_verify_rejects_missing_hash():
    init_data = _build_init_data(omit_hash=True)
    v = ma.verify_telegram_init_data(init_data, BOT_TOKEN)
    assert v["ok"] is False
    assert v["reason"] == "missing hash"


def test_verify_rejects_expired_auth_date():
    old_ts = int(time.time()) - 7200  # 2 часа назад > INIT_DATA_MAX_AGE_SEC (1ч)
    init_data = _build_init_data(auth_date=old_ts)
    v = ma.verify_telegram_init_data(init_data, BOT_TOKEN)
    assert v["ok"] is False
    assert v["reason"] == "auth_date expired"


def test_verify_accepts_auth_date_just_under_limit():
    ts = int(time.time()) - (ma.INIT_DATA_MAX_AGE_SEC - 60)
    init_data = _build_init_data(auth_date=ts)
    v = ma.verify_telegram_init_data(init_data, BOT_TOKEN)
    assert v["ok"] is True


def test_verify_rejects_missing_user():
    init_data = _build_init_data(omit_user=True)
    v = ma.verify_telegram_init_data(init_data, BOT_TOKEN)
    assert v["ok"] is False
    assert v["reason"] == "missing user field"


def test_verify_rejects_malformed_user_json():
    init_data = _build_init_data(malformed_user=True)
    v = ma.verify_telegram_init_data(init_data, BOT_TOKEN)
    assert v["ok"] is False
    assert v["reason"] == "malformed user field"


def test_verify_rejects_malformed_query_string():
    v = ma.verify_telegram_init_data("not a valid=query=string=&&&", BOT_TOKEN)
    assert v["ok"] is False


def test_verify_different_user_id_reflected():
    init_data = _build_init_data(user_id=555)
    v = ma.verify_telegram_init_data(init_data, BOT_TOKEN)
    assert v["ok"] is True
    assert v["user_id"] == 555


# ── check_api_rate_limit() ────────────────────────────────────────────────

def test_rate_limit_allows_under_threshold():
    hist = {}
    now = 1000.0
    for i in range(10):
        assert ma.check_api_rate_limit(1, now=now + i, history=hist) is True


def test_rate_limit_blocks_over_threshold():
    hist = {}
    now = 1000.0
    allowed = [ma.check_api_rate_limit(1, now=now + i * 0.1, history=hist)
               for i in range(ma.RATE_LIMIT_MAX_PER_MIN + 5)]
    assert allowed[-1] is False
    assert all(allowed[:ma.RATE_LIMIT_MAX_PER_MIN])


def test_rate_limit_independent_per_chat_id():
    hist = {}
    now = 1000.0
    for i in range(ma.RATE_LIMIT_MAX_PER_MIN + 5):
        ma.check_api_rate_limit(1, now=now + i * 0.1, history=hist)
    # chat_id 2 -- отдельный счётчик, не пострадал от нагрузки chat_id 1
    assert ma.check_api_rate_limit(2, now=now, history=hist) is True


def test_rate_limit_window_expiry_resets():
    hist = {}
    now = 1000.0
    for i in range(ma.RATE_LIMIT_MAX_PER_MIN + 5):
        ma.check_api_rate_limit(1, now=now + i * 0.1, history=hist)
    # далеко за окном -- лимит должен сброситься
    assert ma.check_api_rate_limit(1, now=now + ma.RATE_LIMIT_WINDOW_SEC + 10, history=hist) is True


# ── _TTLCache ─────────────────────────────────────────────────────────────

def test_ttl_cache_returns_none_before_set():
    cache = ma._TTLCache(ttl_sec=10)
    assert cache.get(now=100.0) is None


def test_ttl_cache_returns_value_within_ttl():
    cache = ma._TTLCache(ttl_sec=10)
    cache.set({"x": 1}, now=100.0)
    assert cache.get(now=105.0) == {"x": 1}


def test_ttl_cache_expires_after_ttl():
    cache = ma._TTLCache(ttl_sec=10)
    cache.set({"x": 1}, now=100.0)
    assert cache.get(now=111.0) is None


# ── HTTP round-trip (aiohttp.test_utils) ────────────────────────────────

class _FakeBotModule:
    BOT_TOKEN = BOT_TOKEN
    # Владелец, ДА, 2026-07-23: /api/v1/signals scope -- те же константы,
    # что bot.py использует для AUTO-пилота (_auto_concurrent_limit_reached()).
    AUTO_LIVE_SOURCES = ("TOP_LONG_AUTO", "TOP_SHORT_AUTO")
    AUTO_EMISSION_EXPERIMENT_START_TS = 1784712842.0

    def __init__(self, zones=None, coins=None, top_long=None, top_short=None, binance_prices=None,
                 btc_ctx=None, btc_ctx_boom=False, onchain_text="on-chain OK", onchain_boom=False):
        self._zones = zones or []
        self._coins = coins or []
        self.TOP_LONG_SIGNALS = top_long or {}
        self.TOP_SHORT_SIGNALS = top_short or {}
        self._binance_prices = binance_prices or {}
        self._btc_ctx = btc_ctx
        self._btc_ctx_boom = btc_ctx_boom

        class _FakeOnchainMetrics:
            def format_onchain_card_text(_self, symbol):
                if onchain_boom:
                    raise RuntimeError("simulated onchain failure")
                return onchain_text

        self.onchain_metrics = _FakeOnchainMetrics()
        self._cg_status = {"in_cooldown": False, "cooldown_remaining_sec": 0.0, "consecutive_429": 0}

    def cg_rate_limit_status(self):
        return self._cg_status

    def _limitki_collect_zones(self):
        return self._zones

    def _limitki_zone_status(self, side, lo, hi, price, cancelled=False):
        # Не дублирует боевую логику (правило проекта) -- зовёт саму боевую
        # функцию bot.py напрямую.
        import bot
        return bot._limitki_zone_status(side, lo, hi, price, cancelled=cancelled)

    def get_top500(self):
        return self._coins

    def get_binance_24h(self, symbol):
        if symbol in self._binance_prices:
            return {"last": self._binance_prices[symbol]}
        return {}

    def top_trades_long_status(self, entry, cur, tp1, tp2, tp3, sl):
        import bot
        return bot.top_trades_long_status(entry, cur, tp1, tp2, tp3, sl)

    def top_trades_short_status(self, entry, cur, tp1, tp2, sl):
        import bot
        return bot.top_trades_short_status(entry, cur, tp1, tp2, sl)

    def get_btc_market_context(self):
        if getattr(self, "_btc_ctx_boom", False):
            raise RuntimeError("simulated btc_ctx failure")
        return self._btc_ctx or {"ok": True, "btc_price": 60000.0, "btc_ch24h": 1.5,
                                  "dominance": 52.0, "trend_1h": "bullish"}


def _make_client(loop, app):
    from aiohttp.test_utils import TestClient, TestServer
    server = TestServer(app, loop=loop)
    client = TestClient(server, loop=loop)
    loop.run_until_complete(client.start_server())
    return client


def test_health_endpoint_no_auth_required():
    async def go():
        app = ma.build_app(_FakeBotModule())
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/health")
            assert resp.status == 200
            body = await resp.json()
            assert body["ok"] is True
    _run(go())


def test_health_endpoint_reports_degraded_on_cg_cooldown():
    """Владелец, приёмка v130 (2026-07-16): "деградация в /health жёлтым"
    при активном CoinGecko circuit breaker -- ok остаётся True (бот жив),
    status переходит в "degraded" с деталями."""
    async def go():
        fake = _FakeBotModule()
        fake._cg_status = {"in_cooldown": True, "cooldown_remaining_sec": 42.0, "consecutive_429": 3}
        app = ma.build_app(fake)
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/health")
            assert resp.status == 200
            body = await resp.json()
            assert body["ok"] is True
            assert body["status"] == "degraded"
            assert body["coingecko"]["consecutive_429"] == 3
    _run(go())


def test_health_endpoint_live_when_cg_not_in_cooldown():
    async def go():
        app = ma.build_app(_FakeBotModule())
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/health")
            body = await resp.json()
            assert body["status"] == "live"
            assert "coingecko" not in body
    _run(go())


def test_protected_endpoint_rejects_missing_auth():
    async def go():
        app = ma.build_app(_FakeBotModule())
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/track-record")
            assert resp.status == 401
    _run(go())


def test_protected_endpoint_rejects_non_whitelisted_chat_id():
    async def go():
        app = ma.build_app(_FakeBotModule())
        init_data = _build_init_data(user_id=1)  # не в ALLOWED_CHAT_IDS
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/track-record",
                                     headers={"X-Telegram-Init-Data": init_data})
            assert resp.status == 403
    _run(go())


def test_track_record_matches_text_source_on_same_data(monkeypatch, tmp_path):
    """DoD Этапа 1: JSON API идентичен данным, которые видит текстовый экран --
    оба источника (API-хендлер и прямой вызов closed_outcomes_report()) читают
    ОДНУ И ТУ ЖЕ функцию, поэтому при одинаковых входных файлах результат
    обязан совпасть побайтово (после json round-trip)."""
    import shadow_engine as se
    import shadow_outcome_analysis as soa
    monkeypatch.setattr(se, "SHADOW_FILE", str(tmp_path / "shadow.json"))
    monkeypatch.setattr(se, "ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setattr(soa, "JOURNAL_SIGNALS_PATH", str(tmp_path / "signals.json"))

    expected = soa.closed_outcomes_report()

    async def go():
        app = ma.build_app(_FakeBotModule())
        init_data = _build_init_data()
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/track-record",
                                     headers={"X-Telegram-Init-Data": init_data})
            assert resp.status == 200
            body = await resp.json()
            assert body["ok"] is True
            assert body["data"] == json.loads(json.dumps(expected))
    _run(go())


def test_track_record_response_is_cached(monkeypatch, tmp_path):
    import shadow_engine as se
    import shadow_outcome_analysis as soa
    monkeypatch.setattr(se, "SHADOW_FILE", str(tmp_path / "shadow.json"))
    monkeypatch.setattr(se, "ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setattr(soa, "JOURNAL_SIGNALS_PATH", str(tmp_path / "signals.json"))

    calls = []
    real_report = soa.closed_outcomes_report

    def _counting_report(*a, **kw):
        calls.append(1)
        return real_report(*a, **kw)

    monkeypatch.setattr(soa, "closed_outcomes_report", _counting_report)

    async def go():
        app = ma.build_app(_FakeBotModule())
        init_data = _build_init_data()
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            headers = {"X-Telegram-Init-Data": init_data}
            r1 = await client.get("/api/v1/track-record", headers=headers)
            r2 = await client.get("/api/v1/track-record", headers=headers)
            assert r1.status == 200 and r2.status == 200
    _run(go())
    assert len(calls) == 1  # второй запрос -- из кэша, не пересчитан


def test_zones_endpoint_schema_and_status():
    zone_item = {"symbol": "BTC", "zone": {"side": "LONG", "lo": 60000, "hi": 61000,
                                            "prio": 1, "tier": "author", "note": "test"}}
    coins = [{"symbol": "BTCUSDT", "quote": {"USDT": {"price": 60500}}}]

    async def go():
        app = ma.build_app(_FakeBotModule(zones=[zone_item], coins=coins))
        init_data = _build_init_data()
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/zones",
                                     headers={"X-Telegram-Init-Data": init_data})
            assert resp.status == 200
            body = await resp.json()
            assert body["ok"] is True
            assert len(body["data"]) == 1
            row = body["data"][0]
            assert row["symbol"] == "BTC"
            assert row["side"] == "LONG"
            assert row["status"] == "ЦЕНА В ЗОНЕ"
            assert row["price"] == 60500
    _run(go())


def test_zones_endpoint_empty_when_no_zones():
    async def go():
        app = ma.build_app(_FakeBotModule(zones=[]))
        init_data = _build_init_data()
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/zones",
                                     headers={"X-Telegram-Init-Data": init_data})
            body = await resp.json()
            assert body["data"] == []
    _run(go())


def test_glossary_endpoint_matches_source():
    import glossary

    async def go():
        app = ma.build_app(_FakeBotModule())
        init_data = _build_init_data()
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/glossary",
                                     headers={"X-Telegram-Init-Data": init_data})
            assert resp.status == 200
            body = await resp.json()
            assert body["data"] == glossary.TERMS
    _run(go())


def test_rate_limit_enforced_over_http(monkeypatch):
    async def go():
        app = ma.build_app(_FakeBotModule())
        init_data = _build_init_data()
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            headers = {"X-Telegram-Init-Data": init_data}
            statuses = []
            for _ in range(ma.RATE_LIMIT_MAX_PER_MIN + 3):
                resp = await client.get("/api/v1/glossary", headers=headers)
                statuses.append(resp.status)
            assert 429 in statuses
    _run(go())


# ── /api/v1/signals ──────────────────────────────────────────────────────

# Живая находка (владелец, 2026-07-23): /api/v1/signals раньше читал
# эфемерные TOP_LONG_SIGNALS/TOP_SHORT_SIGNALS (persist только в /tmp,
# без бэкапа в GitHub -- тот же класс бага, что AUTO-гейт лимита) --
# показывал старые вручную добавленные "watching"-записи вместо реальных
# ENTERED-позиций. Теперь читает ПЕРСИСТЕНТНЫЙ signal_journal._journal,
# только status=="ENTERED" -- тесты патчат signal_journal._journal
# напрямую, не bot_module.TOP_LONG_SIGNALS/TOP_SHORT_SIGNALS.

def test_signals_endpoint_long_active_schema(monkeypatch):
    journal = {1: {"id": 1, "symbol": "BTC", "direction": "long", "entered_price": 60000,
                   "tp1": 61200, "tp2": 62400, "tp3": 64800, "sl": 51000,
                   "rr": 3.2, "status": "ENTERED", "source": "TOP_LONG_AUTO",
                   "ts": _FakeBotModule.AUTO_EMISSION_EXPERIMENT_START_TS + 100}}
    monkeypatch.setattr(signal_journal, "_journal", journal)

    async def go():
        app = ma.build_app(_FakeBotModule(binance_prices={"BTC": 60300}))
        init_data = _build_init_data()
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/signals",
                                     headers={"X-Telegram-Init-Data": init_data})
            assert resp.status == 200
            body = await resp.json()
            assert body["ok"] is True
            assert len(body["data"]) == 1
            row = body["data"][0]
            assert row["symbol"] == "BTC"
            assert row["direction"] == "long"
            assert row["entry"] == 60000
            assert row["current_price"] == 60300
            assert row["rr"] == 3.2
    _run(go())


def test_signals_endpoint_excludes_non_entered_status(monkeypatch):
    journal = {
        1: {"id": 1, "symbol": "BTC", "direction": "long", "entered_price": 60000,
            "tp1": 61200, "tp2": 62400, "tp3": 64800, "sl": 51000, "status": "PENDING"},
        2: {"id": 2, "symbol": "ETH", "direction": "long", "entered_price": 3000,
            "tp1": 3100, "tp2": 3200, "tp3": 3300, "sl": 2800, "status": "SL_HIT"},
    }
    monkeypatch.setattr(signal_journal, "_journal", journal)

    async def go():
        app = ma.build_app(_FakeBotModule())
        init_data = _build_init_data()
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/signals",
                                     headers={"X-Telegram-Init-Data": init_data})
            body = await resp.json()
            assert body["data"] == []
    _run(go())


def test_signals_endpoint_excludes_non_pilot_entered_records(monkeypatch):
    """Регресс-тест self-caught бага (владелец, 2026-07-23, live-verify):
    ENTERED-записи ВНЕ AUTO-пилота (не тот source, или до старта эксперимента)
    -- НЕ должны попадать в "Активные сигналы" (иначе панель отдаёт всю
    историю журнала -- десятки символов вместо реальных 3 позиций пилота,
    и таймаутит на get_binance_24h() по каждому)."""
    START = _FakeBotModule.AUTO_EMISSION_EXPERIMENT_START_TS
    journal = {
        1: {"id": 1, "symbol": "TRUMP", "direction": "long", "entered_price": 1.6,
            "status": "ENTERED", "source": "TOP_SPOT_AUTO", "ts": START + 100},
        2: {"id": 2, "symbol": "LTC", "direction": "long", "entered_price": 44.9,
            "status": "ENTERED", "source": "full_analysis", "ts": START + 100},
        3: {"id": 3, "symbol": "CAKE", "direction": "long", "entered_price": 1.4,
            "status": "ENTERED", "source": "TOP_LONG_AUTO", "ts": START - 1000},  # до старта пилота
        4: {"id": 4, "symbol": "US", "direction": "long", "entered_price": 0.044,
            "status": "ENTERED", "source": "TOP_LONG_AUTO", "ts": START + 100},  # реальный пилот
    }
    monkeypatch.setattr(signal_journal, "_journal", journal)

    async def go():
        app = ma.build_app(_FakeBotModule(binance_prices={"US": 0.045}))
        init_data = _build_init_data()
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/signals",
                                     headers={"X-Telegram-Init-Data": init_data})
            body = await resp.json()
            symbols = [s["symbol"] for s in body["data"]]
            assert symbols == ["US"]
    _run(go())


def test_signals_endpoint_short_direction(monkeypatch):
    journal = {1: {"id": 1, "symbol": "ETH", "direction": "short", "entered_price": 3000,
                   "tp1": 2940, "tp2": 2880, "tp3": None, "sl": 3450, "rr": 2.1,
                   "status": "ENTERED", "source": "TOP_SHORT_AUTO",
                   "ts": _FakeBotModule.AUTO_EMISSION_EXPERIMENT_START_TS + 100}}
    monkeypatch.setattr(signal_journal, "_journal", journal)

    async def go():
        app = ma.build_app(_FakeBotModule(binance_prices={"ETH": 2950}))
        init_data = _build_init_data()
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/signals",
                                     headers={"X-Telegram-Init-Data": init_data})
            body = await resp.json()
            row = body["data"][0]
            assert row["symbol"] == "ETH"
            assert row["direction"] == "short"
            assert row["tp3"] is None
    _run(go())


def test_signals_endpoint_empty_when_no_active_signals(monkeypatch):
    monkeypatch.setattr(signal_journal, "_journal", {})

    async def go():
        app = ma.build_app(_FakeBotModule())
        init_data = _build_init_data()
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/signals",
                                     headers={"X-Telegram-Init-Data": init_data})
            body = await resp.json()
            assert body["data"] == []
    _run(go())


def test_signals_endpoint_rejects_unauthenticated():
    async def go():
        app = ma.build_app(_FakeBotModule())
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/signals")
            assert resp.status == 401
    _run(go())


# ── /api/v1/dashboard ────────────────────────────────────────────────────

def test_dashboard_endpoint_returns_btc_context_and_onchain_text():
    async def go():
        app = ma.build_app(_FakeBotModule(
            btc_ctx={"ok": True, "btc_price": 61000.0, "btc_ch24h": 2.1,
                     "dominance": 53.5, "trend_1h": "bullish", "fear_greed": 60},
            onchain_text="On-chain: netflow negative"))
        init_data = _build_init_data()
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/dashboard",
                                     headers={"X-Telegram-Init-Data": init_data})
            assert resp.status == 200
            body = await resp.json()
            assert body["ok"] is True
            assert body["data"]["btc_context"]["btc_price"] == 61000.0
            assert body["data"]["btc_context"]["dominance"] == 53.5
            assert body["data"]["onchain_text"] == "On-chain: netflow negative"
    _run(go())


def test_dashboard_endpoint_btc_context_failure_is_honest_none():
    async def go():
        app = ma.build_app(_FakeBotModule(btc_ctx_boom=True))
        init_data = _build_init_data()
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/dashboard",
                                     headers={"X-Telegram-Init-Data": init_data})
            assert resp.status == 200  # частичный сбой не валит весь эндпоинт
            body = await resp.json()
            assert body["data"]["btc_context"] is None
            assert body["data"]["onchain_text"] is not None  # второй источник не пострадал
    _run(go())


def test_dashboard_endpoint_onchain_failure_is_honest_none():
    async def go():
        app = ma.build_app(_FakeBotModule(onchain_boom=True))
        init_data = _build_init_data()
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/dashboard",
                                     headers={"X-Telegram-Init-Data": init_data})
            assert resp.status == 200
            body = await resp.json()
            assert body["data"]["onchain_text"] is None
            assert body["data"]["btc_context"] is not None
    _run(go())


def test_dashboard_endpoint_rejects_unauthenticated():
    async def go():
        app = ma.build_app(_FakeBotModule())
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/dashboard")
            assert resp.status == 401
    _run(go())


def test_dashboard_endpoint_response_is_cached():
    calls = []

    class _CountingBotModule(_FakeBotModule):
        def get_btc_market_context(self):
            calls.append(1)
            return super().get_btc_market_context()

    async def go():
        app = ma.build_app(_CountingBotModule())
        init_data = _build_init_data()
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            headers = {"X-Telegram-Init-Data": init_data}
            r1 = await client.get("/api/v1/dashboard", headers=headers)
            r2 = await client.get("/api/v1/dashboard", headers=headers)
            assert r1.status == 200 and r2.status == 200
    _run(go())
    assert len(calls) == 1  # второй запрос -- из кэша, get_btc_market_context не вызван снова


# ── /app -- статическая owner-only Mini App страница (Шаг 3/3) ──

def test_app_static_page_no_auth_required():
    """Сама HTML-страница не содержит секретов (auth -- только на /api/v1/*),
    отдаётся без initData -- тот же паттерн, что /api/v1/health."""
    async def go():
        app = ma.build_app(_FakeBotModule())
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/app")
            assert resp.status == 200
            text = await resp.text()
            assert "BEST TRADE" in text
            assert "text/html" in resp.headers["Content-Type"]
    _run(go())


def test_app_static_page_no_cache():
    """Живая находка (владелец, 2026-07-23): без Cache-Control страница
    могла кэшироваться Telegram WebView неограниченно -- будущие фиксы JS/CSS
    не долетали бы до клиента. no-store гарантирует свежую версию каждый раз."""
    async def go():
        app = ma.build_app(_FakeBotModule())
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/app")
            assert "no-store" in resp.headers.get("Cache-Control", "")
    _run(go())


def test_app_static_page_missing_file_returns_honest_500(tmp_path, monkeypatch):
    """Файл отсутствует -- честная 500 с "н/д", не голый traceback наружу."""
    monkeypatch.setattr(ma, "_MINIAPP_INDEX_PATH", str(tmp_path / "missing_index.html"))

    async def go():
        app = ma.build_app(_FakeBotModule())
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/app")
            assert resp.status == 500
            text = await resp.text()
            assert "н/д" in text
    _run(go())
