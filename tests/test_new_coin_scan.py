"""
pytest для new_coin_scan.py (Пакет 13, EVENT-RADAR М4, узкий вариант -- возраст
через уже отслеживаемую вселенную, метод rug_radar.compute_age_days()).
"""
import new_coin_scan as ncs


def test_is_young_coin_true_under_30_days():
    assert ncs.is_young_coin(15) is True


def test_is_young_coin_false_at_30_days():
    assert ncs.is_young_coin(30) is False


def test_is_young_coin_false_none():
    assert ncs.is_young_coin(None) is False


def test_is_memecoin_true_when_category_present():
    assert ncs.is_memecoin({"categories": ["Smart Contract Platform", "Meme", "Dog-Themed"]}) is True


def test_is_memecoin_false_without_category():
    assert ncs.is_memecoin({"categories": ["Smart Contract Platform"]}) is False


def test_is_memecoin_false_empty_detail():
    assert ncs.is_memecoin({}) is False
    assert ncs.is_memecoin(None) is False


def test_is_memecoin_case_insensitive():
    assert ncs.is_memecoin({"categories": ["MEME"]}) is True


def test_format_young_coin_flag_empty_when_not_young():
    assert ncs.format_young_coin_flag(90) == ""
    assert ncs.format_young_coin_flag(None) == ""


def test_format_young_coin_flag_shows_age():
    text = ncs.format_young_coin_flag(12)
    assert "МОЛОДАЯ" in text
    assert "12" in text


def test_format_young_coin_flag_approx_note():
    text = ncs.format_young_coin_flag(5, age_is_approx=True)
    assert "approx" in text


def test_format_young_coin_flag_meme_note():
    text = ncs.format_young_coin_flag(5, cg_detail={"categories": ["Meme"]})
    assert "мемкоин" in text
