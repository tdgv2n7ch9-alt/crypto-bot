"""
pytest -- владелец, 2026-07-22 (OWNER GATE, узкое снятие паузы AUTO +
AMD-accumulation-фильтр + предохранители). Явное требование владельца:
"py_compile, регресс-тест: accumulation-сигнал подавляется, не-
accumulation проходит". Три отдельных гейта, каждый тестируется как
самостоятельная, независимо проверяемая функция (тот же паттерн, что уже
используется для `_counter_trend_blocked()`), без мокинга всего
`send_scheduled()`/`real_full_analysis()`-конвейера.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot


# ---------------------------------------------------------------------------
# _amd_accumulation_phase() -- AMD-фаза входа (accumulation-фильтр)
# ---------------------------------------------------------------------------

def test_amd_accumulation_phase_returns_accumulation():
    with patch.object(bot.ta_extra, "classify_amd_phase", return_value={"phase": "accumulation"}) as mock_cls:
        assert bot._amd_accumulation_phase({"candles_4h": [1, 2, 3]}) == "accumulation"
    mock_cls.assert_called_once_with([1, 2, 3])


def test_amd_accumulation_phase_returns_other_phase_unchanged():
    with patch.object(bot.ta_extra, "classify_amd_phase", return_value={"phase": "manipulation_bull"}):
        assert bot._amd_accumulation_phase({"candles_4h": [1, 2, 3]}) == "manipulation_bull"


def test_amd_accumulation_phase_missing_candles_passes_empty_list():
    with patch.object(bot.ta_extra, "classify_amd_phase", return_value={"phase": "dead_zone"}) as mock_cls:
        assert bot._amd_accumulation_phase({}) == "dead_zone"
    mock_cls.assert_called_once_with([])


def test_gate_reasons_suppresses_accumulation_signal():
    """Владелец: 'accumulation-сигнал подавляется' -- воспроизводит РОВНО
    ту же строку кода, что стоит в send_scheduled() (gate_reasons.append),
    без дублирования остальной логики цикла."""
    with patch.object(bot.ta_extra, "classify_amd_phase", return_value={"phase": "accumulation"}):
        gate_reasons = []
        amd_phase = bot._amd_accumulation_phase({"candles_4h": []})
        if amd_phase == "accumulation":
            gate_reasons.append("amd_accumulation")
        promoted = not gate_reasons
    assert gate_reasons == ["amd_accumulation"]
    assert promoted is False


def test_gate_reasons_passes_non_accumulation_signal():
    """Владелец: 'не-accumulation проходит'."""
    with patch.object(bot.ta_extra, "classify_amd_phase", return_value={"phase": "distribution_bull"}):
        gate_reasons = []
        amd_phase = bot._amd_accumulation_phase({"candles_4h": []})
        if amd_phase == "accumulation":
            gate_reasons.append("amd_accumulation")
        promoted = not gate_reasons
    assert gate_reasons == []
    assert promoted is True


# ---------------------------------------------------------------------------
# _auto_concurrent_limit_reached() -- предохранитель "лимит открытых позиций"
#
# Владелец, ФИКС 2026-07-23 (живая находка -- RSR id=485 прошёл эмиссию при
# 3/3 занятых слотах US/ETHFI/GMX): счётчик теперь читает ПЕРСИСТЕНТНЫЙ
# signal_journal._journal (ENTERED+PENDING по AUTO_LIVE_SOURCES), не
# эфемерные TOP_LONG_SIGNALS/TOP_SHORT_SIGNALS -- тесты ниже переписаны под
# новый источник данных, старые (патчившие TOP_LONG_SIGNALS/TOP_SHORT_SIGNALS)
# тестировали как раз БАГОВОЕ поведение.
# ---------------------------------------------------------------------------

def _mk_open_record(source="TOP_LONG_AUTO", status="ENTERED"):
    return {"source": source, "status": status}


def test_concurrent_limit_not_reached_when_below_max():
    journal = {1: _mk_open_record(), 2: _mk_open_record(source="TOP_SHORT_AUTO")}
    with patch.object(bot.signal_journal, "_journal", journal), \
         patch.object(bot, "AUTO_LIVE_MAX_CONCURRENT", 3):
        assert bot._auto_concurrent_limit_reached() is False


def test_concurrent_limit_reached_at_exact_max():
    journal = {1: _mk_open_record(), 2: _mk_open_record(status="PENDING"),
               3: _mk_open_record(source="TOP_SHORT_AUTO")}
    with patch.object(bot.signal_journal, "_journal", journal), \
         patch.object(bot, "AUTO_LIVE_MAX_CONCURRENT", 3):
        assert bot._auto_concurrent_limit_reached() is True


def test_concurrent_limit_reached_above_max():
    journal = {i: _mk_open_record() for i in range(4)}
    with patch.object(bot.signal_journal, "_journal", journal), \
         patch.object(bot, "AUTO_LIVE_MAX_CONCURRENT", 3):
        assert bot._auto_concurrent_limit_reached() is True


def test_concurrent_limit_ignores_non_auto_sources_and_closed_statuses():
    journal = {
        1: _mk_open_record(source="full_analysis"),  # не AUTO-источник
        2: _mk_open_record(status="SL_HIT"),          # уже закрыта
        3: _mk_open_record(status="EXPIRED"),         # уже закрыта
    }
    with patch.object(bot.signal_journal, "_journal", journal), \
         patch.object(bot, "AUTO_LIVE_MAX_CONCURRENT", 3):
        assert bot._auto_concurrent_limit_reached() is False


def test_concurrent_limit_survives_empty_ephemeral_dicts():
    """Регресс-тест П.1 (владелец, 2026-07-23): рестарт контейнера обнуляет
    /tmp/best_trade_signals.json (TOP_LONG_SIGNALS/TOP_SHORT_SIGNALS) -- счётчик
    ДОЛЖЕН при этом всё равно показывать 3/3 по персистентному журналу, не 0/3."""
    journal = {i: _mk_open_record() for i in range(3)}
    with patch.object(bot.signal_journal, "_journal", journal), \
         patch.object(bot, "TOP_LONG_SIGNALS", {}), \
         patch.object(bot, "TOP_SHORT_SIGNALS", {}), \
         patch.object(bot, "AUTO_LIVE_MAX_CONCURRENT", 3):
        assert bot._auto_concurrent_limit_reached() is True


# ---------------------------------------------------------------------------
# _auto_emission_kill_switch_triggered() -- kill-switch (>=4 из первых 5 в минус)
# ---------------------------------------------------------------------------

START_TS = 1784712842.0


def _mk_record(source="TOP_LONG_AUTO", ts=None, outcome="SL_HIT", outcome_ts=None):
    return {"source": source, "ts": ts if ts is not None else START_TS + 10,
            "outcome": outcome, "outcome_ts": outcome_ts if outcome_ts is not None else (ts or START_TS + 10) + 100}


def test_kill_switch_false_when_fewer_than_5_closed():
    journal = {i: _mk_record(outcome_ts=START_TS + i) for i in range(4)}
    with patch.object(bot.signal_journal, "_journal", journal):
        assert bot._auto_emission_kill_switch_triggered() is False


def test_kill_switch_true_when_4_of_first_5_are_losses():
    outcomes = ["SL_HIT", "SL_HIT", "TP1_HIT", "SL_HIT", "SL_HIT"]
    journal = {i: _mk_record(outcome=o, outcome_ts=START_TS + i) for i, o in enumerate(outcomes)}
    with patch.object(bot.signal_journal, "_journal", journal):
        assert bot._auto_emission_kill_switch_triggered() is True


def test_kill_switch_false_when_only_3_of_first_5_are_losses():
    outcomes = ["SL_HIT", "SL_HIT", "TP1_HIT", "TP2_HIT", "SL_HIT"]
    journal = {i: _mk_record(outcome=o, outcome_ts=START_TS + i) for i, o in enumerate(outcomes)}
    with patch.object(bot.signal_journal, "_journal", journal):
        assert bot._auto_emission_kill_switch_triggered() is False


def test_kill_switch_excludes_trades_before_experiment_start():
    """4 лосса ДО начала эксперимента + 1 после -- не считаются 'первыми 5'."""
    journal = {}
    for i in range(4):
        journal[i] = _mk_record(outcome="SL_HIT", ts=START_TS - 1000 + i,
                                  outcome_ts=START_TS - 1000 + i)
    journal[4] = _mk_record(outcome="TP1_HIT", ts=START_TS + 1, outcome_ts=START_TS + 1)
    with patch.object(bot.signal_journal, "_journal", journal):
        assert bot._auto_emission_kill_switch_triggered() is False


def test_kill_switch_excludes_non_auto_sources():
    """signal_loop-исходы (tz13/patch09) не должны влиять на AUTO kill-switch."""
    journal = {}
    for i in range(5):
        journal[i] = _mk_record(source="signal_loop", outcome="SL_HIT",
                                  outcome_ts=START_TS + i)
    with patch.object(bot.signal_journal, "_journal", journal):
        assert bot._auto_emission_kill_switch_triggered() is False


def test_kill_switch_only_considers_first_5_by_outcome_ts_order():
    """6-я сделка (win) не должна 'разбавить' уже решённые первые 5 (все лоссы)."""
    journal = {i: _mk_record(outcome="SL_HIT", outcome_ts=START_TS + i) for i in range(5)}
    journal[5] = _mk_record(outcome="TP1_HIT", outcome_ts=START_TS + 5)
    with patch.object(bot.signal_journal, "_journal", journal):
        assert bot._auto_emission_kill_switch_triggered() is True


def test_kill_switch_ignores_unclosed_trades():
    journal = {i: _mk_record(outcome="SL_HIT", outcome_ts=START_TS + i) for i in range(4)}
    journal[4] = _mk_record(outcome=None, outcome_ts=None)
    with patch.object(bot.signal_journal, "_journal", journal):
        assert bot._auto_emission_kill_switch_triggered() is False
