"""
tests/test_level_watch_no_source_attribution.py -- regression-guard на живую
находку 2026-07-18 (протокол правды, repo-wide grep-аудит #292): `level_watch.
format_level_alert()` вставлял `source` (напр. "Королев 13.07" из
journal/watch_zones.json) буквально в подписчицкий текст ("Разметка: {source}")
-- эта функция вызывается из `check_watchlist()`, которая шлётся ВСЕМ активным
подписчикам (`check_alerts()` -> `subscribers.active_chat_ids()`), то есть
реальное имя аналитика уходило в реальный алерт при касании зоны.

Фикс: `format_level_alert()` больше НЕ рендерит `source` вообще (параметр
оставлен в сигнатуре для обратной совместимости, но игнорируется в теле
функции) -- показывается только `updated` (дата разметки). Этот тест
специально передаёт "грязный" `source` с запрещёнными токенами и проверяет,
что они НИКОГДА не попадают в результат -- т.е. защита работает даже если
кто-то в будущем случайно передаст source с именем внутрь."""
import re

import level_watch as lw

FORBIDDEN_TOKENS = re.compile(
    r"\b(kira|ict|королев|korolev|соболев|sobolev|2trade|pixel|заговор|zagovor|"
    r"тимур|timur|санчо|sancho|влад|vlad|гарри|garri|harry|воркшоп|workshop|"
    r"мета|meta|"
    r"cryptomannn|dova\s+lazarus|"
    r"вероятност\w*)\b",
    re.IGNORECASE,
)


def _zone(side="LONG", lo=100.0, hi=110.0, note=None):
    z = {"side": side, "lo": lo, "hi": hi}
    if note:
        z["note"] = note
    return z


def test_format_level_alert_never_renders_source_even_if_dirty():
    text = lw.format_level_alert(
        "BTCUSDT", _zone(), 105.0, "in_zone",
        source="Королев 4h (соболев/2trade канал)", updated="2026-07-18",
    )
    m = FORBIDDEN_TOKENS.search(text)
    assert not m, f"source утёк в подписчицкий текст: {m.group(0)!r} в {text!r}"
    assert "2026-07-18" in text, "updated (дата) должна остаться в тексте"


def test_format_level_alert_clean_with_realistic_note():
    text = lw.format_level_alert(
        "SOLUSDT", _zone(note="Разметка автора 17.07: DCA 25/21.9/19.4, SL 17.2"),
        20.0, "approaching", source="tier_a", updated="2026-07-18",
    )
    m = FORBIDDEN_TOKENS.search(text)
    assert not m, f"запрещённый токен в тексте: {m.group(0)!r} в {text!r}"


def test_format_level_alert_no_source_field_at_all_in_output():
    """Дословная проверка: даже слово 'Королев' в любом падеже/регистре
    (капслок/латиница) не должно встречаться -- широкий негативный тест."""
    dirty_sources = ["Королев", "КОРОЛЕВ", "korolev", "Kira ICT", "2Trade Sobolev"]
    for src in dirty_sources:
        text = lw.format_level_alert("ETHUSDT", _zone(), 100.0, "in_zone",
                                      source=src, updated="2026-07-18")
        m = FORBIDDEN_TOKENS.search(text)
        assert not m, f"грязный source={src!r} утёк: {m.group(0)!r} в {text!r}"
