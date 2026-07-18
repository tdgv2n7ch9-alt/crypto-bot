"""
Владелец, РЕШЕНИЕ #2 (18.07.2026 вечер) -- "restore не затирает живой файл".
Лок регресса: `.dockerignore` обязан исключать git-tracked
journal/shadow_signals.json и journal/archive/ из билд-контекста, иначе
`Dockerfile` (`COPY . .`) снова начнёт бейковать запечённый снапшот в
каждый образ, и shadow_engine.restore_shadow_file_sync()/
restore_archive_sync() (проверка `os.path.exists()`) снова станут мёртвым
кодом -- см. PROGRESS.md, инцидент GitHub-422 14+ часов.
"""
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCKERIGNORE_PATH = os.path.join(REPO_ROOT, ".dockerignore")


def _lines():
    with open(DOCKERIGNORE_PATH, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]


def test_dockerignore_exists():
    assert os.path.exists(DOCKERIGNORE_PATH)


def test_dockerignore_excludes_shadow_signals_file():
    assert "journal/shadow_signals.json" in _lines()


def test_dockerignore_excludes_shadow_archive_dir():
    lines = _lines()
    assert any(ln.rstrip("/") == "journal/archive" for ln in lines)
