"""
pytest для reader.py: extract_symbol/extract_price/format_signal -- чистые функции
парсинга сообщений внешних Telegram-каналов. Не задевает Telethon/сеть/реальные каналы.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# reader.py читает TG_API_ID/TG_API_HASH на уровне модуля через os.getenv с дефолтом "0"/"" --
# безопасно импортировать без реальных креды (не подключается при импорте, только при main()).
import reader


def test_extract_symbol_dollar_format():
    assert reader.extract_symbol("Смотрим $BTC на пробой") == "BTC"


def test_extract_symbol_usdt_pair():
    assert reader.extract_symbol("ETHUSDT лонг от уровня") == "ETH"


def test_extract_symbol_emoji_prefixed():
    assert reader.extract_symbol("🟢 SOL разворот вверх") == "SOL"
    assert reader.extract_symbol("🔴 AVAX пробой вниз") == "AVAX"


def test_extract_symbol_skips_reserved_words():
    """BUY/SELL/LONG/SHORT/STOP/TAKE/PROFIT/LOSS/USD/TP/SL не должны приниматься за тикер."""
    assert reader.extract_symbol("LONG сейчас самое время") is None


def test_extract_symbol_skips_stablecoins():
    assert reader.extract_symbol("$USDT перевод на биржу") is None


def test_extract_symbol_none_when_absent():
    assert reader.extract_symbol("Просто текст без тикера и цифр") is None


def test_extract_price_finds_labeled_value():
    text = "Вход: 65000, TP1: 68000, SL: 63000"
    assert reader.extract_price(text, ["вход", "entry", "ep", "цена"]) == 65000.0
    assert reader.extract_price(text, ["tp1", "тп1", "t1"]) == 68000.0
    assert reader.extract_price(text, ["sl", "стоп", "stop"]) == 63000.0


def test_extract_price_comma_decimal():
    assert reader.extract_price("Вход 1,5", ["вход"]) == 1.5


def test_extract_price_none_when_keyword_absent():
    assert reader.extract_price("Просто текст", ["вход", "entry"]) is None


def test_format_signal_includes_channel_and_symbol():
    text = "🟢 BTCUSDT LONG вход 65000 TP1 68000 SL 63000"
    formatted = reader.format_signal(text, "Test Channel")
    assert "Test Channel" in formatted
    assert "BTCUSDT" in formatted
    assert "ЛОНГ" in formatted


def test_format_signal_no_symbol_still_includes_raw_text():
    """Если тикер/направление не распознаны -- функция не должна падать, просто не
    добавляет структурированный блок, оригинальный текст остаётся в сообщении."""
    text = "Просто новость без сигнала про рынок в целом"
    formatted = reader.format_signal(text, "Test Channel")
    assert "Test Channel" in formatted
    assert "новость" in formatted
