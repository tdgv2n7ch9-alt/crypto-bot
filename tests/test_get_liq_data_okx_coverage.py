"""
pytest для bot.get_liq_data() -- #290 п.6 (владелец, 2026-07-18, живая находка
STARUSDT): OKX liquidation-orders для символа вне покрытия OKX (напр. STAR,
код "51014 Index doesn't exist") раньше падал НАСКВОЗЬ до res.update(...,
'ok': True) -- пустой data=[] выглядел неотличимо от честных "0 ликвидаций
найдено", и сырой текст ошибки OKX утекал в карточку как "причина" в
level_watch.format_liquidation_cluster_line(). Теперь -- честный ok=False +
not_covered=True, без похода за heatmap (той же биржи всё равно нет).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot
import level_watch as lw


class _FakeResponse:
    def __init__(self, status_code, json_data=None):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}

    def json(self):
        return self._json_data


def _zone(lo, hi, side="SHORT"):
    return {"lo": lo, "hi": hi, "side": side}


def test_get_liq_data_okx_index_doesnt_exist_gives_ok_false_not_covered(monkeypatch):
    """Живой ответ OKX для STAR-USDT (проверено #290 п.6, curl 2026-07-18):
    {"code":"51014","data":[],"msg":"Index doesn't exist."}"""
    bot._liq_cache.clear()

    def fake_get(url, params=None, timeout=None):
        if "instruments" in url:
            return _FakeResponse(200, {"data": []})
        if "liquidation-orders" in url:
            return _FakeResponse(200, {"code": "51014", "data": [], "msg": "Index doesn't exist."})
        raise AssertionError(f"unexpected OKX endpoint: {url}")

    monkeypatch.setattr(bot.requests, "get", fake_get)
    res = bot.get_liq_data("STAR")

    assert res["ok"] is False
    assert res["not_covered"] is True
    assert res["heatmap"] is None  # НЕ ходили за heatmap -- рынка всё равно нет
    assert "Index doesn't exist" in res["error"]  # сырой текст остаётся В ДАННЫХ (для диагностики), но НЕ утекает в карточку (см. тест ниже)


def test_get_liq_data_real_okx_success_still_sets_ok_true(monkeypatch):
    """Регресс-защита: успешный путь (code=='0') не сломан правкой -- ok=True,
    без not_covered."""
    bot._liq_cache.clear()

    def fake_get(url, params=None, timeout=None):
        if "instruments" in url:
            return _FakeResponse(200, {"data": [{"ctVal": "1"}]})
        if "liquidation-orders" in url:
            return _FakeResponse(200, {"code": "0", "data": [
                {"details": [{"sz": "10", "bkPx": "100", "side": "sell"}]}
            ]})
        if "market/ticker" in url:
            return _FakeResponse(200, {"data": [{"last": "100"}]})
        raise AssertionError(f"unexpected OKX endpoint: {url}")

    monkeypatch.setattr(bot.requests, "get", fake_get)
    res = bot.get_liq_data("BTC")

    assert res["ok"] is True
    assert res.get("not_covered") is None
    assert res["liq_short"] > 0


def test_format_liq_line_friendly_message_for_not_covered_symbol():
    def _not_covered(sym):
        return {"ok": False, "not_covered": True, "error": "Index doesn't exist."}

    line = lw.format_liquidation_cluster_line("STARUSDT", _zone(0.22, 0.23), get_liq_data_fn=_not_covered)
    assert "нет данных" in line
    assert "вне покрытия" in line
    assert "Bybit/OKX" in line
    assert "Index doesn't exist" not in line  # сырой OKX-текст больше НЕ утекает в карточку


def test_format_liq_line_generic_na_without_not_covered_flag():
    """Регресс-защита: ok=False БЕЗ not_covered (напр. сетевой сбой) -- прежнее
    общее сообщение, не новая формулировка."""
    def _generic_fail(sym):
        return {"ok": False}

    line = lw.format_liquidation_cluster_line("ETHUSDT", _zone(100, 110), get_liq_data_fn=_generic_fail)
    assert "нет данных биржи для этого символа" in line
    assert "вне покрытия" not in line
