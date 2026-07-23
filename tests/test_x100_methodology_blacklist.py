"""
pytest -- владелец, ДА, 2026-07-23 (FIXLIST_INTERFACE.md п.3, КРИТИЧНО):
(а/б) чёрный список методологии x100 (BANK -- монитор мёртв, METHODOLOGY_
CORE.md §22; AKE -- wash-класс, WASH_FILTER_SHADOW.md jid=482) -- символ в
списке исключён из выдачи, футер "Отфильтровано по методологии: N".
(в) внутреннее противоречие -- если самый свежий свип медвежий (sweep_high,
"ликвидность в шорт"), x100 (строит ТОЛЬКО LONG) не эмитит карточку.
Тот же мок-паттерн, что test_x100_structural_fallback.py (тяжёлый мок всего
cmd_x100_scanner() целиком, не только внутренней логики).
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


def _patch_common(monkeypatch, coins, zones, sweep=None):
    monkeypatch.setattr(bot, "get_all_coins", lambda: coins)
    monkeypatch.setattr(bot, "get_binance_ohlc", lambda sym, tf, n: [{"open": 1, "high": 1, "low": 1, "close": 1}] * 5)
    monkeypatch.setattr(ta_extra, "ema_context",
                         lambda c1, c4: {"tf_1h": {"stack": "bullish"}, "tf_4h": {"stack": "bullish"}})
    monkeypatch.setattr(ta_extra, "detect_sweep", lambda c: sweep)
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


_ZONES_WITH_STRUCTURE = {
    "below": [{"lo": 18.0, "hi": 19.0, "mid": 18.5}],
    "above": [{"lo": 25.0, "hi": 26.0, "mid": 25.5}, {"lo": 30.0, "hi": 31.0, "mid": 30.5}],
}


# ── ta_extra.freshest_sweep_direction() -- чистая функция ──

def test_freshest_sweep_direction_none_when_no_sweeps():
    assert ta_extra.freshest_sweep_direction(None, None) is None


def test_freshest_sweep_direction_short_for_sweep_high():
    sweep = {"type": "sweep_high", "level": 100, "bars_ago": 1, "volume_confirmed": True}
    assert ta_extra.freshest_sweep_direction(sweep, None) == "short"


def test_freshest_sweep_direction_long_for_sweep_low():
    sweep = {"type": "sweep_low", "level": 100, "bars_ago": 1, "volume_confirmed": True}
    assert ta_extra.freshest_sweep_direction(sweep, None) == "long"


def test_freshest_sweep_direction_ignores_stale_sweep():
    stale = {"type": "sweep_high", "level": 100, "bars_ago": ta_extra.FRESH_SWEEP_BARS + 50,
             "volume_confirmed": True}
    assert ta_extra.freshest_sweep_direction(stale, None) is None


def test_freshest_sweep_direction_picks_freshest_of_two():
    older_bull = {"type": "sweep_low", "level": 90, "bars_ago": 5, "volume_confirmed": True}
    fresher_bear = {"type": "sweep_high", "level": 110, "bars_ago": 1, "volume_confirmed": True}
    assert ta_extra.freshest_sweep_direction(older_bull, fresher_bear) == "short"


# ── X100_METHODOLOGY_BLACKLIST -- список ──

def test_blacklist_contains_bank_and_ake():
    assert bot.X100_METHODOLOGY_BLACKLIST == {"BANK", "AKE"}


# ── интеграция: cmd_x100_scanner() целиком ──

def test_x100_blacklisted_symbol_excluded_with_footer_line(monkeypatch):
    coins = [_coin("BANK", price=20.0), _coin("AVAX", price=20.0)]
    _patch_common(monkeypatch, coins, _ZONES_WITH_STRUCTURE)

    update = _FakeUpdate()
    asyncio.run(bot.cmd_x100_scanner(update, _FakeCtx()))

    text = update.message.texts[-1]
    assert "AVAX" in text
    # BANK не должен появиться как карточка-кандидат (может встретиться только
    # в самой футер-строке методологии)
    assert "BANK — BANK" not in text and "#1 BANK" not in text and "#2 BANK" not in text
    assert "Отфильтровано по методологии: 1 (BANK)" in text


def test_x100_ake_blacklisted_too(monkeypatch):
    coins = [_coin("AKE", price=0.001)]
    _patch_common(monkeypatch, coins, _ZONES_WITH_STRUCTURE)

    update = _FakeUpdate()
    asyncio.run(bot.cmd_x100_scanner(update, _FakeCtx()))

    text = update.message.texts[-1]
    assert "Отфильтровано по методологии: 1 (AKE)" in text


def test_x100_no_blacklist_note_when_nothing_filtered(monkeypatch):
    coins = [_coin("AVAX", price=20.0)]
    _patch_common(monkeypatch, coins, _ZONES_WITH_STRUCTURE)

    update = _FakeUpdate()
    asyncio.run(bot.cmd_x100_scanner(update, _FakeCtx()))

    text = update.message.texts[-1]
    assert "Отфильтровано по методологии" not in text


def test_x100_suppresses_card_on_bearish_sweep_contradiction(monkeypatch):
    """Живой кейс 'AKE #3': свежий sweep_high (манипуляция -- ликвидность в
    шорт) -- x100 не должен эмитить LONG-карточку для этого кандидата."""
    coin = _coin("VELO", price=20.0)
    bearish_sweep = {"type": "sweep_high", "level": 21.0, "bars_ago": 1, "volume_confirmed": True}
    _patch_common(monkeypatch, [coin], _ZONES_WITH_STRUCTURE, sweep=bearish_sweep)

    update = _FakeUpdate()
    asyncio.run(bot.cmd_x100_scanner(update, _FakeCtx()))

    text = update.message.texts[-1]
    assert "СПОТ" not in text  # карточка вообще не построена
    assert "отброшено (манипуляция против LONG)" in text


def test_x100_bullish_sweep_does_not_block_card(monkeypatch):
    """Регресс-замок: sweep_low ('ликвидность в лонг') НЕ противоречит LONG
    -- карточка строится как обычно."""
    coin = _coin("VELO", price=20.0)
    bullish_sweep = {"type": "sweep_low", "level": 19.0, "bars_ago": 1, "volume_confirmed": True}
    _patch_common(monkeypatch, [coin], _ZONES_WITH_STRUCTURE, sweep=bullish_sweep)

    update = _FakeUpdate()
    asyncio.run(bot.cmd_x100_scanner(update, _FakeCtx()))

    text = update.message.texts[-1]
    assert "VELO" in text
    assert "СПОТ" in text
