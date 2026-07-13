"""
pytest для event_radar.py (Пакет 13, EVENT-RADAR М2 -- листинги/делистинги +
фикс 2026-07-13 по итогам живого прогона). Сетевые fetch_*/fetch_bybit_symbol_
universe мокаются через monkeypatch (requests.get подмена), остальное -- чистые
функции.
"""
import json
import time

import pytest

import event_radar as er


# --- extract_symbols_from_title (key-verb extraction, живой прогон 13.07) ---

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
    # "Removal of" триггерит глагол, но дальше нет открытых тикеров -- честно пусто
    assert er.extract_symbols_from_title(title) == []


def test_extract_empty_title():
    assert er.extract_symbols_from_title("") == []
    assert er.extract_symbols_from_title(None) == []


def test_extract_dedupes_preserving_order():
    title = "Delisting of KORUUSDT and KORUUSDT again"
    symbols = er.extract_symbols_from_title(title)
    assert symbols.count("KORU") == 1


def test_extract_bare_tickers_without_quote_suffix_and_with_ampersand():
    """Живая находка 13.07: реальный заголовок без суффикса котировки вообще --
    старый парсер это пропускал полностью."""
    title = "Binance Margin And Loan Will Delist TST & IOTX on 2026-07-10"
    symbols = er.extract_symbols_from_title(title)
    assert "TST" in symbols
    assert "IOTX" in symbols


def test_extract_cease_support_verb():
    title = "Binance Will Cease Support for Several Tokens: ABC, XYZ"
    symbols = er.extract_symbols_from_title(title)
    assert "ABC" in symbols
    assert "XYZ" in symbols


def test_extract_monitoring_tag_verb():
    title = "Binance Adds DOGGO to the Monitoring Tag"
    symbols = er.extract_symbols_from_title(title)
    assert "DOGGO" in symbols


def test_extract_validates_against_known_symbols():
    title = "New listing: SKHYUSDT Perpetual Contract, with up to 25x leverage"
    assert er.extract_symbols_from_title(title, known_symbols={"BTC", "ETH"}) == []
    assert er.extract_symbols_from_title(title, known_symbols={"SKHY"}) == ["SKHY"]


def test_extract_filters_stopword_garbage_even_without_known_symbols():
    title = "New listing: SKHYUSDT Perpetual Contract, with up to 25x leverage"
    symbols = er.extract_symbols_from_title(title)
    assert "PERPETUAL" not in symbols
    assert "CONTRACT" not in symbols
    assert "LEVERAGE" not in symbols


# --- fetch_bybit_symbol_universe (mocked HTTP) ---

class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_fetch_bybit_symbol_universe_success(monkeypatch):
    er._BYBIT_SYMBOL_CACHE["ts"] = 0.0
    er._BYBIT_SYMBOL_CACHE["symbols"] = set()
    payload = {"result": {"list": [{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}, {"symbol": "USDCUSDT"}]}}
    monkeypatch.setattr(er.requests, "get", lambda *a, **kw: _FakeResponse(payload))
    symbols = er.fetch_bybit_symbol_universe(force=True)
    assert "BTC" in symbols and "ETH" in symbols


def test_fetch_bybit_symbol_universe_cache_reused(monkeypatch):
    er._BYBIT_SYMBOL_CACHE["ts"] = time.time()
    er._BYBIT_SYMBOL_CACHE["symbols"] = {"CACHED"}
    calls = []
    monkeypatch.setattr(er.requests, "get", lambda *a, **kw: calls.append(1) or _FakeResponse({}))
    symbols = er.fetch_bybit_symbol_universe()
    assert symbols == {"CACHED"}
    assert calls == []


def test_fetch_bybit_symbol_universe_network_failure_returns_cache(monkeypatch):
    er._BYBIT_SYMBOL_CACHE["ts"] = 0.0
    er._BYBIT_SYMBOL_CACHE["symbols"] = {"OLD"}
    def _raise(*a, **kw):
        raise ConnectionError("boom")
    monkeypatch.setattr(er.requests, "get", _raise)
    assert er.fetch_bybit_symbol_universe(force=True) == {"OLD"}


# --- classify_noise ---

def test_classify_noise_binance_alpha():
    assert er.classify_noise("Binance Alpha Will List XYZ", "listing") == "binance_alpha"


def test_classify_noise_selected_stocks():
    assert er.classify_noise("Binance Exchange Adds bStocks Tokenized Securities SK Hynix (SKHYB)",
                              "listing") == "selected_stocks"


def test_classify_noise_margin_only_delisting_without_spot():
    assert er.classify_noise("Binance Margin And Loan Will Delist TST & IOTX", "delisting") == "margin_only"


def test_classify_noise_margin_and_spot_delisting_passes():
    assert er.classify_noise("Binance Will Delist Spot and Margin Trading Pairs: TST", "delisting") == ""


def test_classify_noise_normal_delisting_passes():
    assert er.classify_noise("Delisting of KORUUSDT Perpetual Contract", "delisting") == ""


def test_classify_noise_margin_only_rule_not_applied_to_listing():
    assert er.classify_noise("Binance Margin Will List XYZ", "listing") == ""


# --- is_recent (anti-backfill) ---

def test_is_recent_within_window():
    now = time.time()
    assert er.is_recent({"ts": now - 3600}, now=now) is True


def test_is_recent_outside_window():
    now = time.time()
    assert er.is_recent({"ts": now - 49 * 3600}, now=now) is False


def test_is_recent_exactly_at_boundary():
    now = time.time()
    assert er.is_recent({"ts": now - 48 * 3600}, now=now) is True


def test_is_recent_missing_ts_treated_as_epoch_zero_not_recent():
    assert er.is_recent({}, now=time.time()) is False


# --- should_alert (пересмотрено 2026-07-13: симметрично для listing/delisting) ---

def _event(kind="listing", exch="bybit", eid="e1", symbols=None, ts=None):
    return {"exchange": exch, "kind": kind, "id": eid, "title": "T",
            "symbols": symbols or [], "url": "https://u", "ts": ts if ts is not None else time.time()}


def test_should_alert_false_when_no_symbols_extracted():
    e = _event(kind="delisting", symbols=[])
    assert er.should_alert(e, watch_symbols=set(), tracked_symbols=set()) is False


def test_should_alert_delisting_true_when_in_tracked():
    e = _event(kind="delisting", symbols=["FOO"])
    assert er.should_alert(e, watch_symbols=set(), tracked_symbols={"FOO"}) is True


def test_should_alert_delisting_false_when_not_tracked_or_watched():
    e = _event(kind="delisting", symbols=["RANDOMCOIN"])
    assert er.should_alert(e, watch_symbols={"LINK"}, tracked_symbols={"BTC", "ETH"}) is False


def test_should_alert_listing_true_when_in_watch():
    e = _event(kind="listing", symbols=["LINK"])
    assert er.should_alert(e, watch_symbols={"LINK", "AVAX"}, tracked_symbols=set()) is True


def test_should_alert_listing_false_when_not_in_watch_or_tracked():
    e = _event(kind="listing", symbols=["RANDOMCOIN"])
    assert er.should_alert(e, watch_symbols={"LINK", "AVAX"}, tracked_symbols=set()) is False


def test_should_alert_case_insensitive():
    e = _event(kind="listing", symbols=["link"])
    assert er.should_alert(e, watch_symbols={"LINK"}, tracked_symbols=set()) is True


# --- format_event_alert (стандарт карточек + строка портфеля) ---

def test_format_event_alert_includes_separator_and_monospace_tickers():
    e = _event(kind="delisting", exch="binance", symbols=["FOO"])
    text = er.format_event_alert(e, watch_symbols=set())
    assert er.card_v2.SEP in text
    assert "`FOO`" in text
    assert "ДЕЛИСТИНГ" in text
    assert "BINANCE" in text


def test_format_event_alert_na_symbols_when_empty():
    e = _event(kind="listing", symbols=[])
    text = er.format_event_alert(e, watch_symbols=set())
    assert "н/д" in text


def test_format_event_alert_portfolio_line_present_when_in_watch():
    e = _event(kind="listing", symbols=["LINK"])
    text = er.format_event_alert(e, watch_symbols={"LINK"})
    assert "⚠️ Есть в твоём портфеле" in text
    assert "`LINK`" in text.split("Есть в твоём портфеле")[1]


def test_format_event_alert_portfolio_line_absent_when_not_in_watch():
    e = _event(kind="listing", symbols=["FOO"])
    text = er.format_event_alert(e, watch_symbols={"LINK"})
    assert "В портфеле нет" in text
    assert "Есть в твоём портфеле" not in text


# --- fetch_bybit_announcements / fetch_binance_announcements (mocked HTTP) ---

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


# --- filter_new_events ---

def test_filter_new_events_excludes_seen():
    events = [_event(eid="a"), _event(eid="b")]
    assert er.filter_new_events(events, {"a"}) == [events[1]]


# --- seen-ids persistence ---

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


# --- poll_and_get_alerts (integration: anti-backfill + noise + relevance) ---

def test_poll_and_get_alerts_end_to_end(monkeypatch, tmp_path):
    now = time.time()
    events = [
        _event(kind="delisting", eid="d1", symbols=["FOO"], ts=now),
        _event(kind="listing", eid="l1", symbols=["LINK"], ts=now),
        _event(kind="listing", eid="l2", symbols=["NOTWATCHED"], ts=now),
    ]
    monkeypatch.setattr(er, "fetch_all_events", lambda limit=20, known_symbols=None: events)
    seen_path = str(tmp_path / "seen.json")
    events_dir = str(tmp_path / "events")

    alerts = er.poll_and_get_alerts(watch_symbols={"LINK"}, tracked_symbols={"FOO"},
                                     seen_ids_path=seen_path, events_dir=events_dir, now=now)
    assert len(alerts) == 2  # FOO (tracked) + LINK (watch), NOT NOTWATCHED

    alerts2 = er.poll_and_get_alerts(watch_symbols={"LINK"}, tracked_symbols={"FOO"},
                                      seen_ids_path=seen_path, events_dir=events_dir, now=now)
    assert alerts2 == []


def test_poll_and_get_alerts_no_events_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(er, "fetch_all_events", lambda limit=20, known_symbols=None: [])
    alerts = er.poll_and_get_alerts(watch_symbols=set(), seen_ids_path=str(tmp_path / "seen.json"),
                                     events_dir=str(tmp_path / "events"))
    assert alerts == []


def test_poll_and_get_alerts_skips_backfill_but_still_logs(monkeypatch, tmp_path):
    now = time.time()
    old_event = _event(kind="delisting", eid="old1", symbols=["FOO"], ts=now - 72 * 3600)
    monkeypatch.setattr(er, "fetch_all_events", lambda limit=20, known_symbols=None: [old_event])
    events_dir = str(tmp_path / "events")
    alerts = er.poll_and_get_alerts(watch_symbols=set(), tracked_symbols={"FOO"},
                                     seen_ids_path=str(tmp_path / "seen.json"),
                                     events_dir=events_dir, now=now)
    assert alerts == []
    recent = er.read_recent_events(hours=24 * 7, events_dir=events_dir, now=now + 10)
    assert len(recent) == 1
    assert recent[0]["id"] == "old1"


def test_poll_and_get_alerts_skips_noise_but_still_logs(monkeypatch, tmp_path):
    now = time.time()
    noisy = _event(kind="listing", eid="noisy1", symbols=["FOO"], ts=now)
    noisy["title"] = "Binance Alpha Will List FOO"
    monkeypatch.setattr(er, "fetch_all_events", lambda limit=20, known_symbols=None: [noisy])
    events_dir = str(tmp_path / "events")
    alerts = er.poll_and_get_alerts(watch_symbols={"FOO"}, tracked_symbols=set(),
                                     seen_ids_path=str(tmp_path / "seen.json"),
                                     events_dir=events_dir, now=now)
    assert alerts == []
    recent = er.read_recent_events(hours=24, events_dir=events_dir, now=now + 10)
    assert len(recent) == 1


def test_poll_and_get_alerts_skips_events_with_zero_extracted_symbols(monkeypatch, tmp_path):
    now = time.time()
    unmatched = _event(kind="delisting", eid="unmatched1", symbols=[], ts=now)
    monkeypatch.setattr(er, "fetch_all_events", lambda limit=20, known_symbols=None: [unmatched])
    alerts = er.poll_and_get_alerts(watch_symbols=set(), tracked_symbols=set(),
                                     seen_ids_path=str(tmp_path / "seen.json"),
                                     events_dir=str(tmp_path / "events"), now=now)
    assert alerts == []


def test_poll_and_get_alerts_writes_events_log(monkeypatch, tmp_path):
    now = time.time()
    events = [_event(kind="delisting", eid="d1", symbols=["FOO"], ts=now)]
    monkeypatch.setattr(er, "fetch_all_events", lambda limit=20, known_symbols=None: events)
    events_dir = str(tmp_path / "events")
    er.poll_and_get_alerts(watch_symbols=set(), tracked_symbols={"FOO"},
                            seen_ids_path=str(tmp_path / "seen.json"),
                            events_dir=events_dir, now=now)
    recent = er.read_recent_events(hours=24, events_dir=events_dir, now=now + 10)
    assert len(recent) == 1


# --- events log (EVENT-DIGEST, Пакет 13 М5) ---

def test_append_event_log_and_read_recent_events(tmp_path):
    events_dir = str(tmp_path / "events")
    now = time.time()
    e = _event(kind="delisting", eid="d1", symbols=["FOO"], ts=now)
    er.append_event_log(e, events_dir=events_dir)
    recent = er.read_recent_events(hours=12, events_dir=events_dir, now=now + 3600)
    assert len(recent) == 1
    assert recent[0]["id"] == "d1"


def test_read_recent_events_excludes_older_than_window(tmp_path):
    events_dir = str(tmp_path / "events")
    now = time.time()
    old = _event(eid="old", symbols=[], ts=now)
    er.append_event_log(old, events_dir=events_dir)
    recent = er.read_recent_events(hours=1, events_dir=events_dir, now=now + 3 * 3600)
    assert recent == []


def test_read_recent_events_missing_dir_returns_empty(tmp_path):
    assert er.read_recent_events(hours=12, events_dir=str(tmp_path / "nope")) == []


def test_format_event_digest_section_no_events(tmp_path):
    text = er.format_event_digest_section(hours=12, events_dir=str(tmp_path / "empty"))
    assert "EVENT-RADAR" in text
    assert "не было" in text


def test_format_event_digest_section_shows_counts(tmp_path):
    events_dir = str(tmp_path / "events")
    now = time.time()
    er.append_event_log({**_event(kind="listing", eid="l1", symbols=["LINK"]), "ts": now}, events_dir=events_dir)
    er.append_event_log({**_event(kind="delisting", eid="d1", symbols=["FOO"]), "ts": now}, events_dir=events_dir)
    text = er.format_event_digest_section(hours=12, events_dir=events_dir, now=now + 10)
    assert "Листингов: 1" in text
    assert "Делистингов: 1" in text
    assert "LINK" in text or "FOO" in text


def test_format_event_digest_section_events_dir_none_uses_module_global(monkeypatch, tmp_path):
    events_dir = str(tmp_path / "events")
    monkeypatch.setattr(er, "EVENTS_DIR", events_dir)
    now = time.time()
    er.append_event_log({**_event(kind="listing", eid="l1", symbols=["LINK"]), "ts": now}, events_dir=events_dir)
    text = er.format_event_digest_section(hours=12, now=now + 10)
    assert "Листингов: 1" in text
