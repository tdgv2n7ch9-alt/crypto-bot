"""
pytest для inbox.py (ПАКЕТ 19, П2, владелец: антиспам-инбоксы). Файловый
I/O изолирован через monkeypatch на inbox.INBOX_FILE (tmp_path), без
реального journal/inbox.json -- тот же принцип, что tests/test_daily_metrics.py.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import inbox


def _iso(monkeypatch, tmp_path):
    monkeypatch.setattr(inbox, "INBOX_FILE", str(tmp_path / "inbox.json"))


# ── add_item / get_unread_counts / get_section_items ──

def test_add_item_increments_unread_and_pending(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    inbox.add_item("tochki", {"symbol": "BTC"})
    inbox.add_item("tochki", {"symbol": "ETH"})
    assert inbox.get_unread_counts()["tochki"] == 2
    items = inbox.get_section_items("tochki")
    assert len(items) == 2
    assert items[0]["symbol"] == "BTC" and "ts" in items[0]


def test_add_item_unknown_section_raises(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    try:
        inbox.add_item("nope", {})
        assert False, "должно было упасть"
    except ValueError:
        pass


def test_add_item_evicts_oldest_beyond_cap(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    for i in range(inbox.CAP_PER_SECTION + 5):
        inbox.add_item("radary", {"i": i})
    items = inbox.get_section_items("radary")
    assert len(items) == inbox.CAP_PER_SECTION
    assert items[0]["i"] == 5  # первые 5 вытеснены
    assert items[-1]["i"] == inbox.CAP_PER_SECTION + 4


def test_sections_isolated(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    inbox.add_item("tochki", {"a": 1})
    inbox.add_item("radary", {"a": 2})
    inbox.add_item("radary", {"a": 3})
    counts = inbox.get_unread_counts()
    assert counts["tochki"] == 1
    assert counts["radary"] == 2
    assert counts["x100"] == 0


# ── mark_read ──

def test_mark_read_resets_unread_not_pending(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    inbox.add_item("tochki", {"a": 1})
    inbox.mark_read("tochki")
    assert inbox.get_unread_counts()["tochki"] == 0
    # pending НЕ трогается mark_read -- следующий дайджест всё равно увидит его
    pending = inbox.pop_pending_digest(now_ts=inbox.MIN_DIGEST_INTERVAL_SEC + 1)
    assert pending.get("tochki") == 1


def test_mark_read_unknown_section_is_noop(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    inbox.mark_read("nope")  # не должно упасть


# ── should_bypass_inbox ──

def test_bypass_author_zone_touch():
    assert inbox.should_bypass_inbox(is_author_zone_touch=True) is True


def test_bypass_rug_warn():
    assert inbox.should_bypass_inbox(rug_warn=True) is True


def test_bypass_high_rocket_score():
    assert inbox.should_bypass_inbox(rocket_score=85) is True
    assert inbox.should_bypass_inbox(rocket_score=90) is True


def test_no_bypass_below_threshold():
    assert inbox.should_bypass_inbox(rocket_score=84) is False
    assert inbox.should_bypass_inbox() is False
    assert inbox.should_bypass_inbox(rocket_score=None, is_author_zone_touch=False, rug_warn=False) is False


# ── pop_pending_digest / should_send_digest ──

def test_should_send_digest_false_when_empty(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    assert inbox.should_send_digest(now_ts=1_000_000.0) is False


def test_should_send_digest_false_within_interval(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    inbox.add_item("tochki", {"a": 1}, now_ts=1_000_000.0)
    # last_digest_ts всё ещё 0.0 (никогда не слали) -> now - 0 будет ОГРОМНЫМ ->
    # должно быть True; проверим отдельно случай "только что слали"
    inbox.pop_pending_digest(now_ts=1_000_000.0)
    inbox.add_item("tochki", {"a": 2}, now_ts=1_000_100.0)
    assert inbox.should_send_digest(now_ts=1_000_100.0 + 10) is False  # 10с < 30 мин


def test_should_send_digest_true_after_interval(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    inbox.add_item("tochki", {"a": 1}, now_ts=1_000_000.0)
    inbox.pop_pending_digest(now_ts=1_000_000.0)
    inbox.add_item("tochki", {"a": 2}, now_ts=1_000_100.0)
    assert inbox.should_send_digest(now_ts=1_000_100.0 + inbox.MIN_DIGEST_INTERVAL_SEC) is True


def test_pop_pending_digest_resets_and_returns_only_nonzero(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    inbox.add_item("tochki", {"a": 1})
    inbox.add_item("tochki", {"a": 2})
    inbox.add_item("radary", {"a": 3})
    pending = inbox.pop_pending_digest(now_ts=42.0)
    assert pending == {"tochki": 2, "radary": 1}
    assert "x100" not in pending  # ноль -- не включён
    # После pop -- всё обнулено
    pending2 = inbox.pop_pending_digest(now_ts=100.0)
    assert pending2 == {}


# ── format_digest_text ──

def test_format_digest_text_matches_owner_spec():
    text = inbox.format_digest_text({"tochki": 3, "radary": 2, "x100": 1})
    assert text == "📬 Новое: 🎯 ТОЧКИ +3 · 📡 РАДАРЫ +2 · 🚀 x100 +1"


def test_format_digest_text_none_when_empty():
    assert inbox.format_digest_text({}) is None
    assert inbox.format_digest_text({"tochki": 0}) is None


def test_format_digest_text_skips_zero_sections():
    text = inbox.format_digest_text({"tochki": 5, "radary": 0})
    assert text == "📬 Новое: 🎯 ТОЧКИ +5"


# ── menu_badge ──

def test_menu_badge_shows_count_when_unread(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    inbox.add_item("tochki", {"a": 1})
    inbox.add_item("tochki", {"a": 2})
    inbox.add_item("tochki", {"a": 3})
    assert inbox.menu_badge("tochki", "🎯 ТОЧКИ") == "🎯 ТОЧКИ (3)"


def test_menu_badge_plain_when_zero(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    assert inbox.menu_badge("tochki", "🎯 ТОЧКИ") == "🎯 ТОЧКИ"


def test_menu_badge_uses_precomputed_counts():
    assert inbox.menu_badge("radary", "📡 РАДАРЫ", counts={"radary": 7}) == "📡 РАДАРЫ (7)"


# ── persistence across reload ──

def test_state_persists_across_reload(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    inbox.add_item("tochki", {"symbol": "SOL"})
    # Симулируем перезагрузку модуля -- просто вызываем _load() заново,
    # файл на диске должен содержать записанное.
    reloaded = inbox._load()
    assert reloaded["unread"]["tochki"] == 1
    assert reloaded["sections"]["tochki"][0]["symbol"] == "SOL"


def test_missing_file_returns_empty_state(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    assert inbox.get_unread_counts() == {"tochki": 0, "radary": 0, "x100": 0}
    assert inbox.get_section_items("tochki") == []


def test_corrupted_file_falls_back_to_empty_state(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    with open(inbox.INBOX_FILE, "w") as f:
        f.write("{not valid json")
    assert inbox.get_unread_counts() == {"tochki": 0, "radary": 0, "x100": 0}
