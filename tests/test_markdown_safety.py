"""
pytest для ПАКЕТ 19, П0/П1 (владелец, регресс): welcome_text_v2()/
mv2_sistema_sources красились сырыми ключами _DATA_SOURCE_STATUS
("coingecko_markets", "yahoo_finance", ...) прямо в текст с
parse_mode="Markdown" -- голый "_" в legacy Markdown -- маркер курсива,
нечётное число подчёркиваний в собранной строке -> живой
telegram.error.BadRequest ("Can't parse entities"), см. PROGRESS.md
"ПАКЕТ 19, П0" (живой traceback bot.py:3157 cmd_start, bot.py:4586
callback_handler "🏠 Меню"). Фикс -- SOURCE_DISPLAY_LABELS (человекочитаемые
метки без "_"). Эти тесты покрывают ИМЕННО класс регрессии, не переизобретают
парсер Telegram Markdown целиком.
"""
import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot


class _FakeBotModule:
    def __init__(self, ok_names):
        self._ok_names = set(ok_names)

    def get_data_source_status(self):
        return {name: {"ok": name in self._ok_names} for name in bot._DATA_SOURCE_STATUS}


# ── SOURCE_DISPLAY_LABELS -- инвариант, который не даёт багу вернуться ──

def test_source_display_labels_covers_all_status_keys():
    """Каждый ключ _DATA_SOURCE_STATUS обязан иметь человекочитаемую метку --
    иначе welcome_text_v2()/mv2_sistema_sources тихо откатятся на
    name.replace("_", " ") fallback для НОВОГО источника, добавленного в
    будущем без обновления маппинга (fallback безопасен сам по себе, но
    лучше поймать пробел явно здесь)."""
    missing = set(bot._DATA_SOURCE_STATUS) - set(bot.SOURCE_DISPLAY_LABELS)
    assert missing == set(), f"источники без человекочитаемой метки: {missing}"


def test_source_display_labels_contain_no_underscore():
    """Если бы сама метка содержала "_", регрессия вернулась бы через
    маппинг -- защита от повторного бага при редактировании меток."""
    for name, label in bot.SOURCE_DISPLAY_LABELS.items():
        assert "_" not in label, f"{name}: метка {label!r} содержит '_' -- вернёт баг"


# ── welcome_text_v2() ──

ALL_SOURCE_NAMES = list(bot._DATA_SOURCE_STATUS.keys())
# Все непустые подмножества источников -- воспроизводит ЛЮБУЮ комбинацию
# "ok"/не "ok" источников в момент реального вызова, включая ту, что дала
# нечётное число "_" и живой краш (см. PROGRESS.md).
ALL_NONEMPTY_SUBSETS = [
    combo for r in range(1, len(ALL_SOURCE_NAMES) + 1)
    for combo in itertools.combinations(ALL_SOURCE_NAMES, r)
]


def test_welcome_text_v2_no_raw_underscore_keys_in_any_combination():
    for combo in ALL_NONEMPTY_SUBSETS:
        text = bot.welcome_text_v2(_FakeBotModule(combo))
        for name in ALL_SOURCE_NAMES:
            assert name not in text, f"комбинация {combo}: сырой ключ {name!r} утёк в текст"


def test_welcome_text_v2_empty_sources_says_checking():
    text = bot.welcome_text_v2(_FakeBotModule([]))
    assert "проверяются..." in text


def test_welcome_text_v2_shows_friendly_labels():
    text = bot.welcome_text_v2(_FakeBotModule(["coingecko_markets", "yahoo_finance"]))
    assert "CoinGecko markets" in text
    assert "Yahoo" in text


def test_welcome_text_v2_survives_bot_module_exception():
    class _BoomBotModule:
        def get_data_source_status(self):
            raise RuntimeError("network down")

    text = bot.welcome_text_v2(_BoomBotModule())
    assert "проверяются..." in text


def test_welcome_text_v2_unknown_future_source_key_still_safe():
    """Симулирует источник, добавленный в будущем БЕЗ обновления
    SOURCE_DISPLAY_LABELS -- fallback name.replace("_"," ") обязан не
    пропустить сырой "_" дальше, даже если словарь маппинга не обновлён."""
    class _FutureBotModule:
        def get_data_source_status(self):
            return {"brand_new_source": {"ok": True}}

    text = bot.welcome_text_v2(_FutureBotModule())
    assert "brand_new_source" not in text
    assert "brand new source" in text
