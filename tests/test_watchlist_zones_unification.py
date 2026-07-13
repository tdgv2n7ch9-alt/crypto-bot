"""
pytest для bot.check_watchlist_alerts_from_level_watch() -- "пятый движок"
унификация (владелец, 2026-07-13, см. ENGINE_UNIFICATION.md): zone-touch
алерты подписчикам теперь берут границы из journal/watch_zones.json
(level_watch.load_watch_zones()), а не из хардкода WATCHLIST_ZONES. Импорт
bot.py требует BOT_TOKEN в окружении (модуль не подключается к Telegram при
импорте, только при main()).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot
import level_watch


def _coin(symbol, price):
    return {"symbol": symbol, "quote": {"USDT": {"price": price}}}


def test_reads_boundaries_from_watch_zones_json_not_hardcode(monkeypatch, tmp_path):
    """Ключевой тест унификации: даже если WATCHLIST_ZONES говорит одно,
    новая функция берёт границы ИСКЛЮЧИТЕЛЬНО из watch_zones.json."""
    cfg = {
        "updated": "2026-07-13", "source": "Королев 13.07",
        "BTCUSDT": [{"side": "LONG", "lo": 61840.9, "hi": 62285.0, "note": "тест"}],
    }
    monkeypatch.setattr(level_watch, "load_watch_zones", lambda *a, **kw: cfg)
    coins = [_coin("BTC", 62000.0)]  # внутри новой зоны, вне старой WATCHLIST_ZONES (62000-63000 была до фикса)
    alerts = bot.check_watchlist_alerts_from_level_watch(coins)
    assert len(alerts) == 1
    assert alerts[0]["lo"] == 61840.9
    assert alerts[0]["hi"] == 62285.0
    assert alerts[0]["symbol"] == "BTC"
    assert alerts[0]["bias"] == "LONG"


def test_price_outside_zone_no_alert(monkeypatch):
    cfg = {"updated": "d", "source": "s",
           "BTCUSDT": [{"side": "LONG", "lo": 61840.9, "hi": 62285.0}]}
    monkeypatch.setattr(level_watch, "load_watch_zones", lambda *a, **kw: cfg)
    coins = [_coin("BTC", 70000.0)]
    assert bot.check_watchlist_alerts_from_level_watch(coins) == []


def test_info_zones_skipped_not_alerted(monkeypatch):
    cfg = {"updated": "d", "source": "s",
           "BTCUSDT": [{"side": "INFO", "lo": 63239.3, "hi": 63239.3, "note": "broken"}]}
    monkeypatch.setattr(level_watch, "load_watch_zones", lambda *a, **kw: cfg)
    coins = [_coin("BTC", 63239.3)]
    assert bot.check_watchlist_alerts_from_level_watch(coins) == []


def test_multiple_zones_per_symbol_only_matching_one_alerts(monkeypatch):
    cfg = {"updated": "d", "source": "s", "BTCUSDT": [
        {"side": "LONG", "lo": 61840.9, "hi": 62285.0, "note": "zone1"},
        {"side": "SHORT", "lo": 66925.0, "hi": 67130.9, "note": "zone2"},
    ]}
    monkeypatch.setattr(level_watch, "load_watch_zones", lambda *a, **kw: cfg)
    coins = [_coin("BTC", 62000.0)]
    alerts = bot.check_watchlist_alerts_from_level_watch(coins)
    assert len(alerts) == 1
    assert alerts[0]["note"] == "zone1"


def test_symbol_not_in_coins_skipped_gracefully(monkeypatch):
    cfg = {"updated": "d", "source": "s",
           "NOSUCHUSDT": [{"side": "LONG", "lo": 1, "hi": 2}]}
    monkeypatch.setattr(level_watch, "load_watch_zones", lambda *a, **kw: cfg)
    assert bot.check_watchlist_alerts_from_level_watch([_coin("BTC", 62000.0)]) == []


def test_meta_keys_updated_source_not_treated_as_symbols(monkeypatch):
    cfg = {"updated": "2026-07-13", "source": "Королев 13.07",
           "BTCUSDT": [{"side": "LONG", "lo": 61840.9, "hi": 62285.0}]}
    monkeypatch.setattr(level_watch, "load_watch_zones", lambda *a, **kw: cfg)
    alerts = bot.check_watchlist_alerts_from_level_watch([_coin("BTC", 62000.0)])
    assert all(a["symbol"] != "updated" and a["symbol"] != "source" for a in alerts)


def test_source_field_propagated_from_config(monkeypatch):
    cfg = {"updated": "2026-07-13", "source": "Королев 13.07",
           "BTCUSDT": [{"side": "LONG", "lo": 61840.9, "hi": 62285.0}]}
    monkeypatch.setattr(level_watch, "load_watch_zones", lambda *a, **kw: cfg)
    alerts = bot.check_watchlist_alerts_from_level_watch([_coin("BTC", 62000.0)])
    assert alerts[0]["source"] == "Королев 13.07"


def test_check_watchlist_always_calls_unified_path(monkeypatch):
    """Пакет 18, п.1 (владелец): диагностика подтвердила живой alert BTC
    13.07 20:49 шёл через unified-путь (ZONES_UNIFIED=true на Railway, без
    override) -- LEGACY-ветка (check_watchlist_alerts() на WATCHLIST_ZONES)
    и сам флаг ZONES_UNIFIED удалены из check_watchlist(), функция теперь
    БЕЗУСЛОВНО вызывает check_watchlist_alerts_from_level_watch(). Откат
    через переменную окружения больше недоступен -- сознательное решение
    владельца после подтверждённой обкатки, не случайная потеря отката."""
    called = {"new": False}
    monkeypatch.setattr(bot, "check_watchlist_alerts_from_level_watch",
                         lambda coins: called.__setitem__("new", True) or [])
    monkeypatch.setattr(bot, "watchlist_alerted", {})
    assert not hasattr(bot, "check_watchlist_alerts"), \
        "check_watchlist_alerts() (LEGACY) должна быть физически удалена, не просто неиспользуема"
    assert not hasattr(bot, "ZONES_UNIFIED"), \
        "ZONES_UNIFIED-флаг должен быть удалён вместе с LEGACY-веткой, не оставлен мёртвым"

    import asyncio

    class _FakeBot:
        async def send_message(self, *a, **kw):
            pass

    asyncio.run(bot.check_watchlist(_FakeBot(), {123}, []))
    assert called["new"] is True


class _FakeUser:
    def __init__(self, uid):
        self.id = uid


class _FakeReply:
    def __init__(self):
        self.calls = []

    async def __call__(self, text, **kw):
        self.calls.append(text)


class _FakeMessage:
    def __init__(self, text):
        self.text = text
        self.reply_text = _FakeReply()


class _FakeZonesSetUpdate:
    def __init__(self, uid, text):
        self.effective_user = _FakeUser(uid)
        self.message = _FakeMessage(text)


def test_zones_set_accepts_brand_new_symbol_not_only_updates_existing(monkeypatch):
    """Владелец (2026-07-13, п.3 Задачи D): /zones_set должен принимать символ,
    которого ещё нет в watch_zones.json -- создание, не только апдейт (нужно для
    AVAX/AAVE, чьи старые границы из WATCHLIST_ZONES решено не переносить как
    протухшие). replace_watch_zones() -- полный реплейс присланного JSON
    (см. test_level_watch.py::test_replace_watch_zones_is_full_replace_not_merge),
    поэтому у cmd_zones_set нет отдельного пути "апдейт существующего символа" --
    любой ключ в присланном JSON, старый или новый, просто становится частью
    активного конфига. Тест проверяет это на реальном хендлере команды, не только
    на нижнем уровне level_watch."""
    import asyncio
    import os as _os

    _os.environ.setdefault("OWNER_CHAT_ID", "7009350191")
    owner_id = int(_os.getenv("OWNER_CHAT_ID", "7009350191"))

    captured = {}

    def _fake_replace(new_config, *a, **kw):
        captured["config"] = new_config
        return True

    async def _fake_github_sync(config):
        return False  # best-effort, не критично для теста

    monkeypatch.setattr(level_watch, "replace_watch_zones", _fake_replace)
    monkeypatch.setattr(level_watch, "sync_watch_zones_to_github", _fake_github_sync)

    # AVAXUSDT ранее НЕ было в конфиге -- проверяем именно создание нового символа
    payload = (
        '{"updated":"2026-07-13","source":"владелец /zones_set",'
        '"AVAXUSDT":[{"side":"LONG","lo":18.5,"hi":19.2,"prio":1}]}'
    )
    update = _FakeZonesSetUpdate(owner_id, f"/zones_set {payload}")

    asyncio.run(bot.cmd_zones_set(update, None))

    assert "config" in captured, "replace_watch_zones не был вызван"
    assert "AVAXUSDT" in captured["config"]
    assert captured["config"]["AVAXUSDT"][0]["lo"] == 18.5
    assert captured["config"]["AVAXUSDT"][0]["hi"] == 19.2
    assert any("✅" in c for c in update.message.reply_text.calls)


def test_zones_set_style_config_change_reflected_without_restart(monkeypatch):
    """load_watch_zones() читает диск на КАЖДЫЙ вызов (см. level_watch.py) --
    /zones_set-подобное изменение подхватывается немедленно, без рестарта
    процесса. Тест эмулирует это через monkeypatch двух последовательных
    конфигов на одном and том же объекте-функции."""
    state = {"cfg": {"updated": "d1", "source": "s1",
                      "BTCUSDT": [{"side": "LONG", "lo": 100, "hi": 200}]}}
    monkeypatch.setattr(level_watch, "load_watch_zones", lambda *a, **kw: state["cfg"])

    coins = [_coin("BTC", 150.0)]
    alerts_before = bot.check_watchlist_alerts_from_level_watch(coins)
    assert len(alerts_before) == 1
    assert alerts_before[0]["lo"] == 100

    # эмулируем /zones_set -- конфиг поменялся "на диске"
    state["cfg"] = {"updated": "d2", "source": "s2",
                     "BTCUSDT": [{"side": "LONG", "lo": 140, "hi": 160}]}
    alerts_after = bot.check_watchlist_alerts_from_level_watch(coins)
    assert len(alerts_after) == 1
    assert alerts_after[0]["lo"] == 140


def test_format_watchlist_rug_line_shows_line_at_or_above_warn_threshold(monkeypatch):
    """Владелец, 2026-07-13 (кейс LAB): score >= rug_radar.RUG_RISK_WARN_THRESHOLD
    (40) -> строка "🛑 RUG-RADAR: {score} — {детекторы}" в алерте."""
    monkeypatch.setattr(bot, "_cg_get", lambda *a, **kw: {})
    monkeypatch.setattr(bot.rug_radar, "compute_rug_risk",
                         lambda *a, **kw: {"score": 45, "reasons": ["навес разлоков", "тонкий объём"]})
    line = bot.format_watchlist_rug_line("LAB", _coin("LAB", 0.21))
    assert line == "🛑 RUG-RADAR: 45 — навес разлоков; тонкий объём"


def test_format_watchlist_rug_line_empty_below_warn_threshold(monkeypatch):
    monkeypatch.setattr(bot, "_cg_get", lambda *a, **kw: {})
    monkeypatch.setattr(bot.rug_radar, "compute_rug_risk",
                         lambda *a, **kw: {"score": 10, "reasons": []})
    assert bot.format_watchlist_rug_line("BTC", _coin("BTC", 62000.0)) == ""


def test_format_watchlist_rug_line_silent_empty_on_fetch_error(monkeypatch):
    """Best-effort: сеть/CoinGecko падают -> тихая пустая строка, алерт не ломается."""
    def _boom(*a, **kw):
        raise RuntimeError("coingecko down")
    monkeypatch.setattr(bot, "_cg_get", _boom)
    assert bot.format_watchlist_rug_line("XYZ", _coin("XYZ", 1.0)) == ""


def test_check_watchlist_alert_includes_rug_line_for_lab_not_for_btc(monkeypatch):
    """Золотой тестовый кейс владельца, 2026-07-13: LAB -- первый символ сразу
    с активной зоной И WARN rug-скором. Алерт по LAB содержит rug-строку,
    алерт по BTC -- нет."""
    import asyncio

    cfg = {
        "updated": "2026-07-13", "source": "Королев 13.07",
        "LABUSDT": [{"side": "LONG", "lo": 0.2006, "hi": 0.2167, "note": "HIGH RISK"}],
        "BTCUSDT": [{"side": "LONG", "lo": 61840.9, "hi": 62285.0, "note": "поддержка"}],
    }
    monkeypatch.setattr(level_watch, "load_watch_zones", lambda *a, **kw: cfg)
    monkeypatch.setattr(bot, "watchlist_alerted", {})
    monkeypatch.setattr(level_watch, "format_liquidation_cluster_line",
                         lambda *a, **kw: "🗺 Ликвидации рядом: н/д")
    monkeypatch.setattr(bot, "_cg_get", lambda *a, **kw: {})

    def _fake_rug(symbol, coin, cg_detail=None, **kw):
        if symbol == "LAB":
            return {"score": 45, "reasons": ["rug-score 45 WARN"]}
        return {"score": 5, "reasons": []}

    monkeypatch.setattr(bot.rug_radar, "compute_rug_risk", _fake_rug)

    sent = []

    class _FakeBot2:
        async def send_message(self, chat_id, text, **kw):
            sent.append(text)

    coins = [_coin("LAB", 0.21), _coin("BTC", 62000.0)]
    asyncio.run(bot.check_watchlist(_FakeBot2(), {123}, coins))

    lab_text = next(t for t in sent if "LABUSDT" in t)
    btc_text = next(t for t in sent if "BTCUSDT" in t)
    assert "RUG-RADAR" in lab_text
    assert "RUG-RADAR" not in btc_text


def test_zones_set_malformed_json_reply_includes_syntax_hint(monkeypatch):
    """Владелец, 2026-07-13 (Задача D п.3, хвост): подсказка синтаксиса не только
    на "нет аргументов", но и на реальную ошибку разбора -- с телефона после
    опечатки в JSON должен сразу увидеть пример правильной команды, не только
    голое исключение json.loads()."""
    import asyncio
    import os as _os

    _os.environ.setdefault("OWNER_CHAT_ID", "7009350191")
    owner_id = int(_os.getenv("OWNER_CHAT_ID", "7009350191"))

    update = _FakeZonesSetUpdate(owner_id, '/zones_set {"broken json,,,')
    asyncio.run(bot.cmd_zones_set(update, None))

    replies = update.message.reply_text.calls
    assert len(replies) == 1
    assert "Не удалось разобрать JSON" in replies[0]
    assert "Использование:" in replies[0]
    assert "/zones_set" in replies[0]


def test_zones_set_non_dict_json_reply_includes_syntax_hint(monkeypatch):
    """Валидный JSON, но не объект верхнего уровня (например, список) -- тоже
    ошибка формата, тоже должна показывать подсказку синтаксиса."""
    import asyncio
    import os as _os

    _os.environ.setdefault("OWNER_CHAT_ID", "7009350191")
    owner_id = int(_os.getenv("OWNER_CHAT_ID", "7009350191"))

    update = _FakeZonesSetUpdate(owner_id, '/zones_set ["not", "a", "dict"]')
    asyncio.run(bot.cmd_zones_set(update, None))

    replies = update.message.reply_text.calls
    assert len(replies) == 1
    assert "Ожидался JSON-объект" in replies[0]
    assert "Использование:" in replies[0]


def test_zones_set_usage_hint_warns_full_replace_not_merge():
    """Подсказка явно предупреждает про full-replace семантику -- см.
    level_watch.replace_watch_zones docstring и
    test_level_watch.py::test_replace_watch_zones_is_full_replace_not_merge.
    Без этого предупреждения владелец мог бы случайно стереть остальные
    символы, прислав /zones_set только с одним новым тикером."""
    hint = bot._zones_set_usage_hint()
    assert "ЗАМЕНЯЕТ" in hint
    assert "не дописывает" in hint
