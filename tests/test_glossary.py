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
