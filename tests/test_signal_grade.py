"""
pytest для bot._signal_grade() -- чистая 5-факторная функция грейда (A+/A/B/C),
используется и в чек-листе сканов, и в тексте карточки. Импорт bot.py требует
BOT_TOKEN в окружении (не обязательно валидного -- модуль не подключается к Telegram
при импорте, только при main()).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot


def test_grade_a_plus_long_all_5_factors():
    """st_label matching требует буквально 'UP'/'BULL' подстроку (не просто 'BUY') --
    сама проверка кода нашла эту тонкость, тест был неверным с первой попытки, не код."""
    a = {"rsi_4h": 40, "macd_bullish": True, "trend_4h": "bullish",
         "st_label": "TREND UP", "above_ema200": True, "above_ema50": True}
    assert bot._signal_grade(a, is_long=True) == "A+"


def test_grade_st_label_requires_up_or_bull_substring_not_buy():
    """Явно документирует найденную тонкость: st_label='BUY' САМ ПО СЕБЕ не засчитывается
    как supertrend-фактор -- нужна подстрока 'UP' или 'BULL'. Не баг, просто нюанс
    сопоставления строк, стоит знать при добавлении новых Supertrend-меток."""
    a_buy_only = {"rsi_4h": 40, "macd_bullish": True, "trend_4h": "bullish",
                  "st_label": "BUY", "above_ema200": True, "above_ema50": True}
    assert bot._signal_grade(a_buy_only, is_long=True) == "A"  # 4 фактора, не 5


def test_grade_c_long_zero_factors():
    a = {"rsi_4h": 60, "macd_bullish": False, "trend_4h": "bearish",
         "st_label": "SELL", "above_ema200": False, "above_ema50": False}
    assert bot._signal_grade(a, is_long=True) == "C"


def test_grade_b_long_exactly_3_factors():
    a = {"rsi_4h": 40, "macd_bullish": True, "trend_4h": "bullish",
         "st_label": "SELL", "above_ema200": False, "above_ema50": False}
    assert bot._signal_grade(a, is_long=True) == "B"


def test_grade_short_is_not_simple_mirror_of_long_inputs():
    """Одни и те же 'бычьи' входные факторы должны давать C для SHORT (не A+) --
    критерии для short зеркальны по СМЫСЛУ (rsi>55/medvежий тренд/etc.), не по значению."""
    bullish_inputs = {"rsi_4h": 40, "macd_bullish": True, "trend_4h": "bullish",
                       "st_label": "BUY", "above_ema200": True, "above_ema50": True}
    assert bot._signal_grade(bullish_inputs, is_long=False) == "C"


def test_grade_missing_fields_defaults_gracefully():
    """Пустой dict не должен падать -- дефолты (rsi_4h=50 и т.п.) должны дать валидный
    грейд, не исключение."""
    grade = bot._signal_grade({}, is_long=True)
    assert grade in ("A+", "A", "B", "C")
