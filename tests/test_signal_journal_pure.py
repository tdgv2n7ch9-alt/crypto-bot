"""signal_journal.py -- чистые функции (без сети/файлов): вход в зону, SL/TP-хиты,
определение исхода, расчёт actual_r, merge при GitHub-конфликте, метка режима.
Ночная сессия #3, Блок D -- расширение покрытия ("journal-математика")."""
import signal_journal as sj


def test_touches_entry_long_inside_zone():
    assert sj._touches_entry(105, 100, 110) is True


def test_touches_entry_reversed_bounds():
    # entry_hi < entry_lo (short-конвенция) -- функция должна нормализовать порядок
    assert sj._touches_entry(105, 110, 100) is True


def test_touches_entry_outside_zone():
    assert sj._touches_entry(120, 100, 110) is False


def test_sl_hit_long():
    assert sj._sl_hit("long", 89, 90) is True
    assert sj._sl_hit("long", 91, 90) is False


def test_sl_hit_short():
    assert sj._sl_hit("short", 91, 90) is True
    assert sj._sl_hit("short", 89, 90) is False


def test_sl_hit_none_sl_is_false():
    assert sj._sl_hit("long", 50, None) is False


def test_tp_hit_long_and_short():
    assert sj._tp_hit("long", 111, 110) is True
    assert sj._tp_hit("long", 109, 110) is False
    assert sj._tp_hit("short", 89, 90) is True


def test_tp_hit_none_tp_is_false():
    assert sj._tp_hit("long", 999, None) is False


def test_check_outcome_sl_priority_over_tp():
    # цена одновременно бьёт и SL, и TP1 -- SL должен победить (консервативно)
    status, level = sj._check_outcome("long", 80, sl=85, tp1=110, tp2=120, tp3=130)
    assert status == "SL_HIT" and level == "sl"


def test_check_outcome_furthest_tp_wins():
    status, level = sj._check_outcome("long", 125, sl=90, tp1=110, tp2=120, tp3=130)
    assert status == "TP2_HIT" and level == "tp2"


def test_check_outcome_none_when_no_level_hit():
    status, level = sj._check_outcome("long", 100, sl=90, tp1=110, tp2=120, tp3=130)
    assert status is None and level is None


def test_check_outcome_short_direction():
    status, level = sj._check_outcome("short", 88, sl=115, tp1=90, tp2=80, tp3=70)
    assert status == "TP1_HIT" and level == "tp1"


def test_compute_actual_r_win():
    rec = {"entered_price": 100, "sl": 90, "tp1": 120}
    r = sj._compute_actual_r(rec, "tp1")
    assert r == 2.0  # (120-100)/(100-90)


def test_compute_actual_r_sl():
    rec = {"entered_price": 100, "sl": 90}
    r = sj._compute_actual_r(rec, "sl")
    assert r == -1.0


def test_compute_actual_r_missing_entered_price_returns_none():
    rec = {"sl": 90, "tp1": 120}
    assert sj._compute_actual_r(rec, "tp1") is None


def test_merge_records_remote_newer_wins():
    local = {1: {"updated_ts": 100, "status": "PENDING"}}
    remote = {1: {"updated_ts": 200, "status": "TP1_HIT"}}
    merged = sj._merge_records(local, remote)
    assert merged[1]["status"] == "TP1_HIT"


def test_merge_records_local_newer_kept():
    local = {1: {"updated_ts": 300, "status": "SL_HIT"}}
    remote = {1: {"updated_ts": 200, "status": "PENDING"}}
    merged = sj._merge_records(local, remote)
    assert merged[1]["status"] == "SL_HIT"


def test_merge_records_only_in_remote_added():
    local = {}
    remote = {2: {"updated_ts": 100, "status": "PENDING"}}
    merged = sj._merge_records(local, remote)
    assert 2 in merged


def test_regime_label_bullish_4h():
    rec = {"ema_stack": {"tf_4h": {"stack": "бычий"}}}
    assert sj.regime_label(rec) == "uptrend"


def test_regime_label_fallback_to_1h():
    rec = {"ema_stack": {"tf_1h": {"stack": "медвежий"}}}
    assert sj.regime_label(rec) == "downtrend"


def test_regime_label_unknown_when_no_snapshot():
    assert sj.regime_label({}) == "неизвестно"


# get_latest_source() -- АПГРЕЙД 11.07 Этап 1, "Монеты в работе": источник для
# легаси-записей TOP_LONG_SIGNALS/TOP_SHORT_SIGNALS, которые сами source не хранят.

def test_get_latest_source_returns_none_when_no_records(monkeypatch):
    monkeypatch.setattr(sj, "_journal", {})
    assert sj.get_latest_source("BTC") is None


def test_get_latest_source_finds_by_symbol(monkeypatch):
    monkeypatch.setattr(sj, "_journal", {
        1: {"symbol": "BTC", "direction": "long", "source": "TOP_LONG_AUTO", "ts": 100},
    })
    assert sj.get_latest_source("BTCUSDT") == "TOP_LONG_AUTO"


def test_get_latest_source_strips_usdt_suffix_and_uppercases(monkeypatch):
    monkeypatch.setattr(sj, "_journal", {
        1: {"symbol": "SOL", "direction": "short", "source": "pump_reversal", "ts": 100},
    })
    assert sj.get_latest_source("solusdt") == "pump_reversal"


def test_get_latest_source_filters_by_direction(monkeypatch):
    monkeypatch.setattr(sj, "_journal", {
        1: {"symbol": "ETH", "direction": "long", "source": "A", "ts": 100},
        2: {"symbol": "ETH", "direction": "short", "source": "B", "ts": 200},
    })
    assert sj.get_latest_source("ETH", "long") == "A"
    assert sj.get_latest_source("ETH", "short") == "B"


def test_get_latest_source_direction_none_ignores_direction(monkeypatch):
    monkeypatch.setattr(sj, "_journal", {
        1: {"symbol": "ETH", "direction": "long", "source": "A", "ts": 100},
        2: {"symbol": "ETH", "direction": "short", "source": "B", "ts": 200},
    })
    assert sj.get_latest_source("ETH") == "B"


def test_get_latest_source_picks_most_recent_ts(monkeypatch):
    monkeypatch.setattr(sj, "_journal", {
        1: {"symbol": "BTC", "direction": "long", "source": "OLD", "ts": 100},
        2: {"symbol": "BTC", "direction": "long", "source": "NEW", "ts": 500},
    })
    assert sj.get_latest_source("BTC") == "NEW"


def test_get_latest_source_no_match_for_direction_returns_none(monkeypatch):
    monkeypatch.setattr(sj, "_journal", {
        1: {"symbol": "BTC", "direction": "long", "source": "A", "ts": 100},
    })
    assert sj.get_latest_source("BTC", "short") is None
