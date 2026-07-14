"""pytest для glossary.py (Пакет 13, Карточка v2 -- словарь-в-строке + /терминология)."""
import glossary as gl


def test_term_note_known_term():
    assert gl.term_note("R:R") == " (соотношение прибыль/риск)"


def test_term_note_unknown_term_empty():
    assert gl.term_note("НЕИЗВЕСТНЫЙ_ТЕРМИН") == ""


def test_annotate_combines_term_and_note():
    assert gl.annotate("SL") == "SL (стоп-лосс, уровень принудительного закрытия при убытке)"


def test_annotate_unknown_term_no_note():
    assert gl.annotate("XYZ") == "XYZ"


def test_format_glossary_text_includes_all_terms():
    text = gl.format_glossary_text()
    assert "Терминология" in text
    for term in gl.TERMS:
        assert term in text


def test_format_glossary_text_alphabetical_order():
    text = gl.format_glossary_text()
    lines = [l for l in text.split("\n") if l.startswith("*")]
    terms_in_order = [l.split("*")[1] for l in lines]
    assert terms_in_order == sorted(terms_in_order, key=str.lower)


# ── format_card_glossary_text() -- ПАКЕТ UX-НАВИГАЦИЯ п.2 ──

def test_card_glossary_whale_includes_expected_terms():
    text = gl.format_card_glossary_text("whale")
    assert "Whale Radar" in text
    for term in ("funding", "OI", "L/S"):
        assert term in text


def test_card_glossary_does_not_include_other_cards_terms():
    """Whale-словарь не должен содержать Z-Score (памп-радар-специфичный
    термин) -- proof that CARD_TERMS реально фильтрует, не отдаёт всё."""
    text = gl.format_card_glossary_text("whale")
    assert "Z-Score" not in text


def test_card_glossary_all_known_cards_covered():
    for card in gl.CARD_TERMS:
        text = gl.format_card_glossary_text(card)
        assert "н/д" not in text
        assert gl.CARD_TITLES[card] in text


def test_card_glossary_unknown_card_honest_na():
    text = gl.format_card_glossary_text("не_существует")
    assert "н/д" in text


def test_card_terms_all_resolve_to_known_definitions():
    """Каждый термин в CARD_TERMS обязан существовать в TERMS -- иначе
    format_card_glossary_text() тихо пропустит его (см. definition-if
    внутри функции), находка не будет замечена."""
    for card, terms in gl.CARD_TERMS.items():
        for term in terms:
            assert term in gl.TERMS, f"{card}: термин {term!r} не определён в TERMS"
