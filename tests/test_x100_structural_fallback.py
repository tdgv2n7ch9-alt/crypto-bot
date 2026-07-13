"""
pytest для Пакет 18, п.5 (владелец): "x100-сканер: входы от структуры, не от
текущей цены; SL за структурой +2-3%, не фикс -3%. Если структурной зоны
нет — честная строка 'вход по рынку не предлагаем, ждать отката к
{уровень}'." Структурные входы/SL уже существовали (ta_extra.
build_trade_from_structure(), SR_SL_BUFFER_PCT=2.5) -- реальный пробел был
в том, что кандидат без структурной зоны ниже цены (build_trade_from_structure
-> None) отбрасывался ПОЛНОСТЬЮ МОЛЧА (continue без единой строки), а не
показывался честным фоллбеком. Тест гоняет cmd_x100_scanner() целиком через
тяжёлый мок (тот же паттерн, что test_fa_path_logging.py для cmd_precision).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot
import ta_extra


def _coin(symbol, price=0.05, mcap=20_000_000, vol24=25_000_000, ch24=25, ch7d=15, ch30d=5):
    return {
        "symbol": symbol, "slug": symbol.lower(), "name": symbol,
        "quote": {"USDT": {
            "price": price, "market_cap": mcap, "volume_24h": vol24,
            "percent_change_24h": ch24, "percent_change_7d": ch7d, "percent_change_30d": ch30d,
        }}
    }


class _FakeMsg:
    def __init__(self):
        self.texts = []

    async def reply_text(self, text, **kw):
        self.texts.append(text)
        return self


class _FakeUpdate:
    def __init__(self):
        self.message = _FakeMsg()


class _FakeCtx:
    pass


def _patch_common(monkeypatch, coin, zones):
    monkeypatch.setattr(bot, "get_all_coins", lambda: [coin])
    monkeypatch.setattr(bot, "get_binance_ohlc", lambda sym, tf, n: [{"open": 1, "high": 1, "low": 1, "close": 1}] * 5)
    monkeypatch.setattr(ta_extra, "ema_context",
                         lambda c1, c4: {"tf_1h": {"stack": "bullish"}, "tf_4h": {"stack": "bullish"}})
    monkeypatch.setattr(ta_extra, "detect_sweep", lambda c: None)
    monkeypatch.setattr(ta_extra, "find_sr_zones", lambda c1, c4, c1d, p, ema_ctx=None: zones)

    import live_prices
    monkeypatch.setattr(live_prices, "resolve_price", lambda sym, p_cg: (p_cg, "(живая)"))

    monkeypatch.setattr(bot.signal_journal, "log_signal", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "_cg_get", lambda *a, **kw: {})
    monkeypatch.setattr(bot.etherscan_whale, "fetch_transfer_data", lambda *a, **kw: None)
    monkeypatch.setattr(bot.rug_radar, "compute_rug_risk",
                         lambda *a, **kw: {"score": 0, "warn": False, "reasons": []})
    monkeypatch.setattr(bot.rug_radar, "format_rug_risk_line", lambda *a, **kw: "")
    monkeypatch.setattr(bot.rug_radar, "compute_age_days", lambda *a, **kw: {"age_days": 1000, "age_is_approx": False})
    monkeypatch.setattr(bot.new_coin_scan, "format_young_coin_flag", lambda *a, **kw: "")


def test_x100_no_structure_zone_shows_honest_fallback_not_silent_drop(monkeypatch):
    """Ядро п.5: build_trade_from_structure() не может построить сделку (нет
    зоны below цены) -- карточка ОБЯЗАНА появиться с честной строкой про
    отсутствие структурной зоны, а не исчезнуть молча."""
    coin = _coin("KITE")
    # below пуст -- build_trade_from_structure() вернёт None (см. её докстринг)
    zones = {"below": [], "above": [{"lo": 0.06, "hi": 0.07, "mid": 0.065}]}
    _patch_common(monkeypatch, coin, zones)

    update = _FakeUpdate()
    asyncio.run(bot.cmd_x100_scanner(update, _FakeCtx()))

    text = update.message.texts[-1]
    assert "KITE" in text
    assert "Вход по рынку не предлагаем" in text
    assert "структурной зоны" in text


def test_x100_no_structure_uses_real_52w_low_as_level_not_fabricated(monkeypatch):
    coin = _coin("KITE")
    zones = {"below": [], "above": []}
    _patch_common(monkeypatch, coin, zones)
    # переопределим OHLC так, чтобы 52н минимум был легко узнаваемым числом
    monkeypatch.setattr(bot, "get_binance_ohlc",
                         lambda sym, tf, n: [{"open": 1, "high": 1, "low": 0.0321, "close": 1}] * 5)

    update = _FakeUpdate()
    asyncio.run(bot.cmd_x100_scanner(update, _FakeCtx()))

    text = update.message.texts[-1]
    assert "0.0321" in text or "0.032" in text  # реальный low из свечей, не выдумка


def test_x100_structural_zone_present_shows_real_trade_plan_unchanged(monkeypatch):
    """Регрессия: когда структура ЕСТЬ и R:R проходит гейт -- прежнее
    поведение (полная карточка со входами/SL/TP) не сломано этим пакетом."""
    coin = _coin("AVAX", price=20.0)
    zones = {
        "below": [{"lo": 18.0, "hi": 19.0, "mid": 18.5}],
        "above": [{"lo": 25.0, "hi": 26.0, "mid": 25.5}, {"lo": 30.0, "hi": 31.0, "mid": 30.5}],
    }
    _patch_common(monkeypatch, coin, zones)
    monkeypatch.setattr(live_prices := __import__("live_prices"), "resolve_price",
                         lambda sym, p_cg: (20.0, "(живая)"))

    update = _FakeUpdate()
    asyncio.run(bot.cmd_x100_scanner(update, _FakeCtx()))

    text = update.message.texts[-1]
    assert "AVAX" in text
    assert "Вход по рынку не предлагаем" not in text
    assert "СПОТ" in text
