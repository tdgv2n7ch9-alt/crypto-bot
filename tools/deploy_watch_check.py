"""
tools/deploy_watch_check.py -- ПАКЕТ deploy-resilience (владелец, 2026-07-14):
проверка "затронул ли этот коммит Watch Paths из railway.json", вынесена в
отдельный тестируемый модуль (не встроена прямо в tools/deploy.sh) -- та же
логика используется скриптом и покрывается pytest.

Читает `build.watchPatterns` из railway.json ЖИВЬЁМ (не хардкод-копию) --
если владелец поменяет паттерны в railway.json, эта проверка не разойдётся
с реальным поведением Railway автоматически.
"""
import fnmatch
import json
import os
import sys


def load_watch_patterns(railway_json_path: str = None) -> list:
    """Читает build.watchPatterns из railway.json. Пустой список, если файла
    нет или поле отсутствует -- честно, не выдумывает паттерны по умолчанию."""
    if railway_json_path is None:
        railway_json_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "railway.json")
    try:
        with open(railway_json_path) as f:
            cfg = json.load(f)
    except Exception:
        return []
    return cfg.get("build", {}).get("watchPatterns", [])


def matches_pattern(path: str, pattern: str) -> bool:
    """Один файл против одного паттерна из watchPatterns. Три формы,
    наблюдаемые в railway.json этого проекта:
      - 'dir/**' -- рекурсивный префикс (path == dir ИЛИ начинается с dir/).
      - паттерн без '/' (например '*.py', 'requirements.txt') -- root-level
        файл ИЛИ совпадение по basename (наблюдаемое поведение: bot.py в
        корне триггерит деплой на '*.py').
      - остальное -- обычный fnmatch по полному пути."""
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return path == prefix or path.startswith(prefix + "/")
    if "/" not in pattern:
        # Находка (поймана тестом): fnmatch НЕ path-aware -- "*" у него
        # пересекает "/", поэтому fnmatch("tools/x.py", "*.py") == True,
        # что неверно для root-level паттерна. Только явная root-level
        # проверка, без фоллбека на "обычный" fnmatch по полному пути.
        return "/" not in path and fnmatch.fnmatch(path, pattern)
    return fnmatch.fnmatch(path, pattern)


def touches_watch_path(changed_files: list, patterns: list = None) -> bool:
    """True, если хотя бы один изменённый файл попадает хотя бы под один
    watchPattern -- т.е. Railway ДОЛЖЕН был задеплоить этот коммит."""
    if patterns is None:
        patterns = load_watch_patterns()
    return any(matches_pattern(f, p) for f in changed_files for p in patterns)


if __name__ == "__main__":
    # tools/deploy.sh вызывает: CHANGED_FILES_ENV=... python3 tools/deploy_watch_check.py
    changed = os.environ.get("CHANGED_FILES_ENV", "").strip().split("\n")
    changed = [f for f in changed if f]
    print("yes" if touches_watch_path(changed) else "no")
