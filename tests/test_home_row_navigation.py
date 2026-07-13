"""
pytest для Пакет 18, п.2 (владелец): "Навигация из каждого поста: нижний ряд
[🏠 Меню] во всех сообщениях бота". Grep по reply_markup= в bot.py нашёл
реальные пробелы -- часть алертов подписчикам (zone/ST/watchlist/spot/
entry-approach) и часть команд/callback-веток (game, rockets, precision-
шапка, cmd_watchlist) отправляли клавиатуру БЕЗ пути назад в меню.
attach_home_row(markup) -- единый идемпотентный хелпер, подключён во всех
найденных пробелах (см. коммит).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def _has_home(markup):
    return any(
        any(btn.callback_data == "show_menu" for btn in row)
        for row in markup.inline_keyboard
    )


def test_attach_home_row_adds_row_to_markup_without_home():
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Обновить", callback_data="whale_status"),
    ]])
    result = bot.attach_home_row(markup)
    assert _has_home(result)
    # исходный ряд сохранён, домашний добавлен НИЖЕ
    assert result.inline_keyboard[0][0].callback_data == "whale_status"
    assert result.inline_keyboard[-1][-1].callback_data == "show_menu"


def test_attach_home_row_idempotent_when_home_already_present():
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить", callback_data="pump_radar"),
         InlineKeyboardButton("🏠 Меню", callback_data="show_menu")],
    ])
    result = bot.attach_home_row(markup)
    # не дублирует ряд -- всего одна кнопка show_menu на всей клавиатуре
    home_buttons = [btn for row in result.inline_keyboard for btn in row
                    if btn.callback_data == "show_menu"]
    assert len(home_buttons) == 1
    assert len(result.inline_keyboard) == 1


def test_attach_home_row_on_empty_markup():
    result = bot.attach_home_row(None)
    assert _has_home(result)
    assert len(result.inline_keyboard) == 1


def test_attach_home_row_accepts_plain_row_list():
    rows = [[InlineKeyboardButton("A", callback_data="x")]]
    result = bot.attach_home_row(rows)
    assert isinstance(result, InlineKeyboardMarkup)
    assert _has_home(result)


def test_no_reply_markup_inlinekeyboardmarkup_construction_missing_home_row():
    """Регрессия на будущее: каждая точка, где reply_markup= передаётся с
    ad-hoc InlineKeyboardMarkup([[...]]) (не через общий билдер вроде
    nav_kb()/back_kb()/active_main_kb()/overview_kb()/_mv2_back_kb()), либо
    уже содержит "show_menu" в литерале, либо обёрнута в attach_home_row(...).
    Статический grep -- не гоняет код, а фиксирует состояние на момент
    Пакета 18, п.2, чтобы новый код не мог тихо снова потерять кнопку
    домой (см. живые пробелы, найденные и закрытые в этом пакете: алерты
    zone/ST/watchlist/spot/entry-approach, game/rockets/precision-шапка,
    cmd_watchlist)."""
    bot_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot.py")
    with open(bot_py, "r", encoding="utf-8") as f:
        lines = f.readlines()

    exempt_builders = ("def main_kb", "def main_kb_v2", "def back_kb", "def nav_kb",
                       "def overview_kb", "def _mv2_back_kb", "def attach_home_row")
    current_fn_exempt = False
    violations = []
    for i, line in enumerate(lines):
        if line.startswith("def ") or line.startswith("async def "):
            current_fn_exempt = any(name in line for name in exempt_builders)
        if "InlineKeyboardMarkup([[" not in line and "InlineKeyboardMarkup([" not in line:
            continue
        if "attach_home_row" in line:
            continue
        if current_fn_exempt:
            continue
        # ищем закрытие конструкции в ближайших 10 строках -- если
        # "show_menu" где-то внутри блока, литерал уже честный
        block = "".join(lines[i:i + 10])
        if "show_menu" in block:
            continue
        violations.append(i + 1)

    assert violations == [], (
        f"Найдены InlineKeyboardMarkup(...) без show_menu и без attach_home_row "
        f"на строках {violations} -- добавь attach_home_row(...) вокруг конструкции "
        f"или включи show_menu в литерал."
    )
