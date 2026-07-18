"""
pytest для card_format.py (владелец, П-Визуал v2, задачи #207/#208).

#207 (форматирование цен по тику): decimals-from-tick, честный fallback при
отказе источника тика, кэш get_tick_size, приоритет tick_size > symbol-lookup
> магнитудная эвристика, регресс-замок на "микрокап округлился до 0" (та же
находка, что в card_v2.default_price_fmt).

#208 (единый шаблон 5 блоков): каждый format_*_block() -- по точной
спецификации владельца 2026-07-16 (см. card_format.py докстринг блоков),
assemble_card() -- сборка через SEP между блоками, футер без SEP.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import card_format as cf


def test_decimals_from_tick_basic_cases():
    assert cf._decimals_from_tick(0.10) == 1
    assert cf._decimals_from_tick(0.01) == 2
    assert cf._decimals_from_tick(0.000001) == 6
    assert cf._decimals_from_tick(1.0) == 0
    assert cf._decimals_from_tick(25.0) == 0


def test_decimals_from_tick_honest_default_on_invalid_input():
    assert cf._decimals_from_tick(None) == 2
    assert cf._decimals_from_tick(0) == 2
    assert cf._decimals_from_tick(-1) == 2


def test_fetch_tick_size_returns_none_on_network_failure(monkeypatch):
    import requests
    def fake_get(*a, **k):
        raise requests.exceptions.ConnectionError("boom")
    monkeypatch.setattr(cf.requests, "get", fake_get)
    assert cf.fetch_tick_size("BTCUSDT") is None


def test_fetch_tick_size_returns_none_on_empty_list(monkeypatch):
    class FakeResp:
        def json(self):
            return {"result": {"list": []}}
    monkeypatch.setattr(cf.requests, "get", lambda *a, **k: FakeResp())
    assert cf.fetch_tick_size("NOTREAL") is None


def test_fetch_tick_size_parses_price_filter(monkeypatch):
    class FakeResp:
        def json(self):
            return {"result": {"list": [{"priceFilter": {"tickSize": "0.0001"}}]}}
    monkeypatch.setattr(cf.requests, "get", lambda *a, **k: FakeResp())
    assert cf.fetch_tick_size("AKEUSDT") == 0.0001


def test_get_tick_size_uses_cache_within_ttl(monkeypatch):
    cf._TICK_CACHE.clear()
    calls = {"n": 0}
    def fake_fetch(symbol):
        calls["n"] += 1
        return 0.01
    v1 = cf.get_tick_size("BTCUSDT", fetch_fn=fake_fetch)
    v2 = cf.get_tick_size("BTCUSDT", fetch_fn=fake_fetch)
    assert v1 == v2 == 0.01
    assert calls["n"] == 1  # второй вызов -- из кэша


def test_get_tick_size_refetches_after_ttl_expiry(monkeypatch):
    cf._TICK_CACHE.clear()
    calls = {"n": 0}
    def fake_fetch(symbol):
        calls["n"] += 1
        return 0.01
    cf.get_tick_size("ETHUSDT", fetch_fn=fake_fetch)
    cf._TICK_CACHE["ETHUSDT"] = (0.01, time.time() - cf.TICK_CACHE_TTL_SEC - 1)
    cf.get_tick_size("ETHUSDT", fetch_fn=fake_fetch)
    assert calls["n"] == 2


def test_get_tick_size_does_not_cache_honest_none(monkeypatch):
    cf._TICK_CACHE.clear()
    calls = {"n": 0}
    def fake_fetch(symbol):
        calls["n"] += 1
        return None
    cf.get_tick_size("DEADUSDT", fetch_fn=fake_fetch)
    cf.get_tick_size("DEADUSDT", fetch_fn=fake_fetch)
    assert calls["n"] == 2  # честный None не кэшируется -- следующий тик пробует снова


def test_format_price_explicit_tick_size_wins_over_symbol_lookup():
    text = cf.format_price(1.23456, symbol="BTCUSDT", tick_size=0.0001,
                            get_tick_size_fn=lambda s: 1.0)  # был бы 0 знаков
    assert text == "1.2346"


def test_format_price_uses_symbol_lookup_when_no_explicit_tick():
    text = cf.format_price(0.0007123, symbol="AKEUSDT",
                            get_tick_size_fn=lambda s: 0.0000001)
    assert text == "0.0007123"


def test_format_price_falls_back_to_magnitude_when_tick_unavailable():
    text = cf.format_price(0.0007123, symbol="UNKNOWNUSDT",
                            get_tick_size_fn=lambda s: None)
    assert text == "0.00071230"  # магнитудная эвристика: 0.01 > v -> 8 знаков


def test_format_price_no_symbol_no_tick_uses_magnitude_heuristic():
    assert cf.format_price(50000.5) == "50,000.50"
    assert cf.format_price(6.681) == "6.6810"


def test_format_price_regression_microcap_does_not_round_to_zero():
    """Владелец, находка при сборке card_v2 (см. card_v2.default_price_fmt
    докстринг) -- фиксированный .0f округлял микрокапы вида $0.0120 до "0".
    card_format.py -- единая точка правды, тот же класс регресса не должен
    повториться здесь."""
    text = cf.format_price(0.0120)
    assert text != "0"
    assert "12" in text


def test_format_price_thousands_separator_present_for_large_values():
    assert "," in cf.format_price(123456.78)


# ── #208: единый шаблон 5 блоков (спецификация владельца 2026-07-16) ────

def test_format_header_block_long_with_rocket_and_setup():
    lines = cf.format_header_block("long", "SOLUSDT", "15m", rocket_score=78,
                                    setup_type="AMD-разворот")
    assert lines[0] == "🟢 LONG SOLUSDT | 15m | 🚀 78/100"
    assert lines[1] == "AMD-разворот"


def test_format_header_block_short_no_rocket_no_setup():
    lines = cf.format_header_block("short", "BTCUSDT", "1H")
    assert lines == ["🔴 SHORT BTCUSDT | 1H"]


def test_compute_avg_entry_dca_weighted():
    avg = cf.compute_avg_entry([(30000, 50), (29500, 30), (29000, 20)])
    assert avg == 29650.0


def test_compute_avg_entry_single():
    assert cf.compute_avg_entry([(100, 100)]) == 100


def test_format_entry_block_dca_three_lines_plus_average():
    lines = cf.format_entry_block([(30000, 50), (29500, 30), (29000, 20)])
    assert lines[0] == "📍 ВХОД"
    assert "50%:" in lines[1] and cf.format_price(30000) in lines[1]
    assert "30%:" in lines[2] and cf.format_price(29500) in lines[2]
    assert "20%:" in lines[3] and cf.format_price(29000) in lines[3]
    assert lines[4] == f"Средняя: {cf.format_price(29650.0)}"


def test_format_entry_block_single_trigger_with_condition():
    lines = cf.format_entry_block([(0.0007, 100)],
                                   single_condition_note="после закрепа ниже 0.0007")
    assert lines == ["📍 ВХОД", f"{cf.format_price(0.0007)} -- после закрепа ниже 0.0007"]


def test_format_sl_block_includes_pct_and_reason():
    lines = cf.format_sl_block(28000, 29650.0, "за структурой")
    assert "🛑 SL" in lines[0]
    assert cf.format_price(28000) in lines[0]
    risk_pct = abs(28000 - 29650.0) / 29650.0 * 100
    assert f"{risk_pct:.1f}%" in lines[0]
    assert lines[1] == "  за структурой"


def test_format_targets_block_sorts_by_distance_and_shows_rr():
    """Владелец, находка #211 -- защита на уровне рендера: список ЦЕЛЕЙ на
    входе намеренно НЕ по порядку (дальняя цель первая) -- функция обязана
    пересортировать по возрастанию удалённости от входа."""
    tps = [
        {"price": 32000, "rr": 3.0},  # дальше
        {"price": 30500, "rr": 1.2},  # ближе -- минимальный R:R
    ]
    lines = cf.format_targets_block(tps, avg_entry=29650.0)
    assert lines[0] == "🎯 ЦЕЛИ"
    assert lines[1].startswith("TP1:") and cf.format_price(30500) in lines[1]
    assert lines[2].startswith("TP2:") and cf.format_price(32000) in lines[2]
    assert "R:R min: 1.2" in lines[-1]
    assert "⚠️" in lines[-1]  # 1.2 < RR_GATE (ta_extra.SR_MIN_RR_TP1 == 1.5)


def test_format_targets_block_no_warning_when_above_gate():
    tps = [{"price": 31000, "rr": 2.0}]
    lines = cf.format_targets_block(tps, avg_entry=29650.0)
    assert "⚠️" not in lines[-1]
    assert "R:R min: 2.0" in lines[-1]


def test_format_risk_block_shows_deposit_pcts_and_markers():
    lines = cf.format_risk_block({1: 10.0, 2: 20.0, 3: 30.0}, leverage_note="Плечо: до 5x",
                                  warning_markers=["🩸 ТОНКИЙ СТАКАН"])
    assert lines[0] == "💰 РИСК"
    assert "1%: $10.00" in lines[1]
    assert "2%: $20.00" in lines[2]
    assert "3%: $30.00" in lines[3]
    assert "Плечо: до 5x" in lines
    assert "🩸 ТОНКИЙ СТАКАН" in lines


def test_format_scalp_line():
    assert cf.format_scalp_line(4) == "⚡ Скальп 4/6"


def test_format_footer_basic():
    lines = cf.format_footer(5, "SOLUSDT", btc_context_line="BTC: восходящий тренд")
    assert lines[0] == "Kira|ICT чеклист 5/6"
    assert "BTC: восходящий тренд" in lines
    assert lines[-1] == "#SOLUSDT"
    assert "⚠️ контртренд" not in lines


def test_format_footer_counter_trend_marker():
    lines = cf.format_footer(4, "AKEUSDT", counter_trend=True)
    assert "⚠️ контртренд" in lines


def test_assemble_card_blocks_separated_by_sep_footer_not_separated():
    text = cf.assemble_card(["H1"], ["E1"], ["S1"], ["T1"], ["R1"], ["F1", "#SYM"])
    parts = text.split(f"\n{cf.SEP}\n")
    assert len(parts) == 5  # 5 блоков разделены SEP
    assert parts[-1].startswith("R1")
    assert "F1" in parts[-1]  # футер приклеен к последнему блоку БЕЗ своего SEP
    assert "#SYM" in parts[-1]


def test_assemble_card_includes_scalp_line_between_block5_and_footer():
    text = cf.assemble_card(["H"], ["E"], ["S"], ["T"], ["R"], ["F"],
                             scalp_line="⚡ Скальп 4/6")
    assert text.endswith("R\n⚡ Скальп 4/6\nF")


def test_assemble_compact_card_has_entry_sl_tp1_and_header():
    text = cf.assemble_compact_card(["🟢 LONG SOLUSDT | 15m"], 29650.0, 28000, 31000)
    assert "🟢 LONG SOLUSDT | 15m" in text
    assert cf.format_price(29650.0) in text
    assert cf.format_price(28000) in text
    assert cf.format_price(31000) in text
