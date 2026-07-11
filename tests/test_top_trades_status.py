"""
pytest для bot.top_trades_long_status()/top_trades_short_status() -- чистые функции
статуса карточки "Монеты в работе", вынесенные при фиксе АПГРЕЙД 11.07 Этап 1
(карточка рассыпалась пустыми метками + BTC/SOL/ETH никогда не уходили в архив
после пробоя SL, потому что статус считался только для отображения, terminal_result
не существовал вовсе). Формулы порогов не менялись -- только вынесены в
тестируемые функции и получили terminal_result.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot


# ── top_trades_long_status ──

def test_long_tp3_hit_is_terminal():
    status, terminal = bot.top_trades_long_status(entry=100, cur=109, tp1=102, tp2=104, tp3=108, sl=85)
    assert terminal == "TP3"
    assert "TP3" in status


def test_long_tp2_hit_not_terminal():
    status, terminal = bot.top_trades_long_status(entry=100, cur=105, tp1=102, tp2=104, tp3=108, sl=85)
    assert terminal is None
    assert "TP2" in status


def test_long_tp1_hit_not_terminal():
    status, terminal = bot.top_trades_long_status(entry=100, cur=103, tp1=102, tp2=104, tp3=108, sl=85)
    assert terminal is None
    assert "TP1" in status


def test_long_in_profit_below_tp1():
    status, terminal = bot.top_trades_long_status(entry=100, cur=101, tp1=102, tp2=104, tp3=108, sl=85)
    assert terminal is None
    assert "плюсе" in status


def test_long_close_to_entry():
    status, terminal = bot.top_trades_long_status(entry=100, cur=99.5, tp1=102, tp2=104, tp3=108, sl=85)
    assert terminal is None
    assert "Близко" in status


def test_long_waiting_for_entry():
    status, terminal = bot.top_trades_long_status(entry=100, cur=97, tp1=102, tp2=104, tp3=108, sl=85)
    assert terminal is None
    assert "Ждём вход" in status


def test_long_sl_hit_is_terminal():
    status, terminal = bot.top_trades_long_status(entry=100, cur=84, tp1=102, tp2=104, tp3=108, sl=85)
    assert terminal == "SL"
    assert "SL" in status


def test_long_sl_tolerance_1pct():
    # sl*1.01 -- "почти пробит" тоже считается пробитым (та же формула, что была)
    status, terminal = bot.top_trades_long_status(entry=100, cur=85.5, tp1=102, tp2=104, tp3=108, sl=85)
    assert terminal == "SL"


# ── top_trades_short_status ──

def test_short_tp2_hit_is_terminal():
    status, terminal = bot.top_trades_short_status(entry=100, cur=95, tp1=98, tp2=96, sl=115)
    assert terminal == "TP2"
    assert "TP2" in status


def test_short_tp1_hit_not_terminal():
    status, terminal = bot.top_trades_short_status(entry=100, cur=97, tp1=98, tp2=96, sl=115)
    assert terminal is None
    assert "TP1" in status


def test_short_in_profit_above_tp1():
    status, terminal = bot.top_trades_short_status(entry=100, cur=99, tp1=98, tp2=96, sl=115)
    assert terminal is None
    assert "плюсе" in status


def test_short_close_to_entry():
    status, terminal = bot.top_trades_short_status(entry=100, cur=100.5, tp1=98, tp2=96, sl=115)
    assert terminal is None
    assert "Близко" in status


def test_short_waiting_for_entry():
    status, terminal = bot.top_trades_short_status(entry=100, cur=103, tp1=98, tp2=96, sl=115)
    assert terminal is None
    assert "Ждём вход" in status


def test_short_sl_hit_is_terminal():
    status, terminal = bot.top_trades_short_status(entry=100, cur=116, tp1=98, tp2=96, sl=115)
    assert terminal == "SL"
    assert "SL" in status


def test_short_sl_tolerance_1pct():
    # sl*0.99 -- "почти пробит" тоже считается пробитым (та же формула, что была)
    status, terminal = bot.top_trades_short_status(entry=100, cur=113.9, tp1=98, tp2=96, sl=115)
    assert terminal == "SL"
