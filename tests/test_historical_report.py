"""backtest/historical_report.py -- метрики на детерминированных фикстурах."""
import backtest.historical_report as hr


def _t(symbol, direction, r, start_ms, outcome="TP1_HIT"):
    return {"symbol": symbol, "direction": direction, "actual_r": r,
            "start_ms": start_ms, "outcome": outcome, "entry": 100, "sl": 90,
            "tp1": 110, "tp2": 120, "tp3": 130, "rr_tp1": 2.0}


def test_metrics_for_empty():
    m = hr._metrics_for([])
    assert m["total"] == 0
    assert m["win_rate"] is None


def test_metrics_for_basic():
    trades = [_t("A", "long", 2.0, 0), _t("A", "long", -1.0, 1000, "SL_HIT")]
    m = hr._metrics_for(trades)
    assert m["total"] == 2
    assert m["win_rate"] == 50.0
    assert m["avg_r"] == 0.5
    # expectancy: p=0.5, avg_win=2.0, avg_loss=-1.0 -> 0.5*2 + 0.5*(-1) = 0.5
    assert m["expectancy_r"] == 0.5
    assert m["profit_factor"] == 2.0


def test_max_drawdown_r():
    ordered = [_t("A", "long", 1.0, 0), _t("A", "long", -2.0, 1), _t("A", "long", 1.0, 2)]
    # equity: 1 -> -1 -> 0; peak after first =1, dd max = 1-(-1)=2
    assert hr.max_drawdown_r(ordered) == 2.0


def test_session_bucket_dead_zone_default():
    # UTC ms for an hour clearly outside all defined killzones after +3 conversion
    import datetime
    dt = datetime.datetime(2026, 1, 1, 3, 0, tzinfo=datetime.timezone.utc)  # UTC 3 -> UTC+3 = 6h
    ms = int(dt.timestamp() * 1000)
    assert hr._session_bucket(ms) == "Dead Zone (вне killzone)"


def test_month_bucket_format():
    import datetime
    dt = datetime.datetime(2026, 3, 15, tzinfo=datetime.timezone.utc)
    ms = int(dt.timestamp() * 1000)
    assert hr._month_bucket(ms) == "2026-03"


def test_build_report_basic_structure():
    trades = [
        _t("A", "long", 1.0, 0), _t("A", "long", -1.0, 86400_000, "SL_HIT"),
        _t("B", "short", 2.0, 172800_000),
    ]
    report = hr.build_report(trades, ["A", "B"], [])
    assert report["overall"]["total"] == 3
    assert "long" in report["by_direction"]
    assert "short" in report["by_direction"]
    assert report["symbols_scanned"] == 2
    assert report["outcome_breakdown"]["TP1_HIT"] == 2
    assert report["outcome_breakdown"]["SL_HIT"] == 1


def test_render_markdown_no_crash_on_empty():
    report = hr.build_report([], [], [])
    md = hr.render_markdown(report, "период не указан")
    assert "Сделок не найдено" in md
