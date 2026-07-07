"""
Смоук-тесты narrative.py (блок "Разбор"): 3 синтетических кейса (long-сетап, short-сетап,
нет сетапа) в форме result-словаря fa_engine.build_full_analysis().
Без pytest (в проекте его нет, см. requirements.txt) -- запуск напрямую:
    python3 test_narrative.py
Падает с AssertionError и ненулевым кодом возврата при любом провале.
"""

import narrative


def _synthetic_long_result():
    return {
        "ok": True, "symbol": "BTC", "price": 65000,
        "sweep_1h": {"type": "sweep_low", "bars_ago": 3, "volume_confirmed": True},
        "sweep_4h": None,
        "block3_smc": {"type": "BOS_bull", "aligned": True, "label": "BOS up"},
        "block4_poi": {"poi": [{"side": "below", "price": 64000, "distance_pct": -1.5,
                                "klvl": True, "sources": ["1d", "4h"],
                                "_zone": {"lo": 63800, "hi": 64200, "sources": ["1d", "4h"]}}]},
        "block11_trade_plan": {"ok": True, "has_setup": True, "direction": "long",
                               "entry1": 64200, "sl": 63500, "tp1": 66500, "rr_tp1": 1.8},
        "block5_checklist": {"ok": True, "score": 5},
    }


def _synthetic_short_result():
    return {
        "ok": True, "symbol": "ETH", "price": 3000,
        "sweep_1h": None, "sweep_4h": {"type": "sweep_high", "bars_ago": 2, "volume_confirmed": None},
        "block3_smc": {"type": "CHoCH_bear", "aligned": False, "label": "CHoCH"},
        "block4_poi": {"poi": [{"side": "above", "price": 3100, "distance_pct": 3.3,
                                "klvl": False, "sources": ["4h"],
                                "_zone": {"lo": 3080, "hi": 3120, "sources": ["4h"]}}]},
        "block11_trade_plan": {"ok": True, "has_setup": True, "direction": "short",
                               "entry1": 3080, "sl": 3150, "tp1": 2900, "rr_tp1": 2.0},
        "block5_checklist": {"ok": True, "score": 4},
    }


def _synthetic_no_setup_result():
    return {
        "ok": True, "symbol": "SOL", "price": 150,
        "sweep_1h": None, "sweep_4h": None,
        "block3_smc": {"type": None, "aligned": None, "label": "структура не определена"},
        "block4_poi": {"poi": []},
        "block11_trade_plan": {
            "ok": True, "has_setup": False,
            "wait_for": ("жду реакции от K-LVL 148 (пробой = сценарий вниз до 140, "
                        "удержание = продолжение вверх до 160)")},
        "block5_checklist": {"ok": True, "score": 2},
    }


def test_narrative_synthetic_cases():
    """3 синтетических кейса (long-сетап, short-сетап, нет сетапа) -- текст непустой,
    не содержит "None"/"nan", вставки годны под <b>Разбор</b>+HTML."""
    cases = {
        "long": _synthetic_long_result(),
        "short": _synthetic_short_result(),
        "no_setup": _synthetic_no_setup_result(),
    }
    for name, result in cases.items():
        text = narrative.build_narrative(result)
        assert text, f"{name}: narrative is empty"
        assert "None" not in text, f"{name}: narrative contains 'None': {text}"
        assert "nan" not in text.lower(), f"{name}: narrative contains 'nan': {text}"
        block = narrative.render_narrative_block(result)
        assert block.startswith("<b>Разбор</b>\n"), f"{name}: missing header: {block}"
        print(f"[OK] narrative[{name}]: {text}")

    # result не ok -- пустой блок, ничего вставлять не нужно
    assert narrative.build_narrative({"ok": False}) == ""
    assert narrative.render_narrative_block({"ok": False}) == ""
    # result None -- тоже безопасно
    assert narrative.build_narrative(None) == ""
    print("[OK] test_narrative_synthetic_cases")


if __name__ == "__main__":
    test_narrative_synthetic_cases()
    print("\nALL TESTS PASSED")
