"""
pytest для bot.get_onchain_snapshot_cached() -- Фаза C on-chain shadow,
инкремент 1 (владелец, "Наряд на день" 2026-07-15). onchain_metrics.
get_free_onchain_snapshot() у себя НЕ кэшируется (каждый вызов бьёт до 9
внешних эндпоинтов) -- эта обёртка добавляет 2-бакетный TTL-кэш (BTC-полный
снэпшот отдельно от market-only снэпшота для всех остальных символов), чтобы
AUTO-цикл не бил одни и те же market-метрики заново на каждого кандидата.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot


def setup_function(_):
    bot._onchain_snapshot_cache.clear()


def teardown_function(_):
    bot._onchain_snapshot_cache.clear()


def _fake_snapshot(symbol):
    return {"ok": True, "symbol": symbol.upper(), "market": {"fear_greed": {"ok": True, "value": 50}},
            "btc_chain": {"hashrate": {"ok": True}} if symbol.upper() == "BTC" else None}


def test_first_call_fetches_and_caches(monkeypatch):
    calls = []

    def fake_fetch(symbol):
        calls.append(symbol)
        return _fake_snapshot(symbol)

    monkeypatch.setattr(bot.onchain_metrics, "get_free_onchain_snapshot", fake_fetch)
    result = bot.get_onchain_snapshot_cached("BTC")
    assert calls == ["BTC"]
    assert result["symbol"] == "BTC"
    assert result["ok"] is True


def test_second_call_within_ttl_does_not_refetch(monkeypatch):
    calls = []

    def fake_fetch(symbol):
        calls.append(symbol)
        return _fake_snapshot(symbol)

    monkeypatch.setattr(bot.onchain_metrics, "get_free_onchain_snapshot", fake_fetch)
    bot.get_onchain_snapshot_cached("BTC")
    bot.get_onchain_snapshot_cached("BTC")
    assert calls == ["BTC"]  # второй вызов -- из кэша, без сети


def test_different_non_btc_symbols_share_one_market_only_bucket(monkeypatch):
    """ETH и SOL оба НЕ BTC -- market-метрики одинаковые для любого символа,
    не должны триггерить отдельный фетч на каждый уникальный алт-символ."""
    calls = []

    def fake_fetch(symbol):
        calls.append(symbol)
        return _fake_snapshot(symbol)

    monkeypatch.setattr(bot.onchain_metrics, "get_free_onchain_snapshot", fake_fetch)
    r_eth = bot.get_onchain_snapshot_cached("ETH")
    r_sol = bot.get_onchain_snapshot_cached("SOL")
    assert len(calls) == 1  # только первый (ETH) реально сходил в сеть
    assert r_eth["symbol"] == "ETH"
    assert r_sol["symbol"] == "SOL"  # honest: 'symbol' поле отражает ЗАПРОШЕННЫЙ символ,
                                       # даже если данные пришли из общего "OTHER"-бакета


def test_btc_and_other_use_separate_cache_buckets(monkeypatch):
    calls = []

    def fake_fetch(symbol):
        calls.append(symbol)
        return _fake_snapshot(symbol)

    monkeypatch.setattr(bot.onchain_metrics, "get_free_onchain_snapshot", fake_fetch)
    r_btc = bot.get_onchain_snapshot_cached("BTC")
    r_eth = bot.get_onchain_snapshot_cached("ETH")
    assert calls == ["BTC", "ETH"]  # BTC-бакет и OTHER-бакет -- РАЗНЫЕ, оба реально фетчатся один раз
    assert r_btc["btc_chain"] is not None
    assert r_eth["btc_chain"] is None


def test_cache_expires_after_ttl(monkeypatch):
    calls = []

    def fake_fetch(symbol):
        calls.append(symbol)
        return _fake_snapshot(symbol)

    monkeypatch.setattr(bot.onchain_metrics, "get_free_onchain_snapshot", fake_fetch)
    bot.get_onchain_snapshot_cached("BTC")
    # искусственно состариваем запись в кэше на 301с назад
    ts, snap = bot._onchain_snapshot_cache["BTC"]
    bot._onchain_snapshot_cache["BTC"] = (ts - 301, snap)
    bot.get_onchain_snapshot_cached("BTC")
    assert calls == ["BTC", "BTC"]  # второй вызов после истечения TTL -- реальный повторный фетч


def test_returned_snapshot_is_a_copy_not_shared_mutable_reference(monkeypatch):
    """Честность: мутация результата вызывающей стороной не должна портить
    закэшированную запись для следующего вызова."""
    monkeypatch.setattr(bot.onchain_metrics, "get_free_onchain_snapshot", _fake_snapshot)
    r1 = bot.get_onchain_snapshot_cached("BTC")
    r1["injected_junk"] = True
    r2 = bot.get_onchain_snapshot_cached("BTC")
    assert "injected_junk" not in r2
