"""
pytest для tools/deploy_watch_check.py -- ПАКЕТ deploy-resilience (владелец,
2026-07-14): проверка, попадает ли изменённый файл под watchPatterns из
railway.json -- используется tools/deploy.sh для честного различения
"SKIPPED ожидаемо" (journal/docs-only) от "SKIPPED неожиданно" (код не
задеплоился, нужен авто-триггер).

Наряд 15->16.07, владелец: дефект найден живьём 2026-07-14 -- deploy.sh
раньше диффал только HEAD~1..HEAD (последний коммит), теряя файлы из более
ранних коммитов многокоммитного пуша (реальный случай: 7-коммитный пуш дал
ложный "watchPatterns hit: no", хотя 3 коммита трогали bot.py).
changed_files_in_range() -- фикс, тестируется на РЕАЛЬНОМ временном git-
репозитории (subprocess, не мок) -- сам факт диапазона имеет значение.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import deploy_watch_check as dwc


def _git(repo_dir, *args):
    result = subprocess.run(["git", *args], cwd=repo_dir, capture_output=True, text=True)
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout


def _init_repo(repo_dir):
    _git(repo_dir, "init", "-q")
    _git(repo_dir, "config", "user.email", "test@example.com")
    _git(repo_dir, "config", "user.name", "Test")


def _commit_file(repo_dir, filename, content, message):
    path = os.path.join(repo_dir, filename)
    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(filename) else None
    with open(path, "w") as f:
        f.write(content)
    _git(repo_dir, "add", filename)
    _git(repo_dir, "commit", "-q", "-m", message)

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


# ── changed_files_in_range() -- дефект многокоммитного пуша (наряд 15->16.07) ──

def test_changed_files_in_range_single_commit(tmp_path):
    repo = str(tmp_path)
    _init_repo(repo)
    _commit_file(repo, "README.md", "v1", "initial")
    base = _git(repo, "rev-parse", "HEAD").strip()
    _commit_file(repo, "bot.py", "print(1)", "add bot.py")
    files = dwc.changed_files_in_range(base, "HEAD", cwd=repo)
    assert files == ["bot.py"]


def test_changed_files_in_range_covers_whole_multi_commit_push():
    """ТА САМАЯ находка -- 3 коммита ПОСЛЕ base, только последний трогает
    docs, первые два трогают код. Старый баг (HEAD~1..HEAD) увидел бы
    ТОЛЬКО docs-коммит и соврал бы "watchPatterns hit: no"."""
    import tempfile
    with tempfile.TemporaryDirectory() as repo:
        _init_repo(repo)
        _commit_file(repo, "README.md", "v1", "initial")
        base = _git(repo, "rev-parse", "HEAD").strip()
        _commit_file(repo, "bot.py", "print(1)", "commit 1: bot.py")
        _commit_file(repo, "shadow_engine.py", "x=1", "commit 2: shadow_engine.py")
        _commit_file(repo, "PROGRESS.md", "note", "commit 3: docs only (был бы 'последним')")
        files = dwc.changed_files_in_range(base, "HEAD", cwd=repo)
        assert set(files) == {"bot.py", "shadow_engine.py", "PROGRESS.md"}
        # Старый баг: HEAD~1..HEAD увидел бы только PROGRESS.md
        old_buggy_files = dwc.changed_files_in_range("HEAD~1", "HEAD", cwd=repo)
        assert old_buggy_files == ["PROGRESS.md"]
        # Итоговое решение touches_watch_path() РАСХОДИТСЯ между старым и
        # новым диапазоном -- именно это и было живым инцидентом.
        assert dwc.touches_watch_path(files) is True
        assert dwc.touches_watch_path(old_buggy_files) is False


def test_changed_files_in_range_no_changes_returns_empty(tmp_path):
    repo = str(tmp_path)
    _init_repo(repo)
    _commit_file(repo, "README.md", "v1", "initial")
    head = _git(repo, "rev-parse", "HEAD").strip()
    files = dwc.changed_files_in_range(head, "HEAD", cwd=repo)
    assert files == []


def test_changed_files_in_range_invalid_ref_returns_empty_honest(tmp_path):
    repo = str(tmp_path)
    _init_repo(repo)
    _commit_file(repo, "README.md", "v1", "initial")
    files = dwc.changed_files_in_range("nonexistent-ref-xyz", "HEAD", cwd=repo)
    assert files == []


def test_changed_files_in_range_not_a_git_repo_returns_empty_honest(tmp_path):
    files = dwc.changed_files_in_range("HEAD~1", "HEAD", cwd=str(tmp_path))
    assert files == []


# ── main() env-var precedence: DEPLOY_WATCH_BASE_REF wins over CHANGED_FILES_ENV ──

def test_main_prefers_base_ref_over_changed_files_env(tmp_path, monkeypatch, capsys):
    repo = str(tmp_path)
    _init_repo(repo)
    _commit_file(repo, "README.md", "v1", "initial")
    base = _git(repo, "rev-parse", "HEAD").strip()
    _commit_file(repo, "bot.py", "print(1)", "add bot.py")

    monkeypatch.setenv("DEPLOY_WATCH_BASE_REF", base)
    monkeypatch.setenv("CHANGED_FILES_ENV", "PROGRESS.md")  # would say "no" if used
    monkeypatch.chdir(repo)
    monkeypatch.setattr(sys, "argv", ["deploy_watch_check.py"])
    import runpy
    runpy.run_path(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "tools", "deploy_watch_check.py"),
        run_name="__main__")
    out = capsys.readouterr().out.strip()
    assert out == "yes"  # bot.py, не PROGRESS.md из CHANGED_FILES_ENV
