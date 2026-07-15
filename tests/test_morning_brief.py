"""
pytest для tools/morning_brief.py (НОЧЬ#3 Н8, владелец) -- генератор
MORNING_BRIEF. Каждая секция тестируется через monkeypatch на живые
источники (bot/daily_metrics/onchain_metrics/rug_radar/shadow_engine),
без сети/файлового I/O за пределами tmp_path.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import morning_brief as mb


# ── market_section() ──

def test_market_section_shows_zone_position_and_distance(monkeypatch):
    monkeypatch.setattr(mb.bot, "author_zones_status_summary", lambda: {
        "total": 1, "counts": {},
        "zones": [{"symbol": "BTC", "side": "LONG", "status": "ЦЕНА В ЗОНЕ",
                    "price": 62000.0, "distance_pct": 0.0, "lo": 61800.0, "hi": 62200.0}],
    })
    monkeypatch.setattr(mb.daily_metrics, "level_watch_touches_today", lambda **kw: [])
    lines = mb.market_section(1_000_000.0)
    text = "\n".join(lines)
    assert "**BTC** ($62,000.00)" in text
    assert "внутри" in text
    assert "дистанция 0.00%" in text


def test_market_section_no_zones_for_symbol(monkeypatch):
    monkeypatch.setattr(mb.bot, "author_zones_status_summary", lambda: {
        "total": 0, "counts": {}, "zones": [],
    })
    monkeypatch.setattr(mb.daily_metrics, "level_watch_touches_today", lambda **kw: [])
    text = "\n".join(mb.market_section(1_000_000.0))
    assert "BTC**: нет активных author-зон" in text
    assert "ETH**: нет активных author-зон" in text


def test_market_section_na_price_shown_honestly(monkeypatch):
    monkeypatch.setattr(mb.bot, "author_zones_status_summary", lambda: {
        "total": 1, "counts": {},
        "zones": [{"symbol": "BTC", "side": "LONG", "status": "н/д (нет цены)",
                    "price": 0, "distance_pct": None, "lo": 100.0, "hi": 110.0}],
    })
    monkeypatch.setattr(mb.daily_metrics, "level_watch_touches_today", lambda **kw: [])
    text = "\n".join(mb.market_section(1_000_000.0))
    assert "н/д (нет цены)" in text


def test_market_section_touches_listed(monkeypatch):
    monkeypatch.setattr(mb.bot, "author_zones_status_summary", lambda: {
        "total": 0, "counts": {}, "zones": [],
    })
    monkeypatch.setattr(mb.daily_metrics, "level_watch_touches_today",
                         lambda **kw: [{"symbol": "ETHUSDT"}, {"symbol": "ETHUSDT"}])
    text = "\n".join(mb.market_section(1_000_000.0))
    assert "Всего: 2, символы: ETHUSDT" in text


def test_market_section_no_touches_says_none(monkeypatch):
    monkeypatch.setattr(mb.bot, "author_zones_status_summary", lambda: {
        "total": 0, "counts": {}, "zones": [],
    })
    monkeypatch.setattr(mb.daily_metrics, "level_watch_touches_today", lambda **kw: [])
    text = "\n".join(mb.market_section(1_000_000.0))
    assert "Ни одного касания за ночь" in text


def test_market_section_na_on_summary_exception(monkeypatch):
    def _boom():
        raise RuntimeError("watch_zones недоступен")
    monkeypatch.setattr(mb.bot, "author_zones_status_summary", _boom)
    monkeypatch.setattr(mb.daily_metrics, "level_watch_touches_today", lambda **kw: [])
    text = "\n".join(mb.market_section(1_000_000.0))
    assert "н/д (ошибка author_zones_status_summary" in text


# ── shadow_table_section() ──

def test_shadow_table_section_shows_all_contours(monkeypatch):
    monkeypatch.setattr(mb.shadow_engine, "contour_readiness_summary", lambda: {
        "tz13": {"n": 400, "threshold": 100, "ready": True, "remaining": 0},
        "patch05_bpr": {"n": 50, "threshold": 200, "ready": False, "remaining": 150},
        "patch09_oi": {"n": 900, "threshold": 100, "ready": True, "remaining": 0},
    })
    monkeypatch.setattr(mb.shadow_engine, "ema_stack_readiness_summary",
                         lambda: {"n": 1, "ready": False, "elapsed_hours": 13.3, "window_hours": 72.0})
    text = "\n".join(mb.shadow_table_section())
    assert "| tz13 | 400 | 100 | да |" in text
    assert "| Патч 05 (BPR) | 50 | 200 | нет, осталось 150 |" in text
    assert "EMA-стек | 1 |" in text
    assert "13.3/72ч" in text


# ── library_progress_section() -- Пакет П-Библиотека Этап 2 (владелец) ──

def test_library_progress_section_reports_done_total(monkeypatch, tmp_path):
    import json
    progress_file = tmp_path / "_progress.json"
    progress_file.write_text(json.dumps({"total": 160, "done_count": 11}), encoding="utf-8")
    monkeypatch.setattr(mb, "LIBRARY_PROGRESS_PATH", str(progress_file))
    text = "\n".join(mb.library_progress_section())
    assert "11/160" in text


def test_library_progress_section_honest_na_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(mb, "LIBRARY_PROGRESS_PATH", str(tmp_path / "does_not_exist.json"))
    text = "\n".join(mb.library_progress_section())
    assert "н/д" in text


def test_library_progress_section_honest_na_on_corrupt_json(monkeypatch, tmp_path):
    bad_file = tmp_path / "_progress.json"
    bad_file.write_text("not valid json{{{", encoding="utf-8")
    monkeypatch.setattr(mb, "LIBRARY_PROGRESS_PATH", str(bad_file))
    text = "\n".join(mb.library_progress_section())
    assert "н/д" in text


# ── _latest_evolution_finding() ──

def test_latest_evolution_finding_picks_last_dated_heading(monkeypatch, tmp_path):
    evo = tmp_path / "EVOLUTION.md"
    evo.write_text(
        "## 2026-07-10 -- старая запись\n\nТекст старой записи.\n\n"
        "## 2026-07-13 -- новая запись\n\nТекст новой записи с числом 42.\n"
    )
    monkeypatch.setattr(mb, "EVOLUTION_MD_PATH", str(evo))
    result = mb._latest_evolution_finding()
    assert "2026-07-13" in result
    assert "Текст новой записи с числом 42" in result
    assert "Текст старой записи" not in result


def test_latest_evolution_finding_skips_undated_heading(monkeypatch, tmp_path):
    evo = tmp_path / "EVOLUTION.md"
    evo.write_text(
        "## 2026-07-10 -- запись\n\nТекст.\n\n## Дальше (служебное)\n\nНе находка.\n"
    )
    monkeypatch.setattr(mb, "EVOLUTION_MD_PATH", str(evo))
    result = mb._latest_evolution_finding()
    assert "2026-07-10" in result


def test_latest_evolution_finding_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(mb, "EVOLUTION_MD_PATH", str(tmp_path / "missing.md"))
    result = mb._latest_evolution_finding()
    assert "н/д" in result


def test_latest_evolution_finding_truncates_at_word_boundary(monkeypatch, tmp_path):
    evo = tmp_path / "EVOLUTION.md"
    long_line = "слово " * 60
    evo.write_text(f"## 2026-07-13 -- заголовок\n\n{long_line}\n")
    monkeypatch.setattr(mb, "EVOLUTION_MD_PATH", str(evo))
    result = mb._latest_evolution_finding()
    assert len(result) <= 224
    assert result.endswith("...")
    assert not result[:-3].endswith(" ")  # не обрезано посреди пробела перед "..."


# ── _top_onchain_finding() ──

def test_top_onchain_finding_reports_both_metrics(monkeypatch):
    monkeypatch.setattr(mb.onchain_metrics, "get_liquidity_summary", lambda: {
        "ok": True,
        "stablecoin_flow_30d": {"ok": True, "flow_30d_pct": -1.5},
        "usdt_dominance": {"ok": True, "usdt_dominance_pct": 8.26},
    })
    result = mb._top_onchain_finding()
    assert "-1.5%" in result
    assert "8.26%" in result


def test_top_onchain_finding_honest_na_on_failed_sources(monkeypatch):
    monkeypatch.setattr(mb.onchain_metrics, "get_liquidity_summary", lambda: {
        "ok": False,
        "stablecoin_flow_30d": {"ok": False, "reason": "timeout"},
        "usdt_dominance": {"ok": False, "reason": "timeout"},
    })
    result = mb._top_onchain_finding()
    assert "стейблкоины 30д: н/д" in result
    assert "USDT.D: н/д" in result


def test_top_onchain_finding_na_on_exception(monkeypatch):
    def _boom():
        raise RuntimeError("network down")
    monkeypatch.setattr(mb.onchain_metrics, "get_liquidity_summary", _boom)
    result = mb._top_onchain_finding()
    assert "н/д" in result


# ── _top_rugscan_finding() ──

def test_top_rugscan_finding_reports_max_score(monkeypatch):
    monkeypatch.setattr(mb.bot, "_limitki_collect_zones", lambda: [
        {"symbol": "LAB", "zone": {}}, {"symbol": "BTC", "zone": {}},
    ])
    monkeypatch.setattr(mb.bot, "get_top500", lambda: [
        {"symbol": "LAB", "quote": {"USDT": {}}},
        {"symbol": "BTC", "quote": {"USDT": {}}},
    ])

    def _fake_risk(symbol, coin):
        if symbol == "LAB":
            return {"symbol": "LAB", "score": 45, "reasons": ["признак 1", "признак 2"]}
        return {"symbol": "BTC", "score": 0, "reasons": []}

    monkeypatch.setattr(mb.rug_radar, "compute_rug_risk", _fake_risk)
    result = mb._top_rugscan_finding()
    assert "LAB" in result
    assert "45" in result
    assert "проверено 2 символов" in result


def test_top_rugscan_finding_no_elevated_risk(monkeypatch):
    monkeypatch.setattr(mb.bot, "_limitki_collect_zones", lambda: [{"symbol": "BTC", "zone": {}}])
    monkeypatch.setattr(mb.bot, "get_top500", lambda: [{"symbol": "BTC", "quote": {"USDT": {}}}])
    monkeypatch.setattr(mb.rug_radar, "compute_rug_risk",
                         lambda symbol, coin: {"symbol": symbol, "score": 0, "reasons": []})
    result = mb._top_rugscan_finding()
    assert "повышенного риска не найдено" in result


def test_top_rugscan_finding_no_zones(monkeypatch):
    monkeypatch.setattr(mb.bot, "_limitki_collect_zones", lambda: [])
    result = mb._top_rugscan_finding()
    assert "нет активных author-зон" in result


# ── open_questions_section() ──

def test_open_questions_flags_ready_contours(monkeypatch):
    monkeypatch.setattr(mb.shadow_engine, "contour_readiness_summary", lambda: {
        "tz13": {"n": 400, "threshold": 100, "ready": True, "remaining": 0},
        "patch05_bpr": {"n": 50, "threshold": 200, "ready": False, "remaining": 150},
        "patch09_oi": {"n": 5, "threshold": 100, "ready": False, "remaining": 95},
    })
    text = "\n".join(mb.open_questions_section())
    assert "tz13" in text
    assert "Патч 05" not in text.split("\n")[1]  # только готовые в первом вопросе


def test_open_questions_no_ready_contours(monkeypatch):
    monkeypatch.setattr(mb.shadow_engine, "contour_readiness_summary", lambda: {
        "tz13": {"n": 5, "threshold": 100, "ready": False, "remaining": 95},
        "patch05_bpr": {"n": 5, "threshold": 200, "ready": False, "remaining": 195},
        "patch09_oi": {"n": 5, "threshold": 100, "ready": False, "remaining": 95},
    })
    text = "\n".join(mb.open_questions_section())
    assert "продолжаем копить" in text


# ── build_morning_brief() / write_morning_brief() ──

def test_build_morning_brief_assembles_all_sections(monkeypatch):
    monkeypatch.setattr(mb.bot, "author_zones_status_summary", lambda: {
        "total": 0, "counts": {}, "zones": [],
    })
    monkeypatch.setattr(mb.daily_metrics, "level_watch_touches_today", lambda **kw: [])
    monkeypatch.setattr(mb.shadow_engine, "contour_readiness_summary", lambda: {
        "tz13": {"n": 0, "threshold": 100, "ready": False, "remaining": 100},
        "patch05_bpr": {"n": 0, "threshold": 200, "ready": False, "remaining": 200},
        "patch09_oi": {"n": 0, "threshold": 100, "ready": False, "remaining": 100},
    })
    monkeypatch.setattr(mb.shadow_engine, "ema_stack_readiness_summary",
                         lambda: {"n": 0, "ready": False, "elapsed_hours": 0.0, "window_hours": 72.0})
    monkeypatch.setattr(mb, "EVOLUTION_MD_PATH", "/does/not/exist.md")
    monkeypatch.setattr(mb.onchain_metrics, "get_liquidity_summary", lambda: {
        "ok": False, "stablecoin_flow_30d": {"ok": False}, "usdt_dominance": {"ok": False},
    })
    monkeypatch.setattr(mb.bot, "_limitki_collect_zones", lambda: [])

    text = mb.build_morning_brief(now_ts=1_000_000.0)
    assert "# MORNING BRIEF" in text
    assert "## 1) Рынок к утру" in text
    assert "## 2) Тень одной таблицей" in text
    assert "## Библиотека: конспекты видео" in text
    assert "## 3) Топ-3 находки ночи" in text
    assert "## 4) Вопросы владельцу на сегодня" in text


def test_write_morning_brief_creates_dated_file(monkeypatch, tmp_path):
    monkeypatch.setattr(mb, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(mb, "build_morning_brief", lambda now_ts=None: "тестовый контент\n")
    path = mb.write_morning_brief(now_ts=1_752_400_800.0)  # 2025-07-13 05:00:00 UTC
    assert os.path.exists(path)
    assert "MORNING_BRIEF_" in os.path.basename(path)
    with open(path, encoding="utf-8") as f:
        assert f.read() == "тестовый контент\n"
