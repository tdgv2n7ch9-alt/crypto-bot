"""
pytest для ПАКЕТ 19, П1 (владелец, регресс "живой пост без ряда [🏠 Меню]"):
"каждый шаблон покрыть тестом markup содержит home row". Покрывает все
переиспользуемые keyboard-хелперы + send_coin() (главный AUTO-путь,
bot.py:2961) включая его гарантированный last-resort fallback (добавлен
в этом же пакете -- раньше тройной провал send_photo/send_message улетал
необработанным исключением, получатель не видел ни карточки, ни кнопки).
"""
import asyncio
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot


def _has_home_row(markup) -> bool:
    if markup is None:
        return False
    rows = markup.inline_keyboard if hasattr(markup, "inline_keyboard") else markup
    return any(
        any(getattr(btn, "callback_data", None) == "show_menu" for btn in row)
        for row in rows
    )


# ── keyboard-хелперы ──

def test_attach_home_row_adds_when_missing():
    markup = bot.InlineKeyboardMarkup([[bot.InlineKeyboardButton("x", callback_data="y")]])
    assert _has_home_row(bot.attach_home_row(markup))


def test_attach_home_row_idempotent_when_present():
    markup = bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton("🏠 Меню", callback_data="show_menu")],
    ])
    result = bot.attach_home_row(markup)
    assert _has_home_row(result)
    assert len(result.inline_keyboard) == 1  # не задублировал ряд


def test_attach_home_row_handles_none():
    assert _has_home_row(bot.attach_home_row(None))


def test_nav_kb_has_home_row():
    assert _has_home_row(bot.nav_kb())
    assert _has_home_row(bot.nav_kb(refresh_data="some_callback"))


def test_back_kb_has_home_row():
    assert _has_home_row(bot.back_kb())


def test_mv2_back_kb_has_home_row():
    assert _has_home_row(bot._mv2_back_kb())
    assert _has_home_row(bot._mv2_back_kb([[bot.InlineKeyboardButton("x", callback_data="y")]]))


# ── send_coin() -- главный AUTO-путь ──

class _RecordingBot:
    """Фиксирует все вызовы send_photo/send_message, с настраиваемыми
    отказами по счётчику вызова -- воспроизводит ЛЮБУЮ комбинацию
    успех/провал на каждом шаге цепочки фоллбека send_coin()."""

    def __init__(self, fail_photo=False, fail_text=False, fail_last_resort=False):
        self.fail_photo = fail_photo
        self.fail_text = fail_text
        self.fail_last_resort = fail_last_resort
        self.text_calls = []
        self.photo_calls = []

    async def send_photo(self, chat_id, photo, caption=None, parse_mode=None, reply_markup=None):
        self.photo_calls.append({"caption": caption, "reply_markup": reply_markup})
        if self.fail_photo:
            raise RuntimeError("send_photo boom")

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None,
                            disable_web_page_preview=None):
        call = {"text": text, "reply_markup": reply_markup, "parse_mode": parse_mode}
        self.text_calls.append(call)
        is_last_resort = parse_mode is None
        if is_last_resort and self.fail_last_resort:
            raise RuntimeError("last resort boom")
        if not is_last_resort and self.fail_text:
            raise RuntimeError("send_message boom")


def _patch_send_coin_deps(monkeypatch, chart=None):
    monkeypatch.setattr(bot, "get_supertrend_signal", lambda sym: {"label": ""})
    monkeypatch.setattr(bot, "get_binance_24h", lambda sym: None)
    monkeypatch.setattr(bot, "_build_chart_v3_for_signal", lambda sym, a: chart)
    monkeypatch.setattr(bot, "generate_signal_chart", lambda sym, a, stats: chart)
    monkeypatch.setattr(bot.watermark, "embed", lambda text, chat_id: text)


def test_send_coin_no_chart_path_includes_home_row(monkeypatch):
    _patch_send_coin_deps(monkeypatch, chart=None)
    fake_bot = _RecordingBot()
    asyncio.run(
        bot.send_coin(fake_bot, chat_id=1, symbol="DOT", slug="polkadot", a={}, text="карточка DOT"))
    assert len(fake_bot.text_calls) == 1
    assert _has_home_row(fake_bot.text_calls[0]["reply_markup"])


def test_send_coin_photo_path_includes_home_row(monkeypatch):
    _patch_send_coin_deps(monkeypatch, chart=io.BytesIO(b"fake-png-bytes"))
    fake_bot = _RecordingBot()
    asyncio.run(
        bot.send_coin(fake_bot, chat_id=1, symbol="DOT", slug="polkadot", a={}, text="карточка DOT"))
    assert len(fake_bot.photo_calls) == 1
    assert _has_home_row(fake_bot.photo_calls[0]["reply_markup"])


def test_send_coin_last_resort_fallback_never_loses_home_row(monkeypatch):
    """ПАКЕТ 19, П1: если ВСЕ попытки с parse_mode="HTML" провалились (та же
    категория бага, что Markdown-краш из П0 -- невалидный фрагмент в text),
    получатель обязан получить ХОТЯ БЫ голый текст с кнопкой "🏠 Меню",
    не остаться совсем без ответа."""
    _patch_send_coin_deps(monkeypatch, chart=None)
    fake_bot = _RecordingBot(fail_text=True)
    asyncio.run(
        bot.send_coin(fake_bot, chat_id=1, symbol="DOT", slug="polkadot", a={}, text="карточка DOT"))
    # первый вызов -- обычная попытка (провалилась), второй -- last-resort
    assert len(fake_bot.text_calls) == 2
    last_resort = fake_bot.text_calls[-1]
    assert last_resort["parse_mode"] is None  # без разметки -- не может сломаться на ней
    assert _has_home_row(last_resort["reply_markup"])


def test_send_coin_photo_and_text_both_fail_still_gets_last_resort(monkeypatch):
    _patch_send_coin_deps(monkeypatch, chart=io.BytesIO(b"fake-png-bytes"))
    fake_bot = _RecordingBot(fail_photo=True, fail_text=True)
    asyncio.run(
        bot.send_coin(fake_bot, chat_id=1, symbol="DOT", slug="polkadot", a={}, text="карточка DOT"))
    assert len(fake_bot.text_calls) >= 1
    last_resort = fake_bot.text_calls[-1]
    assert last_resort["parse_mode"] is None
    assert _has_home_row(last_resort["reply_markup"])
