"""
pytest для event_radar.py (Пакет 13, EVENT-RADAR М2 -- листинги/делистинги).
Сетевые fetch_* мокаются через monkeypatch (requests.get подмена), остальное --
чистые функции.
"""
import json

import pytest

import event_radar as er


# --- extract_symbols_from_title ---

def test_extract_single_ticker_with_quote_suffix():
    title = "New listing: SKHYUSDT Perpetual Contract, with up to 25x leverage"
    assert "SKHY" in er.extract_symbols_from_title(title)


def test_extract_comma_separated_list():
    title = "Delisting of ARTY,CTA,GTAI,LBTC,MBOX,NAKA,U"
    symbols = er.extract_symbols_from_title(title)
    for s in ["ARTY", "CTA", "GTAI", "MBOX", "NAKA"]:
        assert s in symbols


def test_extract_no_symbol_returns_empty_list():
    title = "Notice of Removal of Spot Trading Pairs - 2026-07-10"
    assert er.extract_symbols_from_title(title) == []


def test_extract_empty_title():
    assert er.extract_symbols_from_title("") == []
    assert er.extract_symbols_from_title(None) == []


def test_extract_dedupes_preserving_order():
    title = "Delisting of KORUUSDT and KORUUSDT again"
    symbols = er.extract_symbols_from_title(title)
    assert symbols.count("KORU") == 1


# --- fetch_bybit_announcements (mocked HTTP) ---

class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_fetch_bybit_announcements_success(monkeypatch):
    payload = {
        "retCode": 0, "retMsg": "OK",
        "result": {"list": [
            {"title": "New listing: FOOUSDT Perpetual Contract", "url": "https://x/1",
             "publishTime": 1783738732000},
        ], "total": 1},
    }
    monkeypatch.setattr(er.requests, "get", lambda *a, **kw: _FakeResponse(payload))
    events = er.fetch_bybit_announcements("listing")
    assert len(events) == 1
    assert events[0]["exchange"] == "bybit"
    assert events[0]["kind"] == "listing"
    assert events[0]["id"] == "bybit:https://x/1"
    assert "FOO" in events[0]["symbols"]


def test_fetch_bybit_announcements_bad_retcode_returns_empty(monkeypatch):
    payload = {"retCode": 10001, "retMsg": "bad param", "result": {}}
    monkeypatch.setattr(er.requests, "get", lambda *a, **kw: _FakeResponse(payload))
    assert er.fetch_bybit_announcements("delisting") == []


def test_fetch_bybit_announcements_network_error_returns_empty(monkeypatch):
    def _raise(*a, **kw):
        raise ConnectionError("boom")
    monkeypatch.setattr(er.requests, "get", _raise)
    assert er.fetch_bybit_announcements("listing") == []


# --- fetch_binance_announcements (mocked HTTP) ---

def test_fetch_binance_announcements_success(monkeypatch):
    payload = {
        "code": "000000", "success": True,
        "data": {"catalogs": [{"catalogId": 161, "articles": [
            {"id": 279327, "title": "Binance Will Delist BARUSDT", "releaseDate": 1783415700000},
        ]}]},
    }
    monkeypatch.setattr(er.requests, "get", lambda *a, **kw: _FakeResponse(payload))
    events = er.fetch_binance_announcements("delisting")
    assert len(events) == 1
    assert events[0]["exchange"] == "binance"
    assert events[0]["id"] == "binance:279327"
    assert "BAR" in events[0]["symbols"]


def test_fetch_binance_announcements_failure_flag_returns_empty(monkeypatch):
    payload = {"code": "400", "success": False, "message": "oops"}
    monkeypatch.setattr(er.requests, "get", lambda *a, **kw: _FakeResponse(payload))
    assert er.fetch_binance_announcements("listing") == []


def test_fetch_binance_announcements_empty_catalogs_returns_empty(monkeypatch):
    payload = {"code": "000000", "success": True, "data": {"catalogs": []}}
    monkeypatch.setattr(er.requests, "get", lambda *a, **kw: _FakeResponse(payload))
    assert er.fetch_binance_announcements("listing") == []


# --- filter_new_events / should_alert / format_event_alert ---

def _event(kind="listing", exch="bybit", eid="e1", symbols=None):
    return {"exchange": exch, "kind": kind, "id": eid, "title": "T",
            "symbols": symbols or [], "url": "https://u", "ts": 1.0}


def test_filter_new_events_excludes_seen():
    events = [_event(eid="a"), _event(eid="b")]
    assert er.filter_new_events(events, {"a"}) == [events[1]]


def test_should_alert_delisting_always_true():
    e = _event(kind="delisting", symbols=[])
    assert er.should_alert(e, watch_symbols=set()) is True


def test_should_alert_listing_true_when_in_watch():
    e = _event(kind="listing", symbols=["LINK"])
    assert er.should_alert(e, watch_symbols={"LINK", "AVAX"}) is True


def test_should_alert_listing_false_when_not_in_watch():
    e = _event(kind="listing", symbols=["RANDOMCOIN"])
    assert er.should_alert(e, watch_symbols={"LINK", "AVAX"}) is False


def test_should_alert_listing_case_insensitive():
    e = _event(kind="listing", symbols=["link"])
    assert er.should_alert(e, watch_symbols={"LINK"}) is True


def test_format_event_alert_includes_kind_and_symbols():
    e = _event(kind="delisting", exch="binance", symbols=["FOO"])
    text = er.format_event_alert(e)
    assert "ДЕЛИСТИНГ" in text
    assert "FOO" in text
    assert "BINANCE" in text


def test_format_event_alert_na_symbols_when_empty():
    e = _event(kind="listing", symbols=[])
    text = er.format_event_alert(e)
    assert "н/д" in text


# --- seen-ids persistence + poll_and_get_alerts ---

def test_load_seen_ids_missing_file_returns_empty_set(tmp_path):
    assert er._load_seen_ids(str(tmp_path / "nope.json")) == set()


def test_save_and_load_seen_ids_roundtrip(tmp_path):
    path = str(tmp_path / "seen.json")
    er._save_seen_ids({"a", "b"}, path)
    assert er._load_seen_ids(path) == {"a", "b"}


def test_load_seen_ids_corrupt_file_returns_empty_set(tmp_path):
    path = tmp_path / "seen.json"
    path.write_text("not json")
    assert er._load_seen_ids(str(path)) == set()


def test_poll_and_get_alerts_end_to_end(monkeypatch, tmp_path):
    events = [
        _event(kind="delisting", eid="d1", symbols=["FOO"]),
        _event(kind="listing", eid="l1", symbols=["LINK"]),
        _event(kind="listing", eid="l2", symbols=["NOTWATCHED"]),
    ]
    monkeypatch.setattr(er, "fetch_all_events", lambda limit=20: events)
    seen_path = str(tmp_path / "seen.json")

    alerts = er.poll_and_get_alerts(watch_symbols={"LINK"}, seen_ids_path=seen_path)
    assert len(alerts) == 2  # delisting always + listing(LINK), NOT listing(NOTWATCHED)

    # second poll with same events -- already seen, no alerts
    alerts2 = er.poll_and_get_alerts(watch_symbols={"LINK"}, seen_ids_path=seen_path)
    assert alerts2 == []


def test_poll_and_get_alerts_no_events_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(er, "fetch_all_events", lambda limit=20: [])
    alerts = er.poll_and_get_alerts(watch_symbols=set(), seen_ids_path=str(tmp_path / "seen.json"))
    assert alerts == []
