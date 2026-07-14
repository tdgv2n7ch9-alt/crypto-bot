"""
pytest для tools/deploy_watch_check.py -- ПАКЕТ deploy-resilience (владелец,
2026-07-14): проверка, попадает ли изменённый файл под watchPatterns из
railway.json -- используется tools/deploy.sh для честного различения
"SKIPPED ожидаемо" (journal/docs-only) от "SKIPPED неожиданно" (код не
задеплоился, нужен авто-триггер).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import deploy_watch_check as dwc

# Тот же набор паттернов, что реально в railway.json на 2026-07-14 -- если
# владелец поменяет railway.json, live-функция load_watch_patterns() это
# подхватит сама (проверено отдельным тестом ниже), эти тесты фиксируют
# КОНТРАКТ matches_pattern()/touches_watch_path() на известном наборе.
PATTERNS = ["*.py", "requirements.txt", "Dockerfile", "railway.json",
            "backtest/**", "tests/**", "patches/**"]


def test_root_level_py_file_matches_star_py():
    assert dwc.matches_pattern("bot.py", "*.py")
    assert dwc.matches_pattern("glossary.py", "*.py")


def test_nested_py_file_does_not_match_star_py_alone():
    """'*.py' без '/' -- root-level only, tools/deploy.py НЕ должен матчить
    (найдено живьём как честная находка -- tools/*.py вне Watch Paths)."""
    assert not dwc.matches_pattern("tools/morning_brief.py", "*.py")


def test_recursive_glob_pattern():
    assert dwc.matches_pattern("tests/test_glossary.py", "tests/**")
    assert dwc.matches_pattern("backtest/isolate_08.py", "backtest/**")
    assert not dwc.matches_pattern("tools/deploy.sh", "tests/**")


def test_exact_root_file_pattern():
    assert dwc.matches_pattern("railway.json", "railway.json")
    assert dwc.matches_pattern("Dockerfile", "Dockerfile")
    assert not dwc.matches_pattern("journal/watch_zones.json", "railway.json")


def test_touches_watch_path_journal_only_is_false():
    changed = ["journal/watch_zones.json", "PROGRESS.md"]
    assert dwc.touches_watch_path(changed, PATTERNS) is False


def test_touches_watch_path_bot_py_mixed_with_journal_is_true():
    changed = ["bot.py", "journal/watch_zones.json"]
    assert dwc.touches_watch_path(changed, PATTERNS) is True


def test_touches_watch_path_tests_dir_is_true():
    changed = ["tests/test_deploy_watch_check.py"]
    assert dwc.touches_watch_path(changed, PATTERNS) is True


def test_touches_watch_path_empty_changed_files_is_false():
    assert dwc.touches_watch_path([], PATTERNS) is False


def test_touches_watch_path_docs_only_is_false():
    changed = ["CLAUDE.md", "NEXT_PACKAGE.md", "docs/SIGNAL_VISUAL_STANDARD.md"]
    assert dwc.touches_watch_path(changed, PATTERNS) is False


def test_load_watch_patterns_reads_live_railway_json():
    """Живая проверка -- НЕ хардкод-копия: читает реальный railway.json
    репозитория, чтобы находка не разошлась молча, если владелец изменит
    конфиг."""
    patterns = dwc.load_watch_patterns()
    assert "*.py" in patterns
    assert "tests/**" in patterns


def test_load_watch_patterns_missing_file_returns_empty_honest():
    assert dwc.load_watch_patterns("/nonexistent/railway.json") == []
