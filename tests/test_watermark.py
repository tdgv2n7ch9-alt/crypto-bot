"""
pytest для watermark.py -- невидимая zero-width метка на сигнальных карточках
(Пакет SECURITY-HARDENING М5, владелец "да").
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import watermark as wm


def test_embed_extract_round_trip_positive_chat_id():
    text = wm.embed("Сигнал по BTC", 7009350191)
    assert wm.extract(text) == 7009350191


def test_embed_extract_round_trip_small_chat_id():
    text = wm.embed("x", 1)
    assert wm.extract(text) == 1


def test_embed_extract_round_trip_zero():
    text = wm.embed("x", 0)
    assert wm.extract(text) == 0


def test_embed_extract_round_trip_negative_chat_id():
    """Групповые chat_id в Telegram отрицательные -- на будущее, хотя сегодня
    водяной знак используется только для персональных VIP-чатов."""
    text = wm.embed("x", -1001234567890)
    assert wm.extract(text) == -1001234567890


def test_embed_does_not_change_visible_text():
    original = "Сигнал по BTC\nEntry: 50000\nSL: 49000"
    text = wm.embed(original, 555)
    assert text.startswith(original)


def test_embed_appends_only_zero_width_characters():
    text = wm.embed("visible", 555)
    suffix = text[len("visible"):]
    assert all(c in (wm.ZW0, wm.ZW1, wm.ZW_MARK) for c in suffix)


def test_extract_no_watermark_returns_none():
    assert wm.extract("обычный текст без метки") is None


def test_extract_empty_string_returns_none():
    assert wm.extract("") is None


def test_extract_none_input_returns_none():
    assert wm.extract(None) is None


def test_extract_truncated_watermark_returns_none():
    """Частичная пересылка -- метка обрезана посередине (нет закрывающего ZW_MARK)."""
    text = wm.embed("x", 12345)
    truncated = text[: len(text) - 5]
    assert wm.extract(truncated) is None


def test_extract_corrupted_payload_length_returns_none():
    corrupted = wm.ZW_MARK + wm.ZW0 * 3 + wm.ZW_MARK  # неверная длина полезной нагрузки
    assert wm.extract(corrupted) is None


def test_extract_foreign_character_inside_mark_returns_none():
    bad_payload = wm.ZW0 * 44 + "x"  # 45 символов, но один -- не zero-width
    corrupted = wm.ZW_MARK + bad_payload + wm.ZW_MARK
    assert wm.extract(corrupted) is None


def test_encode_chat_id_overflow_raises():
    import pytest
    with pytest.raises(ValueError):
        wm.encode_chat_id(2 ** 50)


def test_embed_overflow_falls_back_to_plain_text():
    """Не должно ронять отправку карточки -- при переполнении просто без метки."""
    text = wm.embed("hello", 2 ** 50)
    assert text == "hello"
    assert wm.extract(text) is None


def test_embed_survives_html_tags_in_text():
    original = "<b>BTCUSDT</b> LONG entry <code>50000</code>"
    text = wm.embed(original, 42)
    assert text.startswith(original)
    assert wm.extract(text) == 42


def test_max_magnitude_boundary_round_trips():
    text = wm.embed("x", wm.MAX_MAGNITUDE)
    assert wm.extract(text) == wm.MAX_MAGNITUDE


def test_two_different_chat_ids_produce_different_watermarks():
    a = wm.embed("same text", 111)
    b = wm.embed("same text", 222)
    assert a != b
    assert wm.extract(a) == 111
    assert wm.extract(b) == 222
