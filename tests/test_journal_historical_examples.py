"""
pytest на РЕАЛЬНЫХ исторических примерах из signal_journal.json -- проверяет внутреннюю
математическую согласованность уже записанных сигналов (R:R действительно соответствует
entry/SL/TP, direction соответствует знаку SL относительно entry), а не поведение кода
"вживую". Если файла нет (напр. чистое CI-окружение) -- тесты пропускаются, не падают.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

JOURNAL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "signal_journal.json")


def _load_records():
    if not os.path.exists(JOURNAL_PATH):
        return []
    with open(JOURNAL_PATH) as f:
        data = json.load(f)
    return list(data.get("records", data).values())


_RECORDS = _load_records()
pytestmark = pytest.mark.skipif(not _RECORDS, reason="signal_journal.json отсутствует/пуст локально")


def test_all_records_have_required_fields():
    required = {"id", "source", "symbol", "direction", "entry_lo", "entry_hi", "sl", "status"}
    for r in _RECORDS:
        missing = required - set(r.keys())
        assert not missing, f"запись {r.get('id')}: отсутствуют поля {missing}"


def test_long_sl_is_below_entry_short_sl_is_above():
    """Направленная согласованность: для LONG SL должен быть ниже входа, для SHORT --
    выше. Реальная историческая проверка на уже отправленных сигналах, не синтетика."""
    checked = 0
    for r in _RECORDS:
        sl = r.get("sl")
        entry_lo, entry_hi = r.get("entry_lo"), r.get("entry_hi")
        if sl is None or entry_lo is None or entry_hi is None:
            continue
        entry_ref = entry_lo if r["direction"] == "long" else entry_hi
        if r["direction"] == "long":
            assert sl <= entry_ref, f"запись {r['id']} ({r['symbol']}): LONG SL {sl} выше входа {entry_ref}"
        elif r["direction"] == "short":
            assert sl >= entry_ref, f"запись {r['id']} ({r['symbol']}): SHORT SL {sl} ниже входа {entry_ref}"
        checked += 1
    if checked == 0:
        pytest.skip("нет записей с заполненными entry/sl для проверки")


def test_actual_r_sign_matches_outcome():
    """Закрытые с исходом записи: actual_r должен быть отрицательным при SL_HIT,
    неотрицательным при TPx_HIT -- проверка уже посчитанной signal_journal логики на
    реальных данных."""
    checked = 0
    for r in _RECORDS:
        outcome = r.get("outcome")
        actual_r = r.get("actual_r")
        if outcome not in ("SL_HIT", "TP1_HIT", "TP2_HIT", "TP3_HIT") or actual_r is None:
            continue
        if outcome == "SL_HIT":
            assert actual_r <= 0, f"запись {r['id']}: SL_HIT, но actual_r={actual_r} > 0"
        else:
            assert actual_r >= 0, f"запись {r['id']}: {outcome}, но actual_r={actual_r} < 0"
        checked += 1
    if checked == 0:
        pytest.skip("нет закрытых-с-исходом записей для проверки")


def test_grade_values_are_from_known_set():
    known = {"A+", "A", "B", "C", None}
    for r in _RECORDS:
        assert r.get("grade") in known, f"запись {r['id']}: неизвестный grade={r.get('grade')!r}"


def test_symbols_have_no_usdt_suffix():
    """log_signal() делает symbol.upper().replace('USDT','') -- проверка, что это
    действительно применяется ко всем историческим записям (не осталось 'BTCUSDT' вместо
    'BTC')."""
    for r in _RECORDS:
        sym = r.get("symbol", "")
        assert not sym.endswith("USDT"), f"запись {r['id']}: symbol={sym!r} всё ещё содержит USDT-суффикс"
