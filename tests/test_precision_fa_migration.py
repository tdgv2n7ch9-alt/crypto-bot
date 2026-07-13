"""
pytest для Пакета 15 (владелец "да" 2026-07-13, ENGINE_UNIFICATION.md): /precision
финалист-шаг (уже отобранные топ-5) переведён с real_full_analysis() на
fa_engine.build_full_analysis() -- ЕДИНЫЙ путь с /full и /coin. 500-монетный отбор
ОСТАЁТСЯ на дешёвом full_analysis() (Пакет 10 М3, стоимость) -- не тестируется здесь
заново, он не менялся.

Ключевое требование владельца (п.2 решения): строка "R:R" в карточке финалиста
обязана быть структурной ИЛИ честное "н/д" -- никогда фиксированный старый R:R 0.27.
Три исхода: fa_engine ok=False (живой кейс LOUZI, CoinGecko 429), ok=True но
has_setup=False (гейт/чеклист/K-LVL не пройден), ok=True has_setup=True (реальный
план сделки). Флаг PRECISION_FA_MIGRATED (default true) -- мгновенный откат на
real_full_analysis() без редеплоя.

Импорт bot.py требует BOT_TOKEN в окружении (модуль не подключается к Telegram при
импорте, только при main()).
"""
import asyncio
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot
import fa_engine


def _coin(symbol, rank=50, price=10.0, mcap=1_000_000_000, vol=50_000_000):
    return {
        "symbol": symbol, "slug": symbol.lower(), "cmc_rank": rank,
        "quote": {"USDT": {
            "price": price, "volume_24h": vol, "market_cap": mcap,
            "percent_change_1h": 1.0, "percent_change_24h": 2.0,
            "percent_change_7d": 3.0, "percent_change_30d": 4.0, "percent_change_90d": 5.0,
        }}
    }


FA_OK_SETUP = {
    "ok": True, "price": 10.5,
    "block1_bias": {"bias": "LONG"},
    "block11_trade_plan": {
        "has_setup": True, "direction": "long",
        "entry1": 10.0, "entry2": 9.8, "entry3": 9.6,
        "sl": 9.4, "tp1": 11.0, "tp2": 11.5, "tp3": 12.0,
        "rr_tp1": 2.5, "rr_tp2": 3.0, "rr_tp3": 4.0,
    },
    "candles_4h": [], "zones": {},
}

FA_OK_NO_SETUP = {
    "ok": True, "price": 10.5,
    "block1_bias": {"bias": "LONG"},
    "block11_trade_plan": {"has_setup": False, "reason": "чеклист 2/6 < 4: пример причины"},
    "candles_4h": [], "zones": {},
}

# Живой кейс LOUZI (Пакет 15, диф-отчёт 2026-07-13): CoinGecko 429 в момент фетча
# свечей fa_engine -- функция возвращает ok=False, не бросает исключение.
FA_FAIL_RATE_LIMIT = {"ok": False, "error": "нет данных по свечам (CoinGecko недоступен или лимит запросов)"}


# ── _precision_fields_from_fa_engine() -- чистый адаптер, юнит-тесты ───────────

def test_adapter_ok_false_gives_honest_na_not_old_fixed_rr():
    a_old = bot.full_analysis(_coin("LOUZI"))
    assert a_old["rr"] != 0  # старый движок реально даёт число (обычно ~0.27/0.31)
    a_new = bot._precision_fields_from_fa_engine(FA_FAIL_RATE_LIMIT, a_old)
    assert a_new["fa_engine_ok"] is False
    assert a_new["tp1"] is None and a_new["tp2"] is None and a_new["tp3"] is None
    assert a_new["sl"] is None
    assert a_new["rr"] is None
    assert "лимит" in a_new["rr_na_reason"] or "нет данных" in a_new["rr_na_reason"]
    # price/rank/vol/rsi/ch* -- честно сохранены от чистой quote-математики (не зависят
    # от того, упал ли fa_engine)
    assert a_new["price"] == a_old["price"]
    assert a_new["rank"] == a_old["rank"]


def test_adapter_ok_none_result_gives_honest_na():
    """fa_engine.build_full_analysis() бросил исключение выше по стеку -> None
    передан в адаптер (см. cmd_precision try/except) -- тоже честное н/д, не крэш."""
    a_old = bot.full_analysis(_coin("XYZ"))
    a_new = bot._precision_fields_from_fa_engine(None, a_old)
    assert a_new["fa_engine_ok"] is False
    assert a_new["rr"] is None
    assert a_new["rr_na_reason"]


def test_adapter_has_setup_false_gives_honest_na_with_reason():
    a_old = bot.full_analysis(_coin("SOL"))
    a_new = bot._precision_fields_from_fa_engine(FA_OK_NO_SETUP, a_old)
    assert a_new["fa_engine_ok"] is True
    assert a_new["tp1"] is None and a_new["sl"] is None and a_new["rr"] is None
    assert a_new["rr_na_reason"] == "чеклист 2/6 < 4: пример причины"
    # bias LONG однозначен -- is_long обновляется даже при has_setup=False
    assert a_new["is_long"] is True


def test_adapter_has_setup_true_gives_real_structural_values():
    a_old = bot.full_analysis(_coin("ETH"))
    a_new = bot._precision_fields_from_fa_engine(FA_OK_SETUP, a_old)
    assert a_new["fa_engine_ok"] is True
    assert a_new["tp1"] == 11.0 and a_new["tp2"] == 11.5 and a_new["tp3"] == 12.0
    assert a_new["sl"] == 9.4
    assert a_new["rr"] == 2.5
    assert a_new["rr_na_reason"] is None
    assert a_new["is_long"] is True
    assert a_new["price"] == 10.5  # обновлена от fa_engine, не осталась от a_old


def test_adapter_neutral_bias_keeps_old_direction_but_rr_still_na():
    """bias NEUTRAL -> has_setup всегда False (direction не определён) -- is_long
    не переключается (нет NEUTRAL-рендера у /precision карточки), но R:R всё
    равно честно н/д, не старое фиксированное число."""
    a_old = bot.full_analysis(_coin("BTC"))
    old_is_long = a_old["is_long"]
    fa_neutral = {
        "ok": True, "price": 62000.0,
        "block1_bias": {"bias": "NEUTRAL"},
        "block11_trade_plan": {"has_setup": False, "reason": "направление не определено (1D/4H конфликт)"},
        "candles_4h": [], "zones": {},
    }
    a_new = bot._precision_fields_from_fa_engine(fa_neutral, a_old)
    assert a_new["is_long"] == old_is_long
    assert a_new["rr"] is None
    assert "направление" in a_new["rr_na_reason"]


def test_adapter_preserves_scan_step_fields_untouched():
    """rank/vol/rsi_4h/ch1h-90d -- от старого дешёвого full_analysis()-скана
    (Пакет 10 М3, стоимость), НЕ должны меняться независимо от исхода fa_engine."""
    a_old = bot.full_analysis(_coin("DOGE", rank=9, vol=999_000_000))
    for fa_result in (FA_OK_SETUP, FA_OK_NO_SETUP, FA_FAIL_RATE_LIMIT, None):
        a_new = bot._precision_fields_from_fa_engine(fa_result, a_old)
        assert a_new["rank"] == a_old["rank"]
        assert a_new["vol"] == a_old["vol"]
        assert a_new["rsi_4h"] == a_old["rsi_4h"]
        assert a_new["ch1h"] == a_old["ch1h"]
        assert a_new["ch90d"] == a_old["ch90d"]


# ── grep-тест: финалист-шаг реально вызывает fa_engine, не deprecated-путь ─────

def test_cmd_precision_source_calls_fa_engine_under_flag():
    src = inspect.getsource(bot.cmd_precision)
    assert "PRECISION_FA_MIGRATED" in src
    assert "fa_engine.build_full_analysis(sym, coin)" in src
    assert "_precision_fields_from_fa_engine(" in src
    # деградационный путь на случай отката всё ещё существует (не удалён физически)
    assert "real_full_analysis(coin)" in src
    # 500-монетный скан по-прежнему на дешёвом full_analysis() (Пакет 10 М3, не
    # тронут этим пакетом)
    assert "full_analysis(coin)" in src


# ── end-to-end card build на фикстуре (мок Telegram + все сетевые вызовы) ──────

class _FakeMsg:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text, **kw):
        self.edits.append(text)


class _FakeReply:
    async def __call__(self, text, **kw):
        return _FakeMsg()


class _FakeMessage:
    def __init__(self):
        self.reply_text = _FakeReply()


class _FakeChat:
    id = 555


class _FakeBotObj:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(text)


class _FakeUpdate:
    def __init__(self):
        self.message = _FakeMessage()
        self.effective_chat = _FakeChat()


class _FakeCtx:
    def __init__(self):
        self.bot = _FakeBotObj()


def _run_precision_with_fixture(monkeypatch, fa_result, symbol="ETH"):
    """Гоняет реальный cmd_precision() с одним фиксированным финалистом и
    замоканными сетевыми вызовами -- возвращает список (symbol, a, text),
    захваченных из send_coin()."""
    coin = _coin(symbol)
    monkeypatch.setattr(bot, "get_top500", lambda: [coin])
    monkeypatch.setattr(bot, "precision_shot_analysis",
                         lambda c, a: {"type": "BREAKOUT", "ps": 80, "factors": ["тест-фактор"],
                                        "quality": "🟢 Высокое качество", "potential_x": 3.0})
    monkeypatch.setattr(bot, "get_supertrend_signal", lambda sym: {"label": ""})
    monkeypatch.setattr(bot, "get_binance_24h", lambda sym: None)
    monkeypatch.setattr(bot, "get_market_extras", lambda sym: None)

    captured = []

    async def _fake_send_coin(bot_obj, chat_id, sym, slug, a, text):
        captured.append((sym, a, text))

    monkeypatch.setattr(bot, "send_coin", _fake_send_coin)

    async def _fast_sleep(*a, **kw):
        return None

    monkeypatch.setattr(bot.asyncio, "sleep", _fast_sleep)

    def _fake_build_full_analysis(sym, c):
        if isinstance(fa_result, Exception):
            raise fa_result
        return fa_result

    monkeypatch.setattr(fa_engine, "build_full_analysis", _fake_build_full_analysis)

    update = _FakeUpdate()
    ctx = _FakeCtx()
    asyncio.run(bot.cmd_precision(update, ctx))
    return captured


def test_precision_card_has_setup_true_shows_real_rr(monkeypatch):
    monkeypatch.setattr(bot, "PRECISION_FA_MIGRATED", True)
    captured = _run_precision_with_fixture(monkeypatch, FA_OK_SETUP)
    assert len(captured) == 1
    sym, a, text = captured[0]
    assert sym == "ETH"
    assert "R:R `1:2.5`" in text
    assert "н/д" not in text.split("R:R")[1].split("|")[0]  # сама R:R-часть не "н/д"
    assert a["fa_engine_ok"] is True


def test_precision_card_has_setup_false_shows_honest_na_not_fixed_027(monkeypatch):
    monkeypatch.setattr(bot, "PRECISION_FA_MIGRATED", True)
    captured = _run_precision_with_fixture(monkeypatch, FA_OK_NO_SETUP)
    assert len(captured) == 1
    sym, a, text = captured[0]
    assert "R:R `н/д (чеклист 2/6 < 4: пример причины)`" in text
    assert "1:0.2" not in text and "1:0.3" not in text  # старый фиксированный R:R не проскочил
    assert "TP1:  `н/д`" in text


def test_precision_card_fa_engine_rate_limit_shows_na_not_crash(monkeypatch):
    """Живой кейс LOUZI (Пакет 15 диф-отчёт): fa_engine ok=False из-за 429 --
    карточка обязана честно написать н/д, не упасть молча."""
    monkeypatch.setattr(bot, "PRECISION_FA_MIGRATED", True)
    captured = _run_precision_with_fixture(monkeypatch, FA_FAIL_RATE_LIMIT, symbol="LOUZI")
    assert len(captured) == 1
    sym, a, text = captured[0]
    assert sym == "LOUZI"
    assert "R:R `н/д (" in text
    assert "лимит" in text or "нет данных" in text


def test_precision_card_fa_engine_exception_shows_na_not_crash(monkeypatch):
    """fa_engine.build_full_analysis() бросает исключение (не просто ok=False) --
    cmd_precision оборачивает try/except -> тот же честный н/д, никакого 500."""
    monkeypatch.setattr(bot, "PRECISION_FA_MIGRATED", True)
    captured = _run_precision_with_fixture(monkeypatch, RuntimeError("сетевой сбой"))
    assert len(captured) == 1
    sym, a, text = captured[0]
    assert "R:R `н/д (" in text


def test_precision_rollback_flag_uses_real_full_analysis(monkeypatch):
    """PRECISION_FA_MIGRATED=False -- мгновенный откат: fa_engine НЕ вызывается
    вообще, старый real_full_analysis() путь работает как раньше (Пакет 10 М3)."""
    monkeypatch.setattr(bot, "PRECISION_FA_MIGRATED", False)

    fa_calls = []

    def _fake_build_full_analysis(sym, c):
        fa_calls.append(sym)
        return FA_OK_SETUP

    monkeypatch.setattr(fa_engine, "build_full_analysis", _fake_build_full_analysis)

    real_calls = []

    def _fake_real_full_analysis(coin):
        real_calls.append(coin["symbol"])
        a = bot.full_analysis(coin)
        a["tp1"], a["tp2"], a["tp3"] = 20.0, 21.0, 22.0
        a["sl"] = 18.0
        a["rr"] = 1.8
        return a

    monkeypatch.setattr(bot, "real_full_analysis", _fake_real_full_analysis)

    captured = _run_precision_with_fixture(monkeypatch, FA_OK_SETUP)
    assert fa_calls == [], "fa_engine не должен вызываться при откате флага"
    assert real_calls == ["ETH"]
    sym, a, text = captured[0]
    assert "R:R `1:1.8`" in text
