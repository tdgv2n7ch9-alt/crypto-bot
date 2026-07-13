"""
pytest для Пакет 18, п.9 (владелец): "Whale Monitor" -> "Whale Radar" --
единый брендинг по спеку Меню v2 (раздел РАДАРЫ уже называл кнопку "🐋 Whale
Radar", но открывавшийся экран (whale_status) всё ещё говорил "WHALE
MONITOR" -- несостыковка). Логика (whale_monitor(), whale_monitor_label(),
job-идентификаторы) НЕ переименована -- владелец просил только текст в
шаблонах, не рефакторинг кода.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_no_whale_monitor_string_survives_in_bot_source():
    """Регрессия: ни одного пользовательского упоминания старого бренда не
    должно просочиться обратно в bot.py."""
    bot_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot.py")
    with open(bot_py, "r", encoding="utf-8") as f:
        content = f.read()
    assert "Whale Monitor" not in content
    assert "WHALE MONITOR" not in content


def test_whale_monitor_identifiers_intentionally_unchanged():
    """Владелец: "логику не трогать" -- функция/job-идентификаторы остаются
    прежними (whale_monitor), только рендер-текст переименован. Явная
    регрессия на случай, если кто-то попробует "докрутить" переименование
    identifiers в будущем и случайно сломает job-расписание/ссылки."""
    import bot
    assert hasattr(bot, "whale_monitor")
    assert hasattr(bot, "whale_monitor_label")


def test_whale_radar_button_label_in_radary_menu_source():
    """Меню v2, раздел РАДАРЫ -- кнопка обязана вести на whale_status с
    актуальным брендом."""
    bot_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot.py")
    with open(bot_py, "r", encoding="utf-8") as f:
        content = f.read()
    assert '"🐋 Whale Radar", callback_data="whale_status"' in content
